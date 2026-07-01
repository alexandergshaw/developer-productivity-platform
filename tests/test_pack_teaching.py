import shutil
import sys
import unittest

from detcode.engines import builder
from tests.test_builder import materialize, run_generated_tests


class TeachingAssistantPackTests(unittest.TestCase):
    def test_direction_matches_pack(self):
        project = builder.build("a teaching assistant app")
        self.assertEqual(project.report["pack"], "teaching-assistant")
        self.assertEqual(project.name, "teaching_assistant")

    def test_synonym_directions_match(self):
        for direction in ("a flashcard study tool", "quiz my students on lessons"):
            project = builder.build(direction)
            self.assertEqual(project.report["pack"], "teaching-assistant", direction)

    def test_resume_direction_does_not_match(self):
        project = builder.build("a resume tailorer")
        self.assertEqual(project.report["pack"], "resume-tailorer")

    def test_generated_project_tests_pass(self):
        project = builder.build("a teaching assistant app")
        root = materialize(project)
        try:
            result = run_generated_tests(root, project.name)
            self.assertGreaterEqual(result.testsRun, 8)
            self.assertEqual(result.failures, [])
            self.assertEqual(result.errors, [])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_generated_logic_works_end_to_end(self):
        project = builder.build("a teaching assistant app")
        root = materialize(project)
        sys.path.insert(0, root)
        try:
            from teaching_assistant.quiz import cloze_questions, grade  # noqa: E402
            from teaching_assistant.scheduler import new_card_state, review  # noqa: E402

            qs = cloze_questions("Gravity pulls objects together. Gravity is universal.")
            self.assertEqual(qs[0]["answer"], "gravity")
            self.assertTrue(grade(qs[0]["answer"], " GRAVITY "))
            # Same review history, same schedule — the determinism story holds.
            a = review(review(new_card_state(), 5, 0), 5, 1)
            b = review(review(new_card_state(), 5, 0), 5, 1)
            self.assertEqual(a, b)
        finally:
            sys.path.remove(root)
            for mod in [m for m in list(sys.modules) if m.startswith("teaching_assistant")]:
                del sys.modules[mod]
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
