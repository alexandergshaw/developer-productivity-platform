"""The determinism gate.

For every codemod we assert two properties that together define what
"deterministic" means here:

1. Reproducibility: running the same transform on the same input many times
   yields byte-identical output (same content hash).
2. Idempotency: applying the transform to its own output is a no-op.
"""
import textwrap
import unittest

from detcode.determinism import content_hash
from detcode.sourceedit import OverlappingEdits, TextEdit, apply_edits
from detcode.engines import rewrite


def dedent(s: str) -> str:
    return textwrap.dedent(s).lstrip("\n")


RENAME_CASE = dedent(
    """
    def f(a):
        total = a
        total = total + 1
        return total
    """
)

IMPORTS_CASE = dedent(
    """
    import os
    import sys
    from typing import List, Dict

    def f() -> List[int]:
        print(sys.argv)
        return []
    """
)


class ReproducibilityTests(unittest.TestCase):
    def test_rename_reproducible(self):
        hashes = {
            content_hash(rewrite.rename_local(RENAME_CASE, "f", "total", "acc").source)
            for _ in range(20)
        }
        self.assertEqual(len(hashes), 1)

    def test_imports_reproducible(self):
        hashes = {
            content_hash(rewrite.remove_unused_imports(IMPORTS_CASE).source)
            for _ in range(20)
        }
        self.assertEqual(len(hashes), 1)


class IdempotencyTests(unittest.TestCase):
    def test_rename_roundtrips(self):
        # A rename must be reversible: total->acc->total restores the original
        # bytes exactly, which exercises span editing in both directions.
        once = rewrite.rename_local(RENAME_CASE, "f", "total", "acc").source
        back = rewrite.rename_local(once, "f", "acc", "total").source
        self.assertEqual(RENAME_CASE, back)

    def test_remove_imports_idempotent(self):
        once = rewrite.remove_unused_imports(IMPORTS_CASE).source
        twice = rewrite.remove_unused_imports(once)
        self.assertEqual(once, twice.source)
        self.assertFalse(twice.changed)


class ApplyEditsTests(unittest.TestCase):
    def test_overlap_is_refused(self):
        src = "abcdef\n"
        edits = [
            TextEdit(1, 0, 1, 3, "X"),
            TextEdit(1, 2, 1, 5, "Y"),
        ]
        with self.assertRaises(OverlappingEdits):
            apply_edits(src, edits)

    def test_unicode_offsets(self):
        # col_offset is a UTF-8 byte offset; a multi-byte char precedes the edit.
        src = "é = 1\nvalue = é\n"
        # Rename via raw edits: replace the 'value' name on line 2.
        edits = [TextEdit(2, 0, 2, 5, "result")]
        out = apply_edits(src, edits)
        self.assertEqual(out, "é = 1\nresult = é\n")


if __name__ == "__main__":
    unittest.main()
