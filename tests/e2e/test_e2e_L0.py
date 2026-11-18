"""Stage 6c: documents -> graph -> twin -> optimizer -> legality on world L0 (offline)."""
import json
from pathlib import Path

import pytest

from eval.e2e_L0 import run

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def report(tmp_path_factory):
    out = tmp_path_factory.mktemp('e2e') / 'r.json'
    r = run(ROOT / 'sim' / 'worlds' / 'L0' / 'documents', ROOT / 'eval' / 'ingest_graph_L0.json', out)
    assert json.loads(out.read_text(encoding='utf-8'))['snapshot_id'] == r['snapshot_id']
    return r


def test_chain_and_pending_slot(report):
    assert report['documents'] == 72 and report['questions_to_human']['total'] == 13
    assert report['questions_to_human']['consistent_with_live_run']
    assert report['truth_evaluation'] == 'pending stage 5' and report['snapshot_id']


def test_calibration_within_acceptance_or_missing_listed(report):
    c = report['calibration_gap']
    assert c['twin_volume_packs'] == c['truth_volume_packs'] == 149250
    assert c['accepted_within_0.5pp'] or c['missing_data']


def test_plan_is_legal_and_assumptions_are_listed(report):
    assert report['plan'] is not None and (report['plan']['packs_total'] > 0 or report['plan_note'])  # a zero plan is explained
    assert report['legality']['legal'] and report['legality']['checks_failed'] == 0
    assert any('engine-only parameters' in n for n in report['engine_notes'])
    assert any('no DemandForecast' in n for n in report['engine_notes'])
