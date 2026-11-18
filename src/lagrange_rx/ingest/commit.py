"""Build validated ClientGraph objects from reviewed proposals without guessing."""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json

from pydantic import ValidationError

from ..ontology import ClientGraph, Provenance, SourceDocument


def _collections():
    return {field.annotation.__args__[0].__name__: (name, field.annotation.__args__[0])
            for name, field in ClientGraph.model_fields.items()}


def _source(document):
    doc_class = document.get('doc_class')
    if doc_class == 'irrelevant':
        doc_class = None
    return SourceDocument(document_id=document['document_id'], file_name=document['filename'],
                          mime=document['media_type'], pages=document['pages'],
                          uploaded_at=document['uploaded_at'], doc_class=doc_class,
                          provenance=Provenance(kind='user_provided', source='document upload'))


def _group_key(item):
    fields = item.get('fields', {})
    evidence = [value.get('evidence', {}) for value in fields.values() if isinstance(value, dict)]
    locations = {(v.get('document_id'), v.get('page')) for v in evidence}
    if len(locations) != 1:
        raise ValueError('proposal must have one source document and page')
    document, page = next(iter(locations))
    if not document or not isinstance(page, int) or page < 1:
        raise ValueError('proposal is missing its source document or page')
    # Model-extracted fields without an explicit shared entity key must not be merged.
    row = item.get('source_row')
    group = ['row', row] if row is not None else ['proposal', item.get('id')]
    if group[1] is None:
        raise ValueError('proposal is missing its identity')
    return (item.get('type'), document, page, *group)


def _build(group, entries, model):
    if any(item.get('status') != 'confirmed' for item in entries):
        raise ValueError('row contains fields that are not confirmed')
    entity_type, document = group[:2]
    digest = sha256(json.dumps(group, ensure_ascii=False).encode()).hexdigest()
    provenance = Provenance(kind='extracted_confirmed', source='reviewed document', source_document=document, note='ingest-row:' + digest)
    fields = {}
    for item in entries:
        for qualified, value in item['fields'].items():
            prefix = entity_type + '.'
            if not qualified.startswith(prefix) or not isinstance(value, dict):
                raise ValueError('field does not match entity type')
            name = qualified[len(prefix):]
            if name in ('provenance', 'field_provenance') or name not in model.model_fields:
                raise ValueError('unknown or reserved field: ' + name)
            raw = value.get('corrected_value', value.get('raw'))
            if raw is None:
                raise ValueError('confirmed field has no value: ' + name)
            if name in fields and fields[name] != raw:
                raise ValueError('conflicting confirmed values: ' + name)
            fields[name] = raw
    return model.model_validate({**fields, 'provenance': provenance,
                                 'field_provenance': {name: provenance for name in fields}})


def commit_batch(batch: dict, graph: ClientGraph) -> tuple[ClientGraph, list[dict]]:
    """Return a validated new graph and explicit unresolved groups; never mutate input.

    Eligible proposals share type, source document, page and row. Fields without a
    row identity stay separate. A group containing any unconfirmed value is held.
    Candidates are added atomically only when all graph references are valid.
    """
    graph.for_calculation()
    candidate = graph.model_copy(deep=True)
    incomplete = []
    groups = defaultdict(list)
    for item in batch.get('proposed_instances', []):
        try:
            groups[_group_key(item)].append(item)
        except (ValueError, TypeError) as exc:
            incomplete.append({'proposal_id': item.get('id'), 'reason': str(exc)})
    sources = {d.get('document_id'): d for d in batch.get('documents', [])}
    existing_sources = {d.document_id for d in candidate.source_documents}
    collections = _collections()
    existing = {item.provenance.note: item for name in ClientGraph.model_fields for item in getattr(candidate, name) if item.provenance.note}
    added = []
    for group, entries in groups.items():
        try:
            entity_type, document_id = group[:2]
            if entity_type not in collections or entity_type == 'SourceDocument':
                raise ValueError('unsupported entity type')
            collection, model = collections[entity_type]
            item = _build(group, entries, model)
            if item.provenance.note in existing:
                if item != existing[item.provenance.note]:
                    raise ValueError('previously committed row has changed')
                continue
            if document_id not in existing_sources:
                if document_id not in sources:
                    raise ValueError('missing source document metadata')
                source = _source(sources[document_id])
                candidate.source_documents.append(source)
                existing_sources.add(document_id)
            getattr(candidate, collection).append(item)
            existing[item.provenance.note] = item
            added.append(group)
        except (ValueError, TypeError, KeyError, ValidationError) as exc:
            incomplete.append({'group': list(group), 'reason': str(exc)})
    try:
        candidate.for_calculation()
    except ValueError as exc:
        incomplete.extend({'group': list(group), 'reason': 'graph reference validation failed: ' + str(exc)} for group in added)
        return graph.model_copy(deep=True), incomplete
    return candidate, incomplete
