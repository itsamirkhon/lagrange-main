import pytest

from lagrange_rx.engine.inputs import CalculationGraph, require_calculation_graph
from lagrange_rx.engine.rules import RulesError, load_rules


def test_rules_load_and_values():
    r = load_rules()
    assert r.max_markup == 0.15 and r.vat == 0.12 and r.retail_markup == 0.20
    assert r.threshold_scheduled == 0.03 and r.threshold_immediate == 0.05
    assert r.lag_days == 30 and r.index_dates == ((1, 1), (7, 1))
    assert "UZ-PF13-WHOLESALE-MARKUP" in r.rule_ids and len(r.sha256) == 64


def test_rules_missing_rule_rejected(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text("version: x\nrules: {}\n")
    with pytest.raises(RulesError):
        load_rules(p)


def test_engine_rejects_raw_graph():
    with pytest.raises(TypeError):
        require_calculation_graph(object())
