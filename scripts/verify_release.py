#!/usr/bin/env python3
"""Fail closed on stale or materially unsuccessful evaluation reports.

This is a metadata/consistency gate, not proof of legal or business validity.
"""
from __future__ import annotations
import argparse
import json
import subprocess
from pathlib import Path


def verify(root: Path, report: dict, head: str) -> list[str]:
    blockers = []
    if report.get('code_commit') != head:
        blockers.append(f'report code_commit {report.get("code_commit")} != HEAD {head}')
    if report.get('all_pass') is not True:
        blockers.append('all_pass is not true')
    plan = report.get('plan')
    if plan is None or sum(int(l.get('packs', 0)) for l in plan.get('lines', [])) <= 0:
        blockers.append('report contains no nonzero procurement plan')
    leg = report.get('metrics', {}).get('legality', {})
    if leg.get('pass') is not True or leg.get('legal_rate') != 1 or leg.get('scenarios', 0) <= 0:
        blockers.append('nonvacuous legality assessment has not passed')
    cal = report.get('metrics', {}).get('calibration', {})
    if cal.get('pass') is not True:
        blockers.append('twin calibration target has not passed')
    uplift = report.get('metrics', {}).get('uplift', {})
    if uplift.get('pass') is not True:
        blockers.append('profitability uplift target has not passed')
    return blockers


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument('--report', default='eval/engine_report.json')
    ap.add_argument('--strict', action='store_true', help='exit nonzero if any requirement is unmet')
    args = ap.parse_args()
    root = args.repo.resolve()
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    try:
        report = json.loads((root / args.report).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        print('BLOCKED: cannot load report:', exc)
        return 2
    blockers = verify(root, report, head)
    if blockers:
        print('NOT PRESENTATION-READY:')
        for item in blockers:
            print(' -', item)
    else:
        print('Metadata consistency gates passed (not an independent legal or commercial audit).')
    return 2 if blockers and args.strict else 0


if __name__ == '__main__':
    raise SystemExit(main())
