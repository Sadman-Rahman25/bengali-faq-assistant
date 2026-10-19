"""term_review.py -- every role and product term, as a sheet the field team fills in.

WHY THIS IS NOT HOUSEKEEPING. 53 of the quiz bank's 74 questions are role
questions, and their correct answers come out of roles.py's 16-name
vocabulary. The field team reports that some of those titles may be wrong or
no longer used. If they are, the quiz is teaching wrong answers to the people
who work under the manuals, so this sheet gates the quiz rather than tidying
after it. quiz_questions is the column that says what a verdict costs: a
`not_used` against হিসাব কর্মকর্তা invalidates ten questions, one against
এডিসি invalidates none.

WHAT IT DOES NOT DO. It reads the registries and writes a CSV. It changes no
corpus, no registry and no bank. status and correct_term ship EMPTY -- the
verdict is the reviewer's, and a pre-filled guess is the thing most likely to
be confirmed without being checked.

THE SHEET'S SEMANTICS, which the CSV repeats in its header comment:

  first_clause   where to look the term up, in the form the manual PRINTS.
                 For a product this is the DEFINING clause where products.py
                 finds one -- the same section that status_request.csv cites.
                 For a role it is the first clause that USES the term, because
                 no role title is defined anywhere in either manual: there is
                 no glossary, no abbreviation table, and no section that
                 expands সিডিও. That absence is itself the finding the field
                 team is being asked about -- there is nothing in the corpus
                 to check a title against, which is why a human has to.

  corpus_count   bn_shared.count_literal, so the count is the owning
                 registry's and carries the exclusions it already encodes.
                 পিএ reads 142 rather than the 181 a plain substring count
                 gives, because পিএলসি is masked first. Counted over all
                 three corpora including livestock_grow_2026, which is the
                 newest manual and therefore the best evidence that a title
                 is still in use; it contributes no clause, having none.

  quiz_questions how many bank questions have this term as their correct
                 answer, keyed through quiz_app.answer_key so the sheet and
                 the draw cannot disagree about what one answer is.

TWO THINGS THE COUNTS CANNOT TELL THE REVIEWER, both reported per row in
`notes` rather than left to be inferred from a number:

  1. A count inflated by a longer term in the same vocabulary. Derived, not
     hardcoded: every role literal that is a canon'd substring of another
     role literal and is not masked from it in role_exclusions.EXCLUSIONS.
     One pair qualifies today.

  2. A count taken WITHOUT the registry's own pattern. count_literal resolves
     productmatch.PROGRAMME_NAMES and productmatch.NAMED but not
     productmatch.ENTITIES, so an underwriter is counted by its display
     spelling alone and misses the misspellings its pattern exists to catch.
     Left as it is -- extending _resolve_owner is a change to a shared
     primitive, not to this sheet -- but a bare 0 on a term the pattern finds
     five times would read as `never_existed`, which is the one verdict that
     cannot be walked back.

NO BENGALI LITERAL APPEARS IN THIS FILE, so Conventions 2 and 4 are satisfied
by construction rather than by exemption. Every term, alias and pattern is
read from roles.py or productmatch.py at run time.

Convention 1: nothing runs on import. NOTE that products.py binds its own OUT
from sys.argv at module level, so this module takes no positional argument --
there is nothing for that binding to pick up.

Usage:  python term_review.py [--out PATH]
"""
import argparse
import collections
import csv
import json
import sys
from pathlib import Path

import productmatch as PM
import products as PROD
import role_exclusions as REX
import roles as R
from bn_shared import NUKTA_PAIRS, canon_pattern, count_literal, literal_owner
from bnnorm import canon

P = Path("data/processed")
MANIFEST = P / "chunk_manifest.json"
DEFAULT_OUT = Path("out/term_review.csv")

FIELDS = ["term_bn", "term_en", "source", "manual", "first_clause",
          "corpus_count", "quiz_questions", "status", "correct_term", "notes"]

STATUS_VALUES = ("in_use", "not_used", "renamed", "never_existed", "unsure")

# Mirrors status_request.csv: one commented line naming the allowed values,
# then only what a reader cannot infer from the columns themselves.
HEADER_COMMENTS = [
    "status must be one of: " + " | ".join(STATUS_VALUES),
    "correct_term: fill in ONLY when status is renamed -- the title now in use",
    "first_clause is where to look the term up, in the form the manual prints;"
    " for a role it is the first clause that USES the term, since no role"
    " title is defined anywhere in either manual",
    "corpus_count is bn_shared.count_literal over dabi_2025 + progoti +"
    " livestock_grow_2026 -- the owning registry's count, with its exclusions",
    "quiz_questions is how many bank questions this term answers;"
    " it is what a not_used verdict would cost",
    "manual is the manual first_clause belongs to, not a list of every manual"
    " the term appears in",
]

# The status sheet's idiom for a term the manuals name but never define.
NOTE_NO_DEF = ("no defining clause -- first_clause is the first clause that "
               "uses the term")
NOTE_NOT_NAMED = "not named in either manual"
NOTE_NO_CLAUSE = ("named in the manual but never inside a numbered clause, so "
                  "there is nothing to cite -- read the front matter")


# --------------------------------------------------------------- vocabulary
def role_terms():
    """[(term_bn, term_en, role_key)] in ROLES order, deduplicated.

    Each role declares up to three names -- the Dabi one, the Progoti one and
    an abbreviation -- and they are separate TERMS for review even when they
    are one role: the field team can retire an abbreviation while keeping the
    full title. The abbrev field also holds one composite label, two names
    joined by a slash, which is a display string and not a term anybody uses;
    it is skipped, which is what makes this 16 terms rather than 17.
    """
    out, seen = [], set()
    for key, name_dabi, name_progoti, abbrev, english, _note in R.ROLES:
        for name in (name_dabi, name_progoti, abbrev):
            if not name or "/" in name:
                continue
            k = canon(name)
            if k in seen:
                continue
            seen.add(k)
            out.append((name, english, key))
    return out


def english_alias(aliases):
    """The first alias segment that is English. -> str

    An aliases field mixes spellings, English names and prose notes. A segment
    with Latin letters and no Bengali is an English name; anything else is a
    Bengali spelling variant or a sentence about the term, neither of which
    belongs in term_en. Same rule status_request.csv was filled by, which is
    why its product_name_en column is blank for most rows.
    """
    for seg in str(aliases or "").split(";"):
        seg = seg.strip()
        if not seg:
            continue
        if any("a" <= c.lower() <= "z" for c in seg) and not any(
                0x0980 <= ord(c) <= 0x09FF for c in seg):
            return seg
    return ""


def product_terms():
    """[(term_bn, term_en, source, key, pattern)] -- programmes, then products,
    then entities, in productmatch's own order."""
    out = []
    for key, (name, aliases) in PM.PROGRAMME_NAMES.items():
        pat = dict(PM.PROGRAMME_PATTERNS).get(key)
        out.append((name, english_alias(aliases),
                    f"productmatch.PROGRAMME_NAMES:{key}", key, pat))
    for key, name, aliases, pat in PM.NAMED:
        out.append((name, english_alias(aliases),
                    f"productmatch.NAMED:{key}", key, pat))
    for key, _etype, name, aliases, pat in PM.ENTITIES:
        out.append((name, english_alias(aliases),
                    f"productmatch.ENTITIES:{key}", key, pat))
    return out


# ------------------------------------------------------------------ corpus
def manifest_rows():
    """{chunk_id: manifest row}. The manifest is where clause_display lives.

    dedupe.clause_pair is the one clause parser and the manifest persists its
    output, so this module holds no clause regex -- same reason app.py does
    not.
    """
    doc = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {r["chunk_id"]: r for r in doc.get("chunks") or []}


def load_chunks(corpora):
    """[chunk] in the given corpus order, each with a canon'd _n field."""
    out = []
    for tag in corpora:
        f = P / f"chunks_{tag}_final.jsonl"
        if not f.exists():
            print(f"  [warn] {f} not present; {tag} not counted.",
                  file=sys.stderr)
            continue
        for line in f.open(encoding="utf-8"):
            c = json.loads(line)
            c["_n"] = canon(c["display_text"])
            out.append(c)
    return out


def role_matcher(term):
    """Match a role term the way roles.py counts it. -> callable str -> bool

    The term's exclusion contexts are masked before the search, so a hit
    inside a longer word cannot decide where the reviewer is sent.
    """
    rx = canon_pattern(term)
    return lambda t: bool(rx.search(REX.mask_exclusions(t, term)))


def product_matcher(pat):
    """Match a product term with the REGISTRY'S pattern. -> callable

    The pattern, never the display name. Convention 2 puts matching in
    productmatch for a reason, and the display name is not always what the
    manual prints: the changelog spells one loan with a different letter, the
    source misspells one insurance name, and one underwriter is written two
    ways. Searching for the display literal found NO clause for a term its
    own pattern finds five occurrences of -- which would have sent the
    reviewer a blank row reading `never existed`.
    """
    rx = PM.compile_pat(pat)
    return lambda t: bool(rx.search(t))


def first_use(match, chunks, mrows):
    """Where to send the reviewer. -> (manifest row or None, occurs anywhere)

    A chunk with no clause is skipped rather than returned with a blank --
    the column exists to be looked up. `occurs` is reported separately
    because the two reasons for an empty clause are different findings: a
    term the manuals never name, and a term named only in front matter that
    carries no clause number.
    """
    occurs = False
    for c in chunks:
        if not match(c["_n"]):
            continue
        occurs = True
        row = mrows.get(c["chunk_id"]) or {}
        if row.get("clause_display"):
            return row, True
    return None, occurs


# ------------------------------------------------------------------- notes
def swallowed_by(term, all_role_terms):
    """Role terms that contain `term` and do not mask it. -> [(term, count)]

    The shape §6.3 lists five times over, found by comparing the vocabulary
    against itself instead of waiting for someone to notice a number. A
    literal that is a canon'd substring of a longer literal is counted inside
    it unless role_exclusions masks that context, and here BOTH sides are
    role terms, so the inflation is a registry counting one of its own rows
    inside another.
    """
    lit = canon(term)
    masked = [canon(x) for x in REX.EXCLUSIONS.get(lit, [])]
    return [other for other in all_role_terms
            if canon(other) != lit and lit in canon(other)
            and canon(other) not in masked]


def pattern_count(pat, texts):
    """Occurrences of a registry PATTERN, for comparison with count_literal."""
    rx = PM.compile_pat(pat)
    return sum(len(rx.findall(canon(t))) for t in texts)


# -------------------------------------------------------------------- rows
def build_rows(args):
    mrows = manifest_rows()

    # Two loads, deliberately. Counting spans every corpus, because the
    # newest manual is the best evidence a title is still alive. Clause
    # lookup spans only the manuals that HAVE clauses, in each registry's own
    # corpus order, so this sheet agrees with products_catalogue.csv and with
    # roles_registry.csv about where a term is first found.
    counted = load_chunks(R.CORPORA)
    texts = [c["display_text"] for c in counted]
    role_chunks = load_chunks(R.DECLARING)
    # products.load_chunks, not the local one: build_rows scores a chunk's
    # heading against its body and needs the _head / _body / _head_products
    # fields only that loader adds. _n is aliased onto its _norm so first_use
    # can search the same canon'd text.
    prod_chunks = PROD.load_chunks()
    for c in prod_chunks:
        c["_n"] = c["_norm"]

    quiz = quiz_answer_counts()
    rows = []

    # ---- roles first
    terms = role_terms()
    names = [t for t, _e, _k in terms]
    for name, english, key in terms:
        occ, _n_chunks = count_literal(name, texts, warn=False)
        use, occurs = first_use(role_matcher(name), role_chunks, mrows)
        notes = [NOTE_NO_DEF if use else
                 (NOTE_NO_CLAUSE if occurs else NOTE_NOT_NAMED)]
        for other in swallowed_by(name, names):
            inside, _ = count_literal(other, texts, warn=False)
            notes.append(
                f"corpus_count is inflated: this term is a substring of "
                f"{other}, which occurs {inside} times and is not masked "
                f"from it in role_exclusions.EXCLUSIONS, so roughly "
                f"{max(occ - inside, 0)} of the {occ} are this term itself")
        rows.append({
            "term_bn": name, "term_en": english,
            "source": f"roles.ROLES:{key}",
            "manual": (use or {}).get("manual_id", ""),
            "first_clause": (use or {}).get("clause_display", ""),
            "corpus_count": occ,
            "quiz_questions": quiz.get(canon(name), 0),
            "status": "", "correct_term": "",
            "notes": "; ".join(notes)})

    # ---- then products, programmes and entities
    defining = defining_clauses(prod_chunks, mrows, args.debug)
    for name, english, source, key, pat in product_terms():
        occ, _n_chunks = count_literal(name, texts, warn=False)
        row = defining.get(key)
        notes = []
        if row is None:
            match = (product_matcher(pat) if pat else role_matcher(name))
            row, occurs = first_use(match, prod_chunks, mrows)
            notes.append(NOTE_NO_DEF if row else
                         (NOTE_NO_CLAUSE if occurs else NOTE_NOT_NAMED))
        if pat and literal_owner(name) is None:
            pc = pattern_count(pat, texts)
            if pc != occ:
                notes.append(
                    f"corpus_count is by display spelling only: "
                    f"bn_shared.count_literal does not resolve "
                    f"productmatch.ENTITIES, so its {occ} misses the variant "
                    f"spellings the registry pattern catches, which finds "
                    f"{pc}")
        rows.append({
            "term_bn": name, "term_en": english, "source": source,
            "manual": (row or {}).get("manual_id", ""),
            "first_clause": (row or {}).get("clause_display", ""),
            "corpus_count": occ,
            "quiz_questions": quiz.get(canon(name), 0),
            "status": "", "correct_term": "",
            "notes": "; ".join(notes)})
    return rows


def defining_clauses(chunks, mrows, debug=False):
    """{product_key: manifest row of its defining chunk}, empty where none.

    products.py owns what a definition looks like -- its own heading, a
    labelled list item, one of the manual's definition verbs -- and has the
    corrections for the ways that scoring went wrong. Reusing it is what
    makes first_clause here the same clause status_request.csv cites; a second
    scorer would be CONVENTIONS.md §2 defect 2 in a new module.
    """
    out = {}
    for key, _etype, _name, _aliases, dc, _ids in PROD.build_rows(chunks,
                                                                  debug):
        if dc is not None:
            out[key] = mrows.get(dc["chunk_id"]) or {}
    return out


def quiz_answer_counts():
    """{canon'd correct answer: n questions}, from the bank the quiz serves.

    Keyed with quiz_app.answer_key, not with a comparison of my own, so the
    sheet counts an answer the same way the session draw caps it. Importing
    quiz_app runs nothing and reaches no provider or index, per its docstring.
    """
    import quiz_app
    qs, _meta = quiz_app.bank()
    return collections.Counter(quiz_app.answer_key(q) for q in qs)


# ---------------------------------------------------------------- Convention 3
# Three canaries, each a real failure this sheet is one mistake away from.
#
#   1. The count stops being the registry's. A naive count of the programme
#      acronym reads 180 where the guarded one reads 22 -- measured 17 Sep
#      2026, and the whole reason count_literal exists. If the two collapse,
#      every corpus_count on the sheet is a substring count and the reviewer
#      is being shown numbers nobody guarded.
#   2. The quiz column silently reads zero. The role terms answer every one
#      of the bank's role questions, so their quiz_questions must sum to
#      exactly the number of questions the bank itself labels `role`. A
#      broken comparison gives 0 for every term, which is indistinguishable
#      from a vocabulary no question uses -- and would tell the reviewer that
#      a not_used verdict costs nothing.
#   3. That comparison is nukta-asymmetric. One role name carries য়, and
#      roles.py records what happens to a precomposed literal: it silently
#      counts 0. The precomposed form is BUILT from bn_shared.NUKTA_PAIRS,
#      never typed -- typing it would save whichever form the editor chose on
#      both sides, the asymmetry would vanish, and the canary would pass
#      while proving nothing.
def _precompose(text):
    """Decomposed nukta pairs -> precomposed, from the shared table."""
    for pre, decomp in NUKTA_PAIRS.items():
        text = text.replace(decomp, pre)
    return text


def canary_selftest(verbose=True):
    ok = True

    # 1 -- guarded vs naive, fixture and literal both from productmatch
    cdp = PM.PROGRAMME_NAMES["cdp"][0]
    fixture = " ".join((PM.PROGRAMME_NAMES["ncdp"][0],
                        PM.PROGRAMME_NAMES["scdp"][0], cdp))
    guarded, _ = count_literal(cdp, [fixture], warn=False)
    naive = len(canon_pattern(cdp).findall(canon(fixture)))
    hit = guarded == 1 and naive == 3
    ok &= hit
    if verbose:
        print(f"  canary guarded_count       guarded={guarded} naive={naive} "
              f"(expect 1 vs 3)  [{'DETECTED' if hit else 'MISSED'}]")

    # 2 -- the quiz column is not vacuously zero
    import quiz_app
    qs, _meta = quiz_app.bank()
    quiz = quiz_answer_counts()
    expected = sum(1 for q in qs if q.get("kind") == "role")
    got = sum(quiz.get(canon(t), 0) for t, _e, _k in role_terms())
    hit2 = got == expected and expected > 0
    ok &= hit2
    if verbose:
        print(f"  canary quiz_column         role terms answer {got} of the "
              f"{expected} questions the bank labels role  "
              f"[{'PASS' if hit2 else 'FAIL'}]")

    # 3 -- the same comparison, one side precomposed
    nukta = next((t for t, _e, _k in role_terms() if _precompose(t) != t), None)
    if nukta is None:
        if verbose:
            print("  canary nukta_asymmetry     NOT BUILDABLE: no role term "
                  "carries a nukta letter")
        return False
    n_canon = quiz.get(canon(_precompose(nukta)), 0)
    n_raw = sum(1 for q in qs if q["correct"] == _precompose(nukta))
    hit3 = n_raw == 0 and n_canon > 0
    ok &= hit3
    if verbose:
        print(f"  canary nukta_asymmetry     {nukta}: raw={n_raw} "
              f"canon={n_canon} (expect 0 vs >0)  "
              f"[{'DETECTED' if hit3 else 'MISSED'}]")
    return bool(ok)


# -------------------------------------------------------------------- output
def write_csv(rows, out):
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        for line in HEADER_COMMENTS:
            f.write(f"# {line}\n")
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def print_sheet(rows):
    """The sheet as it will be read, not a summary of it."""
    for line in HEADER_COMMENTS:
        print(f"# {line}")
    print()
    kinds = [("ROLES", lambda r: r["source"].startswith("roles.")),
             ("PRODUCTS, PROGRAMMES AND ENTITIES",
              lambda r: not r["source"].startswith("roles."))]
    for label, pick in kinds:
        sel = [r for r in rows if pick(r)]
        print(f"{label}  ({len(sel)} terms)")
        for r in sel:
            print(f"  {r['term_bn']}")
            print(f"      en {r['term_en'] or '-'}    "
                  f"{r['source']}    {r['manual'] or '-'} "
                  f"{r['first_clause'] or '-'}")
            print(f"      corpus {r['corpus_count']}    "
                  f"quiz {r['quiz_questions']}    "
                  f"status [ ]    correct_term [ ]")
            if r["notes"]:
                print(f"      notes: {r['notes']}")
        print()


def summarise(rows):
    roles = [r for r in rows if r["source"].startswith("roles.")]
    prods = [r for r in rows if not r["source"].startswith("roles.")]
    print(f"  {len(rows)} terms: {len(roles)} roles, {len(prods)} products, "
          f"programmes and entities")
    print(f"  quiz questions covered: "
          f"{sum(r['quiz_questions'] for r in roles)} by role terms, "
          f"{sum(r['quiz_questions'] for r in prods)} by product terms")
    no_clause = [r for r in rows if not r["first_clause"]]
    print(f"  no clause to look up: {len(no_clause)} "
          f"({', '.join(r['term_bn'] for r in no_clause) or 'none'})")
    zero = [r for r in rows if not r["corpus_count"]]
    print(f"  corpus_count 0: {len(zero)} "
          f"({', '.join(r['term_bn'] for r in zero) or 'none'})")
    print("\n  ranked by what a not_used verdict would cost:")
    for r in sorted(rows, key=lambda r: -r["quiz_questions"]):
        if not r["quiz_questions"]:
            continue
        print(f"    {r['quiz_questions']:>3} questions   {r['term_bn']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--debug", action="store_true",
                    help="products.py's defining-section scoring")
    args = ap.parse_args()

    print("CANARY")
    if not canary_selftest():
        print("\nSELF-CHECK FAILED -- this sheet is not measuring anything")
        return 1
    print()

    rows = build_rows(args)
    out = Path(args.out)
    write_csv(rows, out)
    print_sheet(rows)
    summarise(rows)
    print(f"\n  {len(rows)} terms -> {out}")
    print("  status and correct_term are empty by design; the verdict is the "
          "reviewer's.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
