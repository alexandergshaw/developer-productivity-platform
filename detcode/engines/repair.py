"""Deterministic bug-fix / repair.

Given a buggy function and a set of input/output tests, search a bounded space
of small edits for a patch that makes every test pass.

Design:

- **Fault check**: run the function on the tests. If all pass, there is nothing
  to repair (a deterministic no-op).
- **Fault localization** (spectrum-based): trace which lines each example
  executes. Lines executed by failing examples — and rarely by passing ones —
  are searched first. This is a deterministic *ordering* heuristic only; it
  never changes what is reachable, just how soon the search gets there.
- **Mutation space**: enumerate token-level edits *within the target function* —
  swap an operator within its group (arithmetic / comparison / boolean /
  augmented assignment), flip ``True``/``False``, nudge an integer constant
  (n-1, n+1, 0, 1), or swap one variable name for another in scope (the classic
  wrong-variable bug). Over-generation is safe here: every candidate must pass
  the full test suite to be accepted, so wrong mutations are simply filtered
  out by the oracle.
- **Search**: try all 1-edit patches in localized order, then (optionally) all
  2-edit combinations, bounded by an op-count budget. The first patch that
  passes every test is returned — deterministic by construction.
- If nothing works, repair is refused (:class:`NoRepair`) rather than emitting a
  plausible-but-unverified change.

Repair runs the code under test (as running its tests would); it execs in a
fresh namespace with the standard builtins.
"""
from __future__ import annotations

import ast
import io
import keyword
import sys
import tokenize
from dataclasses import dataclass

from ..determinism import BudgetExceeded, OpBudget, provenance
from ..sourceedit import OverlappingEdits, TextEdit, apply_edits
from ..verify import parses

RULE_VERSION = "2"
DEFAULT_BUDGET = 200_000

# Fixed replacement groups — part of the determinism contract.
_ARITH = ("+", "-", "*", "//", "%")
_COMPARE = ("<", "<=", ">", ">=", "==", "!=")
_AUG = ("+=", "-=", "*=", "//=", "%=")


class NoRepair(Exception):
    """No verified patch was found within the edit/budget limits."""


class SpecError(Exception):
    """The repair spec was malformed."""


@dataclass
class Result:
    source: str
    changed: bool
    report: dict


def _byte_col(line: str, char_col: int) -> int:
    """tokenize reports character columns; apply_edits wants UTF-8 byte columns."""
    return len(line[:char_col].encode("utf-8"))


def _replacements_for(tok, names: tuple[str, ...]) -> list[str]:
    s = tok.string
    if tok.type == tokenize.OP:
        if s in _ARITH:
            return [x for x in _ARITH if x != s]
        if s in _COMPARE:
            return [x for x in _COMPARE if x != s]
        if s in _AUG:
            return [x for x in _AUG if x != s]
    elif tok.type == tokenize.NAME:
        if s == "and":
            return ["or"]
        if s == "or":
            return ["and"]
        if s == "True":
            return ["False"]
        if s == "False":
            return ["True"]
        if not keyword.iskeyword(s) and s in names:
            # Wrong-variable bugs: try every other in-scope name here.
            return [n for n in names if n != s]
    elif tok.type == tokenize.NUMBER:
        try:
            n = int(s)
        except ValueError:
            return []  # only decimal integer literals
        out: list[str] = []
        for v in (n - 1, n + 1, 0, 1):
            text = str(v)
            if v != n and text not in out:
                out.append(text)
        return out
    return []


def _scope_names(fn) -> tuple[str, ...]:
    """Parameters and assigned locals of ``fn``, in sorted order."""
    names: set[str] = set()
    a = fn.args
    for group in (a.posonlyargs, a.args, a.kwonlyargs):
        names.update(arg.arg for arg in group)
    if a.vararg:
        names.add(a.vararg.arg)
    if a.kwarg:
        names.add(a.kwarg.arg)
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
    return tuple(sorted(names))


def _find_function(tree, name):
    matches = [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    ]
    if not matches:
        raise SpecError(f"no function named {name!r} in source")
    if len(matches) > 1:
        raise SpecError(f"{len(matches)} functions named {name!r}; specify a unique one")
    return matches[0]


def _mutation_sites(source: str, fn) -> list[list[TextEdit]]:
    """One entry per mutable token in ``fn``; each is a list of alternative edits."""
    lines = source.splitlines(keepends=True)
    names = _scope_names(fn)
    sites: list[list[TextEdit]] = []
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    for tok in tokens:
        srow = tok.start[0]
        if srow < fn.lineno or srow > (fn.end_lineno or fn.lineno):
            continue
        if tok.string == fn.name:
            continue  # never mutate the function's own name
        reps = _replacements_for(tok, names)
        if not reps:
            continue
        (srow, scol), (erow, ecol) = tok.start, tok.end
        edits = [
            TextEdit(
                srow,
                _byte_col(lines[srow - 1], scol),
                erow,
                _byte_col(lines[erow - 1], ecol),
                rep,
            )
            for rep in reps
        ]
        sites.append(edits)
    return sites


def _coverage(source: str, func_name: str, example: dict) -> tuple[bool, set[int]]:
    """Run one example under a line tracer; return (passed, executed linenos)."""
    namespace: dict = {}
    exec(compile(source, "<repair>", "exec"), namespace)
    fn = namespace[func_name]
    executed: set[int] = set()

    def tracer(frame, event, arg):
        if frame.f_code.co_filename == "<repair>":
            if event == "line":
                executed.add(frame.f_lineno)
            return tracer
        return None

    old = sys.gettrace()
    sys.settrace(tracer)
    try:
        try:
            passed = fn(*example["in"]) == example["out"]
        except Exception:
            passed = False
    finally:
        sys.settrace(old)
    return passed, executed


def _rank_sites(
    source: str, func_name: str, examples: list[dict], sites: list[list[TextEdit]]
) -> list[list[TextEdit]]:
    """Order mutation sites by spectrum-based suspiciousness.

    Lines executed by more failing examples (and fewer passing ones) come
    first; unexecuted-by-failure sites keep their original relative order at
    the back. Ordering only — every site is still eventually tried.
    """
    fail_hits: dict[int, int] = {}
    pass_hits: dict[int, int] = {}
    try:
        for example in examples:
            passed, executed = _coverage(source, func_name, example)
            bucket = pass_hits if passed else fail_hits
            for line in executed:
                bucket[line] = bucket.get(line, 0) + 1
    except Exception:
        return sites  # tracing failed; fall back to source order

    def key(indexed_site: tuple[int, list[TextEdit]]) -> tuple:
        index, site = indexed_site
        line = site[0].lineno
        fails = fail_hits.get(line, 0)
        passes = pass_hits.get(line, 0)
        return (0 if fails else 1, -fails, passes, index)

    return [site for _, site in sorted(enumerate(sites), key=key)]


def _make_oracle(func_name: str, examples: list[dict]):
    def passes(candidate: str) -> bool:
        if not parses(candidate):
            return False
        namespace: dict = {}
        try:
            exec(compile(candidate, "<repair>", "exec"), namespace)
            fn = namespace.get(func_name)
            if not callable(fn):
                return False
            for ex in examples:
                if fn(*ex["in"]) != ex["out"]:
                    return False
        except Exception:
            return False
        return True

    return passes


def repair(source: str, spec: dict) -> Result:
    func_name = spec.get("function")
    if not isinstance(func_name, str) or not func_name:
        raise SpecError("spec must name a 'function' to repair")
    examples = spec.get("examples")
    if not isinstance(examples, list) or not examples:
        raise SpecError("spec must provide a non-empty 'examples' list")
    for i, ex in enumerate(examples):
        if not isinstance(ex, dict) or "in" not in ex or "out" not in ex:
            raise SpecError(f"example {i} must have 'in' and 'out'")
        if not isinstance(ex["in"], list):
            raise SpecError(f"example {i} 'in' must be a list")
    max_edits = int(spec.get("max_edits", 1))
    budget = OpBudget(int(spec.get("budget", DEFAULT_BUDGET)))

    tree = ast.parse(source)
    fn = _find_function(tree, func_name)
    passes = _make_oracle(func_name, examples)

    if passes(source):
        report = provenance("repair", RULE_VERSION, status="already-passing", edits=0)
        return Result(source, changed=False, report=report)

    sites = _rank_sites(source, func_name, examples, _mutation_sites(source, fn))

    try:
        # 1-edit patches, in fixed order.
        for site in sites:
            for edit in site:
                budget.tick()
                candidate = apply_edits(source, [edit])
                if passes(candidate):
                    return _accept(candidate, [edit], budget)

        # 2-edit patches, if allowed.
        if max_edits >= 2:
            for i in range(len(sites)):
                for j in range(i + 1, len(sites)):
                    for e1 in sites[i]:
                        for e2 in sites[j]:
                            budget.tick()
                            try:
                                candidate = apply_edits(source, [e1, e2])
                            except OverlappingEdits:
                                continue
                            if passes(candidate):
                                return _accept(candidate, [e1, e2], budget)
    except BudgetExceeded:
        raise NoRepair(
            f"no verified patch before the search budget ({budget.limit}) was exhausted"
        )

    raise NoRepair(f"no verified patch found with up to {max_edits} edit(s)")


def _accept(candidate: str, edits: list[TextEdit], budget: OpBudget) -> Result:
    report = provenance(
        "repair",
        RULE_VERSION,
        status="repaired",
        edits=len(edits),
        patches=[{"line": e.lineno, "col": e.col, "to": e.new_text} for e in edits],
        ops_used=budget.used,
    )
    return Result(candidate, changed=True, report=report)
