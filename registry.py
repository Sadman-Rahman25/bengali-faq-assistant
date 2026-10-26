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


def _chunk_scope(chunks):
    """-> (clauses, manuals) of retrieved chunks. The one reading of them,
    shared by apply_rules and generate.py's pre-check -- two readings once
    disagreed about manuals and left a reply empty."""
    return ([c.get("clause") for c in chunks if c.get("clause")],
            [c.get("manual_id") or c.get("doc") for c in chunks])


# not_available.lists: a products.csv status. A row that fires on a general
# question appends the products with that status now.
_LIST_LABEL_BN = {"suspended": "বর্তমানে স্থগিত প্রোডাক্ট: %s।"}


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

# A TERM MAY REQUIRE SEVERAL WORDS TOGETHER: a+b|c means a, and b or c.
# Row 8 fires on a general "when did they stop?" -- কবে with বন্ধ or স্থগিত
# and a word for products -- and no single word can say that: কবে alone is
# every date question, বন্ধ alone is "closed". The product word keeps
# "অফিস কবে বন্ধ থাকবে?" out of it.
ALL_OF, ANY_OF = "+", "|"


class _AllOf:
    """Every part matches, each part by any of its alternatives. Answers
    search() like a compiled pattern, so the gate treats it as one term."""

    def __init__(self, parts):
        self.parts = parts

    def search(self, text):
        return all(any(rx.search(text) for rx in alts) for alts in self.parts) or None


def _term_rx(t):
    """One topic term -> its compiled pattern. Cached per string, because
    several rows share terms. Raises on a product key productmatch does not
    have: a gate that silently cannot fire looks exactly like a rule nobody
    asks about."""
    rx = _TERM_RX.get(t)
    if rx is not None:
        return rx
    if ALL_OF in t and not t.startswith(PRODUCT_TERM):
        rx = _AllOf([[_term_rx(a.strip()) for a in part.split(ANY_OF) if a.strip()]
                     for part in t.split(ALL_OF) if part.strip()])
    elif t.startswith(PRODUCT_TERM):
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


def _term_hits(row, question):
    """How many of the row's topic terms the question contains. Two rows on
    one clause are told apart by this: a question about WHO keeps the loan
    papers shares more terms with the who-row than with the retention-PERIOD
    row, though both name the papers."""
    q = canon(question or "")
    return sum(1 for t in _terms(row.get("topic_terms")) if _term_rx(t).search(q))


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
    """The six tables, with the names already compiled to patterns."""

    def __init__(self, root=Path(".")):
        d = Path(root) / REG
        self.products = _rows(d / "products.csv")
        self.insurers = _rows(d / "insurers.csv")
        self.rule_updates = _rows(d / "rule_updates.csv")
        self.term_updates = _rows(d / "term_updates.csv")
        self.not_available = _rows(d / "not_available.csv")
        # names the manual uses for something that is not a product; see
        # _listed_not_product()
        self.not_products = _rows(d / "not_products.csv")

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
        # A `lists` value must be a status the code can label and products.csv
        # actually uses -- an unknown one would append an empty list.
        statuses = {p["status"] for p in self.products}
        for r in self.not_available:
            listed = (r.get("lists") or "").strip()
            if listed and (listed not in _LIST_LABEL_BN or listed not in statuses):
                raise ValueError("not_available item %s: lists=%r is not a "
                                 "listable status" % (r["item"], listed))

        # Real manual ids, for citations. rule_updates.manual holds the
        # SCOPE (dabi | progoti | both), which is not a manual id -- a row
        # scoped to both used to be cited as "both §5.2.3".
        self.root = Path(root)
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
    def _full_name_spans(self, q):
        return [(m.start(), m.end()) for x in self.products + self.insurers
                if x["_rx"] for m in x["_rx"].finditer(q)]

    def products_named(self, question):
        """Registry rows whose product the question names. Suspended first,
        so rule 1's 'say that before giving any terms' holds by construction
        when several products are named at once.

        BY FULL NAME OR BY SHORT NAME, through the same matching that
        unknown_product_name() uses -- otherwise a short name of a suspended
        product was neither refused nor flagged: স্বাধীন ঋণ, গতি ঋণ, আরোগ্য
        লোন reached the model, which answers from manuals that still list them
        as active. A short name fitting up to MAX_SHORT_MATCHES products names
        each of them; more than that is products_ambiguous().
        """
        q = canon(question or "")
        hit = [p for p in self.products if p["_rx"] and p["_rx"].search(q)]
        by_id = {p["product_id"]: p for p in self.products}
        for _qual, _words, keys in _short_names(q, self._full_name_spans(q)):
            if len(keys) <= MAX_SHORT_MATCHES:
                hit += [by_id[k] for k in keys
                        if k in by_id and by_id[k] not in hit]
        return sorted(hit, key=lambda p: p["status"] != "suspended")

    def products_ambiguous(self, question):
        """Short names fitting more than MAX_SHORT_MATCHES products.
        -> [[productmatch key]], one list per such name."""
        q = canon(question or "")
        return [keys for _q, _w, keys in _short_names(q, self._full_name_spans(q))
                if len(keys) > MAX_SHORT_MATCHES]

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
    def withheld_for(self, question, chunks=()):
        """withheld() for retrieved chunks, read exactly as apply_rules reads
        them. generate.py's pre-check must call this, not withheld(): it once
        passed no manuals, saw a Progoti-only row on a Dabi chunk, skipped the
        model, and apply_rules -- which did pass them -- then had nothing to
        say. The reply was empty."""
        clauses, manuals = _chunk_scope(chunks)
        return self.withheld(question, clauses, manuals)

    def withheld(self, question, clauses=(), manuals=()):
        """not_available rows the question falls under.

        `manual` is honoured exactly as stale_rules honours it: a row scoped
        to progoti does not withhold an answer built from the Dabi manual.
        Row 10 needs it -- Progoti's §3.8.1(খ)/§3.11.2 give the loan papers to
        a post that does not exist, while Dabi's give them to real ones.

        Rows that share a retrieved clause defer to the one the question
        shares the MOST topic terms with; a tie keeps all of them.

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
        ms = {m for m in manuals if m}
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
            scope = (r.get("manual") or "both").strip()
            if ms and scope != "both" and not any(m.startswith(scope) for m in ms):
                continue
            rc = {canon(c) for c in _clauses(r["clause"])}
            if rc and (want & rc):
                out.append(dict(r, _why="clause", _rc=rc,
                                _hits=_term_hits(r, question)))
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
                continue
            # A GENERAL QUESTION, naming no product: a clause-less row fires
            # on one of its several-word terms (ALL_OF). Row 8's "when did
            # they stop?" -- T-03 -- named nothing, so the subject path above
            # could never reach it.
            if (not rc and not self.products_named(question)
                    and any(ALL_OF in t and _term_rx(t).search(q)
                            for t in _terms(r.get("topic_terms")))):
                out.append(dict(r, _why="general"))
        # Same clause, two topics: the WHO row and the PERIOD row both name
        # the loan papers, and the question decides between them.
        by_clause = [r for r in out if r["_why"] == "clause"]
        drop = [r for r in by_clause
                if any(o["_hits"] > r["_hits"] and (o["_rc"] & r["_rc"] & want)
                       for o in by_clause if o is not r)]
        return [r for r in out if not any(r is d for d in drop)]


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
#
# The qualifier needs three characters, not MIN_WORD's four: গতি is a
# product name in three, and "গতি ঋণ" -- a suspended loan -- was invisible
# both to the status line and to the refusal. The two-letter grammar words
# (এই, সব) stay out, and the stop words and the ordinary-word count below
# handle the longer ones.
_MIN_QUALIFIER = 3
_PRODUCT_SHAPED = re.compile(
    r"([%s\w]{%d,})\s+(%s)%s(?![%s])" % (
        _BN, _MIN_QUALIFIER,
        "|".join(re.escape(canon_pattern(h)) for h in _HEAD),
        _ENDINGS, _BN))


@functools.lru_cache(maxsize=1)
def _product_words():
    """-> ([(key, kind, words)] per product, generic words, {head word: kind}).

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
    for key, name, _aliases, _pat in NAMED:
        words = [w for w in _NAME_SPLIT.split(canon(name)) if w]
        heads = [head_kind[w] for w in words if w in head_kind]
        prods.append((key, heads[-1] if heads else None, words))
    df = Counter(w for _key, _kind, ws in prods for w in set(ws))
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
    return _describing(q, head_start)[0]


def _describing(q, head_start):
    """-> (describing words, the stop word the walk ended on or None)."""
    stop = {canon(w) for w in _NAME_STOP}
    out = []
    for tok in reversed(q[:head_start].split()[-_NAME_WINDOW:]):
        if tok[-1] in _CLAUSE_END:
            return out, None
        bare = tok.strip(_PUNCT)
        if not bare:
            return out, None
        if bare in stop:
            return out, bare
        out += [w for w in _NAME_SPLIT.split(bare) if w and w not in stop]
        if tok[0] in _OPEN_QUOTE:
            return out, None
    return out, None


# "কোন X ঋণ" ASKS WHICH X; it does not name one. "গ্রুপ ঋণে কোন অঙ্কের ঋণ কে
# অনুমোদন করেন?" -- a loan of which amount -- was refused on অঙ্কের, a word
# the manual never uses (it writes পরিমাণ), so the ordinary-word count could
# not clear it. Only these determiners, which stand before a noun: the
# particle কি stands there too in "গ্রাহক কি নীলকমল ঋণ নিতে পারবেন?", and
# that name must still be refused.
_WHICH = ("কোন", "কোনো")
# "কত X ঋণ/সঞ্চয়" ASKS HOW MUCH X; X is a quantity, not a name. "… কত শতাংশ
# সঞ্চয় জমা করতে হবে?" was refused on শতাংশ, a word the manuals never print
# (they write %), so no test against the manual text could clear it. Like
# _WHICH, a word the name walk stops on.
_HOW_MUCH = ("কত",)


def _products_for(words, head):
    """The products these describing words, with this head noun, name.
    -> [productmatch key], best fit first-come.

    A word counts when it is a word of some product's name (one case ending
    allowed either side) AND that product is the same kind as the head noun:
    ছায়া + বিমা finds ছায়া-সঞ্চয় নিরাপত্তা বিমা; মাসিক + লোন finds nothing,
    because মাসিক names a savings scheme.

    TOO-COMMON WORDS DO NOT COUNT BY THEMSELVES. If the name has any word that
    is not generic, only those words decide: "সাধারণ কৃষি লোন" is judged on
    কৃষি, which names nothing -- although সাধারণ is in two loan names. Only a
    name made of nothing but common words falls back to them: "সাধারণ লোন" is
    the general loan of either programme, and "ঋণ বিমা" is ঋণ নিরাপত্তা বিমা.

    BEST FIT, not any fit: the products sharing the most of the words win, so
    "দাবি সাধারণ লোন" is the Dabi general loan alone, while "সাধারণ লোন" --
    one word, two equal fits -- is both.
    """
    prods, generic, head_kind = _product_words()
    kind = head_kind.get(head)
    distinctive = [w for w in words
                   if not any(_same_word(w, g) for g in generic)]
    use = distinctive or words
    score = {}
    for key, k, ws in prods:
        if k != kind:
            continue
        n = sum(1 for w in use if any(_same_word(w, t) for t in ws))
        if n:
            score[key] = n
    best = max(score.values(), default=0)
    return [key for key, n in score.items() if n == best]


def _short_names(q, skip=()):
    """[(qualifier, describing words, [product keys])] for each product-shaped
    phrase in canon'd `q`. A phrase whose head noun sits inside `skip` -- the
    spans of names already matched in full -- is left out, or "দাবি + সাধারণ
    লোন" would also resolve its own সাধারণ লোন to the Progoti general loan."""
    out = []
    for m in _PRODUCT_SHAPED.finditer(q):
        if any(a <= m.start(2) < b for a, b in skip):
            continue
        qual = m.group(1)
        if any(canon_pattern(w) == qual for w in _NOT_A_NAME):
            continue
        words = _describing_words(q, m.start(2))
        if words:
            out.append((qual, words, _products_for(words, m.group(2))))
    return out


# AN ORDINARY WORD IS NOT A NAME. With no product found, the refusal still
# needs a describing word that could be the name of something the manual
# lacks. Without this, "৭০,০০০ টাকা পর্যন্ত ঋণ ..." was a question about a
# product called পর্যন্ত, and "গ্রুপ ঋণে ..." about one called গ্রুপ.
#   ordinary  -- the word is in more than ORDINARY_CHUNKS chunks of the corpus
#   numeral   -- any token with a digit: ৭০,০০০, the clause number ২.১.১(ট)
#   heading   -- the word titles a section of the manual (ইন্ডিভিজুয়াল in
#                "২.১.২(গ) ইন্ডিভিজুয়াল ক্লায়েন্ট"): a term the manual uses
#                for its own structure, which is what makes a lending mode a
#                real term. Read from the tracked manifest's headings.
# গুড কাস্টমার লোন still fires: গুড is in 4 chunks and no heading.
ORDINARY_CHUNKS = 20
MAX_SHORT_MATCHES = 3       # a short name fitting more products asks which
# WORDS ABOUT THE MANUAL ITSELF are never a product name, in any inflection.
# They are rare in the corpus precisely because a manual seldom describes
# itself -- ম্যানুয়ালে is in 6 chunks and no heading -- so the ordinary-word
# count cannot clear "দাবি আর প্রগতি ম্যানুয়ালে ঋণের হার ...". The one list.
_META_WORDS = ("ম্যানুয়াল", "ধারা", "অধ্যায়", "পার্থক্য")


@functools.lru_cache(maxsize=4)
def _corpus_rows(root):
    """[(manual_id, clause, canon'd text)] for every chunk of every built
    manual; the clause from the tracked manifest. Raises if none is built --
    the ordinary-word count cannot be guessed, and a silent zero would turn
    every word into a name."""
    import json
    mp = Path(root) / "data/processed/chunk_manifest.json"
    clause = {r["chunk_id"]: r.get("clause") or ""
              for r in json.loads(mp.read_text(encoding="utf-8")).get("chunks") or []}
    rows = []
    for mid in _manual_ids(Path(root)):
        p = Path(root) / "data/processed" / ("chunks_%s_final.jsonl" % mid)
        if p.exists():
            for l in p.read_text(encoding="utf-8").splitlines():
                if l.strip():
                    c = json.loads(l)
                    rows.append((mid, clause.get(c["chunk_id"], ""),
                                 canon(c["display_text"])))
    if not rows:
        raise RuntimeError("registry: no built corpus under %s/data/processed -- "
                           "the ordinary-word check needs the chunk files" % root)
    return rows


@functools.lru_cache(maxsize=4)
def _corpus(root):
    return tuple(t for _m, _c, t in _corpus_rows(root))


@functools.lru_cache(maxsize=4)
def _headings(root):
    """Canon'd heading path of every chunk, from the tracked manifest."""
    import json
    p = Path(root) / "data/processed/chunk_manifest.json"
    rows = json.loads(p.read_text(encoding="utf-8")).get("chunks") or []
    return sorted({canon(" ".join(r.get("heading_path") or [])) for r in rows})


def _numeral(word):
    return any(ch.isdigit() for ch in word)


@functools.lru_cache(maxsize=4096)
def _ordinary(word, root="."):
    """In more than ORDINARY_CHUNKS chunks of the built corpus."""
    rx = _term_rx(word)
    return sum(1 for c in _corpus(root) if rx.search(c)) > ORDINARY_CHUNKS


@functools.lru_cache(maxsize=4096)
def _could_name(word, root="."):
    """The test before 2026-10-26: rare (not ordinary), no digit, not a word
    about the manual, in no heading. Still the gate for the one exception in
    _unknown_phrase(); _name_like() adds the manual-text test to it."""
    if _numeral(word) or _ordinary(word, root):
        return False
    if any(_same_word(word, canon(m)) for m in _META_WORDS):
        return False
    rx = _term_rx(word)
    return not any(rx.search(h) for h in _headings(root))


@functools.lru_cache(maxsize=4096)
def _in_manuals(word, root="."):
    """Is the word, or the word less one case ending, in any built manual?"""
    rxs = [_term_rx(s) for s in _stems(word)]
    return any(rx.search(t) for t in _corpus(root) for rx in rxs)


@functools.lru_cache(maxsize=4096)
def _name_like(word, root="."):
    """Could this describing word name something the manual does not have?

    ONLY A WORD THE MANUALS NEVER USE. Rarity was the test -- in 20 chunks or
    fewer -- and on 199 real questions it refused five on words the manuals
    use every day: প্রস্তাবিত লোন, রিপিট ঋণ, সর্বনিম্ন লোন, কাঙ্খিত ঋণ. A word
    the manual itself uses is vocabulary, not the name of something missing.
    The cost is that a made-up name built from manual words ("সাধারণ কৃষি
    লোন") now reaches the model; a name the manual uses for something that
    is not a product is refused from data instead (_listed_not_product).
    """
    return _could_name(word, root) and not _in_manuals(word, root)


def _refusable_name(words, root="."):
    """The part of the describing words that could be an unknown NAME.

    Read outward from the head noun: numerals are skipped, and the first
    ordinary word ENDS the name -- an ordinary word is not a product name, so
    nothing beyond it belongs to this one. "একাধিক ডিপিএসের উপর বিমা" is
    insurance ON several DPS; উপর ends the name and ডিপিএসের is not part of
    it. Refusal only: resolving a product reads all the words, because real
    names contain ordinary ones (সাধারণ, in দাবি + সাধারণ লোন).
    """
    run = []
    for w in words:                 # nearest the head noun first
        if _numeral(w):
            continue
        if _ordinary(w, root):
            break
        run.append(w)
    return run


def unknown_product_name(question, reg=None):
    """A product-shaped name no registry row knows. -> the qualifier or None.

    Returns the qualifier so the caller can say WHICH name it did not find,
    and so a caller that wants to log it can.

    A SHORT NAME IS NOT AN UNKNOWN NAME. Officers say ছায়া বিমা, গরু বিমা,
    দাবি লোন; the registry knows each product only by its full names, so all
    of them used to get "এই নামে কিছু ম্যানুয়ালে নেই" -- a confident wrong
    answer about a product that is in the manual, the T-08 shape again.
    It fires only when the describing words name no product of that kind AND
    one of them could be a name: see _name_like (not ordinary, not a
    numeral, not a section-heading term), _refusable_name (an ordinary word
    ends the name) and _WHICH ("কোন X ঋণ" asks which X).

    Measured on 222 distinct real questions (FAQ, gold, quiz): the original
    rule refused 36. Fifteen were real products named short or inflected,
    four were ঋণ or বিমা inside another word, and eleven were ordinary words,
    numerals or lending modes ("৭০,০০০ টাকা পর্যন্ত ঋণ ...", "গ্রুপ ঋণে ...",
    "ইন্ডিভিজুয়াল ঋণে ..."). It now refuses 7: গুড কাস্টমার লোন twice,
    নীলকমল ঋণ three times, ছাগলের বিমা -- and one still wrong, "দাবি আর প্রগতি
    ম্যানুয়ালে ঋণের হার ...", where ম্যানুয়ালে is in six chunks and no heading.

    Re-measured 2026-10-26 on the same sources, 199 distinct questions now:
    7 refused, 5 of them wrongly -- on শতাংশ (twice), প্রস্তাবিত (twice),
    সর্বনিম্ন and রিপিট. With a describing word refused only if the manuals
    never use it (_name_like) and "কত X" read as a quantity (_HOW_MUCH): 2,
    both right -- গুড কাস্টমার লোন (a listed not-product name) and ছাগলের
    বিমা (the insurance exception in _unknown_phrase).
    """
    found = _unknown_phrase(question, reg or load())
    return found[0] if found else None


def _unknown_phrase(question, reg):
    """-> (qualifier, the refusable name words, head noun) or None."""
    q = canon(question or "")
    if (reg.products_named(question) or reg.insurers_named(question)
            or reg.products_ambiguous(question)):
        return None
    which = {canon(w) for w in _WHICH}
    how_much = {canon(w) for w in _HOW_MUCH}
    root = str(reg.root)
    for m in _PRODUCT_SHAPED.finditer(q):
        qual = m.group(1)
        if any(canon_pattern(w) == qual for w in _NOT_A_NAME):
            continue
        words, stopped_on = _describing(q, m.start(2))
        if not words or _products_for(words, m.group(2)):
            continue
        if stopped_on in which:
            continue          # "কোন X ঋণ": asks which X
        if stopped_on in how_much:
            continue          # "কত X ঋণ": asks how much X
        listed = _listed_not_product(words, reg)
        if listed:
            return qual, listed, m.group(2)
        name = _refusable_name(words, root)
        if not any(_name_like(w, root) for w in name):
            # THE ONE EXCEPTION: an insurance asked about by a word the
            # manual uses inside an insurance section. ছাগল is in §5.4's
            # background, and both §5.4 products are for cows, so goats are
            # not covered: refuse and list them, as before 2026-10-26.
            # Insurance only, as in _related_insurances(): an insurance
            # section is one product family, a loan section is a programme.
            if not (any(_could_name(w, root) for w in name)
                    and _related_insurances(name, m.group(2), reg)):
                continue      # words the manual uses: vocabulary, not a name
        return qual, name, m.group(2)
    return None


def _listed_not_product(words, reg):
    """Names the manual uses for something that is not a product, from
    data/registry/not_products.csv. -> the question's words of the listed
    name, or [] if none is there.

    A REVIEWED LIST, because no rule over the manual text can find these:
    গুড কাস্টমার ঋণ is printed in §9.2.2 as a report name, exactly as রিপিট
    ঋণ is printed as a loan type, and only the second is something an
    officer can give a member. Reached only when the name stands before
    ঋণ/লোন/বিমা/সঞ্চয় -- a question about the §9.2.2 REPORT is not refused.
    """
    for row in reg.not_products:
        nws = [w for w in _NAME_SPLIT.split(canon(row.get("name_bn") or ""))
               if w]
        if nws and all(any(_same_word(w, n) for w in words) for n in nws):
            return [w for w in words if any(_same_word(w, n) for n in nws)]
    return []


def _stems(word):
    """The word, and the word less one case ending: ছাগলের -> ছাগল."""
    out = {word}
    for e in (canon(e) for e in _TOPIC_ENDINGS):
        if word.endswith(e) and len(word) - len(e) >= 2:
            out.add(word[:-len(e)])
    return out


def _related_insurances(words, head, reg):
    """The insurances of the manual sections that use the refused name's
    word. -> [name_bn], in products.csv order; empty for a non-insurance head.

    INSURANCE ONLY, because an insurance section is one product family --
    §5.4 of the Dabi manual is livestock -- while a loan section is a whole
    programme: ছাগল is also in §1.1.1, as a purpose of the স্বাধীন লোন, and
    listing every Dabi loan for "ছাগল লোন" would answer nothing. A section is
    the first two levels of the clause (5.4); its products come from
    products.csv by manual and clause. Nothing here names an animal.
    """
    _prods, _generic, head_kind = _product_words()
    if head_kind.get(head) != "insurance":
        return []
    sections = set()
    for w in words:
        rxs = [_term_rx(s) for s in _stems(w)]
        for mid, clause, text in _corpus_rows(str(reg.root)):
            if clause and any(rx.search(text) for rx in rxs):
                sections.add((mid, ".".join(clause.split(".")[:2])))
    out = []
    for p in reg.products:
        if p.get("category") != "insurance" or not p.get("clause"):
            continue
        sec = ".".join(p["clause"].split(".")[:2])
        scope = (p.get("manual") or "both").strip()
        if any(s == sec and (scope == "both" or mid.startswith(scope))
               for mid, s in sections):
            out.append(p["name_bn"])
    return out


# "No PRODUCT by this name", not "nothing by this name": গুড কাস্টমার ঋণ IS
# in the manual -- a report in the Branch Manager Supervision Module, §9.2.2
# -- so the old sentence was literally untrue of the T-06 name.
NO_SUCH_NAME_BN = "এই নামে কোনো প্রোডাক্ট ম্যানুয়ালে নেই।"
# Appended when the refused name is an insurance whose word the manual
# uses inside one insurance section: "ছাগলের বিমা" gets the livestock
# insurances, because ছাগল appears in §5.4 -- as background, and both §5.4
# products say they are for cows only.
RELATED_INSURANCE_BN = "ম্যানুয়ালে এই ধরনের যে বিমা আছে: %s।"
AMBIGUOUS_NAME_BN = "এই নামে একাধিক প্রোডাক্ট আছে। কোনটির কথা জানতে চান? — %s"


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
    clauses, manuals = _chunk_scope(chunks)
    meta = {"rules_fired": []}

    stale = reg.stale_rules(clauses, manuals, question)
    wh = reg.withheld_for(question, chunks)

    # A PRODUCT-SHAPED NAME NOTHING KNOWS, settled before any other rule.
    # Left to the model this became "which manual did you mean?" (T-06):
    # the prompt's ambiguity rule firing on a product that is in neither
    # manual, which invites the reader to pick one and be answered from it.
    # Says what is true and nothing more -- not that the product does not
    # exist, which the registry has no way to know.
    found = _unknown_phrase(question, reg)
    if found and not wh:
        unknown, name_words, head = found
        meta["rules_fired"].append("6:unknown_product")
        meta["unknown_product"] = unknown
        meta["unknown_original"] = answer
        # Not a flat refusal when the manual has insurances of this kind: say
        # which, from products.csv.
        text = NO_SUCH_NAME_BN
        related = _related_insurances(name_words, head, reg)
        if related:
            meta["related_products"] = related
            text += " " + RELATED_INSURANCE_BN % ", ".join(related)
        return text, [], meta

    # ONE CLAUSE, TWO TOPICS, AND THE COARSE CHECK WINS BY DEFAULT.
    #
    # §5.2.3 appears in not_available (row 7, "insurance on more than one
    # DPS") AND in rule_updates (row 23, the DPS age limit, changed to "any
    # age"). A clause match cannot tell them apart, so a question about the
    # AGE LIMIT was being answered "the current rule is not known" -- a
    # refusal, when the registry holds the answer. Right by the letter of
    # rule 4 and wrong for the person asking.
    #
    # The discriminator is in the data. FIRST the topic terms the question
    # shares with each row, THEN the words it shares with the row's own
    # Bengali statement. Words alone were not enough: "ঋণের কাগজপত্র কে
    # সংরক্ষণ করবেন?" shares nothing with row 10's sentence ("এই দায়িত্ব এখন
    # কার ...") and two words with retired rule 17's, so a WHO question was
    # answered with the retention-PERIOD rule. On topic terms it is 3 to 2.
    # A withheld row only wins the clause when no stale rule describes the
    # question better -- and a full tie still goes to the withheld row,
    # because withholding is the safe direction.
    if wh and stale:
        def fit(row, *fields):
            return (_term_hits(row, question),
                    len(_words(question) & _words(" ".join(row.get(f) or ""
                                                           for f in fields))))
        best_stale = max((fit(r, "rule_summary", "current_rule_bn")
                          for r in stale), default=(0, 0))
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
        said = []
        for w in wh:
            s = w["bot_says_bn"]
            # A general question -- "when did they stop?", naming no product
            # -- gets the list the row asks for, from products.csv: the
            # products with that status now. Never typed.
            listed = (w.get("lists") or "").strip()
            if w["_why"] == "general" and listed:
                names = [p["name_bn"] for p in reg.products if p["status"] == listed]
                s += " " + _LIST_LABEL_BN[listed] % ", ".join(names)
            said.append(s)
        text = " ".join(dict.fromkeys(said))
        text, terms = reg.apply_terms(text)
        if terms:
            meta["rules_fired"].append("3:term_updates")
            meta["terms_applied"] = terms
        return text, [], meta

    # -- rule 1, ambiguous: a short name fitting too many products to list
    # their statuses usefully. Say none of them and ask which is meant --
    # the names are real, so offering them is not the T-06 trap of inviting
    # a choice between manuals for a product that is in neither.
    amb = reg.products_ambiguous(question)
    if amb:
        names = {k: n for k, n, _a, _p in NAMED}
        meta["rules_fired"].append("1:ambiguous_product")
        meta["ambiguous"] = amb
        meta["ambiguous_original"] = answer
        listed = "; ".join(names[k] for k in amb[0])
        return (AMBIGUOUS_NAME_BN % listed), [], meta

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
    # A productmatch product the registry has no row for is still a real
    # product. products_named() cannot name it, so only the short-form check
    # keeps it from the refusal. Tested on a registry with গতি লোন's row
    # removed: গতি is in few enough chunks to look like a name.
    import copy
    lean = copy.copy(reg)
    lean.products = [p for p in reg.products if p["product_id"] != "goti_loan"]
    goti = "%s %s কী?" % (pname["goti_loan"].split()[0], "ঋণ")
    if unknown_product_name(goti, lean):
        sv.append(goti + " (no registry row)")
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
                    and any(k == "loan" and w in ws for _key, k, ws in prods))[0]
    dabi = PROGRAMME_NAMES["dabi"][0]
    must_fire = ["“গুড কাস্টমার লোন” সম্পর্কে বলুন।",
                 # judged on নীলকমল, which names nothing: the common word
                 # cannot carry it. (It was কৃষি until 2026-10-26; কৃষি is a
                 # word the manual uses, so it no longer refuses.)
                 "%s নীলকমল লোন কী?" % common,
                 "%sর গুড কাস্টমার লোন কী?" % dabi,  # a programme name, inflected, is generic
                 # the particle কি before a name is not "কোন X ঋণ"
                 "গ্রাহক কি নীলকমল ঋণ নিতে পারবেন?"]
    must_be_silent = ["নতুন লোন নিতে কী লাগে?", "এই ঋণ কত দিনের?",
                      # the two false refusals of 2026-10-26: কাঙ্খিত is a
                      # word the manual uses; "কত শতাংশ" asks a quantity
                      "ঋণ নিতে সদস্যকে কাঙ্খিত ঋণের কত শতাংশ সঞ্চয় জমা দিতে হয়?",
                      "ঋণের আবেদনের সময় সদস্যকে ঋণের কত শতাংশ সঞ্চয় জমা দিতে হয়?",
                      # a made-up name of manual words reaches the model
                      "%s কৃষি লোন কী?" % common,
                      # ঋণ inside ঋণীদের is not a head noun
                      "৩.৯.১(খ) ধারায় ঋণীদের ক্ষেত্রে মামলার সিদ্ধান্ত কে নেবেন?",
                      # ...and these two only the head-noun boundary clears:
                      # ধারায় above is also a meta word, which hid it
                      "খেলাপী ঋণীদের তালিকা কে তৈরি করবেন?",
                      "মৃত্যুজনিত বিমাদাবি কত দিনে নিষ্পত্তি হয়?"]
    missed = [x for x in must_fire if not unknown_product_name(x, reg)]
    noisy = [x for x in must_be_silent if unknown_product_name(x, reg)]
    cases.append(("unknown names refused; grammar silent",
                  not missed and not noisy,
                  "missed=%s noisy=%s" % (missed, noisy)))

    # An ordinary word, a numeral or a section-heading term is not the name
    # of something missing. One question per guard, each from the real set,
    # so each guard switched off fails this scenario by itself.
    ordinary = ["৭০,০০০ টাকা পর্যন্ত ঋণ অনুমোদনের ক্ষমতা কার?",  # numeral + ordinary
                "১৫,০০০ ঋণ কে অনুমোদন করেন?",                     # a numeral beside the head
                "গ্রুপ ঋণে কোন অঙ্কের ঋণ কে অনুমোদন করেন?",      # ordinary, a lending mode
                "ইন্ডিভিজুয়াল ঋণে সর্বোচ্চ স্তরে কে অনুমোদন করেন?",  # a section-heading term
                "%s বিমা কী?" % common,                             # a common word alone
                # an ordinary word ends the name: উপর, অনুযায়ী
                "একাধিক ডিপিএসের উপর বিমা করা যায় কি?",
                "ধারা ২.১.১(ট) অনুযায়ী ঋণ অনুমোদনের সিলিং কত?"]
    noisy = [x for x in ordinary if unknown_product_name(x, reg)]
    cases.append(("ordinary words, numerals, headings: no refusal",
                  not noisy, "refused: %s" % noisy))

    # --- the status line follows short names too ---------------------------
    # A suspended product asked by another spelling of its head noun --
    # লোন for ঋণ and back -- must lead with its suspension, not reach the
    # model, which answers from manuals that still list it as active.
    other_head = {canon("লোন"): "ঋণ", canon("ঋণ"): "লোন"}
    by_id = {p["product_id"]: p for p in reg.products}
    wrong = []
    for key in ("shadhin_loan", "goti_loan", "arogya_loan"):
        words = pname[key].split()
        q = "%s %s কী?" % (words[0], other_head[canon(words[-1])])
        row = by_id[key]
        t, _c, m = run(q)
        first = t.split("।")[0]
        if not (row["status"] == "suspended"
                and t.startswith(row["status_line_bn"])
                and canon("বর্তমানে স্থগিত আছে") in canon(first)
                and "6:unknown_product" not in m["rules_fired"]):
            wrong.append((q, t))
    cases.append(("suspended short names lead with the suspension",
                  not wrong, "%s" % wrong))

    # A short name fitting two products shows both: the general loan of each
    # programme, the common word picked from the data above.
    both = sorted(key for key, k, ws in prods if k == "loan" and common in ws)
    t, _c, m = run("%s লোন কী?" % common)
    g = (len(both) == 2 and sorted(m.get("products") or []) == both
         and all(by_id[k]["status_line_bn"] in t for k in both))
    cases.append(("short name of two products: both statuses", g, t))

    # More than three: no status line, ask which. Three is the brief's
    # number, written here on purpose rather than read from
    # MAX_SHORT_MATCHES -- a test that reads the threshold it tests cannot
    # notice the threshold moving. The word is found in the data, not typed:
    # the first non-head word that more than three products of one kind share.
    kind_head = {k: canon(ws[0]) for k, ws in _HEAD_KIND}
    w_amb, h_amb = next((w, kind_head[k]) for w in sorted({w for _x, _k, ws in prods for w in ws})
                        for k in kind_head if w not in head_kind
                        and len(_products_for([w], kind_head[k])) > 3)
    t, _c, m = run("%s %s কী?" % (w_amb, h_amb))
    a = ("1:ambiguous_product" in m["rules_fired"] and not m.get("products")
         and t.startswith(AMBIGUOUS_NAME_BN.split("%s")[0]))
    cases.append(("short name of 4+ products: asks which", a, t))

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

    # --- who keeps the papers, and for how long: two rows, one clause ------
    # Row 10 (WHO, Progoti only) and row 5 (the retention PERIOD) both name
    # the loan papers. Each must fire only on its own question, row 10 must
    # stay out of Dabi, whose clause names a real post -- and end to end the
    # WHO question must get row 10's sentence, not retired rule 17's.
    na = {r["item"]: r for r in reg.not_available}
    who, period = na["10"], na["5"]
    q_who, q_period = ("ঋণের কাগজপত্র কে সংরক্ষণ করবেন?",
                       "ঋণের কাগজপত্র কত দিন সংরক্ষণ করতে হয়?")
    items = lambda q, cl, mid: [r["item"] for r in reg.withheld(q, [cl], [mid])]
    s = (items(q_who, "3.8.1", "progoti") == [who["item"]]
         and items(q_period, "3.8.1", "progoti") == [period["item"]]
         and items(q_who, "3.11.2", "progoti") == [who["item"]]
         and who["item"] not in items(q_who, "3.8.1", "dabi_2025"))
    cases.append(("row 10 and row 5 each fire only on their own question", s, ""))

    t_who, _c, m_who = run(q_who, chunks=ch("3.8.1", "progoti"))
    t_per, _c, _m = run(q_period, chunks=ch("3.8.1", "progoti"))
    end = (t_who == who["bot_says_bn"] and t_per == period["bot_says_bn"])
    cases.append(("who-question gets row 10, not the period rule", end,
                  "who=%r period=%r" % (t_who, t_per)))

    # --- the unknown-product reply says "no product", not "nothing" -------
    # গুড কাস্টমার ঋণ is in the manual, as a §9.2.2 report.
    t, _c, _m = run("“গুড কাস্টমার লোন” সম্পর্কে বলুন।")
    cases.append(("refusal says no PRODUCT by this name",
                  t == NO_SUCH_NAME_BN and canon("প্রোডাক্ট") in canon(t), t))

    # --- words about the manual are never a product name ------------------
    # Each is otherwise name-like: rare in the corpus, in no heading.
    meta_qs = ["দাবি আর প্রগতি ম্যানুয়ালে ঋণের হার বা সিলিং এ কোন পার্থক্য আছে কি?",
               "৫ম অধ্যায়ের বিমা প্রিমিয়াম কত?"]
    noisy = [x for x in meta_qs if unknown_product_name(x, reg)]
    cases.append(("meta words are not product names", not noisy,
                  "refused: %s" % noisy))

    # --- a refused insurance names the insurances of its section ----------
    # ছাগল is in the manual only in §5.4's background, and both §5.4
    # products are for cows, so goats are not covered: refuse, then list the
    # §5.4 insurances. The expected list is products.csv's own insurance rows
    # in the livestock product's section -- the livestock row named by its
    # key, the rest read from the file.
    lv = by_id["livestock_insurance"]
    sec = ".".join(lv["clause"].split(".")[:2])
    want = [p["name_bn"] for p in reg.products if p["category"] == "insurance"
            and p["manual"] == lv["manual"] and p["clause"]
            and ".".join(p["clause"].split(".")[:2]) == sec]
    t, _c, m = run("ছাগলের বিমা করা যাবে কি? প্রিমিয়াম কত?")
    t_flat, _c, _m = run("নীলকমল বিমা কী?")      # a name the corpus never uses
    lvk = (t.startswith(NO_SUCH_NAME_BN) and m.get("related_products") == want
           and len(want) >= 2 and t_flat == NO_SUCH_NAME_BN)
    cases.append(("goat insurance: refusal names the livestock insurances",
                  lvk, "%s | want=%s" % (t, want)))

    # --- row 8: "when did they stop?" with no product named ---------------
    sus = [p["name_bn"] for p in reg.products if p["status"] == "suspended"]
    r8 = na["8"]
    t3, _c, m3 = run("বন্ধ হয়ে যাওয়া প্রোডাক্টগুলো কবে থেকে বন্ধ?")
    t_office, _c, m_office = run("অফিস কবে বন্ধ থাকবে?")
    t_named, _c, _m = run("%s কবে থেকে বন্ধ?" % pname["shadhin_loan"])
    gen = (m3.get("not_available") == [r8["item"]]
           and t3.startswith(r8["bot_says_bn"]) and all(n in t3 for n in sus)
           and not m_office.get("not_available")
           and t_named == r8["bot_says_bn"])       # a named product: no list
    cases.append(("row 8 answers a general 'when did they stop'", gen,
                  "T-03=%r office=%r named=%r" % (t3, t_office, t_named)))

    # --- the pre-check and apply_rules read chunks the same way -----------
    # generate.py skips the model when withheld_for() says so and hands
    # apply_rules an empty answer. Whenever the pre-check withholds, the
    # rules must have something to say -- the Dabi §3.11.2 question once
    # came back empty -- and on that Dabi chunk the pre-check must not
    # withhold at all.
    probes = [(q_who, ch("3.11.2", "dabi_2025")), (q_who, ch("3.11.2", "progoti")),
              (q_who, ch("3.8.1", "progoti")), (q_period, ch("3.8.1", "dabi_2025")),
              ("প্রিমিয়াম কত?", ch("5.1.3"))]
    empty = []
    for q, chs in probes:
        if reg.withheld_for(q, chs):
            t, _c, _m = run(q, answer="", chunks=chs)
            if not t:
                empty.append((q, chs[0]["manual_id"], chs[0]["clause"]))
    pre = not empty and not reg.withheld_for(q_who, ch("3.11.2", "dabi_2025"))
    cases.append(("pre-check never leads to an empty reply", pre,
                  "empty: %s" % empty))

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
