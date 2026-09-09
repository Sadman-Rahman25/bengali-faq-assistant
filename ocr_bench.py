#!/usr/bin/env python3
"""
ocr_bench.py - Pick a Bangla OCR engine by measurement, not by reputation.

You hand-transcribe 5 pages. This runs every engine against them and scores:

  CER / WER         character + word error rate (normalised, markdown stripped)
  DIGIT F1          numeric tokens only. THE metric for a policy manual - a
                    wrong loan ceiling is worse than ten wrong adjectives,
                    and aggregate CER hides digit errors completely.
  TABLE F1          table cell recall/precision, if your truth uses | pipes
  HEADING F1        markdown headings recovered - measures structure survival
  ORDER tau         Kendall tau on line order. Catches two-column pages being
                    interleaved, which destroys chunking even at 0% CER.

Every (engine, page) result is cached to disk, so re-runs are free and you can
add an engine later without re-paying for the ones already done.

Workflow
--------
    python ocr_bench.py init   --gold ocr_gold
    python ocr_bench.py render --gold ocr_gold manual.pdf --pages 12,45,88,101,150
    #   ... now hand-transcribe ocr_gold/truth/page_*.txt ...
    python ocr_bench.py run    --gold ocr_gold --engines tesseract,gemini

Optional preprocessing ablation:
    python ocr_bench.py preprocess --gold ocr_gold
    python ocr_bench.py run --gold ocr_gold --engines tesseract \\
        --pages-dir pages_prep --tag prep

Engines
-------
  tesseract   pip install pytesseract pillow ; apt install tesseract-ocr-ben
  easyocr     pip install easyocr
  paddle      pip install paddleocr
  gemini      pip install google-genai ; export GEMINI_API_KEY=...
  cmd         any external tool, incl. bbOCR:
              --engine-cmd "python -m bbocr.run --in {img} --out {out}"

Install (minimum)
-----------------
    pip install pymupdf pillow
    pip install rapidfuzz     # optional, ~40x faster scoring
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shlex
import subprocess
import sys
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

try:
    from rapidfuzz.distance import Levenshtein as _RF
    HAVE_RF = True
except ImportError:
    HAVE_RF = False

BN_DIGITS = "০১২৩৪৫৬৭৮৯"
DIGIT_MAP = {ord(c): str(i) for i, c in enumerate(BN_DIGITS)}
ZW = re.compile("[\u200c\u200d]")

TRANSCRIBE_PROMPT = """Transcribe this document page to Markdown, exactly as printed.

Rules:
- Bengali stays Bengali. Do NOT translate, romanise or paraphrase anything.
- Headings become # / ## / ### matching the visual hierarchy.
- Tables become Markdown pipe tables with the header row preserved.
- Keep numbers, dates and currency amounts EXACTLY as printed, including the
  original digit forms (Bengali or ASCII) and punctuation.
- If a character or number is genuinely unreadable, write [?]. Never guess a
  value. An honest [?] is far more useful than a plausible invention.
- Output only the transcription. No commentary, no code fences.
"""

README = """# OCR gold set

## Pick 5 pages that span the document's variety

1. Dense body prose            - the common case
2. A rate / eligibility table   - highest-value content for an FAQ bot
3. A numbered clause list       - tests structure + numbering recovery
4. The WORST scan you can find  - your floor, not your average
5. A mixed Bangla + English page - tests script switching

## Writing truth/page_XX.txt

Transcribe what is PRINTED, not what should be there.

- `#`, `##`, `###` for headings, matching visual hierarchy.
- Markdown pipe tables for tables, header row included.
- Numbers exactly as printed - same digit system, same punctuation.
- `[?]` for genuinely illegible characters.
- Do not fix the source document's own typos.

Budget ~30-40 min per page. This is slow and there is no shortcut: every
number downstream is measured against these files, so an error here makes
the whole benchmark lie to you.

## Then

    python ocr_bench.py run --gold . --engines tesseract,gemini

Read DIGIT F1 first, TABLE F1 second, CER last.
"""


# ------------------------------------------------------------------ metrics

def _lev(a: str, b: str) -> int:
    if HAVE_RF:
        return _RF.distance(a, b)
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _ratio(a: str, b: str) -> float:
    if HAVE_RF:
        return _RF.normalized_similarity(a, b)
    return SequenceMatcher(None, a, b).ratio()


def strip_markdown(t: str) -> str:
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"^\s*[-*+]\s+", "", t, flags=re.M)
    t = re.sub(r"^\s*\|?\s*[-:| ]{5,}\s*\|?\s*$", "", t, flags=re.M)
    t = t.replace("|", " ")
    t = re.sub(r"[*_`>]", "", t)
    return t


def normalise(t: str, fold_digits: bool = False) -> str:
    """Canonicalise so we measure recognition, not encoding differences."""
    t = unicodedata.normalize("NFC", t)
    t = ZW.sub("", t)
    t = t.replace("\u09f7", "\u0964")          # dari variant -> danda
    t = t.replace("\u2018", "'").replace("\u2019", "'")
    t = t.replace("\u201c", '"').replace("\u201d", '"')
    if fold_digits:
        t = t.translate(DIGIT_MAP)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def cer_wer(truth: str, hyp: str) -> tuple[float, float]:
    T = normalise(strip_markdown(truth))
    H = normalise(strip_markdown(hyp))
    cer = _lev(T, H) / max(len(T), 1)
    tw, hw = T.split(), H.split()
    wer = _lev_tokens(tw, hw) / max(len(tw), 1)
    return round(cer, 4), round(wer, 4)


def _lev_tokens(a: list, b: list) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def _prf(truth_items: list[str], hyp_items: list[str]) -> dict:
    """Multiset precision / recall / F1."""
    from collections import Counter
    T, H = Counter(truth_items), Counter(hyp_items)
    tp = sum((T & H).values())
    p = tp / max(sum(H.values()), 1)
    r = tp / max(sum(T.values()), 1)
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"p": round(p, 4), "r": round(r, 4), "f1": round(f, 4),
            "n_truth": sum(T.values()), "n_hyp": sum(H.values()),
            "missing": sorted((T - H).elements())[:12],
            "spurious": sorted((H - T).elements())[:12]}


NUM = re.compile(r"\d[\d,\.\u002F\-]*\d|\d")


def digit_metrics(truth: str, hyp: str) -> dict:
    def toks(t: str) -> list[str]:
        t = normalise(t, fold_digits=True)
        return [m.group(0).replace(",", "").rstrip(".")
                for m in NUM.finditer(t)]
    return _prf(toks(truth), toks(hyp))


def table_metrics(truth: str, hyp: str) -> dict | None:
    def cells(t: str) -> list[str]:
        out = []
        for line in t.splitlines():
            if line.count("|") < 2:
                continue
            if re.fullmatch(r"[\s|:\-]+", line):
                continue
            for c in line.split("|"):
                c = normalise(c, fold_digits=True)
                if c:
                    out.append(c)
        return out
    tc = cells(truth)
    if not tc:
        return None
    return _prf(tc, cells(hyp))


HEAD = re.compile(r"^\s{0,3}(#{1,6})\s+(.*\S)\s*$", re.M)


def heading_metrics(truth: str, hyp: str) -> dict | None:
    def heads(t: str) -> list[str]:
        return [f"{len(m.group(1))}:{normalise(m.group(2))}"
                for m in HEAD.finditer(t)]
    th = heads(truth)
    if not th:
        return None
    return _prf(th, heads(hyp))


def reading_order(truth: str, hyp: str, min_len: int = 20,
                  thresh: float = 0.72) -> dict:
    tl = [normalise(l) for l in truth.splitlines()
          if len(normalise(strip_markdown(l))) >= min_len]
    hl = [normalise(l) for l in hyp.splitlines()
          if len(normalise(strip_markdown(l))) >= min_len]
    if len(tl) < 3 or len(hl) < 3:
        return {"tau": None, "matched": 0, "note": "too few long lines"}

    used, pairs = set(), []
    for ti, t in enumerate(tl):
        best, bi = 0.0, None
        for hi, h in enumerate(hl):
            if hi in used:
                continue
            r = _ratio(t, h)
            if r > best:
                best, bi = r, hi
        if bi is not None and best >= thresh:
            used.add(bi)
            pairs.append((ti, bi))

    if len(pairs) < 3:
        return {"tau": None, "matched": len(pairs),
                "note": "too few confident line matches - CER is probably awful"}

    conc = disc = 0
    for i in range(len(pairs)):
        for j in range(i + 1, len(pairs)):
            s = (pairs[i][0] - pairs[j][0]) * (pairs[i][1] - pairs[j][1])
            conc += s > 0
            disc += s < 0
    tot = conc + disc
    return {"tau": round((conc - disc) / tot, 4) if tot else None,
            "matched": len(pairs), "of_truth_lines": len(tl)}


# ------------------------------------------------------------------ engines

def eng_tesseract(img: Path, cfg: dict) -> str:
    import pytesseract
    from PIL import Image
    return pytesseract.image_to_string(
        Image.open(img),
        lang=cfg.get("lang", "ben+eng"),
        config=cfg.get("config", "--psm 3"),
    )


def eng_easyocr(img: Path, cfg: dict) -> str:
    import easyocr
    key = "_easyocr_reader"
    if key not in cfg:
        cfg[key] = easyocr.Reader(["bn", "en"], gpu=cfg.get("gpu", False))
    res = cfg[key].readtext(str(img), detail=1, paragraph=False)
    res.sort(key=lambda r: (round(r[0][0][1] / 18), r[0][0][0]))
    return "\n".join(r[1] for r in res)


def eng_paddle(img: Path, cfg: dict) -> str:
    from paddleocr import PaddleOCR
    key = "_paddle"
    if key not in cfg:
        cfg[key] = PaddleOCR(use_angle_cls=True, lang=cfg.get("lang", "bn"),
                             show_log=False)
    out = cfg[key].ocr(str(img), cls=True)
    lines = []
    for page in (out or []):
        for det in (page or []):
            try:
                lines.append(det[1][0])
            except Exception:
                pass
    return "\n".join(lines)


def eng_gemini(img: Path, cfg: dict) -> str:
    from google import genai
    from google.genai import types
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError("Set GEMINI_API_KEY")
    ck = "_genai"
    if ck not in cfg:
        cfg[ck] = genai.Client(api_key=key)
    model = cfg.get("model") or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    mime = "image/png" if img.suffix.lower() == ".png" else "image/jpeg"
    r = cfg[ck].models.generate_content(
        model=model,
        contents=[
            types.Part.from_bytes(data=img.read_bytes(), mime_type=mime),
            TRANSCRIBE_PROMPT,
        ],
        config=types.GenerateContentConfig(temperature=0.0),
    )
    return (r.text or "").strip()


def eng_cmd(img: Path, cfg: dict) -> str:
    tmpl = cfg.get("cmd")
    if not tmpl:
        raise RuntimeError("cmd engine needs --engine-cmd")
    out = img.parent.parent / "_cmd_out.txt"
    out.unlink(missing_ok=True)
    filled = tmpl.replace("{img}", str(img)).replace("{out}", str(out))
    res = subprocess.run(shlex.split(filled), capture_output=True, text=True,
                         timeout=cfg.get("timeout", 600))
    if out.exists():
        txt = out.read_text(encoding="utf-8", errors="replace")
        out.unlink(missing_ok=True)
        return txt
    if res.returncode != 0:
        raise RuntimeError(f"cmd failed rc={res.returncode}: {res.stderr[:400]}")
    return res.stdout


ENGINES = {
    "tesseract": eng_tesseract,
    "easyocr": eng_easyocr,
    "paddle": eng_paddle,
    "gemini": eng_gemini,
    "cmd": eng_cmd,
}


# ------------------------------------------------------- subcommand: init

def cmd_init(a) -> None:
    g = a.gold
    for sub in ("pages", "truth", "runs"):
        (g / sub).mkdir(parents=True, exist_ok=True)
    (g / "README.md").write_text(README, encoding="utf-8")
    print(f"Scaffolded {g}/")
    print("  pages/  page images        (fill with `render`)")
    print("  truth/  YOUR transcription (fill by hand - the slow part)")
    print("  runs/   engine outputs, cached")
    print(f"\nRead {g}/README.md for which 5 pages to pick.")


# ----------------------------------------------------- subcommand: render

def cmd_render(a) -> None:
    try:
        import pymupdf as fz
    except ImportError:
        try:
            import fitz as fz
        except ImportError:
            sys.exit("pip install pymupdf")

    pages_dir = a.gold / "pages"
    truth_dir = a.gold / "truth"
    pages_dir.mkdir(parents=True, exist_ok=True)
    truth_dir.mkdir(parents=True, exist_ok=True)

    doc = fz.open(a.pdf)
    nums = [int(x) for x in re.split(r"[,\s]+", a.pages) if x.strip()]
    zoom = a.dpi / 72.0

    for n in nums:
        if not 1 <= n <= doc.page_count:
            print(f"  skip page {n}: out of range (1-{doc.page_count})")
            continue
        pix = doc[n - 1].get_pixmap(matrix=fz.Matrix(zoom, zoom), alpha=False)
        out = pages_dir / f"page_{n:03d}.png"
        pix.save(out)
        print(f"  {out}  {pix.width}x{pix.height}px @ {a.dpi} DPI")
        t = truth_dir / f"page_{n:03d}.txt"
        if not t.exists():
            t.write_text(
                f"<!-- Transcribe page {n} of {Path(a.pdf).name} here.\n"
                "     Headings as #/##/###, tables as | pipes |,\n"
                "     numbers EXACTLY as printed, [?] for illegible.\n"
                "     Delete this comment when done. -->\n",
                encoding="utf-8")
    doc.close()
    print(f"\nNow transcribe the files in {truth_dir}/ by hand.")
    if a.dpi < 300:
        print(f"NOTE: {a.dpi} DPI is low for Bengali - matras and the nukta dot")
        print("are small features. 300-400 DPI is the useful range.")


# ------------------------------------------------- subcommand: preprocess

def cmd_preprocess(a) -> None:
    try:
        import cv2
        import numpy as np
    except ImportError:
        sys.exit("pip install opencv-python-headless numpy")

    src, dst = a.gold / "pages", a.gold / "pages_prep"
    dst.mkdir(parents=True, exist_ok=True)
    imgs = sorted(p for p in src.iterdir()
                  if p.suffix.lower() in (".png", ".jpg", ".jpeg"))
    if not imgs:
        sys.exit(f"No images in {src}")

    for p in imgs:
        g = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        if g is None:
            print(f"  skip {p.name}")
            continue
        # CLAHE: fixes uneven photocopy illumination without nuking thin strokes
        g = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(g)
        # Deskew from the minimum-area rect of dark pixels
        inv = cv2.threshold(g, 0, 255,
                            cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        pts = cv2.findNonZero(inv)
        ang = 0.0
        if pts is not None and len(pts) > 200:
            ang = cv2.minAreaRect(pts)[-1]
            ang = ang - 90 if ang > 45 else ang
            if abs(ang) > 0.25:
                h, w = g.shape
                M = cv2.getRotationMatrix2D((w / 2, h / 2), ang, 1.0)
                g = cv2.warpAffine(g, M, (w, h), flags=cv2.INTER_CUBIC,
                                   borderMode=cv2.BORDER_REPLICATE)
        if a.binarise:
            # Local threshold only. Global Otsu erases Bengali matras on
            # uneven scans, which silently changes vowels -> changes words.
            g = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                      cv2.THRESH_BINARY, 41, 12)
        cv2.imwrite(str(dst / p.name), g)
        print(f"  {p.name}  deskew={ang:+.2f}deg"
              f"{' binarised' if a.binarise else ''}")

    print(f"\n-> {dst}/")
    print("Bench it as an ablation, do not just assume it helped:")
    print(f"  python {Path(sys.argv[0]).name} run --gold {a.gold} "
          "--engines tesseract --pages-dir pages_prep --tag prep")
    if not a.binarise:
        print("\n(Binarisation off by default - it is the main way to destroy")
        print(" Bengali matras. Add --binarise to test it, do not assume it.)")


# -------------------------------------------------------- subcommand: run

def cmd_run(a) -> None:
    pages_dir = a.gold / a.pages_dir
    truth_dir = a.gold / "truth"
    runs_dir = a.gold / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    if not pages_dir.is_dir():
        sys.exit(f"No {pages_dir}/ - run `render` first.")

    imgs = sorted(p for p in pages_dir.iterdir()
                  if p.suffix.lower() in (".png", ".jpg", ".jpeg"))
    jobs = []
    for img in imgs:
        t = truth_dir / f"{img.stem}.txt"
        if not t.exists():
            print(f"  no truth for {img.name} - skipping")
            continue
        txt = t.read_text(encoding="utf-8")
        if "<!--" in txt and len(re.sub(r"<!--.*?-->", "", txt,
                                        flags=re.S).strip()) < 40:
            print(f"  {t.name} is still the placeholder - skipping")
            continue
        jobs.append((img, txt))

    if not jobs:
        sys.exit("No transcribed pages found. Fill in truth/*.txt first - "
                 "that is the whole basis of the benchmark.")

    names = [e.strip() for e in a.engines.split(",") if e.strip()]
    for e in names:
        if e not in ENGINES:
            sys.exit(f"Unknown engine '{e}'. Choose from: "
                     f"{', '.join(ENGINES)}")

    cfg = {"cmd": a.engine_cmd, "lang": a.lang, "model": a.model}
    rows, detail = [], []

    for name in names:
        tag = f"{name}@{a.tag}" if a.tag else name
        out_dir = runs_dir / tag
        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n=== {tag} " + "=" * (58 - len(tag)))

        for img, truth in jobs:
            cache = out_dir / f"{img.stem}.txt"
            if cache.exists() and not a.force:
                hyp, secs, cached = cache.read_text(encoding="utf-8"), 0.0, True
            else:
                t0 = time.time()
                try:
                    hyp = ENGINES[name](img, cfg)
                except Exception as ex:
                    print(f"  {img.name}: FAILED - {type(ex).__name__}: "
                          f"{str(ex)[:120]}")
                    continue
                secs, cached = time.time() - t0, False
                cache.write_text(hyp, encoding="utf-8")

            cer, wer = cer_wer(truth, hyp)
            dig = digit_metrics(truth, hyp)
            tab = table_metrics(truth, hyp)
            hed = heading_metrics(truth, hyp)
            order = reading_order(truth, hyp)

            rows.append({
                "engine": tag, "page": img.stem,
                "cer": cer, "wer": wer,
                "digit_f1": dig["f1"], "digit_recall": dig["r"],
                "table_f1": tab["f1"] if tab else "",
                "heading_f1": hed["f1"] if hed else "",
                "order_tau": order["tau"] if order["tau"] is not None else "",
                "secs": round(secs, 2), "cached": cached,
            })
            detail.append({"engine": tag, "page": img.stem, "cer": cer,
                           "wer": wer, "digits": dig, "table": tab,
                           "headings": hed, "order": order, "secs": secs})

            flag = " [cached]" if cached else ""
            print(f"  {img.stem}: CER {cer:.3f}  WER {wer:.3f}  "
                  f"digitF1 {dig['f1']:.3f}"
                  f"{'  tableF1 %.3f' % tab['f1'] if tab else ''}{flag}")
            if dig["missing"]:
                print(f"      numbers MISSED : {dig['missing']}")
            if dig["spurious"]:
                print(f"      numbers INVENTED: {dig['spurious']}  <-- "
                      "hallucination risk")

    if not rows:
        sys.exit("\nNo engine produced output. Check installs and API keys.")

    print("\n" + "=" * 78)
    print("SUMMARY  (mean across pages)")
    print("=" * 78)
    print(f"{'engine':<18}{'CER':>7}{'WER':>7}{'digF1':>8}{'digRec':>8}"
          f"{'tblF1':>7}{'hdF1':>7}{'tau':>7}{'s/pg':>7}")
    print("-" * 78)

    def mean(vals):
        v = [x for x in vals if x not in ("", None)]
        return sum(v) / len(v) if v else None

    def fmt(x, w=7, d=3):
        return f"{x:>{w}.{d}f}" if x is not None else f"{'-':>{w}}"

    order_out = []
    for tag in dict.fromkeys(r["engine"] for r in rows):
        rr = [r for r in rows if r["engine"] == tag]
        m = {
            "engine": tag,
            "cer": mean(r["cer"] for r in rr),
            "wer": mean(r["wer"] for r in rr),
            "dig": mean(r["digit_f1"] for r in rr),
            "digr": mean(r["digit_recall"] for r in rr),
            "tbl": mean(r["table_f1"] for r in rr),
            "hd": mean(r["heading_f1"] for r in rr),
            "tau": mean(r["order_tau"] for r in rr),
            "secs": mean(r["secs"] for r in rr if not r["cached"]),
        }
        order_out.append(m)
    order_out.sort(key=lambda m: (-(m["dig"] or 0), m["cer"] or 9))

    for m in order_out:
        print(f"{m['engine']:<18}{fmt(m['cer'])}{fmt(m['wer'])}"
              f"{fmt(m['dig'], 8)}{fmt(m['digr'], 8)}{fmt(m['tbl'])}"
              f"{fmt(m['hd'])}{fmt(m['tau'])}"
              f"{fmt(m['secs'], 7, 1)}")

    print("-" * 78)
    print("Sorted by digit F1. Read it in this order:")
    print("  1. digF1  - wrong numbers are the failure that matters most")
    print("  2. tblF1  - rate tables are what field officers actually ask about")
    print("  3. tau    - <0.9 means reading order scrambled; chunking is dead")
    print("             regardless of CER")
    print("  4. hdF1   - '-' means the engine emits no structure, so you will")
    print("             be reconstructing headings by regex")
    print("  5. CER    - last. It is the metric that hides digit errors.")

    best = order_out[0]
    print(f"\nLeader on digits: {best['engine']}")
    if best["dig"] is not None and best["dig"] < 0.97:
        print("WARNING: digit F1 below 0.97. Do NOT index this without a")
        print("numeric verification pass - extract every figure to a table")
        print("and check it against the source page by eye.")
    if best["tau"] is not None and best["tau"] < 0.9:
        print("WARNING: reading order is scrambled. Fix layout handling before")
        print("touching anything else - no chunking strategy survives this.")

    csv_p, json_p = a.gold / "results.csv", a.gold / "results.json"
    with csv_p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    json_p.write_text(json.dumps(
        {"summary": order_out, "per_page": detail,
         "pages_dir": a.pages_dir, "tag": a.tag},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{csv_p}\n{json_p}")
    print(f"Raw engine output kept in {a.gold}/runs/<engine>/ - read the worst")
    print("page by hand. The metrics tell you which engine; only your eyes")
    print("tell you why.")


# ------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Benchmark Bangla OCR engines against hand-written truth.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="scaffold the gold-set folders")
    p.add_argument("--gold", type=Path, default=Path("ocr_gold"))
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("render", help="rasterise gold pages from a PDF")
    p.add_argument("pdf")
    p.add_argument("--gold", type=Path, default=Path("ocr_gold"))
    p.add_argument("--pages", required=True, help="1-based, e.g. 12,45,88")
    p.add_argument("--dpi", type=int, default=350)
    p.set_defaults(fn=cmd_render)

    p = sub.add_parser("preprocess", help="deskew/CLAHE variant for ablation")
    p.add_argument("--gold", type=Path, default=Path("ocr_gold"))
    p.add_argument("--binarise", action="store_true",
                   help="also adaptive-threshold (risky for matras - measure it)")
    p.set_defaults(fn=cmd_preprocess)

    p = sub.add_parser("run", help="run engines and score them")
    p.add_argument("--gold", type=Path, default=Path("ocr_gold"))
    p.add_argument("--engines", default="tesseract",
                   help="comma list: " + ",".join(ENGINES))
    p.add_argument("--pages-dir", default="pages",
                   help="subfolder of --gold holding images")
    p.add_argument("--tag", default="", help="label this run, e.g. prep")
    p.add_argument("--engine-cmd", default="",
                   help="for `cmd` engine; use {img} and {out} placeholders")
    p.add_argument("--lang", default="ben+eng", help="tesseract/paddle lang")
    p.add_argument("--model", default="", help="override vision model name")
    p.add_argument("--force", action="store_true", help="ignore cache")
    p.set_defaults(fn=cmd_run)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()