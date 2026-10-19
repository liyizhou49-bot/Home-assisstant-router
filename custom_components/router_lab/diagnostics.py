"""Admin-only API latency probe reusing HA's configured client; never exports keys."""
from time import perf_counter
from homeassistant.components import tts
from homeassistant.helpers import entity_registry as er


def configured_client(hass,runtime):
    entity=er.async_get(hass).async_get(runtime['settings']['llm_agent'])
    entry=hass.config_entries.async_get_entry(entity.config_entry_id)
    subentry=entry.subentries[entity.config_subentry_id]
    return entry,subentry


async def benchmark(hass,runtime,mode):
    from .deepseek_client import ensure_adapter
    ensure_adapter(hass,runtime['settings'].get('llm_agent',''))
    entry,subentry=configured_client(hass,runtime)
    model=subentry.data['chat_model']
    client=entry.runtime_data
    messages=[{'role':'system','content':'请用中文回答，不要思考过程，严格遵守字数。'},
              {'role':'user','content':'请用100到150字说明夜间使用柔和灯光的好处。'}]
    params={'extra_body':{'thinking':{'type':'disabled'}}} if mode.endswith('_fast') else {}
    started=perf_counter();first=None;first_event=None;chunks=0;chars=0;usage=None
    if mode.startswith('stream'):
        stream=await client.chat.completions.create(model=model,messages=messages,stream=True,
            stream_options={'include_usage':True},max_tokens=1024,**params)
        async with stream:
            async for chunk in stream:
                if first_event is None:first_event=round((perf_counter()-started)*1000,2)
                if chunk.usage:usage=chunk.usage.model_dump()
                if chunk.choices and (text:=chunk.choices[0].delta.content):
                    if first is None:first=round((perf_counter()-started)*1000,2)
                    chunks+=1;chars+=len(text)
    else:
        result=await client.chat.completions.create(model=model,messages=messages,stream=False,max_tokens=1024,**params)
        first=round((perf_counter()-started)*1000,2)
        chars=len(result.choices[0].message.content or '')
        usage=result.usage.model_dump() if result.usage else None
    return {'mode':mode,'model':model,'first_event_ms':first_event,'first_text_ms':first,
            'total_ms':round((perf_counter()-started)*1000,2),'text_chunks':chunks,'output_chars':chars,'usage':usage,
            'scope':'direct HA-hosted API call with short synthetic prompt, no tools; not an equivalent control workload'}


def capabilities(hass,runtime):
    entry,subentry=configured_client(hass,runtime)
    engines=[]
    for state in hass.states.async_all('tts'):
        engine=tts.get_engine_instance(hass,state.entity_id)
        engines.append({'entity_id':state.entity_id,'streaming_input':engine.async_supports_streaming_input(),
                        'languages':engine.supported_languages,
                        'voices':{lang:[v.voice_id for v in (engine.async_get_supported_voices(lang) or [])]
                                  for lang in engine.supported_languages if lang.startswith('zh')}})
    return {'model':subentry.data.get('chat_model'),'api_streaming_enabled':bool(entry.data.get('streaming',False)),
            'tts':engines}
