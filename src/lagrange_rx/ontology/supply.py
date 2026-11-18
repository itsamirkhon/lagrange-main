"""Domain models for supply."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import Field, computed_field, model_validator
from .trust import Entity, Evidence, StrictModel

class PaymentTerms(Entity):
    supplier_days: int = Field(ge=0)
    customer_days: int | None = Field(default=None, ge=0)
    prepayment_share: Decimal = Field(ge=0, le=1)

class Supplier(Entity):
    supplier_id: str
    name: str
    country: str
    payment_terms: PaymentTerms
    reliability: float | None = Field(default=None, ge=0, le=1)

class ProductionSite(Entity):
    site_id: str
    supplier: str
    country: str
    approved_for: list[str]

class Carrier(Entity):
    carrier_id: str
    name: str
    modes: list[Literal['air','road','rail','multimodal']]

class LeadTime(StrictModel):
    p50: int = Field(ge=0)
    p90: int | None = Field(default=None, ge=0)

class Route(Entity):
    route_id: str = Field(validation_alias='route_id')
    # None = not stated in any document (never guessed); see docs/MONOREPO.md stage 6a.
    production_site: str | None = None
    carrier: str
    transport_mode: Literal['air','road','rail','multimodal']
    incoterms: str | None = None
    customs_post: str
    lead_time_days: LeadTime
    freight_per_shipment_uzs: Decimal | None = Field(default=None, ge=0)
    freight_per_pack_uzs: Decimal | None = Field(default=None, ge=0)
    freight_per_shipment_usd: Decimal | None = Field(default=None, ge=0)
    freight_per_pack_usd: Decimal | None = Field(default=None, ge=0)
    min_shipment_packs: int | None = Field(default=None, ge=0)
    max_shipment_packs: int | None = Field(default=None, ge=0)
    max_shipments_per_year: int | None = Field(default=None, ge=0)
    capacity_packs_per_year: int | None = Field(default=None, ge=0)
    activation_cost_uzs: Decimal | None = Field(default=None, ge=0)
    is_eligible: bool | None = None
    # Real measured min/max temperatures, linked to a reviewed source document.
    # Missing evidence is NOT compliant for temperature-sensitive products.
    temperature_record_status: Literal['missing','verified','excursion'] = 'missing'
    temperature_record_document: str | None = None
    observed_min_c: float | None = None
    observed_max_c: float | None = None

    @model_validator(mode="after")
    def check_temperature_evidence(self):
        if self.temperature_record_status == 'verified':
            if not self.temperature_record_document or self.observed_min_c is None or self.observed_max_c is None:
                raise ValueError("verified temperature requires a source document and min/max readings")
        if self.observed_min_c is not None and self.observed_max_c is not None and self.observed_min_c > self.observed_max_c:
            raise ValueError("observed temperature minimum exceeds maximum")
        return self

class Quote(Entity):
    quote_id: str
    supplier: str | None = None
    carrier: str | None = None
    product: str | None = None
    route_code: str | None = None
    issued_on: date | None = None
    valid_until: date | None = None
    unit_price: Decimal = Field(ge=0)
    currency: str
    incoterms: str | None = None
    moq_packs: int | None = Field(default=None, ge=0)
    lead_time_days: int = Field(ge=0)
    min_remaining_shelf_life_months: int | None = Field(default=None, ge=0)
    vat_status: Literal['incl','excl','unknown']
    evidence: dict[str, Evidence] = Field(default_factory=dict)

class Contract(Entity):
    contract_id: str
    supplier: str
    version: str
    signed_on: date
    valid_from: date
    valid_to: date
    unit_price: Decimal = Field(ge=0)
    currency: str
    moq_packs: int = Field(ge=0)
    payment_terms: PaymentTerms
    supersedes: str | None = None

class Shipment(Entity):
    shipment_id: str
    route: str
    dispatch_date: date
    arrival_date: date
    packs: int = Field(gt=0)
    actual_freight_uzs: Decimal | None = Field(default=None, ge=0)
    lots: list[str] = Field(default_factory=list)
    customs_entry: str

class Lot(Entity):
    lot_number: str
    product: str
    production_date: date
    expiry_date: date
    qty_packs: int = Field(ge=0)
    shipment: str
    remaining_packs: int = Field(ge=0)
    # Unknown is deliberately not released for dispensing or FEFO.
    quality_status: Literal['unknown','released','quarantined','recalled'] = 'unknown'

class CustomsEntry(Entity):
    entry_ref: str
    entry_date: date
    cip_value_fx: Decimal = Field(ge=0)
    currency: str
    fx_rate_on_entry: Decimal = Field(gt=0)
    duty_uzs: Decimal = Field(ge=0)
    fees_uzs: Decimal = Field(ge=0)
    vat_uzs: Decimal | None = Field(default=None, ge=0)
    packs: int = Field(gt=0)
    @computed_field
    @property
    def legal_base_c_per_pack(self) -> Decimal:
        return (self.cip_value_fx * self.fx_rate_on_entry + self.duty_uzs + self.fees_uzs) / self.packs
