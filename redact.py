"""Chunks withheld from retrieval because they carry personal data.

WHY THIS IS NOT A DELETION. The chunk row stays in the corpus, keeps its id,
page and source, and still counts in every corpus statistic. Only retrieval
refuses it. A deleted chunk would silently change chunk counts, duplicate
groups and gold-ref resolution, and nothing downstream would say why.

WHY IT IS AN EXCLUSION AND NOT A FIELD-LEVEL REDACTION. In the one affected
chunk the institutional hotline and two named personal mobiles sit in the SAME
SENTENCE, and the numbers also appear ASCII-folded in the `numbers` field --
so a token-level redaction has to hit three fields in two digit systems and
leaks if it misses one. This repo's whole history is asymmetries of exactly
that shape. An id-level exclusion is checkable in one line; a token-level
redaction is checkable only by trusting the redactor.

WHAT IT COSTS, measured rather than assumed. Almost nothing, because the same
material is duplicated in the Dabi manual: the institutional hotline 16457 and
the insurer's institutional number are both in dabi_2025_0269, under the same
claim-settlement heading, and so are the procedure steps. An officer filing a
claim still has all of it. (An earlier note here said the hotline occurred
nowhere else; that was wrong -- it had been searched for in the livestock file
only.)

FORMAT IS NOT THE TEST, PRESENCE OF A NAME IS. Five other chunks --
dabi_2025_0097, dabi_2025_0210, dabi_2025_0269, progoti_0109, progoti_0238 --
carry mobile-format numbers with NO personal name attached: divisional claim
hotlines that happen not to be short codes. Measured across all 867 chunks,
livestock_grow_2026_0032 is the ONLY chunk with a number beside a person's
name. A gate keyed on number FORMAT therefore blocks legitimate institutional
contacts, and an early version of this module did exactly that -- it raised on
a hotline query and broke retrieval. The exclusion is keyed on chunk id.

NO PERSONAL NUMBER IS STORED IN THIS FILE. The chunk is named by id and the
numbers are found by PATTERN, so the thing being withheld is not itself
committed to the repo.
"""
import json
import re
import sys
from pathlib import Path

PROCESSED = Path("data/processed")

# chunk_id -> why. Keyed by id, because an id is exact and a text match is not.
EXCLUDED_CHUNKS = {
    "livestock_grow_2026_0032": (
        "page 8: two staff first names beside personal mobile numbers, "
        "in the same sentence as the institutional hotline. Manual text on a "
        "free tier that trains on inputs is an accepted demo trade-off; "
        "named individuals' personal numbers are not."),
}

# A Bangladesh mobile: 01xxxxxxxxx, optionally +880-prefixed, in either digit
# system. Five-digit institutional short codes do NOT match -- that is the
# distinction the exclusion is drawn on, and the canary checks it holds.
MOBILE_RX = re.compile(r"(?:\+?\s*(?:৮৮০|880))?\s*(?:০১|01)[০-৯0-9]{8,9}")


def is_excluded(chunk_id):
    """-> reason string, or None. The single source of truth."""
    return EXCLUDED_CHUNKS.get(chunk_id)


def contains_personal_number(text):
    """-> bool. Used by the canary and by assert_clean, never to DECIDE an
    exclusion: a pattern that decides exclusions would silently start or stop
    excluding things when the corpus is rebuilt."""
    return bool(MOBILE_RX.search(text or ""))


def scrub(rows):
    """Drop excluded chunks from a list of chunk dicts. -> (kept, dropped)"""
    kept, dropped = [], []
    for r in rows:
        (dropped if is_excluded(r.get("chunk_id")) else kept).append(r)
    return kept, dropped


def personal_spans():
    """The exact number strings inside the excluded chunks. -> [str]

    READ FROM DISK AT RUNTIME, never stored here: committing the numbers to
    source would publish the thing being withheld. Empty when the corpus is
    not present, in which case assert_clean still enforces the id exclusion.
    """
    out = []
    for cid in EXCLUDED_CHUNKS:
        tag = cid.rsplit("_", 1)[0]
        path = PROCESSED / f"chunks_{tag}_final.jsonl"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("chunk_id") != cid:
                continue
            for m in MOBILE_RX.finditer(row.get("display_text", "")):
                tok = m.group(0).strip()
                if tok:
                    out.append(tok)
    return out


def assert_clean(rows, where="retrieval result"):
    """Raise if an excluded chunk, or one of its numbers, survived.

    THE GUARANTEE IS THIS FUNCTION, not the care taken upstream -- the same
    reason the answer post-filter exists rather than a sixth prompt wording.

    TWO CHECKS, and the first is the real one:
      1. no excluded chunk_id is present. Exact, and cannot false-positive.
      2. none of the excluded chunk's own number strings appears anywhere in
         the rows -- which catches the case where that text reaches a result
         through some other chunk, without blocking the institutional numbers
         that legitimately share its format.
    """
    bad_ids = [r.get("chunk_id") for r in rows if is_excluded(r.get("chunk_id"))]
    if bad_ids:
        raise AssertionError(
            f"excluded chunk(s) reached the {where}: {bad_ids}")
    spans = personal_spans()
    if spans:
        leaked = []
        for r in rows:
            blob = json.dumps({k: v for k, v in r.items()
                               if k in ("display_text", "text", "index_text",
                                        "sentences", "numbers")},
                              ensure_ascii=False)
            if any(sp in blob for sp in spans):
                leaked.append(r.get("chunk_id"))
        if leaked:
            # The number itself is NOT printed.
            raise AssertionError(
                f"withheld number reached the {where} via: {leaked}")
    return True


def canary_selftest(verbose=True):
    """Proves the exclusion can fail, on the REAL chunk, read from disk.

    The fixture is SLICED FROM THE STORED ARTEFACT rather than typed --
    CONVENTIONS.md Convention 3. A hand-typed Bengali fixture is not the
    failure it claims to be; a phone number typed from looking at it would be
    worse, because a wrong digit still looks like a phone number.
    """
    ok = True
    path = PROCESSED / "chunks_livestock_grow_2026_final.jsonl"
    if not path.exists():
        if verbose:
            print(f"  canary redact_excluded      SKIPPED -- {path} absent")
        return True
    rows = [json.loads(l) for l in
            path.read_text(encoding="utf-8").splitlines() if l.strip()]
    by_id = {r["chunk_id"]: r for r in rows}

    # 1. the detector fires on the real chunk
    target = by_id.get("livestock_grow_2026_0032")
    fires = target is not None and contains_personal_number(
        target.get("display_text", ""))

    # 2. and it does NOT fire on the institutional short code alone. Built by
    #    deleting every mobile from the real sentence, so the surviving text
    #    is the corpus's own, not a guess about what it looks like.
    hotline_only = MOBILE_RX.sub("", (target or {}).get("display_text", ""))
    quiet = not contains_personal_number(hotline_only)

    # 3. scrub removes it, and leaves the other page-8 chunks alone
    page8 = [r for r in rows if str(r.get("page")) == "8"]
    kept, dropped = scrub(page8)
    removed = [r["chunk_id"] for r in dropped]
    survived = [r["chunk_id"] for r in kept]
    right = (removed == ["livestock_grow_2026_0032"] and len(survived) == 4)

    # 4. assert_clean rejects the unscrubbed list and accepts the scrubbed one
    try:
        assert_clean(page8, "canary")
        caught = False
    except AssertionError:
        caught = True
    try:
        assert_clean(kept, "canary")
        passes = True
    except AssertionError:
        passes = False

    # 5. AND IT MUST NOT BLOCK INSTITUTIONAL NUMBERS. dabi_2025_0210 carries
    #    five divisional claim hotlines in mobile format with no name on any
    #    of them. An earlier version of assert_clean keyed on format and
    #    raised on exactly this row, breaking a legitimate hotline query.
    inst = []
    dabi = PROCESSED / "chunks_dabi_2025_final.jsonl"
    if dabi.exists():
        for line in dabi.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row["chunk_id"] in ("dabi_2025_0210", "dabi_2025_0269"):
                inst.append(row)
    try:
        assert_clean(inst, "canary")
        institutional_ok = True
    except AssertionError:
        institutional_ok = False

    ok = (fires and quiet and right and caught and passes
          and institutional_ok)
    if verbose:
        print(f"  canary redact_excluded      detector fires on real chunk="
              f"{fires}; quiet on short code alone={quiet}; scrub removed="
              f"{removed} kept={len(survived)}; assert_clean caught="
              f"{caught}, passes scrubbed={passes}; institutional "
              f"hotlines allowed={institutional_ok}  "
              f"[{'DETECTED' if ok else 'MISSED'}]")
    return ok


def main():
    ap_ok = canary_selftest()
    print()
    print(f"{len(EXCLUDED_CHUNKS)} chunk(s) withheld from retrieval:")
    for cid, why in sorted(EXCLUDED_CHUNKS.items()):
        print(f"  {cid}\n    {why}")
    return 0 if ap_ok else 1


if __name__ == "__main__":
    sys.exit(main())
