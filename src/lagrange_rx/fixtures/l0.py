"""L0 fixture: an `ontology.ClientGraph` assembled by hand (stand-in for the Ingest output).

SYNTHETIC / ILLUSTRATIVE: numbers are made up to be internally consistent (provenance kind `synthetic` on every
entity); the 'actual' accounting result is constructed so that the client's margin is ~2.5%. Calibration on this
fixture validates plumbing, not real-world fidelity. The engine reads it through `engine.adapter`; the parameters
the ontology does not carry are in `build_l0_config()`.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from functools import lru_cache

from lagrange_rx import ontology as o
from lagrange_rx.engine.adapter import EngineConfig, RouteParams, to_calculation_graph
from lagrange_rx.engine.inputs import ActualSpec, CalculationGraph
from lagrange_rx.engine.twin import _historical_cost, last_12_months

TARGET_MARGIN = 0.025
HORIZON_START = "2026-11"
SKU = "SKU-CARDIO-1"
OTHER = "SKU-OTHER-1"
HISTORY_MONTHS = [f"2025-{m:02d}" for m in range(11, 13)] + [f"2026-{m:02d}" for m in range(1, 11)]
HORIZON_MONTHS = [f"2026-{m:02d}" for m in (11, 12)] + [f"2027-{m:02d}" for m in range(1, 11)]
P = o.Provenance(kind="synthetic", source="fixtures/l0.py (hand-built, not client data)")
D = Decimal

# route_id: (supplier, site, carrier, mode, freight/pack, freight/shipment, min, max, per year, shipments/year, activation, lead p50/p90)
_ROUTES = {
    "R-SEA-A": ("SUP-A", "SITE-A", "CAR-1", "multimodal", 2600, 8_000_000, 10_000, 30_000, 90_000, 6, 2_000_000, (45, 60)),
    "R-RAIL-B": ("SUP-B", "SITE-B", "CAR-2", "rail", 2300, 8_000_000, 5_000, 20_000, 50_000, 8, 2_000_000, (45, 55)),
    "R-AIR-C": ("SUP-C", "SITE-C", "CAR-3", "air", 7000, 3_000_000, 1_000, 8_000, 30_000, 10, 1_000_000, (7, 10)),
    "R-AIR-EMERG": ("SUP-C", "SITE-C", "CAR-3", "air", 9000, 0, 0, 8_000, 0, 0, 0, (7, 10)),
}
_PRICE = {"SUP-A": (D("4.80"), "USD"), "SUP-B": (D("4.10"), "EUR"), "SUP-C": (D("4.95"), "USD")}
_RELIABILITY = {"SUP-A": 0.93, "SUP-B": 0.96, "SUP-C": 0.98}
# (route, shipment id, dispatch month, packs, customs fx)
_HISTORY = [("R-SEA-A", f"SH-A{i}", m, 20_000, 11_900.0 + 40 * i)
            for i, m in enumerate(["2025-11", "2026-02", "2026-05", "2026-08"])] + \
           [("R-RAIL-B", f"SH-B{i}", m, 15_000, 13_050.0 + 60 * i) for i, m in enumerate(["2026-01", "2026-07"])]


def _d(month: str, day: int = 1) -> date:
    return date(int(month[:4]), int(month[5:]), day)


def _pack_terms() -> o.PaymentTerms:
    return o.PaymentTerms(supplier_days=45, customer_days=30, prepayment_share=D(0), provenance=P)


def _supply() -> dict:
    sites = {"SITE-A": "SUP-A", "SITE-B": "SUP-B", "SITE-C": "SUP-C"}
    return dict(
        suppliers=[o.Supplier(supplier_id=s, name=f"Supplier {s[-1]}", country="IN" if s != "SUP-B" else "DE",
                              payment_terms=_pack_terms(), reliability=_RELIABILITY[s], provenance=P)
                   for s in _PRICE],
        production_sites=[o.ProductionSite(site_id=k, supplier=v, country="IN", approved_for=[SKU, OTHER], provenance=P)
                          for k, v in sites.items()],
        carriers=[o.Carrier(carrier_id=f"CAR-{i}", name=f"Carrier {i}", modes=["multimodal", "rail", "air"], provenance=P)
                  for i in (1, 2, 3)],
        routes=[o.Route(route_id=rid, production_site=site, carrier=car, transport_mode=mode, incoterms="CIP",
                        customs_post="Tashkent", lead_time_days=o.LeadTime(p50=lt[0], p90=lt[1]),
                        freight_per_shipment_uzs=D(fps), freight_per_pack_uzs=D(fpp), min_shipment_packs=mn,
                        max_shipment_packs=mx, max_shipments_per_year=ns, capacity_packs_per_year=cap,
                        activation_cost_uzs=D(act), is_eligible=True, provenance=P)
                for rid, (sup, site, car, mode, fpp, fps, mn, mx, cap, ns, act, lt) in _ROUTES.items()],
        contracts=[o.Contract(contract_id=f"CT-{s}", supplier=s, version="1", signed_on=date(2026, 1, 1),
                              valid_from=date(2026, 1, 1), valid_to=date(2027, 12, 31), unit_price=p, currency=c,
                              moq_packs=0, payment_terms=_pack_terms(), provenance=P)
                   for s, (p, c) in _PRICE.items()],
    )


def _history() -> dict:
    entries, ships = [], []
    for rid, sid, month, packs, fx in _HISTORY:
        sup = _ROUTES[rid][0]
        price, ccy = _PRICE[sup]
        cip = price * packs
        base = cip * D(str(fx))
        duty, fees = base * D("0.10"), D(1400 * packs)
        entries.append(o.CustomsEntry(entry_ref=f"CE-{sid}", entry_date=_d(month, 15), cip_value_fx=cip, currency=ccy,
                                      fx_rate_on_entry=D(str(fx)), duty_uzs=duty, fees_uzs=fees,
                                      vat_uzs=(base + duty + fees) * D("0.12"), packs=packs, provenance=P))
        ships.append(o.Shipment(shipment_id=sid, route=rid, dispatch_date=_d(month), arrival_date=_d(month) + timedelta(days=30),
                                packs=packs, actual_freight_uzs=D(_ROUTES[rid][4] * packs), customs_entry=f"CE-{sid}",
                                provenance=P))
    return dict(customs_entries=entries, shipments=ships)


def _costs() -> list[o.CostItem]:
    acq = [o.CostItem(category="other_acquisition", layer="acquisition", amount_uzs=D(800), period="2026-05",
                      driver="per_pack", shipment=sh, product=SKU, allocation="direct", provenance=P)
           for sh in ("SH-A0", "SH-B0")]
    wh = o.CostItem(category="warehousing", layer="below_line", amount_uzs=D(300), period="2026-05", driver="per_pack",
                    product=SKU, allocation="direct", provenance=P)
    sales_team = [o.CostItem(category="distribution", layer="below_line", amount_uzs=D(20_000_000), period=m, driver="fixed",
                             allocation="shared_by_volume", provenance=P) for m in HISTORY_MONTHS]
    return acq + [wh] + sales_team  # sales team 240M/yr; the SKU's volume share is 25% (see _sales)


def _products() -> dict:
    mk = lambda sku, name: o.Product(sku_id=sku, trade_name=name, inn="inn", dosage_form="tablet", strength="10 mg",  # noqa: E731
                                     pack_size=30, rx=True, manufacturer="MFR-1", registration_certificate="RC-1",
                                     provenance=P)
    return dict(
        manufacturers=[o.Manufacturer(name="MFR-1", country="IN", provenance=P)],
        registration_certificates=[o.RegistrationCertificate(number="RC-1", valid_from=date(2024, 1, 1),
                                                             valid_to=date(2029, 1, 1), holder="MFR-1", provenance=P)],
        products=[mk(SKU, "Cardio Rx 30 tab"), mk(OTHER, "Other Rx 30 tab")],
        # P19 formula: (6.8 * 12000 * 1.02 + 3724.5) * 1.12 * 1.15 = 112000 -> published ChUN agrees with the formula
        registry_records=[o.RegistryRecord(product=SKU, registration_number="RN-1", registry_date=date(2026, 1, 1),
                                           rn_fx=D("6.8"), rn_currency="USD", filing_fx_rate=D("12000"),
                                           customs_b_uzs=D("3724.5"), chun_incl_vat=D("112000"),
                                           chchn_incl_vat=D("150000"), provenance=P)],
    )


def _sales(price: float) -> list[o.SalesRecord]:
    rows = []
    for m in HISTORY_MONTHS:
        rows.append(o.SalesRecord(period=m, product=SKU, region="R1", channel="distributors", qty_packs=9_000,
                                  price_ex_vat=D(repr(price)), provenance=P))
        rows.append(o.SalesRecord(period=m, product=OTHER, region="R1", channel="distributors", qty_packs=27_000,
                                  price_ex_vat=D(1000), provenance=P))
    return rows


def _demand() -> list[o.DemandForecast]:
    return [o.DemandForecast(period=m, product=SKU, p10=D("7916.67"), p50=D("9166.67"), p90=D("10666.67"),
                             method="fixture", coverage_calibrated=False, provenance=P) for m in HORIZON_MONTHS]


def _graph(price: float) -> o.ClientGraph:
    return o.ClientGraph(
        **_products(), **_supply(), **_history(), cost_items=_costs(), sales_records=_sales(price),
        demand_forecasts=_demand(),
        currencies=[o.Currency(code=c, provenance=P) for c in ("USD", "EUR")],
        fx_rates=[o.FxRate(currency="USD", date=date(2026, 10, 1), rate_uzs=D(12_000), source="fixture", provenance=P),
                  o.FxRate(currency="EUR", date=date(2026, 10, 1), rate_uzs=D(13_100), source="fixture", provenance=P)],
        credit_lines=[o.CreditLine(limit_uzs=D(9_000_000_000), annual_rate=D("0.14"), available_from=date(2026, 1, 1),
                                   provenance=P)],
        regions=[o.Region(region_id="R1", name="Tashkent", provenance=P)],
        channels=[o.Channel(name="distributors", provenance=P)],
    )


def _config(actual: ActualSpec | None) -> EngineConfig:
    return EngineConfig(
        horizon_start=HORIZON_START, sku_id=SKU, client_id="L0-demo-client", penalty_uzs_per_pack=3000.0, inventory_days=30, calibration_actual=actual,
        route_params={
            "R-SEA-A": RouteParams(mode="sea"),
            "R-RAIL-B": RouteParams(),
            "R-AIR-C": RouteParams(duty_rate=0.10, fees_per_pack_uzs=1400.0, acquisition_per_pack_uzs=800.0),
            "R-AIR-EMERG": RouteParams(duty_rate=0.10, fees_per_pack_uzs=1400.0, emergency=True,
                                       emergency_premium_uzs=6000.0, emergency_cap_packs=8_000),
        })


@lru_cache(maxsize=1)
def _built() -> tuple[o.ClientGraph, EngineConfig]:
    # 1) cost of the last 12 months through the twin on a provisional graph (price does not affect costs)
    prov = to_calculation_graph(_graph(1.0), _config(None))
    sales = last_12_months(prov, prov.sales)
    cost = _historical_cost(prov, last_12_months(prov, prov.shipments), sales)
    packs = sum(s.packs for s in sales)
    price = round(cost / (1.0 - TARGET_MARGIN) / packs, 6)
    revenue = price * packs
    # 2) accounting differs from the twin by a small deterministic residual (0.04 pp of revenue)
    actual = ActualSpec(revenue_uzs=revenue, total_cost_uzs=cost + 0.0004 * revenue)
    return _graph(price), _config(actual)


def build_l0_graph() -> o.ClientGraph:
    """The hand-built ontology graph (what Ingest will produce, synthetic)."""
    return _built()[0]


def build_l0_config(**overrides) -> EngineConfig:
    """Engine-only parameters for the L0 graph (lot size, emergency route, penalty, calibration target...)."""
    return _built()[1].model_copy(update=overrides)


def build_l0_calculation_graph(**config_overrides) -> CalculationGraph:
    return to_calculation_graph(build_l0_graph(), build_l0_config(**config_overrides))
