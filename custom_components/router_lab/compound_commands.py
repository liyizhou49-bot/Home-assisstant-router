"""Finite, all-or-nothing preflight for explicit immediate command combinations."""
import re
import unicodedata

from .local_commands import UNSAFE, _compile, grammar_rule, validate_value
from hassil import recognize_all

MAX_ACTIONS = 8
CONNECTORS = re.compile(r'并且|然后|接着|顺便|同时|以及|再|并|和|与|[,，;；、。]')
GUARD = re.compile(r'不|没|别|勿|莫|如果|假如|一旦|只要|除非|否则|等会|等一|稍后|过一|待会|'
                   r'或者|还是|可能|也许|应该|建议|能否|能不能|可不可以|将来|["\'“”‘’「」『』？?]')


def _split(text, caps):
    protected = []
    for name in {c['name'] for c in caps}:
        for hit in re.finditer(re.escape(name), text):
            protected.append(hit.span())
    hits = [m for m in CONNECTORS.finditer(text)
            if not any(start <= m.start() and m.end() <= end for start, end in protected)]
    if not hits:
        return None
    clauses = []; joiners = []; start = 0; pending = ''
    for hit in hits:
        part = text[start:hit.start()].strip()
        if part:
            if clauses: joiners.append(pending)
            clauses.append(part)
            pending = ''
        elif not clauses:
            return {'clauses': [], 'joiners': []}
        elif pending and not re.search(r'[,，;；、。]', pending) and not re.fullmatch(r'[,，;；、。]', hit.group()):
            return {'clauses': [], 'joiners': []}
        pending += hit.group()
        start = hit.end()
    final = text[start:].strip()
    if not final:
        return {'clauses': [], 'joiners': []}
    joiners.append(pending)
    clauses.append(final)
    return {'clauses': clauses, 'joiners': joiners}


def _sentences(cap):
    name, action = cap['name'], cap['action']
    result = [cap['command']]
    if action in ('on', 'off', 'open', 'close'):
        words = ['打开', '开启', '开'] if action in ('on', 'open') else ['关闭', '关掉', '关']
        for word in words:
            result.extend([word + name, '把' + name + word, name + word])
    elif action in ('brightness', 'temperature', 'volume', 'percentage', 'humidity', 'position', 'color_temperature'):
        title, unit = {'brightness': ('亮度', '%'), 'temperature': ('温度', '度'),
                       'volume': ('音量', '%'), 'percentage': ('风速', '%'),
                       'humidity': ('湿度', '%'), 'position': ('位置', '%'),
                       'color_temperature': ('色温', 'K')}[action]
        for start in (name, '把' + name):
            result.extend([start + title + '调到{value}' + unit,
                           start + title + '调为{value}' + unit])
            if action in ('brightness', 'temperature', 'position'):
                result.append(start + '调到{value}' + unit)
            if action == 'position':
                result.append(start + '开到{value}%')
    return list(dict.fromkeys(result))


def _atom(text, caps, rules, owner):
    import json
    text = re.sub(r'^(?:请帮我|麻烦帮我|请|帮我|麻烦)', '', text).strip()
    text = text.removesuffix('吧').strip()
    entries = []; mapping = {c['id']: c for c in caps}
    for cap in caps:
        for sentence in _sentences(cap):
            try: entries.append(grammar_rule(cap, sentence, 'compound_builtin'))
            except ValueError: continue
    for rule in rules:
        cap = mapping.get(rule.get('capability_id'))
        if not cap or not rule.get('enabled') or rule.get('owner') != owner or rule.get('signature') != cap['signature']:
            continue
        for sentence in rule['sentences']:
            try: entries.append(grammar_rule(cap, sentence, rule['id']))
            except ValueError: continue
    if not entries:
        return None
    grammar = _compile(json.dumps({'language': 'zh-CN', 'settings': {'ignore_whitespace': True},
                                  'intents': {'RouterCompoundAtom': {'data': entries}}}, ensure_ascii=False, sort_keys=True))
    matches = {}
    for match in recognize_all(text, grammar, language='zh-CN'):
        slots = {key: item.value for key, item in match.entities.items()}
        cap = mapping[slots['capability_id']]
        try: value = validate_value(cap, slots.get('value'))
        except ValueError: continue
        matches[(cap['id'], value)] = {'capability_id': cap['id'], 'value': value, 'rule_id': slots['rule_id']}
    return next(iter(matches.values())) if len(matches) == 1 else None


def plan(text, caps, rules=(), owner=None):
    """None means not a combination; a rejected combination always has no plans."""
    if not isinstance(text, str):
        return None
    text = unicodedata.normalize('NFKC', text).strip().rstrip('。！!').strip()
    split = _split(text, caps)
    if split is None:
        return None
    clauses, joiners = split['clauses'], split['joiners']
    rejected = {'ok': False, 'clauses': clauses, 'plans': [], 'reason': '组合中的每一条都需明确指定设备和动作。'}
    if len(text) > 600 or not 2 <= len(clauses) <= MAX_ACTIONS:
        return dict(rejected, reason='本地组合支持 2 至 8 条完整动作。')
    if UNSAFE.search(text) or GUARD.search(text):
        return dict(rejected, reason='条件、否定、查询、引用或未来安排交给大模型处理。')
    # Only explicit full device names participate; no previous-turn device inference.
    selected = [c for c in caps if c['name'] in text]
    parsed = []; expanded = []; previous_verb = None
    for index, clause in enumerate(clauses):
        if index == 0:
            clause = re.sub(r'^((?:请帮我|麻烦帮我|请|帮我|麻烦)?)(?:先)', r'\1', clause).strip()
        # Shared on/off verbs are allowed only for a following complete device name.
        # Never carry numbers, modes, pronouns or omitted devices into another clause.
        joiner = joiners[index - 1] if index else ''
        shared = joiner in ('和', '与', '以及', '、', ',') or joiner.strip(',，') in ('和', '与', '以及')
        if shared and previous_verb and any(c['name'] == clause for c in selected):
            clause = previous_verb + clause
        item = _atom(clause, selected, rules, owner)
        if not item:
            return dict(rejected, reason='至少一条动作未完整识别、目标有歧义或参数超出设备范围。')
        parsed.append(item); expanded.append(clause)
        current = next(c for c in selected if c['id'] == item['capability_id'])
        previous_verb = ('打开' if current['action'] in ('on', 'open') else
                         '关闭' if current['action'] in ('off', 'close') else None)
    return {'ok': True, 'clauses': expanded, 'plans': parsed, 'reason': ''}


def single(text, caps, rules=(), owner=None):
    """A complete explicit one-action match may run before a statistical classifier."""
    if not isinstance(text,str):return None
    text=unicodedata.normalize('NFKC',text).strip().rstrip('。.!！').strip()
    if not text or len(text)>240 or UNSAFE.search(text) or GUARD.search(text):return None
    if _split(text,caps) is not None:return None
    selected=[c for c in caps if c['name'] in text]
    return _atom(text,selected,rules,owner)


def followup(text, caps, previous):
    """Fill only the target of an explicit light on/off clarification, never guess an action."""
    def clean(value):
        return unicodedata.normalize('NFKC',value).strip().rstrip('。.!！').strip()
    if not previous or not isinstance(text,str):return None
    original=clean(previous.get('user',''))
    question=previous.get('assistant','')
    if not re.search(r'哪(?:盏|个|一盏|一个)?灯|哪(?:盏|个|一盏|一个)',question):return None
    if not re.search(r'[?？]',question) or '灯' not in question:return None
    # The previous request must itself contain one clear immediate action.
    match=re.fullmatch(r'(?:请|帮我|请帮我)?(打开|开启|开|关闭|关掉|关)(?:一下)?灯',original)
    if not match:return None
    answer=clean(text)
    if UNSAFE.search(answer) or GUARD.search(answer):return None
    names={c['name'] for c in caps if c['domain']=='light' and c['name']==answer}
    if len(names)!=1:return None
    command=('打开' if match[1] in ('打开','开启','开') else '关闭')+answer
    item=single(command,caps)
    return {'plan':item,'text':command} if item else None
