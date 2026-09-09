#!/usr/bin/env python3
"""
bucket_test.py - Diagnose a Bangla document before you build any RAG pipeline.

Answers ONE question: which of three problems do you have?

  BUCKET 1  UNICODE_TEXT   Real Bengali codepoints in the text layer.
                           -> No OCR. Go straight to structure-aware chunking.
  BUCKET 2  LEGACY_ANSI    Bijoy / SutonnyMJ style ASCII-mapped font encoding.
                           Text looks like "Avgvi bvg". NOT an OCR problem.
                           -> Glyph conversion (bijoy2unicode etc.), then chunk.
  BUCKET 3  SCANNED        No usable text layer, page is an image.
                           -> OCR pipeline. Run ocr_bench.py next.

Also reports normalisation stats you need for Bengali chunking: NFC compliance,
zero-width joiners, Bengali vs ASCII digits, danda vs full-stop counts, and
whether headings are detectable from font size.

Usage
-----
    python bucket_test.py manual.pdf
    python bucket_test.py manual.pdf --pages 12,40,88 --json report.json
    python bucket_test.py manual.docx --dump-text samples/
    python bucket_test.py extracted.txt

Install
-------
    pip install pymupdf              # for PDF  (import name: fitz / pymupdf)
    pip install python-docx          # only if you pass a .docx
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

# --------------------------------------------------------------------------
# Character-class helpers
# --------------------------------------------------------------------------

BENGALI_LO, BENGALI_HI = 0x0980, 0x09FF
BN_DIGITS = "০১২৩৪৫৬৭৮৯"
DANDA = "\u0964"
DANDA_ALT = "\u09F7"          # Bengali currency-numerator dari variant
HASANT = "\u09CD"             # virama, marks conjuncts
ZWNJ, ZWJ = "\u200C", "\u200D"
NUKTA = "\u09BC"

# Font-name fragments that indicate ASCII-mapped legacy Bangla encodings.
LEGACY_FONT_HINTS = (
    "sutonny", "boishakhi", "chandrabati", "modhumati", "shreelipi",
    "bijoy", "proshika", "lekhoni", "rinkiy", "amarbangla", "aponalohit",
    "bangsree", "sulekha", "borno",
)
# Font-name fragments that indicate real Unicode Bangla fonts.
UNICODE_FONT_HINTS = (
    "solaimanlipi", "nikosh", "kalpurush", "siyamrupali", "siyam",
    "mukti", "notosansbengali", "notoserifbengali", "shonarbangla",
    "vrinda", "akaash", "lohit", "hindsiliguri", "baloochettan",
    "atma", "mina", "galada", "bengali",
)
# High-frequency Bijoy/SutonnyMJ glyph sequences. Individually weak signals,
# collectively decisive.
BIJOY_MARKERS = (
    "Av", "Zv", "Kv", "bv", "gv", "iv", "hv", "`v", "wU", "wi", "‡", "†",
    "Ó", "Ò", "¨", "¸", "”", "‰", "„", "Š", "•", "–v", "Bb", "Ki", "‡Z",
)


def _is_bengali(ch: str) -> bool:
    return BENGALI_LO <= ord(ch) <= BENGALI_HI


def _norm_font(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def classify_font(name: str) -> str:
    """Return 'legacy', 'unicode', or 'unknown' for a font name."""
    n = _norm_font(name)
    if not n:
        return "unknown"
    if re.search(r"mj$|mj[0-9]*$|ansi", n):
        return "legacy"
    if any(h in n for h in LEGACY_FONT_HINTS):
        return "legacy"
    if any(h in n for h in UNICODE_FONT_HINTS):
        return "unicode"
    return "unknown"


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def text_profile(text: str) -> dict:
    """Character-level evidence for the bucket decision."""
    visible = [c for c in text if not c.isspace()]
    n = len(visible) or 1

    bengali = sum(1 for c in visible if _is_bengali(c))
    ascii_letters = sum(1 for c in visible if c.isascii() and c.isalpha())
    high_ansi = sum(1 for c in visible if 0x80 <= ord(c) <= 0xFF)
    marker_hits = sum(1 for m in BIJOY_MARKERS if m in text)
    # 'v' maps to the aa-kar in SutonnyMJ, so it is wildly over-represented.
    v_rate = (text.count("v") / max(ascii_letters, 1)) if ascii_letters else 0.0

    return {
        "chars_visible": len(visible),
        "bengali_ratio": round(bengali / n, 4),
        "ascii_letter_ratio": round(ascii_letters / n, 4),
        "high_ansi_ratio": round(high_ansi / n, 4),
        "bijoy_marker_hits": marker_hits,
        "v_rate_among_ascii_letters": round(v_rate, 4),
    }


def bijoy_score(prof: dict) -> float:
    """0..1 confidence that this text is ASCII-mapped legacy Bangla."""
    if prof["chars_visible"] < 40:
        return 0.0
    if prof["bengali_ratio"] > 0.15:
        return 0.0                      # real Bengali present, not legacy
    s = 0.0
    s += 0.30 if prof["ascii_letter_ratio"] > 0.45 else 0.0
    s += 0.25 if prof["high_ansi_ratio"] > 0.02 else 0.0
    s += 0.25 * min(prof["bijoy_marker_hits"] / 8.0, 1.0)
    s += 0.20 if prof["v_rate_among_ascii_letters"] > 0.09 else 0.0
    return round(min(s, 1.0), 3)


def classify_page(prof: dict, fonts: dict, img_cover: float) -> tuple[str, str]:
    """Return (bucket, one-line reason)."""
    bscore = bijoy_score(prof)

    if prof["chars_visible"] < 25:
        if img_cover > 0.30:
            return "SCANNED", f"no text layer, image covers {img_cover:.0%} of page"
        return "EMPTY", "no text and no significant image (blank/divider page?)"

    if prof["bengali_ratio"] >= 0.25:
        return "UNICODE_TEXT", f"{prof['bengali_ratio']:.0%} Bengali codepoints"

    if bscore >= 0.5 or fonts.get("legacy", 0) > 0:
        why = f"bijoy_score={bscore}"
        if fonts.get("legacy"):
            why += f", {fonts['legacy']} legacy-named font(s)"
        return "LEGACY_ANSI", why

    if prof["ascii_letter_ratio"] > 0.5 and prof["bengali_ratio"] < 0.05:
        return "LATIN_TEXT", "looks like ordinary English text"

    return "UNKNOWN", (
        f"bengali={prof['bengali_ratio']:.0%}, "
        f"ascii={prof['ascii_letter_ratio']:.0%}, bijoy_score={bscore}"
    )


def normalisation_stats(text: str) -> dict:
    """Chunking-relevant hygiene metrics. Only meaningful for Unicode text."""
    nfc = unicodedata.normalize("NFC", text)
    bn_dig = sum(text.count(d) for d in BN_DIGITS)
    as_dig = sum(1 for c in text if c.isascii() and c.isdigit())
    return {
        "already_nfc": nfc == text,
        "chars_changed_by_nfc": sum(1 for a, b in zip(text, nfc) if a != b),
        "zwnj_count": text.count(ZWNJ),
        "zwj_count": text.count(ZWJ),
        "bare_nukta_count": text.count(NUKTA),
        "danda_count": text.count(DANDA),
        "danda_variant_count": text.count(DANDA_ALT),
        "period_count": text.count("."),
        "hasant_count": text.count(HASANT),
        "bengali_digits": bn_dig,
        "ascii_digits": as_dig,
        "mixed_digit_systems": bn_dig > 0 and as_dig > 0,
    }


# --------------------------------------------------------------------------
# Readers
# --------------------------------------------------------------------------

def read_pdf(path: Path, want_pages: list[int] | None, sample: int) -> dict:
    try:
        import pymupdf as fz
    except ImportError:
        try:
            import fitz as fz  # older name
        except ImportError:
            sys.exit("Need PyMuPDF:  pip install pymupdf")

    doc = fz.open(path)
    if doc.is_encrypted and not doc.authenticate(""):
        sys.exit("PDF is password-protected. Decrypt it first.")

    total = doc.page_count
    if want_pages:
        idxs = [p - 1 for p in want_pages if 1 <= p <= total]
    else:
        # Sample body pages: skip the first 2 (cover/TOC) and the last one.
        lo, hi = min(2, total - 1), max(total - 1, 1)
        span = max(hi - lo, 1)
        idxs = sorted({lo + (span * i) // max(sample, 1) for i in range(sample)})
        idxs = [i for i in idxs if 0 <= i < total] or [0]

    pages, all_text, all_fonts = [], [], Counter()
    heading_sizes = Counter()

    for i in idxs:
        page = doc[i]
        text = page.get_text()
        all_text.append(text)

        fonts = Counter()
        for f in page.get_fonts(full=True):
            fonts[classify_font(f[3])] += 1
            all_fonts[f[3]] += 1

        # Image coverage as a fraction of page area, plus effective DPI.
        parea, dpis = abs(page.rect.get_area()) or 1.0, []
        icover = 0.0
        for info in page.get_images(full=True):
            xref = info[0]
            for r in page.get_image_rects(xref) or []:
                icover += abs(r.get_area()) / parea
            try:
                meta = doc.extract_image(xref)
                pw_in = page.rect.width / 72.0
                if pw_in > 0:
                    dpis.append(round(meta["width"] / pw_in))
            except Exception:
                pass
        icover = min(icover, 1.0)

        # Font-size histogram -> can we detect headings structurally?
        try:
            d = page.get_text("dict")
            for blk in d.get("blocks", []):
                for line in blk.get("lines", []):
                    for span in line.get("spans", []):
                        if span.get("text", "").strip():
                            heading_sizes[round(span["size"], 1)] += 1
        except Exception:
            pass

        prof = text_profile(text)
        bucket, why = classify_page(prof, fonts, icover)
        pages.append({
            "page": i + 1,
            "bucket": bucket,
            "reason": why,
            "image_coverage": round(icover, 3),
            "image_dpi_estimate": (max(dpis) if dpis else None),
            "fonts": dict(fonts),
            **prof,
            "bijoy_score": bijoy_score(prof),
            "text_sample": text.strip()[:180],
        })

    doc.close()
    return {
        "total_pages": total,
        "sampled": [p["page"] for p in pages],
        "pages": pages,
        "joined_text": "\n".join(all_text),
        "font_names": dict(all_fonts.most_common(40)),
        "font_size_histogram": dict(heading_sizes.most_common(12)),
    }


def read_docx(path: Path) -> dict:
    try:
        import docx
    except ImportError:
        sys.exit("Need python-docx:  pip install python-docx")

    d = docx.Document(str(path))
    fonts, texts, styles = Counter(), [], Counter()
    for para in d.paragraphs:
        if para.text.strip():
            texts.append(para.text)
            styles[para.style.name if para.style else "?"] += 1
        for run in para.runs:
            if run.font is not None and run.font.name:
                fonts[run.font.name] += 1
    for tbl in d.tables:
        for row in tbl.rows:
            texts.append(" | ".join(c.text.strip() for c in row.cells))

    text = "\n".join(texts)
    fclass = Counter(classify_font(n) for n in fonts)
    prof = text_profile(text)
    bucket, why = classify_page(prof, fclass, 0.0)
    return {
        "total_pages": None,
        "sampled": ["whole document"],
        "pages": [{
            "page": 1, "bucket": bucket, "reason": why,
            "image_coverage": 0.0, "image_dpi_estimate": None,
            "fonts": dict(fclass), **prof,
            "bijoy_score": bijoy_score(prof),
            "text_sample": text.strip()[:180],
        }],
        "joined_text": text,
        "font_names": dict(fonts.most_common(40)),
        "paragraph_styles": dict(styles.most_common(15)),
        "table_count": len(d.tables),
    }


def read_txt(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    prof = text_profile(text)
    bucket, why = classify_page(prof, {}, 0.0)
    return {
        "total_pages": None,
        "sampled": ["whole file"],
        "pages": [{
            "page": 1, "bucket": bucket, "reason": why,
            "image_coverage": 0.0, "image_dpi_estimate": None,
            "fonts": {}, **prof, "bijoy_score": bijoy_score(prof),
            "text_sample": text.strip()[:180],
        }],
        "joined_text": text,
        "font_names": {},
    }


# --------------------------------------------------------------------------
# Verdict + report
# --------------------------------------------------------------------------

NEXT_STEPS = {
    "UNICODE_TEXT": [
        "No OCR needed. Do NOT run Tesseract on this - you would be",
        "degrading text you already have.",
        "1. NFC-normalise, strip ZWJ/ZWNJ, unify digit systems.",
        "2. Detect headings from the font-size histogram below + heading regex.",
        "3. Chunk on danda-delimited sentence boundaries within sections.",
    ],
    "LEGACY_ANSI": [
        "This is a FONT ENCODING problem, not an OCR problem.",
        "1. pip install unicodeconverter   (or use rabiulislam-xyz/",
        "   bijoy-to-unicode-converter-python for zero deps)",
        "2. Convert one page, eyeball it, then diff a second converter",
        "   against the first - disagreements mark the glyph-map gaps.",
        "3. Verify conjuncts (ক্ষ ঞ্জ ন্ত্র) and reph placement by hand.",
        "4. Then treat as UNICODE_TEXT. Font sizes survive, so heading",
        "   detection is easy - this bucket is the good outcome.",
    ],
    "SCANNED": [
        "You need OCR. Do NOT pick an engine by reputation.",
        "1. Hand-transcribe 5 gold pages (dense prose, a rate table, a",
        "   numbered clause list, worst scan, mixed Bangla/English).",
        "2. python ocr_bench.py init --gold ocr_gold",
        "3. python ocr_bench.py render manual.pdf --gold ocr_gold --pages ...",
        "4. python ocr_bench.py run --gold ocr_gold --engines tesseract,gemini",
        "5. Pick on DIGIT F1 first, table F1 second, CER last.",
    ],
    "LATIN_TEXT": [
        "Sampled pages look like English. Either you sampled front matter,",
        "or the manual is in English. Re-run with --pages pointing at",
        "Bangla body pages.",
    ],
    "MIXED": [
        "Different pages fall in different buckets - common when a manual",
        "was revised across years. Route per-page, not per-document:",
        "convert LEGACY pages, OCR SCANNED pages, pass UNICODE through.",
        "Record the bucket as chunk metadata so you can trace quality later.",
    ],
    "UNKNOWN": [
        "Inconclusive. Run --dump-text and look at the samples yourself,",
        "and check `pdffonts manual.pdf` - a non-embedded font with a",
        "custom encoding is a strong legacy-encoding signal.",
    ],
}


def verdict(pages: list[dict]) -> tuple[str, Counter]:
    counts = Counter(p["bucket"] for p in pages)
    real = Counter({k: v for k, v in counts.items() if k != "EMPTY"})
    if not real:
        return "UNKNOWN", counts
    top, top_n = real.most_common(1)[0]
    total = sum(real.values())
    if top_n / total < 0.7 and len(real) > 1:
        return "MIXED", counts
    return top, counts


def report(res: dict, path: Path) -> str:
    L: list[str] = []
    add = L.append
    add("=" * 74)
    add(f"BUCKET TEST  ::  {path.name}")
    if res.get("total_pages"):
        add(f"{res['total_pages']} pages, sampled: {res['sampled']}")
    add("=" * 74)

    add("")
    add(f"{'pg':>5} {'bucket':<14} {'bn%':>6} {'ascii%':>7} {'bijoy':>6} "
        f"{'img%':>6} {'dpi':>5}")
    add("-" * 74)
    for p in res["pages"]:
        add(f"{str(p['page']):>5} {p['bucket']:<14} "
            f"{p['bengali_ratio'] * 100:>5.1f}% "
            f"{p['ascii_letter_ratio'] * 100:>6.1f}% "
            f"{p['bijoy_score']:>6.2f} "
            f"{p['image_coverage'] * 100:>5.0f}% "
            f"{(p['image_dpi_estimate'] or '-'):>5}")
        add(f"        why: {p['reason']}")

    v, counts = verdict(res["pages"])
    add("")
    add("=" * 74)
    add(f"VERDICT: {v}      (page buckets: {dict(counts)})")
    add("=" * 74)
    for line in NEXT_STEPS.get(v, []):
        add("  " + line)

    if v == "SCANNED":
        dpis = [p["image_dpi_estimate"] for p in res["pages"]
                if p["image_dpi_estimate"]]
        if dpis:
            add("")
            add(f"  Scan resolution: {min(dpis)}-{max(dpis)} DPI estimated.")
            if min(dpis) < 250:
                add("  WARNING: below ~250 DPI, Bengali matras and the nukta")
                add("  dot are the first things lost. Re-scan at 300-400 DPI")
                add("  if the source is available - cheaper than fixing OCR.")

    if res.get("font_names"):
        add("")
        add("FONTS SEEN (name -> classification)")
        for name, n in list(res["font_names"].items())[:15]:
            add(f"  {classify_font(name):<8} {name}  ({n})")

    if res.get("font_size_histogram"):
        add("")
        add("FONT SIZE HISTOGRAM  (distinct clusters => headings detectable)")
        for size, n in res["font_size_histogram"].items():
            add(f"  {size:>6}pt  {'#' * min(n // 8 + 1, 46)}  {n}")

    if res.get("paragraph_styles"):
        add("")
        add("DOCX PARAGRAPH STYLES (use these as your heading tree)")
        for s, n in res["paragraph_styles"].items():
            add(f"  {n:>5}  {s}")

    text = res["joined_text"]
    if sum(1 for c in text if _is_bengali(c)) > 50:
        ns = normalisation_stats(text)
        add("")
        add("NORMALISATION / CHUNKING HYGIENE")
        add(f"  already NFC ............ {ns['already_nfc']}  "
            f"({ns['chars_changed_by_nfc']} chars differ)")
        add(f"  ZWNJ / ZWJ ............. {ns['zwnj_count']} / {ns['zwj_count']}")
        add(f"  danda (U+0964) ......... {ns['danda_count']}")
        add(f"  danda variant (U+09F7) . {ns['danda_variant_count']}")
        add(f"  full stops ............. {ns['period_count']}")
        add(f"  hasant (conjuncts) ..... {ns['hasant_count']}")
        add(f"  Bengali / ASCII digits . {ns['bengali_digits']} / "
            f"{ns['ascii_digits']}")
        if not ns["already_nfc"]:
            add("  -> NFC-normalise before hashing or you WILL get duplicate")
            add("     chunks that differ only in composition form.")
        if ns["mixed_digit_systems"]:
            add("  -> Mixed digit systems. Index ASCII-folded digits, keep the")
            add("     original in display_text, or '৫০,০০০' never matches '50000'.")
        if ns["danda_count"] < 3:
            add("  -> Almost NO danda in Bengali text. Something dropped it:")
            add("     extraction, font mapping, or OCR. A danda-based sentence")
            add("     splitter will return the whole page as one sentence, and")
            add("     your chunker will silently fall back to fixed-size splits.")
            add("     Diagnose this BEFORE chunking - it is the failure mode")
            add("     that looks like it worked.")
            if ns["period_count"] > 20:
                add("     (Full stops present, so the text may be Latin-")
                add("      punctuated - split on '.' and '।' both.)")
        if ns["danda_count"] >= 3:
            add(f"  -> Split sentences on danda. Expect ~{ns['danda_count']} "
                "sentences in the sampled text.")
    else:
        add("")
        add("(Normalisation stats skipped - not enough Bengali text found.)")

    add("")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Diagnose a Bangla document before building a RAG pipeline.")
    ap.add_argument("path", type=Path, help="PDF, DOCX, or TXT")
    ap.add_argument("--pages", help="1-based pages to sample, e.g. 12,40,88")
    ap.add_argument("--sample", type=int, default=6,
                    help="how many pages to auto-sample (default 6)")
    ap.add_argument("--json", type=Path, help="write full machine-readable report")
    ap.add_argument("--dump-text", type=Path,
                    help="directory to write sampled raw text for eyeballing")
    a = ap.parse_args()

    if not a.path.exists():
        sys.exit(f"Not found: {a.path}")

    want = None
    if a.pages:
        want = [int(x) for x in re.split(r"[,\s]+", a.pages) if x.strip()]

    ext = a.path.suffix.lower()
    if ext == ".pdf":
        res = read_pdf(a.path, want, a.sample)
    elif ext in (".docx", ".dotx"):
        res = read_docx(a.path)
    elif ext in (".txt", ".md"):
        res = read_txt(a.path)
    else:
        sys.exit(f"Unsupported: {ext}. Use .pdf, .docx, or .txt")

    print(report(res, a.path))

    if a.dump_text:
        a.dump_text.mkdir(parents=True, exist_ok=True)
        out = a.dump_text / f"{a.path.stem}_sampled.txt"
        out.write_text(res["joined_text"], encoding="utf-8")
        print(f"Raw sampled text -> {out}")
        print("Open it in a Bengali-capable editor and READ it. No metric")
        print("substitutes for looking at your own data.")

    if a.json:
        payload = {k: v for k, v in res.items() if k != "joined_text"}
        payload["verdict"] = verdict(res["pages"])[0]
        payload["source_file"] = str(a.path)
        a.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        print(f"JSON report -> {a.json}")


if __name__ == "__main__":
    main()