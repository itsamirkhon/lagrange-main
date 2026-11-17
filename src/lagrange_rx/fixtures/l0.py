"""L0 fixture: a ClientGraph assembled by hand (stand-in for the Ingest output).

SYNTHETIC / ILLUSTRATIVE: numbers are made up to be internally consistent; the 'actual' accounting result is
constructed so that the client's margin is ~2.5%. Calibration on this fixture validates plumbing, not real-world
fidelity. Replace with the confirmed ClientGraph from Ingest.
"""
from __future__ import annotations

from lagrange_rx.engine.twin import _historical_cost, last_12_months
from lagrange_rx.ontology import (
    ActualResult, ClientGraph, CostItem, DemandForecast, Finance, Product, Route, Sale, Shipment, Supplier,
)

TARGET_MARGIN = 0.025
HORIZON_START = "2026-11"
HISTORY_MONTHS = [f"2025-{m:02d}" for m in range(11, 13)] + [f"2026-{m:02d}" for m in range(1, 11)]


def _routes() -> list[Route]:
    return [
        Route(id="R-SEA-A", supplier_id="SUP-A", name="Sea, India -> Tashkent", mode="sea", currency="USD",
              kc=4.80, duty_rate=0.10, fees_uzs=1400.0, freight_uzs=2600.0, lot_size=1000, min_batch=10_000,
              max_batch=30_000, capacity_packs=90_000, max_shipments=6, fixed_per_shipment_uzs=8_000_000.0,
              fixed_activation_uzs=2_000_000.0, lead_time_days=45, supplier_terms_days=45),
        Route(id="R-RAIL-B", supplier_id="SUP-B", name="Rail, Europe -> Tashkent", mode="rail", currency="EUR",
              kc=4.10, duty_rate=0.10, fees_uzs=1400.0, freight_uzs=2300.0, lot_size=1000, min_batch=5_000,
              max_batch=20_000, capacity_packs=50_000, max_shipments=8, fixed_per_shipment_uzs=8_000_000.0,
              fixed_activation_uzs=2_000_000.0, lead_time_days=45, supplier_terms_days=45),
        Route(id="R-AIR-C", supplier_id="SUP-C", name="Air, India -> Tashkent", mode="air", currency="USD",
              kc=4.95, duty_rate=0.10, fees_uzs=1400.0, freight_uzs=7000.0, lot_size=1000, min_batch=1_000,
              max_batch=8_000, capacity_packs=30_000, max_shipments=10, fixed_per_shipment_uzs=3_000_000.0,
              fixed_activation_uzs=1_000_000.0, lead_time_days=7, supplier_terms_days=45),
        Route(id="R-AIR-EMERG", supplier_id="SUP-C", name="Emergency air top-up", mode="air", currency="USD",
              kc=4.95, duty_rate=0.10, fees_uzs=1400.0, freight_uzs=9000.0, lot_size=1000, min_batch=0,
              max_batch=8_000, capacity_packs=0, max_shipments=0, lead_time_days=7, emergency=True,
              emergency_premium_uzs=6000.0, emergency_cap_packs=8_000),
        Route(id="R-ROAD-X", supplier_id="SUP-A", name="Road (unconfirmed quote)", mode="road", currency="USD",
              kc=4.60, duty_rate=0.10, fees_uzs=1400.0, freight_uzs=3500.0, lot_size=1000, min_batch=5_000,
              max_batch=15_000, capacity_packs=30_000, max_shipments=4, confirmed=False),
    ]


def _history() -> tuple[list[Shipment], list[Sale]]:
    ships = []
    for i, month in enumerate(["2025-11", "2026-02", "2026-05", "2026-08"]):
        ships.append(Shipment(id=f"SH-A{i}", route_id="R-SEA-A", month=month, packs=20_000, fx_uzs=11_900.0 + 40 * i))
    for i, month in enumerate(["2026-01", "2026-07"]):
        ships.append(Shipment(id=f"SH-B{i}", route_id="R-RAIL-B", month=month, packs=15_000, fx_uzs=13_050.0 + 60 * i))
    return ships, []


def build_l0_graph(price_ratio_of_cap: float | None = None) -> ClientGraph:
    ships, _ = _history()
    base = dict(
        client_id="L0-demo-client",
        product=Product(id="SKU-CARDIO-1", name="Cardio Rx 30 tab", reference_currency="USD",
                        chun_uzs=112_000.0, chchn_uzs=150_000.0, rn_foreign=7.0),
        suppliers=[Supplier(id="SUP-A", name="Supplier A", reliability=0.93),
                   Supplier(id="SUP-B", name="Supplier B", reliability=0.96),
                   Supplier(id="SUP-C", name="Supplier C", reliability=0.98)],
        routes=_routes(),
        cost_items=[
            CostItem(id="AC-1", name="insurance", layer="acquisition", route_id="R-SEA-A", per_pack_uzs=800.0),
            CostItem(id="AC-2", name="insurance", layer="acquisition", route_id="R-RAIL-B", per_pack_uzs=800.0),
            CostItem(id="AC-3", name="insurance", layer="acquisition", route_id="R-AIR-C", per_pack_uzs=800.0),
            CostItem(id="BL-1", name="warehouse variable", layer="below_line", per_pack_uzs=300.0),
            CostItem(id="BL-2", name="sales team (shared by volume)", layer="below_line", annual_uzs=240_000_000.0,
                     allocation="shared_by_volume", share=0.25),
        ],
        demand=DemandForecast(p10=95_000, p50=110_000, p90=128_000),
        shipments=ships,
        finance=Finance(credit_rate_annual=0.14, budget_uzs=9_000_000_000.0, inventory_days=30, receivable_days=30,
                        hedge_forward_premium=0.02),
        nominal_fx={"USD": 12_000.0, "EUR": 13_100.0},
        horizon_start=HORIZON_START, penalty_uzs_per_pack=3000.0,
        fixture_note="SYNTHETIC hand-assembled L0 fixture, not real client data",
    )
    sales_packs = [9_000] * 12
    provisional = ClientGraph(**base).for_calculation()
    cost = _historical_cost(provisional, last_12_months(provisional, provisional.shipments),
                            [Sale(month=m, packs=p, price_uzs=1.0) for m, p in zip(HISTORY_MONTHS, sales_packs)])
    revenue_needed = cost / (1.0 - TARGET_MARGIN)
    price = revenue_needed / sum(sales_packs)
    sales = [Sale(month=m, packs=p, price_uzs=price) for m, p in zip(HISTORY_MONTHS, sales_packs)]
    revenue = sum(s.packs * s.price_uzs for s in sales)
    # accounting differs from the twin by a small deterministic residual (0.04 pp of revenue)
    actual = ActualResult(revenue_uzs=revenue, total_cost_uzs=cost + 0.0004 * revenue)
    return ClientGraph(**{**base, "sales": sales, "actual": actual})
