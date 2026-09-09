"""Convert legacy Bijoy runs in a .docx to Unicode Bengali, span by span.

Writes a NEW file. data/raw/ is never touched.

WHY SPANS, NOT RUNS
-------------------
Word splits one Bijoy word across several runs. Converting run by run cuts
words apart -- a run holding just "vwe" converts to "াবি", a leading া that
cannot begin a word. Measured that way the malformed-cluster rate was 45 per
1000, 15x tolerance. So conversion joins *consecutive* legacy runs into a
span, converts the span, and writes the result back into the span's first run.
A Unicode run always breaks a span, so Unicode text is never fed to the
Bijoy map -- the original "never convert whole paragraphs" rule, kept.

WHAT IS CONVERTED
-----------------
Only font-evidenced runs (direct or style-inherited legacy font). Score-only
runs are held back and printed: score() gives 0.45 to any mostly-latin run
and one accidental marker adds 0.06, so ordinary English clears 0.50 on "iv"
inside "Activity". All 45 in this document are English.

Runs already holding Unicode Bengali are excluded even when their font says
SutonnyMJ. Style-inherited font evidence is ~95% false positive (591 of 623
such runs are ordinary Unicode Bengali under a legacy-named style), so this
guard is what makes that signal usable at all.

Spans of pure ASCII letters with no Bijoy marker are genuinely ambiguous:
"welq" is বিষয় but "E-Approval" is English. Those are hand-reviewed via
AMBIGUOUS_DENY below, and every such decision is printed.

CONVERTER CHOICE
----------------
Primary is `unicodeconverter`, after diffing against `bijoy2unicode`:
bijoy2unicode misplaces reph (Kg©x -> কমীর্ rather than কর্মী) and raises
IndexError on some ya-phala runs. unicodeconverter has its own known defect,
ya-phala after a long vowel (b~¨bZg -> নূ্যনতম, should be ন্যূনতম), which
the orthographic check is calibrated to surface.

Usage:
    python bijoy_convert.py            # dry run: report + verify, write nothing
    python bijoy_convert.py --write    # convert and save
"""
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

from bijoy_scan import (all_paras, ratios, score, effective_fonts,
                        is_legacy_font, MARKERS)

DEFAULT_SRC = Path("data/raw/progoti_manual_2025.docx")
OUT_DIR = Path("data/processed")
UNICODE_FONT = "Nirmala UI"
BENGALI_WORD = re.compile(r"[ঀ-৿]+")
ALREADY_UNICODE_RATIO = 0.15

# Hand-reviewed per document: ASCII-letter spans that are English, not Bijoy.
# Converting them produces garbage ('E-Approval' -> 'ঊ-অঢ়ঢ়ৎড়াধষ'). Every
# ASCII-letter span is printed under "converted after review" so a new
# document's list can be checked before --write.
AMBIGUOUS_DENY = {"E-Approval", "(R&I)"}

nfc = lambda s: unicodedata.normalize("NFC", s or "")

# ---------------------------------------------------------------------------
# Post-conversion cluster repair.
#
# unicodeconverter emits a dependent vowel sign BEFORE a following hasant
# cluster instead of after it. In Bengali a hasant must sit between two
# consonants -- it can never follow or precede a vowel sign -- so both shapes
# below are unconditionally malformed and their repair is deterministic:
#
#   A   প + ে + ্র   ->  প + ্র + ে      (পে্রাডাক্টস -> প্রোডাক্টস)
#                                          (নূ্যনতম    -> ন্যূনতম)
#   B   ম + র + ্ + ী -> র + ্ + ম + ী    (কমর্ী       -> কর্মী)  stranded reph
#
# NFC then composes ে+া -> ো and ে+ৗ -> ৌ.
#
# Verified no-op on 32 hand-checked well-formed words and on all 431 chunks of
# the existing already-Unicode corpus. Rule A alone takes the malformed rate
# from 11.78 to 3.37 per 1000; A+B reach 1.68.
# ---------------------------------------------------------------------------
_CONS = "ক-হড়-য়"
_VS = "া-ৌ"
_HAS = "্"
_RULE_A = re.compile(f"([{_CONS}])([{_VS}])({_HAS})([{_CONS}])")
_RULE_B = re.compile(f"([{_CONS}])(র)({_HAS})([{_VS}])")


def repair_clusters(s):
    prev, out = None, s
    while out != prev:                     # chains need more than one pass
        prev = out
        out = _RULE_A.sub(r"\1\3\4\2", out)
        out = _RULE_B.sub(r"\2\3\1\4", out)
    return nfc(out)
has_highbit = lambda t: any(0x80 <= ord(c) <= 0xFF or 0x2018 <= ord(c) <= 0x2030
                            for c in t)
has_marker = lambda t: any(m in t for m in MARKERS)
has_letter = lambda t: any(c.isascii() and c.isalpha() for c in t)
has_digit = lambda t: any(c.isdigit() and c.isascii() for c in t)

CONVERT, DENY, SKIP_PUNCT, SKIP_UNICODE = "convert", "deny", "skip_punct", "skip_unicode"


def classify_run(run, para):
    """'legacy' | 'unicode_legacyfont' | 'unicode_nofont' | 'blank'"""
    t = run.text
    if not t.strip():
        return "blank"
    direct, inherited = effective_fonts(run, para)
    df = next((f for f in direct if is_legacy_font(f)), None)
    inf = next((f for f in inherited if is_legacy_font(f)), None)
    if not df and not inf:
        return "unicode_nofont"
    bn, _, _ = ratios(t)
    return "unicode_legacyfont" if bn > ALREADY_UNICODE_RATIO else "legacy"


def gate(text):
    """Decide what to do with a whole span."""
    bn, _, _ = ratios(text)
    if bn > ALREADY_UNICODE_RATIO:
        return SKIP_UNICODE
    if has_highbit(text) or has_marker(text):
        return CONVERT
    if not has_letter(text):
        # digits in SutonnyMJ render as Bengali digits, so 1.1.1 -> ১.১.১ is
        # what the reader already sees. Bare punctuation converts to itself.
        return CONVERT if has_digit(text) else SKIP_PUNCT
    return DENY if text.strip() in AMBIGUOUS_DENY else CONVERT


def build_spans(doc):
    """Maximal groups of consecutive legacy runs, plus held-back score-only runs."""
    spans, held = [], []
    for p, where in all_paras(doc):
        cur = []
        for run in p.runs:
            kind = classify_run(run, p)
            if kind == "legacy":
                cur.append(run)
            elif kind == "blank" and cur:
                cur.append(run)            # whitespace inside a span
            else:
                if cur:
                    spans.append((where, cur))
                cur = []
                if kind != "blank" and score(run.text) >= 0.5:
                    held.append((where, p, run))
        if cur:
            spans.append((where, cur))
    # trim trailing whitespace-only runs off each span
    out = []
    for where, runs in spans:
        while runs and not runs[-1].text.strip():
            runs = runs[:-1]
        if runs:
            out.append((where, runs))
    return out, held


def print_held(held):
    print("=" * 78)
    print(f"HELD BACK -- {len(held)} score-only runs, NOT converted")
    print("=" * 78)
    print("  No legacy font anywhere; matched on glyph score alone. score()")
    print("  gives 0.45 to any mostly-latin run and one marker hit adds 0.06,")
    print("  so ordinary English clears 0.50. Review by hand.\n")
    seen = Counter(r.text.strip() for _, _, r in held)
    for i, (t, n) in enumerate(seen.most_common(), 1):
        print(f"  {i:>3}. x{n:<3} {t[:96]!r}")
    print(f"\n  {len(seen)} distinct strings across {len(held)} runs.")


def orthographic_check(pairs, label):
    from bnunicodenormalizer import Normalizer
    norm = Normalizer()
    total = bad = 0
    examples = []
    for src, out in pairs:
        for w in BENGALI_WORD.findall(out):
            total += 1
            try:
                r = norm(w)
            except Exception:
                continue
            fixed = r.get("normalized")
            if fixed is not None and nfc(fixed) != nfc(w):
                bad += 1
                if len(examples) < 10:
                    examples.append((src.strip()[:30], w, fixed))
    rate = 1000 * bad / max(total, 1)
    print("\n" + "=" * 78)
    print(f"CHECK 1 -- ORTHOGRAPHIC VALIDITY ({label})")
    print("=" * 78)
    print(f"  Bengali words checked : {total}")
    print(f"  malformed clusters    : {bad}")
    print(f"  rate                  : {rate:.2f} per 1000   "
          f"[{'PASS' if rate < 3 else 'FAIL'}, threshold 3]")
    for s, w, f in examples:
        print(f"    {s!r:<32} {w!r} -> normaliser wanted {f!r}")
    return rate


def differ_check(pairs):
    from bijoy2unicode import converter as b_mod
    inst = b_mod.Unicode()
    agree = errored = 0
    reph_dis, plain_dis = [], []
    for src, out in pairs:
        try:
            other = inst.convertBijoyToUnicode(src)
        except Exception:
            errored += 1
            continue
        if nfc(other) == nfc(out):
            agree += 1
        elif "©" in src:
            reph_dis.append((src, out, other))
        else:
            plain_dis.append((src, out, other))
    n = len(pairs)
    print("\n" + "=" * 78)
    print("CHECK 2 -- SECOND CONVERTER DIFF (bijoy2unicode vs unicodeconverter)")
    print("=" * 78)
    print(f"  spans compared : {n}")
    print(f"  agree          : {agree} ({100 * agree / max(n, 1):.1f}%)")
    print(f"  disagree       : {len(reph_dis) + len(plain_dis)}  "
          f"({len(reph_dis)} contain reph ©, {len(plain_dis)} do not)")
    print(f"  b2u errored    : {errored}")
    print("\n  bijoy2unicode misplaces reph, so reph disagreements are expected")
    print("  and favour unicodeconverter. Non-reph disagreements localise real")
    print("  glyph-map gaps:")
    for src, a, b in plain_dis[:10]:
        print(f"    {src.strip()[:30]!r}")
        print(f"       uc ={a.strip()[:44]}")
        print(f"       b2u={b.strip()[:44]}")
    if not plain_dis:
        print("    (none -- every disagreement involves a reph)")
    return agree, reph_dis, plain_dis


def show_samples(pairs):
    targets = [
        ("__LONGEST__", "longest converted span (most orthography in one place)"),
        ("ক্ষ", "conjunct ক্ষ"), ("ঞ্জ", "conjunct ঞ্জ"),
        ("ন্ত্র", "three-part conjunct ন্ত্র"), ("স্থ", "conjunct স্থ"),
        ("র্", "reph placement"), ("ো", "o-kar ো"), ("ৌ", "au-kar ৌ"),
        ("্য", "ya-phala ্য"), ("্র", "ra-phala ্র"),
        ("ষ্ঠ", "conjunct ষ্ঠ"), ("ঙ্গ", "conjunct ঙ্গ"),
        ("দ্ধ", "conjunct দ্ধ"), ("ূ", "long ূ"), ("ৃ", "ri-kar ৃ"),
    ]
    print("\n" + "=" * 78)
    print("CHECK 3 -- 15 CONVERTED SPANS COVERING THE HARD CASES")
    print("=" * 78)
    used = set()
    for needle, note in targets:
        pick = None
        cands = sorted(pairs, key=lambda x: len(x[1]))
        if needle == "__LONGEST__":
            pick = max(pairs, key=lambda x: len(x[1])) if pairs else None
            if pick:
                used.add(pick)
                print(f"\n  [{note}]")
                print(f"    bijoy   : {pick[0].strip()[:72]!r}")
                print(f"    unicode : {pick[1].strip()[:72]}")
            continue
        for src, out in cands:
            if (src, out) in used:
                continue
            elif needle in out and 6 <= len(out.strip()) <= 70:
                pick = (src, out)
                break
        print(f"\n  [{note}]")
        if not pick:
            print(f"    -- no span found containing {needle!r}")
            continue
        used.add(pick)
        print(f"    bijoy   : {pick[0].strip()[:72]!r}")
        print(f"    unicode : {pick[1].strip()[:72]}")


def main(write, src=DEFAULT_SRC):
    src = Path(src)
    dst = OUT_DIR / f"{src.stem}_unicode.docx"
    doc = Document(src)
    spans, held = build_spans(doc)

    decided = Counter()
    todo = []
    denied, punct, uni = [], [], []
    ambiguous = []
    for where, runs in spans:
        text = "".join(r.text for r in runs)
        d = gate(text)
        decided[d] += 1
        if d == CONVERT:
            todo.append((where, runs, text))
            if has_letter(text) and not has_highbit(text) and not has_marker(text):
                ambiguous.append(text)
        elif d == DENY:
            denied.append(text)
        elif d == SKIP_PUNCT:
            punct.append(text)
        else:
            uni.append(text)

    print(f"source : {src}")
    print(f"target : {dst}")
    print(f"spans  : {len(spans)}   decisions: {dict(decided)}\n")
    print_held(held)

    print("\n" + "=" * 78)
    print("SPAN GATE DECISIONS")
    print("=" * 78)
    print(f"  convert            : {len(todo)}")
    print(f"  skip (punctuation) : {len(punct)}   e.g. "
          f"{sorted(set(p.strip() for p in punct))[:10]}")
    print(f"  skip (already Bengali): {len(uni)}")
    print(f"  DENIED as English  : {len(denied)}  {[d.strip() for d in denied]}")
    if ambiguous:
        print(f"\n  ASCII-letter spans converted after review ({len(ambiguous)}):")
        for t in sorted(set(ambiguous)):
            print(f"    {t.strip()[:60]!r}")

    from unicodeconverter import convert_bijoy_to_unicode as u_conv
    pairs, failed, repaired = [], [], []
    for where, runs, text in todo:
        try:
            raw = nfc(u_conv(text))
        except Exception as e:
            failed.append((text, f"{type(e).__name__}: {e}"))
            continue
        out = repair_clusters(raw)
        if out != raw:
            repaired.append((text, raw, out))
        pairs.append((text, out))
        runs[0]._converted = out
        for r in runs[1:]:
            r._converted = ""

    print(f"\nconverted {len(pairs)} spans, {len(failed)} errors")
    print(f"cluster repair rewrote {len(repaired)} spans:")
    for bijoy, raw, out in repaired[:8]:      # not `src` -- shadows the param
        print(f"    {bijoy.strip()[:30]!r}")
        print(f"       before repair: {raw.strip()[:52]}")
        print(f"       after  repair: {out.strip()[:52]}")
    for t, e in failed[:6]:
        print(f"    {t.strip()[:52]!r} -> {e}")

    orthographic_check(pairs, "converted spans only")
    differ_check(pairs)
    show_samples(pairs)

    if not write:
        print("\n" + "=" * 78)
        print("DRY RUN -- nothing written. Re-run with --write to save.")
        print("=" * 78)
        return

    n = 0
    for where, runs, text in todo:
        for r in runs:
            if hasattr(r, "_converted"):
                r.text = r._converted
                if r._converted:
                    set_font(r, UNICODE_FONT)
                n += 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    doc.save(dst)
    print("\n" + "=" * 78)
    print(f"WROTE {dst}")
    print(f"  {len(pairs)} spans ({n} runs) converted, font -> {UNICODE_FONT}")
    print(f"  {len(held)} score-only runs left as-is pending review")
    print(f"  {len(denied)} English spans denied")
    print(f"  {src} untouched")
    print("=" * 78)


def set_font(run, name):
    rPr = run._r.get_or_add_rPr()
    rf = rPr.find(qn("w:rFonts"))
    if rf is None:
        rf = rPr.makeelement(qn("w:rFonts"), {})
        rPr.insert(0, rf)
    for a in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rf.set(qn(a), name)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main("--write" in sys.argv, args[0] if args else DEFAULT_SRC)
