"""A small supervised binary model: character TF-IDF -> logistic regression."""
import collections
import json
import unicodedata
from pathlib import Path

import numpy as np

LABELS = ('hassil', 'llm')


def normalize(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('text must be a nonempty string')
    return ' '.join(unicodedata.normalize('NFKC', text).split())


def ngrams(text):
    text = '^' + normalize(text) + '$'
    return collections.Counter(text[i:i+n] for n in (1, 2, 3)
                               for i in range(len(text)-n+1))


class BinaryClassifier:
    def __init__(self):
        self.vocabulary = {}
        self.idf = None
        self.weights = None
        self.bias = 0.

    def features(self, texts):
        if self.idf is None:
            raise ValueError('model is not fitted')
        x = np.zeros((len(texts), len(self.vocabulary)+1), dtype=np.float64)
        for i, text in enumerate(texts):
            for token, count in ngrams(text).items():
                index = self.vocabulary.get(token)
                if index is not None:
                    x[i, index] = (1+np.log(count))*self.idf[index]
            norm = np.linalg.norm(x[i, :-1])
            if norm:
                x[i, :-1] /= norm
            # A generic text feature; no command keywords, domains or routing rules.
            x[i, -1] = np.log1p(len(normalize(text)))/5
        return x

    def fit(self, rows, epochs=600, lr=.05, l2=.001):
        if epochs < 1 or lr <= 0 or l2 < 0:
            raise ValueError('invalid training parameters')
        if {r['label'] for r in rows} != set(LABELS):
            raise ValueError('training needs both hassil and llm labels only')
        frequencies = collections.Counter()
        for row in rows:
            frequencies.update(ngrams(row['text']).keys())
        # Vocabulary and IDF are fitted separately inside each validation fold.
        tokens = sorted(frequencies, key=lambda token: (-frequencies[token], token))[:8000]
        self.vocabulary = {token:i for i, token in enumerate(tokens)}
        self.idf = np.array([np.log((len(rows)+1)/(frequencies[t]+1))+1 for t in tokens])
        x = self.features([r['text'] for r in rows])
        y = np.array([LABELS.index(r['label']) for r in rows])
        counts = np.bincount(y, minlength=2)
        sample_weights = len(rows)/(2*counts[y])
        self.weights = np.zeros(x.shape[1], dtype=np.float64)
        self.bias = 0.
        m = np.zeros(x.shape[1]+1)
        v = np.zeros(x.shape[1]+1)
        history = []
        for epoch in range(1, epochs+1):
            p = self._sigmoid(x@self.weights+self.bias)
            error = (p-y)*sample_weights/len(y)
            grad = np.append(x.T@error+l2*self.weights, error.sum())
            m = .9*m+.1*grad
            v = .999*v+.001*grad*grad
            step = lr*(m/(1-.9**epoch))/(np.sqrt(v/(1-.999**epoch))+1e-8)
            self.weights -= step[:-1]
            self.bias -= step[-1]
            if epoch == 1 or epoch % 50 == 0 or epoch == epochs:
                p = self._sigmoid(x@self.weights+self.bias)
                loss = np.mean(sample_weights*(-y*np.log(np.maximum(p,1e-12))-(1-y)*np.log(np.maximum(1-p,1e-12))))
                history.append({'epoch':epoch,'training_loss':float(loss+l2*(self.weights@self.weights)/2),
                                'training_accuracy':float(((p>=.5)==y).mean())})
        return history

    @staticmethod
    def _sigmoid(z):
        return 1/(1+np.exp(-np.clip(z,-40,40)))

    def predict(self, text):
        if self.weights is None:
            raise ValueError('model is not fitted')
        probability = float(self._sigmoid(self.features([text])@self.weights+self.bias)[0])
        return {'hassil':1-probability,'llm':probability}

    def save(self, directory, metadata=None):
        if self.weights is None:
            raise ValueError('model is not fitted')
        path = Path(directory)
        path.mkdir(parents=True,exist_ok=True)
        np.savez_compressed(path/'weights.npz',weights=self.weights,idf=self.idf,bias=np.array([self.bias]))
        meta = dict(metadata or {}, backend='char_tfidf_logistic_binary',feature_version=1,
                    labels=list(LABELS), vocabulary=list(self.vocabulary),
                    score_note='Classifier scores are not calibrated execution probabilities.')
        (path/'metadata.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')

    @classmethod
    def load(cls, directory):
        path = Path(directory)
        meta = json.loads((path/'metadata.json').read_text(encoding='utf-8'))
        if meta.get('backend') != 'char_tfidf_logistic_binary' or meta.get('labels') != list(LABELS) or meta.get('feature_version') != 1:
            raise ValueError('unsupported binary model schema')
        tokens = meta['vocabulary']
        if not isinstance(tokens,list) or not all(isinstance(t,str) for t in tokens) or len(set(tokens)) != len(tokens):
            raise ValueError('invalid vocabulary')
        obj = cls()
        obj.vocabulary = {token:i for i,token in enumerate(tokens)}
        with np.load(path/'weights.npz',allow_pickle=False) as data:
            obj.weights = data['weights'].copy()
            obj.idf = data['idf'].copy()
            bias = data['bias'].copy()
        if obj.weights.shape != (len(tokens)+1,) or obj.idf.shape != (len(tokens),) or bias.shape != (1,):
            raise ValueError('invalid model shapes')
        if not all(np.isfinite(a).all() for a in (obj.weights,obj.idf,bias)) or not (obj.idf>0).all():
            raise ValueError('invalid model values')
        obj.bias = float(bias[0])
        return obj
