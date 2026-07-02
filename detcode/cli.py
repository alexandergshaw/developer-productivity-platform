"""Command-line front-end.

Deterministic in, deterministic out. Reads a file, applies a codemod, and by
default prints the transformed source to stdout. ``--diff`` prints a unified
diff instead; ``--write`` edits the file in place. An unsafe/refused
transformation exits with code 2 and a clear message on stderr.
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys

from . import cnl, planner
from .determinism import TOOL_VERSION
import os

from .engines import (
    builder,
    document,
    explain,
    gentest,
    mint as mint_engine,
    plan as plan_engine,
    repair,
    retrieve,
    rewrite,
    scaffold,
    synth,
    teach,
)


def _store_error():
    from .store import StoreError

    return StoreError


def _knowledge_error():
    from .engines.knowledge import KnowledgeError

    return KnowledgeError


def _load_corpus(path_arg: str | None) -> tuple:
    """User corpus entries, verified on load.

    Priority: explicit --corpus JSON file, then the local database
    (.detcode/detcode.db), then the legacy .detcode/corpus.json.
    """
    from . import store as store_module

    if path_arg:
        return teach.load_corpus(_read(path_arg))
    if os.path.exists(store_module.DEFAULT_DB_PATH):
        return teach.load_corpus(store_module.Store().corpus_text())
    if os.path.exists(teach.DEFAULT_CORPUS_PATH):
        return teach.load_corpus(_read(teach.DEFAULT_CORPUS_PATH))
    return ()


class _EditResult:
    """Adapter so planner Outcomes flow through the shared _emit path."""

    def __init__(self, outcome):
        self.source = outcome.new_source
        self.changed = outcome.changed
        self.report = outcome.report


def _read(path: str) -> str:
    # utf-8-sig strips a leading BOM if present (common on Windows editors),
    # which would otherwise break ast.parse. Output is always written BOM-less.
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        return fh.read()


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _emit(args, path: str, before: str, result) -> int:
    after = result.source
    if args.diff:
        diff = difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
        sys.stdout.writelines(diff)
    elif args.write:
        if result.changed:
            _write(path, after)
        print(
            f"{path}: {'updated' if result.changed else 'no change'} "
            f"({result.report.get('rule')})",
            file=sys.stderr,
        )
    else:
        sys.stdout.write(after)
    return 0


def _cmd_rename_local(args) -> int:
    before = _read(args.file)
    result = rewrite.rename_local(before, args.func, getattr(args, "from"), args.to)
    return _emit(args, args.file, before, result)


def _cmd_remove_unused_imports(args) -> int:
    before = _read(args.file)
    result = rewrite.remove_unused_imports(before)
    return _emit(args, args.file, before, result)


def _cmd_scaffold(args) -> int:
    spec = json.loads(_read(args.spec))
    result = scaffold.scaffold(spec)
    if args.out:
        _write(args.out, result.source)
        print(f"{args.out}: generated ({result.report.get('rule')})", file=sys.stderr)
    else:
        sys.stdout.write(result.source)
    return 0


def _persist_corpus(corpus_text: str, path_arg: str | None, action: str) -> str:
    """Write the verified corpus: JSON file when --corpus given, DB otherwise."""
    from . import store as store_module

    if path_arg:
        os.makedirs(os.path.dirname(path_arg) or ".", exist_ok=True)
        _write(path_arg, corpus_text)
        return path_arg
    store = store_module.Store()
    store.replace_corpus(corpus_text, action=action)
    return store.path


def _existing_corpus_text(path_arg: str | None) -> str | None:
    from . import store as store_module

    if path_arg:
        return _read(path_arg) if os.path.exists(path_arg) else None
    if os.path.exists(store_module.DEFAULT_DB_PATH):
        return store_module.Store().corpus_text()
    if os.path.exists(teach.DEFAULT_CORPUS_PATH):
        return _read(teach.DEFAULT_CORPUS_PATH)
    return None


def _cmd_teach(args) -> int:
    if args.all:
        return _cmd_teach_all(args)
    if not (args.file and args.func and args.examples):
        raise teach.TeachError("teach needs --file, --func and --examples (or --all)")
    spec = json.loads(_read(args.examples))
    examples = spec.get("examples") if isinstance(spec, dict) else spec
    result = teach.teach(_read(args.file), args.func, examples, _existing_corpus_text(args.corpus))
    where = _persist_corpus(result.corpus_text, args.corpus, "teach")
    if not args.corpus:
        from . import store as store_module

        for question in store_module.Store().close_questions(
            args.func.split("_"), f"taught {args.func}"
        ):
            print(f"  answers open question: {question}", file=sys.stderr)
    print(
        f"{where}: taught {args.func!r} "
        f"({result.report['cases_verified']} example(s) verified, "
        f"{result.report['corpus_entries']} entr(y/ies) total)",
        file=sys.stderr,
    )
    return 0


def _cmd_teach_all(args) -> int:
    root = args.dir or "."
    module_sources: dict = {}
    test_sources: list = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "__pycache__"]
        for fname in sorted(filenames):
            if not fname.endswith(".py"):
                continue
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if "tests/" in rel or fname.startswith("test_"):
                test_sources.append(_read(full))
            else:
                module_sources[rel] = _read(full)
    result = teach.teach_all(module_sources, test_sources, _existing_corpus_text(args.corpus))
    where = _persist_corpus(result.corpus_text, args.corpus, "teach-all")
    taught = result.report["taught"]
    print(f"{where}: taught {len(taught)} function(s): {', '.join(taught) or '(none)'}", file=sys.stderr)
    for name, reason in sorted(result.report["skipped"].items()):
        print(f"  skipped {name}: {reason}", file=sys.stderr)
    return 0


def _cmd_corpus_list(args) -> int:
    entries = _load_corpus(None)
    if not entries:
        print("corpus is empty — teach something first", file=sys.stderr)
        return 0
    for entry in entries:
        print(f"{entry.name}/{entry.arity}")
    return 0


def _cmd_corpus_export(args) -> int:
    text = _existing_corpus_text(None)
    if text is None:
        raise teach.CorpusError("nothing to export — the corpus is empty")
    teach.load_corpus(text)  # never export something that would not verify
    if args.out:
        _write(args.out, text)
        print(f"{args.out}: exported (commit this file to share the corpus)", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


def _cmd_corpus_import(args) -> int:
    import json as _json

    from . import store as store_module

    incoming_text = _read(args.file)
    teach.load_corpus(incoming_text)  # full verification before anything merges
    incoming = _json.loads(incoming_text)["entries"]
    existing_text = _existing_corpus_text(None)
    existing = _json.loads(existing_text)["entries"] if existing_text else []
    merged = {e["name"]: e for e in existing}
    merged.update({e["name"]: e for e in incoming})  # imported entries win
    text = _json.dumps(
        {"detcode_corpus": 1, "entries": sorted(merged.values(), key=lambda e: e["name"])},
        indent=2,
        sort_keys=True,
    ) + "\n"
    store = store_module.Store()
    count = store.replace_corpus(text, action="import")
    print(f"{store.path}: imported {len(incoming)} entr(y/ies); corpus now {count}", file=sys.stderr)
    return 0


def _cmd_ask(args) -> int:
    from . import store as store_module
    from .ir import Intent

    store = store_module.Store()
    outcome = planner.run(Intent.of("ask", question=args.question), None, store)
    print(outcome.output)
    return 0


def _walk_py(root: str) -> dict:
    sources: dict = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "__pycache__"]
        for fname in sorted(filenames):
            if fname.endswith(".py"):
                full = os.path.join(dirpath, fname)
                sources[os.path.relpath(full, root).replace(os.sep, "/")] = _read(full)
    return sources


def _finding_fingerprints(root: str) -> list[str]:
    """Content-based fingerprints (path:severity:message) — stable across
    line-number drift, so the ratchet only trips on genuinely new findings."""
    from .engines import diagnose

    prints: set[str] = set()
    for path, source in _walk_py(root).items():
        for item in diagnose.diagnostics(source):
            prints.add(f"{path}:{item['severity']}:{item['message']}")
    return sorted(prints)


def _cmd_advise(args) -> int:
    from . import store as store_module
    from .engines import knowledge
    from .ir import Intent

    store = (
        store_module.Store() if os.path.exists(store_module.DEFAULT_DB_PATH) else None
    )
    if args.check or args.write_baseline:
        root = args.dir or "."
        baseline_path = args.baseline or os.path.join(root, ".detcode-advise-baseline.json")
        current = _finding_fingerprints(root)
        if args.write_baseline:
            _write(baseline_path, json.dumps(current, indent=2) + "\n")
            print(f"{baseline_path}: baselined {len(current)} finding(s)", file=sys.stderr)
            return 0
        baseline = set(json.loads(_read(baseline_path))) if os.path.exists(baseline_path) else set()
        new = [f for f in current if f not in baseline]
        fixed = sorted(baseline - set(current))
        if new:
            print(f"advise gate: {len(new)} NEW finding(s) not in the baseline:")
            for fingerprint in new:
                print(f"  {fingerprint}")
            print("\nfix them, or accept deliberately: detcode advise --write-baseline"
                  + (f" --dir {args.dir}" if args.dir else ""))
            return 2
        message = f"advise gate: clean — no new findings ({len(baseline)} baselined"
        message += f", {len(fixed)} fixed and removable from the baseline)" if fixed else ")"
        print(message)
        return 0
    if args.dir:
        learned = knowledge.load_knowledge(store.knowledge_text()) if store else ()
        print(knowledge.advise_all(_walk_py(args.dir), extra_entries=learned).text)
        return 0
    if not args.file:
        raise knowledge.KnowledgeError("give --file for one file or --dir for a workspace")
    outcome = planner.run(Intent.of("advise"), _read(args.file), store)
    print(outcome.output)
    return 0


def _cmd_learn(args) -> int:
    from . import store as store_module
    from .engines import knowledge

    guidance = args.guidance or (_read(args.file) if args.file else "")
    entry = {
        "topic": args.topic,
        "keywords": [k for k in (args.keywords or "").split(",") if k.strip()],
        "guidance": guidance,
        "sources": args.source or [],
        "examples": json.loads(_read(args.examples)) if args.examples else [],
    }
    store = store_module.Store()
    text, report = knowledge.learn(entry, store.knowledge_text())
    store.replace_knowledge(text)
    closed = store.close_questions(report["keywords"], report["topic"])
    print(
        f"{store.path}: learned {report['topic']!r} "
        f"({report['verified_examples']} verified example(s))",
        file=sys.stderr,
    )
    for question in closed:
        print(f"  ✓ answers open question: {question}", file=sys.stderr)
    return 0


def _study_markdown(records: list[dict]) -> str:
    """The study queue as a committable reading list (deterministic)."""
    open_items = [r for r in records if r["status"] == "open"]
    answered = [r for r in records if r["status"] != "open"]
    lines = [
        "# detcode study queue",
        "",
        "Questions the engine refused to guess at. Close one by learning:",
        "",
        "```bash",
        'detcode learn --topic "..." --keywords a,b --source URL --guidance "..."',
        "```",
        "",
        f"## Open ({len(open_items)})",
        "",
    ]
    if open_items:
        for record in open_items:
            keywords = ", ".join(record["keywords"])
            lines.append(f"- [ ] {record['question']}  — keywords: {keywords}")
    else:
        lines.append("(none — every question has an answer)")
    lines.extend(["", f"## Answered ({len(answered)})", ""])
    if answered:
        for record in answered:
            lines.append(f"- [x] {record['question']}  — {record['answered_by']}")
    else:
        lines.append("(none yet)")
    return "\n".join(lines) + "\n"


def _study_cards(records: list[dict]) -> str:
    """The study queue as teaching-assistant notes: answered questions become
    Q/A flashcards; open ones become prose the cloze quiz can chew on."""
    lines = []
    for record in records:
        if record["status"] == "answered":
            lines.append(f"Q: {record['question']}")
            lines.append(f"A: {record['answered_by']}")
            lines.append("")
    open_items = [r for r in records if r["status"] == "open"]
    if open_items:
        lines.append("Still researching:")
        lines.extend(f"{r['question']} remains an open question." for r in open_items)
        lines.append("")
    return "\n".join(lines) + ("" if not lines else "\n")


def _cmd_study(args) -> int:
    from . import store as store_module

    records = store_module.Store().open_questions()
    if args.cards:
        text = _study_cards(records)
        if not text.strip():
            print("nothing to export — ask some questions first", file=sys.stderr)
            return 0
        _write(args.cards, text)
        print(
            f"{args.cards}: exported — study it with the teaching assistant pack:\n"
            f"  python -m teaching_assistant cards {args.cards}\n"
            f"  python -m teaching_assistant quiz {args.cards}",
            file=sys.stderr,
        )
        return 0
    if args.out:
        _write(args.out, _study_markdown(records))
        print(f"{args.out}: study queue exported ({len(records)} question(s))", file=sys.stderr)
        return 0
    if not records:
        print("study queue is empty — every question so far has an answer")
        return 0
    for record in records:
        mark = "✓" if record["status"] == "answered" else "•"
        suffix = f"  [answered by: {record['answered_by']}]" if record["answered_by"] else ""
        print(f"{mark} {record['question']}{suffix}")
    return 0


def _cmd_knowledge_list(args) -> int:
    from . import store as store_module
    from .engines import knowledge

    for entry in knowledge.BUILTIN_KNOWLEDGE:
        print(f"{entry['topic']}  [built-in]")
    if os.path.exists(store_module.DEFAULT_DB_PATH):
        for entry in knowledge.load_knowledge(store_module.Store().knowledge_text()):
            print(f"{entry['topic']}  [learned]")
    return 0


def _cmd_knowledge_export(args) -> int:
    from . import store as store_module
    from .engines import knowledge

    text = store_module.Store().knowledge_text()
    knowledge.load_knowledge(text)  # never export what would not verify
    if args.out:
        _write(args.out, text)
        print(f"{args.out}: exported (commit this file to share)", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


def _cmd_knowledge_seeds(args) -> int:
    from .engines import knowledge

    for name in knowledge.seed_names():
        topics = ", ".join(e["topic"] for e in knowledge.SEED_COLLECTIONS[name])
        print(f"{name}: {topics}")
    print("\ninstall one: detcode knowledge seed <name>", file=sys.stderr)
    return 0


def _cmd_knowledge_seed(args) -> int:
    from . import store as store_module
    from .engines import knowledge

    entries = knowledge.seed_entries(args.name)  # validated + examples verified
    store = store_module.Store()
    existing = {e["topic"]: e for e in knowledge.load_knowledge(store.knowledge_text())}
    existing.update({e["topic"]: e for e in entries})
    merged = json.dumps(
        {"detcode_knowledge": 1, "entries": sorted(existing.values(), key=lambda e: e["topic"])},
        indent=2, sort_keys=True,
    ) + "\n"
    count = store.replace_knowledge(merged, action=f"seed:{args.name}")
    closed = []
    for entry in entries:
        closed.extend(store.close_questions(entry["keywords"], entry["topic"]))
    print(
        f"{store.path}: seeded {args.name!r} ({len(entries)} topic(s)); "
        f"knowledge now {count}",
        file=sys.stderr,
    )
    for question in closed:
        print(f"  answers open question: {question}", file=sys.stderr)
    return 0


def _cmd_knowledge_import(args) -> int:
    from . import store as store_module
    from .engines import knowledge

    incoming_text = _read(args.file)
    incoming = knowledge.load_knowledge(incoming_text)  # full re-verification
    store = store_module.Store()
    existing = {e["topic"]: e for e in knowledge.load_knowledge(store.knowledge_text())}
    existing.update({e["topic"]: e for e in incoming})  # imported entries win
    merged = json.dumps(
        {"detcode_knowledge": 1, "entries": sorted(existing.values(), key=lambda e: e["topic"])},
        indent=2, sort_keys=True,
    ) + "\n"
    count = store.replace_knowledge(merged, action="import-knowledge")
    print(f"{store.path}: imported {len(incoming)} entr(y/ies); knowledge now {count}", file=sys.stderr)
    return 0


def _cmd_synth(args) -> int:
    spec = json.loads(_read(args.examples))
    if args.name:
        spec["name"] = args.name
    result = retrieve.write_function(spec, extra=_load_corpus(args.corpus))
    if args.out:
        _write(args.out, result.source)
        print(
            f"{args.out}: synthesized {result.report.get('expr')}", file=sys.stderr
        )
    else:
        sys.stdout.write(result.source)
    return 0


def _cmd_repair(args) -> int:
    before = _read(args.file)
    spec = json.loads(_read(args.spec))
    result = repair.repair(before, spec)
    return _emit(args, args.file, before, result)


def _cmd_sort_imports(args) -> int:
    before = _read(args.file)
    result = rewrite.sort_imports(before)
    return _emit(args, args.file, before, result)


def _cmd_document(args) -> int:
    before = _read(args.file)
    result = document.add_docstrings(before, args.func)
    return _emit(args, args.file, before, result)


def _cmd_explain(args) -> int:
    result = explain.explain(_read(args.file), args.func)
    print(result.text)
    return 0


def _cmd_gentest(args) -> int:
    spec = json.loads(_read(args.spec))
    if args.file and "source" not in spec and "module" not in spec:
        spec["source"] = _read(args.file)
    result = gentest.gentest(spec)
    if args.out:
        _write(args.out, result.source)
        print(f"{args.out}: generated {result.report.get('cases')} tests", file=sys.stderr)
    else:
        sys.stdout.write(result.source)
    return 0


def _cmd_plan(args) -> int:
    result = plan_engine.make_plan(args.direction, name=args.name)
    print(result.questions, file=sys.stderr)
    out = args.out or result.report["plan_file"]
    if args.stdout:
        sys.stdout.write(result.plan_text)
    else:
        if os.path.exists(out):
            raise builder.BuildError(f"{out} already exists; refusing to overwrite")
        _write(out, result.plan_text)
        print(f"\nplan written to {out} — fill the examples, then:", file=sys.stderr)
        print(f"  detcode new --plan {out}", file=sys.stderr)
    return 0


def _user_packs() -> tuple:
    from . import store as store_module

    if os.path.exists(store_module.DEFAULT_DB_PATH):
        return tuple(store_module.Store().user_packs())
    return ()


def _cmd_mint(args) -> int:
    from . import store as store_module

    root = args.dir or "."
    files: dict = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "__pycache__"]
        for fname in sorted(filenames):
            full = os.path.join(dirpath, fname)
            files[os.path.relpath(full, root).replace(os.sep, "/")] = _read(full)
    keywords = [k for k in (args.keywords or "").split(",") if k.strip()]
    record = mint_engine.mint_record(
        files, keywords, key=args.key, title=args.title, description=args.description
    )
    result = mint_engine.verify_project(root, record["default_slug"])
    store = store_module.Store()
    store.upsert_pack(record)
    store.close_questions(record["keywords"], f"minted {record['key']}")
    report = mint_engine.mint_report(record, result.testsRun)
    print(
        f"{store.path}: minted pack {record['key']!r} "
        f"({result.testsRun} test(s) verified green, keywords: {', '.join(record['keywords'])})",
        file=sys.stderr,
    )
    print(f'try: detcode new "a {record["keywords"][0]} thing"', file=sys.stderr)
    return 0


def _cmd_packs_list(args) -> int:
    from . import packs as packs_module

    for pack in packs_module.registry()[:-1]:
        print(f"{pack.key}  [built-in]  keywords: {', '.join(sorted(pack.keywords))}")
    for pack in _user_packs():
        print(f"{pack.key}  [minted]    keywords: {', '.join(sorted(pack.keywords))}")
    return 0


def _pack_records_from_store() -> list[dict]:
    return [
        {
            "key": p.key,
            "title": p.title,
            "default_slug": p.default_slug,
            "keywords": sorted(p.keywords),
            "description": p.description,
            "files": p.files(),
        }
        for p in _user_packs()
    ]


def _cmd_packs_export(args) -> int:
    records = _pack_records_from_store()
    if args.key:
        records = [r for r in records if r["key"] == args.key]
        if not records:
            raise mint_engine.MintError(f"no minted pack named {args.key!r}")
    if not records:
        raise mint_engine.MintError("nothing to export — mint a pack first")
    text = json.dumps(
        {"detcode_packs": 1, "packs": sorted(records, key=lambda r: r["key"])},
        indent=2,
        sort_keys=True,
    ) + "\n"
    if args.out:
        _write(args.out, text)
        print(f"{args.out}: exported {len(records)} pack(s) (commit this file to share)", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


def _cmd_packs_import(args) -> int:
    from . import store as store_module

    try:
        data = json.loads(_read(args.file))
    except json.JSONDecodeError as exc:
        raise mint_engine.MintError(f"not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("detcode_packs") != 1:
        raise mint_engine.MintError('not a detcode packs file (expected {"detcode_packs": 1, ...})')
    records = data.get("packs")
    if not isinstance(records, list) or not records:
        raise mint_engine.MintError("packs file has no packs")

    # Full verification BEFORE anything merges: structure, parse, and the
    # pack's own tests run green — same proof-carrying bar as minting.
    verified = []
    for record in records:
        mint_engine.validate_pack_record(record)
        result = mint_engine.materialize_and_verify(
            mint_engine.concrete_files(record), record["default_slug"]
        )
        verified.append((record, result.testsRun))

    store = store_module.Store()
    for record, tests_run in verified:
        store.upsert_pack(record)
        print(
            f"{store.path}: imported pack {record['key']!r} ({tests_run} test(s) verified green)",
            file=sys.stderr,
        )
    return 0


def _cmd_new(args) -> int:
    if args.plan:
        if args.direction:
            raise builder.BuildError("give a direction OR --plan, not both")
        project = builder.build_from_plan(
            json.loads(_read(args.plan)), web=args.web, corpus=_load_corpus(args.corpus)
        )
    elif args.direction:
        project = builder.build(
            args.direction, name=args.name, web=args.web, extra_packs=_user_packs()
        )
    else:
        raise builder.BuildError('give a direction (detcode new "...") or --plan file.json')
    if args.dry_run:
        print(f"project: {project.name} ({len(project.files)} files)")
        print("decisions:")
        for decision in project.report["decisions"]:
            print(f"  - {decision}")
        print("files:")
        for f in project.files:
            print(f"  {f.path}")
        return 0

    out_dir = args.out or project.name
    # Never overwrite: check every target before writing anything.
    for f in project.files:
        target = os.path.join(out_dir, f.path.replace("/", os.sep))
        if os.path.exists(target):
            raise builder.BuildError(f"{target} already exists; refusing to overwrite")
    for f in project.files:
        target = os.path.join(out_dir, f.path.replace("/", os.sep))
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        _write(target, f.content)
    print(f"{out_dir}: generated {len(project.files)} files ({project.name})", file=sys.stderr)
    for decision in project.report["decisions"]:
        print(f"  - {decision}", file=sys.stderr)
    return 0


def _cmd_do(args) -> int:
    from . import store as store_module

    intents = cnl.parse_all(args.command)
    before = _read(args.file) if args.file else None
    # A store only when needed: teaching requires one; an existing local DB
    # should feed retrieval. Plain commands must not create .detcode/.
    needs_store = any(i.operation in ("teach", "ask") for i in intents)
    store = (
        store_module.Store()
        if needs_store or os.path.exists(store_module.DEFAULT_DB_PATH)
        else None
    )
    outcome = planner.run_all(intents, before, store)
    status = 0
    if outcome.new_source is not None:
        status = _emit(args, args.file, before, _EditResult(outcome))
    elif args.diff or args.write:
        print(
            "detcode: this command generates new content; printing to stdout",
            file=sys.stderr,
        )
    if outcome.output:
        print(outcome.output)
    return status


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--file", required=True, help="Python source file to operate on")
    out = p.add_mutually_exclusive_group()
    out.add_argument("--write", action="store_true", help="edit the file in place")
    out.add_argument("--diff", action="store_true", help="print a unified diff")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="detcode",
        description="A deterministic coding assistant (no LLM).",
    )
    parser.add_argument("--version", action="version", version=f"detcode {TOOL_VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    rl = sub.add_parser("rename-local", help="rename a local variable inside a function")
    _add_common(rl)
    rl.add_argument("--func", required=True, help="function containing the variable")
    rl.add_argument("--from", required=True, dest="from", help="current name")
    rl.add_argument("--to", required=True, help="new name")
    rl.set_defaults(handler=_cmd_rename_local)

    ri = sub.add_parser(
        "remove-unused-imports", help="remove module-level imports that are never used"
    )
    _add_common(ri)
    ri.set_defaults(handler=_cmd_remove_unused_imports)

    sc = sub.add_parser(
        "scaffold", help="generate a Python module (dataclasses/enums) from a JSON spec"
    )
    sc.add_argument("--spec", required=True, help="JSON spec file")
    sc.add_argument("--out", help="write generated module to this path (default: stdout)")
    sc.set_defaults(handler=_cmd_scaffold)

    sy = sub.add_parser(
        "synth", help="synthesize a function from input/output examples"
    )
    sy.add_argument("--examples", required=True, help="JSON file of input/output examples")
    sy.add_argument("--name", help="name for the synthesized function (default: f)")
    sy.add_argument("--out", help="write the function to this path (default: stdout)")
    sy.add_argument("--corpus", help="user corpus file (default: .detcode/corpus.json if present)")
    sy.set_defaults(handler=_cmd_synth)

    tc = sub.add_parser(
        "teach", help="verify a function against examples and add it to the local corpus"
    )
    tc.add_argument("--file", help="Python file containing the function")
    tc.add_argument("--func", help="top-level function to teach")
    tc.add_argument("--examples", help="JSON: {examples: [...]} or a bare list")
    tc.add_argument(
        "--all", action="store_true",
        help="sweep a whole project: mine examples from its tests, teach every "
        "self-contained function they cover",
    )
    tc.add_argument("--dir", help="project directory for --all (default: .)")
    tc.add_argument("--corpus", help="use a JSON corpus file instead of the database")
    tc.set_defaults(handler=_cmd_teach)

    cp = sub.add_parser("corpus", help="inspect and share the taught-function corpus")
    cp_sub = cp.add_subparsers(dest="corpus_command", required=True)
    cp_list = cp_sub.add_parser("list", help="list taught functions")
    cp_list.set_defaults(handler=_cmd_corpus_list)
    cp_exp = cp_sub.add_parser("export", help="canonical JSON for committing/sharing")
    cp_exp.add_argument("--out", help="write to this path (default: stdout)")
    cp_exp.set_defaults(handler=_cmd_corpus_export)
    cp_imp = cp_sub.add_parser("import", help="verify and merge a shared corpus file")
    cp_imp.add_argument("file", help="corpus JSON file to import")
    cp_imp.set_defaults(handler=_cmd_corpus_import)

    ak = sub.add_parser("ask", help="technical guidance (not code generation)")
    ak.add_argument("question", help='e.g. "how should I store money amounts?"')
    ak.set_defaults(handler=_cmd_ask)

    av = sub.add_parser(
        "advise", help="review a file or workspace: diagnostics paired with lessons"
    )
    av.add_argument("--file", help="Python source file")
    av.add_argument("--dir", help="review every .py under this directory instead")
    av.add_argument(
        "--check", action="store_true",
        help="CI gate: exit 2 if findings appear that are not in the baseline",
    )
    av.add_argument(
        "--write-baseline", action="store_true",
        help="accept the current findings as the baseline",
    )
    av.add_argument("--baseline", help="baseline path (default: <dir>/.detcode-advise-baseline.json)")
    av.set_defaults(handler=_cmd_advise)

    ln = sub.add_parser(
        "learn", help="add a verified knowledge entry (closes matching open questions)"
    )
    ln.add_argument("--topic", required=True, help="entry topic")
    ln.add_argument("--keywords", required=True, help="comma-separated match keywords")
    ln.add_argument("--guidance", help="the guidance text")
    ln.add_argument("--file", help="read guidance from this file instead")
    ln.add_argument("--source", action="append", help="citation URL/reference (repeatable)")
    ln.add_argument("--examples", help="JSON file of [{code, note}] assert-bearing examples")
    ln.set_defaults(handler=_cmd_learn)

    st = sub.add_parser("study", help="open questions the engine could not answer yet")
    st.add_argument("--out", help="export a markdown reading list to this path")
    st.add_argument(
        "--cards", help="export teaching-assistant flashcard notes to this path"
    )
    st.set_defaults(handler=_cmd_study)

    kn = sub.add_parser("knowledge", help="inspect and share the knowledge base")
    kn_sub = kn.add_subparsers(dest="knowledge_command", required=True)
    kn_list = kn_sub.add_parser("list", help="built-in and learned topics")
    kn_list.set_defaults(handler=_cmd_knowledge_list)
    kn_exp = kn_sub.add_parser("export", help="canonical JSON for committing/sharing")
    kn_exp.add_argument("--out", help="write to this path (default: stdout)")
    kn_exp.set_defaults(handler=_cmd_knowledge_export)
    kn_imp = kn_sub.add_parser("import", help="re-verify and merge a shared knowledge file")
    kn_imp.add_argument("file", help="knowledge JSON file to import")
    kn_imp.set_defaults(handler=_cmd_knowledge_import)
    kn_seeds = kn_sub.add_parser("seeds", help="curated seed collections available to install")
    kn_seeds.set_defaults(handler=_cmd_knowledge_seeds)
    kn_seed = kn_sub.add_parser("seed", help="install a curated seed collection")
    kn_seed.add_argument("name", help="collection name (see: knowledge seeds)")
    kn_seed.set_defaults(handler=_cmd_knowledge_seed)

    mt = sub.add_parser(
        "mint", help="turn a finished, green-tested project into a reusable pack"
    )
    mt.add_argument("--dir", help="project directory (default: .)")
    mt.add_argument("--keywords", required=True, help="comma-separated match keywords")
    mt.add_argument("--key", help="pack key (default: derived from the package name)")
    mt.add_argument("--title", help="pack title")
    mt.add_argument("--description", help="pack description")
    mt.set_defaults(handler=_cmd_mint)

    pk = sub.add_parser("packs", help="list, export, and import project packs")
    pk_sub = pk.add_subparsers(dest="packs_command", required=True)
    pk_list = pk_sub.add_parser("list", help="built-in and minted packs")
    pk_list.set_defaults(handler=_cmd_packs_list)
    pk_exp = pk_sub.add_parser("export", help="canonical JSON for committing/sharing")
    pk_exp.add_argument("--key", help="export only this pack (default: all minted)")
    pk_exp.add_argument("--out", help="write to this path (default: stdout)")
    pk_exp.set_defaults(handler=_cmd_packs_export)
    pk_imp = pk_sub.add_parser("import", help="verify (tests must pass) and merge shared packs")
    pk_imp.add_argument("file", help="packs JSON file to import")
    pk_imp.set_defaults(handler=_cmd_packs_import)

    rp = sub.add_parser(
        "repair", help="repair a buggy function so it passes input/output tests"
    )
    _add_common(rp)
    rp.add_argument(
        "--spec", required=True, help="JSON spec: function name, examples, max_edits"
    )
    rp.set_defaults(handler=_cmd_repair)

    si = sub.add_parser("sort-imports", help="canonically order the import block")
    _add_common(si)
    si.set_defaults(handler=_cmd_sort_imports)

    dc = sub.add_parser(
        "document", help="insert generated docstrings (functions lacking one)"
    )
    _add_common(dc)
    dc.add_argument("--func", help="document only this function (default: all undocumented)")
    dc.set_defaults(handler=_cmd_document)

    ex = sub.add_parser("explain", help="explain a function or module (AST-derived)")
    ex.add_argument("--file", required=True, help="Python source file")
    ex.add_argument("--func", help="function to explain (default: whole module)")
    ex.set_defaults(handler=_cmd_explain)

    gt = sub.add_parser(
        "gentest", help="generate a unittest module from input/output examples"
    )
    gt.add_argument("--spec", required=True, help="JSON spec: function, examples, source|module")
    gt.add_argument("--file", help="embed this file as the code under test")
    gt.add_argument("--out", help="write the test module to this path (default: stdout)")
    gt.set_defaults(handler=_cmd_gentest)

    pl = sub.add_parser(
        "plan", help="spec interview for a direction detcode cannot build yet"
    )
    pl.add_argument("direction", help='e.g. "a citation formatter"')
    pl.add_argument("--name", help="override the derived package name")
    pl.add_argument("--out", help="plan file path (default: <name>.plan.json)")
    pl.add_argument("--stdout", action="store_true", help="print the plan instead of writing it")
    pl.set_defaults(handler=_cmd_plan)

    nw = sub.add_parser(
        "new", help='generate a project from a general direction, e.g. "resume tailorer"'
    )
    nw.add_argument(
        "direction", nargs="?", help='e.g. "resume tailorer" or "teaching assistant app"'
    )
    nw.add_argument("--plan", help="build from a filled plan file instead of a direction")
    nw.add_argument("--out", help="target directory (default: the derived package name)")
    nw.add_argument("--name", help="override the derived package name")
    nw.add_argument(
        "--web", action="store_true",
        help="add a stdlib WSGI web UI over the CLI (also triggered by 'with a web ui')",
    )
    nw.add_argument("--corpus", help="user corpus for --plan builds (default: .detcode/corpus.json)")
    nw.add_argument(
        "--dry-run", action="store_true", help="print decisions and file list, write nothing"
    )
    nw.set_defaults(handler=_cmd_new)

    do = sub.add_parser(
        "do",
        help='run a controlled-natural-language command, e.g. "remove unused imports"',
    )
    do.add_argument("command", help='e.g. "write a function double where double(2) == 4"')
    do.add_argument("--file", help="Python source file (needed by file-editing commands)")
    out = do.add_mutually_exclusive_group()
    out.add_argument("--write", action="store_true", help="edit the file in place")
    out.add_argument("--diff", action="store_true", help="print a unified diff")
    do.set_defaults(handler=_cmd_do)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Learned guidance and status glyphs can be arbitrary unicode; never let a
    # cp1252 console crash the CLI — degrade unencodable characters instead.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, OSError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (
        rewrite.Unsafe,
        scaffold.SpecError,
        synth.SpecError,
        synth.NoSolution,
        repair.SpecError,
        repair.NoRepair,
        explain.ExplainError,
        gentest.SpecError,
        document.DocError,
        builder.BuildError,
        teach.TeachError,
        teach.CorpusError,
        mint_engine.MintError,
        _knowledge_error(),
        _store_error(),
        cnl.CNLError,
        planner.UnknownIntent,
        planner.MissingSource,
    ) as exc:
        print(f"detcode: refused: {exc}", file=sys.stderr)
        return 2
    except (OSError, SyntaxError, json.JSONDecodeError) as exc:
        print(f"detcode: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
