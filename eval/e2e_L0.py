"""Stage 6c end-to-end on world L0: documents -> Ingest -> graph -> twin -> calibration gap -> optimizer -> legality.

Usage: python eval/e2e_L0.py [--output eval/e2e_report_L0.json]

Product code sees only the documents (the simulated human and this eval read the simulation truth, which eval/ may).
The calibration target of the twin is derived from the documents themselves (lagrange_rx.pnl, via
`default_engine_config`); simulation truth is used here only to measure the gap. Honest numbers only: the report
lists every engine-only parameter / substituted default (`engine_notes`) and the exact missing data if the gap
exceeds the acceptance (0.5 pp). The TruthEvaluator (stage 5) is not part of this script.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from eval.sim_human import SimHuman, TRUTH
from lagrange_rx.engine.adapter import default_engine_config, to_calculation_graph
from lagrange_rx.engine.optimize import OptimizerConfig
from lagrange_rx.engine.scenarios import ScenarioConfig
from lagrange_rx.engine.service import run_optimize
from lagrange_rx.engine.twin import last_12_months, legal_base, twin_baseline
from lagrange_rx.ingest.offline.build import build_draft
from lagrange_rx.ingest.offline.confirm import commit

ROOT = Path(__file__).resolve().parents[1]
ACCEPT_PP = 0.5  # task acceptance; eval/thresholds.json pre-registers a stricter 0.1 pp for the final evaluation


def build_graph(documents: Path):
    now = datetime(2026, 11, 18, tzinfo=timezone.utc)
    draft = build_draft(documents, now=now)
    human = SimHuman()
    graph, report = commit(draft, human.answer_all(draft.questions), now=now)
    return draft, human, graph, report


def run(documents: Path, ingest_log: Path, output: Path | None = None) -> dict:
    draft, human, graph, commit_report = build_graph(documents)
    log = json.loads(ingest_log.read_text(encoding='utf-8'))
    truth = json.loads((TRUTH / 'calibration_report.json').read_text(encoding='utf-8'))

    cfg = default_engine_config(graph)
    cg = to_calculation_graph(graph, cfg)
    cal = twin_baseline(cg)
    sales = last_12_months(cg, cg.sales)
    volume = sum(s.packs for s in sales)
    gap_truth_pp = None if cal.twin_margin is None else (cal.twin_margin - truth['net_margin_ex_vat']) * 100
    missing = list(cal.missing_data)
    if gap_truth_pp is not None and abs(gap_truth_pp) > ACCEPT_PP:
        missing.append('see calibration_gap: data not captured by the twin')
    calibration = {
        'twin_margin': cal.twin_margin, 'document_pnl_margin': cal.actual_margin, 'calibrated_vs_documents': cal.calibrated,
        'truth_margin': truth['net_margin_ex_vat'], 'gap_vs_truth_pp': gap_truth_pp,
        'twin_volume_packs': volume, 'truth_volume_packs': truth['annual_sales_packs'],
        'accepted_within_0.5pp': gap_truth_pp is not None and abs(gap_truth_pp) <= ACCEPT_PP,
        'preregistered_threshold_pp': json.loads((ROOT / 'eval' / 'thresholds.json').read_text())['calibration_max_gap_pp'],
        'missing_data': missing,
    }

    res = run_optimize(cg, scen_cfg=ScenarioConfig(), opt_cfg=OptimizerConfig(risk_mode='cvar'))
    plan = None
    if res.plan is not None:
        plan = {'plan_id': res.plan.plan_id, 'horizon': res.plan.horizon, 'fx_hedge_share': res.plan.fx_hedge_share,
                'packs_total': sum(l.packs for l in res.plan.lines), 'packs_by_route': res.plan.packs_by_route(),
                'shipments': sum(l.shipments for l in res.plan.lines),
                'solve': {'status': res.solve.status, 'proven_optimal': res.solve.proven_optimal, 'seconds': res.solve.seconds},
                'metrics_uzs': None if res.metrics is None else {
                    'expected': res.metrics.expected_uzs, 'cvar95': res.metrics.cvar_uzs, 'worst': res.metrics.worst_uzs,
                    'p_loss': res.metrics.p_loss}}
    failed = [c for c in res.checks if not c.passed]
    # context: the same model asked for the best expected profit (no tail protection)
    alt = run_optimize(cg, scen_cfg=ScenarioConfig(), opt_cfg=OptimizerConfig(risk_mode='expected'))
    expected_plan = None if alt.plan is None else {
        'packs_by_route': alt.plan.packs_by_route(), 'metrics_uzs': {
            'expected': alt.metrics.expected_uzs, 'cvar95': alt.metrics.cvar_uzs, 'worst': alt.metrics.worst_uzs,
            'p_loss': alt.metrics.p_loss}, 'legal': bool(alt.checks) and all(c.passed for c in alt.checks)}
    # effect of D7's fees/freight lump on the 15% rule (legal base per pack at nominal FX, with and without the split)
    nosplit = to_calculation_graph(graph, cfg.model_copy(update={'split_freight_from_fees': False}))
    freight_effect = {r.id: {'legal_base_split': legal_base(r, cg.nominal_fx[r.currency]),
                             'legal_base_unsplit': legal_base(u, cg.nominal_fx[u.currency]),
                             'max_price_overstated_by_uzs_per_pack': 1.15 * (legal_base(u, cg.nominal_fx[u.currency])
                                                                           - legal_base(r, cg.nominal_fx[r.currency]))}
                      for r, u in zip(cg.routes, nosplit.routes)}
    report = {
        'world': 'L0',
        'documents': log['documents'],
        'questions_to_human': {'total': log['questions_total'], 'by_kind': log['answers_by_kind'],
                               'consistent_with_live_run': log['questions_total'] == len(draft.questions) == len(human.log)},
        'graph': {'counts': {k: v for k, v in commit_report['counts'].items() if v}, 'focus_sku': cfg.sku_id,
                  'horizon_start': cfg.horizon_start, 'routes_in_engine': [r.id for r in cg.routes],
                  'not_extracted': [x['what'] for x in draft.not_extracted]},
        'engine_config': cfg.model_dump(mode='json'),
        'engine_notes': list(cg.notes),
        'twin_margin': cal.twin_margin,
        'calibration_gap': calibration,
        'plan': plan,
        'plan_note': ('cvar optimum is to import nothing: loss = fixed below-line costs only' if plan and plan['packs_total'] == 0 else None),
        'plan_expected_mode': expected_plan,
        'fifteen_percent_rule_freight_effect': freight_effect,
        'plan_withheld_reason': None if res.plan is not None else 'no feasible plan',
        'legality': {'checks_total': len(res.checks), 'checks_failed': len(failed), 'legal': bool(res.checks) and not failed},
        'warnings': list(res.warnings),
        'snapshot_id': res.snapshot.snapshot_id,
        'truth_evaluation': 'pending stage 5',  # filled by eval/engine_eval.py TruthEvaluator
    }
    report['report_sha256'] = hashlib.sha256(json.dumps(report, sort_keys=True, default=str).encode()).hexdigest()[:16]
    if output is not None:
        output.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--documents', type=Path, default=ROOT / 'sim' / 'worlds' / 'L0' / 'documents')
    parser.add_argument('--ingest-log', type=Path, default=ROOT / 'eval' / 'ingest_graph_L0.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'eval' / 'e2e_report_L0.json')
    a = parser.parse_args()
    r = run(a.documents, a.ingest_log, a.output)
    print(json.dumps({k: r[k] for k in ('documents', 'questions_to_human', 'twin_margin', 'calibration_gap', 'plan', 'legality', 'snapshot_id')},
                     ensure_ascii=False, indent=1, default=str))


if __name__ == '__main__':
    main()
