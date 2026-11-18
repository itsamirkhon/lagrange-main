"""Engine-side input for the evaluation: the L0 ontology graph produced by Ingest (eval/ingest_graph_L0.json)
plus the engine-only parameters the documents do not state.

Nothing here reads the simulation truth: every number is either a field of the Ingest graph (documents) or an
explicit, documented ASSUMPTION written into the graph with provenance kind `assumption`. The assumptions are
collected in `ASSUMPTIONS` and copied verbatim into the evaluation report.
"""
from __future__ import annotations

import json
import statistics
from datetime import date
from decimal import Decimal
from pathlib import Path

from lagrange_rx import ontology as onto
from lagrange_rx.engine.adapter import EngineConfig
from lagrange_rx.engine.inputs import ActualSpec

ROOT = Path(__file__).resolve().parents[1]
INGEST_GRAPH_PATH = ROOT / "eval" / "ingest_graph_L0.json"
HORIZON_START = "2026-11"
FOCUS_SKU = "kardiozan-10mg-n30"  # the SKU of the registry record (product-side rule: the registered SKU)

ASSUMPTIONS = {
    "horizon": "plan horizon 2026-11..2027-10 (the 12 months after the documents); the truth evaluator replays the "
               "plan on the 12 truth months 2025-11..2026-10, i.e. next year is assumed to look like the last one",
    "route_limits": "min/max shipment packs = min/max shipment size seen in the documents on that route; "
                    "max_shipments_per_year = shipments seen on that route in the last 12 months (the truth "
                    "evaluator takes one order per month, so the total must stay <= 12); capacity_packs_per_year = "
                    "max_shipments x max_shipment_packs; activation cost 0; is_eligible = True",
    "freight_uzs": "freight per pack / per shipment in UZS = USD rate card (document) x the last USD rate in the graph",
    "lot_size": "500 packs: every observed shipment is a multiple of 500",
    "demand": "monthly p50 = packs sold in the same calendar month of the last 12 months; p10/p90 = p50 -/+ 1.2816 x "
              "cv, cv = std/mean of the 12 monthly totals (coverage not calibrated)",
    "reliability": "0.95 for every supplier (not in any document)",
    "credit": "no CreditLine in the documents: budget unlimited, annual rate 20% (adapter default)",
    "receivable_days": "30 days (PaymentTerms.customer_days is in no document)",
    "penalty": "0 UZS per unmet pack (not in any document)",
    "excluded_routes": "routes without a producer (air corridor) cannot be tied to a supplier: removed from the engine graph",
    "reference_currency": "registry Rn is stated in UZS: a UZS rate of 1 is added (reference currency = UZS)",
}


def load_ingest_graph(path: Path = INGEST_GRAPH_PATH) -> tuple[onto.ClientGraph, str]:
    """Read the Ingest result. Derived fields that the ontology recomputes are dropped before validation."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = data["graph"]
    for r in raw["registry_records"]:
        r.pop("chun_formula_incl_vat", None)
    for c in raw["customs_entries"]:
        c.pop("legal_base_c_per_pack", None)
    return onto.ClientGraph.model_validate(raw), data["snapshot_id"]


def _assume(note: str) -> onto.Provenance:
    return onto.Provenance(kind="assumption", source="eval/engine_inputs.py", note=note)


def _months(start: str, n: int) -> list[str]:
    y, m = int(start[:4]), int(start[5:])
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _window_before(horizon_start: str, n: int = 12) -> list[str]:
    y, m = int(horizon_start[:4]), int(horizon_start[5:])
    idx = y * 12 + m - 1 - n
    return _months(f"{idx // 12:04d}-{idx % 12 + 1:02d}", n)


def demand_rows(graph: onto.ClientGraph, sku: str, horizon_start: str) -> list[onto.DemandForecast]:
    window = _window_before(horizon_start)
    horizon = _months(horizon_start, 12)
    totals = {m: 0 for m in window}
    for s in graph.sales_records:
        if s.product == sku and s.period in totals:
            totals[s.period] += s.qty_packs
    if not all(totals.values()):
        raise ValueError("demand forecast needs sales in each of the 12 months before the horizon")
    cv = statistics.pstdev(totals.values()) / statistics.fmean(totals.values())
    prov = _assume(ASSUMPTIONS["demand"])
    rows = []
    for m_past, m_next in zip(window, horizon):
        p50 = Decimal(totals[m_past])
        d = p50 * Decimal(str(round(1.2816 * cv, 6)))
        rows.append(onto.DemandForecast(period=m_next, product=sku, p10=max(Decimal(0), p50 - d), p50=p50, p90=p50 + d,
                                        method="same month of the last 12 months", coverage_calibrated=False,
                                        provenance=prov))
    return rows


def enrich_routes(graph: onto.ClientGraph, horizon_start: str) -> tuple[list[onto.Route], list[str]]:
    usd = max((r for r in graph.fx_rates if r.currency == "USD"), key=lambda r: r.date).rate_uzs
    window = set(_window_before(horizon_start))
    out, notes = [], []
    for r in graph.routes:
        if r.production_site is None:
            notes.append(f"route '{r.route_id}' removed: no producer is known for this corridor")
            continue
        ships = [s for s in graph.shipments if s.route == r.route_id]
        recent = [s for s in ships if f"{s.dispatch_date.year:04d}-{s.dispatch_date.month:02d}" in window]
        if not ships or not recent:
            notes.append(f"route '{r.route_id}' removed: no shipment in the last 12 months, limits cannot be read")
            continue
        packs = [s.packs for s in ships]
        n = len(recent)
        upd = dict(
            freight_per_pack_uzs=r.freight_per_pack_usd * usd, freight_per_shipment_uzs=r.freight_per_shipment_usd * usd,
            min_shipment_packs=min(packs), max_shipment_packs=max(packs), max_shipments_per_year=n,
            capacity_packs_per_year=n * max(packs), activation_cost_uzs=Decimal(0), is_eligible=True,
            field_provenance={**r.field_provenance, **{k: _assume(ASSUMPTIONS["route_limits"]) for k in (
                "min_shipment_packs", "max_shipment_packs", "max_shipments_per_year", "capacity_packs_per_year",
                "activation_cost_uzs", "is_eligible")},
                "freight_per_pack_uzs": _assume(ASSUMPTIONS["freight_uzs"]),
                "freight_per_shipment_uzs": _assume(ASSUMPTIONS["freight_uzs"])})
        out.append(r.model_copy(update=upd))
    return out, notes


def engine_input_graph(graph: onto.ClientGraph, sku: str = FOCUS_SKU, horizon_start: str = HORIZON_START
                       ) -> tuple[onto.ClientGraph, list[str]]:
    """The Ingest graph made computable: routes with limits, demand forecast, UZS reference rate. Notes list every
    removal; assumptions are recorded in `field_provenance`."""
    routes, notes = enrich_routes(graph, horizon_start)
    keep = {r.route_id for r in routes}
    ships = [s for s in graph.shipments if s.route in keep]
    fx = list(graph.fx_rates)
    last = max(r.date for r in fx)
    fx.append(onto.FxRate(currency="UZS", date=last, rate_uzs=Decimal(1), source="identity",
                          provenance=_assume(ASSUMPTIONS["reference_currency"])))
    cur = list(graph.currencies) + [onto.Currency(code="UZS", provenance=_assume(ASSUMPTIONS["reference_currency"]))]
    costs = [c for c in graph.cost_items if c.scope is None or sku in c.scope]  # a pool shared among other SKUs only
    enriched = graph.model_copy(update=dict(
        cost_items=costs, routes=routes, shipments=ships, demand_forecasts=demand_rows(graph, sku, horizon_start), fx_rates=fx,
        currencies=cur))
    return enriched, notes


def calibration_actual(path: Path = INGEST_GRAPH_PATH) -> ActualSpec:
    """The client's 12-month result as Ingest computed it from the documents (`pnl_check`, not from the truth)."""
    p = json.loads(Path(path).read_text(encoding="utf-8"))["pnl_check"]
    return ActualSpec(revenue_uzs=float(p["revenue_ex_vat_uzs"]),
                      total_cost_uzs=float(p["cogs_ex_vat_uzs"]) + float(p["operating_costs_uzs"]))


def engine_config(horizon_start: str = HORIZON_START, sku: str = FOCUS_SKU) -> EngineConfig:
    return EngineConfig(calibration_actual=calibration_actual(),
        horizon_start=horizon_start, sku_id=sku, client_id="L0-ingest", penalty_uzs_per_pack=0.0, default_lot_size=500,
        default_reliability=0.95, default_credit_rate=0.20, receivable_days=30,
        route_params={},
    )
