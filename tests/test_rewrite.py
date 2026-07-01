import textwrap
import unittest

from detcode.engines import rewrite
from detcode.engines.rewrite import Unsafe
from detcode.verify import parses


def dedent(s: str) -> str:
    return textwrap.dedent(s).lstrip("\n")


class RenameLocalTests(unittest.TestCase):
    def test_renames_local_and_preserves_formatting(self):
        src = dedent(
            """
            import os  # keep this comment


            def f(a):
                total = a + 1   # trailing comment
                total += 2
                return total
            """
        )
        result = rewrite.rename_local(src, "f", "total", "acc")
        self.assertTrue(result.changed)
        self.assertIn("acc = a + 1   # trailing comment", result.source)
        self.assertIn("acc += 2", result.source)
        self.assertIn("return acc", result.source)
        # Untouched lines are byte-identical.
        self.assertIn("import os  # keep this comment", result.source)
        self.assertEqual(result.report["occurrences"], 3)
        self.assertTrue(parses(result.source))

    def test_does_not_touch_same_name_in_other_function(self):
        src = dedent(
            """
            def f():
                x = 1
                return x

            def g():
                x = 2
                return x
            """
        )
        result = rewrite.rename_local(src, "f", "x", "y")
        self.assertIn("y = 1", result.source)
        self.assertIn("x = 2", result.source)  # g() untouched

    def test_refuses_parameter_rename(self):
        src = "def f(a):\n    return a\n"
        with self.assertRaises(Unsafe):
            rewrite.rename_local(src, "f", "a", "b")

    def test_refuses_when_not_a_local(self):
        src = "def f():\n    return g\n"
        with self.assertRaises(Unsafe):
            rewrite.rename_local(src, "f", "g", "h")

    def test_refuses_collision(self):
        src = "def f():\n    x = 1\n    y = 2\n    return x + y\n"
        with self.assertRaises(Unsafe):
            rewrite.rename_local(src, "f", "x", "y")

    def test_refuses_nested_scope_usage(self):
        src = dedent(
            """
            def f():
                x = 1
                def inner():
                    return x
                return inner()
            """
        )
        with self.assertRaises(Unsafe):
            rewrite.rename_local(src, "f", "x", "z")

    def test_refuses_global_declared(self):
        src = "def f():\n    global x\n    x = 1\n    return x\n"
        with self.assertRaises(Unsafe):
            rewrite.rename_local(src, "f", "x", "y")

    def test_refuses_ambiguous_function(self):
        src = "def f():\n    x=1\n    return x\n\ndef f():\n    x=2\n    return x\n"
        with self.assertRaises(Unsafe):
            rewrite.rename_local(src, "f", "x", "y")

    def test_does_not_rename_attribute_of_same_name(self):
        src = dedent(
            """
            def f(obj):
                value = obj.value
                return value
            """
        )
        result = rewrite.rename_local(src, "f", "value", "v")
        self.assertIn("v = obj.value", result.source)  # attribute untouched


class RemoveUnusedImportsTests(unittest.TestCase):
    def test_removes_whole_unused_import(self):
        src = dedent(
            """
            import os
            import sys

            print(sys.argv)
            """
        )
        result = rewrite.remove_unused_imports(src)
        self.assertNotIn("import os", result.source)
        self.assertIn("import sys", result.source)
        self.assertIn("print(sys.argv)", result.source)
        self.assertEqual(result.report["removed"], ["os"])

    def test_removes_only_unused_names_from_from_import(self):
        src = dedent(
            """
            from typing import List, Dict, Optional

            x: List[int] = []
            y: Optional[int] = None
            """
        )
        result = rewrite.remove_unused_imports(src)
        self.assertIn("List", result.source)
        self.assertIn("Optional", result.source)
        self.assertNotIn("Dict", result.source)

    def test_keeps_future_import(self):
        src = "from __future__ import annotations\n\nx = 1\n"
        result = rewrite.remove_unused_imports(src)
        self.assertIn("from __future__ import annotations", result.source)

    def test_keeps_star_import(self):
        src = "from os import *\n\nx = 1\n"
        result = rewrite.remove_unused_imports(src)
        self.assertIn("from os import *", result.source)

    def test_all_counts_as_usage(self):
        src = dedent(
            """
            import os

            __all__ = ["os"]
            """
        )
        result = rewrite.remove_unused_imports(src)
        self.assertIn("import os", result.source)

    def test_asname_binding(self):
        src = "import numpy as np\nimport os\n\nprint(np)\n"
        result = rewrite.remove_unused_imports(src)
        self.assertIn("import numpy as np", result.source)
        self.assertNotIn("import os", result.source)


if __name__ == "__main__":
    unittest.main()
