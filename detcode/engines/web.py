"""The knowledge web — meetings, chats, and links, deterministically queryable.

Feed it meeting transcripts and (OCR'd) chat captures as *notes*, and URLs as
*links*. Everything is derived from the stored text by fixed rules at query
time — no stored index to drift:

- **facts**: lines classified as decision / action / question by fixed word
  lists, with the speaker when lines look like "Name: utterance"
- **terms**: stopword-filtered term frequency per note
- **query**: keyword-scored retrieval that returns EVIDENCE WITH CITATIONS
  (note title + line number, decisions and actions flagged, related links,
  code symbols) — never a synthesized claim. A miss is honest and joins the
  study queue.
- **web**: the neighborhood of a term — co-occurring terms, notes, links,
  people

Local-first: notes, links, and the code index live in your machine's
database. Nothing is fetched, nothing is uploaded. LLMs and OCR are external
commands at an explicit boundary (see the CLI), never in this engine.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..determinism import provenance
from .knowledge import _words

RULE_VERSION = "1"

_SPEAKER = re.compile(r"^(?P<name>[A-Z][A-Za-z .'-]{1,30}):\s+(?P<text>.+)$")

# Fixed classification tables — first hit wins.
_DECISION_WORDS = ("decided", "decision", "agreed", "agreement", "approved",
                   "we will go with", "going with", "chose", "chosen", "signed off")
_ACTION_WORDS = ("action item", "action:", "todo", "to do", "will send", "will set up",
                 "will create", "will update", "will fix", "will follow up", "owner:",
                 "assigned to", "due ", "deadline", "follow up", "next step")


class WebError(Exception):
    """The knowledge-web request was malformed."""


@dataclass
class Result:
    text: str
    report: dict


def classify_line(line: str) -> str | None:
    lowered = line.lower()
    if any(w in lowered for w in _DECISION_WORDS):
        return "decision"
    if any(w in lowered for w in _ACTION_WORDS):
        return "action"
    if line.rstrip().endswith("?"):
        return "question"
    return None


def facts(text: str) -> list[dict]:
    """Typed lines: decisions, actions, questions — with speakers when present."""
    out = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        speaker = None
        match = _SPEAKER.match(line)
        if match:
            speaker = match.group("name")
            line = match.group("text")
        kind = classify_line(line)
        if kind:
            out.append({"line": lineno, "kind": kind, "text": line, "speaker": speaker})
    return out


def terms(text: str, n: int = 12) -> list[str]:
    counts: dict[str, int] = {}
    for word in _words(text):
        if word.isdigit():
            continue  # dates and numbers are not terms
        counts[word] = counts.get(word, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [w for w, _c in ranked[:n]]


def speakers(text: str) -> list[str]:
    found = {m.group("name") for m in map(_SPEAKER.match, text.splitlines()) if m}
    return sorted(found)


def _score_words(candidate_words: set[str], question_words: set[str]) -> int:
    return len(candidate_words & question_words)


def query(
    question: str,
    notes: list[dict],
    links: list[dict] = (),
    code: list[dict] = (),
    top: int = 3,
) -> Result:
    """Deterministic retrieval-QA: evidence with citations, never synthesis."""
    if not isinstance(question, str) or not question.strip():
        raise WebError('ask something, e.g. detcode query "what did we decide about auth?"')
    qwords = _words(question)
    if not qwords:
        raise WebError("the question has no searchable words")

    scored_notes = sorted(
        (
            (n, 2 * _score_words(set(terms(n["text"], 25)), qwords)
             + _score_words(_words(n["title"]), qwords))
            for n in notes
        ),
        key=lambda t: (-t[1], t[0]["title"]),
    )
    scored_notes = [(n, s) for n, s in scored_notes if s > 0][:top]

    scored_links = sorted(
        (
            (l, _score_words(
                _words(f"{l['title']} {l['tags']} {l['note']} {l['url']}"), qwords))
            for l in links
        ),
        key=lambda t: (-t[1], t[0]["url"]),
    )
    scored_links = [(l, s) for l, s in scored_links if s > 0][:top]

    scored_code = sorted(
        (
            (c, _score_words(
                set(c["symbol"].lower().split("_")) | _words(c.get("doc") or ""), qwords))
            for c in code
        ),
        key=lambda t: (-t[1], t[0]["path"], t[0]["line"]),
    )
    scored_code = [(c, s) for c, s in scored_code if s > 0][:5]

    if not scored_notes and not scored_links and not scored_code:
        report = provenance(
            "query", RULE_VERSION, outcome="miss",
            question_keywords=sorted(qwords),
        )
        return Result(
            "No evidence on file for this question — refusing to guess.\n"
            "Logged to the study queue. Grow the web:\n"
            "  detcode note add transcript.txt      (meetings, chats)\n"
            "  detcode link add URL --tags a,b      (bookmarks)\n"
            "  detcode index --dir path/to/repo     (your codebase, locally)",
            report,
        )

    lines = ["Evidence (deterministic retrieval — citations, not synthesis):"]
    for note, _score in scored_notes:
        lines.append("")
        lines.append(f"## {note['title']}  [{note['kind']}]")
        note_facts = facts(note["text"])
        note_lines = note["text"].splitlines()
        hits = sorted(
            (
                (i + 1, line, _score_words(_words(line), qwords))
                for i, line in enumerate(note_lines)
            ),
            key=lambda t: (-t[2], t[0]),
        )
        shown = 0
        fact_by_line = {f["line"]: f for f in note_facts}
        for lineno, line, score in hits:
            if score == 0 or shown >= 4:
                break
            fact = fact_by_line.get(lineno)
            tag = f" [{fact['kind'].upper()}]" if fact else ""
            who = f" — {fact['speaker']}" if fact and fact["speaker"] else ""
            lines.append(f"  L{lineno}{tag}: {line.strip()}{who}")
            shown += 1
    if scored_links:
        lines.append("")
        lines.append("## Related links")
        for link, _score in scored_links:
            tags = f"  [{link['tags']}]" if link["tags"] else ""
            lines.append(f"  {link['title'] or link['url']}{tags}")
            lines.append(f"    {link['url']}")
    if scored_code:
        lines.append("")
        lines.append("## In the codebase (local index)")
        for entry, _score in scored_code:
            doc = f" — {entry['doc']}" if entry.get("doc") else ""
            lines.append(f"  {entry['path']}:{entry['line']}  {entry['kind']} {entry['symbol']}{doc}")

    report = provenance(
        "query", RULE_VERSION, outcome="evidence",
        notes=[n["title"] for n, _s in scored_notes],
        links=[l["url"] for l, _s in scored_links],
        code=[f"{c['path']}:{c['line']}" for c, _s in scored_code],
    )
    return Result("\n".join(lines), report)


def related(term: str, notes: list[dict], links: list[dict] = ()) -> Result:
    """The neighborhood of a term in the web."""
    term = (term or "").strip().lower()
    if not term:
        raise WebError("give a term, e.g. detcode web auth")

    matching_notes = [n for n in notes if term in set(terms(n["text"], 40)) or term in _words(n["title"])]
    co_terms: dict[str, int] = {}
    people: set[str] = set()
    speaker_names: set[str] = set()
    for note in matching_notes:
        note_speakers = speakers(note["text"])
        people.update(note_speakers)
        speaker_names.update(s.lower() for s in note_speakers)
        # Line-level co-occurrence: words sharing a LINE with the term are its
        # real neighbors; doc-level frequency just surfaces noise.
        for line in note["text"].splitlines():
            line_words = _words(line)
            if term not in line_words:
                continue
            for word in line_words:
                if word != term and not word.isdigit() and word not in speaker_names:
                    co_terms[word] = co_terms.get(word, 0) + 1
    matching_links = [
        l for l in links
        if term in _words(f"{l['title']} {l['tags']} {l['note']} {l['url']}")
    ]

    if not matching_notes and not matching_links:
        return Result(
            f"nothing in the web mentions {term!r} yet",
            provenance("web", RULE_VERSION, term=term, notes=0, links=0),
        )

    ranked_terms = sorted(co_terms.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    lines = [f"# {term} — knowledge web neighborhood"]
    if ranked_terms:
        lines.append("Related terms: " + ", ".join(t for t, _c in ranked_terms))
    if people:
        lines.append("People: " + ", ".join(sorted(people)))
    if matching_notes:
        lines.append("Notes: " + "; ".join(n["title"] for n in matching_notes))
    if matching_links:
        lines.append("Links:")
        lines.extend(f"  {l['title'] or l['url']} — {l['url']}" for l in matching_links)

    report = provenance(
        "web", RULE_VERSION, term=term,
        notes=len(matching_notes), links=len(matching_links),
        related=[t for t, _c in ranked_terms],
    )
    return Result("\n".join(lines), report)
