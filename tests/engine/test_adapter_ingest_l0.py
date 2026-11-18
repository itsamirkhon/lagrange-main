"""Stage 6c: the engine on the ClientGraph that Ingest builds from the L0 documents (offline, no truth files)."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from eval.sim_human import SimHuman
from lagrange_rx.engine.adapter import (
    AdapterError, EngineConfig, default_engine_config, to_calculation_graph,
)
from lagrange_rx.engine.twin import legal_base, twin_baseline
from lagrange_rx.ingest.offline.build import build_draft
from lagrange_rx.ingest.offline.confirm import commit

L0 = Path(__file__).resolve().parents[2] / "sim" / "worlds" / "L0" / "documents"
SKU = "kardiozan-10mg-n30"


@pytest.fixture(scope="module")
def ontology_graph():
    now = datetime(2026, 11, 18, tzinfo=timezone.utc)
    draft = build_draft(L0, now=now)
    graph, _ = commit(draft, SimHuman().answer_all(draft.questions), now=now)
    return graph


@pytest.fixture(scope="module")
def cfg(ontology_graph):
    return default_engine_config(ontology_graph)


@pytest.fixture(scope="module")
def cg(ontology_graph, cfg):
    return to_calculation_graph(ontology_graph, cfg)


def test_default_config_is_derived_and_explicit(cfg):
    assert cfg.horizon_start == "2026-11" and cfg.sku_id == SKU
    assert cfg.split_freight_from_fees is True
    assert cfg.financing_cost_items == "as_recorded"  # D10 records interest, no CreditLine
    assert cfg.default_reliability is not None  # engine-only default, listed in notes
    assert cfg.calibration_actual is not None and cfg.calibration_actual.revenue_uzs == pytest.approx(16_657_365_645.0)


def test_only_routes_approved_for_the_sku(cg):
    assert {r.id.split("/")[0] for r in cg.routes} == {"KZ-AUTO-01", "TM-AUTO-02"}
    assert any("TAS-AIR-04" in n and "no production site" in n for n in cg.notes)


def test_product_ceilings_from_latest_registry(cg):
    assert cg.product.chun_uzs == 125_000.0 and cg.product.chchn_uzs == 150_000.0 and cg.product.reference_currency == "UZS"
    assert cg.nominal_fx["UZS"] == 1.0


def test_price_is_current_contract(cg):
    kc = {r.id.split("/")[0]: (r.kc, r.currency) for r in cg.routes}
    assert kc == {"KZ-AUTO-01": (7.22, "USD"), "TM-AUTO-02": (7.15, "USD")}


def test_freight_tariff_in_usd_converted_and_split_out_of_fees(cg, ontology_graph):
    r = next(r for r in cg.routes if r.id.startswith("KZ-AUTO-01"))
    assert r.freight_uzs == pytest.approx(0.12 * cg.nominal_fx["USD"]) and r.fixed_per_shipment_uzs == pytest.approx(1800 * cg.nominal_fx["USD"])
    entries = [e for e in ontology_graph.customs_entries if e.entry_ref in
               {s.customs_entry for s in ontology_graph.shipments if s.route == r.id}]
    landed = sum(float(e.legal_base_c_per_pack) * e.packs for e in entries) / sum(e.packs for e in entries)
    base = legal_base(r, sum(float(e.fx_rate_on_entry) * float(e.cip_value_fx) for e in entries) / sum(float(e.cip_value_fx) for e in entries))
    # the 15% legal base excludes the freight share that D7 lumps into fees
    assert base < landed - 2000 and r.fees_uzs >= 0
    assert any("inbound freight estimated from the carrier tariff" in n for n in cg.notes)


def test_no_split_keeps_fees_and_says_so(ontology_graph, cfg):
    g = to_calculation_graph(ontology_graph, cfg.model_copy(update={"split_freight_from_fees": False}))
    assert any("no freight split" in n for n in g.notes)
    assert next(r for r in g.routes if r.id.startswith("KZ-AUTO-01")).fees_uzs > 4000


def test_batch_capacity_defaults_are_listed(cg):
    r = next(r for r in cg.routes if r.id.startswith("KZ-AUTO-01"))
    assert (r.min_batch, r.max_batch) == (5000, 16500)  # contract MOQ, largest observed shipment
    notes = " | ".join(cg.notes)
    for needle in ("no DemandForecast", "capacity=", "max_shipments=", "min_batch=5000 taken from the supplier Contract MOQ",
                   "max_batch=16500 = largest observed shipment", "eligibility not stated", "activation cost not stated",
                   "no CreditLine", "receivable days unknown", "reliability", "EngineConfig"):
        assert needle in notes, needle


def test_demand_from_trailing_sales(cg):
    assert cg.demand.p50 == 149_250 and cg.demand.p10 == pytest.approx(149_250 * 0.85) and cg.demand.p90 == pytest.approx(149_250 * 1.15)


def test_scope_excludes_costs_shared_among_other_skus(cg):
    # shared costs are scoped to the other six SKUs, kardiozan carries only its direct costs: 2 148 641 646.83 UZS
    assert sum(i.annual_uzs for i in cg.cost_items) == pytest.approx(2_148_641_646.83, abs=0.01)


def test_shipments_carry_actual_cip_price(cg):
    assert all(s.kc is not None for s in cg.shipments) and 7.0 < cg.shipments[0].kc < 8.0


def test_twin_baseline_calibrates_on_documents(cg):
    cal = twin_baseline(cg)
    assert cal.twin_margin is not None and cal.gap_pp <= 0.5


def test_missing_reliability_still_errors_without_default(ontology_graph, cfg):
    with pytest.raises(AdapterError, match="reliability"):
        to_calculation_graph(ontology_graph, cfg.model_copy(update={"default_reliability": None}))


def test_demand_fallback_is_opt_in(ontology_graph, cfg):
    with pytest.raises(AdapterError, match="DemandForecast"):
        to_calculation_graph(ontology_graph, cfg.model_copy(update={"demand_from_sales": False}))
