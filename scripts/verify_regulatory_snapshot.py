"""Check downloaded source bytes against a reviewed evidence manifest.

Usage: python scripts/verify_regulatory_snapshot.py --manifest ./manifest.json --root ./evidence --on 2026-11-18
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from lagrange_rx.engine.regulatory_import import Manifest, verify_manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--on', type=date.fromisoformat, required=True)
    args = parser.parse_args()
    manifest = Manifest.model_validate_json(args.manifest.read_text(encoding='utf-8'))
    results = verify_manifest(manifest, args.root, args.on)
    print(json.dumps({'verified_file_digests': len(results), 'regulatory_approval': False,
                      'evidence': results}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
