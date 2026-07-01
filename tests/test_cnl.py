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
        self.assertIn("supported commands", str(ctx.exception))

    def test_did_you_mean_suggestion(self):
        with self.assertRaises(CNLError) as ctx:
            cnl.parse("remove unused improts")
        self.assertIn("Closest supported form: 'remove unused imports'", str(ctx.exception))


class CNLv2Tests(unittest.TestCase):
    """English forms for synth, repair, gentest, and explain."""

    def test_write_a_function(self):
        intent = cnl.parse("write a function double where double(2) == 4 and double(5) == 10")
        self.assertEqual(intent.operation, "synth")
        import json
        spec = json.loads(intent.get("spec_json"))
        self.assertEqual(spec["name"], "double")
        self.assertEqual(spec["examples"], [{"in": [2], "out": 4}, {"in": [5], "out": 10}])

    def test_fix_so_that(self):
        intent = cnl.parse("fix area so that area(2, 3) == 6 and area(4, 5) == 20")
        self.assertEqual(intent.operation, "repair")
        import json
        spec = json.loads(intent.get("spec_json"))
        self.assertEqual(spec["function"], "area")
        self.assertEqual(len(spec["examples"]), 2)

    def test_generate_tests(self):
        intent = cnl.parse("generate tests for area where area(2, 3) == 6")
        self.assertEqual(intent.operation, "gentest")

    def test_explain_function_and_module(self):
        self.assertEqual(cnl.parse("explain compute").get("func"), "compute")
        self.assertEqual(cnl.parse("explain").operation, "explain")

    def test_string_literals_with_and_inside(self):
        # ast-based condition parsing: 'and' inside a string is not a splitter.
        intent = cnl.parse('write a function shout where shout("rock and roll") == "ROCK AND ROLL"')
        import json
        spec = json.loads(intent.get("spec_json"))
        self.assertEqual(spec["examples"], [{"in": ["rock and roll"], "out": "ROCK AND ROLL"}])

    def test_refuses_mismatched_function_name(self):
        with self.assertRaises(CNLError):
            cnl.parse("fix area so that volume(2) == 8")

    def test_refuses_non_literal_arguments(self):
        with self.assertRaises(CNLError):
            cnl.parse("fix f so that f(x) == 1")

    def test_negative_number_literals(self):
        intent = cnl.parse("write a function neg where neg(-3) == 3")
        import json
        spec = json.loads(intent.get("spec_json"))
        self.assertEqual(spec["examples"], [{"in": [-3], "out": 3}])


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
        outcome = planner.run(intent, src)
        self.assertIn("acc = a + 1", outcome.new_source)
        self.assertIn("return acc", outcome.new_source)

    def test_routes_remove_imports(self):
        src = "import os\nimport sys\n\nprint(sys.argv)\n"
        intent = cnl.parse("remove unused imports")
        outcome = planner.run(intent, src)
        self.assertNotIn("import os", outcome.new_source)

    def test_english_to_synthesized_function(self):
        intent = cnl.parse("write a function double where double(2) == 4 and double(5) == 10")
        outcome = planner.run(intent)
        self.assertIn("def double(x):", outcome.output)

    def test_english_to_repair(self):
        src = "def area(w, h):\n    return w + h\n"
        intent = cnl.parse("fix area so that area(2, 3) == 6 and area(4, 5) == 20")
        outcome = planner.run(intent, src)
        self.assertIn("w * h", outcome.new_source)

    def test_english_to_tests(self):
        src = "def area(w, h):\n    return w * h\n"
        intent = cnl.parse("generate tests for area where area(2, 3) == 6")
        outcome = planner.run(intent, src)
        self.assertIn("class TestArea(", outcome.output)
        self.assertIn("def area(w, h):", outcome.output)  # embedded source

    def test_english_to_explanation(self):
        src = "def f(x):\n    return x + 1\n"
        outcome = planner.run(cnl.parse("explain f"), src)
        self.assertIn("def f(x)", outcome.output)

    def test_missing_source_refused(self):
        with self.assertRaises(planner.MissingSource):
            planner.run(cnl.parse("remove unused imports"))

    def test_unknown_intent_refused(self):
        with self.assertRaises(planner.UnknownIntent):
            planner.run(Intent.of("teleport"), "x = 1\n")


if __name__ == "__main__":
    unittest.main()
