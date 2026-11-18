"""Isolated, clearly synthetic pharmaceutical FEFO showcase.

This demo exercises the same tested allocate_released_fefo() function as
the stock-quality module. Never mixes these invented stock quantities into
client data, persistent graph storage or the procurement optimizer.
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from lagrange_rx import ontology as o
from lagrange_rx.engine.pharma_quality import allocate_released_fefo
from lagrange_rx.fixtures.l0 import SKU, build_l0_graph

# Fixed demo date makes the worked example reproducible for the mentors.
AS_OF = date(2026, 11, 18)
MIN_SHELF_DAYS = 30
# Demonstrates all common safety outcomes without claiming any client records.
DEMO_LOTS = (
    ("LOT-102", 50, 20, "released"),
    ("LOT-104", 70, 40, "released"),
    ("LOT-107", 20, 100, "released"),
    ("LOT-109", 100, 100, "recalled"),
    ("LOT-111", 90, 40, "quarantined"),
    ("LOT-115", 110, 30, "unknown"),
)


class FEFORequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requested_packs: int = Field(default=50, ge=0, le=5000)


def _scenario():
    base = build_l0_graph()
    product = next(p for p in base.products if p.sku_id == SKU)
    product = product.model_copy(update={"min_remaining_shelf_life_days": MIN_SHELF_DAYS})
    shipment = base.shipments[0].shipment_id
    lots = [
        o.Lot(
            lot_number=lot_id, product=SKU, production_date=date(2025, 1, 1),
            expiry_date=AS_OF + timedelta(days=remaining_days),
            qty_packs=quantity, remaining_packs=quantity, shipment=shipment,
            quality_status=status, provenance=base.products[0].provenance,
        )
        for lot_id, remaining_days, quantity, status in DEMO_LOTS
    ]
    return base.model_copy(update={
        "products": [product if p.sku_id == SKU else p for p in base.products],
        "lots": lots,
    })


def make_router() -> APIRouter:
    router = APIRouter()

    @router.post("/pharma/demo/fefo")
    def calculate_fefo(body: FEFORequest):
        graph = _scenario()
        result = allocate_released_fefo(graph, SKU, body.requested_packs, AS_OF)
        allocations = {line.lot_number: line.packs for line in result.lines}
        product = next(p for p in graph.products if p.sku_id == SKU)
        rows = []
        for lot in sorted(graph.lots, key=lambda x: (x.expiry_date, x.lot_number)):
            days = (lot.expiry_date - AS_OF).days
            if lot.quality_status == "recalled":
                reason = "Серия отозвана"
            elif lot.quality_status == "quarantined":
                reason = "Карантин: нет разрешения на отпуск"
            elif lot.quality_status != "released":
                reason = "Выпуск серии не подтверждён"
            elif days < MIN_SHELF_DAYS:
                reason = "Недостаточный остаточный срок годности"
            else:
                reason = None
            rows.append({
                "id": lot.lot_number,
                "expiry_date": lot.expiry_date.isoformat(),
                "days_remaining": days,
                "available_packs": lot.remaining_packs,
                "allocated_packs": allocations.get(lot.lot_number, 0),
                "status": lot.quality_status,
                "eligible": reason is None,
                "reason": reason or ("Выбрана по FEFO" if allocations.get(lot.lot_number, 0) else "Допустимый резерв"),
            })
        return {
            "synthetic": True,
            "scenario": "hand-built educational quality-control sample (not real client inventory)",
            "algorithm": "allocate_released_fefo",
            "as_of": AS_OF.isoformat(),
            "product": product.trade_name,
            "sku": SKU,
            "min_shelf_life_days": MIN_SHELF_DAYS,
            "requested_packs": result.requested_packs,
            "allocated_packs": result.allocated_packs,
            "shortfall_packs": result.shortfall_packs,
            "allocation": [line.model_dump(mode="json") for line in result.lines],
            "lots": rows,
            "rejected_count": sum(not row["eligible"] for row in rows),
            "warnings": [
                "FEFO is demonstrated for existing released stock, not 12-month procurement planning.",
                "Cold-chain compliance requires separate verified temperature evidence.",
                "All quantities and batch records on this screen are synthetic.",
            ],
        }

    return router
