"""Plan mode — the spec interview for directions detcode cannot build yet.

The LLM behavior mimicked is "ask clarifying questions before building".
Deterministically: a fixed questionnaire whose answers are *examples*, because
examples are the one currency every engine here consumes. The output is a
plan file; ``detcode new --plan file.json`` builds it (derivable functions
become real code, the rest become stubs with intent tests).
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from ..determinism import provenance
from .. import packs
from .builder import BuildError, _slug, _title, _words

RULE_VERSION = "1"

QUESTIONS = (
    "1. What does {title} take as input? (text, files, numbers, lists?)",
    "2. What should it produce?",
    "3. Name the 1-6 core functions. For each, give 2-3 examples (in -> out) "
    "in the plan file - the examples ARE the spec.",
    "4. Which function should the CLI call first?",
)


@dataclass
class Result:
    plan: dict
    plan_text: str
    questions: str
    report: dict


def make_plan(direction: str, name: str | None = None) -> Result:
    """Produce the interview and a fillable plan for ``direction``."""
    if not isinstance(direction, str) or not direction.strip():
        raise BuildError('give a direction, e.g. detcode plan "a citation formatter"')
    slug = name or _slug(direction)
    if not slug.isidentifier():
        raise BuildError(f"package name {slug!r} is not a valid identifier")
    title = _title(slug)

    notes = [
        "Answer by example: fill functions[].examples. Every function detcode "
        "can derive from its examples becomes working code; the rest become "
        "stubs whose examples ship as expectedFailure tests (executable TODOs).",
        "Then run: detcode new --plan " + slug + ".plan.json",
    ]
    matched = packs.match_all(set(_words(direction)))
    if matched:
        names = ", ".join(p.title for p, _ in matched)
        notes.append(
            f"Note: this direction already matches the {names} pack(s) - "
            f'detcode new "{direction.strip()}" works directly; plan mode is '
            "for going beyond the packs."
        )

    plan = {
        "detcode_plan": 1,
        "direction": direction.strip(),
        "name": slug,
        "notes": notes,
        "functions": [
            {
                "name": "rename_me",
                "description": "what this function does, in one line",
                "examples": [
                    {"in": ["sample input"], "out": "expected output"},
                ],
            }
        ],
    }
    questions = "\n".join(q.format(title=title) for q in QUESTIONS)
    plan_text = json.dumps(plan, indent=2, sort_keys=True) + "\n"
    report = provenance(
        "plan", RULE_VERSION, package=slug, plan_file=f"{slug}.plan.json"
    )
    return Result(plan, plan_text, questions, report)
