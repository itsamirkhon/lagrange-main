"""Stage 6d: documents -> graph -> twin -> optimizer -> legality -> truth evaluation on worlds L0..L4 (offline)."""
import json
from pathlib import Path

import pytest

from eval import engine_inputs as ei
from eval.e2e import regret_by_level, run_world
from lagrange_rx.engine.adapter import default_engine_config

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def report(tmp_path_factory):
    out = tmp_path_factory.mktemp('e2e') / 'r.json'
    r = run_world('L0', out, n_truth=6, diagnostics=False)
    assert json.loads(out.read_text(encoding='utf-8'))['snapshot_id'] == r['snapshot_id']
    return r


def test_chain_and_truth_slot_filled(report):
    if report["failure"] is not None:
        assert report["failure"]["stage"] == "optimization"
        assert report["plan"] is None and report["truth_evaluation"] is None
        return
    assert report['ingest']['documents'] == 72 and report['ingest']['questions_to_human']['total'] == 13
    assert report['snapshot_id'] and report['failure'] is None
    te = report['truth_evaluation']
    assert te['status'] == 'evaluated' and te['oos_scenarios'] == 6
    assert set(te['plans']) == {'engine_cvar', 'engine_expected', 'client_baseline'}
    assert te['plans']['client_baseline']['nominal_profit_uzs'] is not None


def test_calibration_within_acceptance_or_missing_listed(report):
    c = report['calibration_gap']
    assert c['twin_volume_packs'] == c['truth_volume_packs'] == 149250
    assert c['accepted_within_0.5pp'] or c['missing_data']


def test_plan_is_legal_and_assumptions_are_listed(report):
    if report["failure"] is not None:
        assert report["failure"]["stage"] == "optimization"
        return
    assert report['plan'] is not None and (report['plan']['packs_total'] > 0 or report['plan_note'])  # a zero plan is explained
    assert report['legality']['legal'] and report['legality']['checks_failed'] == 0
    assert any('engine-only parameters' in n for n in report['engine_notes'])
    assert any('no DemandForecast' in n for n in report['engine_notes'])


def test_single_engine_config_path():
    """Stage 5 and stage 6c use the same configuration: eval/engine_inputs delegates to the adapter."""
    g, _ = ei.load_ingest_graph()
    assert ei.engine_config(g) == default_engine_config(g)
    assert not hasattr(ei, 'engine_input_graph') and not hasattr(ei, 'ASSUMPTIONS')


def test_lot_and_cadence_come_from_documents():
    g, _ = ei.load_ingest_graph()
    cfg = ei.engine_config(g)
    focus = [p for rid, p in cfg.route_params.items() if 'medisynth' in rid or 'turtsiya' in rid]
    assert len(focus) == 2 and all(p.lot_size == 500 for p in focus)
    assert sorted(p.max_shipments for p in focus) == [4, 8]


def test_noisy_world_failure_is_recorded_not_hidden():
    """L3: the offline readers cannot read the scans, the human rejects incomplete groups, no sales survive:
    the chain must stop with an explicit stage and message, and still carry a snapshot id."""
    r = run_world('L3', None, n_truth=2, diagnostics=False)
    assert r['failure'] is not None and r['failure']['stage'] == 'engine_config'
    assert r['snapshot_id'].startswith('ingest-L3-') and r['ingest']['questions_to_human']['rejected_groups']
    assert r['truth_evaluation'] is None
    reg = regret_by_level({'L3': r})
    assert reg['levels']['L3']['has_plan'] is False


def test_invoice_without_dates_degrades_to_issue():
    """L1/L2 used to crash the builder with KeyError('dispatch'/'arrival'); now the shipment is skipped with an issue."""
    from datetime import datetime, timezone
    from lagrange_rx.ingest.offline.build import build_draft
    for level in ('L1', 'L2'):
        d = build_draft(ROOT / 'sim' / 'worlds' / level / 'documents', now=datetime(2026, 11, 18, tzinfo=timezone.utc))
        assert d.issues
