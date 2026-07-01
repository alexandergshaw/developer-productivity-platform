"""Controlled-natural-language front-end (intent-input mode 3).

A *controlled* natural language: a small, fixed grammar of English-like commands
parsed deterministically into an :class:`~detcode.ir.Intent`. It is intentionally
restricted — that is what makes the mapping unambiguous and reproducible. An
unrecognized command is refused with the closest supported form (deterministic
edit distance) and the full grammar, never guessed.

Supported commands::

    rename local <old> to <new> in <func>
    remove unused imports
    explain [<func>]
    add a docstring to <func> / document [<func>]
    fix <func> so that <func>(args) == value [and ...]
    write a function <name> where <name>(args) == value [and ...]
    generate tests for <func> where <func>(args) == value [and ...]

Three naturalness layers sit in front of the grammar, all fixed tables (no
statistics, no guessing):

- **fillers** are stripped: "please", "can you", trailing "thanks", ...
- **synonyms** normalize to the canonical verb: make/create/build a function →
  write a function; debug/repair → fix; delete/drop unused imports → remove;
  "what does f do?" → explain f
- **chaining**: commands joined by "then" run as a pipeline
  ("remove unused imports then rename local total to acc in compute").
  The split is quote-aware, so a "then" inside a string literal never splits.

Conditions are real Python expressions (``area(2, 3) == 6 and area(1, 5) == 5``)
parsed with ``ast`` — arguments and expected values must be literals.
"""
from __future__ import annotations

import ast
import re

from .determinism import canonical_json
from .ir import Intent

GRAMMAR = (
    "rename local <old> to <new> in <func>",
    "remove unused imports",
    "explain <func>",
    "explain",
    "add a docstring to <func>",
    "document <func>",
    "document",
    "fix <func> so that <func>(args) == value [and ...]",
    "write a function <name> where <name>(args) == value [and ...]",
    "generate tests for <func> where <func>(args) == value [and ...]",
)


class CNLError(Exception):
    """The command did not match the controlled grammar."""


def _literal(node: ast.expr, what: str) -> object:
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError) as exc:
        raise CNLError(f"{what} must be a literal (number, string, list, ...)") from exc


def _conditions(text: str, func: str) -> list[dict]:
    """Parse ``f(1, 2) == 3 and f(0, 0) == 0`` into detcode examples."""
    try:
        node = ast.parse(text.strip(), mode="eval").body
    except SyntaxError as exc:
        raise CNLError(f"could not parse conditions {text!r}: {exc.msg}") from exc

    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
        clauses = node.values
    else:
        clauses = [node]

    examples: list[dict] = []
    for clause in clauses:
        if (
            not isinstance(clause, ast.Compare)
            or len(clause.ops) != 1
            or not isinstance(clause.ops[0], ast.Eq)
        ):
            raise CNLError(
                f"each condition must look like {func}(...) == value, got "
                f"{ast.unparse(clause)!r}"
            )
        call = clause.left
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
            raise CNLError(f"left side of == must be a call to {func}(...)")
        if call.func.id != func:
            raise CNLError(
                f"condition calls {call.func.id!r} but the command names {func!r}"
            )
        if call.keywords:
            raise CNLError("keyword arguments are not supported in conditions")
        args = [_literal(a, f"argument of {func}") for a in call.args]
        expected = _literal(clause.comparators[0], "expected value")
        examples.append({"in": args, "out": expected})
    return examples


def _spec_intent(operation: str, spec: dict) -> Intent:
    # Specs are carried as canonical JSON so Intents stay hashable and canonical.
    return Intent.of(operation, spec_json=canonical_json(spec))


# Patterns are tried in this fixed order. Keywords are case-insensitive;
# captured identifiers and conditions keep their original case.
_PATTERNS = (
    (
        re.compile(
            r"^rename\s+local\s+(?P<old>\w+)\s+to\s+(?P<new>\w+)\s+in\s+(?P<func>\w+)$",
            re.IGNORECASE,
        ),
        lambda m: Intent.of("rename-local", old=m["old"], new=m["new"], func=m["func"]),
    ),
    (
        re.compile(r"^remove\s+unused\s+imports$", re.IGNORECASE),
        lambda m: Intent.of("remove-unused-imports"),
    ),
    (
        re.compile(r"^explain\s+(?P<func>\w+)$", re.IGNORECASE),
        lambda m: Intent.of("explain", func=m["func"]),
    ),
    (
        re.compile(r"^explain$", re.IGNORECASE),
        lambda m: Intent.of("explain"),
    ),
    (
        re.compile(
            r"^(?:add\s+a\s+docstring\s+to|document)\s+(?P<func>\w+)$", re.IGNORECASE
        ),
        lambda m: Intent.of("document", func=m["func"]),
    ),
    (
        re.compile(r"^document$", re.IGNORECASE),
        lambda m: Intent.of("document"),
    ),
    (
        re.compile(r"^fix\s+(?P<func>\w+)\s+so\s+that\s+(?P<cond>.+)$", re.IGNORECASE),
        lambda m: _spec_intent(
            "repair",
            {"function": m["func"], "examples": _conditions(m["cond"], m["func"])},
        ),
    ),
    (
        re.compile(
            r"^write\s+a\s+function\s+(?P<name>\w+)\s+where\s+(?P<cond>.+)$",
            re.IGNORECASE,
        ),
        lambda m: _spec_intent(
            "synth",
            {"name": m["name"], "examples": _conditions(m["cond"], m["name"])},
        ),
    ),
    (
        re.compile(
            r"^generate\s+tests\s+for\s+(?P<func>\w+)\s+where\s+(?P<cond>.+)$",
            re.IGNORECASE,
        ),
        lambda m: _spec_intent(
            "gentest",
            {"function": m["func"], "examples": _conditions(m["cond"], m["func"])},
        ),
    ),
)


# --------------------------------------------------------------------------- #
# naturalness: fillers, synonyms, chaining
# --------------------------------------------------------------------------- #
_LEADING_FILLERS = re.compile(
    r"^(?:please|kindly|hey|hi|now|also|and|just|can\s+you|could\s+you|"
    r"would\s+you|will\s+you)\s+",
    re.IGNORECASE,
)
_TRAILING_FILLERS = re.compile(
    r"\s+(?:please|thanks|thank\s+you|for\s+me)$", re.IGNORECASE
)

# Fixed rewrite table mapping synonym phrasings onto the canonical grammar.
_REWRITES = (
    (re.compile(r"^(?:create|make|build|craft)\s+a\s+function\b", re.IGNORECASE), "write a function"),
    (re.compile(r"^(?:create|make|build|write)\s+tests\s+for\b", re.IGNORECASE), "generate tests for"),
    (re.compile(r"^(?:repair|debug|correct)\s+", re.IGNORECASE), "fix "),
    (re.compile(r"^(?:delete|drop|strip|remove)\s+(?:the\s+)?unused\s+imports$", re.IGNORECASE), "remove unused imports"),
    (re.compile(r"^(?:describe|summarize)\b", re.IGNORECASE), "explain"),
    (re.compile(r"^what\s+does\s+(\w+)\s+do$", re.IGNORECASE), r"explain \1"),
    (re.compile(r"^add\s+docstrings?$", re.IGNORECASE), "document"),
    (re.compile(r"^(?:add\s+docstring\s+to|write\s+a\s+docstring\s+for)\s+", re.IGNORECASE), "add a docstring to "),
)

_CHAIN_VERBS = frozenset(
    "write make create build craft fix repair debug correct remove delete drop "
    "strip rename explain describe summarize document add generate".split()
)


def _normalize(text: str) -> str:
    command = " ".join(text.strip().split())
    command = command.rstrip("?!").rstrip()
    while True:
        stripped = _LEADING_FILLERS.sub("", command)
        stripped = _TRAILING_FILLERS.sub("", stripped)
        if stripped == command:
            break
        command = stripped
    for pattern, replacement in _REWRITES:
        rewritten = pattern.sub(replacement, command)
        if rewritten != command:
            command = rewritten
            break
    return command


def _split_chain(text: str) -> list[str]:
    """Split on the word "then" outside string literals, only where the next
    segment starts with a known command verb."""
    segments: list[str] = []
    quote: str | None = None
    start = i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            i += 1
            continue
        if (
            text[i : i + 4].lower() == "then"
            and (i == 0 or text[i - 1].isspace())
            and (i + 4 == n or text[i + 4].isspace())
        ):
            rest = _normalize(text[i + 4 :])
            first_word = re.match(r"[A-Za-z]+", rest)
            if first_word and first_word.group(0).lower() in _CHAIN_VERBS:
                segments.append(text[start:i])
                start = i + 4
                i += 4
                continue
        i += 1
    segments.append(text[start:])
    return [s.strip(" ,") for s in segments if s.strip(" ,")]


def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb))
            )
        previous = current
    return previous[-1]


def _closest_form(command: str) -> str:
    lowered = command.lower()
    return min(GRAMMAR, key=lambda g: (_levenshtein(lowered, g.lower()), g))


def parse(text: str) -> Intent:
    command = _normalize(text)
    for pattern, build in _PATTERNS:
        match = pattern.match(command)
        if match:
            return build(match)
    supported = "\n  ".join(GRAMMAR)
    raise CNLError(
        f"could not parse {text!r}.\n"
        f"Closest supported form: {_closest_form(command)!r}\n"
        f"All supported commands:\n  {supported}"
    )


def parse_all(text: str) -> list[Intent]:
    """Parse a possibly-chained command ("... then ...") into a pipeline."""
    return [parse(segment) for segment in _split_chain(text)]
