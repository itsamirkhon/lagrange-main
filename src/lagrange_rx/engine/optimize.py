"""C. Optimizer, stage 1: annual two-stage stochastic MILP on HiGHS (scipy.optimize.milp).

Units inside the model: money in million UZS, prices in thousand UZS per pack, volumes in thousand packs.
First stage: y (route on), x (lots), n (shipments), h (hedge share, discrete levels).
Second stage per scenario: q_is (packs sold from route i), e_js (emergency purchase, sold at once), R_s (revenue).
Revenue is exact and linear: R_s <= cap * q_s for each ceiling and R_s <= (1+markup) * sum(base * q_is)
(UZ-PF13-WHOLESALE-MARKUP on the legal base of the packs actually sold).
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field, replace
from typing import Literal, Sequence

import numpy as np
from scipy.optimize import LinearConstraint, Bounds, milp
from scipy.sparse import csr_matrix

from lagrange_rx.engine.twin import (
    UnitEconomics, below_line, capital_per_pack, unit_economics,
)
from lagrange_rx.engine.types import Plan, PlanLine, Scenario
from lagrange_rx.ontology import CalculationGraph, Route, require_calculation_graph
from lagrange_rx.rules import Rules

INF = math.inf
RiskMode = Literal["expected", "cvar", "worst_case"]
GROUPS = ("service", "budget", "capacity", "shipments", "batch", "target")


@dataclass(frozen=True)
class OptimizerConfig:
    risk_mode: RiskMode = "cvar"
    alpha: float = 0.95
    lam: float = 0.5
    hedge_enabled: bool = False
    hedge_levels: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0)
    emergency_enabled: bool = True
    time_limit_s: float = 20.0
    mip_rel_gap: float = 1e-6
    service_level: float | None = None  # expected fill rate >= value
    min_expected_profit_uzs: float | None = None
    budget_uzs: float | None = None  # overrides graph.finance.budget_uzs


@dataclass(frozen=True)
class SolveInfo:
    status: str  # OPTIMAL | FEASIBLE | INFEASIBLE | UNBOUNDED | ERROR
    proven_optimal: bool
    objective: float | None
    bound: float | None
    gap: float | None
    seconds: float


@dataclass(frozen=True)
class RiskMetrics:
    expected_uzs: float
    cvar_uzs: float
    worst_uzs: float
    p_loss: float


@dataclass(frozen=True)
class OptimizeResult:
    plan: Plan | None
    info: SolveInfo
    metrics: RiskMetrics | None
    scenario_profits_uzs: tuple[float, ...] = ()
    notes: tuple[str, ...] = ()
    slacks: dict[str, float] = field(default_factory=dict)  # elastic mode only


# --------------------------------------------------------------------------- risk metrics

def cvar(values: Sequence[float], weights: Sequence[float], alpha: float) -> float:
    """Weighted mean of the worst (1-alpha) probability mass."""
    tail = 1.0 - alpha
    if tail <= 0:
        return float(min(values))
    total = float(sum(weights))
    order = np.argsort(values)
    mass, acc = 0.0, 0.0
    for idx in order:
        take = min(weights[idx] / total, tail - mass)
        if take <= 0:
            break
        acc += take * values[idx]
        mass += take
    return acc / tail if mass >= tail - 1e-12 else acc / max(mass, 1e-12)


def risk_metrics(profits: Sequence[float], weights: Sequence[float], alpha: float) -> RiskMetrics:
    total = float(sum(weights))
    exp = sum(p * w for p, w in zip(profits, weights)) / total
    p_loss = sum(w for p, w in zip(profits, weights) if p < 0) / total
    return RiskMetrics(exp, cvar(list(profits), list(weights), alpha), float(min(profits)), p_loss)


# --------------------------------------------------------------------------- model builder

class _Builder:
    def __init__(self, elastic: frozenset[str] = frozenset()):
        self.lb: list[float] = []
        self.ub: list[float] = []
        self.integer: list[int] = []
        self.cost: list[float] = []
        self.rows: list[tuple[dict[int, float], float, float]] = []
        self.elastic = elastic
        self.slack: dict[str, list[int]] = {g: [] for g in GROUPS}

    def var(self, lb: float = 0.0, ub: float = INF, integer: bool = False) -> int:
        self.lb.append(lb); self.ub.append(ub); self.integer.append(1 if integer else 0); self.cost.append(0.0)
        return len(self.lb) - 1

    def row(self, coefs: dict[int, float], lo: float = -INF, hi: float = INF, group: str | None = None) -> None:
        coefs = {k: v for k, v in coefs.items() if v != 0.0}
        if group in self.elastic:
            s = self.var()
            self.slack[group].append(s)
            if hi < INF:
                coefs[s] = -1.0
            if lo > -INF:
                coefs[s] = 1.0
        self.rows.append((coefs, lo, hi))

    def solve(self, time_limit: float, gap: float):
        n = len(self.lb)
        data, ri, ci = [], [], []
        for r, (coefs, _, _) in enumerate(self.rows):
            for c, v in coefs.items():
                ri.append(r); ci.append(c); data.append(v)
        a = csr_matrix((data, (ri, ci)), shape=(len(self.rows), n))
        cons = LinearConstraint(a, [r[1] for r in self.rows], [r[2] for r in self.rows])
        return milp(np.array(self.cost), constraints=cons, integrality=np.array(self.integer),
                    bounds=Bounds(self.lb, self.ub),
                    options={"time_limit": time_limit, "mip_rel_gap": gap, "disp": False})


@dataclass
class _Vars:
    y: dict[str, int] = field(default_factory=dict)
    x: dict[str, int] = field(default_factory=dict)
    n: dict[str, int] = field(default_factory=dict)
    g: list[int] = field(default_factory=list)
    P: list[int] = field(default_factory=list)
    q: list[int] = field(default_factory=list)  # total sold per scenario


def _xmax(r: Route) -> int:
    return r.capacity_packs // r.lot_size


def _first_stage(b: _Builder, routes: list[Route], graph, budget: float | None, hedge_levels) -> _Vars:
    v = _Vars()
    for r in routes:
        v.y[r.id] = b.var(0, 1, True)
        v.x[r.id] = b.var(0, _xmax(r), True)
        v.n[r.id] = b.var(0, r.max_shipments, True)
        kp = r.lot_size
        b.row({v.x[r.id]: kp, v.y[r.id]: -r.capacity_packs}, hi=0.0, group="capacity")
        b.row({v.n[r.id]: 1.0, v.y[r.id]: -r.max_shipments}, hi=0.0, group="shipments")
        b.row({v.n[r.id]: r.min_batch, v.x[r.id]: -kp}, hi=0.0, group="batch")
        b.row({v.x[r.id]: kp, v.n[r.id]: -r.max_batch}, hi=0.0, group="batch")
    if budget is not None:
        coefs: dict[int, float] = {}
        for r in routes:
            coefs[v.x[r.id]] = capital_per_pack(graph, r) * r.lot_size / 1e6
            coefs[v.n[r.id]] = r.fixed_per_shipment_uzs / 1e6
            coefs[v.y[r.id]] = r.fixed_activation_uzs / 1e6
        b.row(coefs, hi=budget / 1e6, group="budget")
    return v


def _hedge_block(b: _Builder, v: _Vars, routes: list[Route], levels) -> dict[str, dict[int, float]]:
    """Exact disaggregation: hx_i = sum_k level_k * w_ik, sum_k w_ik = x_i, w_ik <= xmax_i * g_k."""
    v.g = [b.var(0, 1, True) for _ in levels]
    b.row({gk: 1.0 for gk in v.g}, 1.0, 1.0)
    hx: dict[str, dict[int, float]] = {}
    for r in routes:
        ws = [b.var(0, _xmax(r)) for _ in levels]
        b.row({**{w: 1.0 for w in ws}, v.x[r.id]: -1.0}, 0.0, 0.0)
        for w, gk in zip(ws, v.g):
            b.row({w: 1.0, gk: -float(_xmax(r))}, hi=0.0)
        hx[r.id] = {w: lvl for w, lvl in zip(ws, levels)}
    return hx


def _scenario_block(b: _Builder, v: _Vars, s: Scenario, ue: UnitEconomics, routes, emerg, graph, rules,
                    hx, fixed_m: float, bl_var: float, bl_fixed: float) -> None:
    pen = graph.penalty_uzs_per_pack / 1000.0
    D = s.demand / 1000.0
    profit: dict[int, float] = {}
    q_vars: list[int] = []
    sold_coef: dict[int, float] = {}
    markup_row: dict[int, float] = {}
    R = b.var(0.0)
    for r in routes:
        eco = ue.routes[r.id]
        kp = r.lot_size / 1000.0
        a = s.available(r.id)
        if a:
            qv = b.var(0.0)
            q_vars.append(qv)
            b.row({qv: 1.0, v.x[r.id]: -kp}, hi=0.0)
            markup_row[qv] = (1.0 + rules.max_markup) * eco.legal_base / 1000.0
            profit[v.x[r.id]] = profit.get(v.x[r.id], 0.0) - eco.unit_cost / 1000.0 * kp
        for w, lvl in hx.get(r.id, {}).items():
            profit[w] = profit.get(w, 0.0) - eco.hedge_unit / 1000.0 * kp * lvl
    for r in emerg:
        if not s.available(r.id) or r.emergency_cap_packs <= 0:
            continue
        eco = ue.routes[r.id]
        ev = b.var(0.0, r.emergency_cap_packs / 1000.0)
        q_vars.append(ev)
        markup_row[ev] = (1.0 + rules.max_markup) * eco.legal_base / 1000.0
        profit[ev] = profit.get(ev, 0.0) - (eco.unit_cost + r.emergency_premium_uzs) / 1000.0
    q_total = b.var(0.0, D)
    b.row({**{qv: 1.0 for qv in q_vars}, q_total: -1.0}, 0.0, 0.0)
    caps = [ue.caps.wholesale, ue.caps.retail] + ([ue.caps.contract] if ue.caps.contract is not None else [])
    for cap in caps:
        b.row({R: 1.0, q_total: -cap / 1000.0}, hi=0.0)
    b.row({R: 1.0, **{k: -c for k, c in markup_row.items()}}, hi=0.0)
    P = b.var(-INF)
    # P = R + terms - pen*(D - q) - bl_var*q - fixed - bl_fixed
    row = {P: 1.0, R: -1.0}
    for k, c in profit.items():
        row[k] = row.get(k, 0.0) - c
    row[q_total] = row.get(q_total, 0.0) - (pen - bl_var / 1000.0)
    b.row(row, -pen * D - fixed_m - bl_fixed / 1e6, -pen * D - fixed_m - bl_fixed / 1e6)
    v.P.append(P)
    v.q.append(q_total)


def build_model(graph: CalculationGraph, scenarios: Sequence[Scenario], rules: Rules, cfg: OptimizerConfig,
                elastic: frozenset[str] = frozenset()):
    """Returns (builder, vars, routes, weights, objective offset info)."""
    require_calculation_graph(graph)
    routes = list(graph.eligible_routes())
    emerg = list(graph.emergency_routes()) if cfg.emergency_enabled else []
    budget = cfg.budget_uzs if cfg.budget_uzs is not None else graph.finance.budget_uzs
    bl_var, bl_fixed = below_line(graph)
    total_w = sum(s.weight for s in scenarios)
    weights = [s.weight / total_w for s in scenarios]
    b = _Builder(elastic)
    v = _first_stage(b, routes, graph, budget, cfg.hedge_levels)
    hx = _hedge_block(b, v, routes, cfg.hedge_levels) if cfg.hedge_enabled else {}
    fixed_m = 0.0
    fixed_row = {}
    for r in routes:
        fixed_row[v.n[r.id]] = r.fixed_per_shipment_uzs / 1e6
        fixed_row[v.y[r.id]] = r.fixed_activation_uzs / 1e6
    for s in scenarios:
        ue = unit_economics(graph, s, rules)
        _scenario_block_with_fixed(b, v, s, ue, routes, emerg, graph, rules, hx, fixed_row, bl_var, bl_fixed)
    return b, v, routes, weights, emerg


def _scenario_block_with_fixed(b, v, s, ue, routes, emerg, graph, rules, hx, fixed_row, bl_var, bl_fixed):
    _scenario_block(b, v, s, ue, routes, emerg, graph, rules, hx, 0.0, bl_var, bl_fixed)
    # first-stage fixed costs enter P_s through the last equality row: patch it
    coefs, lo, hi = b.rows[-1]
    for k, c in fixed_row.items():
        coefs[k] = coefs.get(k, 0.0) + c  # P - ... + fixed = const: fixed cost reduces P
    b.rows[-1] = (coefs, lo, hi)


def _set_objective(b: _Builder, v: _Vars, weights, cfg: OptimizerConfig) -> None:
    """Minimise the negative of the chosen risk objective."""
    if cfg.risk_mode == "expected":
        for p, w in zip(v.P, weights):
            b.cost[p] -= w
    elif cfg.risk_mode == "cvar":
        eta = b.var(-INF)
        for p, w in zip(v.P, weights):
            b.cost[p] -= (1.0 - cfg.lam) * w
            vs = b.var(0.0)
            b.row({vs: 1.0, p: 1.0, eta: -1.0}, lo=0.0)  # v_s >= eta - P_s
            b.cost[vs] += cfg.lam * w / (1.0 - cfg.alpha)
        b.cost[eta] -= cfg.lam
    else:
        z = b.var(-INF)
        for p in v.P:
            b.row({z: 1.0, p: -1.0}, hi=0.0)
        b.cost[z] -= 1.0


def _add_global_constraints(b: _Builder, v: _Vars, scenarios, weights, cfg: OptimizerConfig) -> None:
    if cfg.service_level is not None:
        total = sum(w * s.demand / 1000.0 for w, s in zip(weights, scenarios))
        b.row({q: w for q, w in zip(v.q, weights)}, lo=cfg.service_level * total, group="service")
    if cfg.min_expected_profit_uzs is not None:
        b.row({p: w for p, w in zip(v.P, weights)}, lo=cfg.min_expected_profit_uzs / 1e6, group="target")


# --------------------------------------------------------------------------- solving

def _status(res) -> str:
    if res.status == 0:
        return "OPTIMAL"
    if res.status == 1 and res.x is not None:
        return "FEASIBLE"
    return {2: "INFEASIBLE", 3: "UNBOUNDED"}.get(res.status, "ERROR")


def _add_months(start: str, offset: int) -> str:
    y, m = int(start[:4]), int(start[5:7])
    t = y * 12 + (m - 1) + offset
    return f"{t // 12:04d}-{t % 12 + 1:02d}"


def spread_months(horizon_start: str, shipments: int) -> tuple[str, ...]:
    if shipments <= 0:
        return ()
    return tuple(_add_months(horizon_start, int((j + 0.5) * 12 / shipments)) for j in range(shipments))


def horizon_label(start: str) -> str:
    return f"{start}..{_add_months(start, 11)}"


def _extract_plan(graph, routes, v, sol, cfg, plan_id: str) -> Plan:
    lines = []
    for r in routes:
        lots = int(round(sol[v.x[r.id]]))
        if lots <= 0:
            continue
        n = int(round(sol[v.n[r.id]]))
        lines.append(PlanLine(route_id=r.id, packs=lots * r.lot_size, shipments=n,
                              months=spread_months(graph.horizon_start, n)))
    h = 0.0
    if cfg.hedge_enabled and v.g:
        h = float(sum(lvl for gk, lvl in zip(v.g, cfg.hedge_levels) if sol[gk] > 0.5))
    return Plan(plan_id=plan_id, sku_id=graph.product.id, horizon=horizon_label(graph.horizon_start),
                lines=tuple(lines), fx_hedge_share=h, risk_mode=cfg.risk_mode,
                alpha=cfg.alpha if cfg.risk_mode == "cvar" else None,
                **({"lambda": cfg.lam} if cfg.risk_mode == "cvar" else {"lambda": None}))


def optimize(graph: CalculationGraph, scenarios: Sequence[Scenario], rules: Rules,
             cfg: OptimizerConfig = OptimizerConfig(), plan_id: str = "plan") -> OptimizeResult:
    t0 = time.perf_counter()
    b, v, routes, weights, _ = build_model(graph, scenarios, rules, cfg)
    _add_global_constraints(b, v, scenarios, weights, cfg)
    _set_objective(b, v, weights, cfg)
    res = b.solve(cfg.time_limit_s, cfg.mip_rel_gap)
    status = _status(res)
    secs = time.perf_counter() - t0
    if res.x is None:
        return OptimizeResult(None, SolveInfo(status, False, None, None, None, secs), None,
                              notes=("no feasible plan found",))
    obj = -float(res.fun)
    bound = -float(res.mip_dual_bound) if getattr(res, "mip_dual_bound", None) is not None else None
    gap = float(res.mip_gap) if getattr(res, "mip_gap", None) is not None else None
    plan = _extract_plan(graph, routes, v, res.x, cfg, plan_id)
    profits = tuple(float(res.x[p]) * 1e6 for p in v.P)
    metrics = risk_metrics(profits, weights, cfg.alpha)
    notes = () if status == "OPTIMAL" else ("feasible, optimality not proven",)
    return OptimizeResult(plan, SolveInfo(status, status == "OPTIMAL", obj, bound, gap, secs), metrics,
                          profits, notes)


def solve_elastic(graph, scenarios, rules, cfg: OptimizerConfig, relax: frozenset[str]):
    """Minimise normalised violation of the groups in `relax` (legal constraints are never relaxable)."""
    b, v, routes, weights, _ = build_model(graph, scenarios, rules, cfg, elastic=relax)
    _add_global_constraints(b, v, scenarios, weights, cfg)
    for g in relax:
        for s in b.slack[g]:
            b.cost[s] += 1.0
    res = b.solve(cfg.time_limit_s, cfg.mip_rel_gap)
    if res.x is None or res.status not in (0, 1):
        return None
    amounts = {g: float(sum(res.x[s] for s in b.slack[g])) for g in relax}
    return float(res.fun), amounts
