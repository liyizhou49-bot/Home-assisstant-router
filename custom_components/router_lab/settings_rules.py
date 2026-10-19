"""Pure validation: API keys are write-only and blank means keep saved key."""
from urllib.parse import urlsplit

PUBLIC_DEFAULTS = {'routing_mode':'normal','model':'hybrid_v4','fast_response':True,
    'idle_learning':True,'learning_idle_seconds':90,'learning_daily_limit':3,
    'speech_mode':'fast','tts_source':'cloud','local_tts_model':'kokoro_fp32',
    'local_tts_voice':'zf_001','tts_threads':2,'cloud_voice':'Cherry','llm_provider':'deepseek',
    'cloud_model':'qwen3-tts-flash-realtime','stt_engine':'','stt_language':'zh'}


def validate_settings(data,current):
    if not isinstance(data,dict):raise ValueError('设置格式不正确。')
    allowed=set(PUBLIC_DEFAULTS)|{'assistant_prompt','memory_instructions','router_url','deepseek_model','deepseek_api_key','cloud_api_key','cloud_host','llm_model','llm_api_key','llm_base_url'}
    if set(data)-allowed:raise ValueError('存在不支持的设置。')
    result={k:v for k,v in data.items() if k in allowed}
    for key,choices in {'routing_mode':('normal','economy'),'model':('hybrid_v4','binary_v1'),
        'speech_mode':('natural','fast'),'tts_source':('local','cloud'),
        'local_tts_model':('piper','kokoro_fp32','kokoro_fp16'),
        'local_tts_voice':('zf_001','zf_003','zf_017','zf_094','zm_010','zm_031','zm_069','zm_089'),
        'llm_provider':('deepseek','openai','gemini','kimi','custom')}.items():
        if key in result and result[key] not in choices:raise ValueError('请选择有效的模式、模型或音色。')
    for key,lo,hi in [('learning_idle_seconds',30,3600),('learning_daily_limit',1,20),('tts_threads',1,4)]:
        if key in result and (type(result[key])!=int or not lo<=result[key]<=hi):raise ValueError('线程数或空闲研究次数超出允许范围。')
    for key in ('idle_learning','fast_response'):
        if key in result and type(result[key])!=bool:raise ValueError('开关设置不正确。')
    for key in ('assistant_prompt','memory_instructions','deepseek_model','deepseek_api_key','cloud_api_key','cloud_host','router_url','llm_model','llm_api_key','llm_base_url','cloud_voice','cloud_model','stt_engine','stt_language'):
        if key in result and (not isinstance(result[key],str) or len(result[key])>12000):raise ValueError('设置文本过长或格式不正确。')
    if 'router_url' in result:
        url=urlsplit(result['router_url'])
        if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('','/'):
            raise ValueError('计算服务地址不正确。')
        result['router_url']=result['router_url'].rstrip('/')
    if 'deepseek_model' in result and (not result['deepseek_model'].strip() or len(result['deepseek_model'])>120):raise ValueError('DeepSeek 模型名称不正确。')
    if 'llm_model' in result and len(result['llm_model'])>150:raise ValueError('模型名称过长。')
    import re
    if 'stt_engine' in result and result['stt_engine'] and not re.fullmatch(r'stt\.[a-z0-9_]{1,120}',result['stt_engine']):raise ValueError('语音识别引擎不正确。')
    if 'stt_language' in result and not re.fullmatch(r'[a-zA-Z]{2,3}(?:[-_][a-zA-Z0-9]{2,8}){0,2}',result['stt_language']):raise ValueError('语音识别语言不正确。')
    for key in ('deepseek_api_key','cloud_api_key','llm_api_key'):
        if key in result:
            result[key]=result[key].strip()
            if not result[key]:result.pop(key)
    return result
