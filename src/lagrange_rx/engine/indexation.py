"""P20 indexation + P22 revision as pure functions (twin side).

`engine/compliance.py` keeps its own independent copy of this logic.
"""
from __future__ import annotations

import math

from lagrange_rx.engine.rules import Rules

MONTHS = 12
GROWTH_DECIMALS = 9


def indexation_decision(growth: float, on_index_date: bool, rules: Rules) -> str:
    """'none' | 'scheduled' (applied now, on an index date) | 'immediate' (after lag).

    growth > 5%  -> immediate (lagged); 3% <= growth <= 5% -> only on 1 Jan / 1 Jul.
    """
    growth = round(growth, GROWTH_DECIMALS)  # 1.05 * 100 / 100 - 1 > 0.05 in float: compare on the rounded value
    if growth > rules.threshold_immediate:
        return "immediate"
    if growth >= rules.threshold_scheduled and on_index_date:
        return "scheduled"
    return "none"


def _calendar_month(horizon_start: str, offset: int) -> int:
    start = int(horizon_start[5:7])
    return (start - 1 + offset) % 12 + 1


def ceiling_factors(
    ref_fx_start: float,
    ref_fx_path: tuple[float, ...],
    cut_month: int | None,
    cut_depth: float,
    horizon_start: str,
    rules: Rules,
) -> list[float]:
    """Monthly multiplier of the published ceiling over the horizon."""
    if len(ref_fx_path) != MONTHS:
        raise ValueError("ref_fx_path must have 12 monthly values")
    lag_months = math.ceil(rules.lag_days / 30)
    index_months = {m for m, d in rules.index_dates if d == 1}
    base, factor = ref_fx_start, 1.0
    pending: tuple[int, float, float] | None = None  # (effective offset, growth, fx at trigger)
    out: list[float] = []
    for m in range(MONTHS):
        if pending is not None and m >= pending[0]:
            factor *= 1.0 + pending[1]
            base = pending[2]
            pending = None
        fx = ref_fx_path[m]
        growth = fx / base - 1.0
        if pending is None:
            on_date = _calendar_month(horizon_start, m) in index_months
            decision = indexation_decision(growth, on_date, rules)
            if decision == "immediate":
                pending = (m + lag_months, growth, fx)
            elif decision == "scheduled":
                factor *= 1.0 + growth
                base = fx
        out.append(factor)
    if cut_month is not None:
        for m in range(cut_month - 1, MONTHS):
            out[m] *= 1.0 - cut_depth
    return out


def annual_ceiling_factor(
    ref_fx_start: float,
    ref_fx_path: tuple[float, ...],
    cut_month: int | None,
    cut_depth: float,
    horizon_start: str,
    rules: Rules,
) -> float:
    """Average ceiling multiplier over the horizon (uniform sales across months)."""
    f = ceiling_factors(ref_fx_start, ref_fx_path, cut_month, cut_depth, horizon_start, rules)
    return sum(f) / len(f)
