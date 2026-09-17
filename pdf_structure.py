"""pdf_structure.py -- STEP 4. Rebuild the chunking spine.

The .docx path got the heading tree free from Word styles (16/54/174).
A PDF gives font sizes and nothing else. Font-size histograms break across a
document edited by several people over several versions, so:

  PRIMARY   clause numbering at line start  (১.১.২, 5.4.1)
  SECONDARY font size, only where numbering is absent
  GATE      body lines with no heading path. Near zero, or the spine is
            broken and everything downstream is guesswork. A build FAILURE,
            not a warning.

Letter enumerations (ক) (খ) (গ) are sub-items, not headings -- §2.1.1 runs
(ক) through (ঞ) and the changelog cites a (ট) that does not exist. Do not
promote them to heading level or you will invent structure.

DIGIT enumerations are the same trap, and it bites harder because numbering
is the signal this module trusts most. A bare `১.` at line start is usually a
list item, not a clause: the Livestock Grow PDF is a narrative document whose
only numbered lines are an insurance exclusions list, and reading those as
clauses promoted 21 exclusions to depth-1 headings and drove the orphan rate
to 22%. So a SINGLE-level number earns a heading only with corroborating
typography (bold, or larger than body text); a DOTTED number like ১.১ or
৫.৪.১ is accepted on numbering alone, because nothing enumerates a list that
way.

HEADING LABELS KEEP THE DIGITS AS PRINTED
-----------------------------------------
The label written into `path` uses display_norm, not canon, so ৫.৪ stays ৫.৪.
That is deliberate: ex.py builds the docx heading_path from raw Word heading
text, which keeps Bengali digits, and dupscan/dedupe/resplit compare heading
paths across manuals. A PDF path carrying "5.4 ..." where the docx path
carries "৫.৪ ..." would never group with its docx twin, and a 3-way overlap
report would look clean because the comparison could not fire.

The folded ASCII form is kept alongside as `number`, which is what the
`clause` field and citations use.

Usage:  python pdf_structure.py livestock_grow.pdf --out out/structure.json
"""

import argparse
import json
import statistics
import sys
import re
from pathlib import Path

from bn_shared import canon, canon_pattern, display_norm, fold_digits
from heading_gate import canary_selftest as gate_canary, check_spine

try:
    import fitz
except ImportError:
    print("needs PyMuPDF:  pip install pymupdf", file=sys.stderr)
    raise

# BENGALI DIGITS ONLY. An ASCII decimal at line start is a figure, not a
# clause: the previous [০-৯0-9] class invented 16 clause numbers out of rate
# and amount columns -- 0.25, 0.375, 1.125, 13.5, 341.92, 6.75. These manuals
# number their clauses in Bengali throughout; the docx heading tree has no
# ASCII-numbered heading at all.
DIGITS = r"[০-৯]"
# A dot is required at both call sites. A bare `১` is a list enumerator, and
# depth 1 belongs to the অধ্যায় signal and the front/back-matter registry.
_CLAUSE_NUM = rf"{DIGITS}{{1,3}}(?:\.{DIGITS}{{1,3}}){{1,3}}"
CLAUSE_RX = re.compile(rf"^\s*({_CLAUSE_NUM})\s*[\.\)]?\s+(\S.{{1,120}})$")
# The same number alone on its own line, with the title on the line below.
# Most of Progoti's level-3 headings are shaped this way, which is why only
# 51 of the docx's 204 clause numbers were being found.
BARE_CLAUSE_RX = re.compile(rf"^\s*({_CLAUSE_NUM})\s*[\.\)]?\s*$")
# Convention 4: the অধ্যায় literal is built by canon_pattern and spliced into
# the surrounding regex. Written inline it carried a decomposed য় while both
# PDFs emit precomposed U+09DF, so this never matched and NOT ONE chapter
# heading was ever detected in any manual.
_ADHYAY = canon_pattern("অধ্যায়").pattern
CHAPTER_RX = re.compile(rf"^\s*(\S+)\s*{_ADHYAY}\s*[:ঃ：]?\s*(.*)$")
LETTER_ITEM_RX = re.compile(r"^\s*\(?[ক-হ]\)\s+")

# A heading title is never a bare unit word. Progoti p73 is a premium rate
# table set one value per line with the unit on the line below, so the
# bare-number rule paired ০.২৫ with a "title" of টাকা and invented 30-odd
# clause headings out of one table. Same guard as the Bengali-digit and
# not-itself-a-number tests: what may follow a bare number is a TITLE.
UNIT_ONLY_RX = re.compile(
    "^(?:" + "|".join(canon_pattern(u).pattern for u in
                      ("টাকা", "শতাংশ", "মাস", "বছর", "দিন", "কিস্তি",
                       "হাজার", "লক্ষ", "কোটি", "জন", "বার")) + r")[\s%।:]*$")

# Front and back matter: depth-1 sections that are NOT chapters and contain no
# অধ্যায়. A CLOSED REGISTRY, keyed by canonical title, listing every spelling
# actually observed in an extraction. Not a heuristic -- there is no shape
# these five share with each other that they do not also share with ordinary
# bold lines, so the only honest rule is a list.
#
# Every variant below was read off a real extraction, not guessed. Progoti
# 2025 damages four of the five, and the damage is the ordinary damage of this
# text layer: dropped র্ (ঊর্ধ্বতন -> ঊধ্বতন, পরিবর্তন -> পরিবতন), a dropped ক
# (পরিচালকের -> পরিচালের), a doubled consonant before ো (সংযোজনী ->
# সংযযোজনী), and one heading still in Bijoy bytes in the source document
# itself (অরগানোগ্রাম -> AiMv‡bvMÖvg, which is how the .docx stores it too).
MATTER_TITLES = {
    "সূচনা": ["সূচনা"],
    "ঊর্ধ্বতন পরিচালকের বাণী": ["ঊর্ধ্বতন পরিচালকের বাণী",
                                "ঊধ্বতন পরিচালের বাণী"],
    "অরগানোগ্রাম": ["অরগানোগ্রাম", "অর্গানোগ্রাম", "AiMv‡bvMÖvg"],
    "সংযোজনী": ["সংযোজনী", "সংযযোজনী"],
    "পরিবর্তন সমূহ": ["পরিবর্তন সমূহ", "পরিবতন সমূহ"],
}
# Convention 4: built by canon_pattern, anchored to the line start.
MATTER_PATS = [(title, [re.compile("^" + canon_pattern(v).pattern)
                        for v in variants])
               for title, variants in MATTER_TITLES.items()]


def lines_with_style(doc):
    """[{page, text, size, bold}] in reading order."""
    out = []
    for pno, page in enumerate(doc):
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                if not spans:
                    continue
                text = "".join(s.get("text", "") for s in spans)
                if not text.strip():
                    continue
                out.append({
                    "page": pno + 1,
                    "text": text.rstrip(),
                    "size": round(max(s.get("size", 0) for s in spans), 1),
                    "bold": any("bold" in (s.get("font", "") or "").lower()
                                for s in spans),
                })
    return out


def body_size(lines):
    """Character-weighted median span size: the body text size."""
    sizes = [l["size"] for l in lines for _ in range(len(l["text"]))]
    return statistics.median(sizes) if sizes else 0.0


def _label(h):
    """The heading label as it appears in a path. Digits as printed."""
    return f"{h['number_raw']} {h['title']}".strip()


def chapter_headings(lines):
    """The ONLY source of depth-1 headings: the অধ্যায় signal.

    Returns {line_index: heading_dict}.

    CHAPTER_RX already does the line-initial filter -- it requires অধ্যায় to
    be the second token, so a mid-sentence cross-reference
    ("... দেখুন তৃতীয় অধ্যায়") cannot match -- and the length cap keeps it to
    short lines.

    What is left is that the same chapter appears up to three times in a PDF,
    and none of the copies is wrong, so they cannot be filtered by shape:

        14.0pt  the table-of-contents row        (pages 2-7)
        36.0pt  the chapter's own divider page   (the real start)
        16.0pt  the running header on the next page

    Two collapses, in order:

      1. Per chapter ordinal, keep the largest-font occurrence. That is the
         divider page: bigger than the TOC row and bigger than the running
         header, with no threshold to tune.
      2. Then drop any chapter that starts within 2 pages of the previous
         one. Two chapter openings a page apart is not a real document; what
         it actually means is that a damaged text layer spelled one ordinal
         two ways -- Progoti gives চতুর্থ on p65 and চতুথ on p66 -- which
         rule 1 cannot merge because it keys on the spelling.
    """
    cands = []
    for i, l in enumerate(lines):
        t = l["text"].strip()
        if len(t) >= 90:
            continue
        m = CHAPTER_RX.match(canon(t))
        if m:
            cands.append((i, l, canon(m.group(1)), display_norm(t)))

    best = {}
    for i, l, ordinal, title in cands:
        if ordinal not in best or l["size"] > best[ordinal][1]["size"]:
            best[ordinal] = (i, l, ordinal, title)

    out, last_page = {}, None
    for i, l, ordinal, title in sorted(best.values(), key=lambda x: x[0]):
        if last_page is not None and l["page"] - last_page <= 2:
            continue
        out[i] = {"depth": 1, "number": "", "number_raw": "",
                  "title": title, "source": "chapter_word",
                  "confidence": "high"}
        last_page = l["page"]
    return out


def matter_headings(lines):
    """Depth-1 front and back matter, from the closed MATTER_TITLES registry.

    Returns {line_index: heading_dict}.

    Deduped by canonical title, keeping the largest-font occurrence, because
    each of these also has a table-of-contents row: সূচনা is 14pt on p2 and
    16pt on p10. NO adjacency collapse here, unlike chapter_headings --
    AiMv‡bvMÖvg sits one page after chapter 10's divider, and সংযযোজনী and
    পরিবতন সমূহ are one page apart from each other. A closed registry is
    already deduplicated by construction; it does not need the rule that
    exists to merge two spellings of one ordinal.
    """
    best = {}
    for i, l in enumerate(lines):
        t = l["text"].strip()
        if not t or len(t) > 60:
            continue
        c = canon(t)
        for title, pats in MATTER_PATS:
            if any(p.match(c) for p in pats):
                if title not in best or l["size"] > best[title][1]["size"]:
                    best[title] = (i, l, title)
                break
    return {i: {"depth": 1, "number": "", "number_raw": "",
                "title": display_norm(l["text"].strip()),
                "source": "front_back_matter", "confidence": "high"}
            for i, l, title in best.values()}


def toc_pages_by_density(lines, tagged, min_lines=6, share=0.5):
    """METHOD A -- page-level heading density.

    A contents page is mostly contents: nearly every parsed line on it is a
    clause number. A body page has a handful of headings among hundreds of
    words. So: count parsed lines per page and clause headings per page, and
    call a page a contents page when the headings are at least `share` of it.

    Independent of METHOD B: it never compares one heading with another, only
    a page with itself. It can therefore catch a contents page whose numbers
    happen to appear nowhere else, and it cannot be fooled by a clause that
    is legitimately numbered twice.
    """
    per_page, head_page = {}, {}
    for l, tg in zip(lines, tagged):
        per_page[l["page"]] = per_page.get(l["page"], 0) + 1
        if tg["heading"] and tg["heading"]["source"] == "clause_number":
            head_page[l["page"]] = head_page.get(l["page"], 0) + 1
    return {pg for pg, n in head_page.items()
            if n >= min_lines and n / max(per_page[pg], 1) >= share}


def duplicate_later_wins(lines, tagged):
    """METHOD B -- duplicate clause numbers, keep the later occurrence.

    A clause is numbered once in the body. If ১.১.২ is tagged twice, one of
    them is the contents row, and the contents come first. So for each
    repeated number keep the last occurrence and drop the rest.

    Returns the set of line indexes to DROP.

    Independent of METHOD A: it compares headings with each other and never
    looks at the page. It therefore catches a stray contents row on a page
    that is not mostly contents, and it says nothing about a number that
    appears only once.
    """
    seen = {}
    for i, tg in enumerate(tagged):
        h = tg["heading"]
        if h and h["source"] == "clause_number":
            seen.setdefault(h["number"], []).append(i)
    drop = set()
    for idxs in seen.values():
        if len(idxs) > 1:
            drop.update(idxs[:-1])
    return drop


def detect_headings(lines, body):
    """Tag each line as heading (with depth) or body.

    DEPTH 1 COMES ONLY FROM THE অধ্যায় SIGNAL AND THE MATTER REGISTRY.
    Clause numbering contributes depth 2 and below and requires a dot.
    TYPOGRAPHY CONTRIBUTES NOTHING, AT ANY DEPTH.

    Typography is gone because it cannot do the job. A bare `১.` is a list
    enumerator, and requiring bold-or-larger alongside it was not enough:
    Progoti's surviving depth-1 headings were the bold cells of a loan-term
    table (৩ মাস, ৪ মাস ... ৩৬ মাস on p131) and Dabi 2026's were bold
    enumerated list items (১. স্বাভাবিক মৃত্যু). At depth 2 it was worse in a
    quieter way: 137 of Progoti's 206 depth-2 headings rested on font size
    alone against a truth of 54, so the signal was contributing roughly two
    false headings for every real one it found.

    `body` is retained in the signature: callers pass it, and the orphan-rate
    report still wants the measured body size.
    """
    chapters = chapter_headings(lines)
    matter = matter_headings(lines)
    # Normalise every line ONCE, and match everything against that. This is
    # the root fix for a whole class of near-miss: U+200B is not \s in Python,
    # so `১.১.১​` -- which is how this PDF emits a bare clause number --
    # failed `\s*$`, and only 27 of 80 such lines matched. Patching each
    # pattern to tolerate zero-width has to be remembered at every new
    # pattern; normalising the text side is remembered once.
    #
    # display_norm, NOT canon. canon() also folds Bengali digits to ASCII,
    # which would erase the very distinction the clause patterns rely on --
    # ০.২৫ (a Bengali-digit rate) and 0.25 become the same string, and
    # [০-৯] then matches nothing at all. Measured: canon() takes bare-number
    # matches from 27/80 to 0/80. display_norm does NFC, strips zero-width
    # and collapses spaces, and leaves digits alone.
    ct = [display_norm(l["text"]) for l in lines]
    tagged = []
    for i, l in enumerate(lines):
        t = ct[i]
        h = chapters.get(i) or matter.get(i)

        if h is None and not LETTER_ITEM_RX.match(t):
            m = CLAUSE_RX.match(t) if len(t) < 130 else None
            raw = title = None
            if m and not UNIT_ONLY_RX.match(m.group(2)):
                # `০.২৫ টাকা` is a rate in a table, not clause ০.২৫ titled
                # "টাকা". Same guard as the lookahead path below.
                raw, title = m.group(1), display_norm(m.group(2))
            elif m is None:
                # Number alone on a line: the title is the next line. Require
                # that line to look like a title -- some Bengali, not itself a
                # clause number, not a bare unit -- or a contents row would
                # pair a number with a page number.
                bm = BARE_CLAUSE_RX.match(t)
                if bm:
                    nxt = ct[i + 1] if i + 1 < len(ct) else ""
                    if (nxt and len(nxt) < 130
                            and any("ঀ" <= ch <= "৿" for ch in nxt)
                            and not BARE_CLAUSE_RX.match(nxt)
                            and not UNIT_ONLY_RX.match(nxt)):
                        raw, title = bm.group(1), display_norm(nxt)
            if raw:
                num = fold_digits(raw)
                h = {"depth": min(num.count(".") + 1, 4),
                     "number": num, "number_raw": raw, "title": title,
                     "source": "clause_number", "confidence": "high"}

        tagged.append({**l, "heading": h})
    return tagged


def build_paths(tagged):
    """Walk the tagged lines, carrying a heading stack onto every body line."""
    stack, out = [], []
    for l in tagged:
        h = l["heading"]
        if h:
            stack = [s for s in stack if s["depth"] < h["depth"]]
            stack.append(h)
            out.append({**l, "path": [_label(s) for s in stack],
                        "is_heading": True, "label": _label(h)})
        else:
            out.append({**l, "path": [_label(s) for s in stack],
                        "is_heading": False, "label": None})
    return out


# Manuals that have no heading spine BY DESIGN and must not be run through
# this stage. livestock_grow_2026 is 8 pages of narrative: its only numbered
# lines are an insurance exclusions list and it contains no অধ্যায় heading at
# all, so the orphan rate is 60% however the detector is tuned. It ingests via
# ex_text.py from a transcription, which carries its own heading markup.
#
# A skip list, not a lowered gate. Lowering the orphan threshold to let this
# document through would also let a genuinely broken spine through for the
# other two, and the whole point of the gate is that it is a build failure.
NO_SPINE = {
    "livestock_grow_2026": "8-page narrative, no clause numbering and no "
                           "অধ্যায় heading; ingests via ex_text.py",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--manual-id",
                    help="if listed in NO_SPINE, skip this stage and exit 0")
    ap.add_argument("--out", default="out/structure.json")
    ap.add_argument("--max-orphan-rate", type=float, default=0.02,
                    help="build fails above this share of body lines "
                         "with no path")
    args = ap.parse_args()

    if args.manual_id in NO_SPINE:
        print(f"SKIP: {args.manual_id} has no heading spine by design --")
        print(f"      {NO_SPINE[args.manual_id]}")
        print("      Nothing to build. Use ex_text.py for this manual.")
        return 0

    # Convention 3: the over-detection gate proves it can fail before it is
    # pointed at anything real.
    print("CANARY")
    if not gate_canary():
        print("FATAL: heading gate failed its own canary. Do not trust the "
              "spine verdict.")
        return 2
    print()

    doc = fitz.open(args.pdf)
    lines = lines_with_style(doc)
    body = body_size(lines)
    tagged = detect_headings(lines, body)

    # TOC exclusion. Two methods, computed independently and reported before
    # either is applied, so their agreement is visible rather than assumed.
    toc_pages = toc_pages_by_density(lines, tagged)
    dup_drop = duplicate_later_wins(lines, tagged)
    dens_drop = {i for i, l in enumerate(lines) if l["page"] in toc_pages
                 and tagged[i]["heading"]
                 and tagged[i]["heading"]["source"] == "clause_number"}

    both = dens_drop & dup_drop
    only_dens = dens_drop - dup_drop
    only_dup = dup_drop - dens_drop
    union = dens_drop | dup_drop
    print("\nTOC EXCLUSION")
    print(f"  A  density   pages {sorted(toc_pages)}  -> "
          f"{len(dens_drop)} clause headings")
    print(f"  B  duplicate later-wins        -> {len(dup_drop)} dropped")
    print(f"  agree on {len(both)}; A only {len(only_dens)}; "
          f"B only {len(only_dup)}; union {len(union)}")
    if union:
        print(f"  agreement {len(both) / len(union):.0%} of the union")

    for i in union:
        tagged[i]["heading"] = None
    walked = build_paths(tagged)

    heads = [l for l in walked if l["is_heading"]]
    bodies = [l for l in walked if not l["is_heading"]]
    orphans = [l for l in bodies if not l["path"]]

    by_depth, by_source = {}, {}
    for h in heads:
        d = h["heading"]["depth"]
        s = h["heading"]["source"]
        by_depth[d] = by_depth.get(d, 0) + 1
        by_source[s] = by_source.get(s, 0) + 1

    print(f"body font size .. {body}pt")
    print(f"lines ........... {len(lines):,}")
    print(f"headings ........ {len(heads):,}")
    print("  by depth ...... " + " / ".join(f"L{d}:{by_depth[d]}"
                                            for d in sorted(by_depth)))
    print("  by source ..... " + " / ".join(f"{k}:{v}"
                                            for k, v in by_source.items()))
    low = by_source.get("font_size", 0)
    if low:
        print(f"  !! {low} headings rest on font size alone. Spot-check them "
              f"against the rendered pages before trusting the spine.")

    rate = len(orphans) / max(len(bodies), 1)
    print(f"\norphan body lines  {len(orphans):,} / {len(bodies):,} = "
          f"{rate:.2%}")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(walked, ensure_ascii=False),
                              encoding="utf-8")
    print(f"structure -> {args.out}")

    if rate > args.max_orphan_rate:
        print(f"\nFAIL: orphan rate above {args.max_orphan_rate:.0%}. The "
              f"heading spine is not reliable; chunking on it would be "
              f"guesswork. Fix numbering detection before continuing.")
        return 1

    # The other direction. Orphan rate cannot see over-detection: inventing a
    # heading creates false structure, not orphans, so every line underneath
    # gets a well-formed path. See heading_gate.py.
    ok, sig, fails = check_spine(walked)
    print(f"\ndepth-1 resets ..... {sig['resets']} "
          f"({sig['reset_rate']:.0%} of transitions)   "
          f"lines with অধ্যায়: {sig['chapter_word_lines']}")
    print(f"depth-1 per page ... {sig['depth1_per_page']:.2f}")
    if not ok:
        print("\nFAIL: heading spine is OVER-detected.")
        for f in fails:
            print(f"  - {f}")
        print(f"\nNote the orphan rate above ({rate:.2%}) did not move. It "
              f"cannot: inventing\na heading creates false structure, not "
              f"orphans.")
        return 1

    print("\nspine OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
