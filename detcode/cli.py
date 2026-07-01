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

from .determinism import TOOL_VERSION
from .engines import rewrite, scaffold


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8", newline="") as fh:
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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (rewrite.Unsafe, scaffold.SpecError) as exc:
        print(f"detcode: refused: {exc}", file=sys.stderr)
        return 2
    except (OSError, SyntaxError, json.JSONDecodeError) as exc:
        print(f"detcode: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
