"""The determinism spine.

Every guarantee this project makes rests on the rules enforced here: the same
inputs (source + intent + pinned rule versions) always produce byte-identical
output, on any machine.

Rules:
- No randomness. Nothing in this package imports ``random`` or depends on set
  iteration order for its output.
- All serialization is canonical (sorted keys, fixed separators).
- Work is bounded by an operation *count*, never by wall-clock time. A
  wall-clock timeout would make output depend on machine speed and destroy
  determinism, so we forbid it.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

TOOL_VERSION = "0.8.0"


class BudgetExceeded(RuntimeError):
    """Raised when an operation exceeds its deterministic op-count budget."""


@dataclass
class OpBudget:
    """A deterministic, count-based work limit.

    Unlike a wall-clock timeout, this yields the same outcome on a slow machine
    and a fast one, so it can bound search without breaking reproducibility.
    """

    limit: int
    used: int = 0

    def tick(self, n: int = 1) -> None:
        self.used += n
        if self.used > self.limit:
            raise BudgetExceeded(f"op budget of {self.limit} exceeded")


def canonical_json(obj) -> str:
    """Serialize ``obj`` in a canonical, stable form (sorted keys)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(text: str) -> str:
    """SHA-256 of ``text`` — used for the determinism CI gate and provenance."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def provenance(rule: str, rule_version: str, **extra) -> dict:
    """A record tying an output back to the exact rule that produced it."""
    record = {
        "tool": "detcode",
        "tool_version": TOOL_VERSION,
        "rule": rule,
        "rule_version": rule_version,
    }
    record.update(extra)
    return record
