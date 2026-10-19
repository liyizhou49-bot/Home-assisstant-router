"""OpenAI-compatible providers; credentials remain in separate native HA entries."""
from urllib.parse import urlsplit

PROVIDERS={
    'deepseek':{'name':'DeepSeek','base_url':'https://api.deepseek.com','example_model':'deepseek-flash'},
    'openai':{'name':'OpenAI · GPT','base_url':'https://api.openai.com/v1','example_model':'gpt-4.1-mini'},
    'gemini':{'name':'Google · Gemini','base_url':'https://generativelanguage.googleapis.com/v1beta/openai','example_model':'gemini-3.8-flash'},
    'kimi':{'name':'Moonshot · Kimi','base_url':'https://api.moonshot.cn/v1','example_model':'kimi-k2.6'},
    'custom':{'name':'自定义 OpenAI 兼容接口','base_url':'','example_model':'填写模型 ID'},
}


def base_url(provider,value=''):
    if provider not in PROVIDERS:raise ValueError('请选择支持的服务商。')
    value=(value if provider=='custom' else PROVIDERS[provider]['base_url']).strip().rstrip('/')
    url=urlsplit(value)
    if url.scheme not in ('https','http') or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('请填写不含密钥的 API Base URL。')
    return value


def identify(url):
    normalized=str(url).rstrip('/')
    return next((key for key,meta in PROVIDERS.items() if key!='custom' and normalized==meta['base_url']),'custom')


def find_entry(hass,url,entry_ids=()):
    normalized=url.rstrip('/')
    return next((e for e in hass.config_entries.async_entries('llama_cpp') if str(e.data.get('base_url','')).rstrip('/')==normalized
        and (str(getattr(e,'unique_id','')).startswith('router_lab.provider.') or getattr(e,'entry_id',None) in entry_ids)),None)


def profile(hass,provider,url='',entry_ids=()):
    target=base_url(provider,url);entry=find_entry(hass,target,entry_ids)
    sub=next((s for s in entry.subentries.values() if s.subentry_type=='conversation'),None) if entry else None
    return {'llm_provider':provider,'llm_base_url':target,'llm_model':sub.data.get('chat_model','') if sub else '',
            'llm_configured':bool(entry and entry.data.get('api_key'))}


async def list_models(hass,provider,url='',key='',entry_ids=()):
    target=base_url(provider,url);entry=find_entry(hass,target,entry_ids)
    key=key.strip() or (entry.data.get('api_key','') if entry else '')
    if not key:raise ValueError('请填写此服务商的 API。不同地址不会共用密钥。')
    from homeassistant.setup import async_setup_component
    if not await async_setup_component(hass,'llama_cpp',{}):raise ValueError('无法加载 HA 对话集成。')
    from openai import AsyncOpenAI
    try:
        async with AsyncOpenAI(api_key=key,base_url=target,timeout=25,max_retries=0) as client:
            response=await client.models.list()
            ids=sorted({m.id for m in response.data if isinstance(m.id,str) and len(m.id)<=150})
    except Exception:raise ValueError('读取模型失败，请检查此服务商的地址、API 和网络。') from None
    return {'models':ids,'llm_provider':provider,'llm_base_url':target}


def request_options(provider,model,kwargs):
    """Preserve provider boundaries; do not send DeepSeek's thinking field elsewhere."""
    result=dict(kwargs)
    if provider!='deepseek':
        body=dict(result.get('extra_body') or {});body.pop('thinking',None)
        if body:result['extra_body']=body
        else:result.pop('extra_body',None)
    if provider=='openai':
        if 'max_tokens' in result:result['max_completion_tokens']=result.pop('max_tokens')
        import re
        if re.match(r'^(?:o\d|gpt-(?:[5-9]|[1-9]\d))',model):
            result.pop('temperature',None);result.pop('top_p',None)
    if provider=='gemini':result.pop('user',None)
    if provider=='kimi':
        # HA does not retain reasoning_content in its conversation history.
        # K2.6 offers an official non-thinking mode; always-thinking models do not.
        if model.startswith(('kimi-k3','kimi-k2.7')) or 'thinking' in model:
            raise ValueError('当前 Kimi 接入支持非思考模式，请选择 kimi-k2.6；暂不支持强制思考的模型。')
        result.pop('temperature',None);result.pop('top_p',None)
        if model.startswith(('kimi-k2.5','kimi-k2.6')):
            result['extra_body']={**result.get('extra_body',{}),'thinking':{'type':'disabled'}}
    return result


async def validate_model(hass,provider,url,key,model):
    result=await list_models(hass,provider,url,key)
    if model not in result['models']:raise ValueError('模型不在此 API 可用的列表中，请读取模型列表后选择。')
    from openai import AsyncOpenAI
    from .deepseek_client import DeepSeekClientAdapter
    # Two turns prove both tool generation and returning its result, with no devices.
    try:
        async with AsyncOpenAI(api_key=key,base_url=url,timeout=30,max_retries=0) as client:
            adapter=DeepSeekClientAdapter(client)
            kwargs=request_options(provider,model,{'model':model,'messages':[{'role':'user','content':'调用 router_probe，无需其他回复。'}],
                'tools':[{'type':'function','function':{'name':'router_probe','description':'连接测试，不执行任何操作','parameters':{'type':'object','properties':{},'additionalProperties':False}}}],
                'tool_choice':{'type':'function','function':{'name':'router_probe'}},'max_tokens':1024,'stream':True,'user':'router_connection_probe'})
            if provider=='deepseek':kwargs['extra_body']={'thinking':{'type':'disabled'}}
            stream=await adapter.chat.completions.create(**kwargs);calls=[]
            async with stream:
                async for chunk in stream:
                    if chunk.choices:
                        for call in (chunk.choices[0].delta.tool_calls or []):
                            if call.function and call.function.name=='router_probe':
                                calls.append({'type':'function','id':call.id,'function':{'name':'router_probe','arguments':call.function.arguments or '{}'}})
            if not calls:raise ValueError('tool_protocol')
            kwargs['messages']+=[{'role':'assistant','content':None,'tool_calls':calls}]+[{'role':'tool','tool_call_id':c['id'],'content':'{"ok":true}'} for c in calls]
            kwargs['tool_choice']='none'
            second=await adapter.chat.completions.create(**kwargs);replied=False
            async with second:
                async for chunk in second:
                    if chunk.choices and chunk.choices[0].delta.content:replied=True
            if not replied:raise ValueError('tool_result_protocol')
    except Exception:raise ValueError('此模型未通过流式工具调用测试，请选择支持 Chat Completions 和工具调用的模型。') from None


async def create_entry(hass,provider,url,key,model):
    from types import MappingProxyType
    from homeassistant.config_entries import ConfigEntry
    import hashlib
    entry=ConfigEntry(domain='llama_cpp',title=PROVIDERS[provider]['name'],source='user',version=1,minor_version=1,
        data={'base_url':url,'api_key':key,'streaming':True},options={},discovery_keys=MappingProxyType({}),unique_id='router_lab.provider.'+hashlib.sha256(url.encode()).hexdigest()[:20],
        subentries_data=[{'subentry_type':'conversation','title':model,'unique_id':None,
            'data':{'chat_model':model,'recommended':True,'llm_hass_api':['assist','router_lab_memory']}}])
    try:
        await hass.config_entries.async_add(entry)
        if getattr(entry,'runtime_data',None) is None:raise ValueError('not_loaded')
    except Exception:
        if hass.config_entries.async_get_entry(entry.entry_id):await hass.config_entries.async_remove(entry.entry_id)
        raise ValueError('对话代理尚未初始化，请检查连接后重试。') from None
    return entry
