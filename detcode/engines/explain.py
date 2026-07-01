"""Deterministic code explanation.

"Explain this code" is a core LLM use case, and its structural half is fully
deterministic: everything reported here is read straight off the AST and
rendered through fixed English templates. Same source, same explanation,
byte-for-byte. No interpretation is invented; if the code has no docstring the
explanation says so rather than guessing intent.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass

from ..determinism import provenance

RULE_VERSION = "1"


class ExplainError(Exception):
    """The explanation target was missing or ambiguous."""


@dataclass
class Result:
    text: str
    report: dict


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {plural or word + 's'}"


def _signature(fn) -> str:
    prefix = "async def " if isinstance(fn, ast.AsyncFunctionDef) else "def "
    sig = f"{prefix}{fn.name}({ast.unparse(fn.args)})"
    if fn.returns is not None:
        sig += f" -> {ast.unparse(fn.returns)}"
    return sig


def _complexity(fn) -> int:
    """McCabe-style approximation: 1 + one per branch point."""
    score = 1
    for node in ast.walk(fn):
        if isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp, ast.Assert)):
            score += 1
        elif isinstance(node, ast.BoolOp):
            score += len(node.values) - 1
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            score += sum(1 + len(gen.ifs) for gen in node.generators)
    return score


def _calls(fn) -> list[str]:
    names = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            try:
                names.add(ast.unparse(node.func))
            except Exception:
                continue
    return sorted(names)


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


def _counts(fn) -> dict:
    loops = branches = returns = yields = trys = 0
    for node in ast.walk(fn):
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            loops += 1
        elif isinstance(node, ast.If):
            branches += 1
        elif isinstance(node, ast.Return):
            returns += 1
        elif isinstance(node, (ast.Yield, ast.YieldFrom)):
            yields += 1
        elif isinstance(node, ast.Try):
            trys += 1
    return {"loops": loops, "branches": branches, "returns": returns, "yields": yields, "trys": trys}


def _explain_function(fn) -> str:
    lines = (fn.end_lineno or fn.lineno) - fn.lineno + 1
    counts = _counts(fn)
    out = [
        f"{_signature(fn)} — {_plural(lines, 'line')}, complexity {_complexity(fn)}, "
        f"defined at line {fn.lineno}."
    ]

    doc = ast.get_docstring(fn)
    if doc:
        out.append(f'Docstring: "{doc.splitlines()[0]}"')
    else:
        out.append("No docstring.")

    flow = []
    if counts["branches"]:
        flow.append(_plural(counts["branches"], "branch", "branches"))
    if counts["loops"]:
        flow.append(_plural(counts["loops"], "loop"))
    if counts["trys"]:
        flow.append(_plural(counts["trys"], "try/except block"))
    if flow:
        out.append(f"Control flow: {', '.join(flow)}.")
    else:
        out.append("Control flow: straight-line (no branches or loops).")

    if counts["yields"]:
        out.append("It is a generator (contains yield).")
    if counts["returns"]:
        out.append(f"Exits via {_plural(counts['returns'], 'return statement')}.")

    calls = _calls(fn)
    if calls:
        out.append(f"Calls: {', '.join(f'{c}()' for c in calls)}.")

    raises = _raises(fn)
    if raises:
        out.append(f"Raises: {', '.join(raises)}.")

    return "\n".join(out)


def _explain_module(tree: ast.Module, source: str) -> str:
    total_lines = len(source.splitlines())
    imports: set[str] = set()
    classes: list[ast.ClassDef] = []
    functions: list = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            imports.update(alias.asname or alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.ClassDef):
            classes.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(node)

    out = [
        f"Module — {_plural(total_lines, 'line')}, {_plural(len(imports), 'import')}, "
        f"{_plural(len(classes), 'class', 'classes')}, {_plural(len(functions), 'function')}."
    ]
    doc = ast.get_docstring(tree)
    if doc:
        out.append(f'Docstring: "{doc.splitlines()[0]}"')
    else:
        out.append("No module docstring.")
    if imports:
        out.append(f"Imports: {', '.join(sorted(imports))}.")
    for cls in classes:
        methods = [
            n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        out.append(f"Class {cls.name}: {_plural(len(methods), 'method')} "
                   f"({', '.join(m.name for m in methods)})." if methods
                   else f"Class {cls.name}: no methods.")
    for fn in functions:
        fn_lines = (fn.end_lineno or fn.lineno) - fn.lineno + 1
        out.append(
            f"Function {_signature(fn)} — {_plural(fn_lines, 'line')}, "
            f"complexity {_complexity(fn)}."
        )
    return "\n".join(out)


def explain(source: str, func: str | None = None) -> Result:
    """Explain ``func`` in ``source``, or the whole module when ``func`` is None."""
    tree = ast.parse(source)
    if func is not None:
        matches = [
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func
        ]
        if not matches:
            raise ExplainError(f"no function named {func!r} in source")
        if len(matches) > 1:
            raise ExplainError(f"{len(matches)} functions named {func!r}; target is ambiguous")
        text = _explain_function(matches[0])
    else:
        text = _explain_module(tree, source)

    report = provenance("explain", RULE_VERSION, target=func or "<module>")
    return Result(text, report)
