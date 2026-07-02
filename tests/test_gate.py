import json
import os
import shutil
import tempfile
import unittest

from detcode.cli import _study_cards, main as cli_main
from detcode.engines import builder


class AdviseGateTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="detcode_gate_")
        self.baseline = os.path.join(self.dir, "baseline.json")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, name, content):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as fh:
            fh.write(content)

    def gate(self, *extra):
        return cli_main(
            ["advise", "--dir", self.dir, "--baseline", self.baseline, *extra]
        )

    def test_ratchet_lifecycle(self):
        self.write("app.py", "import os\n\ndef f(x):\n    return x\n")
        # No baseline yet: the finding is new -> fail.
        self.assertEqual(self.gate("--check"), 2)
        # Accept deliberately -> clean.
        self.assertEqual(self.gate("--write-baseline"), 0)
        self.assertEqual(self.gate("--check"), 0)
        # A NEW kind of finding trips the gate even with the baseline.
        self.write("app.py", "import os\n\ndef f(items=[]):\n    return items\n")
        self.assertEqual(self.gate("--check"), 2)

    def test_fingerprints_survive_line_drift(self):
        self.write("app.py", "import os\n\ndef f(x):\n    return x\n")
        self.gate("--write-baseline")
        # Same finding, different line: still clean (content fingerprint).
        self.write("app.py", "\n\n\nimport os\n\ndef f(x):\n    return x\n")
        self.assertEqual(self.gate("--check"), 0)

    def test_clean_tree_passes_with_no_baseline(self):
        self.write("app.py", "def f(x):\n    return x\n")
        self.assertEqual(self.gate("--check"), 0)

    def test_detcode_gates_itself(self):
        # The repo's own baseline must hold: detcode is its reviewer-of-record.
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = cli_main(["advise", "--dir", os.path.join(repo, "detcode"), "--check"])
        self.assertEqual(code, 0)

    def test_generated_projects_ship_ci(self):
        project = builder.build("a resume tailorer")
        ci = next(f for f in project.files if f.path == ".github/workflows/ci.yml")
        self.assertIn("unittest discover", ci.content)
        self.assertIn("advise --dir resume_tailorer --check", ci.content)


class StudyCardsTests(unittest.TestCase):
    RECORDS = [
        {"question": "how do I slugify a title", "keywords": ["slugify"],
         "status": "answered", "answered_by": "taught slugify"},
        {"question": "how does jvm gc work", "keywords": ["jvm", "gc"],
         "status": "open", "answered_by": None},
    ]

    def test_cards_shape(self):
        text = _study_cards(self.RECORDS)
        self.assertIn("Q: how do I slugify a title", text)
        self.assertIn("A: taught slugify", text)
        self.assertIn("how does jvm gc work remains an open question.", text)

    def test_cards_feed_the_teaching_assistant(self):
        import sys

        from tests.test_builder import materialize
        from detcode.engines import builder as builder_engine

        project = builder_engine.build("a teaching assistant app")
        root = materialize(project)
        sys.path.insert(0, root)
        try:
            from teaching_assistant.flashcards import parse_notes, prose  # noqa: E402
            from teaching_assistant.quiz import cloze_questions  # noqa: E402

            text = _study_cards(self.RECORDS)
            cards = parse_notes(text)
            self.assertEqual(cards[0]["front"], "how do I slugify a title")
            self.assertEqual(cards[0]["back"], "taught slugify")
            quiz = cloze_questions(prose(text))
            self.assertTrue(any("____" in q["question"] for q in quiz))
        finally:
            sys.path.remove(root)
            for mod in [m for m in list(sys.modules) if m.startswith("teaching_assistant")]:
                del sys.modules[mod]
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
