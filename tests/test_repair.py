import textwrap
import unittest

from detcode.determinism import content_hash
from detcode.engines import repair
from detcode.engines.repair import NoRepair, SpecError


def dedent(s: str) -> str:
    return textwrap.dedent(s).lstrip("\n")


def run(source, name):
    ns: dict = {}
    exec(compile(source, "<t>", "exec"), ns)
    return ns[name]


class RepairTests(unittest.TestCase):
    def test_fixes_wrong_operator(self):
        src = dedent(
            """
            def area(w, h):
                return w + h
            """
        )
        spec = {
            "function": "area",
            "examples": [
                {"in": [2, 3], "out": 6},
                {"in": [4, 5], "out": 20},
                {"in": [1, 10], "out": 10},
            ],
        }
        result = repair.repair(src, spec)
        self.assertTrue(result.changed)
        self.assertEqual(run(result.source, "area")(6, 7), 42)
        self.assertEqual(result.report["status"], "repaired")

    def test_fixes_off_by_one_comparison(self):
        # Inclusive upper bound bug: should be <=.
        src = dedent(
            """
            def count_upto(n):
                total = 0
                for i in range(100):
                    if i < n:
                        total = total + 1
                return total
            """
        )
        spec = {
            "function": "count_upto",
            "examples": [
                {"in": [3], "out": 4},   # 0,1,2,3
                {"in": [5], "out": 6},
                {"in": [0], "out": 1},
            ],
        }
        result = repair.repair(src, spec)
        self.assertTrue(result.changed)
        self.assertEqual(run(result.source, "count_upto")(10), 11)

    def test_fixes_constant_off_by_one(self):
        src = dedent(
            """
            def next_id(x):
                return x + 2
            """
        )
        spec = {
            "function": "next_id",
            "examples": [{"in": [1], "out": 2}, {"in": [5], "out": 6}],
        }
        result = repair.repair(src, spec)
        self.assertTrue(result.changed)
        self.assertEqual(run(result.source, "next_id")(41), 42)

    def test_already_passing_is_noop(self):
        src = "def f(x):\n    return x + 1\n"
        spec = {"function": "f", "examples": [{"in": [1], "out": 2}]}
        result = repair.repair(src, spec)
        self.assertFalse(result.changed)
        self.assertEqual(result.source, src)
        self.assertEqual(result.report["status"], "already-passing")

    def test_preserves_formatting_and_comments(self):
        src = dedent(
            """
            def area(w, h):
                # compute the area
                return w + h  # bug here
            """
        )
        spec = {"function": "area", "examples": [{"in": [2, 3], "out": 6}, {"in": [3, 3], "out": 9}]}
        result = repair.repair(src, spec)
        self.assertIn("# compute the area", result.source)
        self.assertIn("# bug here", result.source)

    def test_two_edit_repair(self):
        # Two independent bugs: wrong op and wrong constant.
        src = dedent(
            """
            def f(x):
                return (x - 1) + 5
            """
        )
        # Want x*2: needs '-'->'*' ... not expressible; instead target (x + 1) + 0? Keep simple:
        # want (x + 1): fix '-'->'+' and '5'->0.
        spec = {
            "function": "f",
            "examples": [{"in": [10], "out": 11}, {"in": [3], "out": 4}, {"in": [0], "out": 1}],
            "max_edits": 2,
        }
        result = repair.repair(src, spec)
        self.assertTrue(result.changed)
        self.assertEqual(run(result.source, "f")(100), 101)
        self.assertEqual(result.report["edits"], 2)

    def test_refuses_when_unfixable(self):
        src = "def f(x):\n    return x + 1\n"
        spec = {
            "function": "f",
            "examples": [{"in": [1], "out": 99}],  # no single token edit yields this
            "max_edits": 1,
        }
        with self.assertRaises(NoRepair):
            repair.repair(src, spec)

    def test_deterministic(self):
        src = "def area(w, h):\n    return w + h\n"
        spec = {"function": "area", "examples": [{"in": [2, 3], "out": 6}, {"in": [4, 5], "out": 20}]}
        hashes = {content_hash(repair.repair(src, spec).source) for _ in range(15)}
        self.assertEqual(len(hashes), 1)

    def test_refuses_missing_function(self):
        src = "def f(x):\n    return x\n"
        with self.assertRaises(SpecError):
            repair.repair(src, {"function": "nope", "examples": [{"in": [1], "out": 1}]})


if __name__ == "__main__":
    unittest.main()
