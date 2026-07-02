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
    "tools": [
        "do", "new", "synth", "scaffold", "gentest", "repair",
        "rename", "imports", "explain", "document",
    ],
}


def _respond(start_response, status: str, content_type: str, body: bytes, head: bool) -> list[bytes]:
    # Content-Length always reflects the body, but a HEAD response must not
    # carry one — stray bytes corrupt keep-alive connections.
    start_response(
        status, [("Content-Type", content_type), ("Content-Length", str(len(body)))]
    )
    return [] if head else [body]


def app(environ, start_response) -> list[bytes]:
    method = environ.get("REQUEST_METHOD", "GET")
    head = method == "HEAD"
    path = (environ.get("PATH_INFO") or "/").rstrip("/") or "/"

    def json_response(status: str, payload: dict) -> list[bytes]:
        return _respond(
            start_response,
            status,
            "application/json; charset=utf-8",
            json.dumps(payload).encode("utf-8"),
            head,
        )

    if path == "/api/run":
        if method != "POST":
            return json_response("200 OK", USAGE)
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
            raw = environ["wsgi.input"].read(length) if length else b"{}"
            request = json.loads(raw or b"{}")
        except (ValueError, json.JSONDecodeError):
            return json_response(
                "400 Bad Request",
                {"ok": False, "refused": False, "error": "body must be valid JSON"},
            )
        return json_response("200 OK", run_request(request))

    if path in ("/", "/index.html") and method in ("GET", "HEAD"):
        with open(os.path.join(ROOT, "index.html"), "rb") as fh:
            return _respond(start_response, "200 OK", "text/html; charset=utf-8", fh.read(), head)

    return json_response(
        "404 Not Found", {"ok": False, "refused": False, "error": "not found"}
    )
