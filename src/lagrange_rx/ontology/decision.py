"""Domain models for decision."""
from __future__ import annotations

from decimal import Decimal
from typing import Literal
from pydantic import Field
from .trust import Entity

class Scenario(Entity):
    scenario_id: str
    probability: float = Field(ge=0, le=1)
    fx_shock: dict[str, Decimal] = Field(default_factory=dict)
    freight_multiplier: Decimal = Field(ge=0)
    supplier_disrupted: list[str] = Field(default_factory=list)
    demand_multiplier: Decimal = Field(ge=0)
    cap_rule: Literal['index_p20','fixed','official_revision'] = 'index_p20'
    index_lag_days: int = Field(default=0, ge=0)
    official_revision_pct: Decimal = Decimal(0)

class PlanLine(Entity):
    route: str
    packs: int = Field(ge=0)
    shipments: int = Field(ge=0)
    period: str | None = None

class Plan(Entity):
    plan_id: str
    lines: list[PlanLine]
    wholesale_price_ex_vat: Decimal
    objective: Decimal
    risk_mode: Literal['expected','cvar','worst_case']
    risk_lambda: Decimal = Field(alias='lambda')
    alpha: Decimal | None = None
    solver_status: str
    gap: Decimal | None = None
    snapshot: str

class Constraint(Entity):
    name: str
    type: Literal['legal','operational','financial']
    rule: str | None = None
    is_binding: bool
    slack: Decimal
    scenario_id: str

class CheckResult(Entity):
    rule: str
    passed: bool
    observed: Decimal
    limit: Decimal
    message: str
