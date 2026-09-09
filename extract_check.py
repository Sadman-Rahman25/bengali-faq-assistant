#!/usr/bin/env python3
"""
extract_check.py - Is the PDF's text layer actually CORRECT?

bucket_test.py answers "is there Bengali text?". That is not the same
question. A PDF with a broken ToUnicode CMap renders perfectly on screen but
extracts the WRONG Bengali codepoints - still 96% Bengali by character range,
still passes every ratio check, and completely useless once embedded.

Symptoms in the wild:
    প্প্রাডাক্টস   for  প্রোডাক্টস      (substituted characters)
    গ্রাহে প্সবা   for  গ্রাহক সেবা     (pre-base vowel misplaced)
    অভিদ োগ        for  অভিযোগ          (orphan matra after a space)

Two checks, one cheap and one definitive:

  1. ORTHOGRAPHIC VALIDITY (automatic)
     Bengali has sequences that cannot occur in well-formed text: a matra at
     the start of a word, two matras in a row, hasant followed by a matra,
     an independent vowel carrying a matra. Correct text scores near zero.
     Corrupted extraction scores high. Catches misplacement reliably;
     catches pure substitution only sometimes - hence check 2.

  2. SIDE-BY-SIDE HTML (definitive)
     Renders each page next to its own extracted text in one self-contained
     HTML file. Open it, read both. Thirty seconds per page and no metric
     can argue with your own eyes.

Usage
-----
    python extract_check.py data/raw/dabi_manual_2026.pdf --pages 2,20,45,90
    python extract_check.py data/raw/dabi_manual_2026.pdf --scan-all
    python extract_check.py data/raw/x.pdf --pages 10 --out check.html

Install
-------
    pip install pymupdf
"""

from __future__ import annotations

import argparse
import base64
import html
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

# ---------------------------------------------------------------- classes

CONS = set(range(0x0995, 0x09BA)) | {0x09DC, 0x09DD, 0x09DF, 0x09CE}
IVOWEL = set(range(0x0985, 0x0995))
MATRA = (set(range(0x09BE, 0x09C5)) | {0x09C7, 0x09C8}
         | set(range(0x09CB, 0x09CD)) | {0x09D7})
HASANT = {0x09CD}
NUKTA = {0x09BC}
SIGN = {0x0981, 0x0982, 0x0983}
BN_DIGIT = set(range(0x09E6, 0x09F0))
BENGALI = set(range(0x0980, 0x0A00))
ZW = {0x200B: "ZWSP", 0x200C: "ZWNJ", 0x200D: "ZWJ", 0xFEFF: "BOM"}

DEP = MATRA | HASANT | NUKTA          # cannot begin a word


def cls(cp: int) -> str:
    if cp in CONS:
        return "C"
    if cp in IVOWEL:
        return "V"
    if cp in MATRA:
        return "M"
    if cp in HASANT:
        return "H"
    if cp in NUKTA:
        return "N"
    if cp in SIGN:
        return "S"
    if cp in BN_DIGIT:
        return "D"
    if cp in BENGALI:
        return "?"
    return " "


def violations(text: str) -> tuple[list[tuple[str, str]], Counter]:
    """Return (list of (kind, context), counts). Context is for eyeballing."""
    t = unicodedata.normalize("NFC", text)
    out: list[tuple[str, str]] = []
    counts: Counter = Counter()

    def ctx(i: int) -> str:
        return t[max(0, i - 12):i + 12].replace("\n", " ")

    prev = " "
    for i, ch in enumerate(t):
        cp = ord(ch)
        c = cls(cp)

        # dependent sign opening a word
        if cp in DEP and prev in (" ", "\n", "\t") :
            counts["orphan_dependent"] += 1
            out.append(("matra/hasant starts a word", ctx(i)))
        elif c == "M":
            p = cls(ord(prev)) if prev.strip() else " "
            if p == "M":
                counts["double_matra"] += 1
                out.append(("two matras in a row", ctx(i)))
            elif p == "H":
                counts["hasant_matra"] += 1
                out.append(("hasant followed by matra", ctx(i)))
            elif p == "V":
                counts["vowel_matra"] += 1
                out.append(("independent vowel + matra", ctx(i)))
            elif p == "S":
                counts["sign_matra"] += 1
                out.append(("anusvara/visarga + matra", ctx(i)))
        elif c == "H" and i + 1 < len(t) and t[i + 1] in " \n\t":
            counts["dangling_hasant"] += 1
            out.append(("hasant at end of word", ctx(i)))
        prev = ch

    return out, counts


def bn_count(text: str) -> int:
    return sum(1 for c in text if ord(c) in BENGALI)


def zw_counts(text: str) -> Counter:
    c: Counter = Counter()
    for ch in text:
        if ord(ch) in ZW:
            c[ZW[ord(ch)]] += 1
    return c


def grade(rate: float) -> tuple[str, str]:
    """rate = violations per 1000 Bengali characters."""
    if rate < 3:
        return "CLEAN", "text layer looks trustworthy"
    if rate < 10:
        return "SUSPECT", "some malformed clusters - inspect the HTML"
    if rate < 30:
        return "LIKELY CORRUPT", "systematic misplacement - do not index as-is"
    return "CORRUPT", "text layer is unusable; OCR the rendered pages instead"


# ------------------------------------------------------------------ pymupdf

def load(path: Path):
    try:
        import pymupdf as fz
    except ImportError:
        try:
            import fitz as fz
        except ImportError:
            sys.exit("pip install pymupdf")
    doc = fz.open(path)
    if doc.is_encrypted and not doc.authenticate(""):
        sys.exit("Password-protected PDF.")
    return fz, doc


def parse_pages(spec: str, total: int) -> list[int]:
    out: set[int] = set()
    for part in re.split(r"[,\s]+", spec or ""):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return sorted(p for p in out if 1 <= p <= total)


# --------------------------------------------------------------- scan mode

def scan_all(path: Path) -> None:
    fz, doc = load(path)
    rows = []
    for i in range(doc.page_count):
        text = doc[i].get_text()
        n = bn_count(text)
        if n < 100:
            continue
        _, counts = violations(text)
        v = sum(counts.values())
        rows.append((i + 1, n, v, 1000 * v / n, counts))
    doc.close()

    if not rows:
        sys.exit("No pages with enough Bengali text to judge.")

    tot_bn = sum(r[1] for r in rows)
    tot_v = sum(r[2] for r in rows)
    rate = 1000 * tot_v / max(tot_bn, 1)
    g, msg = grade(rate)

    print("=" * 72)
    print(f"EXTRACTION INTEGRITY :: {path.name}")
    print(f"{len(rows)} pages with Bengali text, {tot_bn} Bengali chars")
    print("=" * 72)
    print(f"\n  malformed clusters ... {tot_v}")
    print(f"  per 1000 Bengali ..... {rate:.1f}")
    print(f"  VERDICT .............. {g}  ({msg})")

    agg: Counter = Counter()
    for r in rows:
        agg.update(r[4])
    if agg:
        print("\n  breakdown:")
        for k, n in agg.most_common():
            print(f"    {n:>7}  {k}")

    worst = sorted(rows, key=lambda r: -r[3])[:12]
    print("\n  worst pages (rate per 1000):")
    for pno, n, v, r, _ in worst:
        print(f"    p{pno:>4}  {r:>7.1f}   ({v} in {n} chars)")

    print("\n  NEXT")
    if g in ("CLEAN", "SUSPECT"):
        print("    Run --pages with 3 of the worst pages above and read the")
        print("    HTML before trusting this. Validity catches misplaced")
        print("    matras, not substituted characters.")
    else:
        print("    The text layer cannot be indexed as it stands. Two routes:")
        print("      a) OCR the rendered pages - the PDF renders correctly,")
        print("         so a vision model reading the image gets it right.")
        print("         You are back to ocr_bench.py after all.")
        print("      b) Find the original .docx and extract from that instead.")
        print("         Check this FIRST - it is minutes of work versus hours.")
        print("    Either way, confirm visually with --pages first.")
    print()


# --------------------------------------------------------------- html mode

CSS = """
body{font-family:system-ui,Segoe UI,sans-serif;margin:0;background:#f4f4f5;
color:#18181b}
header{background:#18181b;color:#fff;padding:14px 20px}
header h1{margin:0;font-size:16px;font-weight:600}
header p{margin:4px 0 0;font-size:12px;color:#a1a1aa}
.page{background:#fff;margin:18px;border:1px solid #d4d4d8;border-radius:6px;
overflow:hidden}
.hd{padding:9px 14px;background:#fafafa;border-bottom:1px solid #e4e4e7;
display:flex;gap:14px;align-items:baseline;flex-wrap:wrap}
.hd b{font-size:14px}
.tag{font-size:11px;padding:2px 8px;border-radius:10px;font-weight:600}
.CLEAN{background:#dcfce7;color:#166534}
.SUSPECT{background:#fef9c3;color:#854d0e}
.LIKELY{background:#ffedd5;color:#9a3412}
.CORRUPT{background:#fee2e2;color:#991b1b}
.grid{display:grid;grid-template-columns:1fr 1fr}
.grid>div{padding:12px;min-width:0}
.grid>div+div{border-left:1px solid #e4e4e7}
.lbl{font-size:11px;text-transform:uppercase;letter-spacing:.6px;
color:#71717a;margin-bottom:8px;font-weight:600}
img{width:100%;border:1px solid #e4e4e7;border-radius:3px}
pre{white-space:pre-wrap;word-break:break-word;font-size:15px;line-height:1.9;
margin:0;font-family:'Nirmala UI','Shonar Bangla','Vrinda',
'Noto Sans Bengali',serif}
mark{background:#fecaca;border-bottom:2px solid #dc2626;padding:0 1px}
.vio{margin-top:10px;font-size:12px;color:#71717a}
.vio code{background:#f4f4f5;padding:1px 4px;border-radius:3px;
font-family:'Nirmala UI',monospace}
"""


def mark_violations(text: str) -> str:
    """HTML-escape and wrap each offending character in <mark>."""
    t = unicodedata.normalize("NFC", text)
    bad: set[int] = set()
    prev = " "
    for i, ch in enumerate(t):
        cp, c = ord(ch), cls(ord(ch))
        p = cls(ord(prev)) if prev.strip() else " "
        if (cp in DEP and prev in (" ", "\n", "\t")) or \
           (c == "M" and p in ("M", "H", "V", "S")):
            bad.add(i)
        prev = ch
    out = []
    for i, ch in enumerate(t):
        e = html.escape(ch)
        out.append(f"<mark>{e}</mark>" if i in bad else e)
    return "".join(out)


def build_html(path: Path, pages: list[int], dpi: int, out: Path) -> None:
    fz, doc = load(path)
    parts = [
        "<!doctype html><meta charset='utf-8'>",
        f"<title>Extraction check - {html.escape(path.name)}</title>",
        f"<style>{CSS}</style>",
        "<header><h1>Extraction integrity check &mdash; "
        f"{html.escape(path.name)}</h1>",
        "<p>Left: the page as it renders. Right: what text extraction "
        "returns. Read both. Red marks are malformed Bengali clusters; "
        "substituted characters will NOT be marked, so compare the words "
        "themselves.</p></header>",
    ]

    for pno in pages:
        page = doc[pno - 1]
        pix = page.get_pixmap(matrix=fz.Matrix(dpi / 72, dpi / 72), alpha=False)
        b64 = base64.b64encode(pix.tobytes("png")).decode()
        text = page.get_text()

        n = bn_count(text)
        vio, counts = violations(text)
        v = sum(counts.values())
        rate = 1000 * v / n if n else 0.0
        g, msg = grade(rate) if n >= 40 else ("CLEAN", "too little text to judge")
        klass = g.split()[0] if g != "LIKELY CORRUPT" else "LIKELY"

        zw = zw_counts(text)
        zwtxt = ", ".join(f"{k}&times;{n2}" for k, n2 in zw.items()) or "none"

        parts.append("<div class='page'>")
        parts.append(
            f"<div class='hd'><b>Page {pno}</b>"
            f"<span class='tag {klass}'>{html.escape(g)}</span>"
            f"<span style='font-size:12px;color:#71717a'>"
            f"{n} Bengali chars &middot; {v} malformed "
            f"({rate:.1f}/1000) &middot; zero-width: {zwtxt}</span></div>")
        parts.append("<div class='grid'>")
        parts.append("<div><div class='lbl'>Rendered page</div>"
                     f"<img src='data:image/png;base64,{b64}'></div>")
        parts.append("<div><div class='lbl'>Extracted text</div><pre>"
                     + mark_violations(text[:6000]) + "</pre>")
        if vio:
            parts.append("<div class='vio'><b>Sample malformed clusters:</b><br>")
            seen = set()
            shown = 0
            for kind, c in vio:
                key = (kind, c[:8])
                if key in seen:
                    continue
                seen.add(key)
                parts.append(f"{html.escape(kind)}: "
                             f"<code>{html.escape(c)}</code><br>")
                shown += 1
                if shown >= 6:
                    break
            parts.append("</div>")
        parts.append("</div></div>")

        print(f"  p{pno:>4}  {g:<15} {rate:>6.1f}/1000  "
              f"({v} malformed in {n} Bengali chars)")

    doc.close()
    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"\nWrote {out}  ({out.stat().st_size // 1024} KB)")
    print("Open it and compare the two columns side by side.")
    print("This is the check that decides your whole ingestion route.")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Check whether a PDF's Bengali text layer is CORRECT.")
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--pages", help="e.g. 2,20,45,90 or 10-14")
    ap.add_argument("--scan-all", action="store_true",
                    help="validity rate for every page, no HTML")
    ap.add_argument("--dpi", type=int, default=130)
    ap.add_argument("--out", type=Path, help="HTML output path")
    a = ap.parse_args()

    if not a.pdf.exists():
        sys.exit(f"Not found: {a.pdf}")

    if a.scan_all:
        scan_all(a.pdf)
        return

    if not a.pages:
        sys.exit("Give --pages 2,20,45 or use --scan-all")

    fz, doc = load(a.pdf)
    pages = parse_pages(a.pages, doc.page_count)
    doc.close()
    if not pages:
        sys.exit("No valid pages in that range.")

    out = a.out or Path(f"check_{a.pdf.stem}.html")
    build_html(a.pdf, pages, a.dpi, out)


if __name__ == "__main__":
    main()