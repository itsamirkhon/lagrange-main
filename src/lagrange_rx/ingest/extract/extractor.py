"""Extraction contract and independent verification of quoted evidence."""
from abc import ABC, abstractmethod
from difflib import SequenceMatcher
import re


def _normalized(text):
    return re.sub(r'\s+', ' ', text).strip().casefold()


def _quote_matches(quote, text):
    quote, text = _normalized(quote), _normalized(text)
    if not quote or not text:
        return False
    if quote in text:
        return True
    # Matching blocks locate candidate windows without comparing every page substring.
    blocks = SequenceMatcher(None, quote, text, autojunk=False).get_matching_blocks()
    minimum = max(1, int(len(quote) * .9))
    maximum = min(len(text), int(len(quote) / .9) + 1)
    for block in blocks:
        start = max(0, block.b - block.a)
        for length in range(minimum, maximum + 1):
            if SequenceMatcher(None, quote, text[start:start + length], autojunk=False).ratio() >= .9:
                return True
    return False


def validate_evidence(data, pages):
    """Return (accepted values, issues); pages maps one-based page numbers to text."""
    issues = []
    def visit(value, path):
        if value is None:
            return None
        if isinstance(value, dict) and ('raw' in value or 'evidence' in value):
            evidence = value.get('evidence')
            raw = value.get('raw')
            if isinstance(evidence, dict) and isinstance(raw, str) and raw.strip():
                page, quote = evidence.get('page'), evidence.get('quote')
                text = pages.get(page) if type(page) is int else None
                if isinstance(quote, str) and isinstance(text, str) and _normalized(raw) in _normalized(quote) and _normalized(raw) in _normalized(text) and _quote_matches(quote, text):
                    return value
            issues.append({'issue_type': 'unverified_evidence', 'severity': 'high', 'affected': [path], 'evidence': []})
            return None
        if isinstance(value, dict):
            return {key: result for key, item in value.items() if (result := visit(item, f'{path}.{key}' if path else key)) is not None}
        if isinstance(value, list):
            return [result for index, item in enumerate(value) if (result := visit(item, f'{path}[{index}]')) is not None]
        # Unproven scalar values cannot enter the graph.
        issues.append({'issue_type': 'unverified_evidence', 'severity': 'high', 'affected': [path], 'evidence': []})
        return None
    return visit(data, ''), issues


class Extractor(ABC):
    @abstractmethod
    def extract(self, doc_class, pages, *, image_data_url=None):
        """Return verified fields and issues."""
