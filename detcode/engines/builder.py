"""Project builder — general direction in, runnable project out.

This is the "give it a direction and let it exercise independence" engine.
The independence is real but deterministic: every choice (which domain pack,
what package name, what layout) comes from a fixed decision procedure over the
direction's words, and **every decision is recorded** in the build report and
the generated README. Same direction, byte-identical project; and you can
always see why it built what it built.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from ..determinism import provenance
from .. import packs

RULE_VERSION = "1"

# Words that carry no domain meaning in a direction.
_DIRECTION_NOISE = frozenset(
    "a an the build make create start new me my for of and or to app apps "
    "application project projects tool please that will can helps help with "
    "some this it".split()
)


class BuildError(Exception):
    """The direction or target was unusable; building was refused."""


@dataclass(frozen=True)
class ProjectFile:
    path: str  # POSIX-style, relative
    content: str


@dataclass
class Project:
    name: str
    title: str
    files: tuple[ProjectFile, ...]
    report: dict


def _words(direction: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", direction.lower())


def _slug(direction: str) -> str:
    meaningful = [w for w in _words(direction) if w not in _DIRECTION_NOISE]
    slug = "_".join(meaningful[:4])
    if not slug:
        return "new_project"
    if slug[0].isdigit():
        slug = "p_" + slug
    return slug


def _title(slug: str) -> str:
    return " ".join(part.capitalize() for part in slug.split("_"))


def _order_matches(direction: str, matches: list) -> list:
    """Primary pack = the one whose keyword appears earliest in the direction.

    Deterministic: position of the first matched keyword, then registry order.
    """
    ordered_words = _words(direction)

    def first_position(indexed) -> tuple:
        registry_index, (pack, hits) = indexed
        positions = [ordered_words.index(h) for h in hits if h in ordered_words]
        return (min(positions) if positions else len(ordered_words), registry_index)

    return [m for _, m in sorted(enumerate(matches), key=first_position)]


def elaborate(direction: str, name: str | None = None) -> dict:
    """Derive the build plan from the direction, recording every decision."""
    if not isinstance(direction, str) or not direction.strip():
        raise BuildError("give a direction, e.g. detcode new \"resume tailorer\"")

    decisions: list[str] = []
    words = set(_words(direction))

    matches = _order_matches(direction, packs.match_all(words))
    if not matches:
        generic = packs.registry()[-1]
        matches = [(generic, [])]
        decisions.append(
            "no domain pack matched this direction — generating "
            f"{generic.description} (the honest deterministic boundary: structure "
            "is derivable, novel domain logic is not)"
        )
    elif len(matches) == 1:
        pack, hits = matches[0]
        decisions.append(
            f"matched the {pack.title!r} domain pack on keyword(s): {', '.join(hits)} — "
            f"generating {pack.description}"
        )
    else:
        names = ", ".join(
            f"{p.title} ({', '.join(h)})" for p, h in matches
        )
        decisions.append(
            f"composed {len(matches)} domain packs — {names} — the first is "
            "primary (its keyword appears earliest in the direction); each "
            "ships as its own package in one project"
        )

    primary = matches[0][0]
    slug = name or primary.default_slug or _slug(direction)
    if not slug.isidentifier():
        raise BuildError(f"package name {slug!r} is not a valid identifier")
    decisions.append(
        f"package name {slug!r} " + ("taken from --name" if name else "derived from the direction")
    )

    # (pack, package_slug) pairs: the primary takes the project slug, the
    # rest keep their own default slugs.
    pack_slugs = [(primary, slug)]
    for pack, _hits in matches[1:]:
        pack_slugs.append((pack, pack.default_slug))

    entrypoints = " / ".join(f"python -m {s}" for _, s in pack_slugs)
    decisions.append(
        f"interface: command-line ({entrypoints}) — deterministic core "
        "first; a UI can wrap it later"
    )
    decisions.append("test suite included; run: python -m unittest discover -s tests")

    return {
        "direction": direction.strip(),
        "slug": slug,
        "title": _title(slug),
        "pack": primary,
        "pack_slugs": pack_slugs,
        "decisions": decisions,
    }


def _readme(plan: dict) -> str:
    lines = [
        f"# {plan['title']}",
        "",
        f'Generated deterministically by detcode from the direction: "{plan["direction"]}".',
        "Same direction, byte-identical project — regenerate any time.",
        "",
        "## Decisions detcode made",
        "",
    ]
    lines.extend(f"- {d}" for d in plan["decisions"])
    lines.extend(
        [
            "",
            "## Usage",
            "",
            "```bash",
            f"python -m {plan['slug']} --help",
            "```",
            "",
            "## Development",
            "",
            "```bash",
            "python -m unittest discover -s tests",
            "```",
            "",
            "## Growing this project with detcode",
            "",
            "```bash",
            'detcode do "write a function <name> where <name>(...) == ..."',
            f'detcode do "add a docstring to <func>" --file {plan["slug"]}/core.py --write',
            f'detcode gentest --spec examples.json --file {plan["slug"]}/core.py',
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _pyproject(plan: dict) -> str:
    includes = ", ".join(f'"{s}*"' for _, s in plan["pack_slugs"])
    return (
        "[build-system]\n"
        'requires = ["setuptools>=68"]\n'
        'build-backend = "setuptools.build_meta"\n'
        "\n"
        "[project]\n"
        f'name = "{plan["slug"].replace("_", "-")}"\n'
        'version = "0.1.0"\n'
        f'description = "{plan["title"]} (generated by detcode)"\n'
        'requires-python = ">=3.10"\n'
        "dependencies = []\n"
        "\n"
        "[project.scripts]\n"
        f'{plan["slug"].replace("_", "-")} = "{plan["slug"]}.cli:main"\n'
        "\n"
        "[tool.setuptools.packages.find]\n"
        f"include = [{includes}]\n"
    )


def build(direction: str, name: str | None = None) -> Project:
    """Build a complete project from a general direction.

    A direction matching several packs ("a teaching assistant with a resume
    module") composes them: each pack's package lands in the same project.
    """
    plan = elaborate(direction, name)
    slug = plan["slug"]

    merged: dict[str, str] = {}
    for pack, pack_slug in plan["pack_slugs"]:
        for raw_path, raw_content in sorted(pack.files().items()):
            path = raw_path.replace("__PKG__", pack_slug)
            content = raw_content.replace("__PKG__", pack_slug)
            if path.endswith(".py"):
                try:
                    ast.parse(content)
                except SyntaxError as exc:  # a pack template must never ship broken
                    raise BuildError(f"pack template {raw_path!r} is invalid: {exc}") from exc
            if path in merged and merged[path] != content:
                raise BuildError(f"pack composition collides on {path!r}")
            merged[path] = content

    files = [ProjectFile(p, c) for p, c in merged.items()]
    files.append(ProjectFile("README.md", _readme(plan)))
    files.append(ProjectFile("pyproject.toml", _pyproject(plan)))
    files.append(ProjectFile(".gitignore", "__pycache__/\n*.py[cod]\n*.egg-info/\n"))
    files.sort(key=lambda f: f.path)

    report = provenance(
        "build",
        RULE_VERSION,
        pack=plan["pack"].key,
        packs=[p.key for p, _ in plan["pack_slugs"]],
        package=slug,
        packages=[s for _, s in plan["pack_slugs"]],
        decisions=plan["decisions"],
        files=[f.path for f in files],
    )
    return Project(slug, plan["title"], tuple(files), report)


def render(project: Project) -> str:
    """Flatten a project to a single readable text bundle (for previews)."""
    parts = [f"# Project: {project.name} — {len(project.files)} files"]
    parts.append("# Decisions:")
    parts.extend(f"#   - {d}" for d in project.report["decisions"])
    for f in project.files:
        parts.append(f"\n# ===== {f.path} =====")
        parts.append(f.content.rstrip("\n"))
    return "\n".join(parts) + "\n"
