"""Controlled-natural-language front-end (intent-input mode 3).

A *controlled* natural language: a small, fixed grammar of English-like commands
parsed deterministically into an :class:`~detcode.ir.Intent`. It is intentionally
restricted — that is what makes the mapping unambiguous and reproducible. An
unrecognized command is refused with the list of supported forms, never guessed.

Supported commands::

    rename local <old> to <new> in <func>
    remove unused imports
"""
from __future__ import annotations

import re

from .ir import Intent

GRAMMAR = (
    "rename local <old> to <new> in <func>",
    "remove unused imports",
)

# Patterns are tried in this fixed order.
_PATTERNS = (
    (
        re.compile(
            r"^rename\s+local\s+(?P<old>\w+)\s+to\s+(?P<new>\w+)\s+in\s+(?P<func>\w+)$",
            re.IGNORECASE,
        ),
        lambda m: Intent.of(
            "rename-local", old=m["old"], new=m["new"], func=m["func"]
        ),
    ),
    (
        re.compile(r"^remove\s+unused\s+imports$", re.IGNORECASE),
        lambda m: Intent.of("remove-unused-imports"),
    ),
)


class CNLError(Exception):
    """The command did not match the controlled grammar."""


def parse(text: str) -> Intent:
    command = " ".join(text.strip().split())  # collapse whitespace
    for pattern, build in _PATTERNS:
        match = pattern.match(command)
        if match:
            return build(match)
    supported = "\n  ".join(GRAMMAR)
    raise CNLError(f"could not parse {text!r}. Supported commands:\n  {supported}")
