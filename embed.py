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
import hashlib
import os
import json
import math
import re
import sys
import time
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

# The live index. Overridable so two models' indexes can coexist and be
# switched without re-encoding -- see --out-dir and BFA_INDEX_DIR.
OUT_DIR = Path(os.environ.get("BFA_INDEX_DIR", "out/index"))

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

# PER-MODEL FUSION WEIGHTS (dense share; sparse is the remainder).
# Measured per index, because the right split depends on how good the dense
# side is. Gemini: swept over the 23 gold questions and the 2 Banglish
# variants at 1.0/0.8/0.6 for topic and figure independently, 2026-10-18.
# 1.0/1.0 -- pure dense -- was best at k=5 (21 of 25 against 20 for every
# alternative), best on the 5 figure queries (3 hits against 2), and held
# Banglish at 2/2. It is also the simplest cell in the grid, which is the
# tie-break at n=23: a one-question difference is not evidence.
INDEX_WEIGHTS = {
    "gemini-embedding-001": (1.0, 1.0),
}

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
    # CHANGE 3. Content words the two measured live queries needed and the
    # table did not have. Ordinary vocabulary -- a rate, a profit, a service
    # charge -- owned by nobody, so they belong here rather than in a
    # registry. Still no product, role or insurer name: সাধারণ is "ordinary"
    # and সঞ্চয় is "savings"; the PRODUCT সাধারণ সঞ্চয় is productmatch's and
    # is reached by the registry routing above, not by these two entries.
    "service": "সার্ভিস", "charge": "চার্জ",
    "munafa": "মুনাফা", "har": "হার", "hare": "হারে",
    "sadharon": "সাধারণ", "shadharon": "সাধারণ",
    "shonchoi": "সঞ্চয়", "sonchoy": "সঞ্চয়", "sanchay": "সঞ্চয়",
    "upor": "উপর", "opor": "উপর",
    # `loan` is already above; `lon` is the spelling that the genitive
    # `loner` strips to. Without it CHANGE 2's stemming cannot reach the
    # stem, because the table romanises it "loan" and the inflected form
    # romanises it "lon" -- measured, not assumed.
    "lon": "লোন",
    # THE ONE PRODUCT WORD IN THIS TABLE, AND IT IS DELIBERATE.
    # Everything else here is vocabulary owned by nobody. ওয়াশ is not: it is
    # half of the product ওয়াশ লোন and it is in conventions_check's
    # PRODUCT_WORDS. It is here because routing (ROUTE_REGISTRY_NAMES, off
    # above) bought exactly one thing vocabulary did not cover -- without it
    # `wash loan er meyad koto mas?` and an unrelated role query collapse to
    # the same top-1 chunk. One entry is a smaller price than re-enabling a
    # mechanism that moved Q1 from rank 5 to rank 24.
    #
    # SAFE HERE IN A WAY দাবি WOULD NOT BE. The matching side is the LATIN
    # key "wash"; the Bengali is only ever appended to a query as a gloss,
    # never used as a pattern. And ওয়াশ carries none of the substring hazard
    # that Convention 2 was written for -- দাবি matches inside বিমাদাবি and
    # গতি inside প্রগতি, whereas ওয়াশ is not a fragment of another word in
    # this corpus. A product name needing a real pattern still belongs to
    # productmatch.
    "wash": "ওয়াশ",  # noqa: C2 gloss value, never a matcher; the key is Latin
}
_WORD_RX = re.compile(r"[A-Za-z][A-Za-z'?]*")

# ------------------------------------------- romanised names, from registries
# CHANGE 1. A romanised PRODUCT, PROGRAMME or ROLE can never be glossed by
# BANGLISH above, and that is deliberate: Convention 2 forbids a product
# literal here. The consequence, measured on two live queries, is that
# `progoti loner service charge koto?` reaches BM25 with no corpus vocabulary
# at all and the gold chunk scores 0.0000.
#
# So the names are ROUTED, not copied: every Bengali form and every Latin
# alias below is read out of productmatch, products_catalogue.csv and
# roles.py at call time. Nothing is typed here, and a rename in a registry
# reaches the expansion without anyone editing this file.
#
# AN ALIAS COLUMN IS NOT ALL ALIASES. Measured 20 Sep 2026: 22 of the 43
# catalogue rows carry a Latin-bearing alias, but several of those entries
# are prose notes rather than names --
#     '§5.1.2 excludes its customers from ঋণ নিরাপত্তা বিমা cover'
#     'umbrella for ডিপিএস + মাসিক মুনাফা'
#     'underwrites ফায়ার সেফটি (Progoti §5.4.6) and গবাদিপ্রাণি সুরক্ষা'
# Using those as lookup keys would map the ordinary English words
# "umbrella", "cover", "underwrites" and "excludes" onto product names, and
# inject a product into a query that never mentioned one. That is worse than
# no expansion: a wrong expansion is invisible, where a missing one is only
# unhelpful. _alias_shaped keeps names and drops notes.
# OFF, AND THE MEASUREMENT IS WHY. Routing romanised registry names into the
# query was built, measured on the two live Banglish failures, and not
# adopted. Gold-chunk BM25 on today's index:
#
#   Q1 progoti_0016_p2   baseline 0.0000 rank 11  ->  routing 3.8217 rank 24
#   Q2 dabi_2025_0017    baseline 0.0000 rank  9  ->  routing 0.0000 rank  9
#
# Q1's SCORE rose and its RANK got worse, which is the whole finding. The
# query gained প্রগতি, and প্রগতি appears in 158 of 867 chunks because one of
# the two manuals is entirely about that programme. A term carried by 18% of
# the corpus is not evidence about which chunk answers the question; it just
# lifts every chunk in that manual past the one that does. Q2 gained nothing
# at all -- it names no product, programme or role.
#
# KEPT, NOT DELETED, because the argument against it is specific: a
# programme name that is a whole manual's subject. A product name that is
# NOT -- ওয়াশ লোন, আরোগ্য ঋণ, a named insurer -- is rare in the corpus and
# would behave the opposite way. Turn this on to test that, and re-measure
# rank rather than score.
ROUTE_REGISTRY_NAMES = False

_ALIAS_MAX_WORDS = 4


def _alias_shaped(s):
    """Is this alias field a NAME, or a note someone wrote in the column?"""
    s = (s or "").strip()
    if not s or not re.search(r"[A-Za-z]", s):
        return False
    if "§" in s or "(" in s or ")" in s:
        return False
    return len(s.split()) <= _ALIAS_MAX_WORDS


def _role_latin(name, bengali):
    """[(latin key, bengali)] for ONE English role name, plus its initials.

    The initials are DERIVED, not typed -- "Branch Manager" gives BM. That is
    how officers write these, and it keeps roles.py the owner of the name.
    """
    name = re.sub(r"\(.*?\)", "", name).strip()
    if not name:
        return []
    out = [(name.lower(), bengali)]
    initials = "".join(w[0] for w in name.split() if w)
    if len(initials) >= 2:
        out.append((initials.lower(), bengali))
    return out


# CHANGE 2. Bengali case endings, carried into the romanisation. `loner` is
# loan + the genitive -er; the table holds stems, so every inflected form
# misses. Longest suffix first, and a stripped form is used ONLY when it is
# already a key -- never a guess, so a word that strips to nothing known is
# left exactly as it was.
_CASE_SUFFIXES = ("tir", "der", "er", "ta", "r", "e")


def _stems(w):
    """[w, then each stripping that might be a key]. Longest suffix first."""
    out = [w]
    for suf in _CASE_SUFFIXES:
        if len(w) > len(suf) + 1 and w.endswith(suf):
            out.append(w[: -len(suf)])
    return out


def _lookup(word, gloss):
    """Bengali for one romanised word, via the registries then the table,
    trying the word itself before any stripped form."""
    for cand in _stems(word):
        if cand in gloss:
            return gloss[cand]
        if cand in BANGLISH:
            return BANGLISH[cand]
    return None


_REG_GLOSS = None


def registry_glossary():
    """{lowercased latin name: bengali form}. Built once, from the registries.

    Convention 1: built on first call, never at import.
    """
    global _REG_GLOSS
    if _REG_GLOSS is not None:
        return _REG_GLOSS
    import csv
    from pathlib import Path as _P
    gloss = {}

    def add(latin, bengali):
        if latin and bengali and _alias_shaped(latin):
            gloss.setdefault(latin.strip().lower(), bengali.strip())

    import productmatch as pm
    for key, name_bn, alias, _pat in pm.NAMED:
        for a in (alias or "").split(";"):
            add(a, name_bn)
    for key, _t, name_bn, alias, _pat in pm.ENTITIES:
        for a in (alias or "").split(";"):
            add(a, name_bn)
    # The catalogue carries the PROGRAMMES' Latin aliases -- Dabi, Progoti,
    # BCUP -- which productmatch holds only as patterns, so it is the one
    # source that covers a romanised programme name.
    cat = _P("products_catalogue.csv")
    if cat.exists():
        for r in csv.DictReader(cat.open(encoding="utf-8-sig")):
            for a in (r.get("aliases") or "").split(";"):
                add(a, r.get("name_bn"))
    # ROLES: (key, dabi_name, progoti_name, abbrev, english, note).
    #
    # সিডিও AND পিও ARE NEVER MERGED. roles.py records them as one role named
    # differently per manual, and states that the equivalence is metadata and
    # never query expansion -- the term tells you which manual the asker works
    # under, so expanding one to the other would erase the thing it carries.
    # Where the English field names both variants they are separated by "/"
    # in manual order (Dabi first), so each variant is paired with ITS OWN
    # Bengali form. Explicitly, not by relying on list order.
    import roles
    for row in roles.ROLES:
        dabi_bn, progoti_bn, english = row[1], row[2], row[4]
        parts = [p for p in (english or "").split("/")
                 if re.sub(r"\(.*?\)", "", p).strip()]
        bns = [dabi_bn, progoti_bn] if len(parts) > 1 else [dabi_bn]
        for part, bn in zip(parts, bns):
            for latin, b in _role_latin(part, bn):
                add(latin, b)
    _REG_GLOSS = gloss
    return gloss


def expand_banglish(q):
    """Append Bengali glosses for recognised romanised tokens.

    Appends rather than replaces: the Latin form may itself be in the corpus
    (the manuals use English for UPG, ERP, NFC, Muzzle Id), and replacing
    would lose it.
    """
    hits = []
    toks = [m.group(0) for m in _WORD_RX.finditer(q)]
    words = [t.lower().strip("?'") for t in toks]

    # CHANGE 1: registry names first, longest phrase first, so "goti loan"
    # wins over "loan" and a multi-word product name is not shredded into
    # two unrelated glosses. Bengali forms come from productmatch, the
    # catalogue and roles -- see registry_glossary.
    gloss = registry_glossary() if ROUTE_REGISTRY_NAMES else {}
    used = [False] * len(words)
    for size in (4, 3, 2, 1):
        for i in range(len(words) - size + 1):
            if any(used[i:i + size]):
                continue
            phrase = " ".join(words[i:i + size])
            if phrase in gloss:
                hits.append(gloss[phrase])
                for j in range(i, i + size):
                    used[j] = True

    for i, w in enumerate(words):
        if used[i]:
            continue
        bn = _lookup(w, gloss)          # CHANGE 2: stems tried after the word
        if bn:
            hits.append(bn)
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

def canary_selftest(verbose=True, corpus=None):
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

    if corpus is not None:
        ok &= cache_guard_canary(corpus, verbose=verbose)
    return ok


def cache_guard_canary(corpus, verbose=True):
    """Convention 3, from the near-miss of 2026-09-25 -- not an invented one.

    The fixture is the real thing: the live corpus, with ONE chunk's sha put
    back to the value the manifest held before the R1 heading fix. Chunk
    count identical on both sides, which is exactly why the old count guard
    would have waved it through.

    Four conditions, and the positive control is not optional. A guard that
    refuses every cache is as broken as one that accepts every cache, and
    only the second kind gets noticed -- the first just looks slow.

      1. the unmodified fingerprint is ACCEPTED        (positive control)
      2. the observed pre-R1 sha is REFUSED            (the real failure)
      3. the refusal NAMES the chunk                   (a count is not enough)
      4. a changed chunk count is still REFUSED        (keep the old power)

    Condition 0, checked first: the fixture must actually differ from
    today's value. If R1 were ever reverted the two would coincide, the
    canary would go green on a no-op, and it would be testing nothing.
    """
    cid, old_sha = CACHE_NEAR_MISS
    docs = [index_text(c) for c in corpus]
    want = cache_fingerprint(corpus, docs)
    if cid not in want["chunk_ids"]:
        if verbose:
            print(f"  canary cache_guard       CANNOT RUN -- {cid} is not in "
                  f"this corpus, so the fixture is not the observed failure "
                  f"[FAIL]")
        return False
    i = want["chunk_ids"].index(cid)
    if want["sha256"][i] == old_sha:
        if verbose:
            print(f"  canary cache_guard       CANNOT RUN -- {cid} still has "
                  f"its pre-R1 sha, so the fixture tests nothing  [FAIL]")
        return False

    accept, _, _ = cache_diff(dict(want), want)
    stale = dict(want, sha256=list(want["sha256"]))
    stale["sha256"][i] = old_sha
    refuse, why, moved = cache_diff(stale, want)
    named = cid in moved
    short = dict(want, chunk_ids=want["chunk_ids"][:-1],
                 sha256=want["sha256"][:-1])
    refuse_count, _, _ = cache_diff(short, want)

    good = accept and not refuse and named and not refuse_count
    if verbose:
        print(f"  canary cache_guard       same count ({len(want['chunk_ids'])}"
              f"=={len(stale['chunk_ids'])}), 1 sha differs -> "
              f"{'REFUSED' if not refuse else 'ACCEPTED'} ({why}); "
              f"names {cid}={named}; clean cache accepted={accept}; "
              f"short cache refused={not refuse_count}  "
              f"[{'DETECTED' if good else 'MISSED'}]")
    return good


# ---------------------------------------------------------------- benchmark

def _encode(model, texts, kind, batch=BATCH):
    import numpy as np
    # An API model carries its own batching, pacing and task types.
    if hasattr(model, "encode_task"):
        return model.encode_task(texts, kind)
    pre = PREFIX.get(getattr(model, "_bfa_name", ""), ("", ""))
    p = pre[0] if kind == "query" else pre[1]
    v = model.encode([p + t for t in texts], batch_size=batch,
                     normalize_embeddings=True, show_progress_bar=False)
    return np.asarray(v, dtype="float32")


# ------------------------------------------------------- the Gemini backend
# WHY A HOSTED EMBEDDER AT ALL. This machine has 7.7G and cannot hold the
# toolchain and an embedding model at once (see the journal, 4f): LaBSE needs
# ~2G to load and the first index rebuild of 2026-09-25 died in
# materialize_tensors with MemoryError, succeeding only on a retry with
# nothing else running. An API embedder moves that cost off the box entirely
# and lets the app start without torch.
#
# IT IS A DROP-IN, NOT A SECOND PIPELINE. load_model returns an object with
# the one method _encode needs, so build(), benchmark_one() and the retriever
# are untouched and the two models stay comparable by construction. A second
# encode path is a second thing to drift.
GEMINI_EMBED_MODEL = "gemini-embedding-001"
GEMINI_DIM = 768
# RETRIEVAL_DOCUMENT and RETRIEVAL_QUERY are an ASYMMETRIC pair: the same
# text embeds differently depending on which side it is on, which is the
# point of them. Getting them the wrong way round does not error, it just
# retrieves worse -- so the canary asserts the pair is actually asymmetric.
GEMINI_TASK = {"query": "RETRIEVAL_QUERY", "passage": "RETRIEVAL_DOCUMENT"}
# Batch by CHARACTERS, not by item count. The free tier's binding limit is
# tokens per minute, not requests: a batch of 32 real chunks (~60k chars)
# returns 429 RESOURCE_EXHAUSTED while a batch of 8 (~14k) succeeds, and a
# fixed item count would fail on long chunks and waste quota on short ones.
GEMINI_CHARS_PER_REQ = 9_000
GEMINI_PAUSE = 2.5          # keeps a long build under 100 requests/minute

# THE DAILY QUOTA, AND THE UNIT IT COUNTS.
#
#   quotaId   EmbedContentRequestsPerDayPerProjectPerModel-FreeTier
#   value     1000
#
# IT COUNTS CONTENTS, NOT HTTP CALLS. Measured the hard way on 2026-10-18:
# the corpus build made 191 HTTP requests carrying 867 chunks, roughly 260
# HTTP calls were made all day, and the 1000 quota was still exhausted. The
# only reading consistent with that is that each item in a batch counts.
#
# So batching buys fewer round trips and less per-minute token pressure --
# it buys NOTHING against the daily cap. A ledger counting HTTP calls would
# have read 260 against a limit of 1000, declared 74% headroom, and refused
# nothing on the day the quota ran out.
#
# Both numbers are recorded. `items` is what the preflight spends, because
# over-counting fails safe; `requests` is kept so that one experiment after
# a reset can settle it, rather than another day of inference.
EMBED_RPD = {GEMINI_EMBED_MODEL: 1000}
EMBED_WARN_AT = 0.80
EMBED_LEDGER_KEY = "gemini-embed"


def ledger_read_embed(path=None):
    """Today's embedding spend. -> {"items": int, "requests": int}

    SAME FILE as the Groq token ledger (out/token_ledger.json), different
    key and a different unit. The key is namespaced and the unit is named
    in the stored object so nobody can add tokens to requests and get a
    number that looks like either.
    """
    from generate import LEDGER, ledger_read, _today
    doc = ledger_read(path or LEDGER)
    day = doc.get(_today()) or {}
    rec = day.get(EMBED_LEDGER_KEY) or {}
    if not isinstance(rec, dict):
        return {"items": 0, "requests": 0}
    return {"items": int(rec.get("items", 0)),
            "requests": int(rec.get("requests", 0))}


def ledger_add_embed(model, items, requests=1, path=None):
    """Record one call. Never raises: a ledger write must not lose an
    embedding that was already paid for."""
    import json as _json
    from generate import LEDGER, ledger_read, _today
    try:
        p = Path(path or LEDGER)
        doc = ledger_read(p)
        day = doc.setdefault(_today(), {})
        rec = day.get(EMBED_LEDGER_KEY)
        if not isinstance(rec, dict):
            rec = {"items": 0, "requests": 0, "unit": "contents embedded; "
                   "the daily quota counts these, not HTTP calls"}
        rec["items"] = int(rec.get("items", 0)) + int(items)
        rec["requests"] = int(rec.get("requests", 0)) + int(requests)
        rec["model"] = model
        day[EMBED_LEDGER_KEY] = rec
        for old in sorted(doc)[:-14]:
            doc.pop(old, None)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_json.dumps(doc, ensure_ascii=False, indent=1) + "\n",
                     encoding="utf-8")
    except Exception:                                    # noqa: BLE001
        pass


def embed_preflight(model, n_items, out=print):
    """-> (ok, message). Refuse a batch that cannot finish inside today.

    WHY REFUSE RATHER THAN LET THE 429 DECIDE. A build that dies two thirds
    through has spent two thirds of a day's quota and written no index --
    and because the quota is daily, the retry is not in a minute, it is
    tomorrow. The demo is on this index, so exhausting it takes the app down
    until the reset. Better to not start.

    The ledger is a LOWER BOUND on what the provider has counted: it sees
    only calls made through this process. So it refuses only when what it
    has already recorded is by itself enough to blow the limit -- it will
    never refuse a run the provider would have allowed.
    """
    limit = EMBED_RPD.get(model)
    if not limit:
        return True, "no daily quota recorded for %r" % model
    used = ledger_read_embed()["items"]
    left = limit - used
    pct = used / limit
    msg = ("embedding quota %s -- used %d/%d (%.0f%%) today, %d left; "
           "this run needs %d" % (model, used, limit, pct * 100, left, n_items))
    if n_items > left:
        return False, (msg + "\n  REFUSING TO START. The quota is DAILY: a "
                       "run that exhausts it takes the demo down until the "
                       "reset, and a partial build writes no index at all. "
                       "Wait for the reset, or build against a smaller "
                       "corpus.")
    if pct >= EMBED_WARN_AT:
        out("  WARNING: " + msg)
    else:
        out("  " + msg)
    return True, msg


class GeminiEmbedder:
    """Duck-typed stand-in for a SentenceTransformer, API-backed."""

    # RETRY BUDGET IS A UX DECISION, NOT A NETWORKING ONE.
    #
    # The build is a batch job and can afford to wait out a per-minute cap:
    # six attempts on a 1.8x ladder from 20s is about 7.5 minutes, and a
    # build that pauses beats a build that has to start again.
    #
    # A QUERY CANNOT. The same ladder in front of a person asking a question
    # means 7.5 minutes of a spinner before the Bengali "try again later"
    # appears -- measured here by a probe that outran its own timeout and
    # printed nothing at all. Worse, a DAILY quota will not recover inside
    # any ladder, so every one of those waits is spent on an outcome that
    # was already decided. Interactive callers fail fast; build() opts in.
    ATTEMPTS_INTERACTIVE = 2
    ATTEMPTS_BATCH = 6

    def __init__(self, name=GEMINI_EMBED_MODEL, dim=GEMINI_DIM, key=None,
                 max_attempts=None):
        from google.genai import Client
        from generate import load_key
        self._bfa_name = name
        self.name, self.dim = name, dim
        self.client = Client(api_key=key or load_key("gemini"))
        self.requests = 0
        self.max_attempts = max_attempts or self.ATTEMPTS_INTERACTIVE

    def _batches(self, texts):
        cur, n = [], 0
        for t in texts:
            if cur and n + len(t) > GEMINI_CHARS_PER_REQ:
                yield cur
                cur, n = [], 0
            cur.append(t)
            n += len(t)
        if cur:
            yield cur

    def _one(self, batch, task):
        from google.genai import types
        cfg = types.EmbedContentConfig(task_type=task,
                                       output_dimensionality=self.dim)
        # Free-tier 429s are per-minute, so the first wait has to be a
        # minute-scale wait. 2s was optimistic and just burned attempts.
        delay = 20.0
        for attempt in range(self.max_attempts):
            try:
                r = self.client.models.embed_content(
                    model=self.name, contents=batch, config=cfg)
                self.requests += 1
                ledger_add_embed(self.name, items=len(batch), requests=1)
                return [e.values for e in r.embeddings]
            except Exception as exc:                      # noqa: BLE001
                # generate._status_of owns "where does this provider put the
                # status code"; providers disagree and a second copy would
                # drift from it.
                from generate import _status_of
                status = _status_of(exc)
                last = attempt == self.max_attempts - 1
                if status != 429 or last:
                    raise EmbeddingUnavailable(
                        f"{self.name}: {type(exc).__name__}: "
                        f"{str(exc)[:200]}") from exc
                time.sleep(delay)
                delay *= 1.8
        raise EmbeddingUnavailable(f"{self.name}: retries exhausted")

    def encode_task(self, texts, kind):
        """-> float32 (n, dim), L2-NORMALISED.

        THE NORMALISATION IS NOT OPTIONAL AND IS NOT AUTOMATIC. Only the
        full 3072-dimension output of gemini-embedding-001 comes back unit
        length; a truncated 768 arrives at ~0.59 and Google documents that
        the caller must re-normalise. Everything downstream here assumes
        unit vectors -- faiss.IndexFlatIP IS cosine only for unit vectors,
        rank_hybrid calls its dense term a cosine, and the abstention floor
        is a number on that scale. Skipping this does not raise; it quietly
        rescales every score and moves the floor out from under itself.
        """
        import numpy as np
        task = GEMINI_TASK["query" if kind == "query" else "passage"]
        out = []
        batches = list(self._batches(list(texts)))
        for i, b in enumerate(batches):
            out += self._one(b, task)
            if len(batches) > 4 and (i % 10 == 0 or i == len(batches) - 1):
                print(f"    embedded {len(out)}/{len(texts)} "
                      f"({i + 1}/{len(batches)} requests)", flush=True)
            if i < len(batches) - 1:
                time.sleep(GEMINI_PAUSE)
        V = np.asarray(out, dtype="float32")
        n = np.linalg.norm(V, axis=1, keepdims=True)
        return V / np.maximum(n, 1e-12)

    # sentence-transformers compatibility: build()/benchmark never call this
    # with anything but the two kinds above, but a bare .encode must not
    # silently embed a query as a passage.
    def encode(self, texts, **kw):
        raise NotImplementedError(
            "GeminiEmbedder needs the task type: call encode_task(texts, "
            "'query'|'passage'). An untyped embed would put a question on "
            "the document side of an asymmetric model.")


class EmbeddingUnavailable(RuntimeError):
    """The hosted embedder could not be reached, or the quota is spent.

    Carried as its own type so the app can show one plain Bengali sentence
    instead of a traceback -- see app.py. Retrieval without an embedder is
    not a degraded answer, it is no answer, and the difference has to reach
    the reader as a sentence rather than as a stack.
    """


def gemini_canary(verbose=True, model=None):
    """Convention 3, for the two ways this backend fails without raising.

    1. NORMALISATION. A truncated 768-dim vector arrives at ~0.59 length.
       Used unnormalised it still ranks, still returns five chunks, and
       still produces a float the floor is compared against -- it is simply
       on a different scale. Asserted both ways: raw is NOT unit, and what
       encode_task returns IS.
    2. TASK ASYMMETRY. RETRIEVAL_QUERY and RETRIEVAL_DOCUMENT must produce
       DIFFERENT vectors for the same string. If a future SDK silently
       ignored task_type, the pair would collapse to one embedding and the
       asymmetry the model is trained for would be gone -- with no error and
       a small, plausible recall loss.

    Costs three API calls. Run only on the gemini path.
    """
    import numpy as np
    from google.genai import types
    m = model or load_model(GEMINI_EMBED_MODEL)
    probe = "ঋণ নিরাপত্তা বিমার প্রিমিয়াম কত?"
    raw = m.client.models.embed_content(
        model=m.name, contents=[probe],
        config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT",
                                        output_dimensionality=m.dim))
    rawn = float(np.linalg.norm(np.asarray(raw.embeddings[0].values)))
    not_unit = abs(rawn - 1.0) > 0.01
    unit = abs(float(np.linalg.norm(m.encode_task([probe], "passage")[0])) - 1.0) < 1e-4
    q = m.encode_task([probe], "query")[0]
    d = m.encode_task([probe], "passage")[0]
    asym = float(np.dot(q, d)) < 0.999
    ok = not_unit and unit and asym
    if verbose:
        print(f"  canary gemini_normalise  raw 768-dim norm={rawn:.3f} "
              f"(not unit={not_unit}); after encode_task unit={unit}  "
              f"[{'DETECTED' if not_unit and unit else 'MISSED'}]")
        print(f"  canary gemini_task_types query vs passage cos={float(np.dot(q, d)):.4f} "
              f"-- asymmetric={asym}  [{'PASS' if asym else 'FAIL'}]")
    return ok


def is_api_model(name):
    return (name or "").startswith("gemini-")


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
    if is_api_model(name):
        return GeminiEmbedder(name)
    from sentence_transformers import SentenceTransformer
    m = SentenceTransformer(name, device="cpu")
    m.max_seq_length = min(getattr(m, "max_seq_length", 512) or 512, MAX_SEQ)
    m._bfa_name = name
    return m


def rank_hybrid(dense_q, dense_d, sparse_scores, figure, weights=None):
    """Weighted sum of L2-normalised dense cosine and min-max'd BM25.

    THE WEIGHTS BELONG TO THE INDEX, NOT TO THIS MODULE. They were tuned for
    LaBSE, whose dense side barely worked -- dense recall@5 0.062 -- so BM25
    had to carry the retrieval and 0.4/0.6 on figure queries was correct FOR
    THAT MODEL. On a strong dense model the same numbers spend recall
    defending a weakness that is not there. A single global pair would mean
    switching BFA_INDEX_DIR back to LaBSE silently applied the new model's
    weights to the old model's vectors, which is a worse failure than either
    setting alone: nothing would raise, and retrieval would just get worse.

    `weights` is (w_dense_topic, w_dense_figure) from the index's model.json.
    An index that does not carry them -- every index built before this --
    falls back to the module constants, so their behaviour is unchanged.
    """
    import numpy as np
    d = dense_d @ dense_q
    s = np.asarray(sparse_scores, dtype="float32")
    if s.max() > s.min():
        s = (s - s.min()) / (s.max() - s.min())
    else:
        s = np.zeros_like(s)
    if weights:
        wd = weights[1] if figure else weights[0]
        ws = 1.0 - wd
    else:
        wd, ws = ((W_DENSE_FIGURE, W_SPARSE_FIGURE) if figure
                  else (W_DENSE_TOPIC, W_SPARSE_TOPIC))
    return wd * d + ws * s


BENCH_DIR = Path("out/benchmark")


def _slug(name):
    return name.replace("/", "__")


# --------------------------------------------- the document-matrix cache
#
# INSTANCE 17, AND THE MOST DANGEROUS SO FAR. Until 2026-09-25 the cache was
# guarded by one line:
#
#     if D.shape[0] != len(docs):       # corpus changed under the cache
#
# -- a COUNT. On 2026-09-25 the R1 heading fix rewrote 80 of 867 chunks and
# left the count at 867 on both sides. 867 == 867, so the guard would have
# passed, build() would have reused the old matrix, and the "rebuilt" index
# would have carried PRE-FIX vectors for those 80 chunks while reporting
# success. Nothing would have raised, and retrieval would have been quietly
# scored against text that no longer existed. It was caught by hand before
# the rebuild, not by any check.
#
# THE SHAPE, which is the part worth remembering: a guard that tests the
# CHEAP property (how many) and passes while the EXPENSIVE one (which
# content) is wrong. Same family as the duplicate-crowding metric that
# counted distinct chunk_ids when twins always have different ids, and as
# every other entry on this list.
#
# So the cache is keyed on CONTENT:
#   - sha256 of each chunk's display_text, per chunk. By construction this is
#     the value dedupe.py records in the manifest, because it is the same
#     function on the same input -- imported, not reimplemented, so the two
#     cannot drift.
#   - one digest over the exact strings handed to the encoder, which also
#     catches a change in display_norm or the digit twin that leaves
#     display_text untouched. The per-chunk shas cannot see that; they are
#     about the corpus, and this is about the encoder input.
#
# A refusal NAMES THE CHUNKS. "cache refused" with no list is how you end up
# deleting the cache to make the message go away.
CACHE_VERSION = 2   # 1 was the count-only guard and wrote no fingerprint

# The real near-miss, for the canary. dabi_2025_0034 is one of the 80 chunks
# R1 changed; this is its sha as the manifest recorded it BEFORE that run.
CACHE_NEAR_MISS = (
    "dabi_2025_0034",
    "8e79af4509043824a3f552d0c0da83eb857d574e5c69d8735da596c671999663",
)


def _cache_meta(cache):
    return cache.with_suffix(".meta.json")


def cache_fingerprint(corpus, docs):
    """What the matrix on disk has to still be true of to be reusable."""
    from dedupe import manifest_sha   # one definition of the chunk sha
    h = hashlib.sha256()
    for t in docs:
        h.update(t.encode("utf-8"))
        h.update(b"\x00")
    return {"version": CACHE_VERSION,
            "chunk_ids": [c["chunk_id"] for c in corpus],
            "sha256": [manifest_sha(c.get("display_text") or c.get("text") or "")
                       for c in corpus],
            "docs_sha": h.hexdigest()}


def cache_diff(have, want):
    """-> (reusable, why, [chunk_ids that moved]). Never 'probably fine'."""
    if not have:
        return False, "no fingerprint beside the matrix (written by an older build)", []
    if have.get("version") != want["version"]:
        return (False, "fingerprint version %s, need %s"
                % (have.get("version"), want["version"]), [])
    hid = list(have.get("chunk_ids") or [])
    wid = want["chunk_ids"]
    hsha = dict(zip(hid, have.get("sha256") or []))
    wsha = dict(zip(wid, want["sha256"]))
    changed = [i for i in wid if i in hsha and hsha[i] != wsha[i]]
    added = [i for i in wid if i not in hsha]
    removed = [i for i in hid if i not in wsha]
    if changed or added or removed:
        bits = []
        if changed:
            bits.append("%d chunk(s) changed" % len(changed))
        if added:
            bits.append("%d added" % len(added))
        if removed:
            bits.append("%d removed" % len(removed))
        return False, ", ".join(bits), changed + added + removed
    if len(hid) != len(wid):
        return False, "chunk count %d, need %d" % (len(hid), len(wid)), []
    if have.get("docs_sha") != want["docs_sha"]:
        return False, ("every chunk sha matches but the encoder input digest "
                       "differs -- the normaliser or the digit twin changed"), []
    return True, "content identical", []


def cache_load(cache, corpus, docs, verbose=True):
    """The cached matrix, or None with a reason printed. Never a guess."""
    import numpy as np
    if not cache.exists():
        return None
    meta = _cache_meta(cache)
    have = None
    if meta.exists():
        try:
            have = json.loads(meta.read_text(encoding="utf-8"))
        except ValueError:
            have = None
    ok, why, moved = cache_diff(have, cache_fingerprint(corpus, docs))
    if not ok:
        if verbose:
            print(f"  cache REFUSED  {cache.name}: {why}")
            for cid in moved[:12]:
                print(f"    moved: {cid}")
            if len(moved) > 12:
                print(f"    ... and {len(moved) - 12} more")
            print(f"  re-encoding all {len(docs)} chunks")
        return None
    D = np.load(cache)
    if D.shape[0] != len(docs):
        # The fingerprint agreed and the matrix did not. That is the two
        # files being out of step with each other, which is worse than
        # either one being stale, so it is never reused.
        if verbose:
            print(f"  cache REFUSED  {cache.name}: fingerprint matches but the "
                  f"matrix holds {D.shape[0]} rows for {len(docs)} chunks")
        return None
    if verbose:
        print(f"  cache accepted {cache.name}: {len(docs)} chunks, content identical")
    return D


def cache_save(cache, D, corpus, docs):
    import numpy as np
    np.save(cache, D)
    _cache_meta(cache).write_text(
        json.dumps(cache_fingerprint(corpus, docs), ensure_ascii=False),
        encoding="utf-8")


def benchmark_one(name, corpus, questions, ks=(1, 3, 5, 10, 30)):
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
    D = cache_load(cache, corpus, docs)
    if D is None:
        D = _encode(m, docs, "passage")
        cache_save(cache, D, corpus, docs)
    res = {f"dense@{k}": 0 for k in ks}
    res.update({f"hybrid@{k}": 0 for k in ks})
    total = 0
    per_q = {}
    for q in scoreable:
        gold = set().union(*[v for v in located[q["id"]].values() if v])
        if not gold:
            continue
        total += 1
        qt = query_text(expand_banglish(q["question_bn"]))
        qv = _encode(m, [qt], "query")[0]
        fig = is_figure_query(q["question_bn"])
        row = {"n_gold": len(gold), "figure_query": bool(fig)}
        for label, sc in (("dense", D @ qv),
                          ("hybrid",
                           rank_hybrid(qv, D, bm25.scores(qt), fig))):
            order = np.argsort(-sc)
            for k in ks:
                if gold & {ids[i] for i in order[:k]}:
                    res[f"{label}@{k}"] += 1
            # Rank of the FIRST gold chunk over the whole corpus. Not capped
            # at max(ks): a capped rank cannot tell 31 from 800, and the
            # gap between those two is the whole question about dense.
            row[f"{label}_rank"] = next(
                (i for i, j in enumerate(order, 1) if ids[j] in gold), None)
        per_q[q["id"]] = row
    return {"model": name, "n": total, "ks": list(ks),
            "recall": {k: v / max(total, 1) for k, v in res.items()},
            "hits": dict(res), "per_question": per_q}


def benchmark_subprocs(models, gold_paths, ks=(1, 3, 5, 10, 30)):
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
            cached = json.loads(done.read_text(encoding="utf-8"))
            # A cache written before --ks existed answers a DIFFERENT
            # question: fewer ks, no per-question ranks, and possibly a
            # different gold set. Reusing it would print a stale number
            # under a new heading, which is exactly the failure this repo
            # keeps recording. Re-run instead of reusing silently.
            if (cached.get("ks") == list(ks)
                    and cached.get("per_question") is not None):
                rows.append(cached)
                print(f"  [cached] {name}")
                continue
            print(f"  [stale cache, re-running] {name}", flush=True)
        print(f"  [running] {name} ...", flush=True)
        cmd = [sys.executable, __file__, "--benchmark-one", name,
               "--gold", *gold_paths,
               "--ks", *[str(k) for k in ks]]
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


def print_benchmark(rows, ks=(1, 3, 5, 10, 30)):
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


def build(name, corpus, use_cache=True, out_dir=None):
    """Write a FAISS index plus the sidecar the retriever needs.

    Reuses out/benchmark/<slug>.docs.npy when it matches the corpus. Encoding
    867 chunks costs ~20 minutes on this CPU and the benchmark has already
    paid it; re-paying it to write an index would be pure waste.
    """
    import faiss
    import numpy as np

    docs = [index_text(c) for c in corpus]
    cache = BENCH_DIR / f"{_slug(name)}.docs.npy"
    D = cache_load(cache, corpus, docs) if use_cache else None
    if D is None:
        m = load_model(name)
        if hasattr(m, "max_attempts"):
            m.max_attempts = m.ATTEMPTS_BATCH   # a build can afford to wait
            ok, msg = embed_preflight(name, len(docs))
            if not ok:
                raise EmbeddingUnavailable(msg)
        D = _encode(m, docs, "passage")
    out = Path(out_dir) if out_dir else OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    ix = faiss.IndexFlatIP(D.shape[1])
    ix.add(D)
    faiss.write_index(ix, str(out / "dense.faiss"))
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
    (out / "meta.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in meta),
        encoding="utf-8")
    wt, wf = INDEX_WEIGHTS.get(name, (W_DENSE_TOPIC, W_DENSE_FIGURE))
    (out / "model.json").write_text(
        json.dumps({"model": name, "dim": int(D.shape[1]),
                    "normaliser": "display_norm",
                    "digit_twin": True,
                    # Recorded so the retriever uses the weights this index
                    # was measured at, whatever the module defaults become.
                    "w_dense_topic": wt, "w_dense_figure": wf},
                   ensure_ascii=False),
        encoding="utf-8")
    print(f"index -> {out}  ({len(meta)} chunks, dim {D.shape[1]})")
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
    ap.add_argument("--ks", nargs="*", type=int, default=[1, 3, 5, 10, 30],
                    help="recall cut-offs for --benchmark")
    ap.add_argument("--benchmark-one", metavar="MODEL",
                    help="internal: score one model, print BENCHMARK_JSON")
    ap.add_argument("--build", metavar="MODEL")
    ap.add_argument("--out-dir", metavar="DIR",
                    help="write the index here instead of $BFA_INDEX_DIR "
                         "(default out/index). Lets two models' indexes "
                         "coexist so a switch costs a flag, not an encode.")
    ap.add_argument("--models", nargs="*", default=MODELS)
    args = ap.parse_args()

    # Read before the canaries so the cache canary can be built from the real
    # corpus. Reading is not scanning: nothing below has used it yet.
    corpus = load_corpus()

    print("CANARY")
    if not canary_selftest(corpus=corpus):
        print("FATAL: embed failed its own canary. The index would be built "
              "on the wrong normaliser, or on a stale document matrix.")
        return 2

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
        res = benchmark_one(args.benchmark_one, corpus, questions,
                            ks=tuple(args.ks))
        print("BENCHMARK_JSON " + json.dumps(res, ensure_ascii=False))
        return 0
    if args.benchmark:
        print_benchmark(benchmark_subprocs(args.models, args.gold,
                                           ks=tuple(args.ks)),
                        ks=tuple(args.ks))
    if args.build:
        if is_api_model(args.build):
            print("\nCANARY -- hosted embedder")
            if not gemini_canary():
                print("FATAL: the hosted embedder failed its canary. An index "
                      "built now would be on the wrong scale or the wrong "
                      "task type.")
                return 2
        build(args.build, corpus, out_dir=args.out_dir)
    if not (args.benchmark or args.build or args.benchmark_one):
        print("\nnothing to do: pass --benchmark or --build MODEL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
