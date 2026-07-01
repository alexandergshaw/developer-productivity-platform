"""Intent IR — the canonical representation of *what the user wants*.

Every front-end (examples+types, spec/DSL, controlled natural language)
compiles down to an ``Intent``. Every engine is a pure function of an
``Intent`` plus the source it operates on. This seam keeps the system
deterministic and lets front-ends evolve independently of engines.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Intent:
    """A normalized request: an operation plus canonically-ordered arguments."""

    operation: str
    args: tuple[tuple[str, object], ...] = field(default_factory=tuple)

    @staticmethod
    def of(operation: str, **kwargs) -> "Intent":
        # Sorted so two intents with the same meaning compare and hash equal.
        return Intent(operation, tuple(sorted(kwargs.items())))

    def get(self, key: str, default=None):
        for k, value in self.args:
            if k == key:
                return value
        return default
