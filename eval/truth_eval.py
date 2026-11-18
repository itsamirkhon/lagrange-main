"""Stage 5: evaluation of engine plans on the simulation truth (`sim.generator.evaluate.evaluate_plan_on_truth`).

Only eval/ may read the truth. Three jobs:

1. `build_mapping` - match the engine's routes, suppliers and SKU to truth entities BY FEATURES (transport mode,
   carrier, customs post, transit days, supplier name/country/currency, trade name, retail ceiling), never by
   simulation ids. Each row carries a confidence; ambiguity or a missing match raises `MappingError`.
2. `translate_plan` - DECISION_ENGINE section 8 plan -> the truth evaluator's `monthly_orders` format (12 orders,
   one per month; months without a shipment are explicit zero orders, because the evaluator would otherwise fall
   back to the baseline order for them).
3. `SimTruth` - the `TruthEvaluator`: profit of a plan in each engine scenario. The truth has no scenario input, so a
   scenario is applied as a documented overlay on a copy of the truth tables (demand, FX, freight, price-cap cut,
   route outage) and the unchanged simulation function is called on that copy. A nominal scenario touches nothing.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import pandas as pd

from lagrange_rx import ontology as onto
from lagrange_rx.engine.inputs import CalculationGraph
from lagrange_rx.engine.types import Plan, Scenario
from sim.generator.evaluate import evaluate_plan_on_truth, get_baseline_plan

TRUTH_DIR = Path(__file__).resolve().parents[1] / "sim" / "truth"
MIN_CONFIDENCE = 0.75
_MODE_WORDS = (("auto", "road"), ("rail", "rail"), ("air", "air"), ("sea", "sea"))
_TABLES_TOUCHED = ("sales.parquet", "fx.parquet", "customs.parquet", "routes.parquet", "registry_events.parquet")


class MappingError(ValueError):
    """No feature match, or several equally good ones. The evaluator never guesses."""


def _norm(s: object) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().casefold()


def truth_mode(text: str) -> str:
    """Transport mode class of a truth route/carrier label ('Auto via Kazakhstan' -> road)."""
    for word, mode in _MODE_WORDS:
        if word in _norm(text):
            return mode
    raise MappingError(f"cannot read a transport mode from '{text}'")


@dataclass(frozen=True)
class RowMatch:
    kind: str  # route | supplier | sku
    engine_id: str
    truth_id: str
    confidence: float
    matched: tuple[str, ...]
    mismatched: tuple[str, ...]
    not_compared: tuple[str, ...]

    def to_dict(self) -> dict:
        return {"kind": self.kind, "engine_id": self.engine_id, "truth_id": self.truth_id,
                "confidence": round(self.confidence, 4), "matched_features": list(self.matched),
                "mismatched_features": list(self.mismatched), "not_compared": list(self.not_compared)}


def _pick(kind: str, engine_id: str, engine_feats: dict, candidates: dict[str, dict]) -> RowMatch:
    """Score every truth candidate; the best must be unique and confident enough."""
    scored = []
    for tid, feats in candidates.items():
        comp = [k for k, v in engine_feats.items() if v is not None and k in feats]
        hit = tuple(k for k in comp if _norm(engine_feats[k]) == _norm(feats[k]))
        miss = tuple(k for k in comp if k not in hit)
        skipped = tuple(k for k, v in engine_feats.items() if v is None or k not in feats)
        conf = len(hit) / len(comp) if comp else 0.0
        scored.append((conf, len(hit), tid, hit, miss, skipped))
    scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
    if not scored or scored[0][0] < MIN_CONFIDENCE:
        raise MappingError(f"{kind} '{engine_id}': no truth entity matches by features "
                           f"(best: {scored[0][:3] if scored else None})")
    top = [s for s in scored if (s[0], s[1]) == (scored[0][0], scored[0][1])]
    if len(top) > 1:
        raise MappingError(f"{kind} '{engine_id}': ambiguous, equally good truth entities {[t[2] for t in top]}")
    conf, _, tid, hit, miss, skipped = scored[0]
    return RowMatch(kind, engine_id, tid, conf, hit, miss, skipped)


@dataclass(frozen=True)
class TruthMapping:
    routes: dict[str, RowMatch]
    suppliers: dict[str, str]  # engine route id -> truth supplier id
    supplier_rows: tuple[RowMatch, ...]
    sku: RowMatch
    route_modes: dict[str, str]  # truth route id -> mode class

    def truth_route(self, engine_route_id: str) -> str:
        try:
            return self.routes[engine_route_id].truth_id
        except KeyError:
            raise MappingError(f"route '{engine_route_id}' has no truth match (not in the mapping)") from None

    def table(self) -> list[dict]:
        return [r.to_dict() for r in (*self.routes.values(), *self.supplier_rows, self.sku)]

    @property
    def min_confidence(self) -> float:
        return min(r.confidence for r in (*self.routes.values(), *self.supplier_rows, self.sku))


def _truth_route_features(truth: Path) -> dict[str, dict]:
    routes = pd.read_parquet(truth / "routes.parquet")
    carriers = pd.read_parquet(truth / "carriers.parquet").set_index("carrier_id")
    out = {}
    for _, r in routes.iterrows():
        out[str(r["route_id"])] = {
            "mode": truth_mode(str(r["mode"])), "carrier": carriers.loc[r["carrier_id"], "name"],
            "customs_post": r["customs_post"], "transit_days": int(r["transit_days"])}
    return out


def _truth_supplier_features(truth: Path) -> dict[str, dict]:
    sup = pd.read_parquet(truth / "suppliers.parquet")
    return {str(r["supplier_id"]): {"name": r["name"], "country": r["country"], "currency": r["currency"]}
            for _, r in sup.iterrows()}


def _truth_sku_features(truth: Path) -> dict[str, dict]:
    sku = pd.read_parquet(truth / "skus.parquet")
    return {str(r["sku_id"]): {"trade_name": r["trade_name"], "retail_uzs": round(float(r["retail_uzs"]), 2),
                               "manufacturer": None} for _, r in sku.iterrows()}


def build_mapping(graph: CalculationGraph, onto_graph: onto.ClientGraph, truth_dir: Path = TRUTH_DIR) -> TruthMapping:
    """Feature mapping engine -> truth. `onto_graph` is the graph the engine graph was built from (names of
    carriers and suppliers, customs posts and transit times live there; the engine's own route spec only has an id)."""
    truth_dir = Path(truth_dir)
    t_routes, t_sup = _truth_route_features(truth_dir), _truth_supplier_features(truth_dir)
    sites = {s.site_id: s for s in onto_graph.production_sites}
    sup = {s.supplier_id: s for s in onto_graph.suppliers}
    car = {c.carrier_id: c for c in onto_graph.carriers}
    ont_routes = {r.route_id: r for r in onto_graph.routes}
    routes, sup_rows, sup_of = {}, [], {}
    for r in graph.routes:
        o = ont_routes[r.id]
        s = sup[sites[o.production_site].supplier]
        feats = {"mode": o.transport_mode, "carrier": car[o.carrier].name, "customs_post": o.customs_post,
                 "transit_days": o.lead_time_days.p50}
        routes[r.id] = _pick("route", r.id, feats, t_routes)
        srow = _pick("supplier", s.supplier_id, {"name": s.name, "country": s.country, "currency": r.currency}, t_sup)
        sup_rows.append(srow)
        sup_of[r.id] = srow.truth_id
    prod = next(p for p in onto_graph.products if p.sku_id == graph.product.id)
    sku = _pick("sku", prod.sku_id, {"trade_name": prod.trade_name, "retail_uzs": round(graph.product.chchn_uzs, 2),
                                     "manufacturer": prod.manufacturer}, _truth_sku_features(truth_dir))
    seen: dict[str, str] = {}
    for rid, m in routes.items():
        if m.truth_id in seen:
            raise MappingError(f"routes '{seen[m.truth_id]}' and '{rid}' map to the same truth route {m.truth_id}")
        seen[m.truth_id] = rid
    uniq = {r.engine_id: r for r in sup_rows}
    return TruthMapping(routes, sup_of, tuple(uniq.values()), sku,
                        {tid: t_routes[tid]["mode"] for tid in t_routes})


# --------------------------------------------------------------------------- plan translation

_WINDOW = ("2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07",
           "2026-08", "2026-09", "2026-10")  # the truth evaluator's 12 months


def _month_slot(month: str, horizon_start: str) -> int:
    """Index 0..11 of a plan month: a month of the truth window keeps its place; a horizon month maps by order."""
    if month in _WINDOW:
        return _WINDOW.index(month)
    mi = lambda s: int(s[:4]) * 12 + int(s[5:7]) - 1  # noqa: E731
    k = mi(month) - mi(horizon_start)
    if not 0 <= k < 12:
        raise MappingError(f"plan month {month} is neither in the horizon starting {horizon_start} nor in the truth window")
    return k


def _free_slot(target: int, taken: set[int]) -> int:
    for d in range(12):
        for cand in (target - d, target + d):
            if 0 <= cand < 12 and cand not in taken:
                return cand
    raise MappingError("more than 12 shipments: the truth evaluator takes one order per month")


def schedule_shipments(plan: Plan, horizon_start: str) -> list[tuple[int, str, int]]:
    """(slot, engine route id, packs) for every shipment, one per slot. Target slot = the line's month if given,
    else evenly spread (the engine's own rule). A taken slot moves to the nearest free one (earlier first)."""
    wanted = []
    for ln in plan.lines:
        n = ln.shipments
        if ln.packs <= 0 or n <= 0:
            continue
        if ln.months and len(ln.months) != n:
            raise MappingError(f"line {ln.route_id}: {len(ln.months)} months for {n} shipments")
        slots = [_month_slot(m, horizon_start) for m in ln.months] if ln.months else [int((j + 0.5) * 12 / n) for j in range(n)]
        base, extra = divmod(ln.packs, n)
        wanted += [(slot, ln.route_id, base + (1 if j < extra else 0)) for j, slot in enumerate(sorted(slots))]
    if len(wanted) > 12:
        raise MappingError(f"{len(wanted)} shipments in the plan: the truth evaluator takes one order per month")
    taken: set[int] = set()
    out = []
    for slot, rid, packs in sorted(wanted):
        s = _free_slot(slot, taken)
        taken.add(s)
        out.append((s, rid, packs))
    return sorted(out)


def translate_plan(plan: Plan, mapping: TruthMapping, horizon_start: str, truth_dir: Path = TRUTH_DIR,
                   unavailable: frozenset[str] = frozenset()) -> dict:
    """DECISION_ENGINE section 8 plan -> `evaluate_plan_on_truth` plan. `unavailable` = truth route ids that
    deliver nothing (route outage in a scenario)."""
    if mapping.sku.engine_id != plan.sku_id:
        raise MappingError(f"plan sku '{plan.sku_id}' is not the mapped sku '{mapping.sku.engine_id}'")
    base = get_baseline_plan(truth_dir)
    fallback = {o["month"]: o for o in base["monthly_orders"]}
    orders = {m: {"month": m, "qty_packs": 0, "supplier_id": fallback[m]["supplier_id"], "route_id": fallback[m]["route_id"]}
              for m in _WINDOW}
    for slot, rid, packs in schedule_shipments(plan, horizon_start):
        troute = mapping.truth_route(rid)
        orders[_WINDOW[slot]] = {"month": _WINDOW[slot], "qty_packs": 0 if troute in unavailable else packs,
                                 "supplier_id": mapping.suppliers[rid], "route_id": troute}
    return {"sku_id": mapping.sku.truth_id, "selling_price_ex_vat_uzs": base["selling_price_ex_vat_uzs"],
            "monthly_orders": [orders[m] for m in _WINDOW], "monthly_marketing_uzs": base["monthly_marketing_uzs"]}


# --------------------------------------------------------------------------- the 15% rule on the truth (interpretation A)
# UZ-PF13-WHOLESALE-MARKUP (needs_legal_review): wholesale revenue <= (1 + 15%) x legal base of the packs SOLD.
# `evaluate_plan_on_truth` (sim/, untouched) prices at the registry ceiling and does not know the rule. Interpretation A
# post-processes its result here, independently of the engine's twin: the legal base is read from the truth's own
# customs tables (CIP + duty + clearance fee, ex VAT, freight EXCLUDED - the PKM-185 base), never from twin/optimize.

def _focus_sales_window(truth: Path, sku: str) -> pd.DataFrame:
    sales = pd.read_parquet(truth / "sales.parquet")
    return sales[(sales["sku_id"] == sku) & sales["month"].isin(set(_WINDOW))]


def legal_base_per_pack_by_lot(truth: Path, sku: str) -> dict[str, float]:
    """Legal base per pack of each lot of `sku`: (CIP + duty + clearance fee) / packs of its shipment (inbound freight
    is not part of the base)."""
    cus = pd.read_parquet(Path(truth) / "customs.parquet").set_index("shipment_id")
    lots = pd.read_parquet(Path(truth) / "lots.parquet")
    lots = lots[lots["sku_id"] == sku]
    out = {}
    for _, lot in lots.iterrows():
        c = cus.loc[lot["shipment_id"]]
        landed = float(c["landed_cost_ex_vat_uzs"])
        base = float(c["cip_uzs"] + c["duty_uzs"] + c["clearance_fee_uzs"])
        out[str(lot["lot_id"])] = float(lot["unit_cost_uzs"]) * base / landed
    return out


def legal_base_share_of_cost(truth: Path, sku: str) -> float:
    """kappa = legal base / landed cost of the sku's lots (cost-weighted, freight is the difference). The evaluator does
    not return which lots a plan sold, so the base of the packs a plan sold is estimated as kappa x its COGS."""
    lots = pd.read_parquet(Path(truth) / "lots.parquet")
    lots = lots[lots["sku_id"] == sku]
    base = legal_base_per_pack_by_lot(truth, sku)
    cost = sum(float(r["unit_cost_uzs"]) * float(r["initial_qty"]) for _, r in lots.iterrows())
    b = sum(base[str(r["lot_id"])] * float(r["initial_qty"]) for _, r in lots.iterrows())
    return b / cost


def cap_wholesale_revenue(revenue: float, cogs: float, kappa: float, multiplier: float) -> dict:
    """Interpretation A applied to one evaluation: revenue is cut to multiplier x legal base of packs sold
    (= kappa x COGS); the cut is subtracted from profit one for one (costs are unchanged)."""
    base = kappa * cogs
    cap = multiplier * base
    capped = min(revenue, cap)
    return {"revenue_uzs": capped, "excess_uzs": revenue - capped, "legal_base_sold_uzs": base,
            "markup_before": (revenue / base - 1.0) if base > 0 else None}


def client_history_violation(truth: Path = TRUTH_DIR, sku: str = "SKU_CARDIO_01", multiplier: float | None = None) -> dict:
    """How much the client ALREADY sells above the ceiling, from the truth history (the 12-month window of the
    evaluator and, for scale, the whole history): sales rows x the legal base of the lot each pack came from."""
    from lagrange_rx.engine.rules import load_rules
    mult = 1.0 + (load_rules().max_markup if multiplier is None else multiplier)
    base = legal_base_per_pack_by_lot(truth, sku)
    sales = pd.read_parquet(Path(truth) / "sales.parquet")
    sales = sales[sales["sku_id"] == sku]

    def block(df: pd.DataFrame) -> dict:
        rev = float((df["qty_packs"] * df["unit_price_ex_vat_uzs"]).sum())
        lb = float(sum(q * base[str(l)] for q, l in zip(df["qty_packs"], df["lot_id"])))
        excess = max(0.0, rev - mult * lb)
        return {"packs": int(df["qty_packs"].sum()), "revenue_uzs": rev, "legal_base_sold_uzs": lb,
                "ceiling_uzs": mult * lb, "markup": rev / lb - 1.0, "excess_over_ceiling_uzs": excess,
                "excess_share_of_revenue": excess / rev}
    win = sales[sales["month"].isin(set(_WINDOW))]
    w = block(win)
    return {"rule": "UZ-PF13-WHOLESALE-MARKUP (needs_legal_review)", "multiplier": mult,
            "basis": "sales rows x legal base of the lot sold (CIP + duty + clearance fee, freight excluded), pooled over the period",
            "window_2025-11_2026-10": w, "whole_history": block(sales),
            "headline": f"today the company sells above the legal ceiling by "
                        f"{w['excess_over_ceiling_uzs'] / 1e6:,.1f} M UZS per year "
                        f"({w['excess_share_of_revenue']:.1%} of revenue; markup on the legal base {w['markup']:.1%})"}


# --------------------------------------------------------------------------- scenario overlay

@dataclass(frozen=True)
class Overlay:
    """How an engine scenario changes the truth world. All factors 1 and no outage = the truth as it is."""
    demand_factor: float = 1.0
    fx_ratio: float = 1.0
    price_factor: float = 1.0
    freight: tuple[tuple[str, float], ...] = ()  # (truth route id, multiplier)
    unavailable: frozenset[str] = frozenset()

    @property
    def world_is_nominal(self) -> bool:
        return (self.demand_factor == 1.0 and self.fx_ratio == 1.0 and self.price_factor == 1.0
                and all(m == 1.0 for _, m in self.freight))

    def key(self) -> str:
        return hashlib.sha1(repr((round(self.demand_factor, 9), round(self.fx_ratio, 9), round(self.price_factor, 9),
                                  tuple((r, round(m, 9)) for r, m in self.freight))).encode()).hexdigest()[:16]


def _price_factor(cut_month: int | None, depth: float, weights: Sequence[float]) -> float:
    if cut_month is None or depth <= 0:
        return 1.0
    mult = [1.0 if i < cut_month - 1 else 1.0 - depth for i in range(12)]
    return sum(w * m for w, m in zip(weights, mult)) / sum(weights)


def build_overlay(s: Scenario, mapping: TruthMapping, *, nominal_fx: float, demand_p50: float, ccy: str,
                  month_weights: Sequence[float]) -> Overlay:
    fx = s.fx.get(ccy, nominal_fx) / nominal_fx
    freight = tuple(sorted((tid, s.freight(mode)) for tid, mode in mapping.route_modes.items() if s.freight(mode) != 1.0))
    off = frozenset(mapping.truth_route(rid) for rid in mapping.routes if not s.available(rid))
    return Overlay(demand_factor=s.demand / demand_p50, fx_ratio=fx,
                   price_factor=_price_factor(s.cut_month, s.cut_depth, month_weights), freight=freight, unavailable=off)


def apply_overlay(src: Path, dst: Path, ov: Overlay, sku: str) -> None:
    """Write a copy of the truth tables with the overlay applied (untouched tables are symlinked)."""
    dst.mkdir(parents=True, exist_ok=True)
    for f in Path(src).glob("*.parquet"):
        if f.name not in _TABLES_TOUCHED:
            (dst / f.name).symlink_to(f.resolve())
    win = set(_WINDOW)
    sales = pd.read_parquet(src / "sales.parquet")
    m = (sales["sku_id"] == sku) & sales["month"].isin(win)
    sales.loc[m, "qty_packs"] = (sales.loc[m, "qty_packs"] * ov.demand_factor).round().astype(sales["qty_packs"].dtype)
    sales.to_parquet(dst / "sales.parquet")
    fx = pd.read_parquet(src / "fx.parquet")
    fx.loc[fx["currency"] == "USD", "rate_uzs"] *= ov.fx_ratio
    fx.to_parquet(dst / "fx.parquet")
    cus = pd.read_parquet(src / "customs.parquet")
    lots = pd.read_parquet(src / "lots.parquet")
    ships = pd.read_parquet(src / "shipments.parquet")
    focus = set(lots.loc[lots["sku_id"] == sku, "shipment_id"]) & set(ships.loc[ships["month"].isin(win), "shipment_id"])
    mc = cus["shipment_id"].isin(focus)
    share = (cus.loc[mc, "cip_uzs"] + cus.loc[mc, "duty_uzs"]) / cus.loc[mc, "landed_cost_ex_vat_uzs"]
    cus.loc[mc, "landed_unit_cost_uzs"] *= 1.0 + share * (ov.fx_ratio - 1.0)
    cus.to_parquet(dst / "customs.parquet")
    routes = pd.read_parquet(src / "routes.parquet")
    for tid, mult in ov.freight:
        routes.loc[routes["route_id"] == tid, ["freight_per_shipment_usd", "freight_per_pack_usd"]] *= mult
    routes.to_parquet(dst / "routes.parquet")
    reg = pd.read_parquet(src / "registry_events.parquet")
    reg.loc[reg["event_date"] == reg["event_date"].max(), "wholesale_ex_vat_uzs"] *= ov.price_factor
    reg.to_parquet(dst / "registry_events.parquet")


class SimTruth:
    """`TruthEvaluator` on the simulation truth. `evaluate_plan_on_truth(plan, scenarios)` -> profit (UZS) per scenario."""

    def __init__(self, graph: CalculationGraph, onto_graph: onto.ClientGraph, truth_dir: Path = TRUTH_DIR,
                 scratch: Path | None = None, cap_markup: bool = False):
        """`cap_markup=True`: interpretation A - the 15% rule is applied on the truth for every plan evaluated here
        (the plan and the client baseline alike). False: the truth as simulated (rule not applied)."""
        self.graph, self.truth_dir = graph, Path(truth_dir)
        self.mapping = build_mapping(graph, onto_graph, self.truth_dir)
        self.cap_markup = cap_markup
        from lagrange_rx.engine.rules import load_rules
        self.markup_multiplier = 1.0 + load_rules().max_markup
        self.kappa = legal_base_share_of_cost(self.truth_dir, self.mapping.sku.truth_id)
        self.ccy = next(iter(sorted(c for c in graph.nominal_fx if c != "UZS")))
        sales = pd.read_parquet(self.truth_dir / "sales.parquet")
        sales = sales[sales["sku_id"] == self.mapping.sku.truth_id]
        by_m = sales.groupby("month")["qty_packs"].sum()
        self.month_weights = [float(by_m.get(m, 0)) for m in _WINDOW]
        self._tmp = Path(tempfile.mkdtemp(prefix="truth_overlay_", dir=scratch))
        self._dirs: dict[str, Path] = {}
        self.calls = 0

    def close(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def __enter__(self) -> "SimTruth":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def overlay(self, s: Scenario) -> Overlay:
        return build_overlay(s, self.mapping, nominal_fx=self.graph.nominal_fx[self.ccy],
                             demand_p50=self.graph.demand.p50, ccy=self.ccy, month_weights=self.month_weights)

    def _dir(self, ov: Overlay) -> Path:
        if ov.world_is_nominal:
            return self.truth_dir
        k = ov.key()
        if k not in self._dirs:
            d = self._tmp / k
            apply_overlay(self.truth_dir, d, ov, self.mapping.sku.truth_id)
            self._dirs[k] = d
        return self._dirs[k]

    def profit(self, plan: Plan, s: Scenario) -> float:
        ov = self.overlay(s)
        tp = translate_plan(plan, self.mapping, self.graph.horizon_start, self.truth_dir, ov.unavailable)
        self.calls += 1
        res = evaluate_plan_on_truth(tp, self._dir(ov))
        if not self.cap_markup:
            return float(res)
        cut = cap_wholesale_revenue(float(res["revenue_ex_vat_uzs"]), float(res["cogs_ex_vat_uzs"]),
                                    self.kappa, self.markup_multiplier)
        return float(res) - cut["excess_uzs"]

    def evaluate_plan_on_truth(self, plan: Plan, scenarios: Sequence[Scenario]) -> list[float]:
        return [self.profit(plan, s) for s in scenarios]

    def mapping_table(self) -> list[dict]:
        return self.mapping.table()

    def baseline_on_truth(self) -> dict:
        """The truth's own baseline (what the client really did), for reference in the report. Always as simulated
        (rule not applied); with interpretation A the capped figure is in `baseline_on_truth_capped`."""
        return evaluate_plan_on_truth(None, self.truth_dir).to_dict()

    def baseline_on_truth_capped(self) -> dict:
        m = evaluate_plan_on_truth(None, self.truth_dir).to_dict()
        cut = cap_wholesale_revenue(m["revenue_ex_vat_uzs"], m["cogs_ex_vat_uzs"], self.kappa, self.markup_multiplier)
        return {**m, "revenue_ex_vat_uzs": cut["revenue_uzs"], "net_profit_uzs": m["net_profit_uzs"] - cut["excess_uzs"],
                "excess_cut_uzs": cut["excess_uzs"], "legal_base_sold_uzs": cut["legal_base_sold_uzs"],
                "kappa_legal_base_share_of_cost": self.kappa}


def table_json(mapping: TruthMapping) -> str:
    return json.dumps(mapping.table(), ensure_ascii=False, indent=2)
