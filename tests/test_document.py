import textwrap
import unittest

from detcode import cnl, planner
from detcode.determinism import content_hash
from detcode.engines import document
from detcode.engines.document import DocError


def dedent(s: str) -> str:
    return textwrap.dedent(s).lstrip("\n")


class DocstringContentTests(unittest.TestCase):
    def test_full_docstring_with_args_returns_raises(self):
        src = dedent(
            """
            def apply_discount(price: int, percent: int = 10) -> int:
                if percent < 0:
                    raise ValueError("bad percent")
                return price - price * percent // 100
            """
        )
        result = document.add_docstrings(src, "apply_discount")
        self.assertIn('"""Apply discount.', result.source)
        self.assertIn("Args:", result.source)
        self.assertIn("price (int): The price.", result.source)
        self.assertIn("percent (int, default 10): The percent.", result.source)
        self.assertIn("Returns:\n        int.", result.source)
        self.assertIn("Raises:", result.source)
        self.assertIn("ValueError:", result.source)
        # The inserted docstring is a real docstring per the AST.
        import ast
        tree = ast.parse(result.source)
        fn = tree.body[0]
        self.assertTrue(ast.get_docstring(fn).startswith("Apply discount."))

    def test_prefix_heuristics(self):
        src = "def is_valid(x):\n    return True\n"
        result = document.add_docstrings(src, "is_valid")
        self.assertIn('"""Return whether valid.', result.source)

    def test_generator_gets_yields(self):
        src = "def numbers(n):\n    for i in range(n):\n        yield i\n"
        result = document.add_docstrings(src, "numbers")
        self.assertIn("Yields:", result.source)

    def test_no_args_single_line(self):
        src = "def reset():\n    pass\n"
        result = document.add_docstrings(src, "reset")
        self.assertIn('"""Reset."""', result.source)

    def test_body_and_formatting_preserved(self):
        src = dedent(
            """
            def compute(a):
                total = a + 1  # keep me
                return total
            """
        )
        result = document.add_docstrings(src, "compute")
        self.assertIn("total = a + 1  # keep me", result.source)


class DocstringModeTests(unittest.TestCase):
    def test_all_mode_skips_documented_and_is_idempotent(self):
        src = dedent(
            '''
            def documented():
                """Already fine."""
                return 1


            def bare(x):
                return x
            '''
        )
        once = document.add_docstrings(src)
        self.assertEqual(once.report["documented"], ["bare"])
        self.assertIn('"""Already fine."""', once.source)
        twice = document.add_docstrings(once.source)
        self.assertFalse(twice.changed)
        self.assertEqual(once.source, twice.source)

    def test_targeted_refuses_existing_docstring(self):
        src = 'def f():\n    """Done."""\n    return 1\n'
        with self.assertRaises(DocError):
            document.add_docstrings(src, "f")

    def test_refuses_unknown_function(self):
        with self.assertRaises(DocError):
            document.add_docstrings("def f():\n    pass\n", "nope")

    def test_deterministic(self):
        src = "def apply_discount(price, percent=10):\n    return price\n"
        hashes = {
            content_hash(document.add_docstrings(src).source) for _ in range(10)
        }
        self.assertEqual(len(hashes), 1)

    def test_english_command_path(self):
        src = "def apply_discount(price):\n    return price\n"
        outcome = planner.run(cnl.parse("add a docstring to apply_discount"), src)
        self.assertIn('"""Apply discount.', outcome.new_source)
        outcome2 = planner.run(cnl.parse("document"), src)
        self.assertIn('"""Apply discount.', outcome2.new_source)


if __name__ == "__main__":
    unittest.main()
