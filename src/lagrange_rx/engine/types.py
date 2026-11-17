"""Shared data contracts of the engine (scenario, plan, outcome, check result)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _M(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class Scenario(_M):
    """One realisation of the world over the 12-month horizon. Raw drivers only:
    ceilings are *derived* from them (twin and compliance each do it independently)."""

    id: str
    name: str
    kind: Literal["sample", "stress", "mean"] = "sample"
    weight: float = Field(gt=0.0, default=1.0)
    fx: dict[str, float]  # average UZS per 1 unit of currency over the horizon (costs, customs)
    ref_fx_start: float = Field(gt=0.0)  # reference currency rate at horizon start
    ref_fx_path: tuple[float, ...]  # 12 monthly rates of the reference currency (Rn growth in sums)
    cut_month: int | None = Field(default=None, ge=1, le=12)  # 1-based month from which a P22 cut applies
    cut_depth: float = Field(default=0.0, ge=0.0, lt=1.0)
    freight_mult: dict[str, float] = {}  # by transport mode (sea/rail/air/road); missing = 1.0
    route_available: dict[str, bool] = {}  # missing key = available
    demand: float = Field(ge=0.0)  # packs over the horizon

    def freight(self, mode: str) -> float:
        return self.freight_mult.get(mode, 1.0)

    def available(self, route_id: str) -> bool:
        return self.route_available.get(route_id, True)


class PlanLine(_M):
    route_id: str
    packs: int = Field(ge=0)
    shipments: int = Field(ge=0)
    months: tuple[str, ...] = ()


class Plan(_M):
    plan_id: str
    sku_id: str
    horizon: str
    lines: tuple[PlanLine, ...]
    fx_hedge_share: float = Field(ge=0.0, le=1.0, default=0.0)
    wholesale_price_policy: Literal["max_legal_common_price"] = "max_legal_common_price"
    risk_mode: Literal["expected", "cvar", "worst_case", "baseline"] = "cvar"
    alpha: float | None = 0.95
    lambda_: float | None = Field(default=0.5, alias="lambda")
    snapshot_id: str = ""

    def packs_by_route(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for ln in self.lines:
            out[ln.route_id] = out.get(ln.route_id, 0) + ln.packs
        return out


class ScenarioOutcome(_M):
    """Operational result of a plan in one scenario (second-stage recourse included)."""

    scenario_id: str
    sold: float  # packs sold
    shortfall: float  # packs of unmet demand
    emergency: dict[str, float] = {}  # emergency packs bought, by emergency route id
    price_uzs: float  # single common wholesale price per pack excl. VAT (0 if nothing sold)
    revenue_uzs: float
    profit_uzs: float
    binding: str = "none"  # registry | retail | contract | markup15 | none


class CheckResult(_M):
    rule_id: str
    scenario_id: str
    observed: float
    limit: float
    passed: bool
    detail: str = ""
