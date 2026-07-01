import unittest

from detcode.determinism import content_hash
from detcode.engines import synth
from detcode.engines.synth import NoSolution, SpecError


def run(source, name="f"):
    allowed = {
        "str": str, "len": len, "sorted": sorted, "sum": sum,
        "abs": abs, "max": max, "min": min, "list": list, "reversed": reversed,
    }
    ns = {"__builtins__": allowed}
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


class SynthV2Tests(unittest.TestCase):
    """The expanded DSL: conditionals, lists, booleans, constant mining."""

    def test_conditional_with_mined_branch_constants(self):
        # "yes"/"no" are whole-output strings mined into the constant pool.
        spec = {
            "examples": [
                {"in": [1], "out": "no"},
                {"in": [2], "out": "no"},
                {"in": [3], "out": "yes"},
                {"in": [5], "out": "yes"},
                {"in": [0], "out": "no"},
            ],
            "max_depth": 3,
        }
        result = synth.synthesize(spec)
        f = run(result.source)
        self.assertEqual(f(10), "yes")
        self.assertEqual(f(-4), "no")

    def test_absolute_value(self):
        spec = {
            "examples": [{"in": [-3], "out": 3}, {"in": [4], "out": 4}, {"in": [-1], "out": 1}],
            "max_depth": 3,
        }
        result = synth.synthesize(spec)
        f = run(result.source)
        self.assertEqual(f(-50), 50)
        self.assertEqual(f(7), 7)

    def test_sum_of_list(self):
        spec = {
            "examples": [
                {"in": [[1, 2, 3]], "out": 6},
                {"in": [[10]], "out": 10},
                {"in": [[]], "out": 0},
            ],
            "max_depth": 2,
        }
        result = synth.synthesize(spec)
        f = run(result.source)
        self.assertEqual(f([4, 5]), 9)

    def test_first_word(self):
        spec = {
            "examples": [
                {"in": ["hello world"], "out": "hello"},
                {"in": ["ada lovelace"], "out": "ada"},
            ],
            "max_depth": 3,
        }
        result = synth.synthesize(spec)
        f = run(result.source)
        self.assertEqual(f("grace hopper"), "grace")

    def test_join_with_mined_separator(self):
        # "," is a substring common to all outputs, so it gets mined.
        spec = {
            "examples": [
                {"in": [["a", "b"]], "out": "a,b"},
                {"in": [["x", "y", "z"]], "out": "x,y,z"},
            ],
            "max_depth": 2,
        }
        result = synth.synthesize(spec)
        f = run(result.source)
        self.assertEqual(f(["p", "q"]), "p,q")

    def test_bool_output_with_spec_constant(self):
        spec = {
            "examples": [
                {"in": ["hi"], "out": False},
                {"in": ["hello"], "out": True},
                {"in": ["hey"], "out": False},
                {"in": ["greetings"], "out": True},
            ],
            "constants": [3],
            "max_depth": 3,
        }
        result = synth.synthesize(spec)
        f = run(result.source)
        self.assertTrue(f("bonjour"))
        self.assertFalse(f("ok"))

    def test_reverse_sorted_list(self):
        spec = {
            "examples": [
                {"in": [[3, 1, 2]], "out": [3, 2, 1]},
                {"in": [[5, 4]], "out": [5, 4]},
                {"in": [[1, 9, 5]], "out": [9, 5, 1]},
            ],
            "max_depth": 3,
        }
        result = synth.synthesize(spec)
        f = run(result.source)
        self.assertEqual(f([2, 7, 1]), [7, 2, 1])

    def test_refuses_empty_lists_everywhere(self):
        spec = {"examples": [{"in": [[]], "out": 0}]}
        with self.assertRaises(SpecError):
            synth.synthesize(spec)

    def test_refuses_mixed_list(self):
        spec = {"examples": [{"in": [[1, "a"]], "out": 1}]}
        with self.assertRaises(SpecError):
            synth.synthesize(spec)

    def test_v2_deterministic(self):
        spec = {
            "examples": [{"in": [-3], "out": 3}, {"in": [4], "out": 4}],
            "max_depth": 3,
        }
        from detcode.determinism import content_hash
        hashes = {content_hash(synth.synthesize(spec).source) for _ in range(10)}
        self.assertEqual(len(hashes), 1)


if __name__ == "__main__":
    unittest.main()
