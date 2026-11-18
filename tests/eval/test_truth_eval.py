import pytest
from sim.generator.evaluate import evaluate_plan_on_truth

from eval import engine_inputs as ei
from eval.truth_eval import MappingError, Overlay, SimTruth, TRUTH_DIR, _pick, schedule_shipments, translate_plan
from lagrange_rx.engine.adapter import to_calculation_graph
from lagrange_rx.engine.baseline import baseline_plan
from lagrange_rx.engine.types import Plan, PlanLine, Scenario


@pytest.fixture(scope="module")
def world():
    g, _ = ei.load_ingest_graph()
    cg = to_calculation_graph(g, ei.engine_config(g))
    return cg, g


@pytest.fixture(scope="module")
def st(world):
    with SimTruth(*world) as s:
        yield s


def scn(cg, **kw):
    base = dict(id="x", name="x", fx=dict(cg.nominal_fx), ref_fx_start=1.0, ref_fx_path=(1.0,) * 12, demand=cg.demand.p50)
    base.update(kw)
    return Scenario(**base)


def test_mapping_by_features_with_confidence(st):
    t = {r["engine_id"]: r for r in st.mapping_table()}
    assert t["KZ-AUTO-01/medisynth-s-a-indiya-synthetic"]["truth_id"] == "rte_01"
    assert t["TM-AUTO-02/vektorpharm-pvt-ltd-turtsiya-synthetic"]["truth_id"] == "rte_02"
    assert t["kardiozan-10mg-n30"]["truth_id"] == "SKU_CARDIO_01"
    assert "manufacturer" in t["kardiozan-10mg-n30"]["not_compared"]
    assert all(r["confidence"] >= 0.75 for r in t.values())


def test_ambiguity_and_no_match_raise():
    cands = {"a": {"mode": "road", "carrier": "X"}, "b": {"mode": "road", "carrier": "X"}}
    with pytest.raises(MappingError, match="ambiguous"):
        _pick("route", "e", {"mode": "road", "carrier": "X"}, cands)
    with pytest.raises(MappingError, match="no truth entity"):
        _pick("route", "e", {"mode": "air", "carrier": "Y"}, cands)


def test_schedule_one_order_per_month_and_limit():
    p = Plan(plan_id="p", sku_id="s", horizon="h", lines=(PlanLine(route_id="a", packs=80000, shipments=8),
                                                          PlanLine(route_id="b", packs=40000, shipments=4)))
    sch = schedule_shipments(p, "2026-11")
    assert len({s for s, _, _ in sch}) == 12 and sum(k for *_, k in sch) == 120000
    over = Plan(plan_id="p", sku_id="s", horizon="h", lines=(PlanLine(route_id="a", packs=1000, shipments=13),))
    with pytest.raises(MappingError):
        schedule_shipments(over, "2026-11")


def test_translate_fills_zero_orders(st, world):
    cg, _ = world
    p = Plan(plan_id="p", sku_id=cg.product.id, horizon="h",
             lines=(PlanLine(route_id=cg.routes[0].id, packs=24000, shipments=2),))
    tp = translate_plan(p, st.mapping, cg.horizon_start)
    q = [o["qty_packs"] for o in tp["monthly_orders"]]
    assert len(q) == 12 and sum(q) == 24000 and q.count(0) == 10


def test_nominal_scenario_equals_public_truth_function(st, world):
    cg, _ = world
    p = baseline_plan(cg)
    tp = translate_plan(p, st.mapping, cg.horizon_start)
    assert st.evaluate_plan_on_truth(p, [scn(cg)])[0] == float(evaluate_plan_on_truth(tp, TRUTH_DIR))


def test_overlays_move_profit_the_right_way(st, world):
    cg, _ = world
    p = baseline_plan(cg)
    nom = st.profit(p, scn(cg))
    assert st.profit(p, scn(cg, demand=cg.demand.p50 * 0.6)) < nom
    assert st.profit(p, scn(cg, cut_month=1, cut_depth=0.3)) < nom
    assert st.profit(p, scn(cg, fx={"USD": cg.nominal_fx["USD"] * 1.2, "UZS": 1.0})) != nom
    assert st.profit(p, scn(cg, route_available={cg.routes[0].id: False})) != nom


def test_overlay_nominal_flag():
    assert Overlay().world_is_nominal and not Overlay(demand_factor=0.9).world_is_nominal
