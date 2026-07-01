import ast
import importlib
import os
import shutil
import sys
import tempfile
import unittest

from detcode.determinism import content_hash
from detcode.engines import builder
from detcode.engines.builder import BuildError


def materialize(project) -> str:
    """Write a Project to a temp dir and return its path."""
    root = tempfile.mkdtemp(prefix="detcode_gen_")
    for f in project.files:
        target = os.path.join(root, f.path.replace("/", os.sep))
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="") as fh:
            fh.write(f.content)
    return root


def run_generated_tests(root: str, slug: str) -> unittest.TestResult:
    """Import the generated project and run its own test suite in-process.

    The generated test file is loaded under a unique module name so the
    project's ``tests`` package never collides with detcode's own.
    """
    import importlib.util

    sys.path.insert(0, root)
    try:
        test_file = os.path.join(root, "tests", f"test_{slug}.py")
        spec = importlib.util.spec_from_file_location(f"generated_tests_{slug}", test_file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        suite = unittest.TestLoader().loadTestsFromModule(module)
        result = unittest.TestResult()
        suite.run(result)
        return result
    finally:
        sys.path.remove(root)
        for mod in [m for m in sys.modules if m == slug or m.startswith(slug + ".")]:
            del sys.modules[mod]
        sys.modules.pop(f"generated_tests_{slug}", None)


class ElaborateTests(unittest.TestCase):
    def test_decisions_are_recorded(self):
        plan = builder.elaborate("a widget cataloging tool")
        self.assertTrue(any("package name" in d for d in plan["decisions"]))
        self.assertTrue(any("no domain pack matched" in d for d in plan["decisions"]))

    def test_slug_derivation_drops_noise(self):
        self.assertEqual(builder.elaborate("build a widget catalog app")["slug"], "widget_catalog")

    def test_name_override(self):
        plan = builder.elaborate("some tool", name="mytool")
        self.assertEqual(plan["slug"], "mytool")

    def test_refuses_empty_direction(self):
        with self.assertRaises(BuildError):
            builder.elaborate("   ")

    def test_refuses_bad_name(self):
        with self.assertRaises(BuildError):
            builder.elaborate("a tool", name="not a name!")


class BuildGenericTests(unittest.TestCase):
    def test_project_is_complete_and_valid(self):
        project = builder.build("a widget cataloging tool")
        paths = [f.path for f in project.files]
        self.assertIn("widget_cataloging/core.py", paths)
        self.assertIn("widget_cataloging/cli.py", paths)
        self.assertIn("tests/test_widget_cataloging.py", paths)
        self.assertIn("README.md", paths)
        self.assertIn("pyproject.toml", paths)
        for f in project.files:
            if f.path.endswith(".py"):
                ast.parse(f.content)  # every generated python file parses

    def test_readme_records_decisions(self):
        project = builder.build("a widget tool")
        readme = next(f for f in project.files if f.path == "README.md")
        self.assertIn("## Decisions detcode made", readme.content)
        self.assertIn("Generated deterministically by detcode", readme.content)

    def test_generated_project_tests_pass(self):
        project = builder.build("a widget cataloging tool")
        root = materialize(project)
        try:
            result = run_generated_tests(root, project.name)
            self.assertGreater(result.testsRun, 0)
            self.assertEqual(result.failures, [])
            self.assertEqual(result.errors, [])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_deterministic(self):
        renders = {
            content_hash(builder.render(builder.build("a widget tool"))) for _ in range(10)
        }
        self.assertEqual(len(renders), 1)

    def test_render_bundle_shape(self):
        text = builder.render(builder.build("a widget tool"))
        self.assertIn("# Project: widget", text)
        self.assertIn("# ===== README.md =====", text)


if __name__ == "__main__":
    unittest.main()
