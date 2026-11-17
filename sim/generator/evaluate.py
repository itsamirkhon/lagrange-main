"""Independent economic plan evaluator on ground-truth data for LAGRANGE Rx."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pandas as pd

_DEFAULT_TRUTH_DIR = Path(__file__).resolve().parent.parent / "truth"
_FOCUS_SKU = "SKU_CARDIO_01"
_WINDOW_MONTHS: tuple[str, ...] = (
    "2025-11",
    "2025-12",
    "2026-01",
    "2026-02",
    "2026-03",
    "2026-04",
    "2026-05",
    "2026-06",
    "2026-07",
    "2026-08",
    "2026-09",
    "2026-10",
)


class PlanProfit(float):
    """Float subclass representing net profit in UZS plus full evaluation metrics."""

    def __new__(cls, net_profit_uzs: float, **metrics: Any) -> "PlanProfit":
        instance = super().__new__(cls, float(net_profit_uzs))
        instance._metrics = {
            "net_profit_uzs": float(net_profit_uzs),
            "revenue_ex_vat_uzs": float(metrics.get("revenue_ex_vat_uzs", 0.0)),
            "cogs_ex_vat_uzs": float(metrics.get("cogs_ex_vat_uzs", 0.0)),
            "operating_costs_uzs": float(metrics.get("operating_costs_uzs", 0.0)),
            "net_margin_ex_vat": float(metrics.get("net_margin_ex_vat", 0.0)),
            "annual_sales_packs": int(metrics.get("annual_sales_packs", 0)),
            "stockout_packs": int(metrics.get("stockout_packs", 0)),
            "writeoff_packs": int(metrics.get("writeoff_packs", 0)),
            "baseline_profit_uzs": float(metrics.get("baseline_profit_uzs", net_profit_uzs)),
            "regret_vs_baseline_uzs": float(metrics.get("regret_vs_baseline_uzs", 0.0)),
        }
        return instance

    def __getitem__(self, key: str) -> Any:
        return self._metrics[key]

    def get(self, key: str, default: Any = None) -> Any:
        """Return metric value if present, else default."""
        return self._metrics.get(key, default)

    def to_dict(self) -> dict[str, Any]:
        """Return a shallow copy of all evaluation metrics."""
        return dict(self._metrics)

    def __getattr__(self, name: str) -> Any:
        metrics = object.__getattribute__(self, "_metrics")
        if name in metrics:
            return metrics[name]
        raise AttributeError(f"{type(self).__name__!r} has no attribute {name!r}")


def _resolve_truth_dir(truth_dir: Path | str | None) -> Path:
    return Path(truth_dir) if truth_dir is not None else _DEFAULT_TRUTH_DIR


def _load_truth_context(truth_path: Path) -> dict[str, pd.DataFrame]:
    names = (
        "shipments",
        "lots",
        "routes",
        "suppliers",
        "fx",
        "registry_events",
        "sales",
        "inventory_monthly",
        "writeoffs",
        "costs",
    )
    return {name: pd.read_parquet(truth_path / f"{name}.parquet") for name in names}


def get_baseline_plan(truth_dir: Path | str | None = None) -> dict[str, Any]:
    """Build the 12-month baseline procurement & pricing plan from truth tables."""
    ctx = _load_truth_context(_resolve_truth_dir(truth_dir))
    lots = ctx["lots"]
    shipments = ctx["shipments"]
    costs = ctx["costs"]
    reg = ctx["registry_events"].sort_values("event_date").iloc[-1]

    focus_ship_ids = set(lots.loc[lots["sku_id"] == _FOCUS_SKU, "shipment_id"])
    focus_ships = shipments[
        (shipments["shipment_id"].isin(focus_ship_ids)) & (shipments["month"].isin(_WINDOW_MONTHS))
    ].sort_values("month")

    monthly_orders = [
        {
            "month": str(row["month"]),
            "qty_packs": int(row["total_packs"]),
            "supplier_id": str(row["supplier_id"]),
            "route_id": str(row["route_id"]),
        }
        for _, row in focus_ships.iterrows()
    ]

    mkt_rows = costs[
        (costs["sku_id"] == _FOCUS_SKU)
        & (costs["category"] == "marketing")
        & (costs["month"].isin(_WINDOW_MONTHS))
    ]
    monthly_marketing = {str(r["month"]): float(r["amount_uzs"]) for _, r in mkt_rows.iterrows()}

    return {
        "sku_id": _FOCUS_SKU,
        "selling_price_ex_vat_uzs": float(reg["wholesale_ex_vat_uzs"]),
        "monthly_orders": monthly_orders,
        "monthly_marketing_uzs": monthly_marketing,
    }


def _cost_lookup(costs: pd.DataFrame, category: str) -> dict[str, float]:
    sub = costs[
        (costs["sku_id"] == _FOCUS_SKU)
        & (costs["category"] == category)
        & (costs["month"].isin(_WINDOW_MONTHS))
    ]
    return {str(r["month"]): float(r["amount_uzs"]) for _, r in sub.iterrows()}


def _route_freight_uzs(route_row: pd.Series, qty: int, fx_usd: float) -> float:
    if qty <= 0:
        return 0.0
    usd_cost = float(route_row["freight_per_shipment_usd"]) + qty * float(route_row["freight_per_pack_usd"])
    return usd_cost * fx_usd


def _fx_for_month(fx_df: pd.DataFrame, month_str: str) -> float:
    usd = fx_df[fx_df["currency"] == "USD"]
    sub = usd[usd["date"] <= f"{month_str}-28"]
    row = sub.iloc[-1] if not sub.empty else usd.iloc[0]
    return float(row["rate_uzs"])


def _build_sim_params(
    plan: dict[str, Any],
    base_plan: dict[str, Any],
    ctx: dict[str, pd.DataFrame],
) -> dict[str, Any]:
    reg_latest = ctx["registry_events"].sort_values("event_date").iloc[-1]
    max_price = float(reg_latest["wholesale_ex_vat_uzs"])
    plan_price = float(plan.get("selling_price_ex_vat_uzs", max_price))
    inv_focus = ctx["inventory_monthly"][
        (ctx["inventory_monthly"]["sku_id"] == _FOCUS_SKU)
        & (ctx["inventory_monthly"]["month"].isin(_WINDOW_MONTHS))
    ].set_index("month")
    sales_focus = ctx["sales"][
        (ctx["sales"]["sku_id"] == _FOCUS_SKU) & (ctx["sales"]["month"].isin(_WINDOW_MONTHS))
    ]
    wo_focus = ctx["writeoffs"][
        (ctx["writeoffs"]["sku_id"] == _FOCUS_SKU) & (ctx["writeoffs"]["month"].isin(_WINDOW_MONTHS))
    ]
    return {
        "routes_map": ctx["routes"].set_index("route_id"),
        "suppliers_map": ctx["suppliers"].set_index("supplier_id"),
        "fx_df": ctx["fx"],
        "base_unit_cost": float(reg_latest["calc_base_ex_vat_uzs"]),
        "eff_price": min(plan_price, max_price),
        "cap_breach": max(0.0, plan_price - max_price),
        "inv_focus": inv_focus,
        "demand_by_m": {str(m): int(q) for m, q in sales_focus.groupby("month")["qty_packs"].sum().items()},
        "wo_by_m": {str(m): int(q) for m, q in wo_focus.groupby("month")["qty_packs"].sum().items()},
        "dist_map": _cost_lookup(ctx["costs"], "distribution"),
        "mkt_base_map": _cost_lookup(ctx["costs"], "marketing"),
        "wh_map": _cost_lookup(ctx["costs"], "warehouse"),
        "fin_map": _cost_lookup(ctx["costs"], "financing"),
        "gen_map": _cost_lookup(ctx["costs"], "general"),
        "base_orders": {o["month"]: o for o in base_plan["monthly_orders"]},
        "plan_orders": {o["month"]: o for o in plan.get("monthly_orders", [])},
        "plan_mkt": plan.get("monthly_marketing_uzs", base_plan["monthly_marketing_uzs"]),
    }


def _step_month(m_str: str, opening: int, params: dict[str, Any]) -> dict[str, Any]:
    demand = params["demand_by_m"][m_str]
    b_ord = params["base_orders"][m_str]
    p_ord = params["plan_orders"].get(m_str, b_ord)
    sup_cap = int(params["suppliers_map"].loc[p_ord.get("supplier_id", b_ord["supplier_id"]), "monthly_capacity"])
    received = min(max(0, int(p_ord.get("qty_packs", 0))), sup_cap)

    available = opening + received
    sold = min(available, demand)
    rem = available - sold
    base_close = int(params["inv_focus"].loc[m_str, "closing_stock"])
    excess = max(0, rem - int(demand * 1.5))
    month_wo = min(rem, int(params["wo_by_m"].get(m_str, 0)) + int(round(excess * 0.25)))
    closing = rem - month_wo

    fx_usd = _fx_for_month(params["fx_df"], m_str)
    p_route = params["routes_map"].loc[p_ord.get("route_id", b_ord["route_id"])]
    b_route = params["routes_map"].loc[b_ord["route_id"]]
    freight_delta = _route_freight_uzs(p_route, received, fx_usd) - _route_freight_uzs(b_route, int(b_ord["qty_packs"]), fx_usd)

    avg_stock = (opening + closing) / 2.0
    base_avg = (float(params["inv_focus"].loc[m_str, "opening_stock"]) + base_close) / 2.0
    unit_cost = params["base_unit_cost"]
    cap_breach = params["cap_breach"]
    penalty = (sold * cap_breach * 1.5 + 2_000_000.0) if cap_breach > 0 else 0.0

    op_cost = (
        sold * (params["dist_map"][m_str] / max(1, demand))
        + float(params["plan_mkt"].get(m_str, params["mkt_base_map"][m_str]))
        + avg_stock * (params["wh_map"][m_str] / max(1.0, base_avg))
        + params["fin_map"][m_str]
        + max(0.0, avg_stock - base_avg) * unit_cost * (0.22 / 12.0)
        + month_wo * unit_cost
        + params["gen_map"][m_str]
        + penalty
    )
    return {
        "closing": closing,
        "rev": sold * params["eff_price"],
        "cogs": sold * unit_cost + max(0.0, freight_delta),
        "op_cost": op_cost,
        "sold": sold,
        "stockout": demand - sold,
        "wo": month_wo,
    }


def _simulate_plan_metrics(
    plan: dict[str, Any],
    base_plan: dict[str, Any],
    ctx: dict[str, pd.DataFrame],
) -> dict[str, Any]:
    """Run deterministic 12-month economic simulation of a plan on truth economics."""
    params = _build_sim_params(plan, base_plan, ctx)
    opening = int(params["inv_focus"].loc[_WINDOW_MONTHS[0], "opening_stock"])
    tot_rev = tot_cogs = tot_op = 0.0
    tot_sold = tot_stockout = tot_wo = 0

    for m_str in _WINDOW_MONTHS:
        step = _step_month(m_str, opening, params)
        tot_rev += step["rev"]
        tot_cogs += step["cogs"]
        tot_op += step["op_cost"]
        tot_sold += step["sold"]
        tot_stockout += step["stockout"]
        tot_wo += step["wo"]
        opening = step["closing"]

    net_profit = round(tot_rev - tot_cogs - tot_op, 2)
    margin = (net_profit / tot_rev) if tot_rev > 0 else 0.0
    return {
        "net_profit_uzs": net_profit,
        "revenue_ex_vat_uzs": round(tot_rev, 2),
        "cogs_ex_vat_uzs": round(tot_cogs, 2),
        "operating_costs_uzs": round(tot_op, 2),
        "net_margin_ex_vat": round(margin, 6),
        "annual_sales_packs": int(tot_sold),
        "stockout_packs": int(tot_stockout),
        "writeoff_packs": int(tot_wo),
    }


def evaluate_plan_on_truth(
    plan: dict[str, Any] | None = None,
    truth_dir: Path | str | None = None,
) -> PlanProfit:
    """Evaluate a 12-month procurement/pricing plan against ground-truth economics."""
    truth_path = _resolve_truth_dir(truth_dir)
    ctx = _load_truth_context(truth_path)
    base_plan = get_baseline_plan(truth_path)
    base_metrics = _simulate_plan_metrics(base_plan, base_plan, ctx)
    target_plan = copy.deepcopy(base_plan) if plan is None else plan
    metrics = base_metrics if plan is None else _simulate_plan_metrics(target_plan, base_plan, ctx)

    base_profit = float(base_metrics["net_profit_uzs"])
    regret = round(base_profit - float(metrics["net_profit_uzs"]), 2)
    return PlanProfit(
        **metrics,
        baseline_profit_uzs=base_profit,
        regret_vs_baseline_uzs=regret,
    )
