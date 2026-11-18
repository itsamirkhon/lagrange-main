"""API/business policy for the single-SKU demonstration.

Do not confuse the explicit default with a law, patient safety threshold, or
empirical service target. The unconstrained mathematical model is useful for
research, but *not* a substitute for supply planning.
"""
from __future__ import annotations
from typing import Any, Sequence

DEFAULT_SERVICE_LEVEL = 0.90


def has_supply(plan: Any) -> bool:
    """True only if the proposed plan actually procures some packs."""
    return plan is not None and sum(line.packs for line in plan.lines) > 0


def decision_status(plan: Any, checks: Sequence[Any]) -> dict[str, Any]:
    """A truthful, non-vacuous decision label for presentation and APIs.

    Does not certify legal compliance. An infeasible decision with zero checks
    is *not* a 100% compliant business plan.
    """
    planned = sum(line.packs for line in plan.lines) if plan is not None else 0
    status = "infeasible" if plan is None else "empty" if planned <= 0 else "ready"
    if status != "ready" or not checks:
        legal_status = "not_evaluated"
        legal = None
    else:
        legal = all(check.passed for check in checks)
        legal_status = "passed" if legal else "failed"
    return {
        "supply_status": status,
        "planned_packs": planned,
        "legal_status": legal_status,
        "legal": legal,
    }


class ServiceLevelAuditError(RuntimeError):
    """The independent physical outcome disagrees with a promised fill level."""
    def __init__(self, observed: float, minimum: float):
        self.observed = observed
        self.minimum = minimum
        super().__init__(f'Expected fill {observed:.5f} is below required {minimum:.5f}')


def independently_check_service(outcomes: Sequence[Any], scenarios: Sequence[Any], minimum: float | None) -> float:
    """Compute weighted physical fill from recourse, not MILP internal q variables.

    A missing/duplicate scenario outcome is a hard inconsistency. A zero-demand
    portfolio cannot be called a successful supply decision.
    """
    if len(outcomes) != len(scenarios):
        raise ValueError('Scenario and outcome count mismatch')
    expected_demand = 0.0
    expected_sales = 0.0
    for scenario, outcome in zip(scenarios, outcomes):
        if scenario.id != outcome.scenario_id:
            raise ValueError('Scenario and outcome ordering mismatch')
        expected_demand += scenario.weight * scenario.demand
        expected_sales += scenario.weight * outcome.sold
    if expected_demand <= 0:
        raise ValueError('Demand must be positive for service level audit')
    rate = expected_sales / expected_demand
    if minimum is not None and rate + 1e-6 < minimum:
        raise ServiceLevelAuditError(rate, minimum)
    return rate
