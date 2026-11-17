"""Domain models for finance."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import Field, model_validator
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
    # SKU attribution. `direct` requires `product`; shared costs are split across all
    # SKUs sold in the same period by packs (`shared_by_volume`) or by revenue ex VAT
    # (`shared_by_revenue`). `unallocated` is never silently assigned to a SKU.
    product: str | None = None
    allocation: Literal['direct','shared_by_volume','shared_by_revenue','unallocated'] = 'unallocated'

    @model_validator(mode='after')
    def _check_allocation(self) -> 'CostItem':
        if self.allocation == 'direct' and not self.product:
            raise ValueError('direct allocation requires product')
        if self.product and self.allocation != 'direct':
            raise ValueError('a cost with product must use direct allocation')
        return self

class CreditLine(Entity):
    limit_uzs: Decimal = Field(ge=0)
    annual_rate: Decimal = Field(ge=0)
    available_from: date

class FxHedge(Entity):
    currency: str
    share: Decimal = Field(ge=0, le=1)
    forward_rate: Decimal = Field(gt=0)
    tenor_days: int = Field(ge=0)
