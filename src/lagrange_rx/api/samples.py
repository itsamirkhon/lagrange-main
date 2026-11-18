"""/ingest/samples : sample document sets from a configured directory (LAGRANGE_SAMPLES_DIR).

Each immediate sub-directory is one sample (e.g. L0..L4; a nested `documents/` folder is used when present). Its
files are served as a zip that the UI posts to /ingest/batches. The product does not know where the documents come
from and imports nothing from the simulation.
"""
from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

MAX_ARCHIVE_BYTES = 25 * 1024 * 1024  # same cap as /ingest/batches per upload


def _files(folder: Path) -> tuple[Path, list[Path]]:
    base = folder / 'documents' if (folder / 'documents').is_dir() else folder
    files = sorted(p for p in base.rglob('*') if p.is_file() and not any(part.startswith('.') for part in p.relative_to(base).parts))
    return base, files


def make_router(samples_dir: str | Path | None = None) -> APIRouter:
    router = APIRouter(prefix='/ingest/samples')
    raw = samples_dir if samples_dir is not None else os.getenv('LAGRANGE_SAMPLES_DIR', '')
    root = Path(raw).resolve() if str(raw).strip() else None

    def samples() -> dict[str, tuple[Path, list[Path]]]:
        if root is None or not root.is_dir():
            return {}
        found = {d.name: _files(d) for d in sorted(root.iterdir()) if d.is_dir() and not d.name.startswith('.')}
        return {name: bf for name, bf in found.items() if bf[1]}

    @router.get('')
    async def list_samples():
        found = samples()
        return {'configured': root is not None, 'directory_exists': bool(root and root.is_dir()),
                'samples': [{'name': n, 'files': len(f), 'bytes': sum(p.stat().st_size for p in f)} for n, (_, f) in found.items()]}

    @router.get('/{name}/archive')
    async def archive(name: str):
        entry = samples().get(name)
        if entry is None:
            raise HTTPException(404, 'sample not found')
        base, files = entry
        if sum(p.stat().st_size for p in files) > MAX_ARCHIVE_BYTES:
            raise HTTPException(413, 'sample is larger than the 25 MB upload limit')
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            for p in files:
                z.write(p, p.relative_to(base).as_posix())
        return Response(buf.getvalue(), media_type='application/zip')

    return router
