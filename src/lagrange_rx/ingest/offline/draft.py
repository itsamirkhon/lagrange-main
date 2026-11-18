"""Draft = unconfirmed ontology entities grouped for confirmation, plus questions and findings."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel

from ...ontology import Provenance
from ..normalize import normalize_name


@dataclass
class Entry:
    collection: str
    entity: BaseModel
    group: str
    meta: dict = field(default_factory=dict)


@dataclass
class Draft:
    entries: list = field(default_factory=list)
    source_documents: list = field(default_factory=list)
    questions: list = field(default_factory=list)
    issues: list = field(default_factory=list)
    links: dict = field(default_factory=dict)
    not_extracted: list = field(default_factory=list)

    def add(self, collection: str, entity: BaseModel, group: str, **meta) -> BaseModel:
        self.entries.append(Entry(collection, entity, group, meta))
        return entity

    def of(self, collection: str):
        return [e.entity for e in self.entries if e.collection == collection]

    def issue(self, issue_type: str, severity: str, message: str, affected: list[str]) -> None:
        self.issues.append({'issue_type': issue_type, 'severity': severity, 'message': message, 'affected': affected})

    def missing(self, what: str, why: str) -> None:
        self.not_extracted.append({'what': what, 'why': why})


def unconfirmed(doc: 'object', note: str = '') -> Provenance:
    return Provenance(kind='extracted_unconfirmed', source=doc.name, source_document=doc.document_id, note=note or None)


def assumed(note: str, doc=None) -> Provenance:
    return Provenance(kind='assumption', source=doc.name if doc else 'ingest.offline', source_document=doc.document_id if doc else None, note=note)


def slug(text: str) -> str:
    return re.sub(r'\s+', '-', normalize_name(text)).strip('-')


def question(kind: str, key: str, prompt: str, facts: dict, options, group: str | None = None) -> dict:
    return {'question_id': f'{kind}:{key}', 'kind': kind, 'group': group, 'prompt': prompt, 'facts': facts,
            'options': options, 'status': 'pending'}


def stamp(now: datetime | None) -> datetime:
    return now or datetime.now().astimezone()
