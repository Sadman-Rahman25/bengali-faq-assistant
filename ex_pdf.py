"""ex_pdf.py -- STEP 5. PDF -> chunks.jsonl, ex.py's schema plus provenance.

SCHEMA COMPATIBILITY IS THE WHOLE POINT OF THIS FILE
----------------------------------------------------
Step 7 hands these chunks to retag / resplit / dupscan / dedupe, which were
written against the docx schema and index fields by exact key. The draft of
this module renamed most of them, and the result was not a clean failure:

  heading_path   emitted as a joined STRING, while all four tools do
                 " > ".join(c["heading_path"]). Joining a string joins it
                 character by character. No exception, just a garbage
                 heading on every chunk of this manual.
  display_text   never emitted at all, though every one of the four indexes
                 it directly. KeyError the moment step 7 starts.
  numbers        emitted raw, with commas and trailing full stops, where
                 ex.py stores them folded and stripped. dupscan and dedupe
                 compare number sets, so every cross-manual duplicate
                 involving this manual would quietly fail to group and the
                 3-way overlap report would look clean.

So: the shared fields below are ex.py's, spelled ex.py's way, formatted by
bn_shared helpers that are asserted against ex.py's own output. The new
fields are additions, never renames.

  manual_id              §5.4 is fire-safety in Progoti and livestock
                         insurance in Dabi, and this manual has its own §5.x
                         on the same subject. A clause citation without a
                         manual name is wrong two thirds of the time.
  source_type            docx | pdf_text | ocr  (also stored as `source`,
                         which is the key ex.py uses)
  extraction_confidence  verified | probed | unverified
  page                   1-based; the docx path has no page numbers
  clause                 folded ASCII clause number for citations
  needs_figure_check     the source span held a digit run that touched a
                         line break. ১০০০০\\n১-১৫০০\\n০০ was one ceiling.

NO `roles` FIELD, DELIBERATELY
The draft tagged roles via a roles.match() that does not exist, caught the
AttributeError, and stored [] on every chunk. The docx schema has no roles
field either, so the honest state is no field rather than an empty one.
Role tagging is a corpus-wide decision that belongs in roles.py, which owns
that vocabulary -- not something the PDF path invents for one manual.

Convention 2: product tagging goes through productmatch.programmes_in, the
same call ex.py makes. This module must never match a product name directly;
ex.py carried its own naive matcher and retag.py existed purely to undo it.

Usage:
  python ex_pdf.py livestock_grow.pdf --structure out/structure.json \\
      --manual-id livestock_grow_2026 --source-type pdf_text \\
      --out out/chunks.jsonl
"""

import argparse
import json
import re
import sys
from pathlib import Path

from bn_shared import (BN_DIGITS, DANDA, bengali_count, canon, display_norm,
                       fold_digits, numbers_in, unit_numbers_in)
# Convention 2. The same matcher ex.py uses, so retag.py stays a no-op.
from productmatch import programmes_in

try:
    import pdfplumber
except ImportError:
    print("needs:  pip install pdfplumber", file=sys.stderr)
    raise

MAX_CHARS = 900                     # prose accumulation cap; ex.py uses 900
SPLIT_RX = re.compile(rf"(?<=[{DANDA}.])\s+")
CLAUSE_HEAD_RX = re.compile(r"^\s*([০-৯0-9]+(?:\.[০-৯0-9]+)*)")
DIGIT_BREAK_RX = re.compile(rf"[{BN_DIGITS}0-9]\s*\n\s*[{BN_DIGITS}0-9]")

# Every key here is indexed by exact name in step 7. Keep this list in step
# with the tools; the canary asserts make_chunk() emits all of them.
DOWNSTREAM_REQUIRED = {
    "chunk_id":     "dupscan, dedupe, resplit",
    "display_text": "dupscan, dedupe, retag, resplit, roles.load_text",
    "heading_path": "dupscan, dedupe, retag, resplit  (must be a LIST)",
    "numbers":      "dupscan, dedupe",
    "unit_numbers": "dupscan",
    "product":      "retag",
    "block_type":   "resplit",
    "n_chars":      "resplit",
    "table_rows":   "resplit",
}


def split_sentences(text):
    return [s.strip() for s in SPLIT_RX.split(text) if s.strip()]


def digit_risk(raw_text):
    """True if a digit run touched a line break anywhere in the source span.

    ১০০০০\\n১-১৫০০\\n০০ is a single ceiling shattered across three lines and
    it looks entirely plausible once joined wrong.

    This must be handed the text WITH its line breaks. The draft passed the
    space-joined prose instead, so the pattern could never match and
    needs_figure_check was False for every prose chunk in the corpus -- the
    step-6 audit ran on tables only and reported a clean prose body it had
    never looked at.
    """
    return bool(DIGIT_BREAK_RX.search(raw_text or ""))


def prose_chunks(structure):
    """Accumulate body lines under a stable heading path up to MAX_CHARS.

    Yields (path, page, joined_text, raw_text). raw_text keeps the original
    line breaks so digit_risk() can see them; joined_text is the space-joined
    prose that becomes the chunk.
    """
    out, buf, path, page0 = [], [], None, None

    def emit():
        return (path, page0, " ".join(buf), "\n".join(buf))

    for l in structure:
        if l["is_heading"]:
            if buf:
                out.append(emit())
                buf = []
            path, page0 = l["path"], l["page"]
            continue
        if path != l["path"] and buf:
            out.append(emit())
            buf, path, page0 = [], l["path"], l["page"]
        if page0 is None:
            path, page0 = l["path"], l["page"]
        buf.append(l["text"])
        if sum(len(x) for x in buf) >= MAX_CHARS:
            out.append(emit())
            buf, page0 = [], l["page"]
    if buf:
        out.append(emit())
    return out


def table_rows(pdf_path):
    """pdfplumber tables, with the raw cell text kept alongside the clean one.

    Caption = nearest preceding short line on the page, because a nested
    ceiling table's product identity lives in its parent's caption.
    """
    tables = []
    with pdfplumber.open(pdf_path) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            words = (page.extract_text() or "").split("\n")
            caption = ""
            for w in words:
                if w.strip() and len(w.strip()) < 80:
                    caption = display_norm(w)
                    break
            for ti, tbl in enumerate(page.extract_tables()):
                raw = [[c or "" for c in row] for row in tbl if any(row)]
                if len(raw) < 2:
                    continue
                clean = [[display_norm(c) for c in row] for row in raw]
                tables.append({"page": pno, "index": ti, "caption": caption,
                               "header": clean[0], "rows": clean[1:],
                               "raw": raw})
    return tables


def _row_text(r):
    return " | ".join(c.replace("\n", "\\n") for c in r)


def serialise_table(t, max_rows=8, max_chars=1500):
    """Split into row groups; header AND caption repeated in each part.

    Packs by CHARACTER budget as well as row count, because row count alone
    does not bound the result: these tables have merged narrative cells, and
    one row of Progoti's §7 table of authority runs to 937 characters on its
    own. A fixed --max-rows 8 produced a 2,761-character chunk, and lowering
    it globally to satisfy one table repeats the header across hundreds of
    tiny parts everywhere else.

    A part always holds at least one row, so a single row longer than the
    budget still comes through whole -- splitting a row would put a figure in
    one chunk and its label in another. resplit.py is what handles those.
    """
    head = " | ".join(t["header"])
    fixed = len(t["caption"]) + len(head) + 24      # + room for the label
    groups, cur, cur_len = [], [], 0
    for r in t["rows"]:
        rt = len(_row_text(r)) + 1
        if cur and (len(cur) >= max_rows or fixed + cur_len + rt > max_chars):
            groups.append(cur)
            cur, cur_len = [], 0
        cur.append(r)
        cur_len += rt
    if cur:
        groups.append(cur)

    total = len(groups)
    parts = []
    for n, grp in enumerate(groups, 1):
        label = (f"{t['caption']} (part {n}/{total})" if total > 1
                 else t["caption"])
        body = "\n".join(_row_text(r) for r in grp)
        parts.append((f"{label}\n{head}\n{body}", grp))
    return parts


def make_chunk(cid, manual_id, source_type, confidence, path, page, body,
               kind, raw="", rows=None):
    """One chunk. Shared fields use ex.py's names, formats and helpers."""
    hp = list(path or [])
    head = " > ".join(hp)
    full = (head + "\n" + body) if head else body

    disp = display_norm(full)
    # ex.py takes numbers from the BODY only, not from the heading. Matching
    # that matters: dupscan compares these lists across manuals.
    nb = display_norm(body)
    clause_m = CLAUSE_HEAD_RX.match(hp[-1]) if hp else None

    return {
        # ---- ex.py's schema, spelled and formatted ex.py's way
        "chunk_id": cid,
        "doc": manual_id,
        "block_type": kind,
        "heading_path": hp,                 # LIST. Never pre-joined.
        "heading_depth": len(hp),
        "product": programmes_in(full),
        "text": canon(full),                # folded, for the index
        "display_text": disp,               # unfolded, for humans and dedupe
        "table_rows": rows,
        "in_textbox": False,                # PDFs have no Word text boxes
        # ex.py carries the real table nesting depth here. Neither the PDF
        # nor the transcription path can see a table inside a cell, so this
        # is 0 by construction -- not "no nesting", but "nesting unknown on
        # this path". Present so the field exists corpus-wide.
        "nest_depth": 0,
        "n_chars": len(disp),
        "numbers": numbers_in(nb),
        "unit_numbers": unit_numbers_in(nb),
        "source": source_type,
        # ---- PDF-path additions
        "manual_id": manual_id,
        "source_type": source_type,
        "extraction_confidence": confidence,
        "page": page,
        "clause": fold_digits(clause_m.group(1)) if clause_m else "",
        # From the body, not from `full`: display_text already carries the
        # heading, so splitting `full` prepends the heading to sentence one
        # and (with no danda on a heading line) usually swallows the first
        # real sentence into it.
        "sentences": split_sentences(nb),
        "bengali_chars": bengali_count(disp),
        "needs_figure_check": digit_risk(raw or body),
    }


def canary_selftest(verbose=True):
    """Convention 3. Prove the two silent failures are still caught.

    Both were live in the draft of this module: a pre-joined heading_path and
    a needs_figure_check that could never fire on prose. Neither raises, so
    only an assertion catches them.
    """
    ok = True

    shattered = ["ঋণসীমা ১০০০০", "১-১৫০০", "০০ টাকা।"]
    c = make_chunk("t_0001", "canary", "pdf_text", "probed",
                   ["৫.৪ ঋণের শর্তাবলি"], 7,
                   " ".join(shattered), "prose", raw="\n".join(shattered))

    missing = [k for k in DOWNSTREAM_REQUIRED if k not in c]
    if verbose:
        print(f"  canary downstream_keys      missing={missing or 'none'}  "
              f"[{'PASS' if not missing else 'FAIL'}]")
    ok &= not missing

    is_list = isinstance(c["heading_path"], list)
    joined = " > ".join(c["heading_path"])       # what step 7 actually does
    path_ok = is_list and joined == "৫.৪ ঋণের শর্তাবলি"
    if verbose:
        print(f"  canary heading_path_is_list list={is_list} "
              f"rejoins={joined!r}  [{'PASS' if path_ok else 'FAIL'}]")
    ok &= path_ok

    fired = c["needs_figure_check"]
    clean = make_chunk("t_0002", "canary", "pdf_text", "probed", [], 7,
                       "ঋণসীমা ১০০০০১ টাকা।", "prose",
                       raw="ঋণসীমা ১০০০০১ টাকা।")["needs_figure_check"]
    fig_ok = fired and not clean
    if verbose:
        print(f"  canary needs_figure_check   shattered={fired} "
              f"intact={clean}  [{'DETECTED' if fig_ok else 'MISSED'}]")
    ok &= fig_ok

    num_ok = (c["numbers"] == ["10000", "1", "1500", "00"]
              and "40000 টাকা" in make_chunk(
                  "t_0003", "canary", "pdf_text", "probed", [], 1,
                  "সীমা ৪০,০০০ টাকা।", "prose")["unit_numbers"])
    if verbose:
        print(f"  canary ex_py_number_format  {c['numbers']}  "
              f"[{'PASS' if num_ok else 'FAIL'}]")
    ok &= num_ok

    if verbose and not ok:
        print("  a schema guarantee step 7 depends on has regressed")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--structure", default="out/structure.json")
    ap.add_argument("--manual-id", required=True)
    ap.add_argument("--source-type", required=True,
                    choices=["pdf_text", "ocr"])
    ap.add_argument("--confidence", default="probed",
                    choices=["verified", "probed", "unverified"])
    ap.add_argument("--max-rows", type=int, default=8,
                    help="hard cap on rows per table part")
    ap.add_argument("--max-chars", type=int, default=1500,
                    help="character budget per table part; a part always "
                         "holds at least one row, so a single longer row "
                         "still comes through whole")
    ap.add_argument("--out", default="out/chunks.jsonl")
    args = ap.parse_args()

    print("CANARY")
    if not canary_selftest():
        print("FATAL: ex_pdf failed its own canary. These chunks would break "
              "step 7 silently.")
        return 2

    structure = json.loads(Path(args.structure).read_text(encoding="utf-8"))

    chunks, n = [], 0
    for path, page, body, raw in prose_chunks(structure):
        if len(body.strip()) < 30:
            continue
        n += 1
        chunks.append(make_chunk(
            f"{args.manual_id}_{len(chunks) + 1:04d}", args.manual_id,
            args.source_type, args.confidence, path, page, body, "prose",
            raw=raw))

    m = 0
    for t in table_rows(args.pdf):
        raw = "\n".join("\t".join(r) for r in t["raw"])
        for part, grp in serialise_table(t, args.max_rows, args.max_chars):
            m += 1
            chunks.append(make_chunk(
                f"{args.manual_id}_{len(chunks) + 1:04d}", args.manual_id,
                args.source_type, args.confidence, [t["caption"]], t["page"],
                part, "table", raw=raw, rows=grp))

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        for c in chunks:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")

    oversize = [c for c in chunks if c["n_chars"] > 2000]
    flagged = [c for c in chunks if c["needs_figure_check"]]
    tagged = [c for c in chunks if c["product"]]
    orphan = [c for c in chunks if not c["heading_path"]]

    print(f"\nchunks .............. {len(chunks)}  "
          f"(prose {n}, table parts {m})")
    print(f"oversized (>2000) ... {len(oversize)}   must be 0")
    print(f"no heading path ..... {len(orphan)}")
    print(f"tagged with product . {len(tagged)}")
    print(f"needs figure check .. {len(flagged)}")
    print(f"-> {args.out}")

    if not tagged:
        print("\nWARNING: not one chunk carries a product tag. programmes_in "
              "matched\nnothing at all, which for a product manual means the "
              "text layer is\ndamaged or the product is not in "
              "productmatch.PROGRAMME_PATTERNS yet.")
    if oversize:
        single = [c for c in oversize if len(c.get("table_rows") or []) == 1]
        print(f"\nFAIL: {len(oversize)} oversized chunk(s).")
        if len(single) < len(oversize):
            print("      Lower --max-chars: these parts hold several rows.")
        if single:
            print(f"      {len(single)} hold a SINGLE row already, so neither "
                  f"--max-rows nor\n      --max-chars can help -- one merged "
                  f"narrative cell is over the cap.\n      Hand those to "
                  f"resplit.py, which is what it is for. Splitting a row\n"
                  f"      here would put a figure in one chunk and its label "
                  f"in another.")
        for c in oversize[:5]:
            print(f"        {c['chunk_id']}  {c['n_chars']} chars  "
                  f"{len(c.get('table_rows') or [])} row(s)  "
                  f"p{c['page']}")
        return 1
    if flagged:
        print("\nRun digit_audit.py next. Every flagged figure must be read "
              "against the rendered page before this corpus is embedded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
