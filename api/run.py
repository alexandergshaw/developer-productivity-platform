"""Vercel Python serverless function: POST /api/run.

A thin HTTP shim over ``detcode.service.run_request`` (which is unit-tested
in-package). detcode is stdlib-only, so this deploys with no dependencies.
"""
import json
import os
import sys

# The function executes from api/; make the repo root importable.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from http.server import BaseHTTPRequestHandler

from detcode.service import run_request
from detcode.determinism import TOOL_VERSION

USAGE = {
    "service": "detcode",
    "version": TOOL_VERSION,
    "usage": "POST a JSON body like "
    '{"tool": "do", "command": "write a function double where double(2) == 4"}',
    "tools": ["do", "synth", "scaffold", "gentest", "repair", "rename", "imports", "explain"],
}


class handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(200, USAGE)

    def do_POST(self):
        try:
            length = int(self.headers.get("content-length") or 0)
            request = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send(400, {"ok": False, "refused": False, "error": "body must be valid JSON"})
            return
        self._send(200, run_request(request))
