"""End-to-end Ingest on world L0: documents -> questions -> simulated human -> confirmed ClientGraph -> P&L check.

Usage: python eval/e2e_ingest_L0.py [--documents sim/worlds/L0/documents] [--output eval/ingest_graph_L0.json]

Ingest (product code) sees only the documents folder. The simulated human and the final comparison read the
simulation truth; this script is eval code, so that is allowed. No twin calibration or optimisation here:
the P&L is computed with `lagrange_rx.pnl` on the graph and compared with `calibration_report.json`; any gap
and the exact missing data are reported as they are.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from eval.sim_human import SimHuman, TRUTH
from lagrange_rx.ingest.offline.build import build_draft
from lagrange_rx.ingest.offline.confirm import commit
from lagrange_rx.pnl import calculate_pnl

ROOT = Path(__file__).resolve().parents[1]


def months(window: str) -> list[str]:
    start, end = window.split('..')
    y, m = map(int, start.split('-'))
    out = []
    while f'{y:04d}-{m:02d}' <= end:
        out.append(f'{y:04d}-{m:02d}')
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def focus_product(graph) -> str:
    """The SKU the client's price registry is about (product code rule: has registry records)."""
    ids = {r.product for r in graph.registry_records}
    if len(ids) != 1:
        raise ValueError(f'expected exactly one registered SKU, found {sorted(ids)}')
    return next(iter(ids))


def pnl_check(graph, report: dict) -> dict:
    window = months(report['calibration_window'])
    sku = focus_product(graph)
    categories = {c.category for c in graph.cost_items if c.layer == 'below_line'}
    revenue = cost = Decimal(0)
    volume = 0
    missing = {}
    for period in window:
        result = calculate_pnl(graph, sku, period, categories)
        volume += sum(s.qty_packs for s in graph.sales_records if s.product == sku and s.period == period)
        if result.missing:
            missing[period] = list(result.missing)
            continue
        revenue += result.revenue
        cost += result.cost
    opex = sum((c.amount_uzs for c in graph.cost_items if c.period in window and c.allocation == 'direct' and c.product == sku), Decimal(0))
    out = {'window': report['calibration_window'], 'product': sku, 'months_computed': len(window) - len(missing),
           'months_blocked': missing, 'volume_packs': volume, 'revenue_ex_vat_uzs': str(revenue.quantize(Decimal('0.01'))),
           'cogs_ex_vat_uzs': str((cost - opex).quantize(Decimal('0.01'))), 'operating_costs_uzs': str(opex.quantize(Decimal('0.01')))}
    if missing or not revenue:
        out['margin'] = None
    else:
        out['margin'] = float((revenue - cost) / revenue)
    truth = {'margin': report['net_margin_ex_vat'], 'volume_packs': report['annual_sales_packs'], 'revenue_ex_vat_uzs': report['revenue_ex_vat_uzs'],
             'cogs_ex_vat_uzs': report['cogs_ex_vat_uzs'], 'operating_costs_uzs': report['operating_costs_uzs']}
    out['truth'] = truth
    out['gap'] = None if out['margin'] is None else {
        'margin_pp': round((out['margin'] - truth['margin']) * 100, 4),
        'volume_packs': volume - truth['volume_packs'],
        'revenue_uzs': float(revenue) - truth['revenue_ex_vat_uzs'],
        'cogs_uzs': float(cost - opex) - truth['cogs_ex_vat_uzs'],
        'operating_costs_uzs': float(opex) - truth['operating_costs_uzs']}
    return out


def run(documents: Path, output: Path) -> dict:
    now = datetime(2026, 11, 18, tzinfo=timezone.utc)  # fixed: the snapshot must not depend on the clock
    draft = build_draft(documents, now=now)
    human = SimHuman()
    answers = human.answer_all(draft.questions)
    graph, commit_report = commit(draft, answers, now=now)
    graph.for_calculation()
    calibration = json.loads((TRUTH / 'calibration_report.json').read_text(encoding='utf-8'))
    check = pnl_check(graph, calibration)
    body = graph.model_dump(mode='json')
    snapshot = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    result = {
        'snapshot_id': f'ingest-L0-{snapshot}', 'world': 'L0', 'documents': len(draft.source_documents),
        'questions_total': len(draft.questions), 'human_load_answers': human.load,
        'answers_by_kind': dict(Counter(e['kind'] for e in human.log)),
        'rejected_groups': [e['question_id'] for e in human.log if e['answer'].get('decision') == 'reject'],
        'counts': commit_report['counts'], 'dropped': commit_report['dropped'], 'links': draft.links,
        'issues': dict(Counter(f"{i['issue_type']}/{i['severity']}" for i in draft.issues)),
        'issue_list': draft.issues,
        'not_extracted': draft.not_extracted,
        'pnl_check': check,
        'question_log': human.log,
        'graph': body,
    }
    output.write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--documents', type=Path, default=ROOT / 'sim' / 'worlds' / 'L0' / 'documents')
    parser.add_argument('--output', type=Path, default=ROOT / 'eval' / 'ingest_graph_L0.json')
    args = parser.parse_args()
    result = run(args.documents, args.output)
    summary = {k: v for k, v in result.items() if k not in ('graph', 'question_log', 'issue_list')}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
