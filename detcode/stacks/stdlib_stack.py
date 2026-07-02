"""The default stack: pure Python, standard library only (detcode's own idiom)."""
from __future__ import annotations

from ..packs import webwrap
from . import Stack

STACK = Stack(
    key="stdlib",
    title="Python (stdlib)",
    keywords=frozenset({"python", "stdlib", "wsgi"}),
    description="pure Python, standard library only — a package with a CLI and tests",
    language="python",
    dependencies=(),
    web_files=webwrap.files,
    skeleton=None,
    web_always=False,
    web_label="a stdlib WSGI web UI",
    usage=("python -m __PKG__ --help",),
    dev=("python -m unittest discover -s tests",),
)
