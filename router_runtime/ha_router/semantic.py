"""Frozen Chinese BGE encoder with a small supervised binary head."""
import json
from pathlib import Path

import numpy as np

from .classifier import LABELS, normalize
from .local_runtime import ROOT, enable


class ChineseEncoder:
    def __init__(self, directory=None):
        enable()
        import onnxruntime as ort
        from tokenizers import Tokenizer
        self.directory = Path(directory or ROOT/'models/encoder_bge_zh').resolve()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(self.directory/'model_int8.onnx'),
                                           sess_options=options,
                                           providers=['CPUExecutionProvider'])
        self.tokenizer = Tokenizer.from_file(str(self.directory/'tokenizer.json'))
        self.tokenizer.no_truncation()
        self.tokenizer.enable_padding(pad_id=0, pad_token='[PAD]')
        self.cache = {}

    def encode(self, texts):
        normalized = [normalize(t) for t in texts]
        pending = list(dict.fromkeys(t for t in normalized if t not in self.cache))
        # Dynamic INT8 quantization depends on the batch's numeric range. Use the
        # same single-sentence shape for training and serving to prevent drift.
        for text in pending:
            batch = [text]
            # Never silently classify only the beginning of a long command.
            if any(len(self.tokenizer.encode(t).ids) > 512 for t in batch):
                raise ValueError('text is too long for the local semantic encoder')
            encoded = self.tokenizer.encode_batch(batch)
            feed = {'input_ids':np.array([e.ids for e in encoded], dtype=np.int64),
                    'attention_mask':np.array([e.attention_mask for e in encoded], dtype=np.int64),
                    'token_type_ids':np.array([e.type_ids for e in encoded], dtype=np.int64)}
            hidden = self.session.run(['last_hidden_state'], feed)[0]
            vectors = hidden[:, 0, :].astype(np.float64)
            vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
            for text, vector in zip(batch, vectors, strict=True):
                self.cache[text] = vector
        return np.array([self.cache[t] for t in normalized], dtype=np.float64)


class DenseHead:
    def fit(self, vectors, labels, l2=.001, epochs=500, sample_weights=None):
        vectors = np.asarray(vectors, dtype=np.float64)
        y = np.array([LABELS.index(label) for label in labels], dtype=np.float64)
        if set(labels) != set(LABELS):
            raise ValueError('semantic training needs both labels')
        self.mean = vectors.mean(axis=0)
        self.scale = max(float(np.sqrt(np.mean(np.sum((vectors-self.mean)**2, axis=1)))),1e-6)
        x = (vectors-self.mean)/self.scale
        base_weights = (np.ones(len(y)) if sample_weights is None else
                        np.asarray(sample_weights,dtype=np.float64))
        if base_weights.shape!=y.shape or not np.isfinite(base_weights).all() or (base_weights<=0).any():
            raise ValueError('invalid sample weights')
        counts = np.bincount(y.astype(int),weights=base_weights,minlength=2)
        sample_weights = base_weights/(2*counts[y.astype(int)])
        self.weights = np.zeros(x.shape[1])
        self.bias = 0.
        m = np.zeros(x.shape[1]+1)
        v = m.copy()
        for epoch in range(1, epochs+1):
            scores = self.sigmoid(x@self.weights+self.bias)
            error = (scores-y)*sample_weights
            gradient = np.append(x.T@error+l2*self.weights, error.sum())
            m = .9*m+.1*gradient
            v = .999*v+.001*gradient*gradient
            step = .03*(m/(1-.9**epoch))/(np.sqrt(v/(1-.999**epoch))+1e-8)
            self.weights -= step[:-1]
            self.bias -= step[-1]
        return self

    @staticmethod
    def sigmoid(z):
        return 1/(1+np.exp(-np.clip(z,-40,40)))

    def probabilities(self, vectors):
        return self.sigmoid(((vectors-self.mean)/self.scale)@self.weights+self.bias)


def neighbor_probabilities(reference, labels, queries, k=3, temperature=.05):
    """Similarity baseline: average the top k similarities within each class."""
    similarities = np.asarray(queries)@np.asarray(reference).T
    labels = np.asarray(labels)
    values = []
    for label in LABELS:
        scores = np.sort(similarities[:, labels==label],axis=1)
        values.append(scores[:, -min(k,scores.shape[1]):].mean(axis=1))
    return DenseHead.sigmoid((values[1]-values[0])/temperature)


class SemanticClassifier:
    def __init__(self, encoder, head):
        self.encoder, self.head = encoder, head

    def predict(self, text):
        probability = float(self.head.probabilities(self.encoder.encode([text]))[0])
        return {'hassil':1-probability,'llm':probability}

    def save(self, directory, metadata):
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path/'weights.npz',weights=self.head.weights,mean=self.head.mean,
                            bias=np.array([self.head.bias]),scale=np.array([self.head.scale]))
        encoder_path = Path(self.encoder.directory)
        # Relative to the model directory, independent of the caller's working directory.
        import os
        meta = dict(metadata, backend='frozen_bge_zh_logistic_binary',feature_version=1,
                    labels=list(LABELS),encoder_path=os.path.relpath(encoder_path,path.resolve()),
                    pooling='CLS_L2',encoder_frozen=True,
                    score_note='Classifier scores are not calibrated execution probabilities.')
        (path/'metadata.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')

    @classmethod
    def load(cls, directory, encoder=None):
        path = Path(directory).resolve()
        meta = json.loads((path/'metadata.json').read_text(encoding='utf-8'))
        if meta.get('backend') != 'frozen_bge_zh_logistic_binary' or meta.get('labels') != list(LABELS) or meta.get('feature_version') != 1:
            raise ValueError('unsupported semantic model schema')
        head = DenseHead()
        with np.load(path/'weights.npz',allow_pickle=False) as data:
            head.weights, head.mean = data['weights'].copy(), data['mean'].copy()
            bias,scale = data['bias'].copy(),data['scale'].copy()
        if head.weights.shape!=(512,) or head.mean.shape!=(512,) or bias.shape!=(1,) or scale.shape!=(1,):
            raise ValueError('invalid semantic model shapes')
        if not all(np.isfinite(a).all() for a in (head.weights,head.mean,bias,scale)) or scale[0]<=0:
            raise ValueError('invalid semantic model values')
        head.bias,head.scale = float(bias[0]),float(scale[0])
        encoder_directory = (path/meta['encoder_path']).resolve()
        if encoder is not None and encoder.directory != encoder_directory:
            raise ValueError('encoder path does not match the trained model')
        return cls(encoder or ChineseEncoder(encoder_directory),head)

