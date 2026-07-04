"""Deterministic ticket → code compiler.

A ticket is a free-form problem description (like a Jira issue). This engine
extracts verifiable artifacts from the ticket and compiles them into actions
on the existing engines (repair, synth, gentest, new). Everything it cannot
resolve becomes a precise open question.

Design:

- **Extraction**: parse examples (a) name(args) == expected b) name(args)
  returns expected, c) name(args) should return expected, d) name(args) ->
  expected / =>, e) "expected E but got G" patterns; code blocks (``` lang
  ... ```); tracebacks; backticked file/function refs; intent flags
  (wants_fix, wants_new, wants_tests via keyword tables).

- **Compilation**: fixed rule order producing actions and questions. For each
  function with examples: if workspace has `def <name>`, action to repair; else
  synth + gentest. If parsed.direction and wants_new and no examples matched,
  action to build new project. Open questions for vague tickets.

- **Execution**: repair/synth/gentest/new engines emit files; log questions to
  store; return a human-readable report with provenance.

Determinism: no randomness; iterate in first-occurrence/sorted order; same
inputs → byte-identical canonical_json output.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from ..determinism import provenance
from .. import planner
from . import gentest as gentest_engine, repair as repair_engine, retrieve
from . import builder, synth as synth_engine

RULE_VERSION = "1"


class TicketError(Exception):
    """Refusal: the ticket is too vague or malformed."""


@dataclass
class Result:
    ok: bool
    kind: str
    output: str
    files: dict[str, str] | None
    report: dict


def parse_ticket(text: str) -> dict:
    """Extract verifiable artifacts from a ticket.

    Returns dict with keys:
    - title: first non-empty line (stripped of "bug:/feature:/task:" prefixes)
    - examples: list of {name, args, expected, source_line}
    - code_blocks: list of {lang, code}
    - tracebacks: list of {file, line, error}
    - files: list of backticked file paths
    - functions: list of backticked function names
    - flags: {wants_fix, wants_new, wants_tests}
    - stack: detected stack name or None
    - direction: direction candidate (from "build a X" or title when wants_new)
    - lines: raw ticket lines
    """
    lines = text.strip().split("\n")

    # Extract title (first non-empty line, strip prefixes)
    title = ""
    for line in lines:
        stripped = line.strip()
        if stripped:
            title = re.sub(r"^(bug|feature|task):\s*", "", stripped, flags=re.I)
            break

    # Example forms: a) name(args) == expected  b) name(args) returns expected
    # c) name(args) should return expected  d) name(args) -> expected / =>
    # e) "expected E but got G" / "got G, expected E" / "returns G instead of E"
    examples: list[dict] = []
    example_patterns = [
        # Form a: name(args) == expected (also in backticks)
        # Stop at word boundaries: and, or, ;
        r"`?(\w+)\(([^)]*)\)\s*==\s*([^&;]+?)(?:\s+(?:and|or)|`|;|$)",
        # Form b: name(args) returns expected
        r"(\w+)\(([^)]*)\)\s+returns?\s+([^&;]+?)(?:\s+(?:and|or)|;|$)",
        # Form c: name(args) should return expected
        r"(\w+)\(([^)]*)\)\s+should\s+return\s+([^&;]+?)(?:\s+(?:and|or)|;|$)",
        # Form d: name(args) -> expected or =>
        r"(\w+)\(([^)]*)\)\s+(?:->|=>)\s+([^&;]+?)(?:\s+(?:and|or)|;|$)",
    ]

    seen_examples = set()  # to dedupe by (name, args_str, expected_str)
    for line_num, line in enumerate(lines, 1):
        # Check form e first: "expected E but got G" / "got G, expected E" / "returns G instead of E" / "returns G, expected E"
        # This must be checked first to avoid form d "returns" from matching the same line
        form_e_found = False
        expected_val = None

        form_e_match = re.search(r'expected\s+(.+?)\s+but\s+got', line)
        if form_e_match:
            expected_val = form_e_match.group(1).strip()
            form_e_found = True
        if not form_e_found:
            form_e_match = re.search(r'got\s+.+?,\s+expected\s+(.+?)(?:\s|$)', line)
            if form_e_match:
                expected_val = form_e_match.group(1).strip()
                form_e_found = True
        if not form_e_found:
            form_e_match = re.search(r'returns\s+(.+?)\s+instead\s+of\s+(.+?)(?:\s|$)', line)
            if form_e_match:
                expected_val = form_e_match.group(2).strip()  # the "instead of" value is expected
                form_e_found = True
        if not form_e_found:
            form_e_match = re.search(r'returns\s+.+?,\s+expected\s+(.+?)(?:\s|$)', line)
            if form_e_match:
                expected_val = form_e_match.group(1).strip()
                form_e_found = True

        if form_e_found and expected_val:
            # Look for a function call in the same line
            call_match = re.search(r'(\w+)\(([^)]*)\)', line)
            if call_match:
                name = call_match.group(1)
                args_str = call_match.group(2).strip()
                form_e_key = (name, args_str, expected_val)
                if form_e_key not in seen_examples:
                    try:
                        args = _parse_args(args_str)
                        expected = ast.literal_eval(expected_val)
                        examples.append({
                            "name": name, "args": args, "expected": expected,
                            "source_line": line_num
                        })
                        seen_examples.add(form_e_key)
                    except (ValueError, SyntaxError):
                        pass  # skip unparseable
            # Skip form a-d patterns if form e matched to avoid double-counting
            continue

        # Check forms a-d
        for pattern in example_patterns:
            for match in re.finditer(pattern, line):
                name = match.group(1)
                args_str = match.group(2).strip()
                expected_str = match.group(3).strip().rstrip('.,;:')

                key = (name, args_str, expected_str)
                if key in seen_examples:
                    continue

                try:
                    args = _parse_args(args_str)
                    expected = ast.literal_eval(expected_str)
                    examples.append({
                        "name": name, "args": args, "expected": expected,
                        "source_line": line_num
                    })
                    seen_examples.add(key)
                except (ValueError, SyntaxError):
                    pass  # skip unparseable

    # Sort examples by first occurrence (source_line)
    examples = sorted(examples, key=lambda e: e["source_line"])

    # Extract code blocks
    code_blocks: list[dict] = []
    code_block_re = re.compile(r'```(\w*)\s*\n(.*?)\n```', re.DOTALL)
    for match in code_block_re.finditer(text):
        lang = match.group(1) or "text"
        code = match.group(2)
        code_blocks.append({"lang": lang, "code": code})

    # Extract tracebacks: File "path", line N ... ErrorType: message
    tracebacks: list[dict] = []
    traceback_re = re.compile(
        r'File\s+"([^"]+)",\s+line\s+(\d+).*?(\w+Error):\s*(.+?)(?=\n|$)',
        re.DOTALL
    )
    for match in traceback_re.finditer(text):
        file_path = match.group(1)
        line_no = int(match.group(2))
        error_type = match.group(3)
        message = match.group(4).strip()
        tracebacks.append({
            "file": file_path, "line": line_no,
            "error": f"{error_type}: {message}"
        })

    # Extract backticked file/function refs
    files_found = []
    functions_found = []
    backtick_re = re.compile(r'`([^`]+)`')
    for match in backtick_re.finditer(text):
        token = match.group(1)
        if "/" in token or token.endswith((".py", ".js", ".ts", ".go", ".rs", ".txt")):
            files_found.append(token)
        elif token.endswith("()") or token.isidentifier():
            functions_found.append(token.rstrip("()"))

    # Remove duplicates, keep order of first occurrence
    seen_files = set()
    unique_files = []
    for f in files_found:
        if f not in seen_files:
            unique_files.append(f)
            seen_files.add(f)

    seen_funcs = set()
    unique_funcs = []
    for fn in functions_found:
        if fn not in seen_funcs:
            unique_funcs.append(fn)
            seen_funcs.add(fn)

    # Intent flags: keyword tables scanned over lowercased words
    lower_text = text.lower()
    wants_fix = any(kw in lower_text for kw in
                   ["fix", "bug", "broken", "wrong", "incorrect", "fails",
                    "failing", "error", "crash", "regression"])
    wants_new = any(kw in lower_text for kw in
                   ["build", "create", "implement", "need a", "new", "develop"])
    wants_tests = any(kw in lower_text for kw in ["test", "tests", "coverage"])

    # Stack detection: reuse detcode.stacks.match()/get()
    from .. import stacks
    words = set(re.findall(r'\b\w+\b', lower_text))
    stack_matches = stacks.match(words)
    detected_stack = stack_matches[0][0].key if stack_matches else None

    # Extract direction: first sentence matching "build/create/make/start a <direction>"
    direction = None
    direction_re = re.compile(
        r'(?:build|create|make|start)\s+a(?:n)?\s+([^.!?\n]+)',
        re.IGNORECASE
    )
    for match in direction_re.finditer(text):
        direction = match.group(1).strip()
        break

    # If no direction from CNL pattern and wants_new, use title
    if not direction and wants_new:
        direction = title

    return {
        "title": title,
        "examples": examples,
        "code_blocks": code_blocks,
        "tracebacks": tracebacks,
        "files": unique_files,
        "functions": unique_funcs,
        "flags": {
            "wants_fix": wants_fix,
            "wants_new": wants_new,
            "wants_tests": wants_tests,
        },
        "stack": detected_stack,
        "direction": direction,
        "lines": lines,
    }


def compile_ticket(parsed: dict, workspace: dict[str, str] | None) -> dict:
    """Compile a parsed ticket into actions and questions.

    Actions:
    - repair: fix existing function with examples
    - synth: synthesize new function from examples
    - gentest: generate tests
    - new: build a new project

    Returns dict with keys:
    - actions: list of {kind, target, name?, examples?, spec?, provenance?, ...}
    - questions: list of strings
    - decisions: list of {text, justification} strings
    """
    actions = []
    questions = []
    decisions = []

    # Group examples by function name
    examples_by_name: dict[str, list[dict]] = {}
    for ex in parsed["examples"]:
        name = ex["name"]
        if name not in examples_by_name:
            examples_by_name[name] = []
        examples_by_name[name].append(ex)

    # Process each function with examples
    for func_name in sorted(examples_by_name.keys()):
        func_examples = examples_by_name[func_name]

        # Find which line the first example appears on
        first_line_no = func_examples[0]["source_line"]
        line_text = parsed["lines"][first_line_no - 1] if first_line_no <= len(parsed["lines"]) else ""

        # Prepare spec for this function
        spec = {
            "name": func_name,
            "examples": [
                {"in": ex["args"], "out": ex["expected"]}
                for ex in func_examples
            ]
        }

        # Where is this function defined — a workspace file, or a python
        # code block in the ticket itself (the block wins when both match)?
        target_file = _find_definition(func_name, workspace, parsed["code_blocks"])

        if target_file:
            # Action: repair the existing function
            actions.append({
                "kind": "repair",
                "target": target_file,
                "name": func_name,
                "examples": spec["examples"],
                "provenance": [first_line_no],
            })
            decision = f"repair {func_name} — line {first_line_no}: \"{line_text.strip()}\""
            decisions.append(decision)
        else:
            # Actions: synth + gentest
            actions.append({
                "kind": "synth",
                "name": func_name,
                "examples": spec["examples"],
                "provenance": [first_line_no],
            })
            actions.append({
                "kind": "gentest",
                "name": func_name,
                "examples": spec["examples"],
                "provenance": [first_line_no],
            })
            decision = f"synthesize — line {first_line_no}: \"{line_text.strip()}\""
            decisions.append(decision)

    # If no examples matched but wants_new and has direction, action to build project
    if not actions and parsed["flags"]["wants_new"] and parsed["direction"]:
        actions.append({
            "kind": "new",
            "direction": parsed["direction"],
            "stack": parsed["stack"],
            "provenance": [1],
        })
        decision = f"new project — line 1: \"{parsed['lines'][0].strip()}\""
        decisions.append(decision)

    # Open questions — per function: every referenced function with no
    # examples (and therefore no action) becomes one precise question, even
    # when other functions in the same ticket produced actions.
    for func_name in parsed["functions"]:
        if func_name in examples_by_name:
            continue  # already actioned above
        if parsed["flags"]["wants_tests"]:
            questions.append(f"give one example: {func_name}(...) == ?")
        elif parsed["flags"]["wants_fix"]:
            questions.append(f"which call is wrong? give: {func_name}(args) == expected")
        elif _find_definition(func_name, workspace, parsed["code_blocks"]):
            questions.append(
                f"function {func_name} has no example — give one: {func_name}(...) == ?"
            )
        else:
            questions.append(
                f"function {func_name} is referenced but not found — give an example?"
            )

    # A fix ticket that names only files still gets the generic question.
    if (parsed["flags"]["wants_fix"] and parsed["files"]
            and not parsed["functions"] and not examples_by_name):
        questions.append("which call is wrong? give: name(args) == expected")

    # If completely vague (no actions and no questions), raise TicketError
    if not actions and not questions:
        raise TicketError(
            f"ticket is too vague to compile. please provide: "
            f"(1) which function or behavior you need, "
            f"(2) one input/output example, "
            f"(3) which file (if fixing existing code)"
        )

    return {
        "actions": actions,
        "questions": questions,
        "decisions": decisions,
    }


def run_ticket(text: str, files: dict[str, str] | None = None, store=None) -> Result:
    """Execute a compiled ticket plan.

    repair: locate source (workspace or ticket code), run repair.repair();
    record ok/refused/updated content in files_out.

    synth: retrieve.write_function() → files_out at "<name>.py"; companion
    gentest → files_out at "tests/test_<name>.py".

    new: builder.build() → merge project.files into files_out.

    Log questions to store.log_question() when store is not None.

    Return {"ok": True/False, "kind": "ticket", "output": report text,
    "files": files_out or None, "report": provenance dict}.

    Raises TicketError for vague/malformed tickets.
    """
    parsed = parse_ticket(text)
    compiled = compile_ticket(parsed, files)  # raises TicketError if too vague

    files_out: dict[str, str] = {}
    output_lines = []
    decisions_out = list(compiled["decisions"])

    # Execute actions in order
    for action in compiled["actions"]:
        kind = action["kind"]

        if kind == "repair":
            try:
                target = action["target"]
                source = files.get(target) if files and target != "ticket-code" else None
                if not source:
                    for block in parsed["code_blocks"]:
                        if block["lang"] == "python":
                            source = block["code"]
                            break

                if not source:
                    output_lines.append(f"✗ repair {action['name']}: source not found")
                    continue

                spec = {"function": action["name"], "examples": action["examples"]}
                result = repair_engine.repair(source, spec)
                files_out[target if target != "ticket-code" else f"{action['name']}.py"] = result.source
                line = f"✓ repair {action['name']}"
                if len(action["examples"]) == 1:
                    note = _single_example_note(action["name"])
                    line += f" — {note}"
                    decisions_out.append(note)
                output_lines.append(line)
            except (repair_engine.SpecError, repair_engine.NoRepair) as exc:
                output_lines.append(f"✗ repair {action['name']}: {exc}")

        elif kind == "synth":
            try:
                name = action["name"]
                spec = {"name": name, "examples": action["examples"]}
                extra = planner.corpus_entries(store) if store else ()
                result = retrieve.write_function(spec, extra=extra)
                files_out[f"{name}.py"] = result.source
                line = f"✓ synth {name}"
                if len(action["examples"]) == 1:
                    note = _single_example_note(name)
                    line += f" — {note}"
                    decisions_out.append(note)
                output_lines.append(line)
            except (retrieve.NoMatch, synth_engine.NoSolution, synth_engine.SpecError) as exc:
                output_lines.append(f"✗ synth {action['name']}: {exc}")

        elif kind == "gentest":
            try:
                name = action["name"]
                spec = {
                    "function": name,
                    "examples": action["examples"],
                    "module": f"{name}",  # import from {name}
                }
                result = gentest_engine.gentest(spec)
                files_out[f"tests/test_{name}.py"] = result.source
                output_lines.append(f"✓ gentest {name}")
            except gentest_engine.SpecError as exc:
                output_lines.append(f"✗ gentest {action['name']}: {exc}")

        elif kind == "new":
            try:
                project = builder.build(
                    action["direction"],
                    stack=action["stack"],
                    extra_packs=tuple(store.user_packs()) if store else (),
                )
                for f in project.files:
                    files_out[f.path] = f.content
                output_lines.append(f"✓ new {action['direction']}")
            except builder.BuildError as exc:
                output_lines.append(f"✗ new {action['direction']}: {exc}")

    # Log questions to store
    for question in compiled["questions"]:
        if store:
            keywords = []
            # Extract function names as keywords
            for func_name in parsed["functions"]:
                if func_name in question:
                    keywords.append(func_name)
            # Or direction words
            if not keywords and parsed["direction"]:
                keywords = parsed["direction"].split()[:2]
            store.log_question(question, keywords)
        output_lines.append(f"? {question}")

    ok = not any(line.startswith("✗") for line in output_lines)
    output = "\n".join(output_lines) if output_lines else "(no actions)"

    report = provenance(
        "ticket",
        RULE_VERSION,
        actions=[{"kind": a["kind"]} for a in compiled["actions"]],
        questions=compiled["questions"],
        decisions=decisions_out,
    )

    return Result(
        ok=ok,
        kind="ticket",
        output=output,
        files=files_out if files_out else None,
        report=report,
    )


def _defines(source: str, func_name: str) -> bool:
    """True when ``source`` parses and defines a function named ``func_name``."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == func_name
        for node in ast.walk(tree)
    )


def _find_definition(func_name: str, workspace: dict[str, str] | None,
                     code_blocks: list[dict]) -> str | None:
    """The repair target for ``func_name``: a workspace path, "ticket-code"
    for a python code block in the ticket, or None when nowhere defined."""
    target = None
    if workspace:
        for file_path, file_content in workspace.items():
            if _defines(file_content, func_name):
                target = file_path
                break
    for block in code_blocks:
        if block["lang"] == "python" and _defines(block["code"], func_name):
            target = "ticket-code"
            break
    return target


def _single_example_note(name: str) -> str:
    """Honesty note: one example is a weak oracle for a derived change."""
    return (
        f"note: derived from a single example — add a second "
        f"(e.g. {name}(...) == ?) to pin the behavior"
    )


def _parse_args(args_str: str) -> list:
    """Parse comma-separated literal arguments."""
    if not args_str.strip():
        return []
    try:
        # Wrap in list literal for ast.literal_eval
        return ast.literal_eval(f"[{args_str}]")
    except (ValueError, SyntaxError):
        return []
