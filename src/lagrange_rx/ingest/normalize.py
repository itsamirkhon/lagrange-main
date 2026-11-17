"""Deterministic normalization at the ingestion boundary.

Ambiguous input raises ValueError so callers can ask a reviewer rather than guess.
"""
import datetime as dt
import re
import unicodedata


def normalize_number(raw: str, decimal_separator: str | None = None) -> float:
    text = raw.strip().lower().replace('\u00a0', '').replace(' ', '')
    match = re.fullmatch(r'([+-]?[\d.,]+)(млн|млрд|тыс\.?|т)?', text)
    if not match:
        raise ValueError('invalid number')
    number, suffix = match.groups()
    multiplier = {'млн': 1e6, 'млрд': 1e9, 'тыс': 1e3, 'тыс.': 1e3, 'т': 1e3, None: 1}[suffix]
    if decimal_separator not in (None, '.', ','):
        raise ValueError('invalid decimal separator')
    if decimal_separator is None:
        if '.' in number and ',' in number:
            decimal_separator = '.' if number.rfind('.') > number.rfind(',') else ','
        elif '.' in number or ',' in number:
            sep = '.' if '.' in number else ','
            parts = number.split(sep)
            if len(parts) > 2:
                if not all(len(p) == 3 for p in parts[1:]):
                    raise ValueError('ambiguous number')
                number = ''.join(parts)
            elif len(parts[-1]) == 3 and not suffix:
                raise ValueError('ambiguous number')
            else:
                decimal_separator = sep
    if decimal_separator:
        other = ',' if decimal_separator == '.' else '.'
        number = number.replace(other, '').replace(decimal_separator, '.')
    try:
        return float(number) * multiplier
    except ValueError as exc:
        raise ValueError('invalid number') from exc


_MONTHS = {'янв':1,'фев':2,'мар':3,'апр':4,'май':5,'мая':5,'июн':6,'июл':7,'авг':8,'сен':9,'окт':10,'ноя':11,'дек':12,
           'yan':1,'fev':2,'mart':3,'apr':4,'may':5,'iyun':6,'iyul':7,'avg':8,'sen':9,'okt':10,'noy':11,'dek':12}

def normalize_date(raw: str, date_order: str | None = None) -> str:
    text = raw.strip().lower()
    try:
        if re.fullmatch(r'\d{4}-\d{1,2}-\d{1,2}', text):
            return dt.date.fromisoformat(text).isoformat()
        m = re.fullmatch(r'(\d{1,2})[./](\d{1,2})[./](\d{4})', text)
        if m:
            a, b, year = map(int, m.groups())
            if '/' in text and date_order is None and a <= 12 and b <= 12 and a != b:
                raise ValueError('ambiguous date')
            if date_order not in (None, 'DMY', 'MDY'):
                raise ValueError('invalid date order')
            month, day = (a, b) if date_order == 'MDY' or ('/' in text and b > 12) else (b, a)
            return dt.date(year, month, day).isoformat()
        m = re.fullmatch(r'(?:(\d{1,2})\s+)?([a-zа-яё]+)\.?\s+(\d{2}|\d{4})', text)
        if m:
            day, month_text, year = m.groups()
            month = next((value for key, value in _MONTHS.items() if month_text.startswith(key)), None)
            if month is not None:
                year_value = int(year) + (2000 if len(year) == 2 else 0)
                if day is None:
                    return f'{year_value:04d}-{month:02d}'
                return dt.date(year_value, month, int(day)).isoformat()
    except (ValueError, OverflowError) as exc:
        raise ValueError('invalid or ambiguous date') from exc
    raise ValueError('invalid date')


def normalize_currency(raw: str) -> str:
    text = raw.lower().replace('’', "'")
    for code, pattern in [('USD', r'\$|\busd\b|долл|доллар'), ('EUR', r'€|\beur\b|евро'), ('UZS', r"\buzs\b|сум|сўм|so['‘ʼ`]?m")]:
        if re.search(pattern, text):
            return code
    raise ValueError('currency ambiguous')


def normalize_vat(raw: str) -> bool:
    text = raw.lower()
    if re.search(r'без\s*ндс|qqs\s*siz|excl\.?\s*vat', text): return False
    if re.search(r'с\s*ндс|qqs\s*bilan|incl\.?\s*vat', text): return True
    raise ValueError('VAT ambiguous')


def normalize_unit(raw: str) -> str:
    text = raw.strip().lower()
    for unit, pattern in [('pack', r'упак|уп\.?$|pack'), ('blister', r'блистер|blister'), ('box', r'короб|box'), ('piece', r'шт\.?$|дона|piece')]:
        if re.search(pattern, text): return unit
    raise ValueError('unit ambiguous')


_CYR = dict(zip('абвгдежзийклмнопрстуфхцъыьэ', ['a','b','v','g','d','e','j','z','i','y','k','l','m','n','o','p','r','s','t','u','f','x','ts','','i','','e']))
_CYR.update({'ё':'yo','ж':'j','ч':'ch','ш':'sh','щ':'shch','ю':'yu','я':'ya','ў':'o','қ':'q','ғ':'g','ҳ':'h','г':'g','х':'h'})

def normalize_name(raw: str) -> str:
    text = unicodedata.normalize('NFKC', raw.replace('№', 'n')).lower()
    text = ''.join(_CYR.get(c, c) for c in text)
    text = re.sub(r'(?<=\d)\s*(mg|мг)', 'mg', text)
    text = re.sub(r'\bn\s*(?=\d)', 'n', text)
    return re.sub(r'[^\w]+', ' ', text).strip()


_REGIONS = {
    'Andijon': ['andijon', 'andijan'], 'Buxoro': ['buxoro', 'bukhara', 'buhoro'],
    'Fargona': ['fargona', 'fergana'], 'Jizzax': ['jizzax', 'jizak'],
    'Xorazm': ['xorazm', 'horezm', 'khorezm'], 'Namangan': ['namangan'],
    'Navoiy': ['navoiy', 'navoi'], 'Qashqadaryo': ['qashqadaryo', 'kashkadarya'],
    'Samarqand': ['samarqand', 'samarkand'], 'Sirdaryo': ['sirdaryo', 'syrdarya'],
    'Surxondaryo': ['surxondaryo', 'surhandarya'], 'Toshkent': ['toshkent', 'tashkent'],
    'Qoraqalpogiston': ['qoraqalpogiston', 'karakalpak'],
}

def normalize_region(raw: str) -> str:
    text = normalize_name(raw).replace("'", '')
    for region, variants in _REGIONS.items():
        if any(v in text for v in variants): return region
    raise ValueError('unknown region')
