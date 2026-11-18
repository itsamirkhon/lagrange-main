"""Deterministic document parsing; failures remain visible to callers."""
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from pathlib import Path
import csv
import io
import re
import subprocess

@dataclass
class Page:
    number: int
    text: str
    words: list = field(default_factory=list)
    image_path: str = ''

@dataclass
class Table:
    page: int
    rows: list
    name: str = ''

@dataclass
class ParsedDocument:
    document_id: str
    pages: list = field(default_factory=list)
    tables: list = field(default_factory=list)
    attachments: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def _render_text(text, path):
    from PIL import Image, ImageDraw
    image = Image.new('RGB', (1200, max(500, min(10000, 40 + len(text.splitlines()) * 18))), 'white')
    draw = ImageDraw.Draw(image)
    try:
        from PIL import ImageFont
        font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 14)
    except OSError:
        font = None
    safe_text = ''.join(ch for ch in text if ch in '\n\t' or ord(ch) >= 32)
    draw.multiline_text((12, 12), safe_text, fill='black', font=font, spacing=3)
    image.save(path)


def _add_page(result, output, text, words=None):
    number = len(result.pages) + 1
    image = output / f'{number}.png'
    _render_text(text, image)
    result.pages.append(Page(number, text, words or [], str(image)))


def _ocr(image_path):
    try:
        languages = subprocess.run(['tesseract', '--list-langs'], capture_output=True, text=True, check=True, timeout=30).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return '', [], 'Tesseract is unavailable'
    chosen = [lang for lang in ('rus', 'uzb', 'uzb_cyrl', 'eng') if lang in languages]
    if not chosen:
        return '', [], 'OCR languages rus, uzb, uzb_cyrl and eng unavailable'
    try:
        proc = subprocess.run(['tesseract', str(image_path), 'stdout', '-l', '+'.join(chosen), 'tsv'], capture_output=True, text=True, check=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return '', [], 'Tesseract could not read the image'
    words = []
    for row in csv.DictReader(io.StringIO(proc.stdout), delimiter='\t'):
        if row['text'].strip():
            words.append({'text': row['text'], 'bbox': [int(row['left']), int(row['top']), int(row['left']) + int(row['width']), int(row['top']) + int(row['height'])], 'confidence': float(row['conf'])})
    return ' '.join(w['text'] for w in words), words, ''


def _pdf(result, data, output):
    import fitz
    with fitz.open(stream=data, filetype='pdf') as pdf:
        for page in pdf:
            image = output / f'{page.number + 1}.png'
            page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(str(image))
            # Watermarks are often rotated diagonally: omit non-horizontal PDF text lines.
            lines = []
            for block in page.get_text('dict')['blocks']:
                for line in block.get('lines', []):
                    if abs(line.get('dir', (1, 0))[1]) < 0.1:
                        value = ''.join(s['text'] for s in line['spans'])
                        if not re.search(r'УЧЕБНЫЙ\s+ОБРАЗЕЦ|SYNTHETIC', value, re.I):
                            lines.append(value)
            text = '\n'.join(lines)
            words = [{'text': w[4], 'bbox': list(w[:4])} for w in page.get_text('words') if w[4] in text]
            if not text.strip():
                text, words, warning = _ocr(image)
                if warning: result.warnings.append(f'Page {page.number + 1}: {warning}')
            result.pages.append(Page(page.number + 1, text, words, str(image)))


def parse_document(document, output_dir, store=None):
    output = Path(output_dir) / document.document_id
    output.mkdir(parents=True, exist_ok=True)
    data = Path(document.path).read_bytes()
    result = ParsedDocument(document.document_id)
    suffix = Path(document.filename).suffix.lower()
    if suffix == '.pdf':
        _pdf(result, data, output)
    elif suffix in ('.png', '.jpg', '.jpeg'):
        from PIL import Image
        image = output / '1.png'
        with Image.open(io.BytesIO(data)) as im: im.convert('RGB').save(image)
        text, words, warning = _ocr(image)
        result.pages.append(Page(1, text, words, str(image)))
        if warning: result.warnings.append(warning)
    elif suffix == '.xlsx':
        from openpyxl import load_workbook
        book = load_workbook(io.BytesIO(data), data_only=True)
        for sheet in book:
            merged = {}
            for group in sheet.merged_cells.ranges:
                value = sheet.cell(group.min_row, group.min_col).value
                for row in sheet.iter_rows(min_row=group.min_row, max_row=group.max_row, min_col=group.min_col, max_col=group.max_col):
                    for cell in row: merged[(cell.row, cell.column)] = value
            rows = [[str(v) if v is not None else '' for cell in row for v in [merged.get((cell.row, cell.column), cell.value)]] for row in sheet.iter_rows()]
            result.tables.append(Table(len(result.pages)+1, rows, sheet.title))
            _add_page(result, output, '\n'.join('\t'.join(r) for r in rows))
    elif suffix == '.csv':
        try: text = data.decode('utf-8-sig')
        except UnicodeDecodeError: text = data.decode('cp1251')
        try: dialect = csv.Sniffer().sniff(text[:8192], delimiters=',;')
        except csv.Error: dialect = csv.excel
        rows = list(csv.reader(io.StringIO(text), dialect))
        result.tables.append(Table(1, rows))
        _add_page(result, output, '\n'.join('\t'.join(r) for r in rows))
    elif suffix == '.eml':
        message = BytesParser(policy=policy.default).parsebytes(data)
        body = message.get_body(preferencelist=('plain',)) if message.is_multipart() else message
        text = '\n'.join(f'{key}: {message[key]}' for key in ('From', 'To', 'Subject', 'Date') if message[key])
        if body: text += '\n' + str(body.get_content())
        _add_page(result, output, text)
        for part in message.iter_attachments():
            if store and part.get_filename():
                try: result.attachments.append(store.add(part.get_filename(), part.get_payload(decode=True)))
                except ValueError as exc: result.warnings.append(f'Attachment rejected: {exc}')
    else:
        try: text = data.decode('utf-8-sig')
        except UnicodeDecodeError: text = data.decode('cp1251')
        _add_page(result, output, text)
    return result
