import json
import os
import shutil
import tempfile
import unittest

from detcode import store as store_module
from detcode.engines import teach
from detcode.engines.teach import TeachError
from detcode.store import Store, StoreError

SOURCE = 'def slugify(text):\n    return "-".join(text.lower().split())\n'
EXAMPLES = [{"in": ["Hello World"], "out": "hello-world"}]


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="detcode_store_")
        self.store = Store(os.path.join(self.dir, "detcode.db"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_corpus_roundtrip(self):
        taught = teach.teach(SOURCE, "slugify", EXAMPLES)
        self.store.replace_corpus(taught.corpus_text)
        self.assertEqual(self.store.corpus_count(), 1)
        # DB out == JSON in: the export is the canonical interchange form.
        self.assertEqual(self.store.corpus_text(), taught.corpus_text)
        entries = teach.load_corpus(self.store.corpus_text())
        self.assertEqual(entries[0].name, "slugify")

    def test_export_is_deterministic(self):
        taught = teach.teach(SOURCE, "slugify", EXAMPLES)
        self.store.replace_corpus(taught.corpus_text)
        self.assertEqual(self.store.corpus_text(), self.store.corpus_text())

    def test_pack_roundtrip_and_tamper_detection(self):
        record = {
            "key": "my-pack", "title": "My Pack", "default_slug": "my_pack",
            "keywords": ["mine"], "description": "test pack",
            "files": {"__PKG__/__init__.py": '"""x."""\n'},
        }
        self.store.upsert_pack(record)
        packs = self.store.user_packs()
        self.assertEqual(packs[0].key, "my-pack")
        self.assertEqual(packs[0].files(), record["files"])
        # Tamper with the stored files behind the hash's back.
        with self.store._conn() as db:
            db.execute("UPDATE packs SET files = ?", ('{"evil.py": "boom"}',))
        with self.assertRaises(StoreError):
            self.store.user_packs()

    def test_audit_written(self):
        taught = teach.teach(SOURCE, "slugify", EXAMPLES)
        self.store.replace_corpus(taught.corpus_text)
        with self.store._conn() as db:
            rows = db.execute("SELECT action FROM audit").fetchall()
        self.assertIn(("teach",), rows)


class TeachAllTests(unittest.TestCase):
    MODULES = {
        "pkg/core.py": (
            'def slugify(text):\n    return "-".join(text.lower().split())\n\n\n'
            "def double(x):\n    return x * 2\n\n\n"
            "HELPER = 10\n\n\n"
            "def needs_state(x):\n    return HELPER + x\n\n\n"
            "def _private(x):\n    return x\n"
        ),
    }
    TESTS = [
        (
            "import unittest\nfrom pkg import core\n\n"
            "class T(unittest.TestCase):\n"
            "    def test_a(self):\n"
            "        self.assertEqual(core.slugify('A B'), 'a-b')\n"
            "    def test_b(self):\n"
            "        self.assertEqual(core.double(4), 8)\n"
            "    def test_c(self):\n"
            "        self.assertEqual(core.needs_state(1), 11)\n"
            "    def test_nonliteral(self):\n"
            "        x = 3\n"
            "        self.assertEqual(core.double(x), 6)\n"
        )
    ]

    def test_mines_only_literal_examples(self):
        mined = teach.mine_examples(self.TESTS[0])
        self.assertEqual(mined["slugify"], [{"in": ["A B"], "out": "a-b"}])
        self.assertEqual(mined["double"], [{"in": [4], "out": 8}])  # x=3 case skipped

    def test_sweep_teaches_selfcontained_and_reports_skips(self):
        result = teach.teach_all(self.MODULES, self.TESTS)
        self.assertEqual(result.report["taught"], ["slugify", "double"])  # source order
        skipped = result.report["skipped"]
        self.assertIn("needs_state", skipped)  # module-global dependency
        self.assertIn("isolation", skipped["needs_state"])
        self.assertNotIn("_private", skipped)  # underscore names not swept
        data = json.loads(result.corpus_text)
        self.assertEqual([e["name"] for e in data["entries"]], ["double", "slugify"])

    def test_sweep_output_loads_and_retrieves(self):
        from detcode.engines import retrieve

        result = teach.teach_all(self.MODULES, self.TESTS)
        entries = teach.load_corpus(result.corpus_text)
        hit = retrieve.write_function(
            {"name": "slugify", "examples": [{"in": ["X Y"], "out": "x-y"}]}, extra=entries
        )
        self.assertEqual(hit.report["origin"], "user")

    def test_nothing_mineable_refused(self):
        with self.assertRaises(TeachError):
            teach.teach_all({"m.py": "def f(x):\n    return x\n"}, ["print('no tests')"])


class ServiceTeachTests(unittest.TestCase):
    """The workbench path: teach through the service, retrieve through it too."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="detcode_svc_")
        self.store = Store(os.path.join(self.dir, "detcode.db"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_teach_in_english_then_retrieve(self):
        from detcode.service import run_request

        taught = run_request(
            {
                "tool": "do",
                "command": 'teach slugify where slugify("Hello World") == "hello-world" and slugify("A  B") == "a-b"',
                "source": SOURCE,
            },
            store=self.store,
        )
        self.assertTrue(taught["ok"], taught.get("error"))
        self.assertIn("taught 'slugify'", taught["output"])
        self.assertEqual(self.store.corpus_count(), 1)

        # The same service now retrieves the taught function.
        hit = run_request(
            {
                "tool": "do",
                "command": 'write a function slugify where slugify("Big Idea") == "big-idea"',
            },
            store=self.store,
        )
        self.assertTrue(hit["ok"])
        self.assertIn('"-".join', hit["output"])
        self.assertEqual(hit["report"]["origin"], "user")

    def test_teach_without_store_is_refused(self):
        from detcode.service import run_request

        resp = run_request(
            {"tool": "do", "command": "teach f where f(1) == 1", "source": "def f(x):\n    return x\n"},
            store=None,
        )
        self.assertFalse(resp["ok"])
        self.assertTrue(resp["refused"])

    def test_teach_failing_examples_refused_and_not_persisted(self):
        from detcode.service import run_request

        resp = run_request(
            {"tool": "do", "command": 'teach slugify where slugify("x") == "WRONG"', "source": SOURCE},
            store=self.store,
        )
        self.assertFalse(resp["ok"])
        self.assertTrue(resp["refused"])
        self.assertEqual(self.store.corpus_count(), 0)


if __name__ == "__main__":
    unittest.main()
