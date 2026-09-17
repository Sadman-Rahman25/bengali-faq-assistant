"""ex_text.py -- STEP 5b. Transcribed markdown -> chunks.jsonl.

For a manual whose PDF text layer is unrecoverable. The Livestock Grow PDF is
the case this was written for: all six extraction routes return identical
corruption, and the embedded Kalpurush subsets name 497 of 586 glyphs
`glyph000NN` with GSUB stripped, so there is nothing to repair the conjuncts
from. The renders are perfectly legible, so the text comes off the renders.

The schema comes from ex_pdf.make_chunk -- imported, not reimplemented.
There is exactly one definition of a chunk in this path, and ex_pdf's canary
is what guards it. A second copy here is how the two drift until step 7
starts reading a field one of them stopped writing.

INPUT FORMAT
  `# ` / `## ` / `### `   headings, nested into heading_path
  `<!-- page N -->`       sets the page for everything after it, so
                          digit_audit can render the right page beside each
                          flagged figure
  `<!-- ... -->`          any other comment is provenance, and is skipped
  everything else         body text

source_type is "ocr", not "pdf_text". That is not cosmetic: retrieval is
meant to prefer a docx chunk over an OCR chunk in a duplicate group, and
evaluation is segmented by it, so you find out this manual is dragging
accuracy down here rather than in the field.

extraction_confidence starts at "unverified" and MUST stay there until a
human has read the figure worklist against the renders. A transcription is
OCR output. It is not a source document.

Usage:
  python ex_text.py data/raw/livestock_grow_2026_transcribed.md \\
      --manual-id livestock_grow_2026 --out out/livestock_grow_2026/chunks.jsonl
"""

import argparse
import json
import re
import sys
from pathlib import Path

from ex_pdf import MAX_CHARS, make_chunk

HEADING_RX = re.compile(r"^(#{1,4})\s+(.*\S)\s*$")
PAGE_RX = re.compile(r"^<!--\s*page\s+(\d+)\s*-->\s*$", re.I)
COMMENT_OPEN_RX = re.compile(r"^<!--")
COMMENT_CLOSE_RX = re.compile(r"-->")


def parse(md_text):
    """-> [(heading_path, page, body_text)] in document order."""
    out, buf = [], []
    stack, page = [], 1
    in_comment = False

    def flush():
        if buf:
            body = "\n".join(buf).strip()
            if body:
                out.append(([lbl for _d, lbl in stack], page, body))
            buf.clear()

    for raw in md_text.splitlines():
        line = raw.rstrip()

        # A page marker is also a comment, so test it first.
        m = PAGE_RX.match(line.strip())
        if m:
            flush()
            page = int(m.group(1))
            continue

        if in_comment:
            if COMMENT_CLOSE_RX.search(line):
                in_comment = False
            continue
        if COMMENT_OPEN_RX.match(line.strip()):
            if not COMMENT_CLOSE_RX.search(line):
                in_comment = True
            continue

        m = HEADING_RX.match(line)
        if m:
            flush()
            depth = len(m.group(1))
            stack = [(d, l) for d, l in stack if d < depth]
            stack.append((depth, m.group(2)))
            continue

        if not line.strip():
            # A blank line ends a paragraph but not a section; only split the
            # chunk if we are already at the cap, so a section stays whole
            # where it fits.
            if sum(len(x) for x in buf) >= MAX_CHARS:
                flush()
            continue

        buf.append(line.strip())
        if sum(len(x) for x in buf) >= MAX_CHARS:
            flush()
    flush()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("markdown")
    ap.add_argument("--manual-id", required=True)
    ap.add_argument("--source-type", default="ocr",
                    choices=["pdf_text", "ocr"])
    ap.add_argument("--confidence", default="unverified",
                    choices=["verified", "probed", "unverified"])
    ap.add_argument("--out", default="out/chunks.jsonl")
    args = ap.parse_args()

    md = Path(args.markdown).read_text(encoding="utf-8")
    sections = parse(md)

    chunks = []
    for path, page, body in sections:
        if len(body.strip()) < 30:
            continue
        chunks.append(make_chunk(
            f"{args.manual_id}_{len(chunks) + 1:04d}", args.manual_id,
            args.source_type, args.confidence, path, page, body, "prose",
            raw=body))

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        for c in chunks:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")

    oversize = [c for c in chunks if c["n_chars"] > 2000]
    tagged = [c for c in chunks if c["product"]]
    orphan = [c for c in chunks if not c["heading_path"]]
    pages = sorted({c["page"] for c in chunks})

    print(f"sections ............ {len(sections)}")
    print(f"chunks .............. {len(chunks)}")
    print(f"pages covered ....... {pages}")
    print(f"oversized (>2000) ... {len(oversize)}   must be 0")
    print(f"no heading path ..... {len(orphan)}")
    print(f"tagged with product . {len(tagged)}")
    print(f"-> {args.out}")

    if orphan:
        print("\nFAIL: chunks with no heading path. Every body line must sit "
              "under a heading;\nadd one in the markdown.")
        return 1
    if oversize:
        print("\nFAIL: oversized chunks. Split the section in the markdown, "
              "or run resplit.py.")
        return 1
    print(f"\nsource_type={args.source_type}, "
          f"extraction_confidence={args.confidence}")
    print("Run digit_audit.py next and read EVERY flagged figure against the")
    print("rendered page. This corpus is a transcription until someone has.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
