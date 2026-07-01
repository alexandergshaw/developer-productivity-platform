"""Deterministic example-driven program synthesis.

Given input/output examples (intent-input mode: examples + inferred types),
search a bounded, typed DSL for a function consistent with every example.

The search is classic bottom-up enumerative synthesis:

- Start from terminals: the input variables, a fixed constant pool, and
  constants *mined deterministically from the examples themselves* (small ints
  appearing in the data, short output strings, substrings common to all string
  outputs/inputs — the FlashFill/PROSE trick).
- Grow expressions one depth-layer at a time by applying typed components.
  The DSL covers ints, strings, booleans, and lists, including conditionals
  (``a if p else b``), so branching programs are reachable.
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

RULE_VERSION = "2"
DEFAULT_MAX_DEPTH = 4
DEFAULT_BUDGET = 500_000

SUPPORTED_TYPES = ("int", "str", "bool", "list[int]", "list[str]")


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


def _safe_repeat(a: str, n: int) -> str:
    if not 0 <= n <= 16:
        raise ValueError("repeat count out of bounds")
    return a * n


# Fixed component order — part of the determinism contract.
COMPONENTS: tuple[Component, ...] = (
    # int -> int
    Component("add", ("int", "int"), "int", lambda a, b: a + b, lambda a, b: f"({a} + {b})"),
    Component("sub", ("int", "int"), "int", lambda a, b: a - b, lambda a, b: f"({a} - {b})"),
    Component("mul", ("int", "int"), "int", lambda a, b: a * b, lambda a, b: f"({a} * {b})"),
    Component("floordiv", ("int", "int"), "int", lambda a, b: a // b, lambda a, b: f"({a} // {b})"),
    Component("mod", ("int", "int"), "int", lambda a, b: a % b, lambda a, b: f"({a} % {b})"),
    Component("neg", ("int",), "int", lambda a: -a, lambda a: f"(-{a})"),
    Component("abs", ("int",), "int", lambda a: abs(a), lambda a: f"abs({a})"),
    Component("max2", ("int", "int"), "int", lambda a, b: max(a, b), lambda a, b: f"max({a}, {b})"),
    Component("min2", ("int", "int"), "int", lambda a, b: min(a, b), lambda a, b: f"min({a}, {b})"),
    # str -> str
    Component("concat", ("str", "str"), "str", lambda a, b: a + b, lambda a, b: f"({a} + {b})"),
    Component("upper", ("str",), "str", lambda a: a.upper(), lambda a: f"{a}.upper()"),
    Component("lower", ("str",), "str", lambda a: a.lower(), lambda a: f"{a}.lower()"),
    Component("strip", ("str",), "str", lambda a: a.strip(), lambda a: f"{a}.strip()"),
    Component("title", ("str",), "str", lambda a: a.title(), lambda a: f"{a}.title()"),
    Component("take", ("str", "int"), "str", lambda a, n: a[:n], lambda a, n: f"{a}[:{n}]"),
    Component("drop", ("str", "int"), "str", lambda a, n: a[n:], lambda a, n: f"{a}[{n}:]"),
    Component("char_at", ("str", "int"), "str", lambda a, n: a[n], lambda a, n: f"{a}[{n}]"),
    Component("repeat", ("str", "int"), "str", _safe_repeat, lambda a, n: f"({a} * {n})"),
    # bridges
    Component("length", ("str",), "int", lambda a: len(a), lambda a: f"len({a})"),
    Component("to_str", ("int",), "str", lambda a: str(a), lambda a: f"str({a})"),
    Component("split", ("str", "str"), "list[str]", lambda a, b: a.split(b), lambda a, b: f"{a}.split({b})"),
    Component("join", ("str", "list[str]"), "str", lambda a, b: a.join(b), lambda a, b: f"{a}.join({b})"),
    # lists
    Component("sum_list", ("list[int]",), "int", lambda a: sum(a), lambda a: f"sum({a})"),
    Component("len_list_int", ("list[int]",), "int", lambda a: len(a), lambda a: f"len({a})"),
    Component("len_list_str", ("list[str]",), "int", lambda a: len(a), lambda a: f"len({a})"),
    Component("max_list", ("list[int]",), "int", lambda a: max(a), lambda a: f"max({a})"),
    Component("min_list", ("list[int]",), "int", lambda a: min(a), lambda a: f"min({a})"),
    Component("sorted_int", ("list[int]",), "list[int]", lambda a: sorted(a), lambda a: f"sorted({a})"),
    Component("sorted_str", ("list[str]",), "list[str]", lambda a: sorted(a), lambda a: f"sorted({a})"),
    Component("reversed_int", ("list[int]",), "list[int]", lambda a: list(reversed(a)), lambda a: f"list(reversed({a}))"),
    Component("reversed_str", ("list[str]",), "list[str]", lambda a: list(reversed(a)), lambda a: f"list(reversed({a}))"),
    Component("first_int", ("list[int]",), "int", lambda a: a[0], lambda a: f"{a}[0]"),
    Component("first_str", ("list[str]",), "str", lambda a: a[0], lambda a: f"{a}[0]"),
    Component("last_int", ("list[int]",), "int", lambda a: a[-1], lambda a: f"{a}[-1]"),
    Component("last_str", ("list[str]",), "str", lambda a: a[-1], lambda a: f"{a}[-1]"),
    # predicates
    Component("eq_int", ("int", "int"), "bool", lambda a, b: a == b, lambda a, b: f"({a} == {b})"),
    Component("lt", ("int", "int"), "bool", lambda a, b: a < b, lambda a, b: f"({a} < {b})"),
    Component("le", ("int", "int"), "bool", lambda a, b: a <= b, lambda a, b: f"({a} <= {b})"),
    Component("eq_str", ("str", "str"), "bool", lambda a, b: a == b, lambda a, b: f"({a} == {b})"),
    Component("startswith", ("str", "str"), "bool", lambda a, b: a.startswith(b), lambda a, b: f"{a}.startswith({b})"),
    Component("endswith", ("str", "str"), "bool", lambda a, b: a.endswith(b), lambda a, b: f"{a}.endswith({b})"),
    Component("contains", ("str", "str"), "bool", lambda a, b: b in a, lambda a, b: f"({b} in {a})"),
    Component("not", ("bool",), "bool", lambda a: not a, lambda a: f"(not {a})"),
    # conditionals
    Component("ite_int", ("bool", "int", "int"), "int", lambda c, a, b: a if c else b, lambda c, a, b: f"({a} if {c} else {b})"),
    Component("ite_str", ("bool", "str", "str"), "str", lambda c, a, b: a if c else b, lambda c, a, b: f"({a} if {c} else {b})"),
)

# Base constant pools; the miner extends them from the examples.
_INT_CONSTS = (0, 1, 2)
_STR_CONSTS = (" ",)
_MAX_MINED_INTS = 6
_MAX_MINED_STRS = 8


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


def _freeze(value):
    """Lists become tuples so candidate value-vectors are hashable."""
    if isinstance(value, list):
        return tuple(value)
    return value


def _type_of(value) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        elem_types = {_type_of(v) for v in value}
        if not elem_types:
            return "list[?]"
        if elem_types <= {"int"}:
            return "list[int]"
        if elem_types <= {"str"}:
            return "list[str]"
        return "list[mixed]"
    return type(value).__name__


def _unify(types: set[str], where: str) -> str:
    """Resolve the observed types at one position; 'list[?]' (empty list)
    unifies with any concrete list type."""
    concrete = {t for t in types if t != "list[?]"}
    if not concrete:
        raise SpecError(f"{where}: cannot infer element type (all example lists are empty)")
    if len(concrete) != 1:
        raise SpecError(f"{where} has inconsistent types across examples: {sorted(concrete)}")
    t = next(iter(concrete))
    if t not in SUPPORTED_TYPES:
        raise SpecError(
            f"{where} type {t!r} is unsupported (supported: {', '.join(SUPPORTED_TYPES)})"
        )
    if "list[?]" in types and not t.startswith("list["):
        raise SpecError(f"{where} mixes list and non-list values")
    return t


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
    if arity == 0:
        raise SpecError("examples must take at least one input")

    param_types = [
        _unify({_type_of(row[pos]) for row in inputs}, f"input {pos}")
        for pos in range(arity)
    ]
    out_type = _unify({_type_of(o) for o in outputs}, "output")
    return inputs, outputs, param_types, out_type


def _common_substrings(values: list[str], max_len: int) -> list[str]:
    """Substrings (length 1..max_len) of the first value present in all values."""
    if not values:
        return []
    first, rest = values[0], values[1:]
    found: list[str] = []
    for size in range(1, max_len + 1):
        for start in range(0, len(first) - size + 1):
            sub = first[start : start + size]
            if sub not in found and all(sub in v for v in rest):
                found.append(sub)
    return found


def _mine_constants(
    inputs: list[list], outputs: list, param_types: list[str], out_type: str, spec: dict
) -> tuple[list[int], list[str]]:
    """Deterministically extend the constant pools from the examples.

    - explicit ``spec["constants"]`` entries (routed by type)
    - small ints appearing anywhere in the data
    - whole output strings when short and few (branch values for conditionals)
    - short substrings common to all string outputs / all values of a string
      input (separators for split/join tasks)
    """
    int_pool: list[int] = list(_INT_CONSTS)
    str_pool: list[str] = list(_STR_CONSTS)

    def add_int(n: int) -> None:
        if n not in int_pool:
            int_pool.append(n)

    def add_str(s: str) -> None:
        if s not in str_pool:
            str_pool.append(s)

    for const in spec.get("constants") or []:
        if isinstance(const, bool):
            continue
        if isinstance(const, int):
            add_int(const)
        elif isinstance(const, str):
            add_str(const)
        else:
            raise SpecError(f"unsupported constant {const!r} (int/str only)")

    flat: list = [v for row in inputs for v in row] + list(outputs)
    mined_ints = sorted(
        {v for v in flat if isinstance(v, int) and not isinstance(v, bool) and abs(v) <= 100},
        key=lambda n: (abs(n), n),
    )
    for n in mined_ints[:_MAX_MINED_INTS]:
        add_int(n)

    mined_strs: list[str] = []
    if out_type == "str":
        distinct_outs = sorted(set(outputs))
        if len(distinct_outs) <= 4:
            mined_strs.extend(s for s in distinct_outs if len(s) <= 8)
        mined_strs.extend(_common_substrings([str(o) for o in outputs], 2))
    for pos, ptype in enumerate(param_types):
        if ptype == "str":
            mined_strs.extend(_common_substrings([row[pos] for row in inputs], 1))
    seen_s: set[str] = set()
    ordered = [s for s in mined_strs if s and not (s in seen_s or seen_s.add(s))]
    ordered.sort(key=lambda s: (len(s), s))
    for s in ordered[:_MAX_MINED_STRS]:
        add_str(s)

    return int_pool, str_pool


def synthesize(spec: dict) -> Result:
    inputs, outputs, param_types, out_type = _parse_examples(spec)
    max_depth = int(spec.get("max_depth", DEFAULT_MAX_DEPTH))
    budget = OpBudget(int(spec.get("budget", DEFAULT_BUDGET)))
    n = len(inputs)
    arity = len(param_types)
    param_names = ["x"] if arity == 1 else [f"x{i}" for i in range(arity)]
    target = tuple(_freeze(o) for o in outputs)
    int_pool, str_pool = _mine_constants(inputs, outputs, param_types, out_type, spec)

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

    # Terminals: input variables, then constants, in fixed order.
    terminals: list[Cand] = [
        Cand(param_types[pos], param_names[pos], tuple(_freeze(row[pos]) for row in inputs), 0)
        for pos in range(arity)
    ]
    terminals.extend(Cand("int", repr(c), (c,) * n, 0) for c in int_pool)
    terminals.extend(Cand("str", repr(c), (c,) * n, 0) for c in str_pool)
    terminals.extend(Cand("bool", repr(c), (c,) * n, 0) for c in (True, False))
    for term in terminals:
        hit = add(term)
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
                            _freeze(comp.fn(*[c.values[j] for c in combo]))
                            for j in range(n)
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
    allowed = {
        "str": str, "len": len, "sorted": sorted, "sum": sum,
        "abs": abs, "max": max, "min": min, "list": list, "reversed": reversed,
    }
    namespace: dict = {"__builtins__": allowed}
    exec(compile(source, "<synth>", "exec"), namespace)
    fn = namespace[name]
    for ex in spec["examples"]:
        got = fn(*ex["in"])
        if got != ex["out"]:
            raise NoSolution(
                f"synthesized program failed verification on {ex['in']!r}: "
                f"got {got!r}, expected {ex['out']!r}"
            )
