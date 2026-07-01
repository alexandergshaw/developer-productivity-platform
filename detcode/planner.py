"""Planner — maps an Intent to the engine that fulfills it.

This is the seam that lets any front-end (examples, spec, controlled natural
language, the web API) drive any engine. It is a pure, deterministic dispatch:
same Intent + source always produces the same Outcome.

Outcomes distinguish two shapes of result:
- ``new_source``: an edit to the file the intent was applied to (codemods,
  repair) — callers can diff/write it.
- ``output``: freestanding generated content (synthesized code, scaffolded
  modules, generated tests, explanations).
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from .ir import Intent
from .engines import document, explain, gentest, repair, retrieve, rewrite, scaffold


class UnknownIntent(Exception):
    """No engine is registered for the requested operation."""


class MissingSource(Exception):
    """The operation needs source code but none was provided."""


@dataclass
class Outcome:
    new_source: str | None
    output: str | None
    changed: bool
    report: dict


def _needs_source(intent: Intent, source: str | None) -> str:
    if source is None:
        raise MissingSource(
            f"operation {intent.operation!r} needs source code (pass --file / source)"
        )
    return source


def _spec(intent: Intent) -> dict:
    return json.loads(intent.get("spec_json") or "{}")


def run(intent: Intent, source: str | None = None) -> Outcome:
    """Execute ``intent``, optionally against ``source``."""
    op = intent.operation

    if op == "rename-local":
        r = rewrite.rename_local(
            _needs_source(intent, source),
            intent.get("func"),
            intent.get("old"),
            intent.get("new"),
        )
        return Outcome(r.source, None, r.changed, r.report)

    if op == "remove-unused-imports":
        r = rewrite.remove_unused_imports(_needs_source(intent, source))
        return Outcome(r.source, None, r.changed, r.report)

    if op == "explain":
        r = explain.explain(_needs_source(intent, source), intent.get("func"))
        return Outcome(None, r.text, False, r.report)

    if op == "document":
        r = document.add_docstrings(_needs_source(intent, source), intent.get("func"))
        return Outcome(r.source, None, r.changed, r.report)

    if op == "repair":
        r = repair.repair(_needs_source(intent, source), _spec(intent))
        return Outcome(r.source, None, r.changed, r.report)

    if op == "synth":
        # Retrieval-first: known functions (loops, recursion) come from the
        # verified corpus; novel ones from enumerative synthesis.
        r = retrieve.write_function(_spec(intent))
        return Outcome(None, r.source, False, r.report)

    if op == "scaffold":
        r = scaffold.scaffold(_spec(intent))
        return Outcome(None, r.source, False, r.report)

    if op == "gentest":
        spec = _spec(intent)
        # When invoked against a file (CNL "generate tests for f where ..."),
        # embed that file's code so the generated tests are self-contained.
        if "source" not in spec and "module" not in spec and source is not None:
            spec["source"] = source
        r = gentest.gentest(spec)
        return Outcome(None, r.source, False, r.report)

    raise UnknownIntent(f"no engine for operation {op!r}")
