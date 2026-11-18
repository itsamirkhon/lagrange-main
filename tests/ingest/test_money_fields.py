from datetime import date

from lagrange_rx.ingest.cross_document import check_quote_contract_moq
from lagrange_rx.ingest.money_checks import check_money
from lagrange_rx.ingest.money_fields import money_records

QUOTE = ('COMMERCIAL QUOTATION KP-1\nDate: 2025-01-05 | Incoterms: CIP Tashkent\nItems\nНомер КП\nПрепарат\nMOQ\nСрок\nЦена CIP\nДействует до\n'
         'KP-1\nKardiozan 10 мг\n10000\n22 дн.\n7,50\n2025-02-19\nKP-2\nKardiozan 10 мг\n6000\n18 дн.\n7,28\nокт. 25\n')
CONTRACT = 'ДОГОВОР № CNT-1\nПодписан: 2025-01-05 | Действует до: 2025-12-31\nУсловия\nВалюта:\nUSD\nЦена:\n7.48 USD\nMOQ:\n5000 уп.\n'


def doc(doc_id, doc_class, pages=(), tables=()):
    return {'document_id': doc_id, 'doc_class': doc_class,
            'pages': [{'number': i + 1, 'text': t} for i, t in enumerate(pages)],
            'tables': [{'page': 1, 'rows': rows} for rows in tables]}


def test_quote_rows_become_evidence_backed_records():
    records, issues = money_records([doc('q', 'supplier_quote', [QUOTE])])
    assert [r['fields']['quote_number']['raw'] for r in records] == ['KP-1', 'KP-2']
    first = records[0]['fields']
    assert first['unit_price']['raw'] == '7,50' and first['moq']['raw'] == '10000' and first['valid_until']['raw'] == '2025-02-19'
    assert first['unit_price']['evidence'] == {'document_id': 'q', 'page': 1, 'quote': '7,50'}
    assert 'currency' not in first and first['issue_date']['raw'] == '2025-01-05'


def test_missing_currency_and_expiry_are_flagged_by_the_existing_money_checks():
    records, _ = money_records([doc('q', 'supplier_quote', [QUOTE])])
    kinds = sorted(i['issue_type'] for i in check_money(records, as_of=date(2026, 1, 1)))
    assert kinds == ['currency_ambiguous', 'currency_ambiguous', 'stale']  # 'окт. 25' is not a full date: no guess


def test_explicit_currency_in_header_or_price_silences_c1():
    with_cur = QUOTE.replace('Цена CIP', 'Цена CIP, USD')
    records, _ = money_records([doc('q', 'supplier_quote', [with_cur])])
    assert records[0]['fields']['currency']['raw'] == 'USD'
    assert not [i for i in check_money(records) if i['issue_type'] == 'currency_ambiguous']


def test_contract_versions_are_separate_records_and_drafts_marked():
    pages = [CONTRACT, CONTRACT.replace('CNT-1', 'CNT-1-A1 (ПРОЕКТ v2)').replace('7.48', '7.22')]
    records, _ = money_records([doc('c', 'contract', pages)])
    assert [r['fields']['contract_number']['raw'] for r in records] == ['CNT-1', 'CNT-1-A1']
    assert [r.get('status') for r in records] == [None, 'draft']
    assert records[0]['fields']['moq']['raw'] == '5000 уп.' and records[0]['fields']['currency']['raw'] == 'USD'


def test_uzs_column_value_in_foreign_currency_is_flagged():
    rows = [['Номер', 'Дата', 'CIP UZS']] + [[f'N{i}', '2025-01-01', f'{1_000_000_000 + i}.0'] for i in range(8)]
    rows[3][2] = '122258.63 UZS'
    _, issues = money_records([doc('t', 'customs', tables=[rows])])
    assert len(issues) == 1 and issues[0]['issue_type'] == 'currency_ambiguous'
    assert issues[0]['evidence'][0]['quote'] == '122258.63 UZS' and 'CIP UZS' in issues[0]['detail']


def test_local_currency_word_counts_as_a_label():
    rows = [['Номер', 'Дата', 'CIP UZS']] + [[f'N{i}', '2025-01-01', f'{1_000_000_000 + i}.0'] for i in range(8)]
    rows[2][2] = '43648.00 сум'
    assert len(money_records([doc('t', 'customs', tables=[rows])])[1]) == 1


def test_small_bare_amount_is_not_flagged_without_a_stray_currency_label():
    rows = [['Номер', 'Сумма UZS']] + [[f'N{i}', f'{15_000_000 + i}.0'] for i in range(8)] + [['small', '73225.25']]
    assert money_records([doc('t', 'payments', tables=[rows])])[1] == []


def test_natural_variation_is_not_flagged():
    rows = [['Месяц', 'Остаток UZS']] + [[str(i), str(v)] for i, v in enumerate([100, 90, 80, 50, 20, 10, 8], 1)]
    assert money_records([doc('t', 'payments', tables=[rows])])[1] == []


CUSTOMS = ('СВОДКА\nРеестр ГТД\nНомер ГТД\nИнвойс\nДата ГТД\nКурс\nCIP USD\nСебест. без НДС\n'
           '26004/1\nINV-1\n2025-01-20\n12971.0\n100.0\n1400000.0\n26004/2\nINV-2\n2025-01-27\n12990.0\n100.0\n1070000.0\n')


def test_landed_cost_below_customs_value_is_a_wrong_rate_contradiction():
    _, issues = money_records([doc('d', 'customs', [CUSTOMS])])
    assert len(issues) == 1
    issue = issues[0]
    assert issue['issue_type'] == 'contradiction' and issue['field'] == 'fx_rate_on_entry' and issue['affected'] == ['d']
    assert {e['quote'] for e in issue['evidence']} == {'12990.0', '1070000.0'}


def test_superseded_registry_extract_is_stale():
    table = [['Реестровая запись', 'Препарат', 'Действует с', 'Rn UZS'],
             ['RE-1', 'Kardiozan', '2025-01-01', '1'], ['RE-7', 'Kardiozan', '2025-07-01', '2'], ['RE-11', 'Kardiozan', '2025-11-01', '3']]
    pdf = 'ВЫПИСКА ИЗ РЕЕСТРА ЦЕН № RE-7 (2026)\nПриказ — Kardiozan\nRn:\n2\n'
    docs = [doc('csv', 'registry_extract', ['x'], [table]), doc('pdf', 'registry_extract', [pdf])]
    _, issues = money_records(docs, as_of=date(2026, 6, 1))
    assert [(i['issue_type'], i['affected']) for i in issues] == [('stale', ['pdf'])]
    assert money_records(docs, as_of=date(2025, 9, 1))[1] == []  # the later record was not yet effective
    _, none = money_records([docs[1]], as_of=date(2026, 6, 1))
    assert none == []


def _records(quote_moq, contract_moqs, price='7,50', draft=()):
    docs = [doc('q', 'supplier_quote', [QUOTE.split('KP-2\n')[0].replace('10000', str(quote_moq))])]
    for n, moq in enumerate(contract_moqs):
        text = CONTRACT.replace('5000', str(moq)).replace('CNT-1', f'CNT-{n}' + (' (ПРОЕКТ v2)' if n in draft else ''))
        docs.append(doc(f'c{n}', 'contract', [text]))
    return money_records(docs)[0]


def test_x1_flags_when_every_plausible_contract_disagrees_even_if_link_is_ambiguous():
    issues = check_quote_contract_moq(_records(10000, [5000, 3000]))
    assert [i['issue_type'] for i in issues if 'KP-1' in i['detail']] == ['contradiction']
    assert 'moq' in issues[0]['detail'].casefold() and issues[0]['field'] == 'moq'
    assert set(issues[0]['affected']) == {'q', 'c0', 'c1'}


def test_x1_silent_when_any_candidate_agrees_or_no_candidate_or_draft_only():
    assert check_quote_contract_moq(_records(5000, [5000, 3000])) == []
    assert check_quote_contract_moq(_records(10000, [])) == []
    assert not [i for i in check_quote_contract_moq(_records(10000, [5000], draft={0}))]
    far = _records(10000, [5000])
    far[1]['fields']['unit_price']['raw'] = '99.0 USD'
    assert check_quote_contract_moq(far) == []
