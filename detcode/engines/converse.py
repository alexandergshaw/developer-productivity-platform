"""Deterministic dialogue — talk to the engine in loose natural language.

An LLM chat feels flexible because it guesses. This engine feels flexible
without guessing: a fixed normalization layer (politeness stripping,
contractions, synonym folding, bounded typo repair) maps loose phrasing onto
the controlled grammar in :mod:`detcode.cnl`, and anything still ambiguous
becomes an explicit question with slot-filling. Conversation state is a plain
dict the caller round-trips — no hidden globals — so the same transcript
replays byte-identically.

Resolution order (fixed): normalize; answer a pending question (examples /
confirm / cancel); resolve references ("it", "that function") to the last
function discussed; parse with the cnl grammar and run through the planner;
ask for examples when a function is named without a spec; suggest the closest
supported form; otherwise answer honestly that nothing matched, with the
nearest grammar lines. A conversation never hard-refuses — refusals from the
engines come back as plain replies. :class:`ConverseError` is reserved for
malformed state input.
"""
from __future__ import annotations

import ast
import json
import re

from .. import cnl, planner
from . import gentest as gentest_engine, repair as repair_engine, retrieve

RULE_VERSION = "1"

_HISTORY_CAP = 10
_MAX_ATTEMPTS = 3

# Bounded work, bounded state: conversation turns are short instructions.
# Longer problem descriptions belong to the ticket engine; capping here keeps
# every turn's cost fixed (a size bound, never a wall clock).
MAX_UTTERANCE = 2000       # characters accepted per turn
_HISTORY_SNIPPET = 200     # characters of an utterance kept in history
_FUZZY_DISTANCE_CAP = 200  # skip Levenshtein ranking beyond this length


class ConverseError(Exception):
    """The conversation state passed in was malformed."""


# --------------------------------------------------------------------------- #
# normalizer — fixed tables, no statistics
# --------------------------------------------------------------------------- #

# Grammar words the typo repairer may snap to (and never away from).
VOCABULARY = frozenset(
    "remove unused imports sort clean up explain document docstring rename "
    "local in to write add a an function where generate tests for build "
    "create start make fix so that teach ask advise review and then query "
    "plan".split()
)

# Ordinary English glue words: never repaired, never repair targets. Without
# this, "what" would snap to "that" and break the question forms.
_STOPWORDS = frozenset(
    "what when how why does do did with from into have has had want need "
    "just them they their here there will would could should this these "
    "those some same hello about been being your file files code".split()
)

_CONTRACTIONS = (
    (re.compile(r"\bwhat's\b", re.IGNORECASE), "what is"),
    (re.compile(r"\blet's\b", re.IGNORECASE), "let us"),
    (re.compile(r"\bdon't\b", re.IGNORECASE), "do not"),
    (re.compile(r"\bi'd\b", re.IGNORECASE), "i would"),
)

# Leading politeness/filler, stripped repeatedly (longest first).
_PREFIXES = (
    "i would like you to", "i'd like you to", "i want you to",
    "i need you to", "go ahead and", "can you", "could you", "would you",
    "will you", "please", "okay", "hey", "hi", "ok", "so",
)
_SUFFIXES = ("thank you", "for me", "thanks", "please")

# Synonym folding: fixed order, longest phrases first, one pass each.
_SYNONYMS = (
    (re.compile(r"\badd\s+a\s+docstring\b(?!\s+to\b)", re.IGNORECASE), "document"),
    (re.compile(r"\bthe\s+unused\s+imports\b", re.IGNORECASE), "unused imports"),
    (re.compile(r"\badd\s+docstrings\b", re.IGNORECASE), "document"),
    (re.compile(r"\bget\s+rid\s+of\b", re.IGNORECASE), "remove"),
    (re.compile(r"\bcreate\s+me\s+a\b", re.IGNORECASE), "create a"),
    (re.compile(r"\bwrite\s+me\s+a\b", re.IGNORECASE), "write a"),
    (re.compile(r"\bmake\s+me\s+a\b", re.IGNORECASE), "build a"),
    (re.compile(r"\badd\s+docs\b", re.IGNORECASE), "document"),
    (re.compile(r"\bsort\s+out\b", re.IGNORECASE), "sort"),
    (re.compile(r"\btidy\s+up\b", re.IGNORECASE), "clean up"),
    (re.compile(r"\bcleanup\b", re.IGNORECASE), "clean up"),
    (re.compile(r"\bdelete\b", re.IGNORECASE), "remove"),
    (re.compile(r"\bstrip\b", re.IGNORECASE), "remove"),
    (re.compile(r"\btidy\b", re.IGNORECASE), "clean up"),
    (re.compile(r"\bdrop\b", re.IGNORECASE), "remove"),
)

_PROTECTED = re.compile(
    r"`[^`]*`"            # backtick code spans
    r"|\"[^\"]*\""        # double-quoted strings
    r"|'[^']*'"           # single-quoted strings
    r"|\([^()]*\)"        # parenthesized argument lists
)


def _mask(text: str) -> tuple[str, list[str]]:
    """Replace protected spans with placeholders so the tables never touch them."""
    spans: list[str] = []

    def keep(match: re.Match) -> str:
        spans.append(match.group(0))
        return f"\x00{len(spans) - 1}\x00"

    return _PROTECTED.sub(keep, text), spans


def _unmask(text: str, spans: list[str]) -> str:
    for i, span in enumerate(spans):
        text = text.replace(f"\x00{i}\x00", span)
    return text


def _strip_politeness(text: str) -> str:
    while True:
        before = text
        text = text.rstrip(" ?!.,")
        lowered = text.lower()
        for prefix in _PREFIXES:
            if lowered.startswith(prefix) and (
                len(text) == len(prefix) or text[len(prefix)] in " ,"
            ):
                text = text[len(prefix):].lstrip(" ,")
                break
        lowered = text.lower()
        for suffix in _SUFFIXES:
            if lowered.endswith(suffix) and (
                len(text) == len(suffix) or text[-len(suffix) - 1] in " ,"
            ):
                text = text[: -len(suffix)].rstrip(" ,")
                break
        if text == before:
            return text


def _osa_distance(a: str, b: str) -> int:
    """Optimal-string-alignment distance: Levenshtein plus adjacent swaps.

    Counting a transposition as one edit lets the repairer accept classic
    typos ("remvoe", "improts") at threshold 1 while rejecting real words
    that plain Levenshtein <= 2 would mangle ("resume" -> "rename").
    """
    rows = [[i + j if i * j == 0 else 0 for j in range(len(b) + 1)] for i in range(len(a) + 1)]
    for i, ca in enumerate(a, 1):
        for j, cb in enumerate(b, 1):
            cost = ca != cb
            rows[i][j] = min(rows[i - 1][j] + 1, rows[i][j - 1] + 1, rows[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                rows[i][j] = min(rows[i][j], rows[i - 2][j - 2] + 1)
    return rows[-1][-1]


def _repair_typos(text: str, call_names: frozenset[str]) -> str:
    def fix(match: re.Match) -> str:
        token = match.group(0)
        lowered = token.lower()
        if len(token) < 4 or lowered in VOCABULARY or lowered in _STOPWORDS \
                or lowered in call_names:
            return token
        best = min(VOCABULARY, key=lambda w: (_osa_distance(lowered, w), w))
        if _osa_distance(lowered, best) <= 1:
            return best
        return token

    return re.sub(r"[A-Za-z]+", fix, text)


def normalize(utterance: str) -> str:
    """Fold loose phrasing onto grammar words. Pure and table-driven.

    Quoted strings, backtick spans and parenthesized argument lists are
    masked out first, so example expressions survive untouched; tokens that
    name a call (``double(...)``) are never typo-repaired.
    """
    text = " ".join(str(utterance).strip().split())
    call_names = frozenset(n.lower() for n in re.findall(r"(\w+)\s*\(", text))
    masked, spans = _mask(text)
    for pattern, replacement in _CONTRACTIONS:
        masked = pattern.sub(replacement, masked)
    masked = _strip_politeness(masked)
    for pattern, replacement in _SYNONYMS:
        masked = pattern.sub(replacement, masked)
    masked = _repair_typos(masked, call_names)
    return " ".join(_unmask(masked, spans).split())


# --------------------------------------------------------------------------- #
# conversation state
# --------------------------------------------------------------------------- #

_YES = frozenset({"yes", "y", "yeah", "yep", "sure", "do it"})
_NO = frozenset({"no", "n", "nope", "cancel", "never mind"})
_CANCEL = frozenset({"cancel", "never mind", "nevermind", "forget it"})

_OP_WORDS = frozenset(
    {"tests", "test", "explain", "document", "rename", "fix", "teach", "docstring"}
)
_REF_PHRASES = ("that function", "this function", "the function", "the same one", "that", "it")

# Grammar templates that are directly runnable with no placeholders.
_RUNNABLE = frozenset({"remove unused imports", "sort imports", "clean up", "explain", "document"})


def _fresh_state(state) -> dict:
    if state is None:
        return {"pending": None, "last_function": None, "last_file": None, "history": []}
    if not isinstance(state, dict):
        raise ConverseError("state must be an object — round-trip the returned state")
    pending = state.get("pending")
    if pending is not None and not isinstance(pending, dict):
        raise ConverseError("state['pending'] must be an object or null")
    history = state.get("history") or []
    if not isinstance(history, list):
        raise ConverseError("state['history'] must be a list")
    return {
        "pending": dict(pending) if pending else None,
        "last_function": state.get("last_function"),
        "last_file": state.get("last_file"),
        "history": list(history),
    }


def _respond(state: dict, kind: str, output: str, report: dict | None = None) -> dict:
    return {
        "ok": True,
        "kind": kind,
        "output": output,
        "changed": False,
        "report": report if report is not None else {},
        "state": state,
    }


def _refusals() -> tuple:
    from .. import service

    return service.REFUSALS


def _example_question(name: str) -> str:
    return (
        f"give one example: {name}(...) == ?  "
        f'(e.g. "{name}(2) == 4", or shorthand "2 -> 4")'
    )


# --------------------------------------------------------------------------- #
# pending-question handling
# --------------------------------------------------------------------------- #

_SHORTHANDS = (
    re.compile(r"^(?P<a>.+?)\s*->\s*(?P<b>.+)$"),
    re.compile(r"^(?P<a>.+?)\s+gives\s+(?P<b>.+)$", re.IGNORECASE),
    re.compile(r"^(?P<a>.+?)\s+becomes\s+(?P<b>.+)$", re.IGNORECASE),
    re.compile(r"^input\s+(?P<a>.+?)\s+output\s+(?P<b>.+)$", re.IGNORECASE),
)


def _parse_examples_reply(reply: str, default_name: str) -> tuple[str, list[dict]]:
    """Read examples from a reply; raises ValueError when nothing parses."""
    text = reply.strip().strip("?!.").strip().strip("`").strip()
    call = re.match(r"^(\w+)\s*\(", text)
    if call:
        name = call.group(1)
        try:
            return name, cnl._conditions(text, name)
        except cnl.CNLError as exc:
            raise ValueError(str(exc)) from exc
    for pattern in _SHORTHANDS:
        match = pattern.match(text)
        if not match:
            continue
        try:
            args = ast.literal_eval(f"({match['a']},)")
            expected = ast.literal_eval(match["b"].strip())
        except (ValueError, SyntaxError) as exc:
            raise ValueError(f"could not read literals in {text!r}") from exc
        return default_name, [{"in": list(args), "out": expected}]
    raise ValueError(f"no example found in {reply!r}")


def _run_pending_action(action: str, name: str, examples: list[dict],
                        state: dict, source: str | None, store) -> dict:
    try:
        if action == "repair":
            if source is None:
                return _respond(
                    state, "text",
                    f"I need the file containing {name} open to repair it — "
                    "open it and give the example again",
                )
            r = repair_engine.repair(source, {"function": name, "examples": examples})
            resp = _respond(state, "edit", r.source, r.report)
            resp["changed"] = r.changed
            return resp
        if action == "gentest":
            spec: dict = {"function": name, "examples": examples}
            if source is not None:
                spec["source"] = source
            else:
                spec["module"] = name
            r = gentest_engine.gentest(spec)
            return _respond(state, "generated", r.source, r.report)
        # synth: verified function plus its tests, like an LLM would offer.
        generated = retrieve.write_function(
            {"name": name, "examples": examples}, extra=planner.corpus_entries(store)
        )
        tests = gentest_engine.gentest(
            {"function": name, "examples": examples, "source": generated.source}
        )
        resp = _respond(state, "generated", generated.source, generated.report)
        resp["files"] = {
            f"{name}.py": generated.source,
            f"tests/test_{name}.py": tests.source,
        }
        return resp
    except _refusals() as exc:
        return _respond(state, "text", f"refused: {exc}")


def _handle_examples_reply(raw: str, state: dict, source: str | None, store) -> dict:
    pending = state["pending"]
    name = str(pending.get("name") or "")
    try:
        parsed_name, examples = _parse_examples_reply(raw, name)
    except ValueError:
        attempts = int(pending.get("attempts") or 0) + 1
        if attempts >= _MAX_ATTEMPTS:
            state["pending"] = None
            return _respond(
                state, "text",
                f"giving up on that question after {attempts} tries — when you have "
                f'an example, say: write a function {name} where {name}(...) == ...',
            )
        state["pending"] = dict(pending, attempts=attempts)
        return _respond(
            state, "text",
            f"I could not read an example in that. {_example_question(name)}",
        )
    state["pending"] = None
    state["last_function"] = parsed_name
    return _run_pending_action(
        str(pending.get("action") or "synth"), parsed_name, examples, state, source, store
    )


# --------------------------------------------------------------------------- #
# running commands through the grammar + planner
# --------------------------------------------------------------------------- #

def _intent_function(intent) -> str | None:
    func = intent.get("func") or intent.get("name")
    if func:
        return str(func)
    spec_json = intent.get("spec_json")
    if spec_json:
        spec = json.loads(spec_json)
        return spec.get("function") or spec.get("name")
    return None


def _outcome_response(outcome, intents, state: dict) -> dict:
    for intent in intents:
        func = _intent_function(intent)
        if func:
            state["last_function"] = func
    if outcome.new_source is not None:
        resp = _respond(state, "edit", outcome.new_source, outcome.report)
        resp["changed"] = outcome.changed
        if outcome.output:
            resp["text"] = outcome.output
        if outcome.files:
            resp["files"] = outcome.files
        return resp
    explain_only = all(i.operation == "explain" for i in intents)
    resp = _respond(
        state, "text" if explain_only else "generated", outcome.output or "", outcome.report
    )
    if outcome.files:
        resp["files"] = outcome.files
    return resp


def _run_command(command: str, state: dict, source: str | None, store) -> dict:
    try:
        intents = cnl.parse_all(command)
        outcome = planner.run_all(intents, source, store)
    except _refusals() as exc:
        return _respond(state, "text", f"refused: {exc}")
    return _outcome_response(outcome, intents, state)


# --------------------------------------------------------------------------- #
# reference resolution, slot questions, fuzzy fallback
# --------------------------------------------------------------------------- #

def _resolve_references(norm: str, last_function: str | None) -> tuple[str, bool]:
    """Replace "it"/"that function"/... with the last function discussed.

    Returns (resolved utterance, needs-context) — the flag is set when a
    reference phrase is present but there is no conversation context yet.
    """
    tokens = set(re.findall(r"[a-z]+", norm.lower()))
    if not tokens & _OP_WORDS:
        return norm, False
    resolved, found = norm, False
    for phrase in _REF_PHRASES:
        if phrase == "that" and re.search(r"\bso\s+that\b", resolved, re.IGNORECASE):
            continue  # "fix f so that ..." — that "that" is grammar, not a reference
        pattern = re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)
        if pattern.search(resolved):
            found = True
            if last_function:
                resolved = pattern.sub(last_function, resolved)
    return resolved, (found and not last_function)


def _slot_question(resolved: str, source: str | None, state: dict) -> dict | None:
    """A named function without examples becomes a precise question."""
    probe = cnl._normalize(resolved)
    if " where " in probe.lower():
        return None
    match = re.match(r"^(?:write|add)\s+a\s+function\s+(?P<name>\w+)(?:\s+that\s+.+)?$",
                     probe, re.IGNORECASE)
    action = "synth"
    if not match:
        match = re.match(r"^generate\s+tests?\s+for\s+(?P<name>\w+)$", probe, re.IGNORECASE)
        action = "gentest"
    if not match:
        fix = re.match(r"^fix\s+(?P<name>\w+)$", probe, re.IGNORECASE)
        if fix and source is not None and re.search(
            rf"^\s*def\s+{re.escape(fix['name'])}\s*\(", source, re.MULTILINE
        ):
            match, action = fix, "repair"
    if not match:
        return None
    name = match["name"]
    state["pending"] = {"kind": "examples", "name": name, "action": action, "attempts": 0}
    return _respond(state, "text", _example_question(name))


def _skeletons() -> tuple[tuple[str, str], ...]:
    out = []
    for line in cnl.GRAMMAR:
        s = re.sub(r"<[^>]*>", " ", line)
        s = re.sub(r"\([^)]*\)", " ", s)
        s = s.replace("[and ...]", " ").replace("==", " ").replace("/", " ")
        out.append((line, " ".join(s.split())))
    return tuple(out)


_SKELETONS = _skeletons()


def _fuzzy_fallback(norm: str, state: dict) -> dict:
    tokens = set(re.findall(r"[a-z]+", norm.lower()))
    best_line, best_skeleton, best_overlap = None, None, 0
    for line, skeleton in _SKELETONS:
        overlap = len(tokens & set(re.findall(r"[a-z]+", skeleton.lower())))
        if overlap > best_overlap:  # ties keep the earlier template
            best_line, best_skeleton, best_overlap = line, skeleton, overlap
    if best_overlap >= 2 and best_skeleton in _RUNNABLE:
        state["pending"] = {"kind": "confirm", "command": best_skeleton}
        return _respond(state, "text", f'did you mean: "{best_skeleton}"? (yes/no)')
    if best_overlap >= 2:
        return _respond(
            state, "text",
            f'closest supported form: "{best_line}" — rephrase like that and I will run it',
        )
    if len(norm) <= _FUZZY_DISTANCE_CAP:
        nearest = sorted(
            range(len(cnl.GRAMMAR)),
            key=lambda i: (cnl._levenshtein(norm.lower(), cnl.GRAMMAR[i].lower()), i),
        )[:3]
    else:  # too long to rank by edit distance; show the grammar head instead
        nearest = range(3)
    lines = "\n".join(f"  {cnl.GRAMMAR[i]}" for i in nearest)
    return _respond(
        state, "text",
        "I could not map that to a supported operation.\n"
        f"Nearest supported forms:\n{lines}\n"
        "Tip: examples are the fastest spec — "
        'e.g. "write a function double where double(2) == 4"',
    )


# --------------------------------------------------------------------------- #
# the conversation turn
# --------------------------------------------------------------------------- #

def converse(utterance: str, state: dict | None = None,
             source: str | None = None, store=None) -> dict:
    """Run one conversation turn. Same utterance + state + source ⇒ same reply."""
    st = _fresh_state(state)
    raw = str(utterance or "")
    if len(raw) > MAX_UTTERANCE:
        st["history"] = (st["history"] + [raw[:_HISTORY_SNIPPET]])[-_HISTORY_CAP:]
        return _respond(
            st, "text",
            f"that message is {len(raw)} characters — I work from short "
            "instructions or examples; for long problem descriptions use the "
            "ticket tool",
        )
    plain = raw.strip().strip("?!. ").lower()
    norm = normalize(raw)
    st["history"] = (st["history"] + [norm[:_HISTORY_SNIPPET]])[-_HISTORY_CAP:]

    pending = st["pending"]
    if pending:
        if plain in _CANCEL:
            st["pending"] = None
            return _respond(st, "text", "ok — question withdrawn")
        if pending.get("kind") == "examples":
            return _handle_examples_reply(raw, st, source, store)
        if pending.get("kind") == "confirm":
            st["pending"] = None
            if plain in _YES:
                return _run_command(str(pending.get("command") or ""), st, source, store)
            if plain in _NO:
                return _respond(st, "text", "ok — not doing that")
            # anything else is a new topic; fall through with pending cleared

    resolved, needs_context = _resolve_references(norm, st["last_function"])
    if needs_context:
        return _respond(st, "text", "which function? (I have no conversation context yet)")

    try:
        intents = cnl.parse_all(resolved)
    except cnl.CNLError:
        slot = _slot_question(resolved, source, st)
        if slot is not None:
            return slot
        return _fuzzy_fallback(resolved, st)

    try:
        outcome = planner.run_all(intents, source, store)
    except _refusals() as exc:
        return _respond(st, "text", f"refused: {exc}")
    return _outcome_response(outcome, intents, st)
