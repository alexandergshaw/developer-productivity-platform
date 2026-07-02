"""Teach — deterministic capability growth.

When you implement a function detcode could not derive (a plan stub, or any
function of your own), ``detcode teach`` verifies it against its examples and
promotes it into a local user corpus (``.detcode/corpus.json``). Retrieval
consults that corpus, so every future project that needs the function gets it
for free. The app's knowledge grows by acquiring *verified artifacts* — never
statistics.

Guarantees:
- a taught function must be self-contained (only builtins and its own
  arguments): it is verified in isolation, exactly as retrieval will run it
- the stored examples are re-verified every time the corpus loads, so a
  hand-edited entry that no longer passes refuses loudly instead of serving
  wrong code
"""
from __future__ import annotations

import ast
import json
from dataclasses import dataclass

from ..determinism import content_hash, provenance
from .retrieve import Entry, _passes

RULE_VERSION = "1"
CORPUS_FORMAT = 1
DEFAULT_CORPUS_PATH = ".detcode/corpus.json"


class TeachError(Exception):
    """The function could not be verified and taught."""


class CorpusError(Exception):
    """The user corpus file is malformed or an entry no longer verifies."""


@dataclass
class Result:
    corpus_text: str
    report: dict


def _extract_function(source: str, func: str) -> tuple[str, int]:
    """The standalone source of top-level function ``func`` and its arity."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise TeachError(f"source is not valid Python: {exc}") from exc
    matches = [
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == func
    ]
    if not matches:
        raise TeachError(f"no top-level function named {func!r} in source")
    if len(matches) > 1:
        raise TeachError(f"{len(matches)} functions named {func!r}; ambiguous")
    fn = matches[0]
    a = fn.args
    if a.defaults or a.kw_defaults or a.vararg or a.kwarg or a.kwonlyargs or a.posonlyargs:
        raise TeachError(
            f"{func!r} must take plain positional arguments only (corpus entries are simple)"
        )
    segment = ast.get_source_segment(source, fn)
    if segment is None:
        raise TeachError(f"could not extract the source of {func!r}")
    return segment.strip("\n") + "\n", len(a.args)


def _validate_examples(examples) -> list[dict]:
    if not isinstance(examples, list) or not examples:
        raise TeachError("provide a non-empty 'examples' list — they are the proof")
    for i, ex in enumerate(examples):
        if not isinstance(ex, dict) or "in" not in ex or "out" not in ex:
            raise TeachError(f"example {i} must have 'in' and 'out'")
        if not isinstance(ex["in"], list):
            raise TeachError(f"example {i} 'in' must be a list")
    return examples


def teach(source: str, func: str, examples: list, corpus_text: str | None = None) -> Result:
    """Verify ``func`` against ``examples`` and add it to the corpus text."""
    examples = _validate_examples(examples)
    segment, arity = _extract_function(source, func)
    if any(len(ex["in"]) != arity for ex in examples):
        raise TeachError(f"{func!r} takes {arity} argument(s); an example disagrees")
    if not _passes(segment, func, examples):
        raise TeachError(
            f"{func!r} failed verification in isolation — it must pass every "
            "example using only builtins and its own arguments (no module "
            "globals, imports, or helpers)"
        )

    entries = _parse_corpus(corpus_text) if corpus_text else []
    replaced = any(e["name"] == func for e in entries)
    entries = [e for e in entries if e["name"] != func]
    entries.append({"name": func, "arity": arity, "source": segment, "examples": examples})
    entries.sort(key=lambda e: e["name"])

    new_text = json.dumps(
        {"detcode_corpus": CORPUS_FORMAT, "entries": entries}, indent=2, sort_keys=True
    ) + "\n"
    report = provenance(
        "teach",
        RULE_VERSION,
        function=func,
        arity=arity,
        cases_verified=len(examples),
        replaced=replaced,
        corpus_entries=len(entries),
        corpus_hash=content_hash(new_text),
    )
    return Result(new_text, report)


def mine_examples(test_source: str) -> dict[str, list[dict]]:
    """Extract (function, examples) pairs from a test file.

    Any ``assertEqual(<something>.fn(literal, ...), literal)`` — the exact
    shape detcode-generated tests use — becomes an example for ``fn``. Calls
    with non-literal arguments are skipped; nothing is guessed.
    """
    try:
        tree = ast.parse(test_source)
    except SyntaxError:
        return {}
    mined: dict[str, list[dict]] = {}
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "assertEqual"
            and len(node.args) == 2
            and isinstance(node.args[0], ast.Call)
        ):
            continue
        inner = node.args[0]
        target = inner.func
        fname = target.attr if isinstance(target, ast.Attribute) else (
            target.id if isinstance(target, ast.Name) else None
        )
        if not fname or inner.keywords:
            continue
        try:
            args = [ast.literal_eval(a) for a in inner.args]
            expected = ast.literal_eval(node.args[1])
        except (ValueError, SyntaxError):
            continue
        example = {"in": args, "out": expected}
        bucket = mined.setdefault(fname, [])
        if example not in bucket:
            bucket.append(example)
    return mined


def teach_all(
    module_sources: dict[str, str],
    test_sources: list[str],
    corpus_text: str | None = None,
) -> Result:
    """Sweep a project: teach every self-contained function its tests cover.

    Examples are mined from the test sources; functions without mined
    examples, with fancy signatures, or that fail isolation are skipped with
    a recorded reason — never taught on faith.
    """
    mined: dict[str, list[dict]] = {}
    for text in test_sources:
        for fname, examples in mine_examples(text).items():
            bucket = mined.setdefault(fname, [])
            bucket.extend(ex for ex in examples if ex not in bucket)

    taught: list[str] = []
    skipped: dict[str, str] = {}
    current = corpus_text
    for path in sorted(module_sources):
        source = module_sources[path]
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            skipped[path] = f"file does not parse: {exc}"
            continue
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
                continue
            if node.name in taught or node.name in skipped:
                continue
            if node.name not in mined:
                skipped[node.name] = "no literal examples found in the tests"
                continue
            try:
                result = teach(source, node.name, mined[node.name], current)
                current = result.corpus_text
                taught.append(node.name)
            except TeachError as exc:
                skipped[node.name] = str(exc)

    if current is None:
        raise TeachError("nothing taught: no function had mineable examples")
    report = provenance(
        "teach_all",
        RULE_VERSION,
        taught=taught,
        skipped=skipped,
        corpus_hash=content_hash(current),
    )
    return Result(current, report)


def _parse_corpus(text: str) -> list[dict]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CorpusError(f"corpus file is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("detcode_corpus") != CORPUS_FORMAT:
        raise CorpusError('not a detcode corpus (expected {"detcode_corpus": 1, ...})')
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise CorpusError("corpus 'entries' must be a list")
    return entries


def load_corpus(text: str) -> tuple[Entry, ...]:
    """Parse and RE-VERIFY a user corpus; a failing entry refuses loudly."""
    out: list[Entry] = []
    for raw in _parse_corpus(text):
        name = raw.get("name")
        source = raw.get("source")
        arity = raw.get("arity")
        examples = raw.get("examples")
        if not (isinstance(name, str) and isinstance(source, str) and isinstance(arity, int)):
            raise CorpusError(f"malformed corpus entry: {raw!r}")
        if not isinstance(examples, list) or not examples:
            raise CorpusError(f"corpus entry {name!r} has no examples to verify against")
        if not _passes(source, name, examples):
            raise CorpusError(
                f"corpus entry {name!r} no longer passes its own examples — "
                "the file was edited or corrupted; re-teach it"
            )
        out.append(Entry(name, arity, source))
    out.sort(key=lambda e: e.name)
    return tuple(out)
