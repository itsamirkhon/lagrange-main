"""Quality checks over observed, already-normalized facts.

These functions propose issues; they never change or confirm an observation.
Callers must compare related records and compatible units/currencies only.
"""
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from itertools import combinations
import re
from typing import Any


@dataclass(frozen=True)
class Observation:
    ref: str
    value: Any
    evidence: list[dict] = field(default_factory=list)


def _issue(kind: str, observations: list[Observation], detail: str) -> dict:
    return {'issue_type': kind, 'severity': 'warning',
            'affected': [observation.ref for observation in observations],
            'evidence': [item for observation in observations for item in observation.evidence],
            'detail': detail, 'resolution': 'unresolved'}


def _number(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _arithmetic(target: Observation, parts: list[Observation], signs: list[int], tolerance: Decimal) -> dict | None:
    values = [_number(item.value) for item in [target, *parts]]
    if not parts or any(value is None for value in values):
        return None
    expected = sum((value * sign for value, sign in zip(values[1:], signs)), Decimal(0))
    if abs(values[0] - expected) <= tolerance:
        return None
    return _issue('arithmetic', [target, *parts], f'Observed {values[0]}; calculated {expected}')


def check_sum(total: Observation, lines: list[Observation], tolerance: Decimal = Decimal('0.01')) -> dict | None:
    """Compare a stated total to a complete list of observed line amounts."""
    return _arithmetic(total, lines, [1] * len(lines), tolerance)


def check_stock_balance(closing: Observation, opening: Observation, incoming: Observation,
                        outgoing: Observation, tolerance: Decimal = Decimal('0.01')) -> dict | None:
    return _arithmetic(closing, [opening, incoming, outgoing], [1, 1, -1], tolerance)


def _month_index(value: str) -> int:
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', value):
        raise ValueError('month must be YYYY-MM')
    year, month = map(int, value.split('-'))
    return year * 12 + month - 1


def check_periods(ref: str, observed: list[str], start: str, end: str) -> list[dict]:
    """Find missing months within a caller-supplied expected inclusive interval."""
    first, last = _month_index(start), _month_index(end)
    if first > last:
        raise ValueError('start must not be after end')
    present = {_month_index(month) for month in observed}
    issues = []
    for month in range(first, last + 1):
        if month not in present:
            label = f'{month // 12:04d}-{month % 12 + 1:02d}'
            issues.append(_issue('missing', [Observation(ref, None)], f'Missing period {label}'))
    return issues


def check_contradictions(left: dict[str, Observation], right: dict[str, Observation]) -> list[dict]:
    """Compare shared normalized fields of records the caller has linked."""
    issues = []
    for name in sorted(left.keys() & right.keys()):
        a, b = left[name], right[name]
        if a.value is not None and b.value is not None and a.value != '' and b.value != '' and a.value != b.value:
            issues.append(_issue('contradiction', [a, b], f'Conflicting values for {name}'))
    return issues


def check_duplicates(documents: list[dict], key_fields: list[str]) -> list[dict]:
    """Flag exact content hashes or complete matching caller-selected key fields.

    Key matches are possible duplicates, not proof of duplicate bytes.
    """
    issues = []
    for a, b in combinations(documents, 2):
        same_hash = bool(a.get('hash')) and a.get('hash') == b.get('hash')
        same_keys = bool(key_fields) and all(a.get(key) not in (None, '') and a.get(key) == b.get(key) for key in key_fields)
        if same_hash or same_keys:
            reason = 'Identical content hash' if same_hash else 'Possible duplicate: matching key fields'
            observations = [Observation(doc['id'], None, doc.get('evidence', [])) for doc in (a, b)]
            issues.append(_issue('duplicate', observations, reason))
    return issues
