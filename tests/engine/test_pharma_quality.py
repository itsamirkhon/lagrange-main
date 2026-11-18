"""Pharmaceutical quality controls: unsafe stock/transport must not be optimized."""
from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from lagrange_rx.engine.adapter import AdapterError, to_calculation_graph
from lagrange_rx.engine.pharma_quality import QualityBlocked, allocate_released_fefo
from lagrange_rx.fixtures.l0 import SKU, build_l0_config, build_l0_graph

G = build_l0_graph()
CFG = build_l0_config()


def _fefo_graph():
    p = next(p for p in G.products if p.sku_id == SKU)
    p = p.model_copy(update={"min_remaining_shelf_life_days": 30})
    base = date(2026, 11, 18)
    shipping = G.shipments[0].shipment_id
    source = G.products[0].provenance
    lots = [
        # Earliest released lot is too short-dated; latest lot is recalled.
        __import__("lagrange_rx").ontology.Lot(
            lot_number=name, product=SKU, production_date=date(2025, 1, 1),
            expiry_date=base + timedelta(days=expires), qty_packs=quantity,
            remaining_packs=quantity, shipment=shipping, quality_status=status,
            provenance=source)
        for name, expires, quantity, status in (
            ("A", 70, 40, "released"),
            ("B", 50, 20, "released"),
            ("C", 20, 100, "released"),
            ("D", 100, 100, "recalled"),
        )
    ]
    graph = G.model_copy(update={
        "products": [p if x.sku_id == SKU else x for x in G.products],
        "lots": lots,
    })
    return graph, tuple(x.lot_number for x in lots)

def test_fefo_released_only_and_nearest_eligible_expiry_first():
    graph, ids = _fefo_graph()
    got = allocate_released_fefo(graph, SKU, 50, date(2026, 11, 18))
    assert [x.lot_number for x in got.lines] == [ids[1], ids[0]]
    assert [x.packs for x in got.lines] == [20, 30]
    assert got.shortfall_packs == 0
    assert ids[2] in got.rejected_lots and ids[3] in got.rejected_lots


def test_fefo_expiry_and_recall_cannot_be_bypassed_by_demand():
    graph, ids = _fefo_graph()
    got = allocate_released_fefo(graph, SKU, 1000, date(2026, 11, 18))
    assert got.allocated_packs >= 60
    assert got.shortfall_packs > 0
    assert ids[2] not in [x.lot_number for x in got.lines]
    assert ids[3] not in [x.lot_number for x in got.lines]


def test_fefo_fails_when_minimum_shelf_life_unconfirmed():
    with pytest.raises(QualityBlocked, match="shelf life is unknown"):
        allocate_released_fefo(G, SKU, 20, date(2026, 11, 18))


def test_temperature_sensitive_product_excludes_unverified_routes():
    products = [p.model_copy(update={"storage_temperature_min_c": 2.0,
                                     "storage_temperature_max_c": 8.0}) if p.sku_id == SKU else p
                for p in G.products]
    graph = G.model_copy(update={"products": products})
    with pytest.raises(AdapterError, match="no eligible routes"):
        to_calculation_graph(graph, CFG)


def test_temperature_verified_route_can_enter_but_excursion_is_excluded():
    from lagrange_rx import ontology as o
    doc_id = "TEMP-001"
    doc = o.SourceDocument(document_id=doc_id, file_name="synthetic-logger.csv",
                           mime="text/csv", pages=1, uploaded_at=datetime.now(timezone.utc),
                           provenance=G.products[0].provenance)
    products = [p.model_copy(update={"storage_temperature_min_c": 2.0,
                                     "storage_temperature_max_c": 8.0}) if p.sku_id == SKU else p
                for p in G.products]
    first = G.routes[0]
    allowed = first.model_copy(update={"temperature_record_status": "verified",
                                      "temperature_record_document": doc_id,
                                      "observed_min_c": 2.5, "observed_max_c": 7.8})
    routes = [allowed if r.route_id == first.route_id else r for r in G.routes]
    graph = G.model_copy(update={"products": products, "routes": routes, "source_documents": [doc]})
    result = to_calculation_graph(graph, CFG)
    assert any(r.id == first.route_id for r in result.routes)
    bad = allowed.model_copy(update={"observed_max_c": 10.0})
    graph_bad = graph.model_copy(update={"routes": [bad if r.route_id == first.route_id else r for r in routes]})
    with pytest.raises(AdapterError, match="no eligible routes"):
        to_calculation_graph(graph_bad, CFG)


def test_temperature_range_is_consistent():
    with pytest.raises(ValidationError):
        type(G.products[0]).model_validate({
            **G.products[0].model_dump(),
            "storage_temperature_min_c": 8,
            "storage_temperature_max_c": 2,
        })
