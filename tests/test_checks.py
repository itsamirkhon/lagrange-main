from decimal import Decimal
from lagrange_rx.ingest.checks import Observation, check_sum, check_stock_balance, check_periods, check_contradictions, check_duplicates


def obs(ref, value):
    return Observation(ref, value, [{'document_id': ref, 'page': 1, 'text_span': str(value)}])


def test_arithmetic_reports_sources_and_skips_incomplete_values():
    issue = check_sum(obs('total', 12), [obs('row1', 5), obs('row2', 6)])
    assert issue['issue_type'] == 'arithmetic'
    assert issue['affected'] == ['total', 'row1', 'row2']
    assert len(issue['evidence']) == 3
    assert check_sum(obs('total', 11), [obs('row1', 5), obs('row2', 6)]) is None
    assert check_sum(obs('total', 11), [obs('row1', None)]) is None
    assert check_sum(obs('total', 11), []) is None


def test_stock_balance_detects_wrong_closing():
    assert check_stock_balance(obs('closing', 80), obs('opening', 100), obs('in', 10), obs('out', 30)) is None
    assert check_stock_balance(obs('closing', 81), obs('opening', 100), obs('in', 10), obs('out', 30))['issue_type'] == 'arithmetic'


def test_monthly_gap_only_inside_explicit_range():
    issues = check_periods('sales', ['2026-04', '2026-06'], '2026-04', '2026-06')
    assert len(issues) == 1
    assert issues[0]['issue_type'] == 'missing'
    assert '2026-05' in issues[0]['detail']
    assert check_periods('sales', ['2026-04', '2026-05'], '2026-04', '2026-05') == []


def test_contradictions_only_compare_shared_nonempty_fields():
    issues = check_contradictions({'moq': obs('q.moq', 50), 'currency': obs('q.currency', None)},
                                  {'moq': obs('c.moq', 100), 'currency': obs('c.currency', 'USD')})
    assert len(issues) == 1
    assert issues[0]['affected'] == ['q.moq', 'c.moq']
    assert issues[0]['issue_type'] == 'contradiction'


def test_duplicate_requires_exact_hash_or_all_explicit_key_fields():
    docs = [ {'id':'a', 'hash':'same', 'number':'123', 'date':'2026-01-01'},
             {'id':'b', 'hash':'same', 'number':'different', 'date':'2026-01-01'},
             {'id':'c', 'hash':'third', 'number':'123', 'date':'2026-01-01'},
             {'id':'d', 'hash':'fourth', 'number':'123'}, ]
    pairs = {tuple(issue['affected']) for issue in check_duplicates(docs, ['number', 'date'])}
    assert pairs == {('a', 'b'), ('a', 'c')}
