"""ontology.ClientGraph -> engine CalculationGraph: one test per field mapping (stage 3)."""
from datetime import date
from decimal import Decimal

import pytest

from lagrange_rx import ontology as o
from lagrange_rx.engine.adapter import (
    AdapterError, EngineConfig, RouteParams, UnallocatedCostError, as_calculation_graph, to_calculation_graph,
)
from lagrange_rx.engine.inputs import CalculationGraph
from lagrange_rx.engine.twin import legal_base
from lagrange_rx.fixtures.l0 import SKU, build_l0_config, build_l0_graph

G = build_l0_graph()
C = build_l0_config()
CG = to_calculation_graph(G, C)


def route(rid):
    return CG.route(rid)


def test_result_is_calculation_graph_and_engine_requires_it():
    assert isinstance(CG, CalculationGraph)
    assert as_calculation_graph(CG) is CG
    with pytest.raises(AdapterError):
        as_calculation_graph(G)  # ontology graph needs an EngineConfig


def test_product_from_registry_record_with_vat():
    rec = G.registry_records[0]
    assert CG.product.id == SKU and CG.product.name == "Cardio Rx 30 tab"
    assert CG.product.chun_uzs == float(rec.chun_incl_vat) == 112_000.0
    assert CG.product.chchn_uzs == float(rec.chchn_incl_vat) == 150_000.0
    assert CG.product.rn_foreign == float(rec.rn_fx) and CG.product.reference_currency == "USD"
    assert CG.product.chun_formula_uzs == pytest.approx(float(rec.chun_formula_incl_vat))
    assert CG.product.chun_formula_uzs == pytest.approx(112_000.0, rel=1e-6)  # P19 formula agrees with published


def test_registry_divergence_from_formula_warns_in_twin(rules):
    from lagrange_rx.engine.twin import formula_warnings
    rec = G.registry_records[0].model_copy(update={"chun_incl_vat": Decimal("130000")})
    g = G.model_copy(update={"registry_records": [rec]})
    cg = to_calculation_graph(g, C)
    assert cg.product.chun_uzs == 130_000.0 and formula_warnings(cg, rules)
    assert not formula_warnings(CG, rules)


def test_missing_registry_record_is_an_error():
    with pytest.raises(AdapterError, match="RegistryRecord"):
        to_calculation_graph(G.model_copy(update={"registry_records": []}), C)


def test_supplier_reliability_and_default():
    assert {s.id: s.reliability for s in CG.suppliers} == {"SUP-A": 0.93, "SUP-B": 0.96, "SUP-C": 0.98}
    sup = [s.model_copy(update={"reliability": None}) if s.supplier_id == "SUP-A" else s for s in G.suppliers]
    g = G.model_copy(update={"suppliers": sup})
    with pytest.raises(AdapterError, match="reliability"):
        to_calculation_graph(g, C)
    cg = to_calculation_graph(g, C.model_copy(update={"default_reliability": 0.5}))
    assert {s.id: s.reliability for s in cg.suppliers}["SUP-A"] == 0.5


def test_route_fields_from_ontology_route_and_site():
    r, src = route("R-RAIL-B"), next(x for x in G.routes if x.route_id == "R-RAIL-B")
    assert r.supplier_id == "SUP-B"  # via ProductionSite.supplier
    assert (r.mode, r.freight_uzs) == ("rail", float(src.freight_per_pack_uzs))
    assert (r.min_batch, r.max_batch) == (src.min_shipment_packs, src.max_shipment_packs)
    assert (r.capacity_packs, r.max_shipments) == (src.capacity_packs_per_year, src.max_shipments_per_year)
    assert r.fixed_per_shipment_uzs == float(src.freight_per_shipment_uzs)
    assert r.fixed_activation_uzs == float(src.activation_cost_uzs)
    assert r.lead_time_days == src.lead_time_days.p50 and r.is_eligible
    assert r.supplier_terms_days == 45  # Supplier.payment_terms.supplier_days


def test_route_mode_multimodal_unless_overridden_and_sea_is_config():
    assert route("R-SEA-A").mode == "sea"  # config override (ontology has no sea)
    cg = to_calculation_graph(G, C.model_copy(update={"route_params": {**C.route_params, "R-SEA-A": RouteParams()}}))
    assert cg.route("R-SEA-A").mode == "multimodal"


def test_kc_and_currency_from_contract_valid_at_horizon():
    assert (route("R-SEA-A").kc, route("R-SEA-A").currency) == (4.80, "USD")
    assert (route("R-RAIL-B").kc, route("R-RAIL-B").currency) == (4.10, "EUR")


def test_superseded_contract_is_ignored_and_quote_is_fallback():
    old = G.contracts[0]
    new = old.model_copy(update={"contract_id": "CT-A2", "version": "2", "signed_on": date(2026, 6, 1),
                                 "unit_price": Decimal("5.00"), "supersedes": old.contract_id})
    cg = to_calculation_graph(G.model_copy(update={"contracts": [old, new, *G.contracts[1:]]}), C)
    assert cg.route("R-SEA-A").kc == 5.0
    quote = o.Quote(quote_id="Q1", supplier="SUP-A", product=SKU, issued_on=date(2026, 9, 1), valid_until=date(2027, 3, 1),
                    unit_price=Decimal("4.7"), currency="USD", incoterms="CIP", moq_packs=0, lead_time_days=40,
                    min_remaining_shelf_life_months=12, vat_status="excl", provenance=G.contracts[0].provenance)
    g = G.model_copy(update={"contracts": G.contracts[1:], "quotes": [quote]})
    assert to_calculation_graph(g, C).route("R-SEA-A").kc == 4.7
    with pytest.raises(AdapterError, match="price"):
        to_calculation_graph(G.model_copy(update={"contracts": G.contracts[1:]}), C)


def test_duty_and_fees_from_customs_entries_and_legal_base_matches_entry():
    r = route("R-SEA-A")
    assert r.duty_rate == pytest.approx(0.10) and r.fees_uzs == pytest.approx(1400.0)
    entry = next(e for e in G.customs_entries if e.entry_ref == "CE-SH-A0")
    # engine legal base at the entry's own rate == CustomsEntry.legal_base_c_per_pack (no hedge, no freight)
    assert legal_base(r, float(entry.fx_rate_on_entry)) == pytest.approx(float(entry.legal_base_c_per_pack))


def test_route_without_customs_needs_config_duty_and_fees():
    r = route("R-AIR-C")
    assert (r.duty_rate, r.fees_uzs) == (0.10, 1400.0)
    c = C.model_copy(update={"route_params": {**C.route_params, "R-AIR-C": RouteParams()}})
    with pytest.raises(AdapterError, match="CustomsEntry"):
        to_calculation_graph(G, c)


def test_shipments_use_customs_rate_and_dispatch_month():
    s = next(x for x in CG.shipments if x.id == "SH-A1")
    assert (s.route_id, s.month, s.packs, s.fx_uzs) == ("R-SEA-A", "2026-02", 20_000, 11_940.0)
    assert len(CG.shipments) == 6


def test_emergency_route_params_come_from_config_only():
    e = route("R-AIR-EMERG")
    assert e.emergency and (e.emergency_premium_uzs, e.emergency_cap_packs) == (6000.0, 8000)
    assert not route("R-SEA-A").emergency


def test_lot_size_default_is_recorded_as_assumption():
    assert route("R-SEA-A").lot_size == 1000
    assert any("lot_size=1000 is an engine default" in n for n in CG.notes)
    cg = to_calculation_graph(G, C.model_copy(update={"default_lot_size": 500}))
    assert cg.route("R-SEA-A").lot_size == 500


def test_acquisition_costs_bound_to_route_via_shipment():
    by_route = {i.route_id: i.per_pack_uzs for i in CG.cost_items if i.layer == "acquisition"}
    assert by_route == {"R-SEA-A": 800.0, "R-RAIL-B": 800.0, "R-AIR-C": 800.0}  # AIR-C: config (no shipment)
    free = [c.model_copy(update={"shipment": None}) if c.layer == "acquisition" else c for c in G.cost_items]
    with pytest.raises(AdapterError, match="not bound"):
        to_calculation_graph(G.model_copy(update={"cost_items": free}), C)


def test_below_line_per_pack_and_shared_fixed_by_volume_share():
    bl = [i for i in CG.cost_items if i.layer == "below_line"]
    assert sum(i.per_pack_uzs for i in bl) == 300.0
    # 12 x 20M shared_by_volume; SKU sells 9000 of 36000 packs every month -> 25%
    assert sum(i.annual_uzs for i in bl) == pytest.approx(60_000_000.0)


def test_shared_by_revenue_uses_revenue_share():
    items = [i.model_copy(update={"allocation": "shared_by_revenue"}) if i.allocation == "shared_by_volume" else i
             for i in G.cost_items]
    cg = to_calculation_graph(G.model_copy(update={"cost_items": items}), C)
    price = float(G.sales_records[0].price_ex_vat)
    share = 9000 * price / (9000 * price + 27_000 * 1000.0)
    assert sum(i.annual_uzs for i in cg.cost_items if i.layer == "below_line") == pytest.approx(240e6 * share)


def test_other_sku_direct_cost_is_not_attributed():
    other = o.CostItem(category="marketing", layer="below_line", amount_uzs=Decimal(999), period="2026-05",
                       driver="per_pack", product="SKU-OTHER-1", allocation="direct", provenance=G.contracts[0].provenance)
    cg = to_calculation_graph(G.model_copy(update={"cost_items": [*G.cost_items, other]}), C)
    assert sum(i.per_pack_uzs for i in cg.cost_items if i.layer == "below_line") == 300.0


def test_unallocated_cost_blocks_calculation():
    bad = o.CostItem(category="g_and_a", layer="below_line", amount_uzs=Decimal(5), period="2026-05", driver="fixed",
                     provenance=G.contracts[0].provenance)  # allocation defaults to unallocated
    with pytest.raises(UnallocatedCostError, match="unallocated"):
        to_calculation_graph(G.model_copy(update={"cost_items": [*G.cost_items, bad]}), C)


def test_unconfirmed_data_never_enters():
    bad = o.Provenance(kind="extracted_unconfirmed", source="x.pdf")
    g = G.model_copy(update={"contracts": [G.contracts[0].model_copy(update={"provenance": bad}), *G.contracts[1:]]})
    with pytest.raises(ValueError, match="unconfirmed"):
        to_calculation_graph(g, C)


def test_demand_is_sum_of_horizon_months_and_noted():
    assert CG.demand.p10 == pytest.approx(95_000.04) and CG.demand.p50 == pytest.approx(110_000.04)
    assert CG.demand.p90 == pytest.approx(128_000.04)
    assert any("sums of monthly quantiles" in n for n in CG.notes)
    with pytest.raises(AdapterError, match="DemandForecast"):
        to_calculation_graph(G.model_copy(update={"demand_forecasts": []}), C)


def test_sales_records_of_the_sku_only():
    assert len(CG.sales) == 12 and all(s.packs == 9000 for s in CG.sales)
    assert CG.sales[0].price_uzs == float(G.sales_records[0].price_ex_vat) and CG.sales[0].month == "2025-11"


def test_credit_line_and_payment_terms_to_finance():
    f = CG.finance
    assert f.budget_uzs == 9_000_000_000.0 and f.credit_rate_annual == pytest.approx(0.14)
    assert f.receivable_days == 30 and f.inventory_days == 30  # PaymentTerms.customer_days; config
    no_credit = to_calculation_graph(G.model_copy(update={"credit_lines": []}), C)
    assert no_credit.finance.budget_uzs is None and any("no CreditLine" in n for n in no_credit.notes)


def test_hedge_premium_comes_from_rule_catalog(rules):
    assert CG.finance.hedge_forward_premium == rules.hedge_forward_premium


def test_nominal_fx_is_latest_rate_before_horizon():
    assert CG.nominal_fx == {"EUR": 13_100.0, "USD": 12_000.0}
    late = o.FxRate(currency="USD", date=date(2026, 12, 1), rate_uzs=Decimal(99999), source="x", provenance=G.contracts[0].provenance)
    cg = to_calculation_graph(G.model_copy(update={"fx_rates": [*G.fx_rates, late]}), C)
    assert cg.nominal_fx["USD"] == 12_000.0
    with pytest.raises(AdapterError, match="FxRate"):
        to_calculation_graph(G.model_copy(update={"fx_rates": G.fx_rates[:1]}), C)


def test_config_scalars_and_calibration_target():
    assert (CG.horizon_start, CG.penalty_uzs_per_pack) == ("2026-11", 3000.0)
    assert CG.actual == C.calibration_actual and CG.actual is not None
    assert CG.client_id == "L0-demo-client"


def test_synthetic_provenance_is_flagged():
    assert CG.fixture_note and any("SYNTHETIC" in n for n in CG.notes)


def test_several_products_need_sku_id():
    with pytest.raises(AdapterError, match="sku_id"):
        to_calculation_graph(G, EngineConfig(horizon_start="2026-11"))
    with pytest.raises(AdapterError, match="not in the graph"):
        to_calculation_graph(G, C.model_copy(update={"sku_id": "NOPE"}))
