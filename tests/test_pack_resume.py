import shutil
import unittest

from detcode.engines import builder
from tests.test_builder import materialize, run_generated_tests


class ResumeTailorerPackTests(unittest.TestCase):
    def test_direction_matches_pack(self):
        project = builder.build("a resume tailorer")
        self.assertEqual(project.report["pack"], "resume-tailorer")
        self.assertEqual(project.name, "resume_tailorer")
        decisions = " ".join(project.report["decisions"])
        self.assertIn("Resume tailorer", decisions)

    def test_synonym_directions_match(self):
        for direction in ("tailor my cv to job postings", "an app for tailoring resumes"):
            project = builder.build(direction)
            self.assertEqual(project.report["pack"], "resume-tailorer", direction)

    def test_generated_project_tests_pass(self):
        project = builder.build("a resume tailorer")
        root = materialize(project)
        try:
            result = run_generated_tests(root, project.name)
            self.assertGreaterEqual(result.testsRun, 7)
            self.assertEqual(result.failures, [])
            self.assertEqual(result.errors, [])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_generated_logic_works_end_to_end(self):
        import os
        import sys

        project = builder.build("a resume tailorer")
        root = materialize(project)
        sys.path.insert(0, root)
        try:
            from resume_tailorer.tailor import report  # noqa: E402

            job = "Kubernetes and Python required. Kubernetes, Docker, testing."
            resume = "- Python testing frameworks\n- Team leadership"
            text = report(resume, job)
            self.assertIn("Keyword coverage", text)
            self.assertIn("kubernetes", text)  # named as missing
        finally:
            sys.path.remove(root)
            for mod in [m for m in list(sys.modules) if m.startswith("resume_tailorer")]:
                del sys.modules[mod]
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
