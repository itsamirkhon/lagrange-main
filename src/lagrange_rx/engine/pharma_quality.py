"""Conservative FEFO quality gate for *existing* warehouse batches.

This is a current-stock allocation/audit, NOT a claim of time-indexed
multi-month replenishment optimization. It intentionally cannot infer whether a
lot is released, cold-chain compliant or saleable from its mere existence.
"""
from __future__ import annotations

from datetime import date
from pydantic import BaseModel, ConfigDict, Field

from lagrange_rx import ontology


class QualityBlocked(ValueError):
    pass


class FEFOAllocation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    lot_number: str
    packs: int = Field(gt=0)
    expiry_date: date


class FEFOResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    product: str
    as_of: date
    requested_packs: int = Field(ge=0)
    allocated_packs: int = Field(ge=0)
    lines: tuple[FEFOAllocation, ...]
    rejected_lots: tuple[str, ...]
    shortfall_packs: int = Field(ge=0)


def allocate_released_fefo(
    graph: ontology.ClientGraph, product: str, requested_packs: int, as_of: date,
) -> FEFOResult:
    """Allocate remaining stock by earliest expiry without using blocked lots.

    A batch is eligible only if released, not expired, and its remaining shelf
    life satisfies a product-specific confirmed minimum. Without that minimum,
    we cannot claim any customer-specific shelf-life acceptance threshold.
    """
    graph.for_calculation()
    if requested_packs < 0:
        raise ValueError("requested_packs must be nonnegative")
    sku = next((p for p in graph.products if p.sku_id == product), None)
    if sku is None:
        raise QualityBlocked("product not found")
    if sku.min_remaining_shelf_life_days is None:
        raise QualityBlocked("product minimum remaining shelf life is unknown; cannot certify stock eligibility")

    usable = []
    rejected = []
    for lot in graph.lots:
        if lot.product != product:
            continue
        remaining = (lot.expiry_date - as_of).days
        if (lot.quality_status != "released"
                or remaining < sku.min_remaining_shelf_life_days
                or lot.remaining_packs == 0):
            rejected.append(lot.lot_number)
            continue
        usable.append(lot)
    usable.sort(key=lambda l: (l.expiry_date, l.lot_number))
    left = requested_packs
    lines = []
    for lot in usable:
        if not left:
            break
        take = min(left, lot.remaining_packs)
        if take:
            lines.append(FEFOAllocation(lot_number=lot.lot_number, packs=take, expiry_date=lot.expiry_date))
            left -= take
    return FEFOResult(product=product, as_of=as_of, requested_packs=requested_packs,
                      allocated_packs=requested_packs-left,
                      lines=tuple(lines), rejected_lots=tuple(sorted(rejected)), shortfall_packs=left)
