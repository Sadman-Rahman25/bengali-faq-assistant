"""Canonical Bengali text normalisation for indexing and querying.

The single rule that matters: **index side and query side must call the same
function.** A query for ৫০,০০০ against a digit-folded index returns nothing --
no error, no warning, just a plausible answer from the wrong chunk. Nothing
throws, so the failure survives to production.

Three normalisations, all of which must be symmetric:

  nfc           Unicode NFC
  fold_digits   ০১২৩৪৫৬৭৮৯ -> 0123456789
  strip_groups  thousands separators inside a number: 40,000 -> 40000

NFC matters more than it looks. য় ড় ঢ় (U+09DF/09DC/09DD) are Unicode
composition exclusions, so NFC DECOMPOSES them into base + nukta (U+09BC).
A string literal typed with the precomposed form therefore never matches
NFC-normalised corpus text, and nothing throws -- it just matches fewer
occurrences. গার্ডিয়ান লাইফ was found in 2 of its 12 chunks that way.

Because of that, canon() must be applied to PATTERNS as well as to text.
Anything comparing a hard-coded Bengali literal against the corpus is
exposed, since whether a .py file stores য় composed or decomposed depends on
the editor that saved it.

Guarded by tests/test_digit_fold.py, which asserts a Bengali-digit query and
its ASCII equivalent retrieve the identical top-k, AND that dropping either
normalisation on either side makes that assertion fail.
"""
import re
import unicodedata

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


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "")


def canon(s: str) -> str:
    """The canonical form for TEXT: documents and queries."""
    return strip_groups(fold_digits(nfc(s)))


def canon_pattern(p: str) -> str:
    """The canonical form for a REGEX matched against canon()'d text.

    Deliberately omits strip_groups. That rule deletes a comma between two
    digits, which inside a regex silently rewrites a quantifier:

        \\S{0,4}   ->   \\S{04}

    turning "up to four" into "exactly four". It cost গার্ডিয়ান লাইফ all 12
    of its matches, which is the same silent-under-match failure this whole
    normalisation exists to prevent -- reintroduced by over-normalising.

    Separator stripping is a text-only concern anyway: a pattern author who
    wants to match a grouped number writes the already-stripped form, because
    the text side has stripped it.
    """
    return fold_digits(nfc(p))


def tokenize(s: str) -> list:
    """Tokenise already-canonicalised text."""
    return _TOKEN.findall(s or "")


def canon_tokens(s: str) -> list:
    return tokenize(canon(s))
