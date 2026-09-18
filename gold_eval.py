"""
gold_eval.py — STEP 7. Evaluate answers against the gold set.

Reports FOUR numbers and never averages them:

  1. fact recall            share of gold_facts present, judged atomically
  2. citation accuracy      manual name AND clause both right; a missing
                            manual name counts as wrong, not partial
  3. abstention accuracy    on answerable:no only. Invisible in any aggregate,
                            and the number that matters most.
  4. violation rate         must_not_contain hits and forbidden clause
                            figures. Must be zero. Non-zero is a
                            report-stopper regardless of the other three.

3 AND 4 ARE INDEPENDENT and are never folded together. An answer can
abstain correctly and still quote a forbidden figure while explaining what
it is ruling out — that is one right behaviour and one wrong one. Scoring
the abstention as failed because a violation fired hid which of the two
had actually happened, and made metric 3 hostage to any false positive in
metric 4.

Forbidden clause figures are matched as (value, unit) PAIRS via
bn_shared.UNUM_RX, never as bare numbers: ৫ লক্ষ টাকা is a quoted ceiling,
৫ম ধাপে is an ordinal, and no magnitude threshold can tell them apart.
See forbidden_violations().

Why not just RAGAS faithfulness: faithfulness measures whether the answer
matches the retrieved context. If the context says দাচি, a wrong answer is
perfectly faithful. Keep RAGAS as a secondary panel for retrieval tuning.

Convention 3 canary: uses the গার্ডিয়ান nukta failure — a real historical
bug — to prove the fact matcher can fail. A naive matcher must MISS the
decomposed form; the canon-based matcher must catch it. If the naive one
passes, the canary is not testing anything and the run aborts.

Usage:
  python3 gold_eval.py --gold gold/bootstrap.yaml --answers out/answers.jsonl
  # answers.jsonl: {"id": "G-014", "answer": "...", "citations": ["dabi_2025 §7.1"]}
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import yaml

from bn_shared import (BN_DIGITS, canon, canon_pattern, fold_digits,
                       unit_numbers_in)

NUM_RX = re.compile(rf"[{BN_DIGITS}0-9][{BN_DIGITS}0-9,\.]*")


# ------------------------------------------------------- forbidden figures

def bare_number_violations(clause, answer, forbidden):
    """The matcher used until 16 Sep 2026. DELIBERATELY WRONG.

    Kept, like naive_contains, so the canary has something that fails. It
    compares bare NUM_RX tokens, which is why citing ধারা ৭.১ and numbering
    two steps scored violations against a correct abstention. Never called
    by score_one.
    """
    if not forbidden:
        return []
    anums = {fold_digits(x).replace(",", "")
             for x in NUM_RX.findall(answer)}
    return [str(n) for n in (forbidden.get(clause) or []) if str(n) in anums]


# ------------------------------------------------------------ rule 7
# RULE 7 COULD NOT FAIL UNTIL NOW, and that is why this exists.
#
# Measured 2026-09-18: qwen/qwen3.8-27b answered a Bengali question in
# English, 3 runs of 3 --
#   "The manual does not state it."
#   "The manual does not state the মেয়াদ (tenure) of the নীলকমল ঋণ.
#    According to synthetic_a §9.1.1, the সর্বোচ্চ সীমা (maximum limit) is
#    ৩ লক্ষ টাকা."
# and scored a PERFECT row on all four metrics: violations 0/4, fact recall
# 3/7 (the literal-only maximum), citation 2/2, abstention 1/1. Every number
# said the answer was good. A field officer cannot read it.
#
# TWO SEPARATE FAILURES, because they need separate fixes:
#   script   -- a Bengali question answered predominantly in Latin script
#   literals -- a role or product name TRANSLATED rather than quoted. "the
#               Area Manager" instead of এএম is a rule-7 failure even inside
#               an otherwise English answer, because এএম is the form that
#               appears on the paperwork the officer has to fill in.
#
# REPORTED AS A FIFTH NUMBER AND NEVER MERGED INTO THE FOUR. It is not a
# quality score and averaging it into recall would hide both.
LATIN_RX = re.compile(r"[A-Za-z]")
BENGALI_RX = re.compile(r"[ঀ-\u09ff]")
# Manual ids and clause citations are Latin BY REQUIREMENT -- rule 2 forbids
# translating them and rule 7 keeps clause citations as they are printed. They
# must not count as "the answer is in English". Stripped before measuring:
# any ASCII run carrying an underscore or a digit (dabi_2025, synthetic_a,
# 9.1.1) plus the section sign itself.
_IDENTLIKE_RX = re.compile(r"§|[A-Za-z0-9_]*[_0-9][A-Za-z0-9_.]*")


def bengali_share(text):
    """Bengali letters / (Bengali + Latin letters), identifiers removed.

    -> None when the text carries no letters at all to judge by.
    """
    t = _IDENTLIKE_RX.sub(" ", text or "")
    bn = len(BENGALI_RX.findall(t))
    la = len(LATIN_RX.findall(t))
    return (bn / (bn + la)) if (bn + la) else None


def script_of(text):
    """-> "bengali" | "latin" | "mixed" | "none". For the report, not the
    verdict: the verdict uses the share directly, because the failure this
    metric exists to catch -- English prose with Bengali terms dropped in --
    lands squarely in "mixed" and would pass a three-way test."""
    sh = bengali_share(text)
    if sh is None:
        return "none"
    if sh >= 0.85:
        return "bengali"
    if sh <= 0.15:
        return "latin"
    return "mixed"


# PREDOMINANTLY. A Bengali question needs a majority-Bengali answer; anything
# less is English prose with terms quoted into it, which is the measured qwen
# failure and reads as helpful while being unusable.
BENGALI_MAJORITY = 0.5


# THE GLOSS TABLE, and it is what makes "translated" decidable at all.
# roles.py rows are (key, dabi_form, progoti_form, acronym, english, note), so
# the registry that owns role names also owns their English names. Built lazily
# -- roles.py imports bn_shared, so importing it at module scope here would
# add an import-time cost to every consumer of gold_eval.
_ROLE_GLOSS = None


def role_gloss():
    """{canon(bengali form): English name} for every role. -> dict"""
    global _ROLE_GLOSS
    if _ROLE_GLOSS is None:
        try:
            import roles
        except ImportError:
            _ROLE_GLOSS = {}
            return _ROLE_GLOSS
        g = {}
        for row in getattr(roles, "ROLES", []):
            if len(row) < 5:
                continue
            english = (row[4] or "").strip()
            if not english:
                continue
            for form in row[1:4]:
                form = (form or "").strip()
                if form:
                    g[canon(form)] = english
        _ROLE_GLOSS = g
    return _ROLE_GLOSS


def untranslated_literals(q, answer):
    """Gold literals that were TRANSLATED rather than quoted. -> [str]

    ABSENCE IS NOT TRANSLATION, and conflating the two was a real defect in
    this metric. The first version reported a hit whenever a Bengali literal
    was missing from the answer -- which is exactly metric 1's measurement.
    Measured on the 17-question real run, 2026-09-18: 13 of 13 hits for qwen
    and 11 of 11 for gpt-oss-120b were literals simply ABSENT, i.e. missed
    facts counted a second time under rule 7. Metric 5 read 47% and 53% while
    carrying almost no rule-7 signal.

    A literal now counts as translated only when a TRANSLATED FORM OF IT IS
    PRESENT: the literal is missing AND its English name, from the roles
    registry, is in the answer. "the Area Manager" where এএম was required is
    a hit; এএম simply not mentioned is metric 1's business alone.

    WHAT THIS CANNOT DECIDE, and therefore does not report. A literal with no
    entry in the gloss table -- every multi-clause verbatim span, and every
    product or programme name, none of which carry an English name in any
    registry -- cannot be tested this way, because there is no known
    translated form to look for. Those are SKIPPED rather than guessed at.
    skipped_undecidable() reports how many, so the component's narrowness is
    visible in the report instead of being mistaken for a clean result.
    """
    gloss = role_gloss()
    out = []
    for f in q.get("gold_facts") or []:
        kind = (f.get("kind", "literal") if isinstance(f, dict) else "literal")
        if kind != "literal":
            continue
        lit = fact_text(f)
        if lit.strip().upper().startswith("TODO"):
            continue
        if not BENGALI_RX.search(lit) or NUM_RX.search(lit):
            continue
        if fact_present(lit, answer):
            continue                      # quoted as required
        english = gloss.get(canon(lit.strip()))
        if not english:
            continue                      # undecidable -- metric 1's alone
        if re.search(re.escape(english), answer or "", re.I):
            out.append(lit)
    return out


def skipped_undecidable(q, answer):
    """How many absent Bengali literals this component could not judge. -> int

    Reported beside metric 5 so a low hit count is not read as a clean bill.
    """
    gloss = role_gloss()
    n = 0
    for f in q.get("gold_facts") or []:
        kind = (f.get("kind", "literal") if isinstance(f, dict) else "literal")
        if kind != "literal":
            continue
        lit = fact_text(f)
        if lit.strip().upper().startswith("TODO"):
            continue
        if not BENGALI_RX.search(lit) or NUM_RX.search(lit):
            continue
        if fact_present(lit, answer):
            continue
        if not gloss.get(canon(lit.strip())):
            n += 1
    return n


# THE MIRROR FAILURE, added 2026-09-18 after wording 6 was measured.
# untranslated_literals catches a Bengali literal translated OUT of Bengali.
# The reverse is a Latin identifier transliterated INTO Bengali, and nothing
# saw it: qwen/qwen3.8-27b wrote "সিনথেটিক_এ" for synthetic_a and "§৯.১.১"
# for §9.1.1 in its PROSE while its CITATIONS: line stayed correctly Latin --
# so citation scored 2/2 and language_match 4/4 on an answer whose manual
# reference an officer cannot look up. The corpus writes manual_id as
# dabi_2025 / progoti / synthetic_a and clause as 2.1.2 / 9.1.1, both ASCII;
# a reference in the body has to match that spelling to be searchable.
#
# TWO SHAPES, both keyed on characters rather than on a word list, so no
# Bengali word literal enters a matching context here:
_BN_WORD_RX = re.compile(r"[A-Za-z0-9_\u0980-\u09ff]+")
# an identifier is the only reason an underscore appears next to Bengali text
_BN_UNDERSCORE = "_"
# § or a dotted three-component number, written in Bengali digits. Two
# components are NOT flagged: ২.৩৫ and ২.৪০ are interest rates in these
# manuals, and a percentage is fact recall's business, not rule 7's.
_BN_CLAUSE_RX = re.compile(r"§\s*[০-৯]+(?:\.[০-৯]+)*"
                           r"|[০-৯]+\.[০-৯]+\.[০-৯]+(?:\.[০-৯]+)*")


def wrong_script_identifiers(answer):
    """Manual ids and clause numbers written in Bengali in the answer body.
    -> [str]

    CANNOT SEE a manual id whose transliteration is an ordinary Bengali word.
    progoti -> প্রগতি is undetectable here and must stay so: প্রগতি is also
    the programme's name in running prose, and flagging it would fail every
    correct answer that names the programme. Only ids carrying an underscore
    (synthetic_a, dabi_2025) and clause numbers are caught. The gap is real
    and is the reason this is reported as detail, not merely as a count.
    """
    out = []
    for tok in _BN_WORD_RX.findall(answer or ""):
        if _BN_UNDERSCORE in tok and BENGALI_RX.search(tok):
            out.append(tok)
    out.extend(m.group(0) for m in _BN_CLAUSE_RX.finditer(answer or ""))
    seen, uniq = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def language_match(q, answer):
    """-> dict. Three independent checks, reported together, never averaged.

    script    a Bengali question must get a majority-Bengali answer
    literals  a Bengali role/product name in the gold facts must appear in
              Bengali, not translated -- "the Area Manager" instead of এএম is
              a rule-7 failure even inside an otherwise English answer,
              because এএম is the form on the paperwork.
    ids       the MIRROR of literals: a manual id or clause number must stay
              Latin as the corpus writes it, because that spelling is what an
              officer searches for. Both directions are the same failure --
              an identifier rewritten into the wrong script stops resolving.
    """
    qsh = bengali_share(q.get("question_bn", ""))
    ash = bengali_share(answer)
    q_is_bn = qsh is not None and qsh >= BENGALI_MAJORITY
    if not q_is_bn or ash is None:
        script_ok = True          # nothing to hold the answer to
    else:
        script_ok = ash >= BENGALI_MAJORITY
    missing = untranslated_literals(q, answer)
    wrong_ids = wrong_script_identifiers(answer)
    return {"question_script": script_of(q.get("question_bn", "")),
            "answer_script": script_of(answer),
            "bengali_share": (None if ash is None else round(ash, 3)),
            "script_ok": script_ok,
            "translated_literals": missing,
            "undecidable_literals": skipped_undecidable(q, answer),
            "wrong_script_ids": wrong_ids,
            "ok": bool(script_ok and not missing and not wrong_ids)}


def answer_violations(q, answer, forbidden_numbers=None):
    """Every violation one answer commits against one question. -> [(kind, what)]

    kind is "literal" (a must_not_contain_literals hit) or "clause_figure" (a
    unit-bearing pair the cited clause forbids).

    SHARED WITH THE POST-FILTER IN generate.py, deliberately. The filter that
    discards a leaking answer and the metric that counts the leak must be the
    same code: two copies would drift, and the drift is invisible -- the
    metric would report zero violations because the filter removed what it was
    looking for, while a different literal slipped past both. One function, so
    a filter miss is also a metric miss and shows up as a non-zero count.
    """
    out = []
    for lit in q.get("must_not_contain_literals", []) or []:
        if canon_pattern(lit).search(canon(answer)):
            out.append(("literal", lit))
    for clause in q.get("forbidden_numbers_from_clause", []) or []:
        for pair in forbidden_violations(clause, answer, forbidden_numbers):
            out.append(("clause_figure", f"{clause}:{pair}"))
    return out


def forbidden_violations(clause, answer, forbidden):
    """Unit-bearing figures in `answer` that `clause` forbids. -> [str]

    BARE NUMBERS ARE NOT COMPARED, and that is the whole point.

    REAL FAILURE, 16 Sep 2026. The check used to pull every number out of
    the answer with NUM_RX and flag any that appeared in the clause's list.
    ex.py stores figures as bare tokens, so ৫ লক্ষ is stored as '5' and the
    list therefore contained 1, 2, 3 … 32 — the ক্রম. serial column of the
    authority table among them. A CORRECT abstention for G-209 scored two
    violations for citing ধারা ৭.১, and six once it numbered its steps
    ১ম / ২য়. Filtering the list by magnitude looked like the fix and was
    not: it dropped '50' and so stopped forbidding ৫০ হাজার, which is the
    trap G-209 and G-211 exist to catch.

    A figure is only a figure when it carries its unit. UNUM_RX -- the same
    pattern that filled every chunk's unit_numbers -- yields ('5', 'লক্ষ')
    for ৫ লক্ষ টাকা and NOTHING for ৫ম ধাপে or ধারা ৭.১, which separates
    the quoted amount from the ordinal without any threshold.

    The unit must match too, where the clause's own pairs are known:
    ৫ মাস is not a procurement ceiling just because ৫ লক্ষ is. `_units`
    in forbidden.json carries those pairs. Without it the check falls back
    to value-only-but-must-be-unit-bearing, which is weaker and says so.

    KNOWN GAP, stated rather than papered over: a figure printed with no
    unit at all -- the loan bands ১,০০,০০১-১৫০,০০০ -- yields no pair and
    cannot be caught here. No clause currently in forbidden.json is
    affected; every amount in §7.1 and §1.1 carries টাকা, হাজার, লক্ষ or %.
    """
    if not forbidden:
        return []
    vals = {str(n) for n in (forbidden.get(clause) or [])}
    if not vals:
        return []
    known_pairs = (forbidden.get("_units") or {}).get(clause)
    out = []
    for pair in unit_numbers_in(answer):
        if pair.split(" ")[0] not in vals:
            continue
        if known_pairs is not None and pair not in set(known_pairs):
            continue
        if pair not in out:
            out.append(pair)
    return out


# ---------------------------------------------------------------- matching

def naive_contains(needle, haystack):
    """Deliberately wrong. Exists only so the canary has something to fail."""
    return needle in haystack


def fact_present(fact, answer):
    """
    Nukta- and zero-width-tolerant containment, plus numeric equality for
    facts that are figures. Never plain `in`.
    """
    a = canon(answer)
    f = canon(fact)
    if canon_pattern(f).search(a):
        return True
    fnums = {fold_digits(x).replace(",", "") for x in NUM_RX.findall(f)}
    if fnums:
        anums = {fold_digits(x).replace(",", "") for x in NUM_RX.findall(a)}
        if fnums <= anums:
            return True
    return False


def norm_cite(c):
    return canon(c).replace("§", "").replace(" ", "").lower()


# ------------------------------------------- was the citation even possible
# A METRIC THAT CHARGES FOR THE STRUCTURALLY IMPOSSIBLE CANNOT IMPROVE, and
# citation accuracy was one. Measured on the 17-question real run: 6 of the
# remaining failures per model were questions whose gold chunk was never in
# the context. G-216 is the clearest -- the context held progoti_0017, _0015
# and _0019, all labelled clause 1.1.2, so the answer's "progoti §1.1.2" was
# a truthful citation of what it had been given, while the gold chunk
# progoti_0021 at §1.1.3 was never retrieved. Scoring that as a citation
# failure blames the answer layer for a retrieval miss.
#
# Same shape as the absent-fact fix: a kind:absent fact resolved to a gold
# chunk and was counted in recall, so recall was scoring retrieval on a fact
# that names no chunk to retrieve.
#
# TWO NUMBERS, NEVER MERGED. Accuracy over the achievable questions, and a
# count of the unachievable ones. Merging them would let a retrieval
# regression look like a generation improvement: drop a gold chunk out of
# context and the question leaves the denominator.
MANIFEST_PATH = Path("data/processed/chunk_manifest.json")


def load_twins(path=MANIFEST_PATH):
    """{chunk_id: set of chunk_ids in its dup_group, itself included}.

    Read from the manifest, which is the tracked artefact -- the chunk files
    are under data/processed/* and gitignored. Keyed on dup_group and NOT on
    the manifest sha: the sha covers display_text, which carries the heading,
    so one passage printed at 1.1.2 in Dabi and 1.1.3 in Progoti has two
    shas and one dup_group. dup_group is what collapse() acts on.
    """
    p = Path(path)
    if not p.exists():
        return {}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    groups = defaultdict(set)
    for row in doc.get("chunks") or []:
        g = row.get("dup_group")
        if g:
            groups[g].add(row["chunk_id"])
    out = {}
    for members in groups.values():
        for cid in members:
            out[cid] = set(members)
    return out


def load_context(path):
    """{question_id: [chunk_id, ...]} from a retrieval dump. -> dict"""
    ctx = {}
    p = Path(path)
    if not p.exists():
        return ctx
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        ctx[row.get("question_id")] = [c.get("chunk_id")
                                       for c in (row.get("chunks") or [])]
    return ctx


def gold_chunk_ids(q):
    """Chunk ids this question's gold declares. -> set"""
    ids = set()
    for r in q.get("source_refs") or []:
        c = r.get("chunk_id") if isinstance(r, dict) else None
        if c and not str(c).strip().upper().startswith("TODO"):
            ids.add(str(c))
    for f in q.get("gold_facts") or []:
        r = f.get("ref") if isinstance(f, dict) else None
        if r and not str(r).strip().upper().startswith("TODO"):
            ids.add(str(r))
    return ids


def ref_in_context(ref, ctx_ids, twins):
    """Was this chunk, or a declared twin of it, in the context?
    -> True / False / None

    THE ONE IMPLEMENTATION. Metric 1 and metric 2 both ask this question and
    a second copy would drift -- the same reason the post-filter shares its
    matcher with the violation metric. Keyed on dup_group via `twins`, NOT on
    the manifest sha: the sha covers display_text, which carries the heading,
    so the passage printed at 1.1.2 in Dabi and 1.1.3 in Progoti has two shas
    and one dup_group.

    None when there is nothing to judge by -- no ref, a TODO ref, or no
    context supplied -- so the fact stays out of both buckets rather than
    silently into one.
    """
    if not ref or ctx_ids is None:
        return None
    r = str(ref).strip()
    if not r or r.upper().startswith("TODO"):
        return None
    return bool(({r} | twins.get(r, set())) & set(ctx_ids))


def fact_kind_of(f):
    """A fact's kind. Plain strings are literals."""
    return (f.get("kind", "literal") if isinstance(f, dict) else "literal")


def fact_ref(f):
    """A fact's declared chunk ref, or None."""
    if not isinstance(f, dict):
        return None
    r = f.get("ref")
    return str(r) if r else None


def collapse_twin_facts(facts, twins):
    """Collapse facts that are the same text in a declared duplicate group.
    -> (kept, collapsed)

    G-216 carries one 96-character passage twice, once with ref
    dabi_2025_0014 and once with ref progoti_0021 -- a single dup_group. It
    counted as two facts and, when missed, as two misses, so one passage
    moved the metric twice as far as any other. The twin refs are navigation:
    they say where the passage can be cited from, not that there are two
    things to say.

    Collapsed ONLY when the texts match and the refs are twins of each other.
    The same sentence appearing in two genuinely different places stays two
    facts, because then there really are two chunks to retrieve.
    """
    kept, collapsed = [], []
    for f in facts:
        text = canon(fact_text(f))
        ref = fact_ref(f)
        merged = False
        for k in kept:
            if canon(fact_text(k)) != text:
                continue
            kref = fact_ref(k)
            if ref and kref and (ref == kref or ref in twins.get(kref, set())):
                collapsed.append(f)
                merged = True
                break
        if not merged:
            kept.append(f)
    return kept, collapsed


def citation_achievable(q, ctx_ids, twins):
    """Could a correct citation have been produced from this context?
    -> True / False / None

    None when there is nothing to judge by: no must_cite, no declared gold
    chunk, or no context supplied. None keeps the question OUT of both
    numbers rather than silently into one.
    """
    if not (q.get("must_cite") or []):
        return None
    gold = gold_chunk_ids(q)
    if not gold or ctx_ids is None:
        return None
    return any(ref_in_context(c, ctx_ids, twins) for c in gold)


CITE_MODES = ("all", "any")


def cite_ok(expected, got, mode="all"):
    """Manual name and clause both required. Missing manual name = wrong.

    TWO QUANTIFIERS, because must_cite was only ever all-of and that scored a
    correct answer wrong. Where the same passage is printed in both manuals --
    516 of 867 chunks are byte-identical across manuals -- an answer citing
    either copy has cited the content correctly, and demanding both made
    G-203 fail for emitting dabi_2025 §1.2.1 when progoti §1.2.1 is the same
    bytes.

      mode "all"  every listed citation must appear. The listed manuals say
                  DIFFERENT things and an answer needs each: G-215, where
                  §5.4 is fire-safety insurance in one manual and livestock
                  insurance in the other, so one citation is half an answer.
      mode "any"  at least one listed citation must appear. The listed
                  citations are the same content in different places.

    Set per question with must_cite_mode; absent means "all", so every
    existing question keeps its meaning and only the ones examined changed.
    """
    if not expected:
        return None                               # not scored
    g = {norm_cite(x) for x in got}
    hit = [any(norm_cite(e) in x or x in norm_cite(e) for x in g)
           for e in expected]
    return any(hit) if mode == "any" else all(hit)


# ---------------------------------------------------------------- scoring

def fact_text(f):
    """A fact is either a plain string or {text, verified_against}."""
    return f["text"] if isinstance(f, dict) else f


def fact_verified(f):
    return (f.get("verified_against", "corpus") if isinstance(f, dict)
            else "corpus")


# Abstention markers. Each Bengali literal is compiled by canon_pattern, NOT
# concatenated into one hand-written regex.
#
# The hand-written version shipped with "পাওয়া যায়নি" in one nukta form. An
# answer using the other form did not match, so a correct abstention scored as
# a failure to abstain — on the metric this file's own docstring calls the one
# that matters most, and in the only bucket where no aggregate would reveal it.
# Convention 4, violated in the file that enforces it.
ABSTAIN_BN = [
    "নেই",
    "উল্লেখ নেই",
    "কোন তথ্য নেই",
    "পাওয়া যায়নি",
    "উল্লেখ করা হয়নি",
    "বলা হয়নি",
    "ম্যানুয়ালে নেই",
    "তথ্য পাওয়া যায়নি",
    # NEGATED-VERB FORMS. Added 2026-09-18 after a measured false negative:
    # ABSTAIN_EN already matched "does not state|contain|specify", but the
    # Bengali list had only the "নেই / হয়নি" families and no equivalent of
    # the negated verb. A correct abstention reading
    # "ম্যানুয়ালটি ... উল্লেখ করে না" therefore scored abstention 0/1 on
    # gpt-oss-120b, three runs out of three -- the model was right and the
    # metric was wrong. The asymmetry between the two lists was the bug.
    "উল্লেখ করে না",
    "উল্লেখ করেনি",
    "তথ্য দেয় না",
]
ABSTAIN_EN = re.compile(
    r"not stated|not in the manual|does not (state|contain|specify)|"
    r"no information|is not specified", re.I)

_ABSTAIN_PATS = None


def is_abstention(answer):
    global _ABSTAIN_PATS
    if _ABSTAIN_PATS is None:                 # built lazily; no import-time work
        _ABSTAIN_PATS = [canon_pattern(p) for p in ABSTAIN_BN]
    a = canon(answer)
    return any(p.search(a) for p in _ABSTAIN_PATS) or bool(ABSTAIN_EN.search(a))


def norm_answerable(v):
    """
    YAML 1.1 parses bare `no` as boolean False and `yes` as True. Left
    unhandled, every answerable:no question silently drops out of the
    abstention bucket — the one score that cannot be recovered from an
    aggregate. Quote them in YAML too; this is belt and braces.
    """
    if v is True:
        return "yes"
    if v is False:
        return "no"
    return str(v).strip().lower()


def score_one(q, ans, forbidden_numbers=None, judge=None,
              ctx_ids=None, twins=None):
    answer = ans.get("answer", "")
    twins = twins or {}
    raw_facts = q.get("gold_facts", []) or []

    # THREE BUCKETS, and only the first is a recall denominator. Measured on
    # the 17-question real run: of 43 literal misses across two models, 22
    # were facts whose gold chunk was never retrieved and 12 were claim facts
    # no literal matcher can evaluate. Recall was reporting a number that
    # could not improve without retrieval changing, exactly as citation was.
    #
    # ONE PASSAGE, ONE FACT. Twin refs collapse first: G-216 carried the same
    # 96-character passage under two refs in one dup_group, so it counted
    # twice and missed twice.
    facts, collapsed = collapse_twin_facts(raw_facts, twins)

    # unjudged: a claim fact with no judge to evaluate it. Not missed -- it
    # was never assessed, and leaving it in the denominator charged every
    # model for the judge being absent.
    # CLAIM *AND* ABSENT. An absent fact asserts that something is NOT in the
    # manual -- G-217's "আরোগ্য ঋণের ঋণসীমা বা মেয়াদ ম্যানুয়ালে নেই" -- so it
    # has no ref by construction and there is no span for a literal matcher
    # to find. It was sitting in the denominator and scoring 0 on every model
    # forever, which is the same category error as the claim facts.
    UNJUDGEABLE_KINDS = ("claim", "absent")
    unjudged = [f for f in facts
                if fact_kind_of(f) in UNJUDGEABLE_KINDS and judge is None]
    judged = [f for f in facts if f not in unjudged]

    # unachievable: the gold chunk, and every declared twin of it, absent
    # from the context. None (no ref, or no context given) counts as
    # achievable so that a run without --retrieved reports as before.
    unachievable = [f for f in judged
                    if ref_in_context(fact_ref(f), ctx_ids, twins) is False]
    scoreable = [f for f in judged if f not in unachievable]

    hit = [f for f in scoreable
           if fact_present(fact_text(f), answer)
           or (judge and judge(fact_text(f), answer))]
    # Facts checked only against a self-written transcription cannot detect a
    # transcription error. They are scored, but reported separately so the
    # headline number never rests on them.
    unrendered = [f for f in scoreable if fact_verified(f) != "render"]

    violations = answer_violations(q, answer, forbidden_numbers)

    answerable = norm_answerable(q.get("answerable", "yes"))
    abstained = is_abstention(answer)

    # ABSTENTION AND VIOLATIONS ARE INDEPENDENT. They used to be conjoined
    # (`abstained and not violations`), which meant a single false violation
    # did not annotate the abstention score, it destroyed it -- and the two
    # failures are not the same failure. An answer can correctly say the
    # manual gives no ceiling AND still quote a forbidden figure while doing
    # it; that is one right behaviour and one wrong one, and collapsing them
    # into one boolean hides which. Report both; metric 4 is the
    # report-stopper on its own.
    if answerable == "no":
        abstention = abstained
    elif answerable == "partial":
        abstention = None            # judged by fact recall, not abstention
    else:
        abstention = not abstained   # abstaining on an answerable question fails

    return {
        "id": q["id"],
        "type": q.get("type"),
        "provenance": q.get("provenance", "unknown"),
        "answerable": answerable,
        # DENOMINATOR IS `scoreable`: judged, and its chunk was reachable.
        "facts_total": len(scoreable),
        "facts_hit": len(hit),
        "facts_missed": [fact_text(f) for f in scoreable if f not in hit],
        # Reported beside recall, never inside it.
        "facts_unachievable": [(fact_text(f), fact_ref(f))
                               for f in unachievable],
        "facts_unjudged": [fact_text(f) for f in unjudged],
        "facts_collapsed": [(fact_text(f), fact_ref(f)) for f in collapsed],
        "facts_unrendered": len(unrendered),
        "facts_hit_rendered": len([f for f in hit
                                   if fact_verified(f) == "render"]),
        "facts_total_rendered": len([f for f in scoreable
                                     if fact_verified(f) == "render"]),
        "citation": cite_ok(q.get("must_cite"), ans.get("citations", []),
                            q.get("must_cite_mode", "all")),
        # None when no context was supplied, so an eval run without
        # --retrieved reports citation exactly as it did before.
        "citation_achievable": citation_achievable(q, ctx_ids, twins or {}),
        "citations": ans.get("citations", []),
        # Fifth number. Never folded into the four above.
        "language": language_match(q, answer),
        "abstention": abstention,
        # the raw signal, so a row can be read without re-deriving it
        "abstained": abstained,
        "violation_free": not violations,
        "violations": violations,
    }


JUDGE_SYSTEM = (
    "Does the ANSWER assert the CLAIM? The answer may be in Bengali and the "
    "claim in English or Bengali. Reply with exactly YES or NO. Reply NO if "
    "the answer is merely consistent with the claim without asserting it.")


def make_provider_judge(provider="groq", model="openai/gpt-oss-20b"):
    """A judge routed through generate.complete. -> (judge, label)

    WHY THIS EXISTS BESIDE make_llm_judge. make_llm_judge needs the anthropic
    package, which is not installed here, and generate.make_judge only ever
    wired Gemini -- so --judge llm could not run at all on the provider this
    project actually has quota on, and every real run has been scored with
    --judge literal. That is not a small gap: with the literal matcher, fact
    recall was 46.7% on gpt-oss and ~7 of its 8 achievable misses were facts
    whose information was present in different words.

    imported lazily: generate imports gold_eval for answer_violations, so a
    module-scope import here would cycle.

    THE JUDGE MUST NOT BE THE GENERATOR. gpt-oss-20b is distinct from both
    generators used so far; it is the same FAMILY as gpt-oss-120b, which is
    recorded as a weakness rather than claimed as independence.

    The judge sees the fact and the answer and NOTHING else -- not the gold
    set, not the retrieved context, not the question's notes. A judge given
    the surrounding material grades on vibes.
    """
    import generate
    generate.configure(provider, model)

    def judge(fact, answer):
        text, _meta = generate.complete(
            JUDGE_SYSTEM, f"CLAIM:\n{fact}\n\nANSWER:\n{answer}")
        return (text or "").strip().upper().startswith("YES")

    return judge, f"{provider}:{model}"


def make_llm_judge(model="claude-sonnet-4-6"):
    """
    Optional. Literal and numeric facts are matched deterministically above;
    this is only for prose facts like "the manual does not state a ceiling".

    The judge sees the fact and the answer and NOTHING else — not the gold
    set, not the retrieved context, not the question's notes. A judge given
    the surrounding material grades on vibes.
    """
    import anthropic
    client = anthropic.Anthropic()

    def judge(fact, answer):
        r = client.messages.create(
            model=model, max_tokens=8,
            system=("Does the ANSWER assert the CLAIM? The answer may be in "
                    "Bengali and the claim in English. Reply with exactly "
                    "YES or NO. Reply NO if the answer is merely consistent "
                    "with the claim without asserting it."),
            messages=[{"role": "user",
                       "content": f"CLAIM: {fact}\n\nANSWER: {answer}"}])
        return r.content[0].text.strip().upper().startswith("YES")

    return judge


def canary_selftest(verbose=True):
    """
    Real historical failure: গার্ডিয়ান লাইফ matched 2 of 12 chunks because
    NFC decomposes য় into য + ়, and the pattern was nukta-blind. A judge
    with the same bug silently marks correct answers wrong.
    """
    # Built from explicit codepoints, because NFC/NFD will NOT convert
    # between these: য় is on the Unicode composition exclusion list.
    PRE, DEC = chr(0x09DF), chr(0x09AF) + chr(0x09BC)
    fact = f"গার্ড{chr(0x09BF)}{PRE}ান লাইফ"     # গার্ডিয়ান, precomposed য়
    # No product name in the fixture: দাবি here was incidental to what this
    # canary tests (nukta matching on গার্ডিয়ান) and tripped Convention 2.
    answer = (f"গার্ড{chr(0x09BF)}{DEC}ান লাইফ বিমা কোম্পানির কাছে "
              f"করতে হবে।")

    naive = naive_contains(fact, answer)
    real = fact_present(fact, answer)
    ok = (naive is False) and (real is True)
    if verbose:
        print(f"  canary nukta_blind_matcher  naive={naive} "
              f"canon={real}  [{'DETECTED' if ok else 'MISSED'}]")

    # Second: a wrong figure must not score as a hit.
    ok2 = not fact_present("সর্বোচ্চ ১৫০০০০ টাকা", "সর্বোচ্চ ১০০০০ টাকা")
    if verbose:
        print(f"  canary wrong_figure         "
              f"[{'PASS' if ok2 else 'FALSE POSITIVE'}]")

    # Second-and-a-half: the forbidden-figure matcher, both directions.
    # REAL failure, 16 Sep 2026 -- bare-number matching flagged a correct
    # G-209 abstention for citing ধারা ৭.১ and numbering its steps ১ম/২য়,
    # because ex.py stores ৫০ হাজার as the bare token '50'. Self-contained
    # so `python gold_eval.py` proves its own matcher without out/.
    _CL = "canary §7.1"
    _fb = {_CL: ["50", "7.1"], "_units": {_CL: ["50 হাজার"]}}
    quoted = forbidden_violations(_CL, "৫০ হাজার টাকা পর্যন্ত।", _fb)
    ordinal = forbidden_violations(_CL, "৫০ম ধাপে যোগাযোগ করুন।", _fb)
    clause = forbidden_violations(_CL, "ধারা ৭.১ দেখুন।", _fb)
    wrong_unit = forbidden_violations(_CL, "৫০ মাস মেয়াদ।", _fb)
    legacy = bare_number_violations(_CL, "ধারা ৭.১ দেখুন।", _fb)
    ok2b = (quoted and not ordinal and not clause and not wrong_unit
            and legacy)
    if verbose:
        print(f"  canary forbidden_pairs      quoted={quoted}, "
              f"ordinal={ordinal}, clause_cite={clause}, "
              f"wrong_unit={wrong_unit}, bare_matcher_on_clause={legacy}  "
              f"[{'DETECTED' if ok2b else 'MISSED'}]")
    ok2 = ok2 and bool(ok2b)

    # Third: the abstention detector. REAL historical failure, 14 Sep 2026 —
    # ABSTAIN_RX was a hand-written alternation carrying "পাওয়া যায়নি" in one
    # nukta form. An answer using the other form did not match, so a correct
    # abstention was scored as a failure to abstain. Found by the Convention 4
    # lint, not by any test here.
    _PRE, _DEC = chr(0x09DF), chr(0x09AF) + chr(0x09BC)
    _lit = ABSTAIN_BN[3]                       # পাওয়া যায়নি
    forms = {"precomposed": _lit.replace(_DEC, _PRE),
             "decomposed": _lit.replace(_PRE, _DEC)}
    assert forms["precomposed"] != forms["decomposed"], \
        "nukta forms collapsed; this canary proves nothing"
    both = all(is_abstention(f"এই তথ্য ম্যানুয়ালে {v}।")
               for v in forms.values())
    control = not is_abstention("সেনার হার ১.৯০ শতাংশ।")
    ok3 = both and control
    if verbose:
        print(f"  canary abstain_nukta_forms  both forms match={both} "
              f"control={control}  "
              f"[{'DETECTED' if ok3 else 'MISSED'}]")
    # ---- rule 7: language_match must be able to FAIL ---------------------
    # Real observed failure, qwen/qwen3.8-27b, 2026-09-18, wording 5, S-002 of
    # the synthetic set: a Bengali question answered "The manual does not
    # state it." -- and it scored a perfect four-metric row. Assembled from
    # parts so the file carries no typed Bengali sentence reaching a matcher.
    _QBN = ("জিএম কত টাকা পর্যন্ত ঋণ অনুমোদন করতে পারেন?")
    _q = {"question_bn": _QBN, "gold_facts": []}
    _eng = language_match(_q, "The manual does not state it.")
    _bn = language_match(
        _q, "ম্যানুয়ালটি এই তথ্য উল্লেখ করে না।")
    # and the literal half: a role name translated instead of quoted
    _role = "শাখা ব্যবস্থাপক"
    _qr = {"question_bn": _QBN,
           "gold_facts": [{"text": _role, "kind": "literal"}]}
    _tr = language_match(_qr, "The Branch Manager approves it.")
    _kept = language_match(_qr, f"{_role} অনুমোদন করবেন।")
    # ---- fact recall must not be charged for a retrieval miss either -----
    # A REAL CASE, AND ONE I GOT WRONG. G-223's six step headers were
    # reported as a length artefact -- the suspicion was that a 36-to-72
    # character span cannot be reproduced verbatim. It was not that at all:
    # on the 2026-09-18 run the gold chunks dabi_2025_0198 and _0199 were
    # never retrieved, so the steps could not have been answered from the
    # context at any length. Overlap with the answer was 0.00 on gpt-oss for
    # all six. Shortening them would have changed nothing.
    #
    # The canary asserts a step header with its ref out of context counts
    # unachievable, and that the same fact with the ref present does not --
    # so the bucket cannot swallow everything.
    _step = {"text": "\u09e7\u09ae-\u09a7\u09be\u09aa: "
                     "\u0987\u09a8\u09b8\u09bf\u09a1\u09c7\u09a8\u09cd"
                     "\u099f \u09a8\u09cb\u099f\u09bf\u09ab\u09bf"
                     "\u0995\u09c7\u09b6\u09a8 \u0993 "
                     "\u09b8\u09c7\u099f\u0986\u09aa",
             "kind": "literal", "ref": "dabi_2025_0198"}
    _out = ref_in_context("dabi_2025_0198", ["progoti_0306", "dabi_2025_0284"],
                          {})
    _in = ref_in_context("dabi_2025_0198", ["dabi_2025_0198"], {})
    _via_twin = ref_in_context("dabi_2025_0198", ["progoti_0229"],
                               {"dabi_2025_0198": {"dabi_2025_0198",
                                                   "progoti_0229"}})
    _nc = ref_in_context("dabi_2025_0198", None, {})

    # and the G-216 double-count: one passage, two refs, one dup_group
    _same = "\u09b8\u09a6\u09b8\u09cd\u09af\u0997\u09a3"
    _twin_facts = [{"text": _same, "kind": "literal", "ref": "dabi_2025_0014"},
                   {"text": _same, "kind": "literal", "ref": "progoti_0021"}]
    _tw = {"dabi_2025_0014": {"dabi_2025_0014", "progoti_0021"},
           "progoti_0021": {"dabi_2025_0014", "progoti_0021"}}
    _tkeep, _tcoll = collapse_twin_facts(_twin_facts, _tw)
    # the same text in two UNRELATED chunks must NOT collapse
    _far = [{"text": _same, "kind": "literal", "ref": "dabi_2025_0014"},
            {"text": _same, "kind": "literal", "ref": "dabi_2025_0300"}]
    _tkeep2, _tcoll2 = collapse_twin_facts(_far, _tw)

    ok7 = (_out is False and _in is True and _via_twin is True and _nc is None
           and len(_tkeep) == 1 and len(_tcoll) == 1
           and len(_tkeep2) == 2 and len(_tcoll2) == 0)
    if verbose:
        print(f"  canary fact_achievable      G-223 step ref out of context "
              f"-> {_out} (want False); present -> {_in}; via twin -> "
              f"{_via_twin}; no context -> {_nc}"
              f"  [{'DETECTED' if ok7 else 'MISSED'}]")
        print(f"  canary twin_facts_collapse  twin refs {len(_twin_facts)}->"
              f"{len(_tkeep)} fact(s); unrelated refs {len(_far)}->"
              f"{len(_tkeep2)} (must stay 2)"
              f"  [{'PASS' if ok7 else 'FAIL'}]")
    ok = ok and ok7

    # ---- citation must not be charged for a retrieval miss ---------------
    # A REAL CASE: G-216. The gold chunk is progoti_0021, the same passage as
    # dabi_2025_0014 (one dup_group, printed at 1.1.3 in Progoti and 1.1.2 in
    # Dabi). On the 2026-09-18 run the context held progoti_0017, _0015 and
    # _0019 -- all labelled clause 1.1.2 -- and NEITHER gold chunk nor its
    # twin. Both models cited "progoti §1.1.2", truthfully describing what
    # they were given, and both were scored as citation failures.
    #
    # The canary asserts that context counts as UNACHIEVABLE, and that a
    # context holding the gold chunk or its twin counts as achievable -- so
    # the split cannot silently classify everything one way.
    _q216 = {"id": "G-216",
             "must_cite": ["dabi_2025 \u00a71.1.2", "progoti \u00a71.1.3"],
             "must_cite_mode": "any",
             "source_refs": [{"chunk_id": "dabi_2025_0014"},
                             {"chunk_id": "progoti_0021"}]}
    _twins216 = {"dabi_2025_0014": {"dabi_2025_0014", "progoti_0021"},
                 "progoti_0021": {"dabi_2025_0014", "progoti_0021"}}
    _real_ctx = ["progoti_0017", "progoti_0015", "progoti_0019"]
    _miss = citation_achievable(_q216, _real_ctx, _twins216)
    _hit = citation_achievable(_q216, ["progoti_0017", "progoti_0021"],
                               _twins216)
    _twin_hit = citation_achievable(_q216, ["progoti_0017", "dabi_2025_0014"],
                                    _twins216)
    _noctx = citation_achievable(_q216, None, _twins216)
    ok6 = (_miss is False and _hit is True and _twin_hit is True
           and _noctx is None)
    if verbose:
        print(f"  canary cite_achievable      real G-216 context -> "
              f"{_miss} (want False); gold chunk present -> {_hit}; "
              f"twin present -> {_twin_hit}; no context -> {_noctx}"
              f"  [{'DETECTED' if ok6 else 'MISSED'}]")
    ok = ok and ok6

    # ---- the mirror half, from the real wording-6 run --------------------
    # qwen/qwen3.8-27b, S-004, run 1, verbatim: the manual id transliterated
    # and the clause number in Bengali digits, inside otherwise correct
    # Bengali prose that scores 3/7 recall and 2/2 citation.
    # NOT HAND-TYPED. Generated from the saved answer, because the two things
    # that matter here are invisible in a Bengali glyph: the exact codepoints
    # and the NUKTA FORM. qwen emits য় DECOMPOSED as \u09af\u09bc in both
    # ম্যানুয়ালের and অনুযায়ী, not composed as \u09df; a hand-typed fixture
    # got both wrong and the canary then "passed" on a string the model never
    # produced.
    _ID_BN = ("\u09b8\u09bf\u09a8\u09a5\u09c7\u099f\u09bf\u0995_\u098f")
    _CLAUSE_BN = "\u00a7\u09ef.\u09e7.\u09e7"
    _MIRROR = (_ID_BN + " \u09ae\u09cd\u09af\u09be\u09a8\u09c1\u09af"
               "\u09bc\u09be\u09b2\u09c7\u09b0 " + _CLAUSE_BN
               + " \u0985\u09a8\u09c1\u09af\u09be\u09af\u09bc\u09c0")
    _mir = language_match(_q, _MIRROR)
    # the SAME answer with the id and the clause number left in Latin
    _lat = language_match(_q, _MIRROR.replace(_ID_BN, "synthetic_a")
                                     .replace(_CLAUSE_BN, "\u00a79.1.1"))
    ok5 = (not _mir["ok"]
           and any("_" in x for x in _mir["wrong_script_ids"])
           and any("\u00a7" in x for x in _mir["wrong_script_ids"])
           and _lat["ok"] and not _lat["wrong_script_ids"])
    ok4 = (not _eng["ok"] and _eng["answer_script"] == "latin"
           and _bn["ok"]
           and not _tr["ok"] and _role in _tr["translated_literals"]
           and _kept["ok"]
           and ok5)
    if verbose:
        print(f"  canary language_match       english-on-bengali ok="
              f"{_eng['ok']} (share {_eng['bengali_share']}); bengali ok="
              f"{_bn['ok']}; role translated ok={_tr['ok']}; role kept ok="
              f"{_kept['ok']}  [{'DETECTED' if ok4 else 'MISSED'}]")
        print(f"  canary language_match_ids   real qwen S-004 ok={_mir['ok']} "
              f"hits={_mir['wrong_script_ids']}; same answer in Latin ok="
              f"{_lat['ok']}  [{'DETECTED' if ok5 else 'MISSED'}]")

    return ok and ok2 and ok3 and ok4


# ---------------------------------------------------------------- report

def report(rows):
    by = defaultdict(list)
    for r in rows:
        by[r["provenance"]].append(r)

    def block(name, rs):
        if not rs:
            return
        lang = [r for r in rs if r.get("language")]
        lang_ok = [r for r in lang if r["language"]["ok"]]
        ft = sum(r["facts_total"] for r in rs)
        fh = sum(r["facts_hit"] for r in rs)
        cited = [r for r in rs if r["citation"] is not None]
        abst = [r for r in rs if r["answerable"] == "no"]
        viol = [r for r in rs if r["violations"]]
        print(f"\n[{name}]  n={len(rs)}")
        unach_f = [(r["id"], t, ref) for r in rs
                   for t, ref in (r.get("facts_unachievable") or [])]
        unj_f = [(r["id"], t) for r in rs
                 for t in (r.get("facts_unjudged") or [])]
        coll_f = [(r["id"], t, ref) for r in rs
                  for t, ref in (r.get("facts_collapsed") or [])]
        print(f"  1. fact recall ........ {fh}/{ft} = "
              f"{(fh / ft if ft else 0):.1%}"
              + ("   (gold chunk was in context)" if unach_f else ""))
        if unach_f:
            print(f"  1b. facts unachievable .. {len(unach_f)}"
                  f"   (NOT in the denominator: the gold chunk and every "
                  f"declared\n      twin of it were absent from the context "
                  f"-- a retrieval miss)")
            for qid, t, ref in unach_f:
                print(f"       {qid:<8} ref {str(ref):<20} {t[:52]}")
        if unj_f:
            print(f"  1c. facts unjudged ...... {len(unj_f)}"
                  f"   (NOT in the denominator: kind claim or absent, with "
                  f"no llm judge,\n      so never assessed rather than "
                  f"missed. "
                  f"Run --judge llm to score these)")
            for qid, t in unj_f:
                print(f"       {qid:<8} {t[:60]}")
        if coll_f:
            print(f"  1d. twin refs collapsed . {len(coll_f)}"
                  f"   (one passage in a declared dup_group counted once; "
                  f"the\n      duplicate refs are navigation)")
            for qid, t, ref in coll_f:
                print(f"       {qid:<8} ref {str(ref):<20} {t[:52]}")
        # TWO NUMBERS, NEVER MERGED. Accuracy is over the questions where a
        # correct citation was POSSIBLE from the context; the unachievable
        # ones are counted and listed, not scored. See citation_achievable.
        # A PASS IS PROOF OF ACHIEVABILITY, whatever the context said. G-222
        # cites bare manual ids -- "dabi_2025", "progoti" -- so an answer can
        # satisfy it without the gold clause being retrieved, and it did.
        # Excluding a demonstrated success distorts the number exactly as much
        # as charging for the impossible, so only FAILURES can be
        # unachievable.
        unach = [r for r in cited
                 if r.get("citation_achievable") is False and not r["citation"]]
        scor = [r for r in cited if r not in unach]
        chit = sum(1 for r in scor if r["citation"])
        print(f"  2. citation accuracy .. {chit}/{len(scor)}"
              + (f" = {chit / len(scor):.1%}" if scor else " (none scored)")
              + ("   (gold chunk was in context)" if unach else ""))
        if unach:
            print(f"  2b. citation unachievable  {len(unach)}"
                  f"   (NOT merged into 2: the gold chunk was never "
                  f"retrieved,\n      so no correct citation could be "
                  f"produced -- a retrieval miss, not a\n      citation "
                  f"failure)")
            for r in unach:
                print(f"       {r['id']:<8} cited {r.get('citations') or []}")
        print(f"  3. abstention accuracy  "
              f"{sum(1 for r in abst if r['abstention'])}/{len(abst)}"
              + (f" = {sum(1 for r in abst if r['abstention']) / len(abst):.1%}"
                 if abst else " (no unanswerable questions)")
              + "   (independent of 4)")
        print(f"  4. violations ......... {len(viol)}/{len(rs)}"
              + ("   <-- REPORT STOPPER" if viol else ""))
        # the cross-tab, because the interesting cell is abstained-AND-violated
        both = [r for r in abst if r["abstention"] and r["violations"]]
        if both:
            print(f"     {len(both)} question(s) abstained correctly AND "
                  f"quoted a forbidden figure: "
                  f"{', '.join(r['id'] for r in both)}")
            print("     Both are true. Metric 3 counts the abstention, "
                  "metric 4 counts the violation.")
        ur = sum(r["facts_unrendered"] for r in rs)
        rt = sum(r["facts_total_rendered"] for r in rs)
        rh = sum(r["facts_hit_rendered"] for r in rs)
        if ur:
            print(f"     of which {ur}/{ft} facts are corpus-checked only "
                  f"(self-written transcription; cannot detect a "
                  f"transcription error)")
            print(f"     render-verified subset: {rh}/{rt}"
                  + (f" = {rh / rt:.1%}" if rt else "  <-- nothing verified "
                                                   "against a page image yet"))

        # FIFTH NUMBER. Reported beside the four, never folded into them:
        # rule 7 is a readability gate, not a quality score, and averaging it
        # into recall would hide both. It exists because qwen scored a perfect
        # four-metric row on a Bengali question answered in English.
        if lang:
            bad_script = [r for r in lang if not r["language"]["script_ok"]]
            bad_lits = [r for r in lang if r["language"]["translated_literals"]]
            bad_ids = [r for r in lang
                       if r["language"].get("wrong_script_ids")]
            undec = sum(r["language"].get("undecidable_literals") or 0
                        for r in lang)
            print(f"  5. language match ..... {len(lang_ok)}/{len(lang)}"
                  + (f" = {len(lang_ok) / len(lang):.1%}" if lang else "")
                  + "   (rule 7; NOT merged into 1-4)")
            # SPLIT BY COMPONENT. One number over three different failures
            # hid that the middle one was re-reporting metric 1: before the
            # fix, 13 of 13 "translated" hits on the real run were literals
            # simply absent from the answer.
            print(f"     by component: wrong script {len(bad_script)}, "
                  f"translated literal {len(bad_lits)}, "
                  f"transliterated id {len(bad_ids)}")
            if undec:
                print(f"     {undec} absent literal(s) NOT judged for "
                      f"translation: no English name in any registry, so "
                      f"there is no\n     translated form to look for. Those "
                      f"are metric 1's alone. A low count above is\n     "
                      f"therefore not a clean bill of health.")
            if bad_script:
                print("     wrong script -- a Bengali question answered "
                      "predominantly in Latin:")
                for r in bad_script:
                    L = r["language"]
                    print(f"       {r['id']:<8} bengali share "
                          f"{L['bengali_share']}  ({L['answer_script']})")
            if bad_lits:
                print("     role/product literal translated away rather "
                      "than quoted in Bengali:")
                for r in bad_lits:
                    print(f"       {r['id']:<8} "
                          f"{r['language']['translated_literals']}")
            if bad_ids:
                print("     manual id or clause number transliterated into "
                      "Bengali -- not searchable in the manual:")
                for r in bad_ids:
                    print(f"       {r['id']:<8} "
                          f"{r['language']['wrong_script_ids']}")
            if not bad_script and not bad_lits and not bad_ids:
                print("     every answer matched its question's script, kept "
                      "its Bengali literals, and left ids in Latin")

    print("\n" + "=" * 62)
    print("SCORES ARE REPORTED SEPARATELY BY PROVENANCE AND NEVER MERGED.")
    print("chunk_derived shares assumptions with the code under test; it")
    print("measures agreement, not correctness.")
    print("=" * 62)
    for name in ("field_sourced", "chunk_derived", "unknown"):
        block(name, by.get(name, []))

    viol = [r for r in rows if r["violations"]]
    if viol:
        print("\nVIOLATIONS")
        for r in viol:
            for kind, what in r["violations"]:
                print(f"  {r['id']:<8} {kind:<14} {what}")

    bad_abst = [r for r in rows
                if r["answerable"] == "no" and not r["abstention"]]
    if bad_abst:
        print("\nFAILED TO ABSTAIN (invented an answer where none exists)")
        for r in bad_abst:
            print(f"  {r['id']:<8} {r['type']}")

    misses = [(r["id"], f) for r in rows for f in r["facts_missed"]]
    if misses:
        print(f"\nMISSED FACTS ({len(misses)})")
        for i, f in misses[:25]:
            print(f"  {i:<8} {f}")
    return 1 if (viol or bad_abst) else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default="gold/bootstrap.yaml")
    ap.add_argument("--answers", required=True,
                    help="jsonl: {id, answer, citations[]}")
    ap.add_argument("--forbidden-numbers",
                    help="json: {\"dabi_2025 §7.1\": [\"25000\", ...]} — "
                         "build it from the chunks of each named clause")
    ap.add_argument("--judge", choices=["literal", "llm"], default="literal",
                    help="llm adds a judge for prose facts; literal and "
                         "numeric facts are always matched deterministically")
    ap.add_argument("--require-render", action="store_true",
                    help="fail the run if any scored fact is corpus-checked "
                         "only; use once figure verification is done")
    ap.add_argument("--judge-provider", default="groq",
                    help="provider for --judge llm (default groq)")
    ap.add_argument("--judge-model", default="openai/gpt-oss-20b",
                    help="judge model; must differ from the generator")
    ap.add_argument("--retrieved", metavar="PATH",
                    help="the retrieval dump the answers were generated "
                         "from. With it, citation is split into accuracy "
                         "over questions whose gold chunk was in context and "
                         "a count of those where it was not. Without it, "
                         "citation is reported exactly as before")
    ap.add_argument("--out", default="out/eval_report.json")
    args = ap.parse_args()

    print("CANARY")
    if not canary_selftest():
        print("FATAL: fact matcher failed its canary. Scores are meaningless.")
        return 2

    gold = yaml.safe_load(Path(args.gold).read_text(encoding="utf-8"))
    qs = {q["id"]: q for q in gold["questions"]}
    answers = {}
    for line in Path(args.answers).read_text(encoding="utf-8").splitlines():
        if line.strip():
            a = json.loads(line)
            answers[a["id"]] = a

    forbidden = {}
    if args.forbidden_numbers:
        forbidden = json.loads(
            Path(args.forbidden_numbers).read_text(encoding="utf-8"))

    missing = [i for i in qs if i not in answers]
    if missing:
        print(f"\n[warn] no answer for: {', '.join(missing)}")

    judge, judge_label = None, "literal"
    if args.judge == "llm":
        judge, judge_label = make_provider_judge(args.judge_provider,
                                                 args.judge_model)
    # EVERY REPORT SAYS WHICH JUDGE PRODUCED IT. The literal and llm numbers
    # are not comparable -- the literal matcher requires a verbatim span and
    # the judge accepts a paraphrase -- so a number without its judge is
    # unreadable.
    print(f"\njudge: {judge_label}")
    ctx = load_context(args.retrieved) if args.retrieved else {}
    twins = load_twins() if args.retrieved else {}
    if args.retrieved:
        print(f"context {args.retrieved}: {len(ctx)} question(s); "
              f"{len(twins)} chunk(s) in a declared twin group")
    rows = [score_one(qs[i], answers[i], forbidden, judge,
                      ctx.get(qs[i].get("id")), twins)
            for i in qs if i in answers]
    code = report(rows)
    if args.require_render:
        ur = sum(r["facts_unrendered"] for r in rows)
        if ur:
            print(f"\nFAIL: {ur} facts are corpus-checked only. Flip them to "
                  f"verified_against: render after the blind second "
                  f"transcription has been diffed.")
            code = 1

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(f"\nreport -> {args.out}")
    return code


if __name__ == "__main__":
    sys.exit(main())
