#!/usr/bin/env python3
"""
page_audit.py - Audit EVERY page of a Bangla manual before you chunk it.

bucket_test.py samples pages to tell you what kind of document you have.
This walks all of them to tell you exactly which pages need special handling,
and builds the heading tree you will chunk along.

Reports
-------
  LEGACY FONT PAGES   pages using SutonnyMJ / Bijoy-style ANSI fonts inside an
                      otherwise-Unicode document. These need glyph conversion
                      or they become gibberish chunks that never retrieve.
  LOW-BENGALI PAGES   English annexes, glossaries, forms.
  IMAGE PAGES         diagrams and flowcharts whose text is NOT in the text
                      layer. Needs separate handling or the content is lost.
  TABLE INVENTORY     where the rate/eligibility tables live - the highest
                      value content for an FAQ assistant.
  HEADING TREE        written to headings_<name>.md. This is your coverage
                      map: record it BEFORE you see the field questions, so
                      thin sections are a pre-registered finding rather than
                      a post-hoc excuse.

Usage
-----
    python page_audit.py data/raw/dabi_manual_2026.pdf
    python page_audit.py data/raw/dabi_manual_2026.pdf --csv audit_dabi.csv
    python page_audit.py data/raw/x.pdf --pages 50-60      # zoom in

Install
-------
    pip install pymupdf
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

BENGALI_LO, BENGALI_HI = 0x0980, 0x09FF
BN_DIGITS = "০১২৩৪৫৬৭৮৯"
DIGIT_FOLD = {ord(c): str(i) for i, c in enumerate(BN_DIGITS)}
DANDA = "\u0964"

LEGACY_FONT_HINTS = (
    "sutonny", "boishakhi", "chandrabati", "modhumati", "shreelipi",
    "bijoy", "proshika", "lekhoni", "rinkiy", "amarbangla", "borno",
)
UNICODE_FONT_HINTS = (
    "solaimanlipi", "nikosh", "kalpurush", "siyamrupali", "siyam", "mukti",
    "notosansbengali", "notoserifbengali", "shonarbangla", "vrinda",
    "akaash", "lohit", "hindsiliguri", "atma", "mina", "bengali", "nirmala",
)

# Bengali structural heading vocabulary, ordered roughly by level.
# NFC-normalised at load: য় ড় ঢ় ো ৌ each have two encodings, and an
# un-normalised literal here silently fails to match a document that uses
# the other form. This is not theoretical - it is the single most common
# way Bengali string matching breaks.
_HEADING_WORDS_RAW = (
    "অধ্যায়", "পরিচ্ছেদ", "খণ্ড", "ভাগ",
    "ধারা", "উপধারা", "অনুচ্ছেদ", "উপঅনুচ্ছেদ",
    "তফসিল", "পরিশিষ্ট", "সংযোজনী", "সংশোধনী",
    "ভূমিকা", "উদ্দেশ্য", "লক্ষ্য", "পরিভাষা",
    "শর্তাবলী", "শর্ত", "যোগ্যতা", "অযোগ্যতা",
    "প্রক্রিয়া", "পদ্ধতি", "নীতিমালা", "নিয়মাবলী",
    "দায়িত্ব", "ক্ষমতা", "অনুমোদন", "প্রতিবেদন",
)
HEADING_WORDS = tuple(unicodedata.normalize("NFC", w)
                      for w in _HEADING_WORDS_RAW)
NUMBERED = re.compile(r"^\s*(?:[০-৯\d]{1,3}\.){1,4}[০-৯\d]{0,3}\s*\S")
BRACKETED = re.compile(r"^\s*\(\s*(?:[ক-হ]|[০-৯\d]{1,3})\s*\)\s*\S")
BOLD_FLAG = 1 << 4


def norm_font(n: str) -> str:
    n = re.sub(r"^[A-Z]{6}\+", "", n or "")          # strip subset prefix
    return re.sub(r"[^a-z0-9]", "", n.lower())


def classify_font(name: str) -> str:
    n = norm_font(name)
    if not n:
        return "unnamed"
    if re.search(r"mj$|mj[0-9]*$|ansi", n) or any(h in n for h in LEGACY_FONT_HINTS):
        return "legacy"
    if any(h in n for h in UNICODE_FONT_HINTS):
        return "unicode"
    return "other"


def bn_ratio(text: str) -> float:
    vis = [c for c in text if not c.isspace()]
    if not vis:
        return 0.0
    return sum(1 for c in vis if BENGALI_LO <= ord(c) <= BENGALI_HI) / len(vis)


def ascii_ratio(text: str) -> float:
    vis = [c for c in text if not c.isspace()]
    if not vis:
        return 0.0
    return sum(1 for c in vis if c.isascii() and c.isalpha()) / len(vis)


def parse_pages(spec: str, total: int) -> list[int]:
    out: set[int] = set()
    for part in re.split(r"[,\s]+", spec):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return sorted(p for p in out if 1 <= p <= total)


def audit(path: Path, want: str | None) -> dict:
    try:
        import pymupdf as fz
    except ImportError:
        try:
            import fitz as fz
        except ImportError:
            sys.exit("pip install pymupdf")

    doc = fz.open(path)
    total = doc.page_count
    idxs = [p - 1 for p in parse_pages(want, total)] if want else range(total)

    pages: list[dict] = []
    size_hist: Counter = Counter()
    all_fonts: Counter = Counter()
    spans_by_page: dict[int, list] = {}

    for i in idxs:
        page = doc[i]
        text = page.get_text()

        fcls: Counter = Counter()
        for f in page.get_fonts(full=True):
            c = classify_font(f[3])
            fcls[c] += 1
            all_fonts[(f[3], c)] += 1

        parea = abs(page.rect.get_area()) or 1.0
        icover, dpis = 0.0, []
        for info in page.get_images(full=True):
            xref = info[0]
            for r in page.get_image_rects(xref) or []:
                icover += abs(r.get_area()) / parea
            try:
                m = doc.extract_image(xref)
                w_in = page.rect.width / 72.0
                if w_in:
                    dpis.append(round(m["width"] / w_in))
            except Exception:
                pass
        icover = min(icover, 1.0)

        spans = []
        try:
            for blk in page.get_text("dict").get("blocks", []):
                for line in blk.get("lines", []):
                    txt = "".join(s.get("text", "") for s in line.get("spans", []))
                    if not txt.strip():
                        continue
                    s0 = line["spans"][0]
                    sz = round(s0.get("size", 0), 1)
                    size_hist[sz] += 1
                    spans.append({
                        "text": txt.strip(),
                        "size": sz,
                        "bold": bool(s0.get("flags", 0) & BOLD_FLAG),
                        "font": s0.get("font", ""),
                        "y": round(line["bbox"][1], 1),
                    })
        except Exception:
            pass
        spans_by_page[i + 1] = spans

        try:
            n_tables = len(page.find_tables().tables)
        except Exception:
            n_tables = None

        folded = text.translate(DIGIT_FOLD)
        pages.append({
            "page": i + 1,
            "chars": len(text.strip()),
            "bn_ratio": round(bn_ratio(text), 3),
            "ascii_ratio": round(ascii_ratio(text), 3),
            "legacy_fonts": fcls.get("legacy", 0),
            "unnamed_fonts": fcls.get("unnamed", 0),
            "images": len(page.get_images()),
            "img_cover": round(icover, 3),
            "img_dpi": max(dpis) if dpis else None,
            "tables": n_tables,
            "danda": text.count(DANDA),
            "periods": text.count("."),
            "bn_digits": sum(text.count(d) for d in BN_DIGITS),
            "ascii_digits": sum(1 for c in text if c.isascii() and c.isdigit()),
            "nfc_clean": unicodedata.normalize("NFC", text) == text,
            "numbers": len(re.findall(r"\d[\d,\.]*", folded)),
        })

    doc.close()
    return {
        "file": str(path), "total_pages": total, "pages": pages,
        "size_hist": dict(size_hist.most_common()),
        "fonts": {f"{n} [{c}]": k for (n, c), k in all_fonts.most_common(40)},
        "spans": spans_by_page,
    }


def body_size(hist: dict) -> float:
    return max(hist.items(), key=lambda kv: kv[1])[0] if hist else 0.0


def find_headings(spans_by_page: dict, body: float) -> list[dict]:
    cand = []
    for pg in sorted(spans_by_page):
        for s in spans_by_page[pg]:
            raw = s["text"]
            if len(raw) > 140 or len(raw) < 3:
                continue
            # NFC before comparing, or composition variants never match.
            t = unicodedata.normalize("NFC", raw)
            word_hit = any(w in t for w in HEADING_WORDS)
            num_hit = bool(NUMBERED.match(t)) or bool(BRACKETED.match(t))
            big = s["size"] >= body + 1.0
            score = (2 if word_hit else 0) + (1 if num_hit else 0) \
                + (1 if big else 0) + (1 if s["bold"] else 0)
            if score >= 2:
                cand.append({"page": pg, "size": s["size"],
                             "bold": s["bold"], "text": raw,
                             "score": score, "word": word_hit})

    # Levels from the actual size hierarchy: largest distinct size = L1.
    sizes = sorted({c["size"] for c in cand}, reverse=True)
    tier = {sz: min(i + 1, 4) for i, sz in enumerate(sizes)}
    for c in cand:
        lvl = tier.get(c["size"], 4)
        # A structural keyword promotes a heading one level.
        c["level"] = max(1, lvl - 1) if c["word"] else lvl
        c.pop("word", None)
    return cand


def report(res: dict, top_n: int = 25) -> str:
    L = []
    add = L.append
    P = res["pages"]
    body = body_size(res["size_hist"])

    add("=" * 76)
    add(f"PAGE AUDIT :: {Path(res['file']).name}")
    add(f"{res['total_pages']} pages, {len(P)} audited")
    add("=" * 76)

    # --- legacy fonts: the important one -------------------------------
    leg = [p for p in P if p["legacy_fonts"]]
    add("")
    add(f"[1] LEGACY (Bijoy/ANSI) FONT PAGES: {len(leg)}")
    if leg:
        add("    These pages mix a legacy ASCII-mapped font into an otherwise")
        add("    Unicode document. Convert JUST these, or they become")
        add("    gibberish chunks that silently never retrieve.")
        add(f"    pages: {[p['page'] for p in leg]}")
        add("")
        add("    Cross-check - high ASCII ratio on those pages means the")
        add("    legacy font actually carries body text (not just a stamp):")
        for p in leg:
            mark = "  <-- LIKELY BIJOY BODY TEXT" if p["ascii_ratio"] > 0.15 else ""
            add(f"      p{p['page']:>4}  bn={p['bn_ratio']:.0%} "
                f"ascii={p['ascii_ratio']:.0%}{mark}")
    else:
        add("    None. Whole document is Unicode - no conversion needed.")

    # --- low-Bengali pages ---------------------------------------------
    low = [p for p in P if p["chars"] > 120 and p["bn_ratio"] < 0.35]
    add("")
    add(f"[2] LOW-BENGALI PAGES (English annex / glossary / forms): {len(low)}")
    for p in low[:20]:
        add(f"      p{p['page']:>4}  bn={p['bn_ratio']:.0%} "
            f"ascii={p['ascii_ratio']:.0%}  {p['chars']} chars")
    if len(low) > 20:
        add(f"      ... +{len(low) - 20} more")
    if low:
        add("    Decide per page: index as English, translate, or exclude.")
        add("    Tag the choice in chunk metadata either way.")

    # --- images ---------------------------------------------------------
    img = [p for p in P if p["img_cover"] > 0.15]
    add("")
    add(f"[3] IMAGE-HEAVY PAGES (diagrams / flowcharts / forms): {len(img)}")
    for p in img[:20]:
        add(f"      p{p['page']:>4}  cover={p['img_cover']:.0%} "
            f"dpi={p['img_dpi']}  text={p['chars']} chars")
    if len(img) > 20:
        add(f"      ... +{len(img) - 20} more")
    if img:
        add("    Text inside these images is NOT in the text layer. Either")
        add("    caption them by hand or run a vision model on just these")
        add("    pages. A flowchart nobody indexed is a silent coverage hole.")

    # --- tables ---------------------------------------------------------
    tp = [p for p in P if (p["tables"] or 0) > 0]
    ntab = sum(p["tables"] or 0 for p in tp)
    add("")
    add(f"[4] TABLES: {ntab} across {len(tp)} pages")
    if tp:
        add("    Highest-value content for an FAQ assistant - rates, ceilings,")
        add("    eligibility. Never split a table across chunks; serialise")
        add("    each row to a sentence and extract a structured record too.")
        rows = ", ".join(f"p{p['page']}({p['tables']})" for p in tp[:24])
        add(f"    {rows}{' ...' if len(tp) > 24 else ''}")
        dense = sorted(P, key=lambda p: -p["numbers"])[:6]
        add("    Most number-dense pages (verify these figures by hand first):")
        add(f"      {[(p['page'], p['numbers']) for p in dense]}")

    # --- structure ------------------------------------------------------
    add("")
    add(f"[5] STRUCTURE  (body text size looks like {body}pt)")
    heads = find_headings(res["spans"], body)
    add(f"    heading candidates: {len(heads)}")
    if heads:
        by_lvl = Counter(h["level"] for h in heads)
        add(f"    by level: {dict(sorted(by_lvl.items()))}")
        add("    sample:")
        for h in heads[:12]:
            add(f"      p{h['page']:>4} L{h['level']} {h['size']:>5}pt  "
                f"{h['text'][:64]}")
    ratio = len(heads) / max(len(P), 1)
    if ratio < 0.4:
        add("    WARNING: under 0.4 headings per page. Structure-aware")
        add("    chunking will be coarse - inspect headings_*.md and extend")
        add("    HEADING_WORDS with this manual's actual vocabulary.")
    elif ratio > 6:
        add("    WARNING: very many candidates - the detector is firing on")
        add("    body text. Tighten the score threshold before chunking.")

    # --- hygiene --------------------------------------------------------
    add("")
    add("[6] HYGIENE TOTALS")
    add(f"    pages already NFC ...... {sum(p['nfc_clean'] for p in P)}/{len(P)}")
    add(f"    danda / full stops ..... {sum(p['danda'] for p in P)} / "
        f"{sum(p['periods'] for p in P)}")
    add(f"    Bengali / ASCII digits . {sum(p['bn_digits'] for p in P)} / "
        f"{sum(p['ascii_digits'] for p in P)}")
    add(f"    numeric tokens total ... {sum(p['numbers'] for p in P)}")
    add(f"    empty / near-empty pages {sum(1 for p in P if p['chars'] < 60)}")
    if sum(p["periods"] for p in P) > 0.3 * max(sum(p["danda"] for p in P), 1):
        add("    -> Mixed punctuation. Split sentences on BOTH danda and '.',")
        add("       but never on '.' inside a number or clause reference.")

    add("")
    add("FONTS")
    for name, n in list(res["fonts"].items())[:14]:
        add(f"    {n:>5}  {name}")

    add("")
    return "\n".join(L)


def write_headings_md(res: dict, out: Path) -> int:
    body = body_size(res["size_hist"])
    heads = find_headings(res["spans"], body)
    lines = [
        f"# Coverage map - {Path(res['file']).name}",
        "",
        f"{res['total_pages']} pages. {len(heads)} heading candidates, "
        f"body text ~{body}pt.",
        "",
        "Mark each section THIN / OK / RICH now, before you see the field",
        "questions. A section marked thin in advance is a finding; the same",
        "note written afterwards is an excuse.",
        "",
        "| page | lvl | coverage | heading |",
        "|---|---|---|---|",
    ]
    for h in heads:
        safe = h["text"].replace("|", "\\|")
        lines.append(f"| {h['page']} | {h['level']} |  | {safe} |")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(heads)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Audit every page of a Bangla manual before chunking.")
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--pages", help="subset, e.g. 50-60 or 12,45,88")
    ap.add_argument("--csv", type=Path, help="per-page metrics CSV")
    ap.add_argument("--json", type=Path, help="full JSON report")
    ap.add_argument("--headings", type=Path,
                    help="heading tree markdown (default headings_<name>.md)")
    a = ap.parse_args()

    if not a.pdf.exists():
        sys.exit(f"Not found: {a.pdf}")

    res = audit(a.pdf, a.pages)
    print(report(res))

    hp = a.headings or Path(f"headings_{a.pdf.stem}.md")
    n = write_headings_md(res, hp)
    print(f"Coverage map ({n} headings) -> {hp}")

    if a.csv:
        with a.csv.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(res["pages"][0].keys()))
            w.writeheader()
            w.writerows(res["pages"])
        print(f"Per-page metrics -> {a.csv}")

    if a.json:
        payload = {k: v for k, v in res.items() if k != "spans"}
        payload["headings"] = find_headings(res["spans"],
                                            body_size(res["size_hist"]))
        a.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        print(f"JSON -> {a.json}")


if __name__ == "__main__":
    main()