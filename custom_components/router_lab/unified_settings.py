"""Admin-only, write-only credential settings. No service-event secret payloads."""
import asyncio
import probatio as vol
import aiohttp
from homeassistant.components import websocket_api
from homeassistant.components.assist_pipeline import pipeline as pipelines
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .const import DOMAIN, DEFAULT_URL
from .prompts import DEFAULT_ASSISTANT_PROMPT,DEFAULT_MEMORY_INSTRUCTIONS
from .settings_rules import PUBLIC_DEFAULTS,validate_settings
from . import providers
from .speech_input import choices as speech_choices,pipeline_fields as speech_fields
from ..qwen_cloud_tts.const import MODELS as CLOUD_MODELS


async def compute(hass,runtime,path,data=None,timeout=15):
    url=runtime['settings'].get('router_url',DEFAULT_URL)+path
    session=async_get_clientsession(hass)
    async with session.request('GET' if data is None else 'POST',url,json=data,timeout=aiohttp.ClientTimeout(total=timeout)) as r:
        if r.status!=200:
            try:code=(await r.json()).get('error')
            except Exception:code=None
            raise ValueError({'download_busy':'已有模型正在下载，请完成后再操作。','tts_busy':'正在生成语音，请稍后删除。',
                'invalid_model_source':'请填写 GitHub Release 中 router-heads-v4.zip 的 HTTPS 下载地址。',
                'model_cannot_delete':'基础路由模型不能在此删除。'}.get(code,'计算服务没有完成操作，请查看模型状态或应用日志。'))
        return await r.json()


def llm_config(hass,runtime):
    entity=er.async_get(hass).async_get(runtime['settings'].get('llm_agent',''))
    entry=hass.config_entries.async_get_entry(entity.config_entry_id) if entity else None
    if not entry or entry.domain!='llama_cpp':return None,None
    return entry,entry.subentries.get(entity.config_subentry_id)


def cloud_config(hass):
    entries=hass.config_entries.async_entries('qwen_cloud_tts')
    entry=entries[0] if entries else None
    return entry,({**entry.data,**entry.options} if entry else {})


async def status(hass,runtime):
    settings=runtime['settings'];llm_entry,sub=llm_config(hass,runtime);_,cloud=cloud_config(hass)
    result={k:settings.get(k,v) for k,v in PUBLIC_DEFAULTS.items()}
    result.update(router_url=settings.get('router_url',DEFAULT_URL),
        assistant_prompt=settings.get('assistant_prompt',DEFAULT_ASSISTANT_PROMPT),
        memory_instructions=settings.get('memory_instructions',DEFAULT_MEMORY_INSTRUCTIONS),
        llm_provider=providers.identify(str(llm_entry.data.get('base_url',''))) if llm_entry else settings.get('llm_provider','deepseek'),
        llm_base_url=str(llm_entry.data.get('base_url','')).rstrip('/') if llm_entry else settings.get('llm_base_url','https://api.deepseek.com'),
        llm_model=sub.data.get('chat_model','') if sub else '',
        llm_configured=bool(llm_entry and llm_entry.data.get('api_key')),
        providers=providers.PROVIDERS,
        cloud_configured=bool(cloud.get('api_key')),cloud_host=cloud.get('host',''),
        cloud_voice=cloud.get('voice','Cherry'),cloud_model=cloud.get('model','qwen3-tts-flash-realtime'))
    result['cloud_models']=CLOUD_MODELS
    try:result['compute']=await compute(hass,runtime,'/health',timeout=4)
    except Exception:result['compute']={'available':False}
    result['pipelines']=[{'id':p.id,'name':p.name,'tts_engine':p.tts_engine,'stt_engine':p.stt_engine} for p in pipelines.async_get_pipelines(hass) if p.conversation_engine==runtime['agent_id']]
    result['stt_engines']=speech_choices(hass) if hass is not None else []
    selected=next((p for p in pipelines.async_get_pipelines(hass) if p.conversation_engine==runtime['agent_id']),None)
    if selected:result.update(stt_engine=selected.stt_engine or '',stt_language=selected.stt_language or 'zh')
    return result


def remember(transaction,entry):
    from copy import deepcopy
    if entry.entry_id not in transaction['before'] and entry.entry_id not in transaction['created']:
        transaction['before'][entry.entry_id]=(entry,deepcopy(dict(entry.data)),deepcopy(dict(entry.options)),{k:deepcopy(dict(s.data)) for k,s in entry.subentries.items()})


async def _save(hass,runtime,entry,data,transaction):
    values=validate_settings(data,runtime['settings'])
    current=runtime['settings'];candidate={**current,**{k:v for k,v in values.items() if k in set(PUBLIC_DEFAULTS)|{'router_url','assistant_prompt','memory_instructions','llm_base_url'}}}
    selected=next((p for p in pipelines.async_get_pipelines(hass) if p.conversation_engine==runtime['agent_id']),None)
    speech_current={'stt_engine':selected.stt_engine or '', 'stt_language':selected.stt_language or 'zh'} if selected else {}
    speech_updates=speech_fields({**speech_current,**values},speech_choices(hass)) if {'stt_engine','stt_language'}&values.keys() else {}
    target=dict(runtime,settings=candidate)
    health=await compute(hass,target,'/health')
    if candidate.get('tts_source','cloud')=='local':
        model=candidate.get('local_tts_model','kokoro_fp32')
        if model=='piper':
            if not hass.states.get('tts.piper'):raise ValueError('请先安装并配置官方 Piper。')
        else:
            catalog=await compute(hass,target,'/models')
            if not any(m['id']==model and m['installed'] for m in catalog['models']):
                await compute(hass,target,'/models/download',{'model':model})
                raise ValueError('已开始下载所选模型。下载完成后，再点“保存全部设置”。')
    active_entry,active_sub=llm_config(hass,runtime)
    active_url=str(active_entry.data.get('base_url','')).rstrip('/') if active_entry else ''
    provider=values.get('llm_provider',providers.identify(active_url) if active_entry else 'deepseek')
    url=providers.base_url(provider,values.get('llm_base_url',active_url))
    bindings=dict(current.get('llm_profiles',{}))
    if active_entry:bindings[active_url]=active_entry.entry_id
    llm_entry=providers.find_entry(hass,url,bindings.values())
    sub=(active_sub if active_entry is llm_entry else next((s for s in llm_entry.subentries.values() if s.subentry_type=='conversation'),None)) if llm_entry else None
    llm_key=values.get('llm_api_key',values.get('deepseek_api_key')) or (llm_entry.data.get('api_key','') if llm_entry else '')
    model=values.get('llm_model',values.get('deepseek_model',sub.data.get('chat_model','') if sub else ''))
    changing=bool(llm_key) and (llm_entry is not active_entry or values.get('llm_api_key') or values.get('deepseek_api_key') or (sub and model!=sub.data.get('chat_model')))
    if active_entry and url!=active_url and not llm_key:
        raise ValueError('请填写所选服务商的 API；不会向新服务商发送原来的密钥。')
    if llm_key and not model:raise ValueError('请填写或读取并选择模型名称。')
    if changing or (llm_key and not llm_entry):await providers.validate_model(hass,provider,url,llm_key,model)
    candidate['llm_provider']=provider;candidate['llm_base_url']=url
    cloud_entry,cloud=cloud_config(hass)
    cloud_changes=any(k in values and values[k]!=cloud.get({'cloud_host':'host','cloud_api_key':'api_key','cloud_voice':'voice','cloud_model':'model'}[k]) for k in ('cloud_host','cloud_api_key','cloud_voice','cloud_model'))
    cloud_candidate={**cloud,**{dest:values[src] for src,dest in [('cloud_host','host'),('cloud_api_key','api_key'),('cloud_voice','voice'),('cloud_model','model')] if src in values},'fallback_engine':'tts.piper'}
    from ..qwen_cloud_tts.client import validate_host
    if values.get('cloud_host'):
        cloud_candidate['host']=validate_host(values['cloud_host'])
        if cloud.get('host') and cloud_candidate['host']!=cloud['host'] and not values.get('cloud_api_key'):
            raise ValueError('更换云端语音地址时，请填写该地址对应的 API，不会共用旧地址的密钥。')
    if candidate.get('tts_source','cloud')=='cloud' and not cloud_candidate.get('api_key'):raise ValueError('请填写云端语音 API。')
    if cloud_changes and cloud_candidate.get('api_key'):
        from ..qwen_cloud_tts.config_flow import validate
        try:cloud_candidate=await validate(hass,cloud_candidate)
        except ValueError as error:
            if str(error)=='legacy_endpoint_required':raise ValueError('普通千问3 Flash 需要传统 DashScope Host 及其对应 API。当前工作空间请选择 Realtime、Audio Flash 或 Audio Plus。') from None
            raise ValueError('语音模型或音色不匹配，请重新选择。') from None
        except Exception:raise ValueError('云端语音 API 验证失败，请检查地址、密钥、模型权限和网络。') from None
    # All remote/model validations precede persistent changes.
    if llm_key:
        if not llm_entry:
            llm_entry=await providers.create_entry(hass,provider,url,llm_key,model)
            transaction['created'].append(llm_entry.entry_id)
            sub=next(iter(llm_entry.subentries.values()))
        else:
            if llm_key!=llm_entry.data.get('api_key'):
                remember(transaction,llm_entry)
                hass.config_entries.async_update_entry(llm_entry,data={**llm_entry.data,'api_key':llm_key,'streaming':True})
            sub_data={**sub.data,'chat_model':model}
            if 'router_lab_memory' not in (sub_data.get('llm_hass_api') or []):sub_data['llm_hass_api']=list(sub_data.get('llm_hass_api') or [])+['router_lab_memory']
            if sub_data!=dict(sub.data):
                remember(transaction,llm_entry)
                hass.config_entries.async_update_subentry(llm_entry,sub,data=sub_data)
        entities=er.async_entries_for_config_entry(er.async_get(hass),llm_entry.entry_id)
        agent=next((e.entity_id for e in entities if e.domain=='conversation' and e.config_subentry_id==sub.subentry_id),None)
        if not agent:raise ValueError('对话代理正在初始化，请稍后重试。')
        candidate['llm_agent']=agent
        bindings[url]=llm_entry.entry_id
    candidate['llm_profiles']=bindings
    if cloud_changes and cloud_candidate.get('api_key'):
        if cloud_entry:
            remember(transaction,cloud_entry)
            hass.config_entries.async_update_entry(cloud_entry,data=cloud_candidate,options={})
        else:
            result=await hass.config_entries.flow.async_init('qwen_cloud_tts',context={'source':'user'},data=cloud_candidate)
            if result.get('type')!='create_entry':raise ValueError('无法创建云端语音服务。')
            transaction['created'].append(result['result'].entry_id)
            for _ in range(100):
                if hass.states.get('tts.qwen_cloud_tts'):break
                await asyncio.sleep(.2)
        from homeassistant.components import tts
        for _ in range(100):
            cloud_engine=tts.get_engine_instance(hass,'tts.qwen_cloud_tts')
            if cloud_engine and getattr(cloud_engine,'settings',{}).get('model','qwen3-tts-flash-realtime')==cloud_candidate.get('model','qwen3-tts-flash-realtime'):break
            await asyncio.sleep(.1)
        else:raise ValueError('云端语音配置正在加载，请稍后重试。')
    source=candidate.get('tts_source','cloud');local_model=candidate.get('local_tts_model','kokoro_fp32')
    engine='tts.qwen_cloud_tts' if source=='cloud' else 'tts.piper' if local_model=='piper' else 'tts.router_lab_local'
    language='zh_CN' if engine=='tts.piper' else 'zh-CN'
    voice=cloud_candidate.get('voice','Cherry') if source=='cloud' else 'zh_CN-huayan-medium' if local_model=='piper' else candidate.get('local_tts_voice','zf_001')
    matched=[p for p in pipelines.async_get_pipelines(hass) if p.conversation_engine==runtime['agent_id']]
    # Updating runtime first makes the entry listener a hot update; no virtual-device reset.
    previous=runtime['settings'];transaction['runtime']=dict(previous);transaction['pipelines']=matched
    runtime['settings']=candidate
    try:
        for pipeline in matched:await pipelines.async_update_pipeline(hass,pipeline,tts_engine=engine,tts_language=language,tts_voice=voice,**speech_updates)
    except Exception:
        runtime['settings']=previous
        raise ValueError('语音助手切换失败，请重试。') from None
    remember(transaction,entry)
    hass.config_entries.async_update_entry(entry,options={**entry.options,**{k:v for k,v in candidate.items() if k not in ('deepseek_api_key','cloud_api_key','llm_api_key')}})
    runtime['learning']._schedule()
    return await status(hass,runtime)


async def save(hass,runtime,entry,data):
    """Restore entry/pipeline settings if any later commit step fails."""
    transaction={'before':{},'created':[],'pipelines':[]}
    try:return await _save(hass,runtime,entry,data,transaction)
    except Exception:
        if 'runtime' in transaction:runtime['settings']=transaction['runtime']
        for e,entry_data,options,subs in transaction['before'].values():
            if dict(e.data)!=entry_data or dict(e.options)!=options:hass.config_entries.async_update_entry(e,data=entry_data,options=options)
            for key,sub_data in subs.items():
                if key in e.subentries and dict(e.subentries[key].data)!=sub_data:hass.config_entries.async_update_subentry(e,e.subentries[key],data=sub_data)
        for entry_id in transaction['created']:
            await hass.config_entries.async_remove(entry_id)
        current_pipelines={p.id:p for p in pipelines.async_get_pipelines(hass)}
        for p in transaction['pipelines']:
            live=current_pipelines.get(p.id)
            fields=('tts_engine','tts_language','tts_voice','stt_engine','stt_language')
            original={key:getattr(p,key,None) for key in fields}
            if live and any(getattr(live,key,None)!=value for key,value in original.items()):
                await pipelines.async_update_pipeline(hass,live,**original)
        raise


def register(hass):
    lock=asyncio.Lock()
    @websocket_api.require_admin
    @websocket_api.websocket_command({vol.Required('type'):'router_lab/settings',vol.Optional('operation',default='status'):vol.In(['status','save','models','download','delete_model','model_source','llm_profile','llm_models']),vol.Optional('data'):dict,vol.Optional('model'):str})
    @websocket_api.async_response
    async def handle(hass,connection,msg):
        runtime=next(iter(hass.data.get(DOMAIN,{}).values()),None)
        if runtime is None:connection.send_error(msg['id'],'not_ready','路由测试台尚未启动。');return
        try:
            operation=msg['operation']
            if operation=='status':result=await status(hass,runtime)
            elif operation=='models':result=await compute(hass,runtime,'/models')
            elif operation=='download':result=await compute(hass,runtime,'/models/download',{'model':msg.get('model')})
            elif operation=='model_source':result=await compute(hass,runtime,'/models/source',{'url':msg.get('data',{}).get('router_model_url','')})
            elif operation=='delete_model':
                name=msg.get('model')
                if runtime['settings'].get('tts_source')=='local' and name==runtime['settings'].get('local_tts_model'):
                    raise ValueError('请先切换到云端或其他本地模型，再删除正在使用的模型。')
                result=await compute(hass,runtime,'/models/delete',{'model':name})
            elif operation in ('llm_profile','llm_models'):
                data=msg.get('data',{});provider=data.get('llm_provider','deepseek');url=data.get('llm_base_url','')
                active,_=llm_config(hass,runtime);allowed=list(runtime['settings'].get('llm_profiles',{}).values())
                if active:allowed.append(active.entry_id)
                if operation=='llm_profile':result=providers.profile(hass,provider,url,allowed)
                else:result=await providers.list_models(hass,provider,url,data.get('llm_api_key',''),allowed)
            else:
                entry=next(iter(hass.config_entries.async_entries(DOMAIN)))
                async with lock:result=await save(hass,runtime,entry,msg.get('data',{}))
        except ValueError as error:connection.send_error(msg['id'],'invalid_settings',str(error))
        except Exception:connection.send_error(msg['id'],'settings_failed','操作没有完成，请检查网络和应用状态；原有密钥不会显示在此处。')
        else:connection.send_result(msg['id'],result)
    websocket_api.async_register_command(hass,handle)
