"""Build the decision scorer and embedder from config/ai.yaml and LAGRANGE_AI_MODE.

off          no model is contacted; callers fall back to deterministic code.
replay       only recorded responses are served (demo without network).
openrouter   live calls, recorded to the cache, bounded by a positive budget.
"""
from __future__ import annotations

from pathlib import Path

from .classify import load_settings
from .openrouter import OpenRouterClient


def build_client(mode, cache_dir, budget_usd, max_request_cost_usd):
    if mode not in ('replay', 'openrouter'):
        raise ValueError('Invalid AI mode')
    if mode == 'openrouter' and (budget_usd <= 0 or max_request_cost_usd <= 0):
        raise ValueError('Live mode requires a positive budget')
    return OpenRouterClient(cache_dir, replay=mode == 'replay', budget_usd=budget_usd, max_request_cost_usd=max_request_cost_usd)


def build_decision_scorer(mode, cache_dir, *, budget_usd=0.0, max_request_cost_usd=0.02, config_path=None, client=None):
    from ..ingest.decisions import FallbackScorer, GoogleDecisionScorer, OpenRouterDecisionScorer
    if mode in (None, '', 'off'):
        return None
    settings = load_settings(config_path)
    model = settings.get('decision_scorer')
    if not model:
        return None
    client = client or build_client(mode, Path(cache_dir) / 'decisions', budget_usd, max_request_cost_usd)
    fallback_model = settings.get('decision_fallback')
    fallback = OpenRouterDecisionScorer(client, fallback_model) if fallback_model else None
    return FallbackScorer(GoogleDecisionScorer(client, model), fallback)


class Embedder:
    """Cosine similarity over cached multilingual embeddings. A feature for names only, never a decision."""
    def __init__(self, client, model_id):
        self.client, self.model_id = client, model_id

    def vectors(self, texts):
        return self.client.embed(self.model_id, list(texts))

    def similarities(self, query, choices):
        if not choices:
            return []
        query_vector, *vectors = self.vectors([query, *choices])
        return [_cosine(query_vector, v) for v in vectors]

    def similarity(self, a, b):
        return self.similarities(a, [b])[0]


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = sum(x * x for x in a) ** .5, sum(y * y for y in b) ** .5
    return dot / (na * nb) if na and nb else 0.0


def build_embedder(mode, cache_dir, *, budget_usd=0.0, max_request_cost_usd=0.02, config_path=None, client=None):
    if mode in (None, '', 'off'):
        return None
    model = load_settings(config_path).get('embeddings')
    if not model:
        return None
    return Embedder(client or build_client(mode, Path(cache_dir) / 'embeddings', budget_usd, max_request_cost_usd), model)
