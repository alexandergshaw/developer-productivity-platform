import textwrap
import unittest

from detcode import service
from detcode.determinism import canonical_json
from detcode.engines import converse
from detcode.engines.converse import ConverseError, normalize


def dedent(s: str) -> str:
    return textwrap.dedent(s).lstrip("\n")


def run(source, name):
    ns: dict = {}
    exec(compile(source, "<t>", "exec"), ns)
    return ns[name]


class NormalizeTests(unittest.TestCase):
    def test_politeness_stripped(self):
        self.assertEqual(
            normalize("hey, can you please remove the unused imports?"),
            "remove unused imports",
        )

    def test_stacked_politeness_and_suffixes(self):
        self.assertEqual(
            normalize("ok so could you clean up for me, thanks!"),
            "clean up",
        )

    def test_synonym_folding(self):
        self.assertEqual(normalize("get rid of unused imports"), "remove unused imports")
        self.assertEqual(normalize("tidy up"), "clean up")
        self.assertEqual(normalize("make me a resume tailorer"), "build a resume tailorer")

    def test_add_a_docstring_to_survives_folding(self):
        # "add a docstring to <func>" is grammar; only the bare phrase folds.
        self.assertEqual(normalize("add a docstring to area"), "add a docstring to area")

    def test_typo_repair(self):
        self.assertEqual(normalize("remvoe unused improts"), "remove unused imports")

    def test_typo_repair_skips_call_names_and_protected_spans(self):
        # "duble" names a call, so it is never snapped to a vocabulary word.
        out = normalize('fix duble so that duble(2) == 4')
        self.assertIn("duble(2)", out)
        # Quoted content is untouched.
        self.assertIn('"remvoe THIS"', normalize('explain what "remvoe THIS" does'))

    def test_contractions(self):
        self.assertTrue(normalize("what's the deal").startswith("what is"))

    def test_deterministic(self):
        text = "hey could you please get rid of the unused improts, thanks"
        self.assertEqual(normalize(text), normalize(text))


class SlotFillingTests(unittest.TestCase):
    def test_write_a_function_asks_for_examples(self):
        resp = converse.converse("write a function double", None, None)
        self.assertTrue(resp["ok"])
        self.assertIn("give one example", resp["output"])
        self.assertIn("double", resp["output"])
        pending = resp["state"]["pending"]
        self.assertEqual(pending["kind"], "examples")
        self.assertEqual(pending["name"], "double")
        self.assertEqual(pending["action"], "synth")

    def test_full_example_reply_synthesizes(self):
        first = converse.converse("write a function double", None, None)
        resp = converse.converse("double(2) == 4 and double(3) == 6", first["state"], None)
        self.assertEqual(resp["kind"], "generated")
        self.assertIn("def double", resp["output"])
        self.assertEqual(run(resp["output"], "double")(2), 4)
        self.assertIn("double.py", resp["files"])
        self.assertIn("tests/test_double.py", resp["files"])
        self.assertIn("class TestDouble", resp["files"]["tests/test_double.py"])
        self.assertIsNone(resp["state"]["pending"])
        self.assertEqual(resp["state"]["last_function"], "double")

    def test_shorthand_reply_works(self):
        first = converse.converse("write a function double", None, None)
        resp = converse.converse("2 -> 4", first["state"], None)
        self.assertEqual(resp["kind"], "generated")
        self.assertEqual(run(resp["output"], "double")(2), 4)

    def test_multi_arg_shorthand(self):
        first = converse.converse("write a function area", None, None)
        resp = converse.converse("2, 3 -> 6", first["state"], None)
        self.assertEqual(run(resp["output"], "area")(2, 3), 6)

    def test_three_garbage_replies_clear_pending(self):
        state = converse.converse("write a function double", None, None)["state"]
        for i in range(2):
            resp = converse.converse("banana banana", state, None)
            state = resp["state"]
            self.assertIsNotNone(state["pending"], f"pending lost after reply {i + 1}")
            self.assertIn("could not read an example", resp["output"])
        resp = converse.converse("banana banana", state, None)
        self.assertIsNone(resp["state"]["pending"])
        self.assertIn("giving up", resp["output"])

    def test_fix_without_examples_asks_when_source_has_def(self):
        source = "def area(w, h):\n    return w + h\n"
        resp = converse.converse("fix area", None, source)
        pending = resp["state"]["pending"]
        self.assertEqual(pending["action"], "repair")
        # Answering with the failing example repairs the file.
        done = converse.converse(
            "area(2, 3) == 6 and area(4, 5) == 20", resp["state"], source
        )
        self.assertEqual(done["kind"], "edit")
        self.assertTrue(done["changed"])
        self.assertEqual(run(done["output"], "area")(2, 3), 6)


class ReferenceResolutionTests(unittest.TestCase):
    def test_it_resolves_to_last_function(self):
        first = converse.converse("write a function double", None, None)
        made = converse.converse("double(2) == 4", first["state"], None)
        resp = converse.converse("generate tests for it", made["state"], made["output"])
        # gentest needs examples, so the engine asks — precisely about double.
        self.assertIn("double", resp["output"])
        pending = resp["state"]["pending"]
        self.assertEqual(pending["name"], "double")
        self.assertEqual(pending["action"], "gentest")
        # Supplying the example yields a test module for double.
        tests = converse.converse("double(5) == 10", resp["state"], made["output"])
        self.assertEqual(tests["kind"], "generated")
        self.assertIn("class TestDouble", tests["output"])

    def test_reference_without_context_asks_which_function(self):
        resp = converse.converse("generate tests for it", None, None)
        self.assertIn("which function", resp["output"])
        self.assertIsNone(resp["state"]["pending"])


class ConfirmFlowTests(unittest.TestCase):
    SOURCE = "import os\n\nx = 1\n"

    def _suggestion(self):
        # "unused imports" alone overlaps 2 tokens with "remove unused imports".
        return converse.converse("unused imports", None, self.SOURCE)

    def test_suggestion_sets_confirm_pending(self):
        resp = self._suggestion()
        self.assertIn("did you mean", resp["output"])
        self.assertIn("remove unused imports", resp["output"])
        self.assertEqual(resp["state"]["pending"]["kind"], "confirm")

    def test_yes_executes_the_command(self):
        resp = self._suggestion()
        done = converse.converse("yes", resp["state"], self.SOURCE)
        self.assertEqual(done["kind"], "edit")
        self.assertTrue(done["changed"])
        self.assertNotIn("import os", done["output"])
        self.assertIsNone(done["state"]["pending"])

    def test_no_cancels(self):
        resp = self._suggestion()
        done = converse.converse("no", resp["state"], self.SOURCE)
        self.assertIn("not doing that", done["output"])
        self.assertIsNone(done["state"]["pending"])

    def test_new_topic_implicitly_cancels_confirm(self):
        resp = self._suggestion()
        done = converse.converse("explain", resp["state"], self.SOURCE)
        self.assertIsNone(done["state"]["pending"])
        self.assertNotIn("did you mean", done["output"])

    def test_typoed_suggestion_lands_in_fuzzy_branch(self):
        resp = converse.converse("remvoe all the unused improts somehow", None, self.SOURCE)
        self.assertIn("did you mean", resp["output"])
        self.assertEqual(resp["state"]["pending"]["command"], "remove unused imports")


class CancelTests(unittest.TestCase):
    def test_never_mind_withdraws_the_question(self):
        first = converse.converse("write a function double", None, None)
        resp = converse.converse("never mind", first["state"], None)
        self.assertIsNone(resp["state"]["pending"])
        self.assertIn("withdrawn", resp["output"])


class DeterminismAndStateTests(unittest.TestCase):
    def _script(self):
        responses = []
        state = None
        for turn in ("write a function double", "banana", "double(2) == 4"):
            resp = converse.converse(turn, state, None)
            state = resp["state"]
            responses.append(resp)
        return responses

    def test_scripted_conversation_replays_identically(self):
        self.assertEqual(
            canonical_json(self._script()), canonical_json(self._script())
        )

    def test_input_state_is_never_mutated(self):
        first = converse.converse("write a function double", None, None)
        snapshot = canonical_json(first["state"])
        converse.converse("double(2) == 4", first["state"], None)
        self.assertEqual(canonical_json(first["state"]), snapshot)

    def test_history_is_capped(self):
        state = None
        for i in range(12):
            state = converse.converse(f"hello number {i}", state, None)["state"]
        self.assertEqual(len(state["history"]), 10)

    def test_malformed_state_is_refused(self):
        with self.assertRaises(ConverseError):
            converse.converse("hello", "not a dict", None)
        with self.assertRaises(ConverseError):
            converse.converse("hello", {"pending": "nope"}, None)


class SizeCapTests(unittest.TestCase):
    def test_huge_input_redirects_to_ticket(self):
        huge = "asdf qwer " * 10000  # 100 KB
        resp = converse.converse(huge, None, None)
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["kind"], "text")
        self.assertIn("use the ticket tool", resp["output"])
        self.assertIn(f"{len(huge)} characters", resp["output"])
        self.assertIsNone(resp["state"]["pending"])

    def test_history_entries_are_truncated(self):
        huge = "x" * 100000
        resp = converse.converse(huge, None, None)
        self.assertEqual(len(resp["state"]["history"]), 1)
        self.assertLessEqual(len(resp["state"]["history"][0]), 200)
        # Normal turns are truncated in history too.
        resp2 = converse.converse("y" * 1500, resp["state"], None)
        self.assertTrue(all(len(h) <= 200 for h in resp2["state"]["history"]))

    def test_cap_boundary_is_exact(self):
        at_cap = converse.converse("z" * converse.MAX_UTTERANCE, None, None)
        self.assertNotIn("use the ticket tool", at_cap["output"])
        over_cap = converse.converse("z" * (converse.MAX_UTTERANCE + 1), None, None)
        self.assertIn("use the ticket tool", over_cap["output"])

    def test_huge_input_is_deterministic(self):
        huge = "asdf qwer " * 10000
        first = converse.converse(huge, None, None)
        second = converse.converse(huge, None, None)
        self.assertEqual(canonical_json(first), canonical_json(second))


class ServiceTests(unittest.TestCase):
    def test_service_converse_returns_state_and_help(self):
        resp = service.run_request({"tool": "converse", "utterance": "hello there"})
        self.assertTrue(resp["ok"])
        self.assertIn("state", resp)
        self.assertIn("could not map", resp["output"])

    def test_service_round_trips_state(self):
        first = service.run_request(
            {"tool": "converse", "utterance": "write a function double"}
        )
        self.assertTrue(first["ok"])
        second = service.run_request(
            {"tool": "converse", "utterance": "double(2) == 4", "state": first["state"]}
        )
        self.assertTrue(second["ok"])
        self.assertIn("def double", second["output"])

    def test_service_refuses_malformed_state(self):
        resp = service.run_request(
            {"tool": "converse", "utterance": "hello", "state": {"pending": 5}}
        )
        self.assertFalse(resp["ok"])
        self.assertTrue(resp["refused"])


if __name__ == "__main__":
    unittest.main()
