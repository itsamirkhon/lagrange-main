#!/usr/bin/env python3
"""Prepare a labelled synthetic L0 graph for an offline judge-facing demo.

This script lives outside the product package because it uses simulation truth
for the synthetic approval exercise. Never run it on real client documents.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
from pathlib import Path
# Allow direct invocation as `python scripts/prepare_demo.py` from the repository.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eval.e2e import ROOT, build_graph


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    ap.add_argument('--source', type=Path, default=ROOT / 'sim/worlds/L0/documents')
    args = ap.parse_args()
    source = args.source.resolve()
    if not source.is_dir():
        ap.error('Synthetic L0 source directory not found')
    draft, human, graph, report = build_graph(source)
    graph.for_calculation()
    if len(graph.sales_records) == 0:
        raise RuntimeError('No sales records in synthetic graph; cannot prepare a usable demo')
    payload = graph.model_dump(mode='json', exclude_computed_fields=True)
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    graph_id = 'demo' + digest[:16]
    folder = args.data_dir / 'graphs'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (graph_id + '.json')
    if target.exists() and target.read_bytes() != encoded:
        raise RuntimeError('Existing graph differs: refusing to replace without inspection')
    target.write_bytes(encoded)
    print(json.dumps({'graph_id': graph_id, 'snapshot_hash': digest,
                      'source': 'SYNTHETIC L0 only', 'documents': len(draft.source_documents),
                      'sales_records': len(graph.sales_records), 'human_answers': human.load,
                      'destination': str(target)}, indent=2))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
