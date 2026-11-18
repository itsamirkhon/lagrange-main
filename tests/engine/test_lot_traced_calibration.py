"""Historical P&L must be grounded in sold lots, never averaged purchases."""
import pytest

from eval import engine_inputs
from lagrange_rx.engine.adapter import default_engine_config, to_calculation_graph
from lagrange_rx.engine.twin import TwinError, _historical_cost, last_12_months, last_12_months_shipments, twin_baseline


def test_documented_l0_lots_calibrate_within_preregistered_threshold():
    ontology, _ = engine_inputs.load_ingest_graph()
    graph = to_calculation_graph(ontology, default_engine_config(ontology))
    sales = last_12_months(graph, graph.sales)
    assert sales and all(s.unit_landed_cost_uzs is not None for s in sales)
    result = twin_baseline(graph)
    assert result.twin_margin is not None
    assert result.actual_margin is not None
    assert result.gap_pp <= 0.1, (
        f"lot-traced historical P&L still mismatches by {result.gap_pp:.6f} pp; "
        "fix source-derived cost allocation without changing the threshold"
    )


def test_partial_lot_cost_data_fails_closed():
    ontology, _ = engine_inputs.load_ingest_graph()
    graph = to_calculation_graph(ontology, default_engine_config(ontology))
    sales = last_12_months(graph, graph.sales)
    partial = [sales[0].model_copy(update={"unit_landed_cost_uzs": None}), *sales[1:]]
    with pytest.raises(TwinError, match="coverage is incomplete"):
        _historical_cost(graph, last_12_months_shipments(graph), partial)
