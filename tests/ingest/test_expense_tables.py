from lagrange_rx.ingest.expense_tables import extract_expenses
from lagrange_rx.ingest.parse import Table


def test_extracts_expenses_and_retains_product_context_and_cell_evidence():
    table = Table(2, [['Акт/Док.', 'Месяц', 'Номенклатура', 'Статья', 'Сумма UZS'],
                      ['АВР-01', '2026-01', 'Kardiozan', 'distribution', 29722000]])
    row = extract_expenses(table, 'doc')['rows'][0]
    assert row['type'] == 'CostItem'
    assert row['fields']['CostItem.amount_uzs']['raw'] == '29722000'
    assert row['fields']['CostItem.category']['evidence']['quote'] == 'distribution'
    assert row['product']['raw'] == 'Kardiozan'
    assert 'CostItem.layer' not in row['fields']
    assert 'CostItem.driver' not in row['fields']


def test_rejects_ambiguous_columns_and_unrelated_credit_table():
    assert not extract_expenses(Table(1, [['Месяц', 'Проценты UZS'], ['2026-01', 5]]), 'doc')['rows']
    ambiguous = Table(1, [['Месяц', 'Статья', 'Сумма UZS', 'Сумма UZS'], ['2026-01', 'marketing', 5, 6]])
    assert not extract_expenses(ambiguous, 'doc')['rows']
