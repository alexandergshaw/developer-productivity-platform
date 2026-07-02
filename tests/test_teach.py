import json
import unittest

from detcode.engines import builder, retrieve, teach
from detcode.engines.teach import CorpusError, TeachError


SOURCE = '''
import os  # module-level import the taught function must NOT depend on

HELPER = 42


def slugify(text):
    """Lowercase, words joined by hyphens."""
    return "-".join(text.lower().split())


def needs_module_state(x):
    return HELPER + x
'''

EXAMPLES = [
    {"in": ["Hello World"], "out": "hello-world"},
    {"in": ["One  Two Three"], "out": "one-two-three"},
]


class TeachTests(unittest.TestCase):
    def test_teach_verifies_and_stores(self):
        result = teach.teach(SOURCE, "slugify", EXAMPLES)
        data = json.loads(result.corpus_text)
        self.assertEqual(data["detcode_corpus"], 1)
        self.assertEqual(data["entries"][0]["name"], "slugify")
        self.assertEqual(data["entries"][0]["arity"], 1)
        self.assertEqual(result.report["cases_verified"], 2)
        self.assertIn("corpus_hash", result.report)

    def test_reteach_replaces_entry(self):
        first = teach.teach(SOURCE, "slugify", EXAMPLES)
        again = teach.teach(SOURCE, "slugify", EXAMPLES, first.corpus_text)
        self.assertTrue(again.report["replaced"])
        self.assertEqual(again.report["corpus_entries"], 1)

    def test_refuses_failing_examples(self):
        with self.assertRaises(TeachError):
            teach.teach(SOURCE, "slugify", [{"in": ["x"], "out": "WRONG"}])

    def test_refuses_module_dependent_function(self):
        # needs_module_state uses a module global; in isolation it must fail.
        with self.assertRaises(TeachError):
            teach.teach(SOURCE, "needs_module_state", [{"in": [1], "out": 43}])

    def test_refuses_missing_function_and_fancy_signatures(self):
        with self.assertRaises(TeachError):
            teach.teach(SOURCE, "nope", EXAMPLES)
        src = "def f(a, b=1):\n    return a + b\n"
        with self.assertRaises(TeachError):
            teach.teach(src, "f", [{"in": [1], "out": 2}])

    def test_arity_mismatch_refused(self):
        with self.assertRaises(TeachError):
            teach.teach(SOURCE, "slugify", [{"in": ["a", "b"], "out": "a-b"}])


class CorpusTests(unittest.TestCase):
    def corpus_text(self):
        return teach.teach(SOURCE, "slugify", EXAMPLES).corpus_text

    def test_load_reverifies(self):
        entries = teach.load_corpus(self.corpus_text())
        self.assertEqual(entries[0].name, "slugify")

    def test_tampered_entry_refused_loudly(self):
        data = json.loads(self.corpus_text())
        data["entries"][0]["source"] = "def slugify(text):\n    return text\n"
        with self.assertRaises(CorpusError):
            teach.load_corpus(json.dumps(data))

    def test_retrieval_finds_taught_function(self):
        entries = teach.load_corpus(self.corpus_text())
        result = retrieve.write_function(
            {"name": "slugify", "examples": [{"in": ["Big Idea"], "out": "big-idea"}]},
            extra=entries,
        )
        self.assertEqual(result.report["rule"], "retrieve")
        self.assertEqual(result.report["origin"], "user")

    def test_plan_build_uses_taught_function(self):
        entries = teach.load_corpus(self.corpus_text())
        plan = {
            "detcode_plan": 1,
            "name": "url_helper",
            "functions": [
                {
                    "name": "slugify",
                    "examples": [{"in": ["My Post Title"], "out": "my-post-title"}],
                }
            ],
        }
        project = builder.build_from_plan(plan, corpus=entries)
        self.assertEqual(project.report["solved"], ["slugify"])
        core = next(f for f in project.files if f.path == "url_helper/core.py")
        self.assertIn('"-".join(text.lower().split())', core.content)


if __name__ == "__main__":
    unittest.main()
