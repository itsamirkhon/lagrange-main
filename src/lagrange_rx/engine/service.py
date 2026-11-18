"""Orchestration of one engine run: calibrate -> scenarios -> optimise -> independent legality check ->
explain -> snapshot. Used by the API and by the evaluation module."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Sequence

from lagrange_rx.engine import compliance
from lagrange_rx.engine.baseline import baseline_plan
from lagrange_rx.engine.explain import (
    Constraint, ImpossibleReport, WaterfallStep, WhatIfResult, binding_constraints, explain_impossible,
    waterfall, what_if,
)
from lagrange_rx.engine.optimize import OptimizeResult, OptimizerConfig, RiskMetrics, SolveInfo, optimize
from lagrange_rx.engine.scenarios import FxHistory, ScenarioConfig, ScenarioSet, generate_scenarios, load_fx_history
from lagrange_rx.engine.snapshot import Snapshot, make_snapshot
from lagrange_rx.engine.twin import CalibrationResult, evaluate_plan_set, twin_baseline, unit_economics
from lagrange_rx.engine.types import CheckResult, Plan, Scenario, ScenarioOutcome
from lagrange_rx import ontology
from lagrange_rx.engine.adapter import EngineConfig, as_calculation_graph
from lagrange_rx.engine.inputs import CalculationGraph, EngineGraph
from lagrange_rx.engine.rules import Rules, load_rules

DEMO_WARNING = "demo mode: the twin is NOT calibrated"


class CalibrationRequired(RuntimeError):
    def __init__(self, calibration: CalibrationResult):
        super().__init__("twin is not calibrated; the solver does not run without the explicit demo flag")
        self.calibration = calibration


class LegalityError(RuntimeError):
    def __init__(self, failed: list[CheckResult]):
        super().__init__(f"{len(failed)} legality check(s) failed; the plan is withheld")
        self.failed = failed


@dataclass(frozen=True)
class EngineContext:
    graph: CalculationGraph
    scenarios: tuple[Scenario, ...]
    rules: Rules
    opt_cfg: OptimizerConfig
    scen_cfg: ScenarioConfig


@dataclass(frozen=True)
class EngineResult:
    plan: Plan | None
    snapshot: Snapshot
    calibration: CalibrationResult
    solve: SolveInfo
    metrics: RiskMetrics | None
    checks: tuple[CheckResult, ...]
    constraints: tuple[Constraint, ...]
    waterfall: tuple[WaterfallStep, ...]
    outcomes: tuple[ScenarioOutcome, ...]
    warnings: tuple[str, ...]
    impossible: ImpossibleReport | None
    seconds: float
    context: EngineContext = field(repr=False, default=None)  # type: ignore[assignment]


def calculation_graph(graph: "ontology.ClientGraph | EngineGraph", config: EngineConfig | None = None) -> CalculationGraph:
    """Single entry: an `ontology.ClientGraph` (with `config`) goes through `adapter.to_calculation_graph`."""
    return as_calculation_graph(graph, config)


def make_scenarios(graph: CalculationGraph, rules: Rules, fx: FxHistory | None = None,
                   cfg: ScenarioConfig = ScenarioConfig()) -> ScenarioSet:
    return generate_scenarios(graph, fx or load_fx_history(), rules, cfg)


def _failed(checks: Sequence[CheckResult]) -> list[CheckResult]:
    return [c for c in checks if not c.passed]


def run_optimize(graph: "ontology.ClientGraph | EngineGraph", *, config: EngineConfig | None = None, rules: Rules | None = None, fx: FxHistory | None = None,
                 scen_cfg: ScenarioConfig = ScenarioConfig(), opt_cfg: OptimizerConfig = OptimizerConfig(),
                 demo_mode: bool = False, scenario_set: ScenarioSet | None = None) -> EngineResult:
    t0 = time.perf_counter()
    rules = rules or load_rules()
    g = calculation_graph(graph, config)
    cal = twin_baseline(g)
    demo = demo_mode or g.demo_mode
    if not cal.calibrated and not demo:
        raise CalibrationRequired(cal)
    warnings = [] if cal.calibrated else [DEMO_WARNING]
    sset = scenario_set or make_scenarios(g, rules, fx, scen_cfg)
    scenarios = sset.optimisation_set()
    snap = make_snapshot(g, scenarios, sset.seed, rules, opt_cfg)
    ctx = EngineContext(g, scenarios, rules, opt_cfg, scen_cfg)
    warnings += list(g.notes)
    warnings += list(unit_economics(g, scenarios[0], rules).warnings)
    if sset.fx_source != "cbu":
        warnings.append(f"fx history source is '{sset.fx_source}', not real CBU rates")
    res = optimize(g, scenarios, rules, opt_cfg, plan_id=f"plan-{snap.snapshot_id[:12]}")
    if res.plan is None:
        report = explain_impossible(g, scenarios, rules, opt_cfg)
        return EngineResult(None, snap, cal, res.info, None, (), (), (), (), tuple(warnings), report,
                            time.perf_counter() - t0, ctx)
    plan = res.plan.model_copy(update={"snapshot_id": snap.snapshot_id})
    outcomes = evaluate_plan_set(g, plan, scenarios, rules, allow_emergency=opt_cfg.emergency_enabled)
    checks = compliance.check_plan(g, plan, scenarios, outcomes, rules)
    if not compliance.is_legal(checks):
        raise LegalityError(_failed(checks))
    steps = waterfall(g, plan, scenarios, rules, base=baseline_plan(g)) if g.shipments else []
    cons = binding_constraints(g, plan, outcomes, opt_cfg)
    if not res.info.proven_optimal:
        warnings.append("feasible plan, optimality not proven")
    return EngineResult(plan, snap, cal, res.info, res.metrics, tuple(checks), tuple(cons), tuple(steps),
                        tuple(outcomes), tuple(warnings), None, time.perf_counter() - t0, ctx)


def run_what_if(result: EngineResult, change: dict) -> WhatIfResult:
    if result.plan is None:
        raise ValueError("no plan to compare with")
    c = result.context
    return what_if(c.graph, c.scenarios, c.rules, c.opt_cfg, result.plan, change)
