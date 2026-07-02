import os
import shutil
import tempfile
import unittest

from detcode.engines import builder, mint
from detcode.engines.mint import MintError
from detcode.store import Store
from tests.test_builder import materialize, run_generated_tests


def project_files(project) -> dict:
    return {f.path: f.content for f in project.files}


class MintRecordTests(unittest.TestCase):
    def test_templates_package_and_tests_only(self):
        project = builder.build("a resume tailorer")
        record = mint.mint_record(project_files(project), ["minty"])
        self.assertEqual(record["default_slug"], "resume_tailorer")
        self.assertEqual(record["key"], "resume-tailorer")
        paths = sorted(record["files"])
        self.assertIn("__PKG__/tailor.py", paths)
        self.assertIn("tests/test___PKG__.py", paths)
        self.assertNotIn("README.md", paths)  # regenerated at build time
        # Word-boundary templating: the slug never survives in contents.
        self.assertNotIn("resume_tailorer", record["files"]["__PKG__/tailor.py"])

    def test_refuses_no_keywords_no_package_no_tests(self):
        project = builder.build("a resume tailorer")
        files = project_files(project)
        with self.assertRaises(MintError):
            mint.mint_record(files, [])
        with self.assertRaises(MintError):
            mint.mint_record({"just_a_file.py": "x = 1\n"}, ["k"])
        no_tests = {p: c for p, c in files.items() if not p.startswith("tests/")}
        with self.assertRaises(MintError):
            mint.mint_record(no_tests, ["k"])


class MintVerifyTests(unittest.TestCase):
    def test_green_project_verifies(self):
        project = builder.build("a resume tailorer")
        root = materialize(project)
        try:
            result = mint.verify_project(root, "resume_tailorer")
            self.assertGreater(result.testsRun, 0)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_red_project_refused(self):
        project = builder.build("a resume tailorer")
        root = materialize(project)
        try:
            broken = os.path.join(root, "resume_tailorer", "keywords.py")
            with open(broken, "a", encoding="utf-8") as fh:
                fh.write("\nSTOPWORDS = frozenset()\n")  # break the stopword tests
            with self.assertRaises(MintError):
                mint.verify_project(root, "resume_tailorer")
        finally:
            shutil.rmtree(root, ignore_errors=True)


class MintedPackRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="detcode_mint_")
        self.store = Store(os.path.join(self.dir, "detcode.db"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_mint_store_match_build_green(self):
        source_project = builder.build("a resume tailorer")
        record = mint.mint_record(project_files(source_project), ["minty", "gadget"])
        self.store.upsert_pack(record)

        packs = tuple(self.store.user_packs())
        rebuilt = builder.build("a minty gadget", extra_packs=packs)
        self.assertEqual(rebuilt.report["pack"], "resume-tailorer")
        self.assertEqual(rebuilt.name, "resume_tailorer")
        self.assertIn(
            "resume_tailorer/tailor.py", [f.path for f in rebuilt.files]
        )
        root = materialize(rebuilt)
        try:
            result = run_generated_tests(root, "resume_tailorer")
            self.assertGreater(result.testsRun, 0)
            self.assertEqual(result.failures, [])
            self.assertEqual(result.errors, [])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_minted_pack_composes_with_builtins(self):
        record = mint.mint_record(
            project_files(builder.build("a resume tailorer")), ["minty"],
            key="my-mint",
        )
        self.store.upsert_pack(record)
        project = builder.build(
            "a teaching assistant with a minty module",
            extra_packs=tuple(self.store.user_packs()),
        )
        self.assertEqual(project.report["packs"], ["teaching-assistant", "my-mint"])

    def test_service_new_uses_minted_packs(self):
        from detcode.service import run_request

        record = mint.mint_record(
            project_files(builder.build("a resume tailorer")), ["minty"]
        )
        self.store.upsert_pack(record)
        resp = run_request({"tool": "new", "direction": "a minty thing"}, store=self.store)
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["report"]["pack"], "resume-tailorer")


if __name__ == "__main__":
    unittest.main()
