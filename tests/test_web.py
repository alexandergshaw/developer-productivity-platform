import os
import shutil
import tempfile
import unittest

from detcode.engines import codeindex, web
from detcode.engines.web import WebError
from detcode.service import run_request
from detcode.store import Store

TRANSCRIPT = """\
Sprint planning 2026-06-30

Alex: We agreed to move the auth timeout to 30 seconds.
Priya: I will update the gateway config by Friday.
Sam: Should we also rotate the signing keys this quarter?
Alex: Action item: Priya owns the gateway change.
General discussion about the roadmap followed.
"""


class FactExtractionTests(unittest.TestCase):
    def test_classifies_decisions_actions_questions_with_speakers(self):
        extracted = web.facts(TRANSCRIPT)
        kinds = {(f["kind"], f["speaker"]) for f in extracted}
        self.assertIn(("decision", "Alex"), kinds)
        self.assertIn(("action", "Priya"), kinds)
        self.assertIn(("question", "Sam"), kinds)

    def test_speakers(self):
        self.assertEqual(web.speakers(TRANSCRIPT), ["Alex", "Priya", "Sam"])

    def test_deterministic(self):
        runs = {str(web.facts(TRANSCRIPT)) for _ in range(5)}
        self.assertEqual(len(runs), 1)


class QueryTests(unittest.TestCase):
    NOTES = [{"id": 1, "title": "Sprint planning", "kind": "transcript", "text": TRANSCRIPT}]
    LINKS = [
        {"url": "https://wiki.corp/auth-runbook", "title": "Auth runbook",
         "tags": "auth,oncall", "note": "gateway timeout settings live here"},
        {"url": "https://wiki.corp/holiday-menu", "title": "Cafeteria menu", "tags": "", "note": ""},
    ]

    def test_evidence_with_citations(self):
        result = web.query("what did we decide about the auth timeout", self.NOTES, self.LINKS)
        self.assertIn("citations, not synthesis", result.text)
        self.assertIn("Sprint planning", result.text)
        self.assertIn("[DECISION]", result.text)
        self.assertIn("30 seconds", result.text)
        self.assertIn("Auth runbook", result.text)
        self.assertNotIn("Cafeteria", result.text)
        self.assertEqual(result.report["outcome"], "evidence")

    def test_miss_is_honest(self):
        result = web.query("what is our kafka retention policy", self.NOTES, self.LINKS)
        self.assertEqual(result.report["outcome"], "miss")
        self.assertIn("refusing to guess", result.text)

    def test_code_citations(self):
        code = [{"root": "work", "path": "svc/auth.py", "line": 42, "kind": "function",
                 "symbol": "validate_token", "doc": "Check the auth token signature."}]
        result = web.query("where do we validate the auth token", self.NOTES, [], code)
        self.assertIn("svc/auth.py:42", result.text)
        self.assertIn("validate_token", result.text)

    def test_web_neighborhood(self):
        result = web.related("auth", self.NOTES, self.LINKS)
        self.assertIn("Related terms:", result.text)
        self.assertIn("timeout", result.text)
        self.assertIn("People: Alex, Priya, Sam", result.text)
        self.assertIn("wiki.corp/auth-runbook", result.text)

    def test_empty_question_refused(self):
        with self.assertRaises(WebError):
            web.query("  ", self.NOTES)


class CodeIndexTests(unittest.TestCase):
    def test_python_and_js_symbols(self):
        py = 'class Gateway:\n    """Routes requests."""\n\ndef validate_token(t):\n    """Check signature."""\n    return t\n'
        js = "export async function fetchUser(id) {}\nconst renderPage = async (props) => {}\nclass Store {}\n"
        py_symbols = codeindex.index_source("svc/auth.py", py)
        self.assertEqual(
            [(s["symbol"], s["kind"]) for s in py_symbols],
            [("Gateway", "class"), ("validate_token", "function")],
        )
        self.assertEqual(py_symbols[1]["doc"], "Check signature.")
        js_symbols = codeindex.index_source("ui/app.ts", js)
        self.assertEqual(
            [(s["symbol"], s["kind"]) for s in js_symbols],
            [("fetchUser", "function"), ("renderPage", "function"), ("Store", "class")],
        )

    def test_index_tree_and_query_roundtrip(self):
        root = tempfile.mkdtemp(prefix="detcode_idx_")
        db_dir = tempfile.mkdtemp(prefix="detcode_idxdb_")
        try:
            os.makedirs(os.path.join(root, "svc"))
            with open(os.path.join(root, "svc", "auth.py"), "w", encoding="utf-8") as fh:
                fh.write('def validate_token(t):\n    """Check the token signature."""\n    return t\n')
            entries = codeindex.index_tree(root)
            store = Store(os.path.join(db_dir, "detcode.db"))
            store.replace_code_index("work", entries)
            result = web.query("where is the token signature validated", [], [], store.code_entries())
            self.assertIn("svc/auth.py:1", result.text)
        finally:
            shutil.rmtree(root, ignore_errors=True)
            shutil.rmtree(db_dir, ignore_errors=True)


class WorkPlatformServiceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="detcode_work_")
        self.store = Store(os.path.join(self.dir, "detcode.db"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_query_through_service_and_miss_logs(self):
        self.store.add_note("Sprint planning", "transcript", TRANSCRIPT)
        hit = run_request(
            {"tool": "query", "question": "what did we decide about auth timeout"},
            store=self.store,
        )
        self.assertTrue(hit["ok"])
        self.assertIn("[DECISION]", hit["output"])

        miss = run_request(
            {"tool": "query", "question": "kafka retention policy"}, store=self.store
        )
        self.assertIn("refusing to guess", miss["output"])
        questions = [q["question"] for q in self.store.open_questions()]
        self.assertIn("kafka retention policy", questions)

    def test_links_never_fetched_just_stored(self):
        self.store.add_link("https://internal.corp/only-on-vpn", "VPN doc", "vpn", "")
        links = self.store.links()
        self.assertEqual(links[0]["url"], "https://internal.corp/only-on-vpn")


if __name__ == "__main__":
    unittest.main()
