"""Select an installed native HA speech recognizer without changing the agent."""


def choices(hass):
    from homeassistant.components import stt
    result=[]
    for state in hass.states.async_all('stt'):
        engine=stt.async_get_speech_to_text_engine(hass,state.entity_id)
        if engine:
            result.append({'id':state.entity_id,'name':state.attributes.get('friendly_name',state.entity_id),
                           'languages':list(engine.supported_languages)})
    return result


def pipeline_fields(values,engines):
    if not {'stt_engine','stt_language'}&values.keys():return {}
    engine_id=values.get('stt_engine','')
    if not engine_id:return {'stt_engine':None,'stt_language':None}
    engine=next((item for item in engines if item['id']==engine_id),None)
    if not engine:raise ValueError('语音识别引擎尚未安装或已移除，请先安装并配置 Whisper。')
    language=values.get('stt_language','zh')
    if language not in engine['languages']:raise ValueError('此识别引擎不支持所选语言。请从可用语言中选择。')
    return {'stt_engine':engine_id,'stt_language':language}
