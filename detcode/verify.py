"""Deterministic verification helpers.

Cheap, hermetic checks that gate an engine's output. For vertical 1 this is a
parse check plus an idempotency check (running a codemod twice should be a
no-op on the second pass). Later verticals extend this with the sandboxed
test/typecheck loop.
"""
from __future__ import annotations

import ast


def parses(source: str) -> bool:
    """True if ``source`` is syntactically valid Python."""
    try:
        ast.parse(source)
    except SyntaxError:
        return False
    return True


def assert_parses(source: str) -> None:
    ast.parse(source)  # raises SyntaxError with location if invalid
