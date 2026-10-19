"""Learned binary routing from character, pronunciation and optional BGE features."""
import collections
import json
import os
from pathlib import Path
import numpy as np

from .classifier import LABELS, ngrams, normalize
from .local_runtime import enable
from .semantic import ChineseEncoder, DenseHead


def pinyin_ngrams(text):
    enable()
    from pypinyin import lazy_pinyin
    syllables = ['^'] + lazy_pinyin(normalize(text)) + ['$']
    return collections.Counter(' '.join(syllables[i:i+n]) for n in (1,2,3)
                               for i in range(len(syllables)-n+1))


class TfidfBank:
    def __init__(self,kind):
        if kind not in ('char','pinyin'):
            raise ValueError('invalid feature bank')
        self.kind = kind
        self.vocabulary = {}
        self.idf = None

    def tokens(self,text):
        return ngrams(text) if self.kind=='char' else pinyin_ngrams(text)

    def fit(self,texts):
        counts = collections.Counter()
        for text in texts:
            counts.update(self.tokens(text).keys())
        tokens = sorted(counts,key=lambda t:(-counts[t],t))[:8000]
        self.vocabulary = {t:i for i,t in enumerate(tokens)}
        self.idf = np.array([np.log((len(texts)+1)/(counts[t]+1))+1 for t in tokens])
        return self

    def features(self,texts):
        x = np.zeros((len(texts),len(self.vocabulary)),dtype=np.float64)
        for row,text in enumerate(texts):
            for token,count in self.tokens(text).items():
                column = self.vocabulary.get(token)
                if column is not None:
                    x[row,column] = (1+np.log(count))*self.idf[column]
        x /= np.maximum(np.linalg.norm(x,axis=1,keepdims=True),1e-12)
        return x


def combine(texts,char,pinyin,semantic,config):
    # No lexical command/condition branches: feature weights are selected by CV.
    parts = [char]
    if config['pinyin']:
        parts.append(pinyin*config['pinyin'])
    if config['semantic']:
        parts.append(semantic*config['semantic'])
    parts.append(np.array([[np.log1p(len(normalize(t)))/5] for t in texts]))
    return np.concatenate(parts,axis=1)


class HybridClassifier:
    def __init__(self,char_bank,pinyin_bank,encoder,head,config,threshold=.5):
        self.char_bank, self.pinyin_bank = char_bank,pinyin_bank
        self.encoder, self.head = encoder,head
        self.config, self.threshold = dict(config),float(threshold)

    def features(self,texts):
        return combine(texts,self.char_bank.features(texts),
                       self.pinyin_bank.features(texts) if self.pinyin_bank else None,
                       self.encoder.encode(texts) if self.encoder else None,self.config)

    def predict(self,text):
        p = float(self.head.probabilities(self.features([text]))[0])
        return {'hassil':1-p,'llm':p}

    def save(self,directory,metadata):
        path = Path(directory).resolve()
        path.mkdir(parents=True,exist_ok=True)
        np.savez_compressed(path/'weights.npz',weights=self.head.weights,mean=self.head.mean,
                            scale=np.array([self.head.scale]),bias=np.array([self.head.bias]),
                            char_idf=self.char_bank.idf,
                            pinyin_idf=self.pinyin_bank.idf if self.pinyin_bank else np.array([]))
        meta = dict(metadata,backend='hybrid_tfidf_bge_binary',feature_version=1,labels=list(LABELS),
                    config=self.config,threshold=self.threshold,
                    char_vocabulary=list(self.char_bank.vocabulary),
                    pinyin_vocabulary=list(self.pinyin_bank.vocabulary) if self.pinyin_bank else [],
                    encoder_path=os.path.relpath(self.encoder.directory,path) if self.encoder else None,
                    score_note='Uncalibrated classifier scores; route uses the validation-selected threshold.')
        (path/'metadata.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')

    @classmethod
    def load(cls,directory,encoder=None):
        path = Path(directory).resolve()
        meta = json.loads((path/'metadata.json').read_text(encoding='utf-8'))
        if meta.get('backend')!='hybrid_tfidf_bge_binary' or meta.get('feature_version')!=1 or meta.get('labels')!=list(LABELS):
            raise ValueError('unsupported hybrid schema')
        config = meta['config']
        if set(config)!= {'pinyin','semantic'} or not all(isinstance(v,(int,float)) and np.isfinite(v) and 0<=v<=4 for v in config.values()):
            raise ValueError('invalid hybrid config')
        threshold = meta['threshold']
        if not isinstance(threshold,(int,float)) or not np.isfinite(threshold) or not 0<threshold<1:
            raise ValueError('invalid hybrid threshold')
        banks = []
        with np.load(path/'weights.npz',allow_pickle=False) as data:
            for kind in ('char','pinyin'):
                tokens = meta[kind+'_vocabulary']
                if not isinstance(tokens,list) or not all(isinstance(t,str) for t in tokens) or len(set(tokens))!=len(tokens):
                    raise ValueError('invalid hybrid vocabulary')
                bank = TfidfBank(kind)
                bank.vocabulary = {t:i for i,t in enumerate(tokens)}
                bank.idf = data[kind+'_idf'].copy()
                if bank.idf.shape!=(len(tokens),) or not np.isfinite(bank.idf).all() or (bank.idf<=0).any():
                    raise ValueError('invalid hybrid IDF')
                banks.append(bank)
            head = DenseHead()
            head.weights,head.mean = data['weights'].copy(),data['mean'].copy()
            bias,scale = data['bias'].copy(),data['scale'].copy()
        if not banks[0].vocabulary or bool(banks[1].vocabulary)!=bool(config['pinyin']):
            raise ValueError('hybrid feature config mismatch')
        width = len(banks[0].vocabulary)+len(banks[1].vocabulary)+(512 if config['semantic'] else 0)+1
        if head.weights.shape!=(width,) or head.mean.shape!=(width,) or bias.shape!=(1,) or scale.shape!=(1,):
            raise ValueError('invalid hybrid dimensions')
        if not all(np.isfinite(v).all() for v in (head.weights,head.mean,bias,scale)) or scale[0]<=0:
            raise ValueError('invalid hybrid weights')
        head.bias,head.scale = float(bias[0]),float(scale[0])
        if config['semantic']:
            encoder_path = (path/meta['encoder_path']).resolve()
            if encoder is not None and encoder.directory!=encoder_path:
                raise ValueError('hybrid encoder path mismatch')
            encoder = encoder or ChineseEncoder(encoder_path)
        else:
            encoder = None
        return cls(banks[0],banks[1] if config['pinyin'] else None,encoder,head,config,threshold)
