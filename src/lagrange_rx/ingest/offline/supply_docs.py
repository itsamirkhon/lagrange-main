"""Deterministic parsers for supply-side documents: invoices, customs, stock, rates, quotes, contracts."""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from .sales import number

_DATE = re.compile(r'\d{4}-\d{2}-\d{2}')


def _date(text: str) -> date:
    return date.fromisoformat(_DATE.search(text).group())


def _labelled(lines, labels):
    """`Label:` lines followed by their value on the next line (PDF cell layout)."""
    return {line[:-1]: lines[i + 1] for i, line in enumerate(lines[:-1]) if line.endswith(':') and line[:-1] in labels}


_QUOTE_ROW = re.compile(r'(KP-\d{4}-\d+)\s+(.+?)\s*(\d{3,6})\s+(\d+)\s*дн\.\s+([\d.]+)\s+([A-Z]{3})\s+(\d{4}-\d{2}-\d{2})')


def _grid(cells, width):
    if len(cells) % width:
        raise ValueError(f'table cells ({len(cells)}) do not form rows of {width}')


def parse_invoice_page(lines: list[str]) -> dict:
    title = re.match(r'INVOICE & PACKING LIST (\S+)', lines[0])
    if not title:
        raise ValueError('not an invoice page')
    out = {'invoice': title.group(1), 'supplier': None, 'contract': None, 'lots': [], 'total_packs': None}
    for line in lines:
        m = re.match(r'Supplier: (.+?) \| Contract: (\S+)', line)
        if m:
            out['supplier'], out['contract'] = m.groups()
        m = re.match(r'Dispatch: (\S+) \| Arrival: (\S+)', line)
        if m:
            out['dispatch'], out['arrival'] = date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))
        m = re.match(r'Invoice \S+ \| Total packs: (\d+)', line)
        if m:
            out['total_packs'] = int(m.group(1))
    start = lines.index('Срок годн.') + 1
    cells = []
    for line in lines[start:]:
        if line.startswith('Invoice '):
            break
        cells.append(line)
    _grid(cells, 5)
    for i in range(0, len(cells), 5):
        lot, product, qty, produced, expiry = cells[i:i + 5]
        out['lots'].append({'lot': lot, 'product': product, 'qty': int(re.match(r'\d+', qty).group()),
                            'produced': date.fromisoformat(produced), 'expiry': date.fromisoformat(expiry)})
    return out


def parse_customs_pdf(lines: list[str]) -> list[dict]:
    """GTD register: number, invoice, date, rate, CIP in the invoice currency, landed cost ex VAT."""
    header = next(i for i, line in enumerate(lines) if line.startswith('Себест'))
    currency = re.search(r'CIP (\w{3})', ' '.join(lines)).group(1)
    cells = [c for c in lines[header + 1:] if '.pdf' not in c]
    _grid(cells, 6)
    rows = []
    for i in range(0, len(cells), 6):
        gtd, invoice, day, rate, cip, landed = cells[i:i + 6]
        rows.append({'gtd': gtd, 'invoice': invoice, 'date': date.fromisoformat(day), 'rate': number(rate),
                     'cip_fx': number(cip), 'currency': currency, 'landed_uzs': number(landed)})
    return rows


def parse_customs_table(rows) -> list[dict]:
    header = [str(c).strip() for c in rows[0]]
    col = {name: header.index(name) for name in
           ('Номер ГТД', 'Инвойс', 'Дата ГТД', 'Таможенный пост', 'Курс ЦБ', 'CIP UZS', 'Пошлина UZS', 'Себестоимость без НДС')}
    return [{'gtd': str(r[col['Номер ГТД']]).strip(), 'invoice': str(r[col['Инвойс']]).strip(),
             'date': date.fromisoformat(str(r[col['Дата ГТД']])[:10]), 'post': str(r[col['Таможенный пост']]).strip(),
             'rate': number(r[col['Курс ЦБ']]), 'cip_uzs': number(r[col['CIP UZS']]),
             'duty_uzs': number(r[col['Пошлина UZS']]), 'landed_uzs': number(r[col['Себестоимость без НДС']])}
            for r in rows[1:] if r and r[0]]


def parse_stock_lots(rows) -> list[dict]:
    header = [str(c).strip() for c in rows[0]]
    col = {name: header.index(name) for name in ('Серия', 'Накладная', 'Препарат', 'Приход', 'Срок годн.', 'Нач. остаток', 'Продано', 'Списано', 'Остаток')}
    return [{'lot': str(r[col['Серия']]).strip(), 'waybill': str(r[col['Накладная']]).strip(), 'product': str(r[col['Препарат']]).strip(),
             'arrival': date.fromisoformat(str(r[col['Приход']])[:10]), 'expiry': date.fromisoformat(str(r[col['Срок годн.']])[:10]),
             'initial': int(number(r[col['Нач. остаток']])), 'sold': int(number(r[col['Продано']])),
             'written_off': int(number(r[col['Списано']])), 'remaining': int(number(r[col['Остаток']]))}
            for r in rows[1:] if r and r[0]]


def parse_rates(rows) -> list[dict]:
    header = [str(c).strip() for c in rows[0]]
    col = {name: header.index(name) for name in ('Код маршрута', 'Перевозчик', 'Вид транспорта', 'Коридор', 'За партию USD', 'За упак. USD', 'Срок дн.', 'Пост')}
    return [{'code': str(r[col['Код маршрута']]).strip(), 'carrier': str(r[col['Перевозчик']]).strip(),
             'mode': str(r[col['Вид транспорта']]).strip(), 'corridor': str(r[col['Коридор']]).strip(),
             'usd_per_shipment': number(r[col['За партию USD']]), 'usd_per_pack': number(r[col['За упак. USD']]),
             'days': int(number(r[col['Срок дн.']])), 'post': str(r[col['Пост']]).strip()} for r in rows[1:] if r and r[0]]


def parse_supplier_quote(lines: list[str]) -> dict:
    title = re.search(r'(KP-\d{4}-\d+)$', lines[0])
    header = next(line for line in lines if line.startswith('Supplier:'))
    m = re.match(r'Supplier: (.+) \(([A-Z]+-[A-Z]+)\)$', header)
    meta = re.search(r'Date: (\S+) \| Incoterms: (.+)', ' '.join(lines))
    labels = _labelled(lines, {'Оплата'})
    start = lines.index('Действует до') + 1
    # Cells are truncated and may run together ("Trombolek 75 мг №" + "3000"), so rows are
    # read from the joined text; the number of rows must equal the number of quote numbers.
    text = ' '.join(c for c in lines[start:] if not c.startswith('ООО'))
    items = []
    for row in _QUOTE_ROW.finditer(text):
        items.append({'quote': row.group(1), 'product': row.group(2).strip(), 'moq': int(row.group(3)), 'lead_days': int(row.group(4)),
                      'price': number(row.group(5)), 'currency': row.group(6), 'valid_until': date.fromisoformat(row.group(7))})
    if len(items) != len(re.findall(r'KP-\d{4}-\d+', text)):
        raise ValueError('quote items table is not fully readable')
    return {'quote': title.group(1), 'supplier': m.group(1), 'supplier_code': m.group(2),
            'date': date.fromisoformat(meta.group(1)), 'incoterms': meta.group(2).strip(),
            'payment_terms': labels.get('Оплата'), 'items': items}


def parse_carrier_quotes(lines: list[str]) -> list[dict]:
    start = lines.index('Срок') + 1
    cells = [c for c in lines[start:] if '.pdf' not in c]
    _grid(cells, 6)
    rows = []
    for i in range(0, len(cells), 6):
        number_, code, mode, rate, vat, days = cells[i:i + 6]
        amount, currency = rate.split()
        rows.append({'quote': number_, 'route_code': code, 'mode_prefix': mode, 'rate_per_pack': number(amount),
                     'currency': currency, 'vat': 'excl' if 'без' in vat.lower() else 'incl' if 'с НДС' in vat else 'unknown',
                     'lead_days': int(days.split()[0])})
    return rows


def parse_contract_page(lines: list[str]) -> dict:
    title = re.match(r'ДОГОВОР № (\S+)', lines[0])
    party = next(line for line in lines if line.startswith('Контрагент:'))
    signed = re.search(r'Подписан: (\S+) \| Действует до: (\S+)', ' '.join(lines))
    labels = _labelled(lines, {'Валюта', 'Цена', 'MOQ', 'Оплата', 'Базис'})
    price, currency = labels['Цена'].split()
    note = lines[lines.index('Примечание') + 1] if 'Примечание' in lines else None
    return {'contract': title.group(1), 'party': party.split(':', 1)[1].strip(),
            'signed': date.fromisoformat(signed.group(1)), 'valid_to': date.fromisoformat(signed.group(2)),
            'price': number(price), 'currency': currency, 'moq': int(labels['MOQ'].split()[0]),
            'payment_terms': labels['Оплата'], 'incoterms': labels['Базис'], 'note': note}


_TERMS = [
    (re.compile(r'(\d+)\s*дн\w*\s+отсрочк', re.I), lambda m: (int(m.group(1)), Decimal(0))),
    (re.compile(r'(\d+)%\s*предоплат', re.I), lambda m: (0, Decimal(m.group(1)) / 100)),
    (re.compile(r'(\d+)%\s*аккредитив', re.I), lambda m: (0, Decimal(m.group(1)) / 100)),
]


def parse_payment_terms(text: str) -> tuple[int, Decimal] | None:
    """(supplier_days, prepayment_share); None when the wording is not recognised."""
    for pattern, build in _TERMS:
        m = pattern.search(text)
        if m:
            return build(m)
    return None
