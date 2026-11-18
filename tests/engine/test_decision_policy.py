"""Non-vacuous business labels on plans with no supply."""
from types import SimpleNamespace

from lagrange_rx.engine.decision_policy import DEFAULT_SERVICE_LEVEL, decision_status, has_supply


def test_business_policy_is_explicit_assumption():
    assert DEFAULT_SERVICE_LEVEL == 0.90


def test_empty_supply_never_passes_compliance():
    empty = SimpleNamespace(lines=[])
    for plan in (None, empty):
        assert not has_supply(plan)
        status = decision_status(plan, [])
        assert status['legal'] is None
        assert status['legal_status'] == 'not_evaluated'
        assert status['planned_packs'] == 0
    assert decision_status(None, [])['supply_status'] == 'infeasible'
    assert decision_status(empty, [])['supply_status'] == 'empty'


def test_nonempty_supply_needs_checks():
    plan = SimpleNamespace(lines=[SimpleNamespace(packs=150000)])
    assert has_supply(plan)
    assert decision_status(plan, [])['legal'] is None
    good = decision_status(plan, [SimpleNamespace(passed=True)])
    bad = decision_status(plan, [SimpleNamespace(passed=False)])
    assert good['legal_status'] == 'passed' and good['legal'] is True
    assert bad['legal_status'] == 'failed' and bad['legal'] is False


def test_independent_expected_fill_audit_and_failure():
    import pytest
    from lagrange_rx.engine.decision_policy import independently_check_service, ServiceLevelAuditError

    scenarios = [SimpleNamespace(id='a', weight=0.5, demand=100),
                 SimpleNamespace(id='b', weight=0.5, demand=200)]
    outcomes = [SimpleNamespace(scenario_id='a', sold=100),
                SimpleNamespace(scenario_id='b', sold=140)]
    assert independently_check_service(outcomes, scenarios, .80) == pytest.approx(.8)
    with pytest.raises(ServiceLevelAuditError):
        independently_check_service(outcomes, scenarios, .95)
    with pytest.raises(ValueError, match='ordering'):
        independently_check_service(list(reversed(outcomes)), scenarios, .8)
