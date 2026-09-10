"""Option C: group cross-manual duplicates offline, collapse at query time.

THRESHOLD IS EXACTLY 1.000, AND THAT IS A DESIGN DECISION, NOT A KNOB.

dupscan.py measured the two manuals: at identical normalised body there is
zero semantic divergence, and every pair BELOW 1.000 differs by something an
answer must not erase --

    সিডিও / পিও                      the same field role, named differently
    (দাবি) / (দাবি/প্রগতি)           the scope a rule applies to
    এএম(দাবি) / এএম(দাবি)/আরএম(প্রগতি)  WHO MAY APPROVE

Collapsing at the intuitive 0.95 would merge 35 pairs that disagree about
approval authority. At 1.000 the attribution-bearing set stays intact by
construction, so no judgement call about "was this merge safe" is ever needed.

WHY CROWDING IS GUARANTEED, NOT MERELY LIKELY
Members of a 1.000 group have byte-identical normalised bodies, so any lexical
scorer assigns them the same score and returns them adjacently. 272 groups x
(members-1) = 274 top-k slots that are certain to be spent on a repeat
whenever one member ranks. Collapsing is not an optimisation, it is removing a
known defect.

WHAT THIS WRITES
Each chunk gains:
    dup_group      12-hex id, stable across runs (sha1 of normalised body)
    dup_members    every chunk_id in the group, sorted
    dup_sources    every doc tag in the group, sorted
    dup_primary    true for one deterministic member
    dup_citations  [{doc, clause, heading}] for every member -- REQUIRED,
                   because 15 groups hold the same rule under DIFFERENT clause
                   numbers (§1.1.2 in Dabi is §1.1.3 in Progoti), and the two
                   manuals even put different products at the same number
                   (§5.4 is Fire Safety in Progoti, Livestock in Dabi).

Singleton chunks get dup_group=None and dup_sources=[their own doc]: a single
source is the signal that an answer MUST name its manual.

Usage:
    python dedupe.py              # annotate both corpora + report
    python dedupe.py --check      # report only, write nothing
"""
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from bnnorm import canon

P = Path("data/processed")
CORPORA = ("progoti", "dabi_2025")
# Bodies in a group are byte-identical, so which member is "primary" cannot
# change any answer. Fixed only so runs are reproducible.
DOC_PRIORITY = {tag: i for i, tag in enumerate(CORPORA)}
CLAUSE = re.compile(r"\d+(?:\.\d+)+")


def norm_body(c):
    """The chunk's body with its heading line peeled off, canonicalised."""
    head = " > ".join(c["heading_path"])
    dt = c["display_text"]
    body = dt[len(head):].lstrip("\n") if head and dt.startswith(head) else dt
    return re.sub(r"\s+", " ", canon(unicodedata.normalize("NFC", body))).strip()


def clause_of(c):
    """Deepest numbered clause in the heading path, digit-folded."""
    nums = CLAUSE.findall(canon(" > ".join(c["heading_path"])))
    return nums[-1] if nums else ""


def load():
    out = {}
    for tag in CORPORA:
        cs = [json.loads(l) for l in
              (P / f"chunks_{tag}_final.jsonl").open(encoding="utf-8")]
        for c in cs:
            c["_doc"] = tag
            c["_body"] = norm_body(c)
        out[tag] = cs
    return out


def group(corpora):
    by_body = defaultdict(list)
    for tag in CORPORA:
        for c in corpora[tag]:
            if c["_body"]:
                by_body[c["_body"]].append(c)

    groups = {}
    for body, members in by_body.items():
        if len(members) < 2:
            continue
        gid = hashlib.sha1(body.encode("utf-8")).hexdigest()[:12]
        members.sort(key=lambda c: (DOC_PRIORITY[c["_doc"]], c["chunk_id"]))
        groups[gid] = members
    return groups


def annotate(corpora, groups):
    of_chunk = {}
    for gid, members in groups.items():
        ids = sorted(c["chunk_id"] for c in members)
        srcs = sorted({c["_doc"] for c in members})
        cites = [{"doc": c["_doc"], "clause": clause_of(c),
                  "heading": " > ".join(c["heading_path"])} for c in members]
        for i, c in enumerate(members):
            of_chunk[c["chunk_id"]] = dict(
                dup_group=gid, dup_members=ids, dup_sources=srcs,
                dup_primary=(i == 0), dup_citations=cites)
    for tag in CORPORA:
        for c in corpora[tag]:
            c.update(of_chunk.get(c["chunk_id"], dict(
                dup_group=None, dup_members=[c["chunk_id"]],
                dup_sources=[c["_doc"]], dup_primary=True,
                dup_citations=[{"doc": c["_doc"], "clause": clause_of(c),
                                "heading": " > ".join(c["heading_path"])}])))


def collapse(ranked, k=5):
    """Query-time collapse: keep the best-scoring chunk per dup_group.

    `ranked` is [(score, chunk), ...] already sorted best-first. Returns at
    most k entries. The kept chunk carries dup_sources/dup_citations, so the
    answer can say "both manuals" or name the single one it came from.
    """
    seen, out = set(), []
    for score, c in ranked:
        g = c.get("dup_group")
        if g is not None:
            if g in seen:
                continue
            seen.add(g)
        out.append((score, c))
        if len(out) >= k:
            break
    return out


def main(write):
    corpora = load()
    groups = group(corpora)
    annotate(corpora, groups)
    total = sum(len(cs) for cs in corpora.values())

    print("=" * 76)
    print("DUPLICATE GROUPING AT similarity == 1.000")
    print("=" * 76)
    dup_chunks = sum(len(m) for m in groups.values())
    removable = sum(len(m) - 1 for m in groups.values())
    print(f"  corpus                : {total} chunks "
          f"({', '.join(f'{t}={len(corpora[t])}' for t in CORPORA)})")
    print(f"  duplicate groups      : {len(groups)}")
    print(f"  chunks in groups      : {dup_chunks}")
    print(f"  guaranteed wasted top-k slots : {removable}")
    print(f"  effective index size  : {total - removable} "
          f"({100*(total-removable)//total}% of {total})")
    print(f"  group sizes           : "
          f"{dict(Counter(len(m) for m in groups.values()))}")
    cross = sum(1 for m in groups.values() if len({c['_doc'] for c in m}) > 1)
    print(f"  cross-manual groups   : {cross}  (within-manual: {len(groups)-cross})")

    # integrity: identical body must imply identical figures
    bad = [gid for gid, m in groups.items()
           if len({tuple(sorted(set(c["numbers"]))) for c in m}) > 1]
    print(f"\n  groups whose members disagree on any number : {len(bad)}")
    for gid in bad[:5]:
        m = groups[gid]
        print(f"    {gid}: {[c['chunk_id'] for c in m]}")
        for c in m:
            print(f"      {c['chunk_id']}: {sorted(set(c['numbers']))[:8]}")
    if not bad:
        print("    none -- identical text implies identical figures, as expected")

    multi = [gid for gid, m in groups.items()
             if len({clause_of(c) for c in m}) > 1]
    print(f"\n  groups spanning DIFFERENT clause numbers : {len(multi)}")
    print("  (a merged answer must render the clause of the manual asked about)")
    for gid in multi[:6]:
        m = groups[gid]
        print("    " + " | ".join(f"{c['_doc']}:{clause_of(c) or '-'}" for c in m))

    singles = [c for tag in CORPORA for c in corpora[tag] if c["dup_group"] is None]
    print(f"\n  single-source chunks  : {len(singles)}  "
          f"(these REQUIRE naming the manual)")
    print(f"    unit_numbers in them: "
          f"{sum(len(c['unit_numbers']) for c in singles)}")

    if not write:
        print("\n  --check: nothing written")
        return

    for tag in CORPORA:
        dst = P / f"chunks_{tag}_dedup.jsonl"
        with dst.open("w", encoding="utf-8") as f:
            for c in corpora[tag]:
                f.write(json.dumps({k: v for k, v in c.items()
                                    if not k.startswith("_")},
                                   ensure_ascii=False) + "\n")
        print(f"\n  -> {dst}  ({len(corpora[tag])} chunks)")


if __name__ == "__main__":
    main("--check" not in sys.argv)
