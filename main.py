"""WSGI entrypoint — what Vercel's Python builder looks for at the repo root.

Serves the playground UI at ``/`` and the JSON API at ``/api/run``, all from
the standard library. The same ``app`` runs locally under wsgiref
(``python devserver.py``), so local behavior matches the deployment exactly.
"""
from __future__ import annotations

import json
import os

from detcode.determinism import TOOL_VERSION
from detcode.service import run_request

ROOT = os.path.dirname(os.path.abspath(__file__))

USAGE = {
    "service": "detcode",
    "version": TOOL_VERSION,
    "usage": "POST a JSON body like "
    '{"tool": "do", "command": "write a function double where double(2) == 4"}',
    "tools": ["do", "synth", "scaffold", "gentest", "repair", "rename", "imports", "explain"],
}


def _json_response(start_response, status: str, payload: dict) -> list[bytes]:
    body = json.dumps(payload).encode("utf-8")
    start_response(
        status,
        [("Content-Type", "application/json; charset=utf-8"), ("Content-Length", str(len(body)))],
    )
    return [body]


def _html_response(start_response, text: str) -> list[bytes]:
    body = text.encode("utf-8")
    start_response(
        "200 OK",
        [("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(body)))],
    )
    return [body]


def app(environ, start_response) -> list[bytes]:
    method = environ.get("REQUEST_METHOD", "GET")
    path = (environ.get("PATH_INFO") or "/").rstrip("/") or "/"

    if path == "/api/run":
        if method != "POST":
            return _json_response(start_response, "200 OK", USAGE)
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
            raw = environ["wsgi.input"].read(length) if length else b"{}"
            request = json.loads(raw or b"{}")
        except (ValueError, json.JSONDecodeError):
            return _json_response(
                start_response,
                "400 Bad Request",
                {"ok": False, "refused": False, "error": "body must be valid JSON"},
            )
        return _json_response(start_response, "200 OK", run_request(request))

    if path in ("/", "/index.html") and method in ("GET", "HEAD"):
        with open(os.path.join(ROOT, "index.html"), "r", encoding="utf-8") as fh:
            return _html_response(start_response, fh.read())

    return _json_response(
        start_response, "404 Not Found", {"ok": False, "refused": False, "error": "not found"}
    )
