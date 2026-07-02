import importlib
import io
import json
import shutil
import sys
import unittest

from detcode.engines import builder
from tests.test_builder import materialize


def load_web_module(root: str, slug: str):
    # Import through the package so the relative `.cli` import resolves.
    sys.path.insert(0, root)
    return importlib.import_module(f"{slug}.web")


def cleanup(root: str, slug: str):
    if root in sys.path:
        sys.path.remove(root)
    for mod in [m for m in list(sys.modules) if m == slug or m.startswith(slug + ".")]:
        del sys.modules[mod]
    shutil.rmtree(root, ignore_errors=True)


class WebWrapTests(unittest.TestCase):
    def test_web_flag_adds_wrapper(self):
        project = builder.build("a resume tailorer", web=True)
        paths = [f.path for f in project.files]
        self.assertIn("resume_tailorer/web.py", paths)
        self.assertIn("devserver.py", paths)
        self.assertTrue(project.report["web"])

    def test_direction_phrase_triggers_web(self):
        project = builder.build("a resume tailorer with a web ui")
        self.assertTrue(project.report["web"])
        decisions = " ".join(project.report["decisions"])
        self.assertIn("WSGI web UI", decisions)

    def test_default_has_no_wrapper(self):
        project = builder.build("a resume tailorer")
        self.assertFalse(project.report["web"])
        self.assertNotIn("devserver.py", [f.path for f in project.files])

    def test_web_words_do_not_pollute_generic_slug(self):
        project = builder.build("a recipe manager with a web ui")
        self.assertEqual(project.name, "recipe_manager")
        self.assertTrue(project.report["web"])

    def test_generated_web_app_runs_the_cli(self):
        project = builder.build("a resume tailorer", web=True)
        root = materialize(project)
        try:
            web = load_web_module(root, "resume_tailorer")

            # run_command captures argparse help output (SystemExit 0 -> ok).
            result = web.run_command("--help")
            self.assertTrue(result["ok"])
            self.assertIn("usage", result["output"])

            # Full WSGI round-trip, same shape as detcode's own app.
            body = json.dumps({"command": "--help"}).encode("utf-8")
            environ = {
                "REQUEST_METHOD": "POST",
                "PATH_INFO": "/api/run",
                "CONTENT_LENGTH": str(len(body)),
                "wsgi.input": io.BytesIO(body),
            }
            captured = {}
            chunks = web.app(environ, lambda s, h: captured.update(status=s))
            self.assertEqual(captured["status"], "200 OK")
            self.assertIn("usage", json.loads(b"".join(chunks))["output"])

            # The page itself serves and names the package.
            environ = {"REQUEST_METHOD": "GET", "PATH_INFO": "/"}
            chunks = web.app(environ, lambda s, h: captured.update(status=s))
            self.assertIn(b"resume_tailorer", b"".join(chunks))
        finally:
            cleanup(root, "resume_tailorer")

    def test_bad_cli_command_is_not_ok(self):
        project = builder.build("a resume tailorer", web=True)
        root = materialize(project)
        try:
            web = load_web_module(root, "resume_tailorer")
            result = web.run_command("--no-such-flag")
            self.assertFalse(result["ok"])
        finally:
            cleanup(root, "resume_tailorer")


if __name__ == "__main__":
    unittest.main()
