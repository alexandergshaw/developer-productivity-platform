# detcode — a deterministic coding assistant

`detcode` aims to mimic the *useful* parts of an LLM coding assistant —
refactors, code generation, small-function synthesis, and repair — **with no
LLM and no randomness**.

## Why deterministic?

An LLM's coding ability comes from statistical priors over unbounded natural
language and code. That power is exactly what makes it non-reproducible and
occasionally wrong in confident-sounding ways. `detcode` trades open-endedness
for guarantees:

- **Reproducible** — the same input always produces byte-identical output, on
  any machine. Enforced by a CI gate that hashes results.
- **Auditable** — every output carries a provenance record naming the exact
  rule (and rule version) that produced it. No hallucination.
- **Correctness-by-refusal** — anything that can't be done safely is refused
  loudly (a clear message, exit code 2), never guessed.
- **Fast, offline, free** — no model, no network, standard library only.

The tradeoff is honest: `detcode` cannot understand arbitrary English about an
arbitrary codebase. It handles a well-chosen, growing slice of tasks perfectly
and rejects the rest. **The boundary is the product.**

## The determinism spine

Every stage obeys these or the guarantee collapses (see `detcode/determinism.py`):

- No randomness; no reliance on set/dict iteration order for output.
- Canonical serialization everywhere (sorted keys, fixed separators).
- Work is bounded by an **operation count, never a wall-clock timeout** — a
  time limit would make output depend on machine speed.
- Inputs (rules, versions) are pinned and recorded in provenance.

## Install

```bash
pip install -e .
```

Requires Python 3.10+. No third-party dependencies.

## Usage

```bash
# Rename a local variable inside one function (preserves comments & formatting)
detcode rename-local --file app.py --func compute --from total --to accumulator --diff

# Remove module-level imports that are never used
detcode remove-unused-imports --file app.py --write

# Generate a module of dataclasses/enums from a JSON spec
detcode scaffold --spec examples/models.spec.json --out models.py
```

See [examples/models.spec.json](examples/models.spec.json) for the spec format.

- default: print transformed source to stdout
- `--diff`: print a unified diff instead
- `--write`: edit the file in place
- refused/unsafe transform → exit code `2` with a message on stderr

Run as `detcode ...` (after install) or `python -m detcode ...`.

## Architecture

```
Request → Intent parser → Planner → { Retrieval | Synthesis | AST rewrite }
        → Emitter → Verifier (hermetic) → Verified output      ↺ repair loop
```

Every front-end compiles to an `Intent` (`detcode/ir.py`); every engine is a
pure function of an `Intent` plus source. Edits are span-based
(`detcode/sourceedit.py`) so untouched code stays byte-identical.

## Roadmap

Capabilities are added as self-contained verticals, in this order:

1. **Refactors / codemods** — ✅ `rename-local`, `remove-unused-imports`
2. **Scaffolding / codegen** — ✅ `scaffold` (dataclasses + enums from a JSON spec)
3. **Example-driven synthesis** — small functions from I/O examples + types
4. **Bug-fix / repair** — fault-localize + constrained repair against a test

The intent front-end evolves in parallel: I/O examples + types → structured
spec/DSL → controlled natural language.

## Development

```bash
python -m unittest discover -s tests
```

`tests/test_determinism.py` is the determinism gate: it asserts reproducibility
(identical content hash across many runs) and reversibility/idempotency for
every codemod. CI runs the suite on Linux and Windows across Python 3.10–3.13.

## License

MIT
