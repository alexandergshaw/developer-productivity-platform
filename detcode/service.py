"""Service layer: a pure dict-in / dict-out request runner.

This is what the web API (``api/run.py`` on Vercel) calls, kept inside the
package so it is unit-testable without HTTP. Refusals — the deliberate
"cannot do that safely" outcomes — are distinguished from malformed requests.

Request shape::

    {"tool": "do",       "command": "...", "source": "..."?}
    {"tool": "new",      "direction": "..."}
    {"tool": "synth",    "spec": {...}}
    {"tool": "scaffold", "spec": {...}}
    {"tool": "gentest",  "spec": {...}}
    {"tool": "repair",   "source": "...", "spec": {...}}
    {"tool": "rename",   "source": "...", "func": "...", "old": "...", "new": "..."}
    {"tool": "imports",  "source": "..."}
    {"tool": "explain",  "source": "...", "func": "..."?}

Response shape::

    {"ok": true,  "kind": "edit"|"generated"|"text", "output": "...",
     "changed": bool, "report": {...}}
    {"ok": false, "refused": bool, "error": "..."}
"""
from __future__ import annotations

from . import cnl, planner
from .engines import (
    builder,
    document,
    explain,
    gentest,
    repair,
    retrieve,
    rewrite,
    scaffold,
    synth,
)

from .engines import teach as teach_engine
from .engines.mint import MintError
from .store import StoreError

REFUSALS = (
    MintError,
    rewrite.Unsafe,
    builder.BuildError,
    teach_engine.TeachError,
    teach_engine.CorpusError,
    StoreError,
    scaffold.SpecError,
    synth.SpecError,
    synth.NoSolution,
    repair.SpecError,
    repair.NoRepair,
    gentest.SpecError,
    explain.ExplainError,
    document.DocError,
    cnl.CNLError,
    planner.UnknownIntent,
    planner.MissingSource,
)


def _edit(source: str, changed: bool, report: dict) -> dict:
    return {"ok": True, "kind": "edit", "output": source, "changed": changed, "report": report}


def _generated(text: str, report: dict) -> dict:
    return {"ok": True, "kind": "generated", "output": text, "changed": False, "report": report}


def _text(text: str, report: dict) -> dict:
    return {"ok": True, "kind": "text", "output": text, "changed": False, "report": report}


def run_request(req, store=None) -> dict:
    if not isinstance(req, dict):
        return {"ok": False, "refused": False, "error": "request must be a JSON object"}
    tool = req.get("tool")
    try:
        if tool == "do":
            intents = cnl.parse_all(str(req.get("command") or ""))
            outcome = planner.run_all(intents, req.get("source"), store)
            if outcome.new_source is not None:
                resp = _edit(outcome.new_source, outcome.changed, outcome.report)
                if outcome.output:  # a chain can both edit and generate
                    resp["text"] = outcome.output
                if outcome.files:
                    resp["files"] = outcome.files
                return resp
            explain_only = all(i.operation == "explain" for i in intents)
            kind = _text if explain_only else _generated
            resp = kind(outcome.output or "", outcome.report)
            if outcome.files:
                resp["files"] = outcome.files
            return resp
        if tool == "synth":
            r = retrieve.write_function(
                req.get("spec") or {}, extra=planner.corpus_entries(store)
            )
            return _generated(r.source, r.report)
        if tool == "scaffold":
            r = scaffold.scaffold(req.get("spec") or {})
            return _generated(r.source, r.report)
        if tool == "gentest":
            r = gentest.gentest(req.get("spec") or {})
            return _generated(r.source, r.report)
        if tool == "repair":
            r = repair.repair(str(req.get("source") or ""), req.get("spec") or {})
            return _edit(r.source, r.changed, r.report)
        if tool == "rename":
            r = rewrite.rename_local(
                str(req.get("source") or ""),
                str(req.get("func") or ""),
                str(req.get("old") or ""),
                str(req.get("new") or ""),
            )
            return _edit(r.source, r.changed, r.report)
        if tool == "imports":
            r = rewrite.remove_unused_imports(str(req.get("source") or ""))
            return _edit(r.source, r.changed, r.report)
        if tool == "explain":
            r = explain.explain(str(req.get("source") or ""), req.get("func") or None)
            return _text(r.text, r.report)
        if tool == "document":
            r = document.add_docstrings(str(req.get("source") or ""), req.get("func") or None)
            return _edit(r.source, r.changed, r.report)
        if tool == "new":
            if isinstance(req.get("plan"), dict):
                project = builder.build_from_plan(
                    req["plan"], web=bool(req.get("web")),
                    corpus=planner.corpus_entries(store),
                )
            else:
                project = builder.build(
                    str(req.get("direction") or ""),
                    web=bool(req.get("web")),
                    extra_packs=tuple(store.user_packs()) if store is not None else (),
                )
            resp = _generated(builder.render(project), project.report)
            resp["files"] = {f.path: f.content for f in project.files}
            return resp
        if tool == "complete":
            from .engines import complete as complete_engine

            items = complete_engine.complete(
                str(req.get("source") or ""),
                str(req.get("prefix") or ""),
                extra=planner.corpus_entries(store),
            )
            return {"ok": True, "kind": "complete", "items": items}
        if tool == "diagnostics":
            from .engines import diagnose

            items = diagnose.diagnostics(str(req.get("source") or ""))
            return {"ok": True, "kind": "diagnostics", "items": items}
        if tool == "runtests":
            from .engines import mint as mint_engine

            result = mint_engine.materialize_and_run(req.get("files") or {})
            failures = [
                {"test": str(test), "message": trace.strip().splitlines()[-1]}
                for test, trace in result.failures + result.errors
            ]
            passed = not failures and result.testsRun > 0
            summary = (
                f"ran {result.testsRun} test(s): "
                + ("all green ✓" if passed else f"{len(failures)} problem(s)")
                + (
                    f" ({len(result.expectedFailures)} expected failure(s) — "
                    "planned stubs)" if result.expectedFailures else ""
                )
            )
            return {
                "ok": True,
                "kind": "tests",
                "output": summary,
                "passed": passed,
                "ran": result.testsRun,
                "failures": failures,
            }
        if tool == "mint":
            from .engines import mint as mint_engine

            if store is None:
                return {
                    "ok": False,
                    "refused": True,
                    "error": "minting needs a pack store (not available here)",
                }
            record = mint_engine.mint_record(
                req.get("files") or {},
                list(req.get("keywords") or []),
                key=req.get("key") or None,
                title=req.get("title") or None,
                description=req.get("description") or None,
            )
            result = mint_engine.materialize_and_verify(
                req.get("files") or {}, record["default_slug"]
            )
            store.upsert_pack(record)
            return _text(
                f"minted pack {record['key']!r} — {result.testsRun} test(s) verified "
                f"green; directions mentioning {', '.join(record['keywords'])} now "
                "retrieve this project",
                mint_engine.mint_report(record, result.testsRun),
            )
        if tool == "plan":
            from .engines import plan as plan_engine

            r = plan_engine.make_plan(str(req.get("direction") or ""))
            resp = _generated(r.questions + "\n\n" + r.plan_text, r.report)
            resp["files"] = {r.report["plan_file"]: r.plan_text}
            return resp
        return {"ok": False, "refused": False, "error": f"unknown tool {tool!r}"}
    except REFUSALS as exc:
        return {"ok": False, "refused": True, "error": str(exc)}
    except SyntaxError as exc:
        return {"ok": False, "refused": False, "error": f"source is not valid Python: {exc}"}
    except (KeyError, TypeError, ValueError) as exc:
        return {"ok": False, "refused": False, "error": f"{type(exc).__name__}: {exc}"}
