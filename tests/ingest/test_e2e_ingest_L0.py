"""End-to-end on the real L0 world: documents -> questions -> simulated human -> graph -> P&L vs calibration."""
import json
from pathlib import Path

import pytest

from eval.e2e_ingest_L0 import run

L0 = Path(__file__).resolve().parents[2] / 'sim' / 'worlds' / 'L0' / 'documents'


@pytest.fixture(scope='module')
def result(tmp_path_factory):
    out = tmp_path_factory.mktemp('e2e') / 'graph.json'
    res = run(L0, out)
    assert json.loads(out.read_text(encoding='utf-8'))['snapshot_id'] == res['snapshot_id']
    return res


def test_graph_is_confirmed_and_complete(result):
    assert result['documents'] == 72
    assert result['counts']['shipments'] == 44 and result['counts']['sales_records'] == 3694
    assert result['dropped'] == [] and result['rejected_groups'] == []


def test_human_load_is_counted_and_logged(result):
    assert result['human_load_answers'] == len(result['question_log']) == result['questions_total'] == 13
    assert result['answers_by_kind'] == {'contract_effective_from': 2, 'confirm_group': 9, 'cost_attribution': 2}


def test_pnl_margin_matches_calibration_report_within_half_a_point(result):
    check = result['pnl_check']
    assert check['months_blocked'] == {}
    assert check['volume_packs'] == 149250
    assert abs(check['gap']['margin_pp']) <= 0.5
    assert abs(check['gap']['revenue_uzs']) < 1


def test_what_could_not_be_extracted_is_reported(result):
    what = ' '.join(x['what'] for x in result['not_extracted'])
    assert 'image documents' in what and 'CreditLine' in what
