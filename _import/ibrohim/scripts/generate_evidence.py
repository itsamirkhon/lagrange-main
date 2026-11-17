"""Deterministic proof package; uses computed results, never hardcoded "wins"."""

from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import numpy
import scipy

from lagrange_rx import __version__
from lagrange_rx.demo_data import demo_case
from lagrange_rx.economics import validate_allocation
from lagrange_rx.models import PharmaCase
from lagrange_rx.solver import optimize


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_case(raw: dict, *, risk: float = 0.35) -> dict:
    case = PharmaCase.model_validate(raw)
    result = optimize(case, risk)
    if result.optimized:
        issues = validate_allocation(case, result.optimized.allocations, result.optimized.wholesale_price_uzs)
        if issues:
            raise AssertionError(f'Independent audit failed: {issues}')
    return {'input': case.model_dump(mode='json'), 'output': result.model_dump(mode='json')}


def main() -> None:
    raw = demo_case().model_dump(mode='json')
    cases = {'default': run_case(deepcopy(raw))}
    shock = deepcopy(raw)
    next(s for s in shock['scenarios'] if s['id'] == 'fx_rise')['fx_change_pct'] = 18
    cases['fx_18_pct'] = run_case(shock)
    route_down = deepcopy(raw)
    next(o for o in route_down['options'] if o['id'] == 'regional_pool')['eligible'] = False
    cases['pool_outage'] = run_case(route_down)
    impossible = deepcopy(raw)
    impossible['working_capital_budget_uzs'] = 1_000_000_000
    cases['cash_infeasible'] = run_case(impossible)
    assert cases['cash_infeasible']['output']['status'] == 'infeasible'

    tracked = [
        ROOT / 'src/lagrange_rx/models.py',
        ROOT / 'src/lagrange_rx/economics.py',
        ROOT / 'src/lagrange_rx/solver.py',
        ROOT / 'src/lagrange_rx/demo_data.py',
        ROOT / 'web/app.js',
    ]
    report = {
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'prototype_version': __version__,
        'python': sys.version.split()[0],
        'numpy': numpy.__version__,
        'scipy': scipy.__version__,
        'data_classification': 'synthetic',
        'legal_certification': False,
        'real_world_profit_verified': False,
        'source_sha256': {str(p.relative_to(ROOT)): sha256(p) for p in tracked},
        'cases': cases,
    }
    out = ROOT / 'evidence' / 'verification_report.json'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (ROOT / 'evidence' / 'sample_result.json').write_text(
        json.dumps(cases['default']['output'], ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
    )
    for name, entry in cases.items():
        res = entry['output']
        profit = res['optimized']['expected_profit_uzs'] / 1e6 if res['optimized'] else None
        print(f"{name:17} | {res['status']:10} | expected profit (million UZS) = {profit}")
    print(f"Wrote {out}")


if __name__ == '__main__':
    main()
