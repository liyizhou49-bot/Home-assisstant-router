from .classifier import BinaryClassifier
import json
from pathlib import Path


class Router:
    """Only classify the original text. No devices, grammars, slots or actions."""

    def __init__(self, model='models/binary_v1', classifier=None):
        if classifier is not None:
            self.classifier = classifier
        else:
            meta = json.loads((Path(model)/'metadata.json').read_text(encoding='utf-8'))
            if meta.get('backend') == 'frozen_bge_zh_logistic_binary':
                from .semantic import SemanticClassifier
                self.classifier = SemanticClassifier.load(model)
            elif meta.get('backend') == 'hybrid_tfidf_bge_binary':
                from .hybrid import HybridClassifier
                self.classifier = HybridClassifier.load(model)
            else:
                self.classifier = BinaryClassifier.load(model)

    def route(self, text):
        scores = self.classifier.predict(text)
        route = 'llm' if scores['llm'] >= getattr(self.classifier,'threshold',.5) else 'hassil'
        return {'route':route,'scores':scores}
