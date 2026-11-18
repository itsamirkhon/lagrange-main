"""Conservative, deterministic entity-resolution suggestions.

Scores are ranking heuristics, not calibrated probabilities. A result never
becomes a confirmed graph edge merely by being returned here.
"""
from dataclasses import dataclass
import datetime as dt
from math import isfinite
import re

from .normalize import normalize_name
from .similarity import similarity


@dataclass(frozen=True)
class Entity:
    id: str
    lot_number: str | None = None
    invoice_number: str | None = None
    contract_number: str | None = None
    counterparty: str | None = None
    name: str | None = None
    date: dt.date | None = None
    quantity: float | None = None

    def __post_init__(self):
        if not self.id:
            raise ValueError('entity id is required')
        if self.quantity is not None and (not isfinite(self.quantity) or self.quantity < 0):
            raise ValueError('quantity must be finite and non-negative')
        if self.date is not None and not isinstance(self.date, dt.date):
            raise ValueError('date must be a date')


@dataclass(frozen=True)
class Candidate:
    id: str
    confidence: float
    features: tuple[str, ...]


@dataclass(frozen=True)
class Resolution:
    candidates: list[Candidate]
    match: Candidate | None
    needs_review: bool

    @property
    def options(self) -> list[str]:
        return [candidate.id for candidate in self.candidates] + ['none_of_these'] if self.needs_review else []


def _identifier(value: str) -> str:
    return re.sub(r'[^\w]+', '', value.casefold())


EMBEDDING_THRESHOLD = 0.85  # cosine; pair-level precision sweep in eval/embeddings_report.json (0.80 gave precision 0.45 on regions)


def _digits(text: str) -> list[str]:
    return sorted(re.findall(r'\d+(?:[.,]\d+)?', text))


def _embedding_match(left: str, right: str, scores: dict) -> bool:
    """Embeddings may relate names, never numbers: differing doses or counts (10 mg vs 20 mg) veto the match."""
    if _digits(left) != _digits(right):
        return False
    return scores.get((left, right), 0.0) >= EMBEDDING_THRESHOLD


def _candidate(source: Entity, target: Entity, scores: dict | None = None) -> Candidate | None:
    features = []
    score = 0.0
    for field in ('lot_number', 'invoice_number', 'contract_number'):
        left, right = getattr(source, field), getattr(target, field)
        if left and right:
            # A conflicting identifier is stronger evidence than a similar name.
            if _identifier(left) != _identifier(right):
                return None
            features.append(field)
            score = max(score, 0.90)
    for field in ('counterparty', 'name'):
        left, right = getattr(source, field), getattr(target, field)
        if left and right:
            a, b = normalize_name(left), normalize_name(right)
            if a and b and (a == b or similarity(left, right) >= 0.75):
                features.append(field)
                score = max(score, 0.55)
            elif scores and _embedding_match(left, right, scores):
                features.append(field + '~embedding')
                score = max(score, 0.55)
    if not features:
        return None
    if source.date and target.date and abs((source.date - target.date).days) <= 7:
        features.append('date±7d')
        score += 0.04
    if source.quantity is not None and target.quantity is not None:
        denominator = max(source.quantity, target.quantity)
        if denominator == 0 or abs(source.quantity - target.quantity) / denominator <= 0.05:
            features.append('quantity±5%')
            score += 0.04
    return Candidate(target.id, min(score, 1.0), tuple(features))


def _embedding_scores(source: Entity, targets: list[Entity], embedder) -> dict:
    scores = {}
    for field in ('counterparty', 'name'):
        left = getattr(source, field)
        names = sorted({getattr(t, field) for t in targets if getattr(t, field)})
        if left and names:
            for name, value in zip(names, embedder.similarities(left, names)):
                scores[(left, name)] = value
    return scores


def resolve(source: Entity, targets: list[Entity], embedder=None) -> Resolution:
    """Rank up to ten blocked candidates and surface ambiguity for Q2 review.

    ``embedder`` (optional) adds a name-similarity feature for names the n-gram baseline misses;
    a failing embedder is ignored so the deterministic result stands.
    """
    ids = [target.id for target in targets]
    if len(ids) != len(set(ids)):
        raise ValueError('target ids must be unique')
    scores = None
    if embedder is not None:
        try:
            scores = _embedding_scores(source, targets, embedder)
        except Exception:  # replay miss, budget, network
            scores = None
    candidates = [candidate for target in targets if (candidate := _candidate(source, target, scores))]
    candidates.sort(key=lambda candidate: (-candidate.confidence, candidate.id))
    candidates = candidates[:10]
    confident = bool(candidates and candidates[0].confidence >= 0.9 and
                     (len(candidates) == 1 or candidates[0].confidence - candidates[1].confidence >= 0.1))
    return Resolution(candidates, candidates[0] if confident else None, bool(candidates) and not confident)
