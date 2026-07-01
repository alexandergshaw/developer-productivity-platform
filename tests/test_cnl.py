import textwrap
import unittest

from detcode import cnl, planner
from detcode.cnl import CNLError
from detcode.ir import Intent


def dedent(s: str) -> str:
    return textwrap.dedent(s).lstrip("\n")


class CNLParseTests(unittest.TestCase):
    def test_parses_rename_local(self):
        intent = cnl.parse("rename local total to acc in compute")
        self.assertEqual(intent.operation, "rename-local")
        self.assertEqual(intent.get("old"), "total")
        self.assertEqual(intent.get("new"), "acc")
        self.assertEqual(intent.get("func"), "compute")

    def test_parses_remove_unused_imports(self):
        intent = cnl.parse("remove unused imports")
        self.assertEqual(intent, Intent.of("remove-unused-imports"))

    def test_case_and_whitespace_insensitive(self):
        intent = cnl.parse("  Rename   Local  x  TO  y   in   f ")
        self.assertEqual(intent.operation, "rename-local")
        self.assertEqual(intent.get("old"), "x")

    def test_deterministic_parse(self):
        results = {cnl.parse("remove unused imports") for _ in range(10)}
        self.assertEqual(len(results), 1)

    def test_refuses_unknown_command(self):
        with self.assertRaises(CNLError) as ctx:
            cnl.parse("please make my code faster")
        self.assertIn("Supported commands", str(ctx.exception))


class PlannerTests(unittest.TestCase):
    def test_routes_rename_through_full_seam(self):
        src = dedent(
            """
            def compute(a):
                total = a + 1
                return total
            """
        )
        intent = cnl.parse("rename local total to acc in compute")
        result = planner.run(intent, src)
        self.assertIn("acc = a + 1", result.source)
        self.assertIn("return acc", result.source)

    def test_routes_remove_imports(self):
        src = "import os\nimport sys\n\nprint(sys.argv)\n"
        intent = cnl.parse("remove unused imports")
        result = planner.run(intent, src)
        self.assertNotIn("import os", result.source)

    def test_unknown_intent_refused(self):
        with self.assertRaises(planner.UnknownIntent):
            planner.run(Intent.of("teleport"), "x = 1\n")


if __name__ == "__main__":
    unittest.main()
