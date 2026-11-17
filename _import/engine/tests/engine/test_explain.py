import pytest

from lagrange_rx.engine.baseline import baseline_plan
from lagrange_rx.engine.explain import (
    apply_change, binding_constraints, explain_impossible, waterfall, what_if,
)
from lagrange_rx.engine.optimize import OptimizerConfig, optimize
from lagrange_rx.engine.twin import evaluate_plan
from lagrange_rx.eval.oracle import random_mini_problem
from lagrange_rx.rules import load_rules

R = load_rules()
EXP = OptimizerConfig(risk_mode="expected", time_limit_s=10)


def test_binding_constraints_reference_rules_and_objects():
    graph, scen = random_mini_problem(2)
    res = optimize(graph, scen, R, EXP)
    outs = [evaluate_plan(graph, res.plan, s, R) for s in scen]
    cons = binding_constraints(graph, res.plan, outs, EXP)
    legal = {c.ref for c in cons if c.kind == "legal"}
    assert {"UZ-PF13-WHOLESALE-MARKUP", "UZ-3735-P19-CEILING"} <= legal
    assert any(c.kind == "structural" and c.ref in {r.id for r in graph.routes} for c in cons)
    assert sum(c.value for c in cons if c.name.startswith("legal:") and c.name != "legal:retail") > 0


def test_capacity_binding_detected():
    graph, scen = random_mini_problem(4)
    res = optimize(graph, scen, R, EXP)
    cons = binding_constraints(graph, res.plan, [evaluate_plan(graph, res.plan, s, R) for s in scen], EXP)
    for ln in res.plan.lines:
        r = graph.route(ln.route_id)
        c = next(c for c in cons if c.name == f"capacity {r.id}")
        assert c.is_binding == (ln.packs == r.capacity_packs)


def test_waterfall_sums_to_total_gain():
    graph, scen = random_mini_problem(5)
    res = optimize(graph, scen, R, EXP)
    base = res.plan.model_copy(update={"fx_hedge_share": 0.0})  # stand-in baseline: a valid plan
    steps = waterfall(graph, res.plan, scen, R, base=base)
    assert [s.label for s in steps][0].startswith("baseline")
    assert sum(s.delta_uzs for s in steps) == pytest.approx(steps[-1].expected_profit_uzs - steps[0].expected_profit_uzs)
    assert steps[-1].expected_profit_uzs == pytest.approx(res.metrics.expected_uzs, rel=1e-6, abs=1.0)


def test_waterfall_on_l0_baseline_is_worse_or_equal():
    from lagrange_rx.fixtures.l0 import build_l0_graph
    from tests.conftest import make_scenario
    g = build_l0_graph().for_calculation()
    scen = [make_scenario(id=f"s{i}", fx={"USD": 12_000.0 * (1 + 0.03 * i), "EUR": 13_100.0 * (1 + 0.03 * i)},
                          ref_fx_start=12_000.0, ref_fx_path=(12_000.0,) * 12, demand=d)
            for i, d in enumerate([95_000.0, 110_000.0, 128_000.0])]
    res = optimize(g, scen, R, EXP)
    steps = waterfall(g, res.plan, scen, R)
    assert steps[-1].expected_profit_uzs >= steps[0].expected_profit_uzs - 1.0
    assert baseline_plan(g).lines


def test_what_if_resolves_and_reports_difference():
    graph, scen = random_mini_problem(6)
    res = optimize(graph, scen, R, EXP)
    rid = res.plan.lines[0].route_id
    w = what_if(graph, scen, R, EXP, res.plan, {"route_unavailable": rid})
    assert w.status in {"OPTIMAL", "FEASIBLE"} and w.delta_expected_uzs <= 1.0
    assert rid not in w.new_packs
    w2 = what_if(graph, scen, R, EXP, res.plan, {"fx_shift": 0.15})
    assert "expected profit" in w2.text


def test_apply_change_validates():
    graph, scen = random_mini_problem(6)
    with pytest.raises(ValueError):
        apply_change(graph, scen, {"bogus": 1})
    g2, s2 = apply_change(graph, scen, {"ceiling_cut": 0.37})
    assert all(s.cut_month == 1 and s.cut_depth == 0.37 for s in s2)


def test_impossible_service_names_minimal_relaxation_without_touching_legal_rules():
    graph, scen = random_mini_problem(8)
    cfg = OptimizerConfig(risk_mode="expected", time_limit_s=10, service_level=0.999)
    rep = explain_impossible(graph, scen, R, cfg)
    if rep.feasible_as_stated:  # make it surely infeasible: lift demand above any capacity
        scen = [s.model_copy(update={"demand": 1e6}) for s in scen]
        rep = explain_impossible(graph, scen, R, cfg)
    assert not rep.feasible_as_stated and rep.relax_sets
    allowed = {"service", "budget", "capacity", "shipments", "batch", "target"}
    assert all(set(rs) <= allowed for rs in rep.relax_sets)
    assert any("service" in rs or "capacity" in rs for rs in rep.relax_sets)
    assert "service level" in rep.text and rep.legal_notes


def test_feasible_problem_is_reported_feasible():
    graph, scen = random_mini_problem(9)
    assert explain_impossible(graph, scen, R, EXP).feasible_as_stated
