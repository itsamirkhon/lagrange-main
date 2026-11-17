import pytest

from lagrange_rx.engine.twin import TwinError, caps_for, evaluate_plan, unit_economics, below_line
from lagrange_rx.engine.types import Plan, PlanLine
from lagrange_rx.ontology import ClientGraph, CostItem
from tests.conftest import make_graph, make_route, make_scenario


def plan(packs=1000, shipments=1, route="R1", h=0.0):
    return Plan(plan_id="p", sku_id="P1", horizon="2026-11..2027-10", fx_hedge_share=h,
                lines=(PlanLine(route_id=route, packs=packs, shipments=shipments),))


def test_legal_base_and_cost_by_hand(tiny, rules):
    ue = unit_economics(tiny, make_scenario(), rules)
    r = ue.routes["R1"]
    assert r.legal_base == pytest.approx(5 * 12_000 * 1.10 + 1000)  # 67,000
    assert r.unit_cost == pytest.approx(67_000 + 3000 + 500)  # freight + acquisition item, no finance


def test_hedge_and_freight_not_in_legal_base(tiny, rules):
    a = unit_economics(tiny, make_scenario(freight_mult={"sea": 2.0}), rules).routes["R1"]
    assert a.legal_base == pytest.approx(67_000) and a.unit_cost == pytest.approx(67_000 + 6000 + 500)


def test_caps_registry_retail_contract(tiny, rules):
    c = caps_for(tiny, make_scenario(), rules)
    assert c.wholesale == pytest.approx(100_000) and c.retail == pytest.approx(150_000 / 1.12 / 1.20)
    assert c.contract is None and c.factor == pytest.approx(1.0)


def test_cut_lowers_both_caps(tiny, rules):
    c = caps_for(tiny, make_scenario(cut_month=1, cut_depth=0.37), rules)
    assert c.wholesale == pytest.approx(100_000 * 0.63)


def test_fx_index_raises_caps(tiny, rules):
    path = tuple([12_000.0 * 1.10] * 12)  # +10% at start -> immediate, lag 1 month
    c = caps_for(tiny, make_scenario(ref_fx_path=path), rules)
    assert c.factor == pytest.approx((1 + 11 * 1.10) / 12)


def test_unallocated_cost_refuses(rules):
    g = make_graph(cost_items=[CostItem(id="b", name="rent", layer="below_line", annual_uzs=10.0, allocation="unallocated")])
    with pytest.raises(TwinError, match="rent"):
        below_line(g.for_calculation())


def test_shared_allocation_needs_share():
    g = make_graph(cost_items=[CostItem(id="b", name="rent", layer="below_line", annual_uzs=1000.0, allocation="shared_by_volume")])
    with pytest.raises(TwinError):
        below_line(g.for_calculation())
    g2 = make_graph(cost_items=[CostItem(id="b", name="rent", layer="below_line", annual_uzs=1000.0,
                                         per_pack_uzs=10.0, allocation="shared_by_volume", share=0.25)])
    assert below_line(g2.for_calculation()) == (pytest.approx(2.5), pytest.approx(250.0))


def test_evaluate_plan_by_hand_markup_binds(tiny, rules):
    o = evaluate_plan(tiny, plan(), make_scenario(demand=800.0), rules)
    assert o.sold == pytest.approx(800) and o.shortfall == pytest.approx(0)
    assert o.price_uzs == pytest.approx(1.15 * 67_000) and o.binding == "markup15"
    revenue = 800 * 1.15 * 67_000
    assert o.revenue_uzs == pytest.approx(revenue) and o.sold_by_route == {"R1": pytest.approx(800)}
    assert o.profit_uzs == pytest.approx(revenue - 1000 * 70_500 - 2_000_000 - 500_000)


def test_unavailable_route_means_shortfall_and_no_purchase_cost(tiny, rules):
    o = evaluate_plan(tiny, plan(), make_scenario(route_available={"R1": False}), rules)
    assert o.sold == 0 and o.shortfall == pytest.approx(800) and o.price_uzs == 0
    assert o.profit_uzs == pytest.approx(-2_500_000)


def test_registry_cap_binds_when_markup_loose(rules):
    g = make_graph(product=make_graph().product.model_copy(update={"chun_uzs": 67_200.0})).for_calculation()
    o = evaluate_plan(g, plan(), make_scenario(), rules)
    assert o.binding == "registry" and o.price_uzs == pytest.approx(60_000)


def test_hedge_changes_cost_not_legal_base(tiny, rules):
    base = evaluate_plan(tiny, plan(), make_scenario(), rules)
    hedged = evaluate_plan(tiny, plan(h=1.0), make_scenario(), rules)
    fwd_extra = 5 * (12_000 * 1.02 - 12_000) * 1000  # forward premium paid on all 1000 packs
    assert hedged.price_uzs == pytest.approx(base.price_uzs)
    assert hedged.profit_uzs == pytest.approx(base.profit_uzs - fwd_extra)


def test_emergency_purchase_used_when_profitable(rules):
    emerg = make_route(id="E1", supplier_id="SB", name="air", mode="air", emergency=True,
                       emergency_premium_uzs=1000.0, emergency_cap_packs=500, freight_uzs=3000.0)
    g = make_graph(routes=[make_route(), emerg]).for_calculation()
    o = evaluate_plan(g, plan(packs=1000), make_scenario(demand=1300.0, ), rules)
    assert o.emergency.get("E1", 0) > 0 and o.sold <= 1300
    no = evaluate_plan(g, plan(packs=1000), make_scenario(demand=1300.0), rules, allow_emergency=False)
    assert no.emergency == {} and no.sold == pytest.approx(1000)


def test_engine_rejects_non_calculation_graph(rules):
    with pytest.raises(TypeError):
        unit_economics(make_graph(), make_scenario(), rules)
