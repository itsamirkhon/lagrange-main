"""Verify hash, source class and freshness of operator-supplied registry exports.

Does not establish authenticity of a document; human reviewer must compare
with the government source and confirm exact fields/effective legal text.
No network access and no automatic registry import are performed here.
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from lagrange_rx.engine.regulatory_gate import Source, evidence_usable

MAX_EVIDENCE_BYTES = 25 * 1024 * 1024


class ManifestRow(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: str = Field(min_length=1, max_length=250)
    source: Source


class Manifest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int = Field(default=1, ge=1, le=1)
    rows: tuple[ManifestRow, ...] = Field(min_length=1, max_length=200)


def verify_manifest(manifest: Manifest, root: Path, on: date) -> list[dict]:
    """Reject path traversal, symlinks, stale records, duplicates and tampered bytes."""
    root = root.resolve(strict=True)
    results = []
    visited: set[tuple[str, str]] = set()
    for row in manifest.rows:
        candidate = root / row.path
        # Reject symlinks at any level rather than resolving outside root.
        if candidate.is_symlink() or any(parent.is_symlink() for parent in candidate.parents if parent != root):
            raise ValueError(f'symlink path not permitted: {row.path}')
        path = candidate.resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f'invalid evidence path: {row.path}')
        if path.stat().st_size == 0 or path.stat().st_size > MAX_EVIDENCE_BYTES:
            raise ValueError(f'evidence file size outside 1..{MAX_EVIDENCE_BYTES}: {row.path}')
        source = row.source
        if not evidence_usable(source, on, 'operator_snapshot', source.system):
            raise ValueError(f'untrusted, stale or mismatched source: {source.record_id}')
        key = (source.system, source.record_id)
        if key in visited:
            raise ValueError(f'duplicate source record: {key}')
        visited.add(key)
        hashed = hashlib.sha256(path.read_bytes()).hexdigest()
        if not source.sha256 or not hmac.compare_digest(source.sha256, hashed):
            raise ValueError(f'content hash mismatch: {row.path}')
        results.append({'path': row.path, 'system': source.system, 'record_id': source.record_id,
                        'sha256': hashed, 'reviewed_by': source.reviewed_by,
                        'source_url': source.url, 'retrieved_on': source.retrieved_on.isoformat()})
    return results
