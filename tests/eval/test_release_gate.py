"""Release metadata must not certify stale or vacuous reports."""
import importlib.util
from pathlib import Path

MODULE = Path(__file__).resolve().parents[2] / 'scripts' / 'verify_release.py'
spec = importlib.util.spec_from_file_location('verify_release', MODULE)
verify_release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify_release)


def _report():
    return {
        'code_commit': 'abc', 'all_pass': True,
        'plan': {'lines': [{'packs': 120000}]},
        'metrics': {
            'legality': {'pass': True, 'legal_rate': 1, 'scenarios': 10000},
            'calibration': {'pass': True}, 'uplift': {'pass': True},
        }
    }


def test_rejects_stale_report_and_empty_plan():
    d = _report()
    assert verify_release.verify(Path('.'), d, 'abc') == []
    assert any('code_commit' in p for p in verify_release.verify(Path('.'), d, 'def'))
    d['plan'] = {'lines': []}
    assert any('no nonzero' in p for p in verify_release.verify(Path('.'), d, 'abc'))


def test_rejects_vacuous_legality_and_bad_uplift():
    d = _report()
    d['metrics']['legality']['scenarios'] = 0
    d['metrics']['uplift']['pass'] = False
    blockers = verify_release.verify(Path('.'), d, 'abc')
    assert any('legality' in p for p in blockers)
    assert any('uplift' in p for p in blockers)
