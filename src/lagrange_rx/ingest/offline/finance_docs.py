"""D10 expense rows (article names are English inside a Russian table) and D13 registry rows."""
from __future__ import annotations

from datetime import date

from .sales import number

# Articles as they appear in the client's table -> ontology CostItem.category.
ARTICLES = {'distribution': 'distribution', 'marketing': 'marketing', 'warehouse': 'warehousing',
            'warehousing': 'warehousing', 'financing': 'financing', 'writeoffs': 'write_off',
            'write-offs': 'write_off', 'general': 'g_and_a'}


def parse_expenses(rows) -> list[dict]:
    header = [str(c).strip() for c in rows[0]]
    col = {name: header.index(name) for name in ('Акт/Док.', 'Месяц', 'Номенклатура', 'Статья', 'Сумма UZS')}
    out = []
    for n, r in enumerate(rows[1:], 1):
        if not r or not r[0]:
            continue
        article = str(r[col['Статья']]).strip()
        out.append({'row': n, 'doc_no': str(r[col['Акт/Док.']]).strip(), 'period': str(r[col['Месяц']]).strip(),
                    'label': str(r[col['Номенклатура']]).strip(), 'article': article,
                    'category': ARTICLES.get(article.casefold()), 'amount': number(r[col['Сумма UZS']])})
    return out


def parse_registry(rows) -> list[dict]:
    header = [str(c).strip() for c in rows[0]]
    col = {name: header.index(name) for name in ('Реестровая запись', 'Препарат', 'Действует с', 'Тип события', 'Rn UZS', 'B UZS',
                                                 'Bx UZS', 'Расч. база без НДС', 'Опт без НДС', 'ChUN с НДС', 'ChChN с НДС')}
    return [{'number': r[col['Реестровая запись']].strip(), 'product': r[col['Препарат']].strip(),
             'effective_from': date.fromisoformat(r[col['Действует с']].strip()), 'event': r[col['Тип события']].strip(),
             'rn': number(r[col['Rn UZS']]), 'b': number(r[col['B UZS']]), 'bx': number(r[col['Bx UZS']]),
             'base': number(r[col['Расч. база без НДС']]), 'wholesale': number(r[col['Опт без НДС']]),
             'chun': number(r[col['ChUN с НДС']]), 'chchn': number(r[col['ChChN с НДС']])}
            for r in rows[1:] if r and r[0]]
