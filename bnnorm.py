"""Canonical Bengali text normalisation for indexing and querying.

The single rule that matters: **index side and query side must call the same
function.** A query for ৫০,০০০ against a digit-folded index returns nothing --
no error, no warning, just a plausible answer from the wrong chunk. Nothing
throws, so the failure survives to production.

Two normalisations, both of which must be symmetric:

  fold_digits   ০১২৩৪৫৬৭৮৯ -> 0123456789
  strip_groups  thousands separators inside a number: 40,000 -> 40000

Guarded by tests/test_digit_fold.py, which asserts a Bengali-digit query and
its ASCII equivalent retrieve the identical top-k, AND that dropping either
normalisation on either side makes that assertion fail.
"""
import re

BN_DIGITS = "০১২৩৪৫৬৭৮৯"
_FOLD = {ord(c): str(i) for i, c in enumerate(BN_DIGITS)}

# A number, in either digit system, with optional , or . separators.
_TOKEN = re.compile(r"[০-৯0-9]+(?:[.,][০-৯0-9]+)*"   # numbers first
                    r"|[ঀ-৿]+"              # Bengali words
                    r"|[A-Za-z]+")                    # latin words
_GROUPS = re.compile(r"(?<=\d),(?=\d)")


def fold_digits(s: str) -> str:
    """Bengali digits -> ASCII. Matches ex.py's fold() exactly."""
    return (s or "").translate(_FOLD)


def strip_groups(s: str) -> str:
    """Remove thousands separators *inside* numbers: 40,000 -> 40000.

    Runs after fold_digits, so the lookarounds only need to know ASCII digits.
    """
    return _GROUPS.sub("", s or "")


def canon(s: str) -> str:
    """The canonical form. Call this on documents AND on queries."""
    return strip_groups(fold_digits(s))


def tokenize(s: str) -> list:
    """Tokenise already-canonicalised text."""
    return _TOKEN.findall(s or "")


def canon_tokens(s: str) -> list:
    return tokenize(canon(s))
