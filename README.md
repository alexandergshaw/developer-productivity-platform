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

The fastest way in is the English interface:

```bash
# English -> synthesized code (no file needed)
detcode do "write a function double where double(2) == 4 and double(5) == 10"

# English -> verified bug fix
detcode do "fix area so that area(2, 3) == 6 and area(4, 5) == 20" --file app.py --diff

# English -> explanation / tests / refactors
detcode do "explain area" --file app.py
detcode do "generate tests for area where area(2, 3) == 6" --file app.py
detcode do "rename local total to acc in compute" --file app.py --write
```

Unrecognized commands are refused with the closest supported form
(deterministic edit distance) — never guessed at.

Each capability also has a direct subcommand:

```bash
# Rename a local variable inside one function (preserves comments & formatting)
detcode rename-local --file app.py --func compute --from total --to accumulator --diff

# Remove module-level imports that are never used
detcode remove-unused-imports --file app.py --write

# Generate a module of dataclasses/enums from a JSON spec
detcode scaffold --spec examples/models.spec.json --out models.py

# Synthesize a function from input/output examples
detcode synth --examples examples/fullname.examples.json
# -> def full_name(x0, x1):
#        return (x0 + (' ' + x1))

# Repair a buggy function until it passes input/output tests
detcode repair --file examples/buggy_area.py --spec examples/buggy_area.repair.json --diff
# -    return width + height
# +    return width * height

# Explain a function or module (AST-derived, never invented)
detcode explain --file app.py --func apply_discount

# Generate a runnable unittest module from examples
detcode gentest --spec tests.spec.json --file app.py --out test_app.py
```

See [examples/](examples/) for the spec, example-set, and repair-spec formats.

## Web playground (Vercel)

A static playground UI ([index.html](index.html)) served by a stdlib WSGI app
([main.py](main.py) — the entrypoint Vercel's Python builder detects) exposes
every engine over HTTP. detcode is stdlib-only, so there is nothing to install
or configure:

```bash
# local development — runs the exact WSGI app Vercel deploys
python devserver.py     # -> http://127.0.0.1:8000

# deploy
vercel deploy           # or push the repo and import it at vercel.com/new
```

`POST /api/run` takes `{"tool": "do", "command": "...", "source": "..."}` (or
direct tool payloads — see `detcode/service.py`) and returns
`{"ok": true, "kind": "edit"|"generated"|"text", "output": "...", "report": {...}}`.
Refusals come back as `{"ok": false, "refused": true, "error": "..."}`.

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

## Capabilities

1. **Refactors / codemods** — `rename-local`, `remove-unused-imports`,
   `sort-imports` (isort-lite), and the `clean up` composite
2. **Scaffolding / codegen** — `scaffold` (dataclasses + enums from a JSON spec)
3. **Function writing** — retrieval-first: a corpus of 27 hand-verified
   classic functions (is_prime, fibonacci, gcd, binary_search, ...) matched
   against your examples — examples are the oracle — with fallback to `synth`,
   bottom-up enumerative search over a 45-component typed DSL (ints, strings,
   booleans, lists, conditionals) with deterministic constant mining
4. **Bug-fix / repair** — `repair`: spectrum-based fault localization ranks
   suspicious lines, then token-level mutation search (operators, constants,
   boolean literals, augmented assignments, wrong-variable swaps) with the
   test suite as the oracle
5. **Code explanation** — `explain`: AST-derived structural summaries through
   fixed English templates
6. **Test generation** — `gentest`: examples → a runnable unittest module,
   plus deterministic edge cases: branch boundaries mined from comparisons,
   type-based probes, exception pinning via assertRaises, each probe bounded
   by a line-count budget
7. **Docstring generation** — `document`: name-derived summaries plus
   Args/Returns/Yields/Raises read off the AST; never overwrites, idempotent
8. **English interface** — `do`: a controlled natural language covering every
   engine, with filler stripping ("please ... thanks"), synonym normalization
   (make/build → write, debug → fix, "what does f do?" → explain f), "did you
   mean" refusals, and quote-aware chaining ("remove unused imports then
   rename local total to acc in compute")

Every front-end compiles to an `Intent` (`ir.py`) dispatched by `planner.py`;
the same seam serves the CLI, the CNL, and the web API (`service.py`).

## Development

```bash
python -m unittest discover -s tests
```

`tests/test_determinism.py` is the determinism gate: it asserts reproducibility
(identical content hash across many runs) and reversibility/idempotency for
every codemod. CI runs the suite on Linux and Windows across Python 3.10–3.13.

## License

MIT
