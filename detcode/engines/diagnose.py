"""Deterministic diagnostics — the agent that watches your code as you type.

All checks read straight off the AST/tokens through fixed rules; same source,
same problems, byte for byte. Items carry an optional ``fix`` naming the
detcode command that repairs them (one-click quick fixes in the workbench).
"""
from __future__ import annotations

import ast
import io
import tokenize

from .rewrite import _bound_name, _used_names

MAX_ITEMS = 50


def _item(line: int, col: int, severity: str, message: str, fix: str | None = None) -> dict:
    return {"line": line, "col": col, "severity": severity, "message": message, "fix": fix}


def diagnostics(source: str) -> list[dict]:
    """Problems in ``source``, ordered by (line, col). Never guesses intent."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [_item(exc.lineno or 1, exc.offset or 1, "error", f"syntax error: {exc.msg}")]

    out: list[dict] = []

    # Unused module-level imports (fix: remove unused imports).
    used = _used_names(tree)
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    continue
                if _bound_name(alias) not in used:
                    out.append(_item(
                        node.lineno, node.col_offset + 1, "warning",
                        f"unused import {_bound_name(alias)!r}",
                        fix="remove unused imports",
                    ))

    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            out.append(_item(
                node.lineno, node.col_offset + 1, "warning",
                "bare except swallows every error, including KeyboardInterrupt",
            ))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for default in list(node.args.defaults) + [
                d for d in node.args.kw_defaults if d is not None
            ]:
                if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                    out.append(_item(
                        default.lineno, default.col_offset + 1, "warning",
                        f"mutable default argument in {node.name!r} is shared "
                        "across calls",
                    ))
        elif isinstance(node, ast.Compare):
            for op, comparator in zip(node.ops, node.comparators):
                if (
                    isinstance(op, (ast.Eq, ast.NotEq))
                    and isinstance(comparator, ast.Constant)
                    and comparator.value is None
                ):
                    verb = "is" if isinstance(op, ast.Eq) else "is not"
                    out.append(_item(
                        node.lineno, node.col_offset + 1, "info",
                        f"comparison with None: prefer '{verb} None'",
                    ))

    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                comment = tok.string.lstrip("# ").upper()
                if comment.startswith(("TODO", "FIXME", "XXX", "HACK")):
                    out.append(_item(
                        tok.start[0], tok.start[1] + 1, "info", tok.string.lstrip("# ")
                    ))
    except tokenize.TokenizeError:
        pass

    for lineno, line in enumerate(source.splitlines(), 1):
        indent = line[: len(line) - len(line.lstrip())]
        if "\t" in indent:
            out.append(_item(lineno, 1, "warning", "tab in indentation (use spaces)"))

    out.sort(key=lambda i: (i["line"], i["col"], i["message"]))
    return out[:MAX_ITEMS]
