"""pdf_shootout.py -- STEP 3. Does any other library read this PDF correctly?

Run before paying for OCR. Seven routes through the text layer, scored by
probe coverage plus the two negative signals. Objective; no judgement call.

If every route scores the same, the text layer itself is broken and no
extraction library will save you -- go to OCR. That is what happened with
Dabi 2026: every route returned the same corruption.

Usage:  python pdf_shootout.py livestock_grow.pdf --save-best out/best.txt
"""

import argparse
import sys
from pathlib import Path

from bn_shared import bengali_count, legal_damage, validity_rate
from probe_words import canary_selftest, load_probes, score_text, verdict


def r_pymupdf_text(path):
    import fitz
    return "\n".join(p.get_text("text") for p in fitz.open(path))


def r_pymupdf_blocks(path):
    import fitz
    out = []
    for p in fitz.open(path):
        for b in sorted(p.get_text("blocks"), key=lambda b: (b[1], b[0])):
            out.append(b[4])
    return "\n".join(out)


def r_pymupdf_rawdict(path):
    import fitz
    out = []
    for p in fitz.open(path):
        for blk in p.get_text("rawdict").get("blocks", []):
            for line in blk.get("lines", []):
                for span in line.get("spans", []):
                    out.append("".join(c.get("c", "")
                                       for c in span.get("chars", [])))
                out.append("\n")
    return "".join(out)


def r_pdfplumber(path):
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def r_pypdf(path):
    from pypdf import PdfReader
    return "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)


def r_pdfminer(path):
    from pdfminer.high_level import extract_text
    return extract_text(path)


def r_pdftotext_layout(path):
    import subprocess
    return subprocess.run(["pdftotext", "-layout", path, "-"],
                          capture_output=True, text=True, check=True).stdout


ROUTES = [
    ("pymupdf:text", r_pymupdf_text),
    ("pymupdf:blocks", r_pymupdf_blocks),
    ("pymupdf:rawdict", r_pymupdf_rawdict),
    ("pdfplumber", r_pdfplumber),
    ("pypdf", r_pypdf),
    ("pdfminer.six", r_pdfminer),
    ("pdftotext -layout", r_pdftotext_layout),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--probes", default="probes_livestock_grow.csv")
    ap.add_argument("--save-best", help="write the winning route's text here")
    args = ap.parse_args()

    print("CANARY")
    if not canary_selftest():
        print("FATAL: scoring function failed its canary.")
        return 2

    probes = load_probes(args.probes)

    rows, texts = [], {}
    for name, fn in ROUTES:
        try:
            txt = fn(args.pdf)
        except Exception as exc:
            rows.append((name, None, None, None, None,
                         f"{type(exc).__name__}: {exc}"))
            continue
        bn = bengali_count(txt)
        if bn == 0:
            # Produced no Bengali at all. That is a route that did not run,
            # not a route that scored zero -- pdftotext returns empty stdout
            # rather than failing when poppler is missing. Scoring it as
            # 0.0% coverage inflated the spread to 25% on a file where every
            # working route agreed exactly, which suppressed the "the text
            # layer itself is broken, go to OCR" conclusion -- the one thing
            # this tool exists to tell you.
            rows.append((name, None, None, None, None,
                         "no Bengali text produced"))
            continue
        texts[name] = txt
        pr = score_text(txt, probes)
        rows.append((name, bn, pr["coverage"], validity_rate(txt),
                     len(legal_damage(txt)), verdict(pr)))

    print(f"\n{'route':<20} {'bn chars':>10} {'probe':>8} "
          f"{'mal/1k':>8} {'damage':>8}  verdict")
    print("-" * 70)
    for name, bn, cov, val, dmg, v in rows:
        if bn is None:
            print(f"{name:<20} {'-':>10} {'-':>8} {'-':>8} {'-':>8}  {v}")
            continue
        print(f"{name:<20} {bn:>10,} {cov:>7.1%} {val:>8.1f} {dmg:>8,}  {v}")

    ok = [r for r in rows if r[1] is not None]
    if not ok:
        print("\nevery route failed to run")
        return 1

    spread = max(r[2] for r in ok) - min(r[2] for r in ok)
    best = max(ok, key=lambda r: (r[2], -r[3], -r[4]))

    print(f"\nbest .......... {best[0]}  (probe {best[2]:.1%})")
    print(f"spread ........ {spread:.1%}")
    if spread < 0.05 and best[2] < 0.90:
        print("\nAll routes agree and all are bad. The text layer itself is")
        print("broken -- a wrong ToUnicode CMap, not a library problem.")
        print(f'-> go to OCR:  python ocr_bench.py "{args.pdf}"')
    elif best[2] >= 0.90:
        print("\nA route reads this correctly. Use it; skip OCR.")

    if args.save_best and best[0] in texts:
        Path(args.save_best).parent.mkdir(parents=True, exist_ok=True)
        Path(args.save_best).write_text(texts[best[0]], encoding="utf-8")
        print(f"saved -> {args.save_best}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
