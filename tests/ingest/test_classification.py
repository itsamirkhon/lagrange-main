import pytest

from lagrange_rx.ingest.classification import DocumentClassifier, classify_document


def cls(filename, text='', media=''):
    return classify_document(filename, [text] if text else [], media_type=media)


@pytest.mark.parametrize(('text', 'expected'), [
    ('СЧЁТ-ФАКТУРА № 123\nПоставщик', 'invoice'),
    ('INVOICE & PACKING LIST INV-1\nSupplier: X', 'invoice'),
    ('HISOB-FAKTURA № 5\nYetkazib beruvchi', 'invoice'),
    ('ДОГОВОР № CNT-1 (ПРОЕКТ v2)\nКонтрагент', 'contract'),
    ('CONTRACT No. 7\nCounterparty', 'contract'),
    ('SHARTNOMA № 7', 'contract'),
    ('КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ KP-1\nSupplier: A', 'supplier_quote'),
    ('COMMERCIAL QUOTATION Q-9', 'supplier_quote'),
    ('КОММЕРЧЕСКИЕ ПРЕДЛОЖЕНИЯ ПЕРЕВОЗЧИКОВ — ЧАСТЬ 1\nТарифы', 'carrier_quote'),
    ('СКЛАДСКАЯ НАКЛАДНАЯ № РН-1\nПозиции', 'waybill'),
    ('WAREHOUSE WAYBILL 15', 'waybill'),
    ('СВОДКА ТАМОЖЕННОЙ ОЧИСТКИ — 2025\nРеестр ГТД', 'customs'),
    ('ВЫПИСКА ИЗ РЕЕСТРА ЦЕН № RE-1 (2026)', 'registry_extract'),
    ('РАСПЕЧАТКА ПЕРЕПИСКИ ПО ДОГОВОРАМ\nТекст сообщения', 'email'),
    ('From: a@b.c\nTo: d@e.f\nSubject: Договор X\nDate: Mon', 'email'),
    ('Реклама\nСпецпредложение на витамины', 'irrelevant'),
])
def test_title_classifies_across_languages(text, expected):
    assert cls('scan.pdf', text).doc_class == expected


@pytest.mark.parametrize(('header', 'expected'), [
    ('Период\tДокумент реализации\tКонтрагент\tРегион\tКанал сбыта\tНоменклатура\tКод 1С\tКоличество', 'sales_export'),
    ('Серия\tНакладная\tПрепарат\tПриход\tСрок годн.\tНач. остаток\tПродано\tСписано\tОстаток', 'stock'),
    ('Плат. док.\tМесяц\tВх. остаток\tВыборка\tПогашение\tИсх. остаток\tПроценты UZS', 'payments'),
    ('Номер ГТД\tИнвойс\tДата ГТД\tТаможенный пост\tКурс ЦБ\tCIP UZS', 'customs'),
    ('Код маршрута\tПеревозчик\tВид транспорта\tКоридор\tЗа партию USD\tСрок дн.', 'carrier_quote'),
    ('Реестровая запись\tПрепарат\tДействует с\tRn UZS\tB UZS\tBx UZS\tChUN с НДС', 'registry_extract'),
])
def test_table_header_classifies(header, expected):
    assert cls('data.xlsx', header + '\nrow').doc_class == expected


def test_regional_report_title_over_repeated_cells_and_uzbek_header():
    text = 'Hududiy savdo hisoboti: Toshkent\tHududiy savdo hisoboti: Toshkent\nOy\tHudud\tXaridor\tKanal\tDori nomi'
    assert cls('x.xlsx', text).doc_class == 'regional_report'
    assert cls('x.xlsx', 'Сводка продаж: Бухара\nМесяц\tОбласть\tПокупатель').doc_class == 'regional_report'


def test_filename_only_is_low_confidence_but_classifies_empty_scans():
    result = cls('waybill_photo_01.jpg', '', 'image/jpeg')
    assert result.doc_class == 'waybill' and .6 <= result.confidence < .8
    assert cls('carrier_chat_auto.png', '', 'image/png').doc_class == 'carrier_quote'
    assert cls('memo_marketing.jpg', '', 'image/jpeg').doc_class == 'memo'
    assert cls('junk_blank_scan_01.jpg', '', 'image/jpeg').doc_class == 'irrelevant'


def test_content_beats_filename_and_content_confidence_is_higher():
    result = cls('invoice_copy.pdf', 'ДОГОВОР № 1\nКонтрагент')
    assert result.doc_class == 'contract'
    assert result.confidence > cls('contract.pdf', '').confidence


def test_unrecognized_stays_unknown_and_scan_prefix_is_not_a_signal():
    assert cls('D5_scan.pdf', '').doc_class == 'unknown'
    assert cls('IMG_0001.jpg', '', 'image/jpeg').doc_class == 'unknown'
    assert cls('doc.pdf', 'Some unrelated text about nothing').doc_class == 'unknown'


def test_unrelated_catalog_is_irrelevant():
    assert cls('list.csv', 'Код\tНаименование\tЦена UZS\nOTC-901\tVitaMix Syrup').doc_class == 'irrelevant'


def test_signals_are_recorded():
    result = cls('x.pdf', 'ДОГОВОР № 1')
    assert result.signals and result.method == 'deterministic'


def test_classifier_uses_llm_only_below_threshold_and_records_method():
    calls = []

    class Llm:
        def classify(self, filename, text, media_type=''):
            calls.append(filename)
            from lagrange_rx.ingest.classification import Classification
            return Classification('memo', .8, ('llm',), 'llm')

    classifier = DocumentClassifier(llm=Llm())
    assert classifier.classify('x.pdf', ['ДОГОВОР № 1\nКонтрагент'], 'application/pdf').doc_class == 'contract'
    assert calls == []
    result = classifier.classify('scan.jpg', [], 'image/jpeg')
    assert (result.doc_class, result.method) == ('memo', 'llm') and calls == ['scan.jpg']


def test_llm_failure_keeps_deterministic_answer():
    class Llm:
        def classify(self, *args, **kwargs):
            raise LookupError('no recording')

    result = DocumentClassifier(llm=Llm()).classify('waybill.jpg', [], 'image/jpeg')
    assert result.doc_class == 'waybill' and 'llm_unavailable' in result.signals
