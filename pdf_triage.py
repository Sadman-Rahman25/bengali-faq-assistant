"""pdf_triage.py -- STEP 1. What kind of PDF is this?

Run before anything else. Thirty seconds, and it catches the worst case
immediately: an embedded legacy font means part of the text layer is Latin
bytes that contribute zero Bengali characters and zero malformed clusters.
dcheck's 0.03/1000 CLEAN verdict was true and useless for exactly that.

Deliberately does NOT emit a go/no-go on text correctness. bucket_test said
UNICODE_TEXT for a PDF whose every দাবি read দাকি. This tool reports what is
there; pdf_extract_check.py decides whether it is right.

Usage:  python pdf_triage.py livestock_grow.pdf --dump out/raw.txt
"""

import argparse
import collections
import sys
from pathlib import Path

from bn_shared import (BN_DIGITS, DANDA, LEGACY_FONTS, ZERO_WIDTH,
                       bengali_count, looks_legacy_ansi)

try:
    import fitz  # PyMuPDF
except ImportError:
    print("needs PyMuPDF:  pip install pymupdf", file=sys.stderr)
    raise


def font_inventory(doc):
    """{normalised_font_name: page_count} across the document."""
    seen = collections.Counter()
    for page in doc:
        for f in page.get_fonts(full=True):
            name = (f[3] or "").split("+")[-1]
            seen[name] += 1
    return seen


def legacy_fonts_present(fonts):
    hits = []
    for name in fonts:
        low = name.lower().replace(" ", "").replace("-", "")
        for fam in LEGACY_FONTS:
            if fam in low:
                hits.append((name, fam, fonts[name]))
                break
    return hits


def size_histogram(doc, max_pages=40):
    """Rounded span font sizes -> char count. Secondary heading signal only."""
    hist = collections.Counter()
    for page in list(doc)[:max_pages]:
        d = page.get_text("dict")
        for block in d.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    hist[round(span.get("size", 0), 1)] += len(
                        span.get("text", ""))
    return hist


def page_stats(page):
    text = page.get_text("text")
    return {
        "chars": len(text),
        "bengali": bengali_count(text),
        "latin": sum(1 for c in text if "a" <= c.lower() <= "z"),
        "bn_digits": sum(1 for c in text if c in BN_DIGITS),
        "ascii_digits": sum(1 for c in text
                            if c.isdigit() and c not in BN_DIGITS),
        "danda": text.count(DANDA),
        "fullstop": text.count("."),
        "zero_width": sum(1 for c in text if c in ZERO_WIDTH),
        "legacy_like": looks_legacy_ansi(text),
        "text": text,
    }


def classify(stats, legacy_hits):
    total_chars = sum(s["chars"] for s in stats)
    total_bn = sum(s["bengali"] for s in stats)
    pages_with_text = sum(1 for s in stats if s["chars"] > 40)
    if pages_with_text < max(1, len(stats) * 0.25):
        return "SCANNED"
    if legacy_hits or sum(1 for s in stats
                          if s["legacy_like"]) > len(stats) * 0.2:
        return "LEGACY_ANSI"
    if total_chars and total_bn / total_chars > 0.25:
        return "UNICODE_TEXT"
    return "MIXED_OR_UNKNOWN"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--dump", help="write the raw text layer here for probing")
    ap.add_argument("--max-pages", type=int, default=0)
    args = ap.parse_args()

    doc = fitz.open(args.pdf)
    pages = list(doc)[: args.max_pages] if args.max_pages else list(doc)

    print(f"FILE .......... {Path(args.pdf).name}")
    print(f"PAGES ......... {len(doc)}")

    fonts = font_inventory(doc)
    legacy = legacy_fonts_present(fonts)
    print(f"\nFONTS ({len(fonts)})")
    for name, n in fonts.most_common(20):
        flag = "  <-- LEGACY/ANSI" if any(name == h[0] for h in legacy) else ""
        print(f"  {n:>4} pages  {name}{flag}")

    if legacy:
        print("\n!! LEGACY FONT DETECTED")
        print("   Part of this text layer is Bengali stored as Latin bytes.")
        print("   It contributes ZERO Bengali chars and ZERO malformed")
        print("   clusters, so every orthography metric will call it clean.")
        print("   -> route those runs through bijoy_convert.py before scoring.")

    stats = [page_stats(p) for p in pages]
    bucket = classify(stats, legacy)

    def tot(k):
        return sum(s[k] for s in stats)

    print(f"\nchars ......... {tot('chars'):,}")
    print(f"bengali ....... {tot('bengali'):,}")
    print(f"latin ......... {tot('latin'):,}")
    print(f"bn digits ..... {tot('bn_digits'):,}   "
          f"ascii digits: {tot('ascii_digits'):,}")
    print(f"danda ......... {tot('danda'):,}   "
          f"full stop: {tot('fullstop'):,}")
    print(f"zero-width .... {tot('zero_width'):,}")
    print(f"empty pages ... {sum(1 for s in stats if s['chars'] <= 40)}")

    print("\nFONT SIZE HISTOGRAM (secondary heading signal only)")
    for size, n in sorted(size_histogram(doc).items(), reverse=True)[:12]:
        print(f"  {size:>6}pt  {n:>8,} chars")

    print(f"\nBUCKET ........ {bucket}")
    print("NOTE .......... a bucket is not a correctness verdict.")
    print("               run pdf_extract_check.py next; bucket_test said")
    print("               UNICODE_TEXT for a PDF where every দাবি read দাকি.")

    if args.dump:
        Path(args.dump).parent.mkdir(parents=True, exist_ok=True)
        Path(args.dump).write_text("\n\f\n".join(s["text"] for s in stats),
                                   encoding="utf-8")
        print(f"\ntext layer dumped -> {args.dump}")

    return 0 if bucket in ("UNICODE_TEXT", "LEGACY_ANSI") else 1


if __name__ == "__main__":
    sys.exit(main())
