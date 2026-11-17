"""Typed public request and response contract. Money inputs in UZS; risk in [0, 1]."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SupplyOption(StrictModel):
    id: str = Field(min_length=1, max_length=48, pattern=r"^[a-z][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="Synthetic authorized route for the same SKU", max_length=300)
    eligible: bool = True
    factory_cost_uzs: int = Field(ge=1, le=1_000_000_000)
    import_fees_uzs: int = Field(ge=0, le=1_000_000_000)
    freight_uzs: int = Field(ge=0, le=1_000_000_000)
    below_line_cost_uzs: int = Field(ge=0, le=1_000_000_000)
    fx_exposed_fraction: float = Field(default=0.9, ge=0, le=1)
    capacity_packs: int = Field(ge=0, le=2_000_000)
    min_shipment_packs: int = Field(gt=0, le=2_000_000)
    max_shipment_packs: int = Field(gt=0, le=2_000_000)
    max_shipments: int = Field(gt=0, le=1_000)
    shipment_fixed_uzs: int = Field(ge=0, le=1_000_000_000)
    activation_fixed_uzs: int = Field(ge=0, le=1_000_000_000)

    @model_validator(mode="after")
    def check_size(self):
        if self.min_shipment_packs > self.max_shipment_packs:
            raise ValueError("min_shipment_packs must not exceed max_shipment_packs")
        return self


class Scenario(StrictModel):
    id: str = Field(min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=100)
    probability: float = Field(ge=0, le=1)
    fx_change_pct: float = Field(default=0, ge=-50, le=100)
    freight_change_pct: float = Field(default=0, ge=-50, le=200)
    wholesale_ceiling_change_pct: float = Field(default=0, ge=-50, le=50)


class PharmaCase(StrictModel):
    id: str = Field(default="synthetic_case_16", min_length=1, max_length=60)
    title: str = Field(default="Imported cardiovascular medicine -- illustrative", max_length=150)
    data_classification: Literal["synthetic"] = "synthetic"
    currency: Literal["UZS"] = "UZS"
    annual_demand_packs: int = Field(gt=0, le=2_000_000)
    lot_size_packs: int = Field(gt=0, le=10_000)
    brief_retail_ceiling_uzs: int = Field(gt=0, le=1_000_000_000)
    assumed_registered_wholesale_cap_uzs: int = Field(gt=0, le=1_000_000_000)
    assumed_max_contract_wholesale_uzs: int = Field(gt=0, le=1_000_000_000)
    illustrative_markup_limit: float = Field(default=0.15, ge=0, le=0.15)
    working_capital_budget_uzs: int = Field(gt=0, le=1_000_000_000_000)
    options: list[SupplyOption] = Field(min_length=1, max_length=24)
    scenarios: list[Scenario] = Field(min_length=1, max_length=16)
    baseline_option_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_case(self):
        lot = self.lot_size_packs
        if self.annual_demand_packs % lot:
            raise ValueError("annual_demand_packs must be divisible by lot_size_packs")
        ids = [o.id for o in self.options]
        if len(ids) != len(set(ids)):
            raise ValueError("option ids must be unique")
        if self.baseline_option_id not in ids:
            raise ValueError("baseline_option_id must match an option")
        if any(o.capacity_packs % lot or o.min_shipment_packs % lot or o.max_shipment_packs % lot for o in self.options):
            raise ValueError("all option pack capacities and shipment sizes must be multiples of lot_size_packs")
        scenario_ids = [s.id for s in self.scenarios]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("scenario ids must be unique")
        if abs(sum(s.probability for s in self.scenarios) - 1) > 1e-6:
            raise ValueError("scenario probabilities must sum to 1")
        return self


class OptimizeRequest(StrictModel):
    risk_aversion: float = Field(default=0.35, ge=0, le=1)
    case: PharmaCase | None = None


class Allocation(StrictModel):
    option_id: str
    option_name: str
    packs: int
    shipments: int
    activation_cost_uzs: int
    shipment_cost_uzs: int


class ScenarioResult(StrictModel):
    scenario_id: str
    name: str
    probability: float
    wholesale_price_uzs: float
    wholesale_cap_uzs: float
    revenue_uzs: float
    acquisition_cost_uzs: float
    below_line_cost_uzs: float
    fixed_cost_uzs: float
    profit_uzs: float
    margin_pct: float
    working_capital_used_uzs: float
    under_budget: bool
    markup_compliant: bool
    registered_ceiling_compliant: bool


class Solution(StrictModel):
    wholesale_price_uzs: float
    allocations: list[Allocation]
    expected_profit_uzs: float
    worst_case_profit_uzs: float
    expected_margin_pct: float
    worst_case_margin_pct: float
    scenario_results: list[ScenarioResult]
    objective_uzs: float
    worst_case_cash_used_uzs: float


class OptimizeResponse(StrictModel):
    case_id: str
    status: Literal["optimal", "feasible", "infeasible"]
    risk_aversion: float
    model: str = "single-stage robust MILP with fixed common wholesale price"
    data_classification: Literal["synthetic"] = "synthetic"
    solver: str = "scipy.optimize.milp / HiGHS"
    solver_message: str
    relative_optimality_gap: float | None = None
    diagnostics: list[str] = Field(default_factory=list)
    baseline: Solution | None = None
    optimized: Solution | None = None
    expected_profit_delta_uzs: float | None = None
    assumptions: list[str]
