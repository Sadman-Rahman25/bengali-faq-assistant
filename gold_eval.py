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


def untranslated_literals(q, answer):
    """Bengali role/product literals in the gold facts MISSING from the
    answer. -> [str]

    Only Bengali non-figure literals are checked: a figure like ৩ লক্ষ is
    fact recall's job, and an English gold fact has nothing to translate.
    Uses fact_present, the same matcher recall uses, so the two cannot
    disagree about whether a literal is present.
    """
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
        if not fact_present(lit, answer):
            out.append(lit)
    return out


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


def cite_ok(expected, got):
    """Manual name and clause both required. Missing manual name = wrong."""
    if not expected:
        return None                               # not scored
    g = {norm_cite(x) for x in got}
    return all(any(norm_cite(e) in x or x in norm_cite(e) for x in g)
               for e in expected)


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


def score_one(q, ans, forbidden_numbers=None, judge=None):
    answer = ans.get("answer", "")
    facts = q.get("gold_facts", []) or []
    hit = [f for f in facts
           if fact_present(fact_text(f), answer)
           or (judge and judge(fact_text(f), answer))]
    # Facts checked only against a self-written transcription cannot detect a
    # transcription error. They are scored, but reported separately so the
    # headline number never rests on them.
    unrendered = [f for f in facts if fact_verified(f) != "render"]

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
        "facts_total": len(facts),
        "facts_hit": len(hit),
        "facts_missed": [fact_text(f) for f in facts if f not in hit],
        "facts_unrendered": len(unrendered),
        "facts_hit_rendered": len([f for f in hit
                                   if fact_verified(f) == "render"]),
        "facts_total_rendered": len([f for f in facts
                                     if fact_verified(f) == "render"]),
        "citation": cite_ok(q.get("must_cite"), ans.get("citations", [])),
        # Fifth number. Never folded into the four above.
        "language": language_match(q, answer),
        "abstention": abstention,
        # the raw signal, so a row can be read without re-deriving it
        "abstained": abstained,
        "violation_free": not violations,
        "violations": violations,
    }


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
        print(f"  1. fact recall ........ {fh}/{ft} = "
              f"{(fh / ft if ft else 0):.1%}")
        print(f"  2. citation accuracy .. "
              f"{sum(1 for r in cited if r['citation'])}/{len(cited)}"
              + (f" = {sum(1 for r in cited if r['citation']) / len(cited):.1%}"
                 if cited else " (none scored)"))
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
            print(f"  5. language match ..... {len(lang_ok)}/{len(lang)}"
                  + (f" = {len(lang_ok) / len(lang):.1%}" if lang else "")
                  + "   (rule 7; NOT merged into 1-4)")
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

    judge = make_llm_judge() if args.judge == "llm" else None
    rows = [score_one(qs[i], answers[i], forbidden, judge)
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
