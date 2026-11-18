"""Embeddings vs the n-gram baseline for name matching (INGEST.md 2.6, DoD item 'embeddings only with proven gain').

Truth (sim/truth/*.json) is read here only, as the allowed evaluation module. Canonical names are regions, SKU
trade names (plus dose/pack twins that must stay distinct) and counterparties; queries are Latin/Cyrillic/typo/format
variants. Methods: trigram baseline, raw embedding cosine, and the production hybrid (trigram >= .75, else embedding
with a digit veto, as in ingest/resolve.py).

    python -m eval.embeddings_eval --models baai/bge-m3 google/gemini-embedding-2 --budget 0.5
"""
from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

from lagrange_rx.ai.factory import Embedder, build_client
from lagrange_rx.ingest.resolve import EMBEDDING_THRESHOLD, _digits
from lagrange_rx.ingest.similarity import similarity

ROOT = Path(__file__).resolve().parents[1]
LAT2CYR = [('shch', 'щ'), ('sh', 'ш'), ('ch', 'ч'), ('yo', 'ё'), ('yu', 'ю'), ('ya', 'я'), ('kh', 'х'), ('ts', 'ц'),
           ('a', 'а'), ('b', 'б'), ('d', 'д'), ('e', 'е'), ('f', 'ф'), ('g', 'г'), ('h', 'х'), ('i', 'и'), ('j', 'ж'), ('k', 'к'),
           ('l', 'л'), ('m', 'м'), ('n', 'н'), ('o', 'о'), ('p', 'п'), ('q', 'қ'), ('r', 'р'), ('s', 'с'), ('t', 'т'), ('u', 'у'),
           ('v', 'в'), ('x', 'х'), ('y', 'й'), ('z', 'з')]


def to_cyr(text: str) -> str:
    out, i, low = [], 0, text
    while i < len(low):
        for lat, cyr in LAT2CYR:
            if low[i:i + len(lat)].lower() == lat:
                out.append(cyr.upper() if low[i].isupper() else cyr)
                i += len(lat)
                break
        else:
            out.append(low[i])
            i += 1
    return ''.join(out)


def typo(text: str, rng: random.Random) -> str:
    letters = [i for i, c in enumerate(text) if c.isalpha()]
    i = rng.choice(letters[1:-1])
    return text[:i] + text[i] + text[i:]  # doubled letter, the sim's own SKU typo style


def build_cases(seed: int = 7):
    rng = random.Random(seed)
    sales = json.loads((ROOT / 'sim/truth/sales.json').read_text(encoding='utf-8'))
    skus = json.loads((ROOT / 'sim/truth/skus.json').read_text(encoding='utf-8'))
    regions = sorted({(r['region_name_ru'], r['region_name_uz_lat'], r['region_name_uz_cyr']) for r in sales})
    cases = {'region': ([ru for ru, _, _ in regions], []), 'sku': ([], []), 'counterparty': ([], [])}
    for ru, lat, cyr in regions:
        short = re.sub(r'\s*(область|вилояти|viloyati)$', '', ru).strip()
        for q in (lat, cyr, lat.replace('viloyati', 'вилояти'), short + ' обл.', lat.lower(), cyr.upper()):
            cases['region'][1].append((q, ru))
    names = [s['trade_name'] for s in skus]
    canon = list(names)
    for s in skus:  # twins that differ only by dose or pack: they must never be merged
        m = re.search(r'(\d+) мг №(\d+)', s['trade_name'])
        brand = s['trade_name'].split()[0]
        canon.append(f'{brand} {int(m.group(1)) * 2} мг №{m.group(2)}')
        canon.append(f'{brand} {m.group(1)} мг №{int(m.group(2)) * 2}')
    cases['sku'] = (canon, [])
    for name in canon:
        m = re.match(r'(\w+) (\d+) мг №(\d+)', name)
        brand, dose, pack = m.groups()
        for q in (f'{brand} {dose}mg N{pack}', f'{to_cyr(brand)} {dose} мг №{pack}', f'{brand.upper()} {dose} MG №{pack}',
                  typo(f'{brand} {dose}mg N{pack}', rng), f'{to_cyr(brand)}  {dose}мг № {pack}'):
            cases['sku'][1].append((q, name))
    parties = sorted({re.sub(r'\s*\(SYNTHETIC\)', '', s['counterparty_name']) for s in sales})
    cases['counterparty'] = (parties, [])
    for p in parties:
        quoted = re.search(r'«(.+?)»', p)
        if quoted and re.search(r'[A-Za-z]', quoted.group(1)):
            core = quoted.group(1)
            for q in (f'{core} LLC', to_cyr(core), core.upper(), typo(core, rng)):
                cases['counterparty'][1].append((q, p))
        else:
            kind, region = re.match(r'(.+?) \((.+)\)', p).groups()
            cases['counterparty'][1].append((f'{kind} {region}'.lower(), p))
            cases['counterparty'][1].append((f'{region} {kind}', p))
    return cases


def rankings(canon, queries, embedder):
    vectors = embedder.vectors(canon + [q for q, _ in queries]) if embedder else None
    out = {'trigram': [], 'embedding': [], 'hybrid': []}
    for n, (query, truth) in enumerate(queries):
        tri = [similarity(query, c) for c in canon]
        if embedder:
            q = vectors[len(canon) + n]
            emb = [sum(x * y for x, y in zip(q, v)) / ((sum(x * x for x in q) ** .5) * (sum(y * y for y in v) ** .5)) for v in vectors[:len(canon)]]
            ok = [_digits(query) == _digits(c) for c in canon]
            # Production logic: the baseline stands when it is confident; the embedding only fills gaps, digit-vetoed.
            hybrid = [t if t >= .75 else (e if ok_ and e >= EMBEDDING_THRESHOLD else t * .5) for t, e, ok_ in zip(tri, emb, ok)]
        else:
            emb = hybrid = None
        for name, scores in (('trigram', tri), ('embedding', emb), ('hybrid', hybrid)):
            if scores is not None:
                order = sorted(range(len(canon)), key=lambda i: -scores[i])
                out[name].append((canon.index(truth), [canon[i] for i in order].index(truth), scores[order[0]], canon[order[0]] == truth))
    return out


def pair_sweep(canon, queries, embedder, thresholds=(.75, .80, .85, .90)):
    """Candidate-match precision/recall over every (query, canonical) pair, as resolve.py decides it."""
    vectors = embedder.vectors(canon + [q for q, _ in queries])
    result = {}
    for threshold in (None, *thresholds):
        tp = fp = fn = 0
        for n, (query, truth) in enumerate(queries):
            q = vectors[len(canon) + n]
            for name, v in zip(canon, vectors[:len(canon)]):
                tri = similarity(query, name) >= .75
                emb = threshold is not None and _digits(query) == _digits(name) and \
                    sum(x * y for x, y in zip(q, v)) / ((sum(x * x for x in q) ** .5) * (sum(y * y for y in v) ** .5)) >= threshold
                hit = tri or emb
                tp += hit and name == truth
                fp += hit and name != truth
                fn += (not hit) and name == truth
        precision = tp / (tp + fp) if tp + fp else 1.0
        recall = tp / (tp + fn)
        result['baseline' if threshold is None else str(threshold)] = {'precision': round(precision, 4), 'recall': round(recall, 4),
                                                                         'f1': round(2 * precision * recall / (precision + recall), 4)}
    return result


def metrics(rows):
    n = len(rows)
    top1 = sum(r[1] == 0 for r in rows) / n
    mrr = sum(1 / (r[1] + 1) for r in rows) / n
    ranked = sorted(rows, key=lambda r: -r[2])
    best, correct = 0.0, 0
    for k, r in enumerate(ranked, 1):  # recall at >= 95% precision of the top-1 match
        correct += r[3]
        if correct / k >= .95:
            best = correct / n
    return {'n': n, 'top1': round(top1, 4), 'mrr': round(mrr, 4), 'recall_at_95_precision': round(best, 4)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--models', nargs='+', default=[])
    parser.add_argument('--budget', type=float, default=0.5)
    parser.add_argument('--cache', default=str(ROOT / 'data/cache/embeddings_eval'))
    parser.add_argument('--out', default=str(ROOT / 'eval/embeddings_report.json'))
    args = parser.parse_args()
    cases = build_cases()
    report = {'cases': {k: {'canonical': len(c), 'queries': len(q)} for k, (c, q) in cases.items()}, 'models': {}}
    base = {k: metrics(rankings(c, q, None)['trigram']) for k, (c, q) in cases.items()}
    report['baseline_trigram'] = base
    for model in args.models:
        embedder = Embedder(build_client('openrouter', args.cache, args.budget, 0.05), model)
        result = {}
        try:
            for kind, (canon, queries) in cases.items():
                ranks = rankings(canon, queries, embedder)
                result[kind] = {method: metrics(ranks[method]) for method in ('trigram', 'embedding', 'hybrid')}
                result[kind]['pair_sweep'] = pair_sweep(canon, queries, embedder)
        except Exception as exc:  # one unavailable model must not hide the others
            report['models'][model] = {'error': f'{type(exc).__name__}: {exc}'}
            print(model, 'FAILED', exc)
            continue
        report['models'][model] = result
        print(model, json.dumps({k: v['hybrid'] for k, v in result.items()}))
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
