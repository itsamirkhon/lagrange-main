"""Apply human answers to a Draft and commit a validated ClientGraph.

Only confirmed groups enter the graph. Cost attribution and addendum start dates come from
the answers; an unanswered question keeps its items out. Dangling references are pruned
(and reported), so the result always passes `ClientGraph.for_calculation()`.
"""
from __future__ import annotations

from datetime import date, datetime
from collections import defaultdict

from pydantic import BaseModel

from ...ontology import ClientGraph, CostItem, DataQualityIssue, Provenance
from .draft import Draft, stamp


def _confirm(value, by: str, at: datetime):
    if isinstance(value, Provenance):
        if value.kind == 'extracted_unconfirmed':
            return value.model_copy(update={'kind': 'extracted_confirmed', 'confirmed_by': by, 'confirmed_at': at})
        return value
    if isinstance(value, BaseModel):
        return value.model_copy(update={n: _confirm(getattr(value, n), by, at) for n in type(value).model_fields})
    if isinstance(value, dict):
        return {k: _confirm(v, by, at) for k, v in value.items()}
    if isinstance(value, list):
        return [_confirm(v, by, at) for v in value]
    return value


def _attribute(entry, answer: dict):
    allocation = answer['allocation']
    update = {'allocation': allocation, 'product': answer.get('product') if allocation == 'direct' else None,
              'scope': answer.get('scope') if allocation.startswith('shared') else None}
    return CostItem.model_validate({**entry.entity.model_dump(), **update})


def _prune(collections: dict, dropped: list) -> None:
    """Remove entities whose references point at removed ones, until stable."""
    keys, refs = ClientGraph._keys, ClientGraph._refs
    while True:
        present = {c: {getattr(e, keys[c]) for e in items} for c, items in collections.items() if c in keys}
        removed = False
        for collection, fields in refs.items():
            kept = []
            for item in collections.get(collection, []):
                bad = [(f, v) for f, target in fields.items() for v in _as_list(getattr(item, f))
                       if v is not None and v not in present.get(target, set())]
                if bad:
                    dropped.append({'collection': collection, 'key': str(getattr(item, keys.get(collection, ''), '')),
                                    'reason': f'dangling reference {bad[0][0]}={bad[0][1]}'})
                    removed = True
                else:
                    kept.append(item)
            if collection in collections:
                collections[collection] = kept
        if not removed:
            return


def _as_list(value):
    return value if isinstance(value, list) else [value]


def commit(draft: Draft, answers: list[dict], now: datetime | None = None) -> tuple[ClientGraph, dict]:
    at = stamp(now)
    by_id = {a['question_id']: a for a in answers}
    confirmed = {q['group'] for q in draft.questions
                 if q['kind'] == 'confirm_group' and by_id.get(q['question_id'], {}).get('answer', {}).get('decision') == 'confirm'}
    dropped = []
    collections = defaultdict(list)
    for e in draft.entries:
        who = by_id.get(f'confirm_group:{e.group}', {}).get('answered_by', 'unknown')
        if e.group not in confirmed:
            dropped.append({'collection': e.collection, 'reason': f'group {e.group} not confirmed'})
            continue
        entity = e.entity
        if e.collection == 'cost_items':
            a = by_id.get(f"cost_attribution:{e.meta['label']}")
            if a is None:
                dropped.append({'collection': 'cost_items', 'reason': f"attribution of {e.meta['label']!r} unanswered"})
                continue
            entity = _attribute(e, a['answer'])
        elif e.collection == 'contracts' and f'contract_effective_from:{entity.contract_id}' in {q['question_id'] for q in draft.questions}:
            a = by_id.get(f'contract_effective_from:{entity.contract_id}')
            if a is None:
                dropped.append({'collection': 'contracts', 'key': entity.contract_id, 'reason': 'effective date unanswered'})
                continue
            entity = entity.model_copy(update={
                'valid_from': date.fromisoformat(a['answer']['valid_from']),
                'field_provenance': {**entity.field_provenance, 'valid_from': Provenance(
                    kind='user_provided', source='confirmation answer', confirmed_by=a.get('answered_by'), confirmed_at=at)}})
        collections[e.collection].append(_confirm(entity, who, at))
    _prune(collections, dropped)
    issues = [DataQualityIssue(issue_type=i['issue_type'], severity=i['severity'], affected=i['affected'] or [i['message'][:60]],
                               provenance=Provenance(kind='assumption', source='ingest.offline', note=i['message']))
              for i in draft.issues]
    graph = ClientGraph(source_documents=draft.source_documents, issues=issues, **collections)
    graph.for_calculation()
    return graph, {'dropped': dropped, 'confirmed_groups': sorted(confirmed),
                   'counts': {c: len(getattr(graph, c)) for c in ClientGraph.model_fields if getattr(graph, c)}}
