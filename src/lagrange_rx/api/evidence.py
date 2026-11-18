"""/evidence/reports : read-only view of the JSON reports in the eval directory (LAGRANGE_EVAL_DIR, default ./eval)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException

MAX_REPORT_BYTES = 1024 * 1024  # graph dumps and the like are not reports


def _snapshot_id(data) -> str | None:
    if isinstance(data, dict):
        for key in ('snapshot_id', 'ingest_snapshot_id'):
            if isinstance(data.get(key), str) and data[key]:
                return data[key]
    return None


def make_router(eval_dir: str | Path | None = None) -> APIRouter:
    router = APIRouter(prefix='/evidence')
    raw = eval_dir if eval_dir is not None else os.getenv('LAGRANGE_EVAL_DIR', '')
    root = Path(raw).resolve() if str(raw).strip() else Path(__file__).resolve().parents[3] / 'eval'

    def entries() -> dict[str, Path]:
        return {p.stem: p for p in sorted(root.glob('*.json')) if p.is_file()} if root.is_dir() else {}

    def load(path: Path) -> dict:
        if path.stat().st_size > MAX_REPORT_BYTES:
            return {'name': path.stem, 'snapshot_id': None, 'skipped': f'larger than {MAX_REPORT_BYTES} bytes'}
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except ValueError as exc:
            return {'name': path.stem, 'snapshot_id': None, 'skipped': f'invalid JSON: {exc}'}
        return {'name': path.stem, 'snapshot_id': _snapshot_id(data), 'data': data}

    @router.get('/reports')
    async def reports():
        return {'directory_exists': root.is_dir(), 'reports': [load(p) for p in entries().values()]}

    @router.get('/reports/{name}')
    async def report(name: str):
        path = entries().get(name)
        if path is None:
            raise HTTPException(404, 'report not found')
        return load(path)

    return router
