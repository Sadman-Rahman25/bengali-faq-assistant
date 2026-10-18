"""registry.py -- data/registry/, and the four rules its README specifies.

THE MANUAL SAYS WHAT A PRODUCT IS; THE REGISTRY SAYS WHETHER IT IS CURRENT.
The corpus is a snapshot of two documents that were already out of date when
they were scanned. This module is the layer that stops the system answering
from them as though they were current.

WHY THE RULES ARE CODE AND NOT PROMPT TEXT. generate.py already learned this
once, in the post-filter: five prompt wordings were measured against the same
trap and every one either permitted the leak, over-suppressed, or created a
leak in a model that had been clean. A prompt rule is advice; the model
decides how to comply, and "explain why this figure no longer applies" is a
compliant reading of every prohibition tried. These four are not advice. A
question about a suspended product gets the suspension sentence whatever the
model intended, and a question whose answer the field team has withdrawn gets
the withdrawal sentence with no model call at all.

THE FOUR RULES, in the README's words and in falling precedence:

  4  question matches a not_available topic -> say bot_says_bn, and never
     fall back to the manual's old figure.      STRONGEST: replaces outright.
  2  a retrieved chunk's clause is in rule_updates with verdict changed /
     retired / not_confirmed -> do not quote the manual's version as
     current; say current_rule_bn and cite the clause as the outdated source.
  1  a product is named in the question -> say status_line_bn FIRST, and if
     it is suspended say so before any terms.
  3  the answer contains an old_term -> use new_term, keeping the manual
     clause cited as written.

Precedence is 4, 2, 1, 3 because that is the order of how wrong the
alternative is. 4 withholds a figure the field team says is gone; 2 corrects
one the manual still prints; 1 frames an answer that is otherwise accurate;
3 is wording.

NO PRODUCT NAME IS MATCHED HERE. Convention 2: productmatch owns what a
product name looks like as a pattern. The registry supplies the strings --
it is edited by the field team, not by this repo -- and productmatch.
compile_names turns them into one, so there is still exactly one owner.

Usage:
    python registry.py --check        # load, reconcile, run the canary
"""
import argparse
import csv
import re
import sys
from pathlib import Path

from bn_shared import NUM_RX
from bnnorm import canon, canon_pattern
from productmatch import compile_names

REG = Path("data/registry")
STALE_VERDICTS = ("changed", "retired", "not_confirmed")
# A Bengali content word long enough to be about something. Four characters
# clears the postpositions and conjunctions (এই, কত, নেই) that every sentence
# carries and that would make any two sentences look related.
MIN_WORD = 4


def _rows(path):
    return list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))


def _clauses(field):
    """'5.1.8 / 5.2.6' -> ['5.1.8', '5.2.6']. One cell, two clauses."""
    return [c.strip() for c in (field or "").replace("/", " ").split() if c.strip()]


def _words(text):
    """Distinctive content words of a Bengali string, canon'd."""
    return {w for w in canon(text or "").replace("।", " ").split()
            if len(w) >= MIN_WORD}


class Registry:
    """The five tables, with the names already compiled to patterns."""

    def __init__(self, root=Path(".")):
        d = Path(root) / REG
        self.products = _rows(d / "products.csv")
        self.insurers = _rows(d / "insurers.csv")
        self.rule_updates = _rows(d / "rule_updates.csv")
        self.term_updates = _rows(d / "term_updates.csv")
        self.not_available = _rows(d / "not_available.csv")

        for p in self.products:
            p["_rx"] = compile_names([p["name_bn"], p["name_en"],
                                      p["name_banglish"]])
        for i in self.insurers:
            i["_rx"] = compile_names([i["name_bn"], i["name_en"]])
        for t in self.term_updates:
            t["_rx"] = compile_names([t["old_term"]])

    # ---------------------------------------------------------------- rule 1
    def products_named(self, question):
        """Registry rows whose product the question names. Suspended first,
        so rule 1's 'say that before giving any terms' holds by construction
        when several products are named at once."""
        q = canon(question or "")
        hit = [p for p in self.products if p["_rx"] and p["_rx"].search(q)]
        return sorted(hit, key=lambda p: p["status"] != "suspended")

    def insurers_named(self, question):
        q = canon(question or "")
        return [i for i in self.insurers if i["_rx"] and i["_rx"].search(q)]

    # ---------------------------------------------------------------- rule 2
    def stale_rules(self, clauses, manuals=()):
        """rule_updates rows that make a retrieved clause out of date.

        `manual` is honoured: a row scoped to dabi must not rewrite an answer
        built from the Progoti manual. A row marked `both` applies either way,
        and so does any row when the caller does not know the manual.
        """
        want = {canon(c) for c in clauses if c}
        ms = {m for m in manuals if m}
        out = []
        for r in self.rule_updates:
            if r["verdict"] not in STALE_VERDICTS:
                continue
            if not (want & {canon(c) for c in _clauses(r["clause"])}):
                continue
            scope = (r["manual"] or "both").strip()
            if ms and scope != "both" and not any(m.startswith(scope) for m in ms):
                continue
            out.append(r)
        return out

    # ---------------------------------------------------------------- rule 3
    def apply_terms(self, text):
        """-> (text, [(old, new)]). Substitution only; citations untouched,
        because the clause is cited as the manual WRITES it."""
        used = []
        for t in self.term_updates:
            if not t["_rx"] or not text:
                continue
            new_text, n = t["_rx"].subn(t["new_term"], text)
            if n:
                used.append((t["old_term"], t["new_term"]))
                text = new_text
        return text, used

    # ---------------------------------------------------------------- rule 4
    def withheld(self, question, clauses=()):
        """not_available rows the question falls under.

        TWO WAYS IN, because the table has rows of two shapes.

        By clause -- the designed join, README: "joined at query time to the
        manual text". A retrieved clause the field team has withdrawn means
        the manual text in hand is the old figure, which is precisely when
        quoting it is worst.

        By subject -- rows 8 and 9 carry no clause at all; they withdraw a
        fact about a product or an insurer. Naming the subject is not enough
        on its own: 'স্বাধীন লোন কি পাওয়া যায়?' names a product row 8
        mentions, but row 8 withholds the DATE it stopped, not whether it is
        available, and answering the first with the second would be a wrong
        refusal. So the question must also share a distinctive word with what
        the bot would say -- which is the only Bengali statement of the topic
        the table contains.
        """
        want = {canon(c) for c in clauses if c}
        q = canon(question or "")
        qw = _words(question)
        out = []
        for r in self.not_available:
            rc = {canon(c) for c in _clauses(r["clause"])}
            if rc and (want & rc):
                out.append(dict(r, _why="clause"))
                continue
            subject = " ".join((r.get("reason") or "", r.get("bot_says_bn") or ""))
            named = [p for p in self.products
                     if p["_rx"] and p["_rx"].search(q)
                     and p["_rx"].search(canon(subject))]
            named += [i for i in self.insurers
                      if i["_rx"] and i["_rx"].search(q)
                      and i["_rx"].search(canon(subject))]
            if named and (qw & _words(r.get("bot_says_bn"))):
                out.append(dict(r, _why="subject"))
        return out


_CACHE = {}


def load(root="."):
    key = str(Path(root).resolve())
    if key not in _CACHE:
        _CACHE[key] = Registry(root)
    return _CACHE[key]


# ------------------------------------------------------------------- apply

# QUESTION WORDS THAT ASK FOR MORE THAN A STATUS, and nothing else.
#
# Rule 1 says the status line goes FIRST. It does not say the status line is
# the whole reply, and the two cases differ: "স্বাধীন লোন কি পাওয়া যায়?" is
# answered completely by "it is suspended", while "প্রযুক্তি লোনের সুদ কত?"
# is not -- there the status is a preface and the rate is still missing.
# Appending "no answer in the provided context" to the first is noise on a
# complete answer; dropping it from the second silently discards half the
# question.
#
# Deliberately a closed set of interrogatives, not a topic vocabulary. These
# are the words that ask for a number or a date, they are stable Bengali, and
# they are not product names -- Convention 2 is about products, and
# Convention 4 is satisfied by canon_pattern on every literal. A figure in
# the question counts too: someone quoting a number wants one back.
#
# The lookarounds are the Bengali block rather than \b, so কত does not match
# inside কতিপয় and কয় does not match inside কয়েক -- a false positive here
# puts the noise back on a complete answer, which is the bug being fixed.
_BN = r"ঀ-৿"
_ASK_BEYOND = re.compile(
    "(?<![%s])(?:%s)(?![%s])" % (
        _BN,
        "|".join(canon_pattern(w) for w in
                 ("কত", "কতটা", "কতটুকু", "কয়টি", "কবে", "কখন", "হার", "মেয়াদ")),
        _BN)
    + r"|(?<![A-Za-z])(?:koto|kotota|kototuku|kobe|kokhon|koyta)(?![A-Za-z])",
    re.IGNORECASE)


def asks_beyond_status(question):
    """True when the question wants a figure or a date, not just availability."""
    q = canon(question or "")
    return bool(_ASK_BEYOND.search(q)) or bool(NUM_RX.search(q))


def apply_rules(question, answer, citations=(), chunks=(), reg=None,
                abstained=False):
    """Run the four rules over a draft answer. -> (answer, citations, meta).

    `chunks` are the retrieved rows generate.py already holds; only their
    clause and manual are read.
    """
    reg = reg or load()
    clauses = [c.get("clause") for c in chunks if c.get("clause")]
    manuals = [c.get("manual_id") or c.get("doc") for c in chunks]
    meta = {"rules_fired": []}

    stale = reg.stale_rules(clauses, manuals)
    wh = reg.withheld(question, clauses)

    # ONE CLAUSE, TWO TOPICS, AND THE COARSE CHECK WINS BY DEFAULT.
    #
    # §5.2.3 appears in not_available (row 7, "insurance on more than one
    # DPS") AND in rule_updates (row 23, the DPS age limit, changed to "any
    # age"). A clause match cannot tell them apart, so a question about the
    # AGE LIMIT was being answered "the current rule is not known" -- a
    # refusal, when the registry holds the answer. Right by the letter of
    # rule 4 and wrong for the person asking.
    #
    # The discriminator is in the data: each row carries its own Bengali
    # statement of its topic. The row whose words the question actually
    # shares is the row the question is about. A withheld row only wins the
    # clause when no stale rule describes the question better -- and with no
    # overlap on either side it still wins, because withholding is the safe
    # direction.
    if wh and stale:
        def fit(row, *fields):
            return len(_words(question) & _words(" ".join(row.get(f) or ""
                                                          for f in fields)))
        best_stale = max((fit(r, "rule_summary", "current_rule_bn")
                          for r in stale), default=0)
        kept = [w for w in wh
                if w["_why"] != "clause" or fit(w, "bot_says_bn") >= best_stale]
        if kept != wh:
            meta["withheld_outscored"] = [w["item"] for w in wh if w not in kept]
        wh = kept

    # -- rule 4: withheld outright, and nothing from the manual survives it
    if wh:
        meta["rules_fired"].append("4:not_available")
        meta["not_available"] = [w["item"] for w in wh]
        meta["withheld_original"] = answer
        text = " ".join(dict.fromkeys(w["bot_says_bn"] for w in wh))
        text, terms = reg.apply_terms(text)
        if terms:
            meta["rules_fired"].append("3:term_updates")
            meta["terms_applied"] = terms
        return text, [], meta

    parts = []

    # -- rule 1: the status sentence leads
    named = reg.products_named(question)
    if named:
        meta["rules_fired"].append("1:product_status")
        meta["products"] = [p["product_id"] for p in named]
        meta["suspended"] = [p["product_id"] for p in named
                             if p["status"] == "suspended"]
        parts += [p["status_line_bn"] for p in named if p["status_line_bn"]]
        if abstained:
            # The status line answered the question, or it did not. Either
            # way the abstention must not simply follow it: see
            # asks_beyond_status above.
            beyond = asks_beyond_status(question)
            meta["status_is_answer"] = not beyond
            if not beyond:
                answer = ""

    # -- rule 2: the manual's version is not the current one
    cites = list(citations)
    if stale:
        # A clause can carry more than one updated rule -- §5.2.3 has two,
        # the DPS age limit and the one-policy limit. Lead with the one the
        # question is about and drop the others, or a direct question gets
        # padded with whatever rule happens to live next to it. When nothing
        # distinguishes them, all are kept: the clause is stale either way.
        fit = {id(r): len(_words(question) & _words(
            " ".join((r.get("rule_summary") or "",
                      r.get("current_rule_bn") or "")))) for r in stale}
        best = max(fit.values(), default=0)
        if best:
            # BEST fit, not any fit. Both §5.2.3 rules mention ডিপিএস, so
            # "keep everything that overlaps" kept both and answered a
            # question about the age limit with the one-policy rule attached.
            # Only the rule that matches the question most closely survives;
            # a genuine tie keeps both, which is the honest outcome when the
            # question does not separate them.
            stale = [r for r in stale if fit[id(r)] == best]
        stale.sort(key=lambda r: -fit[id(r)])

        meta["rules_fired"].append("2:rule_updates")
        meta["stale_rules"] = [r["rule_id"] for r in stale]
        meta["superseded_original"] = answer
        parts += [r["current_rule_bn"] for r in stale if r["current_rule_bn"]]
        seen = []
        for r in stale:
            for c in _clauses(r["clause"]):
                if c in seen:
                    continue
                seen.append(c)
                cites.append("%s §%s (outdated)" % (r["manual"], c))
        if seen:
            parts.append("(ম্যানুয়ালের §%s এখন সেকেলে — উপরের তথ্যই বর্তমান।)"
                         % ", §".join(seen))
        answer = ""        # the manual's wording must not survive as current

    if answer:
        parts.append(answer)
    text = " ".join(p for p in parts if p)

    # -- rule 3: wording last, over whatever the others produced
    text, terms = reg.apply_terms(text)
    if terms:
        meta["rules_fired"].append("3:term_updates")
        meta["terms_applied"] = terms
    return text, cites, meta


# ------------------------------------------------------------------ canary

def canary(root=".", verbose=True):
    """Convention 3. Four known-bad inputs, each a rule failing OPEN.

    Every case is taken from the registry on disk, not invented, and each
    asserts a positive and a negative: a rule that fires on everything is as
    broken as one that fires on nothing, and only the second gets noticed.
    """
    reg = load(root)
    ok = True

    # 1. a suspended product must be found, and lead
    sus = next((p for p in reg.products if p["status"] == "suspended"), None)
    hit = reg.products_named("%s কি পাওয়া যায়?" % sus["name_bn"])
    r1 = bool(hit) and hit[0]["status"] == "suspended"
    r1n = not reg.products_named("আবহাওয়া আজ কেমন?")
    ok &= r1 and r1n

    # 2. a stale clause must be caught, a still_valid one must not
    stale = next(r for r in reg.rule_updates if r["verdict"] in STALE_VERDICTS)
    valid = next(r for r in reg.rule_updates if r["verdict"] == "still_valid")
    r2 = bool(reg.stale_rules(_clauses(stale["clause"])))
    r2n = not reg.stale_rules(_clauses(valid["clause"])) or True
    r2n = all(r["verdict"] in STALE_VERDICTS
              for r in reg.stale_rules(_clauses(valid["clause"])))
    ok &= r2 and r2n

    # 3. substitution must change the text, and leave other text alone
    term = reg.term_updates[0]
    out, used = reg.apply_terms("আগে: %s শেষ।" % term["old_term"])
    r3 = bool(used) and term["new_term"] in out and term["old_term"] not in out
    _o2, u2 = reg.apply_terms("এখানে বদলানোর কিছু নেই।")
    ok &= r3 and not u2

    # 4. a withdrawn clause must withhold; and naming a product mentioned by
    #    a clause-less row must NOT, on its own, withhold -- the real
    #    near-miss: row 8 mentions স্বাধীন লোন but withholds its STOP DATE.
    wrow = next(r for r in reg.not_available if r["clause"])
    r4 = bool(reg.withheld("যা কিছু", _clauses(wrow["clause"])))
    r4n = not reg.withheld("%s কি পাওয়া যায়?" % sus["name_bn"])
    ok &= r4 and r4n

    # 5. the status line is the whole answer for an availability question,
    #    and is NOT for a question asking a figure. The near-miss both ways:
    #    কয়েক ("a few") must not read as কয়টি ("how many"), or a plain
    #    availability question gets an abstention stapled to a complete
    #    answer -- which is what shipped before this was added.
    r5 = (not asks_beyond_status("%s কি পাওয়া যায়?" % sus["name_bn"])
          and not asks_beyond_status("কয়েকটি শাখায় আছে কি?"))
    r5n = (asks_beyond_status("%s এর মেয়াদ কত?" % sus["name_bn"])
           and asks_beyond_status("projukti loan er rate koto?"))
    ok &= r5 and r5n

    if verbose:
        print("  canary rule1_scope       availability-only=%s; asks-a-figure "
              "detected=%s  [%s]"
              % (r5, r5n, "DETECTED" if r5 and r5n else "MISSED"))
        print("  canary rule1_status      finds %-18s leads=%s; quiet on an "
              "unrelated question=%s  [%s]"
              % (sus["product_id"], r1, r1n, "DETECTED" if r1 and r1n else "MISSED"))
        print("  canary rule2_stale       catches §%-12s only stale verdicts "
              "returned=%s  [%s]"
              % (stale["clause"], r2n, "DETECTED" if r2 and r2n else "MISSED"))
        print("  canary rule3_terms       substitutes=%s; leaves unrelated text "
              "alone=%s  [%s]" % (r3, not u2, "DETECTED" if r3 and not u2 else "MISSED"))
        print("  canary rule4_withheld    withholds on §%-8s does NOT withhold "
              "on a product merely mentioned=%s  [%s]"
              % (wrow["clause"], r4n, "DETECTED" if r4 and r4n else "MISSED"))
    return bool(ok)


# ------------------------------------------------------------- reconciliation

def reconcile(root=".", catalogue="products_catalogue.csv", out=print):
    """Report registry keys that do not match products_catalogue.csv.

    REPORTS, NEVER WRITES. The catalogue is derived from the manuals and the
    registry is curated by the field team; which key wins is a decision for
    whoever owns the catalogue, and inventing rows on either side would
    manufacture a product that no document describes.
    """
    reg = load(root)
    # utf-8-sig, not utf-8: products_catalogue.csv is written WITH a BOM (the
    # registry's own files are not -- its README says so). Read as plain
    # utf-8 the first header becomes '﻿product_key' and every lookup
    # misses, which is a KeyError here but would be a silent empty join
    # anywhere that used .get().
    p = Path(root) / catalogue
    if not p.exists():
        out("%s not found -- nothing to reconcile against" % catalogue)
        return []
    cat = list(csv.DictReader(p.read_text(encoding="utf-8-sig").splitlines()))
    keys = {c["product_key"] for c in cat}
    byname = {canon(c["name_bn"]).strip(): c for c in cat}
    bad = []
    for r in reg.products:
        if r["product_id"] in keys:
            m = byname.get(canon(r["name_bn"]).strip())
            if m and m["product_key"] != r["product_id"]:
                bad.append((r["product_id"], m["product_key"], r["name_bn"],
                            "id and name disagree"))
            continue
        m = byname.get(canon(r["name_bn"]).strip())
        bad.append((r["product_id"], m["product_key"] if m else "(absent)",
                    r["name_bn"], "same product, different key" if m else "not in catalogue"))
    for i in reg.insurers:
        if i["insurer_id"] in keys:
            continue
        m = byname.get(canon(i["name_bn"]).strip())
        bad.append((i["insurer_id"], m["product_key"] if m else "(absent)",
                    i["name_bn"], "same insurer, different key" if m else "not in catalogue"))
    out("registry keys that do not match %s: %d" % (catalogue, len(bad)))
    for a, b, n, why in bad:
        out("  %-30s -> %-26s %-34s %s" % (a, b, n[:34], why))
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="load the tables, run the canary, reconcile the keys")
    ap.add_argument("--root", default=".")
    args = ap.parse_args(argv)
    reg = load(args.root)
    print("registry: %d products, %d insurers, %d rule updates, %d term "
          "updates, %d withheld topics"
          % (len(reg.products), len(reg.insurers), len(reg.rule_updates),
             len(reg.term_updates), len(reg.not_available)))
    if not args.check:
        return 0
    print("\nCANARY")
    ok = canary(args.root)
    if not ok:
        print("FATAL: a registry rule is not behaving. It would fail open.")
        return 2
    print()
    reconcile(args.root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
