"""Closed-choice decisions: uncertainty remains explicit and reviewable."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DecisionQuestion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    qid: str
    kind: Literal['boolean', 'choice', 'score']
    instructions: str
    options: list[str] | None = None
    rubric: list[str] | None = None

    @model_validator(mode='after')
    def validate_choices(self):
        if self.kind == 'choice' and (not self.options or not set(self.options) & {'unknown', 'none_of_these', 'cannot_tell', 'not_duplicate', 'not_stated', 'absent'}):
            raise ValueError('Choice must include an abstention option')
        if self.options and len(set(self.options)) != len(self.options):
            raise ValueError('Duplicate options')
        return self


class DecisionAnswer(BaseModel):
    model_config = ConfigDict(extra='forbid')
    qid: str
    distribution: dict[str, float]
    top: str
    confidence: float = Field(ge=0, le=1)
    model_id: str
    latency_ms: float = Field(ge=0)

    @model_validator(mode='after')
    def validate_distribution(self):
        if not self.distribution or any(not math.isfinite(v) or v < 0 or v > 1 for v in self.distribution.values()):
            raise ValueError('Invalid probabilities')
        if not math.isclose(sum(self.distribution.values()), 1, abs_tol=1e-6):
            raise ValueError('Probabilities must sum to one')
        if self.top not in self.distribution or self.distribution[self.top] != max(self.distribution.values()) or not math.isclose(self.confidence, self.distribution[self.top], abs_tol=1e-6):
            raise ValueError('Top and confidence must agree with distribution')
        return self


class DecisionScorer(Protocol):
    def decide(self, document: str, questions: list[DecisionQuestion]) -> list[DecisionAnswer]: ...


def review_policy(confidence: float, top: str, *, money: bool, contradictions: bool = False) -> str:
    if not 0 <= confidence <= 1:
        raise ValueError('Invalid confidence')
    if top in {'unknown', 'none_of_these', 'cannot_tell'} or confidence < .60:
        return 'issue'
    if not money and not contradictions and confidence >= .95:
        return 'auto'
    return 'review'


# Patterns require explicit markers; conflicting markers always lead to abstention.
_HEURISTIC_PATTERNS = {
    'ALL.doc_class': {
        'invoice': r'\b(?:сч[её]т[ -]?фактур\w*|invoice)\b',
        'contract': r'\b(?:договор\w*|contract)\b',
        'waybill': r'\b(?:накладн\w*|waybill)\b',
        'customs': r'\b(?:таможенн\w* деклараци\w*|customs declaration)\b',
        'supplier_quote': r'\b(?:коммерческ\w* предложени\w*|supplier quotation)\b',
        'payments': r'\b(?:плат[её]жн\w* поручени\w*|payment order)\b',
    },
    'Q.price_currency': {
        'USD': r'\b(?:usd|доллар\w*)\b|\$',
        'EUR': r'\b(?:eur|евро)\b|€',
        'RUB': r'\b(?:rub|руб(?:ль|ля|лей|\.)?)\b|₽',
        'UZS': r'\b(?:uzs|сум(?:ов|а)?|so[ʻ’\']?m)\b',
    },
    'Q.vat_status': {
        'incl_vat': r'\b(?:с ндс|(?<!не )включа(?:ет|я) ндс|(?<!not )including vat|vat included)\b',
        'excl_vat': r'\b(?:без ндс|не включая ндс|excluding vat|excluding tax|vat excluded)\b',
    },
}


_TABLE_SIGNATURES = {
    'sales_export': {'sale_id', 'month', 'channel', 'qty_packs'},
    'stock': {'lot_code', 'expiry_date', 'initial_qty', 'remaining_qty'},
    'customs': {'clearance_date', 'customs_post', 'fx_rate_uzs'},
    'carrier_quote': {'carrier', 'mode', 'per_shipment_usd', 'transit_days'},
    'registry_extract': {'effective_from', 'rn_uzs', 'chun_with_vat_uzs', 'chchn_with_vat_uzs'},
}
_HEADINGS = {
    'invoice': r'(?:(?:commercial )?invoice(?:\s*&\s*packing list)?|сч[её]т[ -]?фактур\w*)',
    'contract': r'(?:контракт\w*|договор\w*|contract)',
    'supplier_quote': r'(?:commercial quotation|коммерческ\w* предложени\w*)',
    'carrier_quote': r'(?:кп перевозчика|carrier quote)',
    'waybill': r'(?:накладная склада|warehouse waybill)',
    'customs': r'(?:учебная сводка по растаможке|customs declaration)',
    'registry_extract': r'(?:выписка из реестра предельных цен|price registry extract)',
    'regional_report': r'(?:отч[её]т регионального менеджера|regional sales summary|[^\n]*bo[ʻ’\']?yicha savdo hisoboti)',
    'email': r'email thread printout',
}


def _document_class(document):
    lines = [line.strip() for line in document.splitlines() if line.strip()]
    if not lines:
        return None
    # Email headers describe the enclosing message, not attached documents.
    header = '\n'.join(lines[:10])
    if re.search(r'(?im)^from:\s*\S+', header) and re.search(r'(?im)^subject:\s*\S+', header):
        return 'email'
    cells = {cell.strip().casefold() for cell in lines[0].split('\t')}
    table_matches = [kind for kind, required in _TABLE_SIGNATURES.items() if required <= cells]
    if len(table_matches) == 1:
        return table_matches[0]
    # Look at the title only: references in the body do not determine class.
    title = lines[0].split('\t')[0].strip()
    matches = [kind for kind, pattern in _HEADINGS.items() if re.search(r'\b(?:' + pattern + r')\b', title, re.IGNORECASE)]
    if len(matches) != 1:
        return None
    pattern = _HEADINGS[matches[0]]
    return matches[0] if re.match(r'^(?:' + pattern + r')\b', title, re.IGNORECASE) else None


class HeuristicScorer:
    """Conservative keyword baseline; scores are uncalibrated, not probabilities of truth."""
    def decide(self, document: str, questions: list[DecisionQuestion]) -> list[DecisionAnswer]:
        return [self._answer(document, question) for question in questions]

    @staticmethod
    def _answer(document: str, question: DecisionQuestion) -> DecisionAnswer:
        choices = question.options or ['unknown']
        abstain = next((v for v in ('unknown', 'none_of_these', 'cannot_tell', 'not_duplicate', 'not_stated', 'absent') if v in choices), choices[0])
        patterns = _HEURISTIC_PATTERNS.get(question.qid, {})
        detected = _document_class(document) if question.qid == 'ALL.doc_class' else None
        matches = ([detected] if detected else []) if question.qid == 'ALL.doc_class' else [name for name, pattern in patterns.items() if re.search(pattern, document, re.IGNORECASE)]
        top = matches[0] if len(matches) == 1 and matches[0] in choices else abstain
        # Leave uncertainty visible, including for a lack of evidence.
        confidence = .75 if len(choices) > 1 else 1.0
        remainder = (1 - confidence) / (len(choices) - 1) if len(choices) > 1 else 0
        distribution = {v: confidence if v == top else remainder for v in choices}
        return DecisionAnswer(qid=question.qid, distribution=distribution, top=top,
                              confidence=confidence, model_id='heuristic-keywords-v1', latency_ms=0)


class ReplayScorer:
    def __init__(self, cache_dir):
        self.root = Path(cache_dir)

    @staticmethod
    def _path(root, document_id):
        if not document_id or not all(c.isalnum() or c in '-_' for c in document_id):
            raise ValueError('Invalid document id')
        return Path(root) / (document_id + '.json')

    @classmethod
    def write(cls, root, document_id, answers):
        path = cls._path(root, document_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([a.model_dump() for a in answers], ensure_ascii=False), encoding='utf-8')

    def decide(self, document: str, questions: list[DecisionQuestion]) -> list[DecisionAnswer]:
        path = self._path(self.root, document)
        if not path.exists():
            raise LookupError('No replay for document')
        answers = {a.qid: a for a in (DecisionAnswer.model_validate(x) for x in json.loads(path.read_text(encoding='utf-8')))}
        result = []
        for q in questions:
            if q.qid not in answers:
                raise LookupError('No replay for question ' + q.qid)
            answer = answers[q.qid]
            if q.options and set(answer.distribution) != set(q.options):
                raise ValueError('Replay choices differ from question')
            result.append(answer)
        return result


class OpenRouterDecisionScorer:
    """Chat-model fallback; its reported probabilities require calibration."""
    def __init__(self, client, model_id):
        self.client, self.model_id = client, model_id

    def decide(self, document: str, questions: list[DecisionQuestion]) -> list[DecisionAnswer]:
        if any(q.kind != 'choice' or not q.options for q in questions):
            raise ValueError('Chat fallback currently supports explicit choice questions only')
        # The wrapper schema prevents a provider from returning out-of-set labels.
        schema = {'type': 'object', 'properties': {'answers': {'type': 'array', 'items': {'type': 'object', 'properties': {'qid': {'type': 'string'}, 'distribution': {'type': 'object', 'additionalProperties': {'type': 'number'}}, 'top': {'type': 'string'}, 'confidence': {'type': 'number'}}, 'required': ['qid', 'distribution', 'top', 'confidence'], 'additionalProperties': False}}}, 'required': ['answers'], 'additionalProperties': False}
        response = self.client.chat(self.model_id, 'Select only supplied options and return a probability distribution for each question. Use unknown when uncertain.', json.dumps({'document': document, 'questions': [q.model_dump() for q in questions]}, ensure_ascii=False), schema)
        supplied = {q.qid: q for q in questions}
        answers = {}
        for value in response.content['answers']:
            answer = DecisionAnswer(**value, model_id=response.model_id, latency_ms=response.latency_ms)
            if answer.qid not in supplied or set(answer.distribution) != set(supplied[answer.qid].options) or answer.qid in answers:
                raise ValueError('Unexpected question or answer options')
            answers[answer.qid] = answer
        if set(answers) != set(supplied):
            raise ValueError('Incomplete response')
        return [answers[q.qid] for q in questions]
