"""An empty plan may not pass the 10,000-scenario legality evaluation."""
from types import SimpleNamespace

from eval.engine_eval import metric_legality


def test_legality_for_empty_plan_is_not_applicable_and_fails():
    empty = SimpleNamespace(lines=[])
    status = metric_legality(None, None, empty, None, None, {'legality_scenarios': 10000})
    assert status['pass'] is False
    assert status['status'] == 'not_evaluated_no_supply'
    assert status['legal_rate'] is None
    assert status['scenarios'] == 0
