"""Deterministic docstring generation.

Writing docstrings is one of the most common LLM requests, and the structural
half is purely mechanical: a summary derived from the function's name, an Args
section from the signature, Returns/Yields from annotations and the AST, and
Raises from the raise statements. Same source, byte-identical docstrings.

Honesty rule: nothing is invented. Descriptions are derived from names only
("apply_discount" → "Apply discount."), and functions that already have a
docstring are left untouched (or refused, when targeted explicitly).
"""
from __future__ import annotations

import ast
from dataclasses import dataclass

from ..determinism import provenance
from ..sourceedit import TextEdit, apply_edits

RULE_VERSION = "1"

# Name-prefix heuristics for the summary line, tried in fixed order.
_PREFIXES = (
    ("is_", "Return whether {rest}."),
    ("has_", "Return whether it has {rest}."),
    ("can_", "Return whether it can {rest}."),
    ("get_", "Return the {rest}."),
    ("set_", "Set the {rest}."),
    ("to_", "Convert to {rest}."),
    ("from_", "Build from {rest}."),
)


class DocError(Exception):
    """The docstring target was missing, ambiguous, or already documented."""


@dataclass
class Result:
    source: str
    changed: bool
    report: dict


def _words(name: str) -> str:
    return " ".join(part for part in name.split("_") if part)


def _summary(name: str) -> str:
    for prefix, template in _PREFIXES:
        if name.startswith(prefix) and len(name) > len(prefix):
            return template.format(rest=_words(name[len(prefix):]))
    words = _words(name)
    return (words[0].upper() + words[1:] + ".") if words else "Function."


def _param_lines(fn) -> list[str]:
    a = fn.args
    lines: list[str] = []
    positional = list(a.posonlyargs) + list(a.args)
    defaults: dict[str, str] = {}
    for arg, default in zip(reversed(positional), reversed(a.defaults)):
        defaults[arg.arg] = ast.unparse(default)
    for arg, default in zip(a.kwonlyargs, a.kw_defaults):
        if default is not None:
            defaults[arg.arg] = ast.unparse(default)

    def describe(arg, star: str = "") -> str:
        qualifiers = []
        if arg.annotation is not None:
            qualifiers.append(ast.unparse(arg.annotation))
        if arg.arg in defaults:
            qualifiers.append(f"default {defaults[arg.arg]}")
        suffix = f" ({', '.join(qualifiers)})" if qualifiers else ""
        words = _words(arg.arg)
        return f"{star}{arg.arg}{suffix}: The {words}."

    for arg in positional:
        if arg.arg in ("self", "cls"):
            continue
        lines.append(describe(arg))
    if a.vararg:
        lines.append(describe(a.vararg, star="*"))
    for arg in a.kwonlyargs:
        lines.append(describe(arg))
    if a.kwarg:
        lines.append(describe(a.kwarg, star="**"))
    return lines


def _raises(fn) -> list[str]:
    names = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Raise) and node.exc is not None:
            exc = node.exc
            if isinstance(exc, ast.Call):
                exc = exc.func
            if isinstance(exc, ast.Name):
                names.add(exc.id)
            elif isinstance(exc, ast.Attribute):
                names.add(exc.attr)
    return sorted(names)


def _returns_line(fn) -> str | None:
    if any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(fn)):
        return "Yields:\n    Values produced by the generator."
    if fn.returns is not None:
        return f"Returns:\n    {ast.unparse(fn.returns)}."
    return None


def _build_docstring(fn, indent: str) -> str:
    summary = _summary(fn.name)
    params = _param_lines(fn)
    raises = _raises(fn)
    returns = _returns_line(fn)

    if not params and not raises and returns is None:
        return f'{indent}"""{summary}"""\n'

    content = [f'"""{summary}', ""]
    if params:
        content.append("Args:")
        content.extend(f"    {p}" for p in params)
        content.append("")
    if returns:
        content.extend(returns.splitlines())
        content.append("")
    if raises:
        content.append("Raises:")
        content.extend(f"    {r}: Raised by this function." for r in raises)
        content.append("")
    while content and content[-1] == "":
        content.pop()
    content.append('"""')
    return "".join(f"{indent}{line}\n" if line else "\n" for line in content)


def add_docstrings(source: str, func: str | None = None) -> Result:
    """Insert generated docstrings.

    With ``func``: document exactly that function (refused if it already has a
    docstring or is missing/ambiguous). Without: document every function that
    lacks one, skipping documented ones — so the operation is idempotent.
    """
    tree = ast.parse(source)
    all_fns = [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    if func is not None:
        matches = [f for f in all_fns if f.name == func]
        if not matches:
            raise DocError(f"no function named {func!r} in source")
        if len(matches) > 1:
            raise DocError(f"{len(matches)} functions named {func!r}; target is ambiguous")
        if ast.get_docstring(matches[0]) is not None:
            raise DocError(f"{func!r} already has a docstring; not overwriting it")
        targets = matches
    else:
        targets = [f for f in all_fns if ast.get_docstring(f) is None]

    edits: list[TextEdit] = []
    documented: list[str] = []
    for fn in sorted(targets, key=lambda f: (f.lineno, f.col_offset)):
        if not fn.body:
            continue
        first = fn.body[0]
        indent = " " * first.col_offset
        docstring = _build_docstring(fn, indent)
        edits.append(TextEdit(first.lineno, 0, first.lineno, 0, docstring))
        documented.append(fn.name)

    new_source = apply_edits(source, edits)
    ast.parse(new_source)

    report = provenance(
        "add_docstrings", RULE_VERSION, documented=documented, count=len(documented)
    )
    return Result(new_source, changed=bool(edits), report=report)
