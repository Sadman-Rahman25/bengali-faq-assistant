"""quiz_recheck.py -- the quiz bank against data/registry/, then plausible
distractors, then what no person has read. NO MODEL, NO INDEX, NO NETWORK.

The bank was built from the manuals; the registry says which of the manuals'
rules are still current. A quiz question is a stronger claim than a chatbot
answer -- it marks the officer RIGHT or WRONG -- so a question the registry
has overtaken is worse than useless. This module removes those, makes the
surviving figure distractors plausible, and lists what still needs a reader.

ENFORCED, NOT PROMISED: main() first blocks every socket and refuses to
import the modules that reach a provider or the index (generate, embed,
retrieve, app, and the provider SDKs), then proves the block fires before it
reads anything. Every step after that is file reads and string comparison.
If any step tried to call a model, the run would stop with the reason.

STEP 1 -- what the registry overtakes. A question is dropped when:

  a) rule_updates: its clause carries a rule with verdict changed, retired
     or not_confirmed AND the question is about that rule. The join is
     registry.Registry.stale_rules itself -- same clause match, same manual
     scope, same topic_terms gate -- so the quiz cannot disagree with the
     chatbot about which rules a clause's question touches. Clause alone is
     the T-08 bug: retrieval near a rule is not a question about it.
  b) products: the question names a suspended product (registry
     products_named). Teaching the terms of a loan officers cannot offer
     teaches the wrong thing.
  c) term_updates: the correct answer or any option uses an old term, in the
     form the registry writes it or in the parts of a "Name (ABBR)" entry --
     so "CMS" alone counts. The parts are DERIVED from the registry row,
     never typed here.
  d) a distractor the registry now makes correct. The registry statements
     that are true now -- a still_valid row's summary and current text, any
     other row's current text -- are read for figures and role names. A
     distractor found there while the keyed answer is NOT is the worst quiz
     bug: the officer picks the true answer and is marked wrong. Dropped.
     A distractor found there ALONGSIDE the keyed answer is another row of
     the same table; reported for a reader, not dropped.

STEP 2 -- plausible distractors, figure questions only. A figure distractor
is plausible when it is a corpus figure span of the same unit (money at any
scale counts as one unit, quiz_build.unit_class), within one order of
magnitude of the answer, from a chunk that is not the answer's and shares
neither its clause number (across manuals: the Dabi and Progoti chapters are
twins) nor any product. "Product" is both of this repo's meanings: the
chunk's `product` field, which holds PROGRAMME keys, and the named products
productmatch.NAMED finds in the chunk -- the second is the one that matters,
because a figure from another clause about the same insurance or loan may be
true for it (Q-065's ceiling question drew a §5.1.5 payout for the same
insurance before it was added). Matched with productmatch's own patterns,
so Convention 2 holds. STRICT: a span qualifies only if NO chunk it
occurs in is excluded -- a span that also appears in the answer's own clause
may be a true value there. A distractor already meeting all of that is KEPT,
so a question whose options were plausible does not change and a reader's
read stays valid. Only failing distractors are re-picked, seeded, in place.
Every re-pick passes step 1d's gate and quiz_build.validate before it lands.
Role questions are untouched: a role name has no unit and no magnitude.

STEP 4 -- three generator bugs, checked across the whole bank and REPORTED,
never fixed: (a) two options that are one post, by the role vocabulary's
names and abbreviations; (b) a role answer the source names as the thing
checked ("X-এর কাজ", "X-এর করা", "X ভিত্তিক"); (c) a figure answer whose
source is a worked example; (d) a role answer found only inside a longer
word. Each check proves on stored chunks that it fires and that it stays
silent on a sound case, before it reads the bank.

STEP 3 -- out/quiz_unread.csv. The v3 additions (quiz/_v3_new_ids.json;
out/quiz_review_v3.md records the other 29 as read) that survive step 1,
PLUS any read question whose options step 2 changed -- its options are new,
so nobody has read the question as it now stands.

Usage:
    python quiz_recheck.py           # report only; writes nothing
    python quiz_recheck.py --write   # also rewrite the bank and the CSV
"""
import argparse
import csv
import importlib
import importlib.abc
import json
import random
import socket
import sys
from pathlib import Path

BANK = Path("quiz/bank_validated.yaml")
V3_IDS = Path("quiz/_v3_new_ids.json")
MANIFEST = Path("data/processed/chunk_manifest.json")
UNREAD = Path("out/quiz_unread.csv")

# Seed for the re-pick. Recorded in the bank's meta so the draw can be
# reproduced; change it only deliberately, since every re-picked option moves.
SEED = 20261019

# Plausibility bound: a distractor's value over the answer's.
MAX_RATIO = 10.0

# Everything that reaches a provider, the embedding API or the index.
FORBIDDEN_MODULES = ("generate", "embed", "retrieve", "app",
                     "google.genai", "google.generativeai", "openai",
                     "anthropic", "groq", "faiss", "torch",
                     "sentence_transformers", "requests", "httpx")


# ---------------------------------------------------------------- offline
class _NoModels(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if any(name == m or name.startswith(m + ".") for m in FORBIDDEN_MODULES):
            raise ImportError(f"quiz_recheck: refused to import {name!r} -- "
                              f"this run must not reach a model or the index")
        return None


def _no_network(*_a, **_k):
    raise RuntimeError("quiz_recheck: network access attempted -- this run "
                       "must not call a model or the embedding API")


def go_offline():
    """Block sockets and model imports, then prove both blocks fire.

    -> list of failures; empty means offline and verified. A module already
    imported before the guard is a failure too: the finder only sees NEW
    imports, so an earlier import would walk straight past it.
    """
    bad = [m for m in FORBIDDEN_MODULES if m in sys.modules]
    sys.meta_path.insert(0, _NoModels())
    socket.socket.connect = _no_network
    socket.socket.connect_ex = _no_network
    socket.create_connection = _no_network
    socket.getaddrinfo = _no_network
    # Convention 3: the guard proves it can fail before anything is trusted.
    try:
        socket.create_connection(("127.0.0.1", 9), timeout=0.1)
        bad.append("socket.create_connection was NOT blocked")
    except RuntimeError:
        pass
    try:
        importlib.import_module("generate")
        bad.append("import generate was NOT blocked")
    except ImportError:
        pass
    return bad


# --------------------------------------------------------------- helpers
def _bn_letter(ch):
    return bool(ch) and 0x0980 <= ord(ch) <= 0x09FF


def roles_in(text, names, canon, canon_pattern):
    """Role names written in `text`. -> set of names

    Longest name first, and a shorter name may not match inside a longer
    one already found -- পিএ is a prefix of পিএস. A match must also not be
    the tail of a longer word: বিএম sits inside এবিএম, the Assistant Branch
    Manager, who is not in the role vocabulary at all. Left side only:
    Bengali inflects on the right (সিডিওর), never on the left, so this
    drops no real occurrence.
    """
    t = canon(text or "")
    taken, found = [], set()
    for n in sorted(names, key=len, reverse=True):
        for m in canon_pattern(n).finditer(t):
            if m.start() and _bn_letter(t[m.start() - 1]):
                continue
            if any(m.start() < b and a < m.end() for a, b in taken):
                continue
            taken.append((m.start(), m.end()))
            found.add(n)
    return found


def figures_in(text, qb, bn_shared):
    """{(unit_class, value)} written in a registry sentence.

    Includes the low end of a range that writes its unit once: rule 8 says
    "৫০,০০০ থেকে ২,০০,০০০ টাকা", and figure_spans sees only the high end,
    because ৫০,০০০ is followed by a range word, not a unit. The low end
    inherits the high end's unit. Separators are quiz_build's own.
    """
    t = text or ""
    out = set()
    for sp, u in qb.figure_spans(t):
        v = qb.span_value(sp, u)
        if v is not None:
            out.add((qb.unit_class(u), v))
    for m in bn_shared.NUM_RX.finditer(t):
        after = t[m.end(): m.end() + 40]
        stripped = after.lstrip()
        sep = next((s for s in qb._RANGE_SEP if stripped.startswith(s)), None)
        if not sep:
            continue
        rest = stripped[len(sep):].lstrip()
        for sp, u in qb.figure_spans(rest):
            if rest.startswith(sp):
                v = qb.span_value(m.group(0), u)
                if v is not None:
                    out.add((qb.unit_class(u), v))
                break
    return out


def old_term_names(row):
    """An old term and its parts: "Name (ABBR)" -> [whole, Name, ABBR].

    Derived from the registry string so no term is typed here. A composite
    title with no parenthesis stays whole -- its slash-separated pieces are
    CURRENT role names and must not be matched as old ones.
    """
    whole = (row.get("old_term") or "").strip()
    names = [whole]
    if whole.endswith(")") and "(" in whole:
        head, _, abbr = whole[:-1].rpartition("(")
        names += [head.strip(), abbr.strip()]
    return [n for n in names if n]


def rules_about(reg, registry, q, verdicts=None):
    """rule_updates rows about this question, any verdict. Mirrors
    Registry.stale_rules exactly -- clause, manual scope, topic gate -- with
    the verdict filter lifted, because step 1d needs still_valid rows too.
    """
    from bnnorm import canon
    want = {canon(q["clause"])}
    out = []
    for r in reg.rule_updates:
        if verdicts and r["verdict"] not in verdicts:
            continue
        if not (want & {canon(c) for c in registry._clauses(r["clause"])}):
            continue
        scope = (r["manual"] or "both").strip()
        if scope != "both" and not q["manual_id"].startswith(scope):
            continue
        if not registry.topic_match(r, q["question_bn"]):
            continue
        out.append(r)
    return out


def true_now(r):
    """The registry's statement of what is current for this row."""
    if r["verdict"] == "still_valid":
        return " ".join((r.get("rule_summary") or "",
                         r.get("current_rule_bn") or ""))
    return r.get("current_rule_bn") or ""


# ---------------------------------------------------------------- step 1
def step1(qs, reg, registry, qb, bn_shared, canon, canon_pattern, compile_names):
    """-> (drops {id: [reason]}, notes [str])"""
    drops, notes = {}, []
    role_names = [n for n, _k in qb.role_vocabulary()]

    old_rx = [(t, compile_names(old_term_names(t))) for t in reg.term_updates]

    for q in qs:
        qid = q["id"]

        # a) stale rules, through the registry's own join
        for r in reg.stale_rules([q["clause"]], [q["manual_id"]],
                                 q["question_bn"]):
            drops.setdefault(qid, []).append(
                f"1a rule {r['rule_id']} is {r['verdict']}: "
                f"{r['rule_summary']} (§{r['clause']})")

        # b) suspended products named in the question
        for p in reg.products_named(q["question_bn"]):
            if p["status"] == "suspended":
                drops.setdefault(qid, []).append(
                    f"1b asks about a suspended product: {p['name_bn']}")

        # c) old terms in the answer or an option
        for t, rx in old_rx:
            if rx is None:
                continue
            for o in q["options"]:
                if rx.search(canon(o)):
                    drops.setdefault(qid, []).append(
                        f"1c option {o!r} uses the old term "
                        f"{t['old_term']!r} (now {t['new_term']!r})")
            # NOT a drop under 1c's wording, but a reader should see it
            for field in ("question_bn", "explanation"):
                m = rx.search(canon(q.get(field) or ""))
                if m:
                    notes.append(f"{qid} {field} uses old term "
                                 f"{t['old_term']!r} ({m.group(0)!r}); "
                                 f"answer and options do not -- kept")

        # d) distractors the registry now makes correct
        distractors = [o for o in q["options"] if o != q["correct"]]
        for r in rules_about(reg, registry, q):
            stmt = true_now(r)
            if not stmt.strip():
                continue
            if q["kind"] == "figure":
                figs = figures_in(stmt, qb, bn_shared)
                seg = q.get("explanation") or ""
                cu = qb.unit_of(q["correct"], seg)
                key_c = (qb.unit_class(cu), qb.span_value(q["correct"], cu))
                for d in distractors:
                    du = qb.unit_class(qb.figure_spans(d)[0][1]) \
                        if qb.figure_spans(d) else None
                    key_d = (du, qb.span_value(d, qb.figure_spans(d)[0][1])
                             if qb.figure_spans(d) else None)
                    if key_d[1] is None or key_d not in figs:
                        continue
                    if key_c in figs:
                        notes.append(
                            f"{qid} 1d AMBIGUOUS: rule {r['rule_id']} "
                            f"states both the answer {q['correct']!r} and "
                            f"distractor {d!r} -- another row of the same "
                            f"rule; kept, read it")
                    else:
                        drops.setdefault(qid, []).append(
                            f"1d distractor {d!r} is now correct: rule "
                            f"{r['rule_id']} ({r['verdict']}) says "
                            f"{stmt.strip()!r}; the keyed answer "
                            f"{q['correct']!r} is not in it")
            else:
                said = roles_in(stmt, role_names, canon, canon_pattern)
                for d in distractors:
                    if d not in said:
                        continue
                    if q["correct"] in said:
                        notes.append(
                            f"{qid} 1d AMBIGUOUS: rule {r['rule_id']} names "
                            f"both {q['correct']!r} and {d!r}; kept, read it")
                    else:
                        drops.setdefault(qid, []).append(
                            f"1d distractor {d!r} is now correct: rule "
                            f"{r['rule_id']} ({r['verdict']}) says "
                            f"{stmt.strip()!r}")

        # d, term updates: an old-term answer makes the new term correct
        for t, rx in old_rx:
            if rx is None or not rx.search(canon(q["correct"])):
                continue
            new_roles = roles_in(t["new_term"], role_names, canon, canon_pattern)
            for d in distractors:
                if d in new_roles:
                    drops.setdefault(qid, []).append(
                        f"1d distractor {d!r} is the new term for the "
                        f"answer's old one ({t['old_term']!r})")
    return drops, notes


def step1_context(qs, reg, registry, canon):
    """What the gate and the registry's other tables say, for the report.
    Nothing here drops anything."""
    lines = []
    # The T-08 comparison: what clause-alone would have dropped.
    clause_only = {}
    for q in qs:
        hits = reg.stale_rules([q["clause"]], [q["manual_id"]])  # no question
        if hits:
            clause_only[q["id"]] = [r["rule_id"] for r in hits]
    lines.append(f"clause alone (the T-08 join) would flag: "
                 f"{clause_only or 'nothing'}")
    # Rows the registry joins at a PARENT clause -- not reached by exact join
    for r in reg.rule_updates:
        if r["verdict"] not in registry.STALE_VERDICTS:
            continue
        for c in registry._clauses(r["clause"]):
            kids = [q["id"] for q in qs if q["clause"].startswith(c + ".")
                    and registry.topic_match(r, q["question_bn"])]
            if kids:
                lines.append(
                    f"rule {r['rule_id']} ({r['verdict']}, §{c}) is about "
                    f"§{c}; on-topic questions in its SUB-clauses: {kids}. "
                    f"Not joined -- the registry matches clauses exactly")
    # not_available is not one of a-d, but a quiz figure on a withheld topic
    # is worth a reader's eye
    for q in qs:
        w = reg.withheld(q["question_bn"], [q["clause"]], [q["manual_id"]])
        for row in w:
            lines.append(f"{q['id']} falls under not_available item "
                         f"{row['item']} ({row['topic']}, §{row['clause']}) "
                         f"-- not one of 1a-1d, kept; read it")
    # products named only in the evidence, not the question
    for q in qs:
        ev = reg.products_named(q.get("explanation") or "")
        sus = [p["name_bn"] for p in ev if p["status"] == "suspended"]
        if sus and not any(p["status"] == "suspended"
                           for p in reg.products_named(q["question_bn"])):
            lines.append(f"{q['id']} evidence names a suspended product "
                         f"{sus} but the question does not -- kept; read it")
    return lines


# ---------------------------------------------------------------- step 2
def chunk_products(c, named, canon):
    """Programme tags plus named products, for one chunk. -> set of keys"""
    t = canon(c.get("display_text") or "")
    return (set(c.get("product") or [])
            | {k for k, rx in named if rx.search(t)})


def build_pool(chunks, qb, clause_pair, redact, named, canon):
    """span -> [occurrence] over every clause-bearing, retrievable chunk."""
    pool = {}
    for c in chunks:
        if redact.is_excluded(c["chunk_id"]):
            continue
        clause, _disp = clause_pair(c)
        if not clause:
            continue
        for sp, u in qb.figure_spans(c.get("display_text") or ""):
            pool.setdefault(sp, []).append({
                "chunk_id": c["chunk_id"], "clause": clause,
                "products": chunk_products(c, named, canon), "unit": u})
    return pool


def eligible(span, q, ans, pool, qb, canon, taken, registry_true):
    """Is `span` a plausible distractor for q? -> (bool, why_not)"""
    occ = pool.get(span)
    if not occ:
        return False, "not a corpus figure span"
    if any(o["chunk_id"] == q["chunk_id"] for o in occ):
        return False, "occurs in the answer's chunk"
    # quiz_build.validate's own test, which is a RAW SUBSTRING test: ৫ দিন is
    # "in" a chunk that prints ৩৫ দিন. The span comparison above cannot see
    # that, and the re-pick must never propose an option validate refuses --
    # measured: Q-073 (answer ৩৫ দিন) was offered ৫ দিন and the gate stopped it.
    if span in ans["chunk_text"]:
        return False, "is a substring of the answer's chunk text"
    if any(o["clause"] == q["clause"] for o in occ):
        return False, "occurs in a chunk with the answer's clause"
    if any(o["products"] & ans["products"] for o in occ):
        return False, "occurs in a chunk sharing a product"
    u = occ[0]["unit"]
    if qb.unit_class(u) != ans["unit_class"]:
        return False, "different unit"
    v = qb.span_value(span, u)
    if not v or not ans["value"]:
        return False, "no value"
    ratio = v / ans["value"]
    if not (1 / MAX_RATIO <= ratio <= MAX_RATIO):
        return False, f"{ratio:.3g}x the answer"
    if canon(span) in taken["canon"] or (ans["unit_class"], v) in taken["value"]:
        return False, "same as the answer or another option"
    if (ans["unit_class"], v) in registry_true:
        return False, "the registry makes it true for this question"
    return True, ""


def step2(qs, chunks_by_id, pool, reg, registry, qb, bn_shared, canon, rng,
          named):
    """-> (changes {id: (before, after)}, short {id: [(option, why)]})

    `short` names every old option that FAILED the test and stayed only
    because no replacement qualified -- with the reason, since "occurs in
    the answer's clause" means it may be another true row of the same table.
    """
    changes, short = {}, {}
    for q in qs:
        if q["kind"] != "figure":
            continue
        chunk = chunks_by_id[q["chunk_id"]]
        seg = q.get("explanation") or ""
        cu = qb.unit_of(q["correct"], seg) or qb.unit_of(
            q["correct"], chunk.get("display_text") or "")
        ans = {"unit_class": qb.unit_class(cu),
               "value": qb.span_value(q["correct"], cu),
               "products": chunk_products(chunk, named, canon),
               "chunk_text": chunk.get("display_text") or ""}
        registry_true = set()
        for r in rules_about(reg, registry, q):
            registry_true |= figures_in(true_now(r), qb, bn_shared)

        taken = {"canon": {canon(q["correct"])},
                 "value": {(ans["unit_class"], ans["value"])}}
        keep, bad_slots, why_bad = list(q["options"]), [], {}
        for i, o in enumerate(q["options"]):
            if o == q["correct"]:
                continue
            ok, why = eligible(o, q, ans, pool, qb, canon, taken, registry_true)
            if not ok:
                why_bad[i] = why
            if ok:
                taken["canon"].add(canon(o))
                taken["value"].add((ans["unit_class"],
                                    qb.span_value(o, pool[o][0]["unit"])))
            else:
                bad_slots.append(i)
        if not bad_slots:
            continue

        cands = sorted(s for s in pool
                       if eligible(s, q, ans, pool, qb, canon, taken,
                                   registry_true)[0])
        rng.shuffle(cands)
        for i in bad_slots:
            pick = next((s for s in cands
                         if eligible(s, q, ans, pool, qb, canon, taken,
                                     registry_true)[0]), None)
            if pick is None:
                short.setdefault(q["id"], []).append((q["options"][i],
                                                      why_bad[i]))
                continue
            keep[i] = pick
            taken["canon"].add(canon(pick))
            taken["value"].add((ans["unit_class"],
                                qb.span_value(pick, pool[pick][0]["unit"])))
        if keep != q["options"]:
            changes[q["id"]] = (list(q["options"]), keep)
    return changes, short


# ---------------------------------------------------------------- step 4
# THREE GENERATOR BUGS, found by reading 49 questions by hand. They explain
# most of the 32 that failed, so each is checked across the whole bank.
# REPORT ONLY: a hit is listed for a reader and never fixed here, because
# each fix is a judgement about what the source says.
#
#   4a  two options are one post: a full name and its abbreviation (শাখা
#       ব্যবস্থাপক / বিএম), or one role's per-manual names. pick_distractors
#       de-duplicated by spelling, not by post.
#   4b  the role answer is what the source CHECKS, not who checks: "X-এর
#       কাজ", "X-এর করা", "X ভিত্তিক". Q-030's answer was the officer whose
#       work the branch manager verifies.
#   4c  a figure answer whose source is a worked example: a calculation, or
#       a word that introduces one. Q-016's ২৪% was the rate in an example.
#   4d  a role answer that only occurs inside a longer word. Q-039's পিএ was
#       the first two letters of পিএলসি.
_GENITIVE = ("এর", "ের", "র")
_CHECKED = ("কাজ", "করা")
_BASIS = ("ভিত্তিক",)
_EXAMPLE_WORDS = ("উদাহরণ", "যেমন", "দেখুন")
# The honorific plural, which neither list below carries: Q-045's source
# writes শাখা ব্যবস্থাপকগণ, and without it 4d read the post as part of a
# longer word.
_PLURAL_ENDINGS = ("গণ", "গণের", "গণকে")


def _alt(words, canon_pattern):
    """One regex alternation of literals, longest first, nukta-tolerant."""
    return "(?:%s)" % "|".join(canon_pattern(w).pattern for w in
                               sorted(words, key=len, reverse=True))


def same_post_options(q, keys, canon):
    """4a. -> [(option, option, role key)] for options naming one post."""
    seen, out = {}, []
    for o in q["options"]:
        k = keys.get(canon(o))
        if k is None:
            continue
        if k in seen:
            out.append((seen[k], o, k))
        else:
            seen[k] = o
    return out


def object_of_checking(q, canon, canon_pattern):
    """4b. -> [snippet] where the role answer is written as the thing
    checked. Role questions only; read from the explanation, the segment
    shown beside the answer."""
    import re
    if q["kind"] != "role":
        return []
    name = canon_pattern(q["correct"]).pattern
    rx = re.compile(
        "%s(?:[-’]?\\s*%s\\s*%s|\\s+%s)" % (
            name, _alt(_GENITIVE, canon_pattern),
            _alt(_CHECKED, canon_pattern), _alt(_BASIS, canon_pattern)))
    t = canon(q.get("explanation") or "")
    return [t[max(0, m.start() - 20): m.end() + 10]
            for m in rx.finditer(t)
            if not (m.start() and _bn_letter(t[m.start() - 1]))]


def worked_example(q, heading, canon, canon_pattern):
    """4c. -> [marker] showing a figure answer comes from a worked example:
    an × or = sign, a digit-x-digit product, or a word introducing one, in
    the explanation or the heading above it."""
    import re
    if q["kind"] != "figure":
        return []
    t = canon((q.get("explanation") or "") + " " + (heading or ""))
    found = [m.group(0) for m in
             re.finditer(_alt(_EXAMPLE_WORDS, canon_pattern), t)]
    found += [m.group(0) for m in re.finditer(r"\d\s*[xX×]\s*[\d(]", t)]
    if "=" in t:
        found.append("=")
    if "×" in t and not any("×" in f for f in found):
        found.append("×")
    return found


def only_inside_a_word(q, endings, canon, canon_pattern):
    """4d. -> True when the role answer occurs in its explanation but never
    as a word of its own. An inflection on the right (পিও-র, ব্যবস্থাপকের)
    still counts as the word; anything else on either side does not."""
    if q["kind"] != "role":
        return False
    t = canon(q.get("explanation") or "")
    hits = list(canon_pattern(q["correct"]).finditer(t))
    if not hits:
        return False                  # absent: quiz_build.validate's job
    for m in hits:
        if m.start() and _bn_letter(t[m.start() - 1]):
            continue
        tail = t[m.end():]
        if not tail or not _bn_letter(tail[0]):
            return False
        for e in endings:
            if tail.startswith(e) and not _bn_letter(tail[len(e):][:1]):
                return False
    return True


def audit_canary(by_id, qb, registry, canon, canon_pattern):
    """Convention 3. Each check fires on the failure it exists for and stays
    silent on a sound case. Fixtures are sliced from stored chunks and role
    names come from the vocabulary; nothing is retyped. -> (ok, [lines])"""
    vocab = qb.role_vocabulary()
    keys = {canon(n): k for n, k in vocab}
    names = {}
    for n, k in vocab:
        names.setdefault(k, []).append(n)
    endings = _endings(qb, registry, canon)
    lines, ok = [], True

    def fixture(cid, correct, kind, pick=None):
        c = by_id.get(cid) or {}
        text = c.get("display_text") or ""
        seg = pick(text) if pick else qb.explanation_sentence(text, correct)
        return ({"kind": kind, "correct": correct, "explanation": seg,
                 "options": []},
                " > ".join(c.get("heading_path") or []))

    def line(check, case, got, want):
        nonlocal ok
        good = bool(got) == want
        ok &= good
        lines.append(f"  {check}  {case:<44} hit={bool(got)}  "
                     f"[{'PASS' if good else 'FAIL'}]")

    bm = names["branch_manager"]
    others = [names[k][0] for k in ("credit_officer", "programme_sohokari")]
    line("4a", "full name beside its abbreviation",
         same_post_options({"options": bm[:2] + others}, keys, canon), True)
    line("4a", "four different posts",
         same_post_options({"options": [names[k][0] for k in (
             "branch_manager", "credit_officer", "programme_sohokari",
             "adc")]}, keys, canon), False)

    fo = next(n for n in names["field_officer"]
              if canon_pattern(n).search(canon(
                  by_id["progoti_0175"]["display_text"])))
    work = canon_pattern(_CHECKED[0])

    def checked_seg(text):
        return next(s for s in qb.role_segments(text)
                    if canon_pattern(fo).search(canon(s))
                    and work.search(canon(s)))
    neg, _h = fixture("progoti_0175", fo, "role", checked_seg)
    line("4b", "progoti_0175: the officer whose work is checked",
         object_of_checking(neg, canon, canon_pattern), True)
    rm = names["regional_manager"][-1]
    pos, _h = fixture("progoti_0139", rm, "role")
    line("4b", "progoti_0139: the RM ensures the discussion",
         object_of_checking(pos, canon, canon_pattern), False)

    ex_text = by_id["dabi_2025_0256"]["display_text"]
    fig = qb.figure_spans(ex_text)[0][0]
    neg, h = fixture("dabi_2025_0256", fig, "figure")
    line("4c", "dabi_2025_0256: premium in a calculation",
         worked_example(neg, h, canon, canon_pattern), True)
    pos, h = fixture("dabi_2025_0254", fig, "figure")
    line("4c", "dabi_2025_0254: the same rate as the rule",
         worked_example(pos, h, canon, canon_pattern), False)

    pa = names["programme_assistant"][0]
    neg, _h = fixture("dabi_2025_0256", pa, "role")
    line("4d", "dabi_2025_0256: PA inside the insurer's PLC",
         only_inside_a_word(neg, endings, canon, canon_pattern), True)
    rm_q, _h = fixture("progoti_0139", rm, "role")
    line("4d", "progoti_0139: the RM written as a word",
         only_inside_a_word(rm_q, endings, canon, canon_pattern), False)
    bm_q, _h = fixture("progoti_0187", bm[0], "role")
    line("4d", "progoti_0187: the BM with a plural ending",
         only_inside_a_word(bm_q, endings, canon, canon_pattern), False)
    lines.append("  " + ("DETECTED" if ok else "FAILED"))
    return ok, lines


def _endings(qb, registry, canon):
    """Case and plural endings a role name may carry and still be the word:
    registry's topic endings, quiz_build's object suffixes and the
    honorific plural, Bengali only."""
    out = {canon(e) for e in registry._TOPIC_ENDINGS + qb._OBJECT_SUFFIX
           + _PLURAL_ENDINGS}
    return sorted((e for e in out if e and all(_bn_letter(c) for c in e)),
                  key=len, reverse=True)


def step4(qs, by_id, qb, registry, canon, canon_pattern):
    """-> {check: [(id, detail)]} over every question given."""
    keys = {canon(n): k for n, k in qb.role_vocabulary()}
    endings = _endings(qb, registry, canon)
    hits = {"4a": [], "4b": [], "4c": [], "4d": []}
    for q in qs:
        c = by_id.get(q["chunk_id"]) or {}
        heading = " > ".join(c.get("heading_path") or [])
        for a, b, k in same_post_options(q, keys, canon):
            right = q["correct"] in (a, b)
            hits["4a"].append((q["id"], f"{a} / {b} ({k})"
                               + ("  -- one is the answer: TWO RIGHT"
                                  if right else "  -- both distractors")))
        snips = object_of_checking(q, canon, canon_pattern)
        if snips:
            hits["4b"].append((q["id"], f"{q['correct']}: "
                               + " | ".join(s.strip() for s in snips)))
        marks = worked_example(q, heading, canon, canon_pattern)
        if marks:
            hits["4c"].append((q["id"], f"{q['correct']}: markers "
                               + ", ".join(sorted(set(marks)))))
        if only_inside_a_word(q, endings, canon, canon_pattern):
            hits["4d"].append((q["id"], f"{q['correct']} occurs only inside "
                               f"a longer word"))
    return hits


# ---------------------------------------------------------------- step 3
def citation(q, manuals):
    """দাবি অপারেশন ম্যানুয়াল ২০২৫ §১.১.১ -- quiz_app.citation's form, read
    from the manifest directly so app.py is never imported."""
    name = manuals.get(q["manual_id"], q["manual_id"])
    disp = q.get("clause_display") or q.get("clause") or ""
    return f"{name} §{disp}" if disp else name


def write_unread(rows, read_but_changed, manuals, out):
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        f.write("# Quiz questions no person has read as they now stand.\n")
        f.write("# v3 additions never reviewed, plus read questions whose "
                "options changed in the distractor re-pick: "
                + (", ".join(read_but_changed) or "none") + "\n")
        w = csv.writer(f)
        w.writerow(["id", "question_bn", "option_1", "option_2", "option_3",
                    "option_4", "correct", "explanation", "citation"])
        for q in rows:
            w.writerow([q["id"], q["question_bn"], *q["options"],
                        q["correct"], q["explanation"], citation(q, manuals)])


# ------------------------------------------------------------------ main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true",
                    help="rewrite quiz/bank_validated.yaml and "
                         "out/quiz_unread.csv")
    args = ap.parse_args(argv)

    print("OFFLINE GUARD")
    bad = go_offline()
    if bad:
        for b in bad:
            print(f"  FAIL {b}")
        print("STOPPED -- a step could reach a model or the network.")
        return 2
    print("  sockets blocked, model/index imports refused  [VERIFIED]\n")

    # Imported only now, behind the guard: if any of these pulled in a
    # provider module, the import above would have raised.
    import yaml
    import bn_shared
    import quiz_build as qb
    import redact
    import registry
    from bn_shared import canon_pattern
    from bnnorm import canon
    from dedupe import clause_pair
    from productmatch import compile_names

    reg = registry.load()
    if not registry.canary(verbose=False):
        print("STOPPED -- registry canary failed; its rules would fail open.")
        return 2

    raw = BANK.read_text(encoding="utf-8")
    doc = yaml.safe_load(raw)
    qs = doc["questions"]
    n_before = len(qs)
    manuals = json.loads(MANIFEST.read_text(encoding="utf-8"))["manuals"]
    v3 = set(json.loads(V3_IDS.read_text(encoding="utf-8")))

    # ---- step 1
    drops, notes = step1(qs, reg, registry, qb, bn_shared, canon,
                         canon_pattern, compile_names)
    # The one rule the brief named as a trap: rule 14 is still valid.
    r14 = next(r for r in reg.rule_updates if r["rule_id"] == "14")
    r14_qs = [q["id"] for q in qs if rules_about(reg, registry, q)
              and r14 in rules_about(reg, registry, q)]
    print("STEP 1 -- registry re-check")
    print(f"  questions before: {n_before}")
    for qid in sorted(drops):
        q = next(x for x in qs if x["id"] == qid)
        print(f"\n  DROP {qid}  ({q['kind']}, {q['manual_id']} "
              f"§{q['clause']})  {q['question_bn']}")
        print(f"       keyed answer: {q['correct']}   options: "
              f"{' / '.join(q['options'])}")
        for why in drops[qid]:
            print(f"       - {why}")
    kept = [q for q in qs if q["id"] not in drops]
    print(f"\n  questions after: {len(kept)}  "
          f"({n_before - len(kept)} dropped)")
    print(f"\n  rule 14 ({r14['verdict']}) applies to {r14_qs} -- "
          f"{'kept' if all(i not in drops for i in r14_qs) else 'DROPPED (wrong)'}")
    changed_hits = sorted(i for i, rs in drops.items()
                          if any(" is changed:" in w for w in rs))
    print(f"  dropped on a CHANGED rule: {changed_hits or 'none'}")
    print("\n  notes (nothing below was dropped):")
    for line in notes + step1_context(qs, reg, registry, canon):
        print(f"    - {line}")

    # ---- step 2
    chunks = qb.load_chunks()
    by_id = {c["chunk_id"]: c for c in chunks}
    import productmatch as PM
    named = [(k, PM.compile_pat(pat)) for k, _n, _a, pat in PM.NAMED]
    pool = build_pool(chunks, qb, clause_pair, redact, named, canon)
    rng = random.Random(SEED)
    changes, short = step2(kept, by_id, pool, reg, registry, qb, bn_shared,
                           canon, rng, named)

    # validate every changed question: the re-pick may not make one worse
    for q in kept:
        if q["id"] not in changes:
            continue
        text = by_id[q["chunk_id"]].get("display_text") or ""
        before = set(qb.validate(q, text))
        after_q = dict(q, options=changes[q["id"]][1])
        after = set(qb.validate(after_q, text))
        worse = after - before
        if worse:
            print(f"STOPPED -- re-pick made {q['id']} fail validation: {worse}")
            return 2
        # and step 1d again, on the new options
        d2, _n = step1([after_q], reg, registry, qb, bn_shared, canon,
                       canon_pattern, compile_names)
        if d2:
            print(f"STOPPED -- re-pick made {q['id']} fail step 1: {d2}")
            return 2

    n_fig = sum(1 for q in kept if q["kind"] == "figure")
    print("\nSTEP 2 -- plausible distractors")
    stuck = sorted(i for i in short if i not in changes)
    print(f"  figure questions: {n_fig}   changed: {len(changes)}   "
          f"already plausible: {n_fig - len(changes) - len(stuck)}   "
          f"no replacement exists: {len(stuck)} {stuck}")
    print(f"  role questions untouched: "
          f"{sum(1 for q in kept if q['kind'] == 'role')}")
    if short:
        print("  no plausible replacement exists; these OLD options stay, and "
              "each failed the test:")
        for qid, items in short.items():
            for o, why in items:
                print(f"    {qid}  {o:<12} {why}")
    for qid, (b, a) in changes.items():
        q = next(x for x in kept if x["id"] == qid)
        print(f"\n  {qid}  answer {q['correct']}   {q['question_bn']}")
        print(f"     before: {' / '.join(o for o in b if o != q['correct'])}")
        print(f"     after : {' / '.join(o for o in a if o != q['correct'])}")

    # ---- step 4 (report only)
    print("\nSTEP 4 -- generator-bug checks across the bank (report only)")
    ok4, lines4 = audit_canary(by_id, qb, registry, canon, canon_pattern)
    for ln in lines4:
        print(ln)
    if not ok4:
        print("STOPPED -- a step 4 check cannot detect the bug it is for.")
        return 2
    audited = [dict(q, options=changes[q["id"]][1]) if q["id"] in changes
               else q for q in kept]
    hits4 = step4(audited, by_id, qb, registry, canon, canon_pattern)
    for check, title in (("4a", "two options are the same post"),
                         ("4b", "answer is the post being checked"),
                         ("4c", "figure answer from a worked example"),
                         ("4d", "role answer only inside a longer word")):
        print(f"\n  {check} {title}: {len(hits4[check])} hit(s) "
              f"in {len(audited)} questions")
        for qid, detail in hits4[check]:
            print(f"    {qid}  {detail}")

    # ---- step 3
    for q in kept:
        if q["id"] in changes:
            q["options"] = changes[q["id"]][1]
    # A RE-RUN MUST NOT FORGET. Which read questions were re-optioned is only
    # visible in the pass that changed them; a later pass finds them already
    # plausible and changes nothing. So the record in the bank's meta is the
    # memory, and unread is computed from it as well as from this run --
    # measured: without this, a second --write shrank the sheet from 50 rows
    # to 44 and replaced the drop record with an empty one.
    meta = doc["meta"]
    prior = meta.get("registry_recheck") or {}
    repicked = set(changes) | set(prior.get("distractors_repicked") or [])
    kept_ids = {q["id"] for q in kept}
    read_changed = sorted(i for i in repicked if i not in v3 and i in kept_ids)
    unread = [q for q in kept if q["id"] in v3 or q["id"] in repicked]
    print(f"\nSTEP 3 -- unread: {len(unread)} "
          f"({sum(1 for q in unread if q['id'] in v3)} never reviewed, "
          f"{len(read_changed)} read but re-optioned: {read_changed})")

    if not args.write:
        print("\nreport only -- nothing written. Re-run with --write.")
        return 0

    if drops or changes:
        dropped = {d["id"]: d["reason"] for d in prior.get("dropped") or []}
        dropped.update({i: "; ".join(drops[i]) for i in drops})
        doc["questions"] = kept
        meta["n_questions"] = len(kept)
        meta["registry_recheck"] = {
            "by": "quiz_recheck.py",
            "n_before": prior.get("n_before", n_before),
            "n_after": len(kept),
            "dropped": [{"id": i, "reason": dropped[i]} for i in sorted(dropped)],
            "distractors_repicked": sorted(repicked),
            "repick_seed": SEED,
        }
        out = qb.BANK_HEADER + yaml.safe_dump(doc, allow_unicode=True,
                                              sort_keys=False, width=100)
        # LF, explicitly: the repo stores LF (.gitattributes eol=lf) and the
        # bank was LF on disk. Without it, Windows text mode writes CRLF and
        # every line of the bank shows as changed in the working copy.
        BANK.write_text(out, encoding="utf-8", newline=chr(10))
        print(f"\nwrote {BANK} ({len(kept)} questions)")
    else:
        print(f"\n{BANK} unchanged -- nothing dropped, nothing re-picked")
    write_unread(unread, read_changed, manuals, UNREAD)
    print(f"wrote {UNREAD} ({len(unread)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
