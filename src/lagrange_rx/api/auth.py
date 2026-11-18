"""Optional HTTP Basic protection for the whole app (UI, API, docs). /health stays open for probes."""
from __future__ import annotations

import base64
import hmac
import os

from fastapi import FastAPI
from starlette.responses import Response

OPEN_PATHS = {'/health'}


def configured_password(explicit: str | None = None) -> str | None:
    return explicit or os.getenv('LAGRANGE_AUTH_PASSWORD') or None


def _credentials(header: str | None) -> tuple[str, str] | None:
    if not header or not header.lower().startswith('basic '):
        return None
    try:
        user, _, password = base64.b64decode(header[6:]).decode('utf-8').partition(':')
    except ValueError:
        return None
    return user, password


def install_basic_auth(app: FastAPI, password: str, user: str | None = None) -> None:
    expected_user = user or os.getenv('LAGRANGE_AUTH_USER') or 'lagrange'

    @app.middleware('http')
    async def require_password(request, call_next):
        if request.url.path in OPEN_PATHS:
            return await call_next(request)
        given = _credentials(request.headers.get('authorization'))
        ok = given is not None and hmac.compare_digest(given[0], expected_user) and hmac.compare_digest(given[1], password)
        if not ok:
            return Response('authentication required', status_code=401, headers={'WWW-Authenticate': 'Basic realm="LAGRANGE Rx"'})
        return await call_next(request)
