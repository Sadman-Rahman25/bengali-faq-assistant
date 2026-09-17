"""bn_shared.py -- Bengali text primitives shared by the PDF ingestion path.

Convention 1: no side effects at import. Nothing here writes files.
Convention 2: this module contains NO product literals. Product and role
              names are owned by productmatch.py / roles.py and are pulled
              in at runtime by probe_words.py.

WHY canon() COMES FROM bnnorm BUT canon_pattern() DOES NOT
----------------------------------------------------------
`bnnorm.canon` is the repo's canonical text normaliser and this module uses
it verbatim: index side and query side must call the same function, which is
the single rule bnnorm exists to enforce.

`bnnorm.canon_pattern` is NOT used, for two independent reasons:

  1. It returns a normalised pattern *string*, not a compiled regex. Every
     call site here does `canon_pattern(x).search(...)`, so importing it
     raises AttributeError: 'str' object has no attribute 'search'.
  2. Its contract is different. It normalises an AUTHOR-WRITTEN REGEX, and
     so deliberately omits strip_groups -- because deleting a comma between
     two digits rewrites `\\S{0,4}` into `\\S{04}`, turning "up to four" into
     "exactly four". That cost গার্ডিয়ান লাইফ all 12 of its matches.

     What this module needs is the opposite job: compile a plain Bengali
     LITERAL into a regex that matches canon()'d text. A literal has no
     quantifiers to mangle, and it MUST be group-stripped, because the text
     side is. Otherwise the literal ৪০,০০০ normalises to "40,000" while the
     corpus reads "40000" and the probe silently finds nothing.

So: `canon` for text, `canon_pattern` for literals, and
`productmatch.compile_pat` for hand-written patterns. Three jobs, three
functions. Do not collapse them.

bnnorm IS A HARD DEPENDENCY. THERE IS NO FALLBACK.
This module used to carry its own canon/fold_digits behind
`try: from bnnorm import ... except ImportError:`, which meant the
normaliser in force depended on whether an import had succeeded, silently.
Two runs of the same script could normalise differently and nothing said so
-- the exact shape of CONVENTIONS.md section 3. The fallbacks were verified
identical to bnnorm across the whole Bengali block before deletion, so
removing them changed no output; it removed the ambiguity, not a behaviour.

Tolerance for zero-width joiners and for flexible whitespace lives in
canon_pattern, where it cannot desynchronise the two sides. Where the text
side is yours to normalise, display_norm handles zero-width at the root --
see Convention 4's preference order.
"""

import functools
import sys
import re
import unicodedata

# bnnorm owns the fold table and the canon. Not guarded: a missing bnnorm is
# a broken checkout, and failing at import is the only way that stays visible.
from bnnorm import canon, fold_digits

# ---------------------------------------------------------------- codepoints

BN_LO, BN_HI = 0x0980, 0x09FF
DANDA = "।"           # ।
DANDA_ALT = "৷"       # ৷  -- ex.py folds this onto DANDA
HASANT = "্"          # virama
NUKTA = "়"
ZERO_WIDTH = "​‌‍﻿­"
NBSP = " "

# A character class matching any run of zero-width characters. re.escape so
# that adding a codepoint to ZERO_WIDTH can never turn it into a metacharacter.
_ZW_CLASS = "[" + re.escape(ZERO_WIDTH) + "]*"

BN_DIGITS = "০১২৩৪৫৬৭৮৯"
DIGIT_MAP = {c: str(i) for i, c in enumerate(BN_DIGITS)}

CONSONANTS = "".join(chr(c) for c in range(0x0995, 0x09B9 + 1))
IND_VOWELS = "".join(chr(c) for c in range(0x0985, 0x0994 + 1))
MATRAS = "".join(chr(c) for c in range(0x09BE, 0x09CC + 1)
                 if c not in (0x09C5, 0x09C6))
PRE_BASE_MATRAS = "িেৈোৌ"

# Legacy Bijoy / ASCII-mapped font families. Presence of ANY of these in a
# PDF means part of the text layer is Latin bytes, which contributes zero
# Bengali characters and therefore zero malformed clusters -- invisible to
# every Bengali-orthography metric by construction.
LEGACY_FONTS = (
    "sutonnymj", "sutonnyemj", "sutonny", "shreelipi", "boishakhi",
    "amarbangla", "modhumati", "chandrabati", "lipishree", "borno",
    "aponalohit", "ananda", "proshika", "bijoy",
)


def is_bengali(ch: str) -> bool:
    return BN_LO <= ord(ch) <= BN_HI


def bengali_count(text: str) -> int:
    return sum(1 for ch in text if is_bengali(ch))


def strip_zero_width(text: str) -> str:
    return "".join(ch for ch in text if ch not in ZERO_WIDTH)


# ------------------------------------------------------------- normalisation

# Bengali nukta forms are on the Unicode COMPOSITION EXCLUSION list, so NFC
# DECOMPOSES য় into য + ় rather than composing it. canon() therefore puts
# both spellings into the decomposed form and the alternation below is
# belt-and-braces -- it stays because it costs nothing and it is the only
# thing protecting a literal that reaches a pattern without passing canon().
NUKTA_PAIRS = {
    "ড়": "ড়",   # ড়
    "ঢ়": "ঢ়",   # ঢ়
    "য়": "য়",   # য়
}
_DECOMP_TO_PRE = {v: k for k, v in NUKTA_PAIRS.items()}

# Matches nothing at all. Used for an empty literal, because re.compile("")
# matches EVERYTHING -- an empty probe term would then be reported as found
# on every page and inflate coverage toward a false CLEAN.
_NEVER = r"(?!)"


@functools.lru_cache(maxsize=4096)
def canon_pattern(literal: str) -> "re.Pattern":
    """Compile a Bengali LITERAL into a regex matching canon()'d text.

    Nukta-tolerant, zero-width-tolerant, and flexible about whitespace runs.
    The literal is passed through canon() -- the same normalisation the text
    side gets -- so digits and thousands separators cannot desynchronise.
    That is safe here precisely because this builds the regex from escaped
    single characters; there is no author-written quantifier to corrupt.
    """
    lit = canon(literal or "")
    toks, i, n = [], 0, len(lit)
    while i < n:
        two = lit[i:i + 2]
        ch = lit[i]
        if two in _DECOMP_TO_PRE:                    # য + ় in the literal
            toks.append("(?:%s|%s)"
                        % (re.escape(_DECOMP_TO_PRE[two]), re.escape(two)))
            i += 2
        elif ch in NUKTA_PAIRS:                      # য় in the literal
            toks.append("(?:%s|%s)"
                        % (re.escape(ch), re.escape(NUKTA_PAIRS[ch])))
            i += 1
        elif ch.isspace():
            while i < n and lit[i].isspace():
                i += 1
            toks.append(r"\s+")
        else:
            toks.append(re.escape(ch))
            i += 1
    if not toks:
        return re.compile(_NEVER)
    return re.compile(_ZW_CLASS.join(toks))


# ------------------------------------------------------------ counting
# WHY THIS EXISTS, AND WHY IT IS SHORT.
#
# A count is a match followed by an addition, so anything that counts a
# Bengali literal inherits every substring collision the matchers already
# guard against. §6.3's table, reproduced in role_exclusions.py:
#
#     দাবি      inside  বিমাদাবি           insurance claim
#     গতি       inside  অগ্রগতি / গতিশীল    progress, dynamic
#     স্বাধীন    inside  স্বাধীনতা           independence
#     সিডিপি    inside  এনসিডিপি / এসসিডিপি  different programmes
#     পিএ       inside  পিএলসি             public limited company
#
# All five are already handled structurally -- productmatch carries
# `(?<![নস])সিডিপি`, role_exclusions carries `"পিএ": ["পিএলসি"]`. What went
# wrong on 2026-09-17 was not a missing guard: it was a one-off `str.count`
# in a throwaway script that never reached either registry, and reported 440
# occurrences of the four programme acronyms where the guarded figure is 282.
# The number was then written into a gold-file note as evidence.
#
# So this is deliberately NOT a new convention. A rule costing more than the
# wrong thing collects exemptions. This is one call that is shorter than the
# loop it replaces, resolves the owning registry itself, and says so when no
# registry owns the literal:
#
#     occ, n = count_literal("সিডিপি", (c["display_text"] for c in corpus))
#
# CONVENTION 2 IS NOT BROKEN BY THIS. The literals stay in productmatch and
# roles; this function holds none and imports them at call time, which is
# also what keeps role_exclusions -> bn_shared from becoming a cycle.
_UNOWNED_WARNED = set()


def _resolve_owner(literal):
    """-> (kind, matcher). kind is "programme" | "product" | "role" | None.

    matcher is a callable str -> int over canon'd text. Registries are
    imported HERE, not at module scope: role_exclusions imports bn_shared.
    """
    lit = canon(literal or "")
    if not lit:
        return None, lambda t: 0

    try:
        import productmatch as pm
    except ImportError:                       # pragma: no cover
        pm = None
    if pm is not None:
        for key, rx in pm.PROGRAMMES:
            name = (pm.PROGRAMME_NAMES.get(key) or ("",))[0]
            if canon(name) == lit:
                return "programme", lambda t, rx=rx: len(rx.findall(t))
        for entry in pm.NAMED:
            key, name_bn, aliases, pat = entry[0], entry[1], entry[2], entry[3]
            names = [name_bn] + [a for a in str(aliases).split(";") if a.strip()]
            if any(canon(n) == lit for n in names):
                rx = pm.compile_pat(pat)
                return "product", lambda t, rx=rx: len(rx.findall(t))

    try:
        import role_exclusions as rex
    except ImportError:                       # pragma: no cover
        rex = None
    if rex is not None and lit in rex.EXCLUSIONS:
        return "role", lambda t, l=literal: rex.count_with_exclusions(t, l)

    return None, lambda t, l=literal: len(canon_pattern(l).findall(t))


def count_literal(literal, texts, warn=True):
    """Count `literal` across `texts` through its owning registry.

    -> (occurrences, n_chunks): total matches, and how many of `texts`
    contain at least one. `texts` may be any iterable of strings, including a
    generator -- it is consumed once.

    The registry's own pattern is used, so the exclusions it already encodes
    apply: `count_literal("সিডিপি", ...)` will not count the tails of
    এনসিডিপি or এসসিডিপি, and `count_literal("পিএ", ...)` masks পিএলসি first.

    When NO registry owns the literal the count still runs, nukta-tolerantly,
    but a one-time warning names it on stderr. Silence would make this
    indistinguishable from `str.count` for exactly the literals nobody has
    written a guard for yet. Pass warn=False in a test that means it.
    """
    kind, matcher = _resolve_owner(literal)
    if kind is None and warn:
        key = canon(literal or "")
        if key not in _UNOWNED_WARNED:
            _UNOWNED_WARNED.add(key)
            print(f"[bn_shared] count_literal: no registry owns {literal!r}; "
                  f"counting nukta-tolerantly with no exclusions. If it "
                  f"collides with a longer word, add it to productmatch or "
                  f"role_exclusions.", file=sys.stderr)
    occ = n_chunks = 0
    for t in texts:
        c = matcher(canon(t or ""))
        if c:
            occ += c
            n_chunks += 1
    return occ, n_chunks


def literal_owner(literal):
    """-> "programme" | "product" | "role" | None. For reports that must say
    whether a count was guarded."""
    return _resolve_owner(literal)[0]


# --------------------------------------------------- display-side primitives
# These mirror ex.py so that a PDF chunk and a docx chunk of the same passage
# normalise identically. dupscan and dedupe compare display_text and the
# `numbers` list across manuals; if the PDF path formatted either field
# differently, every cross-manual duplicate involving this manual would
# silently fail to group, and a 3-way overlap report would look clean.
#
# ex.py: nm() strips zero-width, folds ৷ onto ।, collapses spaces and tabs
# but NOT newlines, then strips. Keep it that way.

_ZW_SUB = re.compile("[" + re.escape(ZERO_WIDTH) + "]")
_SPACES = re.compile("[ \t" + NBSP + "]+")

# ex.py's NUM / UNUM, character for character.
NUM_RX = re.compile(r"[\d০-৯][\d০-৯,\.]*")
UNUM_RX = re.compile(
    r"([\d০-৯][\d০-৯,\.]*)\s*"
    r"(%|শতাংশ|টাকা|মাস|বছর|দিন|হাজার|লক্ষ|কোটি|কিস্তি)")


def display_norm(text: str) -> str:
    """ex.py's nm(): the unfolded, human-readable form stored as display_text."""
    s = _ZW_SUB.sub("", unicodedata.normalize("NFC", text or ""))
    s = s.replace(DANDA_ALT, DANDA)
    return _SPACES.sub(" ", s).strip()


def numbers_in(text: str) -> list:
    """ex.py's `numbers`: folded, no separators, no trailing full stop."""
    return [fold_digits(m.group(0)).rstrip(".").replace(",", "")
            for m in NUM_RX.finditer(text or "")]


def unit_numbers_in(text: str) -> list:
    """ex.py's `unit_numbers`: "<folded number> <unit>", single space."""
    return [f'{fold_digits(m.group(1)).replace(",", "")} {m.group(2)}'
            for m in UNUM_RX.finditer(text or "")]


# ------------------------------------------------------- orthographic checks
# Each rule is a sequence that cannot occur in well-formed Bengali. These run
# on RAW extracted text, never on canon()'d text -- the newline rule needs the
# line breaks that a normaliser would otherwise tidy away.

MALFORMED_RULES = [
    ("matra_word_initial", re.compile(rf"(?:^|[\s{DANDA}])[{MATRAS}]")),
    ("double_matra",       re.compile(rf"[{MATRAS}][{MATRAS}]")),
    ("hasant_then_matra",  re.compile(rf"{HASANT}[{MATRAS}]")),
    ("vowel_with_matra",   re.compile(rf"[{IND_VOWELS}][{MATRAS}]")),
    ("hasant_word_initial", re.compile(rf"(?:^|\s){HASANT}")),
    ("double_hasant",      re.compile(rf"{HASANT}{HASANT}")),
]

# Damage that is orthographically LEGAL and therefore invisible above.
# These are the Progoti-PDF failure: it scored 2.0/1000 "CLEAN" and was still
# damaged (ললোন for লোন, প্রপ্রোডাক্ট, পাসপপোর্ট, অনুমমোদন).
LEGAL_DAMAGE_RULES = [
    ("doubled_consonant_before_o",
     re.compile(rf"([{CONSONANTS}])\1(?=ো)")),
    ("doubled_cluster_before_o",
     re.compile(rf"([{CONSONANTS}]{HASANT}[{CONSONANTS}])\1(?=ো)")),
    ("digit_run_split_by_newline",
     re.compile(rf"[{BN_DIGITS}0-9]\n[{BN_DIGITS}0-9]")),
]


def malformed_clusters(text: str):
    """Return [(rule_name, match_text, offset)] for illegal sequences."""
    hits = []
    for name, rx in MALFORMED_RULES:
        for m in rx.finditer(text):
            hits.append((name, m.group(0), m.start()))
    return hits


def legal_damage(text: str):
    """Return [(rule_name, match_text, offset)] for legal-but-wrong shapes."""
    hits = []
    for name, rx in LEGAL_DAMAGE_RULES:
        for m in rx.finditer(text):
            hits.append((name, m.group(0), m.start()))
    return hits


def validity_rate(text: str) -> float:
    """Malformed clusters per 1000 Bengali characters."""
    bn = bengali_count(text)
    if bn == 0:
        return 0.0
    return 1000.0 * len(malformed_clusters(text)) / bn


def looks_legacy_ansi(text: str) -> bool:
    """Bijoy stores Bengali as Latin/ANSI bytes.

    High Latin density with near-zero Bengali and non-word-like letter runs
    is the signature.
    """
    letters = sum(1 for ch in text if ch.isalpha())
    if letters < 40:
        return False
    if bengali_count(text) / letters > 0.05:
        return False
    # Bijoy output has very few vowels relative to English text.
    vowels = sum(1 for ch in text.lower() if ch in "aeiou")
    latin = sum(1 for ch in text if "a" <= ch.lower() <= "z")
    return latin > 40 and (vowels / max(latin, 1)) < 0.22


# ---------------------------------------------------------------- Convention 3
# count_literal's whole purpose is that a guarded count differs from a naive
# one. If productmatch's lookbehind is ever removed, or the owner lookup stops
# resolving, the two collapse to the same number and every caller goes quiet.
# The canary is the real historical case: সিডিপি inside এনসিডিপি / এসসিডিপি,
# measured 17 Sep 2026 as 22 guarded against 180 naive over the built corpus.
# CONVENTION 2: the fixture and the probe literal come FROM productmatch,
# never typed here. A canary that types the product name it is guarding holds
# a product literal in a matching context, which is the rule 2 violation --
# and it would also stop following the registry if the name were ever
# corrected. Built at call time, so nothing runs at import.
def _canary_fixture():
    """-> (fixture_text, cdp_literal, role_literal) from the owning registries."""
    import productmatch as pm
    import role_exclusions as rex
    cdp = pm.PROGRAMME_NAMES["cdp"][0]
    ncdp = pm.PROGRAMME_NAMES["ncdp"][0]
    scdp = pm.PROGRAMME_NAMES["scdp"][0]
    role = next(iter(rex.EXCLUSIONS))
    # ncdp and scdp both END with cdp, which is the whole collision.
    text = f"{ncdp} {scdp} {cdp}"
    return text, cdp, role


def canary_selftest(verbose=True):
    """-> True if count_literal still suppresses a known collision."""
    ok = True
    try:
        fixture, cdp, role = _canary_fixture()
    except ImportError as e:
        if verbose:
            print(f"  canary cdp_collision     NOT BUILDABLE: {e}")
        return False
    guarded, _ = count_literal(cdp, [fixture])
    naive = len(canon_pattern(cdp).findall(canon(fixture)))
    hit = guarded == 1 and naive == 3
    ok &= hit
    if verbose:
        print(f"  canary cdp_collision     guarded={guarded} naive={naive} "
              f"(expect 1 vs 3)  [{'PASS' if hit else 'FAIL'}]")
    owned = (literal_owner(cdp) == "programme"
             and literal_owner(role) == "role")
    ok &= owned
    if verbose:
        print(f"  canary owner_resolution  {cdp}->{literal_owner(cdp)} "
              f"{role}->{literal_owner(role)}  [{'PASS' if owned else 'FAIL'}]")
    # an unowned probe: ASCII, so it cannot be a product literal here
    unowned = literal_owner("zzz-not-a-product") is None
    ok &= unowned
    if verbose:
        print(f"  canary unowned_is_visible  unowned probe -> "
              f"{literal_owner('zzz-not-a-product')}  "
              f"[{'PASS' if unowned else 'FAIL'}]")
    return bool(ok)


def main():
    print("bn_shared canaries")
    return 0 if canary_selftest() else 1


if __name__ == "__main__":
    sys.exit(main())
