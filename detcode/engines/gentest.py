"""Deterministic test generation.

Given a function name and input/output examples, emit a runnable ``unittest``
module — one test method per example. LLMs write tests constantly; the
example-to-assertion translation is purely mechanical, so it can be done
deterministically and byte-reproducibly.

Two source modes:
- ``spec["source"]``: the code under test is embedded in the generated file
  (self-contained — pairs naturally with ``synth`` output).
- ``spec["module"]``: the generated file imports the function instead.

**Edge cases** (source mode, on by default; ``"edge_cases": false`` disables):
the LLM behavior being mimicked is "it thought of edge cases I didn't
specify". Deterministically: integer literals in the function's comparisons
become boundary probes (``if percent < 0`` → try -1, 0, 1), plus standard
probes per parameter type (0/1/-1, "", empty list). Each probe runs under a
line-count budget and its *current* behavior is pinned as a characterization
test — ``assertEqual`` for returns, ``assertRaises`` for exceptions. These
document what the code does today at its boundaries; they do not guess intent.

Generating tests that *fail* is valid (that is TDD); the generator only
guarantees the file is syntactically valid and faithful to the examples.
"""
from __future__ import annotations

import ast
import sys
from dataclasses import dataclass

from ..determinism import provenance

RULE_VERSION = "2"
INDENT = "    "
_MAX_EDGE_TESTS = 12
_MAX_PROBE_LINES = 100_000  # line-event budget per probe run (never wall-clock)


class SpecError(Exception):
    """The gentest spec was malformed."""


@dataclass
class Result:
    source: str
    report: dict


def _camel(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_") if part)


def _validate(spec: dict) -> tuple[str, list[dict]]:
    if not isinstance(spec, dict):
        raise SpecError("spec must be a JSON object")
    func = spec.get("function")
    if not isinstance(func, str) or not func.isidentifier():
        raise SpecError("spec must name a valid 'function'")
    examples = spec.get("examples")
    if not isinstance(examples, list) or not examples:
        raise SpecError("spec must provide a non-empty 'examples' list")
    for i, ex in enumerate(examples):
        if not isinstance(ex, dict) or "in" not in ex or "out" not in ex:
            raise SpecError(f"example {i} must have 'in' and 'out'")
        if not isinstance(ex["in"], list):
            raise SpecError(f"example {i} 'in' must be a list")
    return func, examples


class _ProbeBudgetExceeded(Exception):
    pass


def _run_bounded(fn, args: list):
    """Run ``fn(*args)`` under a deterministic line-event budget.

    Returns ("equal", value) or ("raises", exception name). Probes that
    exhaust the budget are discarded by the caller — a wall-clock timeout
    would break determinism, a line count does not.
    """
    remaining = [_MAX_PROBE_LINES]

    def tracer(frame, event, arg):
        if event == "line":
            remaining[0] -= 1
            if remaining[0] <= 0:
                raise _ProbeBudgetExceeded
        return tracer

    old = sys.gettrace()
    sys.settrace(tracer)
    try:
        try:
            return ("equal", fn(*args))
        except _ProbeBudgetExceeded:
            raise
        except Exception as exc:
            return ("raises", type(exc).__name__)
    finally:
        sys.settrace(old)


def _boundary_ints(fn) -> list[int]:
    """Integer literals used in comparisons, each expanded to k-1, k, k+1."""
    values: set[int] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Compare):
            for side in [node.left, *node.comparators]:
                if (
                    isinstance(side, ast.Constant)
                    and isinstance(side.value, int)
                    and not isinstance(side.value, bool)
                ):
                    values.update((side.value - 1, side.value, side.value + 1))
    return sorted(values)


def _probes_for(value, boundary_ints: list[int]) -> list:
    if isinstance(value, bool):
        return [True, False]
    if isinstance(value, int):
        return [0, 1, -1] + boundary_ints
    if isinstance(value, str):
        return ["", "a", " "]
    if isinstance(value, list):
        return [[], value[:1]]
    return []


def _edge_cases(source: str, func: str, examples: list[dict]) -> list[tuple]:
    """Derive (args, kind, outcome) characterization probes, capped and deduped."""
    tree = ast.parse(source)
    fns = [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func
    ]
    if len(fns) != 1:
        return []
    boundary = _boundary_ints(fns[0])

    namespace: dict = {}
    try:
        exec(compile(source, "<gentest>", "exec"), namespace)
        fn = namespace[func]
        if not callable(fn):
            return []
    except Exception:
        return []

    base = examples[0]["in"]
    known = {repr(ex["in"]) for ex in examples}
    cases: list[tuple] = []
    for position in range(len(base)):
        for probe in _probes_for(base[position], boundary):
            args = list(base)
            args[position] = probe
            key = repr(args)
            if key in known:
                continue
            known.add(key)
            try:
                kind, outcome = _run_bounded(fn, args)
            except _ProbeBudgetExceeded:
                continue
            if kind == "equal":
                try:  # the outcome must survive a repr round-trip as a literal
                    if ast.literal_eval(repr(outcome)) != outcome:
                        continue
                except (ValueError, SyntaxError):
                    continue
            cases.append((args, kind, outcome))
            if len(cases) >= _MAX_EDGE_TESTS:
                return cases
    return cases


def gentest(spec: dict) -> Result:
    func, examples = _validate(spec)

    source = spec.get("source")
    module = spec.get("module")
    if source is not None:
        try:
            ast.parse(source)
        except SyntaxError as exc:
            raise SpecError(f"'source' is not valid Python: {exc}") from exc
        header = source.rstrip("\n")
        mode = "inline-source"
    elif module is not None:
        if not all(part.isidentifier() for part in str(module).split(".")):
            raise SpecError(f"'module' {module!r} is not a valid module path")
        header = f"from {module} import {func}"
        mode = "import"
    else:
        raise SpecError("spec needs 'source' (code under test) or 'module' (import path)")

    lines = [
        f'"""Tests for {func} (generated by detcode gentest)."""',
        "import unittest",
        "",
        "",
        header,
        "",
        "",
        f"class Test{_camel(func)}(unittest.TestCase):",
    ]
    for i, ex in enumerate(examples):
        args = ", ".join(repr(a) for a in ex["in"])
        lines.append(f"{INDENT}def test_{func}_{i}(self):")
        lines.append(f"{INDENT}{INDENT}self.assertEqual({func}({args}), {ex['out']!r})")
        lines.append("")

    edges: list[tuple] = []
    if mode == "inline-source" and spec.get("edge_cases", True):
        edges = _edge_cases(source, func, examples)
    for i, (args_list, kind, outcome) in enumerate(edges):
        args = ", ".join(repr(a) for a in args_list)
        lines.append(f"{INDENT}def test_{func}_edge_{i}(self):")
        lines.append(f"{INDENT}{INDENT}# Characterization: pins current behavior at a boundary input.")
        if kind == "raises":
            lines.append(f"{INDENT}{INDENT}with self.assertRaises({outcome}):")
            lines.append(f"{INDENT}{INDENT}{INDENT}{func}({args})")
        else:
            lines.append(f"{INDENT}{INDENT}self.assertEqual({func}({args}), {outcome!r})")
        lines.append("")

    lines.extend(
        [
            "",
            'if __name__ == "__main__":',
            f"{INDENT}unittest.main()",
        ]
    )
    generated = "\n".join(lines) + "\n"
    ast.parse(generated)  # generated file must be valid Python

    report = provenance(
        "gentest",
        RULE_VERSION,
        function=func,
        cases=len(examples),
        edge_cases=len(edges),
        mode=mode,
    )
    return Result(generated, report)
