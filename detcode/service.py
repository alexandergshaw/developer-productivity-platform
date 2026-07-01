"""Service layer: a pure dict-in / dict-out request runner.

This is what the web API (``api/run.py`` on Vercel) calls, kept inside the
package so it is unit-testable without HTTP. Refusals — the deliberate
"cannot do that safely" outcomes — are distinguished from malformed requests.

Request shape::

    {"tool": "do",       "command": "...", "source": "..."?}
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
from .engines import explain, gentest, repair, rewrite, scaffold, synth

REFUSALS = (
    rewrite.Unsafe,
    scaffold.SpecError,
    synth.SpecError,
    synth.NoSolution,
    repair.SpecError,
    repair.NoRepair,
    gentest.SpecError,
    explain.ExplainError,
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


def run_request(req) -> dict:
    if not isinstance(req, dict):
        return {"ok": False, "refused": False, "error": "request must be a JSON object"}
    tool = req.get("tool")
    try:
        if tool == "do":
            intent = cnl.parse(str(req.get("command") or ""))
            outcome = planner.run(intent, req.get("source"))
            if outcome.new_source is not None:
                return _edit(outcome.new_source, outcome.changed, outcome.report)
            kind = _text if intent.operation == "explain" else _generated
            return kind(outcome.output or "", outcome.report)
        if tool == "synth":
            r = synth.synthesize(req.get("spec") or {})
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
        return {"ok": False, "refused": False, "error": f"unknown tool {tool!r}"}
    except REFUSALS as exc:
        return {"ok": False, "refused": True, "error": str(exc)}
    except SyntaxError as exc:
        return {"ok": False, "refused": False, "error": f"source is not valid Python: {exc}"}
    except (KeyError, TypeError, ValueError) as exc:
        return {"ok": False, "refused": False, "error": f"{type(exc).__name__}: {exc}"}
