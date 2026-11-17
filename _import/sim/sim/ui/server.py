"""Stdlib ThreadingHTTPServer and route dispatcher for the LAGRANGE Rx Web UI."""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from sim.ui.api import (
    evaluate_scenario,
    get_document_preview,
    get_overview,
    get_truth_table,
    get_world_manifest,
    list_truth_tables,
    regenerate_world_action,
    resolve_document_path,
)

_STATIC_DIR = Path(__file__).resolve().parent / "static"

_MIME_BY_EXT: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv; charset=utf-8",
    ".eml": "message/rfc822",
}


def _json_response(payload: Any, status: int = HTTPStatus.OK) -> tuple[int, str, bytes]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return int(status), "application/json; charset=utf-8", body


def _serve_static_file(static_dir: Path, filename: str) -> tuple[int, str, bytes]:
    if not filename or any(sep in filename for sep in ("/", "\\", "..")):
        raise ValueError(f"Invalid static path: {filename!r}")
    base = static_dir.resolve()
    target = (base / filename).resolve()
    if target.parent != base or not target.is_file():
        raise FileNotFoundError(f"Static asset not found: {filename!r}")
    mime = _MIME_BY_EXT.get(target.suffix.lower(), "application/octet-stream")
    return int(HTTPStatus.OK), mime, target.read_bytes()


def _parse_json_body(raw_body: bytes) -> dict[str, Any]:
    if not raw_body.strip():
        return {}
    parsed = json.loads(raw_body.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("Request JSON body must be an object")
    return parsed


def _dispatch_world_route(
    parts: list[str],
    sim_dir: Path,
) -> tuple[int, str, bytes] | None:
    if parts[:2] != ["api", "worlds"]:
        return None
    if len(parts) == 3:
        return _json_response(get_world_manifest(sim_dir, parts[2]))
    if len(parts) == 5 and parts[3] == "document":
        doc_path = resolve_document_path(sim_dir, parts[2], parts[4])
        mime = _MIME_BY_EXT.get(doc_path.suffix.lower(), "application/octet-stream")
        return int(HTTPStatus.OK), mime, doc_path.read_bytes()
    if len(parts) == 5 and parts[3] == "preview":
        return _json_response(get_document_preview(sim_dir, parts[2], parts[4]))
    return None


def _dispatch_truth_route(
    parts: list[str],
    query: dict[str, list[str]],
    sim_dir: Path,
) -> tuple[int, str, bytes] | None:
    if parts == ["api", "truth"]:
        return _json_response({"tables": list_truth_tables(sim_dir)})
    if len(parts) == 3 and parts[0] == "api" and parts[1] == "truth":
        limit_raw = query.get("limit", ["200"])[0]
        sku_id = query.get("sku_id", [None])[0]
        return _json_response(get_truth_table(sim_dir, parts[2], limit=int(limit_raw), sku_id=sku_id))
    return None


def _dispatch_get(
    path: str,
    query: dict[str, list[str]],
    sim_dir: Path,
    static_dir: Path,
) -> tuple[int, str, bytes]:
    if path == "/":
        return _serve_static_file(static_dir, "index.html")
    if path.startswith("/static/"):
        return _serve_static_file(static_dir, unquote(path[len("/static/"):]))
    if path == "/api/overview":
        return _json_response(get_overview(sim_dir))
    if path == "/api/evaluate/baseline":
        return _json_response(evaluate_scenario(sim_dir, {}))

    parts = [unquote(seg) for seg in path.strip("/").split("/") if seg]
    world_resp = _dispatch_world_route(parts, sim_dir)
    if world_resp is not None:
        return world_resp
    truth_resp = _dispatch_truth_route(parts, query, sim_dir)
    if truth_resp is not None:
        return truth_resp
    return _json_response({"error": f"Not found: {path}"}, status=HTTPStatus.NOT_FOUND)


def _dispatch_post(path: str, raw_body: bytes, sim_dir: Path) -> tuple[int, str, bytes]:
    payload = _parse_json_body(raw_body)
    if path == "/api/evaluate":
        return _json_response(evaluate_scenario(sim_dir, payload))
    if path == "/api/regenerate":
        if "seed" not in payload:
            raise ValueError("Missing 'seed' in regenerate payload")
        return _json_response(regenerate_world_action(sim_dir, int(payload["seed"])))
    return _json_response({"error": f"Not found: {path}"}, status=HTTPStatus.NOT_FOUND)


def dispatch_request(
    method: str,
    raw_target: str,
    raw_body: bytes,
    sim_dir: Path,
    static_dir: Path = _STATIC_DIR,
) -> tuple[int, str, bytes]:
    """Dispatch an HTTP request purely and return (status, content_type, body)."""
    parsed = urlparse(raw_target)
    query = parse_qs(parsed.query)
    try:
        if method == "GET":
            return _dispatch_get(parsed.path, query, sim_dir, static_dir)
        if method == "POST":
            return _dispatch_post(parsed.path, raw_body, sim_dir)
        return _json_response({"error": f"Method not allowed: {method}"}, status=HTTPStatus.METHOD_NOT_ALLOWED)
    except (ValueError, json.JSONDecodeError) as exc:
        return _json_response({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
    except FileNotFoundError as exc:
        return _json_response({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
    except Exception as exc:  # pragma: no cover - defensive fallback
        return _json_response({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)


class _LagrangeHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer carrying sim_dir and static_dir configuration."""

    def __init__(self, server_address: tuple[str, int], sim_dir: Path, static_dir: Path) -> None:
        self.sim_dir = sim_dir
        self.static_dir = static_dir
        super().__init__(server_address, _LagrangeRequestHandler)


class _LagrangeRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler delegating to pure dispatch_request."""

    server: _LagrangeHTTPServer

    def _handle(self, raw_body: bytes) -> None:
        status, ctype, body = dispatch_request(
            self.command,
            self.path,
            raw_body,
            self.server.sim_dir,
            self.server.static_dir,
        )
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        """Handle HTTP GET requests."""
        self._handle(b"")

    def do_POST(self) -> None:  # noqa: N802
        """Handle HTTP POST requests."""
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw_body = self.rfile.read(max(0, length))
        self._handle(raw_body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Suppress noisy stderr request logs during normal and test runs."""
        _ = (format, args)


def create_server(
    host: str,
    port: int,
    sim_dir: Path | str,
    static_dir: Path | str = _STATIC_DIR,
) -> ThreadingHTTPServer:
    """Create a configured ThreadingHTTPServer bound to (host, port)."""
    return _LagrangeHTTPServer((host, int(port)), Path(sim_dir), Path(static_dir))
