"""Domain models for regulation."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import Field, computed_field
from .trust import Entity

class Regulation(Entity):
    regulation_id: str
    title: str
    number: str
    registered_on: date
    effective_from: date
    effective_to: date | None = None
    url: str
    replaces: str | None = None

class Rule(Entity):
    rule_id: str
    regulation: str | None = None
    point: str | None = None
    kind: Literal['ceiling','markup','formula','indexation','revision','invariant','trust']
    parameters: dict = Field(default_factory=dict)
    effective_from: date | None = None
    status: Literal['verified','needs_legal_review','modeling_assumption','invariant']
    description: str

class RegistryRecord(Entity):
    product: str
    registration_number: str
    registry_date: date
    rn_fx: Decimal
    rn_currency: str
    filing_fx_rate: Decimal
    customs_b_uzs: Decimal
    bx_share: Decimal = Decimal('0.02')
    vat_rate: Decimal = Decimal('0.12')
    chun_incl_vat: Decimal
    chchn_incl_vat: Decimal
    rn_candidates: dict[str, Decimal] | None = None
    @computed_field
    @property
    def chun_formula_incl_vat(self) -> Decimal:
        return (self.rn_fx * self.filing_fx_rate * (1 + self.bx_share) + self.customs_b_uzs) * (1 + self.vat_rate) * Decimal('1.15')

class PriceIndexationEvent(Entity):
    registry_record: str
    event_type: Literal['registration','index_p20_scheduled','index_p20_immediate','revision_p22','official_cut']
    effective_date: date
    old_chun: Decimal
    new_chun: Decimal
    fx_ratio: Decimal | None = None
    reason: str
