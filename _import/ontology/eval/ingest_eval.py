"""Evaluate the public ingest API against simulation manifests, without exposing truth to ingest.

Usage: python eval/ingest_eval.py /path/to/sim --output eval
Optional --class-map JSON maps API class labels to simulation document types.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import hashlib
import re
from pathlib import Path
import tempfile
from time import perf_counter


def document_path(root: Path, relative: str) -> Path:
    root = root.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Manifest document escapes simulation root')
    return path


# Only semantically compatible issue types can count as detecting an operator.
_OPERATOR_ISSUES = {
    'X1': {'contradiction'}, 'X2': {'contradiction'}, 'D1x': {'duplicate'},
    'V1': {'contradiction', 'vat_mismatch', 'vat_ambiguous'}, 'U1': {'unit_mismatch', 'unit_ambiguous'},
    'A1': {'arithmetic'}, 'M1': {'missing'}, 'C1': {'currency_ambiguous'},
    'C3': {'contradiction'}, 'S1': {'stale'},
}
_TECHNICAL_ISSUES = {'parse_warning', 'parse_error', 'extraction_error', 'classification_required', 'unmapped_column'}


def score_corruptions(entries, document_ids, issues):
    """Conservative, one-to-one document/field matching; unmatchable != detected."""
    used = set()
    rows = []
    by_operator = {}
    for entry in entries:
        for corruption in entry.get('corruptions', []):
            operator, field = corruption['operator'], corruption['field']
            doc_id = document_ids.get(entry['document'])
            aliases = {('X1', 'min_order_qty'): 'moq', ('C1', 'unit_price'): 'currency',
                       ('C3', 'landed_cost_ex_vat_uzs'): 'fx_rate_on_entry', ('S1', 'quote_status'): 'valid_until'}
            field_marker = aliases.get((operator, field), field)
            if operator == 'M1':
                match = re.search(r'\d{4}-\d{2}', field)
                field_marker = match.group() if match else field
            found = None
            for index, issue in enumerate(issues):
                if index in used or issue.get('issue_type') not in _OPERATOR_ISSUES.get(operator, set()):
                    continue
                references = issue.get('affected', []) + [e.get('document_id') for e in issue.get('evidence', []) if isinstance(e, dict)]
                description = str(issue.get('field', '')) + ' ' + str(issue.get('detail', ''))
                if doc_id and doc_id in references and (operator == 'D1x' or field_marker.casefold() in description.casefold()):
                    found = index
                    break
            if found is not None:
                used.add(found)
            stats = by_operator.setdefault(operator, {'total': 0, 'detected': 0, 'matchable': operator in _OPERATOR_ISSUES})
            stats['total'] += 1
            stats['detected'] += found is not None
            rows.append({'document': entry['document'], 'operator': operator, 'field': field, 'detected': found is not None, 'issue_index': found})
    return {'total': len(rows), 'detected': len(used), 'by_operator': by_operator, 'matches': rows,
            'unmatched_issues': sum(i not in used and issue.get('issue_type') not in _TECHNICAL_ISSUES for i, issue in enumerate(issues)),
            'excluded_technical_issues': sum(issue.get('issue_type') in _TECHNICAL_ISSUES for issue in issues),
            'method': 'Conservative one-to-one matching by issue type, document hash and field or period (document-level for duplicates); unmatched does not prove a false positive.'}


def summarize(level: str, results: list[dict]) -> dict:
    groups = {}
    for result in results:
        group = groups.setdefault(result['doc_type'], {'documents': 0, 'classification': {'correct': 0, 'total': 0, 'unmapped_predictions': 0}, 'questions': 0, 'issues': 0, 'errors': 0})
        group['documents'] += 1
        group['questions'] += result['questions']
        group['issues'] += result['issues']
        group['errors'] += bool(result.get('error'))
        classification = group['classification']
        classification['total'] += 1
        prediction = result.get('prediction')
        if prediction == result['doc_type']:
            classification['correct'] += 1
        elif prediction not in (None, 'unknown', 'unreadable', 'irrelevant') and not (isinstance(prediction, str) and prediction.startswith('D') and prediction[1:].isdigit()):
            classification['unmapped_predictions'] += 1
    return {
        'level': level, 'documents': len(results),
        'questions': sum(r['questions'] for r in results),
        'issues': sum(r['issues'] for r in results),
        'errors': sum(bool(r.get('error')) for r in results),
        'elapsed_seconds': round(sum(r['elapsed_seconds'] for r in results), 3),
        'by_document_type': groups,
        'unavailable': {
            'field_accuracy': 'Truth fields are not aligned with extracted instance fields.',
            'evidence_accuracy': 'Source-to-value verification has not been evaluated.',
            'corruption_detection': 'Manifest corruptions have not been aligned to output issues.',
            'honesty': 'Unrecoverable corruption outcomes have not been aligned to output fields.',
            'link_accuracy': 'Predicted links have not been aligned to truth relations.',
            'accuracy_after_review': 'No simulated reviewer has been implemented.',
            'pnl_and_margin': 'No independently verified P&L evaluation has been implemented.',
            'cost': 'API does not report per-document model costs.',
        },
        'results': results,
    }


async def evaluate_level(root: Path, level: str, data_dir: Path, class_map: dict) -> dict:
    import httpx
    from lagrange_rx.api import create_app

    manifest = json.loads((root / 'worlds' / level / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('level') != level:
        raise ValueError(f'Manifest level does not match {level}')
    entries = manifest['documents']
    results = [{'document': e['document'], 'doc_type': e['doc_type'], 'prediction': None,
                'questions': 0, 'issues': 0, 'elapsed_seconds': 0} for e in entries]
    start = perf_counter()
    document_ids = {}
    issues = []
    try:
        files = []
        for entry in entries:
            path = document_path(root, entry['document'])
            content = path.read_bytes()
            document_ids[entry['document']] = hashlib.sha256(content).hexdigest()
            files.append(('files', (path.name, content)))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(data_dir)), base_url='http://test', timeout=None) as client:
            response = await client.post('/ingest/batches', files=files)
            response.raise_for_status()
            response = await client.get('/ingest/batches/' + response.json()['batch_id'])
            response.raise_for_status()
            batch = response.json()
        # IDs are content hashes; list preserves duplicate entries with identical content.
        by_id = {}
        for document in batch['documents']:
            by_id.setdefault(document['document_id'], []).append(document)
        for result in results:
            candidates = by_id.get(document_ids[result['document']], [])
            if not candidates:
                raise ValueError('Batch does not contain manifest document hash')
            document = candidates.pop(0)
            prediction = document.get('doc_class', 'unknown')
            result['prediction'] = class_map.get(prediction, prediction)
            result['raw_prediction'] = prediction
        if any(by_id.values()):
            raise ValueError('Batch contains unexpected documents')
        issues = batch['issues']
    except (OSError, ValueError, KeyError, httpx.HTTPError) as exc:
        for result in results:
            result['error'] = str(exc)
    report = summarize(level, results)
    report['elapsed_seconds'] = round(perf_counter() - start, 3)
    if not any(r.get('error') for r in results):
        # Cross-document findings cannot be apportioned to one document without double counting.
        for group in report['by_document_type'].values():
            group['questions'] = None
            group['issues'] = None
        for result in report['results']:
            result['questions'] = None
            result['issues'] = None
        report['questions'] = len(batch['review_queue'])
        report['issues'] = len(issues)
        report['issue_details'] = issues
        report['issue_types'] = {kind: sum(i.get('issue_type') == kind for i in issues) for kind in sorted({i.get('issue_type') for i in issues})}
        report['corruption_detection'] = score_corruptions(entries, document_ids, issues)
        report['unavailable'].pop('corruption_detection')
    return report



def summary_row(level, report):
    classes = [group['classification'] for group in report['by_document_type'].values()]
    detection = report.get('corruption_detection', {})
    technical = detection.get('excluded_technical_issues')
    return {'level': level, 'documents': report['documents'],
            'correct': sum(c['correct'] for c in classes), 'total': sum(c['total'] for c in classes),
            'unmapped': sum(c['unmapped_predictions'] for c in classes), 'questions': report['questions'],
            'technical_warnings': technical,
            'quality_findings': report['issues'] - technical if technical is not None else None,
            'corruptions_detected': detection.get('detected'), 'corruptions_total': detection.get('total'),
            'errors': report['errors'], 'elapsed_seconds': report['elapsed_seconds']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('simulation', type=Path)
    parser.add_argument('--output', type=Path, default=Path('eval'))
    parser.add_argument('--levels', nargs='+', choices=[f'L{i}' for i in range(5)], default=[f'L{i}' for i in range(5)])
    parser.add_argument('--class-map', type=Path, help='JSON object mapping API labels to D1, D2, etc.')
    args = parser.parse_args()
    mapping = json.loads(args.class_map.read_text(encoding='utf-8')) if args.class_map else {}
    if not isinstance(mapping, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()):
        parser.error('--class-map must contain a JSON object of strings')
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    with tempfile.TemporaryDirectory(prefix='lagrange-eval-') as temp:
        for level in args.levels:
            report = asyncio.run(evaluate_level(args.simulation.resolve(), level, Path(temp) / level, mapping))
            (args.output / f'report_ingest_{level}.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            row = summary_row(level, report)
            if not rows:
                rows.append('\t'.join(row.keys()))
            rows.append('\t'.join('' if v is None else str(v) for v in row.values()))
    (args.output / 'summary.tsv').write_text('\n'.join(rows) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
