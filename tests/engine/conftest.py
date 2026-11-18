import numpy as np
import pytest

from lagrange_rx.engine.scenarios import FxHistory
from lagrange_rx.engine.inputs import (
    EngineGraph, DemandSpec, ProductSpec, RouteSpec, ShipmentSpec, SupplierSpec,
)
from lagrange_rx.engine.rules import load_rules


def _route(rid, sup, mode, ccy, **kw):
    base = dict(id=rid, supplier_id=sup, name=rid, mode=mode, currency=ccy, kc=1.0, duty_rate=0.0,
                fees_uzs=0.0, freight_uzs=100.0, lot_size=10, min_batch=0, max_batch=100000,
                capacity_packs=100000, max_shipments=12)
    base.update(kw)
    return RouteSpec(**base)


@pytest.fixture(scope="session")
def rules():
    return load_rules()


@pytest.fixture(scope="session")
def graph():
    g = EngineGraph(
        client_id="t",
        product=ProductSpec(id="p", name="p", reference_currency="USD", chun_uzs=1000, chchn_uzs=1500),
        suppliers=(SupplierSpec(id="SB", name="B", reliability=0.95), SupplierSpec(id="SA", name="A", reliability=0.8)),
        routes=(
            _route("ra_sea", "SA", "sea", "USD"),
            _route("ra_air", "SA", "air", "USD", emergency=True),
            _route("rb_rail", "SB", "rail", "EUR"),
            _route("rb_road", "SB", "road", "RUB"),
        ),
        demand=DemandSpec(p10=800_000, p50=1_000_000, p90=1_300_000),
        shipments=(
            ShipmentSpec(id="s1", route_id="ra_sea", month="2026-01", packs=1000, fx_uzs=12000),
            ShipmentSpec(id="s2", route_id="rb_rail", month="2026-02", packs=500, fx_uzs=13000),
        ),
        nominal_fx={"USD": 12700.0, "EUR": 13800.0, "RUB": 150.0},
        horizon_start="2026-11",
    )
    return g.for_calculation()


def make_history(seed=1, months=60, usd_equals_eur=False):
    rng = np.random.default_rng(seed)
    ms = tuple(f"{2020 + i // 12}-{i % 12 + 1:02d}" for i in range(months))
    base = {"USD": 10500.0, "EUR": 11700.0, "RUB": 140.0}
    rates = {}
    usd_r = rng.normal(0.004, 0.02, months - 1)
    for c, b in base.items():
        r = usd_r if (c in ("USD", "EUR") and usd_equals_eur) else rng.normal(0.003, 0.025, months - 1)
        rates[c] = tuple(float(x) for x in b * np.exp(np.concatenate([[0.0], np.cumsum(r)])))
    return FxHistory(source="synthetic-sample", months=ms, rates=rates)


@pytest.fixture(scope="session")
def fx():
    return make_history()
