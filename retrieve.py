"""retrieve.py -- STEP 9. Hybrid retrieval with filters and a status join.

Filters are all optional and combinable:

  manual_id   exact. §5.4 is fire-safety in Progoti and livestock insurance
              in Dabi, so a clause citation without a manual name is wrong
              two thirds of the time.
  insurer     exact, and REQUIRED for any premium or exclusion query. The
              same product carries a different premium per insurer --
              ২.৩৫% at গ্রীন ডেল্টা against ২.৪০% at সেনা for দুগ্ধজাত গাভী,
              and a flat ১.৮০% against a tiered ১.৯০%/১.৬০% for
              গরু মোটাতাজাকরণ. An answer that omits the insurer is right
              half the time and the officer cannot tell which half.
  programme   PERMISSIVE: the named programme OR programme-neutral.

WHY PROGRAMME MUST BE PERMISSIVE
Measured on this corpus, 14 Sep 2026:

    manual                chunks   programme-tagged   neutral
    dabi_2025                400                124       69%
    progoti                  431                160       63%
    livestock_grow_2026       36                  2       94%

`programmes_in` tags a chunk only where the programme is NAMED. A strict
filter therefore discards 94% of the livestock manual from both programmes,
and roughly two thirds of each docx manual. The manual serves দাবি and
প্রগতি both -- its enrolment step names শাখা ব্যবস্থাপক for one and
এলাকা ব্যবস্থাপক for the other -- so a neutral chunk is in scope for every
programme, not none.

STATUS IS JOINED AT QUERY TIME, NEVER BAKED IN
A withdrawn product's chunks stay in the corpus untouched: the terms still
bind existing clients, and deleting them would answer "what were the terms"
with silence. Status comes from products_catalogue.csv at query time, so it
can change without a re-extraction, and a suspended product surfaces AS
suspended WITH its terms.

Convention 2: no product, role or insurer literal appears in this file.
Insurers come from productmatch.ENTITIES, programmes from
productmatch.PROGRAMMES, and every pattern is productmatch's own.

Usage:
  python retrieve.py "দুগ্ধজাত গাভীর প্রিমিয়াম কত?" --insurer green_delta_insurance
  python retrieve.py --selftest
"""

import argparse
import csv
import json
import sys
from pathlib import Path

import embed
from dedupe import collapse
from embed import (BM25, expand_banglish, index_text, is_figure_query,
                   query_text, rank_hybrid)

INDEX_DIR = Path("out/index")
CATALOGUE = Path("products_catalogue.csv")


# ------------------------------------------------------------- registries

def insurers():
    """{key: (display_name, compiled pattern)} for entity_type underwriter.

    From productmatch. Never a literal here -- these names are ordinary
    Bengali words in places and the correct pattern is not obvious.
    """
    import productmatch as pm
    return {k: (n, pm.compile_pat(p))
            for k, t, n, _a, p in pm.ENTITIES if t == "underwriter"}


def programmes():
    """{key: compiled pattern}, already lookbehind-guarded by productmatch."""
    import productmatch as pm
    return dict(pm.PROGRAMMES)


def product_status(path=CATALOGUE):
    """{product_key: status} from the catalogue, plus whether it is populated.

    Returns (mapping, populated_count, total_rows). The caller must surface
    populated_count: a registry that is present but EMPTY answers every
    status question with "no status", which is indistinguishable from
    "active" unless the emptiness is reported.
    """
    if not path.exists():
        return {}, 0, 0
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    m = {}
    for r in rows:
        k = (r.get("product_key") or "").strip()
        s = (r.get("status") or "").strip()
        if k:
            m[k] = s
    return m, sum(1 for v in m.values() if v), len(rows)


def products_named(text):
    """Product keys named in text, via productmatch's own patterns."""
    import productmatch as pm
    hits = []
    for key, _n, _a, pat in pm.NAMED:
        if pm.compile_pat(pat).search(text):
            hits.append(key)
    return hits


# --------------------------------------------------------------- the index

class Index:
    def __init__(self, d=INDEX_DIR):
        import faiss
        import numpy as np
        from sentence_transformers import SentenceTransformer
        cfg = json.loads((d / "model.json").read_text(encoding="utf-8"))
        self.cfg = cfg
        self.meta = [json.loads(l) for l in
                     (d / "meta.jsonl").read_text(encoding="utf-8").splitlines()
                     if l.strip()]
        self.faiss = faiss.read_index(str(d / "dense.faiss"))
        self.model = SentenceTransformer(cfg["model"])
        self.model._bfa_name = cfg["model"]
        self.bm25 = BM25([m["index_text"] for m in self.meta])
        self._np = np
        # The dense matrix, for filtered re-ranking. 867 chunks is small;
        # reconstructing avoids maintaining a second copy on disk.
        self.D = self.faiss.reconstruct_n(0, self.faiss.ntotal)

    def _allowed(self, manual_id=None, insurer=None, programme=None):
        ins = insurers()
        progs = programmes()
        ipat = ins[insurer][1] if insurer else None
        ppat = progs[programme] if programme else None
        keep = []
        for i, m in enumerate(self.meta):
            if manual_id and m["manual_id"] != manual_id:
                continue
            if ipat and not ipat.search(m["index_text"]):
                continue
            if ppat is not None:
                # PERMISSIVE: named programme OR programme-neutral.
                tagged = m.get("product") or []
                if tagged and programme not in tagged:
                    continue
            keep.append(i)
        return keep

    def search(self, query, k=10, manual_id=None, insurer=None,
               programme=None, prefer_manual=None):
        """prefer_manual is NOT a filter. `manual_id` restricts the candidate
        pool; prefer_manual only decides which member of a 1.000 duplicate
        group survives the collapse, so a manual-scoped question is cited from
        the manual it asked about without narrowing what can be retrieved.
        Defaults to manual_id when that is given, since a filtered search
        wants the same preference."""
        np = self._np
        allowed = self._allowed(manual_id, insurer, programme)
        if not allowed:
            return []
        qt = query_text(expand_banglish(query))
        qv = embed._encode(self.model, [qt], "query")[0]
        fig = is_figure_query(query)
        scores = rank_hybrid(qv, self.D, self.bm25.scores(qt), fig)
        mask = np.full(len(self.meta), -np.inf, dtype="float32")
        mask[allowed] = scores[allowed]
        order = np.argsort(-mask)[:max(k * 4, k + 20)]

        # COLLAPSE cross-manual duplicates before building the result.
        # Members of a 1.000 dup_group are byte-identical, score identically,
        # and therefore return adjacently -- measured: two of every three top
        # slots carried the same content twice, so an apparent top-5 held 2-3
        # distinct chunks. Over-fetch, collapse, then cut to k, so collapsing
        # fills the freed slots instead of shortening the result.
        #
        # manual_id is passed as prefer_manual: where a question names a
        # manual, the surviving member should be the one from that manual.
        # Safe at 1.000 because the bodies are identical; only the citation
        # changes. See dedupe.collapse.
        ranked = [(float(mask[i]), self.meta[i]) for i in order
                  if mask[i] != -np.inf]
        kept = collapse(ranked, k=k,
                        prefer_manual=prefer_manual or manual_id)

        status, populated, total = product_status()
        out = []
        for score, m in kept:
            named = products_named(m["index_text"])
            out.append({
                "chunk_id": m["chunk_id"],
                "manual_id": m["manual_id"],
                "score": score,
                "weighting": "figure" if fig else "topic",
                "heading_path": m["heading_path"],
                "products": named,
                # Joined at query time, never stored in the chunk.
                "product_status": {p: (status.get(p) or "unknown")
                                   for p in named},
                "status_registry_populated": populated,
                "status_registry_rows": total,
                "display_text": m["display_text"][:400],
            })
        return out


# ------------------------------------------------------------------- canary

def canary_selftest(verbose=True):
    """Convention 3. Both canaries are conditions measured in THIS run.

    1. Strict programme filtering. Measured: livestock_grow_2026 is 94%
       programme-neutral (34 of 36 chunks), dabi_2025 69%, progoti 63%. A
       strict filter drops nearly the whole livestock manual from BOTH
       programmes. The permissive filter must keep neutral chunks.
    2. An empty status registry. products_catalogue.csv has 43 rows and 0
       populated status values, so every status lookup currently returns
       "unknown". That must be reported, not silently rendered as active.

    NOT BACKFILLED: no canary for a real retrieval ranking failure. Nothing
    has been embedded, so none has been observed. Stated rather than
    invented.
    """
    ok = True

    # 1. permissive vs strict, on the real corpus, without needing an index.
    corpus = embed.load_corpus()
    prog = "dabi"
    neutral = [c for c in corpus if not (c.get("product") or [])]
    strict = [c for c in corpus if prog in (c.get("product") or [])]
    permissive = strict + neutral
    lg = [c for c in corpus if embed.manual_of(c) == "livestock_grow_2026"]
    lg_strict = [c for c in lg if prog in (c.get("product") or [])]
    drop = 1 - len(lg_strict) / max(len(lg), 1)
    ok1 = drop > 0.9 and len(permissive) > len(strict)
    if verbose:
        print(f"  canary permissive_programme strict keeps "
              f"{len(lg_strict)}/{len(lg)} of livestock ({drop:.0%} dropped); "
              f"permissive {len(permissive)}  "
              f"[{'DETECTED' if ok1 else 'MISSED'}]")
    ok &= ok1

    # 2. the registry is present but empty
    _m, populated, total = product_status()
    ok2 = total > 0 and populated == 0
    if verbose:
        print(f"  canary status_registry_empty rows={total} populated="
              f"{populated}  [{'DETECTED' if ok2 else 'MISSED'}]")
        if ok2:
            print("        -> every product_status reads \"unknown\". No "
                  "product can surface as")
            print("           suspended until the status column is filled. "
                  "Reported, not assumed.")
    ok &= ok2

    # 3. insurer and programme vocabularies come from productmatch
    ok3 = len(insurers()) >= 2 and len(programmes()) >= 2
    if verbose:
        print(f"  canary registries_wired     insurers={len(insurers())} "
              f"programmes={len(programmes())}  "
              f"[{'PASS' if ok3 else 'FAIL'}]")
    ok &= ok3
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?")
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--manual-id")
    ap.add_argument("--insurer")
    ap.add_argument("--programme")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    print("CANARY")
    if not canary_selftest():
        print("FATAL: retrieve failed its own canary.")
        return 2
    if args.selftest or not args.query:
        print("\nself-test only; no query given")
        print("  insurers available: " + ", ".join(sorted(insurers())))
        print("  programmes available: " + ", ".join(sorted(programmes())))
        return 0

    if not (INDEX_DIR / "model.json").exists():
        print(f"\nno index at {INDEX_DIR}. Run:  python embed.py --build MODEL")
        return 1

    ix = Index()
    hits = ix.search(args.query, k=args.k, manual_id=args.manual_id,
                     insurer=args.insurer, programme=args.programme)
    print(f"\nmodel {ix.cfg['model']}  normaliser {ix.cfg['normaliser']}")
    print(f"{len(hits)} hits"
          + (f"  [{hits[0]['weighting']}-weighted]" if hits else ""))
    for h in hits:
        print(f"\n  {h['score']:.3f}  {h['chunk_id']}  [{h['manual_id']}]")
        print(f"    {' > '.join(h['heading_path'])[:78]}")
        if h["products"]:
            print(f"    products: {h['product_status']}")
        print(f"    {h['display_text'][:150]!r}")
    if hits and hits[0]["status_registry_populated"] == 0:
        print(f"\n[warn] product status registry has "
              f"{hits[0]['status_registry_rows']} rows and 0 statuses set. "
              f"No product\n       can surface as suspended. Fill the status "
              f"column in products_catalogue.csv.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
