"""Planner — maps an Intent to the engine that fulfills it.

This is the seam that lets any front-end (examples, spec, controlled natural
language) drive any engine. It is a pure, deterministic dispatch: same Intent +
source always routes to the same engine call.
"""
from __future__ import annotations

from .ir import Intent
from .engines import rewrite


class UnknownIntent(Exception):
    """No engine is registered for the requested operation."""


def run(intent: Intent, source: str) -> rewrite.Result:
    """Execute ``intent`` against ``source`` (for source-editing operations)."""
    if intent.operation == "rename-local":
        return rewrite.rename_local(
            source, intent.get("func"), intent.get("old"), intent.get("new")
        )
    if intent.operation == "remove-unused-imports":
        return rewrite.remove_unused_imports(source)
    raise UnknownIntent(f"no engine for operation {intent.operation!r}")
