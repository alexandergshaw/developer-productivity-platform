"""Domain packs — project-scale retrieval.

A pack is to projects what the corpus is to functions: a hand-verified,
deterministic implementation of a domain, matched against the user's
*direction* by a fixed keyword table. Each pack returns a mapping of relative
paths to file contents, with ``__PKG__`` as the package-name placeholder.

Matching is deterministic: packs are tried in registry order and the first
whose keyword set intersects the direction's words wins; no intersection
falls through to the generic skeleton.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Pack:
    key: str
    title: str
    default_slug: str
    keywords: frozenset[str]
    description: str
    files: object  # callable() -> dict[str, str] with __PKG__ placeholders


def registry() -> tuple[Pack, ...]:
    from . import expense_tracker, generic, resume_tailorer, teaching_assistant

    return (
        resume_tailorer.PACK,
        teaching_assistant.PACK,
        expense_tracker.PACK,
        generic.PACK,  # generic must stay last: it matches nothing and is the fallback
    )


def match(words: set[str]) -> tuple[Pack, list[str]]:
    """First pack whose keywords intersect ``words``; generic otherwise."""
    packs = registry()
    for pack in packs[:-1]:
        hits = sorted(pack.keywords & words)
        if hits:
            return pack, hits
    return packs[-1], []
