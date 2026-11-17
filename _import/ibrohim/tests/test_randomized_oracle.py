"""Vary cost structure and risk while preserving independent enumeration."""

from copy import deepcopy
from random import Random

import pytest

from lagrange_rx.models import PharmaCase
from lagrange_rx.solver import optimize
from tests.test_bruteforce import brute_force, tiny_case


@pytest.mark.parametrize('seed', list(range(16)))
def test_random_small_cases_agree_with_oracle(seed):
    random = Random(seed)
    raw = deepcopy(tiny_case().model_dump())
    for route in raw['options']:
        route['factory_cost_uzs'] += random.randint(-2000, 2000)
        route['below_line_cost_uzs'] += random.randint(-1500, 1500)
        route['freight_uzs'] += random.randint(0, 1000)
        route['shipment_fixed_uzs'] += random.randint(0, 250)
    raw['assumed_registered_wholesale_cap_uzs'] -= random.randint(0, 8000)
    raw['scenarios'][0]['probability'] = random.choice([0.2, 0.4, 0.6, 0.8])
    raw['scenarios'][1]['probability'] = 1 - raw['scenarios'][0]['probability']
    case = PharmaCase.model_validate(raw)
    risk = random.choice([0, 0.25, 0.5, 0.75, 1])
    oracle = brute_force(case, risk)
    result = optimize(case, risk)
    assert result.status == 'optimal', result.solver_message
    assert result.optimized.objective_uzs == pytest.approx(oracle, abs=0.05)
    assert result.relative_optimality_gap < 0.002
