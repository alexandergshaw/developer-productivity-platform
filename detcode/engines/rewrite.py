"""Vertical 1 — deterministic refactors / codemods.

Two codemods so far, both operating on Python source:

- ``rename_local``: rename a local variable inside one function.
- ``remove_unused_imports``: drop module-level imports that are never used.

Both follow *correctness-by-refusal*: if a transformation cannot be proven
safe, the engine raises :class:`Unsafe` rather than guessing. A refusal is a
first-class, deterministic outcome — never a wrong edit made silently.
"""
from __future__ import annotations

import ast
import io
import sys
import tokenize
from dataclasses import dataclass

from ..determinism import provenance
from ..sourceedit import TextEdit, apply_edits

RULE_VERSION = "1"

# Nodes that introduce a new scope. Names bound inside these are *not* part of
# the enclosing function's scope, so a local rename must not cross into them.
_SCOPE_NODES = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.Lambda,
    ast.ClassDef,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


class Unsafe(Exception):
    """A transformation could not be proven safe and was refused."""


@dataclass
class Result:
    source: str
    changed: bool
    report: dict


# --------------------------------------------------------------------------- #
# rename_local
# --------------------------------------------------------------------------- #
def _params(fn) -> list[str]:
    a = fn.args
    names: list[str] = []
    for group in (a.posonlyargs, a.args, a.kwonlyargs):
        names.extend(arg.arg for arg in group)
    if a.vararg:
        names.append(a.vararg.arg)
    if a.kwarg:
        names.append(a.kwarg.arg)
    return names


def _own_store_and_nested(fn) -> tuple[set[str], set[str], set[str]]:
    """Partition identifiers used inside ``fn``.

    Returns ``(own_store, nested_ids, own_global_nonlocal)``:

    - ``own_store``: names assigned in ``fn``'s own scope.
    - ``nested_ids``: every identifier appearing inside a nested scope.
    - ``own_global_nonlocal``: names ``fn`` declares ``global``/``nonlocal``.
    """
    own_store: set[str] = set()
    nested_ids: set[str] = set()
    own_gnl: set[str] = set()

    def collect_nested(node) -> None:
        for inner in ast.walk(node):
            if isinstance(inner, ast.Name):
                nested_ids.add(inner.id)
            elif isinstance(inner, ast.arg):
                nested_ids.add(inner.arg)
            elif isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                nested_ids.add(inner.name)

    def visit(node) -> None:
        if isinstance(node, _SCOPE_NODES):
            collect_nested(node)
            return
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            own_store.add(node.id)
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            own_gnl.update(node.names)
        for child in ast.iter_child_nodes(node):
            visit(child)

    for stmt in fn.body:
        visit(stmt)
    return own_store, nested_ids, own_gnl


def rename_local(source: str, func_name: str, old: str, new: str) -> Result:
    """Rename local variable ``old`` to ``new`` inside function ``func_name``.

    Refuses when the rename cannot be guaranteed correct: the function is not
    unique, ``old`` is a parameter or not a local, ``old``/``new`` appears in a
    nested scope, ``new`` would collide, or ``old`` is declared global/nonlocal.
    """
    if not new.isidentifier():
        raise Unsafe(f"{new!r} is not a valid identifier")

    tree = ast.parse(source)
    matches = [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func_name
    ]
    if not matches:
        raise Unsafe(f"no function named {func_name!r} found")
    if len(matches) > 1:
        raise Unsafe(f"{len(matches)} functions named {func_name!r}; specify a unique one")
    fn = matches[0]

    params = set(_params(fn))
    own_store, nested_ids, own_gnl = _own_store_and_nested(fn)

    if old in params:
        raise Unsafe(f"{old!r} is a parameter; rename-local handles local variables only")
    if old in own_gnl:
        raise Unsafe(f"{old!r} is declared global/nonlocal in {func_name!r}")
    if old not in own_store:
        raise Unsafe(f"{old!r} is not a local variable assigned in {func_name!r}")
    if old in nested_ids:
        raise Unsafe(f"{old!r} is also used in a nested scope; rename not guaranteed safe")
    if new in params or new in own_store or new in nested_ids:
        raise Unsafe(f"{new!r} already exists in {func_name!r}; rename would collide")

    edits = [
        TextEdit(n.lineno, n.col_offset, n.end_lineno, n.end_col_offset, new)
        for n in ast.walk(fn)
        if isinstance(n, ast.Name) and n.id == old
    ]
    new_source = apply_edits(source, edits)
    # Safety net: the result must still parse.
    ast.parse(new_source)

    report = provenance(
        "rename_local",
        RULE_VERSION,
        func=func_name,
        renamed_from=old,
        renamed_to=new,
        occurrences=len(edits),
    )
    return Result(new_source, changed=bool(edits), report=report)


# --------------------------------------------------------------------------- #
# remove_unused_imports
# --------------------------------------------------------------------------- #
def _bound_name(alias: ast.alias) -> str:
    """The name an import binds into scope."""
    if alias.asname:
        return alias.asname
    # ``import a.b.c`` binds ``a``; ``from x import y`` binds ``y``.
    return alias.name.split(".", 1)[0]


def _used_names(tree: ast.AST) -> set[str]:
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            # Roots surface as Name(Load); nothing extra needed here.
            pass
    # Names listed in ``__all__`` count as used (they are the module's exports).
    for node in tree.body if isinstance(tree, ast.Module) else []:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        ):
            if isinstance(node.value, (ast.List, ast.Tuple)):
                for elt in node.value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        used.add(elt.value)
    return used


def _line_span_removal(source: str, node: ast.stmt) -> TextEdit:
    """An edit that deletes the whole physical line(s) a statement occupies."""
    lines = source.splitlines(keepends=True)
    end = node.end_lineno
    # Include the trailing newline by deleting up to the start of the next line.
    if end < len(lines):
        return TextEdit(node.lineno, 0, end + 1, 0, "")
    return TextEdit(node.lineno, 0, end, len(lines[end - 1].encode("utf-8")), "")


def remove_unused_imports(source: str) -> Result:
    """Remove module-level imports whose bound names are never used.

    Only touches top-level ``import`` / ``from ... import`` statements that
    occupy their own line(s). ``from __future__`` imports and ``import *`` are
    always kept. ``__all__`` entries count as usages.
    """
    tree = ast.parse(source)
    used = _used_names(tree)

    # Which linenos hold a non-import top-level statement (to detect shared lines).
    import_nodes = [
        n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))
    ]
    other_linenos = {
        n.lineno
        for n in tree.body
        if not isinstance(n, (ast.Import, ast.ImportFrom))
    }

    edits: list[TextEdit] = []
    removed: list[str] = []

    for node in import_nodes:
        if node.lineno in other_linenos:
            continue  # shares a line with other code; leave it alone
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue
        if any(alias.name == "*" for alias in node.names):
            continue

        keep = [a for a in node.names if _bound_name(a) in used]
        drop = [a for a in node.names if _bound_name(a) not in used]
        if not drop:
            continue

        removed.extend(_bound_name(a) for a in drop)
        if not keep:
            edits.append(_line_span_removal(source, node))
        else:
            kept = ast.copy_location(
                type(node)(**_import_kwargs(node, keep)), node
            )
            edits.append(
                TextEdit(
                    node.lineno,
                    node.col_offset,
                    node.end_lineno,
                    node.end_col_offset,
                    ast.unparse(kept),
                )
            )

    new_source = apply_edits(source, edits)
    ast.parse(new_source)

    report = provenance(
        "remove_unused_imports",
        RULE_VERSION,
        removed=sorted(removed),
        count=len(removed),
    )
    return Result(new_source, changed=bool(edits), report=report)


def _import_kwargs(node, names):
    if isinstance(node, ast.Import):
        return {"names": names}
    return {"module": node.module, "names": names, "level": node.level}


# --------------------------------------------------------------------------- #
# add_function
# --------------------------------------------------------------------------- #
def _top_level_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(_bound_name(alias) for alias in node.names)
    return names


def add_function(source: str, func_source: str) -> Result:
    """Append a top-level function to a module.

    Refuses when ``func_source`` is not exactly one function definition or
    when its name would collide with anything already bound at module level.
    Existing content is untouched; the function lands at the end with
    standard two-blank-line separation.
    """
    tree = ast.parse(source)
    try:
        new_tree = ast.parse(func_source)
    except SyntaxError as exc:
        raise Unsafe(f"the function source does not parse: {exc}") from exc
    if len(new_tree.body) != 1 or not isinstance(
        new_tree.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)
    ):
        raise Unsafe("expected exactly one function definition to add")
    name = new_tree.body[0].name
    if name in _top_level_names(tree):
        raise Unsafe(f"{name!r} already exists at module level; refusing to shadow it")

    base = source.rstrip("\n")
    body = func_source.strip("\n")
    new_source = (base + "\n\n\n" + body + "\n") if base else (body + "\n")
    ast.parse(new_source)

    report = provenance("add_function", RULE_VERSION, function=name)
    return Result(new_source, changed=True, report=report)


# --------------------------------------------------------------------------- #
# sort_imports
# --------------------------------------------------------------------------- #
def _import_units(node) -> list[tuple[int, str, str]]:
    """Break an import statement into sortable (group, root, rendered) units.

    Groups: 0 = __future__, 1 = stdlib, 2 = third-party/local absolute,
    3 = relative. ``import a, b`` splits into one unit per module (isort
    style); ``from x import b, a`` gets its names sorted.
    """

    def group_of(root: str, level: int, future: bool) -> int:
        if future:
            return 0
        if level > 0:
            return 3
        return 1 if root in sys.stdlib_module_names else 2

    if isinstance(node, ast.Import):
        units = []
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            rendered = ast.unparse(ast.Import(names=[alias]))
            units.append((group_of(root, 0, False), root, rendered))
        return units

    future = node.module == "__future__" and node.level == 0
    if any(alias.name == "*" for alias in node.names):
        names = list(node.names)  # never reorder around a star import
    else:
        names = sorted(node.names, key=lambda a: (a.name.lower(), a.name, a.asname or ""))
    rendered = ast.unparse(ast.ImportFrom(module=node.module, names=names, level=node.level))
    root = "" if node.level else (node.module or "").split(".", 1)[0]
    return [(group_of(root, node.level, future), root, rendered)]


def sort_imports(source: str) -> Result:
    """Canonically order the leading import block (isort-lite).

    Groups — __future__, standard library, third-party/local, relative —
    separated by a blank line, alphabetical within each. Only the contiguous
    import block at the top of the module (after the docstring) is touched.
    Refuses if that block contains comments, which reordering would misplace.
    """
    tree = ast.parse(source)
    body = tree.body
    start_index = 0
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        start_index = 1

    block: list = []
    for node in body[start_index:]:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            block.append(node)
        else:
            break
    if len(block) < 2:
        return Result(source, False, provenance("sort_imports", RULE_VERSION, statements=0))

    first, last = block[0], block[-1]
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT and first.lineno <= tok.start[0] <= last.end_lineno:
            raise Unsafe(
                "the import block contains comments; reordering would misplace them"
            )

    units = sorted(
        {unit for node in block for unit in _import_units(node)},
        key=lambda u: (u[0], u[1].lower(), u[2]),
    )
    grouped: list[str] = []
    previous_group: int | None = None
    for group, _root, rendered in units:
        if previous_group is not None and group != previous_group:
            grouped.append("")
        grouped.append(rendered)
        previous_group = group
    new_block = "\n".join(grouped)

    lines = source.splitlines(keepends=True)
    last_line = lines[last.end_lineno - 1]
    edit = TextEdit(
        first.lineno, 0, last.end_lineno, len(last_line.rstrip("\r\n").encode("utf-8")), new_block
    )
    new_source = apply_edits(source, [edit])
    ast.parse(new_source)

    report = provenance("sort_imports", RULE_VERSION, statements=len(units))
    return Result(new_source, changed=new_source != source, report=report)
