from collections import deque
import probatio as vol
from homeassistant.const import Platform
from homeassistant.core import SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.components import conversation as ha_conversation
from .const import DOMAIN
from .catalog import ENVIRONMENT_DEFAULTS
from .memory import HomeMemory
from .memory_api import MemoryAPI
from homeassistant.helpers import llm, entity_registry as er
from homeassistant.exceptions import Unauthorized
from .diagnostics import benchmark,capabilities
from pathlib import Path
from homeassistant.components.http import StaticPathConfig

PLATFORMS = [Platform.CONVERSATION, Platform.SENSOR, Platform.LIGHT, Platform.CLIMATE, Platform.COVER,
             Platform.FAN, Platform.SWITCH, Platform.MEDIA_PLAYER, Platform.HUMIDIFIER,
             Platform.BINARY_SENSOR, Platform.NUMBER, Platform.TTS]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass, config):
    await hass.http.async_register_static_paths([StaticPathConfig('/router_lab_ui',str(Path(__file__).parent/'frontend'),False)])
    from .unified_settings import register
    register(hass)
    async def run_test(call):
        entries = hass.data.get(DOMAIN, {})
        if not entries:
            raise ValueError('请先添加路由测试台集成')
        runtime = next(iter(entries.values()))
        result = await ha_conversation.async_converse(
            hass, call.data['text'], call.data.get('conversation_id'), call.context,
            language=call.data.get('language', 'zh-CN'), agent_id=runtime['agent_id'])
        return {'conversation': result.as_dict(), 'trace': runtime['history'][-1]}

    async def reset(call):
        for runtime in hass.data.get(DOMAIN, {}).values():
            for device in runtime['devices']:
                device.reset()

    def memory_runtime():
        return next(iter(hass.data[DOMAIN].values()))

    async def memory_manage(call):
        memory=memory_runtime()['memory']
        return await memory.manage(memory.owner(call.context),**call.data)

    async def memory_backup(call):
        user=await hass.auth.async_get_user(call.context.user_id) if call.context.user_id else None
        if not user or not user.is_admin:
            raise Unauthorized()
        from .habit_backup import manage_backup
        return await manage_backup(memory_runtime()['memory'],**call.data)

    async def home_preview(call):
        memory=memory_runtime()['memory']
        env=memory.environment()
        env.update(call.data)
        return await memory.run_home(memory.owner(call.context),{},call.context,preview=True,environment=env)

    async def scene_preview(call):
        memory=memory_runtime()['memory']
        env=memory.environment();env.update({k:v for k,v in call.data.items() if k!='scene'})
        return await memory.run_scene(memory.owner(call.context),{},call.context,call.data['scene'],preview=True,environment=env)

    async def enable_memory(call):
        user=await hass.auth.async_get_user(call.context.user_id) if call.context.user_id else None
        if not user or not user.is_admin:
            raise Unauthorized()
        runtime=memory_runtime()
        entity=er.async_get(hass).async_get(runtime['settings'].get('llm_agent',''))
        if not entity or not entity.config_entry_id or not entity.config_subentry_id:
            raise ValueError('没有找到支持工具配置的DeepSeek对话代理。')
        entry=hass.config_entries.async_get_entry(entity.config_entry_id)
        subentry=entry.subentries[entity.config_subentry_id]
        apis=list(subentry.data.get('llm_hass_api') or [])
        if 'router_lab_memory' not in apis:
            apis.append('router_lab_memory')
            hass.config_entries.async_update_subentry(entry,subentry,data={**subentry.data,'llm_hass_api':apis})
        return {'memory_tools_enabled':True,'agent_id':entity.entity_id,'apis':apis}

    async def diagnose(call):
        user=await hass.auth.async_get_user(call.context.user_id) if call.context.user_id else None
        if not user or not user.is_admin:raise Unauthorized()
        runtime=memory_runtime()
        if call.data['mode']=='capabilities':return capabilities(hass,runtime)
        return await benchmark(hass,runtime,call.data['mode'])

    async def speech_mode(call):
        runtime=memory_runtime()
        if 'mode' in call.data:
            user=await hass.auth.async_get_user(call.context.user_id) if call.context.user_id else None
            if not user or not user.is_admin:raise Unauthorized()
            entry=next(iter(hass.config_entries.async_entries(DOMAIN)))
            hass.config_entries.async_update_entry(entry,options={**entry.options,'speech_mode':call.data['mode']})
            runtime['settings']['speech_mode']=call.data['mode']
        return {'mode':runtime['settings'].get('speech_mode','fast')}

    async def route_mode(call):
        runtime=memory_runtime();learning=runtime['learning']
        owner=runtime['memory'].owner(call.context)
        updates={key:call.data[key] for key in ('routing_mode','idle_learning','learning_idle_seconds','learning_daily_limit') if key in call.data}
        operation=call.data.get('operation','status')
        if updates or operation!='status':
            user=await hass.auth.async_get_user(call.context.user_id) if call.context.user_id else None
            if not user or not user.is_admin:raise Unauthorized()
        if updates:
            entry=learning.entry
            hass.config_entries.async_update_entry(entry,options={**entry.options,**updates})
            runtime['settings'].update(updates)
            if not learning.setting('idle_learning',True) and learning.task and not learning.task.done():
                learning.task.cancel()
                try:await learning.task
                except __import__('asyncio').CancelledError:pass
            learning._schedule()
        return await learning.manage(owner,operation,call.data.get('rule_id'))

    hass.services.async_register(DOMAIN,'route_mode',route_mode,
        schema=vol.Schema({vol.Optional('routing_mode'):vol.In(['normal','economy']),
            vol.Optional('idle_learning'):bool,
            vol.Optional('learning_idle_seconds'):vol.All(int,vol.Range(min=30,max=3600)),
            vol.Optional('learning_daily_limit'):vol.All(int,vol.Range(min=1,max=20)),
            vol.Optional('operation',default='status'):vol.In(['status','enable','disable','retry']),
            vol.Optional('rule_id'):str}),supports_response=SupportsResponse.ONLY)

    hass.services.async_register(DOMAIN, 'test', run_test,
        schema=vol.Schema({vol.Required('text'): str, vol.Optional('language', default='zh-CN'): str,
                           vol.Optional('conversation_id'):str}),
        supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, 'reset', reset, schema=vol.Schema({}))
    hass.services.async_register(DOMAIN,'memory',memory_manage,
        schema=vol.Schema({vol.Required('operation'):vol.In(['list','save_rule','delete_rule','save_note','delete_note','accept_suggestion','reject_suggestion','move_rules']),
                           vol.Optional('rule'):dict,vol.Optional('temporary',default=False):bool,
                           vol.Optional('scene'):str,vol.Optional('ids'):[str],
                           vol.Optional('id'):str,vol.Optional('text'):str}),supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN,'preview_scene',scene_preview,
        schema=vol.Schema({vol.Required('scene'):str,
                           vol.Optional('hour'):vol.All(int,vol.Range(min=0,max=23)),
                           vol.Optional('daylight'):bool,vol.Optional('occupied'):bool,
                           vol.Optional('temperature'):vol.Coerce(float),vol.Optional('illuminance'):vol.Coerce(float)}),
        supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN,'preview_home',home_preview,
        schema=vol.Schema({vol.Optional('hour'):vol.All(int,vol.Range(min=0,max=23)),
                           vol.Optional('daylight'):bool,vol.Optional('occupied'):bool,
                           vol.Optional('temperature'):vol.Coerce(float),vol.Optional('illuminance'):vol.Coerce(float)}),
        supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN,'memory_backup',memory_backup,
        schema=vol.Schema({vol.Optional('operation',default='status'):vol.In(['create','download','status']),
                          vol.Optional('filename'):str}),supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN,'enable_memory',enable_memory,schema=vol.Schema({}),supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN,'diagnose_latency',diagnose,
        schema=vol.Schema({vol.Required('mode'):vol.In(['capabilities','stream','non_stream','stream_fast','non_stream_fast'])}),supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN,'speech_mode',speech_mode,
        schema=vol.Schema({vol.Optional('mode'):vol.In(['natural','fast'])}),supports_response=SupportsResponse.ONLY)
    return True


async def async_setup_entry(hass, entry):
    memory=HomeMemory(hass,entry)
    await memory.load()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        'settings': {**entry.data, **entry.options}, 'history': deque(maxlen=20),
        'devices': [], 'agent_id': None, 'environment': dict(ENVIRONMENT_DEFAULTS),'memory':memory}
    from .route_learning import RouteLearning
    runtime=hass.data[DOMAIN][entry.entry_id]
    runtime['learning']=RouteLearning(hass,entry,runtime)
    await runtime['learning'].load()
    entry.async_on_unload(llm.async_register_api(hass,MemoryAPI(hass,memory)))
    entry.async_on_unload(entry.add_update_listener(update_options))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    from homeassistant.components.assist_pipeline import pipeline as pipelines
    if not any(p.conversation_engine==runtime['agent_id'] for p in pipelines.async_get_pipelines(hass)):
        await hass.data[pipelines.KEY_ASSIST_PIPELINE].pipeline_store.async_create_item({
            'name':'家庭路由助手','language':'zh-CN','conversation_engine':runtime['agent_id'],
            'conversation_language':'zh-CN','prefer_local_intents':False,
            'stt_engine':None,'stt_language':None,'tts_engine':None,'tts_language':None,
            'tts_voice':None,'wake_word_entity':None,'wake_word_id':None})
    runtime['learning']._schedule()
    return True


async def update_options(hass, entry):
    runtime=hass.data[DOMAIN][entry.entry_id]
    updated={**entry.data,**entry.options}
    old=runtime['settings']
    if all(old.get(key)==updated.get(key) for key in set(old)|set(updated)
           if key not in ('assistant_prompt','memory_instructions','fast_response','speech_mode',
                          'routing_mode','idle_learning','learning_idle_seconds','learning_daily_limit')):
        runtime['settings']=updated
        runtime['learning']._schedule()
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    await hass.data[DOMAIN][entry.entry_id]['learning'].close()
    success = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if success:
        hass.data[DOMAIN].pop(entry.entry_id)
    return success
