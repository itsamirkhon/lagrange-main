"""Brute-force oracle and random mini-problems for checking the MILP (expected-profit mode)."""
from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np

from lagrange_rx.engine.optimize import horizon_label
from lagrange_rx.engine.twin import capital_per_pack, evaluate_plan
from lagrange_rx.engine.types import Plan, PlanLine, Scenario
from lagrange_rx.engine.inputs import (
    CalculationGraph, EngineGraph, CostSpec, DemandSpec, FinanceSpec, ProductSpec, RouteSpec, SupplierSpec,
)
from lagrange_rx.engine.rules import Rules


def _plan(graph: CalculationGraph, choice: dict[str, tuple[int, int]]) -> Plan:
    lines = tuple(
        PlanLine(route_id=rid, packs=lots * graph.route(rid).lot_size, shipments=n)
        for rid, (lots, n) in choice.items() if lots > 0
    )
    return Plan(plan_id="oracle", sku_id=graph.product.id, horizon=horizon_label(graph.horizon_start),
                lines=lines, risk_mode="expected")


def _shipment_options(r: RouteSpec, lots: int) -> list[int]:
    if lots == 0:
        return [0]
    packs = lots * r.lot_size
    return [n for n in range(1, r.max_shipments + 1) if r.min_batch * n <= packs <= r.max_batch * n]


def _within_budget(graph: CalculationGraph, plan: Plan, budget: float | None) -> bool:
    if budget is None:
        return True
    total = 0.0
    for ln in plan.lines:
        r = graph.route(ln.route_id)
        total += (capital_per_pack(graph, r) * ln.packs + r.fixed_per_shipment_uzs * ln.shipments
                  + r.fixed_activation_uzs)
    return total <= budget + 1e-6


def expected_profit(graph, plan, scenarios, rules, allow_emergency=True) -> float:
    tw = sum(s.weight for s in scenarios)
    return sum(s.weight * evaluate_plan(graph, plan, s, rules, allow_emergency=allow_emergency).profit_uzs
               for s in scenarios) / tw


@dataclass(frozen=True)
class OracleResult:
    best_expected_uzs: float
    best_plan: Plan
    evaluated: int


def brute_force(graph: CalculationGraph, scenarios, rules: Rules, *, allow_emergency=True) -> OracleResult:
    routes = list(graph.eligible_routes())
    budget = graph.finance.budget_uzs
    per_route = []
    for r in routes:
        opts = [(lots, n) for lots in range(r.capacity_packs // r.lot_size + 1) for n in _shipment_options(r, lots)]
        per_route.append(opts)
    best, best_plan, count = -float("inf"), None, 0
    for combo in itertools.product(*per_route):
        plan = _plan(graph, {r.id: c for r, c in zip(routes, combo)})
        if not _within_budget(graph, plan, budget):
            continue
        count += 1
        val = expected_profit(graph, plan, scenarios, rules, allow_emergency)
        if val > best:
            best, best_plan = val, plan
    return OracleResult(best, best_plan, count)


def random_mini_problem(seed: int) -> tuple[CalculationGraph, list[Scenario]]:
    """Tiny random instance: 2 routes (+ sometimes an emergency route), 3 scenarios, <=5 lots per route."""
    rng = np.random.default_rng(seed)
    kc = rng.uniform(3.0, 6.0, 3)
    routes = []
    for i in range(2):
        lot = 1000
        routes.append(RouteSpec(
            id=f"R{i}", supplier_id=f"S{i}", name=f"r{i}", mode=["sea", "rail"][i], currency="USD",
            kc=float(kc[i]), duty_rate=float(rng.uniform(0.05, 0.15)), fees_uzs=float(rng.uniform(500, 2000)),
            freight_uzs=float(rng.uniform(1000, 5000)), lot_size=lot,
            min_batch=int(rng.integers(0, 2)) * lot, max_batch=int(rng.integers(2, 5)) * lot,
            capacity_packs=int(rng.integers(2, 6)) * lot, max_shipments=int(rng.integers(1, 4)),
            fixed_per_shipment_uzs=float(rng.uniform(0, 3e6)), fixed_activation_uzs=float(rng.uniform(0, 2e6)),
        ))
    if rng.random() < 0.5:
        routes.append(RouteSpec(
            id="E0", supplier_id="S0", name="air", mode="air", currency="USD", kc=float(kc[2]),
            duty_rate=0.1, fees_uzs=1000.0, freight_uzs=6000.0, lot_size=1000, min_batch=0, max_batch=2000,
            capacity_packs=0, max_shipments=0, emergency=True,
            emergency_premium_uzs=float(rng.uniform(0, 8000)), emergency_cap_packs=int(rng.integers(1, 3)) * 1000,
        ))
    budget = float(rng.uniform(3e8, 9e8)) if rng.random() < 0.4 else None
    graph = EngineGraph(
        client_id="mini", product=ProductSpec(id="P", name="d", chun_uzs=float(rng.uniform(95_000, 140_000)),
                                          chchn_uzs=float(rng.uniform(130_000, 200_000))),
        suppliers=[SupplierSpec(id=f"S{i}", name=f"s{i}", reliability=0.9) for i in range(2)],
        routes=routes,
        cost_items=[CostSpec(id="bl", name="bl", layer="below_line", per_pack_uzs=float(rng.uniform(0, 3000)))],
        demand=DemandSpec(p10=2000, p50=4000, p90=6000),
        finance=FinanceSpec(credit_rate_annual=float(rng.uniform(0, 0.3)), budget_uzs=budget, inventory_days=30,
                        receivable_days=20),
        nominal_fx={"USD": 12_000.0}, horizon_start="2026-11",
        penalty_uzs_per_pack=float(rng.uniform(0, 5000)),
    ).for_calculation()
    scenarios = []
    for k in range(3):
        fx = 12_000.0 * float(rng.uniform(0.9, 1.2))
        scenarios.append(Scenario(
            id=f"m{k}", name="mini", weight=float(rng.uniform(0.5, 2.0)), fx={"USD": fx}, ref_fx_start=12_000.0,
            ref_fx_path=tuple([fx] * 12), demand=float(rng.integers(1, 7) * 1000),
            cut_month=int(rng.integers(1, 13)) if rng.random() < 0.3 else None,
            cut_depth=float(rng.uniform(0.1, 0.37)),
            route_available={"R0": False} if rng.random() < 0.2 else {},
            freight_mult={"sea": float(rng.uniform(0.8, 1.5))},
        ))
    return graph, scenarios
