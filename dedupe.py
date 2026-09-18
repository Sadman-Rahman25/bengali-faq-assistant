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
CORPORA = ("progoti", "dabi_2025", "livestock_grow_2026")
# livestock_grow_2026 added 2026-09-17. Dabi 5.4 and the livestock manual
# cover the same insurance, so cross-manual duplicates are expected there.
# Listed LAST so DOC_PRIORITY, and therefore dup_primary on every existing
# group, is unchanged.
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


def _manual_of(c):
    return c.get("manual_id") or c.get("doc") or c.get("_doc")


def collapse(ranked, k=5, prefer_manual=None):
    """Query-time collapse: one chunk per dup_group, best-scoring position kept.

    `ranked` is [(score, chunk), ...] already sorted best-first. Returns at
    most k entries. The kept chunk carries dup_sources/dup_citations, so the
    answer can say "both manuals" or name the single one it came from.

    WHY THIS IS NOT AN OPTIMISATION. Members of a 1.000 group have
    byte-identical normalised bodies, so any scorer gives them the same score
    and returns them adjacently. Measured on the dense side: progoti_0153 and
    dabi_2025_0137 both score 0.3459, progoti_0211 and dabi_2025_0184 both
    0.3697 -- two of every three top slots carried the same content twice, so
    an apparent top-5 was 2-3 distinct chunks.

    prefer_manual: when a group has several members in `ranked`, keep the one
    from THIS manual instead of the best-scoring one. Safe precisely because
    the bodies are identical at 1.000 -- the choice cannot change what the
    answer says, only which manual it is cited from, which is what a
    manual-scoped question needs. The emitted position is still the group's
    best-scoring one, so ordering is unaffected.
    """
    # Which member of each group to emit. Default: the first (best-scoring)
    # one. With prefer_manual: the first member from that manual, if any.
    chosen = {}
    for pos, (_score, c) in enumerate(ranked):
        g = c.get("dup_group")
        if g is None:
            continue
        if g not in chosen:
            chosen[g] = pos
        elif prefer_manual and _manual_of(c) == prefer_manual                 and _manual_of(ranked[chosen[g]][1]) != prefer_manual:
            chosen[g] = pos

    seen, out = set(), []
    for _pos, (score, c) in enumerate(ranked):
        g = c.get("dup_group")
        if g is None:
            out.append((score, c))
        else:
            if g in seen:
                continue
            seen.add(g)
            # the group's best score, the chosen member's identity
            out.append((score, ranked[chosen[g]][1]))
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

    annotate_final(corpora)
    write_manifest()


# THE CHUNK MANIFEST.
#
# gold/ is in git; data/processed/ is not, and data/raw/ is not either -- the
# manuals are BRAC internal documents and stay out. So every
# source_refs.chunk_id in the gold file names a chunk that cannot be checked
# from a clone: the id might not exist, or the chunk's text might have changed
# since the fact was quoted out of it. The verbatim check still passes in that
# second case, because it runs against the CURRENT text. A stale fact and a
# correct one are indistinguishable.
#
# The manifest closes that without shipping the manuals: chunk_id, a sha256 of
# display_text, and the navigation needed to find the clause by eye. No body,
# no display_text, nothing from which the source could be reconstructed.
#
# WRITTEN HERE, in the run that last writes the final chunk files, for the
# dup_* reason: an artefact computed by one step and persisted by a step
# someone has to remember gets lost. resplit.py -> dedupe.py is the order, and
# dedupe is the end of it. If someone re-runs resplit and forgets dedupe, the
# final files change and the manifest does not -- which validate_gold then
# reports as drift rather than passing silently. That failure is the point.
MANIFEST = P / "chunk_manifest.json"


def manifest_sha(display_text):
    """sha256 of display_text AS STORED, utf-8, nothing normalised."""
    return hashlib.sha256((display_text or "").encode("utf-8")).hexdigest()


def write_manifest():
    """Write data/processed/chunk_manifest.json from the FINAL files on disk.

    Reads them back rather than using the in-memory corpora, so the recorded
    sha is of the bytes that were actually written. n_chars is len of the same
    display_text the sha covers -- not the chunk's own n_chars field, which
    counts the body.
    """
    # THE PERSONAL-DATA EXCLUSION, carried into the manifest because the
    # manifest is the only tracked artefact under data/processed -- .gitignore
    # excludes that directory and re-includes this one file. The chunk files
    # themselves are not tracked, so the excluded_from_retrieval flag written
    # onto a chunk row does not survive a rebuild; recorded here it does, and
    # it shows up in a diff when it changes.
    #
    # redact.py STAYS THE SINGLE OWNER and this is a derived copy, re-read
    # from it on every manifest write. A second tracked list of ids would be
    # a second source of truth, and every duplicate in this repo became a
    # divergence.
    import redact
    rows = []
    for tag in CORPORA:
        src = P / f"chunks_{tag}_final.jsonl"
        for line in src.open(encoding="utf-8"):
            if not line.strip():
                continue
            c = json.loads(line)
            dt = c.get("display_text") or ""
            rows.append({
                "chunk_id": c["chunk_id"],
                "sha256": manifest_sha(dt),
                "manual_id": tag,
                "clause": clause_of(c),
                "heading_path": list(c.get("heading_path") or []),
                "n_chars": len(dt),
                # THE TWIN RELATION, and it is not the sha above. The sha is
                # of display_text, which carries the heading -- so the one
                # passage printed at 1.1.2 in Dabi and 1.1.3 in Progoti has
                # two different shas and one dup_group. dup_group is computed
                # on index_text and is the relation collapse() acts on, so it
                # is the one anything reasoning about twins must use.
                "dup_group": c.get("dup_group"),
            })
            why = redact.is_excluded(c["chunk_id"])
            if why:
                rows[-1]["excluded_from_retrieval"] = True
                rows[-1]["exclusion_reason"] = why
    rows.sort(key=lambda r: r["chunk_id"])
    MANIFEST.write_text(
        json.dumps({"version": 1, "n_chunks": len(rows), "chunks": rows},
                   ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    n_excl = sum(1 for r in rows if r.get("excluded_from_retrieval"))
    print(f"  -> {MANIFEST}  ({len(rows)} chunks, {n_excl} withheld from "
          f"retrieval)")
    return len(rows)


def load_manifest(path=MANIFEST):
    """-> {chunk_id: row} or {} when the manifest is absent."""
    p = Path(path)
    if not p.exists():
        return {}
    doc = json.loads(p.read_text(encoding="utf-8"))
    return {r["chunk_id"]: r for r in doc.get("chunks") or []}


# ANNOTATION ONTO THE FINAL CORPUS.
#
# dup_group/dup_members are load-bearing for gold resolution:
# embed.locate_gold_chunks expands a source_refs chunk_id to the duplicates
# DECLARED here, and refuses to infer them by comparing text. So they have to
# live on the chunk files that CORPUS_FILES actually loads.
#
# They used to exist only in chunks_<tag>_dedup.jsonl, which nothing loads,
# and were silently absent from chunks_<tag>_final.jsonl once resplit.py
# regenerated it -- the same failure shape as nest_depth being computed in
# ex.py and dropped by add(). Writing them in the run that COMPUTES them is
# what stops that recurring: resplit.py -> dedupe.py, with no separate step
# left for someone to remember.
#
# Only dup_group and dup_members go onto the final chunk. The other three
# (dup_sources, dup_primary, dup_citations) stay in the _dedup file: they are
# for reporting and citation rendering, not for resolving a ref.
#
# All three corpora are in CORPORA as of 2026-09-17, so every final file
# gets the fields; a chunk with no twin carries dup_group=None.
FINAL_FIELDS = ('dup_group', 'dup_members')


def annotate_final(corpora):
    """Write dup_group/dup_members onto chunks_<tag>_final.jsonl, in place.

    Rewrites through a temporary file so an interrupted run cannot leave a
    half-written corpus behind. Re-running is idempotent.
    """
    for tag in CORPORA:
        dst = P / f"chunks_{tag}_final.jsonl"
        ann = {c["chunk_id"]: c for c in corpora[tag]}
        rows, n_grouped = [], 0
        for line in dst.open(encoding="utf-8"):
            if not line.strip():
                continue
            c = json.loads(line)
            src = ann.get(c["chunk_id"])
            for k in FINAL_FIELDS:
                c[k] = src.get(k) if src is not None else c.get(k)
            if c.get("dup_group"):
                n_grouped += 1
            rows.append(c)
        tmp = dst.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for c in rows:
                print(json.dumps({k: v for k, v in c.items()
                                  if not k.startswith("_")},
                                 ensure_ascii=False), file=f)
        tmp.replace(dst)
        print(f"  -> {dst}  ({len(rows)} chunks, "
              f"{n_grouped} with dup_group)")

if __name__ == "__main__":
    main("--check" not in sys.argv)
