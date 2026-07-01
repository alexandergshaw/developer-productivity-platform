"""Domain pack: resume tailorer.

Real, working domain logic — because the core of tailoring a resume to a job
posting is deterministic: extract the posting's keywords (stopword-filtered
term frequency), measure the resume's coverage of them, rank the resume's
bullets by relevance, and turn the gaps into concrete suggestions.
"""
from __future__ import annotations

from . import Pack

_KEYWORDS = '''
"""Deterministic keyword extraction: stopword-filtered term frequency."""
import re

STOPWORDS = frozenset(
    """a an the and or but if then else for of to in on at by with from as is
    are was were be been being have has had do does did will would can could
    should may might must this that these those it its we you your our their
    they he she i me my us them not no nor so than too very just about into
    over under out up down off own same other more most some such only both
    each few all any while during before after above below between through
    also etc using use used strong ability experience years work working
    team plus required preferred qualifications responsibilities role
    candidate ideal looking seeking join company""".split()
)

_TOKEN = re.compile(r"[a-z0-9+#]+(?:[./-][a-z0-9+#]+)*")


def tokenize(text):
    """Lowercase word tokens; keeps compound tech terms like c++, node.js, ci/cd."""
    return _TOKEN.findall(text.lower())


def keywords(text):
    """Meaningful tokens: stopwords and single characters removed."""
    return [w for w in tokenize(text) if w not in STOPWORDS and len(w) > 1]


def top_keywords(text, n=15):
    """The n most frequent keywords as (word, count), ties broken alphabetically."""
    counts = {}
    for word in keywords(text):
        counts[word] = counts.get(word, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[:n]
'''

_MATCH = '''
"""Score a resume against a job posting."""
from .keywords import keywords, top_keywords


def coverage(resume_text, job_text, n=15):
    """How well the resume covers the posting's top keywords.

    Returns a dict with a 0-100 score, the matched keywords, and the missing
    ones as (word, count-in-posting) pairs, most important first.
    """
    job_top = top_keywords(job_text, n)
    resume_words = set(keywords(resume_text))
    matched = [w for w, _ in job_top if w in resume_words]
    missing = [(w, c) for w, c in job_top if w not in resume_words]
    score = round(100 * len(matched) / len(job_top)) if job_top else 0
    return {"score": score, "matched": matched, "missing": missing, "total": len(job_top)}


def bullets(resume_text):
    """The resume's bullet lines; falls back to all non-empty lines."""
    marked = [
        line.strip().lstrip("-*\\u2022").strip()
        for line in resume_text.splitlines()
        if line.strip().startswith(("-", "*", "\\u2022"))
    ]
    if marked:
        return marked
    return [line.strip() for line in resume_text.splitlines() if line.strip()]


def rank_bullets(resume_text, job_text, n=15):
    """Bullets ordered by relevance to the posting: (bullet, overlap count).

    Deterministic: sorted by overlap descending, original order for ties.
    """
    job_words = {w for w, _ in top_keywords(job_text, n)}
    scored = [
        (line, len(job_words & set(keywords(line))))
        for line in bullets(resume_text)
    ]
    order = sorted(range(len(scored)), key=lambda i: (-scored[i][1], i))
    return [scored[i] for i in order]
'''

_TAILOR = '''
"""Turn coverage gaps into concrete, ordered suggestions."""
from .match import bullets, coverage, rank_bullets


def suggestions(resume_text, job_text, n=15):
    """Actionable suggestions, most impactful first. Deterministic."""
    out = []
    cov = coverage(resume_text, job_text, n)
    out.append(
        f"Keyword coverage: {cov['score']}% "
        f"({len(cov['matched'])} of {cov['total']} top job keywords)."
    )
    for word, count in cov["missing"][:5]:
        times = "time" if count == 1 else "times"
        out.append(
            f"Add evidence for '{word}' - it appears {count} {times} in the "
            "posting but not in your resume."
        )
    ranked = rank_bullets(resume_text, job_text, n)
    if len(ranked) > 1 and ranked[0][1] > 0:
        strongest = ranked[0][0]
        if bullets(resume_text)[0] != strongest:
            out.append(f"Consider leading with your strongest bullet: '{strongest}'")
    if not cov["missing"]:
        out.append("All top job keywords are covered - tighten wording rather than adding more.")
    return out


def report(resume_text, job_text, n=15):
    """A printable tailoring report."""
    lines = ["Resume tailoring report", "======================="]
    lines.extend(f"- {s}" for s in suggestions(resume_text, job_text, n))
    return "\\n".join(lines)
'''

_CLI = '''
"""Command-line interface: __PKG__ resume.txt job.txt"""
import argparse

from .tailor import report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="__PKG__", description="Tailor a resume to a job posting, deterministically."
    )
    parser.add_argument("resume", help="path to the resume text file")
    parser.add_argument("job", help="path to the job posting text file")
    parser.add_argument("--top", type=int, default=15, help="how many job keywords to target")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    with open(args.resume, "r", encoding="utf-8-sig") as fh:
        resume_text = fh.read()
    with open(args.job, "r", encoding="utf-8-sig") as fh:
        job_text = fh.read()
    print(report(resume_text, job_text, args.top))
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

from __PKG__.keywords import top_keywords, tokenize
from __PKG__.match import bullets, coverage, rank_bullets
from __PKG__.tailor import report, suggestions

JOB = """
We are seeking a backend engineer. Kubernetes experience required.
Kubernetes, Docker, and Python are our core stack. Python testing
skills and CI/CD pipelines are a plus.
"""

RESUME = """
- Built Python services handling 10k requests per second
- Wrote testing frameworks for a payments platform
- Led migration of legacy apps
"""


class KeywordTests(unittest.TestCase):
    def test_tokenize_keeps_tech_terms(self):
        self.assertIn("ci/cd", tokenize("CI/CD pipelines and C++"))
        self.assertIn("c++", tokenize("CI/CD pipelines and C++"))

    def test_top_keywords_ranked_by_frequency_then_alpha(self):
        top = top_keywords(JOB, 4)
        self.assertEqual(top[0], ("kubernetes", 2))
        self.assertEqual(top[1], ("python", 2))


class MatchTests(unittest.TestCase):
    def test_coverage_finds_matches_and_gaps(self):
        cov = coverage(RESUME, JOB, 12)
        self.assertIn("python", cov["matched"])
        self.assertIn("testing", cov["matched"])
        missing_words = [w for w, _ in cov["missing"]]
        self.assertIn("kubernetes", missing_words)
        self.assertIn("docker", missing_words)
        self.assertTrue(0 < cov["score"] < 100)

    def test_bullets_extracted(self):
        self.assertEqual(len(bullets(RESUME)), 3)

    def test_rank_bullets_puts_relevant_first(self):
        ranked = rank_bullets(RESUME, JOB, 6)
        self.assertIn("Python", ranked[0][0])
        self.assertGreaterEqual(ranked[0][1], ranked[-1][1])


class TailorTests(unittest.TestCase):
    def test_suggestions_name_missing_keywords(self):
        text = " ".join(suggestions(RESUME, JOB, 6))
        self.assertIn("kubernetes", text)
        self.assertIn("Keyword coverage", text)

    def test_report_is_deterministic(self):
        outputs = {report(RESUME, JOB) for _ in range(5)}
        self.assertEqual(len(outputs), 1)


if __name__ == "__main__":
    unittest.main()
'''


def _files() -> dict[str, str]:
    return {
        "__PKG__/__init__.py": '"""__PKG__ (generated by detcode)."""\n',
        "__PKG__/keywords.py": _KEYWORDS.lstrip("\n"),
        "__PKG__/match.py": _MATCH.lstrip("\n"),
        "__PKG__/tailor.py": _TAILOR.lstrip("\n"),
        "__PKG__/cli.py": _CLI.lstrip("\n"),
        "__PKG__/__main__.py": _MAIN.lstrip("\n"),
        "tests/__init__.py": "",
        "tests/test___PKG__.py": _TESTS.lstrip("\n"),
    }


PACK = Pack(
    key="resume-tailorer",
    title="Resume tailorer",
    default_slug="resume_tailorer",
    keywords=frozenset(["resume", "resumes", "cv", "tailor", "tailorer", "tailoring"]),
    description=(
        "a working resume tailorer: job-posting keyword extraction, coverage "
        "scoring, bullet ranking, and tailoring suggestions, with tests"
    ),
    files=_files,
)
