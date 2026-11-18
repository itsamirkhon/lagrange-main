"""Shared hand-built fixtures (synthetic, for tests only)."""
from __future__ import annotations

import pytest

from lagrange_rx.engine.types import Scenario
from lagrange_rx.engine._legacy_ontology import (
    ClientGraph, CostItem, DemandForecast, Finance, Product, Route, Supplier,
)
from lagrange_rx.engine.rules import load_rules


def make_route(**kw) -> Route:
    base = dict(
        id="R1", supplier_id="SA", name="sea A", mode="sea", currency="USD", kc=5.0, duty_rate=0.10,
        fees_uzs=1000.0, freight_uzs=3000.0, lot_size=1000, min_batch=1000, max_batch=5000,
        capacity_packs=10000, max_shipments=4, fixed_per_shipment_uzs=2_000_000.0,
        fixed_activation_uzs=500_000.0,
    )
    base.update(kw)
    return Route(**base)


def make_graph(routes=None, **kw) -> ClientGraph:
    routes = routes if routes is not None else [make_route()]
    base = dict(
        client_id="C1",
        product=Product(id="P1", name="drug", chun_uzs=112_000.0, chchn_uzs=150_000.0),
        suppliers=[Supplier(id="SA", name="A", reliability=0.95), Supplier(id="SB", name="B", reliability=0.9)],
        routes=routes,
        cost_items=[CostItem(id="a1", name="insurance", layer="acquisition", route_id=routes[0].id, per_pack_uzs=500.0)],
        demand=DemandForecast(p10=800, p50=1000, p90=1200),
        finance=Finance(credit_rate_annual=0.0, budget_uzs=None, inventory_days=0, receivable_days=0),
        nominal_fx={"USD": 12_000.0, "EUR": 13_000.0},
        horizon_start="2026-11",
    )
    base.update(kw)
    return ClientGraph(**base)


def make_scenario(**kw) -> Scenario:
    base = dict(
        id="s1", name="flat", fx={"USD": 12_000.0, "EUR": 13_000.0}, ref_fx_start=12_000.0,
        ref_fx_path=tuple([12_000.0] * 12), demand=800.0,
    )
    base.update(kw)
    return Scenario(**base)


@pytest.fixture
def rules():
    return load_rules()


@pytest.fixture
def tiny():
    return make_graph().for_calculation()
