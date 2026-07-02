import json
import shutil
import unittest

from detcode.engines import builder, plan
from detcode.engines.builder import BuildError
from tests.test_builder import materialize, run_generated_tests


FILLED_PLAN = {
    "detcode_plan": 1,
    "direction": "a text metrics helper",
    "name": "text_metrics",
    "functions": [
        {  # derivable via retrieval (corpus: count_words)
            "name": "count_words",
            "description": "number of words in a string",
            "examples": [{"in": ["one two three"], "out": 3}, {"in": [""], "out": 0}],
        },
        {  # derivable via synthesis
            "name": "shout",
            "description": "uppercase the input",
            "examples": [{"in": ["hi"], "out": "HI"}, {"in": ["ok"], "out": "OK"}],
        },
        {  # not derivable: becomes a stub with intent tests
            "name": "readability_score",
            "description": "Flesch-style readability score",
            "examples": [{"in": ["The cat sat."], "out": 116}],
            "budget": 20000,  # bound the doomed search; plans may cap per function
        },
    ],
}


class PlanModeTests(unittest.TestCase):
    def test_plan_has_interview_and_fillable_skeleton(self):
        result = plan.make_plan("a citation formatter")
        self.assertIn("What does Citation Formatter take as input?", result.questions)
        self.assertIn("examples ARE the spec", result.questions)
        self.assertEqual(result.plan["name"], "citation_formatter")
        self.assertEqual(result.plan["detcode_plan"], 1)
        self.assertTrue(result.plan["functions"][0]["examples"])
        json.loads(result.plan_text)  # round-trips

    def test_plan_notes_when_a_pack_already_matches(self):
        result = plan.make_plan("a resume tailorer")
        self.assertIn("already matches", " ".join(result.plan["notes"]))

    def test_plan_deterministic(self):
        texts = {plan.make_plan("a citation formatter").plan_text for _ in range(5)}
        self.assertEqual(len(texts), 1)

    def test_refuses_empty_direction(self):
        with self.assertRaises(BuildError):
            plan.make_plan("  ")


class BuildFromPlanTests(unittest.TestCase):
    def test_derives_what_examples_pin_down(self):
        project = builder.build_from_plan(FILLED_PLAN)
        self.assertEqual(project.report["solved"], ["count_words", "shout"])
        self.assertEqual(project.report["unsolved"], ["readability_score"])
        core = next(f for f in project.files if f.path == "text_metrics/core.py")
        self.assertIn("def count_words(", core.content)  # real corpus implementation
        self.assertIn("s.split()", core.content)
        self.assertIn("def shout(x):", core.content)  # synthesized
        self.assertIn("NotImplementedError", core.content)  # the stub

    def test_intent_tests_keep_suite_green_but_documented(self):
        project = builder.build_from_plan(FILLED_PLAN)
        tests = next(f for f in project.files if f.path == "tests/test_text_metrics.py")
        self.assertIn("@unittest.expectedFailure", tests.content)
        self.assertIn("test_readability_score_intent_0", tests.content)
        root = materialize(project)
        try:
            result = run_generated_tests(root, "text_metrics")
            self.assertGreaterEqual(result.testsRun, 5)
            self.assertEqual(result.failures, [])
            self.assertEqual(result.errors, [])
            self.assertEqual(len(result.expectedFailures), 1)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_generated_cli_calls_core(self):
        import io
        import sys as _sys
        from contextlib import redirect_stdout

        project = builder.build_from_plan(FILLED_PLAN)
        root = materialize(project)
        _sys.path.insert(0, root)
        try:
            import importlib

            cli = importlib.import_module("text_metrics.cli")
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = cli.main(["call", "shout", "'hello'"])
            self.assertEqual(code, 0)
            self.assertEqual(buffer.getvalue().strip(), "'HELLO'")
        finally:
            _sys.path.remove(root)
            for mod in [m for m in list(_sys.modules) if m.startswith("text_metrics")]:
                del _sys.modules[mod]
            shutil.rmtree(root, ignore_errors=True)

    def test_refuses_bad_plans(self):
        with self.assertRaises(BuildError):
            builder.build_from_plan({"name": "x"})
        with self.assertRaises(BuildError):
            builder.build_from_plan({"detcode_plan": 1, "name": "x", "functions": []})
        with self.assertRaises(BuildError):
            builder.build_from_plan(
                {"detcode_plan": 1, "name": "x", "functions": [{"name": "not a name"}]}
            )


class PlanWiringTests(unittest.TestCase):
    def test_english_plan_command(self):
        from detcode import cnl, planner

        outcome = planner.run(cnl.parse("plan a citation formatter"))
        self.assertIn("citation_formatter.plan.json", outcome.files)
        self.assertIn("What does Citation Formatter take as input?", outcome.output)

    def test_service_plan_and_build_from_plan(self):
        from detcode.service import run_request

        resp = run_request({"tool": "plan", "direction": "a citation formatter"})
        self.assertTrue(resp["ok"])
        self.assertIn("citation_formatter.plan.json", resp["files"])

        built = run_request({"tool": "new", "plan": FILLED_PLAN})
        self.assertTrue(built["ok"])
        self.assertIn("text_metrics/core.py", built["files"])
        self.assertEqual(built["report"]["origin"], "plan")


if __name__ == "__main__":
    unittest.main()
