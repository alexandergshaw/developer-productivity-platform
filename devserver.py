"""Local dev server for the detcode playground.

Runs the exact WSGI app Vercel deploys (``main.app``) under wsgiref:

    python devserver.py [port]
"""
from __future__ import annotations

import sys
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server

from main import app


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    """Threaded server: one stalled browser connection must not wedge the rest."""

    daemon_threads = True


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    with make_server("127.0.0.1", port, app, server_class=ThreadingWSGIServer) as server:
        print(f"detcode playground: http://127.0.0.1:{port}")
        server.serve_forever()


if __name__ == "__main__":
    main()
