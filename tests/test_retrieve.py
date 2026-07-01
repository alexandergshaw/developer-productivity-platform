import unittest

from detcode.determinism import content_hash
from detcode.engines import retrieve
from detcode.engines.retrieve import NoMatch


def run(source, name):
    ns: dict = {}
    exec(compile(source, "<t>", "exec"), ns)
    return ns[name]


class RetrieveTests(unittest.TestCase):
    def test_is_prime_from_examples(self):
        spec = {
            "name": "is_prime",
            "examples": [
                {"in": [7], "out": True},
                {"in": [8], "out": False},
                {"in": [1], "out": False},
                {"in": [2], "out": True},
            ],
        }
        result = retrieve.retrieve(spec)
        f = run(result.source, "is_prime")
        self.assertTrue(f(97))
        self.assertFalse(f(100))
        self.assertEqual(result.report["entry"], "is_prime")

    def test_loops_and_recursion_reachable(self):
        # fibonacci needs a loop — impossible for the synthesizer, easy here.
        spec = {
            "name": "fibonacci",
            "examples": [{"in": [0], "out": 0}, {"in": [1], "out": 1}, {"in": [7], "out": 13}],
        }
        result = retrieve.retrieve(spec)
        self.assertEqual(run(result.source, "fibonacci")(10), 55)

    def test_renames_to_requested_name(self):
        spec = {
            "name": "prime_check",
            "examples": [
                {"in": [7], "out": True},
                {"in": [8], "out": False},
                {"in": [1], "out": False},
            ],
        }
        result = retrieve.retrieve(spec)
        self.assertIn("def prime_check(", result.source)
        self.assertTrue(run(result.source, "prime_check")(11))
        self.assertEqual(result.report["renamed_to"], "prime_check")

    def test_name_match_breaks_ties(self):
        # f(1) == 1 fits many entries; naming it factorial should pick factorial.
        spec = {"name": "factorial", "examples": [{"in": [1], "out": 1}]}
        result = retrieve.retrieve(spec)
        self.assertEqual(result.report["entry"], "factorial")

    def test_dict_returning_entry(self):
        spec = {
            "name": "char_frequency",
            "examples": [{"in": ["aab"], "out": {"a": 2, "b": 1}}],
        }
        result = retrieve.retrieve(spec)
        self.assertEqual(run(result.source, "char_frequency")("zzz"), {"z": 3})

    def test_contradicting_examples_refused(self):
        spec = {"examples": [{"in": [7], "out": "purple"}]}
        with self.assertRaises(NoMatch):
            retrieve.retrieve(spec)

    def test_oversized_inputs_skip_retrieval(self):
        spec = {"examples": [{"in": [10**9], "out": True}]}
        with self.assertRaises(NoMatch):
            retrieve.retrieve(spec)

    def test_deterministic(self):
        spec = {
            "name": "is_prime",
            "examples": [{"in": [7], "out": True}, {"in": [8], "out": False}],
        }
        hashes = {content_hash(retrieve.retrieve(spec).source) for _ in range(10)}
        self.assertEqual(len(hashes), 1)

    def test_every_corpus_entry_is_valid_and_documented(self):
        import ast
        for entry in retrieve.CORPUS:
            tree = ast.parse(entry.source)
            fns = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
            names = [f.name for f in fns]
            self.assertIn(entry.name, names, entry.name)
            target = next(f for f in fns if f.name == entry.name)
            self.assertEqual(len(target.args.args), entry.arity, entry.name)
            self.assertIsNotNone(ast.get_docstring(target), entry.name)


class WriteFunctionTests(unittest.TestCase):
    def test_retrieval_first(self):
        spec = {
            "name": "is_prime",
            "examples": [{"in": [7], "out": True}, {"in": [8], "out": False}],
        }
        result = retrieve.write_function(spec)
        self.assertEqual(result.report["rule"], "retrieve")

    def test_synth_fallback(self):
        spec = {"name": "double", "examples": [{"in": [2], "out": 4}, {"in": [5], "out": 10}]}
        result = retrieve.write_function(spec)
        self.assertEqual(result.report["rule"], "synthesize")
        self.assertIn("def double(x):", result.source)

    def test_no_engine_matches_refused(self):
        from detcode.engines.synth import NoSolution
        spec = {
            "examples": [{"in": [1], "out": 999983}, {"in": [2], "out": 314159}],
            "max_depth": 2,
        }
        with self.assertRaises(NoSolution):
            retrieve.write_function(spec)


if __name__ == "__main__":
    unittest.main()
