import pytest
from lagrange_rx.ingest.normalize import (normalize_number, normalize_date, normalize_currency,
    normalize_vat, normalize_unit, normalize_name, normalize_region)
from lagrange_rx.ingest.similarity import similarity, candidates

@pytest.mark.parametrize(('raw','expected'), [('1 250 000,00',1250000),('1.250.000',1250000),('1,25 млн',1250000),('1250т',1250000),('74,33 млн',74330000)])
def test_numbers(raw, expected):
    assert normalize_number(raw) == expected

def test_ambiguous_number():
    with pytest.raises(ValueError): normalize_number('1,250')
    assert normalize_number('1,250', decimal_separator=',') == 1.25

@pytest.mark.parametrize(('raw','expected'), [('03.08.2026','2026-08-03'),('2026-08-03','2026-08-03'),('апр. 26','2026-04'),('3 августа 2026','2026-08-03')])
def test_dates(raw, expected):
    assert normalize_date(raw) == expected

def test_ambiguous_date():
    with pytest.raises(ValueError): normalize_date('06/02/2026')
    assert normalize_date('06/02/2026', date_order='DMY') == '2026-02-06'

@pytest.mark.parametrize(('raw','expected'), [('$','USD'),('долл.','USD'),("so'm",'UZS'),('сум','UZS'),('€','EUR')])
def test_currency(raw, expected): assert normalize_currency(raw) == expected

def test_unknown_currency():
    with pytest.raises(ValueError): normalize_currency('не указана')

@pytest.mark.parametrize(('raw','expected'), [('с НДС',True),('без НДС',False),('QQS bilan',True),('QQSsiz',False),('incl. VAT',True)])
def test_vat(raw, expected): assert normalize_vat(raw) is expected

def test_unit_and_names():
    assert normalize_unit('упаковка') == 'pack'
    assert normalize_name('Kardiozan 10mg N30') == normalize_name('Кардиозан 10 мг №30')
    assert normalize_region('Andijon viloyati') == 'Andijon'
    assert normalize_region('Андижон вилояти') == 'Andijon'

def test_similarity_and_ranking():
    assert similarity('Kardiozan 10mg N30', 'Кардиозан 10 мг №30') > 0.8
    assert candidates('Kardiozan', ['Other', 'Кардиозан'])[0][0] == 'Кардиозан'
