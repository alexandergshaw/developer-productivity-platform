"""Vercel Python serverless function: POST /api/chat.

A thin HTTP shim over ``gemini.chat`` — the server-side Gemini proxy for the
workbench chat view. Stdlib-only, so it deploys with no dependencies; set
GEMINI_API_KEY in the project's environment variables.
"""
import json
import os
import sys

# The function executes from api/; make the repo root importable.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from http.server import BaseHTTPRequestHandler

from gemini import USAGE, chat


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
        self._send(200, chat(request))
