import unittest

from detcode.determinism import content_hash
from detcode.engines import synth
from detcode.engines.synth import NoSolution, SpecError


def run(source, name="f"):
    ns = {"__builtins__": {"str": str, "len": len}}
    exec(compile(source, "<t>", "exec"), ns)
    return ns[name]


class SynthTests(unittest.TestCase):
    def test_addition(self):
        spec = {
            "examples": [
                {"in": [2, 3], "out": 5},
                {"in": [10, 1], "out": 11},
                {"in": [0, 0], "out": 0},
            ]
        }
        result = synth.synthesize(spec)
        f = run(result.source)
        self.assertEqual(f(7, 8), 15)

    def test_increment(self):
        spec = {"examples": [{"in": [1], "out": 2}, {"in": [5], "out": 6}, {"in": [9], "out": 10}]}
        result = synth.synthesize(spec)
        f = run(result.source)
        self.assertEqual(f(100), 101)

    def test_full_name_from_two_strings(self):
        spec = {
            "examples": [
                {"in": ["ada", "lovelace"], "out": "ada lovelace"},
                {"in": ["alan", "turing"], "out": "alan turing"},
            ]
        }
        result = synth.synthesize(spec)
        f = run(result.source)
        self.assertEqual(f("grace", "hopper"), "grace hopper")

    def test_uppercase(self):
        spec = {"examples": [{"in": ["abc"], "out": "ABC"}, {"in": ["xy"], "out": "XY"}]}
        result = synth.synthesize(spec)
        f = run(result.source)
        self.assertEqual(f("hello"), "HELLO")

    def test_length_bridges_str_to_int(self):
        spec = {"examples": [{"in": ["ab"], "out": 2}, {"in": ["hello"], "out": 5}, {"in": [""], "out": 0}]}
        result = synth.synthesize(spec)
        f = run(result.source)
        self.assertEqual(f("abcd"), 4)

    def test_custom_name(self):
        spec = {"name": "add", "examples": [{"in": [1, 2], "out": 3}, {"in": [4, 5], "out": 9}]}
        result = synth.synthesize(spec)
        self.assertTrue(result.source.startswith("def add("))
        self.assertEqual(run(result.source, "add")(10, 20), 30)

    def test_deterministic(self):
        spec = {"examples": [{"in": [2, 3], "out": 5}, {"in": [10, 1], "out": 11}]}
        hashes = {content_hash(synth.synthesize(spec).source) for _ in range(15)}
        self.assertEqual(len(hashes), 1)

    def test_no_solution_refused(self):
        # No component composition maps these under the tiny DSL/budget.
        spec = {
            "examples": [{"in": [1], "out": 1000000}, {"in": [2], "out": 999999}],
            "max_depth": 2,
        }
        with self.assertRaises(NoSolution):
            synth.synthesize(spec)

    def test_refuses_inconsistent_arity(self):
        spec = {"examples": [{"in": [1], "out": 1}, {"in": [1, 2], "out": 3}]}
        with self.assertRaises(SpecError):
            synth.synthesize(spec)

    def test_refuses_unsupported_type(self):
        spec = {"examples": [{"in": [1.5], "out": 2.5}]}
        with self.assertRaises(SpecError):
            synth.synthesize(spec)

    def test_report_has_provenance(self):
        spec = {"examples": [{"in": [1], "out": 2}, {"in": [3], "out": 4}]}
        result = synth.synthesize(spec)
        self.assertEqual(result.report["rule"], "synthesize")
        self.assertIn("expr", result.report)
        self.assertIn("ops_used", result.report)


if __name__ == "__main__":
    unittest.main()
