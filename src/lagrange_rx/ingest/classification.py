"""Deterministic document-class scoring (ru/uz/en) with an optional LLM fallback.

Signals are weighted: title line, table header, body keywords and file name.
Only generic words are used; no corpus-specific file-name scheme or simulation
data is consulted. File-name-only results stay at low confidence, so they go to
human review instead of silently entering calculation.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

CLASSES = ('sales_export', 'regional_report', 'supplier_quote', 'carrier_quote', 'contract', 'invoice', 'customs',
           'waybill', 'stock', 'payments', 'memo', 'email', 'registry_extract', 'irrelevant')
LLM_BELOW = .85          # deterministic confidence under which the optional LLM is asked
MIN_SCORE, MIN_MARGIN = 2.5, 1.0
TITLE, HEADER, BODY, NAME = 4.0, 3.0, 1.0, 2.5

_F = re.IGNORECASE
_TITLE = {
    'invoice': r'сч[её]т[ -]?фактур|инвойс|invoice|hisob[ -]?faktura|ҳисоб[ -]?фактура',
    'contract': r'договор|контракт|contract|shartnoma|шартнома',
    'supplier_quote': r'^(?!.*(?:перевозчик|carrier|tashuvchi)).*(?:коммерческ\w* предложени|commercial quot|supplier quot|price offer|tijoriy taklif)',
    'carrier_quote': r'перевозчик|carrier|freight|tashuvchi|тарифы доставки',
    'waybill': r'накладн|waybill|delivery note|yuk xati|nakladnoy',
    'customs': r'таможен|customs|bojxona|божхона|\bгтд\b',
    'registry_extract': r'выписк\w* из реестр|реестр\w* (?:цен|предельн)|price registry|registry extract|narxlar reestri|реестр',
    'regional_report': r'региональн\w*|regional|hududiy|viloyat\w* bo|бўйича сотув|сводка продаж|savdo hisoboti|сотув ҳисоботи',
    'payments': r'плат[её]жн|payment|кредит\w* (?:линия|график)|credit line|to.lov',
    'memo': r'служебн\w* записк|докладн\w* записк|\bmemo(?:randum)?\b|xizmat xati|меморандум',
    'email': r'переписк|email thread|e-mail thread|correspondence|yozishma',
    'irrelevant': r'^(?:реклама|рекламн\w+|advert\w*|promo\w*|reklama)\b|прайс[ -]?лист\b',
}
_TITLE_BOOST = {'email': 1.0, 'carrier_quote': 1.0, 'registry_extract': .0}  # a specific form beats a topic word
_HEADER = {
    'sales_export': r'документ реализации|код 1с|номенклатура|канал сбыта|ндс 12|sale_id|qty_packs|ед\. ?изм',
    'stock': r'серия|накладная|срок годн|нач\. остаток|списано|списан|lot_code|expiry_date|remaining_qty|initial_qty',
    'payments': r'плат\. док|погашение|выборка|исх\. остаток|вх\. остаток|проценты',
    'customs': r'номер гтд|дата гтд|таможенный пост|курс цб|customs_post|clearance_date|fx_rate',
    'carrier_quote': r'код маршрута|перевозчик|за партию|за упак|коридор|вид транспорта|carrier|per_shipment|transit_days',
    'registry_extract': r'реестровая запись|rn uzs|\bbx\b|chun|chchn|effective_from|rn_uzs',
    'regional_report': r'^(?:область|hudud|viloyat|region|ҳудуд|вилоят)$|^(?:покупатель|xaridor|mijoz|counterparty)$|^(?:oy|davr|месяц|period)$',
    'supplier_quote': r'номер кп|действует до|valid_until|\bmoq\b|incoterms',
    'invoice': r'инвойс|invoice|сч[её]т',
}
_HEADER_MIN = {'regional_report': 3}
_BODY = {
    'invoice': r'packing list|invoice|инвойс|сч[её]т[ -]?фактур',
    'contract': r'контрагент|подписан|действует до|counterparty|shartnoma',
    'supplier_quote': r'\bmoq\b|incoterms|цена cip|supplier:|коммерческ\w* предложени',
    'waybill': r'накладная|позиции|waybill',
    'customs': r'гтд|таможен|customs',
    'registry_extract': r'chun|chchn|реестр|rn:',
    'carrier_quote': r'перевозчик|carrier|route|маршрут',
    'email': r'^from:|^subject:|^to:',
    'irrelevant': r'\botc-\d+|не относится|реклама|спецпредложение|promo',
}
_BODY_WEIGHT = {'irrelevant': 3.0}  # explicit junk markers are rare in genuine documents
_NAME = {
    'supplier_quote': r'quot|\bkp\b|кп|offer|предложен|taklif',
    'carrier_quote': r'carrier|freight|forwarder|перевозчик|tashuvchi|transport',
    'contract': r'contract|договор|shartnoma|\bcnt\b',
    'invoice': r'invoice|инвойс|hisob[ -]?faktura|\binv\b',
    'waybill': r'waybill|nakladn|накладн|delivery[ _-]?note',
    'customs': r'customs|\bgtd\b|гтд|таможен|bojxona',
    'registry_extract': r'regist|reestr|реестр',
    'email': r'e-?mail|письм|\bmail\b|\beml\b|correspondence|переписк',
    'memo': r'\bmemo|служебн|докладн|xizmat',
    'payments': r'payment|credit|платеж|платёж|to.lov|кредит',
    'stock': r'\bstock|warehouse|склад|остатк|ombor',
    'sales_export': r'\b1c\b|1с|sales[ _-]?export|продаж[и]? 1',
    'regional_report': r'regional|region|hududiy|област|регион',
    'irrelevant': r'junk|spam|promo|blank|unrelated|advert|реклам|мусор',
}


@dataclass(frozen=True)
class Classification:
    doc_class: str
    confidence: float
    signals: tuple = ()
    method: str = 'deterministic'


def _tokens(name: str) -> str:
    # Split on punctuation/underscores but keep letters and digits together; add spaces so \b works.
    return re.sub(r'[_\-.]+', ' ', name.rsplit('.', 1)[0] if '.' in name else name)


def _lines(pages):
    return [line.strip() for page in pages for line in page.splitlines() if line.strip()]


def _cells(line):
    return [c.strip() for c in line.split('\t') if c.strip()]


def _header_cells(lines):
    for line in lines[:10]:
        cells = _cells(line)
        if len(set(cells)) >= 3:
            return [c.casefold() for c in cells]
    return []


def score_document(filename, pages, media_type=''):
    scores, signals = {}, []

    def add(kind, weight, why):
        scores[kind] = scores.get(kind, 0.0) + weight
        signals.append(f'{why}:{kind}')

    lines = _lines(pages)
    if lines:
        head = '\n'.join(lines[:10])
        if re.search(r'(?im)^from:\s*\S+', head) and re.search(r'(?im)^subject:\s*\S+', head):
            add('email', TITLE + 2, 'mail_headers')
        title = _cells(lines[0])[0] if _cells(lines[0]) else ''
        for kind, pattern in _TITLE.items():
            if re.search(pattern, title, _F):
                add(kind, TITLE + _TITLE_BOOST.get(kind, 0), 'title')
        header = _header_cells(lines)
        for kind, pattern in _HEADER.items():
            hits = sum(bool(re.search(pattern, cell, _F)) for cell in header)
            if hits >= _HEADER_MIN.get(kind, 2):
                add(kind, HEADER, 'header')
        body = '\n'.join(lines[:60])
        for kind, pattern in _BODY.items():
            hits = len(re.findall(pattern, body, _F | re.M))
            if hits:
                add(kind, min(hits, 2) * _BODY_WEIGHT.get(kind, BODY), 'body')
    stem = _tokens(filename)
    for kind, pattern in _NAME.items():
        if re.search(pattern, stem, _F):
            add(kind, NAME, 'filename')
    return scores, signals, bool(lines)


def classify_document(filename, pages, tables=(), media_type='') -> Classification:
    scores, signals, has_text = score_document(filename or '', list(pages), media_type)
    if media_type == 'message/rfc822' or (filename or '').lower().endswith('.eml'):
        scores['email'] = scores.get('email', 0.0) + TITLE
        signals.append('media:email')
    ranked = sorted(scores.items(), key=lambda item: -item[1])
    if not ranked or ranked[0][1] < MIN_SCORE or (len(ranked) > 1 and ranked[0][1] - ranked[1][1] < MIN_MARGIN):
        return Classification('unknown', 0.0, tuple(signals))
    kind, score = ranked[0]
    content = any(s.split(':')[0] in ('title', 'header', 'mail_headers', 'media') and s.endswith(':' + kind) for s in signals)
    confidence = .95 if score >= 7 else .9 if content and score >= 4 else .8 if content else .65
    return Classification(kind, confidence, tuple(signals))


class DocumentClassifier:
    """Deterministic first; an optional LLM only for weak or missing evidence.

    An injected legacy ``scorer`` (DecisionScorer protocol) replaces the
    deterministic path, which keeps older callers and tests working.
    """

    def __init__(self, llm=None, scorer=None):
        self.llm, self.scorer = llm, scorer

    def classify(self, filename, pages, media_type='', tables=()) -> Classification:
        if self.scorer is not None:
            from .decisions import DecisionQuestion
            choices = list(CLASSES) + ['unknown']
            question = DecisionQuestion(qid='ALL.doc_class', kind='choice', instructions='Classify this document', options=choices)
            answer = self.scorer.decide('\n'.join(pages), [question])[0]
            return Classification(answer.top if answer.top in choices else 'unknown', answer.confidence, (), 'scorer')
        result = classify_document(filename, pages, tables, media_type)
        if result.confidence >= LLM_BELOW or self.llm is None:
            return result
        try:
            answer = self.llm.classify(filename, '\n'.join(pages), media_type)
        except Exception:  # replay miss, budget, network: the deterministic answer stands
            return Classification(result.doc_class, result.confidence, result.signals + ('llm_unavailable',), result.method)
        if answer.doc_class == 'unknown' or answer.doc_class not in CLASSES:
            return result
        return answer
