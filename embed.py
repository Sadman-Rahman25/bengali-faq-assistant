"""embed.py -- STEP 8. Build the hybrid index. Benchmark before choosing.

Nothing here picks a model. It scores three against the gold set and prints a
table; choosing is a decision for whoever reads it.

  BAAI/bge-m3                        dense + native sparse
  intfloat/multilingual-e5-large     dense only, needs "query:"/"passage:"
  sentence-transformers/LaBSE        dense only

NORMALISATION: display_norm, NOT canon
--------------------------------------
`canon()` folds ০-৯ to ASCII. Measured in this repo on 14 Sep 2026: switching
a matcher from raw text to canon'd text took bare-Bengali-number matches from
27/80 to 0/80, because the Bengali digit class then matched nothing at all.
An index built on canon'd text cannot distinguish ২.৩৫ from 2.35 -- and
cannot represent ০-৯ at all, which is how every figure in these manuals is
written.

display_norm does NFC, strips zero-width, collapses spaces, and leaves digits
alone. NFC already unifies the nukta forms onto the decomposed form (য় ড় ঢ়
are composition exclusions, so composition INTO them is excluded), so once
corpus and query both go through display_norm no per-pattern nukta handling
is needed. Convention 4, first preference: canon the text where you can.

CORPUS AND QUERY USE THE SAME FUNCTION. That is the single rule bnnorm
exists to enforce and it is the one thing here that cannot be got wrong
quietly -- a mismatch returns plausible answers from the wrong chunk.

THE DIGIT TWIN
Because display_norm leaves digits alone, an officer typing 2.35 would miss
a chunk that says ২.৩৫. So each chunk is indexed as its display form PLUS
the digit-folded form appended. Both spellings are then present in one
vector and one sparse posting list, and neither query form is privileged.

WHY HYBRID AND NOT DENSE ONLY
Dense retrieval treats ২.৩৫ and ২.৪০ as near-identical -- they differ in one
digit and share every other feature. That is exactly the per-insurer premium
trap the gold set is built around: G-102 (২.৩৫% Green Delta vs ২.৪০% Sena)
and G-103 (১.৮০% flat vs ১.৯০%/১.৬০% tiered on an ৮০,০০০ threshold). A dense
index will happily return the wrong insurer's rate with high confidence. So
queries carrying a clause number or a figure weight the sparse side up,
where an exact token match is worth more than semantic similarity.

Local only. FAISS on disk, no embedding API is called.

Usage:
  python embed.py --benchmark              # table only, builds nothing
  python embed.py --build BAAI/bge-m3      # write the index
"""

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from bn_shared import (NUM_RX, canon_pattern, display_norm, fold_digits)
from bnnorm import strip_thousands

# Convention 2: ONE OWNER PER RULE. Gold-ref resolution is validate_gold's --
# it implements the gold file's own FACT KINDS contract and is the tool that
# checks provenance. This module used to carry its own copy, and the two
# diverged: embed's filtered TODO entries BEFORE counting, so [real, TODO]
# read as one unambiguous ref and every unref'd fact silently resolved to the
# real one. G-102's ২.৪০% was thereby attributed to chunk 0007, where it does
# not occur. validate_gold caught it; the copy did not. Importing is what stops
# a second copy drifting again.
#
# Safe direction: validate_gold imports only stdlib, yaml and bn_shared -- it
# does NOT import embed -- and has no import-time side effects.
from validate_gold import fact_kind, fact_text, is_todo, resolve_ref

MODELS = [
    "BAAI/bge-m3",
    "intfloat/multilingual-e5-large",
    "sentence-transformers/LaBSE",
]

CORPUS_FILES = [
    "data/processed/chunks_dabi_2025_final.jsonl",
    "data/processed/chunks_progoti_final.jsonl",
    "data/processed/chunks_livestock_grow_2026_final.jsonl",
]

OUT_DIR = Path("out/index")

# e5 is trained with asymmetric prefixes and degrades measurably without
# them. Applied per model rather than globally, because prefixing BGE-M3 or
# LaBSE would be wrong.
PREFIX = {
    "intfloat/multilingual-e5-large": ("query: ", "passage: "),
}

# A query mentioning a clause number or a figure is a lookup, not a topic
# search. Sparse gets the weight there. Tuned by nothing -- it is two values,
# stated, and the benchmark prints both so the effect is visible.
W_DENSE_TOPIC, W_SPARSE_TOPIC = 0.8, 0.2
W_DENSE_FIGURE, W_SPARSE_FIGURE = 0.4, 0.6

CLAUSE_IN_QUERY_RX = re.compile(r"[০-৯0-9]+\.[০-৯0-9]")

# Memory budget, not performance tuning. This machine has 7.7G of RAM with
# ~1.3G free and BGE-M3 is XLM-R-large (560M params, ~2.2G in fp32); the
# first benchmark attempt died in the Rust tokenizer with "memory allocation
# of 67088977 bytes failed". See load_model() and benchmark_subprocs().
BATCH = 4
MAX_SEQ = 512


def manual_of(c):
    """docx chunks carry `doc`; the PDF/text path carries `manual_id`."""
    return c.get("manual_id") or c.get("doc") or "?"


def load_corpus(files=CORPUS_FILES):
    out = []
    for f in files:
        p = Path(f)
        if not p.exists():
            raise FileNotFoundError(f"corpus file missing: {f}")
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def index_text(c):
    """The string that gets embedded: heading path + body, plus a digit twin.

    Heading path is included because a clause number lives there, not in the
    body -- `৫.৪ গবাদিপ্রাণি সুরক্ষা বিমা` is the only place §5.4 appears.
    """
    head = " > ".join(c.get("heading_path") or [])
    body = c.get("display_text") or c.get("text") or ""
    base = display_norm(f"{head}\n{body}" if head else body)
    folded = fold_digits(base)
    return base if folded == base else f"{base}\n{folded}"


def query_text(q):
    """Same normaliser as the corpus. Also appends the digit twin, so a
    query typed in one digit system reaches a chunk written in the other."""
    base = display_norm(q)
    folded = fold_digits(base)
    return base if folded == base else f"{base} {folded}"


def is_figure_query(q):
    """True when the query names a clause number or any figure."""
    n = display_norm(q)
    return bool(CLAUSE_IN_QUERY_RX.search(n)) or bool(NUM_RX.search(n))


# ------------------------------------------------------------------- sparse
# BM25 over the same normalised text as the dense side. BGE-M3 can emit
# sparse weights natively, but only through FlagEmbedding, which is not
# installed here -- so the sparse side is this, and the deviation is stated
# rather than papered over. It is lexical, local and deterministic, which is
# what the figure case actually needs: an exact ২.৩৫ token match.

_TOK_RX = re.compile(r"[০-৯0-9]+(?:[.,][০-৯0-9]+)*|[ঀ-৿]+|[A-Za-z]+")

# ENGLISH FUNCTION WORDS ONLY. Dropped from both sides of BM25.
#
# Why this is needed at all, and why it is not symmetric with Bengali: idf is
# computed over a Bengali corpus, so an English function word is RARE here
# and therefore scores HIGH. Measured before this list existed, on the G-206
# /207/208 English variants: `to` 7.70, `the` 8.85, `of` 6.96 -- the top doc
# for two of the three was chosen 100% by function words, with 0.6 of the
# fused weight behind it. The rarity that normally makes a token informative
# is an artefact here: `the` says nothing about which clause you want.
#
# THERE IS DELIBERATELY NO BENGALI LIST. Bengali function words are common in
# this corpus, so idf already discounts them -- টাকা and পর্যন্ত are content
# words that carry real figure-clause signal (পর্যন্ত 4.44 on G-207 bn is a
# CORRECT match on "up to"), and a hand-written Bengali list would suppress
# exactly the tokens the figure case depends on. Only the English side has
# the inverted-rarity problem, so only the English side is filtered.
#
# Applied case-insensitively: the tokeniser is case-sensitive, so `Who` and
# `who` are distinct tokens, and a list that only caught one would leak.
EN_STOP = frozenset("""
a an the this that these those
and or but nor so yet
in on at to for of with by from into onto up down over under about
is are was were be been being am
do does did doing done
have has had having
i you he she it we they me him her them us my your his its our their
who whom whose which what when where why how
not no if then than as too very can could should would will shall may
might must there here all any both each few more most other some such
""".split())


def _is_en_stop(tok):
    return tok.lower() in EN_STOP



def tokens(text):
    """Tokenise for BM25, appending a SEPARATOR TWIN for grouped numbers.

    Same append-don't-replace shape as the digit twin in index_text: a token
    written ১,৫০,০০০ also yields ১৫০০০০, so a query in either grouping
    reaches a chunk written in the other. The grouped form is KEPT, so a
    corpus that writes the grouped form is still matchable by a grouped
    query, and an enumeration mis-split can only ever add a spurious token,
    never destroy the original.

    Deliberately placed here rather than in index_text/query_text, for two
    reasons:

      1. Symmetry by construction. This is the one function BM25 calls on
         BOTH documents (__init__) and queries (scores), so index and query
         cannot drift apart -- the single rule bnnorm exists to enforce.
      2. index_text feeds the DENSE encoder, which truncates at MAX_SEQ=512
         tokens. The median doc is already 1795 chars and 534 of 867 exceed
         1400, so appending more surface forms there would push real content
         past truncation. A restated digit string carries no extra semantics
         for LaBSE anyway; an exact token match is the sparse side's job.

    WHY NOT canon(): canon() folds ০-৯ to ASCII irreversibly, which erases
    the Bengali surface form the index deliberately keeps (27/80 -> 0/80, see
    canary_selftest). strip_thousands is digit-system preserving.

    CASE: lowercased on both sides. The corpus writes `who` and a query
    writes `Who`; case-sensitively those are different tokens, so every
    capitalised English content word -- sentence-initial, or an acronym the
    manuals write as UPG / ERP / NFC -- silently missed. Bengali is caseless,
    so .lower() is a no-op on it and no Bengali signal is lost. Applied
    before the stopword test, so `The` is caught as well as `the`.
    """
    out = [t for t in (m.lower() for m in _TOK_RX.findall(display_norm(text)))
           if not _is_en_stop(t)]
    twins = [strip_thousands(t) for t in out if "," in t]
    return out + [t for t in twins if t]


class BM25:
    def __init__(self, docs, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.docs = [tokens(d) for d in docs]
        self.len = [len(d) for d in self.docs]
        self.avg = (sum(self.len) / len(self.len)) if self.len else 0.0
        self.df = Counter()
        for d in self.docs:
            self.df.update(set(d))
        self.n = len(self.docs)
        self.post = defaultdict(list)
        for i, d in enumerate(self.docs):
            for t, f in Counter(d).items():
                self.post[t].append((i, f))

    def idf(self, t):
        df = self.df.get(t, 0)
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5))

    def scores(self, query):
        s = [0.0] * self.n
        for t in set(tokens(query)):
            idf = self.idf(t)
            for i, f in self.post.get(t, ()):
                denom = f + self.k1 * (1 - self.b + self.b *
                                       self.len[i] / max(self.avg, 1e-9))
                s[i] += idf * (f * (self.k1 + 1)) / denom
        return s


# ------------------------------------------------------- Banglish query path
# Romanised Bengali. This is a DELIBERATELY SMALL rule table, not a
# transliteration engine: Banglish is ambiguous (koto/kato, taka/taka/টাকা)
# and a rule table cannot resolve it. It exists so the failure is measurable
# instead of theoretical -- see benchmark output for G-131's romanised
# variant. If it does not work, that is the reported result.
#
# NO PRODUCT, ROLE OR INSURER LITERALS HERE. Only function words and units,
# which are owned by nobody. Anything product-shaped must be matched through
# productmatch, which is what retrieve.py's filters do.
BANGLISH = {
    "koto": "কত", "kato": "কত", "koyta": "কয়টি", "koti": "কয়টি",
    "taka": "টাকা", "tk": "টাকা",
    "porjonto": "পর্যন্ত", "porjontho": "পর্যন্ত",
    "kora": "করা", "korte": "করতে", "kore": "করে",
    "pare": "পারেন", "paren": "পারেন", "pabo": "পাব", "pawa": "পাওয়া",
    "hobe": "হবে", "hoy": "হয়", "hole": "হলে",
    "ki": "কি", "kivabe": "কিভাবে", "kothay": "কোথায়", "keno": "কেন",
    "kon": "কোন", "kar": "কার", "ke": "কে",
    "loan": "লোন", "rin": "ঋণ",
    "bima": "বিমা", "bimar": "বিমার",
    "premium": "প্রিমিয়াম", "mas": "মাস", "bochor": "বছর",
    "din": "দিন", "kisti": "কিস্তি", "sudh": "সুদ", "sud": "সুদ",
    "approve": "অনুমোদন", "onumodon": "অনুমোদন",
    "sima": "সীমা", "siling": "সিলিং", "meyad": "মেয়াদ",
    "sorbocho": "সর্বোচ্চ", "sorbonimno": "সর্বনিম্ন",
    "grahok": "গ্রাহক", "sodosso": "সদস্য", "shakha": "শাখা",
    "gorur": "গরুর", "goru": "গরু", "chagol": "ছাগল",
    "kivabe?": "কিভাবে",
}
_WORD_RX = re.compile(r"[A-Za-z][A-Za-z'?]*")


def expand_banglish(q):
    """Append Bengali glosses for recognised romanised tokens.

    Appends rather than replaces: the Latin form may itself be in the corpus
    (the manuals use English for UPG, ERP, NFC, Muzzle Id), and replacing
    would lose it.
    """
    hits = []
    for m in _WORD_RX.finditer(q):
        w = m.group(0).lower().strip("?'")
        if w in BANGLISH:
            hits.append(BANGLISH[w])
    return (q + " " + " ".join(hits)) if hits else q


# ------------------------------------------------------- gold fact locating

def _dup_expand(cid, by_id):
    """cid plus every chunk EXPLICITLY DECLARED its duplicate by dedupe.py.

    Declared, never inferred. dedupe.py writes dup_members at a similarity
    threshold of exactly 1.000 and documents why 0.95 is unsafe: 35 pairs
    below 1.000 disagree about WHO MAY APPROVE. Re-deriving duplicates here
    by comparing text would reintroduce precisely the implicit text-matching
    this function exists to remove, at a threshold nobody chose.

    NOTE: CORPUS_FILES currently point at *_final.jsonl, which do NOT carry
    dup_* -- the annotation lives in *_dedup.jsonl and was dropped when the
    final files were regenerated. Until dedupe.py is re-run over the final
    corpus this expansion is inert and a ref resolves to exactly one chunk.
    """
    c = by_id.get(cid) or {}
    return {cid} | set(c.get("dup_members") or [])


def gold_scoreability(questions, corpus):
    """-> {question_id: {"status", "reason", "facts", "judged"}}.

    THREE statuses, because there are two different ways to score a fact and
    they must never be mixed into one number:

      "scoreable"   at least one LITERAL fact resolves to a ref naming a real
                    chunk. Countable in recall.
      "judge_only"  no literal fact resolves, but the question carries at
                    least one `claim` fact. Scoreable by an LLM judge, NOT by
                    recall. Must be excluded from any recall denominator --
                    including it would score retrieval on a fact that names
                    no chunk to retrieve.
      "unscoreable" neither. A gold-file problem; `reason` says which.

    `facts` holds only the LOCATABLE facts (literals resolved to chunk sets).
    `judged` holds the claim facts. Keeping them in separate dicts is what
    stops a claim reaching a recall computation by accident: callers that
    union `facts` cannot see `judged` at all.
    """
    by_id = {c["chunk_id"]: c for c in corpus}
    known = set(by_id)
    out = {}
    for q in questions:
        facts, judged, reasons = {}, {}, []
        for f in q.get("gold_facts") or []:
            txt = fact_text(f)
            if is_todo(txt):
                facts[txt] = set()
                reasons.append("fact text is TODO")
                continue
            # An `absent` fact has NO gold chunk, by definition: its evidence
            # is that it appears nowhere in the corpus, and the gold file's own
            # contract says its source_refs point at the clause that SHOULD
            # have carried it and are navigation only. Resolving it to a chunk
            # produced a "gold chunk" the fact provably does not occur in, and
            # any recall computed against it is meaningless. This is an error
            # per fact, not per question: G-217 carries both an absent and a
            # literal fact, and the literal one is still scoreable.
            #
            # NOT left to the answerable != "no" filter in retrieval_eval. That
            # filter hid this rather than preventing it, and it only covers
            # questions whose ANSWERABLE field is "no" -- an absent fact on a
            # partial question would still have resolved silently.
            if fact_kind(f) == "absent":
                facts[txt] = set()
                reasons.append("absent fact has no gold chunk by definition "
                               "(source_refs are navigation only)")
                continue
            # A `claim` is a paraphrase with no literal to quote, and the
            # gold file's contract skips its verbatim check entirely. So a
            # per-fact ref on a claim enforces NOTHING: there is no string to
            # check against the named chunk. Subjecting claims to the
            # ambiguous-provenance rule therefore reported a gold-file error
            # where none existed -- G-221 was called unscoreable, and G-214's
            # claim was called ambiguous, for failing to carry a ref that
            # could not have been checked.
            #
            # Same category error as resolving an `absent` fact to a chunk:
            # applying a literal's rule to a fact kind whose evidence is not a
            # literal. A claim is JUDGED, not located.
            if fact_kind(f) == "claim":
                judged[txt] = str(f.get("ref") or "") if isinstance(f, dict) else ""
                continue
            cid, err = resolve_ref(f, q)
            if err:
                facts[txt] = set()
                reasons.append(err)
                continue
            tgt = _dup_expand(cid, by_id) & known
            if not tgt:
                reasons.append(f"ref names a chunk not in the corpus: {cid}")
            facts[txt] = tgt
        located = any(facts.values())
        if located:
            status, reason = "scoreable", ""
        elif judged:
            status = "judge_only"
            reason = (f"{len(judged)} claim fact(s): judged, not located"
                      + ("; " + "; ".join(dict.fromkeys(reasons)) if reasons else ""))
        else:
            status = "unscoreable"
            reason = "; ".join(dict.fromkeys(reasons)) or "no gold_facts"
        out[q["id"]] = {
            "status": status,
            "reason": reason,
            "facts": facts,
            "judged": judged,
        }
    return out


def locate_gold_chunks(questions, corpus):
    """{question_id: {fact: set(chunk_id)}} -- where each gold fact may be cited from.

    AUTHORITATIVE SOURCE: source_refs.chunk_id, NOT a text match.

    This used to locate a fact by searching the whole corpus for its text and
    returning EVERY chunk that contained it. That was wrong here for a
    measured reason: 516 of 867 chunks (60%) have byte-identical index_text
    across the two manuals, in 258 cross-manual duplicate groups. A question
    carrying `manual_scope: progoti` and `must_cite: ["progoti_2025"]` would
    therefore score a hit on the Dabi twin -- text-match structurally cannot
    resolve a manual-scoped question. It also could not express G-206's own
    requirement that the ref be a nested-table chunk "not a prose chunk that
    happens to mention the figure", and it made recall a function of corpus
    duplication, so re-chunking moved the number silently.

    CLAIM facts are NOT in this mapping at all. A claim has no chunk to
    retrieve, so including it -- even with an empty set -- would put it in
    front of every caller that unions these values, and one of them would
    eventually count it. See gold_scoreability()'s "judged" dict.

    TODO facts are kept with an empty set, NOT skipped. Skipping them dropped
    the question out of the benchmark denominator without saying so, which is
    how G-206/207/208 looked healthy while contributing nothing. An empty set
    means unscoreable and must be reported; see gold_scoreability().
    """
    return {qid: v["facts"]
            for qid, v in gold_scoreability(questions, corpus).items()}


def report_gold_scoreability(questions, corpus, verbose=True):
    """Print the three-way split. -> (n_recall, n_judge_only, n_unscoreable).

    The three counts are printed SEPARATELY and never summed into one
    "scoreable" figure. Only n_recall belongs in a recall denominator.
    """
    st = gold_scoreability(questions, corpus)
    rec = sorted(k for k, v in st.items() if v["status"] == "scoreable")
    jud = sorted(k for k, v in st.items() if v["status"] == "judge_only")
    bad = sorted((k, v["reason"]) for k, v in st.items()
                 if v["status"] == "unscoreable")
    if verbose:
        print(f"gold recall-scoreable ... {len(rec)}/{len(st)}  "
              f"(>=1 literal fact resolved via source_refs.chunk_id)")
        print(f"gold judge-only ......... {len(jud)}/{len(st)}  "
              f"(claim facts only -- NOT in any recall denominator)")
        for qid in jud:
            print(f"  [judge-only]   {qid:<8} {st[qid]['reason']}")
        print(f"gold unscoreable ........ {len(bad)}/{len(st)}")
        for qid, why in bad:
            print(f"  [unscoreable]  {qid:<8} {why}")
    return len(rec), len(jud), len(bad)


# ------------------------------------------------------------------- canary

def canary_selftest(verbose=True):
    """Convention 3. Both canaries are failures observed IN THIS REPO.

    1. canon() erases Bengali digits. Measured 14 Sep 2026: a matcher moved
       from raw to canon'd text went from 27/80 to 0/80 on bare Bengali
       clause numbers. An index normalised with canon() cannot tell ২.৩৫
       from 2.35 and cannot hold ০-৯ at all.
    2. The digit twin. Without it a query typed 2.35 cannot reach a chunk
       written ২.৩৫, which is the ordinary case in these manuals.

    NOT BACKFILLED: there is no canary here for a real RETRIEVAL failure --
    nothing has been embedded yet, so no such failure has been observed. The
    benchmark below is the first opportunity to produce one. Do not invent
    it; a synthetic canary tests what you imagined the bug was.
    """
    from bn_shared import canon
    ok = True

    bn = "২.৩৫% প্রিমিয়াম, ধারা ৫.৪.১"
    dn, cn = display_norm(bn), canon(bn)
    keeps = any("০" <= ch <= "৯" for ch in dn)
    erases = not any("০" <= ch <= "৯" for ch in cn)
    if verbose:
        print(f"  canary digits_survive_norm  display_norm keeps ০-৯={keeps} "
              f"canon erases={erases}  [{'PASS' if keeps and erases else 'FAIL'}]")
    ok &= keeps and erases

    c = {"heading_path": ["৫.৪ শর্ত"], "display_text": "প্রিমিয়াম ২.৩৫%"}
    idx = index_text(c)
    both = "২.৩৫" in idx and "2.35" in idx
    if verbose:
        print(f"  canary digit_twin_present   ২.৩৫ and 2.35 both indexed="
              f"{both}  [{'PASS' if both else 'FAIL'}]")
    ok &= both

    # A figure query must be routed to the sparse-heavy weights, or the
    # premium trap is decided by cosine similarity.
    # Product-free fixtures: Convention 2 forbids a product literal here,
    # and this canary is about digit routing, not product identity.
    fig = is_figure_query("প্রিমিয়াম ২.৩৫% কি?")
    top = is_figure_query("এই সুবিধা কিভাবে কাজ করে?")
    if verbose:
        print(f"  canary figure_query_routing figure={fig} topic={top}  "
              f"[{'PASS' if fig and not top else 'FAIL'}]")
    ok &= fig and not top
    return ok


# ---------------------------------------------------------------- benchmark

def _encode(model, texts, kind, batch=BATCH):
    import numpy as np
    pre = PREFIX.get(getattr(model, "_bfa_name", ""), ("", ""))
    p = pre[0] if kind == "query" else pre[1]
    v = model.encode([p + t for t in texts], batch_size=batch,
                     normalize_embeddings=True, show_progress_bar=False)
    return np.asarray(v, dtype="float32")


def load_model(name):
    """Load one model on CPU, fp32, with a capped sequence length.

    NOT fp16. Measured here: fp16 on CPU is a false economy -- torch emulates
    half-precision matmuls, and the weights still have to be materialised to
    load. fp32 encoded 24 chunks in 33.8s (~20 min for 867); the fp16 attempt
    never finished a pass.

    Memory is handled by process isolation instead, which is the thing that
    actually works. Loading a SECOND model into a process that already held
    one died with "The paging file is too small for this operation to
    complete" (Windows os error 1455) -- see benchmark_subprocs().
    """
    from sentence_transformers import SentenceTransformer
    m = SentenceTransformer(name, device="cpu")
    m.max_seq_length = min(getattr(m, "max_seq_length", 512) or 512, MAX_SEQ)
    m._bfa_name = name
    return m


def rank_hybrid(dense_q, dense_d, sparse_scores, figure):
    """Weighted sum of L2-normalised dense cosine and min-max'd BM25."""
    import numpy as np
    d = dense_d @ dense_q
    s = np.asarray(sparse_scores, dtype="float32")
    if s.max() > s.min():
        s = (s - s.min()) / (s.max() - s.min())
    else:
        s = np.zeros_like(s)
    wd, ws = ((W_DENSE_FIGURE, W_SPARSE_FIGURE) if figure
              else (W_DENSE_TOPIC, W_SPARSE_TOPIC))
    return wd * d + ws * s


BENCH_DIR = Path("out/benchmark")


def _slug(name):
    return name.replace("/", "__")


def benchmark_one(name, corpus, questions, ks=(1, 3, 5, 10)):
    """Recall@k for ONE model. Meant to be run in its own process.

    Caches the document matrix to out/benchmark/<slug>.docs.npy. Encoding 867
    chunks on this CPU takes ~20 minutes per model, and a session that dies
    mid-run should not have to pay it twice -- it already did once.
    """
    import numpy as np

    located = locate_gold_chunks(questions, corpus)
    ids = [c["chunk_id"] for c in corpus]
    docs = [index_text(c) for c in corpus]
    bm25 = BM25(docs)

    scoreable = [q for q in questions
                 if any(located[q["id"]].get(f) for f in located[q["id"]])
                 and str(q.get("answerable")).lower() != "no"]
    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    cache = BENCH_DIR / f"{_slug(name)}.docs.npy"
    m = load_model(name)
    if cache.exists():
        D = np.load(cache)
        if D.shape[0] != len(docs):       # corpus changed under the cache
            D = None
    else:
        D = None
    if D is None:
        D = _encode(m, docs, "passage")
        np.save(cache, D)
    res = {f"dense@{k}": 0 for k in ks}
    res.update({f"hybrid@{k}": 0 for k in ks})
    total = 0
    for q in scoreable:
        gold = set().union(*[v for v in located[q["id"]].values() if v])
        if not gold:
            continue
        total += 1
        qt = query_text(expand_banglish(q["question_bn"]))
        qv = _encode(m, [qt], "query")[0]
        fig = is_figure_query(q["question_bn"])
        for label, sc in (("dense", D @ qv),
                          ("hybrid",
                           rank_hybrid(qv, D, bm25.scores(qt), fig))):
            order = np.argsort(-sc)
            for k in ks:
                if gold & {ids[i] for i in order[:k]}:
                    res[f"{label}@{k}"] += 1
    return {"model": name, "n": total,
            "recall": {k: v / max(total, 1) for k, v in res.items()}}


def benchmark_subprocs(models, gold_paths):
    """Run each model in a fresh process and collect the JSON it prints.

    Not premature engineering: three large models in one process exhausted
    this machine's RAM on the first attempt. A subprocess per model also
    means a model that cannot load reports FAILED without taking the other
    two down with it.
    """
    import subprocess
    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for name in models:
        done = BENCH_DIR / f"{_slug(name)}.json"
        if done.exists():                 # resume: already scored
            rows.append(json.loads(done.read_text(encoding="utf-8")))
            print(f"  [cached] {name}")
            continue
        print(f"  [running] {name} ...", flush=True)
        cmd = [sys.executable, __file__, "--benchmark-one", name,
               "--gold", *gold_paths]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        line = next((l for l in r.stdout.splitlines()
                     if l.startswith("BENCHMARK_JSON ")), None)
        if line:
            row = json.loads(line[len("BENCHMARK_JSON "):])
            done.write_text(json.dumps(row, ensure_ascii=False),
                            encoding="utf-8")
        else:
            err = (r.stderr or r.stdout).strip().splitlines()
            row = {"model": name, "n": 0, "recall": None,
                   "error": (err[-1] if err else "no output")[:90]}
        rows.append(row)
    return rows


def print_benchmark(rows, ks=(1, 3, 5, 10)):
    ns = {r["n"] for r in rows if r.get("recall")}
    tail = f", n={ns.pop()} scoreable questions" if len(ns) == 1 else ""
    print(f"\nMODEL BENCHMARK -- recall@k of gold-fact chunks{tail}")
    print("Nothing is chosen here. Read the table.")
    hdr = f"{'model':<34}{'mode':<8}" + "".join(f"{'@'+str(k):>8}" for k in ks)
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        if not r.get("recall"):
            print(f"{r['model']:<34}{'FAILED':<8}  {r.get('error', '')}")
            continue
        for mode in ("dense", "hybrid"):
            print(f"{r['model']:<34}{mode:<8}"
                  + "".join(f"{r['recall'][mode + '@' + str(k)]:>7.0%} "
                            for k in ks))
    print("\nhybrid weights: topic "
          f"{W_DENSE_TOPIC}/{W_SPARSE_TOPIC}, figure "
          f"{W_DENSE_FIGURE}/{W_SPARSE_FIGURE} (dense/sparse)")


def build(name, corpus, use_cache=True):
    """Write a FAISS index plus the sidecar the retriever needs.

    Reuses out/benchmark/<slug>.docs.npy when it matches the corpus. Encoding
    867 chunks costs ~20 minutes on this CPU and the benchmark has already
    paid it; re-paying it to write an index would be pure waste.
    """
    import faiss
    import numpy as np

    docs = [index_text(c) for c in corpus]
    cache = BENCH_DIR / f"{_slug(name)}.docs.npy"
    D = None
    if use_cache and cache.exists():
        D = np.load(cache)
        if D.shape[0] != len(docs):
            D = None
        else:
            print(f"  reusing cached document matrix {cache}")
    if D is None:
        m = load_model(name)
        D = _encode(m, docs, "passage")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ix = faiss.IndexFlatIP(D.shape[1])
    ix.add(D)
    faiss.write_index(ix, str(OUT_DIR / "dense.faiss"))
    # dup_group travels INTO the index, because the retriever collapses on it
    # and meta.jsonl is what the retriever loads. Leaving it out is how the
    # annotation went missing the first time: computed by dedupe.py, written to
    # a file nobody reads. The producer carries it.
    meta = [{"chunk_id": c["chunk_id"], "manual_id": manual_of(c),
             "dup_group": c.get("dup_group"),
             "product": c.get("product") or [],
             "heading_path": c.get("heading_path") or [],
             "display_text": c.get("display_text") or c.get("text") or "",
             "index_text": t}
            for c, t in zip(corpus, docs)]
    (OUT_DIR / "meta.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in meta),
        encoding="utf-8")
    (OUT_DIR / "model.json").write_text(
        json.dumps({"model": name, "dim": int(D.shape[1]),
                    "normaliser": "display_norm",
                    "digit_twin": True}, ensure_ascii=False),
        encoding="utf-8")
    print(f"index -> {OUT_DIR}  ({len(meta)} chunks, dim {D.shape[1]})")
    return len(meta)


def load_questions(paths):
    import yaml
    qs = []
    for p in paths:
        f = Path(p)
        if not f.exists():
            print(f"  [warn] gold file missing, skipped: {p}", file=sys.stderr)
            continue
        g = yaml.safe_load(f.read_text(encoding="utf-8"))
        qs += g.get("questions") or []
    return qs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", nargs="*",
                    default=["gold/bootstrap.yaml",
                             "gold/livestock_additions.yaml",
                             "gold/bootstrap_livestock_grow.yaml"])
    ap.add_argument("--benchmark", action="store_true")
    ap.add_argument("--benchmark-one", metavar="MODEL",
                    help="internal: score one model, print BENCHMARK_JSON")
    ap.add_argument("--build", metavar="MODEL")
    ap.add_argument("--models", nargs="*", default=MODELS)
    args = ap.parse_args()

    print("CANARY")
    if not canary_selftest():
        print("FATAL: embed failed its own canary. The index would be built "
              "on the wrong normaliser.")
        return 2

    corpus = load_corpus()
    print(f"\ncorpus ......... {len(corpus)} chunks")
    print("  by manual .... " + " / ".join(
        f"{k}:{v}" for k, v in Counter(manual_of(c) for c in corpus).items()))

    questions = load_questions(args.gold)
    print(f"gold ........... {len(questions)} questions")
    report_gold_scoreability(questions, corpus)
    if not questions:
        print("no gold questions found; nothing to benchmark")
        return 1

    if args.benchmark_one:
        res = benchmark_one(args.benchmark_one, corpus, questions)
        print("BENCHMARK_JSON " + json.dumps(res, ensure_ascii=False))
        return 0
    if args.benchmark:
        print_benchmark(benchmark_subprocs(args.models, args.gold))
    if args.build:
        build(args.build, corpus)
    if not (args.benchmark or args.build or args.benchmark_one):
        print("\nnothing to do: pass --benchmark or --build MODEL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
