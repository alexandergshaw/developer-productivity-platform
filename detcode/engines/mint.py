"""Mint — learning at project scale.

``teach`` grows the corpus one verified function at a time; ``mint`` does the
same for whole projects: a finished, green-tested project becomes a domain
pack stored in the database, matched by keywords exactly like the built-in
packs. The next ``detcode new`` that mentions your keywords retrieves the
whole project.

Honesty rules match teach:
- a project only mints if its own test suite passes (proof-carrying packs)
- stored packs are content-hash-verified on every load (see store.py)
"""
from __future__ import annotations

import ast
import importlib.util
import os
import re
import sys
import unittest

from ..determinism import provenance

RULE_VERSION = "1"


class MintError(Exception):
    """The project could not be verified and minted."""


def _find_package(files: dict[str, str]) -> str:
    """The single top-level package directory (its name becomes __PKG__)."""
    packages = sorted(
        {p.split("/", 1)[0] for p in files if p.endswith("/__init__.py") and p.count("/") == 1}
    )
    packages = [p for p in packages if p != "tests"]
    if not packages:
        raise MintError("no package found (need a directory with __init__.py)")
    if len(packages) > 1:
        raise MintError(
            f"multiple packages found ({', '.join(packages)}); mint one at a time"
        )
    return packages[0]


def mint_record(
    files: dict[str, str],
    keywords: list[str],
    key: str | None = None,
    title: str | None = None,
    description: str | None = None,
) -> dict:
    """Template a project's files into a storable pack record (pure)."""
    cleaned_keywords = sorted({k.strip().lower() for k in keywords if k and k.strip()})
    if not cleaned_keywords:
        raise MintError("give at least one keyword — packs are matched by them")
    slug = _find_package(files)

    pattern = re.compile(rf"\b{re.escape(slug)}\b")
    templated: dict[str, str] = {}
    for path, content in sorted(files.items()):
        top = path.split("/", 1)[0]
        if top not in (slug, "tests"):
            continue  # README/pyproject/.gitignore are regenerated at build time
        # Paths are structured: plain substring replace (catches
        # tests/test_<slug>.py, where '_' defeats a \b boundary). Contents
        # use word boundaries so identifiers merely containing the slug survive.
        new_path = path.replace(slug, "__PKG__")
        new_content = pattern.sub("__PKG__", content)
        if new_path.endswith(".py"):
            try:
                ast.parse(new_content.replace("__PKG__", slug))
            except SyntaxError as exc:
                raise MintError(f"{path} does not parse: {exc}") from exc
        templated[new_path] = new_content
    if not any(p.startswith("tests/test_") for p in templated):
        raise MintError("the project has no tests/test_*.py — packs must be proof-carrying")

    pack_key = key or slug.replace("_", "-")
    record = {
        "key": pack_key,
        "title": title or " ".join(w.capitalize() for w in slug.split("_")),
        "default_slug": slug,
        "keywords": cleaned_keywords,
        "description": description
        or f"a minted pack from your {slug} project, with its tests",
        "files": templated,
    }
    return record


def verify_project(root: str, slug: str) -> unittest.TestResult:
    """Run the project's own tests in-process; minting requires green."""
    test_dir = os.path.join(root, "tests")
    if not os.path.isdir(test_dir):
        raise MintError("the project has no tests/ directory — packs must be proof-carrying")
    sys.path.insert(0, root)
    try:
        result = unittest.TestResult()
        for fname in sorted(os.listdir(test_dir)):
            if not (fname.startswith("test_") and fname.endswith(".py")):
                continue
            spec = importlib.util.spec_from_file_location(
                f"minting_{slug}_{fname[:-3]}", os.path.join(test_dir, fname)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            unittest.TestLoader().loadTestsFromModule(module).run(result)
        if result.testsRun == 0:
            raise MintError("no tests ran — packs must be proof-carrying")
        if result.failures or result.errors:
            raise MintError(
                f"the project's tests are not green ({len(result.failures)} failure(s), "
                f"{len(result.errors)} error(s)) — fix them, then mint"
            )
        return result
    finally:
        sys.path.remove(root)
        for mod in [m for m in list(sys.modules) if m == slug or m.startswith(slug + ".")
                    or m.startswith(f"minting_{slug}_")]:
            del sys.modules[mod]


def materialize_and_verify(files: dict[str, str], slug: str) -> unittest.TestResult:
    """Write project files to a temp dir, run their tests, clean up."""
    import shutil
    import tempfile

    root = tempfile.mkdtemp(prefix="detcode_mint_")
    try:
        for path, content in files.items():
            target = os.path.join(root, path.replace("/", os.sep))
            os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
            with open(target, "w", encoding="utf-8", newline="") as fh:
                fh.write(content)
        return verify_project(root, slug)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def validate_pack_record(record: dict) -> dict:
    """Structural validation of a pack record (import path)."""
    required = ("key", "title", "default_slug", "keywords", "description", "files")
    if not isinstance(record, dict) or not all(k in record for k in required):
        raise MintError(f"pack record needs {required}")
    slug = record["default_slug"]
    if not isinstance(slug, str) or not slug.isidentifier():
        raise MintError(f"pack default_slug {slug!r} is not a valid identifier")
    if not record["keywords"]:
        raise MintError(f"pack {record['key']!r} has no keywords")
    files = record["files"]
    if not isinstance(files, dict) or not any(
        p.startswith("tests/test_") for p in files
    ):
        raise MintError(f"pack {record['key']!r} has no tests — packs must be proof-carrying")
    for path, content in files.items():
        if path.endswith(".py"):
            try:
                ast.parse(content.replace("__PKG__", slug))
            except SyntaxError as exc:
                raise MintError(f"pack {record['key']!r} file {path!r} does not parse: {exc}") from exc
    return record


def concrete_files(record: dict) -> dict[str, str]:
    """A pack record's files with __PKG__ substituted — a runnable project."""
    slug = record["default_slug"]
    return {
        path.replace("__PKG__", slug): content.replace("__PKG__", slug)
        for path, content in record["files"].items()
    }


def mint_report(record: dict, tests_run: int) -> dict:
    return provenance(
        "mint",
        RULE_VERSION,
        pack=record["key"],
        package=record["default_slug"],
        keywords=record["keywords"],
        files=sorted(record["files"]),
        tests_verified=tests_run,
    )
