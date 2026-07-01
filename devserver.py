"""Local dev server for the detcode playground.

Serves index.html and routes POST /api/run through the same service layer the
Vercel function uses — local behavior matches the deployment exactly.

    python devserver.py [port]
"""
from __future__ import annotations

import json
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from detcode.service import run_request


class PlaygroundHandler(SimpleHTTPRequestHandler):
    def do_POST(self):
        if self.path.rstrip("/") != "/api/run":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("content-length") or 0)
            request = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._json(400, {"ok": False, "refused": False, "error": "body must be valid JSON"})
            return
        self._json(200, run_request(request))

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # quieter default logging
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    server = ThreadingHTTPServer(("127.0.0.1", port), PlaygroundHandler)
    print(f"detcode playground: http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
