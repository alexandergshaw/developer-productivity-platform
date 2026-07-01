import io
import json
import unittest

import main


def call(method: str, path: str, body: dict | None = None):
    raw = json.dumps(body).encode("utf-8") if body is not None else b""
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_LENGTH": str(len(raw)),
        "wsgi.input": io.BytesIO(raw),
    }
    captured: dict = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    chunks = main.app(environ, start_response)
    return captured["status"], captured["headers"], b"".join(chunks)


class WsgiTests(unittest.TestCase):
    def test_serves_playground_at_root(self):
        status, headers, body = call("GET", "/")
        self.assertEqual(status, "200 OK")
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn(b"detcode", body)

    def test_api_post_runs_request(self):
        status, _, body = call(
            "POST",
            "/api/run",
            {"tool": "do", "command": "write a function double where double(2) == 4 and double(5) == 10"},
        )
        self.assertEqual(status, "200 OK")
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertIn("def double(x):", data["output"])

    def test_api_get_returns_usage(self):
        status, _, body = call("GET", "/api/run")
        self.assertEqual(status, "200 OK")
        self.assertEqual(json.loads(body)["service"], "detcode")

    def test_bad_json_is_400(self):
        environ = {
            "REQUEST_METHOD": "POST",
            "PATH_INFO": "/api/run",
            "CONTENT_LENGTH": "5",
            "wsgi.input": io.BytesIO(b"{oops"),
        }
        captured: dict = {}
        main.app(environ, lambda s, h: captured.update(status=s))
        self.assertEqual(captured["status"], "400 Bad Request")

    def test_unknown_path_is_404(self):
        status, _, _ = call("GET", "/nope")
        self.assertEqual(status, "404 Not Found")

    def test_head_has_headers_but_no_body(self):
        status, headers, body = call("HEAD", "/")
        self.assertEqual(status, "200 OK")
        self.assertGreater(int(headers["Content-Length"]), 0)
        self.assertEqual(body, b"")  # a body on HEAD corrupts keep-alive


if __name__ == "__main__":
    unittest.main()
