"""probe_words.py -- POSITIVE control for a text layer.

Every check in the existing ladder is a NEGATIVE control: it looks for the
absence of bad shapes. All six of them passed on data they could not see.

This one asserts the PRESENCE of words that must be there. দাবি extracting as
দাকি on every page is orthographically legal, passes bucket_test, passes
extract_check's validity metric, and fails this check in four seconds.

Convention 2: product and role literals are NOT stored here. They come from
productmatch / roles at runtime. probes.csv holds domain and structural
vocabulary only.

Convention 3: canary_selftest() runs a recorded corruption through this check
before any real scanning. If it does not report CORRUPT, main() exits 2.

TWO PANELS, AND ONLY ONE OF THEM GATES
--------------------------------------
  probes.csv          GATES. Curated per manual, with a real min_count for
                      each term, calibrated against the rendered pages.
  registry vocabulary INFORMATIONAL. Every product/role literal the repo
                      knows about, counted and printed, gating nothing.

The split is not cosmetic. A blanket "every known product literal must appear
at least once" rule scores a *clean* livestock manual as CORRUPT, because it
does not mention প্রত্যাশা লোন or মাইগ্রেশন লোন and has no reason to. Coverage
would collapse for a vocabulary reason and read exactly like a CMap fault.
A check that cries corruption on good input gets switched off, and then it is
not protecting anything.

So the registry panel answers a different and genuinely useful question --
"which names the repo knows about does this manual actually use, and are any
of them arriving damaged" -- and it feeds reconcile.py. It must not be able
to fail the build on its own.
"""

import argparse
import csv
import re
import sys
from pathlib import Path

from bn_shared import canon, canon_pattern

# Transcribed from the Dabi 2026 PDF extraction, 8 Sep 2026: a ToUnicode CMap
# fault that substituted legal Bengali for legal Bengali. Every word here is
# valid Bengali, which is exactly why the validity metrics missed it.
#
# PROVENANCE CAVEAT, stated rather than papered over: these two strings were
# transcribed from a mojibaked copy of the record, not read back from the
# original extraction. The substitutions are the recorded ones (দাবি->দাকি,
# গ্রাহক->গ্রাহৃ, কর্মসূচি->কম্মসূচি, প্রোডাক্টস->পৃপ্রাডাক্টস, সাধারণ->সাধাৃণ,
# দ্বিতীয়->রিতীয়) and the pair reproduces the recorded scores exactly --
# 0.40 coverage CORRUPT against 1.00 CLEAN, see canary_selftest. That match is
# the evidence they behave like the original. Re-record them from the real
# Dabi 2026 extraction when it is to hand, and delete this paragraph.
CANARY_CORRUPT = (
    "ঊর্ধ্বতন পরিচালকৃি বাণী। প্রথম অধ্যায়: পৃপ্রাডাক্টস। "
    "দাকি ও কম্মসূচি। দাকি, রঙ্গি, কিংসইএউপ, সিডিপি'ৃ। "
    "সাধাৃণ সঞ্চয়। সঞ্চয় ফৃিত সৃবা। রিতীয় অধ্যায় : গ্রাহৃ সৃবা।"
)
CANARY_CLEAN = (
    "ঊর্ধ্বতন পরিচালকের বাণী। প্রথম অধ্যায়: প্রোডাক্টস। "
    "দাবি ও কর্মসূচি। দাবি, প্রগতি, বিসিইউপি, সিডিপি'র। "
    "সাধারণ সঞ্চয়। সঞ্চয় ফেরত সেবা। দ্বিতীয় অধ্যায় : গ্রাহক সেবা।"
)
CANARY_PROBES = [
    ("দাবি", 1, "product"), ("কর্মসূচি", 1, "domain"), ("সঞ্চয়", 2, "domain"),
    ("গ্রাহক", 1, "domain"), ("অধ্যায়", 2, "structural"),
]


def load_probes(csv_path):
    """Read probes.csv -> [(term, min_count, category)]. These gate."""
    rows = []
    with open(csv_path, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            term = (r.get("term") or "").strip()
            if not term or term.startswith("#"):
                continue
            rows.append((term, int(r.get("min_count") or 1),
                         (r.get("category") or "domain").strip()))
    return rows


BN_LETTER = "[ঀ-৿]"


def registry_vocabulary():
    """The repo's product and role vocabulary -> [(label, category, regex)].

    Convention 2: these come from the modules that own them, never hardcoded
    here. Convention 2 has been broken four times; a copy of the list in this
    file is how it happens a fifth.

    AND THE PATTERNS COME FROM THERE TOO, which is the part the first version
    of this function got wrong. It probed productmatch's display NAMES with a
    plain literal matcher, so bare দাবি matched inside বিমাদাবি and this
    panel reported দাবি 51 times in a manual that names the Dabi programme
    twice. That is CONVENTIONS.md section 2, defect #1, reintroduced
    verbatim -- by the module whose docstring says not to.

    productmatch.PROGRAMMES is already compiled with the lookbehinds that
    exclude বিমাদাবি / মৃত্যুদাবি; NAMED and ENTITIES carry pattern source
    that productmatch.compile_pat turns into the same thing. Use those.

    roles.py owns no patterns, only names, so role names are wrapped in a
    not-a-Bengali-letter guard on both sides. Without the right-hand guard
    পিএ matches inside পিএলসি (7 false hits in this manual). The cost is
    that inflected forms -- সিডিওকে, সিডিওর -- are not counted, which is
    acceptable for a presence panel and is noted in the output.

    Raises on a broken registry API rather than returning a short list. The
    pasted draft wrapped the whole body in `except Exception: pass`, turning
    a renamed function into a silent 0% panel -- the precise shape of the
    five failures in CONVENTIONS.md section 3.
    """
    import productmatch                                       # type: ignore
    import roles                                              # type: ignore

    out = []
    # PROGRAMMES: [(key, compiled)] -- already correct, lookbehinds included.
    names = productmatch.PROGRAMME_NAMES
    for key, rx in productmatch.PROGRAMMES:
        label = names.get(key, (key,))[0]
        out.append((label, "programme", rx))
    # NAMED: (key, name_bn, aliases, pattern)
    for _key, name_bn, _aliases, pat in productmatch.NAMED:
        out.append((name_bn, "product", productmatch.compile_pat(pat)))
    # ENTITIES: (key, entity_type, name_bn, aliases, pattern)
    for _key, etype, name_bn, _aliases, pat in productmatch.ENTITIES:
        out.append((name_bn, etype, productmatch.compile_pat(pat)))
    # ROLES: (role_key, name_dabi, name_progoti, abbrev, english, note)
    for _key, nd, npg, abbrev, _eng, _note in roles.ROLES:
        for term in (nd, npg, abbrev):
            term = (term or "").strip()
            if term:
                inner = canon_pattern(term).pattern
                out.append((term, "role", re.compile(
                    f"(?<!{BN_LETTER})(?:{inner})(?!{BN_LETTER})")))

    seen, uniq = set(), []
    for label, cat, rx in out:
        if label not in seen:
            seen.add(label)
            uniq.append((label, cat, rx))
    return uniq


def score_text(text, probes):
    """Count each gating probe. coverage = share meeting their min_count."""
    t = canon(text)
    counts, misses = {}, []
    for term, min_count, category in probes:
        n = len(canon_pattern(term).findall(t))
        counts[term] = n
        if n < min_count:
            misses.append({"term": term, "found": n,
                           "required": min_count, "category": category})
    met = len(probes) - len(misses)
    return {
        "probes": len(probes),
        "met": met,
        "coverage": (met / len(probes)) if probes else 0.0,
        "counts": counts,
        "misses": misses,
    }


def scan_vocabulary(text, vocabulary):
    """Count registry vocabulary. Informational; gates nothing.

    Takes [(label, category, regex)] from registry_vocabulary() and matches
    with those regexes -- never with a pattern built here. Returns
    {"present": [(label, cat, n)], "absent": [(label, cat)]}. An absent
    label is not a fault: this manual may simply not offer that product.
    """
    t = canon(text)
    present, absent = [], []
    for label, cat, rx in vocabulary:
        n = len(rx.findall(t))
        (present if n else absent).append(
            (label, cat, n) if n else (label, cat))
    return {"present": present, "absent": absent}


def verdict(result, floor=0.90):
    """CLEAN / SUSPECT / CORRUPT from gating probe coverage."""
    c = result["coverage"]
    if c >= floor:
        return "CLEAN"
    if c >= 0.60:
        return "SUSPECT"
    return "CORRUPT"


def canary_selftest(verbose=True):
    """Convention 3. Prove this check can fail, before it scans anything.

    The recorded corruption must score 0.40 -> CORRUPT and the same sentences
    spelled correctly must score 1.00 -> CLEAN. Both numbers are asserted,
    not just the verdicts: a check that called everything CORRUPT would pass
    a verdict-only canary while being useless.
    """
    bad = score_text(CANARY_CORRUPT, CANARY_PROBES)
    good = score_text(CANARY_CLEAN, CANARY_PROBES)
    ok = (verdict(bad) == "CORRUPT" and abs(bad["coverage"] - 0.40) < 1e-9
          and verdict(good) == "CLEAN" and good["coverage"] == 1.0)
    if verbose:
        print(f"  canary corrupt -> {verdict(bad)} "
              f"(coverage {bad['coverage']:.2f}, expected 0.40) "
              f"[{'DETECTED' if verdict(bad) == 'CORRUPT' else 'MISSED'}]")
        print(f"  canary clean   -> {verdict(good)} "
              f"(coverage {good['coverage']:.2f}, expected 1.00) "
              f"[{'PASS' if verdict(good) == 'CLEAN' else 'FALSE POSITIVE'}]")
        if not ok:
            print("  the corrupt/clean pair no longer reproduces the recorded "
                  "0.40 / 1.00 split")
    return ok


def main():
    ap = argparse.ArgumentParser(description="probe a text file for words "
                                             "that must be present")
    ap.add_argument("text_file", nargs="?",
                    help="UTF-8 text dump of an extraction; "
                         "omit to self-test only")
    ap.add_argument("--probes", default="probes_livestock_grow.csv")
    ap.add_argument("--floor", type=float, default=0.90)
    ap.add_argument("--no-vocab-panel", action="store_true",
                    help="skip the informational product/role vocabulary scan")
    args = ap.parse_args()

    print("CANARY")
    if not canary_selftest():
        print("FATAL: probe check failed its own canary. Do not trust it.")
        return 2

    if not args.text_file:
        print("\nself-test only; no text file given")
        return 0

    probes = load_probes(args.probes)
    text = Path(args.text_file).read_text(encoding="utf-8", errors="replace")
    res = score_text(text, probes)
    v = verdict(res, args.floor)

    print(f"\nGATING PROBES ({args.probes})")
    print(f"  coverage .... {res['met']}/{res['probes']} = "
          f"{res['coverage']:.1%}")
    print(f"  VERDICT ..... {v}")
    if res["misses"]:
        print("\n  missing / under-count (each one is a candidate corruption):")
        for m in sorted(res["misses"], key=lambda x: x["found"]):
            print(f"    {m['found']:>5} / {m['required']:<5} "
                  f"[{m['category']:<10}] {m['term']}")
        print("\n  Next: open the rendered page image for a page that should "
              "contain a missing term and read it against the extraction.")

    if not args.no_vocab_panel:
        vocab = registry_vocabulary()
        scan = scan_vocabulary(text, vocab)
        print(f"\nREGISTRY VOCABULARY PANEL  (informational, gates nothing)")
        print(f"  known literals ... {len(vocab)}")
        print(f"  present .......... {len(scan['present'])}")
        for term, cat, n in sorted(scan["present"], key=lambda x: -x[2])[:25]:
            print(f"    {n:>5}  [{cat:<12}] {term}")
        print(f"  absent ........... {len(scan['absent'])}  "
              f"(not a fault -- this manual may not offer them)")
        print("  Feed this to reconcile.py: a product this manual clearly "
              "does\n  offer but which reads 0 here is a damaged name, not a "
              "missing one.")

    return 0 if v == "CLEAN" else 1


if __name__ == "__main__":
    sys.exit(main())
