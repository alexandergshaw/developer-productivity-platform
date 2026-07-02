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

And detcode can build out whole experimental projects from a direction:

```bash
detcode new "a resume tailorer" --out resume_tailorer
detcode new "a teaching assistant app" --out teaching_assistant
detcode new "an expense tracker" --web --out expense_tracker   # + WSGI web UI
detcode new "a teaching assistant with a resume module" --out suite  # composition
detcode new "an invoice reconciliation tool" --dry-run   # decisions + file list

cd resume_tailorer
python -m resume_tailorer my_resume.txt job_posting.txt
python -m unittest discover -s tests    # generated projects ship green tests
```

The generated README lists every decision detcode made and how to grow the
project with detcode's example-driven tools. Existing files are never
overwritten — a collision refuses the whole write.

## When detcode doesn't know how to build it: plan → build → teach

For directions beyond the domain packs, detcode runs a deterministic
*spec interview* instead of dead-ending — the analogue of an LLM asking
clarifying questions, answered **by example**:

```bash
detcode plan "a citation formatter"        # interview -> citation_formatter.plan.json
# fill functions[].examples — the examples ARE the spec
detcode new --plan citation_formatter.plan.json
```

Every planned function detcode can derive from its examples (retrieval →
synthesis) becomes real code with green tests. The rest become stubs whose
examples ship as `@unittest.expectedFailure` tests — executable TODOs that
keep the suite green and flip loudly when implemented. Then close the loop:

```bash
detcode teach --file core.py --func format_authors --examples ex.json
```

`teach` verifies the function in isolation against its examples and promotes
it into detcode's memory. Retrieval consults it (re-verifying every entry on
load — a tampered entry refuses loudly), so every future project that needs
the function gets it for free: **the app's capability grows by acquiring
verified artifacts, never statistics.**

## detcode's memory: the database

`.detcode/detcode.db` (SQLite — still zero dependencies) backs everything
learned, at two scales:

```bash
detcode teach --all --dir myproject   # sweep: examples mined from its tests
detcode corpus list                   # what's been taught
detcode corpus export --out team.json # canonical JSON — commit it to share
detcode corpus import team.json      # fully verified before merging

detcode mint --keywords "studybuddy,revision"  # a green project becomes a PACK
detcode packs list                    # built-in and minted packs
detcode packs export --out packs.json # commit to share packs between machines
detcode packs import packs.json       # verified: structure, parse, TESTS GREEN
detcode new "a studybuddy for revision season" # retrieves the whole project
```

Functions are *taught* (corpus), projects are *minted* (packs), and
knowledge is *learned* — all proof-carrying: teaching verifies in isolation,
minting requires the project's own tests green, learned guidance needs
sources or assert-bearing examples that are re-verified on every load, and
everything is hash-verified out of the database.

## Technical guidance: ask, learn, study

```bash
detcode ask "should I use floats for money?"   # guidance with receipts
detcode advise --file app.py                   # your diagnostics, paired with lessons
detcode study                                  # questions the engine couldn't answer
detcode learn --topic "..." --keywords a,b --source URL --guidance "..."
detcode knowledge list | export | import       # share it, fully re-verified
```

`ask` cascades honestly: knowledge base (deterministic keyword scoring) →
engine knowledge (corpus functions, packs) → **"I don't know this yet"**
with the question logged to the study queue. The loop closes from every
direction: `learn` adds a verified entry and answers matching open
questions — and so do `teach` and `mint`, because experience counts. Bare
questions work in English too ("how do I ...", "should I ...").
The workbench teaches and mints too: the "TEACH FROM ACTIVE FILE" panel,
`teach <func> where <func>(...) == ...` in the terminal, and "Mint this
project…" / `mint kw1,kw2` (the workspace's tests run server-side and must
pass) all persist through the server's store.

Plan mode also suggests function names mined from the direction's verbs —
"a citation formatter that parses bibtex" seeds `format_citation` and
`parse_bibtex` in the plan, examples left for you to fill.

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

The web UI ([index.html](index.html)) is a VS Code-style workbench: a file
explorer that loads folders of code in the browser, a tabbed **editable**
editor with Python syntax highlighting, and a terminal that talks to the
engine — `new "a resume tailorer"` materializes a whole project into the
explorer, and English commands act on the active file's buffer.

Working with code, the engine behaves like a pair programmer, always
deterministically:

- **edit & add**: type in the buffers; `add a function is_even where
  is_even(4) == True` derives the function (corpus → synthesis) and appends
  it with a collision-refusing codemod; Ctrl+S saves back to disk through
  the File System Access handles (File → Save Project As… for generated
  projects)
- **autocomplete**: buffer identifiers, keywords, builtins, and ⚡ corpus
  completions — accepting `is_prime` on an empty line inserts the whole
  verified implementation
- **diagnostics as you type**: syntax errors, unused imports (one-click
  quick fix), mutable default arguments, bare except, `== None`, TODOs —
  fixed AST rules, problem counts in the status bar
- **`test`**: runs the workspace's own suite server-side and reports green,
  red, and expected failures (planned stubs) It is served by a stdlib
WSGI app ([main.py](main.py) — the entrypoint Vercel's Python builder
detects). detcode is stdlib-only, so there is nothing to install or configure:

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
9. **Project builder** — `new`: give a general direction and detcode builds
   out a complete runnable project, exercising independence deterministically:
   every decision (domain pack, package name, layout) comes from a fixed
   procedure and is recorded in the build report and the generated README.
   Domain packs ship real, tested logic — **resume tailorer** (keyword
   extraction, coverage scoring, bullet ranking, tailoring suggestions),
   **teaching assistant** (flashcards from notes, cloze quizzes, SM-2 spaced
   repetition), and **expense tracker** (CSV parsing, keyword-rule
   categorization, integer-cents monthly reports); unmatched directions get a
   runnable skeleton that marks exactly where the domain logic goes.
   Directions matching several packs **compose** ("a teaching assistant with
   a resume module" — the pack named earliest is primary, each ships as its
   own package), and `--web` (or "... with a web ui") wraps the primary CLI
   in a stdlib WSGI page — the same pattern detcode's own playground uses

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
