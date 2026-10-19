"""Read timestamps from this exact native Assist run; never record audio."""
import asyncio
from datetime import datetime


def summarize(events,text,conversation_id):
    by={str(event.type):event for event in events}
    intent=by.get('intent-start')
    if not intent or intent.data.get('intent_input')!=text or intent.data.get('conversation_id')!=conversation_id:
        return None
    if 'stt-start' not in by:return {'source':'text'}
    end=by.get('stt-end')
    if not end or end.data.get('stt_output',{}).get('text')!=text:return None
    result={'source':'voice','engine':by['stt-start'].data.get('engine',''),
            'recognized_at':end.timestamp,'basis':'speech_end_detected_to_text'}
    def duration(start,finish):
        def parsed(value):return datetime.fromisoformat(value) if isinstance(value,str) else value
        value=(parsed(finish.timestamp)-parsed(start.timestamp)).total_seconds()*1000
        return round(value,2) if value>=0 else None
    result['capture_ms']=duration(by['stt-start'],end)
    if 'stt-vad-end' in by:result['recognition_ms']=duration(by['stt-vad-end'],end)
    return result


def current_run(hass,user_input,agent_id):
    """Active context + pipeline + conversation + text prevent stale/cross-user matches.

    Native run metadata is read-only. If a future HA version changes this internal
    structure, timing becomes unavailable; routing and speech keep working.
    """
    try:
        from homeassistant.components.assist_pipeline.pipeline import KEY_ASSIST_PIPELINE
        data=hass.data[KEY_ASSIST_PIPELINE]
        for pipeline_id,runs in data.pipeline_runs._pipeline_runs.items():
            for run_id,run in runs.items():
                if run.context.id!=user_input.context.id or run.context.user_id!=user_input.context.user_id:continue
                if run.pipeline.conversation_engine!=agent_id:continue
                debug=data.pipeline_debug.get(pipeline_id,{}).get(run_id)
                if debug:
                    result=summarize(debug.events,user_input.text,user_input.conversation_id)
                    if result is not None:return dict(result,pipeline_run_id=run_id)
    except (AttributeError,KeyError,TypeError,ValueError,ImportError):pass
    return {'source':'not_recorded'}


def model_label(options):
    library=options.get('stt_library');model=options.get('model')
    if library=='funasr' and model=='auto' and options.get('language')=='zh':return '正常 · SenseVoice 中文'
    if library=='faster-whisper' and model=='base-int8' and options.get('beam_size')==1:return '极速 · Whisper Base INT8'
    return '自定义识别配置'


async def describe(hass,measurement):
    if measurement.get('source')!='voice':return measurement
    result=dict(measurement)
    state=hass.states.get(result.get('engine',''))
    result['engine_name']=state.name if state else result.get('engine','')
    if result.get('engine')=='stt.faster_whisper':
        try:
            from homeassistant.components.hassio import get_supervisor_client
            async with asyncio.timeout(.5):
                info=await get_supervisor_client(hass).addons.addon_info('core_whisper')
            result['mode']=model_label(info.options)
        except Exception:result['mode']='Whisper · 模式未读取'
    return result
