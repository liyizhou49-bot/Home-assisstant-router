"""Update only the existing trace card; preserve the rest of the dashboard."""

SPEECH_FIELDS='''{% set rec = state_attr("sensor.router_lab_trace", "recognition") or {} %}
**本次识别模式：** {% if rec.get("source") == "voice" %}{{ rec.get("mode", rec.get("engine_name", "未记录")) }}{% elif rec.get("source") == "text" %}文字输入{% else %}尚未记录，请再说一句{% endif %}

**语音识别：** {% if rec.get("source") == "voice" and rec.get("recognition_ms") is number %}{{ (rec["recognition_ms"] / 1000) | round(2) }} 秒（检测说完后）{% elif rec.get("source") == "voice" %}未记录结束时间，无法精确计算{% elif rec.get("source") == "text" %}不适用 · 本次为文字输入{% else %}未记录{% endif %}

**录音到文字：** {% if rec.get("source") == "voice" and rec.get("capture_ms") is number %}{{ (rec["capture_ms"] / 1000) | round(2) }} 秒（含等待和说话）{% else %}—{% endif %}

'''


def upgrade(config):
    count=0
    def visit(value):
        nonlocal count
        if isinstance(value,dict):
            if value.get('type')=='markdown' and value.get('title')=='最近一次测试':
                text=value.get('content','')
                if '**语音识别：**' not in text:
                    marker='**耗时：**' if '**耗时：**' in text else '**文字处理：**'
                    if marker in text:
                        value['content']=text.replace(marker,SPEECH_FIELDS+'**文字处理：**',1);count+=1
            for child in value.values():visit(child)
        elif isinstance(value,list):
            for child in value:visit(child)
    visit(config)
    return count
