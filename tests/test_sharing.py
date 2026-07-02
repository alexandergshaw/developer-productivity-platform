import json
import os
import shutil
import tempfile
import unittest

from detcode.engines import builder, mint, plan
from detcode.engines.mint import MintError
from detcode.store import Store


def minted_record(keywords=("minty",)):
    project = builder.build("a resume tailorer")
    return mint.mint_record({f.path: f.content for f in project.files}, list(keywords))


class PackExportImportTests(unittest.TestCase):
    """Mint on one machine, use on another — two stores stand in for two boxes."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="detcode_share_")
        self.machine_a = Store(os.path.join(self.dir, "a.db"))
        self.machine_b = Store(os.path.join(self.dir, "b.db"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def export_text(self, store) -> str:
        records = [
            {
                "key": p.key, "title": p.title, "default_slug": p.default_slug,
                "keywords": sorted(p.keywords), "description": p.description,
                "files": p.files(),
            }
            for p in store.user_packs()
        ]
        return json.dumps(
            {"detcode_packs": 1, "packs": sorted(records, key=lambda r: r["key"])},
            indent=2, sort_keys=True,
        ) + "\n"

    def test_roundtrip_between_machines(self):
        self.machine_a.upsert_pack(minted_record())
        text = self.export_text(self.machine_a)

        # Machine B: verify (structure + parse + green tests) then merge.
        data = json.loads(text)
        for record in data["packs"]:
            mint.validate_pack_record(record)
            result = mint.materialize_and_verify(
                mint.concrete_files(record), record["default_slug"]
            )
            self.assertGreater(result.testsRun, 0)
            self.machine_b.upsert_pack(record)

        rebuilt = builder.build(
            "a minty thing", extra_packs=tuple(self.machine_b.user_packs())
        )
        self.assertEqual(rebuilt.report["pack"], "resume-tailorer")

    def test_export_deterministic(self):
        self.machine_a.upsert_pack(minted_record())
        self.assertEqual(self.export_text(self.machine_a), self.export_text(self.machine_a))

    def test_import_verification_refuses_broken_pack(self):
        record = minted_record()
        record["files"]["__PKG__/tailor.py"] = "def broken(:\n"
        with self.assertRaises(MintError):
            mint.validate_pack_record(record)

    def test_import_verification_refuses_red_tests(self):
        record = minted_record()
        # Sabotage: an assertion that cannot hold.
        record["files"]["tests/test___PKG__.py"] += (
            "\n\nclass Sabotage(unittest.TestCase):\n"
            "    def test_red(self):\n"
            "        self.assertEqual(1, 2)\n"
        )
        mint.validate_pack_record(record)  # structurally fine
        with self.assertRaises(MintError):
            mint.materialize_and_verify(
                mint.concrete_files(record), record["default_slug"]
            )


class ServiceMintTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="detcode_svcmint_")
        self.store = Store(os.path.join(self.dir, "detcode.db"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_mint_via_service_then_retrieve(self):
        from detcode.service import run_request

        project = builder.build("a teaching assistant app")
        files = {f.path: f.content for f in project.files}
        resp = run_request(
            {"tool": "mint", "files": files, "keywords": ["studybuddy"]},
            store=self.store,
        )
        self.assertTrue(resp["ok"], resp.get("error"))
        self.assertIn("minted pack", resp["output"])

        built = run_request(
            {"tool": "new", "direction": "a studybuddy for exams"}, store=self.store
        )
        self.assertTrue(built["ok"])
        self.assertIn("teaching_assistant/scheduler.py", built["files"])

    def test_mint_without_store_refused(self):
        from detcode.service import run_request

        resp = run_request({"tool": "mint", "files": {}, "keywords": ["x"]}, store=None)
        self.assertFalse(resp["ok"])
        self.assertTrue(resp["refused"])


class PlanSuggestionTests(unittest.TestCase):
    def test_agent_noun_takes_preceding_object(self):
        names = [f["name"] for f in plan.suggest_functions("a citation formatter")]
        self.assertEqual(names, ["format_citation"])

    def test_verb_takes_following_object(self):
        names = [
            f["name"]
            for f in plan.suggest_functions(
                "a citation formatter that parses bibtex and counts pages"
            )
        ]
        self.assertEqual(names, ["format_citation", "parse_bibtex", "count_pages"])

    def test_suggestions_land_in_the_plan(self):
        result = plan.make_plan("a bibtex parser")
        self.assertEqual(result.plan["functions"][0]["name"], "parse_bibtex")
        self.assertEqual(result.plan["functions"][0]["examples"], [])
        self.assertIn("suggested", " ".join(result.plan["notes"]))

    def test_fallback_placeholder_when_nothing_mined(self):
        result = plan.make_plan("a widget doodad")
        self.assertEqual(result.plan["functions"][0]["name"], "rename_me")

    def test_deterministic(self):
        runs = {str(plan.suggest_functions("a bibtex parser that counts pages")) for _ in range(5)}
        self.assertEqual(len(runs), 1)


if __name__ == "__main__":
    unittest.main()
