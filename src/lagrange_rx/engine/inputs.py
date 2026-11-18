"""Engine-internal calculation inputs (flat, frozen, numeric).

NOT a second ontology. The single world model is `lagrange_rx.ontology.ClientGraph`; these types are the
engine's numeric view of it, produced only by `lagrange_rx.engine.adapter.to_calculation_graph`
(ontology graph -> `ontology.ClientGraph.for_calculation()` -> these specs). Names deliberately differ from the
ontology's (`*Spec`, `EngineGraph`). Tests may build them directly to exercise the engine in isolation.
Every spec carries `confirmed`; only `EngineGraph.for_calculation()` output (a `CalculationGraph`) is accepted by
the engine.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _M(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _is_month(s: str) -> bool:
    return len(s) == 7 and s[4] == "-" and s[:4].isdigit() and s[5:].isdigit() and 1 <= int(s[5:]) <= 12


class ProductSpec(_M):
    id: str
    name: str
    reference_currency: str = "USD"
    chun_uzs: float = Field(gt=0, description="published max wholesale price per pack, incl. VAT")
    chchn_uzs: float = Field(gt=0, description="published max retail price per pack, incl. VAT")
    contract_price_uzs: float | None = Field(default=None, gt=0, description="contract sale price per pack, excl. VAT")
    chun_formula_uzs: float | None = Field(default=None, gt=0, description="ChUN by the p.19 formula (RegistryRecord), incl. VAT")
    rn_foreign: float | None = Field(default=None, gt=0, description="registered reference price, reference currency")
    confirmed: bool = True


class SupplierSpec(_M):
    id: str
    name: str
    reliability: float = Field(ge=0.0, le=1.0)
    confirmed: bool = True


class RouteSpec(_M):
    id: str
    supplier_id: str
    name: str
    mode: Literal["sea", "rail", "air", "road", "multimodal"]
    currency: str
    kc: float = Field(gt=0, description="contract price per pack, foreign currency")
    duty_rate: float = Field(ge=0.0, lt=1.0)
    fees_uzs: float = Field(ge=0.0, description="customs fees per pack")
    pr_uzs: float | None = Field(default=None, ge=0.0, description="Pr per pack, only counted if pr_confirmed")
    pr_confirmed: bool = False
    freight_uzs: float = Field(ge=0.0, description="freight per pack before scenario multiplier")
    lot_size: int = Field(gt=0, description="packs per lot")
    min_batch: int = Field(ge=0, description="min packs per shipment")
    max_batch: int = Field(gt=0, description="max packs per shipment")
    capacity_packs: int = Field(ge=0, description="annual capacity, packs")
    max_shipments: int = Field(ge=0)
    fixed_per_shipment_uzs: float = Field(ge=0.0, default=0.0)
    fixed_activation_uzs: float = Field(ge=0.0, default=0.0)
    lead_time_days: int = Field(ge=0, default=30)
    supplier_terms_days: int = Field(ge=0, default=0)
    is_eligible: bool = True
    emergency: bool = False
    emergency_premium_uzs: float = Field(ge=0.0, default=0.0)
    emergency_cap_packs: int = Field(ge=0, default=0)
    confirmed: bool = True

    @model_validator(mode="after")
    def _check(self) -> "Route":
        if self.min_batch > self.max_batch:
            raise ValueError("min_batch > max_batch")
        return self


class CostSpec(_M):
    id: str
    name: str
    layer: Literal["acquisition", "below_line"]
    route_id: str | None = None
    per_pack_uzs: float = Field(ge=0.0, default=0.0)
    annual_uzs: float = Field(ge=0.0, default=0.0)
    allocation: Literal["direct", "shared_by_volume", "shared_by_revenue", "unallocated"] = "direct"
    share: float | None = Field(default=None, gt=0.0, le=1.0, description="fraction attributed to this product")
    confirmed: bool = True

    @model_validator(mode="after")
    def _check(self) -> "CostItem":
        if self.layer == "acquisition":
            if not self.route_id:
                raise ValueError("acquisition cost item needs route_id")
            if self.annual_uzs:
                raise ValueError("acquisition cost items are per pack only")
        return self


class DemandSpec(_M):
    p10: float = Field(ge=0.0)
    p50: float = Field(ge=0.0)
    p90: float = Field(ge=0.0)

    @model_validator(mode="after")
    def _check(self) -> "DemandForecast":
        if not (self.p10 <= self.p50 <= self.p90):
            raise ValueError("demand quantiles must be monotone")
        return self


class FinanceSpec(_M):
    credit_rate_annual: float = Field(ge=0.0, default=0.20)
    budget_uzs: float | None = Field(default=None, ge=0.0, description="working-capital credit line")
    inventory_days: int = Field(ge=0, default=45)
    receivable_days: int = Field(ge=0, default=30)
    hedge_forward_premium: float = Field(default=0.02, description="forward rate premium over nominal (assumption)")


class ShipmentSpec(_M):
    id: str
    route_id: str
    month: str
    packs: int = Field(gt=0)
    fx_uzs: float = Field(gt=0, description="actual customs rate UZS per 1 unit of route currency")
    confirmed: bool = True

    @model_validator(mode="after")
    def _check(self) -> "Shipment":
        if not _is_month(self.month):
            raise ValueError("month must be YYYY-MM")
        return self


class SaleSpec(_M):
    month: str
    packs: int = Field(gt=0)
    price_uzs: float = Field(gt=0, description="price per pack excl. VAT")
    confirmed: bool = True

    @model_validator(mode="after")
    def _check(self) -> "Sale":
        if not _is_month(self.month):
            raise ValueError("month must be YYYY-MM")
        return self


class ActualSpec(_M):
    revenue_uzs: float = Field(gt=0)
    total_cost_uzs: float = Field(ge=0)
    confirmed: bool = True

    @property
    def margin(self) -> float:
        return (self.revenue_uzs - self.total_cost_uzs) / self.revenue_uzs


class EngineGraph(_M):
    client_id: str
    product: ProductSpec
    suppliers: tuple[SupplierSpec, ...]
    routes: tuple[RouteSpec, ...]
    cost_items: tuple[CostSpec, ...] = ()
    demand: DemandSpec
    shipments: tuple[ShipmentSpec, ...] = ()
    sales: tuple[SaleSpec, ...] = ()
    actual: ActualSpec | None = None
    finance: FinanceSpec = FinanceSpec()
    nominal_fx: dict[str, float]
    horizon_start: str = Field(description="first month of the 12-month plan horizon, YYYY-MM")
    penalty_uzs_per_pack: float = Field(ge=0.0, default=0.0, description="service penalty per unmet pack")
    demo_mode: bool = False
    fixture_note: str | None = None
    notes: tuple[str, ...] = Field(default=(), description="adapter assumptions and exclusions, surfaced as warnings")

    @model_validator(mode="after")
    def _check(self) -> "ClientGraph":
        if not _is_month(self.horizon_start):
            raise ValueError("horizon_start must be YYYY-MM")
        sup = {s.id for s in self.suppliers}
        ids = [r.id for r in self.routes]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate route ids")
        for r in self.routes:
            if r.supplier_id not in sup:
                raise ValueError(f"route {r.id}: unknown supplier {r.supplier_id}")
            if r.currency not in self.nominal_fx:
                raise ValueError(f"route {r.id}: no nominal_fx for {r.currency}")
        if self.product.reference_currency not in self.nominal_fx:
            raise ValueError("no nominal_fx for reference currency")
        rid = set(ids)
        for it in self.cost_items:
            if it.route_id is not None and it.route_id not in rid:
                raise ValueError(f"cost item {it.id}: unknown route {it.route_id}")
        for sh in self.shipments:
            if sh.route_id not in rid:
                raise ValueError(f"shipment {sh.id}: unknown route {sh.route_id}")
        return self

    def route(self, route_id: str) -> RouteSpec:
        for r in self.routes:
            if r.id == route_id:
                return r
        raise KeyError(route_id)

    def for_calculation(self) -> "CalculationGraph":
        """Only confirmed data. Unconfirmed Pr is dropped; unconfirmed product is an error."""
        if not self.product.confirmed:
            raise ValueError("product is not confirmed: nothing to calculate")
        sup = tuple(s for s in self.suppliers if s.confirmed)
        sup_ids = {s.id for s in sup}
        routes = tuple(
            r.model_copy(update={} if r.pr_confirmed else {"pr_uzs": None})
            for r in self.routes
            if r.confirmed and r.supplier_id in sup_ids
        )
        rids = {r.id for r in routes}
        items = tuple(
            i for i in self.cost_items if i.confirmed and (i.route_id is None or i.route_id in rids)
        )
        ships = tuple(s for s in self.shipments if s.confirmed and s.route_id in rids)
        sales = tuple(s for s in self.sales if s.confirmed)
        actual = self.actual if self.actual is not None and self.actual.confirmed else None
        data = self.model_dump()
        data.update(
            suppliers=sup,
            routes=routes,
            cost_items=items,
            shipments=ships,
            sales=sales,
            actual=actual,
        )
        return CalculationGraph.model_validate(
            {**data, "suppliers": [s.model_dump() for s in sup], "routes": [r.model_dump() for r in routes],
             "cost_items": [i.model_dump() for i in items], "shipments": [s.model_dump() for s in ships],
             "sales": [s.model_dump() for s in sales], "actual": actual.model_dump() if actual else None}
        )


class CalculationGraph(EngineGraph):
    """Marker type: produced only by `EngineGraph.for_calculation()`; the engine requires it."""

    def eligible_routes(self) -> tuple[RouteSpec, ...]:
        return tuple(r for r in self.routes if r.is_eligible and not r.emergency)

    def emergency_routes(self) -> tuple[RouteSpec, ...]:
        return tuple(r for r in self.routes if r.is_eligible and r.emergency)


def require_calculation_graph(graph: object) -> CalculationGraph:
    if not isinstance(graph, CalculationGraph):
        raise TypeError("engine accepts only graph.for_calculation() (confirmed data)")
    return graph
