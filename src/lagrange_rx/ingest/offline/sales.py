"""1C sales export (row per sale) and multilingual regional summaries (corroboration)."""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

CHANNELS = {'дистрибьюторы': 'distributors', 'аптечные сети': 'pharmacy_chains', 'госзакупки': 'hospital_tenders'}

def _key(value) -> str:
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', str(value)).casefold().strip())


_ALIASES = {
    'period': ('период', 'месяц', 'period', 'давр', 'oy', 'davr'),
    'region': ('регион', 'область', 'region', 'ҳудуд', 'hudud', 'viloyat'),
    'customer': ('контрагент', 'покупатель', 'counterparty', 'мижоз', 'xaridor', 'mijoz'),
    'channel': ('канал', 'канал сбыта', 'channel', 'kanal', 'yoʻnalish', "yo'nalish"),
    'product': ('препарат', 'номенклатура', 'product', 'dori nomi', 'preparat'),
    'lot': ('серия', 'batch', 'seriya', 'partiya'),
    'qty': ('упаковок', 'кол-во', 'packs', 'қадоқ', 'miqdor (up.)', 'qadoq'),
    'amount': ('выручка без ндс', 'сумма без ндс', 'net revenue uzs', 'Сумма ҚҚСсиз', 'Summa (QQSsiz)', 'tushum uzs'),
}
_LOOKUP = {_key(alias): field for field, aliases in _ALIASES.items() for alias in aliases}
_REQUIRED = {'period', 'region', 'lot', 'qty', 'amount'}

_1C = {'period': 'Период', 'doc': 'Документ реализации', 'customer': 'Контрагент', 'region': 'Регион',
       'channel': 'Канал сбыта', 'product': 'Номенклатура', 'lot': 'Серия', 'qty': 'Количество',
       'price': 'Цена без НДС', 'amount': 'Сумма без НДС'}


def number(value) -> Decimal:
    """Decimal from a cell: native number or text with decimal comma/point and spaces."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return Decimal(str(value))
    text = str(value).strip().replace(' ', '').replace(' ', '').replace(',', '.')
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f'not a number: {value!r}') from exc


def regional_columns(header) -> dict[str, int] | None:
    fields = {}
    for index, cell in enumerate(header):
        field = _LOOKUP.get(_key(cell)) if cell is not None else None
        if field and field not in fields:
            fields[field] = index
    return fields if _REQUIRED <= set(fields) else None


def parse_regional(rows, sheet=''):
    for header_index, header in enumerate(rows[:6]):
        columns = regional_columns(list(header))
        if columns is None:
            continue
        out = []
        for index, row in enumerate(rows[header_index + 1:], header_index + 1):
            if len(row) <= max(columns.values()) or row[columns['qty']] in (None, ''):
                continue
            out.append({'row': index, 'sheet': sheet, 'period': str(row[columns['period']]),
                        'region': str(row[columns['region']]), 'lot': str(row[columns['lot']]),
                        'qty': int(number(row[columns['qty']])), 'amount': number(row[columns['amount']])})
        return out
    return []


def parse_1c(rows):
    """Rows of the 1C export: one dict per sale line, with unit price."""
    header = [str(c).strip() for c in rows[0]]
    index = {name: header.index(label) for name, label in _1C.items()}
    out = []
    for n, row in enumerate(rows[1:], 1):
        if not any(str(c).strip() for c in row):
            continue
        out.append({'row': n, 'period': row[index['period']].strip(), 'doc': row[index['doc']].strip(),
                    'customer': row[index['customer']].strip(), 'region': row[index['region']].strip(),
                    'channel': row[index['channel']].strip(), 'product': row[index['product']].strip(),
                    'lot': row[index['lot']].strip(), 'qty': int(number(row[index['qty']])),
                    'price': number(row[index['price']]), 'amount': number(row[index['amount']])})
    return out
