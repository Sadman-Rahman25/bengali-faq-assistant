"""retrieval_eval.py -- STEP 10. Does retrieval put the right chunk in reach?

recall@1/3/5/10: for each gold question, is a chunk containing one of its
gold facts in the top k?

FOUR SPLITS, NEVER AVERAGED, NO HEADLINE NUMBER
-----------------------------------------------
  by provenance   field_sourced vs chunk_derived. chunk_derived questions
                  were written while looking at a chunk, copying its
                  spelling, so they share assumptions with the index and
                  measure agreement, not correctness. A merged figure hides
                  exactly the gap that matters: the digit-fold classifier
                  once scored 7/7 against self-written sentences using বীমা
                  where the corpus says বিমা.
  by manual_id    a manual can be dragging retrieval down while the total
                  looks fine. livestock_grow_2026 is 36 chunks against 831
                  from the docx path; it cannot move a pooled average.

There is deliberately no single number printed anywhere in this file.

UNANSWERABLE QUESTIONS SCORE INVERSELY
For answerable: "no", retrieving confident-looking near-miss content is the
FAILURE mode, not the success mode. G-133 asks what ceiling a সিডিও may
approve; no such ceiling exists, and the plausible wrong answer is sitting in
another manual's §7.1 প্রাধিকারসূচি formatted exactly like a right answer.
Retrieving that clause is how a generator is talked into inventing a figure.
So for these, the score is the share of questions where the near-miss chunks
were NOT retrieved -- "quiet@k" -- and a high recall@k is bad news.

Near-miss chunks for an unanswerable question are identified from the
question's own forbidden_numbers_from_clause field: the clauses the gold set
already names as the source of the plausible wrong answer. Nothing is
invented here.

Usage:
  python retrieval_eval.py                    # needs out/index
  python retrieval_eval.py --dry-run          # locatability only, no index
"""

import argparse
import json
import sys
from collections import defaultdict
from math import comb
from pathlib import Path

import embed
from embed import (gold_scoreability, load_corpus, load_questions,
                   locate_gold_chunks, manual_of)

KS = (1, 3, 5, 10)

# ---------------------------------------------------------- random baseline
# A recall figure without its baseline is not a result. Measured case: G-108
# scored 100% recall@3 while its text-matched gold set covered 396 of 867
# chunks (46%) -- a retriever drawing 3 chunks at random scores 84% on it.
# The number was true and meant almost nothing.
#
# baseline_at(G, N, k) is the probability that k chunks drawn WITHOUT
# replacement from a corpus of N include at least one of the G gold chunks:
#
#     1 - C(N-G, k) / C(N, k)
#
# At k=1 that reduces to G/N. Read the pair, never the recall alone: recall
# far above baseline is a result, recall near it is a coin toss with extra
# steps, and recall BELOW it means the retriever is doing worse than chance.
def baseline_at(n_gold, n_corpus, k):
    if not n_corpus or not n_gold:
        return 0.0
    if n_gold >= n_corpus or k >= n_corpus:
        return 1.0
    return 1.0 - comb(n_corpus - n_gold, k) / comb(n_corpus, k)


def variant_language(q):
    """bn | banglish -- by script, since the gold files carry no language tag.

    Any Bengali codepoint makes it bn; an all-Latin variant is the romanised
    form. Deliberately crude: it only has to separate the two kinds of string
    these files actually contain.
    """
    return "bn" if any("ঀ" <= ch <= "৿" for ch in q) else "banglish"


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0

GOLD_DEFAULT = ["gold/bootstrap.yaml", "gold/livestock_additions.yaml",
                "gold/bootstrap_livestock_grow.yaml"]


def norm_answerable(v):
    """YAML 1.1 parses bare `no` as False and `yes` as True.

    Unhandled, every answerable:no question drops out of the inverse bucket
    -- the one score that cannot be recovered from an aggregate.
    """
    if v is True:
        return "yes"
    if v is False:
        return "no"
    return str(v).strip().lower()


def question_manuals(q, corpus_manuals):
    """Which manual_ids a question is scoped to. `any`/`ambiguous` means all.

    manual_scope in the gold files is a short name (`livestock_grow`,
    `dabi`), while chunks carry `livestock_grow_2026`, `dabi_2025`,
    `progoti`. Matched by prefix, both directions, so neither file has to
    know the other's spelling.
    """
    scope = q.get("manual_scope")
    if scope in (None, "any", "ambiguous", "both"):
        return set(corpus_manuals)
    names = scope if isinstance(scope, list) else [scope]
    out = set()
    for n in names:
        n = str(n)
        for m in corpus_manuals:
            if m.startswith(n) or n.startswith(m):
                out.add(m)
    return out or set(corpus_manuals)


def near_miss_chunks(q, corpus):
    """Chunks that make an unanswerable question dangerous.

    Taken from forbidden_numbers_from_clause -- the clauses the gold file
    itself names as holding the plausible wrong answer. A chunk counts as a
    near miss when its heading path carries that clause number in that
    manual.
    """
    from bn_shared import canon, fold_digits
    out = set()
    for spec in q.get("forbidden_numbers_from_clause") or []:
        parts = str(spec).replace("§", " ").split()
        if not parts:
            continue
        manual, clause = parts[0], (parts[1] if len(parts) > 1 else "")
        if not clause:
            continue
        for c in corpus:
            if not manual_of(c).startswith(manual.split("_")[0]):
                continue
            # PER SEGMENT, not the joined path. The clause number is its own
            # heading level: dabi_2025 §7.1 is
            #   ['সপ্তম অধ্যায় : টেবিল অব অথরিটি', '৭.১ প্রাধিকারসূচি:']
            # so testing the joined string tested the CHAPTER TITLE and
            # matched nothing. Measured: 21 such chunks exist and 0 were
            # found, which made quiet@k vacuous for the only unanswerable
            # question that has a named near-miss clause -- the inverse
            # metric silently reported a perfect 100%.
            for seg in c.get("heading_path") or []:
                if fold_digits(canon(seg)).strip().startswith(clause):
                    out.add(c["chunk_id"])
                    break
    return out


def evaluate(ix, corpus, questions, ks=KS):
    """-> rows, one per scored question."""
    located = locate_gold_chunks(questions, corpus)
    scoreability = gold_scoreability(questions, corpus)
    manuals = {manual_of(c) for c in corpus}
    rows = []
    for q in questions:
        ans = norm_answerable(q.get("answerable", "yes"))
        gold = set().union(*[v for v in located[q["id"]].values() if v]) \
            if located[q["id"]] else set()
        unlocatable = [f for f, s in located[q["id"]].items() if not s]
        near = near_miss_chunks(q, corpus) if ans == "no" else set()

        variants = [q["question_bn"]] + list(q.get("question_variants") or [])
        per_variant = []
        for v in variants:
            hits = ix.search(v, k=max(ks))
            ids = [h["chunk_id"] for h in hits]
            per_variant.append({
                "query": v,
                "language": variant_language(v),
                "recall": {k: bool(gold & set(ids[:k])) for k in ks},
                "near_miss": {k: bool(near & set(ids[:k])) for k in ks},
                "top_manual": hits[0]["manual_id"] if hits else None,
            })
        rows.append({
            "id": q["id"],
            "provenance": q.get("provenance", "unknown"),
            "answerable": ans,
            "manuals": sorted(question_manuals(q, manuals)),
            "gold_chunks": len(gold),
            "baseline": {k: baseline_at(len(gold), len(corpus), k)
                         for k in ks},
            "unlocatable_facts": unlocatable,
            # "scoreable" | "judge_only" | "unscoreable". judge_only means the
            # question carries only claim facts: an LLM judge can score it, a
            # recall number cannot, because there is no chunk to retrieve.
            "scoreability": scoreability[q["id"]]["status"],
            "near_miss_chunks": len(near),
            "variants": per_variant,
        })
    return rows


def _block(label, rows, ks=KS):
    if not rows:
        return
    # Primary variant only for the headline rows; variants reported separately.
    ansable = [r for r in rows if r["answerable"] != "no" and r["gold_chunks"]]
    unans = [r for r in rows if r["answerable"] == "no"]
    print(f"\n[{label}]  n={len(rows)}  "
          f"(scoreable {len(ansable)}, unanswerable {len(unans)})")
    if ansable:
        line = "  recall@k ....."
        base = "  baseline ....."
        edge = "  above chance .."
        for k in ks:
            hit = sum(1 for r in ansable if r["variants"][0]["recall"][k])
            got = hit / len(ansable)
            b = mean(r["baseline"][k] for r in ansable)
            line += f"  @{k}:{hit}/{len(ansable)}={got:.0%}"
            base += f"  @{k}:{b:.0%}"
            edge += f"  @{k}:{got - b:+.0%}"
        print(line)
        print(base)
        print(edge)
        print("                 (baseline = mean chance of k random draws "
              "hitting the gold set)")
    else:
        print("  recall@k .....  no scoreable questions in this split")
    if unans:
        line = "  quiet@k ......"
        for k in ks:
            q_ = sum(1 for r in unans if not r["variants"][0]["near_miss"][k])
            line += f"  @{k}:{q_}/{len(unans)}={q_/len(unans):.0%}"
        print(line)
        print("                 (share NOT retrieving the named near-miss "
              "clause; higher is better)")
        blind = [r for r in unans if r["near_miss_chunks"] == 0]
        if blind:
            print(f"                 {len(blind)} of {len(unans)} have no "
                  f"near-miss chunk located, so quiet@k is vacuous for them: "
                  f"{[r['id'] for r in blind]}")


def _per_question_baseline(rows, ks):
    """Per question: gold-set size, chance, actual. The pair, side by side."""
    ansable = [r for r in rows if r["answerable"] != "no" and r["gold_chunks"]]
    if not ansable:
        return
    print()
    print("-" * 66)
    print("PER QUESTION: recall vs random baseline")
    print("A gold set that covers much of the corpus makes recall easy. Read")
    print("the gap, not the recall. Sorted by gold-set size, worst signal first.")
    print("-" * 66)
    head = f"  {'id':<8}{'gold':>6}{'%corp':>7}"
    for k in ks:
        head += f"{'@' + str(k):>10}"
    print(head + "   (actual/chance)")
    for r in sorted(ansable, key=lambda x: -x["gold_chunks"]):
        line = f"  {r['id']:<8}{r['gold_chunks']:>6}"
        line += f"{r['baseline'][1]:>6.0%} "
        for k in ks:
            got = "Y" if r["variants"][0]["recall"][k] else "n"
            line += f"{got + '/' + format(r['baseline'][k], '.0%'):>10}"
        print(line)


def _per_language_baseline(rows, ks):
    """Mean baseline and recall per variant language. Never averaged across."""
    by_lang = defaultdict(list)
    for r in rows:
        if r["answerable"] == "no" or not r["gold_chunks"]:
            continue
        for v in r["variants"]:
            by_lang[v.get("language", "bn")].append((r, v))
    if not by_lang:
        return
    print()
    print("-" * 66)
    print("PER LANGUAGE: recall vs random baseline (all variants, not just #1)")
    print("-" * 66)
    for lang in sorted(by_lang):
        pairs = by_lang[lang]
        print(f"  [{lang}]  n={len(pairs)}")
        line, base, edge = "    recall .....", "    baseline ...", "    above ......"
        for k in ks:
            got = mean(1.0 if v["recall"][k] else 0.0 for _r, v in pairs)
            b = mean(r["baseline"][k] for r, _v in pairs)
            line += f"  @{k}:{got:.0%}"
            base += f"  @{k}:{b:.0%}"
            edge += f"  @{k}:{got - b:+.0%}"
        print(line); print(base); print(edge)


def report(rows, ks=KS):
    print("\n" + "=" * 66)
    print("REPORTED BY PROVENANCE AND BY MANUAL. NEVER AVERAGED.")
    print("There is no single headline number in this report, by design.")
    print("=" * 66)

    by_prov = defaultdict(list)
    for r in rows:
        by_prov[r["provenance"]].append(r)
    for name in ("field_sourced", "chunk_derived", "unknown"):
        _block(f"provenance: {name}", by_prov.get(name, []), ks)

    by_man = defaultdict(list)
    for r in rows:
        for m in r["manuals"]:
            by_man[m].append(r)
    for m in sorted(by_man):
        _block(f"manual: {m}", by_man[m], ks)

    _per_question_baseline(rows, ks)
    _per_language_baseline(rows, ks)

    unloc = [(r["id"], f) for r in rows for f in r["unlocatable_facts"]]
    if unloc:
        print(f"\nUNLOCATABLE GOLD FACTS ({len(unloc)}) -- not scored, and not "
              f"counted as misses.")
        print("A fact that matches no chunk is a gold-file problem; calling "
              "it a retrieval\nmiss would blame the index for it.")
        for i, f in unloc[:15]:
            print(f"  {i:<8} {f[:66]!r}")

    multi = [r for r in rows if len(r["variants"]) > 1]
    if multi:
        print("\nQUESTION VARIANTS (romanised / reworded)")
        for r in multi:
            for v in r["variants"]:
                got = "".join(f" @{k}:{'Y' if v['recall'][k] else 'n'}"
                              for k in ks)
                print(f"  {r['id']:<8}{got}   {v['query'][:52]!r}")

    # A judge-only question has no gold chunk BY DESIGN -- all its facts are
    # claims. Warning about it would train the reader to ignore this warning,
    # which is the one that catches a genuinely broken ref.
    judge = [r for r in rows if r.get("scoreability") == "judge_only"]
    if judge:
        print(f"\n[note] {len(judge)} question(s) are JUDGE-ONLY (claim facts "
              f"only): {[r['id'] for r in judge]}")
        print("Excluded from every recall figure above -- there is no chunk to "
              "retrieve.\nScore them with --judge llm; they are not retrieval "
              "failures.")

    empty = [r for r in rows if not r["gold_chunks"]
             and r["answerable"] != "no"
             and r.get("scoreability") != "judge_only"]
    if empty:
        print(f"\n[warn] {len(empty)} answerable question(s) have NO gold "
              f"chunk located at all: {[r['id'] for r in empty]}")
    return 0


# ------------------------------------------------------------------- canary

def canary_selftest(corpus=None, questions=None, verbose=True):
    """Convention 3.

    1. Inverse scoring must actually invert. A synthetic ranking that returns
       a near-miss chunk must score quiet@k = 0, and one that avoids it must
       score 1. Without this the unanswerable bucket silently reads as
       recall and a dangerous retrieval looks like a good one.
    2. Fact locatability is measured, not assumed -- reported below.

    3. Near-miss location. BACKFILLED FROM A REAL FAILURE in the first run,
       15 Sep 2026: near_miss_chunks() tested the JOINED heading path with
       startswith, so `dabi_2025 §7.1` was compared against
       "সপ্তম অধ্যায় : টেবিল অব অথরিটি > ৭.১ ..." and matched nothing. 21
       §7.1 chunks existed; 0 were found. quiet@k then reported a perfect
       100% for the one unanswerable question that has a named near-miss
       clause -- the inverse metric, reporting success because it was
       measuring an empty set. This asserts the clause is found.
    """
    ok = True
    ks = (1, 3)
    fake_near = {"X1"}
    bad = [{"id": "T-bad", "provenance": "field_sourced", "answerable": "no",
            "manuals": ["m"], "gold_chunks": 0, "unlocatable_facts": [],
            "scoreability": "scoreable",
            "near_miss_chunks": 1,
            "variants": [{"query": "q", "recall": {k: False for k in ks},
                          "near_miss": {k: True for k in ks},
                          "top_manual": "m"}]}]
    good = [dict(bad[0], id="T-good",
                 variants=[{"query": "q", "recall": {k: False for k in ks},
                            "near_miss": {k: False for k in ks},
                            "top_manual": "m"}])]
    q_bad = sum(1 for r in bad if not r["variants"][0]["near_miss"][1])
    q_good = sum(1 for r in good if not r["variants"][0]["near_miss"][1])
    ok1 = q_bad == 0 and q_good == 1
    if verbose:
        print(f"  canary inverse_scoring      near-miss retrieved quiet@1="
              f"{q_bad} avoided quiet@1={q_good}  "
              f"[{'PASS' if ok1 else 'FAIL'}]")
    ok &= ok1

    if corpus is not None and questions is not None:
        # 3. the real one: a named near-miss clause must actually resolve.
        named = [q for q in questions
                 if norm_answerable(q.get("answerable")) == "no"
                 and (q.get("forbidden_numbers_from_clause") or [])]
        found = {q["id"]: len(near_miss_chunks(q, corpus)) for q in named}
        ok3 = bool(named) and all(v > 0 for v in found.values())
        shown = found if found else "no question names a near-miss clause"
        if verbose:
            print(f"  canary near_miss_resolves   {shown}  "
                  f"[{'PASS' if ok3 else 'FAIL'}]")
            if not named:
                print("        -> only G-133 carries "
                      "forbidden_numbers_from_clause; G-131 and G-132 carry")
                print("           none, so quiet@k is vacuous for them by "
                      "construction, not by bug.")
        ok &= ok3 or not named

        located = locate_gold_chunks(questions, corpus)
        tot = sum(len(v) for v in located.values())
        got = sum(1 for v in located.values() for s in v.values() if s)
        if verbose:
            print(f"  fact locatability           {got}/{tot} gold facts "
                  f"match at least one chunk")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", nargs="*", default=GOLD_DEFAULT)
    ap.add_argument("--dry-run", action="store_true",
                    help="locatability and splits only; no index needed")
    ap.add_argument("--out", default="out/retrieval_eval.json")
    args = ap.parse_args()

    corpus = load_corpus()
    questions = load_questions(args.gold)
    print("CANARY")
    if not canary_selftest(corpus, questions):
        print("FATAL: retrieval_eval failed its own canary.")
        return 2

    print(f"\ncorpus {len(corpus)} chunks, gold {len(questions)} questions")
    if not questions:
        print("no gold questions; nothing to score")
        return 1

    if args.dry_run:
        located = locate_gold_chunks(questions, corpus)
        manuals = {manual_of(c) for c in corpus}
        print("\nDRY RUN -- gold fact locatability, no retrieval\n")
        print(f"  {'id':<9}{'prov':<15}{'ans':<9}{'facts':>6}{'located':>9}"
              f"   manuals")
        for q in questions:
            per = located[q["id"]]
            n = len(per)
            got = sum(1 for s in per.values() if s)
            print(f"  {q['id']:<9}{q.get('provenance','?'):<15}"
                  f"{norm_answerable(q.get('answerable','yes')):<9}{n:>6}"
                  f"{got:>9}   "
                  f"{','.join(sorted(question_manuals(q, manuals)))}")
        return 0

    if not (Path("out/index") / "model.json").exists():
        print("\nno index at out/index. Run:  python embed.py --build MODEL")
        return 1
    import retrieve
    ix = retrieve.Index()
    rows = evaluate(ix, corpus, questions)
    code = report(rows)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(f"\nreport -> {args.out}")
    return code


if __name__ == "__main__":
    sys.exit(main())
