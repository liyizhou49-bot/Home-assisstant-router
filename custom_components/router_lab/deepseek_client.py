"""Request-scoped DeepSeek options for HA's compatible client.

The native integration does not expose provider-specific extra_body options.
This transparent adapter keeps its chat log, streaming and tool handling intact.
Outside this router's request context every call is forwarded unchanged.
"""
from contextvars import ContextVar
from time import perf_counter
from urllib.parse import urlsplit
from .providers import identify,request_options
from .tool_stream import ToolMetadata,ToolStream

API_REQUEST_MODE=ContextVar('router_lab_api_request_mode',default=None)


class _Completions:
    def __init__(self,original,provider='deepseek',cache=None):self.original=original;self.provider=provider;self.cache=cache or ToolMetadata()
    def __getattr__(self,name):return getattr(self.original,name)

    async def create(self,*args,**kwargs):
        scope=kwargs.get('user','');model=kwargs.get('model','')
        if self.provider=='gemini' and 'messages' in kwargs:
            kwargs['messages']=self.cache.restore(scope,model,kwargs['messages'])
        kwargs=request_options(self.provider,kwargs.get('model',''),kwargs)
        mode=API_REQUEST_MODE.get()
        if mode and mode.get('fast') and self.provider=='deepseek':
            body=dict(kwargs.get('extra_body') or {})
            body['thinking']={'type':'disabled'}
            kwargs['extra_body']=body
        started=perf_counter()
        result=await self.original.create(*args,**kwargs)
        if mode:
            stats=mode['stats']
            stats['api_requests']=stats.get('api_requests',0)+1
            stats.setdefault('api_headers_ms',[]).append(round((perf_counter()-started)*1000,2))
        if kwargs.get('stream') and kwargs.get('tools'):
            return ToolStream(result,self.cache,scope,model)
        return result


class _Chat:
    def __init__(self,original,provider='deepseek',cache=None):
        self.original=original
        self.completions=_Completions(original.completions,provider,cache)
    def __getattr__(self,name):return getattr(self.original,name)


class DeepSeekClientAdapter:
    def __init__(self,original,cache=None):
        self.original=original
        self.provider=identify(str(original.base_url))
        self.cache=cache or ToolMetadata()
        self.chat=_Chat(original.chat,self.provider,self.cache)
    def __getattr__(self,name):return getattr(self.original,name)

    def with_options(self,**kwargs):return type(self)(self.original.with_options(**kwargs),self.cache)


def ensure_adapter(hass,agent_id):
    from homeassistant.helpers import entity_registry as er
    entity=er.async_get(hass).async_get(agent_id)
    if not entity or not entity.config_entry_id:return False
    entry=hass.config_entries.async_get_entry(entity.config_entry_id)
    if entry.domain!='llama_cpp':return False
    client=entry.runtime_data
    if not isinstance(client,DeepSeekClientAdapter):
        entry.runtime_data=DeepSeekClientAdapter(client)
    return True
