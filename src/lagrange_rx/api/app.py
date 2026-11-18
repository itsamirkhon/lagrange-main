"""The one FastAPI application: /ingest, /ontology, /engine, /health and (optionally) the static UI.

Run: `lagrange-rx serve` (loopback only) or `uvicorn lagrange_rx.api.app:app --host 127.0.0.1`.
There is no authentication: the service must never be exposed beyond the local machine.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from lagrange_rx.api import engine as engine_api
from lagrange_rx.api import ingest as ingest_api
from lagrange_rx.api import ontology as ontology_api

__version__ = '0.1.0'


def _web_dir() -> Path | None:
    env = os.getenv('LAGRANGE_WEB_DIR')
    candidates = [Path(env)] if env else [Path(__file__).resolve().parents[3] / 'web', Path.cwd() / 'web']
    return next((p for p in candidates if (p / 'index.html').is_file()), None)


def create_app(data_dir: str | Path | None = None, *, scorer=None, extractor=None, fx_provider=None,
               web_dir: str | Path | None = None, serve_web: bool = True) -> FastAPI:
    app = FastAPI(title='LAGRANGE Rx', version=__version__)
    ingest_router, root = ingest_api.make_router(data_dir, scorer=scorer, extractor=extractor, fx_provider=fx_provider)
    app.include_router(ingest_router)
    app.include_router(ontology_api.make_router(root))
    app.include_router(engine_api.make_router(root))

    @app.get('/health')
    async def health():
        return {'status': 'ok', 'version': __version__}

    web = (Path(web_dir) if web_dir else _web_dir()) if serve_web else None
    if web is not None and (web / 'index.html').is_file():
        app.mount('/', StaticFiles(directory=web, html=True), name='web')  # last: API routes win
    return app


app = create_app()
