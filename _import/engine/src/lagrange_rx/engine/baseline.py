"""Baseline = the client's current practice: routes, packs and shipments of the last 12 confirmed months."""
from __future__ import annotations

from lagrange_rx.engine.optimize import horizon_label
from lagrange_rx.engine.twin import last_12_months_shipments
from lagrange_rx.engine.types import Plan, PlanLine
from lagrange_rx.ontology import CalculationGraph


def baseline_plan(graph: CalculationGraph, plan_id: str = "baseline") -> Plan:
    eligible = {r.id for r in graph.eligible_routes()}
    packs: dict[str, int] = {}
    months: dict[str, list[str]] = {}
    for sh in last_12_months_shipments(graph):
        if sh.route_id not in eligible:
            continue
        packs[sh.route_id] = packs.get(sh.route_id, 0) + sh.packs
        months.setdefault(sh.route_id, []).append(sh.month)
    lines = tuple(PlanLine(route_id=rid, packs=p, shipments=len(months[rid]), months=tuple(sorted(months[rid])))
                  for rid, p in sorted(packs.items()))
    return Plan(plan_id=plan_id, sku_id=graph.product.id, horizon=horizon_label(graph.horizon_start),
                lines=lines, fx_hedge_share=0.0, risk_mode="baseline", alpha=None, **{"lambda": None})
