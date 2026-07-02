import ast
import json
import shutil
import unittest

from detcode import stacks
from detcode.determinism import content_hash
from detcode.engines import builder
from detcode.engines.builder import BuildError
from detcode.service import run_request
from tests.test_builder import materialize, run_generated_tests


def paths_of(project) -> list[str]:
    return [f.path for f in project.files]


def file_of(project, path: str) -> str:
    return next(f.content for f in project.files if f.path == path)


class RegistryTests(unittest.TestCase):
    def test_default_is_stdlib(self):
        self.assertEqual(stacks.default().key, "stdlib")

    def test_get_by_key_and_alias(self):
        self.assertEqual(stacks.get("flask").key, "flask")
        self.assertEqual(stacks.get("FastAPI").key, "fastapi")
        self.assertEqual(stacks.get("express").key, "node")
        self.assertEqual(stacks.get("javascript").key, "node")
        self.assertIsNone(stacks.get("cobol"))

    def test_match_skips_the_default(self):
        self.assertEqual(stacks.match({"python", "todo"}), [])
        [(stack, hits)] = stacks.match({"todo", "flask"})
        self.assertEqual(stack.key, "flask")
        self.assertEqual(hits, ["flask"])


class ResolutionTests(unittest.TestCase):
    def test_direction_keyword_picks_the_stack(self):
        plan = builder.elaborate("a todo app in flask")
        self.assertEqual(plan["stack"].key, "flask")
        self.assertTrue(any("tech stack: Flask" in d for d in plan["decisions"]))

    def test_stack_words_never_enter_the_slug(self):
        self.assertEqual(builder.elaborate("a todo app in flask")["slug"], "todo")
        self.assertEqual(builder.elaborate("a recipe manager using fastapi")["slug"], "recipe_manager")

    def test_default_is_recorded(self):
        plan = builder.elaborate("a todo app")
        self.assertEqual(plan["stack"].key, "stdlib")
        self.assertTrue(any("tech stack: Python (stdlib)" in d for d in plan["decisions"]))

    def test_explicit_stack_wins(self):
        plan = builder.elaborate("a todo app", stack="fastapi")
        self.assertEqual(plan["stack"].key, "fastapi")
        self.assertTrue(any("requested explicitly" in d for d in plan["decisions"]))

    def test_unknown_stack_is_refused(self):
        with self.assertRaises(BuildError):
            builder.elaborate("a todo app", stack="cobol")

    def test_two_stacks_in_one_direction_are_refused(self):
        with self.assertRaises(BuildError):
            builder.elaborate("a js playground in flask")

    def test_web_is_implied_by_web_stacks(self):
        self.assertTrue(builder.elaborate("a todo app in flask")["web"])
        self.assertFalse(builder.elaborate("a todo app")["web"])


class FlaskBuildTests(unittest.TestCase):
    def test_flask_project_shape(self):
        project = builder.build("a resume tailorer in flask")
        paths = paths_of(project)
        self.assertIn("resume_tailorer/web.py", paths)
        self.assertIn("devserver.py", paths)
        self.assertEqual(project.report["stack"], "flask")
        self.assertTrue(project.report["web"])
        for f in project.files:
            if f.path.endswith(".py"):
                ast.parse(f.content)

    def test_web_layer_uses_flask(self):
        project = builder.build("a resume tailorer in flask")
        self.assertIn("from flask import", file_of(project, "resume_tailorer/web.py"))

    def test_dependency_lands_in_pyproject(self):
        project = builder.build("a resume tailorer in flask")
        self.assertIn('dependencies = ["flask"]', file_of(project, "pyproject.toml"))

    def test_domain_pack_rides_along_and_its_tests_pass(self):
        # The core is untouched by the stack; pack tests never import web.py.
        project = builder.build("a resume tailorer in flask")
        root = materialize(project)
        try:
            result = run_generated_tests(root, project.name)
            self.assertGreater(result.testsRun, 0)
            self.assertEqual(result.failures, [])
            self.assertEqual(result.errors, [])
        finally:
            shutil.rmtree(root, ignore_errors=True)


class FastapiBuildTests(unittest.TestCase):
    def test_fastapi_project_shape(self):
        project = builder.build("a todo app", stack="fastapi")
        self.assertIn("todo/web.py", paths_of(project))
        self.assertIn("from fastapi import", file_of(project, "todo/web.py"))
        self.assertIn('dependencies = ["fastapi", "uvicorn"]', file_of(project, "pyproject.toml"))
        self.assertIn("uvicorn.run", file_of(project, "devserver.py"))
        for f in project.files:
            if f.path.endswith(".py"):
                ast.parse(f.content)


class NodeBuildTests(unittest.TestCase):
    def test_node_project_shape(self):
        project = builder.build("an expense tracker with node")
        paths = paths_of(project)
        for path in ("package.json", "server.js", "src/core.js", "src/cli.js", "tests/core.test.js"):
            self.assertIn(path, paths)
        self.assertNotIn("pyproject.toml", paths)
        self.assertFalse(any(p.endswith(".py") for p in paths))
        self.assertEqual(project.report["stack"], "node")
        self.assertEqual(project.name, "expense_tracker")  # pack slug still names it

    def test_package_json_is_valid(self):
        project = builder.build("an expense tracker with node")
        manifest = json.loads(file_of(project, "package.json"))
        self.assertEqual(manifest["name"], "expense_tracker")
        self.assertEqual(manifest["scripts"]["test"], "node --test")

    def test_python_only_boundary_is_recorded(self):
        project = builder.build("an expense tracker with node")
        decisions = " ".join(project.report["decisions"])
        self.assertIn("Python-only", decisions)
        readme = file_of(project, "README.md")
        self.assertIn("node --test", readme)

    def test_no_pack_matched_records_skeleton_decision(self):
        project = builder.build("a widget catalog with node")
        decisions = " ".join(project.report["decisions"])
        self.assertIn("skeleton", decisions)

    def test_ci_runs_node_tests(self):
        project = builder.build("a widget catalog with node")
        ci = file_of(project, ".github/workflows/ci.yml")
        self.assertIn("setup-node", ci)
        self.assertIn("node --test", ci)

    def test_deterministic(self):
        renders = {
            content_hash(builder.render(builder.build("an expense tracker with node")))
            for _ in range(5)
        }
        self.assertEqual(len(renders), 1)


class ServiceTests(unittest.TestCase):
    def test_direction_keyword_via_service(self):
        resp = run_request({"tool": "new", "direction": "a todo app in flask"})
        self.assertTrue(resp["ok"])
        self.assertIn("todo/web.py", resp["files"])
        self.assertEqual(resp["report"]["stack"], "flask")

    def test_explicit_stack_field(self):
        resp = run_request({"tool": "new", "direction": "a todo app", "stack": "node"})
        self.assertTrue(resp["ok"])
        self.assertIn("server.js", resp["files"])
        self.assertEqual(resp["report"]["stack"], "node")

    def test_unknown_stack_is_a_refusal(self):
        resp = run_request({"tool": "new", "direction": "a todo app", "stack": "cobol"})
        self.assertFalse(resp["ok"])
        self.assertTrue(resp["refused"])

    def test_english_command_carries_the_stack(self):
        resp = run_request({"tool": "do", "command": "build a todo app in flask"})
        self.assertTrue(resp["ok"])
        self.assertIn("todo/web.py", resp["files"])

    def test_stdlib_output_keeps_no_dependencies(self):
        resp = run_request({"tool": "new", "direction": "a todo app"})
        self.assertTrue(resp["ok"])
        self.assertIn("dependencies = []", resp["files"]["pyproject.toml"])


if __name__ == "__main__":
    unittest.main()
