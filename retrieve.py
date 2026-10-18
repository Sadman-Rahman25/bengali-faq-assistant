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
import os
import sys
from pathlib import Path

import embed
from bnnorm import canon
from dedupe import clause_of, collapse
import redact
from embed import (BM25, expand_banglish, index_text, is_figure_query,
                   query_text, rank_hybrid)

# Which index is live. Set BFA_INDEX_DIR to switch models without rebuilding:
#   out/index_labse    dim  768
#   out/index_bge_m3   dim 1024
# Read once at import, like every other module constant here.
INDEX_DIR = Path(os.environ.get("BFA_INDEX_DIR", "out/index"))
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
    """Product keys named in text, via productmatch's own patterns.

    THE TEXT IS canon()'d FIRST (Convention 4: canon the text where you
    can). It was matched raw, and that failed on loan_18_month's OWN display
    name, ১৮ মাস মেয়াদি লোন, for two independent reasons -- either one alone
    was enough. Measured:

      digits  the pattern carries ASCII 18; a question types ১৮. canon folds
              digits, raw text does not.
      nukta   with ASCII digits, precomposed য় (U+09DF) still missed and only
              the decomposed U+09AF U+09BC matched. canon gives one form.

    Raw, 1 of the NAMED products failed to match its own name; canon'd, 0.
    registry.products_named already canon()'d -- which is why the registry
    found this product while this function did not.
    """
    import productmatch as pm
    text = canon(text or "")
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
        cfg = json.loads((d / "model.json").read_text(encoding="utf-8"))
        self.cfg = cfg
        self.meta = [json.loads(l) for l in
                     (d / "meta.jsonl").read_text(encoding="utf-8").splitlines()
                     if l.strip()]
        self.faiss = faiss.read_index(str(d / "dense.faiss"))
        # A gemini-* index is served by the hosted embedder; anything else is
        # a local sentence-transformer, constructed EXACTLY as before.
        #
        # The branch is deliberately additive rather than a single call to
        # embed.load_model. load_model also pins device="cpu" and caps
        # max_seq_length, and while both are almost certainly no-ops for
        # LaBSE here, "almost certainly" is not a thing to assert about the
        # live index on a machine that currently cannot load LaBSE to check.
        # The local path is left untouched so it cannot have changed.
        if embed.is_api_model(cfg["model"]):
            self.model = embed.load_model(cfg["model"])
        else:
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer(cfg["model"])
        self.model._bfa_name = cfg["model"]
        # None for an index built before weights were recorded, which makes
        # rank_hybrid fall back to the module constants -- the behaviour
        # those indexes were measured with.
        self.weights = ((cfg["w_dense_topic"], cfg["w_dense_figure"])
                        if "w_dense_topic" in cfg else None)
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
            # WITHHELD FOR PERSONAL DATA, checked first and unconditionally.
            # This is the only choke point every search shares -- filtered,
            # unfiltered, dumped or interactive -- so the exclusion cannot be
            # bypassed by calling search() a different way. See redact.py.
            if redact.is_excluded(m["chunk_id"]):
                continue
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

    @property
    def _row(self):
        """{chunk_id: row index}, built once. The component scores are indexed
        by corpus position, the results by chunk_id."""
        if getattr(self, "_row_cache", None) is None:
            self._row_cache = {m["chunk_id"]: i
                               for i, m in enumerate(self.meta)}
        return self._row_cache

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
        sparse_raw = self.bm25.scores(qt)
        scores = rank_hybrid(qv, self.D, sparse_raw, fig, self.weights)
        # The two sides, kept separately. A fused score alone cannot say
        # WHICH component found a chunk, and that is the first question asked
        # of every miss. rank_hybrid stays the single owner of the weighting;
        # this only re-derives the parts that go into it.
        dense_raw = self.D @ qv
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
        # BELT AND BRACES. _allowed already excluded these; this catches a
        # future path that builds results without going through it, and it
        # raises rather than filtering, because silently dropping a chunk here
        # would hide the bug that let it through.
        redact.assert_clean([m for _score, m in kept], "search result")

        status, populated, total = product_status()
        out = []
        for score, m in kept:
            named = products_named(m["index_text"])
            i = self._row.get(m["chunk_id"])
            out.append({
                "chunk_id": m["chunk_id"],
                "manual_id": m["manual_id"],
                "score": score,
                "fused_score": score,
                "dense_score": (float(dense_raw[i]) if i is not None else None),
                "sparse_score": (float(sparse_raw[i]) if i is not None else None),
                "weighting": "figure" if fig else "topic",
                "clause": clause_of(m),
                "heading_path": m["heading_path"],
                "products": named,
                # Joined at query time, never stored in the chunk.
                "product_status": {p: (status.get(p) or "unknown")
                                   for p in named},
                "status_registry_populated": populated,
                "status_registry_rows": total,
                # FULL text. Truncating here would silently shorten the
                # context every downstream caller sees; the CLI truncates at
                # print time instead, which is where the limit belongs.
                "display_text": m["display_text"],
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

    # 4. the personal-data exclusion, and it is asserted END TO END below as
    #    well: redact.py proves the chunk is dropped from a chunk list, and
    #    the dump path proves it never reaches a retrieval result.
    ok4 = redact.canary_selftest(verbose=verbose)
    ok &= ok4
    return ok


DUMP_FIELDS = ("chunk_id", "manual_id", "clause", "heading_path",
               "display_text", "dense_score", "sparse_score", "fused_score")


# manual_scope values that name no single manual. These questions TEST
# disambiguation -- G-203 and G-216 exist to see whether the answer asks which
# manual -- so forcing a preferred manual would defeat the question rather
# than help it. A list of manuals is in the same position: more than one.
SCOPE_NOT_SINGLE = frozenset({"ambiguous", "both", "any", "", "none"})


def scope_to_manual_id(scope, known):
    """manual_scope -> a corpus manual_id, or None.

    The gold files write short names -- dabi, progoti, livestock_grow -- and
    the corpus writes dabi_2025, progoti, livestock_grow_2026. Resolved by
    prefix against the ids the corpus actually uses, the same way the
    must_cite repair was, so nothing here hardcodes a manual name. Ambiguous
    or absent scope -> None, and a scope matching several ids -> None rather
    than a guess.
    """
    if not isinstance(scope, str):
        return None                       # a list names more than one manual
    sc = scope.strip().lower()
    if sc in SCOPE_NOT_SINGLE:
        return None
    if sc in known:
        return sc
    cands = [k for k in known if k.startswith(sc)]
    return cands[0] if len(cands) == 1 else None


def corpus_manual_ids(ix):
    """The manual ids present in the index. -> set"""
    return {m.get("manual_id") for m in ix.meta if m.get("manual_id")}


def dump_retrieved(ix, questions, k, out_path):
    """Write one row per gold question to `out_path` as JSONL.

    WHY THIS EXISTS AS A FILE. generate.py reads this and never calls the
    retriever. Context must be byte-identical between runs: when a prompt rule
    changes and a score moves, the cause has to be the rule, not a context
    that drifted because the index or the model was reloaded in between.
    """
    n = 0
    known = corpus_manual_ids(ix)
    with Path(out_path).open("w", encoding="utf-8") as f:
        for q in questions:
            qid, qbn = q["id"], q["question_bn"]
            # PREFER THE QUESTION'S OWN MANUAL when it names one. This is not
            # a filter: prefer_manual only decides which member of a 1.000
            # duplicate group survives the collapse. Without it the surviving
            # twin is whichever scored higher, which is arbitrary with respect
            # to the question -- G-201 asks about Dabi and was handed the
            # byte-identical Progoti chunk, so the citation was wrong while
            # the content was right.
            prefer = scope_to_manual_id(q.get("manual_scope"), known)
            hits = ix.search(qbn, k=k, prefer_manual=prefer)
            row = {"question_id": qid, "question_bn": qbn,
                   "chunks": [{key: h[key] for key in DUMP_FIELDS}
                              for h in hits]}
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?")
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--manual-id")
    ap.add_argument("--insurer")
    ap.add_argument("--programme")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--dump", metavar="PATH",
                    help="write one row per gold question to PATH as JSONL "
                         "and exit; generate.py reads this instead of "
                         "calling the retriever")
    ap.add_argument("--gold", nargs="*",
                    default=["gold/bootstrap.yaml",
                             "gold/bootstrap_livestock_grow.yaml"],
                    help="gold files the --dump rows come from")
    args = ap.parse_args()

    print("CANARY")
    if not canary_selftest():
        print("FATAL: retrieve failed its own canary.")
        return 2
    if args.dump:
        if not (INDEX_DIR / "model.json").exists():
            print(f"\nno index at {INDEX_DIR}. "
                  f"Run:  python embed.py --build MODEL")
            return 1
        from embed import load_questions
        qs = load_questions(args.gold)
        if not qs:
            print(f"\nno gold questions in {args.gold}")
            return 1
        ix = Index()
        n = dump_retrieved(ix, qs, args.k, args.dump)
        print(f"\n-> {args.dump}  ({n} questions, k={args.k})")
        return 0

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
        print(f"    dense={h['dense_score']:.4f} "
              f"sparse={h['sparse_score']:.3f} fused={h['fused_score']:.4f}"
              f"  clause={h['clause'] or '-'}")
    if hits and hits[0]["status_registry_populated"] == 0:
        print(f"\n[warn] product status registry has "
              f"{hits[0]['status_registry_rows']} rows and 0 statuses set. "
              f"No product\n       can surface as suspended. Fill the status "
              f"column in products_catalogue.csv.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
