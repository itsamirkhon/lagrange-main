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


def _candidate(source: Entity, target: Entity) -> Candidate | None:
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


def resolve(source: Entity, targets: list[Entity]) -> Resolution:
    """Rank up to ten blocked candidates and surface ambiguity for Q2 review."""
    ids = [target.id for target in targets]
    if len(ids) != len(set(ids)):
        raise ValueError('target ids must be unique')
    candidates = [candidate for target in targets if (candidate := _candidate(source, target))]
    candidates.sort(key=lambda candidate: (-candidate.confidence, candidate.id))
    candidates = candidates[:10]
    confident = bool(candidates and candidates[0].confidence >= 0.9 and
                     (len(candidates) == 1 or candidates[0].confidence - candidates[1].confidence >= 0.1))
    return Resolution(candidates, candidates[0] if confident else None, bool(candidates) and not confident)
