"""Calibration report builder and strict invariant validator for LAGRANGE Rx."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


class CalibrationError(ValueError):
    """Raised when generated truth violates Section 3.3 calibration invariants."""


def _window_pnl(
    sales: pd.DataFrame,
    costs: pd.DataFrame,
    sku_id: str,
    start_month: str,
    end_month: str,
) -> dict[str, float]:
    s_mask = (
        (sales["sku_id"] == sku_id)
        & (sales["month"] >= start_month)
        & (sales["month"] <= end_month)
    )
    c_mask = (
        (costs["sku_id"] == sku_id)
        & (costs["month"] >= start_month)
        & (costs["month"] <= end_month)
    )
    sub_s = sales.loc[s_mask]
    sub_c = costs.loc[c_mask]

    qty = int(sub_s["qty_packs"].sum())
    rev = float(sub_s["revenue_ex_vat_uzs"].sum())
    cogs = float(sub_s["cogs_ex_vat_uzs"].sum())
    op_cost = float(sub_c["amount_uzs"].sum())
    net_profit = rev - cogs - op_cost
    margin = (net_profit / rev) if rev > 0 else 0.0
    return {
        "sales_packs": qty,
        "revenue_ex_vat_uzs": round(rev, 2),
        "cogs_ex_vat_uzs": round(cogs, 2),
        "operating_costs_uzs": round(op_cost, 2),
        "net_profit_uzs": round(net_profit, 2),
        "net_margin_ex_vat": round(margin, 6),
    }


def _check_bounds(value: float, low: float, high: float, label: str) -> None:
    if not (low <= value <= high):
        raise CalibrationError(f"{label}={value} outside [{low}, {high}]")


def _check_approx(value: float, target: float, tol: float, label: str) -> None:
    if abs(value - target) > tol:
        raise CalibrationError(f"{label}={value} differs from target {target} by > {tol}")


def build_calibration_report(
    truth_tables: dict[str, pd.DataFrame],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Compute Section 3.3 calibration metrics from generated truth tables."""
    period = config["period"]
    focus_id = config["focus_sku"]["sku_id"]
    sales = truth_tables["sales"]
    costs = truth_tables["costs"]
    reg = truth_tables["registry_events"]
    lots = truth_tables["lots"]
    customs = truth_tables["customs"]

    post = _window_pnl(
        sales,
        costs,
        focus_id,
        period["calibration_start_month"],
        period["end_month"],
    )
    pre = _window_pnl(
        sales,
        costs,
        focus_id,
        period["start_month"],
        period["pre_reform_end_month"],
    )

    latest_reg = reg.sort_values("event_date").iloc[-1]
    focus_ship_ids = set(lots.loc[lots["sku_id"] == focus_id, "shipment_id"])
    focus_customs = customs.loc[customs["shipment_id"].isin(focus_ship_ids)]
    mean_landed_cost = float(focus_customs["landed_unit_cost_uzs"].mean())

    return {
        "sku_id": focus_id,
        "calibration_window": f"{period['calibration_start_month']}..{period['end_month']}",
        "pre_reform_window": f"{period['start_month']}..{period['pre_reform_end_month']}",
        "annual_sales_packs": int(post["sales_packs"]),
        "chchn_with_vat_uzs": float(latest_reg["chchn_with_vat_uzs"]),
        "chun_with_vat_uzs": float(latest_reg["chun_with_vat_uzs"]),
        "wholesale_ex_vat_uzs": float(latest_reg["wholesale_ex_vat_uzs"]),
        "calc_base_ex_vat_uzs": float(latest_reg["calc_base_ex_vat_uzs"]),
        "mean_landed_unit_cost_uzs": round(mean_landed_cost, 2),
        "revenue_ex_vat_uzs": post["revenue_ex_vat_uzs"],
        "cogs_ex_vat_uzs": post["cogs_ex_vat_uzs"],
        "operating_costs_uzs": post["operating_costs_uzs"],
        "net_profit_uzs": post["net_profit_uzs"],
        "net_margin_ex_vat": post["net_margin_ex_vat"],
        "pre_reform_net_margin_ex_vat": pre["net_margin_ex_vat"],
        "pre_reform_revenue_ex_vat_uzs": pre["revenue_ex_vat_uzs"],
        "pre_reform_net_profit_uzs": pre["net_profit_uzs"],
    }


def validate_calibration(report: dict[str, Any], config: dict[str, Any]) -> None:
    """Validate calibration report against Section 3.3 targets; raise CalibrationError on breach."""
    t = config["calibration_targets"]
    vol_target = float(t["annual_sales_packs_target"])
    vol_tol = float(t["annual_sales_tolerance_pct"])
    _check_bounds(
        float(report["annual_sales_packs"]),
        vol_target * (1.0 - vol_tol),
        vol_target * (1.0 + vol_tol),
        "annual_sales_packs",
    )
    _check_approx(float(report["chchn_with_vat_uzs"]), float(t["chchn_with_vat_uzs"]), 0.01, "chchn_with_vat_uzs")
    _check_approx(float(report["chun_with_vat_uzs"]), float(t["chun_with_vat_uzs"]), 0.01, "chun_with_vat_uzs")
    _check_approx(float(report["wholesale_ex_vat_uzs"]), float(t["wholesale_ex_vat_uzs"]), 1.0, "wholesale_ex_vat_uzs")
    _check_approx(float(report["calc_base_ex_vat_uzs"]), float(t["calc_base_ex_vat_uzs"]), 10.0, "calc_base_ex_vat_uzs")
    _check_bounds(
        float(report["net_margin_ex_vat"]),
        float(t["net_margin_ex_vat_min"]),
        float(t["net_margin_ex_vat_max"]),
        "net_margin_ex_vat",
    )
    _check_bounds(
        float(report["pre_reform_net_margin_ex_vat"]),
        float(t["pre_reform_margin_min"]),
        float(t["pre_reform_margin_max"]),
        "pre_reform_net_margin_ex_vat",
    )


def save_calibration_report(report: dict[str, Any], path: Path | str) -> Path:
    """Persist calibration report deterministically as formatted JSON."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    out.write_text(text, encoding="utf-8")
    return out
