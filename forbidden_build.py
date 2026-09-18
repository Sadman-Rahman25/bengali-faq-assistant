"""Build out/forbidden.json from the clauses the gold files name.

WHY THIS EXISTS. out/forbidden.json is an input to metric 4 -- the violation
rate, the one metric that is a report-stopper -- and until now nothing in the
repo produced it. `out/` is gitignored, so the file was neither tracked nor
reproducible: if it were deleted, metric 4 would have had to be rebuilt from
memory. It had also drifted from the corpus, keying `progoti_2025 §7.1` where
the manual id is `progoti`, which left two questions unarmed without anything
noticing.

HOW DRIFT IS PREVENTED NOW. Manual ids come from chunk_manifest.json, the
tracked manifest, and a clause naming a manual the manifest does not have is
a hard error rather than an empty entry. The clause list is not a constant
here either: it is read from the gold files' forbidden_numbers_from_clause, so
the armed clauses are exactly the clauses the gold asks to arm.

WHAT THE OUTPUT MEANS, since gold_eval reads only two parts of it:
  forbidden[clause]          bare value strings found in the clause
  forbidden["_units"][clause]  the clause's own (value, unit) pairs
Everything beginning with an underscore beyond that is documentation and is
ignored by the consumer -- kept because a figure list with no provenance is
not checkable by a human.
"""
import argparse
import json
import re
import sys
from collections import OrderedDict
from pathlib import Path

from dedupe import CORPORA, clause_of

PROCESSED = Path("data/processed")
MANIFEST = PROCESSED / "chunk_manifest.json"
OUT = Path("out/forbidden.json")
GOLD_DEFAULT = ["gold/bootstrap.yaml", "gold/bootstrap_livestock_grow.yaml"]

# Citing a clause is required behaviour, not a violation, so a token shaped
# like a clause number never arms. Digit ranges only -- no Bengali literal
# reaches a matching operation in this module.
CLAUSE_SHAPED = re.compile(r"^[0-9০-৯]+(\.[0-9০-৯]+)+$")

FILTER_NOTE = (
    "^[0-9০-৯]+(\\.[0-9০-৯]+)+$ -- citing a clause is required behaviour, "
    "not a violation")
UNITS_NOTE = (
    "the clause's own (value, unit) pairs. gold_eval.forbidden_violations "
    "matches an answer's UNUM_RX pairs against these, so '50' fires as "
    "৫০ হাজার and never as ৫ম. A value with no unit anywhere in the clause "
    "cannot be matched at all; those are listed per clause under "
    "dropped.no_unit_in_corpus.")
CONSUMER_NOTE = (
    "Clause keys map to flat lists so gold_eval.py can consume this directly. "
    "_provenance and _note are ignored by it.")
PAGE_NOTE = ("the .docx path records no page number; chunk_id is given "
             "instead")


def manifest_manual_ids(path=MANIFEST):
    """-> set of manual ids the corpus actually uses."""
    if not Path(path).exists():
        raise SystemExit(f"no manifest at {path} -- run dedupe.py first")
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return {r["manual_id"] for r in doc.get("chunks") or [] if r.get("manual_id")}


def load_corpus():
    """-> {manual_id: [chunk, ...]}, from the final files on disk."""
    out = {}
    for tag in CORPORA:
        src = PROCESSED / f"chunks_{tag}_final.jsonl"
        if not src.exists():
            continue
        rows = [json.loads(l) for l in src.open(encoding="utf-8") if l.strip()]
        for c in rows:
            c["_clause"] = clause_of(c)
        out[tag] = rows
    return out


def clauses_from_gold(paths):
    """Clause keys the gold files ask to arm. -> [str], sorted, de-duplicated

    Read from forbidden_numbers_from_clause rather than hardcoded, so the
    armed set cannot silently diverge from what the questions reference.
    """
    import yaml
    keys = []
    for p in paths:
        f = Path(p)
        if not f.exists():
            print(f"  [warn] gold file missing, skipped: {p}", file=sys.stderr)
            continue
        g = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for q in g.get("questions") or []:
            for c in q.get("forbidden_numbers_from_clause") or []:
                c = str(c).strip()
                if c and c not in keys:
                    keys.append(c)
    return sorted(keys)


def split_key(key):
    """'dabi_2025 §7.1' -> ('dabi_2025', '7.1')"""
    if "§" not in key:
        return key.strip(), ""
    manual, clause = key.split("§", 1)
    return manual.strip(), clause.strip()


def chunks_for(clause, rows):
    """-> (exact, subclauses). Exact carries the clause itself; subclauses
    carry a clause beneath it. Exact wins when non-empty, because a clause
    that exists as a chunk should not pull in its children's figures."""
    exact = [c for c in rows if c["_clause"] == clause]
    pref = clause + "."
    subs = [c for c in rows if c["_clause"].startswith(pref)]
    return exact, subs


def build_clause(key, corpus, known):
    """-> (values, unit_pairs, provenance, dropped) for one clause key."""
    manual, clause = split_key(key)
    if manual not in known:
        raise SystemExit(
            f"clause key {key!r} names manual {manual!r}, which the manifest "
            f"does not have. Known: {sorted(known)}. Fix the gold file -- "
            f"this is the drift that left two questions unarmed.")
    rows = corpus.get(manual) or []
    exact, subs = chunks_for(clause, rows)
    used = exact or subs
    resolved = ("exact clause" if exact else
                "subclauses -- no chunk carries this clause itself")

    values, pairs, clause_shaped = [], [], []
    by_number = OrderedDict()
    for c in used:
        for n in c.get("numbers") or []:
            n = str(n)
            if CLAUSE_SHAPED.match(n):
                if n not in clause_shaped:
                    clause_shaped.append(n)
                continue
            if n not in values:
                values.append(n)
            by_number.setdefault(n, {"chunks": []})
            if c["chunk_id"] not in by_number[n]["chunks"]:
                by_number[n]["chunks"].append(c["chunk_id"])
        for pr in c.get("unit_numbers") or []:
            pr = " ".join(str(x) for x in pr) if isinstance(pr, (list, tuple)) \
                else str(pr)
            if pr not in pairs:
                pairs.append(pr)

    def numkey(v):
        try:
            return (0, float(v))
        except ValueError:
            return (1, 0.0)

    values.sort(key=numkey)
    pairs.sort(key=lambda s: numkey(s.split(" ")[0]))
    paired_values = {p.split(" ")[0] for p in pairs}
    no_unit = [v for v in values if v not in paired_values]

    prov = OrderedDict([
        ("manual_id", manual),
        ("clause", clause),
        ("chunks_exact", [c["chunk_id"] for c in exact]),
        ("chunks_subclauses", [c["chunk_id"] for c in subs] if not exact
         else []),
        ("resolved_from", resolved),
        ("page_available", False),
        ("page_note", PAGE_NOTE),
        ("by_number", by_number),
    ])
    dropped = OrderedDict([("clause_shaped", clause_shaped),
                           ("no_unit_in_corpus", no_unit)])
    return values, pairs, prov, dropped


def build(gold_paths=None, out_path=OUT):
    gold_paths = gold_paths or GOLD_DEFAULT
    known = manifest_manual_ids()
    corpus = load_corpus()
    keys = clauses_from_gold(gold_paths)
    if not keys:
        raise SystemExit("no forbidden_numbers_from_clause in any gold file")

    doc = OrderedDict()
    units, prov, dropped = OrderedDict(), OrderedDict(), OrderedDict()
    for key in keys:
        vals, prs, pv, dr = build_clause(key, corpus, known)
        doc[key] = vals
        units[key] = prs
        prov[key] = pv
        dropped[key] = dr
    doc["_provenance"] = prov
    doc["_note"] = CONSUMER_NOTE
    doc["_units"] = units
    doc["_filter"] = OrderedDict([
        ("clause_shaped_dropped", FILTER_NOTE),
        ("magnitude_floor", None),
        ("_units_note", UNITS_NOTE),
        ("dropped", dropped),
        ("_built_by", "forbidden_build.py"),
        ("_clause_source", "gold forbidden_numbers_from_clause"),
    ])
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(
        json.dumps(doc, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    return doc, keys


def canary_selftest(verbose=True):
    """Convention 3. Two conditions, both able to fail.

    1. the clause-shaped filter drops a clause number and keeps an amount
    2. a clause key naming an unknown manual is a hard error -- this is the
       progoti_2025 drift, and an empty entry rather than an error is exactly
       how it went unnoticed
    """
    ok1 = (bool(CLAUSE_SHAPED.match("7.1"))
           and bool(CLAUSE_SHAPED.match("১.১.২"))
           and not CLAUSE_SHAPED.match("50")
           and not CLAUSE_SHAPED.match("200000"))
    try:
        build_clause("no_such_manual_9999 §7.1", {}, {"dabi_2025"})
        ok2 = False
    except SystemExit:
        ok2 = True
    if verbose:
        print(f"  canary clause_shaped_drop   '7.1' and '১.১.২' dropped, "
              f"'50' and '200000' kept: {ok1}  "
              f"[{'PASS' if ok1 else 'FAIL'}]")
        print(f"  canary unknown_manual_dies  unknown manual raises: {ok2}  "
              f"[{'DETECTED' if ok2 else 'MISSED'}]")
    return ok1 and ok2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", nargs="*", default=GOLD_DEFAULT)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    print("CANARY")
    if not canary_selftest():
        print("FATAL: forbidden_build failed its own canary.")
        return 2
    if args.selftest:
        return 0

    doc, keys = build(args.gold, Path(args.out))
    print(f"\n{len(keys)} clause(s) armed, from "
          f"{', '.join(Path(g).name for g in args.gold)}")
    for k in keys:
        print(f"  {k:<22} {len(doc[k]):>3} value(s), "
              f"{len(doc['_units'][k]):>3} unit pair(s)")
    print(f"\n-> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
