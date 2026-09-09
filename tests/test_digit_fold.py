"""Digit folding must be symmetric between index and query.

A query for ৪০,০০০ against a folded index silently returns nothing useful --
no exception, no warning, just a confident answer from the wrong chunk. This
test is the thing that throws.

The fixture figure is chosen so the test cannot pass vacuously:

    ৪০,০০০  (folds to 40000) -- a branch-office allocation ceiling in
    ৭.১ প্রাধিকারসূচি (টেবিল অব অথরিটি), chunk progoti_0307_p2.

It occurs in exactly ONE chunk, in Bengali digits, and NOWHERE in the corpus
in ASCII digits. So an ASCII query can only ever reach it through folding --
if folding is dropped on either side, there is no other path to a pass.

Runs standalone (python tests/test_digit_fold.py) or under pytest.
"""
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bnnorm import canon, tokenize  # noqa: E402

CHUNKS = (Path(__file__).resolve().parent.parent
          / "data" / "processed" / "chunks_progoti_final.jsonl")

# The fixture. All four forms are things a user plausibly types for the same
# figure, and all four must reach the same chunk. The separator-free forms are
# what make strip_groups load-bearing: the corpus writes it as ৪০,০০০, so a
# query of 40000 can only match if separators are stripped on BOTH sides.
QUERIES = {
    "bengali+comma": "৪০,০০০ টাকা",
    "bengali+nosep": "৪০০০০ টাকা",
    "ascii+comma": "40,000 টাকা",
    "ascii+nosep": "40000 টাকা",
}
BENGALI_QUERY = QUERIES["bengali+comma"]
ASCII_QUERY = QUERIES["ascii+comma"]
FIGURE = "40000"                    # as it appears in chunk["numbers"]
EXPECT_CHUNK = "progoti_0307_p2"
K = 5


def load():
    return [json.loads(l) for l in CHUNKS.open(encoding="utf-8")]


# --------------------------------------------------------------------------
# A minimal BM25. Deliberately stdlib-only and dependency-free: the point is
# to test the NORMALISATION, not whatever engine ships later. Any lexical
# index has the same index/query symmetry requirement.
# --------------------------------------------------------------------------
class BM25:
    def __init__(self, chunks, normalise, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.ids, self.tf, self.len = [], [], []
        df = Counter()
        for c in chunks:
            toks = tokenize(normalise(c["display_text"]))
            self.ids.append(c["chunk_id"])
            self.tf.append(Counter(toks))
            self.len.append(len(toks))
            df.update(set(toks))
        self.n = len(self.ids)
        self.avg = sum(self.len) / max(self.n, 1)
        self.idf = {t: math.log(1 + (self.n - v + 0.5) / (v + 0.5))
                    for t, v in df.items()}

    def search(self, query, normalise, k=K):
        q = tokenize(normalise(query))
        scored = []
        for i, cid in enumerate(self.ids):
            tf, dl = self.tf[i], self.len[i]
            s = 0.0
            for t in q:
                f = tf.get(t, 0)
                if not f:
                    continue
                s += self.idf.get(t, 0.0) * f * (self.k1 + 1) / (
                    f + self.k1 * (1 - self.b + self.b * dl / max(self.avg, 1)))
            if s > 0:
                scored.append((s, cid))
        # deterministic ordering for ties
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [cid for _, cid in scored[:k]]


IDENTITY = lambda s: s  # noqa: E731  -- "folding was never wired up"


# --------------------------------------------------------------------------
# Guard: the fixture must stay non-vacuous even if the corpus is rebuilt.
#
# Deliberately does NOT use bnnorm -- a guard that measures the corpus must
# not depend on the module it is guarding, or mutating bnnorm makes this fail
# for reasons that have nothing to do with the corpus.
#
# DO NOT CONSOLIDATE THIS TABLE INTO bnnorm. ex.py and resplit.py were merged
# into bnnorm precisely so there is one fold table in the pipeline; this copy
# is the deliberate exception. It is the independent oracle. If the test
# imported the same table it is asserting against, a wrong table would agree
# with itself and every assertion here would pass -- the test would verify
# self-consistency rather than correctness. Mutation testing proved the
# difference: with this table shared, breaking fold_digits made the fixture
# guard fail for a spurious reason instead of the real one.
# --------------------------------------------------------------------------
_LOCAL_FOLD = {ord(c): str(i) for i, c in enumerate("০১২৩৪৫৬৭৮৯")}
_local_canon = lambda s: s.translate(_LOCAL_FOLD).replace(",", "").rstrip(".")


def test_fixture_is_bengali_digits_only():
    chunks = load()
    bengali_hits, ascii_hits = set(), set()
    for c in chunks:
        dt = c["display_text"]
        for m in re.finditer(r"[০-৯][০-৯,\.]*", dt):
            if _local_canon(m.group(0)) == FIGURE:
                bengali_hits.add(c["chunk_id"])
        for m in re.finditer(r"[0-9][0-9,\.]*", dt):
            if _local_canon(m.group(0)) == FIGURE:
                ascii_hits.add(c["chunk_id"])

    assert bengali_hits == {EXPECT_CHUNK}, (
        f"fixture drifted: {FIGURE} should appear in Bengali digits in exactly "
        f"{EXPECT_CHUNK}, found {sorted(bengali_hits)}")
    assert not ascii_hits, (
        f"fixture is now VACUOUS: {FIGURE} also appears in ASCII digits in "
        f"{sorted(ascii_hits)}, so an ASCII query could succeed without folding")
    assert FIGURE in next(c for c in chunks if c["chunk_id"] == EXPECT_CHUNK)["numbers"]


# --------------------------------------------------------------------------
# The real assertions, on the correctly-wired index.
# --------------------------------------------------------------------------
def test_bengali_and_ascii_return_identical_topk():
    idx = BM25(load(), canon)
    got = {label: idx.search(q, canon) for label, q in QUERIES.items()}
    for label, top in got.items():
        assert top, f"query form {label!r} returned nothing at all"
    distinct = {tuple(v) for v in got.values()}
    assert len(distinct) == 1, (
        "query forms disagree on top-k:\n"
        + "\n".join(f"  {l:<14} {v}" for l, v in got.items()))


def test_top1_actually_contains_the_figure():
    """Parity alone would pass if every form returned the same WRONG chunk."""
    chunks = load()
    idx = BM25(chunks, canon)
    by_id = {c["chunk_id"]: c for c in chunks}
    for label, q in QUERIES.items():
        top = idx.search(q, canon)
        assert top, f"query form {label!r} returned nothing"
        assert top[0] == EXPECT_CHUNK, (
            f"{label} top-1 is {top[0]}, expected {EXPECT_CHUNK}")
        assert FIGURE in by_id[top[0]]["numbers"], (
            f"{label} top-1 {top[0]} does not actually contain {FIGURE} "
            f"-- retrieval agreed on the wrong chunk")


# --------------------------------------------------------------------------
# The part that stops this file rotting into a no-op: if someone removes
# folding from either side, the assertions above MUST start failing.
# --------------------------------------------------------------------------
def _correct_behaviour(index_norm, query_norm):
    """Does this wiring satisfy parity AND correctness, for every query form?"""
    chunks = load()
    idx = BM25(chunks, index_norm)
    by_id = {c["chunk_id"]: c for c in chunks}
    tops = [idx.search(q, query_norm) for q in QUERIES.values()]
    if not all(tops) or len({tuple(t) for t in tops}) != 1:
        return False
    return tops[0][0] == EXPECT_CHUNK and FIGURE in by_id[tops[0][0]]["numbers"]


def test_dropping_folding_is_detected():
    assert _correct_behaviour(canon, canon), \
        "correctly-wired index should pass -- something else is broken"

    for name, inorm, qnorm in (
        ("folded index, raw query", canon, IDENTITY),
        ("raw index, folded query", IDENTITY, canon),
        ("no folding anywhere", IDENTITY, IDENTITY),
    ):
        assert not _correct_behaviour(inorm, qnorm), (
            f"'{name}' PASSED -- this test can no longer detect a dropped "
            f"fold, so it is not protecting anything")


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted((k, v) for k, v in globals().items()
                           if k.startswith("test_") and callable(v)):
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            fails += 1
            print(f"FAIL  {name}\n      {e}")
    print(f"\n{'FAILED' if fails else 'OK'} -- {fails} failure(s)")
    sys.exit(1 if fails else 0)
