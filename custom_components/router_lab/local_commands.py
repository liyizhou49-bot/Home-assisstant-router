"""Finite HA capabilities and Hassil grammar shared by both routing modes.

Only metadata-backed actions are executable. Model proposals cannot introduce
services, Python, templates, wildcard slots, or new device capabilities.
"""
import hashlib
import math
import re
from functools import lru_cache

from hassil import Intents, recognize_all

UNSAFE = re.compile(r'不要|不用|别|不许|取消|如果|假如|当.+就|时候|之后|之前|分钟后|小时后|明天|每天|定时|记住|以后|习惯|偏好|忘|为什么|为何|怎么|如何|是否|有没有|了吗|了没|多少|几度|查询|查看|[？?]')
META = re.compile(r'[\[\]()<>|;{}\\\n\r]')
LABELS = {'on':'打开','off':'关闭','brightness':'亮度','temperature':'温度',
          'position':'位置','percentage':'风速','volume':'音量','humidity':'湿度',
          'color_temperature':'色温','number':'数值'}


def actionable(text):
    return isinstance(text,str) and 0 < len(text) <= 240 and not UNSAFE.search(text)


def capabilities(states, allowed=None):
    result=[]
    for state in states:
        entity=state['entity_id'];domain=entity.split('.')[0];attrs=state.get('attributes',{})
        name=attrs.get('friendly_name') or entity
        if allowed is not None and entity not in allowed:
            continue
        if 'simulate_' in entity or META.search(name) or len(name)>60 or state.get('state') in ('unavailable','unknown'):
            continue
        def add(action,service,param=None,low=None,high=None,unit='',fixed=None,command=None):
            cap={'id':entity+'|'+action,'entity_id':entity,'name':name,'action':action,
                 'domain':domain,'service':service,'param':param,'min':low,'max':high,
                 'unit':unit,'fixed':fixed or {},'command':command or LABELS.get(action,action)+name,
                 'state':state['state']}
            cap['step']=attrs.get('step',1) if action=='number' else attrs.get('target_temp_step',.5) if action=='temperature' else 1
            cap['signature']=hashlib.sha256(str((name,domain,service,param,low,high,cap['step'],sorted(cap['fixed'].items()))).encode()).hexdigest()[:16]
            result.append(cap)
        if domain in ('light','switch','fan','climate','media_player','humidifier'):
            add('on','turn_on',command='打开'+name)
            add('off','turn_off',command='关闭'+name)
        if domain=='light':
            modes=attrs.get('supported_color_modes',[])
            if modes and modes!=['onoff']:
                add('brightness','turn_on','brightness_pct',0,100,'%',command='设置'+name+'亮度为{value}%')
            if 'color_temp' in modes:
                add('color_temperature','turn_on','color_temp_kelvin',
                    attrs.get('min_color_temp_kelvin',2000),attrs.get('max_color_temp_kelvin',6500),'K',
                    command='设置'+name+'色温为{value}K')
        elif domain=='climate':
            if attrs.get('temperature') is not None:
                add('temperature','set_temperature','temperature',attrs.get('min_temp',16),attrs.get('max_temp',30),'℃',
                    command='设置'+name+'温度为{value}度')
            for mode in attrs.get('hvac_modes',[]):
                if mode not in ('off','cool','heat','auto','dry','fan_only','heat_cool'):continue
                title={'off':'关闭','cool':'制冷','heat':'制热','auto':'自动','dry':'除湿','fan_only':'送风','heat_cool':'冷热自动'}[mode]
                add('mode:'+mode,'set_hvac_mode',fixed={'hvac_mode':mode},command='设置'+name+'模式为'+title)
        elif domain=='cover':
            add('open','open_cover',command='打开'+name);add('close','close_cover',command='关闭'+name)
            if 'current_position' in attrs:
                add('position','set_cover_position','position',0,100,'%',command='设置'+name+'位置为{value}%')
        elif domain=='fan' and 'percentage' in attrs:
            add('percentage','set_percentage','percentage',0,100,'%',command='设置'+name+'风速为{value}%')
        elif domain=='media_player' and ('volume_level' in attrs or int(attrs.get('supported_features',0)) & 4):
            add('volume','volume_set','volume_level',0,100,'%',command='设置'+name+'音量为{value}%')
        elif domain=='humidifier' and 'humidity' in attrs:
            add('humidity','set_humidity','humidity',attrs.get('min_humidity',30),attrs.get('max_humidity',80),'%',
                command='设置'+name+'湿度为{value}%')
        elif domain=='number':
            low,high=attrs.get('min'),attrs.get('max')
            if isinstance(low,(float,int)) and isinstance(high,(float,int)):
                add('number','set_value','value',low,high,attrs.get('unit_of_measurement',''),
                    command='设置'+name+'为{value}'+attrs.get('unit_of_measurement',''))
    return result


def public(cap):
    return {k:cap[k] for k in ('id','name','command','min','max','step','unit','state')}


def select_caps(caps,text,limit=18,exchanges=()):
    words=['空调','灯','电视','窗帘','风扇','加湿','音箱','插座','温度','音量','色温','亮度','力度','热水','咖啡','洗衣','净化']
    rooms=['客厅','卧室','书房','玄关','厨房','卫生间','阳台','餐厅','客卧']
    mentioned=[word for word in rooms if word in text]
    # A short answer may omit the device named in the previous question.
    # Keep retrieval bounded; explicit current devices always take precedence.
    context=''
    if not any(cap['name'] in text for cap in caps):
        context=' '.join(str(item.get(key,''))[-240:] for item in exchanges[-2:] for key in ('user','assistant'))
    def score(cap):
        name=cap['name']
        points=20 if name in text else 0
        points+=sum(4 for word in words if word in text and word in name)
        points+=sum(6 for word in mentioned if word in name)
        if mentioned and not any(word in name for word in mentioned):points-=12
        action=cap['action']
        if any(w in text for w in ('凉','热','冷','温度')) and action=='temperature':points+=8
        if any(w in text for w in ('亮','暗','光')) and action=='brightness':points+=8
        if any(w in text for w in ('声音','音量','吵')) and action=='volume':points+=8
        if any(w in text for w in ('色温','暖光','冷光')) and action=='color_temperature':points+=8
        if '度' in text and '色温' not in text and action=='temperature':points+=8
        if context:
            points+=12 if name in context else 0
            points+=sum(2 for word in words if word in context and word in name)
            if mentioned and not any(word in name for word in mentioned):points-=24
        return points
    ranked=sorted(caps,key=score,reverse=True)
    positive=[cap for cap in ranked if score(cap)>0]
    return (positive or ranked)[:limit]


def validate_value(cap,value):
    if cap['param'] is None:
        if value is not None:raise ValueError('这个动作不接受数值。')
        return None
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError('需要有效的数值。')
    if not cap['min']<=value<=cap['max']:
        raise ValueError('数值超出设备范围。')
    if cap['action'] not in ('temperature','number') and value!=round(value):
        raise ValueError('这个参数需要整数。')
    step=cap.get('step',1)
    if isinstance(step,(float,int)) and step>0:
        count=(value-cap['min'])/step
        if not math.isclose(count,round(count),abs_tol=1e-6):raise ValueError('数值不符合设备步长。')
    return float(value)


def grammar_rule(cap,sentence,rule_id):
    if not isinstance(sentence,str) or len(sentence)>100 or sentence.count('{value}')!=(1 if cap['param'] else 0):
        raise ValueError('句式参数不正确。')
    literal=sentence.replace('{value}','')
    if META.search(literal) or not actionable(literal) or cap['name'] not in literal:
        raise ValueError('句式必须明确指定设备，且不能含通配符、条件、否定或查询。')
    data={'sentences':[sentence], 'slots':{'capability_id':cap['id'],'rule_id':rule_id}}
    if cap['param']:
        # Decimal bounds are enforced again by validate_value after recognition.
        start,stop=math.floor(cap['min']),math.ceil(cap['max'])
        if stop-start>10000:raise ValueError('数值范围过大，暂不自动生成句式。')
        range_info={'from':start,'to':stop,'digits':True,'words':True}
        if cap['action'] in ('temperature','number'):range_info['fractions']='tenths'
        data['lists']={'value':{'range':range_info}}
    return data


@lru_cache(maxsize=16)
def _compile(serialized):
    import json
    return Intents.from_dict(json.loads(serialized))


def parse_commands(text,caps,rules=(),owner=None,canonical=False):
    """Reject ambiguous parses. Only explicitly enabled own-user rules are used."""
    import json
    if not actionable(text):return None
    mapping={cap['id']:cap for cap in caps};entries=[]
    if canonical:
        for cap in caps:
            try:entries.append(grammar_rule(cap,cap['command'],'canonical'))
            except ValueError:continue
    for rule in rules:
        cap=mapping.get(rule.get('capability_id'))
        if not cap or not rule.get('enabled') or rule.get('owner')!=owner or rule.get('signature')!=cap['signature']:continue
        for sentence in rule['sentences']:
            try:entries.append(grammar_rule(cap,sentence,rule['id']))
            except ValueError:continue
    if not entries:return None
    grammar=_compile(json.dumps({'language':'zh-CN','settings':{'ignore_whitespace':True},
        'intents':{'RouterLocalCommand':{'data':entries}}},ensure_ascii=False,sort_keys=True))
    matches=[]
    for result in recognize_all(text.strip('。！! '),grammar,language='zh-CN'):
        slots={key:item.value for key,item in result.entities.items()}
        cap=mapping[slots['capability_id']]
        try:value=validate_value(cap,slots.get('value'))
        except ValueError:continue
        item={'capability_id':cap['id'],'value':value,'rule_id':slots['rule_id']}
        if item not in matches:matches.append(item)
    if len({(item['capability_id'],item['value']) for item in matches})!=1:return None
    return matches[0] if matches else None


def evidence(changes,caps):
    """One unambiguous observed control change is required for auto activation."""
    out=[]
    for entity,change in changes.items():
        before,after=change['before'],change['after'];a,b=before.get('attributes',{}),after.get('attributes',{})
        candidates=[cap for cap in caps if cap['entity_id']==entity]
        numeric=[]
        for cap in candidates:
            key={'brightness':'brightness','temperature':'temperature','position':'current_position',
                 'percentage':'percentage','volume':'volume_level','humidity':'humidity',
                 'color_temperature':'color_temp_kelvin'}.get(cap['action'])
            old,new=(a.get(key),b.get(key)) if key else (None,None)
            if cap['action']=='number':
                try:old,new=float(before['state']),float(after['state'])
                except (ValueError,TypeError):continue
            if new is not None and old!=new:
                if cap['action']=='brightness':new=round(new/255*100)
                if cap['action']=='volume':new=round(new*100)
                try:numeric.append({'capability_id':cap['id'],'value':validate_value(cap,new)})
                except ValueError:pass
        if numeric:out.extend(numeric);continue
        old,new=before.get('state'),after.get('state')
        if old==new:continue
        action=None
        if entity.startswith('cover.') and new in ('open','closed'):action='open' if new=='open' else 'close'
        elif entity.startswith('climate.') and new not in ('off','unknown','unavailable'):action='mode:'+new
        elif new=='off':action='off'
        elif old=='off':action='on'
        cap=next((c for c in candidates if c['action']==action),None)
        if cap:out.append({'capability_id':cap['id'],'value':None})
    return out


def validate_proposal(proposal,case,caps,existing=()):
    mapping={c['id']:c for c in caps};proof=case.get('evidence',[])
    if len(proof)!=1:raise ValueError('没有单一、可核对的执行结果，只记录分析。')
    expected=proof[0];cap=mapping.get(proposal.get('capability_id'))
    if not cap or cap['id']!=expected['capability_id']:raise ValueError('提议的动作与实际执行结果不同。')
    sentences=proposal.get('sentences')
    if not isinstance(sentences,list) or not 1<=len(sentences)<=3:raise ValueError('需要1至3条明确句式。')
    rule={'id':hashlib.sha256((case['owner']+'|'+cap['id']+'|'+str(sentences)).encode()).hexdigest()[:20],
          'owner':case['owner'],'capability_id':cap['id'],'signature':cap['signature'],
          'sentences':sentences,'enabled':True,'source_case':case['id']}
    for sentence in sentences:
        grammar_rule(cap,sentence,rule['id'])
        values=[None] if cap['param'] is None else sorted(set([cap['min'],cap['max'],expected['value']]))
        for value in values:
            text=sentence.replace('{value}',f'{value:g}' if value is not None else '')
            got=parse_commands(text,caps,[*existing,rule],case['owner'])
            if not got or got['capability_id']!=cap['id'] or got['value']!=value:raise ValueError('句式边界测试失败或与已有规则冲突。')
    got=parse_commands(case['text'],caps,[rule],case['owner'])
    if not got or got['capability_id']!=cap['id'] or got['value']!=expected['value']:
        raise ValueError('新句式没有正确覆盖原始指令及其参数。')
    for prefix in ('不要','别','如果','查看'):
        if parse_commands(prefix+case['text'],caps,[rule],case['owner']):raise ValueError('否定/查询检查失败。')
    return rule
