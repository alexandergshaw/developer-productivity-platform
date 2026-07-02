"""SQLite persistence — detcode's memory.

The database is *state*, never output: everything read out of it is
re-verified (corpus entries against their examples, packs against their
content hash) before it can influence generation, and all exports are
canonical JSON — byte-identical for identical content. sqlite3 is stdlib,
so the zero-dependency guarantee holds.

Tables:
- corpus: taught functions (name, arity, source, examples)
- packs:  minted project packs (key, files as JSON, content hash)
- audit:  what was taught/minted/imported and when (metadata only)
"""
from __future__ import annotations

import json
import os
import sqlite3
import time

from .determinism import canonical_json, content_hash

DEFAULT_DB_PATH = os.path.join(".detcode", "detcode.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS corpus (
    name     TEXT PRIMARY KEY,
    arity    INTEGER NOT NULL,
    source   TEXT NOT NULL,
    examples TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS packs (
    key          TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    default_slug TEXT NOT NULL,
    keywords     TEXT NOT NULL,
    description  TEXT NOT NULL,
    files        TEXT NOT NULL,
    content_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      REAL NOT NULL,
    action  TEXT NOT NULL,
    subject TEXT NOT NULL,
    detail  TEXT NOT NULL
);
"""


class StoreError(Exception):
    """The store was unusable or held data that failed verification."""


class Store:
    """A per-call-connection SQLite store (safe under threaded servers)."""

    def __init__(self, path: str = DEFAULT_DB_PATH):
        self.path = path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with self._conn() as db:
            db.executescript(_SCHEMA)

    def _conn(self):
        return sqlite3.connect(self.path)

    def audit(self, action: str, subject: str, detail: str = "") -> None:
        with self._conn() as db:
            db.execute(
                "INSERT INTO audit (at, action, subject, detail) VALUES (?, ?, ?, ?)",
                (time.time(), action, subject, detail),
            )

    # ------------------------------------------------------------------ corpus
    def corpus_text(self) -> str:
        """The whole corpus as canonical JSON — the committable interchange form."""
        with self._conn() as db:
            rows = db.execute(
                "SELECT name, arity, source, examples FROM corpus ORDER BY name"
            ).fetchall()
        entries = [
            {"name": n, "arity": a, "source": s, "examples": json.loads(e)}
            for n, a, s, e in rows
        ]
        return json.dumps(
            {"detcode_corpus": 1, "entries": entries}, indent=2, sort_keys=True
        ) + "\n"

    def replace_corpus(self, corpus_text: str, action: str = "teach") -> int:
        """Replace the corpus with the (already verified) corpus JSON text."""
        try:
            entries = json.loads(corpus_text)["entries"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise StoreError(f"bad corpus text: {exc}") from exc
        with self._conn() as db:
            db.execute("DELETE FROM corpus")
            db.executemany(
                "INSERT INTO corpus (name, arity, source, examples) VALUES (?, ?, ?, ?)",
                [
                    (e["name"], e["arity"], e["source"], canonical_json(e["examples"]))
                    for e in entries
                ],
            )
        self.audit(action, f"{len(entries)} corpus entr(y/ies)", content_hash(corpus_text))
        return len(entries)

    def corpus_count(self) -> int:
        with self._conn() as db:
            return db.execute("SELECT COUNT(*) FROM corpus").fetchone()[0]

    # ------------------------------------------------------------------- packs
    def upsert_pack(self, record: dict) -> None:
        required = ("key", "title", "default_slug", "keywords", "description", "files")
        if not all(k in record for k in required):
            raise StoreError(f"pack record needs {required}")
        files_json = canonical_json(record["files"])
        with self._conn() as db:
            db.execute(
                "INSERT OR REPLACE INTO packs "
                "(key, title, default_slug, keywords, description, files, content_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    record["key"],
                    record["title"],
                    record["default_slug"],
                    canonical_json(sorted(record["keywords"])),
                    record["description"],
                    files_json,
                    content_hash(files_json),
                ),
            )
        self.audit("mint", record["key"], content_hash(files_json))

    def user_packs(self) -> list:
        """Stored packs as packs.Pack objects, hash-verified on every load."""
        from . import packs as packs_module

        with self._conn() as db:
            rows = db.execute(
                "SELECT key, title, default_slug, keywords, description, files, "
                "content_hash FROM packs ORDER BY key"
            ).fetchall()
        out = []
        for key, title, slug, keywords, description, files_json, digest in rows:
            if content_hash(files_json) != digest:
                raise StoreError(
                    f"stored pack {key!r} failed its content hash — the database "
                    "was edited or corrupted; re-mint it"
                )
            files = json.loads(files_json)
            out.append(
                packs_module.Pack(
                    key=key,
                    title=title,
                    default_slug=slug,
                    keywords=frozenset(json.loads(keywords)),
                    description=description,
                    files=lambda f=files: dict(f),
                )
            )
        return out


def open_default(path: str | None = None) -> Store:
    """Open the configured store; fall back to a temp path when the default
    location is unwritable (e.g. serverless read-only filesystems)."""
    import tempfile

    target = path or os.environ.get("DETCODE_DB") or DEFAULT_DB_PATH
    try:
        return Store(target)
    except (OSError, sqlite3.OperationalError):
        return Store(os.path.join(tempfile.gettempdir(), "detcode.db"))
