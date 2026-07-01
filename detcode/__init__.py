"""detcode — a deterministic coding assistant.

The goal: mimic the *useful* parts of an LLM coding assistant — refactors,
code generation, small-function synthesis, and repair — with no LLM and no
randomness. Same inputs always produce byte-identical output, every output
is traceable to the exact rule that produced it, and anything that cannot be
done safely is refused loudly rather than guessed.
"""
from .determinism import TOOL_VERSION as __version__

__all__ = ["__version__"]
