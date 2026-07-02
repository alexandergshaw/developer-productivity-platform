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

from .determinism import provenance
from .ir import Intent
from .engines import builder, document, explain, gentest, repair, retrieve, rewrite, scaffold


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
    # Structured file map for project-building intents (path -> content),
    # so UIs can materialize the project instead of parsing the text bundle.
    files: dict | None = None


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

    if op == "sort-imports":
        r = rewrite.sort_imports(_needs_source(intent, source))
        return Outcome(r.source, None, r.changed, r.report)

    if op == "cleanup":
        # The LLM "tidy this file" move: drop unused imports, then sort.
        r1 = rewrite.remove_unused_imports(_needs_source(intent, source))
        r2 = rewrite.sort_imports(r1.source)
        return Outcome(
            r2.source,
            None,
            r1.changed or r2.changed,
            provenance("cleanup", "1", steps=[r1.report, r2.report]),
        )

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

    if op == "new":
        project = builder.build(intent.get("direction") or "")
        return Outcome(
            None,
            builder.render(project),
            False,
            project.report,
            files={f.path: f.content for f in project.files},
        )

    if op == "gentest":
        spec = _spec(intent)
        # When invoked against a file (CNL "generate tests for f where ..."),
        # embed that file's code so the generated tests are self-contained.
        if "source" not in spec and "module" not in spec and source is not None:
            spec["source"] = source
        r = gentest.gentest(spec)
        return Outcome(None, r.source, False, r.report)

    raise UnknownIntent(f"no engine for operation {op!r}")


def run_all(intents: list[Intent], source: str | None = None) -> Outcome:
    """Run a pipeline of intents ("... then ...").

    File edits feed forward: each step sees the previous step's edited source.
    Freestanding outputs (generated code, explanations) accumulate in order.
    """
    if len(intents) == 1:
        return run(intents[0], source)

    current = source
    outputs: list[str] = []
    reports: list[dict] = []
    files: dict = {}
    edited = False
    for intent in intents:
        outcome = run(intent, current)
        reports.append(outcome.report)
        if outcome.new_source is not None:
            current = outcome.new_source
            edited = edited or outcome.changed
        if outcome.output:
            outputs.append(outcome.output)
        if outcome.files:
            files.update(outcome.files)
    return Outcome(
        current if edited else None,
        "\n\n".join(outputs) if outputs else None,
        edited,
        provenance("pipeline", "1", steps=reports),
        files=files or None,
    )
