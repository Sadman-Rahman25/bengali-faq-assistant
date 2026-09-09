"""Near-duplicate detection across two chunk corpora.

Why this matters twice over:

  1. Retrieval  -- if the same rule sits in both manuals, a top-k of 5 can be
     two or three copies of one answer, crowding out genuinely different
     content.
  2. Attribution -- where the manuals AGREE, a duplicate is harmless and the
     answer need not say which manual it came from. Where they DIVERGE on a
     figure, retrieval that silently picks either one is wrong, and the answer
     MUST name the manual. Those pairs are the ones that need finding, and
     text similarity alone will not surface them: two chunks can be 97%
     identical and still disagree on the one number that matters.

So similarity is reported alongside a separate number-level comparison.

Normalisation before hashing: NFC, then bnnorm.canon (digit fold + thousands
separators), then whitespace collapse. Digit folding means a rule written
৫০,০০০ in one manual and 50000 in the other is recognised as the same text --
which is the point -- so number DIFFERENCES are read from the chunks'
own `numbers`/`unit_numbers` fields, not from the normalised string.

Usage:
    python dupscan.py                          # dabi_2025 vs progoti
    python dupscan.py dabi_2025 progoti 0.75   # explicit, with threshold
"""
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from bnnorm import canon

P = Path("data/processed")
SHINGLE = 3
NEAR = float(sys.argv[3]) if len(sys.argv) > 3 else 0.75
A_TAG = sys.argv[1] if len(sys.argv) > 1 else "dabi_2025"
B_TAG = sys.argv[2] if len(sys.argv) > 2 else "progoti"


def norm(s):
    s = canon(unicodedata.normalize("NFC", s or ""))
    return re.sub(r"\s+", " ", s).strip()


def load(tag):
    cs = [json.loads(l) for l in
          (P / f"chunks_{tag}_final.jsonl").open(encoding="utf-8")]
    for c in cs:
        head = " > ".join(c["heading_path"])
        dt = c["display_text"]
        body = dt[len(head):].lstrip("\n") if head and dt.startswith(head) else dt
        c["_head"] = norm(head)
        c["_body"] = norm(body)
        toks = c["_body"].split()
        c["_shingles"] = (set(zip(*(toks[i:] for i in range(SHINGLE))))
                          if len(toks) >= SHINGLE else set(toks))
        c["_nums"] = set(c["unit_numbers"])
        c["_allnums"] = set(c["numbers"])
    return cs


def jaccard(a, b):
    if not a or not b:
        return 0.0
    i = len(a & b)
    return i / (len(a) + len(b) - i)


A, B = load(A_TAG), load(B_TAG)
print("=" * 78)
print(f"NEAR-DUPLICATE SCAN :: {A_TAG} ({len(A)} chunks) vs {B_TAG} ({len(B)} chunks)")
print(f"normalise: NFC + bnnorm.canon + whitespace | {SHINGLE}-gram Jaccard "
      f"| near >= {NEAR}")
print("=" * 78)

# ---------------------------------------------------------------- exact
by_body = defaultdict(list)
for c in A:
    by_body[c["_body"]].append((A_TAG, c))
for c in B:
    by_body[c["_body"]].append((B_TAG, c))

exact_cross, exact_within = [], []
for body, items in by_body.items():
    if len(items) < 2 or not body:
        continue
    tags = {t for t, _ in items}
    (exact_cross if len(tags) > 1 else exact_within).append((body, items))

xchars = sum(len(b) * (len(i) - 1) for b, i in exact_cross)
tot_chars = sum(len(c["_body"]) for c in A) + sum(len(c["_body"]) for c in B)
print(f"\nEXACT DUPLICATES (identical normalised body)")
print(f"  cross-manual groups   : {len(exact_cross)}  "
      f"covering {sum(len(i) for _, i in exact_cross)} chunks")
print(f"  within-manual groups  : {len(exact_within)}  "
      f"covering {sum(len(i) for _, i in exact_within)} chunks")
print(f"  redundant chars       : {xchars} of {tot_chars} "
      f"({100*xchars/max(tot_chars,1):.1f}% of both corpora)")
print(f"\n  longest cross-manual exact duplicates:")
for body, items in sorted(exact_cross, key=lambda x: -len(x[0]))[:8]:
    ids = ", ".join(f"{t}:{c['chunk_id']}" for t, c in items)
    print(f"    {len(body):>5} chars  {ids}")
    print(f"           {body[:88]}")

# ------------------------------------------------------- near, cross only
exact_bodies = {b for b, i in exact_cross}
inv = defaultdict(list)
for j, c in enumerate(B):
    for sh in c["_shingles"]:
        inv[sh].append(j)

pairs = []
for c in A:
    cand = Counter()
    for sh in c["_shingles"]:
        for j in inv[sh]:
            cand[j] += 1
    for j, _ in cand.most_common():
        d = B[j]
        s = jaccard(c["_shingles"], d["_shingles"])
        if s >= NEAR:
            pairs.append((s, c, d))
pairs.sort(key=lambda x: -x[0])

buckets = Counter()
for s, _, _ in pairs:
    buckets["1.00 exact-shingle" if s == 1.0 else
            ">=0.95" if s >= 0.95 else
            ">=0.90" if s >= 0.90 else
            ">=0.80" if s >= 0.80 else ">=%.2f" % NEAR] += 1
print(f"\nNEAR-DUPLICATE PAIRS (cross-manual, Jaccard >= {NEAR})")
print(f"  total pairs: {len(pairs)}")
for k in ("1.00 exact-shingle", ">=0.95", ">=0.90", ">=0.80"):
    if buckets.get(k):
        print(f"    {k:<20} {buckets[k]}")
rest = {k: v for k, v in buckets.items() if k.startswith(">=0.7")}
for k, v in rest.items():
    print(f"    {k:<20} {v}")

a_hit = {c["chunk_id"] for _, c, _ in pairs}
b_hit = {d["chunk_id"] for _, _, d in pairs}
print(f"\n  {A_TAG:<12} chunks with >=1 cross-manual near-dup: "
      f"{len(a_hit)} of {len(A)} ({100*len(a_hit)/len(A):.0f}%)")
print(f"  {B_TAG:<12} chunks with >=1 cross-manual near-dup: "
      f"{len(b_hit)} of {len(B)} ({100*len(b_hit)/len(B):.0f}%)")

# ------------------------------------------- the dangerous class: same
# text, different numbers
print("\n" + "=" * 78)
print("DIVERGENT PAIRS -- near-identical text, DIFFERENT figures")
print("=" * 78)
print("These are the pairs where an answer must name its manual. Retrieval")
print("that returns either one at random is not slower, it is wrong.\n")
div = [(s, c, d) for s, c, d in pairs if c["_nums"] != d["_nums"]]
div_all = [(s, c, d) for s, c, d in pairs if c["_allnums"] != d["_allnums"]]
print(f"  pairs differing in unit_numbers : {len(div)}")
print(f"  pairs differing in any number   : {len(div_all)}")
for s, c, d in div[:12]:
    only_a = sorted(c["_nums"] - d["_nums"])
    only_b = sorted(d["_nums"] - c["_nums"])
    print(f"\n  sim={s:.3f}  {A_TAG}:{c['chunk_id']}  vs  {B_TAG}:{d['chunk_id']}")
    print(f"    heading A: {c['_head'][:66]}")
    print(f"    heading B: {d['_head'][:66]}")
    if only_a:
        print(f"    only in {A_TAG:<10}: {only_a[:7]}")
    if only_b:
        print(f"    only in {B_TAG:<10}: {only_b[:7]}")

# ------------------------------------------------- identical text+numbers
same = [(s, c, d) for s, c, d in pairs if c["_allnums"] == d["_allnums"]]
print("\n" + "=" * 78)
print("SAFE PAIRS -- same text AND same figures")
print("=" * 78)
print(f"  {len(same)} pairs. These only cost top-k slots; either copy answers")
print(f"  the question identically, so attribution does not matter.")
print(f"\n  worst top-k crowding (chunks with the most cross-manual twins):")
crowd = Counter()
for s, c, d in pairs:
    crowd[f"{A_TAG}:{c['chunk_id']}"] += 1
    crowd[f"{B_TAG}:{d['chunk_id']}"] += 1
for cid, n in crowd.most_common(8):
    print(f"    {n:>3} twins  {cid}")

print("\n" + "=" * 78)
print("HEADINGS: do duplicated bodies sit under the same heading?")
print("=" * 78)
sameh = sum(1 for s, c, d in pairs if c["_head"] == d["_head"])
print(f"  pairs with identical normalised heading_path : {sameh} of {len(pairs)}")
print(f"  pairs whose headings differ                  : {len(pairs)-sameh}")
for s, c, d in pairs:
    if c["_head"] != d["_head"]:
        print(f"    sim={s:.2f}  A: {c['_head'][:62]}")
        print(f"              B: {d['_head'][:62]}")
        break
