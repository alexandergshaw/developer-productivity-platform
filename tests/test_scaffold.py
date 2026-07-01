import unittest

from detcode.determinism import content_hash
from detcode.engines import scaffold
from detcode.engines.scaffold import SpecError


SPEC = {
    "module_doc": "Domain models.",
    "enums": [{"name": "Status", "members": ["ACTIVE", "INACTIVE"]}],
    "dataclasses": [
        {
            "name": "User",
            "doc": "A user.",
            "frozen": True,
            "fields": [
                {"name": "id", "type": "int"},
                {"name": "name", "type": "str", "default": '""'},
                {"name": "status", "type": "Status", "default": "Status.ACTIVE"},
            ],
            "methods": ["to_dict", "from_dict"],
        }
    ],
}


class ScaffoldTests(unittest.TestCase):
    def test_generates_valid_module(self):
        source = scaffold.scaffold(SPEC).source
        # It must be importable and behave correctly.
        namespace: dict = {}
        exec(compile(source, "<generated>", "exec"), namespace)
        User = namespace["User"]
        Status = namespace["Status"]
        u = User(id=1, name="ada", status=Status.ACTIVE)
        self.assertEqual(u.to_dict(), {"id": 1, "name": "ada", "status": "ACTIVE"})
        # from_dict reconstructs, converting the enum from its value.
        u2 = User.from_dict({"id": 1, "name": "ada", "status": "ACTIVE"})
        self.assertEqual(u2, u)

    def test_frozen_dataclass(self):
        source = scaffold.scaffold(SPEC).source
        self.assertIn("@dataclass(frozen=True)", source)

    def test_deterministic(self):
        hashes = {content_hash(scaffold.scaffold(SPEC).source) for _ in range(20)}
        self.assertEqual(len(hashes), 1)

    def test_method_order_is_canonical(self):
        # Spec lists methods to_dict then from_dict, but from_dict is emitted
        # first per the fixed canonical order.
        source = scaffold.scaffold(SPEC).source
        self.assertLess(source.index("def from_dict"), source.index("def to_dict"))

    def test_refuses_nondefault_after_default(self):
        spec = {
            "dataclasses": [
                {
                    "name": "Bad",
                    "fields": [
                        {"name": "a", "type": "int", "default": "0"},
                        {"name": "b", "type": "int"},
                    ],
                }
            ]
        }
        with self.assertRaises(SpecError):
            scaffold.scaffold(spec)

    def test_refuses_mutable_default(self):
        spec = {
            "dataclasses": [
                {"name": "Bad", "fields": [{"name": "a", "type": "list", "default": "[]"}]}
            ]
        }
        with self.assertRaises(SpecError):
            scaffold.scaffold(spec)

    def test_refuses_bad_identifier(self):
        spec = {"dataclasses": [{"name": "1Bad", "fields": [{"name": "a", "type": "int"}]}]}
        with self.assertRaises(SpecError):
            scaffold.scaffold(spec)

    def test_refuses_duplicate_type_name(self):
        spec = {
            "enums": [{"name": "X", "members": ["A"]}],
            "dataclasses": [{"name": "X", "fields": [{"name": "a", "type": "int"}]}],
        }
        with self.assertRaises(SpecError):
            scaffold.scaffold(spec)

    def test_refuses_unsupported_method(self):
        spec = {
            "dataclasses": [
                {"name": "X", "fields": [{"name": "a", "type": "int"}], "methods": ["frobnicate"]}
            ]
        }
        with self.assertRaises(SpecError):
            scaffold.scaffold(spec)

    def test_refuses_empty_spec(self):
        with self.assertRaises(SpecError):
            scaffold.scaffold({})


if __name__ == "__main__":
    unittest.main()
