"""validate_gold.py -- check a gold file that is still being written.

Designed for a HALF-WRITTEN file. Remaining TODO markers are counted and
listed, not treated as errors, unless --strict is passed. Everything that has
actually been filled in is checked properly.

THREE FACT KINDS, three different checks. Definitions are gold/bootstrap.yaml's
own (see its FACT KINDS header); this file implements them.

  kind: literal   must occur VERBATIM in the chunk named by its `ref`.
                  Ref resolution:
                    - `ref` given            -> that chunk
                    - no ref, question has exactly ONE source_refs entry
                                             -> that entry
                    - no ref, several entries -> FAIL, ambiguous provenance.
                  The last case is the point of the rule: a fact that could
                  have come from either of two chunks is not a citation, and
                  picking one silently would invent provenance.

  kind: absent    must NOT occur ANYWHERE in the corpus. Its evidence is
                  absence, so finding it makes the fact FALSE -- the check
                  fails and names the chunk that contains it. Its
                  source_refs point at the clause that SHOULD have carried
                  it and are navigation only; they are NOT verbatim-checked,
                  and their chunk_id is routinely "TODO" by design.

  kind: claim     a paraphrase with no literal to quote. The verbatim check
                  is skipped entirely. These need --judge llm to score, at
                  one API call per fact, so their count is reported as a
                  cost figure.

  Default when `kind` is absent from a fact: literal.

WHY VERBATIM-IN-THE-NAMED-CHUNK AND NOT ANYWHERE
A fact that is true of the corpus but not of the chunk you cited is a
citation error, and it is invisible to any check that searches the whole
corpus. That is the failure this file exists to catch.

Usage:
  python validate_gold.py --gold gold/bootstrap.yaml
  python validate_gold.py --gold gold/bootstrap.yaml --strict
"""

import argparse
import difflib
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from bn_shared import display_norm

CORPUS_FILES = [
    "data/processed/chunks_dabi_2025_final.jsonl",
    "data/processed/chunks_progoti_final.jsonl",
    "data/processed/chunks_livestock_grow_2026_final.jsonl",
]

TODO_RX = re.compile(r"^\s*TODO\b", re.I)
KINDS = ("literal", "absent", "claim")

# The single exemption, by id, with its reason. NOT generalised: a rule with
# one exception is still a rule; a rule with a pattern-matched exception is a
# different rule. If a second question needs this, add it here by id and
# write down why, so the list stays a readable list of decisions.
UNANSWERABLE_EXEMPT = {
    "G-218": "documents in its own comment why it carries neither "
             "forbidden_numbers_from_clause nor must_not_contain_literals",
    "G-220": "the wrong answer names branch posts, but its second gold fact "
             "says branch posts exist elsewhere -- so a correct answer may "
             "name them too while redirecting, and must_not_contain_literals "
             "seeded with role names would flag both alike",
}


# ------------------------------------------------------------- fact accessors

def fact_text(f):
    return f.get("text", "") if isinstance(f, dict) else str(f)


def fact_kind(f):
    k = (f.get("kind") or "literal") if isinstance(f, dict) else "literal"
    return k if k in KINDS else f"?{k}"


def is_todo(v):
    return bool(TODO_RX.match(str(v or "")))


def source_refs(q):
    """-> [(chunk_id, render_or_None)]; entries whose chunk_id is TODO kept
    so they can be reported as unfilled rather than silently dropped."""
    out = []
    for r in q.get("source_refs") or []:
        if isinstance(r, dict):
            out.append((str(r.get("chunk_id") or ""), r.get("render")))
        else:
            out.append((str(r), None))
    return out


def ref_clause_ok(r, heads):
    """(ok, reason) for one source_refs entry's `clause`.

    CLAUSE IS THE REFERENCE in this corpus -- there are no usable page
    numbers -- so a filled ref without one normally cannot be looked up.
    The exception is front matter: সূচনা, পরিবর্তন সমূহ and the organogram
    chapter head carry no clause number at all, and demanding one there
    would force an invented citation. A null clause is therefore accepted
    exactly when the named chunk has a heading_path to navigate by, which
    is what the reader actually follows.

    An ABSENT key is not the same as an explicit null: absent means nobody
    decided, null means someone looked and there is none. Only null passes.
    """
    if not isinstance(r, dict):
        return True, ""
    cid = str(r.get("chunk_id") or "")
    if not cid or is_todo(cid):
        return True, ""                    # unfilled: reported as a TODO
    if "clause" not in r:
        return False, "no clause key; use `clause: null` if the chunk has none"
    cl = r.get("clause")
    if cl is None:
        path = heads.get(cid)
        if path is None:
            return False, f"clause is null and {cid} is not in the corpus"
        if not path:
            return False, (f"clause is null and {cid} has an empty "
                           f"heading_path -- nothing to navigate by")
        return True, ""
    if is_todo(cl):
        return True, ""                    # unfilled: reported as a TODO
    return True, ""


def resolve_ref(f, q):
    """-> (chunk_id, error). error is a string when it cannot be resolved."""
    if isinstance(f, dict) and f.get("ref"):
        ref = str(f["ref"])
        if is_todo(ref):
            return None, "ref is still TODO"
        return ref, None
    refs = [c for c, _r in source_refs(q)]
    if len(refs) == 1:
        if is_todo(refs[0]) or not refs[0]:
            return None, "question's single source_refs chunk_id is TODO"
        return refs[0], None
    if not refs:
            return None, "no ref and the question has no source_refs"
    return None, (f"no ref and the question has {len(refs)} source_refs "
                  f"-- ambiguous provenance")


def fact_renders(f, q):
    out = [f["render"]] if isinstance(f, dict) and f.get("render") else []
    out += [r for _c, r in source_refs(q) if r]
    return out


# ------------------------------------------------------------------- corpus

def load_corpus(files=CORPUS_FILES):
    """-> (text_by_id, heading_path_by_id, missing_files).

    heading_path is carried separately because a source_ref with a null
    clause is navigated by it; see ref_clause_ok.
    """
    by_id, heads, missing = {}, {}, []
    for f in files:
        p = Path(f)
        if not p.exists():
            missing.append(f)
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            c = json.loads(line)
            by_id[c["chunk_id"]] = c.get("display_text") or c.get("text") or ""
            heads[c["chunk_id"]] = list(c.get("heading_path") or [])
    return by_id, heads, missing


# ------------------------------------------------------- manifest drift
# data/processed/ is not in git; gold/ is. So a clone can read a
# source_refs.chunk_id but not the chunk, and cannot tell whether the text
# the fact was quoted from is still the text on disk.
#
# The verbatim check CANNOT catch this. It runs against the CURRENT chunk, so
# a fact quoted from an older version of that chunk either still matches --
# and passes while citing text that has moved -- or stops matching and looks
# like an authoring error rather than a corpus change. Those are different
# problems with different fixes and the verbatim result does not separate
# them.
#
# data/processed/chunk_manifest.json is committed and holds a sha256 of each
# chunk's display_text (plus id, manual, clause, heading, length -- no body).
# Comparing it to the corpus on disk says which of the two happened.
MANIFEST_PATH = Path("data/processed/chunk_manifest.json")


def load_manifest(path=MANIFEST_PATH):
    """-> ({chunk_id: row}, error_or_None)."""
    p = Path(path)
    if not p.exists():
        return {}, f"manifest not found at {p} -- run dedupe.py to write it"
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        return {}, f"manifest unreadable: {type(e).__name__}: {e}"
    return {r["chunk_id"]: r for r in doc.get("chunks") or []}, None


def chunk_sha(display_text):
    """Must match dedupe.manifest_sha exactly: sha256 of the stored text."""
    return hashlib.sha256((display_text or "").encode("utf-8")).hexdigest()


def check_manifest_drift(refs, corpus, manifest):
    """-> list of (chunk_id, outcome, detail) for each ref'd chunk.

    outcome is "ok" | "drift" | "unknown". `refs` is the set of chunk_ids
    cited anywhere in the gold file; `corpus` is {chunk_id: display_text}.
    """
    out = []
    for cid in sorted(refs):
        row = manifest.get(cid)
        if row is None:
            out.append((cid, "unknown",
                        "not in the manifest -- the ref names a chunk the "
                        "manifest has never seen"))
            continue
        if cid not in corpus:
            out.append((cid, "drift",
                        f"in the manifest but NOT in the corpus on disk "
                        f"(manifest sha {row['sha256'][:12]})"))
            continue
        now = chunk_sha(corpus[cid])
        if now != row["sha256"]:
            out.append((cid, "drift",
                        f"manifest {row['sha256'][:12]} != corpus "
                        f"{now[:12]} -- the chunk changed since the fact was "
                        f"quoted from it, so the fact may be stale even "
                        f"though its verbatim check passes"))
        else:
            out.append((cid, "ok", row["sha256"][:12]))
    return out


def manifest_manual_ids(manifest):
    """Every manual id the corpus actually uses. -> set"""
    return {row.get("manual_id") for row in manifest.values()
            if row.get("manual_id")}


def cite_manual_id(citation):
    """The manual id part of a must_cite string. -> str

    A citation is "<manual_id> §<clause>" or bare "<manual_id>". Split on the
    section sign rather than on whitespace: a manual id never contains one,
    and splitting on space would truncate nothing today but would the day an
    id gains a space.
    """
    return (citation or "").split("\u00a7")[0].strip()


def unknown_cite_manuals(gold, manifest):
    """must_cite entries naming a manual the manifest has never seen.
    -> [(qid, citation, manual_id)]

    WHY THIS EXISTS, and it is a failure I introduced and did not notice.
    Both gold files cited "progoti_2025" for 30 entries. The corpus manual id
    is "progoti". Nothing checked the two against each other, so 12 of the 17
    recall-scoreable questions required a citation string that could not be
    produced from any chunk, and citation accuracy was capped near 5/17
    before any model was called. Both models scored 1/17 on a real run and
    the number looked like a model failure.

    A wrong manual id cannot be caught by reading the gold file -- progoti and
    progoti_2025 both look like plausible ids -- so it has to be checked
    against the corpus.
    """
    known = manifest_manual_ids(manifest)
    if not known:
        return []
    out = []
    for q in gold.get("questions") or []:
        for c in q.get("must_cite") or []:
            if is_todo(str(c)):
                continue
            mid = cite_manual_id(str(c))
            if mid and mid not in known:
                out.append((q.get("id", "?"), str(c), mid))
    return out


# manual_scope values that name no single manual.
SCOPE_MULTI = frozenset({"any", "both", "ambiguous"})


def scope_manual_id(scope, known):
    """manual_scope -> a corpus manual_id, or None if it names no single one.

    Same prefix resolution retrieve.scope_to_manual_id uses: the gold files
    write dabi / progoti / livestock_grow and the corpus writes dabi_2025 /
    progoti / livestock_grow_2026.
    """
    if not isinstance(scope, str):
        return None
    sc = scope.strip().lower()
    if sc in SCOPE_MULTI or not sc:
        return None
    if sc in known:
        return sc
    cands = [k for k in known if k.startswith(sc)]
    return cands[0] if len(cands) == 1 else None


def scope_cite_conflicts(gold, manifest):
    """Questions whose manual_scope and must_cite contradict each other.
    -> [(qid, detail)]

    A REAL CLASS OF DEFECT, and G-205 was an instance: manual_scope "any"
    with must_cite naming dabi_2025 alone. The scope says either manual
    answers the question; the citation demanded one specific manual. Every
    answer citing the Progoti copy -- byte-identical, same clause -- scored
    wrong, and nothing compared the two fields.

    TWO DIRECTIONS:
      1. scope names no single manual (any / both / ambiguous) but must_cite
         names exactly one manual, and must_cite_mode is not "any". Either
         the content really is in one manual only, in which case the scope is
         wrong, or it is in both and the mode should be any-of.
      2. scope names one manual and must_cite never mentions it. The question
         is scoped to a manual it does not ask to be cited.
    """
    known = manifest_manual_ids(manifest)
    out = []
    for q in gold.get("questions") or []:
        cites = [str(c) for c in (q.get("must_cite") or [])
                 if not is_todo(str(c))]
        if not cites:
            continue
        qid = q.get("id", "?")
        scope = q.get("manual_scope")
        mode = (q.get("must_cite_mode") or "all").strip().lower()
        manuals = {cite_manual_id(c) for c in cites}
        manuals.discard("")
        sc = str(scope).strip().lower() if isinstance(scope, str) else ""
        if sc in SCOPE_MULTI:
            if len(manuals) == 1 and mode != "any":
                only = sorted(manuals)[0]
                out.append((qid, f'manual_scope "{sc}" but must_cite names '
                                 f'only {only} and must_cite_mode is "{mode}"'
                                 f' -- either the scope is wrong or the mode '
                                 f'should be any'))
        else:
            want = scope_manual_id(scope, known)
            if want and want not in manuals:
                out.append((qid, f'manual_scope "{sc}" resolves to {want}, '
                                 f'which must_cite does not mention '
                                 f'({sorted(manuals)})'))
    return out


def manifest_orphans(corpus, manifest):
    """Chunk ids the manifest has and the corpus no longer does."""
    return sorted(set(manifest) - set(corpus))


def verbatim_in(literal, text):
    """(hit, how). Exact substring first, then display_norm on both sides.

    The distinction is reported: 'exact' means the citation can be copied
    as-is; 'normalised' means the literal is there but spelled with
    different whitespace or a zero-width joiner, which bites anything
    comparing raw strings later.
    """
    if not literal:
        return False, "empty"
    if literal in text:
        return True, "exact"
    if display_norm(literal) in display_norm(text):
        return True, "normalised"
    return False, "absent"


def occurs_anywhere(literal, corpus):
    """-> chunk_id of the first chunk containing it, or None."""
    dn = display_norm(literal)
    if not dn:
        return None
    for cid, t in corpus.items():
        if literal in t or dn in display_norm(t):
            return cid
    return None


def closest(literal, text, corpus):
    lines = [s for s in re.split(r"[।\n]", text) if s.strip()]
    near = difflib.get_close_matches(literal, lines, n=1, cutoff=0.3)
    return (near[0].strip() if near else None), occurs_anywhere(literal,
                                                                corpus)


# ----------------------------------------------------------------- validate

def validate(gold, corpus, heads=None, root=Path(".")):
    heads = {} if heads is None else heads
    qs = gold.get("questions") or []
    rep = {
        "n_questions": len(qs),
        "todo_facts": defaultdict(list),
        "todo_refs": [],
        "kinds": Counter(),
        "kinds_filled": Counter(),
        "claim_questions": [],
        "unresolved_ref": [],
        "unknown_chunk": [],
        "bad_verbatim": [],
        "normalised_only": [],
        "absent_but_present": [],
        "missing_render": [],
        "unarmed_unanswerable": [],
        "exempt_used": [],
        "answerable": Counter(),
        "types": Counter(),
        "bad_kind": [],
        "bad_ref_clause": [],
        "manifest_error": None,
        "manifest_drift": [],
        "manifest_unknown": [],
        "manifest_orphans": [],
        "manifest_ok": 0,
        "unknown_cite_manual": [],
        "scope_cite_conflict": [],
    }
    for q in qs:
        qid = q.get("id", "?")
        a = q.get("answerable")
        a = ("yes" if a is True else "no" if a is False
             else str(a).strip().lower())
        rep["answerable"][a] += 1
        rep["types"][q.get("type", "?")] += 1

        for c, _r in source_refs(q):
            if is_todo(c) or not c:
                rep["todo_refs"].append((qid, c or "(empty)"))

        for r in q.get("source_refs") or []:
            ok_cl, why = ref_clause_ok(r, heads)
            if not ok_cl:
                rep["bad_ref_clause"].append(
                    (qid, str(r.get("chunk_id", "?")), why))

        for f in q.get("gold_facts") or []:
            lit, kind = fact_text(f), fact_kind(f)
            rep["kinds"][kind] += 1
            if kind.startswith("?"):
                rep["bad_kind"].append((qid, lit, kind[1:]))
                continue
            if is_todo(lit):
                rep["todo_facts"][qid].append(f"[{kind}] {lit}")
                continue
            rep["kinds_filled"][kind] += 1

            # ---- claim: no verbatim check at all
            if kind == "claim":
                if qid not in rep["claim_questions"]:
                    rep["claim_questions"].append(qid)
                continue

            # ---- absent: must not occur ANYWHERE. refs are navigation only.
            if kind == "absent":
                where = occurs_anywhere(lit, corpus)
                if where:
                    rep["absent_but_present"].append((qid, lit, where))
                continue

            # ---- literal: verbatim in the chunk its ref names
            cid, err = resolve_ref(f, q)
            if err:
                rep["unresolved_ref"].append((qid, lit, err))
                continue
            if cid not in corpus:
                rep["unknown_chunk"].append((qid, lit, cid))
                continue
            hit, how = verbatim_in(lit, corpus[cid])
            if not hit:
                near, elsewhere = closest(lit, corpus[cid], corpus)
                rep["bad_verbatim"].append((qid, lit, cid, near, elsewhere))
            elif how == "normalised":
                rep["normalised_only"].append((qid, lit, cid))

            for r in fact_renders(f, q):
                if not (root / r).exists():
                    rep["missing_render"].append((qid, lit, r))

        if a == "no":
            armed = bool(q.get("forbidden_numbers_from_clause")
                         or q.get("must_not_contain_literals"))
            if not armed:
                if qid in UNANSWERABLE_EXEMPT:
                    rep["exempt_used"].append((qid, UNANSWERABLE_EXEMPT[qid]))
                else:
                    rep["unarmed_unanswerable"].append(qid)
    # MANIFEST DRIFT, over every chunk_id the gold file cites anywhere --
    # source_refs and per-fact refs alike, since either can go stale.
    cited = set()
    for q in qs:
        for c, _r in source_refs(q):
            if c and not is_todo(c):
                cited.add(c)
        for f in q.get("gold_facts") or []:
            r = f.get("ref") if isinstance(f, dict) else None
            if r and not is_todo(str(r)):
                cited.add(str(r))
    manifest, mf_err = load_manifest()
    rep["manifest_error"] = mf_err
    if not mf_err:
        for cid, outcome, detail in check_manifest_drift(cited, corpus, manifest):
            if outcome == "ok":
                rep["manifest_ok"] += 1
            elif outcome == "drift":
                rep["manifest_drift"].append((cid, detail))
            else:
                rep["manifest_unknown"].append((cid, detail))
        rep["manifest_orphans"] = manifest_orphans(corpus, manifest)
        rep["unknown_cite_manual"] = unknown_cite_manuals(gold, manifest)
        rep["scope_cite_conflict"] = scope_cite_conflicts(gold, manifest)

    return rep


# ------------------------------------------------------------------- canary

def _find_literal_anchor(gold, corpus, want_qid=None):
    """A filled literal fact with a resolvable ref that verifies."""
    for q in gold.get("questions") or []:
        if want_qid and q.get("id") != want_qid:
            continue
        for f in q.get("gold_facts") or []:
            if fact_kind(f) != "literal" or is_todo(fact_text(f)):
                continue
            cid, err = resolve_ref(f, q)
            if err or cid not in corpus:
                continue
            if verbatim_in(fact_text(f), corpus[cid])[0]:
                return q.get("id"), fact_text(f), cid
    return None


def _find_absent_anchor(gold):
    for q in gold.get("questions") or []:
        for f in q.get("gold_facts") or []:
            if fact_kind(f) == "absent" and not is_todo(fact_text(f)):
                return q.get("id"), fact_text(f)
    return None


FORBIDDEN = Path("out/forbidden.json")
FORBIDDEN_RAW = Path("out/forbidden_raw.json")
# noqa: BN4 a digit CLASS, not a word: canon_pattern builds nukta
# alternations for a literal string and would corrupt a character range.
# Bengali digits carry no nukta, so there is nothing for it to unify.
CLAUSE_SHAPED_RX = re.compile(r"^[0-9০-৯]+(?:\.[0-9০-৯]+)+$")  # noqa: BN4 digit class, not a word

# A CORRECT abstention for G-209, hand-written. It does the two things a
# right answer has to do -- name the clause it is ruling out, and give the
# officer numbered next steps -- and those are exactly the two things the
# unfiltered forbidden list punished.
G209_GOOD_ABSTENTION = (
    "ঋণ অনুমোদনের সিলিং ম্যানুয়ালে উল্লেখ নেই। "
    "প্রাধিকারসূচি (ধারা ৭.১) শুধু ক্রয় ও প্রশাসনিক ব্যয়ের অনুমোদনের সীমা "
    "নির্ধারণ করে, ঋণ অনুমোদনের নয়। "
    "করণীয়: ১. ধারা ৭.১ পড়ে নিশ্চিত হোন। "
    "২. ঋণ অনুমোদনের ক্ষমতার জন্য ঊর্ধ্বতন কর্তৃপক্ষের সাথে কথা বলুন।"
)
# No role or product literal in that string, deliberately -- Convention 2.
# It names no post; the two properties it has to have are that it cites
# ধারা ৭.১ and that it numbers its steps.


def canary_forbidden_not_overbroad(gold, verbose=True):
    """Convention 3, from a REAL failure observed on 16 Sep 2026.

    The forbidden-figure check compared BARE numbers. ex.py stores figures
    as bare tokens, so §7.1's list held the ক্রম. serial column ১-৩২ and
    the clause number itself, and the correct G-209 abstention above scored
    violations for citing ধারা ৭.১ and for numbering its two steps.

    Three cells, on the SAME answer, so the fix is attributed to the right
    change rather than to the list:

      old matcher + raw list   must produce violations   (the bug)
      new matcher + raw list   must produce none         (the scorer is
                                                          what fixed it)
      new matcher + filtered   must produce none         (shipping state)

    Then the other direction, which matters more: a made-up ceiling written
    WITH its unit must still be caught, or the filter has simply disarmed
    the trap. That half is what the magnitude floor broke on 16 Sep before
    it was removed.
    """
    q = next((x for x in (gold.get("questions") or [])
              if x.get("id") == "G-209"), None)
    if q is None:
        if verbose:
            print("  canary forbidden_overbroad  G-209 IS NOT IN THE GOLD "
                  "FILE -- not buildable, not invented")
        return True
    if not FORBIDDEN.exists():
        if verbose:
            print(f"  canary forbidden_overbroad  {FORBIDDEN} missing; "
                  f"cannot check")
        return True

    import gold_eval
    filt = json.loads(FORBIDDEN.read_text(encoding="utf-8"))
    good = G209_GOOD_ABSTENTION
    clauses = q.get("forbidden_numbers_from_clause") or []

    def count(fn, fb, answer):
        return sum(len(fn(cl, answer, fb)) for cl in clauses)

    if FORBIDDEN_RAW.exists():
        rawf = json.loads(FORBIDDEN_RAW.read_text(encoding="utf-8"))
        old_raw = count(gold_eval.bare_number_violations, rawf, good)
        new_raw = count(gold_eval.forbidden_violations, rawf, good)
        new_filt = count(gold_eval.forbidden_violations, filt, good)
        ok1 = old_raw > 0 and new_raw == 0 and new_filt == 0
        if verbose:
            print(f"  canary forbidden_overbroad  correct abstention: "
                  f"bare+raw={old_raw}, pair+raw={new_raw}, "
                  f"pair+filtered={new_filt}  "
                  f"[{'DETECTED' if ok1 else 'MISSED'}]")
    else:
        new_filt = count(gold_eval.forbidden_violations, filt, good)
        ok1 = new_filt == 0
        if verbose:
            print(f"  canary forbidden_overbroad  pair+filtered={new_filt}; "
                  f"{FORBIDDEN_RAW} absent so the failing side cannot be "
                  f"replayed  [{'PASS' if ok1 else 'FAIL'}]")

    # ---- the trap must still fire. Built from a real pair on the list,
    # never a typed figure: take the largest armed (value, unit) pair for
    # one of this question's own clauses and invent an answer quoting it.
    armed = []
    for cl in clauses:
        armed += (filt.get("_units") or {}).get(cl) or []
    if not armed:
        ok2 = False
        if verbose:
            print("  canary forbidden_armed      NO (value, unit) PAIRS for "
                  f"{clauses} -- the trap is disarmed  [FAIL]")
    else:
        pair = max(armed, key=lambda p: float(p.split(" ")[0]))
        val, unit = pair.split(" ", 1)
        invented = f"{val} {unit} পর্যন্ত অনুমোদন করা যায়।"
        caught = count(gold_eval.forbidden_violations, filt, invented)
        # and the ordinal form of the same value must NOT fire
        ordinal = f"{val}ম ধাপে যোগাযোগ করুন।"
        false_pos = count(gold_eval.forbidden_violations, filt, ordinal)
        ok2 = caught > 0 and false_pos == 0
        if verbose:
            print(f"  canary forbidden_armed      {len(armed)} armed pair(s); "
                  f"{pair!r} quoted={caught} hit(s), as an ordinal="
                  f"{false_pos}  [{'DETECTED' if ok2 else 'MISSED'}]")

    # ---- and the one invariant the filter still owes: no clause-shaped
    # token survives. There is no magnitude floor any more, by design.
    bad_tokens = [(k, n) for k, vals in filt.items()
                  if not k.startswith("_") and isinstance(vals, list)
                  for n in vals if CLAUSE_SHAPED_RX.match(str(n))]
    ok3 = not bad_tokens
    if verbose:
        print("  canary forbidden_filtered   "
              + ("no clause-shaped token survives the filter  [PASS]"
                 if ok3 else
                 f"{len(bad_tokens)} slipped through: {bad_tokens[:3]}  "
                 f"[FAIL]"))
    return ok1 and ok2 and ok3


def canary_selftest(gold, corpus, verbose=True):
    """Convention 3. Three canaries, all built from REAL facts in the file.

    1. MISPOINTED LITERAL. Take a filled literal fact that verifies against
       its own chunk -- G-213's আরএম at progoti_0133 -- and point it at a
       different chunk. The verbatim check must FAIL. If it passes, the
       check is not reading the named chunk and every citation in the file
       is unverified while the report says clean.

    2. FALSE ABSENT. Take a real absent fact and substitute a string that
       DOES occur in the corpus. The absent check must fail on it. Without
       this, an absent check that never finds anything is indistinguishable
       from one that never looks -- and absent facts are exactly the ones
       whose evidence nobody can see.

    3. MANIFEST DRIFT. Take a chunk a committed gold fact actually cites,
       mutate its sha in a COPY of the manifest, and require the drift check
       to fire. A drift check that never fires is worse than none: it prints
       "N chunks match" every run and reports clean while the corpus moves
       under the citations. Nothing is invented here -- the chunk, the ref
       and the true sha are all real; only the recorded sha is flipped, in
       memory, and the file on disk is never touched.
    """
    ok = True

    anchor = (_find_literal_anchor(gold, corpus, "G-213")
              or _find_literal_anchor(gold, corpus))
    if anchor is None:
        if verbose:
            print("  canary mispointed_literal   NO VERIFIED LITERAL FACT "
                  "WITH A RESOLVABLE ref YET")
            print("        -> not buildable from a real fact; will not "
                  "invent one. Arms itself")
            print("           as soon as one literal fact verifies.")
    else:
        qid, lit, cid = anchor
        wrong = next((c for c in corpus
                      if c != cid and not verbatim_in(lit, corpus[c])[0]),
                     None)
        right = verbatim_in(lit, corpus[cid])[0]
        bad = verbatim_in(lit, corpus[wrong])[0] if wrong else True
        ok1 = right and not bad
        if verbose:
            print(f"  canary mispointed_literal   {qid} {lit!r} in {cid}"
                  f"={right}, in {wrong}={bad}  "
                  f"[{'DETECTED' if ok1 else 'MISSED'}]")
        ok &= ok1

    ab = _find_absent_anchor(gold)
    if ab is None:
        if verbose:
            print("  canary false_absent         no filled absent fact to "
                  "build from")
    else:
        qid, lit = ab
        # Real absent fact must be absent...
        really_absent = occurs_anywhere(lit, corpus) is None
        # ...and a string taken FROM the corpus must be caught as present.
        sample_cid = next(iter(corpus))
        present_str = display_norm(corpus[sample_cid])[:24].strip()
        caught = occurs_anywhere(present_str, corpus)
        ok2 = really_absent and bool(caught)
        if verbose:
            print(f"  canary false_absent         {qid} real absent fact "
                  f"absent={really_absent}; corpus string caught in "
                  f"{caught or 'NOTHING'}  "
                  f"[{'DETECTED' if ok2 else 'MISSED'}]")
        ok &= ok2

    ok &= canary_forbidden_not_overbroad(gold, verbose)
    # 3. MANIFEST DRIFT, from a real cited chunk.
    manifest, mf_err = load_manifest()
    cited = []
    for q in gold.get("questions") or []:
        for c, _r in source_refs(q):
            if c and not is_todo(c) and c in manifest and c in corpus:
                cited.append(c)
    if mf_err or not cited:
        if verbose:
            why = mf_err or "no cited chunk is in both the manifest and corpus"
            print(f"  canary manifest_drift       NOT BUILDABLE: {why}")
            print("        -> will not invent a chunk; arms itself as soon as "
                  "one ref resolves.")
    else:
        cid = sorted(cited)[0]
        true_sha = manifest[cid]["sha256"]
        clean = check_manifest_drift({cid}, corpus, manifest)
        mutated = dict(manifest)
        mutated[cid] = dict(manifest[cid], sha256="0" * 64)
        fired = check_manifest_drift({cid}, corpus, mutated)
        missing = check_manifest_drift({cid}, corpus, {})
        ok3 = (clean and clean[0][1] == "ok"
               and fired and fired[0][1] == "drift"
               and missing and missing[0][1] == "unknown")
        if verbose:
            print(f"  canary manifest_drift       {cid} sha "
                  f"{true_sha[:12]}: true={clean[0][1]}, "
                  f"mutated={fired[0][1]}, absent={missing[0][1]}  "
                  f"[{'DETECTED' if ok3 else 'MISSED'}]")

    # ---- manual_scope must not contradict must_cite ----------------------
    # A REAL INSTANCE, AND IT WAS MINE: G-205 carried manual_scope "any" with
    # must_cite naming dabi_2025 alone, while progoti_0040 is the same bytes
    # at the same clause. Every answer citing the Progoti copy scored wrong.
    # The canary restores that exact shape on a copy -- scope "any", one
    # manual, mode all -- and asserts the check fires, then asserts the gold
    # as it now stands is clean.
    manifest3, why3 = load_manifest()
    if why3:
        if verbose:
            print(f"  canary scope_vs_cite        NOT BUILDABLE: {why3}")
    else:
        import copy as _copy2
        live3 = scope_cite_conflicts(gold, manifest3)
        broke = _copy2.deepcopy(gold)
        target = None
        for bq in broke.get("questions") or []:
            if bq.get("id") == "G-205":
                target = bq
                break
        if target is None:                 # fall back to any cited question
            for bq in broke.get("questions") or []:
                if bq.get("must_cite"):
                    target = bq
                    break
        if target is not None:
            target["manual_scope"] = "any"
            target["must_cite"] = [str(target["must_cite"][0])]
            target.pop("must_cite_mode", None)
        hit3 = scope_cite_conflicts(broke, manifest3)
        ok_s = bool(hit3) and not live3
        if verbose:
            which = target.get("id") if target else "?"
            print(f"  canary scope_vs_cite        {which} reverted to scope="
                  f"any/one-manual/all detected={[h[0] for h in hit3]}; "
                  f"live gold clean={not live3}"
                  f"  [{'DETECTED' if ok_s else 'MISSED'}]")
        ok = ok and ok_s

    # ---- must_cite manual ids must exist in the corpus -------------------
    # A REAL OBSERVED FAILURE, AND IT WAS MINE. Both gold files cited
    # "progoti_2025" where the corpus manual id is "progoti", for 30 entries
    # across both files. 12 of the 17 recall-scoreable questions therefore
    # required an unproducible citation, and a real two-model run scored
    # 1/17 each -- which read as a model failure until the ids were compared
    # against the manifest.
    #
    # The canary points one citation at a manual that does not exist and
    # asserts the check fires, then asserts it stays quiet on the gold file
    # as it now stands.
    manifest2, why2 = load_manifest()
    if why2:
        if verbose:
            print(f"  canary cite_manual_exists   NOT BUILDABLE: {why2}")
    else:
        live = unknown_cite_manuals(gold, manifest2)
        # a copy with one citation's manual id broken
        import copy as _copy
        broken = None
        for q in gold.get("questions") or []:
            if q.get("must_cite"):
                broken = _copy.deepcopy(gold)
                for bq in broken["questions"]:
                    if bq.get("id") == q.get("id"):
                        first = str(bq["must_cite"][0])
                        rest = first.split("\u00a7", 1)
                        bq["must_cite"][0] = (
                            "no_such_manual_9999"
                            + ("\u00a7" + rest[1] if len(rest) > 1 else ""))
                        break
                break
        hit = unknown_cite_manuals(broken, manifest2) if broken else []
        ok_c = bool(hit) and not live
        if verbose:
            print(f"  canary cite_manual_exists   broken id detected="
                  f"{[h[2] for h in hit]}; live gold clean={not live}"
                  f"  [{'DETECTED' if ok_c else 'MISSED'}]")
        ok = ok and ok_c

        ok &= ok3

    return ok


def gold_eval_loads(path, verbose=True):
    try:
        import gold_eval
        g = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        qs = {q["id"]: q for q in g["questions"]}
        gold_eval.score_one(next(iter(qs.values())),
                            {"answer": "", "citations": []})
        if verbose:
            print(f"  gold_eval compatibility     loads {len(qs)} questions "
                  f"and scores one  [PASS]")
        return True
    except Exception as exc:
        if verbose:
            print("  gold_eval compatibility     FAILED: "
                  f"{type(exc).__name__}: {exc}"[:110])
        return False


# -------------------------------------------------------------------- report

def report(rep, strict=False):
    todo_n = sum(len(v) for v in rep["todo_facts"].values())
    print(f"\nquestions ........... {rep['n_questions']}")
    print("answerable split .... "
          + " / ".join(f"{k}:{v}" for k, v in sorted(rep["answerable"].items())))
    print("\nfacts by kind ....... total / filled / TODO")
    for k in KINDS:
        tot = rep["kinds"][k]
        fil = rep["kinds_filled"][k]
        print(f"  {k:<8} {tot:>5} {fil:>9} {tot - fil:>7}")
    print(f"  {'ALL':<8} {sum(rep['kinds'].values()):>5} "
          f"{sum(rep['kinds_filled'].values()):>9} {todo_n:>7}")
    print(f"\nCLAIM FACTS: {rep['kinds'][ 'claim']} total, "
          f"{rep['kinds_filled']['claim']} filled.")
    print("  These need --judge llm: one API call per fact per eval run.")
    _cf, _ct = rep["kinds_filled"]["claim"], rep["kinds"]["claim"]
    print(f"  LLM-judge cost per run = {_cf} call(s)"
          + (f", rising to {_ct} once the claim TODOs are filled."
             if _ct > _cf else "."))
    if rep["claim_questions"]:
        print(f"  in questions: {', '.join(rep['claim_questions'])}")

    print("\nper type ............")
    for t, n in sorted(rep["types"].items(), key=lambda x: -x[1]):
        print(f"  {n:>3}  {t}")

    print(f"\nTODO facts .......... {todo_n} across "
          f"{len(rep['todo_facts'])} question(s)"
          + ("   <-- --strict: these fail" if strict
             else "   (reported, not a failure)"))
    for qid in sorted(rep["todo_facts"]):
        print(f"  {qid:<8} {len(rep['todo_facts'][qid])}  "
              f"{'; '.join(rep['todo_facts'][qid])[:60]}")
    if rep["todo_refs"]:
        print(f"TODO source_refs .... {len(rep['todo_refs'])} "
              f"(navigation not yet filled)")
        for qid, c in rep["todo_refs"][:8]:
            print(f"  {qid:<8} chunk_id={c!r}")

    fails = 0
    if rep["bad_kind"]:
        fails += len(rep["bad_kind"])
        print(f"\nFAIL unknown fact kind ({len(rep['bad_kind'])}) -- must be "
              f"one of {KINDS}")
        for qid, lit, k in rep["bad_kind"][:8]:
            print(f"  {qid:<8} kind={k!r} {lit[:44]!r}")

    if rep["bad_ref_clause"]:
        fails += len(rep["bad_ref_clause"])
        print(f"\nFAIL source_ref clause is unusable "
              f"({len(rep['bad_ref_clause'])})")
        print("     Clause is the reference in this corpus. `clause: null` "
              "is allowed only for a\n     chunk with a heading_path to "
              "navigate by -- front matter with no clause number.")
        for qid, cid, why in rep["bad_ref_clause"][:8]:
            print(f"  {qid:<8} {cid}: {why}")

    if rep["absent_but_present"]:
        fails += len(rep["absent_but_present"])
        print(f"\nFAIL absent fact IS present in the corpus "
              f"({len(rep['absent_but_present'])})")
        print("     The fact asserts absence, so a chunk containing it makes "
              "the fact FALSE.")
        for qid, lit, cid in rep["absent_but_present"][:10]:
            print(f"  {qid:<8} {lit[:52]!r}")
            print(f"           found in: {cid}")

    if rep["unresolved_ref"]:
        fails += len(rep["unresolved_ref"])
        print(f"\nFAIL literal fact whose ref cannot be resolved "
              f"({len(rep['unresolved_ref'])})")
        for qid, lit, why in rep["unresolved_ref"][:10]:
            print(f"  {qid:<8} {why}")
            print(f"           {lit[:62]!r}")

    if rep["unknown_chunk"]:
        fails += len(rep["unknown_chunk"])
        print(f"\nFAIL ref names a chunk_id that does not exist "
              f"({len(rep['unknown_chunk'])})")
        for qid, lit, cid in rep["unknown_chunk"][:10]:
            print(f"  {qid:<8} {cid!r} for {lit[:44]!r}")

    if rep["manifest_error"]:
        print(f"\n[warn] chunk manifest: {rep['manifest_error']}")
        print("Refs cannot be checked for drift without it. Not counted as a "
              "failure, because\na fresh clone has no corpus to compare against "
              "either.")
    else:
        print(f"\nmanifest ............ {rep['manifest_ok']} ref'd chunk(s) "
              f"match their recorded sha")

    if rep["manifest_drift"]:
        fails += len(rep["manifest_drift"])
        print(f"\nFAIL chunk changed since the gold fact was quoted from it "
              f"({len(rep['manifest_drift'])})")
        print("The verbatim check cannot see this: it runs against the CURRENT "
              "text.\nRe-read the clause and re-quote the fact, or re-run "
              "dedupe.py if the\nchange was intended.")
        for cid, detail in rep["manifest_drift"][:10]:
            print(f"  {cid:<22} {detail}")

    if rep["manifest_unknown"]:
        fails += len(rep["manifest_unknown"])
        print(f"\nFAIL ref names a chunk the manifest has never seen "
              f"({len(rep['manifest_unknown'])})")
        for cid, detail in rep["manifest_unknown"][:10]:
            print(f"  {cid:<22} {detail}")

    if rep["unknown_cite_manual"]:
        fails += len(rep["unknown_cite_manual"])
        print(f"\nFAIL must_cite names a manual the corpus does not have "
              f"({len(rep['unknown_cite_manual'])})")
        print("A citation naming a manual id no chunk carries can never be "
              "produced, so\nevery answer scores wrong on it however correct "
              "it is. This capped citation\naccuracy near 5/17 on a real run "
              "and read as a model failure.")
        for qid, cite, mid in rep["unknown_cite_manual"][:10]:
            print(f"  {qid:<8} {cite!r}  -- no manual {mid!r} in the manifest")

    if rep["scope_cite_conflict"]:
        fails += len(rep["scope_cite_conflict"])
        print(f"\nFAIL manual_scope contradicts must_cite "
              f"({len(rep['scope_cite_conflict'])})")
        print("The scope says which manual answers the question and must_cite "
              "says which manual\nmust be named. When they disagree a correct "
              "answer scores wrong, and no single\nfield is visibly at fault. "
              "Mark the citation any-of, or narrow the scope.")
        for qid, detail in rep["scope_cite_conflict"][:10]:
            print(f"  {qid:<8} {detail}")

    if rep["manifest_orphans"]:
        print(f"\n[warn] manifest has {len(rep['manifest_orphans'])} chunk(s) "
              f"the corpus no longer does")
        print("Not a failure on its own -- re-chunking drops ids. It IS a "
              "failure if a gold\nref points at one; that shows above as "
              "drift.")
        for cid in rep["manifest_orphans"][:10]:
            print(f"  {cid}")

    if rep["bad_verbatim"]:
        fails += len(rep["bad_verbatim"])
        print(f"\nFAIL literal not verbatim in its cited chunk "
              f"({len(rep['bad_verbatim'])})")
        for qid, lit, cid, near, elsewhere in rep["bad_verbatim"][:10]:
            print(f"  {qid:<8} {lit[:52]!r}")
            print(f"           cited: {cid}")
            print(f"           closest there: {(near or '-')[:58]!r}")
            print(f"           found instead in: "
                  f"{elsewhere or 'nowhere in the corpus'}")

    if rep["missing_render"]:
        fails += len(rep["missing_render"])
        print(f"\nFAIL render paths not on disk ({len(rep['missing_render'])})")
        for qid, lit, r in rep["missing_render"][:10]:
            print(f"  {qid:<8} {r}")

    if rep["unarmed_unanswerable"]:
        fails += len(rep["unarmed_unanswerable"])
        print(f"\nFAIL answerable:\"no\" with nothing to fail on "
              f"({len(rep['unarmed_unanswerable'])})")
        print("     Needs forbidden_numbers_from_clause or "
              "must_not_contain_literals, or the")
        print("     question cannot tell abstention from a lucky guess.")
        for qid in rep["unarmed_unanswerable"]:
            print(f"  {qid}")

    if rep["exempt_used"]:
        print(f"\nexemptions applied ({len(rep['exempt_used'])})")
        for qid, why in rep["exempt_used"]:
            print(f"  {qid}: {why}")

    if rep["normalised_only"]:
        print(f"\nADVISORY literal present only after normalisation "
              f"({len(rep['normalised_only'])})")
        print("  In the cited chunk, but not as a raw substring -- "
              "whitespace or a zero-width")
        print("  joiner differs. Anything comparing raw strings later will "
              "miss it.")
        for qid, lit, cid in rep["normalised_only"][:8]:
            print(f"  {qid:<8} {cid:<22} {lit[:40]!r}")

    return fails, todo_n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default="gold/bootstrap.yaml")
    ap.add_argument("--strict", action="store_true",
                    help="also fail on remaining TODO markers")
    ap.add_argument("--root", default=".")
    args = ap.parse_args()

    p = Path(args.gold)
    if not p.exists():
        print(f"FATAL: gold file not found: {args.gold}")
        return 2
    gold = yaml.safe_load(p.read_text(encoding="utf-8"))
    corpus, heads, missing = load_corpus()

    print("CANARY")
    if not canary_selftest(gold, corpus):
        print("FATAL: a canary did not fire. The checks are not reading what "
              "they claim to\n       read; nothing in this report can be "
              "trusted.")
        return 2
    gold_eval_loads(p)
    if missing:
        print(f"  [warn] corpus files missing, not searched: {missing}")
    print(f"  corpus .................... {len(corpus)} chunks")

    rep = validate(gold, corpus, heads, Path(args.root))
    fails, todo_n = report(rep, args.strict)

    print("\n" + "=" * 62)
    print(f"{fails} problem(s) in filled-in material."
          if fails else "filled-in material is consistent.")
    if todo_n:
        print(f"{todo_n} TODO fact(s) still to fill."
              + ("  --strict: counted as failures." if args.strict
                 else "  Not a failure without --strict."))
    print("=" * 62)
    return 1 if (fails or (args.strict and todo_n)) else 0


if __name__ == "__main__":
    sys.exit(main())
