from lagrange_rx.ingest.parse import Table
from lagrange_rx.ingest.table_quality import check_table_quality


def test_credit_total_discrepancy_and_valid_total():
    header = ['month', 'monthly_interest_uzs']
    good = Table(1, [header, ['2026-01', '10'], ['2026-02', '20'], ['TOTAL', '30']])
    bad = Table(1, [header, ['2026-01', '10'], ['2026-02', '20'], ['TOTAL', '27']])
    assert check_table_quality(good, 'd') == []
    issues = check_table_quality(bad, 'd')
    assert len(issues) == 1 and issues[0]['issue_type'] == 'arithmetic'
    assert issues[0]['evidence'][0]['quote'] == '27'


def test_incomplete_rows_are_not_reported_as_wrong_total():
    table = Table(1, [['month', 'monthly_interest_uzs'], ['2026-01', ''], ['TOTAL', '100']])
    assert check_table_quality(table, 'd') == []


def test_total_label_can_be_in_first_column():
    table = Table(1, [['id', 'month', 'monthly_interest_uzs'], ['x', '2026-01', '10'], ['TOTAL', '2026-01..2026-01', '5']])
    assert len(check_table_quality(table, 'd')) == 1


def test_localized_credit_header_and_total():
    table = Table(1, [['Плат. док.', 'Месяц', 'Проценты UZS'], ['p1', '2026-01', '10.0'], ['p2', '2026-02', '20'], ['ИТОГО', '2026', '27']])
    assert len(check_table_quality(table, 'doc')) == 1
