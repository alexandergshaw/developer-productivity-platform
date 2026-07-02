import shutil
import unittest

from detcode.engines import builder
from tests.test_builder import materialize, run_generated_tests


class CompositionTests(unittest.TestCase):
    DIRECTION = "a teaching assistant with a resume module"

    def test_matches_both_packs_primary_first(self):
        project = builder.build(self.DIRECTION)
        # "teaching" appears before "resume" in the direction -> primary.
        self.assertEqual(project.report["packs"], ["teaching-assistant", "resume-tailorer"])
        self.assertEqual(project.name, "teaching_assistant")
        self.assertEqual(
            project.report["packages"], ["teaching_assistant", "resume_tailorer"]
        )

    def test_primary_follows_direction_order(self):
        flipped = builder.build("a resume tailorer with teaching flashcards")
        self.assertEqual(flipped.report["packs"], ["resume-tailorer", "teaching-assistant"])
        self.assertEqual(flipped.name, "resume_tailorer")

    def test_both_packages_and_tests_present(self):
        project = builder.build(self.DIRECTION)
        paths = [f.path for f in project.files]
        self.assertIn("teaching_assistant/scheduler.py", paths)
        self.assertIn("resume_tailorer/tailor.py", paths)
        self.assertIn("tests/test_teaching_assistant.py", paths)
        self.assertIn("tests/test_resume_tailorer.py", paths)
        pyproject = next(f for f in project.files if f.path == "pyproject.toml")
        self.assertIn('"teaching_assistant*", "resume_tailorer*"', pyproject.content)

    def test_decisions_record_composition(self):
        project = builder.build(self.DIRECTION)
        decisions = " ".join(project.report["decisions"])
        self.assertIn("composed 2 domain packs", decisions)
        self.assertIn("primary", decisions)

    def test_composed_project_tests_all_pass(self):
        project = builder.build(self.DIRECTION)
        root = materialize(project)
        try:
            for slug in project.report["packages"]:
                result = run_generated_tests(root, slug)
                self.assertGreater(result.testsRun, 0, slug)
                self.assertEqual(result.failures, [], slug)
                self.assertEqual(result.errors, [], slug)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_single_pack_unchanged(self):
        project = builder.build("a resume tailorer")
        self.assertEqual(project.report["packs"], ["resume-tailorer"])
        self.assertNotIn(
            "composed", " ".join(project.report["decisions"])
        )


if __name__ == "__main__":
    unittest.main()
