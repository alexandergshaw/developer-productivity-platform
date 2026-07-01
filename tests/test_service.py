import unittest

from detcode.service import run_request


class ServiceTests(unittest.TestCase):
    def test_do_generates_code_from_english(self):
        resp = run_request(
            {"tool": "do", "command": "write a function double where double(2) == 4 and double(5) == 10"}
        )
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["kind"], "generated")
        self.assertIn("def double(x):", resp["output"])

    def test_do_edits_source(self):
        resp = run_request(
            {
                "tool": "do",
                "command": "remove unused imports",
                "source": "import os\nimport sys\n\nprint(sys.argv)\n",
            }
        )
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["kind"], "edit")
        self.assertTrue(resp["changed"])
        self.assertNotIn("import os", resp["output"])

    def test_repair_tool(self):
        resp = run_request(
            {
                "tool": "repair",
                "source": "def area(w, h):\n    return w + h\n",
                "spec": {
                    "function": "area",
                    "examples": [{"in": [2, 3], "out": 6}, {"in": [4, 5], "out": 20}],
                },
            }
        )
        self.assertTrue(resp["ok"])
        self.assertIn("w * h", resp["output"])

    def test_explain_tool(self):
        resp = run_request(
            {"tool": "explain", "source": "def f(x):\n    return x\n", "func": "f"}
        )
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["kind"], "text")
        self.assertIn("def f(x)", resp["output"])

    def test_gentest_tool(self):
        resp = run_request(
            {
                "tool": "gentest",
                "spec": {
                    "function": "area",
                    "source": "def area(w, h):\n    return w * h\n",
                    "examples": [{"in": [2, 3], "out": 6}],
                },
            }
        )
        self.assertTrue(resp["ok"])
        self.assertIn("class TestArea(", resp["output"])

    def test_refusal_is_flagged(self):
        resp = run_request({"tool": "do", "command": "make my code faster"})
        self.assertFalse(resp["ok"])
        self.assertTrue(resp["refused"])
        self.assertIn("Closest supported form", resp["error"])

    def test_unknown_tool(self):
        resp = run_request({"tool": "teleport"})
        self.assertFalse(resp["ok"])
        self.assertFalse(resp["refused"])

    def test_malformed_request(self):
        resp = run_request("not a dict")
        self.assertFalse(resp["ok"])

    def test_invalid_python_source(self):
        resp = run_request({"tool": "imports", "source": "def def def"})
        self.assertFalse(resp["ok"])
        self.assertIn("not valid Python", resp["error"])


if __name__ == "__main__":
    unittest.main()
