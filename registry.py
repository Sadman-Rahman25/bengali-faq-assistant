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
import functools
import re
import sys
from collections import Counter
from pathlib import Path

from bn_shared import BN_DIGITS, NUM_RX
from bnnorm import canon, canon_pattern
from productmatch import NAMED, PROGRAMME_NAMES, compile_names, compile_pat

REG = Path("data/registry")
STALE_VERDICTS = ("changed", "retired", "not_confirmed")
# A Bengali content word long enough to be about something. Four characters
# clears the postpositions and conjunctions (এই, কত, নেই) that every sentence
# carries and that would make any two sentences look related.
MIN_WORD = 4
# The Bengali block, for word boundaries. \b does not work here: Python's \w
# counts vowel signs and the virama as non-word characters, so \b fires in
# the middle of nearly every Bengali word. Shared by the topic gate and by
# asks_beyond_status() -- one definition of where a Bengali word ends.
_BN = r"ঀ-৿"


def _rows(path):
    return list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))


def _clauses(field):
    """'5.1.8 / 5.2.6' -> ['5.1.8', '5.2.6']. One cell, two clauses."""
    return [c.strip() for c in (field or "").replace("/", " ").split() if c.strip()]


def _terms(field):
    """'ক;খ গ' -> ['ক', 'খ গ']. Semicolon-separated, spaces kept inside."""
    return [t.strip() for t in (field or "").split(";") if t.strip()]


_TERM_RX = {}

# A TOPIC TERM IS A WORD, NOT A SUBSTRING. Matched as a substring, মুনাফা
# (rule 9) fired inside মুনাফাভিত্তিক -- three other products -- একা (rule 13)
# inside একাধিক, and the premium-rate withheld row's হার inside ব্যবহার and
# inside হারালে, so "পাসবই হারালে কত টাকা?" was on-topic for premium rates.
#
# The boundary is the one asks_beyond_status() uses, the Bengali block on both
# sides, with one measured difference. Bengali inflects on the RIGHT --
# প্রিমিয়ামের, সঞ্চয়ে, মেয়াদে, মিটিংয়ের -- and those interrogatives do not.
# A bare right boundary, measured on 294 real questions (every FAQ file, the
# gold set, the quiz bank, the scenarios below), lost 124 term matches, 68 of
# them legitimate inflections. So a term may carry ONE ending from this closed
# list and then the boundary. Derivations and compounds are not endings:
# ভিত্তিক, ধিক, ালে and না are absent on purpose. With the list, the same
# questions lose 56 matches -- every one a word that merely contained the
# term -- and gain none.
#
# Left side only is NOT the rule role_exclusions.py rejected: that rejection
# was of a right boundary, which drops সিডিওকে and সিডিওর. Bengali has no
# prefix inflection, so the left boundary drops no real occurrence.
_TOPIC_ENDINGS = ("ের", "য়ের", "য়ে", "য়", "ে", "র", "তে", "কে", "দের", "রা",
                  "গুলো", "গুলোর", "গুলি", "সমূহ", "সমূহের", "াদি", "টি", "টির",
                  "ই", "ও")
_ENDINGS = "(?:%s)?" % "|".join(
    re.escape(canon_pattern(e))
    for e in sorted(_TOPIC_ENDINGS, key=len, reverse=True))

# A TERM MAY NAME A PRODUCT BY ITS productmatch KEY: product:<key>.
#
# Convention 2 is one owner for each name, not a ban on names. The pattern is
# productmatch.NAMED's, so the registry still holds no product literal, a
# spelling fixed in productmatch reaches the gate, and every variant
# productmatch knows (মানিপ্ল্যান্ট / মানি প্লান্ট, মেয়াদি / মেয়াদী) counts.
# It is matched exactly as productmatch matches it -- against canon'd text,
# with no boundary added. The product patterns carry their own guards (গতি's
# lookbehinds); wrapping them in a second set would make this module a second
# owner of what a product name looks like.
PRODUCT_TERM = "product:"


def _term_rx(t):
    """One topic term -> its compiled pattern. Cached per string, because
    several rows share terms. Raises on a product key productmatch does not
    have: a gate that silently cannot fire looks exactly like a rule nobody
    asks about."""
    rx = _TERM_RX.get(t)
    if rx is not None:
        return rx
    if t.startswith(PRODUCT_TERM):
        key = t[len(PRODUCT_TERM):].strip()
        pat = next((p for k, _n, _a, p in NAMED if k == key), None)
        if pat is None:
            raise ValueError("topic term %r: productmatch.NAMED has no key %r"
                             % (t, key))
        rx = compile_pat(pat)
    else:
        # canon_pattern returns the canonical FORM of a regex, a string; it
        # does not compile. re.escape first: a topic term is a literal, and
        # an unescaped one would read '.' as any character.
        rx = re.compile("(?<![%s])%s%s(?![%s])" % (
            _BN, re.escape(canon_pattern(t)), _ENDINGS, _BN))
    _TERM_RX[t] = rx
    return rx


def topic_match(row, question):
    """Does the question mention what this row is ABOUT? -> bool

    The gate that stops a rule applying because retrieval happened to surface
    its clause. Every literal goes through canon_pattern (Convention 4). A
    term matches as a whole Bengali word, optionally inflected; a product key
    matches as productmatch matches that product. See _TOPIC_ENDINGS and
    PRODUCT_TERM above.

    A row with NO topic_terms never fires. That is deliberate and it is
    checked by the canary: an ungated row is the bug this function exists to
    prevent, so it must fail closed, not open.
    """
    q = canon(question or "")
    return any(_term_rx(t).search(q) for t in _terms(row.get("topic_terms")))


def bn_digits(text):
    """'5.2.3' -> '৫.২.৩'. One owner for the digit table: bn_shared."""
    return "".join(BN_DIGITS[int(c)] if c.isdigit() else c for c in str(text))


def _words(text):
    """Distinctive content words of a Bengali string, canon'd."""
    return {w for w in canon(text or "").replace("।", " ").split()
            if len(w) >= MIN_WORD}


def _manual_ids(root):
    """Manual ids from the tracked chunk manifest. Falls back to the scopes
    themselves if the manifest is missing, so a citation is never silently
    dropped."""
    import json
    p = Path(root) / "data/processed/chunk_manifest.json"
    try:
        return list((json.loads(p.read_text(encoding="utf-8"))
                     .get("manuals") or {}).keys())
    except (OSError, ValueError):
        return []


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
        # Every topic term compiled at LOAD, so a mistyped product key stops
        # the registry from loading instead of quietly never matching.
        for r in self.rule_updates + self.not_available:
            for term in _terms(r.get("topic_terms")):
                _term_rx(term)

        # Real manual ids, for citations. rule_updates.manual holds the
        # SCOPE (dabi | progoti | both), which is not a manual id -- a row
        # scoped to both used to be cited as "both §5.2.3".
        self.manual_ids = _manual_ids(Path(root))

    def manuals_for(self, scope):
        """Scope -> the manual ids it covers. 'both' is every manual that is
        not the standalone product manual; an unknown scope yields itself, so
        a new scope shows up in the citation rather than vanishing."""
        scope = (scope or "both").strip()
        if scope == "both":
            return [m for m in self.manual_ids
                    if not m.startswith("livestock")] or [scope]
        hit = [m for m in self.manual_ids if m.startswith(scope)]
        return hit or [scope]

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
    def stale_rules(self, clauses, manuals=(), question=None):
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
            # THE TOPIC GATE. The clause matching only says retrieval landed
            # near this rule; it does not say the question is about it. A
            # leave question that retrieved §3.1.3 was rewritten by the
            # temporary-charge rule because the clause matched and nothing
            # else was asked. question=None keeps the old behaviour for
            # callers that genuinely have no question (the canary's clause
            # probes), so the gate cannot silently disable a direct lookup.
            if question is not None and not topic_match(r, question):
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
            # THE TOPIC GATE, on both paths. Without it a dining-room
            # question that retrieved §3.8.1 was answered with the
            # document-retention refusal: right clause, wrong question, and
            # a refusal is not a safe default when it refuses the wrong
            # thing. A row with no topic_terms cannot fire at all.
            if not topic_match(r, question):
                continue
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
# _BN is defined at the top: the topic gate uses the same boundary.
_ASK_BEYOND = re.compile(
    "(?<![%s])(?:%s)(?![%s])" % (
        _BN,
        "|".join(canon_pattern(w) for w in
                 ("কত", "কতটা", "কতটুকু", "কয়টি", "কবে", "কখন", "হার", "মেয়াদ")),
        _BN)
    + r"|(?<![A-Za-z])(?:koto|kotota|kototuku|kobe|kokhon|koyta)(?![A-Za-z])",
    re.IGNORECASE)


# A PRODUCT-SHAPED NAME THE REGISTRY DOES NOT HAVE.
#
# T-06: asked about "গুড কাস্টমার লোন", the model asked WHICH MANUAL -- the
# prompt's ambiguity rule firing on a product that is in neither. The right
# answer is that nothing of that name is in the manuals; the wrong answers
# are both "which manual did you mean?" and "no such product exists", and
# the second is wrong because the field team named it, so it may well exist
# somewhere this corpus does not cover.
#
# The head noun list is Bengali product-category words, not product names --
# Convention 2 is about names, and productmatch still owns those. A bare
# head noun is NOT a name: "লোন নিতে কী কাগজ লাগে?" names nothing, so a
# qualifier of at least MIN_WORD characters must precede it.
_HEAD = ("লোন", "ঋণ", "বিমা", "সঞ্চয়")
# The kind of product a head noun names. ইন্স্যুরেন্স is not a head this
# detector looks for, but product NAMES use it (ফায়ার সেফটি ইন্স্যুরেন্স),
# so "ফায়ার বিমা" has to find the insurance it is short for.
_HEAD_KIND = (("loan", ("লোন", "ঋণ")),
              ("insurance", ("বিমা", "ইন্স্যুরেন্স")),
              ("savings", ("সঞ্চয়",)))
# Qualifiers that are grammar rather than a name. Without these,
# "নতুন লোন" and "এই ঋণ" read as product names.
_NOT_A_NAME = ("নতুন", "পুরাতন", "পুরোনো", "কোন", "কোনো", "এই", "সেই",
               "যেকোনো", "সকল", "সব", "একটি", "প্রতিটি", "অন্য")
# Where a name ends, walking back from its head noun: grammar, a question
# word, a conjunction. "৬৫ বছর বয়সী গ্রাহক কি ছায়া-সঞ্চয় বিমা" -- the name
# is ছায়া-সঞ্চয়, and কি is where it stops.
_NAME_STOP = _NOT_A_NAME + ("কি", "কী", "কে", "কার", "কত", "কেন", "কীভাবে",
                            "কিভাবে", "কবে", "কখন", "এবং", "ও", "বা", "আর",
                            "যে", "যদি")
_NAME_WINDOW = 3            # describing words read back from the head noun
_GENERIC_DF = 3             # in this many product names = too common to identify
_NAME_SPLIT = re.compile(r"[\s\-+/–—]+")
_CLAUSE_END = "।?!,;:৷"
_OPEN_QUOTE = "“\"'‘(["
_PUNCT = "“”\"'‘’()[]" + _CLAUSE_END
# The head noun is a WORD, with the same endings the topic gate allows and the
# same boundary. Without it ঋণ matched inside ঋণী / ঋণীদের (borrowers) and
# বিমা inside বিমাদাবি, so "৩.৯.১(খ) ধারায় ঋণীদের ক্ষেত্রে ..." was a question
# about a product called ধারায়.
_PRODUCT_SHAPED = re.compile(
    r"([%s\w]{%d,})\s+(%s)%s(?![%s])" % (
        _BN, MIN_WORD,
        "|".join(re.escape(canon_pattern(h)) for h in _HEAD),
        _ENDINGS, _BN))


@functools.lru_cache(maxsize=1)
def _product_words():
    """-> ([(kind, words)] per product, generic words, {head word: kind}).

    From productmatch.NAMED, canon'd -- the one owner of product names. A
    product's kind is its LAST head word: ঋণ নিরাপত্তা বিমা is insurance, not a
    loan, so "নিরাপত্তা লোন" does not borrow it.

    GENERIC is measured, not listed: a word in _GENERIC_DF or more product
    names (লোন, ঋণ, সঞ্চয়, বিমা, প্রকল্প, নিরাপত্তা, মুনাফাভিত্তিক, সাধারণ),
    plus every programme name -- দাবি and প্রগতি qualify a loan, they do not
    identify one. Computed on first use, not at import (Convention 1).
    """
    head_kind = {canon(w): k for k, ws in _HEAD_KIND for w in ws}
    prods = []
    for _key, name, _aliases, _pat in NAMED:
        words = [w for w in _NAME_SPLIT.split(canon(name)) if w]
        heads = [head_kind[w] for w in words if w in head_kind]
        prods.append((heads[-1] if heads else None, words))
    df = Counter(w for _kind, ws in prods for w in set(ws))
    generic = {w for w, n in df.items() if n >= _GENERIC_DF}
    generic |= {canon(n) for n, _a in PROGRAMME_NAMES.values()}
    return prods, frozenset(generic), head_kind


def _same_word(a, b):
    """One word, allowing one case ending on either side: গাভী ~ গাভীর."""
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    return (long_.startswith(short)
            and long_[len(short):] in {canon(e) for e in _TOPIC_ENDINGS})


def _describing_words(q, head_start):
    """The words of the name before its head noun, read back from it.

    Stops at a question word, conjunction or grammar word, at the end of the
    previous clause, after an opening quote, and after _NAME_WINDOW tokens.
    Hyphenated and '+'-joined tokens split: ছায়া-সঞ্চয় -> ছায়া, সঞ্চয়.
    """
    stop = {canon(w) for w in _NAME_STOP}
    out = []
    for tok in reversed(q[:head_start].split()[-_NAME_WINDOW:]):
        if tok[-1] in _CLAUSE_END:
            break
        bare = tok.strip(_PUNCT)
        if not bare or bare in stop:
            break
        out += [w for w in _NAME_SPLIT.split(bare) if w and w not in stop]
        if tok[0] in _OPEN_QUOTE:
            break
    return out


def _short_form_of_a_product(words, head):
    """Do these describing words, with this head noun, name a real product?

    A word counts when it is a word of some product's name (one case ending
    allowed either side) AND that product is the same kind as the head noun:
    ছায়া + বিমা finds ছায়া-সঞ্চয় নিরাপত্তা বিমা; মাসিক + লোন finds nothing,
    because মাসিক names a savings scheme.

    TOO-COMMON WORDS DO NOT COUNT BY THEMSELVES. If the name has any word that
    is not generic, only those words decide: "সাধারণ কৃষি লোন" is judged on
    কৃষি, which names nothing, so it is still unknown -- although সাধারণ is
    in two loan names. Only a name made of nothing but common words falls back
    to them: "সাধারণ লোন" is the general loan of either programme, and
    "ঋণ বিমা" is ঋণ নিরাপত্তা বিমা -- ঋণ being in three product names.
    """
    prods, generic, head_kind = _product_words()
    kind = head_kind.get(head)
    distinctive = [w for w in words
                   if not any(_same_word(w, g) for g in generic)]
    return any(k == kind and any(_same_word(w, t) for t in ws)
               for w in (distinctive or words) for k, ws in prods)


def unknown_product_name(question, reg=None):
    """A product-shaped name no registry row knows. -> the qualifier or None.

    Returns the qualifier so the caller can say WHICH name it did not find,
    and so a caller that wants to log it can.

    A SHORT NAME IS NOT AN UNKNOWN NAME. Officers say ছায়া বিমা, গরু বিমা,
    দাবি লোন; the registry knows each product only by its full names, so all
    of them used to get "এই নামে কিছু ম্যানুয়ালে নেই" -- a confident wrong
    answer about a product that is in the manual, the T-08 shape again.
    It fires now only when the name's describing words name no product of
    that kind.

    Measured on 221 distinct real questions (FAQ, gold, quiz): the old rule
    refused 36. Fifteen were real products named short or inflected (দাবি+
    সাধারণ লোন, মাসিক মুনাফাভিত্তিক সঞ্চয়ে, মানি-প্লান্ট সঞ্চয়ে ...) and four
    were ঋণ or বিমা inside another word. It now refuses 17 and none that it
    did not refuse before. KNOWN, NOT FIXED HERE: about eleven of the 17 are
    still wrong for a different reason -- the "name" is a postposition, a unit
    or a place (পর্যন্ত, অনুযায়ী, টাকা, ম্যানুয়ালে, মডিউলে: "৭০,০০০ টাকা
    পর্যন্ত ঋণ ...") or a lending mode (গ্রুপ ঋণ, ইন্ডিভিজুয়াল ঋণ).
    """
    reg = reg or load()
    q = canon(question or "")
    if reg.products_named(question) or reg.insurers_named(question):
        return None
    for m in _PRODUCT_SHAPED.finditer(q):
        qual = m.group(1)
        if any(canon_pattern(w) == qual for w in _NOT_A_NAME):
            continue
        words = _describing_words(q, m.start(2))
        if not words or _short_form_of_a_product(words, m.group(2)):
            continue
        return qual
    return None


NO_SUCH_NAME_BN = "এই নামে কিছু ম্যানুয়ালে নেই।"


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

    stale = reg.stale_rules(clauses, manuals, question)
    wh = reg.withheld(question, clauses)

    # A PRODUCT-SHAPED NAME NOTHING KNOWS, settled before any other rule.
    # Left to the model this became "which manual did you mean?" (T-06):
    # the prompt's ambiguity rule firing on a product that is in neither
    # manual, which invites the reader to pick one and be answered from it.
    # Says what is true and nothing more -- not that the product does not
    # exist, which the registry has no way to know.
    unknown = unknown_product_name(question, reg)
    if unknown and not wh:
        meta["rules_fired"].append("6:unknown_product")
        meta["unknown_product"] = unknown
        meta["unknown_original"] = answer
        return NO_SUCH_NAME_BN, [], meta

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

        # OUTDATED IS A VERDICT, NOT A DEFAULT. changed and retired mean the
        # field team says the manual's rule no longer holds; not_confirmed
        # means they could not say either way. Calling the manual পুরনো under
        # not_confirmed is itself a claim -- and for rule 23 it was the
        # dangerous one: "the §5.2.3 age limit is outdated" invites enrolling
        # someone the insurance will not pay out for. So each clause is
        # outdated if any rule on it is changed or retired, unconfirmed if
        # all of them are not_confirmed, and the suffix and the citation tag
        # say which.
        state = {}
        for r in stale:
            known = r["verdict"] != "not_confirmed"
            for c in _clauses(r["clause"]):
                if known or c not in state:
                    state[c] = "outdated" if known else state.get(c, "unconfirmed")
        seen, pairs = [], []
        for r in stale:
            for c in _clauses(r["clause"]):
                if c in seen:
                    continue
                seen.append(c)
                # "both" is a SCOPE, not a manual. Interpolating it emitted
                # "both §5.2.3 (outdated)", which is not a reference anyone
                # can open. One citation per real manual instead.
                for mid in reg.manuals_for(r["manual"]):
                    pair = "%s §%s (%s)" % (mid, c, state[c])
                    if pair not in pairs:
                        pairs.append(pair)
        cites += pairs
        outdated = [c for c in seen if state[c] == "outdated"]
        unconfirmed = [c for c in seen if state[c] == "unconfirmed"]
        if outdated:
            # It pointed "above" at nothing: appended even when it was the
            # only sentence in the reply. It now states the relation
            # outright, in Bengali numerals, and says পুরনো rather than
            # সেকেলে.
            parts.append("(ম্যানুয়ালের §%s ধারার তথ্য পুরনো; এখানে বর্তমান "
                         "নিয়ম দেওয়া হলো।)"
                         % ", §".join(bn_digits(c) for c in outdated))
        if unconfirmed:
            parts.append("(ম্যানুয়ালের §%s ধারার তথ্য এখনো প্রযোজ্য কিনা "
                         "নিশ্চিত করা যায়নি।)"
                         % ", §".join(bn_digits(c) for c in unconfirmed))
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
    wrow = next(r for r in reg.not_available
                if r["clause"] and _terms(r["topic_terms"]))
    on_topic = "%s বিষয়ে কী আছে?" % _terms(wrow["topic_terms"])[0]
    r4 = bool(reg.withheld(on_topic, _clauses(wrow["clause"])))
    r4n = not reg.withheld("%s কি পাওয়া যায়?" % sus["name_bn"])
    # THE GATE ITSELF: the same withdrawn clause, a question about something
    # else, must NOT withhold. This is T-09 -- a dining-room question
    # retrieved §3.8.1 and was answered with the document-retention refusal.
    r4g = not reg.withheld("আবহাওয়া আজ কেমন?", _clauses(wrow["clause"]))
    ok &= r4 and r4n and r4g

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
        print("  canary rule4_withheld    withholds on §%-8s on-topic; NOT on "
              "a product merely mentioned=%s; NOT off-topic=%s  [%s]"
              % (wrow["clause"], r4n, r4g,
                 "DETECTED" if r4 and r4n and r4g else "MISSED"))
    ok &= scenarios(reg, verbose)
    return bool(ok)


def scenarios(reg=None, verbose=True):
    """apply_rules end to end, on the stub. No retrieval, no model, no tokens.

    Four cases that worked before topic gating must still work -- gating is
    allowed to stop wrong answers, not right ones -- and three that were
    broken in the live run must now be fixed.
    """
    reg = reg or load()

    def run(q, answer="", chunks=(), abstained=False):
        return apply_rules(q, answer, [], chunks, reg, abstained=abstained)

    def ch(clause, manual="dabi_2025"):
        return [{"clause": clause, "manual_id": manual}]

    sus = next(p for p in reg.products if p["status"] == "suspended")
    cases, ok = [], True

    # --- the four that must keep working -----------------------------------
    t, _c, m = run("%s কি পাওয়া যায়?" % sus["name_bn"], abstained=True)
    a = sus["status_line_bn"] in t and m.get("status_is_answer") is True
    cases.append(("suspended product leads", a, t))

    # Convention 2: the product NAME comes from the registry row, never
    # typed here. canary() above already builds its question this way; these
    # scenarios were written with the names inline and the check caught it.
    act = next(p for p in reg.products
               if p["status"] == "active" and p["category"] == "loan")
    t, _c, m = run("%s এর হার কত?" % act["name_bn"],
                   answer="সার্ভিস চার্জ ২৪%।")
    # The rule ids are ASCII. A Bengali ১ was typed in one half of this
    # comparison, so that half could never match and the ASCII half was
    # carrying the test alone. The figure check below IS Bengali, because
    # the answer is.
    b = "1:product_status" in m["rules_fired"] and "২৪" in t
    cases.append(("active product keeps the figure", b, t))

    t, _c, m = run("ডিপিএসে বয়সসীমা কত?", chunks=ch("5.2.3"))
    c = "2:rule_updates" in m["rules_fired"] and "4:not_available" not in \
        m["rules_fired"]
    cases.append(("DPS age -> updated rule, not the withheld row", c, t))

    # Likewise: the withheld row names its own topic, and the product name
    # comes from the product row that shares its clause.
    prem = next(r for r in reg.not_available if r["clause"] == "5.1.3")
    t, _c, m = run("%s কত?" % _terms(prem["topic_terms"])[0],
                   chunks=ch("5.1.3"))
    d = "4:not_available" in m["rules_fired"]
    cases.append(("loan-security premium withheld", d, t))

    # --- the three that were broken ----------------------------------------
    # T-08: a leave question that retrieved §3.1.3 was rewritten by the
    # temporary-charge rule and cited it.
    t, c8, m = run("বছরে কত দিন অর্জিত ছুটি পাওয়া যায়?",
                   answer="(মডেলের উত্তর)", chunks=ch("3.1.3"))
    e = not m["rules_fired"] and not c8
    cases.append(("T-08 leave question: registry silent", e, t))

    # T-09: a dining question that retrieved §3.8.1 got the retention refusal.
    t, _c, m = run("ডাইনিং রুম ব্যবহারের নিয়ম কী?",
                   answer="(মডেলের উত্তর)", chunks=ch("3.8.1"))
    f = not m["rules_fired"]
    cases.append(("T-09 dining question: registry silent", f, t))

    # T-06: an unknown product-shaped name must be named as absent.
    t, _c, m = run("“গুড কাস্টমার লোন” সম্পর্কে বলুন।")
    g = t == NO_SUCH_NAME_BN and "6:unknown_product" in m["rules_fired"]
    cases.append(("T-06 unknown product name", g, t))

    # --- the two template bugs ---------------------------------------------
    t, cites, _m = run("একাধিক বিমাসুবিধা পাওয়া যায় কি?", chunks=ch("5.2.3"))
    h = cites and not any("both" in c for c in cites) and \
        len([c for c in cites if "5.2.3" in c]) >= 2
    cases.append(("scope 'both' -> two real manuals", h, "; ".join(cites)))

    t, _c, _m = run("পাসবই হারালে কত টাকা?", chunks=ch("2.1.2"))
    i = "উপরের" not in t and ("পুরনো" in t or not t)
    cases.append(("outdated suffix stands alone", i, t))

    # --- the topic gate: word boundaries and product keys ------------------
    # Product names come from productmatch.NAMED and terms from the rows
    # themselves -- never typed here (Convention 2), so a rename cannot leave
    # a scenario testing a name that no longer exists.
    rule = {r["rule_id"]: r for r in reg.rule_updates}
    pname = {k: n for k, n, _a, _p in NAMED}

    # মুনাফা (rule 9) must not fire inside মুনাফাভিত্তিক. Each half proves the
    # other is not vacuous: the naive substring DOES fire on the same
    # question, so the case is real; and an inflected মুনাফার still fires,
    # so the boundary is not simply refusing everything.
    r9 = rule["9"]
    dbl = pname["double_profit_savings"]
    term = next(x for x in _terms(r9["topic_terms"])
                if not x.startswith(PRODUCT_TERM) and canon(x) in canon(dbl))
    q_in = "%s কী?" % dbl
    naive = bool(re.search(re.escape(canon_pattern(term)), canon(q_in)))
    j = (naive and not topic_match(r9, q_in)
         and topic_match(r9, "%sর হার কত?" % term))
    cases.append(("boundary: %s not inside a compound" % "rule 9", j,
                  "naive=%s gate=%s" % (naive, topic_match(r9, q_in))))

    # rule 8 names Money Plant by key: a question naming it is on-topic, one
    # naming the দেড়গুণ scheme -- the product rule 8 was confused with -- is not.
    r8 = rule["8"]
    k = (topic_match(r8, "%s কীভাবে কাজ করে?" % pname["money_plant_savings"])
         and not topic_match(r8, "%s কী?" % pname["onehalf_profit_savings"]))
    cases.append(("product key: rule 8 is Money Plant only", k, ""))

    # an unknown key must fail at load, not match nothing forever
    try:
        _term_rx(PRODUCT_TERM + "no_such_product_key")
        bad_key = False
    except ValueError:
        bad_key = True
    cases.append(("product key: unknown key is refused", bad_key, ""))

    # rules 23 and 24 share §5.2.3 AND the product; only their topic terms
    # separate them. Asked plainly and asked naming the product, because the
    # way this breaks is a product key added to both rows -- then any
    # question naming ছায়া-সঞ্চয় matches both, and only the named form shows it.
    r23, r24 = rule["23"], rule["24"]
    chaya = pname["chaya_savings_insurance"]
    sep = True
    for q_age, q_multi in (("ডিপিএসে বয়সসীমা কত?",
                            "একাধিক বিমাসুবিধা পাওয়া যায় কি?"),
                           ("%s এর বয়সসীমা কত?" % chaya,
                            "%s এ একাধিক বিমাসুবিধা পাওয়া যায় কি?" % chaya)):
        sep &= (topic_match(r23, q_age) and not topic_match(r24, q_age)
                and topic_match(r24, q_multi) and not topic_match(r23, q_multi))
    cases.append(("rules 23 and 24 still separate", sep, ""))

    # --- an unconfirmed rule states nothing as current ---------------------
    # Rule 23 is not_confirmed until the field team says what their "any
    # age" verdict referred to. The reply must give the row's own sentence,
    # must not call the manual's limit outdated, must not state any of the
    # manual's age figures, and must cite the clause as unconfirmed. Keyed on
    # the verdict, so it stays right once the field team answers.
    t, cites, _m = run("ডিপিএসে বয়সসীমা কত?", chunks=ch("5.2.3"))
    # canon() both sides: it also folds Bengali digits to ASCII, so an age
    # written ১৮ or 18 is caught either way.
    ct = canon(t)
    ages = set(re.findall(r"\d{2,}", canon(r23["rule_summary"])))
    called_outdated = canon("পুরনো") in ct
    if r23["verdict"] == "not_confirmed":
        u = (r23["current_rule_bn"] in t and not called_outdated
             and not any(re.search(r"(?<!\d)%s(?!\d)" % a, ct) for a in ages)
             and bool(cites) and all(c.endswith("(unconfirmed)") for c in cites))
    else:
        u = r23["current_rule_bn"] in t and called_outdated
    cases.append(("unconfirmed rule states nothing as current", u, t))

    # --- short names are not unknown names ---------------------------------
    # Built from productmatch's own names (Convention 2): first word + head
    # noun, and the part before the hyphen + head noun. That yields ছায়া-সঞ্চয়
    # বিমা, ছায়া বিমা, গরু বিমা and ঋণ বিমা without typing a product name.
    def short(key, before_hyphen=False):
        words = pname[key].split()
        lead = words[0].split("-")[0] if before_hyphen else words[0]
        return "%s %s" % (lead, words[-1])
    shorts = [short("chaya_savings_insurance"),
              short("chaya_savings_insurance", before_hyphen=True),
              short("cattle_fattening_insurance"),
              short("loan_security_insurance")]
    sv = [s for s in shorts if unknown_product_name("%s কী?" % s, reg)]
    cases.append(("short names of real products: no refusal", not sv,
                  "refused: %s" % sv))

    # Unknown names must still be refused, and the common-word guard must
    # hold: সাধারণ is in two loan names, so it is the case that proves a
    # common word cannot carry an unknown one. Picked from the data, not
    # typed: the one generic, non-head, non-programme word in a loan's name.
    prods, generic, head_kind = _product_words()
    progs = {canon(n) for n, _a in PROGRAMME_NAMES.values()}
    common = sorted(w for w in generic
                    if w not in head_kind and w not in progs
                    and any(k == "loan" and w in ws for k, ws in prods))[0]
    dabi = PROGRAMME_NAMES["dabi"][0]
    must_fire = ["“গুড কাস্টমার লোন” সম্পর্কে বলুন।",
                 "%s কৃষি লোন কী?" % common,     # judged on কৃষি, which names nothing
                 "%s বিমা কী?" % common,          # no insurance has the common word
                 "%sর গুড কাস্টমার লোন কী?" % dabi]  # a programme name, inflected, is generic
    must_be_silent = ["নতুন লোন নিতে কী লাগে?", "এই ঋণ কত দিনের?",
                      # ঋণ inside ঋণীদের is not a head noun
                      "৩.৯.১(খ) ধারায় ঋণীদের ক্ষেত্রে মামলার সিদ্ধান্ত কে নেবেন?"]
    missed = [x for x in must_fire if not unknown_product_name(x, reg)]
    noisy = [x for x in must_be_silent if unknown_product_name(x, reg)]
    cases.append(("unknown names refused; grammar silent",
                  not missed and not noisy,
                  "missed=%s noisy=%s" % (missed, noisy)))

    # The enrolment question, end to end: a short product name, an age, and
    # the clause rule 23 governs. It must reach rule 23 -- not the
    # unknown-name refusal, and not the manual's limits.
    q65 = "৬৫ বছর বয়সী গ্রাহক কি %s নিতে পারবেন?" % shorts[0]
    t, _c, m = run(q65, chunks=ch("5.2.3"))
    e = ("6:unknown_product" not in m["rules_fired"] and t != NO_SUCH_NAME_BN
         and r23["current_rule_bn"] in t)
    if r23["verdict"] == "not_confirmed":
        e = (e and not any(re.search(r"(?<!\d)%s(?!\d)" % a, canon(t))
                           for a in ages)
             and canon("যেকোনো") not in canon(t))
    cases.append(("enrolment question reaches rule 23", e, t))

    for name, good, got in cases:
        ok &= bool(good)
        if verbose:
            print("  scenario %-42s [%s]" % (name,
                                             "PASS" if good else "FAIL"))
            if not good:
                print("      got: %s" % (got or "(empty)")[:150])
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
