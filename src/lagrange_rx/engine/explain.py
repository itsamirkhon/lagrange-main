"""E. Explanation: binding constraints, waterfall vs baseline, what-if by re-solving, "impossible and why".

All text is assembled from templates and numbers. No LLM. Legal constraints are never relaxed: they are only named.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Sequence

from lagrange_rx.engine.baseline import baseline_plan
from lagrange_rx.engine.optimize import (
    GROUPS, OptimizerConfig, optimize, solve_elastic,
)
from lagrange_rx.engine.twin import capital_per_pack, evaluate_plan
from lagrange_rx.engine.types import Plan, PlanLine, Scenario, ScenarioOutcome
from lagrange_rx.engine._legacy_ontology import CalculationGraph
from lagrange_rx.engine.rules import Rules

TOL = 1e-6
LEGAL_REF = {
    "registry": ("UZ-3735-P19-CEILING", "published wholesale ceiling (ChUN / 1.12)"),
    "retail": ("UZ-3735-P19-CEILING", "retail-chain ceiling (ChChN / 1.12 / 1.20)"),
    "markup15": ("UZ-PF13-WHOLESALE-MARKUP", "15% markup on the legal base"),
    "contract": ("CONTRACT-PRICE", "contract sale price"),
}


@dataclass(frozen=True)
class Constraint:
    name: str
    kind: str  # structural | legal
    ref: str  # rule_id or ontology object id
    value: float
    limit: float
    is_binding: bool
    text: str


@dataclass(frozen=True)
class WaterfallStep:
    label: str
    expected_profit_uzs: float
    delta_uzs: float


@dataclass(frozen=True)
class WhatIfResult:
    change: dict
    base_expected_uzs: float
    new_expected_uzs: float
    delta_expected_uzs: float
    base_packs: dict[str, int]
    new_packs: dict[str, int]
    status: str
    text: str


@dataclass(frozen=True)
class ImpossibleReport:
    feasible_as_stated: bool
    relax_sets: tuple[dict[str, float], ...]  # minimal sets of groups to relax, with amounts
    legal_notes: tuple[str, ...]
    text: str


def _binding(value: float, limit: float) -> bool:
    return abs(limit - value) <= TOL * max(1.0, abs(limit))


# --------------------------------------------------------------------------- binding constraints

def binding_constraints(graph: CalculationGraph, plan: Plan, outcomes: Sequence[ScenarioOutcome],
                        cfg: OptimizerConfig) -> list[Constraint]:
    out: list[Constraint] = []
    for rid, packs in plan.packs_by_route().items():
        r = graph.route(rid)
        n = sum(ln.shipments for ln in plan.lines if ln.route_id == rid)
        out.append(_c(f"capacity {rid}", "structural", rid, packs, r.capacity_packs,
                      f"route '{r.name}' capacity: {packs:,} of {r.capacity_packs:,} packs"))
        out.append(_c(f"shipments {rid}", "structural", rid, n, r.max_shipments,
                      f"route '{r.name}' shipments: {n} of {r.max_shipments}"))
        out.append(_c(f"max batch {rid}", "structural", rid, packs, r.max_batch * n,
                      f"route '{r.name}' max batch: {packs:,} of {r.max_batch * n:,}"))
        out.append(_c(f"min batch {rid}", "structural", rid, r.min_batch * n, packs,
                      f"route '{r.name}' min batch: {r.min_batch * n:,} of {packs:,}"))
    budget = cfg.budget_uzs if cfg.budget_uzs is not None else graph.finance.budget_uzs
    if budget is not None:
        used = sum(capital_per_pack(graph, graph.route(ln.route_id)) * ln.packs for ln in plan.lines)
        used += sum(graph.route(ln.route_id).fixed_per_shipment_uzs * ln.shipments for ln in plan.lines)
        used += sum(graph.route(rid).fixed_activation_uzs for rid in plan.packs_by_route())
        out.append(_c("credit line", "structural", "finance.budget", used, budget,
                      f"credit line: {used / 1e6:,.0f} of {budget / 1e6:,.0f} mln UZS"))
    out.extend(_legal_binding(outcomes))
    return out


def _c(name, kind, ref, value, limit, text) -> Constraint:
    return Constraint(name, kind, ref, float(value), float(limit), _binding(value, limit), text)


def _legal_binding(outcomes: Sequence[ScenarioOutcome]) -> list[Constraint]:
    priced = [o for o in outcomes if o.price_uzs > 0]
    out = []
    for key, (rule_id, desc) in LEGAL_REF.items():
        share = (sum(1 for o in priced if o.binding == key) / len(priced)) if priced else 0.0
        out.append(Constraint(f"legal:{key}", "legal", rule_id, share, 0.0, share > 0.0,
                              f"{desc} binds the price in {share:.0%} of scenarios"))
    return out


# --------------------------------------------------------------------------- waterfall

def _expected(graph, plan, scenarios, rules, allow_emergency: bool) -> float:
    tw = sum(s.weight for s in scenarios)
    return sum(s.weight * evaluate_plan(graph, plan, s, rules, allow_emergency=allow_emergency).profit_uzs
               for s in scenarios) / tw


def _nearest_shipments(r, packs: int, preferred: int) -> int:
    lo = max(1, math.ceil(packs / r.max_batch))
    hi = r.max_shipments if r.min_batch == 0 else min(r.max_shipments, packs // r.min_batch)
    if hi < lo:
        return lo
    return min(max(preferred, lo), hi)


def _with_baseline_shipments(graph, plan: Plan, base: Plan) -> Plan:
    base_n = {ln.route_id: ln.shipments for ln in base.lines}
    lines = tuple(PlanLine(route_id=ln.route_id, packs=ln.packs,
                           shipments=_nearest_shipments(graph.route(ln.route_id), ln.packs,
                                                        base_n.get(ln.route_id, 1)))
                  for ln in plan.lines)
    return plan.model_copy(update={"lines": lines, "fx_hedge_share": 0.0})


def waterfall(graph: CalculationGraph, plan: Plan, scenarios: Sequence[Scenario], rules: Rules,
              base: Plan | None = None) -> list[WaterfallStep]:
    """Sequential attribution, in this order: baseline -> routes and volumes -> shipments -> hedge -> emergency."""
    base = base or baseline_plan(graph)
    v1 = _with_baseline_shipments(graph, plan, base)
    v2 = plan.model_copy(update={"fx_hedge_share": 0.0})
    values = [
        ("baseline (current practice)", _expected(graph, base, scenarios, rules, False)),
        ("choice of routes and volumes", _expected(graph, v1, scenarios, rules, False)),
        ("choice of shipments", _expected(graph, v2, scenarios, rules, False)),
        ("fx hedge", _expected(graph, plan, scenarios, rules, False)),
        ("emergency top-up (recourse)", _expected(graph, plan, scenarios, rules, True)),
    ]
    steps, prev = [], None
    for label, val in values:
        steps.append(WaterfallStep(label, val, 0.0 if prev is None else val - prev))
        prev = val
    return steps


# --------------------------------------------------------------------------- what-if

def apply_change(graph: CalculationGraph, scenarios: Sequence[Scenario], change: dict):
    """Returns (graph, scenarios, cfg_overrides). Supported keys: budget_delta_uzs, fx_shift, route_unavailable,
    ceiling_cut."""
    unknown = set(change) - {"budget_delta_uzs", "fx_shift", "route_unavailable", "ceiling_cut"}
    if unknown:
        raise ValueError(f"unsupported what-if parameters: {sorted(unknown)}")
    g, scen = graph, list(scenarios)
    if "budget_delta_uzs" in change:
        base = g.finance.budget_uzs
        if base is None:
            raise ValueError("graph has no credit line to change")
        g = g.model_copy(update={"finance": g.finance.model_copy(update={"budget_uzs": base + change["budget_delta_uzs"]})})
    if "fx_shift" in change:
        k = 1.0 + change["fx_shift"]
        g = g.model_copy(update={"nominal_fx": {c: v * k for c, v in g.nominal_fx.items()}})
        scen = [s.model_copy(update={"fx": {c: v * k for c, v in s.fx.items()},
                                     "ref_fx_path": tuple(v * k for v in s.ref_fx_path)}) for s in scen]
    if "route_unavailable" in change:
        rid = change["route_unavailable"]
        g.route(rid)
        g = g.model_copy(update={"routes": tuple(r.model_copy(update={"is_eligible": False}) if r.id == rid else r
                                                 for r in g.routes)})
    if "ceiling_cut" in change:
        scen = [s.model_copy(update={"cut_month": 1, "cut_depth": change["ceiling_cut"]}) for s in scen]
    return g, scen


def what_if(graph: CalculationGraph, scenarios: Sequence[Scenario], rules: Rules, cfg: OptimizerConfig,
            base_plan: Plan, change: dict) -> WhatIfResult:
    """Change a parameter, SOLVE AGAIN, report the difference (no dual prices)."""
    g2, s2 = apply_change(graph, scenarios, change)
    base_val = _expected(graph, base_plan, scenarios, rules, cfg.emergency_enabled)
    res = optimize(g2, s2, rules, cfg, plan_id="what-if")
    if res.plan is None:
        return WhatIfResult(change, base_val, float("nan"), float("nan"), base_plan.packs_by_route(), {},
                            res.info.status, f"after the change {change} the problem has no feasible plan")
    new_val = _expected(g2, res.plan, s2, rules, cfg.emergency_enabled)
    delta = new_val - base_val
    text = (f"change {change}: expected profit {base_val / 1e6:,.1f} -> {new_val / 1e6:,.1f} mln UZS "
            f"({delta / 1e6:+,.1f}); packs {base_plan.packs_by_route()} -> {res.plan.packs_by_route()}")
    return WhatIfResult(change, base_val, new_val, delta, base_plan.packs_by_route(), res.plan.packs_by_route(),
                        res.info.status, text)


# --------------------------------------------------------------------------- "impossible, and why"

def _active_groups(graph: CalculationGraph, cfg: OptimizerConfig) -> list[str]:
    groups = ["capacity", "shipments", "batch"]
    if cfg.service_level is not None:
        groups.append("service")
    if cfg.min_expected_profit_uzs is not None:
        groups.append("target")
    if cfg.budget_uzs is not None or graph.finance.budget_uzs is not None:
        groups.append("budget")
    return [g for g in GROUPS if g in groups]


UNITS = {"service": "k packs of expected unmet demand", "budget": "mln UZS of credit", "capacity": "packs of capacity",
         "shipments": "shipments", "batch": "packs of batch limit", "target": "mln UZS of expected profit"}


def explain_impossible(graph: CalculationGraph, scenarios: Sequence[Scenario], rules: Rules,
                       cfg: OptimizerConfig) -> ImpossibleReport:
    """If the problem is infeasible, find the minimal groups to relax (never the legal ones) and by how much."""
    base = optimize(graph, scenarios, rules, cfg)
    if base.plan is not None:
        return ImpossibleReport(True, (), (), "the problem is feasible as stated")
    groups = _active_groups(graph, cfg)
    for size in range(1, len(groups) + 1):
        found = []
        for combo in itertools.combinations(groups, size):
            sol = solve_elastic(graph, scenarios, rules, cfg, frozenset(combo))
            if sol is not None:
                found.append(sol)
        if found:
            found.sort(key=lambda s: s[0])
            relax_sets = tuple({g: amt for g, amt in amounts.items() if amt > TOL} for _, amounts in found)
            return ImpossibleReport(False, relax_sets, _legal_notes(graph, scenarios, rules, cfg),
                                    _impossible_text(cfg, relax_sets))
    return ImpossibleReport(False, (), _legal_notes(graph, scenarios, rules, cfg),
                            "infeasible even with every non-legal group relaxed")


def _legal_notes(graph, scenarios, rules, cfg) -> tuple[str, ...]:
    """Legal constraints are never relaxed; name them when they limit the best achievable result."""
    relaxed = OptimizerConfig(**{**cfg.__dict__, "service_level": None, "min_expected_profit_uzs": None})
    res = optimize(graph, scenarios, rules, relaxed)
    if res.plan is None:
        return ()
    outs = [evaluate_plan(graph, res.plan, s, rules) for s in scenarios]
    notes = [c.text + f" [{c.ref}]" for c in _legal_binding(outs) if c.is_binding]
    notes.append(f"best achievable expected profit without the target: {res.metrics.expected_uzs / 1e6:,.1f} mln UZS")
    return tuple(notes)


def _impossible_text(cfg: OptimizerConfig, relax_sets) -> str:
    head = ""
    if cfg.service_level is not None:
        head = f"service level {cfg.service_level:.1%} is not reachable: "
    elif cfg.min_expected_profit_uzs is not None:
        head = "the profit target is not reachable: "
    options = []
    for rs in relax_sets:
        options.append(" and ".join(f"{amt:,.2f} {UNITS[g]} ({g})" for g, amt in rs.items()))
    return head + "it is enough to relax " + "; or ".join(options)
