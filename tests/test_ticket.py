import os
import shutil
import tempfile
import textwrap
import unittest

from detcode.determinism import canonical_json
from detcode.engines import ticket


def dedent(s: str) -> str:
    return textwrap.dedent(s).lstrip("\n")


def run(source, name):
    """Execute source and return the named function."""
    ns: dict = {}
    exec(compile(source, "<t>", "exec"), ns)
    return ns[name]


class ParseTicketTests(unittest.TestCase):
    def test_parse_example_form_a_equals(self):
        """Extract name(args) == expected form."""
        text = "area(2, 3) == 6"
        parsed = ticket.parse_ticket(text)
        self.assertEqual(len(parsed["examples"]), 1)
        self.assertEqual(parsed["examples"][0]["name"], "area")
        self.assertEqual(parsed["examples"][0]["args"], [2, 3])
        self.assertEqual(parsed["examples"][0]["expected"], 6)

    def test_parse_example_form_a_backticked(self):
        """Extract name(args) == expected from backticks."""
        text = "The function `area(2, 3) == 6` is broken"
        parsed = ticket.parse_ticket(text)
        self.assertEqual(len(parsed["examples"]), 1)
        self.assertEqual(parsed["examples"][0]["name"], "area")
        self.assertEqual(parsed["examples"][0]["expected"], 6)

    def test_parse_example_form_b_returns(self):
        """Extract name(args) returns expected form."""
        text = "double(5) returns 10"
        parsed = ticket.parse_ticket(text)
        self.assertEqual(len(parsed["examples"]), 1)
        self.assertEqual(parsed["examples"][0]["name"], "double")
        self.assertEqual(parsed["examples"][0]["args"], [5])
        self.assertEqual(parsed["examples"][0]["expected"], 10)

    def test_parse_example_form_c_should_return(self):
        """Extract name(args) should return expected form."""
        text = "is_prime(7) should return True"
        parsed = ticket.parse_ticket(text)
        self.assertEqual(len(parsed["examples"]), 1)
        self.assertEqual(parsed["examples"][0]["name"], "is_prime")
        self.assertEqual(parsed["examples"][0]["expected"], True)

    def test_parse_example_form_d_arrow(self):
        """Extract name(args) -> expected form."""
        text = "factorial(5) -> 120"
        parsed = ticket.parse_ticket(text)
        self.assertEqual(len(parsed["examples"]), 1)
        self.assertEqual(parsed["examples"][0]["name"], "factorial")
        self.assertEqual(parsed["examples"][0]["expected"], 120)

    def test_parse_example_form_d_fat_arrow(self):
        """Extract name(args) => expected form."""
        text = "sum_list([1, 2, 3]) => 6"
        parsed = ticket.parse_ticket(text)
        self.assertEqual(len(parsed["examples"]), 1)
        self.assertEqual(parsed["examples"][0]["name"], "sum_list")
        self.assertEqual(parsed["examples"][0]["expected"], 6)

    def test_parse_example_form_e_expected_but_got(self):
        """Extract 'expected E but got G' form with function call."""
        text = "expected 6 but got 5 for area(2, 3)"
        parsed = ticket.parse_ticket(text)
        self.assertEqual(len(parsed["examples"]), 1)
        self.assertEqual(parsed["examples"][0]["name"], "area")
        self.assertEqual(parsed["examples"][0]["expected"], 6)

    def test_parse_example_form_e_got_expected(self):
        """Extract 'got G, expected E' form."""
        text = "got 5, expected 6 when calling add(2, 3)"
        parsed = ticket.parse_ticket(text)
        self.assertEqual(len(parsed["examples"]), 1)
        self.assertEqual(parsed["examples"][0]["name"], "add")
        self.assertEqual(parsed["examples"][0]["expected"], 6)

    def test_parse_example_form_e_returns_instead(self):
        """Extract 'returns G instead of E' form."""
        text = "multiply(2, 3) returns 5 instead of 6"
        parsed = ticket.parse_ticket(text)
        self.assertEqual(len(parsed["examples"]), 1)
        self.assertEqual(parsed["examples"][0]["name"], "multiply")
        self.assertEqual(parsed["examples"][0]["expected"], 6)

    def test_parse_code_block_python(self):
        """Extract Python code blocks."""
        text = dedent("""
            Here's the code:
            ```python
            def area(w, h):
                return w + h
            ```
            Fix it!
        """)
        parsed = ticket.parse_ticket(text)
        self.assertEqual(len(parsed["code_blocks"]), 1)
        self.assertEqual(parsed["code_blocks"][0]["lang"], "python")
        self.assertIn("def area", parsed["code_blocks"][0]["code"])

    def test_parse_traceback(self):
        """Extract traceback file/line/error."""
        text = dedent("""
            Error when running:
            File "src/utils.py", line 42
                result = area(2, 3)
            AssertionError: values don't match
        """)
        parsed = ticket.parse_ticket(text)
        self.assertEqual(len(parsed["tracebacks"]), 1)
        self.assertEqual(parsed["tracebacks"][0]["file"], "src/utils.py")
        self.assertEqual(parsed["tracebacks"][0]["line"], 42)
        self.assertIn("AssertionError", parsed["tracebacks"][0]["error"])

    def test_parse_backticked_file_refs(self):
        """Extract backticked file paths."""
        text = "Check `src/area.py` and `tests/test_area.py`"
        parsed = ticket.parse_ticket(text)
        self.assertIn("src/area.py", parsed["files"])
        self.assertIn("tests/test_area.py", parsed["files"])

    def test_parse_backticked_function_refs(self):
        """Extract backticked function names."""
        text = "The `area` and `perimeter()` functions are broken"
        parsed = ticket.parse_ticket(text)
        self.assertIn("area", parsed["functions"])
        self.assertIn("perimeter", parsed["functions"])

    def test_parse_wants_fix_flag(self):
        """Detect wants_fix intent flags."""
        text = "The area function is broken"
        parsed = ticket.parse_ticket(text)
        self.assertTrue(parsed["flags"]["wants_fix"])

        text = "area(2, 3) crashes"
        parsed = ticket.parse_ticket(text)
        self.assertTrue(parsed["flags"]["wants_fix"])

    def test_parse_wants_new_flag(self):
        """Detect wants_new intent flags."""
        text = "Build a resume tailorer"
        parsed = ticket.parse_ticket(text)
        self.assertTrue(parsed["flags"]["wants_new"])

        text = "We need a new function double"
        parsed = ticket.parse_ticket(text)
        self.assertTrue(parsed["flags"]["wants_new"])

    def test_parse_wants_tests_flag(self):
        """Detect wants_tests intent flags."""
        text = "Add tests for the area function"
        parsed = ticket.parse_ticket(text)
        self.assertTrue(parsed["flags"]["wants_tests"])

    def test_parse_direction_from_cnl(self):
        """Extract direction from 'build/create a X' pattern."""
        text = "Build a todo app in flask"
        parsed = ticket.parse_ticket(text)
        self.assertIsNotNone(parsed["direction"])
        self.assertIn("todo", parsed["direction"])

    def test_parse_title_from_first_line(self):
        """Extract title from first non-empty line."""
        text = "Bug: area returns wrong value\nDetails here..."
        parsed = ticket.parse_ticket(text)
        self.assertEqual(parsed["title"], "area returns wrong value")

    def test_parse_title_strips_prefixes(self):
        """Strip bug:/feature:/task: prefixes from title."""
        text = "feature: add a new function"
        parsed = ticket.parse_ticket(text)
        self.assertEqual(parsed["title"], "add a new function")

    def test_parse_determinism_idempotent(self):
        """Same ticket parsed twice yields identical canonical_json."""
        text = dedent("""
            Build a todo app in flask where:
            - add_todo(text) == todo_id (given non-empty text)
            - get_todo(todo_id) returns the text
        """)
        parsed1 = ticket.parse_ticket(text)
        parsed2 = ticket.parse_ticket(text)
        # Compare serialized form for true determinism
        j1 = canonical_json({
            "examples": parsed1["examples"],
            "functions": parsed1["functions"],
            "flags": parsed1["flags"],
        })
        j2 = canonical_json({
            "examples": parsed2["examples"],
            "functions": parsed2["functions"],
            "flags": parsed2["flags"],
        })
        self.assertEqual(j1, j2)


class CompileTicketTests(unittest.TestCase):
    def test_compile_repair_action_when_def_exists_in_workspace(self):
        """When function exists in workspace, emit repair action."""
        parsed = {
            "title": "fix area",
            "examples": [
                {"name": "area", "args": [2, 3], "expected": 6, "source_line": 5}
            ],
            "code_blocks": [],
            "tracebacks": [],
            "files": [],
            "functions": [],
            "flags": {"wants_fix": True, "wants_new": False, "wants_tests": False},
            "stack": None,
            "direction": None,
            "lines": ["area(2, 3) should equal 6"],
        }
        workspace = {
            "area.py": "def area(w, h):\n    return w + h\n"
        }
        compiled = ticket.compile_ticket(parsed, workspace)
        self.assertEqual(len(compiled["actions"]), 1)
        self.assertEqual(compiled["actions"][0]["kind"], "repair")
        self.assertEqual(compiled["actions"][0]["name"], "area")

    def test_compile_synth_gentest_when_no_def_in_workspace(self):
        """When function doesn't exist, emit synth + gentest actions."""
        parsed = {
            "title": "implement double",
            "examples": [
                {"name": "double", "args": [2], "expected": 4, "source_line": 1}
            ],
            "code_blocks": [],
            "tracebacks": [],
            "files": [],
            "functions": [],
            "flags": {"wants_fix": False, "wants_new": False, "wants_tests": False},
            "stack": None,
            "direction": None,
            "lines": ["double(2) == 4"],
        }
        compiled = ticket.compile_ticket(parsed, None)
        self.assertEqual(len(compiled["actions"]), 2)
        self.assertEqual(compiled["actions"][0]["kind"], "synth")
        self.assertEqual(compiled["actions"][1]["kind"], "gentest")
        self.assertEqual(compiled["actions"][0]["name"], "double")

    def test_compile_new_action_when_wants_new_and_direction(self):
        """When wants_new and has direction but no examples, emit new action."""
        parsed = {
            "title": "build a resume tailorer",
            "examples": [],
            "code_blocks": [],
            "tracebacks": [],
            "files": [],
            "functions": [],
            "flags": {"wants_fix": False, "wants_new": True, "wants_tests": False},
            "stack": "flask",
            "direction": "resume tailorer",
            "lines": ["Build a resume tailorer"],
        }
        compiled = ticket.compile_ticket(parsed, None)
        self.assertEqual(len(compiled["actions"]), 1)
        self.assertEqual(compiled["actions"][0]["kind"], "new")
        self.assertEqual(compiled["actions"][0]["direction"], "resume tailorer")

    def test_compile_question_for_function_with_no_examples(self):
        """When wants_tests and no examples, emit question."""
        parsed = {
            "title": "test area",
            "examples": [],
            "code_blocks": [],
            "tracebacks": [],
            "files": [],
            "functions": ["area"],
            "flags": {"wants_fix": False, "wants_new": False, "wants_tests": True},
            "stack": None,
            "direction": None,
            "lines": ["test area"],
        }
        compiled = ticket.compile_ticket(parsed, None)
        self.assertTrue(len(compiled["questions"]) > 0)
        self.assertIn("area", compiled["questions"][0])

    def test_compile_error_when_too_vague(self):
        """Raise TicketError when completely vague (no actions, no questions)."""
        parsed = {
            "title": "something",
            "examples": [],
            "code_blocks": [],
            "tracebacks": [],
            "files": [],
            "functions": [],
            "flags": {"wants_fix": False, "wants_new": False, "wants_tests": False},
            "stack": None,
            "direction": None,
            "lines": ["something"],
        }
        with self.assertRaises(ticket.TicketError):
            ticket.compile_ticket(parsed, None)

    def test_compile_repair_from_ticket_code_block(self):
        """Repair action from function in ticket code block."""
        parsed = {
            "title": "fix area",
            "examples": [
                {"name": "area", "args": [2, 3], "expected": 6, "source_line": 1}
            ],
            "code_blocks": [
                {
                    "lang": "python",
                    "code": dedent("""
                        def area(w, h):
                            return w + h
                    """),
                }
            ],
            "tracebacks": [],
            "files": [],
            "functions": [],
            "flags": {"wants_fix": True, "wants_new": False, "wants_tests": False},
            "stack": None,
            "direction": None,
            "lines": ["area(2, 3) == 6"],
        }
        compiled = ticket.compile_ticket(parsed, None)
        self.assertEqual(len(compiled["actions"]), 1)
        self.assertEqual(compiled["actions"][0]["kind"], "repair")
        self.assertEqual(compiled["actions"][0]["target"], "ticket-code")


class RunTicketTests(unittest.TestCase):
    def test_run_ticket_repair_bug_with_examples(self):
        """Execute repair action: buggy code + examples → fixed code."""
        ticket_text = dedent("""
            The area function is broken:
            area(2, 3) returns 5, expected 6
            area(4, 5) returns 9, expected 20
        """)
        buggy_code = dedent("""
            def area(w, h):
                return w + h
        """)
        files = {"area.py": buggy_code}

        result = ticket.run_ticket(ticket_text, files=files)
        self.assertTrue(result.ok, f"Failed: {result.output}")
        self.assertIn("✓", result.output)
        self.assertIsNotNone(result.files)
        self.assertIn("area.py", result.files)
        repaired = result.files["area.py"]
        # Verify the repaired code works
        area_fn = run(repaired, "area")
        self.assertEqual(area_fn(2, 3), 6)
        self.assertEqual(area_fn(4, 5), 20)

    def test_run_ticket_synth_new_function(self):
        """Execute synth + gentest: examples → synthesized function + tests."""
        ticket_text = dedent("""
            We need a function double where:
            double(2) == 4
            double(3) == 6
            double(5) == 10
        """)
        result = ticket.run_ticket(ticket_text, files=None)
        self.assertTrue(result.ok, f"Failed: {result.output}")
        self.assertIn("✓", result.output)
        self.assertIsNotNone(result.files)
        self.assertIn("double.py", result.files)
        self.assertIn("tests/test_double.py", result.files)

    def test_run_ticket_mixed_ok_and_question(self):
        """Mixed ticket: one repair action succeeds AND the example-less
        function still gets its precise open question."""
        from detcode.store import Store

        ticket_text = dedent("""
            Fix area: area(2, 3) == 6 and area(1, 5) == 5
            Also need tests for the `perimeter` function
        """)
        files = {
            "area.py": "def area(w, h):\n    return w + h\n"
        }
        tmp = tempfile.mkdtemp()
        try:
            store = Store(os.path.join(tmp, "detcode.db"))
            result = ticket.run_ticket(ticket_text, files=files, store=store)
            self.assertTrue(result.ok)
            # Exactly one repair action, and it succeeded.
            self.assertEqual(result.report["actions"], [{"kind": "repair"}])
            self.assertIn("✓ repair area", result.output)
            # Exactly one question, and it names perimeter precisely.
            self.assertEqual(len(result.report["questions"]), 1)
            self.assertIn("perimeter", result.report["questions"][0])
            self.assertIn("perimeter", result.output)
            # The question landed in the study queue.
            logged = [q["question"] for q in store.open_questions()]
            self.assertEqual(len(logged), 1)
            self.assertIn("perimeter", logged[0])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_fallback_question_wording_tracks_definition(self):
        """Regression: a referenced function that IS defined in the workspace
        gets asked for an example — not called "not found"."""
        text = "Please look at the `perimeter` helper sometime"
        workspace = {"geo.py": "def perimeter(w, h):\n    return 2 * (w + h)\n"}
        found = ticket.compile_ticket(ticket.parse_ticket(text), workspace)
        self.assertEqual(
            found["questions"],
            ["function perimeter has no example — give one: perimeter(...) == ?"],
        )
        # Genuinely absent: the honest "not found" wording stays.
        absent = ticket.compile_ticket(ticket.parse_ticket(text), None)
        self.assertEqual(
            absent["questions"],
            ["function perimeter is referenced but not found — give an example?"],
        )

    def test_compile_repair_decision_names_the_function(self):
        """Regression: the repair decision must quote the actual function."""
        parsed = ticket.parse_ticket("foo(2,3) returns 5, expected 6")
        compiled = ticket.compile_ticket(
            parsed, {"m.py": "def foo(w, h):\n    return w + h\n"}
        )
        self.assertEqual(compiled["actions"][0]["kind"], "repair")
        self.assertTrue(compiled["decisions"][0].startswith("repair foo"),
                        compiled["decisions"][0])
        self.assertNotIn("area", compiled["decisions"][0])

    def test_single_example_action_carries_honesty_note(self):
        """A repair driven by one example warns that one example is a weak
        oracle, in both the report line and the decisions."""
        files = {"area.py": "def area(w, h):\n    return w + h\n"}
        result = ticket.run_ticket("Fix `area`: area(2, 3) == 6", files=files)
        self.assertTrue(result.ok)
        self.assertIn("derived from a single example", result.output)
        self.assertIn("area(...) == ?", result.output)
        notes = [d for d in result.report["decisions"] if "single example" in d]
        self.assertEqual(len(notes), 1)
        # Two examples: no note.
        result2 = ticket.run_ticket(
            "Fix `area`: area(2, 3) == 6 and area(4, 5) == 20", files=files
        )
        self.assertNotIn("single example", result2.output)

    def test_run_ticket_determinism_idempotent(self):
        """Run twice on same input → byte-identical canonical_json report."""
        ticket_text = "area(2, 3) == 6"
        files = {"area.py": "def area(w, h):\n    return w + h\n"}

        result1 = ticket.run_ticket(ticket_text, files=files)
        result2 = ticket.run_ticket(ticket_text, files=files)

        j1 = canonical_json(result1.report)
        j2 = canonical_json(result2.report)
        self.assertEqual(j1, j2)

    def test_run_ticket_service_integration_ok_path(self):
        """Service layer: tool='ticket' → ok response with files."""
        from detcode import service

        req = {
            "tool": "ticket",
            "text": "area(2, 3) == 6\n```python\ndef area(w, h):\n    return w + h\n```",
            "files": {"area.py": "def area(w, h):\n    return w + h\n"}
        }
        resp = service.run_request(req, store=None)
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["kind"], "text")
        self.assertIsNotNone(resp.get("files"))

    def test_run_ticket_service_integration_refusal_path(self):
        """Service layer: vague ticket → refused response."""
        from detcode import service

        req = {
            "tool": "ticket",
            "text": "something vague",
        }
        resp = service.run_request(req, store=None)
        self.assertFalse(resp["ok"])
        self.assertTrue(resp.get("refused"))


class CliExitCodeTests(unittest.TestCase):
    """detcode ticket exit codes: 0 = actions all ran, 1 = questions-only or
    refused action, 2 = the ticket itself was refused as too vague."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.prev_cwd = os.getcwd()
        os.chdir(self.dir)  # keep the CLI away from the repo's .detcode store
        self.ws = os.path.join(self.dir, "ws")
        os.makedirs(self.ws)

    def tearDown(self):
        os.chdir(self.prev_cwd)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _ticket_file(self, text):
        path = os.path.join(self.dir, "ticket.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def _main(self, path):
        from detcode.cli import main

        return main(["ticket", path, "--dir", self.ws])

    def test_vague_ticket_exits_2(self):
        self.assertEqual(self._main(self._ticket_file("something vague\n")), 2)

    def test_questions_only_ticket_exits_1(self):
        path = self._ticket_file("Add tests for the `area` function\n")
        self.assertEqual(self._main(path), 1)

    def test_successful_repair_exits_0(self):
        with open(os.path.join(self.ws, "area.py"), "w", encoding="utf-8") as fh:
            fh.write("def area(w, h):\n    return w + h\n")
        path = self._ticket_file(
            "Fix `area`: area(2, 3) == 6 and area(4, 5) == 20\n"
        )
        self.assertEqual(self._main(path), 0)


if __name__ == "__main__":
    unittest.main()
