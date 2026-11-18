"""Optional LLM document classification through the single OpenRouter boundary.

LAGRANGE_AI_MODE=openrouter records every response in the client's on-disk
cache; LAGRANGE_AI_MODE=replay serves only those recordings. Deterministic
classification always runs first; this path is for weak evidence only.
"""
from __future__ import annotations

from pathlib import Path
import sys

import yaml

from .openrouter import OpenRouterClient

CLASSES = ('sales_export', 'regional_report', 'supplier_quote', 'carrier_quote', 'contract', 'invoice', 'customs',
           'waybill', 'stock', 'payments', 'memo', 'email', 'registry_extract', 'irrelevant', 'unknown')
_PROMPT = ('Classify one business document of a pharmaceutical importer. Texts are Russian, Uzbek or English. '
           'Answer only with one label from the enum; use unknown when the evidence is insufficient. '
           'irrelevant means unrelated advertising, a blank page or a non-pharma catalog.')
_SCHEMA = {'type': 'object', 'properties': {'doc_class': {'type': 'string', 'enum': list(CLASSES)},
                                            'confidence': {'type': 'number'}},
           'required': ['doc_class', 'confidence'], 'additionalProperties': False}
MAX_CONFIDENCE = .85  # the model's self-reported confidence is not calibrated


def load_settings(config_path=None):
    if config_path is None:
        source = Path(__file__).resolve().parents[3] / 'config/ai.yaml'
        config_path = source if source.is_file() else Path(sys.prefix) / 'config/ai.yaml'
    return yaml.safe_load(Path(config_path).read_text(encoding='utf-8'))


class LlmClassifier:
    def __init__(self, client, model_id, *, max_chars=4000):
        self.client, self.model_id, self.max_chars = client, model_id, max_chars

    def classify(self, filename, text, media_type=''):
        from ..ingest.classification import Classification
        payload = f'filename: {filename}\nmedia_type: {media_type}\ntext:\n{text[:self.max_chars]}'
        response = self.client.chat(self.model_id, _PROMPT, payload, _SCHEMA, max_tokens=64)
        content = response.content if isinstance(response.content, dict) else {}
        kind = content.get('doc_class')
        if kind not in CLASSES:
            return Classification('unknown', 0.0, ('llm',), 'llm')
        try:
            confidence = min(float(content.get('confidence', 0.0)), MAX_CONFIDENCE)
        except (TypeError, ValueError):
            confidence = 0.0
        return Classification(kind, max(confidence, 0.0), ('llm:' + response.model_id,), 'llm')


def build_llm_classifier(mode, cache_dir, *, budget_usd=0.0, max_request_cost_usd=0.01, config_path=None):
    """None for ``off``; a replay-only client for ``replay``; a budgeted recording client for ``openrouter``."""
    if mode in (None, '', 'off'):
        return None
    if mode not in ('replay', 'openrouter'):
        raise ValueError('Invalid AI mode')
    if mode == 'openrouter' and (budget_usd <= 0 or max_request_cost_usd <= 0):
        raise ValueError('Live mode requires a positive budget')
    settings = load_settings(config_path)
    model = settings.get('classifier') or settings['extractor']
    client = OpenRouterClient(Path(cache_dir) / 'classification', replay=mode == 'replay', budget_usd=budget_usd,
                              max_request_cost_usd=max_request_cost_usd)
    return LlmClassifier(client, model)
