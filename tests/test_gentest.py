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
        self.assertEqual(result.testsRun, 2)
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


if __name__ == "__main__":
    unittest.main()
