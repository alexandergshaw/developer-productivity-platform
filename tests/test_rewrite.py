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


class SortImportsTests(unittest.TestCase):
    def test_groups_and_alphabetizes(self):
        src = dedent(
            """
            import sys
            import requests
            from __future__ import annotations
            import os

            print(os, sys, requests)
            """
        )
        result = rewrite.sort_imports(src)
        expected = dedent(
            """
            from __future__ import annotations

            import os
            import sys

            import requests

            print(os, sys, requests)
            """
        )
        self.assertEqual(result.source, expected)
        self.assertTrue(result.changed)

    def test_splits_multi_imports_and_sorts_from_names(self):
        src = "import sys, os\nfrom typing import Optional, Any\n\nprint(os, sys, Any, Optional)\n"
        result = rewrite.sort_imports(src)
        self.assertIn("import os\nimport sys", result.source)
        self.assertIn("from typing import Any, Optional", result.source)

    def test_docstring_stays_on_top(self):
        src = '"""Module doc."""\nimport sys\nimport os\n\nprint(os, sys)\n'
        result = rewrite.sort_imports(src)
        self.assertTrue(result.source.startswith('"""Module doc."""\nimport os\nimport sys'))

    def test_refuses_comments_in_block(self):
        src = "import sys\n# needed for legacy reasons\nimport os\n\nprint(os, sys)\n"
        with self.assertRaises(Unsafe):
            rewrite.sort_imports(src)

    def test_idempotent(self):
        src = "import sys\nimport os\n\nprint(os, sys)\n"
        once = rewrite.sort_imports(src)
        twice = rewrite.sort_imports(once.source)
        self.assertEqual(once.source, twice.source)
        self.assertFalse(twice.changed)

    def test_single_import_untouched(self):
        src = "import os\n\nprint(os)\n"
        result = rewrite.sort_imports(src)
        self.assertFalse(result.changed)


class CleanupTests(unittest.TestCase):
    def test_cleanup_composite_via_english(self):
        from detcode import cnl, planner
        src = dedent(
            """
            import sys
            import unused_thing
            import os

            print(os.sep, sys.argv)
            """
        )
        outcome = planner.run(cnl.parse("clean up"), src)
        self.assertNotIn("unused_thing", outcome.new_source)
        self.assertTrue(outcome.new_source.startswith("import os\nimport sys"))
        self.assertEqual(outcome.report["rule"], "cleanup")

    def test_tidy_synonym(self):
        from detcode import cnl
        self.assertEqual(cnl.parse("tidy up this file").operation, "cleanup")
        self.assertEqual(cnl.parse("sort the imports").operation, "sort-imports")


if __name__ == "__main__":
    unittest.main()
