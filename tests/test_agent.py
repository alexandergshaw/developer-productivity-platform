import textwrap
import unittest

from detcode import cnl, planner
from detcode.engines import complete, diagnose, rewrite
from detcode.engines.rewrite import Unsafe
from detcode.service import run_request


def dedent(s: str) -> str:
    return textwrap.dedent(s).lstrip("\n")


class AddFunctionTests(unittest.TestCase):
    def test_appends_with_separation(self):
        src = "import os\n\n\ndef existing():\n    return os.sep\n"
        result = rewrite.add_function(src, "def added(x):\n    return x\n")
        self.assertTrue(result.source.endswith("def existing():\n    return os.sep\n\n\ndef added(x):\n    return x\n"))
        self.assertEqual(result.report["function"], "added")

    def test_refuses_collision_and_non_function(self):
        src = "def f():\n    return 1\n"
        with self.assertRaises(Unsafe):
            rewrite.add_function(src, "def f():\n    return 2\n")
        with self.assertRaises(Unsafe):
            rewrite.add_function(src, "x = 1\n")

    def test_english_add_function_edits_the_file(self):
        src = "GREETING = 'hi'\n"
        intent = cnl.parse("add a function double where double(2) == 4 and double(5) == 10")
        outcome = planner.run(intent, src)
        self.assertIn("GREETING = 'hi'", outcome.new_source)
        self.assertIn("def double(x):", outcome.new_source)
        self.assertTrue(outcome.changed)
        self.assertEqual(outcome.report["rule"], "add_function")

    def test_corpus_functions_reachable(self):
        outcome = planner.run(
            cnl.parse("add a function is_prime where is_prime(7) == True and is_prime(8) == False"),
            "x = 1\n",
        )
        self.assertIn("while i * i <= n:", outcome.new_source)


class CompleteTests(unittest.TestCase):
    SOURCE = "total_price = 10\n\ndef total_discount(x):\n    return x\n\nis_pr"

    def test_corpus_first_with_full_insert(self):
        items = complete.complete(self.SOURCE, "is_pr")
        self.assertEqual(items[0]["label"], "is_prime")
        self.assertEqual(items[0]["kind"], "corpus")
        self.assertIn("def is_prime(n):", items[0]["insert"])
        self.assertIn("prime", items[0]["detail"])

    def test_buffer_identifiers_and_keywords(self):
        items = complete.complete(self.SOURCE, "tot")
        labels = [i["label"] for i in items]
        self.assertEqual(labels[:2], ["total_discount", "total_price"])
        kw = complete.complete("x = 1\nret", "ret")
        self.assertIn("return", [i["label"] for i in kw])

    def test_tolerates_unparsable_buffer(self):
        items = complete.complete("def broken(:\nmy_value = 1\nmy_v", "my_v")
        self.assertIn("my_value", [i["label"] for i in items])

    def test_deterministic_and_capped(self):
        runs = {str(complete.complete(self.SOURCE, "s")) for _ in range(5)}
        self.assertEqual(len(runs), 1)
        self.assertLessEqual(len(complete.complete(self.SOURCE, "s")), complete.MAX_ITEMS)

    def test_service_tool(self):
        resp = run_request({"tool": "complete", "source": "co", "prefix": "co"})
        self.assertTrue(resp["ok"])
        self.assertTrue(any(i["label"] == "count_words" for i in resp["items"]))


class DiagnoseTests(unittest.TestCase):
    def test_syntax_error_short_circuits(self):
        items = diagnose.diagnostics("def broken(:\n")
        self.assertEqual(items[0]["severity"], "error")
        self.assertIn("syntax error", items[0]["message"])

    def test_agent_checks(self):
        src = dedent(
            """
            import os
            import sys

            def risky(items=[]):
                # TODO: harden this
                if items == None:
                    return sys.argv
                try:
                    return items
                except:
                    pass
            """
        )
        items = diagnose.diagnostics(src)
        messages = " | ".join(i["message"] for i in items)
        self.assertIn("unused import 'os'", messages)
        self.assertIn("mutable default argument", messages)
        self.assertIn("prefer 'is None'", messages)
        self.assertIn("bare except", messages)
        self.assertIn("TODO", messages)
        fix = next(i for i in items if i["fix"])
        self.assertEqual(fix["fix"], "remove unused imports")

    def test_clean_source_is_clean(self):
        self.assertEqual(diagnose.diagnostics("def f(x):\n    return x\n"), [])

    def test_service_tool(self):
        resp = run_request({"tool": "diagnostics", "source": "import os\n"})
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["items"][0]["severity"], "warning")


class RunTestsToolTests(unittest.TestCase):
    def test_green_and_red_reported_not_refused(self):
        from detcode.engines import builder

        project = builder.build("a resume tailorer")
        files = {f.path: f.content for f in project.files}
        green = run_request({"tool": "runtests", "files": files})
        self.assertTrue(green["ok"])
        self.assertTrue(green["passed"])
        self.assertIn("all green", green["output"])

        files["tests/test_resume_tailorer.py"] += (
            "\n\nclass Red(unittest.TestCase):\n"
            "    def test_red(self):\n        self.assertEqual(1, 2)\n"
        )
        red = run_request({"tool": "runtests", "files": files})
        self.assertTrue(red["ok"])  # reporting, not refusing
        self.assertFalse(red["passed"])
        self.assertEqual(len(red["failures"]), 1)

    def test_broken_test_module_reported_not_crashed(self):
        files = {
            "pkg/__init__.py": "",
            "tests/test_pkg.py": "from pkg.missing import nothing\n",
        }
        resp = run_request({"tool": "runtests", "files": files})
        self.assertTrue(resp["ok"])
        self.assertFalse(resp["passed"])
        self.assertIn("Error", resp["failures"][0]["message"])


if __name__ == "__main__":
    unittest.main()
