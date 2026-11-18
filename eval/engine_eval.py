"""F. Measuring the effect. The ONLY module that reads the simulation truth.

The truth is reached through a `TruthEvaluator` (the simulation's `evaluate_plan_on_truth`). Until the simulation
exists, `LocalTruth` stands in: it evaluates a plan with the twin on a *truth graph* given by the caller.
Metrics and thresholds are pre-registered in eval/thresholds.json; numbers for slides come from the report only.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Sequence

from lagrange_rx.engine import compliance
from lagrange_rx.engine.baseline import baseline_plan
from lagrange_rx.engine.optimize import OptimizerConfig, cvar, optimize
from lagrange_rx.engine.decision_policy import DEFAULT_SERVICE_LEVEL, has_supply
from lagrange_rx.engine.scenarios import (
    FxHistory, ScenarioConfig, load_fx_history, mean_scenario, out_of_sample,
)
from lagrange_rx.engine.service import EngineResult, calculation_graph, run_optimize, run_what_if
from lagrange_rx.engine.twin import evaluate_plan, evaluate_plan_set, twin_baseline
from lagrange_rx.engine.types import Plan, PlanLine, Scenario
from eval.oracle import brute_force, random_mini_problem
from lagrange_rx.engine.adapter import EngineConfig
from lagrange_rx.engine.inputs import CalculationGraph, RouteSpec
from lagrange_rx.engine.rules import Rules, load_rules

THRESHOLDS_PATH = Path(__file__).resolve().parent / "thresholds.json"
DEFAULT_REPORT_PATH = Path(__file__).resolve().parent / "engine_report.json"


class TruthEvaluator(Protocol):
    def evaluate_plan_on_truth(self, plan: Plan, scenarios: Sequence[Scenario]) -> list[float]:
        """Profit (UZS) of the plan in each scenario, evaluated on the truth."""


def route_signature(r: RouteSpec, supplier_name: str) -> tuple:
    return (supplier_name, r.mode, r.currency, round(r.kc, 2), r.emergency)


def match_routes(source: CalculationGraph, truth: CalculationGraph) -> dict[str, str]:
    """Map route ids of a graph to truth route ids by features (not by internal ids). Saved in the report."""
    names = lambda g: {s.id: s.name for s in g.suppliers}  # noqa: E731
    sn, tn = names(source), names(truth)
    index = {route_signature(r, tn[r.supplier_id]): r.id for r in truth.routes}
    out = {}
    for r in source.routes:
        sig = route_signature(r, sn[r.supplier_id])
        if sig in index:
            out[r.id] = index[sig]
    return out


def translate_plan(plan: Plan, mapping: dict[str, str]) -> Plan:
    lines = tuple(PlanLine(route_id=mapping[ln.route_id], packs=ln.packs, shipments=ln.shipments, months=ln.months)
                  for ln in plan.lines if ln.route_id in mapping)
    return plan.model_copy(update={"lines": lines})


@dataclass
class LocalTruth:
    """Stand-in for the simulation: evaluates on `truth_graph` with the twin's recourse model."""

    truth_graph: CalculationGraph
    rules: Rules
    mapping: dict[str, str] = field(default_factory=dict)

    def evaluate_plan_on_truth(self, plan: Plan, scenarios: Sequence[Scenario]) -> list[float]:
        p = translate_plan(plan, self.mapping) if self.mapping else plan
        return [o.profit_uzs for o in evaluate_plan_set(self.truth_graph, p, scenarios, self.rules)]


# --------------------------------------------------------------------------- metrics

def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def _stats(profits: Sequence[float], alpha: float = 0.95) -> dict:
    w = [1.0] * len(profits)
    return {"expected_uzs": _mean(profits), "cvar_uzs": cvar(list(profits), w, alpha),
            "p_loss": sum(1 for p in profits if p < 0) / len(profits)}


def metric_calibration(graph: CalculationGraph, thr: dict) -> dict:
    cal = twin_baseline(graph)
    ok = cal.calibrated and cal.gap_pp is not None and cal.gap_pp <= thr["calibration_max_gap_pp"]
    return {"twin_margin": cal.twin_margin, "truth_margin": cal.actual_margin, "gap_pp": cal.gap_pp,
            "missing_data": list(cal.missing_data), "pass": bool(ok)}


def metric_uplift(truth: TruthEvaluator, plan: Plan, base: Plan, oos: Sequence[Scenario], thr: dict) -> dict:
    pe, pb = truth.evaluate_plan_on_truth(plan, oos), truth.evaluate_plan_on_truth(base, oos)
    se, sb = _stats(pe), _stats(pb)
    return {"engine": se, "baseline": sb, "uplift_uzs_per_year": se["expected_uzs"] - sb["expected_uzs"],
            "cvar_gain_uzs": se["cvar_uzs"] - sb["cvar_uzs"], "p_loss_change": se["p_loss"] - sb["p_loss"],
            "scenarios": len(oos), "pass": se["expected_uzs"] - sb["expected_uzs"] >= thr["uplift_min_uzs_per_year"]}


def metric_vss(graph, rules, scen_set_opt: Sequence[Scenario], mean_scn: Scenario, oos, truth, thr, service_level: float | None = None) -> dict:
    cfg = OptimizerConfig(risk_mode="expected", service_level=service_level)
    stoch = optimize(graph, scen_set_opt, rules, cfg).plan
    ev = optimize(graph, [mean_scn], rules, cfg).plan
    if not has_supply(stoch) or not has_supply(ev):
        return {"vss_uzs": None, "pass": False, "status": "no_feasible_supply_plan"}
    vs, ve = _mean(truth.evaluate_plan_on_truth(stoch, oos)), _mean(truth.evaluate_plan_on_truth(ev, oos))
    return {"stochastic_expected_uzs": vs, "mean_scenario_expected_uzs": ve, "vss_uzs": vs - ve,
            "pass": vs - ve >= thr["vss_min_uzs"] - 1.0}


def metric_evpi(graph, rules, scen_set_opt, oos: Sequence[Scenario], truth, thr, service_level: float | None = None) -> dict:
    n = min(thr["evpi_scenarios"], len(oos))
    sub = list(oos[:n])
    cfg = OptimizerConfig(risk_mode="expected", service_level=service_level)
    stoch = optimize(graph, scen_set_opt, rules, cfg).plan
    if not has_supply(stoch):
        return {"evpi_uzs": None, "pass": False, "status": "no_feasible_supply_plan"}
    stoch_vals = truth.evaluate_plan_on_truth(stoch, sub)
    ws_vals = []
    for s in sub:
        res = optimize(graph, [s.model_copy(update={"weight": 1.0})], rules, cfg)
        if res.plan is None:
            return {"evpi_uzs": None, "pass": False, "status": "wait_and_see_infeasible"}
        ws_vals.append(truth.evaluate_plan_on_truth(res.plan, [s])[0])
    evpi = _mean(ws_vals) - _mean(stoch_vals)
    return {"wait_and_see_uzs": _mean(ws_vals), "stochastic_uzs": _mean(stoch_vals), "evpi_uzs": evpi,
            "scenarios": n, "pass": evpi >= thr["evpi_min_uzs"] - 1.0}


def metric_legality(graph, rules, plan: Plan, fx: FxHistory, scen_cfg: ScenarioConfig, thr: dict,
                    allow_emergency: bool = True) -> dict:
    if not has_supply(plan):
        return {"scenarios": 0, "legal_rate": None, "violations": 0, "pass": False,
                "status": "not_evaluated_no_supply"}
    n = thr["legality_scenarios"]
    scs = out_of_sample(graph, fx, rules, ScenarioConfig(**{**scen_cfg.__dict__, "n": n, "seed": scen_cfg.seed + 7}))
    outs = evaluate_plan_set(graph, plan, scs, rules, allow_emergency=allow_emergency)
    checks = compliance.check_plan(graph, plan, scs, outs, rules)
    by_scn: dict[str, bool] = {s.id: True for s in scs}
    for c in checks:
        if not c.passed and c.scenario_id in by_scn:
            by_scn[c.scenario_id] = False
    plan_level_ok = all(c.passed for c in checks if c.scenario_id == "*")
    rate = sum(by_scn.values()) / len(by_scn) if plan_level_ok else 0.0
    return {"scenarios": n, "legal_rate": rate, "violations": sum(1 for c in checks if not c.passed),
            "pass": rate >= thr["legality_min_rate"]}


def metric_optimality(rules: Rules, thr: dict) -> dict:
    matches, gaps = 0, []
    for seed in range(thr["optimality_problems"]):
        g, scs = random_mini_problem(seed)
        oracle = brute_force(g, scs, rules)
        res = optimize(g, scs, rules, OptimizerConfig(risk_mode="expected", time_limit_s=10))
        ok = res.info.status == "OPTIMAL" and abs(res.metrics.expected_uzs - oracle.best_expected_uzs) <= max(
            1.0, 1e-6 * abs(oracle.best_expected_uzs))
        matches += ok
        gaps.append(res.info.gap or 0.0)
    n = thr["optimality_problems"]
    return {"problems": n, "matches": matches, "match_rate": matches / n, "max_gap": max(gaps),
            "pass": matches / n >= thr["optimality_min_match_rate"]}


def metric_speed(result: EngineResult, thr: dict) -> dict:
    t0 = time.perf_counter()
    run_what_if(result, {"fx_shift": 0.15})
    secs = time.perf_counter() - t0
    return {"recompute_after_shock_seconds": secs, "pass": secs <= thr["recompute_after_shock_max_seconds"]}


def metric_regret(level_graphs: dict, truth_graph: CalculationGraph, rules: Rules,
                  scenarios: Sequence[Scenario], oos: Sequence[Scenario], opt_cfg: OptimizerConfig,
                  truth: TruthEvaluator, config: EngineConfig | None = None) -> dict:
    """Regret of planning on a graph assembled by Ingest at level Lk, evaluated on the truth, vs planning on truth."""
    best = optimize(truth_graph, scenarios, rules, opt_cfg).plan
    best_val = _mean(truth.evaluate_plan_on_truth(best, oos))
    out = {}
    for level, raw in sorted(level_graphs.items()):
        g = calculation_graph(raw, config)
        plan = optimize(g, scenarios, rules, opt_cfg).plan
        mapping = match_routes(g, truth_graph)
        val = _mean(truth.evaluate_plan_on_truth(translate_plan(plan, mapping), oos))
        out[level] = {"expected_uzs": val, "regret_uzs": best_val - val, "route_mapping": mapping}
    return {"best_on_truth_uzs": best_val, "levels": out}


# --------------------------------------------------------------------------- report

def _truth_label(truth, truth_graph) -> str:
    if isinstance(truth, LocalTruth):
        return "LocalTruth stand-in (simulation not connected)" if truth_graph is None else "LocalTruth on provided truth graph"
    return "simulation evaluate_plan_on_truth"


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=THRESHOLDS_PATH.parent, text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def load_thresholds(path: Path = THRESHOLDS_PATH) -> tuple[dict, str]:
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def run_evaluation(raw_graph, *, config: EngineConfig | None = None, truth_graph=None,
                   truth: TruthEvaluator | None = None, extra: dict | None = None,
                   level_graphs: dict | None = None, rules: Rules | None = None,
                   fx: FxHistory | None = None, scen_cfg: ScenarioConfig = ScenarioConfig(),
                   opt_cfg: OptimizerConfig = OptimizerConfig(), thresholds_path: Path = THRESHOLDS_PATH) -> dict:
    thr, thr_sha = load_thresholds(thresholds_path)
    rules = rules or load_rules()
    fx = fx or load_fx_history()
    g = calculation_graph(raw_graph, config)
    tg = g if truth is not None else calculation_graph(truth_graph if truth_graph is not None else raw_graph, config)
    result = run_optimize(g, rules=rules, fx=fx, scen_cfg=scen_cfg, opt_cfg=opt_cfg, demo_mode=True)
    plan, opt_set = result.plan, result.context.scenarios
    if not has_supply(plan):
        return {"snapshot_id": result.snapshot.snapshot_id,
                "snapshot": result.snapshot.__dict__,
                "thresholds_sha256": thr_sha,
                "thresholds_commit": _git("log", "-1", "--format=%H", "--", str(THRESHOLDS_PATH)),
                "code_commit": _git("rev-parse", "HEAD"),
                "truth": "не оценено: поставок нет plan",
                "fx_source": fx.source_label,
                "plan": None if plan is None else plan.model_dump(mode="json", by_alias=True),
                "supply_policy": {"minimum_expected_fill_rate": opt_cfg.service_level},
                "error": "no_supply_plan: solver infeasible or returned zero packs",
                "metrics": {"supply_feasibility": {"pass": False, "status": "no_supply_plan"}},
                "all_pass": False}
    truth = truth or LocalTruth(tg, rules, match_routes(g, tg))
    oos = out_of_sample(g, fx, rules, ScenarioConfig(**{**scen_cfg.__dict__, "n": thr["out_of_sample_scenarios"]}))
    mean_scn = mean_scenario(oos, g)
    report = {
        "snapshot_id": result.snapshot.snapshot_id,
        "snapshot": result.snapshot.__dict__,
        "thresholds_sha256": thr_sha,
        "thresholds_commit": _git("log", "-1", "--format=%H", "--", str(THRESHOLDS_PATH)),
        "code_commit": _git("rev-parse", "HEAD"),
        "truth": _truth_label(truth, truth_graph),
        "fx_source": fx.source_label,
        **({"truth_mapping": truth.mapping_table()} if hasattr(truth, "mapping_table") else {"route_mapping": truth.mapping}),
        **(extra or {}),
        "plan": plan.model_dump(mode="json", by_alias=True),
        "baseline_plan": baseline_plan(g).model_dump(mode="json", by_alias=True),
        "supply_policy": {"minimum_expected_fill_rate": opt_cfg.service_level,
                          "basis": "explicit synthetic business policy"},
        "metrics": {
            "calibration": metric_calibration(g, thr),
            "uplift": metric_uplift(truth, plan, baseline_plan(g), oos, thr),
            "vss": metric_vss(g, rules, opt_set, mean_scn, oos, truth, thr, opt_cfg.service_level),
            "evpi": metric_evpi(g, rules, opt_set, oos, truth, thr, opt_cfg.service_level),
            "legality": metric_legality(g, rules, plan, fx, scen_cfg, thr, opt_cfg.emergency_enabled),
            "optimality": metric_optimality(rules, thr),
            "speed": metric_speed(result, thr),
        },
    }
    if level_graphs:
        report["metrics"]["regret_by_level"] = metric_regret(level_graphs, tg, rules, opt_set, oos,
                                                             OptimizerConfig(risk_mode="expected", service_level=opt_cfg.service_level), truth, config)
    else:
        report["metrics"]["regret_by_level"] = {"status": "not available: Ingest has not produced L0-L4 graphs yet"}
    report["all_pass"] = all(m.get("pass", True) for m in report["metrics"].values())
    return report


def run_on_simulation(*, ingest_path: Path | None = None, truth_dir: Path | None = None,
                      thresholds_path: Path = THRESHOLDS_PATH, scen_cfg: ScenarioConfig = ScenarioConfig(),
                      opt_cfg: OptimizerConfig = OptimizerConfig(service_level=DEFAULT_SERVICE_LEVEL)) -> dict:
    """Stage 5: engine graph from the Ingest L0 ontology graph (via the adapter), plans evaluated on the simulation
    truth through `SimTruth` (feature-mapped, `evaluate_plan_on_truth`)."""
    from eval import engine_inputs as ei
    from eval.truth_eval import TRUTH_DIR, SimTruth

    ingest_path = ingest_path or ei.INGEST_GRAPH_PATH
    graph, ingest_snapshot = ei.load_ingest_graph(ingest_path)
    onto_graph = graph
    cfg = ei.engine_config(graph)
    g = calculation_graph(onto_graph, cfg)
    with SimTruth(g, onto_graph, truth_dir or TRUTH_DIR) as st:
        extra = {
            "graph_source": f"ontology graph from Ingest ({ingest_path.name}, snapshot {ingest_snapshot}) -> "
                            "engine.adapter.to_calculation_graph with adapter.default_engine_config (single config path, stage 6d)",
            "ingest_snapshot_id": ingest_snapshot,
            "engine_config": cfg.model_dump(mode="json"),
            "adapter_notes": list(g.notes),
            "truth_native_baseline": st.baseline_on_truth(),
            "truth_overlay": ("scenario -> copy of truth tables: demand = scenario.demand/forecast p50 on window sales; "
                              "USD rate ratio on fx, on the FX-linked share (CIP+duty) of landed unit cost; freight "
                              "multiplier by mode on route rates; P22 cut = demand-weighted mean of monthly cap factors "
                              "on the registry price; unavailable route = zero packs on it (no re-purchase). "
                              "Not modelled on truth: emergency routes, hedge, supplier price differences."),
            "plan_schedule": "one shipment per month; line months used if given, else evenly spread; a taken month "
                             "moves to the nearest free one; months without shipment are explicit zero orders",
            "min_mapping_confidence": st.mapping.min_confidence,
        }
        report = run_evaluation(g, truth=st, extra=extra, scen_cfg=scen_cfg, opt_cfg=opt_cfg,
                                thresholds_path=thresholds_path)
        report["truth_calls"] = st.calls
    return report


def main(argv: list[str] | None = None) -> int:
    import argparse
    from lagrange_rx.fixtures.l0 import build_l0_config, build_l0_graph

    ap = argparse.ArgumentParser(description="Run the engine evaluation (default: Ingest L0 graph on the simulation truth)")
    ap.add_argument("--out", default=str(DEFAULT_REPORT_PATH))
    ap.add_argument("--fixture", action="store_true", help="legacy: synthetic fixture on LocalTruth, not the simulation")
    args = ap.parse_args(argv)
    report = run_evaluation(build_l0_graph(), config=build_l0_config()) if args.fixture else run_on_simulation()
    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True, default=str, ensure_ascii=False))
    print(f"report -> {args.out}; all_pass={report['all_pass']}")
    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
