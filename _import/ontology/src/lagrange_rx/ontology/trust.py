"""Domain models for trust."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Provenance(StrictModel):
    kind: Literal['official', 'user_provided', 'extracted_confirmed', 'extracted_unconfirmed', 'synthetic', 'assumption']
    source: str = Field(min_length=1)
    source_document: str | None = None
    retrieved_at: datetime | None = None
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    note: str | None = None

class Entity(StrictModel):
    provenance: Provenance
    field_provenance: dict[str, Provenance] = Field(default_factory=dict)

class SourceDocument(Entity):
    document_id: str
    file_name: str
    mime: str
    pages: int = Field(ge=0)
    uploaded_at: datetime
    doc_class: Literal['sales_export','regional_report','supplier_quote','carrier_quote','contract','invoice','customs','waybill','stock','payments','memo','email','registry_extract','unknown'] | None = None
    language: list[Literal['ru','uz-latn','uz-cyrl','en']] = Field(default_factory=list)

class Evidence(Entity):
    source_document: str
    page: int = Field(ge=1)
    bbox: tuple[float, float, float, float] | None = None
    text_span: str
    confidence: float = Field(ge=0, le=1)

class DataQualityIssue(Entity):
    issue_type: Literal['contradiction','missing','duplicate','currency_ambiguous','vat_ambiguous','unit_ambiguous','stale','arithmetic','unreadable','irrelevant']
    severity: Literal['blocking','warning','info']
    affected: list[str]
    evidence: list[Evidence] = Field(default_factory=list)
    resolution: Literal['confirmed_value','user_override','ignored','unresolved'] | None = None

class Snapshot(Entity):
    snapshot_id: str
    created_at: datetime
