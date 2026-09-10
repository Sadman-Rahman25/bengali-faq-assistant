"""Digit folding must be symmetric between index and query.

A query for ৪০,০০০ against a folded index silently returns nothing useful --
no exception, no warning, just a confident answer from the wrong chunk. This
test is the thing that throws.

Every corpus gets its own fixture, chosen so the test cannot pass vacuously:

    progoti    ৪০,০০০  (-> 40000)   branch-office allocation ceiling,
                                     §7.1 প্রাধিকারসূচি, progoti_0307_p2
    dabi_2025  ১০,২০,০০০ (-> 1020000) total insurance payout (১০ লক্ষ cover
                                     + ২০,০০০ immediate), §5.1.5,
                                     dabi_2025_0191

Each figure occurs in exactly ONE chunk of its corpus, in Bengali digits, and
NOWHERE in that corpus in ASCII digits. So an ASCII query can only ever reach
it through folding -- if folding is dropped on either side there is no other
path to a pass. Both are comma-grouped, which makes strip_groups load-bearing
too; the Dabi one additionally uses lakh-style grouping (১০,২০,০০০), so it
exercises a separator position the Progoti fixture does not.

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

DATA = Path(__file__).resolve().parent.parent / "data" / "processed"
K = 5

# Per corpus: the four forms a user plausibly types for one figure. All four
# must reach the same chunk. The separator-free forms are what make
# strip_groups load-bearing -- the corpus writes ৪০,০০০, so a query of 40000
# can only match if separators are stripped on BOTH sides.
FIXTURES = [
    dict(tag="progoti", figure="40000", chunk="progoti_0307_p2",
         queries={"bengali+comma": "৪০,০০০ টাকা",
                  "bengali+nosep": "৪০০০০ টাকা",
                  "ascii+comma": "40,000 টাকা",
                  "ascii+nosep": "40000 টাকা"}),
    dict(tag="dabi_2025", figure="1020000", chunk="dabi_2025_0191",
         queries={"bengali+comma": "১০,২০,০০০ টাকা",
                  "bengali+nosep": "১০২০০০০ টাকা",
                  "ascii+comma": "10,20,000 টাকা",
                  "ascii+nosep": "1020000 টাকা"}),
]


def load(tag):
    return [json.loads(l) for l in
            (DATA / f"chunks_{tag}_final.jsonl").open(encoding="utf-8")]


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
    for fx in FIXTURES:
        tag, figure, expect = fx["tag"], fx["figure"], fx["chunk"]
        chunks = load(tag)
        bengali_hits, ascii_hits = set(), set()
        for c in chunks:
            dt = c["display_text"]
            for m in re.finditer(r"[০-৯][০-৯,\.]*", dt):
                if _local_canon(m.group(0)) == figure:
                    bengali_hits.add(c["chunk_id"])
            for m in re.finditer(r"[0-9][0-9,\.]*", dt):
                if _local_canon(m.group(0)) == figure:
                    ascii_hits.add(c["chunk_id"])

        assert bengali_hits == {expect}, (
            f"[{tag}] fixture drifted: {figure} should appear in Bengali digits "
            f"in exactly {expect}, found {sorted(bengali_hits)}")
        assert not ascii_hits, (
            f"[{tag}] fixture is now VACUOUS: {figure} also appears in ASCII "
            f"digits in {sorted(ascii_hits)}, so an ASCII query could succeed "
            f"without folding")
        hit = next(c for c in chunks if c["chunk_id"] == expect)
        assert figure in hit["numbers"], (
            f"[{tag}] {figure} is not in {expect}'s numbers list")


# --------------------------------------------------------------------------
# The real assertions, on the correctly-wired index.
# --------------------------------------------------------------------------
def test_bengali_and_ascii_return_identical_topk():
    for fx in FIXTURES:
        idx = BM25(load(fx["tag"]), canon)
        got = {label: idx.search(q, canon) for label, q in fx["queries"].items()}
        for label, top in got.items():
            assert top, f"[{fx['tag']}] query form {label!r} returned nothing"
        distinct = {tuple(v) for v in got.values()}
        assert len(distinct) == 1, (
            f"[{fx['tag']}] query forms disagree on top-k:\n"
            + "\n".join(f"  {l:<14} {v}" for l, v in got.items()))


def test_top1_actually_contains_the_figure():
    """Parity alone would pass if every form returned the same WRONG chunk."""
    for fx in FIXTURES:
        tag, figure, expect = fx["tag"], fx["figure"], fx["chunk"]
        chunks = load(tag)
        idx = BM25(chunks, canon)
        by_id = {c["chunk_id"]: c for c in chunks}
        for label, q in fx["queries"].items():
            top = idx.search(q, canon)
            assert top, f"[{tag}] query form {label!r} returned nothing"
            assert top[0] == expect, (
                f"[{tag}] {label} top-1 is {top[0]}, expected {expect}")
            assert figure in by_id[top[0]]["numbers"], (
                f"[{tag}] {label} top-1 {top[0]} does not actually contain "
                f"{figure} -- retrieval agreed on the wrong chunk")


# --------------------------------------------------------------------------
# The part that stops this file rotting into a no-op: if someone removes
# folding from either side, the assertions above MUST start failing.
# --------------------------------------------------------------------------
def _correct_behaviour(fx, index_norm, query_norm):
    """Does this wiring satisfy parity AND correctness, for every query form?"""
    chunks = load(fx["tag"])
    idx = BM25(chunks, index_norm)
    by_id = {c["chunk_id"]: c for c in chunks}
    tops = [idx.search(q, query_norm) for q in fx["queries"].values()]
    if not all(tops) or len({tuple(t) for t in tops}) != 1:
        return False
    return (tops[0][0] == fx["chunk"]
            and fx["figure"] in by_id[tops[0][0]]["numbers"])


def test_dropping_folding_is_detected():
    for fx in FIXTURES:
        assert _correct_behaviour(fx, canon, canon), (
            f"[{fx['tag']}] correctly-wired index should pass -- "
            f"something else is broken")

        for name, inorm, qnorm in (
            ("folded index, raw query", canon, IDENTITY),
            ("raw index, folded query", IDENTITY, canon),
            ("no folding anywhere", IDENTITY, IDENTITY),
        ):
            assert not _correct_behaviour(fx, inorm, qnorm), (
                f"[{fx['tag']}] '{name}' PASSED -- this test can no longer "
                f"detect a dropped fold, so it is not protecting anything")


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
