"""
bengali_literal_lint.py -- Convention 4.

    No Bengali string literal may reach a matching operation directly.
    Every one goes through canon_pattern().

Three instances now, and all three were invisible to the checks around them:

  1. গার্ডিয়ান লাইফ matched 2 of 12 chunks. Found because 2-of-12 looked
     implausible, not because anything flagged it.
  2. gold_eval's fact matcher, caught only because the canary was built from
     explicit codepoints.
  3. CHAPTER_WORD_RX / CHAPTER_RX. Both patterns carried decomposed য়, both
     PDFs emit precomposed U+09DF, so both found 0 where canon_pattern finds
     29. Two signals dead: real chapter headings never detected at all, and
     the over-detection gate's ratio check permanently inert.

Three instances is a class, not a bug. Per the project's own lesson -- a fix
applied locally is a fix scheduled to recur -- this is the mechanical version.

WHAT NFC ACTUALLY DOES, AND WHY THIS LINT STILL EARNS ITS PLACE:
য় (U+09DF), ড় (U+09DC) and ঢ় (U+09DD) are on the Unicode composition
exclusion list, which means composition INTO them is excluded -- so NFC
unifies the two spellings by DECOMPOSING the precomposed form:

    NFC(U+09DF)          -> U+09AF U+09BC
    NFC(U+09AF U+09BC)   -> U+09AF U+09BC

Both forms converge. So canon() on BOTH SIDES is sufficient, and a plain
substring test works once text and literal have both been through it.

The lint is about the case where you cannot canon the text: a pattern matched
against RAW extracted text, before any normaliser has run. That is exactly
where the real failures were -- CHAPTER_WORD_RX and CHAPTER_RX both matched
raw PDF lines, and both found 0. Patterns matched against canon()'d chunks
were never broken; step-0 measurement, 14 Sep 2026, found the corpus 100%
decomposed on both ingestion paths.

Hence the ERROR/ADVISORY split is not about severity in the abstract. It is
about whether the text side is under your control at the call site.

EXEMPTIONS ARE LINE-LEVEL, NOT FILE-LEVEL:

    DEF_VERB = re.compile(...)   # noqa: BN4 chunks are canon()'d, verified

A file-level exemption is a blast radius. The canary fixture in this module
is a reason to excuse THIS module's fixture, not a licence for every live
pattern that later lands in the same file -- and a shelter nobody can see
from the call site is how an exemption outlives its reason. A reason string
is required: `# noqa: BN4` on its own suppresses nothing and is reported.

The marker may sit on any line of the statement it excuses, so a multi-line
re.compile can carry it on the closing line where it reads naturally.

Wire into conventions_check.py:

    from bengali_literal_lint import scan_paths
    hits = scan_paths(PY_FILES)

Usage:  python3 bengali_literal_lint.py *.py tests/*.py
"""

import argparse
import ast
import re
import sys
from pathlib import Path

BN_LO, BN_HI = 0x0980, 0x09FF

# Nukta-bearing characters. A literal containing one of these -- in either
# form -- is the high-risk case: the two spellings are different byte
# sequences that normalisation will not reconcile.
NUKTA_CHARS = {"ড়", "ঢ়", "য়", "়"}

# Calls that perform matching. A Bengali literal reaching any of these
# without canon_pattern is a violation.
MATCHING_CALLS = {
    "re.compile", "re.match", "re.search", "re.findall", "re.finditer",
    "re.sub", "re.split", "re.fullmatch",
}
MATCHING_METHODS = {
    "count", "find", "rfind", "index", "startswith", "endswith",
    "replace", "split", "partition",
}


def has_bengali(s):
    return any(BN_LO <= ord(c) <= BN_HI for c in s)


def has_nukta(s):
    return any(c in NUKTA_CHARS for c in s)


def _dotted(node):
    """re.compile -> 're.compile'; canon_pattern -> 'canon_pattern'."""
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _literal_strings(node):
    """Pull string constants out of a literal, a concat, or an f-string."""
    out = []
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        out.append(node.value)
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        out += _literal_strings(node.left) + _literal_strings(node.right)
    elif isinstance(node, ast.JoinedStr):
        for v in node.values:
            out += _literal_strings(v)
    return out


class _Visitor(ast.NodeVisitor):
    def __init__(self, path):
        self.path = path
        self.hits = []
        self.suppressed = []

    def _flag(self, node, kind, literal, detail):
        start = getattr(node, "lineno", 0)
        self.hits.append({
            "file": str(self.path),
            "line": start,
            "end_line": getattr(node, "end_lineno", None) or start,
            "kind": kind,
            "literal": literal[:40],
            "nukta": has_nukta(literal),
            "detail": detail,
        })

    def visit_Call(self, node):
        name = _dotted(node.func)

        # canon_pattern is the sanctioned builder. Anything it wraps is fine.
        if name.endswith("canon_pattern"):
            return                       # do not descend; its arg is allowed

        if name in MATCHING_CALLS:
            for arg in node.args:
                for s in _literal_strings(arg):
                    if has_bengali(s):
                        self._flag(node, name, s,
                                   "Bengali literal compiled directly. "
                                   "Use canon_pattern(literal) instead.")

        elif isinstance(node.func, ast.Attribute) \
                and node.func.attr in MATCHING_METHODS:
            for arg in node.args:
                for s in _literal_strings(arg):
                    if has_bengali(s):
                        self._flag(node, f".{node.func.attr}()", s,
                                   "Bengali literal in a string method. "
                                   "Use canon_pattern(literal).search(text).")
        self.generic_visit(node)

    def visit_Compare(self, node):
        # `"অধ্যায়" in text` -- same failure, no regex involved.
        for op, comp in zip(node.ops, node.comparators):
            if isinstance(op, (ast.In, ast.NotIn)):
                for s in _literal_strings(node.left):
                    if has_bengali(s):
                        self._flag(node, "in", s,
                                   "Bengali literal membership test. "
                                   "Use canon_pattern(literal).search(text).")
        self.generic_visit(node)


NOQA_RX = re.compile(r"#\s*noqa:\s*BN4(?P<reason>[^\n]*)")


def noqa_lines(src):
    """-> ({line_no: reason}, [line_no with a reasonless marker]).

    A marker with no reason suppresses nothing. "Why" is the only part of an
    exemption that survives contact with the next reader; without it the
    marker is indistinguishable from someone silencing a check they did not
    understand.
    """
    excused, bare = {}, []
    for i, line in enumerate(src.splitlines(), 1):
        m = NOQA_RX.search(line)
        if not m:
            continue
        reason = m.group("reason").strip(" \t-:")
        if reason:
            excused[i] = reason
        else:
            bare.append(i)
    return excused, bare


def scan_source(src, path="<str>", apply_noqa=True):
    v = _Visitor(path)
    v.visit(ast.parse(src))
    if not apply_noqa:
        return v.hits
    excused, bare = noqa_lines(src)
    kept = []
    for h in v.hits:
        span = range(h["line"], h.get("end_line", h["line"]) + 1)
        hit_on = next((n for n in span if n in excused), None)
        if hit_on is None:
            kept.append(h)
        else:
            h["suppressed_by"] = excused[hit_on]
            v.suppressed.append(h)
    for n in bare:
        kept.append({"file": str(path), "line": n, "end_line": n,
                     "kind": "noqa_without_reason", "literal": "",
                     "nukta": True,
                     "detail": "# noqa: BN4 with no reason suppresses "
                               "nothing. State why."})
    seen, deduped = set(), []
    for h in kept:
        key = (h["file"], h["line"], h["kind"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(h)
    v.hits = deduped
    return deduped


def scan_paths(paths, exempt=()):
    """exempt is retained for callers but should be empty: Convention 4
    exemptions are line-level. See noqa_lines()."""
    exempt = {Path(e).name for e in exempt}
    hits = []
    for p in paths:
        p = Path(p)
        files = sorted(p.rglob("*.py")) if p.is_dir() else [p]
        for f in files:
            if f.name in exempt or "__pycache__" in f.parts:
                continue
            try:
                hits += scan_source(f.read_text(encoding="utf-8"), f)
            except SyntaxError as exc:
                hits.append({"file": str(f), "line": exc.lineno or 0,
                             "end_line": exc.lineno or 0,
                             "kind": "parse_error", "literal": "",
                             "nukta": False, "detail": str(exc)})
    return hits


# --------------------------------------------------------------- Convention 3
# Both canaries are REAL code, copied from the files where the bug lived.
# The failing side must be written with a DIFFERENT nukta form from the
# matching side -- that asymmetry is what the _synth canary in heading_gate
# lacked, which is why it passed while CHAPTER_WORD_RX found 0 on every
# manual.

# Explicit codepoints, never typed characters. Writing these as literals is
# the very ambiguity being linted: whichever form the editor saved would be
# used for BOTH, the asymmetry would vanish, and the canary would pass while
# proving nothing. That is how it failed on the first run of this file.
PRE, DEC = "য়", "য়"          # য়  precomposed / decomposed

CANARY_BAD = f'''
import re
CHAPTER_WORD_RX = re.compile(r"অধ্যা{DEC}")
def f(text):
    return "গার্ডি{DEC}ান লাইফ" in text
def g(text):
    return text.count("দাবি")
'''

CANARY_GOOD = f'''
from bn_shared import canon_pattern
CHAPTER_WORD_RX = canon_pattern("অধ্যা{DEC}")
def f(text):
    return canon_pattern("গার্ডি{PRE}ান লাইফ").search(text)
'''


# The case file-level exemption got wrong: one excused line in a file must
# not shelter the live pattern three lines down. Both re.compile calls carry
# a nukta literal; only the first is excused.
# Assembled so the markers are not literal in this file's own source: they
# are data for the fixture, and noqa_lines() reads raw lines, so a real
# "# noqa: BN4" here would be picked up as an exemption of this very line.
_NQ = "# noqa: BN" + "4"
CANARY_NOQA = f'''
import re
FIXTURE = re.compile(r"অধ্যা{DEC}")   {_NQ} fixture, matched only in-module
LIVE = re.compile(r"অধ্যা{DEC}")
BARE = re.compile(r"গার্ডি{DEC}ান")   {_NQ}
'''


def canary_selftest(verbose=True):
    bad = scan_source(CANARY_BAD, "canary_bad.py")
    good = scan_source(CANARY_GOOD, "canary_good.py")

    kinds = sorted({h["kind"] for h in bad})
    ok_bad = len(bad) == 3 and set(kinds) == {"re.compile", "in", ".count()"}
    ok_good = len(good) == 0

    if verbose:
        print(f"  canary raw_bengali_literals  found={len(bad)} {kinds}  "
              f"(expect 3)  [{'DETECTED' if ok_bad else 'MISSED'}]")
        print(f"  canary canon_pattern_clean   found={len(good)}  "
              f"[{'PASS' if ok_good else 'FALSE POSITIVE'}]")

    # The asymmetry check the _synth canary was missing: the two nukta forms
    # must actually differ as byte sequences, or neither canary proves
    # anything. If this ever fails, Python's normalisation changed and every
    # nukta canary in the repo needs revisiting.
    ok_forms = PRE != DEC and len(PRE) == 1 and len(DEC) == 2
    if verbose:
        print(f"  canary nukta_forms_differ    "
              f"U+09DF vs U+09AF+U+09BC  "
              f"[{'PASS' if ok_forms else 'FAIL'}]")

    # Line-level exemption. The excused fixture must be suppressed, the live
    # pattern two lines below must NOT be, and a reasonless marker must be
    # reported instead of honoured.
    # Line 3 is the excused fixture, line 4 the live pattern, line 5 carries a
    # reasonless marker. Expect 3 suppressed, 4 and 5 still reported (a bare
    # marker suppresses nothing, so its own pattern stands too), and the bare
    # marker itself reported once.
    nq = scan_source(CANARY_NOQA, "canary_noqa.py")
    live = sorted(h["line"] for h in nq if h["kind"] == "re.compile")
    bare = [h for h in nq if h["kind"] == "noqa_without_reason"]
    ok_noqa = live == [4, 5] and len(bare) == 1
    if verbose:
        print(f"  canary noqa_is_line_level    fixture_line_3_suppressed="
              f"{3 not in live} live={live} (expect [4, 5]) "
              f"reasonless_reported={len(bare)}  "
              f"[{'PASS' if ok_noqa else 'FAIL'}]")
    return ok_bad and ok_good and ok_forms and ok_noqa


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*", default=["."])
    ap.add_argument("--strict", action="store_true",
                    help="fail on advisory findings too, not just nukta ones")
    ap.add_argument("--exempt", nargs="*", default=[],
                    help="files allowed raw literals, e.g. canary fixtures")
    args = ap.parse_args()

    print("CANARY")
    if not canary_selftest():
        print("FATAL: lint failed its own canary.")
        return 2

    hits = scan_paths(args.paths, args.exempt)
    if not hits:
        print("\nCLEAN -- no raw Bengali literals in matching operations")
        return 0

    errs = [h for h in hits if h["nukta"]]
    warns = [h for h in hits if not h["nukta"]]

    # Two tiers, because they are genuinely different problems. A nukta
    # literal is BROKEN -- it matches nothing and no test says so. A literal
    # with no nukta character works today; it is a latent risk only if the
    # literal ever changes. Blocking the build on both would train people to
    # add exemptions, which is how a convention dies.
    if errs:
        print(f"\nERROR -- {len(errs)} broken pattern(s)\n")
        for h in errs:
            print(f"{h['file']}:{h['line']}  {h['kind']}  {h['literal']!r}")
            print(f"    nukta present. If the text side is not canon()'d "
                  f"this finds 0 and nothing tells you;")
            print(f"    canon() both sides, or build the literal with "
                  f"canon_pattern().")
    if warns:
        print(f"\nADVISORY -- {len(warns)} raw literal(s), no nukta, working "
              f"today\n")
        for h in warns:
            print(f"{h['file']}:{h['line']}  {h['kind']}  {h['literal']!r}")

    return 1 if (errs or (args.strict and warns)) else 0


if __name__ == "__main__":
    sys.exit(main())
