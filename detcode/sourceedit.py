"""Span-based source editing.

Codemods here never reformat the whole file. They compute a set of exact text
spans to replace (from AST node positions) and splice them into the original
bytes. Comments, blank lines, and formatting outside the edited spans are
preserved exactly.

Offsets follow CPython AST conventions: ``col_offset`` is a UTF-8 *byte* offset
within its line, so we splice on the encoded bytes to stay correct for
non-ASCII identifiers.
"""
from __future__ import annotations

from dataclasses import dataclass


class OverlappingEdits(ValueError):
    """Two edits target overlapping spans; applying them would corrupt output."""


@dataclass(frozen=True)
class TextEdit:
    lineno: int  # 1-based
    col: int  # 0-based UTF-8 byte offset within the line
    end_lineno: int
    end_col: int
    new_text: str

    def sort_key(self) -> tuple:
        """Canonical ordering for deterministic tie-breaking."""
        return (self.lineno, self.col, self.end_lineno, self.end_col, self.new_text)


def _line_start_offsets(source: str) -> list[int]:
    """Byte offset at which each line begins; index ``n`` is line ``n+1``.

    A final sentinel entry marks the end of the source so a span ending on the
    last line can be resolved with the same arithmetic as any other.
    """
    offsets = [0]
    acc = 0
    for line in source.splitlines(keepends=True):
        acc += len(line.encode("utf-8"))
        offsets.append(acc)
    return offsets


def apply_edits(source: str, edits: list[TextEdit]) -> str:
    """Apply ``edits`` to ``source`` deterministically.

    Edits are ordered canonically, checked for overlap (refused rather than
    silently corrupting), and spliced right-to-left so earlier byte offsets stay
    valid as later ones are replaced.
    """
    if not edits:
        return source

    line_starts = _line_start_offsets(source)
    data = source.encode("utf-8")

    resolved = []
    for edit in sorted(edits, key=TextEdit.sort_key):
        start = line_starts[edit.lineno - 1] + edit.col
        end = line_starts[edit.end_lineno - 1] + edit.end_col
        if end < start:
            raise ValueError("edit end precedes its start")
        resolved.append((start, end, edit.new_text.encode("utf-8")))

    for (a_start, a_end, _a), (b_start, _b_end, _b) in zip(resolved, resolved[1:]):
        if b_start < a_end:
            raise OverlappingEdits("edits overlap; refusing to apply")

    for start, end, repl in reversed(resolved):
        data = data[:start] + repl + data[end:]
    return data.decode("utf-8")
