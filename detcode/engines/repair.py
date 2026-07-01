"""Vertical 4 — deterministic bug-fix / repair.

Given a buggy function and a set of input/output tests, search a bounded space
of small edits for a patch that makes every test pass.

Design:

- **Fault check**: run the function on the tests. If all pass, there is nothing
  to repair (a deterministic no-op).
- **Mutation space**: enumerate token-level edits *within the target function* —
  swap an operator for another in its group (arithmetic / comparison / boolean)
  or nudge an integer constant (n-1, n+1, 0, 1). Over-generation is safe here:
  every candidate must pass the full test suite to be accepted, so wrong
  mutations are simply filtered out by the oracle.
- **Search**: try all 1-edit patches in a fixed order, then (optionally) all
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
import tokenize
from dataclasses import dataclass

from ..determinism import BudgetExceeded, OpBudget, provenance
from ..sourceedit import OverlappingEdits, TextEdit, apply_edits
from ..verify import parses

RULE_VERSION = "1"
DEFAULT_BUDGET = 200_000

# Fixed replacement groups — part of the determinism contract.
_ARITH = ("+", "-", "*", "//", "%")
_COMPARE = ("<", "<=", ">", ">=", "==", "!=")


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


def _replacements_for(tok) -> list[str]:
    s = tok.string
    if tok.type == tokenize.OP:
        if s in _ARITH:
            return [x for x in _ARITH if x != s]
        if s in _COMPARE:
            return [x for x in _COMPARE if x != s]
    elif tok.type == tokenize.NAME:
        if s == "and":
            return ["or"]
        if s == "or":
            return ["and"]
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
    sites: list[list[TextEdit]] = []
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    for tok in tokens:
        srow = tok.start[0]
        if srow < fn.lineno or srow > (fn.end_lineno or fn.lineno):
            continue
        reps = _replacements_for(tok)
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

    sites = _mutation_sites(source, fn)

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
