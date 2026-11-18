import pytest

from lagrange_rx.engine.twin import caps_for, twin_baseline, unit_economics
from lagrange_rx.fixtures.l0 import build_l0_calculation_graph
from lagrange_rx.engine.inputs import ActualSpec
from lagrange_rx.engine.rules import load_rules
from tests.conftest import make_scenario

R = load_rules()


def test_l0_twin_reproduces_margin_within_tolerance():
    g = build_l0_calculation_graph()
    cal = twin_baseline(g)
    assert cal.calibrated and cal.gap_pp <= 0.5
    assert cal.actual_margin == pytest.approx(0.025, abs=0.001)


def test_l0_baseline_sale_price_is_legal():
    g = build_l0_calculation_graph()
    price = g.sales[0].price_uzs
    caps = caps_for(g, make_scenario(fx=g.nominal_fx, ref_fx_start=12_000.0, ref_fx_path=(12_000.0,) * 12), R)
    assert price <= min(caps.wholesale, caps.retail)


def test_miscalibrated_twin_reports_gap_and_missing_data():
    g = build_l0_calculation_graph()
    bad = g.model_copy(update={"actual": ActualSpec(revenue_uzs=g.actual.revenue_uzs,
                                                      total_cost_uzs=g.actual.total_cost_uzs * 0.97)})
    cal = twin_baseline(bad)
    assert not cal.calibrated and cal.gap_pp > 0.5 and cal.issues[0].missing_data


def test_missing_history_lists_exactly_what_is_missing():
    g = build_l0_calculation_graph().model_copy(update={"actual": None, "sales": (), "shipments": ()})
    cal = twin_baseline(g)
    assert not cal.calibrated and len(cal.missing_data) == 3


def test_unconfirmed_route_never_enters_calculation():
    # an unconfirmed (extracted_unconfirmed) entity makes ontology for_calculation() refuse the whole graph
    from datetime import date
    from decimal import Decimal
    from lagrange_rx import ontology as o
    from lagrange_rx.engine.adapter import to_calculation_graph
    from lagrange_rx.fixtures.l0 import build_l0_config, build_l0_graph
    g = build_l0_graph()
    bad = o.Provenance(kind="extracted_unconfirmed", source="quote.pdf")
    road = g.routes[0].model_copy(update={"route_id": "R-ROAD-X", "provenance": bad})
    g2 = g.model_copy(update={"routes": [*g.routes, road]})
    with pytest.raises(ValueError, match="unconfirmed"):
        to_calculation_graph(g2, build_l0_config())
    ue = unit_economics(build_l0_calculation_graph(), make_scenario(fx={"USD": 12000.0, "EUR": 13100.0}, ref_fx_start=12_000.0, ref_fx_path=(12_000.0,) * 12), R)
    assert "R-ROAD-X" not in ue.routes
