import pytest

from lagrange_rx.engine.twin import caps_for, twin_baseline, unit_economics
from lagrange_rx.fixtures.l0 import build_l0_graph
from lagrange_rx.engine._legacy_ontology import ActualResult
from lagrange_rx.engine.rules import load_rules
from tests.conftest import make_scenario

R = load_rules()


def test_l0_twin_reproduces_margin_within_tolerance():
    g = build_l0_graph().for_calculation()
    cal = twin_baseline(g)
    assert cal.calibrated and cal.gap_pp <= 0.5
    assert cal.actual_margin == pytest.approx(0.025, abs=0.001)


def test_l0_baseline_sale_price_is_legal():
    g = build_l0_graph().for_calculation()
    price = g.sales[0].price_uzs
    caps = caps_for(g, make_scenario(fx=g.nominal_fx, ref_fx_start=12_000.0, ref_fx_path=(12_000.0,) * 12), R)
    assert price <= min(caps.wholesale, caps.retail)


def test_miscalibrated_twin_reports_gap_and_missing_data():
    g = build_l0_graph()
    bad = g.model_copy(update={"actual": ActualResult(revenue_uzs=g.actual.revenue_uzs,
                                                      total_cost_uzs=g.actual.total_cost_uzs * 0.97)})
    cal = twin_baseline(bad.for_calculation())
    assert not cal.calibrated and cal.gap_pp > 0.5 and cal.issues[0].missing_data


def test_missing_history_lists_exactly_what_is_missing():
    g = build_l0_graph().model_copy(update={"actual": None, "sales": (), "shipments": ()})
    cal = twin_baseline(g.for_calculation())
    assert not cal.calibrated and len(cal.missing_data) == 3


def test_unconfirmed_route_excluded_from_calculation():
    g = build_l0_graph().for_calculation()
    assert "R-ROAD-X" not in {r.id for r in g.routes}
    ue = unit_economics(g, make_scenario(fx=g.nominal_fx, ref_fx_start=12_000.0, ref_fx_path=(12_000.0,) * 12), R)
    assert "R-ROAD-X" not in ue.routes
