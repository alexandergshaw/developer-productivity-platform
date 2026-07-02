"""Deterministic code retrieval — a verified corpus of classic functions.

The enumerative synthesizer writes single expressions; it cannot produce loops
or recursion. But much of what people ask an LLM for is a *known* function —
is_prime, fibonacci, gcd, binary_search. This engine holds a fixed corpus of
hand-verified implementations and matches them against the user's examples:
every candidate is executed on every example, and only an entry that passes
them all is returned. The examples are the oracle; the corpus is just a
library of hypotheses.

Determinism: the corpus order is fixed, entries whose canonical name equals
the requested name are tried first (then corpus order), and the first fully
verified entry wins. Inputs beyond fixed size bounds skip retrieval entirely
(so no corpus loop can run unboundedly long) and fall through to synthesis.
"""
from __future__ import annotations

import io
import tokenize
from dataclasses import dataclass

from ..determinism import provenance
from . import synth as synth_engine

RULE_VERSION = "1"

_MAX_INT = 10**6
_MAX_SEQ = 10**4


class NoMatch(Exception):
    """No corpus entry is consistent with the examples."""


@dataclass(frozen=True)
class Entry:
    name: str
    arity: int
    source: str


@dataclass
class Result:
    source: str
    report: dict


def _entry(name: str, arity: int, source: str) -> Entry:
    return Entry(name, arity, source.strip("\n") + "\n")


# The corpus. Every entry must terminate on ALL inputs within the size bounds
# (no unbounded loops on user data) and carry a docstring.
CORPUS: tuple[Entry, ...] = (
    _entry("is_prime", 1, '''
def is_prime(n):
    """Return True if n is a prime number."""
    if n < 2:
        return False
    i = 2
    while i * i <= n:
        if n % i == 0:
            return False
        i += 1
    return True
'''),
    _entry("fibonacci", 1, '''
def fibonacci(n):
    """Return the nth Fibonacci number (fibonacci(0) == 0)."""
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a
'''),
    _entry("factorial", 1, '''
def factorial(n):
    """Return n! (and 1 for n < 2)."""
    result = 1
    for i in range(2, n + 1):
        result *= i
    return result
'''),
    _entry("gcd", 2, '''
def gcd(a, b):
    """Return the greatest common divisor of a and b."""
    while b:
        a, b = b, a % b
    return abs(a)
'''),
    _entry("lcm", 2, '''
def lcm(a, b):
    """Return the least common multiple of a and b."""
    if a == 0 or b == 0:
        return 0
    x, y = abs(a), abs(b)
    while y:
        x, y = y, x % y
    return abs(a * b) // x
'''),
    _entry("is_palindrome", 1, '''
def is_palindrome(s):
    """Return True if s reads the same forwards and backwards."""
    return s == s[::-1]
'''),
    _entry("reverse_string", 1, '''
def reverse_string(s):
    """Return s reversed."""
    return s[::-1]
'''),
    _entry("count_vowels", 1, '''
def count_vowels(s):
    """Return the number of vowels in s."""
    return sum(1 for ch in s.lower() if ch in "aeiou")
'''),
    _entry("fizzbuzz", 1, '''
def fizzbuzz(n):
    """Return "Fizz"/"Buzz"/"FizzBuzz" for multiples of 3/5/15, else str(n)."""
    if n % 15 == 0:
        return "FizzBuzz"
    if n % 3 == 0:
        return "Fizz"
    if n % 5 == 0:
        return "Buzz"
    return str(n)
'''),
    _entry("is_even", 1, '''
def is_even(n):
    """Return True if n is even."""
    return n % 2 == 0
'''),
    _entry("is_odd", 1, '''
def is_odd(n):
    """Return True if n is odd."""
    return n % 2 != 0
'''),
    _entry("sum_digits", 1, '''
def sum_digits(n):
    """Return the sum of the decimal digits of n (sign ignored)."""
    n = abs(n)
    total = 0
    while n:
        total += n % 10
        n //= 10
    return total
'''),
    _entry("is_anagram", 2, '''
def is_anagram(a, b):
    """Return True if a and b are anagrams (case-insensitive)."""
    return sorted(a.lower()) == sorted(b.lower())
'''),
    _entry("clamp", 3, '''
def clamp(n, low, high):
    """Return n limited to the inclusive range [low, high]."""
    return min(max(n, low), high)
'''),
    _entry("sign", 1, '''
def sign(n):
    """Return -1, 0, or 1 according to the sign of n."""
    return (n > 0) - (n < 0)
'''),
    _entry("median", 1, '''
def median(nums):
    """Return the median of a non-empty list of numbers."""
    ordered = sorted(nums)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2
'''),
    _entry("average", 1, '''
def average(nums):
    """Return the arithmetic mean of a non-empty list of numbers."""
    return sum(nums) / len(nums)
'''),
    _entry("count_words", 1, '''
def count_words(s):
    """Return the number of whitespace-separated words in s."""
    return len(s.split())
'''),
    _entry("capitalize_words", 1, '''
def capitalize_words(s):
    """Return s with each whitespace-separated word capitalized."""
    return " ".join(word.capitalize() for word in s.split())
'''),
    _entry("remove_duplicates", 1, '''
def remove_duplicates(items):
    """Return items with duplicates removed, first occurrence kept."""
    seen = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen
'''),
    _entry("is_leap_year", 1, '''
def is_leap_year(year):
    """Return True if year is a leap year in the Gregorian calendar."""
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
'''),
    _entry("snake_to_camel", 1, '''
def snake_to_camel(s):
    """Convert snake_case to lowerCamelCase."""
    parts = [p for p in s.split("_") if p]
    if not parts:
        return ""
    return parts[0] + "".join(p.capitalize() for p in parts[1:])
'''),
    _entry("camel_to_snake", 1, '''
def camel_to_snake(s):
    """Convert camelCase (or PascalCase) to snake_case."""
    out = []
    for ch in s:
        if ch.isupper():
            out.append("_" + ch.lower())
        else:
            out.append(ch)
    return "".join(out).lstrip("_")
'''),
    _entry("binary_search", 2, '''
def binary_search(items, target):
    """Return the index of target in sorted items, or -1 if absent."""
    low, high = 0, len(items) - 1
    while low <= high:
        mid = (low + high) // 2
        if items[mid] == target:
            return mid
        if items[mid] < target:
            low = mid + 1
        else:
            high = mid - 1
    return -1
'''),
    _entry("is_sorted", 1, '''
def is_sorted(items):
    """Return True if items are in non-decreasing order."""
    return all(items[i] <= items[i + 1] for i in range(len(items) - 1))
'''),
    _entry("char_frequency", 1, '''
def char_frequency(s):
    """Return a dict mapping each character in s to its count."""
    counts = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    return counts
'''),
    _entry("celsius_to_fahrenheit", 1, '''
def celsius_to_fahrenheit(c):
    """Convert degrees Celsius to Fahrenheit."""
    return c * 9 / 5 + 32
'''),
)


def _too_big(value) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return abs(value) > _MAX_INT
    if isinstance(value, (str, list)):
        return len(value) > _MAX_SEQ or (
            isinstance(value, list) and any(_too_big(v) for v in value)
        )
    return False


def _validate(spec: dict) -> tuple[str, list[dict], int]:
    if not isinstance(spec, dict):
        raise NoMatch("spec must be a JSON object")
    examples = spec.get("examples")
    if not isinstance(examples, list) or not examples:
        raise NoMatch("no examples to verify against")
    arity = None
    for ex in examples:
        if not isinstance(ex, dict) or "in" not in ex or "out" not in ex:
            raise NoMatch("malformed example")
        if not isinstance(ex["in"], list):
            raise NoMatch("malformed example")
        if arity is None:
            arity = len(ex["in"])
        elif len(ex["in"]) != arity:
            raise NoMatch("inconsistent arity")
        if any(_too_big(v) for v in ex["in"]) or _too_big(ex["out"]):
            raise NoMatch("example values exceed corpus verification bounds")
    name = spec.get("name", "f")
    if not isinstance(name, str) or not name.isidentifier():
        raise NoMatch(f"function name {name!r} is not a valid identifier")
    return name, examples, arity


def _rename(source: str, old: str, new: str) -> str | None:
    """Rename identifier ``old`` to ``new``; None if ``new`` already appears."""
    if new == old:
        return source
    tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    if any(t.type == tokenize.NAME and t.string == new for t in tokens):
        return None
    out = []
    for tok in tokens:
        if tok.type == tokenize.NAME and tok.string == old:
            out.append(tok._replace(string=new))
        else:
            out.append(tok)
    return tokenize.untokenize(out)


def _passes(source: str, func_name: str, examples: list[dict]) -> bool:
    namespace: dict = {}
    try:
        exec(compile(source, "<corpus>", "exec"), namespace)
        fn = namespace[func_name]
        for ex in examples:
            if fn(*ex["in"]) != ex["out"]:
                return False
    except Exception:
        return False
    return True


def retrieve(spec: dict, extra: tuple[Entry, ...] = ()) -> Result:
    """Return a corpus entry verified against every example, or refuse.

    ``extra`` holds user-taught entries; they are tried after the built-in
    corpus (name matches still jump the queue across both).
    """
    name, examples, arity = _validate(spec)

    candidates = tuple(CORPUS) + tuple(extra)
    # Entries whose canonical name matches the request are tried first.
    ordered = sorted(
        enumerate(candidates), key=lambda pair: (0 if pair[1].name == name else 1, pair[0])
    )
    builtin_count = len(CORPUS)
    for index, entry in ordered:
        if entry.arity != arity:
            continue
        if not _passes(entry.source, entry.name, examples):
            continue
        source = _rename(entry.source, entry.name, name)
        if source is None:
            continue  # requested name collides with the entry's internals
        if name != entry.name and not _passes(source, name, examples):
            continue  # defense in depth: renamed copy must still verify
        report = provenance(
            "retrieve",
            RULE_VERSION,
            entry=entry.name,
            renamed_to=name,
            origin="builtin" if index < builtin_count else "user",
            cases_verified=len(examples),
        )
        return Result(source, report)

    raise NoMatch("no corpus entry is consistent with the examples")


def write_function(spec: dict, extra: tuple[Entry, ...] = ()) -> Result:
    """The combined "write me a function" capability.

    Retrieval first (cheap, and covers loops/recursion the synthesizer cannot
    express); on no match, fall back to enumerative synthesis. Both paths
    verify against every example, and provenance records which engine won.
    """
    try:
        return retrieve(spec, extra)
    except NoMatch:
        r = synth_engine.synthesize(spec)
        return Result(r.source, r.report)
