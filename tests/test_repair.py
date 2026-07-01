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


class RepairV2Tests(unittest.TestCase):
    """Fault localization + the widened mutation space."""

    def test_fixes_wrong_variable_bug(self):
        src = dedent(
            """
            def area(w, h):
                return w * w
            """
        )
        spec = {
            "function": "area",
            "examples": [{"in": [2, 3], "out": 6}, {"in": [4, 5], "out": 20}],
        }
        result = repair.repair(src, spec)
        self.assertTrue(result.changed)
        self.assertEqual(run(result.source, "area")(6, 7), 42)

    def test_fixes_boolean_literal(self):
        src = dedent(
            """
            def found(items, target):
                hit = True
                for item in items:
                    if item == target:
                        hit = True
                return hit
            """
        )
        spec = {
            "function": "found",
            "examples": [
                {"in": [[1, 2], 2], "out": True},
                {"in": [[1, 2], 9], "out": False},
                {"in": [[], 1], "out": False},
            ],
        }
        result = repair.repair(src, spec)
        self.assertTrue(result.changed)
        f = run(result.source, "found")
        self.assertTrue(f([5, 6], 5))
        self.assertFalse(f([5, 6], 7))

    def test_fixes_augmented_assignment(self):
        src = dedent(
            """
            def total(items):
                acc = 0
                for item in items:
                    acc -= item
                return acc
            """
        )
        spec = {
            "function": "total",
            "examples": [{"in": [[1, 2, 3]], "out": 6}, {"in": [[]], "out": 0}],
        }
        result = repair.repair(src, spec)
        self.assertTrue(result.changed)
        self.assertEqual(run(result.source, "total")([10, 5]), 15)

    def test_never_mutates_function_name(self):
        # The only NAME token equal to the function name is the def itself;
        # a repair must never rename the function to make tests "pass".
        src = "def f(x):\n    return x + 2\n"
        spec = {"function": "f", "examples": [{"in": [1], "out": 2}]}
        result = repair.repair(src, spec)
        self.assertIn("def f(", result.source)

    def test_localization_finds_bug_among_many_sites(self):
        # A larger function with plenty of mutable tokens; only one line is
        # wrong. Localization should still converge (and deterministically).
        src = dedent(
            """
            def stats(a, b, c):
                low = min(a, b)
                low = min(low, c)
                high = max(a, b)
                high = max(high, c)
                spread = high + low
                return spread
            """
        )
        spec = {
            "function": "stats",
            "examples": [
                {"in": [1, 5, 3], "out": 4},
                {"in": [10, 2, 6], "out": 8},
                {"in": [7, 7, 7], "out": 0},
            ],
        }
        result = repair.repair(src, spec)
        self.assertTrue(result.changed)
        self.assertEqual(run(result.source, "stats")(1, 9, 4), 8)

    def test_v2_deterministic(self):
        src = "def area(w, h):\n    return w * w\n"
        spec = {
            "function": "area",
            "examples": [{"in": [2, 3], "out": 6}, {"in": [4, 5], "out": 20}],
        }
        from detcode.determinism import content_hash
        hashes = {content_hash(repair.repair(src, spec).source) for _ in range(10)}
        self.assertEqual(len(hashes), 1)


if __name__ == "__main__":
    unittest.main()
