"""Domain models for demand."""
from __future__ import annotations

from decimal import Decimal
from typing import Literal
from pydantic import Field
from .trust import Entity

class Region(Entity):
    region_id: str
    name: str

class Warehouse(Entity):
    warehouse_id: str
    region: str
    capacity_packs: int = Field(ge=0)
    is_commingled: bool

class Channel(Entity):
    name: Literal['distributors','pharmacy_chains','hospital_tenders']

class Customer(Entity):
    name: str
    channel: str
    region: str

class SalesRecord(Entity):
    period: str
    product: str
    region: str
    channel: str
    qty_packs: int = Field(ge=0)
    price_ex_vat: Decimal = Field(ge=0)
    lot: str | None = None

class DemandForecast(Entity):
    period: str
    product: str
    p10: Decimal = Field(ge=0)
    p50: Decimal = Field(ge=0)
    p90: Decimal = Field(ge=0)
    method: str
    coverage_calibrated: bool
