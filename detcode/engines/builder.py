"""Project builder — general direction in, runnable project out.

This is the "give it a direction and let it exercise independence" engine.
The independence is real but deterministic: every choice (which domain pack,
what package name, what layout) comes from a fixed decision procedure over the
direction's words, and **every decision is recorded** in the build report and
the generated README. Same direction, byte-identical project; and you can
always see why it built what it built.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from ..determinism import provenance
from .. import packs, stacks

RULE_VERSION = "3"

_EDITORCONFIG = """root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
indent_style = space
indent_size = 4

[*.{js,jsx,ts,tsx,json,yml,yaml,html,css,toml}]
indent_size = 2

[*.go]
indent_style = tab
"""

# Words that carry no domain meaning in a direction.
_DIRECTION_NOISE = frozenset(
    "a an the build make create start new me my for of and or to app apps "
    "application project projects tool please that will can helps help with "
    "some this it web ui website browser frontend interface webapp module "
    "in using via on".split()
)

# Direction words that request the --web wrapper.
_WEB_WORDS = frozenset("web ui website browser frontend webapp".split())


class BuildError(Exception):
    """The direction or target was unusable; building was refused."""


@dataclass(frozen=True)
class ProjectFile:
    path: str  # POSIX-style, relative
    content: str


@dataclass
class Project:
    name: str
    title: str
    files: tuple[ProjectFile, ...]
    report: dict


def _words(direction: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", direction.lower())


def _slug(direction: str) -> str:
    # Stack words name the chassis, not the domain — they never enter a slug.
    drop = _DIRECTION_NOISE | stacks.all_keywords()
    meaningful = [w for w in _words(direction) if w not in drop]
    slug = "_".join(meaningful[:4])
    if not slug:
        return "new_project"
    if slug[0].isdigit():
        slug = "p_" + slug
    return slug


def _title(slug: str) -> str:
    return " ".join(part.capitalize() for part in slug.split("_"))


def _order_matches(direction: str, matches: list) -> list:
    """Primary pack = the one whose keyword appears earliest in the direction.

    Deterministic: position of the first matched keyword, then registry order.
    """
    ordered_words = _words(direction)

    def first_position(indexed) -> tuple:
        registry_index, (pack, hits) = indexed
        positions = [ordered_words.index(h) for h in hits if h in ordered_words]
        return (min(positions) if positions else len(ordered_words), registry_index)

    return [m for _, m in sorted(enumerate(matches), key=first_position)]


def _resolve_stack(words: set, requested: str | None) -> tuple:
    """(stack, why) — explicit request wins, then direction keywords, then default."""
    if requested:
        stack = stacks.get(requested)
        if stack is None:
            known = ", ".join(s.key for s in stacks.registry())
            raise BuildError(f"unknown tech stack {requested!r} — supported: {known}")
        return stack, "requested explicitly"
    matched = stacks.match(words)
    if len(matched) > 1:
        names = " vs ".join(f"{s.title} ({', '.join(h)})" for s, h in matched)
        raise BuildError(f"one tech stack per project — the direction names {names}")
    if matched:
        stack, hits = matched[0]
        return stack, f"matched keyword(s): {', '.join(hits)}"
    return stacks.default(), (
        'the default — pick another with --stack or in the direction, '
        'e.g. "in flask", "using fastapi", "with node"'
    )


def elaborate(
    direction: str,
    name: str | None = None,
    web: bool = False,
    extra_packs: tuple = (),
    stack: str | None = None,
) -> dict:
    """Derive the build plan from the direction, recording every decision."""
    if not isinstance(direction, str) or not direction.strip():
        raise BuildError("give a direction, e.g. detcode new \"resume tailorer\"")

    decisions: list[str] = []
    words = set(_words(direction))

    stack_obj, stack_why = _resolve_stack(words, stack)
    web = web or bool(words & _WEB_WORDS) or stack_obj.web_always
    decisions.append(f"tech stack: {stack_obj.title} ({stack_why}) — {stack_obj.description}")

    matches = _order_matches(direction, packs.match_all(words, extra_packs))
    python_core = stack_obj.language == "python"
    if not python_core:
        if matches:
            names = ", ".join(f"{p.title} ({', '.join(h)})" for p, h in matches)
            decisions.append(
                f"matched domain pack(s) {names}, but packs are Python-only — the "
                f"{stack_obj.title} project ships the skeleton with the domain "
                "logic marked TODO (the honest deterministic boundary)"
            )
        else:
            decisions.append(
                f"generating the {stack_obj.title} skeleton — structure is "
                "derivable, novel domain logic is not"
            )
        if not matches:
            matches = [(packs.registry()[-1], [])]
    elif not matches:
        generic = packs.registry()[-1]
        matches = [(generic, [])]
        decisions.append(
            "no domain pack matched this direction — generating "
            f"{generic.description} (the honest deterministic boundary: structure "
            "is derivable, novel domain logic is not)"
        )
    elif len(matches) == 1:
        pack, hits = matches[0]
        decisions.append(
            f"matched the {pack.title!r} domain pack on keyword(s): {', '.join(hits)} — "
            f"generating {pack.description}"
        )
    else:
        names = ", ".join(
            f"{p.title} ({', '.join(h)})" for p, h in matches
        )
        decisions.append(
            f"composed {len(matches)} domain packs — {names} — the first is "
            "primary (its keyword appears earliest in the direction); each "
            "ships as its own package in one project"
        )

    primary = matches[0][0]
    slug = name or primary.default_slug or _slug(direction)
    if not slug.isidentifier():
        raise BuildError(f"package name {slug!r} is not a valid identifier")
    decisions.append(
        f"package name {slug!r} " + ("taken from --name" if name else "derived from the direction")
    )

    # (pack, package_slug) pairs: the primary takes the project slug, the
    # rest keep their own default slugs.
    pack_slugs = [(primary, slug)]
    for pack, _hits in matches[1:]:
        pack_slugs.append((pack, pack.default_slug))

    if not python_core:
        decisions.append(stack_obj.interface_line)
    else:
        entrypoints = " / ".join(f"python -m {s}" for _, s in pack_slugs)
        if web:
            tail = (
                " — the same pattern detcode's own playground uses"
                if stack_obj.key == "stdlib"
                else ""
            )
            decisions.append(
                f"interface: command-line ({entrypoints}) plus {stack_obj.web_label} "
                f"over the primary CLI ({stack_obj.web_run}){tail}"
            )
        else:
            decisions.append(
                f"interface: command-line ({entrypoints}) — deterministic core "
                "first; a UI can wrap it later (--web or say 'with a web ui')"
            )
    decisions.append(f"test suite included; run: {' && '.join(stack_obj.dev)}")

    return {
        "direction": direction.strip(),
        "slug": slug,
        "title": _title(slug),
        "pack": primary,
        "pack_slugs": pack_slugs,
        "web": web,
        "stack": stack_obj,
        "decisions": decisions,
    }


def _readme(plan: dict) -> str:
    lines = [
        f"# {plan['title']}",
        "",
        f'Generated deterministically by detcode from the direction: "{plan["direction"]}".',
        "Same direction, byte-identical project — regenerate any time.",
        "",
        "## Decisions detcode made",
        "",
    ]
    lines.extend(f"- {d}" for d in plan["decisions"])
    stack = plan.get("stack")
    if stack is None or stack.key == "stdlib":
        usage = [f"python -m {plan['slug']} --help"]
        if plan.get("web"):
            usage.append("python devserver.py    # web UI at http://127.0.0.1:8000")
        dev = ["python -m unittest discover -s tests"]
    else:
        usage = [line.replace("__PKG__", plan["slug"]) for line in stack.usage]
        dev = [line.replace("__PKG__", plan["slug"]) for line in stack.dev]
    lines.extend(
        [
            "",
            "## Usage",
            "",
            "```bash",
            *usage,
            "```",
            "",
            "## Development",
            "",
            "```bash",
            *dev,
            "```",
            "",
        ]
    )
    if stack is None or stack.language == "python":
        lines.extend(
            [
                "## Growing this project with detcode",
                "",
                "```bash",
                'detcode do "write a function <name> where <name>(...) == ..."',
                f'detcode do "add a docstring to <func>" --file {plan["slug"]}/core.py --write',
                f'detcode gentest --spec examples.json --file {plan["slug"]}/core.py',
                "```",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "## Growing this project with detcode",
                "",
                "detcode's example-driven tools (do / gentest / teach) synthesize",
                "Python; on this stack, grow the core module by hand — or rebuild the",
                "same direction on a Python stack to get the domain packs.",
                "",
            ]
        )
    return "\n".join(lines)


def _ci_workflow(slug: str) -> str:
    return (
        "name: ci\n\non:\n  push:\n  pull_request:\n\njobs:\n  test:\n"
        "    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - uses: actions/setup-python@v5\n"
        '        with: { python-version: "3.12" }\n'
        "      - name: Tests\n"
        "        run: python -m unittest discover -s tests -v\n"
        "      # Advise gate: new findings since the baseline fail the build.\n"
        "      # Requires detcode on PATH; drop `|| true` to make it blocking.\n"
        "      - name: Advise gate\n"
        f"        run: python -m detcode advise --dir {slug} --check || true\n"
    )




def _pyproject(plan: dict) -> str:
    includes = ", ".join(f'"{s}*"' for _, s in plan["pack_slugs"])
    stack = plan.get("stack")
    deps = ", ".join(f'"{d}"' for d in (stack.dependencies if stack else ()))
    return (
        "[build-system]\n"
        'requires = ["setuptools>=68"]\n'
        'build-backend = "setuptools.build_meta"\n'
        "\n"
        "[project]\n"
        f'name = "{plan["slug"].replace("_", "-")}"\n'
        'version = "0.1.0"\n'
        f'description = "{plan["title"]} (generated by detcode)"\n'
        'requires-python = ">=3.10"\n'
        f"dependencies = [{deps}]\n"
        "\n"
        "[project.scripts]\n"
        f'{plan["slug"].replace("_", "-")} = "{plan["slug"]}.cli:main"\n'
        "\n"
        "[tool.setuptools.packages.find]\n"
        f"include = [{includes}]\n"
    )


def build(
    direction: str,
    name: str | None = None,
    web: bool = False,
    extra_packs: tuple = (),
    stack: str | None = None,
) -> Project:
    """Build a complete project from a general direction.

    A direction matching several packs ("a teaching assistant with a resume
    module") composes them: each pack's package lands in the same project.
    ``web=True`` (or "with a web ui" in the direction) adds the stack's web
    layer over the primary package's CLI. ``stack`` picks the tech stack
    explicitly; otherwise stack keywords in the direction ("in flask",
    "using fastapi", "with node") decide, falling back to Python stdlib.
    ``extra_packs`` holds user-minted packs (hash-verified by the store on load).
    """
    plan = elaborate(direction, name, web, extra_packs, stack)
    slug = plan["slug"]
    stack_obj = plan["stack"]

    if stack_obj.language != "python":
        # Non-Python chassis: the stack's own skeleton, no Python packaging.
        merged = {
            raw_path.replace("__PKG__", slug): raw_content.replace("__PKG__", slug)
            for raw_path, raw_content in sorted(stack_obj.skeleton().items())
        }
        files = [ProjectFile(p, c) for p, c in merged.items()]
        files.append(ProjectFile("README.md", _readme(plan)))
        files.append(ProjectFile(".editorconfig", _EDITORCONFIG))
        files.append(ProjectFile(".gitignore", stack_obj.gitignore.replace("__PKG__", slug)))
        files.append(ProjectFile(".github/workflows/ci.yml", stack_obj.ci))
        if stack_obj.docker:
            files.append(ProjectFile("Dockerfile", stack_obj.docker.replace("__PKG__", slug)))
        files.sort(key=lambda f: f.path)
        report = provenance(
            "build",
            RULE_VERSION,
            stack=stack_obj.key,
            pack=None,
            packs=[],
            package=slug,
            packages=[slug],
            web=plan["web"],
            decisions=plan["decisions"],
            files=[f.path for f in files],
        )
        return Project(slug, plan["title"], tuple(files), report)

    template_sets = [
        (pack.files(), pack_slug) for pack, pack_slug in plan["pack_slugs"]
    ]
    if plan["web"]:
        template_sets.append((stack_obj.web_files(), slug))

    merged: dict[str, str] = {}
    for templates, pack_slug in template_sets:
        for raw_path, raw_content in sorted(templates.items()):
            path = raw_path.replace("__PKG__", pack_slug)
            content = raw_content.replace("__PKG__", pack_slug)
            if path.endswith(".py"):
                try:
                    ast.parse(content)
                except SyntaxError as exc:  # a pack template must never ship broken
                    raise BuildError(f"pack template {raw_path!r} is invalid: {exc}") from exc
            if path in merged and merged[path] != content:
                raise BuildError(f"pack composition collides on {path!r}")
            merged[path] = content

    files = [ProjectFile(p, c) for p, c in merged.items()]
    files.append(ProjectFile("README.md", _readme(plan)))
    files.append(ProjectFile("pyproject.toml", _pyproject(plan)))
    files.append(ProjectFile(".editorconfig", _EDITORCONFIG))
    files.append(ProjectFile(".gitignore", "__pycache__/\n*.py[cod]\n*.egg-info/\n"))
    files.append(ProjectFile(".github/workflows/ci.yml", _ci_workflow(slug)))
    if stack_obj.docker:
        files.append(ProjectFile("Dockerfile", stack_obj.docker.replace("__PKG__", slug)))
    files.sort(key=lambda f: f.path)

    report = provenance(
        "build",
        RULE_VERSION,
        stack=stack_obj.key,
        pack=plan["pack"].key,
        packs=[p.key for p, _ in plan["pack_slugs"]],
        package=slug,
        packages=[s for _, s in plan["pack_slugs"]],
        web=plan["web"],
        decisions=plan["decisions"],
        files=[f.path for f in files],
    )
    return Project(slug, plan["title"], tuple(files), report)


_PLAN_CLI = '''
"""Command-line interface: __PKG__ call <function> [args...]"""
import argparse
import ast

from . import core


def _parse_arg(text):
    """Python literal when possible, raw string otherwise (fixed rule)."""
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="__PKG__",
        description=(
            "__PKG__ - built by detcode from a plan. Call the functions in "
            "__PKG__/core.py with Python-literal arguments; stubbed functions "
            "fail loudly until you implement them."
        ),
        epilog="example: python -m __PKG__ call <function> 2 3",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    call = sub.add_parser("call", help="call a core function with literal arguments")
    call.add_argument("function", help="function name in core.py")
    call.add_argument("args", nargs="*", help="arguments as Python literals")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    fn = getattr(core, args.function, None)
    if not callable(fn):
        print(f"no such function: {args.function}")
        return 2
    print(repr(fn(*[_parse_arg(a) for a in args.args])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

_PLAN_MAIN = '"""Enables python -m __PKG__."""\nfrom .cli import main\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'


def _validate_plan(plan: dict) -> tuple[str, list[dict]]:
    if not isinstance(plan, dict) or plan.get("detcode_plan") != 1:
        raise BuildError('not a detcode plan (expected {"detcode_plan": 1, ...})')
    slug = plan.get("name")
    if not isinstance(slug, str) or not slug.isidentifier():
        raise BuildError(f"plan name {slug!r} is not a valid identifier")
    functions = plan.get("functions")
    if not isinstance(functions, list) or not functions:
        raise BuildError("plan has no functions")
    seen: set[str] = set()
    for fn in functions:
        fname = fn.get("name") if isinstance(fn, dict) else None
        if not isinstance(fname, str) or not fname.isidentifier():
            raise BuildError(f"plan function name {fname!r} is not a valid identifier")
        if fname in seen:
            raise BuildError(f"duplicate plan function {fname!r}")
        seen.add(fname)
        if not isinstance(fn.get("examples", []), list):
            raise BuildError(f"examples of {fname!r} must be a list")
    return slug, functions


def _stub(fname: str, description: str, examples: list) -> str:
    n = len(examples[0]["in"]) if examples and isinstance(examples[0], dict) else None
    params = "*args" if n is None else ("x" if n == 1 else ", ".join(f"x{i}" for i in range(n)))
    desc = description or "planned function"
    return (
        f"def {fname}({params}):\n"
        f'    """{desc} (stub: not derivable from the given examples yet)."""\n'
        f"    raise NotImplementedError(\n"
        f'        "implement {fname}, then: detcode teach --file core.py --func {fname}"\n'
        f"    )\n"
    )


def build_from_plan(plan: dict, web: bool = False, corpus: tuple = ()) -> Project:
    """Build a project from a filled plan file.

    Each planned function is attempted via retrieval then synthesis from its
    examples. Derived functions become real code with passing tests; the rest
    become stubs whose examples ship as expectedFailure tests — executable
    TODOs that flip loudly once implemented.
    """
    from . import retrieve as retrieve_engine
    from .retrieve import NoMatch
    from .synth import NoSolution, SpecError

    slug, functions = _validate_plan(plan)
    direction = str(plan.get("direction") or slug)
    solved: list[tuple[str, str, list]] = []
    unsolved: list[tuple[str, str, list]] = []
    for fn in functions:
        fname = fn["name"]
        examples = fn.get("examples") or []
        description = str(fn.get("description") or "")
        if examples:
            spec = {"name": fname, "examples": examples}
            # Optional per-function search bounds ride along from the plan.
            for key in ("max_depth", "budget"):
                if key in fn:
                    spec[key] = fn[key]
            try:
                r = retrieve_engine.write_function(spec, extra=corpus)
                solved.append((fname, r.source, examples))
                continue
            except (NoMatch, NoSolution, SpecError):
                pass
        unsolved.append((fname, description, examples))

    decisions = [
        f'built from a plan for "{direction}" — examples are the spec',
        f"{len(solved)} of {len(functions)} function(s) derived from their examples "
        "(retrieval/synthesis); "
        + (
            f"{len(unsolved)} left as stub(s) with their examples as expectedFailure "
            "tests — implement them, watch the tests flip, then `detcode teach`"
            if unsolved
            else "nothing left to implement"
        ),
        f"package name {slug!r} from the plan",
        f"interface: command-line (python -m {slug} call <function> [args...])",
        "test suite included; run: python -m unittest discover -s tests",
    ]

    core_parts = [f'"""Core logic for {slug} (built from a detcode plan)."""']
    core_parts.extend(source.rstrip("\n") for _, source, _ in solved)
    core_parts.extend(_stub(f, d, ex).rstrip("\n") for f, d, ex in unsolved)
    core_py = "\n\n\n".join(core_parts) + "\n"

    test_lines = [
        f'"""Tests for {slug}. expectedFailure = planned intent, not yet implemented."""',
        "import unittest",
        "",
        f"from {slug} import core",
        "",
        "",
        "class CoreTests(unittest.TestCase):",
    ]
    for fname, _source, examples in solved:
        for i, ex in enumerate(examples):
            args = ", ".join(repr(a) for a in ex["in"])
            test_lines.append(f"    def test_{fname}_{i}(self):")
            test_lines.append(f"        self.assertEqual(core.{fname}({args}), {ex['out']!r})")
            test_lines.append("")
    for fname, _desc, examples in unsolved:
        for i, ex in enumerate(examples):
            args = ", ".join(repr(a) for a in ex.get("in", []))
            test_lines.append("    @unittest.expectedFailure")
            test_lines.append(f"    def test_{fname}_intent_{i}(self):")
            test_lines.append(f"        self.assertEqual(core.{fname}({args}), {ex['out']!r})")
            test_lines.append("")
        if not examples:
            test_lines.append("    @unittest.expectedFailure")
            test_lines.append(f"    def test_{fname}_intent(self):")
            test_lines.append(f"        core.{fname}()")
            test_lines.append("")
    test_lines.extend(['', 'if __name__ == "__main__":', "    unittest.main()"])
    tests_py = "\n".join(test_lines) + "\n"

    readme_plan = {
        "direction": direction,
        "slug": slug,
        "title": _title(slug),
        "decisions": decisions,
        "pack_slugs": [(None, slug)],
        "web": web,
    }
    merged = {
        f"{slug}/__init__.py": f'"""{slug} (generated by detcode from a plan)."""\n',
        f"{slug}/core.py": core_py,
        f"{slug}/cli.py": _PLAN_CLI.lstrip("\n").replace("__PKG__", slug),
        f"{slug}/__main__.py": _PLAN_MAIN.replace("__PKG__", slug),
        "tests/__init__.py": "",
        f"tests/test_{slug}.py": tests_py,
        "README.md": _readme(readme_plan),
        "pyproject.toml": _pyproject(readme_plan),
        ".editorconfig": _EDITORCONFIG,
        ".gitignore": "__pycache__/\n*.py[cod]\n*.egg-info/\n",
        ".github/workflows/ci.yml": _ci_workflow(slug),
    }
    if web:
        from ..packs import webwrap

        for raw_path, raw_content in webwrap.files().items():
            merged[raw_path.replace("__PKG__", slug)] = raw_content.replace("__PKG__", slug)

    for path, content in merged.items():
        if path.endswith(".py"):
            ast.parse(content)

    files = tuple(sorted((ProjectFile(p, c) for p, c in merged.items()), key=lambda f: f.path))
    report = provenance(
        "build",
        RULE_VERSION,
        origin="plan",
        pack="plan",
        packs=["plan"],
        package=slug,
        packages=[slug],
        web=web,
        solved=[f for f, _, _ in solved],
        unsolved=[f for f, _, _ in unsolved],
        decisions=decisions,
        files=[f.path for f in files],
    )
    return Project(slug, _title(slug), files, report)


def render(project: Project) -> str:
    """Flatten a project to a single readable text bundle (for previews)."""
    parts = [f"# Project: {project.name} — {len(project.files)} files"]
    parts.append("# Decisions:")
    parts.extend(f"#   - {d}" for d in project.report["decisions"])
    for f in project.files:
        parts.append(f"\n# ===== {f.path} =====")
        parts.append(f.content.rstrip("\n"))
    return "\n".join(parts) + "\n"
