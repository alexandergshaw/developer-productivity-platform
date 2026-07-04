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
from .ticket import single_example_note

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
    r"|\S*[/_]\S*"        # path-like / snake_case tokens (tests/test_x.py)
    r"|\S+\.\S+"          # interior-dot tokens (utils.py) — a sentence-final
                          # "file." keeps its dot outside the mask
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
_DONE = frozenset({"done", "that's it", "thats it", "that's all", "thats all",
                   "go ahead", "go"})

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
        f"give one or more examples: {name}(...) == ? — "
        f'I\'ll keep collecting until you say "done" '
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
                        state: dict, source: str | None, store,
                        target_file: str | None = None) -> dict:
    single = len(examples) == 1
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
            if target_file:
                resp["files"] = {target_file: r.source}
            if single:
                resp["text"] = single_example_note(name)
            return resp
        if action == "gentest":
            spec: dict = {"function": name, "examples": examples}
            if source is not None:
                spec["source"] = source
            else:
                spec["module"] = name
            r = gentest_engine.gentest(spec)
            resp = _respond(state, "generated", r.source, r.report)
        else:
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
        if single:
            resp["output"] = (
                resp["output"].rstrip("\n") + f"\n\n# {single_example_note(name)}\n"
            )
        return resp
    except _refusals() as exc:
        return _respond(state, "text", f"refused: {exc}")


def _handle_examples_reply(raw: str, plain: str, state: dict,
                           source: str | None, store, files=None) -> dict:
    pending = state["pending"]
    name = str(pending.get("name") or "")
    collected = list(pending.get("examples") or [])
    target_file = pending.get("file")
    if target_file and isinstance(files, dict) and target_file in files:
        source = files[target_file]

    if plain in _DONE:
        if not collected:
            state["pending"] = dict(pending, attempts=0)
            return _respond(state, "text", f"no examples yet — {_example_question(name)}")
        state["pending"] = None
        state["last_function"] = name
        return _run_pending_action(
            str(pending.get("action") or "synth"), name, collected,
            state, source, store, target_file=target_file,
        )

    try:
        parsed_name, examples = _parse_examples_reply(raw, name)
    except ValueError:
        attempts = int(pending.get("attempts") or 0) + 1
        if attempts >= _MAX_ATTEMPTS:
            state["pending"] = None
            if collected:
                # Never discard verified spec material the user already gave.
                state["last_function"] = name
                resp = _run_pending_action(
                    str(pending.get("action") or "synth"), name, collected,
                    state, source, store, target_file=target_file,
                )
                proceed = (
                    f"proceeding with the {len(collected)} example(s) you gave me"
                )
                if resp.get("text"):
                    resp["text"] = f"{proceed}; {resp['text']}"
                else:
                    resp["text"] = proceed
                return resp
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
    merged = collected + [ex for ex in examples if ex not in collected]
    state["pending"] = dict(pending, name=parsed_name, examples=merged, attempts=0)
    return _respond(
        state, "text",
        f'got it ({len(merged)} so far) — another example? (or "done")',
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


def _slot_question(resolved: str, source: str | None, state: dict,
                   target_file: str | None = None) -> dict | None:
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
    pending = {"kind": "examples", "name": name, "action": action,
               "attempts": 0, "examples": []}
    if target_file:
        pending["file"] = target_file
    state["pending"] = pending
    return _respond(state, "text", _example_question(name))


# --------------------------------------------------------------------------- #
# compound utterances — split only where every part parses (atomicity)
# --------------------------------------------------------------------------- #

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_PART_PREFIX = re.compile(r"^(?:and\s+then|then|and|also)\s+", re.IGNORECASE)
_PART_SUFFIX = re.compile(r"\s+(?:and\s+then|then|and|also)$", re.IGNORECASE)
_CONNECTORS = (" and then ", " and also ", " also ", " and ", ", ")
_CONNECTOR_SPLIT = re.compile(
    r"\s+and\s+then\s+|\s+and\s+also\s+|\s+also\s+|\s+and\s+|,\s+"
)

MAX_COMPOUND_PARTS = 8  # honest bound: longer chains must be split by hand


def _strip_reference_phrases(text: str) -> str:
    for phrase in _REF_PHRASES:
        text = re.sub(r"\b" + re.escape(phrase) + r"\b", " ", text, flags=re.IGNORECASE)
    return " ".join(text.split())


def _clean_part(part: str) -> str:
    """Strip surrounding punctuation and bare connectors from a part.

    A truncated chain ("add docstrings and") must not parse as an operation
    on a function literally named "and"; a connector-only part cleans to "".
    """
    piece = part.strip(" ,.!?")
    while True:
        before = piece
        piece = _PART_PREFIX.sub("", piece).strip()
        piece = _PART_SUFFIX.sub("", piece).strip()
        if piece == before:
            return piece


def _quick_parse(text: str):
    """cnl.parse_all as a boolean-ish probe: intents or None, never an error.

    cnl.parse builds its refusal message with an edit-distance ranking; a
    compound split probes many long non-commands, so skip that cost here.
    """
    segments = cnl._split_chain(text)
    if not segments:
        return None
    intents = []
    for segment in segments:
        command = cnl._normalize(segment)
        for pattern, build in cnl._PATTERNS:
            match = pattern.match(command)
            if match:
                try:
                    intents.append(build(match))
                except cnl.CNLError:
                    return None
                break
        else:
            return None
    return intents


def _parse_part(part: str, last_function: str | None, memo: dict):
    """Intents for one compound part, or None. Memoized within a turn.

    Parts are substrings of the already-normalized utterance, so no
    re-normalization happens here — only cheap reference resolution.
    """
    piece = _clean_part(part)
    if not piece:
        return None
    if piece in memo:
        return memo[piece]
    resolved, needs_context = _resolve_references(piece, last_function)
    if needs_context:
        # A dangling "it" in a compound falls back to the whole file
        # ("document it" → "document"), never to a guessed function.
        resolved = _strip_reference_phrases(piece)
    result = _quick_parse(resolved) if resolved.strip() else None
    memo[piece] = result
    return result


def _split_connected(segment: str, last_function: str | None, memo: dict):
    """Greedy split into independently parseable parts; None when impossible.

    The whole remainder is tried before any split, so example conditions
    joined by "and" ("fix f so that f(1) == 2 and f(3) == 4") never break.
    Within an iteration the split points are scanned left to right and the
    FIRST prefix that parses wins (greedy, deterministic).
    """
    parts: list[str] = []
    rest = segment
    while True:
        if _parse_part(rest, last_function, memo) is not None:
            parts.append(rest)
            return parts
        positions = []
        for order, connector in enumerate(_CONNECTORS):
            start = 0
            while True:
                idx = rest.find(connector, start)
                if idx < 0:
                    break
                positions.append((idx, order, connector))
                start = idx + 1
        advanced = False
        for idx, _order, connector in sorted(positions):
            left = rest[:idx]
            if _parse_part(left, last_function, memo) is not None:
                parts.append(left)
                rest = rest[idx + len(connector):]
                advanced = True
                break
        if not advanced:
            return None


def _first_unparseable(sentence: str, last_function: str | None, memo: dict) -> str:
    """Best-effort name for the piece that blocked a compound (diagnostics)."""
    for piece in _CONNECTOR_SPLIT.split(sentence):
        cleaned = _clean_part(piece)
        if cleaned and _parse_part(piece, last_function, memo) is None:
            return cleaned
    return sentence.strip(" .!?,")


def _compound_response(text: str, state: dict, source: str | None, store):
    """Run a multi-part utterance atomically; None when not compound-shaped."""
    memo: dict = {}
    last_function = state["last_function"]
    sentences = [s for s in _SENTENCE_SPLIT.split(text) if _clean_part(s)]
    parts: list[str] = []
    failed: str | None = None
    parsed_any = False
    for sentence in sentences:
        split = _split_connected(sentence, last_function, memo)
        if split is None:
            # Partial evidence still counts as compound: a sentence whose
            # pieces partly parse must fail atomically, not fall to fuzzy.
            pieces = _CONNECTOR_SPLIT.split(sentence)
            if len(pieces) > 1 and any(
                _parse_part(p, last_function, memo) is not None for p in pieces
            ):
                parsed_any = True
            if failed is None:
                failed = _first_unparseable(sentence, last_function, memo)
        else:
            parsed_any = True
            parts.extend(split)
    if failed is not None:
        if not parsed_any:
            return None  # nothing recognizable — the ordinary fallbacks apply
        return _respond(
            state, "text",
            f'I could not map this part: "{failed}" — nothing was executed '
            "(compound commands run all-or-nothing)",
        )

    # Collapse consecutive duplicate steps, then bound the chain length.
    kept: list[str] = []
    skipped = 0
    for part in parts:
        cleaned = _clean_part(part)
        if not cleaned:
            continue
        if kept and cleaned == kept[-1]:
            skipped += 1
            continue
        kept.append(cleaned)
    if len(kept) > MAX_COMPOUND_PARTS:
        return _respond(
            state, "text",
            f"that chains more than {MAX_COMPOUND_PARTS} steps — "
            "split it into smaller requests",
        )
    if len(kept) < 2 and skipped == 0:
        return None

    intents: list = []
    for part in kept:
        for intent in _parse_part(part, last_function, memo):
            if intents and intent == intents[-1]:
                skipped += 1
                continue
            intents.append(intent)
    try:
        outcome = planner.run_all(intents, source, store)
    except _refusals() as exc:
        return _respond(state, "text", f"refused: {exc}")
    resp = _outcome_response(outcome, intents, state)
    if skipped:
        resp["report"] = dict(resp["report"])
        resp["report"]["note"] = f"skipped {skipped} duplicate step(s)"
    return resp


# --------------------------------------------------------------------------- #
# file targeting — "in app/util.py, remove unused imports"
# --------------------------------------------------------------------------- #

_FILE_PATH_RE = re.compile(r"\bin\s+(?P<path>[\w.\\/-]+)\s*,?\s*", re.IGNORECASE)
_FILE_REF_RE = re.compile(
    r"\bin\s+(?:the\s+same|the|that|this|same)\s+file\b,?\s*", re.IGNORECASE
)


def _extract_file_target(norm: str, files_map: dict, state: dict):
    """Pull an "in <path>" target out of the utterance.

    Returns (rest-of-utterance, path, response): ``response`` is a complete
    reply (unknown file, or a which-file question) and ends the turn.
    """
    match = _FILE_PATH_RE.search(norm)
    if match:
        path = match.group("path").rstrip(".,")
        if "/" in path or "\\" in path or path.endswith(".py"):
            rest = " ".join((norm[:match.start()] + " " + norm[match.end():]).split())
            if path in files_map:
                return rest, path, None
            ranked = sorted(
                files_map, key=lambda p: (cnl._levenshtein(path, p), p)
            )[:3]
            hint = ", ".join(ranked) if ranked else "(no workspace files provided)"
            return norm, None, _respond(
                state, "text", f"no file {path} — did you mean: {hint}?"
            )
    ref = _FILE_REF_RE.search(norm)
    if ref:
        rest = " ".join((norm[:ref.start()] + " " + norm[ref.end():]).split())
        last = state.get("last_file")
        if last and last in files_map:
            return rest, last, None
        return norm, None, _respond(
            state, "text",
            "which file? (no file in context yet — name it like: in app/util.py, ...)",
        )
    return norm, None, None


def _skeletons() -> tuple[tuple[str, str], ...]:
    out = []
    for line in cnl.GRAMMAR:
        s = re.sub(r"<[^>]*>", " ", line)
        s = re.sub(r"\([^)]*\)", " ", s)
        s = s.replace("[and ...]", " ").replace("==", " ").replace("/", " ")
        out.append((line, " ".join(s.split())))
    return tuple(out)


_SKELETONS = _skeletons()


def _fuzzy_fallback(norm: str, state: dict, target_file: str | None = None) -> dict:
    tokens = set(re.findall(r"[a-z]+", norm.lower()))
    best_line, best_skeleton, best_overlap = None, None, 0
    for line, skeleton in _SKELETONS:
        overlap = len(tokens & set(re.findall(r"[a-z]+", skeleton.lower())))
        if overlap > best_overlap:  # ties keep the earlier template
            best_line, best_skeleton, best_overlap = line, skeleton, overlap
    if best_overlap >= 2 and best_skeleton in _RUNNABLE:
        pending = {"kind": "confirm", "command": best_skeleton}
        if target_file:
            pending["file"] = target_file
        state["pending"] = pending
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
             source: str | None = None, store=None, files=None) -> dict:
    """Run one conversation turn. Same utterance + state + source ⇒ same reply.

    ``files`` is an optional {path: content} map; "in app/util.py, ..."
    targets an operation at that file and edits come back in ``files``.
    """
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
    files_map = files if isinstance(files, dict) and files else None

    pending = st["pending"]
    if pending:
        if plain in _CANCEL:
            st["pending"] = None
            return _respond(st, "text", "ok — question withdrawn")
        if pending.get("kind") == "examples":
            return _handle_examples_reply(raw, plain, st, source, store, files_map)
        if pending.get("kind") == "confirm":
            st["pending"] = None
            if plain in _YES:
                confirm_file = pending.get("file")
                confirm_source = source
                if confirm_file and files_map and confirm_file in files_map:
                    confirm_source = files_map[confirm_file]
                    st["last_file"] = confirm_file
                resp = _run_command(
                    str(pending.get("command") or ""), st, confirm_source, store
                )
                if confirm_file and resp.get("kind") == "edit":
                    resp["files"] = {confirm_file: resp["output"]}
                return resp
            if plain in _NO:
                return _respond(st, "text", "ok — not doing that")
            # anything else is a new topic; fall through with pending cleared

    # File targeting: "in app/util.py, remove unused imports".
    target_path = None
    if files_map is not None:
        norm, target_path, early = _extract_file_target(norm, files_map, st)
        if early is not None:
            return early
        if target_path is not None:
            source = files_map[target_path]
            st["last_file"] = target_path

    # An utterance that was ONLY a file target: ask what to do there.
    if not norm.strip():
        if target_path:
            return _respond(
                st, "text",
                f"what should I do in {target_path}? "
                '(e.g. "remove unused imports", "add docstrings")',
            )
        return _fuzzy_fallback(norm, st, None)

    resolved, needs_context = _resolve_references(norm, st["last_function"])
    if needs_context:
        # A compound like "clean up. then document it" resolves its own
        # dangling references part by part; only a lone reference asks.
        resp = _compound_response(norm, st, source, store)
        if resp is None:
            return _respond(
                st, "text", "which function? (I have no conversation context yet)"
            )
    else:
        # _quick_parse never yields an empty intents list, so the planner
        # always receives real work; anything else routes to the fallbacks.
        intents = _quick_parse(resolved)
        if intents is None:
            resp = _compound_response(resolved, st, source, store)
            if resp is None:
                slot = _slot_question(resolved, source, st, target_path)
                if slot is not None:
                    return slot
                return _fuzzy_fallback(resolved, st, target_path)
        else:
            try:
                outcome = planner.run_all(intents, source, store)
            except _refusals() as exc:
                return _respond(st, "text", f"refused: {exc}")
            resp = _outcome_response(outcome, intents, st)
    if target_path and resp.get("kind") == "edit":
        resp["files"] = {target_path: resp["output"]}
    return resp
