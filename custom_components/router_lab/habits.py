"""Bounded, deterministic home preferences. No model training or HA imports."""
import copy
import re

PERIODS = ('any', 'morning', 'afternoon', 'day', 'night')
CONDITIONS = ('always', 'empty', 'occupied', 'dark', 'hot')
SCENE_NAMES = {'home':'回家','away':'离家','sleep':'睡觉','wake':'起床'}
SCENE_ALIASES = dict({key:key for key in SCENE_NAMES}, **{'回家':'home','到家':'home','离家':'away','出门':'away',
    '睡觉':'sleep','睡前':'sleep','晚安':'sleep','起床':'wake','早安':'wake','看电影':'观影'})
PERIOD_NAMES = {'any':'每次', 'morning':'上午', 'afternoon':'下午', 'day':'白天', 'night':'晚上'}
CONDITION_NAMES = {'always':'', 'empty':'家里原本无人时', 'occupied':'家里有人时', 'dark':'光线暗时', 'hot':'室温偏高时'}
ACTIONS = {
    'light': ('on','off','keep','brightness'), 'cover': ('open','close','keep','position'),
    'fan': ('on','off','keep','percentage'), 'climate': ('on','off','keep','temperature'),
    'switch': ('on','off','keep'), 'media_player': ('on','off','keep','volume'),
    'humidifier': ('on','off','keep','humidity'),
}
LIMITS = {'brightness':(1,100), 'position':(0,100), 'percentage':(1,100),
          'temperature':(16,30), 'volume':(0,100), 'humidity':(30,80)}
DURABLE = re.compile(r'记住|记一下|以后|今后|往后|每次|总是|长期|习惯|偏好|平时|通常|一般都')
TEMPORARY = re.compile(r'今天|今晚|这次|本次|暂时|现在|就这回')
HOME = re.compile(r'^(我|我们)?(回来[了啦咯]?|到家[了啦咯]?|回家[了啦咯]?)[！!。~～\s]*$')
CONTEXTUAL = re.compile(r'记住|记一下|以后|今后|往后|习惯|偏好|今天|今晚|这次|暂时|窗帘就|也[开关打拉]|照旧|老样子|同样|还是|别这样|忘[记掉]|删.*(习惯|记忆)|回来|回家|到家|离家|出门|走的时候|睡觉|睡前|起床|观影|家里没人|都记了|记得什么|记了什么|就拉着|保持.*窗帘')


def normalize_scene(value):
    if not isinstance(value,str):raise ValueError('需要明确场景。')
    value=value.strip()
    if len(value)>2 and value.endswith(('模式','场景')):value=value[:-2].strip()
    if value in SCENE_ALIASES:return SCENE_ALIASES[value]
    if not re.fullmatch(r'[\w\u4e00-\u9fff· -]{1,20}',value):raise ValueError('场景名称需为1至20个字，不含控制符号。')
    return value


def scene_label(scene):
    scene=normalize_scene(scene)
    return SCENE_NAMES.get(scene,scene)


def mentioned_scenes(text,known=()):
    found=set()
    explicit=set()
    patterns={'home':r'回家|回来|到家','away':r'离家|出门|离开家|走的时候|走时|我(?:们)?(?:要)?走[了啦]',
              'sleep':r'睡觉|睡前|晚安|我(?:们)?(?:要|先)?睡[了啦]','wake':r'起床|早安|我(?:们)?醒[了啦]'}
    for scene,pattern in patterns.items():
        if re.search(pattern,text):found.add(scene)
    for label,scene in (('回家','home'),('离家','away'),('出门','away'),('睡觉','sleep'),('起床','wake')):
        if re.search(r'不是(?:说|指|在)?'+label,text):found.discard(scene)
    for label in ('观影','做饭','用餐','学习','工作',*known):
        if label in SCENE_NAMES:continue
        if re.search(re.escape(scene_label(label))+r'(?:时|的时候|模式|场景)',text):found.add(normalize_scene(label))
    if re.search(r'看电影(?:时|的时候|就)',text):found.add('观影')
    for match in re.finditer(r'[“「"]([^”」"\n]{1,20})[”」"](?:模式|场景)',text):
        explicit.add(normalize_scene(match.group(1)))
    # Explicit named modes, e.g. 记住，以后开启观影模式时把灯调暗.
    for match in re.finditer(r'(?:开启|进入|启动|执行|切换到)([\w\u4e00-\u9fff· -]{1,20}?)(?:模式|场景)',text):
        explicit.add(normalize_scene(match.group(1)))
    found.update(explicit)
    for scene in list(found):
        if scene not in SCENE_NAMES and any(scene!=name and scene in name for name in explicit):found.discard(scene)
    return found


def trigger_scene(text,known=()):
    text=text.strip('！!。~～ \n')
    if HOME.fullmatch(text):return 'home'
    if re.fullmatch(r'(我|我们)?(要走了|要走啦|走了|走啦|先走了|出门了|出门啦|要出门了|要出门啦|离家了|离开家了|准备出门了)',text):return 'away'
    if re.fullmatch(r'(我|我们)?(要睡觉了|睡觉了|睡了|先睡了|要睡了|准备睡觉了)|晚安',text):return 'sleep'
    if re.fullmatch(r'(我|我们)?(起床了|起床啦|醒了)|早安',text):return 'wake'
    match=re.fullmatch(r'(开启|启动|进入|执行|切换到)(.+?)(模式|场景)',text)
    if match:
        scene=normalize_scene(match.group(2))
        if scene in SCENE_NAMES or scene in known:return scene
    return None


def temporary_period(text,proposed='any'):
    if '今晚' in text:
        return 'night'
    if not re.search(r'上午|下午|白天|晚上|早上|早晨|夜里|夜间|傍晚|晚间',text):
        return 'any'
    return proposed


def new_profile():
    return {'rules':[], 'temporary':[], 'notes':[], 'observations':{}, 'suggestions':[]}


def validate_rule(rule, allowed):
    entity = rule['entity_id']
    domain = entity.split('.')[0]
    if entity not in allowed or domain not in ACTIONS or 'simulate_' in entity:
        raise ValueError('只支持本测试台的虚拟家电，不支持模拟开关或真实设备。')
    action = rule['action']
    if action not in ACTIONS[domain]:
        raise ValueError('这个设备不支持该动作。')
    if rule.get('period','any') not in PERIODS or rule.get('condition','always') not in CONDITIONS:
        raise ValueError('不支持的时间或环境条件。')
    result = {'entity_id':entity, 'action':action, 'scene':normalize_scene(rule.get('scene','home')), 'period':rule.get('period','any'),
              'condition':rule.get('condition','always')}
    if 'enabled' in rule:
        if not isinstance(rule['enabled'],bool):raise ValueError('规则启停值不正确。')
        result['enabled']=rule['enabled']
    if action in LIMITS:
        value = rule.get('value')
        low,high = LIMITS[action]
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not low <= value <= high:
            raise ValueError(f'参数应在{low}到{high}之间。')
        result['value'] = float(value)
    return result


def rule_key(rule):
    legacy=':'.join(rule[key] for key in ('period','condition','entity_id'))
    scene=normalize_scene(rule.get('scene','home'))
    return legacy if scene=='home' else scene+':'+legacy


def upsert(profile, rule, *, date=None,session_id=None,expires_at=None):
    field = 'temporary' if date else 'rules'
    item = dict(rule, id=rule_key(rule))
    if date:
        item['date'] = date
        item['id']+=':once:'+session_id if session_id else ':today'
    if session_id:
        item['session_id']=session_id
        item['expires_at']=expires_at
    profile[field] = [old for old in profile[field] if old['id'] != item['id']]
    if len(profile[field]) >= 60:
        raise ValueError('已达到60条习惯上限，请先删除不用的习惯。')
    profile[field].append(item)
    return copy.deepcopy(item)


def matches(rule, env):
    if not rule.get('enabled',True) or normalize_scene(rule.get('scene','home'))!=normalize_scene(env.get('scene','home')):return False
    hour,day = env['hour'],env['daylight']
    periods = {'any':True, 'day':day, 'night':not day,
               'morning':day and hour < 12, 'afternoon':day and hour >= 12}
    conditions = {'always':True, 'empty':env.get('occupied') is False,
                  'occupied':env.get('occupied') is True,
                  'dark':env.get('illuminance') is not None and env['illuminance'] < 150,
                  'hot':env.get('temperature') is not None and env['temperature'] >= 28}
    return periods[rule['period']] and conditions[rule['condition']]


def plan(profile, env, overrides=()):
    """Specific conditions beat generic ones; explicit current instructions win."""
    candidates=[]
    for rule in profile['rules']:
        if matches(rule,env):
            specificity = (2 if rule['period'] in ('morning','afternoon') else 1 if rule['period'] != 'any' else 0)
            candidates.append((0,specificity,rule['condition'] != 'always',rule))
    for rule in profile['temporary']:
        if (rule['date'] == env['date'] and matches(rule,env)
            and (not rule.get('session_id') or (rule['session_id']==env.get('session_id') and rule['expires_at']>env['time']))):
            candidates.append((2 if rule.get('session_id') else 1,0,False,rule))
    for rule in overrides:
        if normalize_scene(rule.get('scene','home'))==normalize_scene(env.get('scene','home')):
            candidates.append((3,0,False,rule))
    selected={}
    for *_priority,rule in sorted(candidates,key=lambda item:item[:3]):
        selected[rule['entity_id']] = copy.deepcopy(rule)
    return list(selected.values())


def describe(rule, names):
    action = {'on':'打开','off':'关闭','keep':'保持原状','open':'打开','close':'关闭',
              'brightness':'亮度设为','position':'位置设为','percentage':'风速设为',
              'temperature':'温度设为','volume':'音量设为','humidity':'湿度设为'}[rule['action']]
    name = names.get(rule['entity_id'],rule['entity_id'])
    value = rule.get('value')
    suffix = f'{value:g}' + ('℃' if rule['action']=='temperature' else '%') if value is not None else ''
    scene=scene_label(rule.get('scene','home'))
    return f"{PERIOD_NAMES[rule['period']]}{scene}，{CONDITION_NAMES[rule['condition']]}{name}{action}{suffix}"


def observe(profile, rule, event_id):
    """Three distinct return-home events can suggest, never silently save a rule."""
    key = rule_key(rule)+':'+rule['action']+':'+str(rule.get('value',''))
    observations=profile['observations']
    if key not in observations and len(observations)>=60:
        observations.pop(next(iter(observations)))
    item=observations.setdefault(key,{'rule':copy.deepcopy(rule),'events':[]})
    if event_id not in item['events']:
        item['events']=(item['events']+[event_id])[-3:]
    if len(item['events'])>=3 and not any(s['id']==key for s in profile['suggestions']):
        profile['suggestions']=(profile['suggestions']+[{'id':key,'rule':copy.deepcopy(rule)}])[-10:]
    return key if len(item['events'])>=3 else None
