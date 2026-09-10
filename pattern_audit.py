"""Audit every hard-coded Bengali pattern for silent under-matching.

THE FAILURE THIS HUNTS
য় ড় ঢ় are Unicode composition exclusions: NFC decomposes them to base +
nukta. A pattern literal saved in its precomposed form therefore cannot match
NFC-normalised corpus text, and nothing throws -- the row just reports fewer
hits than it should. গার্ডিয়ান লাইফ matched 2 of 12 chunks this way, and it
was noticed only because 2 looked wrong by eye. Any row whose true count is
not obviously wrong would have gone unnoticed indefinitely.

THREE COUNTS PER ROW
  raw     the pattern exactly as written in source, vs canon'd text
  canon   canon_pattern(pattern) vs canon'd text
  naive   canon(bare name) as a plain substring, vs canon'd text

canon_pattern, not canon: canon's separator stripping deletes the comma in a
regex quantifier, so \\S{0,4} becomes \\S{04} and গার্ডিয়ান লাইফ loses all 12
matches -- the same silent under-match, reintroduced by over-normalising.
This audit found that while checking the fix for the original bug.

  canon > raw  -> THE BUG. The literal was non-canonical. Always a defect.
  raw > canon  -> normalising the pattern BROKE it. Also always a defect.
  naive > canon -> the pattern is narrower than its own name. Sometimes
                   deliberate (গতি লোন excludes প্রগতি লোন; দাবি excludes
                   বিমাদাবি), sometimes a defect. Reported for judgement,
                   with the deliberate cases annotated.

Usage:  python pattern_audit.py
"""
import json
import re
import sys
import unicodedata
from pathlib import Path

from bnnorm import canon, canon_pattern
from products import ENTITIES, NAMED, PROGRAMMES
from retag import PROD as RETAG_PROD
from roles import ROLES

P = Path("data/processed")
CORPORA = ("progoti", "dabi_2025")

# Rows whose pattern is deliberately narrower than the bare name, with why.
INTENTIONALLY_NARROW = {
    "dabi": "excludes বিমাদাবি / বীমাদাবি / মৃত্যুদাবি compounds",
    "goti_loan": "excludes the tail of প্রগতি লোন",
    "goti": "excludes প্রগতি; requires a product word to follow",
    "cdp": "excludes the tails of এনসিডিপি / এসসিডিপি",
    "refinance_loan": "requires লোন; bare রিফাইন্যান্সিং is the process",
    "rescheduled_loan": "requires লোন; bare রিশিডিউলিং is the process",
}

chunks = []
for tag in CORPORA:
    for l in (P / f"chunks_{tag}_final.jsonl").open(encoding="utf-8"):
        c = json.loads(l)
        c["_norm"] = re.sub(r"\s+", " ", canon(c["display_text"]))
        chunks.append(c)


def hits(pattern):
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return None, f"regex error: {e}"
    return sum(1 for c in chunks if rx.search(c["_norm"])), None


def substr_hits(text):
    return sum(1 for c in chunks if text in c["_norm"])


def nukta_risk(s):
    """Precomposed chars that NFC would decompose."""
    return sorted({ch for ch in s if unicodedata.normalize("NFC", ch) != ch})


NUKTA = "়"
NUKTA_BASE = "যড঵ঢ"          # bases that take ় in Bengali


def nukta_tolerant(pat):
    """Allow an optional ় after every nukta-taking base, outside classes.

    This is the test that actually catches the গার্ডিয়ান failure. That
    pattern was NOT stored precomposed -- every literal here is already NFC.
    It broke because the author wrote it as if য় were one character, while
    decomposed text spells it য + ়. So the probe is not "is the literal
    precomposed" but "does the pattern tolerate the nukta that decomposition
    introduces".

    Skips character classes: inserting into [নণ] would corrupt the class.
    """
    out, i = [], 0
    cls_has_base = False
    in_class = False
    while i < len(pat):
        ch = pat[i]
        out.append(ch)
        if ch == "\\" and i + 1 < len(pat):
            out.append(pat[i + 1])
            i += 2
            continue
        if ch == "[":
            in_class, cls_has_base = True, False
        elif ch == "]" and in_class:
            in_class = False
            i += 1
            # copy any quantifier before injecting, so [য]? stays optional
            while i < len(pat) and (pat[i] in "?*+"
                                    or (pat[i] == "{" and "}" in pat[i:])):
                if pat[i] == "{":
                    j = pat.index("}", i)
                    out.append(pat[i:j + 1])
                    i = j + 1
                else:
                    out.append(pat[i])
                    i += 1
            # [য]? never matches the ় that decomposition leaves behind --
            # this is exactly how গার্ডিয়ান লাইফ lost 10 of its 12 chunks
            if cls_has_base and pat[i:i + 1] != NUKTA:
                out.append(NUKTA + "?")
            continue
        elif in_class:
            if ch in NUKTA_BASE:
                cls_has_base = True
        elif ch in NUKTA_BASE and pat[i + 1:i + 2] != NUKTA:
            out.append(NUKTA + "?")
        i += 1
    return "".join(out)


# The pattern as it was when the bug was live. The audit must flag it; if it
# does not, the audit is not protecting anything.
CANARY = (r"গার্ড[িী]?[য]?ান\s*লাইফ", "guardian_life as originally written")


rows = []
for key, name, _ in PROGRAMMES:
    rows.append(("programme", key, name, dict(RETAG_PROD)[key].pattern))
for key, name, _, pat in NAMED:
    rows.append(("product", key, name, pat))
for key, etype, name, _, pat in ENTITIES:
    rows.append((etype, key, name, pat))
for key, nd, npg, *_ in ROLES:
    rows.append(("role", key + " [dabi]", nd, re.escape(nd)))
    if npg != nd:
        rows.append(("role", key + " [progoti]", npg, re.escape(npg)))

print("=" * 92)
print("PATTERN AUDIT -- silent under-matching from non-canonical literals")
print(f"{len(rows)} patterns over {len(chunks)} chunks")
print("=" * 92)
print(f"  {'key':<26}{'raw':>5}{'canon':>7}{'nukta':>7}{'naive':>7}  verdict")

broken, narrow, nukta_blind, clean = [], [], [], 0
for kind, key, name, pat in rows:
    raw, err = hits(pat)
    cpat = canon_pattern(pat)
    can, _ = hits(cpat)
    # a lookbehind must stay fixed-width, so ়? cannot always be injected;
    # when the tolerant variant will not compile, fall back to no-change
    nuk, nuk_err = hits(nukta_tolerant(cpat))
    if nuk is None:
        nuk = can
    naive = substr_hits(canon(name))
    if err:
        print(f"  {key:<26}{'ERR':>5}{'':>7}{'':>7}{'':>7}  {err}")
        continue
    verdict = ""
    if nuk_err:
        verdict = "nukta probe skipped (lookbehind width)"
        clean += 1
    elif nuk > can:
        verdict = f"NUKTA-BLIND  tolerating ় finds {nuk - can} more"
        nukta_blind.append((kind, key, name, pat, can, nuk, naive))
    elif can > raw:
        verdict = f"BROKEN  canon_pattern finds {can - raw} more"
        broken.append((kind, key, name, pat, raw, can, naive))
    elif raw > can:
        verdict = f"BROKEN  normalising the pattern LOSES {raw - can}"
        broken.append((kind, key, name, pat, raw, can, naive))
    elif naive > can and key.split(" ")[0] not in INTENTIONALLY_NARROW:
        verdict = f"narrower than its name by {naive - can}"
        narrow.append((kind, key, name, pat, raw, can, naive))
    elif naive > can:
        verdict = f"narrow by design ({INTENTIONALLY_NARROW[key.split(' ')[0]]})"
        clean += 1
    else:
        clean += 1
    print(f"  {key:<26}{raw:>5}{can:>7}{nuk:>7}{naive:>7}  {verdict}")

print()
print("=" * 92)
print(f"NUKTA-BLIND -- pattern does not tolerate decomposed য়/ড়/ঢ়: "
      f"{len(nukta_blind)}")
print("=" * 92)
for kind, key, name, pat, can, nuk, naive in nukta_blind:
    print(f"\n  {key}  ({kind})   {can} -> {nuk} chunks")
    print(f"    name    : {name}")
    print(f"    pattern : {pat}")
    print(f"    tolerant: {nukta_tolerant(canon_pattern(pat))}")

print()
print("=" * 92)
print(f"BROKEN -- non-canonical literal, silently under-matching: {len(broken)}")
print("=" * 92)
for kind, key, name, pat, raw, can, naive in broken:
    print(f"\n  {key}  ({kind})   {raw} -> {can} chunks")
    print(f"    name    : {name}")
    print(f"    pattern : {pat}")
    risk = nukta_risk(pat)
    print(f"    precomposed chars in pattern: {risk}  "
          f"-> {[unicodedata.normalize('NFC', ch) for ch in risk]}")

print()
print("=" * 92)
print(f"NARROWER THAN NAME, not explained as deliberate: {len(narrow)}")
print("=" * 92)
for kind, key, name, pat, raw, can, naive in narrow:
    print(f"  {key:<26} canon={can} naive={naive}  name={name}")
    print(f"    pattern: {pat}")
if not narrow:
    print("  none")

print()
print(f"clean: {clean} of {len(rows)}")
print()
print("=" * 92)
print("PRECOMPOSED CHARACTERS ANYWHERE IN THE SOURCE LITERALS")
print("=" * 92)
any_risk = False
for kind, key, name, pat in rows:
    r = nukta_risk(pat) + nukta_risk(name)
    if r:
        any_risk = True
        status = ("handled by canon_pattern()"
                  if hits(canon_pattern(pat))[0] == hits(pat)[0]
                  else "STILL DIFFERS")
        print(f"  {key:<26} {sorted(set(r))}  {status}")
if not any_risk:
    print("  none -- every literal in this file is already NFC")
print()
print("=" * 92)
print("SELF-CHECK -- can this audit still detect the bug it was written for?")
print("=" * 92)
cpat, why = CANARY
c_plain, _ = hits(canon_pattern(cpat))
c_tol, _ = hits(nukta_tolerant(canon_pattern(cpat)))
detected = c_tol > c_plain
print(f"  canary   : {why}")
print(f"  pattern  : {cpat}")
print(f"  tolerant : {nukta_tolerant(canon_pattern(cpat))}")
print(f"  hits     : {c_plain} plain vs {c_tol} nukta-tolerant")
print(f"  {'DETECTED' if detected else 'MISSED -- the audit is not protecting anything'}")

print()
print(f"nukta-blind {len(nukta_blind)} | broken {len(broken)} | "
      f"unexplained-narrow {len(narrow)} | clean {clean} of {len(rows)}")
sys.exit(1 if broken or narrow or nukta_blind or not detected else 0)
