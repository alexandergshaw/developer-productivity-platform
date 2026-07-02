"""Technical guidance — ask, learn, study.

The third scale of detcode's memory: functions are taught, projects are
minted, knowledge is *learned*. Guidance retrieval is an honest cascade:

1. the knowledge base — topic entries matched by deterministic keyword
   scoring. Entries carry sources, and optionally executable examples whose
   assertions are RE-VERIFIED on every load: knowledge that can rot refuses
   to serve
2. engine knowledge — what detcode already knows in executable form
   (corpus functions, domain packs)
3. an honest miss: "I don't know yet", the closest topics, and the question
   logged to the study queue

The loop closes with ``learn``: a new entry needs sources or passing
examples (no bar, no entry) and automatically answers matching open
questions — the same question asked later hits.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..determinism import content_hash, provenance

RULE_VERSION = "1"
KNOWLEDGE_FORMAT = 1

_STOP = frozenset(
    """a an the and or but is are was be been do does did how what why when
    where which who should could would can i my me we you it its in on of to
    for with from as at by this that these those there here about into use
    using used best way ways good right proper properly handle handling deal
    if then than versus vs between avoid avoiding""".split()
)
_WORD = re.compile(r"[a-z0-9_]+")


class KnowledgeError(Exception):
    """An entry failed verification, or the knowledge file is malformed."""


@dataclass
class Answer:
    outcome: str  # "knowledge" | "engine" | "miss"
    text: str
    topic: str | None
    report: dict


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1}


def _verify_examples(topic: str, examples: list) -> None:
    """Every example must contain an assertion and run clean in isolation."""
    for i, example in enumerate(examples):
        code = example.get("code") if isinstance(example, dict) else None
        if not isinstance(code, str) or "assert" not in code:
            raise KnowledgeError(
                f"example {i} of {topic!r} must be code containing an assert — "
                "knowledge entries make checkable claims"
            )
        try:
            exec(compile(code, f"<knowledge:{topic}>", "exec"), {})
        except Exception as exc:
            raise KnowledgeError(
                f"example {i} of {topic!r} failed verification: {type(exc).__name__}: {exc}"
            ) from exc


def _validate_entry(entry: dict) -> dict:
    topic = entry.get("topic")
    if not isinstance(topic, str) or not topic.strip():
        raise KnowledgeError("entry needs a topic")
    keywords = entry.get("keywords")
    if not isinstance(keywords, list) or not keywords:
        raise KnowledgeError(f"entry {topic!r} needs keywords — asks match on them")
    guidance = entry.get("guidance")
    if not isinstance(guidance, str) or not guidance.strip():
        raise KnowledgeError(f"entry {topic!r} needs guidance text")
    sources = entry.get("sources") or []
    examples = entry.get("examples") or []
    if not sources and not examples:
        raise KnowledgeError(
            f"entry {topic!r} needs at least one source or one verified example — "
            "accountability is the bar"
        )
    _verify_examples(topic, examples)
    return {
        "topic": topic.strip(),
        "keywords": sorted({str(k).strip().lower() for k in keywords if str(k).strip()}),
        "guidance": guidance.strip(),
        "sources": [str(s) for s in sources],
        "examples": examples,
    }


# --------------------------------------------------------------------------- #
# built-in knowledge: things this engine genuinely knows, with receipts
# --------------------------------------------------------------------------- #
def _b(topic, keywords, guidance, sources=(), examples=()):
    return {
        "topic": topic,
        "keywords": sorted(keywords),
        "guidance": guidance.strip(),
        "sources": list(sources),
        "examples": list(examples),
    }


BUILTIN_KNOWLEDGE: tuple[dict, ...] = (
    _b(
        "Mutable default arguments",
        ["mutable", "default", "argument", "arguments", "list", "dict", "def"],
        "A default like `def f(items=[])` is evaluated ONCE at definition time "
        "and shared across every call — appends leak between calls. Use "
        "`items=None` and `items = [] if items is None else items` inside the "
        "body. detcode's diagnostics flag this pattern.",
        sources=["https://docs.python.org/3/reference/compound_stmts.html#function-definitions"],
        examples=[{
            "code": (
                "def bad(items=[]):\n    items.append(1)\n    return items\n"
                "assert bad() == [1]\nassert bad() == [1, 1]  # shared state!\n"
                "def good(items=None):\n    items = [] if items is None else items\n"
                "    items.append(1)\n    return items\n"
                "assert good() == [1]\nassert good() == [1]\n"
            ),
            "note": "the shared-state failure, demonstrated",
        }],
    ),
    _b(
        "Comparing with None",
        ["none", "comparison", "compare", "equality", "identity"],
        "`x == None` invokes __eq__, which arbitrary objects can override; "
        "`x is None` checks identity against the one None singleton and cannot "
        "be fooled. Always `is None` / `is not None`.",
        sources=["https://peps.python.org/pep-0008/#programming-recommendations"],
        examples=[{
            "code": (
                "class Weird:\n    def __eq__(self, other):\n        return True\n"
                "w = Weird()\nassert (w == None) is True   # lies\n"
                "assert (w is None) is False  # truth\n"
            ),
            "note": "__eq__ can lie; identity cannot",
        }],
    ),
    _b(
        "Bare except clauses",
        ["except", "exception", "exceptions", "error", "errors", "try", "catch"],
        "`except:` catches EVERYTHING, including KeyboardInterrupt and "
        "SystemExit, making programs unkillable and hiding real bugs. Catch "
        "the narrowest exception you can handle; use `except Exception:` only "
        "at true top-level boundaries, and log what you swallow.",
        sources=["https://docs.python.org/3/tutorial/errors.html"],
        examples=[{
            "code": (
                "caught = []\ntry:\n    int('x')\nexcept ValueError as e:\n"
                "    caught.append(type(e).__name__)\n"
                "assert caught == ['ValueError']\n"
            ),
            "note": "narrow catch documents intent",
        }],
    ),
    _b(
        "Money and floats",
        ["money", "float", "floats", "currency", "cents", "decimal", "rounding", "amount"],
        "Binary floats cannot represent most decimal fractions: 0.1 + 0.2 != "
        "0.3, and the error compounds across sums. Store money as integer "
        "cents (or decimal.Decimal) and format at the edge. detcode's expense "
        "tracker pack is built this way.",
        sources=["https://docs.python.org/3/tutorial/floatingpoint.html"],
        examples=[{
            "code": (
                "assert 0.1 + 0.2 != 0.3\n"
                "assert 10 + 20 == 30  # integer cents: exact\n"
            ),
            "note": "the classic",
        }],
    ),
    _b(
        "Deterministic tests and time",
        ["deterministic", "determinism", "test", "tests", "flaky", "time", "clock", "timeout", "random"],
        "Flaky tests almost always smuggle in nondeterminism: wall-clock "
        "timeouts (machine-speed dependent), datetime.now(), random without a "
        "seed, dict/set iteration used for output ordering, or network. Bound "
        "work by operation COUNTS not seconds, pass 'now' in as a parameter, "
        "seed or remove randomness, sort before asserting. This project's "
        "determinism spine exists because of exactly these failure modes.",
        sources=["https://martinfowler.com/articles/nonDeterminism.html"],
        examples=[{
            "code": (
                "def review(state, today):\n    return {'due': today + state['interval']}\n"
                "a = review({'interval': 6}, today=100)\n"
                "b = review({'interval': 6}, today=100)\n"
                "assert a == b  # same inputs, same schedule — no wall clock\n"
            ),
            "note": "pass time in; never read it",
        }],
    ),
    _b(
        "Membership tests: list vs set/dict",
        ["set", "list", "dict", "membership", "lookup", "performance", "contains", "in"],
        "`x in some_list` scans every element (O(n)); `x in some_set` hashes "
        "(O(1) average). Inside a loop that difference is quadratic vs linear. "
        "If you test membership more than once, build a set first. Keep lists "
        "when order and duplicates matter.",
        sources=["https://wiki.python.org/moin/TimeComplexity"],
        examples=[{
            "code": (
                "items = list(range(100))\nlookup = set(items)\n"
                "assert 99 in lookup\nassert 99 in items  # same answer, different cost\n"
            ),
            "note": "same semantics, different complexity",
        }],
    ),
    _b(
        "Spaced repetition scheduling",
        ["spaced", "repetition", "sm2", "flashcard", "flashcards", "review", "memorization", "study"],
        "SM-2 (SuperMemo 2) grows the review interval 1 day -> 6 days -> "
        "interval * ease, where ease drifts with answer quality and floors at "
        "1.3; a failed recall resets repetitions. Run it on day NUMBERS, not "
        "timestamps, and the same history always yields the same schedule. "
        "detcode's teaching-assistant pack ships a tested implementation.",
        sources=["https://super-memory.com/english/ol/sm2.htm"],
        examples=[{
            "code": (
                "def next_interval(reps, interval, ease):\n"
                "    return 1 if reps == 1 else 6 if reps == 2 else round(interval * ease)\n"
                "assert next_interval(1, 0, 2.5) == 1\n"
                "assert next_interval(2, 1, 2.5) == 6\n"
                "assert next_interval(3, 6, 2.5) == 15\n"
            ),
            "note": "the interval ladder",
        }],
    ),
    _b(
        "Keyword extraction without ML",
        ["keyword", "keywords", "extraction", "tf", "frequency", "text", "ranking", "stopwords"],
        "Stopword-filtered term frequency gets you surprisingly far: tokenize, "
        "drop a fixed stopword list, count, rank by (count desc, word asc) for "
        "deterministic ties. It powers detcode's resume tailorer. Reach for "
        "TF-IDF only when you have a corpus to weight against.",
        sources=["https://en.wikipedia.org/wiki/Tf%E2%80%93idf"],
        examples=[{
            "code": (
                "words = 'the cat sat on the cat mat'.split()\n"
                "counts = {}\n"
                "for w in words:\n"
                "    if w != 'the' and w != 'on':\n"
                "        counts[w] = counts.get(w, 0) + 1\n"
                "top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))\n"
                "assert top[0] == ('cat', 2)\n"
            ),
            "note": "TF with deterministic tie-break",
        }],
    ),
    _b(
        "Example-driven development",
        ["example", "examples", "spec", "specification", "tdd", "requirements", "synthesis"],
        "Concrete input->output examples beat prose specs: they are testable, "
        "unambiguous, and machine-usable. Write the examples first; they pin "
        "the boundary cases prose glosses over. This whole engine runs on it — "
        "examples are the interface to synthesis, repair, teaching, and plans. "
        "Under-specified examples get you a program consistent with them but "
        "not your intent, so pin the boundaries.",
        sources=["https://en.wikipedia.org/wiki/Test-driven_development"],
        examples=[{
            "code": (
                "examples = [((2, 3), 6), ((4, 5), 20)]\n"
                "candidate = lambda a, b: a * b\n"
                "assert all(candidate(*i) == o for i, o in examples)\n"
            ),
            "note": "examples as executable spec",
        }],
    ),
)


# --------------------------------------------------------------------------- #
# ask / learn / study
# --------------------------------------------------------------------------- #
def _score(entry: dict, question_words: set[str]) -> int:
    keyword_hits = len(set(entry["keywords"]) & question_words)
    topic_hits = len(_words(entry["topic"]) & question_words)
    return 2 * keyword_hits + topic_hits


def _render(entry: dict, origin: str) -> str:
    lines = [f"# {entry['topic']}"]
    if entry["examples"]:
        lines[0] += "   [✓ verified example]"
    lines.append("")
    lines.append(entry["guidance"])
    for example in entry["examples"]:
        lines.append("")
        note = example.get("note")
        lines.append(f"```python  # {note}" if note else "```python")
        lines.append(example["code"].rstrip("\n"))
        lines.append("```")
    if entry["sources"]:
        lines.append("")
        lines.extend(f"Source: {s}" for s in entry["sources"])
    lines.append("")
    lines.append(f"[knowledge origin: {origin}]")
    return "\n".join(lines)


def ask(
    question: str,
    extra_entries: tuple = (),
    corpus: tuple = (),
    pack_list: tuple = (),
) -> Answer:
    """Answer a technical question through the honest cascade."""
    if not isinstance(question, str) or not question.strip():
        raise KnowledgeError("ask a question, e.g. detcode ask \"how do I store money?\"")
    question_words = _words(question)

    candidates = [(e, "builtin") for e in BUILTIN_KNOWLEDGE]
    candidates += [(e, "learned") for e in extra_entries]
    scored = sorted(
        ((entry, origin, _score(entry, question_words)) for entry, origin in candidates),
        key=lambda t: (-t[2], t[0]["topic"]),
    )
    if scored and scored[0][2] > 0:
        entry, origin, score = scored[0]
        also = [e["topic"] for e, _o, s in scored[1:3] if s > 0]
        text = _render(entry, origin)
        if also:
            text += "\nRelated topics: " + "; ".join(also)
        report = provenance(
            "ask", RULE_VERSION, outcome="knowledge", topic=entry["topic"],
            origin=origin, score=score,
        )
        return Answer("knowledge", text, entry["topic"], report)

    # Engine knowledge: verified artifacts that touch the question's words.
    for entry in corpus:
        name_words = set(entry.name.split("_"))
        if name_words & question_words:
            text = (
                f"# {entry.name} (verified corpus function)\n\n"
                "detcode holds a tested implementation covering this:\n\n"
                f"```python\n{entry.source.rstrip(chr(10))}\n```\n\n"
                f'Get it into a file: add a function {entry.name} where '
                f"{entry.name}(...) == ...\n\n[knowledge origin: corpus]"
            )
            report = provenance(
                "ask", RULE_VERSION, outcome="engine", topic=entry.name, origin="corpus",
            )
            return Answer("engine", text, entry.name, report)
    for pack in pack_list:
        if set(pack.keywords) & question_words:
            text = (
                f"# {pack.title} (domain pack)\n\n{pack.description}.\n\n"
                f'Build it: detcode new "a {sorted(pack.keywords)[0]} project"\n\n'
                "[knowledge origin: pack]"
            )
            report = provenance(
                "ask", RULE_VERSION, outcome="engine", topic=pack.key, origin="pack",
            )
            return Answer("engine", text, pack.key, report)

    closest = sorted(
        {e["topic"] for e, _o in candidates},
    )[:3]
    text = (
        "I don't know this yet — refusing to guess.\n\n"
        "Logged to the study queue (see: detcode study). When you find the "
        "answer, feed it back:\n"
        '  detcode learn --topic "..." --keywords a,b --source URL --guidance "..."\n'
        "and this question answers itself from then on.\n\n"
        "Nearest known topics: " + "; ".join(closest)
    )
    report = provenance(
        "ask", RULE_VERSION, outcome="miss",
        question_keywords=sorted(question_words),
    )
    return Answer("miss", text, None, report)


def learn(entry: dict, knowledge_text: str | None = None) -> tuple[str, dict]:
    """Validate + verify an entry; return (new knowledge text, report)."""
    validated = _validate_entry(entry)
    entries = _parse_knowledge(knowledge_text) if knowledge_text else []
    replaced = any(e["topic"] == validated["topic"] for e in entries)
    entries = [e for e in entries if e["topic"] != validated["topic"]]
    entries.append(validated)
    entries.sort(key=lambda e: e["topic"])
    text = json.dumps(
        {"detcode_knowledge": KNOWLEDGE_FORMAT, "entries": entries},
        indent=2, sort_keys=True,
    ) + "\n"
    report = provenance(
        "learn", RULE_VERSION, topic=validated["topic"],
        keywords=validated["keywords"], replaced=replaced,
        verified_examples=len(validated["examples"]),
        knowledge_hash=content_hash(text),
    )
    return text, report


def _parse_knowledge(text: str) -> list[dict]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise KnowledgeError(f"knowledge file is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("detcode_knowledge") != KNOWLEDGE_FORMAT:
        raise KnowledgeError('not a detcode knowledge file (expected {"detcode_knowledge": 1})')
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise KnowledgeError("knowledge 'entries' must be a list")
    return entries


def load_knowledge(text: str) -> tuple[dict, ...]:
    """Parse and RE-VERIFY learned knowledge; rotted examples refuse loudly."""
    out = []
    for raw in _parse_knowledge(text):
        out.append(_validate_entry(raw))
    out.sort(key=lambda e: e["topic"])
    return tuple(out)
