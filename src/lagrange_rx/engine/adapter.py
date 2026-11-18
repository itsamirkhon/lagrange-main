"""Thin adapter: `ontology.ClientGraph` -> engine-internal `CalculationGraph` (stage 3, docs/MONOREPO.md).

The ontology graph is the only world model. This module is the single place where its entities are mapped onto
the engine's flat numeric specs (`engine/inputs.py`). Field map (ontology -> engine):

  RegistryRecord.chun/chchn_incl_vat, rn_fx, rn_currency -> ProductSpec.chun_uzs/chchn_uzs/rn_foreign/reference_currency
  Product(sku_id, trade_name)                            -> ProductSpec.id/name
  Supplier(supplier_id, name, reliability)               -> SupplierSpec;  Supplier.payment_terms.supplier_days -> RouteSpec.supplier_terms_days
  Route (+ProductionSite.supplier, +Carrier)             -> RouteSpec (freight, batch limits, capacity, lead time p50, activation)
  Contract / Quote (price, currency)                     -> RouteSpec.kc / currency   (contract valid at horizon, else quote)
  CustomsEntry (duty, fees, cip, fx)                     -> RouteSpec.duty_rate / fees_uzs; Shipment fx_uzs  (legal base per CustomsEntry.legal_base_c_per_pack)
  CostItem (layer, driver, allocation, period)           -> CostSpec; `unallocated` blocks calculation
  DemandForecast (p10/p50/p90 per month)                 -> DemandSpec (sum over the 12 horizon months)
  SalesRecord                                            -> SaleSpec;  Shipment -> ShipmentSpec
  CreditLine / FxRate                                    -> FinanceSpec.budget/rate;  nominal_fx

Parameters the ontology does not carry (lot size, emergency routes and premium, penalty, inventory days, calibration
target, ...) live in `EngineConfig` — an explicit, hashed, engine-only object; every default that substitutes for
missing data is recorded in `CalculationGraph.notes` and surfaced as a warning. Missing required data raises
`AdapterError` naming the field; unconfirmed data is rejected by `ClientGraph.for_calculation()` first.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from lagrange_rx import ontology as onto
from lagrange_rx.engine.inputs import (
    ActualSpec, CalculationGraph, CostSpec, DemandSpec, EngineGraph, FinanceSpec, ProductSpec, RouteSpec,
    SaleSpec, ShipmentSpec, SupplierSpec,
)
from lagrange_rx.engine.rules import Rules, load_rules


class AdapterError(ValueError):
    """The ontology graph lacks something the engine needs, or contains something it must not compute on."""


class UnallocatedCostError(AdapterError):
    """A CostItem with allocation=unallocated: it is never silently assigned to a SKU."""


class _C(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RouteParams(_C):
    """Engine-only parameters of one route (route_id key in `EngineConfig.route_params`). Each overrides / fills
    what the ontology graph does not carry."""

    lot_size: int | None = Field(default=None, gt=0)
    mode: Literal["sea", "rail", "air", "road", "multimodal"] | None = None  # ontology has no 'sea'
    kc: float | None = Field(default=None, gt=0)
    currency: str | None = None
    duty_rate: float | None = Field(default=None, ge=0.0, lt=1.0)  # only when no CustomsEntry exists for the route
    fees_per_pack_uzs: float | None = Field(default=None, ge=0.0)
    acquisition_per_pack_uzs: float | None = Field(default=None, ge=0.0)  # only when no shipment-bound acquisition cost
    pr_uzs: float | None = Field(default=None, ge=0.0)
    pr_confirmed: bool = False
    emergency: bool = False
    emergency_premium_uzs: float = Field(default=0.0, ge=0.0)
    emergency_cap_packs: int = Field(default=0, ge=0)
    max_shipments: int | None = Field(default=None, gt=0)  # only when Route.max_shipments_per_year is not stated


class EngineConfig(_C):
    horizon_start: str = Field(description="first month of the 12-month plan horizon, YYYY-MM")
    client_id: str | None = None  # label of the calculation graph; default = sku id
    sku_id: str | None = None  # required when the graph holds several products
    penalty_uzs_per_pack: float = Field(default=0.0, ge=0.0)
    default_lot_size: int = Field(default=1000, gt=0)
    inventory_days: int = Field(default=45, ge=0)
    default_credit_rate: float = Field(default=0.20, ge=0.0)  # used only when the graph has no CreditLine
    receivable_days: int | None = Field(default=None, ge=0)  # None -> median PaymentTerms.customer_days
    default_reliability: float | None = Field(default=None, ge=0.0, le=1.0)  # used only if Supplier.reliability is None
    contract_price_uzs: float | None = Field(default=None, gt=0)  # sale contract price ex VAT, if any
    calibration_actual: ActualSpec | None = None  # client's reported 12-month result (calibration target)
    demo_mode: bool = False
    apply_wholesale_markup_rule: bool = True  # False: the engine does not apply the 15% wholesale rule (legal switch)
    route_params: dict[str, RouteParams] = Field(default_factory=dict)
    # --- defaults that substitute for data the documents did not state (every use is written to `notes`) ---
    split_freight_from_fees: bool = False  # CustomsEntry.fees_uzs may hold inbound freight (D7): split it out, see below
    default_min_batch_packs: int = Field(default=0, ge=0)  # Route.min_shipment_packs, else Contract.moq_packs, else this
    default_max_batch_packs: int | None = Field(default=None, gt=0)  # Route.max_shipment_packs, else largest observed shipment, else this
    capacity_factor: float = Field(default=1.5, gt=0.0)  # Route.capacity_packs_per_year, else factor x demand p90
    default_max_shipments_per_year: int = Field(default=24, gt=0)  # Route.max_shipments_per_year, else this
    demand_from_sales: bool = False  # no DemandForecast -> use trailing-12-month sales instead of failing (opt-in)
    demand_p10_factor: float = Field(default=0.85, gt=0.0)  # no DemandForecast: p10 = factor x trailing-12-month sales
    demand_p90_factor: float = Field(default=1.15, gt=0.0)  # ... p90 = factor x trailing-12-month sales
    default_receivable_days: int = Field(default=30, ge=0)  # PaymentTerms.customer_days unknown everywhere
    financing_cost_items: Literal["engine_model", "as_recorded"] = "engine_model"
    # "engine_model": CostItem(category=financing) is dropped, the engine charges financing from CreditLine/credit rate;
    # "as_recorded": recorded financing CostItems stay below the line and the engine's own financing charge is zeroed
    # (inventory_days=0 is NOT used; the credit rate is set to 0), so interest is counted exactly once.


# --------------------------------------------------------------------------------------------- helpers

def _f(x: Decimal | float | int) -> float:
    return float(x)


def _month(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _mi(m: str) -> int:
    if len(m) != 7 or m[4] != "-" or not (m[:4].isdigit() and m[5:].isdigit()):
        raise AdapterError(f"period '{m}' is not YYYY-MM")
    return int(m[:4]) * 12 + int(m[5:]) - 1


def _hs_date(hs: str) -> date:
    _mi(hs)
    return date(int(hs[:4]), int(hs[5:]), 1)


def _in_window(period: str, hs: str, back: int = 12) -> bool:
    return _mi(hs) - back <= _mi(period) < _mi(hs)


def _fx_on(g: onto.ClientGraph, currency: str, day: date) -> float | None:
    rows = [r for r in g.fx_rates if r.currency == currency and r.date <= day]
    if not rows:
        rows = [r for r in g.fx_rates if r.currency == currency]  # nearest later rate if none before
        return _f(min(rows, key=lambda r: r.date).rate_uzs) if rows else None
    return _f(max(rows, key=lambda r: r.date).rate_uzs)


def _in_horizon(period: str, hs: str) -> bool:
    return _mi(hs) <= _mi(period) < _mi(hs) + 12


# --------------------------------------------------------------------------------------------- pieces

def _sku(g: onto.ClientGraph, cfg: EngineConfig) -> onto.Product:
    if cfg.sku_id is not None:
        for p in g.products:
            if p.sku_id == cfg.sku_id:
                return p
        raise AdapterError(f"sku '{cfg.sku_id}' is not in the graph")
    if len(g.products) != 1:
        raise AdapterError("graph holds several products: set EngineConfig.sku_id")
    return g.products[0]


def check_cost_allocation(g: onto.ClientGraph) -> None:
    bad = [i for i, c in enumerate(g.cost_items) if c.allocation == "unallocated"]
    if bad:
        raise UnallocatedCostError(
            f"{len(bad)} cost item(s) are unallocated (cost_items[{bad[0]}]...): set allocation before calculating")


def product_spec(g: onto.ClientGraph, sku: onto.Product, cfg: EngineConfig, rules: Rules, notes: list[str]) -> ProductSpec:
    recs = sorted((r for r in g.registry_records if r.product == sku.sku_id), key=lambda r: r.registry_date)
    if not recs:
        raise AdapterError(f"no RegistryRecord for sku '{sku.sku_id}': ceilings unknown")
    r = recs[-1]
    return ProductSpec(id=sku.sku_id, name=sku.trade_name, reference_currency=r.rn_currency,
                       chun_uzs=_f(r.chun_incl_vat), chchn_uzs=_f(r.chchn_incl_vat),
                       contract_price_uzs=cfg.contract_price_uzs, rn_foreign=_f(r.rn_fx),
                       chun_formula_uzs=_f(r.chun_formula_incl_vat))


def _price(g: onto.ClientGraph, supplier: str, sku: str, hs: date) -> tuple[float, str] | None:
    superseded = {c.supersedes for c in g.contracts if c.supersedes}
    cs = [c for c in g.contracts if c.supplier == supplier and c.contract_id not in superseded
          and c.valid_from <= hs <= c.valid_to]
    if cs:
        c = max(cs, key=lambda c: c.signed_on)
        return _f(c.unit_price), c.currency
    qs = [q for q in g.quotes if q.supplier == supplier and q.product == sku and q.valid_until >= hs]
    if qs:
        q = max(qs, key=lambda q: q.issued_on)
        return _f(q.unit_price), q.currency
    return None


def _route_customs(g: onto.ClientGraph, rid: str):
    ships = [s for s in g.shipments if s.route == rid]
    entries = {e.entry_ref: e for e in g.customs_entries}
    out = []
    for s in ships:
        if s.customs_entry not in entries:
            raise AdapterError(f"shipment '{s.shipment_id}': customs entry '{s.customs_entry}' is missing")
        out.append((s, entries[s.customs_entry]))
    return out


def _moq(g: onto.ClientGraph, supplier: str, hs: date) -> int | None:
    cs = [c for c in g.contracts if c.supplier == supplier and c.valid_from <= hs <= c.valid_to and c.moq_packs]
    return max(cs, key=lambda c: c.signed_on).moq_packs if cs else None


def _freight(g: onto.ClientGraph, r: onto.Route, hs: date) -> tuple[float, float]:
    """(per pack, per shipment) UZS from the route's tariff: UZS fields if given, else USD at the nominal USD rate."""
    if r.freight_per_pack_uzs is not None:
        return _f(r.freight_per_pack_uzs), _f(r.freight_per_shipment_uzs or 0)
    if r.freight_per_pack_usd is None:
        raise AdapterError(f"route '{r.route_id}': no freight tariff (freight_per_pack_uzs/usd)")
    usd = _fx_on(g, "USD", hs)
    if usd is None:
        raise AdapterError(f"route '{r.route_id}': freight tariff is in USD but the graph has no USD FxRate")
    return _f(r.freight_per_pack_usd) * usd, _f(r.freight_per_shipment_usd or 0) * usd


def _fees_per_pack(g: onto.ClientGraph, r: onto.Route, customs, cfg: EngineConfig, notes: list[str]) -> float:
    """Clearance fees per pack of the route's entries. D7 states landed cost, so CustomsEntry.fees_uzs = clearance fees
    + inbound freight. With `split_freight_from_fees` the carrier tariff (valid for the entry date) is subtracted, so the
    legal base of the 15% rule excludes freight (freight is added back as RouteSpec.freight_uzs: landed cost unchanged)."""
    total_fees = sum(_f(e.fees_uzs) for _, e in customs)
    packs = sum(e.packs for _, e in customs)
    has_tariff = r.freight_per_pack_uzs is not None or r.freight_per_pack_usd is not None
    if not cfg.split_freight_from_fees or not has_tariff:
        notes.append(f"route '{r.route_id}': CustomsEntry.fees_uzs used as is (no freight split); if it contains inbound "
                     f"freight the 15% legal base is overstated by that share")
        return total_fees / packs
    est = clamped = 0.0
    for s, e in customs:
        if r.freight_per_pack_uzs is not None:
            per_pack, per_ship = _f(r.freight_per_pack_uzs), _f(r.freight_per_shipment_uzs or 0)
        else:
            usd = _fx_on(g, "USD", e.entry_date)
            if usd is None:
                raise AdapterError(f"route '{r.route_id}': freight tariff is in USD but the graph has no USD FxRate")
            per_pack, per_ship = _f(r.freight_per_pack_usd) * usd, _f(r.freight_per_shipment_usd or 0) * usd
        fr = per_pack * e.packs + per_ship
        est += min(fr, _f(e.fees_uzs))
        clamped += max(fr - _f(e.fees_uzs), 0.0)
    notes.append(f"route '{r.route_id}': inbound freight estimated from the carrier tariff ({est / packs:,.0f} UZS/pack) "
                 f"and split out of CustomsEntry.fees_uzs; actual freight paid is not in the documents, so the legal base "
                 f"carries the (actual - tariff) difference")
    if clamped > 0:
        notes.append(f"route '{r.route_id}': tariff freight exceeded fees on some entries by {clamped:,.0f} UZS; fees floored at 0")
    return (total_fees - est) / packs


def route_specs(g: onto.ClientGraph, sku: str, cfg: EngineConfig, hs: str, notes: list[str],
                demand_p90: float = 0.0) -> tuple[RouteSpec, ...]:
    sites = {s.site_id: s for s in g.production_sites}
    sup = {s.supplier_id: s for s in g.suppliers}
    hsd = _hs_date(hs)
    out = []
    for r in g.routes:
        if r.production_site is None or r.production_site not in sites:
            notes.append(f"route '{r.route_id}' excluded: no production site (no document ties it to a producer)")
            continue
        site = sites[r.production_site]
        if sku not in site.approved_for:
            notes.append(f"route '{r.route_id}' excluded: production site '{site.site_id}' is not approved for '{sku}'")
            continue
        p = cfg.route_params.get(r.route_id, RouteParams())
        price = _price(g, site.supplier, sku, hsd)
        kc = p.kc if p.kc is not None else (price[0] if price else None)
        ccy = p.currency or (price[1] if price else None)
        if kc is None or ccy is None:
            raise AdapterError(f"route '{r.route_id}': no valid Contract/Quote price for supplier '{site.supplier}'")
        customs = _route_customs(g, r.route_id)
        if customs:
            base = sum(_f(e.cip_value_fx) * _f(e.fx_rate_on_entry) for _, e in customs)
            duty = sum(_f(e.duty_uzs) for _, e in customs) / base
            fees = _fees_per_pack(g, r, customs, cfg, notes)
        elif p.duty_rate is not None and p.fees_per_pack_uzs is not None:
            duty, fees = p.duty_rate, p.fees_per_pack_uzs
            notes.append(f"route '{r.route_id}': duty and fees from EngineConfig (no CustomsEntry)")
        else:
            raise AdapterError(f"route '{r.route_id}': no CustomsEntry and no duty_rate/fees_per_pack_uzs in config")
        freight_pack, freight_ship = _freight(g, r, hsd)
        if p.lot_size is None:
            notes.append(f"route '{r.route_id}': lot_size={cfg.default_lot_size} is an engine default")
        else:
            notes.append(f"route '{r.route_id}': lot_size={p.lot_size} = gcd of the shipment sizes observed in the documents")
        observed = [s.packs for s, _ in customs]
        if r.min_shipment_packs is not None:
            min_b = r.min_shipment_packs
        elif (moq := _moq(g, site.supplier, hsd)) is not None:
            min_b = moq
            notes.append(f"route '{r.route_id}': min_batch={moq} taken from the supplier Contract MOQ (route states none)")
        else:
            min_b = cfg.default_min_batch_packs
            notes.append(f"route '{r.route_id}': min_batch={min_b} is an engine default")
        if r.max_shipment_packs is not None:
            max_b = r.max_shipment_packs
        elif observed:
            max_b = max(observed)
            notes.append(f"route '{r.route_id}': max_batch={max_b} = largest observed shipment (route states none)")
        elif cfg.default_max_batch_packs is not None:
            max_b = cfg.default_max_batch_packs
            notes.append(f"route '{r.route_id}': max_batch={max_b} is an engine default")
        else:
            raise AdapterError(f"route '{r.route_id}': no max shipment size (route, history or default_max_batch_packs)")
        min_b = min(min_b, max_b)
        if r.capacity_packs_per_year is not None:
            cap = r.capacity_packs_per_year
        else:
            cap = max(int(cfg.capacity_factor * demand_p90), max_b)
            notes.append(f"route '{r.route_id}': capacity={cap} packs/year is an engine assumption "
                         f"({cfg.capacity_factor} x demand p90; no document states route capacity)")
        if r.max_shipments_per_year is not None:
            max_sh = r.max_shipments_per_year
        elif p.max_shipments is not None:
            max_sh = p.max_shipments
            notes.append(f"route '{r.route_id}': max_shipments={max_sh}/year from EngineConfig "
                         f"(shipments observed on the route in the 12 months before the horizon)")
        else:
            max_sh = cfg.default_max_shipments_per_year
            notes.append(f"route '{r.route_id}': max_shipments={max_sh}/year is an engine default")
        if r.is_eligible is None:
            notes.append(f"route '{r.route_id}': eligibility not stated, assumed eligible")
        if r.activation_cost_uzs is None:
            notes.append(f"route '{r.route_id}': activation cost not stated, taken as 0")
        supplier = sup[site.supplier]
        out.append(RouteSpec(
            id=r.route_id, supplier_id=site.supplier, name=f"{site.site_id} via {r.carrier} ({r.customs_post})",
            mode=p.mode or r.transport_mode, currency=ccy, kc=kc, duty_rate=duty, fees_uzs=fees,
            pr_uzs=p.pr_uzs, pr_confirmed=p.pr_confirmed, freight_uzs=freight_pack,
            lot_size=p.lot_size or cfg.default_lot_size, min_batch=min_b, max_batch=max_b,
            capacity_packs=cap, max_shipments=max_sh,
            fixed_per_shipment_uzs=freight_ship, fixed_activation_uzs=_f(r.activation_cost_uzs or 0),
            lead_time_days=r.lead_time_days.p50, supplier_terms_days=supplier.payment_terms.supplier_days,
            is_eligible=True if r.is_eligible is None else r.is_eligible, emergency=p.emergency,
            emergency_premium_uzs=p.emergency_premium_uzs, emergency_cap_packs=p.emergency_cap_packs))
    return tuple(out)


def supplier_specs(g: onto.ClientGraph, route_ids: set[str], cfg: EngineConfig,
                   notes: list[str] | None = None) -> tuple[SupplierSpec, ...]:
    used = {s.supplier for s in g.production_sites for r in g.routes
            if r.route_id in route_ids and r.production_site == s.site_id}
    out = []
    for s in g.suppliers:
        if s.supplier_id not in used:
            continue
        rel = s.reliability if s.reliability is not None else cfg.default_reliability
        if rel is None:
            raise AdapterError(f"supplier '{s.supplier_id}': reliability is unknown (set EngineConfig.default_reliability)")
        if s.reliability is None and notes is not None:
            notes.append(f"supplier '{s.supplier_id}': reliability not stated in any document, "
                         f"EngineConfig.default_reliability={rel} used (an assumption, not data)")
        out.append(SupplierSpec(id=s.supplier_id, name=s.name, reliability=rel))
    return tuple(out)


def _share(g: onto.ClientGraph, sku: str, period: str, how: str, scope: list[str] | None = None) -> float:
    rows = [s for s in g.sales_records if s.period == period and (scope is None or s.product in scope)]
    w = (lambda s: float(s.qty_packs)) if how == "shared_by_volume" else (lambda s: float(s.qty_packs * s.price_ex_vat))
    total = sum(w(s) for s in rows)
    if total <= 0:
        raise AdapterError(f"cannot split a shared cost ({how}) in {period}: no sales in that period")
    own = sum(w(s) for s in rows if s.product == sku)
    return own / total


def cost_specs(g: onto.ClientGraph, sku: str, routes: tuple[RouteSpec, ...], cfg: EngineConfig, hs: str,
               notes: list[str]) -> tuple[CostSpec, ...]:
    check_cost_allocation(g)
    ship_route = {s.shipment_id: s.route for s in g.shipments}
    packs_by_route: dict[str, int] = defaultdict(int)
    for s in g.shipments:
        packs_by_route[s.route] += s.packs
    rids = {r.id for r in routes}
    acq_rate: dict[tuple[str, str], list[float]] = defaultdict(list)  # (route, category) -> per-pack rates
    acq_lump: dict[tuple[str, str], float] = defaultdict(float)  # (route, category) -> UZS total
    below: list[CostSpec] = []
    rate_groups: dict[tuple, list[tuple[float, float | None]]] = defaultdict(list)
    skipped: dict[str, int] = defaultdict(int)
    for i, c in enumerate(g.cost_items):
        if c.layer == "acquisition":
            if c.shipment is None or ship_route[c.shipment] not in rids:
                if c.shipment is not None:
                    continue  # belongs to a route outside this calculation
                raise AdapterError(f"cost_items[{i}] ({c.category}): acquisition cost is not bound to a shipment/route")
            rid = ship_route[c.shipment]
            if c.driver == "per_pack":
                acq_rate[(rid, c.category)].append(_f(c.amount_uzs))
            elif c.driver in ("per_shipment", "fixed"):
                acq_lump[(rid, c.category)] += _f(c.amount_uzs)
            else:
                raise AdapterError(f"cost_items[{i}]: driver '{c.driver}' is not supported for acquisition costs")
            continue
        if (c.category == "financing" or c.driver == "per_uzs_financed") and cfg.financing_cost_items == "engine_model":
            skipped["financing"] += 1
            continue
        if c.allocation == "direct" and c.product != sku:
            continue  # another SKU's cost
        if c.scope is not None and sku not in c.scope:
            continue  # shared among other SKUs only (CostItem.scope)
        if not _in_window(c.period, hs):
            skipped["outside the 12 months before the horizon"] += 1
            continue
        share = (_share(g, sku, c.period, c.allocation, c.scope) if c.allocation in ("shared_by_volume", "shared_by_revenue")
                 else None)
        if c.allocation != "direct" and not share:
            raise AdapterError(f"cost_items[{i}]: share of '{sku}' in {c.period} is zero or undefined")
        if c.driver == "per_pack":
            rate_groups[(c.category, c.allocation)].append((_f(c.amount_uzs), share))
        elif c.driver == "fixed":
            below.append(CostSpec(id=f"BL-{i}", name=c.category, layer="below_line", annual_uzs=_f(c.amount_uzs) * (share or 1.0),
                                  allocation="direct"))
        else:
            raise AdapterError(f"cost_items[{i}]: driver '{c.driver}' is not supported for below-line costs")
    if skipped["financing"]:
        notes.append(f"{skipped['financing']} financing CostItem(s) excluded: financing is modelled by the engine "
                     f"(CreditLine, else EngineConfig.default_credit_rate)")
    if skipped["outside the 12 months before the horizon"]:
        notes.append(f"{skipped['outside the 12 months before the horizon']} CostItem(s) outside the 12 months before the "
                     f"horizon ignored")
    for (cat, alloc), rows in sorted(rate_groups.items()):
        rate = statistics.fmean(a * (s if s is not None else 1.0) for a, s in rows)  # mean over periods
        below.append(CostSpec(id=f"BL-{cat}-{alloc}", name=cat, layer="below_line", per_pack_uzs=rate, allocation="direct"))
    acq: list[CostSpec] = []
    for r in routes:
        cats = sorted({k[1] for k in list(acq_rate) + list(acq_lump) if k[0] == r.id})
        total = 0.0
        for cat in cats:
            rates = acq_rate.get((r.id, cat))
            if rates:
                total += statistics.fmean(rates)
            if (r.id, cat) in acq_lump:
                if packs_by_route[r.id] <= 0:
                    raise AdapterError(f"route '{r.id}': lump-sum acquisition cost without shipped packs")
                total += acq_lump[(r.id, cat)] / packs_by_route[r.id]
        p = cfg.route_params.get(r.id)
        if not cats and p is not None and p.acquisition_per_pack_uzs:
            total = p.acquisition_per_pack_uzs
            notes.append(f"route '{r.id}': acquisition cost per pack from EngineConfig (no shipment-bound CostItem)")
        if total > 0:
            acq.append(CostSpec(id=f"AC-{r.id}", name="acquisition", layer="acquisition", route_id=r.id,
                                per_pack_uzs=total))
    return tuple(acq) + tuple(below)


def demand_spec(g: onto.ClientGraph, sku: str, hs: str, notes: list[str], cfg: EngineConfig | None = None) -> DemandSpec:
    rows = [d for d in g.demand_forecasts if d.product == sku and _in_horizon(d.period, hs)]
    if not rows:
        past = sum(s.qty_packs for s in g.sales_records if s.product == sku and _in_window(s.period, hs))
        if cfg is None or not cfg.demand_from_sales or past <= 0:
            raise AdapterError(f"no DemandForecast for '{sku}' in the horizon starting {hs}")
        notes.append(f"no DemandForecast: demand p50 = trailing-12-month sales ({past} packs); "
                     f"p10/p90 = x{cfg.demand_p10_factor}/x{cfg.demand_p90_factor} (EngineConfig assumption, not a forecast)")
        return DemandSpec(p10=past * cfg.demand_p10_factor, p50=float(past), p90=past * cfg.demand_p90_factor)
    months = {d.period for d in rows}
    if len(months) != len(rows):
        raise AdapterError("several DemandForecast rows for one month")
    if len(rows) < 12:
        notes.append(f"demand covers {len(rows)} of 12 horizon months")
    notes.append("annual demand quantiles are sums of monthly quantiles (assumes perfectly correlated months)")
    return DemandSpec(p10=sum(_f(d.p10) for d in rows), p50=sum(_f(d.p50) for d in rows), p90=sum(_f(d.p90) for d in rows))


def finance_spec(g: onto.ClientGraph, cfg: EngineConfig, rules: Rules, hs: str, sup_ids: set[str],
                 notes: list[str]) -> FinanceSpec:
    start = _hs_date(hs)
    lines = [c for c in g.credit_lines if c.available_from <= start]
    if lines:
        limit = sum(_f(c.limit_uzs) for c in lines)
        rate = sum(_f(c.annual_rate) * _f(c.limit_uzs) for c in lines) / limit if limit else cfg.default_credit_rate
        budget: float | None = limit
    else:
        budget, rate = None, cfg.default_credit_rate
        notes.append(f"no CreditLine: budget unlimited, credit rate {rate:.0%} from EngineConfig")
    if cfg.financing_cost_items == "as_recorded":
        rate = 0.0
        notes.append("financing: recorded financing CostItems are used as they are; the engine's own financing charge is off (rate 0)")
    if cfg.receivable_days is not None:
        recv = cfg.receivable_days
    else:
        days = [s.payment_terms.customer_days for s in g.suppliers
                if s.supplier_id in sup_ids and s.payment_terms.customer_days is not None]
        if days:
            recv = int(statistics.median_low(days))
        else:
            recv = cfg.default_receivable_days
            notes.append(f"receivable days unknown (PaymentTerms.customer_days): {recv} from EngineConfig")
    if g.fx_hedges:
        notes.append("FxHedge present: the engine uses the rule-catalog forward premium, not the booked forward")
    return FinanceSpec(credit_rate_annual=rate, budget_uzs=budget, inventory_days=cfg.inventory_days,
                       receivable_days=recv, hedge_forward_premium=rules.hedge_forward_premium)


def nominal_fx(g: onto.ClientGraph, currencies: set[str], hs: str) -> dict[str, float]:
    start = _hs_date(hs)
    out = {}
    for c in sorted(currencies):
        if c == "UZS":
            out[c] = 1.0  # the national currency needs no FxRate
            continue
        rows = [r for r in g.fx_rates if r.currency == c and r.date <= start]
        if not rows:
            raise AdapterError(f"no FxRate for {c} on or before {start}")
        out[c] = _f(max(rows, key=lambda r: r.date).rate_uzs)
    return out


def history(g: onto.ClientGraph, sku: str, route_ids: set[str]) -> tuple[tuple[ShipmentSpec, ...], tuple[SaleSpec, ...]]:
    entries = {e.entry_ref: e for e in g.customs_entries}
    ships = tuple(ShipmentSpec(id=s.shipment_id, route_id=s.route, month=_month(s.dispatch_date), packs=s.packs,
                               fx_uzs=_f(entries[s.customs_entry].fx_rate_on_entry),
                               kc=_f(entries[s.customs_entry].cip_value_fx) / s.packs)
                  for s in g.shipments if s.route in route_ids)
    sales = tuple(SaleSpec(month=s.period, packs=s.qty_packs, price_uzs=_f(s.price_ex_vat))
                  for s in g.sales_records if s.product == sku and s.qty_packs > 0)
    return ships, sales


# --------------------------------------------------------------------------------------------- entry point

def _provenance_kinds(g: onto.ClientGraph) -> set[str]:
    kinds: set[str] = set()

    def visit(v):
        if isinstance(v, onto.Provenance):
            kinds.add(v.kind)
        elif isinstance(v, BaseModel):
            for c in v.__dict__.values():
                visit(c)
        elif isinstance(v, dict):
            for c in v.values():
                visit(c)
        elif isinstance(v, (list, tuple)):
            for c in v:
                visit(c)

    visit(g)
    return kinds


def to_calculation_graph(graph: onto.ClientGraph, config: EngineConfig, rules: Rules | None = None) -> CalculationGraph:
    """Ontology graph -> engine calculation input. Order of defences: ontology `for_calculation()` (references,
    no `extracted_unconfirmed`), unallocated-cost block, then field mapping with explicit errors."""
    rules = rules or load_rules()
    g = graph.for_calculation()
    check_cost_allocation(g)
    hs = config.horizon_start
    notes: list[str] = []
    sku = _sku(g, config)
    product = product_spec(g, sku, config, rules, notes)
    demand = demand_spec(g, sku.sku_id, hs, notes, config)
    routes = route_specs(g, sku.sku_id, config, hs, notes, demand.p90)
    if not routes:
        raise AdapterError("no routes approved for the sku")
    rids = {r.id for r in routes}
    suppliers = supplier_specs(g, rids, config, notes)
    costs = cost_specs(g, sku.sku_id, routes, config, hs, notes)
    finance = finance_spec(g, config, rules, hs, {s.id for s in suppliers}, notes)
    fx = nominal_fx(g, {r.currency for r in routes} | {product.reference_currency}, hs)
    ships, sales = history(g, sku.sku_id, rids)
    notes.append(f"engine-only parameters (EngineConfig, not in the ontology): penalty={config.penalty_uzs_per_pack} UZS/pack, "
                 f"inventory_days={config.inventory_days}, default_lot_size={config.default_lot_size}, "
                 f"credit_rate={finance.credit_rate_annual:.2%}, hedge_forward_premium={finance.hedge_forward_premium}, "
                 f"financing_cost_items={config.financing_cost_items}, split_freight_from_fees={config.split_freight_from_fees}")
    kinds = _provenance_kinds(g)
    if "synthetic" in kinds:
        notes.append("graph contains SYNTHETIC provenance: results are illustrative")
    if "assumption" in kinds:
        notes.append("graph contains ASSUMPTION provenance")
    return CalculationGraph(
        client_id=config.client_id or sku.sku_id, product=product, suppliers=suppliers, routes=routes, cost_items=costs,
        demand=demand, shipments=ships, sales=sales, actual=config.calibration_actual, finance=finance,
        nominal_fx=fx, horizon_start=hs, penalty_uzs_per_pack=config.penalty_uzs_per_pack,
        demo_mode=config.demo_mode, apply_wholesale_markup_rule=config.apply_wholesale_markup_rule,
        fixture_note="synthetic provenance" if "synthetic" in kinds else None,
        notes=tuple(notes))


def as_calculation_graph(graph: onto.ClientGraph | EngineGraph, config: EngineConfig | None = None,
                         rules: Rules | None = None) -> CalculationGraph:
    """Accept an ontology graph (needs `config`), or an engine graph / already-adapted calculation graph."""
    if isinstance(graph, CalculationGraph):
        return graph
    if isinstance(graph, EngineGraph):
        return graph.for_calculation()
    if config is None:
        raise AdapterError("an ontology ClientGraph needs an EngineConfig")
    return to_calculation_graph(graph, config, rules)


# --------------------------------------------------------------------------------------------- derived config

def derive_calibration_actual(g: onto.ClientGraph, sku: str, hs: str) -> ActualSpec | None:
    """The client's 12-month result before `hs` computed from the graph's own documents with `lagrange_rx.pnl`
    (lot-traced landed cost + allocated below-line costs). None if any month is blocked. Never read from simulation
    truth: product code has no access to it."""
    from lagrange_rx.pnl import calculate_pnl
    cats = {c.category for c in g.cost_items if c.layer == "below_line"}
    revenue = cost = 0.0
    for k in range(_mi(hs) - 12, _mi(hs)):
        period = f"{k // 12:04d}-{k % 12 + 1:02d}"
        res = calculate_pnl(g, sku, period, cats)
        if res.missing or res.revenue is None or res.cost is None:
            return None
        revenue += float(res.revenue)
        cost += float(res.cost)
    return ActualSpec(revenue_uzs=revenue, total_cost_uzs=cost) if revenue > 0 else None


def derive_route_params(g: onto.ClientGraph, hs: str) -> dict[str, RouteParams]:
    """Per-route engine parameters read from the graph's own shipments (documents, never simulation truth):
    lot_size = gcd of all observed shipment sizes on the route (only when >= 100: a smaller gcd is noise, not a lot);
    max_shipments = shipments dispatched on the route in the 12 months before the horizon (the client's observed cadence)."""
    from math import gcd
    out: dict[str, RouteParams] = {}
    for r in g.routes:
        ships = [s for s in g.shipments if s.route == r.route_id]
        if not ships:
            continue
        lot = 0
        for s in ships:
            lot = gcd(lot, s.packs)
        recent = sum(1 for s in ships if _in_window(_month(s.dispatch_date), hs))
        kw = {}
        if lot >= 100:
            kw["lot_size"] = lot
        if recent > 0:
            kw["max_shipments"] = recent
        if kw:
            out[r.route_id] = RouteParams(**kw)
    return out


def default_engine_config(g: onto.ClientGraph, *, default_reliability: float = 0.95) -> EngineConfig:
    """EngineConfig derived from the graph for documents-only clients. Every choice is an assumption of the engine,
    not data (each substitution is also written to `CalculationGraph.notes`):
      horizon_start = month after the last SalesRecord period; sku_id = the single product with a RegistryRecord;
      default_reliability = 0.95 (no Supplier.reliability in any document); split_freight_from_fees = True (D7 lumps
      inbound freight into fees); financing_cost_items = as_recorded when financing CostItems exist and there is no
      CreditLine (interest is then counted once, from the books); calibration_actual = lagrange_rx.pnl over the 12
      months before the horizon; route_params = lot size / shipment cadence observed in the graph's shipments
      (`derive_route_params`); all other fields keep the EngineConfig defaults."""
    g.for_calculation()
    skus = sorted({r.product for r in g.registry_records})
    if len(skus) != 1:
        raise AdapterError(f"cannot pick the focus SKU: registered products are {skus}; pass an explicit EngineConfig")
    periods = [s.period for s in g.sales_records]
    if not periods:
        raise AdapterError("no SalesRecord: horizon cannot be derived")
    last = _mi(max(periods)) + 1
    hs = f"{last // 12:04d}-{last % 12 + 1:02d}"
    fin = any(c.category == "financing" for c in g.cost_items) and not g.credit_lines
    return EngineConfig(horizon_start=hs, sku_id=skus[0], default_reliability=default_reliability,
                        split_freight_from_fees=True, demand_from_sales=True, financing_cost_items="as_recorded" if fin else "engine_model",
                        calibration_actual=derive_calibration_actual(g, skus[0], hs),
                        route_params=derive_route_params(g, hs))
