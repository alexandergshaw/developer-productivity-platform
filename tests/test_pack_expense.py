import shutil
import sys
import unittest

from detcode.engines import builder
from tests.test_builder import materialize, run_generated_tests


class ExpenseTrackerPackTests(unittest.TestCase):
    def test_direction_matches_pack(self):
        for direction in ("an expense tracker", "a budgeting tool", "track my spending"):
            project = builder.build(direction)
            self.assertEqual(project.report["pack"], "expense-tracker", direction)

    def test_generated_project_tests_pass(self):
        project = builder.build("an expense tracker")
        root = materialize(project)
        try:
            result = run_generated_tests(root, project.name)
            self.assertGreaterEqual(result.testsRun, 7)
            self.assertEqual(result.failures, [])
            self.assertEqual(result.errors, [])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_generated_logic_works_end_to_end(self):
        project = builder.build("an expense tracker")
        root = materialize(project)
        sys.path.insert(0, root)
        try:
            from expense_tracker.report import format_report  # noqa: E402
            from expense_tracker.transactions import parse_transactions  # noqa: E402

            csv = "2026-05-01,Kroger,-10.00\n2026-05-02,Netflix,-15.49\n"
            text = format_report(parse_transactions(csv))
            self.assertIn("groceries", text)
            self.assertIn("entertainment", text)
            self.assertIn("-$25.49", text)  # integer-cents total
        finally:
            sys.path.remove(root)
            for mod in [m for m in list(sys.modules) if m.startswith("expense_tracker")]:
                del sys.modules[mod]
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
