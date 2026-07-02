"""Command-line front-end.

Deterministic in, deterministic out. Reads a file, applies a codemod, and by
default prints the transformed source to stdout. ``--diff`` prints a unified
diff instead; ``--write`` edits the file in place. An unsafe/refused
transformation exits with code 2 and a clear message on stderr.
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys

from . import cnl, planner
from .determinism import TOOL_VERSION
import os

from .engines import (
    builder,
    document,
    explain,
    gentest,
    plan as plan_engine,
    repair,
    retrieve,
    rewrite,
    scaffold,
    synth,
)


class _EditResult:
    """Adapter so planner Outcomes flow through the shared _emit path."""

    def __init__(self, outcome):
        self.source = outcome.new_source
        self.changed = outcome.changed
        self.report = outcome.report


def _read(path: str) -> str:
    # utf-8-sig strips a leading BOM if present (common on Windows editors),
    # which would otherwise break ast.parse. Output is always written BOM-less.
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        return fh.read()


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _emit(args, path: str, before: str, result) -> int:
    after = result.source
    if args.diff:
        diff = difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
        sys.stdout.writelines(diff)
    elif args.write:
        if result.changed:
            _write(path, after)
        print(
            f"{path}: {'updated' if result.changed else 'no change'} "
            f"({result.report.get('rule')})",
            file=sys.stderr,
        )
    else:
        sys.stdout.write(after)
    return 0


def _cmd_rename_local(args) -> int:
    before = _read(args.file)
    result = rewrite.rename_local(before, args.func, getattr(args, "from"), args.to)
    return _emit(args, args.file, before, result)


def _cmd_remove_unused_imports(args) -> int:
    before = _read(args.file)
    result = rewrite.remove_unused_imports(before)
    return _emit(args, args.file, before, result)


def _cmd_scaffold(args) -> int:
    spec = json.loads(_read(args.spec))
    result = scaffold.scaffold(spec)
    if args.out:
        _write(args.out, result.source)
        print(f"{args.out}: generated ({result.report.get('rule')})", file=sys.stderr)
    else:
        sys.stdout.write(result.source)
    return 0


def _cmd_synth(args) -> int:
    spec = json.loads(_read(args.examples))
    if args.name:
        spec["name"] = args.name
    result = retrieve.write_function(spec)
    if args.out:
        _write(args.out, result.source)
        print(
            f"{args.out}: synthesized {result.report.get('expr')}", file=sys.stderr
        )
    else:
        sys.stdout.write(result.source)
    return 0


def _cmd_repair(args) -> int:
    before = _read(args.file)
    spec = json.loads(_read(args.spec))
    result = repair.repair(before, spec)
    return _emit(args, args.file, before, result)


def _cmd_sort_imports(args) -> int:
    before = _read(args.file)
    result = rewrite.sort_imports(before)
    return _emit(args, args.file, before, result)


def _cmd_document(args) -> int:
    before = _read(args.file)
    result = document.add_docstrings(before, args.func)
    return _emit(args, args.file, before, result)


def _cmd_explain(args) -> int:
    result = explain.explain(_read(args.file), args.func)
    print(result.text)
    return 0


def _cmd_gentest(args) -> int:
    spec = json.loads(_read(args.spec))
    if args.file and "source" not in spec and "module" not in spec:
        spec["source"] = _read(args.file)
    result = gentest.gentest(spec)
    if args.out:
        _write(args.out, result.source)
        print(f"{args.out}: generated {result.report.get('cases')} tests", file=sys.stderr)
    else:
        sys.stdout.write(result.source)
    return 0


def _cmd_plan(args) -> int:
    result = plan_engine.make_plan(args.direction, name=args.name)
    print(result.questions, file=sys.stderr)
    out = args.out or result.report["plan_file"]
    if args.stdout:
        sys.stdout.write(result.plan_text)
    else:
        if os.path.exists(out):
            raise builder.BuildError(f"{out} already exists; refusing to overwrite")
        _write(out, result.plan_text)
        print(f"\nplan written to {out} — fill the examples, then:", file=sys.stderr)
        print(f"  detcode new --plan {out}", file=sys.stderr)
    return 0


def _cmd_new(args) -> int:
    if args.plan:
        if args.direction:
            raise builder.BuildError("give a direction OR --plan, not both")
        project = builder.build_from_plan(json.loads(_read(args.plan)), web=args.web)
    elif args.direction:
        project = builder.build(args.direction, name=args.name, web=args.web)
    else:
        raise builder.BuildError('give a direction (detcode new "...") or --plan file.json')
    if args.dry_run:
        print(f"project: {project.name} ({len(project.files)} files)")
        print("decisions:")
        for decision in project.report["decisions"]:
            print(f"  - {decision}")
        print("files:")
        for f in project.files:
            print(f"  {f.path}")
        return 0

    out_dir = args.out or project.name
    # Never overwrite: check every target before writing anything.
    for f in project.files:
        target = os.path.join(out_dir, f.path.replace("/", os.sep))
        if os.path.exists(target):
            raise builder.BuildError(f"{target} already exists; refusing to overwrite")
    for f in project.files:
        target = os.path.join(out_dir, f.path.replace("/", os.sep))
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        _write(target, f.content)
    print(f"{out_dir}: generated {len(project.files)} files ({project.name})", file=sys.stderr)
    for decision in project.report["decisions"]:
        print(f"  - {decision}", file=sys.stderr)
    return 0


def _cmd_do(args) -> int:
    intents = cnl.parse_all(args.command)
    before = _read(args.file) if args.file else None
    outcome = planner.run_all(intents, before)
    status = 0
    if outcome.new_source is not None:
        status = _emit(args, args.file, before, _EditResult(outcome))
    elif args.diff or args.write:
        print(
            "detcode: this command generates new content; printing to stdout",
            file=sys.stderr,
        )
    if outcome.output:
        print(outcome.output)
    return status


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--file", required=True, help="Python source file to operate on")
    out = p.add_mutually_exclusive_group()
    out.add_argument("--write", action="store_true", help="edit the file in place")
    out.add_argument("--diff", action="store_true", help="print a unified diff")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="detcode",
        description="A deterministic coding assistant (no LLM).",
    )
    parser.add_argument("--version", action="version", version=f"detcode {TOOL_VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    rl = sub.add_parser("rename-local", help="rename a local variable inside a function")
    _add_common(rl)
    rl.add_argument("--func", required=True, help="function containing the variable")
    rl.add_argument("--from", required=True, dest="from", help="current name")
    rl.add_argument("--to", required=True, help="new name")
    rl.set_defaults(handler=_cmd_rename_local)

    ri = sub.add_parser(
        "remove-unused-imports", help="remove module-level imports that are never used"
    )
    _add_common(ri)
    ri.set_defaults(handler=_cmd_remove_unused_imports)

    sc = sub.add_parser(
        "scaffold", help="generate a Python module (dataclasses/enums) from a JSON spec"
    )
    sc.add_argument("--spec", required=True, help="JSON spec file")
    sc.add_argument("--out", help="write generated module to this path (default: stdout)")
    sc.set_defaults(handler=_cmd_scaffold)

    sy = sub.add_parser(
        "synth", help="synthesize a function from input/output examples"
    )
    sy.add_argument("--examples", required=True, help="JSON file of input/output examples")
    sy.add_argument("--name", help="name for the synthesized function (default: f)")
    sy.add_argument("--out", help="write the function to this path (default: stdout)")
    sy.set_defaults(handler=_cmd_synth)

    rp = sub.add_parser(
        "repair", help="repair a buggy function so it passes input/output tests"
    )
    _add_common(rp)
    rp.add_argument(
        "--spec", required=True, help="JSON spec: function name, examples, max_edits"
    )
    rp.set_defaults(handler=_cmd_repair)

    si = sub.add_parser("sort-imports", help="canonically order the import block")
    _add_common(si)
    si.set_defaults(handler=_cmd_sort_imports)

    dc = sub.add_parser(
        "document", help="insert generated docstrings (functions lacking one)"
    )
    _add_common(dc)
    dc.add_argument("--func", help="document only this function (default: all undocumented)")
    dc.set_defaults(handler=_cmd_document)

    ex = sub.add_parser("explain", help="explain a function or module (AST-derived)")
    ex.add_argument("--file", required=True, help="Python source file")
    ex.add_argument("--func", help="function to explain (default: whole module)")
    ex.set_defaults(handler=_cmd_explain)

    gt = sub.add_parser(
        "gentest", help="generate a unittest module from input/output examples"
    )
    gt.add_argument("--spec", required=True, help="JSON spec: function, examples, source|module")
    gt.add_argument("--file", help="embed this file as the code under test")
    gt.add_argument("--out", help="write the test module to this path (default: stdout)")
    gt.set_defaults(handler=_cmd_gentest)

    pl = sub.add_parser(
        "plan", help="spec interview for a direction detcode cannot build yet"
    )
    pl.add_argument("direction", help='e.g. "a citation formatter"')
    pl.add_argument("--name", help="override the derived package name")
    pl.add_argument("--out", help="plan file path (default: <name>.plan.json)")
    pl.add_argument("--stdout", action="store_true", help="print the plan instead of writing it")
    pl.set_defaults(handler=_cmd_plan)

    nw = sub.add_parser(
        "new", help='generate a project from a general direction, e.g. "resume tailorer"'
    )
    nw.add_argument(
        "direction", nargs="?", help='e.g. "resume tailorer" or "teaching assistant app"'
    )
    nw.add_argument("--plan", help="build from a filled plan file instead of a direction")
    nw.add_argument("--out", help="target directory (default: the derived package name)")
    nw.add_argument("--name", help="override the derived package name")
    nw.add_argument(
        "--web", action="store_true",
        help="add a stdlib WSGI web UI over the CLI (also triggered by 'with a web ui')",
    )
    nw.add_argument(
        "--dry-run", action="store_true", help="print decisions and file list, write nothing"
    )
    nw.set_defaults(handler=_cmd_new)

    do = sub.add_parser(
        "do",
        help='run a controlled-natural-language command, e.g. "remove unused imports"',
    )
    do.add_argument("command", help='e.g. "write a function double where double(2) == 4"')
    do.add_argument("--file", help="Python source file (needed by file-editing commands)")
    out = do.add_mutually_exclusive_group()
    out.add_argument("--write", action="store_true", help="edit the file in place")
    out.add_argument("--diff", action="store_true", help="print a unified diff")
    do.set_defaults(handler=_cmd_do)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (
        rewrite.Unsafe,
        scaffold.SpecError,
        synth.SpecError,
        synth.NoSolution,
        repair.SpecError,
        repair.NoRepair,
        explain.ExplainError,
        gentest.SpecError,
        document.DocError,
        builder.BuildError,
        cnl.CNLError,
        planner.UnknownIntent,
        planner.MissingSource,
    ) as exc:
        print(f"detcode: refused: {exc}", file=sys.stderr)
        return 2
    except (OSError, SyntaxError, json.JSONDecodeError) as exc:
        print(f"detcode: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
