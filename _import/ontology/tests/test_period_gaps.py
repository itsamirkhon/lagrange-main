from lagrange_rx.ingest.parse import Table
from lagrange_rx.ingest.period_gaps import check_period_gaps


def test_internal_missing_month_reports_neighbor_evidence():
    table = Table(2, [['Месяц', 'Регион', 'Количество'], ['2026-04', 'А', 5], ['2026-06', 'А', 7]])
    issue, = check_period_gaps(table, 'abc')
    assert issue['issue_type'] == 'missing'
    assert '2026-05' in issue['detail']
    assert issue['affected'] == ['abc']
    assert [e['quote'] for e in issue['evidence']] == ['2026-04', '2026-06']
    assert all(e['page'] == 2 and e['document_id'] == 'abc' for e in issue['evidence'])


def test_no_speculation_outside_observed_range_or_on_non_period_table():
    assert check_period_gaps(Table(1, [['month'], ['2026-01'], ['2026-02']]), 'a') == []
    assert check_period_gaps(Table(1, [['date'], ['2026-01'], ['2026-03']]), 'a') == []


def test_do_not_assume_missing_month_when_dates_ambiguous_or_headers_duplicated():
    assert check_period_gaps(Table(1, [['month'], ['2026-01'], ['invalid'], ['2026-03']]), 'a') == []
    assert check_period_gaps(Table(1, [['month', 'month'], ['2026-01', '2026-02'], ['2026-03', '2026-04']]), 'a') == []


def test_repeated_months_and_year_boundary():
    table = Table(1, [['Period', 'qty'], ['2025-12', 1], ['2025-12', 2], ['2026-02', 3]])
    issue, = check_period_gaps(table, 'a')
    assert '2026-01' in issue['detail']
