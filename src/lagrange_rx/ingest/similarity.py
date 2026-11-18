"""Offline character trigram TF-IDF similarity for names, not numeric fields."""
from collections import Counter
from math import log, sqrt
from .normalize import normalize_name


def _trigrams(text: str) -> Counter:
    text = f'  {normalize_name(text)}  '
    return Counter(text[i:i+3] for i in range(len(text)-2))


def _scores(query: str, choices: list[str]) -> list[float]:
    documents = [_trigrams(query), *(_trigrams(choice) for choice in choices)]
    frequencies = Counter(term for doc in documents for term in doc)
    weights = {term: log((1 + len(documents)) / (1 + count)) + 1 for term, count in frequencies.items()}
    vectors = [{term: value * weights[term] for term, value in doc.items()} for doc in documents]
    def norm(vector):
        return sqrt(sum(value * value for value in vector.values()))
    source = vectors[0]
    source_norm = norm(source)
    return [sum(value * target.get(term, 0) for term, value in source.items()) / (source_norm * norm(target))
            if source_norm and norm(target) else 0.0 for target in vectors[1:]]


def similarity(a: str, b: str) -> float:
    return _scores(a, [b])[0]


def candidates(query: str, choices: list[str], limit: int = 10) -> list[tuple[str, float]]:
    return sorted(zip(choices, _scores(query, choices)), key=lambda item: (-item[1], item[0]))[:limit]
