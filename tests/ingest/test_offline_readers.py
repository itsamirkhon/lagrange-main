"""Offline reading and structural routing of the 72 real L0 documents (no network, no LLM)."""
from collections import Counter
from pathlib import Path

import pytest

from lagrange_rx.ingest.offline.readers import read_folder

L0 = Path(__file__).resolve().parents[2] / 'sim' / 'worlds' / 'L0' / 'documents'


@pytest.fixture(scope='module')
def docs():
    return read_folder(L0)


def test_reads_every_document_with_unique_hash(docs):
    assert len(docs) == 72
    assert len({d.document_id for d in docs}) == 72
    assert all(len(d.document_id) == 64 for d in docs)


def test_structural_classes(docs):
    counts = Counter(d.doc_class for d in docs)
    assert counts['sales_export'] == 2
    assert counts['regional_report'] == 6
    assert counts['registry_extract'] == 4
    assert counts['invoice'] == 11
    assert counts['customs'] == 4
    assert counts['stock'] == 3
    assert counts['payments'] == 2
    assert counts['contract'] == 6
    assert counts['email'] == 6  # 4 .eml + 2 PDF printouts of correspondence
    assert counts['waybill'] == 6
    assert counts['supplier_quote'] == 8
    assert counts['carrier_quote'] == 4  # 2 rate tables + 2 carrier quote PDFs
    assert counts['unknown'] == 10  # images: not readable offline


def test_pdf_lines_keep_supplier_line_that_mentions_synthetic(docs):
    inv = next(d for d in docs if d.name == 'D6_invoices_bundle_03.pdf')
    first_page = '\n'.join(inv.pages[0])
    assert 'Supplier: Medisynth S.A. (Индия, SYNTHETIC) | Contract: CNT-SUP-2025-01' in first_page
    assert 'УЧЕБНЫЙ ОБРАЗЕЦ' not in first_page


def test_images_are_unreadable_offline_not_guessed(docs):
    images = [d for d in docs if d.kind == 'image']
    assert len(images) == 10
    assert all(d.pages == [] and d.tables == [] and d.warnings for d in images)


def test_csv_is_decoded_from_cp1251(docs):
    sales = next(d for d in docs if d.name == 'D1_1c_sales_2025.csv')
    assert sales.tables[0][0][:3] == ['Период', 'Документ реализации', 'Контрагент']
