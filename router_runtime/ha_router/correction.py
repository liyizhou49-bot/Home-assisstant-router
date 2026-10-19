"""Optional constrained correction against an explicit catalog. Never executes."""
import json
import re
from pathlib import Path

from .classifier import normalize
from .local_runtime import ROOT, enable


def surface(text):
    return normalize(text).strip('。！？.!?').replace(' ', '')


class LocalCorrector:
    def __init__(self, catalog=None):
        enable()
        from hassil import Intents, recognize
        from pypinyin import lazy_pinyin
        from rapidfuzz.distance import Levenshtein
        self.recognize, self.pinyin, self.distance = recognize, lazy_pinyin, Levenshtein
        self.catalog_path = Path(catalog or ROOT/'data/correction_demo.json')
        document = json.loads(self.catalog_path.read_text(encoding='utf-8'))
        if document.get('schema')!=1 or not document.get('commands'):
            raise ValueError('invalid correction catalog')
        self.entries, self.aliases, intents = {}, {}, {}
        for entry in document['commands']:
            key, intent = entry['id'], entry['intent']
            if key in self.entries or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*',intent):
                raise ValueError('duplicate command or invalid intent')
            texts = list(dict.fromkeys([entry['canonical']]+entry.get('aliases',[])))
            clean = []
            for text in texts:
                text = surface(text)
                if re.search(r'[\[\](){}<>|\\]',text) or len(text)>80:
                    raise ValueError('catalog requires literal short phrases, not templates')
                if text in self.aliases:
                    raise ValueError('duplicate catalog phrase')
                self.aliases[text] = key
                clean.append(text)
            self.entries[key] = entry
            intents.setdefault(intent, {'data':[]})['data'].append(
                {'sentences':clean,'slots':{'command_id':key}})
        self.intents = Intents.from_dict({'language':'zh-CN','intents':intents})
        self.phonetics = {text:self.pinyin(text) for text in self.aliases}

    def _parse(self, text, key):
        result = self.recognize(text, self.intents)
        return (result is not None and result.intent.name==self.entries[key]['intent']
                and result.entities['command_id'].value==key)

    def _blocked(self, text):
        # This guard belongs only to the optional correction adapter, not to routing.
        if re.search(r'不|别|勿|莫|禁止|如果|若|当|等|同时|并|然后|再|顺便|以及|和|才|就|除了|只|以后|之后|明天|明早|分钟|小时|，|,|；|;',text):
            return True
        sounds = self.pinyin(text)
        # A misspelled negation must not be deleted as harmless ASR noise.
        return any(sounds[i:i+2] in (['bu','yao'],['bu','yong'],['bu','bi'],['bu','neng'])
                   for i in range(len(sounds)-1)) or (sounds and sounds[0] in ('bie','wu','mo'))

    def correct(self, text):
        raw = surface(text)
        rejected = {'accepted':False,'status':'rejected','canonical_text':None,'parsed_intent':None}
        if self._blocked(raw):
            return dict(rejected, reason='unsupported_negation_condition_or_multiple_actions')
        if raw in self.aliases:
            key = self.aliases[raw]
            if not self._parse(raw,key):
                return dict(rejected,reason='raw_parse_failed')
            return self._accepted(key,'exact')
        # Limit corrections to a one-character edit, with
        # exactly equal Chinese pronunciation and exactly unchanged numeric tokens.
        sounds, numbers = self.pinyin(raw), re.findall(r'\d+(?:\.\d+)?',raw)
        candidates = {}
        for alias, key in self.aliases.items():
            if numbers != re.findall(r'\d+(?:\.\d+)?',alias):
                continue
            distance = self.distance.distance(raw,alias)
            if distance!=1 or len(raw)<4 or sounds!=self.phonetics[alias]:
                continue
            score = 1-distance/max(len(raw),len(alias))
            if score>=.75:
                candidates[key] = max(candidates.get(key,0),score)
        ordered = sorted(candidates.items(),key=lambda item:-item[1])
        if not ordered:
            return dict(rejected,reason='no_single_homophone_candidate')
        if len(ordered)>1 and ordered[0][1]-ordered[1][1]<.1:
            return dict(rejected,reason='ambiguous_catalog_commands')
        key, score = ordered[0]
        canonical = surface(self.entries[key]['canonical'])
        if not self._parse(canonical,key):
            return dict(rejected,reason='canonical_parse_failed')
        return dict(self._accepted(key,'corrected'),match_score=score)

    def _accepted(self, key, status):
        entry = self.entries[key]
        canonical = entry['canonical']
        if not self._parse(surface(canonical),key):
            raise ValueError('catalog canonical phrase cannot be parsed')
        return dict(accepted=True,status=status,canonical_text=canonical,
                    parsed_intent=entry['intent'],command_id=key,reason='catalog_hassil_parse_confirmed')


class RoutingPreview:
    def __init__(self, router, corrector=None):
        self.router, self.corrector = router, corrector or LocalCorrector()

    def prepare(self, text):
        prediction = self.router.route(text)
        result = dict(router_prediction=prediction,final_route='llm',text_for_handler=text,
                      executed=False,catalog='example catalog; replace with actual supported commands')
        if prediction['route']=='llm':
            return dict(result,correction=None,reason='router_selected_llm')
        correction = self.corrector.correct(text)
        if correction['accepted']:
            result.update(final_route='hassil',text_for_handler=correction['canonical_text'])
        return dict(result,correction=correction,
                    reason='local_parse_confirmed' if correction['accepted'] else 'local_parse_failed_fallback_llm')
