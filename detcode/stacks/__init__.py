"""Tech stacks — the chassis a generated project is built on.

A stack decides the language, the web layer, the dependency manifest, and how
the project's tests run. Like domain packs, stacks are deterministic
templates: they are matched against the *direction* by a fixed keyword table
("a todo app in flask"), or named explicitly (``--stack flask`` on the CLI,
``"stack"`` in a service request). The choice and its reason land in the
build decisions.

Python stacks keep the domain packs' core untouched and swap the interface
layer. Non-Python stacks cannot carry the Python domain packs, so they ship
their own skeleton with the domain logic marked TODO — that boundary is
recorded, never papered over.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Stack:
    key: str
    title: str
    keywords: frozenset[str]  # direction words that select this stack
    description: str          # one line, quoted in the build decisions
    language: str             # "python" | "javascript"
    dependencies: tuple       # pyproject [project] dependencies (python stacks)
    web_files: object | None  # callable() -> dict: web layer over the pack CLI
    skeleton: object | None   # callable() -> dict: full project skeleton (non-python)
    web_always: bool          # the stack is inherently a web interface
    web_label: str            # e.g. "a Flask web app", used in the interface decision
    usage: tuple              # README usage lines (__PKG__ placeholder)
    dev: tuple                # README development lines (__PKG__ placeholder)


def registry() -> tuple[Stack, ...]:
    from . import fastapi_stack, flask_stack, node_stack, stdlib_stack

    return (
        stdlib_stack.STACK,  # first entry is the default
        flask_stack.STACK,
        fastapi_stack.STACK,
        node_stack.STACK,
    )


def default() -> Stack:
    return registry()[0]


def get(name: str) -> Stack | None:
    """Stack by key or alias keyword (case-insensitive); None if unknown."""
    needle = (name or "").strip().lower()
    for stack in registry():
        if needle == stack.key or needle in stack.keywords:
            return stack
    return None


def match(words: set) -> list[tuple[Stack, list[str]]]:
    """Every non-default stack whose keywords intersect ``words`` (registry order)."""
    out = []
    for stack in registry()[1:]:
        hits = sorted(stack.keywords & words)
        if hits:
            out.append((stack, hits))
    return out


def all_keywords() -> frozenset[str]:
    """Union of every stack's keywords — stack words never enter a slug."""
    out: set = set()
    for stack in registry():
        out |= stack.keywords
    return frozenset(out)
