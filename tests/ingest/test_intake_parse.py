from pathlib import Path
from io import BytesIO

import pytest
from openpyxl import Workbook

from lagrange_rx.ingest.intake import IntakeStore, IntakeError
from lagrange_rx.ingest.parse import parse_document


def test_intake_deduplicates_and_rejects_dangerous(tmp_path):
    store = IntakeStore(tmp_path)
    a = store.add('sales.csv', b'a,b\n1,2')
    b = store.add('other.csv', b'a,b\n1,2')
    assert a.document_id == b.document_id and b.duplicate
    assert Path(a.path).read_bytes() == b'a,b\n1,2'
    with pytest.raises(IntakeError):
        store.add('evil.exe', b'bad')


def test_csv_decoding_and_evidence(tmp_path):
    store = IntakeStore(tmp_path / 'store')
    doc = store.add('data.csv', 'Товар;Количество\nАспирин;12'.encode('cp1251'))
    parsed = parse_document(doc, tmp_path / 'out')
    assert parsed.tables[0].rows == [['Товар', 'Количество'], ['Аспирин', '12']]
    assert 'Аспирин' in parsed.pages[0].text
    assert Path(parsed.pages[0].image_path).exists()


def test_xlsx_merged_cells_and_email_attachments(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(['Title'])
    sheet.append(['Name', 'Qty'])
    sheet.append(['A', 3])
    sheet.merge_cells('A1:B1')
    out = BytesIO(); workbook.save(out)
    store = IntakeStore(tmp_path / 'store')
    parsed = parse_document(store.add('a.xlsx', out.getvalue()), tmp_path / 'out')
    assert parsed.tables[0].rows[0] == ['Title', 'Title']
    assert parsed.tables[0].rows[2] == ['A', '3']
    raw = b'MIME-Version: 1.0\nContent-Type: multipart/mixed; boundary="X"\n\n--X\nContent-Type: text/plain\n\nHello\n--X\nContent-Type: text/plain\nContent-Disposition: attachment; filename="a.txt"\n\nAttached\n--X--\n'
    email = parse_document(store.add('email.eml', raw), tmp_path / 'mail', store)
    assert 'Hello' in email.pages[0].text
    assert email.attachments[0].filename == 'a.txt'


def test_zip_intake_and_bounded_archive(tmp_path):
    from zipfile import ZipFile
    content = BytesIO()
    with ZipFile(content, 'w') as archive:
        archive.writestr('nested/a.txt', 'hello')
        archive.writestr('../escape.txt', 'bad')
    with pytest.raises(IntakeError):
        IntakeStore(tmp_path).add_archive(content.getvalue())


def test_missing_ocr_is_reported_without_fabricating_text(tmp_path, monkeypatch):
    from PIL import Image
    import subprocess
    image = BytesIO(); Image.new('RGB', (20, 20), 'white').save(image, format='PNG')
    def missing(*args, **kwargs):
        raise FileNotFoundError('tesseract')
    monkeypatch.setattr(subprocess, 'run', missing)
    result = parse_document(IntakeStore(tmp_path / 'store').add('scan.png', image.getvalue()), tmp_path / 'out')
    assert result.pages[0].text == ''
    assert result.warnings
