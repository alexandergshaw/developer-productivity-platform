"""Local code index — query a codebase without it leaving the machine.

``detcode index --dir path/to/work/repo`` extracts symbols (functions,
classes, methods) into the local database: path, line, kind, name, and the
docstring's first line. Python is parsed with the AST; JS/TS get fixed
pattern matching. ``detcode query`` then answers "where is X handled?" with
path:line citations.

Nothing is uploaded anywhere — detcode has no remote to upload to. The index
lives in .detcode/detcode.db on your machine, and only symbol metadata is
stored, never full source.
"""
from __future__ import annotations

import ast
import os
import re

MAX_FILE_BYTES = 600_000
_SKIP_DIRS = frozenset(
    [".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
     ".pytest_cache", ".mypy_cache", ".detcode"]
)
_JS_PATTERN = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
    r"(?P<kind>function|class)\s+(?P<name>[A-Za-z_$][\w$]*)"
)
_JS_ARROW = re.compile(
    r"^\s*(?:export\s+)?const\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(",
)
_JS_EXTENSIONS = (".js", ".jsx", ".ts", ".tsx")


def _python_symbols(path: str, source: str) -> list[dict]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[dict] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            doc = ast.get_docstring(node) or ""
            out.append({
                "path": path, "line": node.lineno, "kind": kind,
                "symbol": node.name, "doc": doc.splitlines()[0] if doc else "",
            })
    return out


def _js_symbols(path: str, source: str) -> list[dict]:
    out: list[dict] = []
    for lineno, line in enumerate(source.splitlines(), 1):
        match = _JS_PATTERN.match(line)
        if match:
            out.append({
                "path": path, "line": lineno, "kind": match.group("kind"),
                "symbol": match.group("name"), "doc": "",
            })
            continue
        arrow = _JS_ARROW.match(line)
        if arrow:
            out.append({
                "path": path, "line": lineno, "kind": "function",
                "symbol": arrow.group("name"), "doc": "",
            })
    return out


def index_source(path: str, source: str) -> list[dict]:
    if path.endswith(".py"):
        return _python_symbols(path, source)
    if path.endswith(_JS_EXTENSIONS):
        return _js_symbols(path, source)
    return []


def index_tree(root: str) -> list[dict]:
    """Symbols for every indexable file under ``root``, sorted and stable."""
    entries: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.startswith("."))
        for fname in sorted(filenames):
            if not (fname.endswith(".py") or fname.endswith(_JS_EXTENSIONS)):
                continue
            full = os.path.join(dirpath, fname)
            try:
                if os.path.getsize(full) > MAX_FILE_BYTES:
                    continue
                with open(full, "r", encoding="utf-8-sig", errors="replace") as fh:
                    source = fh.read()
            except OSError:
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            entries.extend(index_source(rel, source))
    entries.sort(key=lambda e: (e["path"], e["line"], e["symbol"]))
    return entries
