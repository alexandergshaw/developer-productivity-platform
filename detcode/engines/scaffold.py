"""Vertical 2 — deterministic scaffolding / codegen from a structured spec.

Given a spec (a plain dict, typically parsed from JSON), generate a Python
module of enums and dataclasses. The emitter is string-based with fixed
formatting rules, so output is byte-identical across Python versions (unlike
``ast.unparse``, whose formatting can drift between releases).

Correctness-by-refusal: invalid identifiers, duplicate names, mutable default
literals, non-default fields following defaulted ones, and unsupported method
names are all refused with a clear message rather than emitted as broken code.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass

from ..determinism import provenance

RULE_VERSION = "1"
SUPPORTED_METHODS = ("from_dict", "to_dict")
INDENT = "    "


class SpecError(Exception):
    """The spec was invalid; scaffolding was refused."""


@dataclass
class Result:
    source: str
    report: dict


# --------------------------------------------------------------------------- #
# validation helpers
# --------------------------------------------------------------------------- #
def _require_identifier(value, what: str) -> str:
    if not isinstance(value, str) or not value.isidentifier():
        raise SpecError(f"{what} must be a valid identifier, got {value!r}")
    return value


def _require_type_expr(value, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SpecError(f"{what} must be a non-empty type expression")
    try:
        ast.parse(value, mode="eval")
    except SyntaxError as exc:
        raise SpecError(f"{what} is not a valid type expression: {value!r}") from exc
    return value


def _check_default_expr(value, what: str) -> str:
    try:
        node = ast.parse(value, mode="eval").body
    except SyntaxError as exc:
        raise SpecError(f"{what} is not a valid expression: {value!r}") from exc
    if isinstance(node, (ast.List, ast.Dict, ast.Set)):
        raise SpecError(
            f"{what} is a mutable literal ({value!r}); use "
            f'"field(default_factory=...)" instead'
        )
    return value


# --------------------------------------------------------------------------- #
# emitters
# --------------------------------------------------------------------------- #
def _emit_enum(spec: dict) -> str:
    name = _require_identifier(spec.get("name"), "enum name")
    members = spec.get("members") or []
    if not members:
        raise SpecError(f"enum {name!r} has no members")
    lines = [f"class {name}(Enum):"]
    doc = spec.get("doc")
    if doc:
        lines.append(f'{INDENT}"""{doc}"""')
    for member in members:
        _require_identifier(member, f"enum member of {name}")
        lines.append(f'{INDENT}{member} = "{member}"')
    return "\n".join(lines)


def _emit_dataclass(spec: dict, enum_names: frozenset[str]) -> str:
    name = _require_identifier(spec.get("name"), "dataclass name")
    fields = spec.get("fields") or []
    if not fields:
        raise SpecError(f"dataclass {name!r} has no fields")

    decorator = "@dataclass(frozen=True)" if spec.get("frozen") else "@dataclass"
    lines = [decorator, f"class {name}:"]
    doc = spec.get("doc")
    if doc:
        lines.append(f'{INDENT}"""{doc}"""')

    seen: set[str] = set()
    seen_default = False
    field_info: list[tuple[str, str]] = []  # (field_name, type)
    for field in fields:
        fname = _require_identifier(field.get("name"), f"field of {name}")
        if fname in seen:
            raise SpecError(f"duplicate field {fname!r} in {name!r}")
        seen.add(fname)
        ftype = _require_type_expr(field.get("type"), f"type of {name}.{fname}")
        field_info.append((fname, ftype))
        if "default" in field:
            default = _check_default_expr(str(field["default"]), f"default of {name}.{fname}")
            lines.append(f"{INDENT}{fname}: {ftype} = {default}")
            seen_default = True
        else:
            if seen_default:
                raise SpecError(
                    f"field {fname!r} has no default but follows a defaulted field in {name!r}"
                )
            lines.append(f"{INDENT}{fname}: {ftype}")

    methods = spec.get("methods") or []
    for method in methods:
        if method not in SUPPORTED_METHODS:
            raise SpecError(
                f"unsupported method {method!r}; supported: {', '.join(SUPPORTED_METHODS)}"
            )
    # Emit in a fixed order regardless of spec order, for canonical output.
    for method in SUPPORTED_METHODS:
        if method == "to_dict" and "to_dict" in methods:
            lines.append("")
            lines.extend(_emit_to_dict(field_info, enum_names))
        if method == "from_dict" and "from_dict" in methods:
            lines.append("")
            lines.extend(_emit_from_dict(name, field_info, enum_names))
    return "\n".join(lines)


def _emit_to_dict(field_info, enum_names) -> list[str]:
    entries = []
    for fname, ftype in field_info:
        value = f"self.{fname}.value" if ftype in enum_names else f"self.{fname}"
        entries.append(f'{INDENT}{INDENT}{INDENT}"{fname}": {value},')
    body = [f"{INDENT}def to_dict(self) -> dict:", f"{INDENT}{INDENT}return {{"]
    body.extend(entries)
    body.append(f"{INDENT}{INDENT}}}")
    return body


def _emit_from_dict(name, field_info, enum_names) -> list[str]:
    args = []
    for fname, ftype in field_info:
        if ftype in enum_names:
            args.append(f'{INDENT}{INDENT}{INDENT}{fname}={ftype}(data["{fname}"]),')
        else:
            args.append(f'{INDENT}{INDENT}{INDENT}{fname}=data["{fname}"],')
    body = [
        f"{INDENT}@classmethod",
        f'{INDENT}def from_dict(cls, data: dict) -> "{name}":',
        f"{INDENT}{INDENT}return cls(",
    ]
    body.extend(args)
    body.append(f"{INDENT}{INDENT})")
    return body


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def scaffold(spec: dict) -> Result:
    """Generate a Python module from ``spec``."""
    if not isinstance(spec, dict):
        raise SpecError("spec must be a JSON object")
    enums = spec.get("enums") or []
    dataclasses_ = spec.get("dataclasses") or []
    if not enums and not dataclasses_:
        raise SpecError("spec generates nothing: provide 'enums' and/or 'dataclasses'")

    enum_names = frozenset(_require_identifier(e.get("name"), "enum name") for e in enums)
    class_names: set[str] = set()
    for group, label in ((enums, "enum"), (dataclasses_, "dataclass")):
        for item in group:
            cname = item.get("name")
            if cname in class_names:
                raise SpecError(f"duplicate type name {cname!r}")
            class_names.add(cname)

    units: list[str] = []

    # Preamble: module docstring + a deterministic, sorted import block.
    imports = []
    if dataclasses_:
        imports.append("from dataclasses import dataclass, field")
    if enums:
        imports.append("from enum import Enum")
    preamble_parts = []
    module_doc = spec.get("module_doc")
    if module_doc:
        preamble_parts.append(f'"""{module_doc}"""')
    if imports:
        preamble_parts.append("\n".join(sorted(imports)))
    if preamble_parts:
        units.append("\n\n".join(preamble_parts))

    for enum in enums:
        units.append(_emit_enum(enum))
    for dc in dataclasses_:
        units.append(_emit_dataclass(dc, enum_names))

    source = "\n\n\n".join(units) + "\n"
    ast.parse(source)  # generated code must be valid Python

    report = provenance(
        "scaffold",
        RULE_VERSION,
        enums=sorted(enum_names),
        dataclasses=sorted(d.get("name") for d in dataclasses_),
    )
    return Result(source, report)
