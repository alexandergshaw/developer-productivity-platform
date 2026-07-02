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
        self.assertEqual(stacks.get("django").key, "django")
        self.assertEqual(stacks.get("express").key, "express")
        self.assertEqual(stacks.get("javascript").key, "node")
        self.assertEqual(stacks.get("vite").key, "react")
        self.assertEqual(stacks.get("ts").key, "typescript")
        self.assertEqual(stacks.get("go").key, "go")  # by key, not keyword
        self.assertEqual(stacks.get("golang").key, "go")
        self.assertEqual(stacks.get("cargo").key, "rust")
        self.assertIsNone(stacks.get("cobol"))

    def test_bare_go_never_hijacks_a_direction(self):
        # "go" is too common an English word to be a stack keyword.
        plan = builder.elaborate("an on the go checklist app")
        self.assertEqual(plan["stack"].key, "stdlib")

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


class DjangoBuildTests(unittest.TestCase):
    def test_django_project_shape(self):
        project = builder.build("a recipe manager in django")
        paths = paths_of(project)
        for path in ("manage.py", "config/settings.py", "config/urls.py",
                     "config/views.py", "config/wsgi.py", "config/asgi.py"):
            self.assertIn(path, paths)
        self.assertEqual(project.report["stack"], "django")
        self.assertIn('dependencies = ["django"]', file_of(project, "pyproject.toml"))
        for f in project.files:
            if f.path.endswith(".py"):
                ast.parse(f.content)

    def test_views_wrap_the_pack_cli(self):
        project = builder.build("a recipe manager in django")
        views = file_of(project, "config/views.py")
        self.assertIn("from recipe_manager.cli import main as cli_main", views)
        self.assertIn("csrf_exempt", views)

    def test_pack_core_rides_along_and_its_tests_pass(self):
        project = builder.build("a resume tailorer in django")
        root = materialize(project)
        try:
            result = run_generated_tests(root, project.name)
            self.assertGreater(result.testsRun, 0)
            self.assertEqual(result.failures, [])
            self.assertEqual(result.errors, [])
        finally:
            shutil.rmtree(root, ignore_errors=True)


class ExpressBuildTests(unittest.TestCase):
    def test_express_project_shape(self):
        project = builder.build("a todo app in express")
        paths = paths_of(project)
        for path in ("package.json", "server.js", "src/core.js", "src/cli.js",
                     "tests/core.test.js", "tests/server.test.js"):
            self.assertIn(path, paths)
        manifest = json.loads(file_of(project, "package.json"))
        self.assertIn("express", manifest["dependencies"])
        self.assertEqual(project.report["stack"], "express")

    def test_express_is_no_longer_a_node_alias(self):
        plan = builder.elaborate("a todo app in express")
        self.assertEqual(plan["stack"].key, "express")


class ReactBuildTests(unittest.TestCase):
    def test_react_project_shape(self):
        project = builder.build("an expense tracker in react")
        paths = paths_of(project)
        for path in ("package.json", "vite.config.js", "index.html", "src/main.jsx",
                     "src/App.jsx", "src/styles.css", "src/lib/core.js", "tests/core.test.js"):
            self.assertIn(path, paths)
        manifest = json.loads(file_of(project, "package.json"))
        self.assertIn("react", manifest["dependencies"])
        self.assertIn("vite", manifest["devDependencies"])
        self.assertEqual(project.name, "expense_tracker")

    def test_python_only_boundary_is_recorded(self):
        project = builder.build("an expense tracker in react")
        self.assertIn("Python-only", " ".join(project.report["decisions"]))


class TypescriptBuildTests(unittest.TestCase):
    def test_typescript_project_shape(self):
        project = builder.build("a todo app in typescript")
        paths = paths_of(project)
        for path in ("package.json", "tsconfig.json", "src/core.ts", "src/cli.ts",
                     "src/server.ts", "src/tests/core.test.ts"):
            self.assertIn(path, paths)
        tsconfig = json.loads(file_of(project, "tsconfig.json"))
        self.assertTrue(tsconfig["compilerOptions"]["strict"])
        self.assertEqual(project.report["stack"], "typescript")


class GoBuildTests(unittest.TestCase):
    def test_go_project_shape(self):
        project = builder.build("a link shortener in golang")
        paths = paths_of(project)
        for path in ("go.mod", "main.go", "page.go", "core/core.go", "core/core_test.go"):
            self.assertIn(path, paths)
        self.assertIn("module link_shortener", file_of(project, "go.mod"))
        self.assertEqual(project.report["stack"], "go")

    def test_go_page_has_no_backticks_inside_the_raw_string(self):
        # A backtick inside Go's raw string literal would break compilation.
        project = builder.build("a link shortener in golang")
        page = file_of(project, "page.go")
        body = page.split("`", 1)[1].rsplit("`", 1)[0]
        self.assertNotIn("`", body)


class RustBuildTests(unittest.TestCase):
    def test_rust_project_shape(self):
        project = builder.build("a markdown parser in rust")
        paths = paths_of(project)
        for path in ("Cargo.toml", "src/lib.rs", "src/main.rs", "tests/integration.rs"):
            self.assertIn(path, paths)
        self.assertIn('name = "markdown_parser"', file_of(project, "Cargo.toml"))
        self.assertEqual(project.report["stack"], "rust")

    def test_rust_is_honest_about_no_http(self):
        project = builder.build("a markdown parser in rust")
        self.assertIn("no HTTP server", " ".join(project.report["decisions"]))


class ComprehensiveExtrasTests(unittest.TestCase):
    def test_every_stack_ships_editorconfig_gitignore_and_ci(self):
        directions = {
            "stdlib": "a widget tool",
            "flask": "a widget tool in flask",
            "fastapi": "a widget tool using fastapi",
            "django": "a widget tool in django",
            "node": "a widget tool with node",
            "express": "a widget tool in express",
            "react": "a widget tool in react",
            "typescript": "a widget tool in typescript",
            "go": "a widget tool in golang",
            "rust": "a widget tool in rust",
        }
        for key, direction in directions.items():
            with self.subTest(stack=key):
                project = builder.build(direction)
                paths = paths_of(project)
                self.assertEqual(project.report["stack"], key)
                self.assertIn(".editorconfig", paths)
                self.assertIn(".gitignore", paths)
                self.assertIn(".github/workflows/ci.yml", paths)
                self.assertIn("README.md", paths)

    def test_web_stacks_ship_a_dockerfile(self):
        for direction in ("a tool in flask", "a tool using fastapi", "a tool in django",
                          "a tool with node", "a tool in express", "a tool in react",
                          "a tool in typescript", "a tool in golang"):
            with self.subTest(direction=direction):
                self.assertIn("Dockerfile", paths_of(builder.build(direction)))

    def test_cli_only_stacks_ship_no_dockerfile(self):
        self.assertNotIn("Dockerfile", paths_of(builder.build("a tool")))
        self.assertNotIn("Dockerfile", paths_of(builder.build("a tool in rust")))

    def test_every_stack_is_deterministic(self):
        for direction in ("a tool in django", "a tool in express", "a tool in react",
                          "a tool in typescript", "a tool in golang", "a tool in rust"):
            with self.subTest(direction=direction):
                renders = {
                    content_hash(builder.render(builder.build(direction))) for _ in range(3)
                }
                self.assertEqual(len(renders), 1)

    def test_go_gitignore_names_the_binary(self):
        project = builder.build("a link shortener in golang")
        self.assertIn("link_shortener", file_of(project, ".gitignore"))

    def test_skeleton_help_is_informative(self):
        # Every skeleton CLI's --help names the project, how to serve it (when
        # it has a server), and how to run its tests — not just a usage line.
        cli_files = {
            "a widget tool with node": "src/cli.js",
            "a widget tool in express": "src/cli.js",
            "a widget tool in typescript": "src/cli.ts",
            "a widget tool in golang": "main.go",
            "a widget tool in rust": "src/main.rs",
            "a widget tool in react": "src/App.jsx",
        }
        for direction, path in cli_files.items():
            with self.subTest(direction=direction):
                content = file_of(builder.build(direction), path)
                self.assertIn("generated by detcode", content)
                self.assertIn("test suite", content)
                self.assertIn("usage", content)

    def test_generic_python_help_is_informative(self):
        project = builder.build("a widget tool")
        cli = file_of(project, "widget/cli.py")
        self.assertIn("generated by detcode", cli)
        self.assertIn("fails loudly", cli)


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
