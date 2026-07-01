"""Domain pack: teaching assistant.

Real, working domain logic — the studying core of a teaching assistant is
deterministic: parse notes into flashcards, generate cloze (fill-in-the-blank)
quizzes by blanking the most significant term of each sentence, grade answers
by normalized comparison, and schedule reviews with the SM-2 spaced-repetition
algorithm. Scheduling uses day numbers, never the wall clock, so the same
review history always yields the same schedule.
"""
from __future__ import annotations

from . import Pack

_FLASHCARDS = '''
"""Parse study notes into flashcards.

Two note formats are recognized, and both can be mixed with prose:

    Q: What is photosynthesis?
    A: The process plants use to convert light into energy.

    mitochondria: the powerhouse of the cell
"""
import re

_TERM_LINE = re.compile(r"^(?P<term>[^:]{1,60}):\\s*(?P<definition>.+)$")


def parse_notes(text):
    """Extract flashcards as {"front", "back"} dicts, in note order."""
    cards = []
    pending_question = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        upper = line[:2].upper()
        if upper == "Q:":
            pending_question = line[2:].strip()
            continue
        if upper == "A:" and pending_question:
            cards.append({"front": pending_question, "back": line[2:].strip()})
            pending_question = None
            continue
        match = _TERM_LINE.match(line)
        if match and match.group("term").strip().upper() not in ("Q", "A"):
            cards.append(
                {
                    "front": f"Define: {match.group('term').strip()}",
                    "back": match.group("definition").strip(),
                }
            )
    return cards


def prose(text):
    """The lines that are NOT flashcard lines — the quizzable prose."""
    kept = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[:2].upper() in ("Q:", "A:") or _TERM_LINE.match(line):
            continue
        kept.append(line)
    return "\\n".join(kept)
'''

_QUIZ = '''
"""Cloze quiz generation and grading.

Each sentence in the notes becomes a fill-in-the-blank question: the most
significant term (highest frequency across the notes, longest, then
alphabetical — fixed tie-breaking) is blanked out.
"""
import re

STOPWORDS = frozenset(
    """a an the and or but if then else for of to in on at by with from as is
    are was were be been being have has had do does did will would can could
    should may might must this that these those it its we you your our their
    they not no so than too very just about into over under out up down when
    which what where who whom whose why how all any each most other some
    such""".split()
)

_WORD = re.compile(r"[A-Za-z][A-Za-z-]{2,}")


def _sentences(text):
    flat = " ".join(text.split())
    return [s.strip() for s in re.split(r"(?<=[.!?])\\s+", flat) if s.strip()]


def _frequencies(text):
    counts = {}
    for word in _WORD.findall(text.lower()):
        if word not in STOPWORDS:
            counts[word] = counts.get(word, 0) + 1
    return counts


def cloze_questions(text):
    """Fill-in-the-blank questions as {"question", "answer"} dicts."""
    freq = _frequencies(text)
    questions = []
    for sentence in _sentences(text):
        candidates = [
            w for w in _WORD.findall(sentence.lower())
            if w not in STOPWORDS and len(w) > 3
        ]
        if not candidates:
            continue
        answer = min(candidates, key=lambda w: (-freq.get(w, 0), -len(w), w))
        blanked = re.sub(re.escape(answer), "____", sentence, count=1, flags=re.IGNORECASE)
        questions.append({"question": blanked, "answer": answer})
    return questions


def grade(expected, given):
    """True if the answer matches after normalization (case, spacing)."""
    normalize = lambda s: " ".join(s.lower().split())
    return normalize(expected) == normalize(given)
'''

_SCHEDULER = '''
"""SM-2 spaced repetition, on day numbers — never the wall clock.

The caller supplies "today" as an integer day number; the same review history
always produces the same schedule, byte for byte.
"""


def new_card_state():
    """The state of a card that has never been reviewed."""
    return {"reps": 0, "interval": 0, "ease": 2.5, "due_day": 0}


def review(state, quality, today):
    """Apply one review (quality 0-5) and return the new state.

    Classic SM-2: quality < 3 resets the repetition count; otherwise the
    interval grows 1 -> 6 -> round(interval * ease), and ease drifts with
    answer quality (floored at 1.3).
    """
    if not 0 <= quality <= 5:
        raise ValueError("quality must be 0..5")
    reps = state["reps"]
    interval = state["interval"]
    ease = state["ease"]

    if quality < 3:
        reps = 0
        interval = 1
    else:
        reps += 1
        if reps == 1:
            interval = 1
        elif reps == 2:
            interval = 6
        else:
            interval = round(interval * ease)
    ease = max(1.3, ease + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    return {"reps": reps, "interval": interval, "ease": ease, "due_day": today + interval}


def due(cards, today):
    """The subset of (card, state) pairs due for review on ``today``."""
    return [(card, state) for card, state in cards if state["due_day"] <= today]
'''

_CLI = '''
"""Command-line interface: __PKG__ cards|quiz notes.txt"""
import argparse

from .flashcards import parse_notes, prose
from .quiz import cloze_questions


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="__PKG__", description="Deterministic study tools from your notes."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    cards = sub.add_parser("cards", help="parse notes into flashcards")
    cards.add_argument("notes", help="path to the notes file")
    quiz = sub.add_parser("quiz", help="generate a fill-in-the-blank quiz")
    quiz.add_argument("notes", help="path to the notes file")
    quiz.add_argument("--answers", action="store_true", help="show the answers")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    with open(args.notes, "r", encoding="utf-8-sig") as fh:
        text = fh.read()
    if args.command == "cards":
        for i, card in enumerate(parse_notes(text)):
            print(f"[{i}] {card['front']}")
            print(f"    -> {card['back']}")
    else:
        for i, q in enumerate(cloze_questions(prose(text))):
            print(f"[{i}] {q['question']}")
            if args.answers:
                print(f"    answer: {q['answer']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

_MAIN = '''
"""Enables python -m __PKG__."""
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
'''

_TESTS = '''
"""Tests for the generated __PKG__ project."""
import unittest

from __PKG__.flashcards import parse_notes, prose
from __PKG__.quiz import cloze_questions, grade
from __PKG__.scheduler import due, new_card_state, review

NOTES = """
Q: What is photosynthesis?
A: The process plants use to convert light into energy.

mitochondria: the powerhouse of the cell

Photosynthesis happens in the chloroplast. Photosynthesis needs sunlight.
"""


class FlashcardTests(unittest.TestCase):
    def test_parses_qa_pairs(self):
        cards = parse_notes(NOTES)
        self.assertEqual(cards[0]["front"], "What is photosynthesis?")
        self.assertIn("energy", cards[0]["back"])

    def test_parses_term_definitions(self):
        cards = parse_notes(NOTES)
        self.assertEqual(cards[1]["front"], "Define: mitochondria")
        self.assertEqual(cards[1]["back"], "the powerhouse of the cell")

    def test_prose_excludes_card_lines(self):
        text = prose(NOTES)
        self.assertNotIn("Q:", text)
        self.assertNotIn("mitochondria:", text)
        self.assertIn("chloroplast", text)


class QuizTests(unittest.TestCase):
    def test_cloze_blanks_the_most_significant_term(self):
        questions = cloze_questions("Photosynthesis happens in the chloroplast. Photosynthesis needs sunlight.")
        self.assertEqual(questions[0]["answer"], "photosynthesis")
        self.assertIn("____", questions[0]["question"])

    def test_grade_normalizes(self):
        self.assertTrue(grade("Photosynthesis", "  photosynthesis "))
        self.assertFalse(grade("chloroplast", "mitochondria"))

    def test_deterministic(self):
        runs = {str(cloze_questions(NOTES)) for _ in range(5)}
        self.assertEqual(len(runs), 1)


class SchedulerTests(unittest.TestCase):
    def test_sm2_progression(self):
        state = new_card_state()
        state = review(state, 5, today=0)
        self.assertEqual(state["interval"], 1)
        state = review(state, 5, today=1)
        self.assertEqual(state["interval"], 6)
        state = review(state, 5, today=7)
        self.assertGreater(state["interval"], 6)

    def test_failure_resets(self):
        state = review(review(new_card_state(), 5, 0), 2, 1)
        self.assertEqual(state["reps"], 0)
        self.assertEqual(state["interval"], 1)

    def test_due_filtering(self):
        fresh = new_card_state()
        later = dict(fresh, due_day=9)
        cards = [("a", fresh), ("b", later)]
        self.assertEqual([c for c, _ in due(cards, 0)], ["a"])

    def test_quality_bounds(self):
        with self.assertRaises(ValueError):
            review(new_card_state(), 6, 0)


if __name__ == "__main__":
    unittest.main()
'''


def _files() -> dict[str, str]:
    return {
        "__PKG__/__init__.py": '"""__PKG__ (generated by detcode)."""\n',
        "__PKG__/flashcards.py": _FLASHCARDS.lstrip("\n"),
        "__PKG__/quiz.py": _QUIZ.lstrip("\n"),
        "__PKG__/scheduler.py": _SCHEDULER.lstrip("\n"),
        "__PKG__/cli.py": _CLI.lstrip("\n"),
        "__PKG__/__main__.py": _MAIN.lstrip("\n"),
        "tests/__init__.py": "",
        "tests/test___PKG__.py": _TESTS.lstrip("\n"),
    }


PACK = Pack(
    key="teaching-assistant",
    title="Teaching assistant",
    default_slug="teaching_assistant",
    keywords=frozenset(
        [
            "teaching", "teacher", "tutor", "tutoring", "flashcard", "flashcards",
            "quiz", "quizzes", "lesson", "lessons", "study", "studying",
        ]
    ),
    description=(
        "a working teaching assistant: flashcards from notes, cloze quiz "
        "generation, answer grading, and SM-2 spaced-repetition scheduling, "
        "with tests"
    ),
    files=_files,
)
