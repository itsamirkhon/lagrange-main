"""Offline readers: bytes -> text lines / tables, plus structural document class.

No network, no OCR, no LLM. A file that cannot be read offline (images) yields no
content and a warning; its class stays `unknown` rather than being guessed.
"""
from __future__ import annotations

import csv
import io
import re
import warnings
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from hashlib import sha256
from pathlib import Path

from ..intake import TYPES

_WATERMARK = re.compile(r'^\s*(УЧЕБНЫЙ\s+ОБРАЗЕЦ\s*/\s*SYNTHETIC|SYNTHETIC)\s*$', re.I)


@dataclass
class RawDoc:
    name: str
    path: Path
    document_id: str
    mime: str
    kind: str  # csv | xlsx | pdf | eml | image | text
    tables: list = field(default_factory=list)   # list of sheets, each a list of rows
    sheets: list = field(default_factory=list)   # sheet names (xlsx)
    pages: list = field(default_factory=list)    # pdf: list of pages, each a list of text lines
    text: str = ''
    attachments: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    doc_class: str = 'unknown'


def _pdf_pages(data: bytes) -> list[list[str]]:
    import pymupdf
    pages = []
    with pymupdf.open(stream=data, filetype='pdf') as pdf:
        for page in pdf:
            lines = []
            for block in page.get_text('dict')['blocks']:
                for line in block.get('lines', []):
                    if abs(line.get('dir', (1, 0))[1]) >= 0.1:  # rotated watermark letters
                        continue
                    value = ''.join(span['text'] for span in line['spans']).strip()
                    if value and not _WATERMARK.match(value):
                        lines.append(value)
            pages.append(lines)
    return pages


def _csv_rows(data: bytes) -> list[list[str]]:
    for encoding in ('utf-8-sig', 'cp1251'):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=',;')
    except csv.Error:
        dialect = csv.excel
    return list(csv.reader(io.StringIO(text), dialect))


def _xlsx_sheets(data: bytes):
    from openpyxl import load_workbook
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        book = load_workbook(io.BytesIO(data), data_only=True)
    names, tables = [], []
    for sheet in book:
        names.append(sheet.title)
        tables.append([list(row) for row in sheet.iter_rows(values_only=True)])
    return names, tables


def _eml(data: bytes):
    message = BytesParser(policy=policy.default).parsebytes(data)
    body = message.get_body(preferencelist=('plain',))
    text = '\n'.join(f'{k}: {message[k]}' for k in ('From', 'To', 'Subject', 'Date') if message[k])
    if body:
        text += '\n' + str(body.get_content())
    return text, [part.get_filename() for part in message.iter_attachments() if part.get_filename()]


_PDF_MARKERS = [
    ('INVOICE & PACKING LIST', 'invoice'), ('СВОДКА ТАМОЖЕННОЙ', 'customs'),
    ('КОММЕРЧЕСКИЕ ПРЕДЛОЖЕНИЯ ПЕРЕВОЗЧИКОВ', 'carrier_quote'),
    ('COMMERCIAL QUOTATION', 'supplier_quote'), ('КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ', 'supplier_quote'),
    ('ДОГОВОР №', 'contract'), ('СКЛАДСКАЯ НАКЛАДНАЯ', 'waybill'),
    ('ВЫПИСКА ИЗ РЕЕСТРА', 'registry_extract'), ('РАСПЕЧАТКА ПЕРЕПИСКИ', 'email'),
]
_HEADERS = [  # (required header cells of the table's header row, class)
    ({'Документ реализации', 'Номенклатура', 'Серия'}, 'sales_export'),
    ({'Реестровая запись', 'ChUN с НДС'}, 'registry_extract'),
    ({'Номер ГТД', 'Инвойс'}, 'customs'),
    ({'Код маршрута', 'Перевозчик'}, 'carrier_quote'),
    ({'Акт/Док.', 'Статья'}, 'payments'),
    ({'Плат. док.', 'Вх. остаток'}, 'payments'),
    ({'Серия', 'Накладная'}, 'stock'),
]


def _header_class(rows) -> str | None:
    for row in rows[:6]:
        cells = {str(c).strip() for c in row if c is not None}
        for required, doc_class in _HEADERS:
            if required <= cells:
                return doc_class
        # Regional sales summaries: multilingual headers, recognised by their shape
        # (a period column, a region column, lot/series and a packs column) - see sales.py.
        from .sales import regional_columns
        if regional_columns(list(row)):
            return 'regional_report'
    return None


def _classify(doc: RawDoc) -> str:
    if doc.kind == 'pdf':
        head = ' '.join(doc.pages[0][:3]) if doc.pages else ''
        return next((c for marker, c in _PDF_MARKERS if marker in head), 'unknown')
    if doc.kind == 'eml':
        return 'email'
    if doc.kind in ('csv', 'xlsx'):
        for rows in doc.tables:
            found = _header_class(rows)
            if found:
                return found
    return 'unknown'


def read_document(path: Path) -> RawDoc:
    data = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix not in TYPES:
        raise ValueError(f'unsupported file type: {path.name}')
    kind = {'.pdf': 'pdf', '.csv': 'csv', '.xlsx': 'xlsx', '.eml': 'eml', '.txt': 'text'}.get(suffix, 'image')
    doc = RawDoc(path.name, path, sha256(data).hexdigest(), TYPES[suffix], kind)
    if kind == 'pdf':
        doc.pages = _pdf_pages(data)
    elif kind == 'csv':
        doc.tables = [_csv_rows(data)]
    elif kind == 'xlsx':
        doc.sheets, doc.tables = _xlsx_sheets(data)
    elif kind == 'eml':
        doc.text, doc.attachments = _eml(data)
    elif kind == 'text':
        doc.text = data.decode('utf-8-sig', errors='replace')
    else:
        doc.warnings.append('image: OCR is unavailable offline (no tesseract rus data); content not extracted')
    doc.doc_class = _classify(doc)
    return doc


def read_folder(folder) -> list[RawDoc]:
    folder = Path(folder)
    return [read_document(p) for p in sorted(folder.iterdir()) if p.is_file()]
