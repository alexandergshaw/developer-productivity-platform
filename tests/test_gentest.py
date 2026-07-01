import unittest

from detcode.determinism import content_hash
from detcode.engines import gentest
from detcode.engines.gentest import SpecError


SPEC = {
    "function": "area",
    "source": "def area(w, h):\n    return w * h\n",
    "examples": [{"in": [2, 3], "out": 6}, {"in": [4, 5], "out": 20}],
}


class GentestTests(unittest.TestCase):
    def test_generated_tests_run_and_pass(self):
        generated = gentest.gentest(SPEC).source
        namespace = {"__name__": "generated_tests"}
        exec(compile(generated, "<gentest>", "exec"), namespace)
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromTestCase(namespace["TestArea"])
        result = unittest.TestResult()
        suite.run(result)
        self.assertGreaterEqual(result.testsRun, 2)  # 2 examples + edge cases
        self.assertEqual(result.failures, [])
        self.assertEqual(result.errors, [])

    def test_import_mode(self):
        spec = {
            "function": "full_name",
            "module": "myapp.names",
            "examples": [{"in": ["a", "b"], "out": "a b"}],
        }
        generated = gentest.gentest(spec).source
        self.assertIn("from myapp.names import full_name", generated)
        self.assertIn("class TestFullName(", generated)
        self.assertIn("self.assertEqual(full_name('a', 'b'), 'a b')", generated)

    def test_failing_expectations_still_generate(self):
        # Generating tests that fail is valid TDD; only syntax is guaranteed.
        spec = dict(SPEC, examples=[{"in": [2, 3], "out": 999}])
        generated = gentest.gentest(spec).source
        self.assertIn("999", generated)

    def test_deterministic(self):
        hashes = {content_hash(gentest.gentest(SPEC).source) for _ in range(10)}
        self.assertEqual(len(hashes), 1)

    def test_refuses_without_source_or_module(self):
        with self.assertRaises(SpecError):
            gentest.gentest({"function": "f", "examples": [{"in": [1], "out": 1}]})

    def test_refuses_bad_module_path(self):
        spec = {
            "function": "f",
            "module": "not a module!",
            "examples": [{"in": [1], "out": 1}],
        }
        with self.assertRaises(SpecError):
            gentest.gentest(spec)


class EdgeCaseTests(unittest.TestCase):
    DIV_SPEC = {
        "function": "per_unit",
        "source": "def per_unit(total, count):\n    return total // count\n",
        "examples": [{"in": [10, 2], "out": 5}],
    }

    def test_boundary_probes_from_comparisons(self):
        spec = {
            "function": "grade",
            "source": (
                "def grade(score):\n"
                "    if score < 50:\n"
                "        return 'fail'\n"
                "    return 'pass'\n"
            ),
            "examples": [{"in": [80], "out": "pass"}],
        }
        generated = gentest.gentest(spec).source
        # 49/50/51 come from the `score < 50` comparison.
        self.assertIn("self.assertEqual(grade(49), 'fail')", generated)
        self.assertIn("self.assertEqual(grade(50), 'pass')", generated)

    def test_exception_probe_becomes_assert_raises(self):
        generated = gentest.gentest(self.DIV_SPEC).source
        # count=0 probe divides by zero — pinned as assertRaises.
        self.assertIn("with self.assertRaises(ZeroDivisionError):", generated)
        self.assertIn("per_unit(10, 0)", generated)

    def test_generated_edge_tests_pass(self):
        generated = gentest.gentest(self.DIV_SPEC).source
        namespace = {"__name__": "generated_tests"}
        exec(compile(generated, "<gentest>", "exec"), namespace)
        suite = unittest.TestLoader().loadTestsFromTestCase(namespace["TestPerUnit"])
        result = unittest.TestResult()
        suite.run(result)
        self.assertGreater(result.testsRun, 1)  # examples + edge cases
        self.assertEqual(result.failures, [])
        self.assertEqual(result.errors, [])

    def test_edge_cases_can_be_disabled(self):
        spec = dict(self.DIV_SPEC, edge_cases=False)
        generated = gentest.gentest(spec).source
        self.assertNotIn("_edge_", generated)

    def test_infinite_loop_probe_is_discarded(self):
        # n=-1 would loop forever; the line budget discards it deterministically.
        spec = {
            "function": "countdown",
            "source": (
                "def countdown(n):\n"
                "    while n != 0:\n"
                "        n -= 1\n"
                "    return 'done'\n"
            ),
            "examples": [{"in": [3], "out": "done"}],
        }
        generated = gentest.gentest(spec).source
        self.assertNotIn("countdown(-1)", generated)
        self.assertIn("countdown(0)", generated)  # safe probe survives

    def test_deterministic_with_edges(self):
        hashes = {content_hash(gentest.gentest(self.DIV_SPEC).source) for _ in range(10)}
        self.assertEqual(len(hashes), 1)


if __name__ == "__main__":
    unittest.main()
