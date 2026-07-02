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

# Verbs the suggester recognizes, plus agent-noun forms ("formatter" ->
# format). Fixed tables: suggestions are mined, never guessed.
_VERBS = frozenset(
    "parse format convert extract count sort filter merge validate score rank "
    "generate compute check split join load render clean group match find "
    "tokenize summarize compare grade schedule".split()
)
_AGENT_NOUNS = {
    "formatter": "format", "parser": "parse", "tracker": "track",
    "counter": "count", "generator": "generate", "analyzer": "analyze",
    "checker": "check", "converter": "convert", "extractor": "extract",
    "validator": "validate", "scorer": "score", "sorter": "sort",
    "merger": "merge", "renderer": "render", "tokenizer": "tokenize",
    "grader": "grade", "scheduler": "schedule", "matcher": "match",
    "summarizer": "summarize", "comparer": "compare", "grouper": "group",
}


def _verb_of(word: str) -> str | None:
    if word in _AGENT_NOUNS:
        return _AGENT_NOUNS[word]
    if word in _VERBS:
        return word
    if word.endswith("s") and word[:-1] in _VERBS:
        return word[:-1]
    if word.endswith("ing") and word[:-3] in _VERBS:
        return word[:-3]
    return None


def _is_object(word: str) -> bool:
    from .builder import _DIRECTION_NOISE

    return (
        word not in _DIRECTION_NOISE
        and word not in _VERBS
        and word not in _AGENT_NOUNS
        and _verb_of(word) is None
        and word.isidentifier()
    )


def suggest_functions(direction: str) -> list[dict]:
    """Function names mined from the direction's verbs, in appearance order.

    Agent nouns take the preceding object ("citation formatter" ->
    format_citation); plain verbs take the following one ("parses bibtex" ->
    parse_bibtex). Empty examples: build-from-plan stubs them until filled.
    """
    words = _words(direction)
    suggestions: list[dict] = []
    seen: set[str] = set()
    for i, word in enumerate(words):
        verb = _verb_of(word)
        if verb is None:
            continue
        candidates = (
            range(i - 1, -1, -1) if word in _AGENT_NOUNS else range(i + 1, len(words))
        )
        obj = next((words[j] for j in candidates if _is_object(words[j])), None)
        name = f"{verb}_{obj}" if obj else verb
        if name in seen or not name.isidentifier():
            continue
        seen.add(name)
        suggestions.append(
            {
                "name": name,
                "description": f"suggested from {word!r} in the direction - "
                "fill 2-3 examples, or delete",
                "examples": [],
            }
        )
    return suggestions[:6]


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

    suggested = suggest_functions(direction)
    if suggested:
        notes.append(
            f"detcode suggested {len(suggested)} function name(s) from the "
            "direction's verbs - rename freely; only the examples matter."
        )
    functions = suggested or [
        {
            "name": "rename_me",
            "description": "what this function does, in one line",
            "examples": [
                {"in": ["sample input"], "out": "expected output"},
            ],
        }
    ]
    plan = {
        "detcode_plan": 1,
        "direction": direction.strip(),
        "name": slug,
        "notes": notes,
        "functions": functions,
    }
    questions = "\n".join(q.format(title=title) for q in QUESTIONS)
    plan_text = json.dumps(plan, indent=2, sort_keys=True) + "\n"
    report = provenance(
        "plan", RULE_VERSION, package=slug, plan_file=f"{slug}.plan.json"
    )
    return Result(plan, plan_text, questions, report)
