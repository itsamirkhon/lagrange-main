"""Domain models for finance."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import Field
from .trust import Entity

class Currency(Entity):
    code: str = Field(pattern=r'^[A-Z]{3}$')

class FxRate(Entity):
    currency: str
    date: date
    rate_uzs: Decimal = Field(gt=0)
    source: str

class CostItem(Entity):
    category: Literal['freight','customs','certification','other_acquisition','distribution','marketing','warehousing','financing','write_off','g_and_a']
    layer: Literal['acquisition','below_line']
    amount_uzs: Decimal = Field(ge=0)
    period: str
    driver: Literal['per_pack','per_shipment','fixed','per_uzs_financed']
    shipment: str | None = None

class CreditLine(Entity):
    limit_uzs: Decimal = Field(ge=0)
    annual_rate: Decimal = Field(ge=0)
    available_from: date

class FxHedge(Entity):
    currency: str
    share: Decimal = Field(ge=0, le=1)
    forward_rate: Decimal = Field(gt=0)
    tenor_days: int = Field(ge=0)
