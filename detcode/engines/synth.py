"""Vertical 3 — deterministic example-driven program synthesis.

Given input/output examples (intent-input mode: examples + inferred types),
search a bounded, typed DSL for a function consistent with every example.

The search is classic bottom-up enumerative synthesis:

- Start from terminals (the input variables and a small fixed set of constants).
- Grow expressions one depth-layer at a time by applying typed components.
- Prune with *observational equivalence*: two expressions that produce the same
  outputs on the example inputs are interchangeable, so keep only the first.
- Bound the work with an operation *count* (never wall-clock), so the same
  inputs always explore the same space and yield the same program.

The first program found in this fixed traversal is returned — deterministic by
construction. If none is found within the depth/budget limits, synthesis is
refused (:class:`NoSolution`) rather than guessing.
"""
from __future__ import annotations

import ast
import itertools
from dataclasses import dataclass

from ..determinism import BudgetExceeded, OpBudget, provenance

RULE_VERSION = "1"
DEFAULT_MAX_DEPTH = 4
DEFAULT_BUDGET = 200_000


class NoSolution(Exception):
    """No consistent program was found within the depth/budget limits."""


class SpecError(Exception):
    """The examples spec was malformed or used unsupported types."""


@dataclass(frozen=True)
class Component:
    name: str
    arg_types: tuple[str, ...]
    ret_type: str
    fn: object  # callable
    render: object  # callable(*arg_strs) -> str


# Fixed component order — part of the determinism contract.
COMPONENTS: tuple[Component, ...] = (
    Component("add", ("int", "int"), "int", lambda a, b: a + b, lambda a, b: f"({a} + {b})"),
    Component("sub", ("int", "int"), "int", lambda a, b: a - b, lambda a, b: f"({a} - {b})"),
    Component("mul", ("int", "int"), "int", lambda a, b: a * b, lambda a, b: f"({a} * {b})"),
    Component("concat", ("str", "str"), "str", lambda a, b: a + b, lambda a, b: f"({a} + {b})"),
    Component("upper", ("str",), "str", lambda a: a.upper(), lambda a: f"{a}.upper()"),
    Component("lower", ("str",), "str", lambda a: a.lower(), lambda a: f"{a}.lower()"),
    Component("length", ("str",), "int", lambda a: len(a), lambda a: f"len({a})"),
    Component("to_str", ("int",), "str", lambda a: str(a), lambda a: f"str({a})"),
)

# Fixed constant pool per type.
_INT_CONSTS = (0, 1, 2)
_STR_CONSTS = (" ",)


@dataclass(frozen=True)
class Cand:
    type: str
    render: str
    values: tuple
    depth: int


@dataclass
class Result:
    source: str
    report: dict


def _type_of(value) -> str:
    if isinstance(value, bool):
        return "bool"  # unsupported in v1; surfaces as a clear refusal
    if isinstance(value, int):
        return "int"
    if isinstance(value, str):
        return "str"
    return type(value).__name__


def _parse_examples(spec: dict) -> tuple[list[list], list, list[str], str]:
    if not isinstance(spec, dict) or "examples" not in spec:
        raise SpecError('spec must be an object with an "examples" list')
    examples = spec["examples"]
    if not isinstance(examples, list) or not examples:
        raise SpecError("examples must be a non-empty list")

    inputs: list[list] = []
    outputs: list = []
    arity = None
    for i, ex in enumerate(examples):
        if not isinstance(ex, dict) or "in" not in ex or "out" not in ex:
            raise SpecError(f"example {i} must have 'in' and 'out'")
        args = ex["in"]
        if not isinstance(args, list):
            raise SpecError(f"example {i} 'in' must be a list")
        if arity is None:
            arity = len(args)
        elif len(args) != arity:
            raise SpecError("all examples must have the same number of inputs")
        inputs.append(args)
        outputs.append(ex["out"])

    # Infer and check per-position input types and the output type.
    param_types: list[str] = []
    for pos in range(arity):
        types = {_type_of(row[pos]) for row in inputs}
        if len(types) != 1:
            raise SpecError(f"input {pos} has inconsistent types across examples")
        t = types.pop()
        if t not in ("int", "str"):
            raise SpecError(f"input {pos} type {t!r} is unsupported (int/str only)")
        param_types.append(t)

    out_types = {_type_of(o) for o in outputs}
    if len(out_types) != 1:
        raise SpecError("output types are inconsistent across examples")
    out_type = out_types.pop()
    if out_type not in ("int", "str"):
        raise SpecError(f"output type {out_type!r} is unsupported (int/str only)")
    return inputs, outputs, param_types, out_type


def synthesize(spec: dict) -> Result:
    inputs, outputs, param_types, out_type = _parse_examples(spec)
    max_depth = int(spec.get("max_depth", DEFAULT_MAX_DEPTH))
    budget = OpBudget(int(spec.get("budget", DEFAULT_BUDGET)))
    n = len(inputs)
    arity = len(param_types)
    param_names = ["x"] if arity == 1 else [f"x{i}" for i in range(arity)]
    target = tuple(outputs)

    by_type: dict[str, list[Cand]] = {}
    seen: set[tuple[str, tuple]] = set()

    def add(cand: Cand) -> Cand | None:
        key = (cand.type, cand.values)
        if key in seen:
            return None
        seen.add(key)
        by_type.setdefault(cand.type, []).append(cand)
        if cand.type == out_type and cand.values == target:
            return cand
        return None

    # Terminals: input variables + constants.
    for pos in range(arity):
        hit = add(
            Cand(param_types[pos], param_names[pos], tuple(row[pos] for row in inputs), 0)
        )
        if hit:
            return _finish(hit, param_names, spec, budget)
    for c in _INT_CONSTS:
        hit = add(Cand("int", repr(c), (c,) * n, 0))
        if hit:
            return _finish(hit, param_names, spec, budget)
    for c in _STR_CONSTS:
        hit = add(Cand("str", repr(c), (c,) * n, 0))
        if hit:
            return _finish(hit, param_names, spec, budget)

    try:
        for depth in range(1, max_depth + 1):
            snapshot = {t: list(cands) for t, cands in by_type.items()}
            new_cands: list[Cand] = []
            for comp in COMPONENTS:
                arg_lists = [snapshot.get(t, []) for t in comp.arg_types]
                if not all(arg_lists):
                    continue
                for combo in itertools.product(*arg_lists):
                    if max(c.depth for c in combo) != depth - 1:
                        continue  # keep layers distinct; avoids re-deriving old exprs
                    budget.tick()
                    try:
                        values = tuple(
                            comp.fn(*[c.values[j] for c in combo]) for j in range(n)
                        )
                    except Exception:
                        continue
                    render = comp.render(*[c.render for c in combo])
                    cand = Cand(comp.ret_type, render, values, depth)
                    key = (cand.type, cand.values)
                    if key in seen:
                        continue
                    new_cands.append(cand)
                    if cand.type == out_type and cand.values == target:
                        return _finish(cand, param_names, spec, budget)
            for cand in new_cands:
                add(cand)
            if not new_cands:
                break
    except BudgetExceeded:
        raise NoSolution(
            f"no program found before the search budget ({budget.limit}) was exhausted"
        )

    raise NoSolution(f"no program found within depth {max_depth}")


def _finish(cand: Cand, param_names, spec, budget: OpBudget) -> Result:
    name = spec.get("name", "f")
    if not isinstance(name, str) or not name.isidentifier():
        raise SpecError(f"function name {name!r} is not a valid identifier")
    source = f"def {name}({', '.join(param_names)}):\n    return {cand.render}\n"
    ast.parse(source)
    _verify(source, name, spec)
    report = provenance(
        "synthesize",
        RULE_VERSION,
        expr=cand.render,
        depth=cand.depth,
        ops_used=budget.used,
    )
    return Result(source, report)


def _verify(source: str, name: str, spec: dict) -> None:
    """Defense in depth: compile the program and re-run every example."""
    namespace: dict = {"__builtins__": {"str": str, "len": len}}
    exec(compile(source, "<synth>", "exec"), namespace)
    fn = namespace[name]
    for ex in spec["examples"]:
        got = fn(*ex["in"])
        if got != ex["out"]:
            raise NoSolution(
                f"synthesized program failed verification on {ex['in']!r}: "
                f"got {got!r}, expected {ex['out']!r}"
            )
