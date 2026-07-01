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
