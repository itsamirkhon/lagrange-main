"""Q0 pass: one Decisions request per document yields class, language, readability and visual-damage proposals.

Answers are proposals with probabilities. They never confirm anything: the class replaces a weak deterministic
result only through ``DocumentClassifier``, and the damage signals become reviewable DataQualityIssues.
"""
from __future__ import annotations

from .classification import CLASSES, Classification
from .decisions import review_policy
from .questions import load_questions

MAX_CLASS_CONFIDENCE = .9  # Decision probabilities are uncalibrated; never let the class skip review on its own.
READABILITY_ISSUE_AT = 1   # top level <= 1 (unreadable / poor)


class Q0Runner:
    def __init__(self, scorer):
        self.scorer = scorer
        self.questions = list(load_questions('q0').values())
        self._memo: dict[str, dict] = {}

    def run(self, text: str) -> dict:
        if text not in self._memo:
            answers = self.scorer.decide(text, self.questions)
            self._memo[text] = {a.qid: a for a in answers}
        return self._memo[text]

    def classify(self, filename, text, media_type=''):
        """The DocumentClassifier ``llm`` protocol, backed by the shared Q0 answer."""
        answer = self.run(text)['ALL.doc_class']
        top = answer.top if answer.top in CLASSES else 'unknown'
        return Classification(top, min(answer.confidence, MAX_CLASS_CONFIDENCE), ('decision:' + answer.model_id,), 'decision')

    def issues(self, document_id: str, text: str) -> list[dict]:
        answers = self.run(text)
        found = []

        def issue(kind, severity, qid, detail):
            a = answers[qid]
            found.append({'issue_type': kind, 'severity': severity, 'affected': [document_id], 'evidence': [], 'status': 'extracted_unconfirmed',
                          'detail': detail, 'decision': {'qid': qid, 'top': a.top, 'confidence': round(a.confidence, 4),
                                                         'distribution': {k: round(v, 4) for k, v in a.distribution.items()}, 'model_id': a.model_id}})

        readability = answers['ALL.readability']
        if int(readability.top) <= READABILITY_ISSUE_AT and readability.confidence >= .6:
            issue('unreadable', 'high', 'ALL.readability', f'Model rates the text {readability.top}/3 readable; values may be missing or wrong.')
        for qid, kind, text_ in (('ALL.has_handwritten_edits', 'handwritten_edits', 'Possible handwritten corrections.'),
                                 ('ALL.stamp_over_numbers', 'stamp_over_numbers', 'A stamp may cover numbers.')):
            a = answers[qid]
            if a.top == 'yes' and review_policy(a.confidence, a.top, money=True) != 'issue':
                issue(kind, 'medium', qid, text_)
        return found

    def language(self, text: str) -> str | None:
        top = self.run(text)['ALL.language'].top
        return None if top == 'unknown' else top
