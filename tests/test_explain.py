import textwrap
import unittest

from detcode.engines import explain
from detcode.engines.explain import ExplainError


def dedent(s: str) -> str:
    return textwrap.dedent(s).lstrip("\n")


SOURCE = dedent(
    '''
    """Order utilities."""
    import os
    from typing import List


    class Order:
        def total(self):
            return 1

        def cancel(self):
            pass


    def apply_discount(price: int, percent: int = 10) -> int:
        """Apply a percentage discount to a price."""
        if percent < 0 or percent > 100:
            raise ValueError("bad percent")
        for _ in range(1):
            pass
        return price - price * percent // 100
    '''
)


class ExplainFunctionTests(unittest.TestCase):
    def test_explains_function(self):
        text = explain.explain(SOURCE, "apply_discount").text
        self.assertIn("def apply_discount(price: int, percent: int=10) -> int", text)
        self.assertIn('Docstring: "Apply a percentage discount to a price."', text)
        self.assertIn("1 branch", text)
        self.assertIn("1 loop", text)
        self.assertIn("Raises: ValueError.", text)
        self.assertIn("Calls: ValueError(), range().", text)
        self.assertIn("1 return statement", text)

    def test_reports_missing_docstring(self):
        text = explain.explain("def f(x):\n    return x\n", "f").text
        self.assertIn("No docstring.", text)
        self.assertIn("straight-line", text)

    def test_detects_generator(self):
        src = "def gen(n):\n    for i in range(n):\n        yield i\n"
        text = explain.explain(src, "gen").text
        self.assertIn("generator", text)

    def test_complexity_counts_branch_points(self):
        text = explain.explain(SOURCE, "apply_discount").text
        # 1 base + if + or + for = 4
        self.assertIn("complexity 4", text)

    def test_refuses_unknown_function(self):
        with self.assertRaises(ExplainError):
            explain.explain(SOURCE, "nope")

    def test_refuses_ambiguous_function(self):
        src = "def f():\n    pass\n\ndef f():\n    pass\n"
        with self.assertRaises(ExplainError):
            explain.explain(src, "f")


class ExplainModuleTests(unittest.TestCase):
    def test_explains_module(self):
        text = explain.explain(SOURCE).text
        self.assertIn('Docstring: "Order utilities."', text)
        self.assertIn("Imports: List, os.", text)
        self.assertIn("Class Order: 2 methods (total, cancel).", text)
        self.assertIn("Function def apply_discount", text)

    def test_deterministic(self):
        texts = {explain.explain(SOURCE).text for _ in range(10)}
        self.assertEqual(len(texts), 1)


if __name__ == "__main__":
    unittest.main()
