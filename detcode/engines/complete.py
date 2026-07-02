"""Deterministic autocomplete.

What IntelliSense does statistically, this does with fixed sources and fixed
ranking. For a prefix, candidates come from (in priority order):

1. the retrieval corpora — built-in and taught functions. Accepting one can
   insert the WHOLE verified implementation, provenance included
2. identifiers already in the buffer (word-based, tokenizer-tolerant: the
   buffer rarely parses mid-keystroke)
3. Python keywords
4. builtins

Within each group, alphabetical. Same buffer + prefix + corpus, same
suggestions, byte for byte.
"""
from __future__ import annotations

import ast
import builtins
import keyword
import re

from .retrieve import CORPUS, Entry

MAX_ITEMS = 12
_WORD = re.compile(r"[A-Za-z_]\w{2,}")

_KEYWORDS = tuple(sorted(keyword.kwlist + ["self", "cls"]))
_BUILTINS = tuple(sorted(n for n in dir(builtins) if not n.startswith("_")))


def _docline(source: str, name: str) -> str:
    try:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                doc = ast.get_docstring(node)
                return doc.splitlines()[0] if doc else ""
    except SyntaxError:
        pass
    return ""


def complete(source: str, prefix: str, extra: tuple[Entry, ...] = ()) -> list[dict]:
    """Ranked completion items for ``prefix`` in ``source``."""
    if not prefix or not (prefix[0].isalpha() or prefix[0] == "_"):
        return []

    items: list[dict] = []
    seen: set[str] = set()

    def add(label: str, kind: str, insert: str, detail: str = "") -> None:
        if label == prefix or label in seen or not label.startswith(prefix):
            return
        seen.add(label)
        items.append({"label": label, "kind": kind, "insert": insert, "detail": detail})

    for entry in tuple(CORPUS) + tuple(extra):
        add(
            entry.name,
            "corpus",
            entry.source.strip("\n") + "\n",
            _docline(entry.source, entry.name),
        )

    buffer_words = sorted(set(_WORD.findall(source)))
    for word in buffer_words:
        if not keyword.iskeyword(word):
            add(word, "identifier", word)

    for word in _KEYWORDS:
        add(word, "keyword", word)
    for word in _BUILTINS:
        add(word, "builtin", word)

    priority = {"corpus": 0, "identifier": 1, "keyword": 2, "builtin": 3}
    items.sort(key=lambda i: (priority[i["kind"]], i["label"]))
    return items[:MAX_ITEMS]
