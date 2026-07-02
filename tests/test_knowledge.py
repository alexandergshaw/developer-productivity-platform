import os
import shutil
import tempfile
import unittest

from detcode.engines import knowledge
from detcode.engines.knowledge import KnowledgeError
from detcode.service import run_request
from detcode.store import Store


class BuiltinKnowledgeTests(unittest.TestCase):
    def test_every_builtin_entry_verifies(self):
        for entry in knowledge.BUILTIN_KNOWLEDGE:
            validated = knowledge._validate_entry(dict(entry))
            self.assertTrue(validated["sources"] or validated["examples"], entry["topic"])

    def test_ask_hits_builtin_topic(self):
        answer = knowledge.ask("how should I handle mutable default arguments in a def?")
        self.assertEqual(answer.outcome, "knowledge")
        self.assertEqual(answer.topic, "Mutable default arguments")
        self.assertIn("verified example", answer.text)
        self.assertIn("Source:", answer.text)

    def test_ask_deterministic(self):
        runs = {knowledge.ask("should I use floats for money?").text for _ in range(5)}
        self.assertEqual(len(runs), 1)

    def test_engine_fallback_to_corpus(self):
        from detcode.engines.retrieve import CORPUS

        answer = knowledge.ask("how do I check whether a number is prime", corpus=CORPUS)
        self.assertEqual(answer.outcome, "engine")
        self.assertEqual(answer.topic, "is_prime")
        self.assertIn("verified corpus function", answer.text)

    def test_miss_is_honest(self):
        answer = knowledge.ask("how does kubernetes pod eviction interact with cgroups")
        self.assertEqual(answer.outcome, "miss")
        self.assertIn("I don't know this yet", answer.text)
        self.assertIn("study", answer.text)


class LearnTests(unittest.TestCase):
    ENTRY = {
        "topic": "Kubernetes pod eviction",
        "keywords": ["kubernetes", "pod", "eviction", "cgroups"],
        "guidance": "Eviction is driven by node pressure; requests/limits decide order.",
        "sources": ["https://kubernetes.io/docs/concepts/scheduling-eviction/"],
    }

    def test_learn_then_ask_hits(self):
        text, report = knowledge.learn(self.ENTRY)
        self.assertEqual(report["topic"], "Kubernetes pod eviction")
        entries = knowledge.load_knowledge(text)
        answer = knowledge.ask(
            "how does kubernetes pod eviction interact with cgroups", extra_entries=entries
        )
        self.assertEqual(answer.outcome, "knowledge")
        self.assertIn("origin: learned", answer.text)

    def test_accountability_bar(self):
        bare = dict(self.ENTRY, sources=[], examples=[])
        with self.assertRaises(KnowledgeError):
            knowledge.learn(bare)

    def test_failing_example_refused(self):
        entry = dict(self.ENTRY, examples=[{"code": "assert 1 == 2"}])
        with self.assertRaises(KnowledgeError):
            knowledge.learn(entry)

    def test_example_without_assert_refused(self):
        entry = dict(self.ENTRY, examples=[{"code": "x = 1"}])
        with self.assertRaises(KnowledgeError):
            knowledge.learn(entry)

    def test_load_reverifies_tampered_entry(self):
        entry = dict(self.ENTRY, examples=[{"code": "assert 1 == 1"}])
        text, _ = knowledge.learn(entry)
        tampered = text.replace("assert 1 == 1", "assert 1 == 2")
        with self.assertRaises(KnowledgeError):
            knowledge.load_knowledge(tampered)


class StudyLoopTests(unittest.TestCase):
    """ask (miss) -> study queue -> learn -> question closed -> ask hits."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="detcode_know_")
        self.store = Store(os.path.join(self.dir, "detcode.db"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_full_loop_through_the_service(self):
        question = "how does kubernetes pod eviction interact with cgroups"

        miss = run_request({"tool": "ask", "question": question}, store=self.store)
        self.assertTrue(miss["ok"])
        self.assertIn("I don't know this yet", miss["output"])

        queue = run_request({"tool": "study"}, store=self.store)
        self.assertIn(question, queue["output"])

        learned = run_request(
            {"tool": "learn", "entry": LearnTests.ENTRY}, store=self.store
        )
        self.assertTrue(learned["ok"], learned.get("error"))
        self.assertIn(question, learned["closed_questions"])

        hit = run_request({"tool": "ask", "question": question}, store=self.store)
        self.assertIn("Kubernetes pod eviction", hit["output"])
        self.assertIn("origin: learned", hit["output"])

        queue_after = run_request({"tool": "study"}, store=self.store)
        self.assertIn("✓", queue_after["output"])
        self.assertIn("answered by: Kubernetes pod eviction", queue_after["output"])

    def test_bare_question_routes_through_english(self):
        resp = run_request(
            {"tool": "do", "command": "should I use floats for money?"}, store=self.store
        )
        self.assertTrue(resp["ok"])
        self.assertIn("integer cents", resp["output"])

    def test_what_does_x_do_still_explains(self):
        resp = run_request(
            {"tool": "do", "command": "what does f do?", "source": "def f(x):\n    return x\n"}
        )
        self.assertTrue(resp["ok"])
        self.assertIn("def f(x)", resp["output"])


class AdviseTests(unittest.TestCase):
    SOURCE = (
        "import os\n\n\n"
        "def risky(items=[]):\n"
        "    if items == None:\n"
        "        return []\n"
        "    return items\n"
    )

    def test_findings_paired_with_lessons(self):
        answer = knowledge.advise(self.SOURCE)
        self.assertEqual(answer.outcome, "advise")
        self.assertIn("Mutable default arguments", answer.text)
        self.assertIn("Comparing with None", answer.text)
        self.assertIn("L4:", answer.text)  # findings carry their lines
        self.assertIn("Quick fix available", answer.text)
        self.assertGreaterEqual(answer.report["lessons"], 2)

    def test_clean_file_has_nothing_to_teach(self):
        answer = knowledge.advise("def f(x):\n    return x\n")
        self.assertIn("No problems detected", answer.text)

    def test_english_review_command(self):
        resp = run_request(
            {"tool": "do", "command": "review this file", "source": self.SOURCE}
        )
        self.assertTrue(resp["ok"])
        self.assertIn("lesson(s)", resp["output"])

    def test_deterministic(self):
        runs = {knowledge.advise(self.SOURCE).text for _ in range(5)}
        self.assertEqual(len(runs), 1)


class ExperienceClosesQuestionsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="detcode_exp_")
        self.store = Store(os.path.join(self.dir, "detcode.db"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_teaching_answers_an_open_question(self):
        run_request(
            {"tool": "ask", "question": "how do I slugify a post title"}, store=self.store
        )
        self.assertEqual(self.store.open_questions()[0]["status"], "open")
        taught = run_request(
            {
                "tool": "do",
                "command": 'teach slugify where slugify("A B") == "a-b"',
                "source": 'def slugify(text):\n    return "-".join(text.lower().split())\n',
            },
            store=self.store,
        )
        self.assertTrue(taught["ok"], taught.get("error"))
        self.assertIn("closed 1 open question", taught["output"])
        self.assertEqual(self.store.open_questions()[0]["status"], "answered")

    def test_minting_answers_an_open_question(self):
        from detcode.engines import builder

        run_request(
            {"tool": "ask", "question": "how do I build a moneymap tool"}, store=self.store
        )
        project = builder.build("an expense tracker")
        run_request(
            {
                "tool": "mint",
                "files": {f.path: f.content for f in project.files},
                "keywords": ["moneymap"],
            },
            store=self.store,
        )
        record = self.store.open_questions()[0]
        self.assertEqual(record["status"], "answered")
        self.assertIn("minted", record["answered_by"])


if __name__ == "__main__":
    unittest.main()
