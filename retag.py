"""Recompute the `product` field with context-aware matching.

ex.py tags products by plain substring match over heading+body. That over-tags,
because two product names are also ordinary Bengali words:

  গতি   "speed"  -> also inside প্রগতি, অগ্রগতি, গতিশীল, সংগতি, গতানুগতিক
  দাবি  "claim"  -> also inside বিমাদাবি / বিমাদাবির ("insurance claim")

Corpus evidence (chunks_progoti.jsonl, 393 chunks, post-Bijoy-conversion):
  দাবি  468 occurrences, of which 215 are বিমা-/বীমা-/মৃত্যু- compounds.
        দাবি IS a real product here (১.১.১ দাবি + কর্মসূচি, ৪.৩ এলাকা
        ব্যবস্থাপক - দাবি, ১০.২ দাবি অপারেশনাল ...), so it is kept but the
        insurance-claim compounds are excluded.
  গতি   322 occurrences, 291 of them প্রগতি/প্রগতির/প্রগতিসহ and ~22 common
        nouns (অগ্রগতি, গতিশীল, গতিধারা, গতানুগতিক, সংগতি).

        গতি IS a product -- "গতি লোন" -- but it never appears as a heading:
        it is item 6 INSIDE ১.১.২ প্রগতি কর্মসূচি. An earlier pass checked
        headings only, concluded there was no গতি product, and dropped the
        tag. That was wrong. progoti_0018 is a full গতি লোন feature table
        (১-৫ লক্ষ, ২/৩/৪ মাস, 24/22/20% service charge) and progoti_0391's
        changelog records "নতুন সংযুক্তি: গতি লোন — ১.১.২".

        Bare substring matching is still wrong, though: of 19 "গতি লোন"
        substrings, 10 are the tail of "প্রগতি লোন". So গতি is matched only
        when a product word follows AND প্র/অগ্র/সং does not precede.
        That tags exactly 3 chunks, with 0 false positives.

Acronym products need ordering care too: সিডিপি is a substring of এনসিডিপি and
এসসিডিপি. Here that is enforced by an explicit lookbehind rather than by the
order rules happen to run in.

Usage:  python retag.py data/processed/chunks_progoti.jsonl
Writes: data/processed/chunks_progoti_retagged.jsonl
"""
import sys, json, re, unicodedata
from collections import Counter
from pathlib import Path

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else "data/processed/chunks_progoti.jsonl")

PROD = [
    ("progoti", re.compile(r"প্রগতি")),
    ("bcup",    re.compile(r"বিসিইউপি")),
    ("ncdp",    re.compile(r"এনসিডিপি")),
    ("scdp",    re.compile(r"এসসিডিপি")),
    # সিডিপি only when not the tail of এনসিডিপি / এসসিডিপি
    ("cdp",     re.compile(r"(?<![নস])সিডিপি")),
    # দাবি only when not an insurance/death claim compound, joined or spaced
    ("dabi",    re.compile(r"(?<!বিমা)(?<!বীমা)(?<!বিম)(?<!মৃত্যু)"
                           r"(?<!বিমা )(?<!বীমা )(?<!মৃত্যু )দাবি")),
    # গতি only as a product: a product word must follow, and প্রগতি/অগ্রগতি/
    # সংগতি must not be what we are looking at. Lookahead keeps it off the
    # common nouns (গতিশীল, গতিধারা, গতানুগতিক) without enumerating them.
    ("goti",    re.compile(r"(?<!প্র)(?<!অগ্র)(?<!সং)(?<!সঙ্)"
                           r"গতি\s*(?=লোন|কর্মসূচি|প্রোডাক্ট)")),
]

nm = lambda s: re.sub(r"[ \t]+", " ", unicodedata.normalize("NFC", s or "")).strip()


def prods(text):
    t = nm(text)
    return [k for k, rx in PROD if rx.search(t)]


chunks = [json.loads(l) for l in SRC.open(encoding="utf-8")]

before = Counter()
after = Counter()
added, removed = Counter(), Counter()
examples = {}
changed = 0

for c in chunks:
    old = list(c.get("product") or [])
    new = prods(c["display_text"])
    before.update(old or ["<none>"])
    after.update(new or ["<none>"])
    for k in set(old) - set(new):
        removed[k] += 1
        examples.setdefault(("-", k), []).append(c)
    for k in set(new) - set(old):
        added[k] += 1
        examples.setdefault(("+", k), []).append(c)
    if set(old) != set(new):
        changed += 1
    c["product"] = new

print("=" * 74)
print(f"PRODUCT RETAG  ({SRC.name})   {changed} of {len(chunks)} chunks changed")
print("=" * 74)
print(f"  {'product':<10}{'before':>8}{'after':>8}{'added':>8}{'removed':>9}")
for k in sorted(set(before) | set(after)):
    print(f"  {k:<10}{before.get(k, 0):>8}{after.get(k, 0):>8}"
          f"{added.get(k, 0):>8}{removed.get(k, 0):>9}")

for (sign, k), cs in sorted(examples.items()):
    word = "REMOVED" if sign == "-" else "ADDED"
    print(f"\n  {word} {k} -- {len(cs)} chunks, first 4:")
    for c in cs[:4]:
        head = " > ".join(c["heading_path"])[:56]
        hay = nm(c["display_text"])
        probe = {"dabi": "দাবি", "goti": "গতি", "progoti": "প্রগতি",
                 "cdp": "সিডিপি", "ncdp": "এনসিডিপি", "scdp": "এসসিডিপি",
                 "bcup": "বিসিইউপি"}[k]
        m = re.search(probe, hay)
        ctx = hay[max(0, m.start() - 38):m.start() + 38].replace("\n", " ") if m else ""
        print(f"    {c['chunk_id']}  {head}")
        print(f"      …{ctx}…")

dst = SRC.with_name(SRC.stem + "_retagged.jsonl")
with dst.open("w", encoding="utf-8") as f:
    for c in chunks:
        f.write(json.dumps(c, ensure_ascii=False) + "\n")
print(f"\n  -> {dst}")
print("  Spot-check a REMOVED dabi chunk: it should be about বিমাদাবি")
print("  (insurance claim), not about the দাবি loan product.")
