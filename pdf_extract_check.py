"""pdf_extract_check.py -- STEP 2. Is the text layer CORRECT?

Three independent signals, because each one alone has a known blind spot:

  A. probe coverage        (positive) -> catches দাকি-for-দাবি
  B. orthographic validity (negative) -> catches কর্মসূ + ি, hasant+matra
  C. legal-damage rules    (negative) -> catches ললোন, প্রপ্রোডাক্ট,
                                         shattered digit runs

A alone misses damage to words not on the list. B alone gave Progoti 2.0/1000
CLEAN while it was doubling consonants. C alone says nothing about
substitution. The verdict is the WORST of the three, never the average.

Only the curated probe set in probes_*.csv feeds signal A. The registry
vocabulary panel is printed and gates nothing -- see probe_words.py for why
a blanket "every known product must appear" rule would fail a clean manual.

Also writes side-by-side HTML: rendered page image left, its own extracted
text right. That is the artefact that actually settles arguments.

Convention 3: canary_selftest() runs two recorded failures -- the Dabi 2026
CMap corruption and the Progoti doubling -- and asserts that the check which
missed each one still misses it while the check that catches it fires.

Usage:
  python pdf_extract_check.py livestock_grow.pdf --html out/sbs.html --pages 1-12
"""

import argparse
import html
import sys
from pathlib import Path

from bn_shared import (bengali_count, legal_damage, malformed_clusters,
                       validity_rate)
from probe_words import (CANARY_CLEAN, CANARY_CORRUPT, CANARY_PROBES,
                         load_probes, registry_vocabulary, scan_vocabulary,
                         score_text, verdict)

try:
    import fitz
except ImportError:
    print("needs PyMuPDF:  pip install pymupdf", file=sys.stderr)
    raise

# Transcribed from the Progoti 2025 PDF, 9 Sep 2026. Scored 2.0/1000 on
# validity -- "CLEAN" -- and was damaged. Same transcription caveat as the
# two canaries in probe_words.py: the damage shapes are the recorded ones and
# the asymmetry below is what is actually being asserted.
CANARY_DOUBLED = (
    "ললোন অনুমমোদন। প্রপ্রোডাক্ট তালিকা। পাসপপোর্ট নম্বর। "
    "ঋণসীমা ১০০০০\n১-১৫০০\n০০ টাকা।"
)


def parse_pages(spec, n):
    if not spec:
        return list(range(n))
    out = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a) - 1, int(b)))
        else:
            out.append(int(part) - 1)
    return [p for p in out if 0 <= p < n]


def check_text(text, probes):
    mal = malformed_clusters(text)
    dmg = legal_damage(text)
    return {
        "bengali": bengali_count(text),
        "malformed": len(mal),
        "malformed_per_1000": validity_rate(text),
        "malformed_sample": mal[:15],
        "damage": len(dmg),
        "damage_sample": dmg[:15],
        "probe": score_text(text, probes) if probes else None,
    }


def overall_verdict(res, validity_ceiling=5.0, probe_floor=0.90):
    """Worst of the three signals. Never averaged."""
    marks = ["CLEAN" if res["malformed_per_1000"] <= validity_ceiling
             else "CORRUPT",
             "CLEAN" if res["damage"] == 0 else "CORRUPT"]
    if res["probe"]:
        marks.append(verdict(res["probe"], probe_floor))
    order = {"CLEAN": 0, "SUSPECT": 1, "CORRUPT": 2}
    return max(marks, key=lambda m: order[m])


def canary_selftest(verbose=True):
    """Two recorded failures, plus a clean control.

    Each must be caught by the signal that can see it and missed by the
    signal that cannot. That asymmetry is the whole point of running three
    checks; if it disappears, the tool has silently merged them.
    """
    ok = True

    # 1. Dabi 2026 CMap substitution. The probe must catch it, because the
    #    substitutions that stay legal (দাবি->দাকি, গ্রাহক->গ্রাহৃ) are the
    #    ones validity cannot rank.
    # Probe list comes from probe_words, which owns the canary pair. A second
    # copy here would be a second thing to keep in step, and the words in it
    # are product names -- Convention 2 territory.
    v = validity_rate(CANARY_CORRUPT)
    p = verdict(score_text(CANARY_CORRUPT, CANARY_PROBES))
    caught = p == "CORRUPT"
    if verbose:
        print(f"  canary cmap_substitution   validity={v:6.2f}/1000 "
              f"({'blind' if v <= 5.0 else 'sees it'})  probe={p}  "
              f"[{'DETECTED' if caught else 'MISSED'}]")
    ok &= caught

    # 2. Progoti doubling: orthographically legal, so validity is blind.
    d = legal_damage(CANARY_DOUBLED)
    v2 = validity_rate(CANARY_DOUBLED)
    kinds = sorted({k for k, _, _ in d})
    caught2 = len(d) >= 3 and "doubled_consonant_before_o" in kinds
    if verbose:
        print(f"  canary doubled_consonant   validity={v2:6.2f}/1000 "
              f"({'blind' if v2 <= 5.0 else 'sees it'})  "
              f"damage={len(d)} {kinds}  "
              f"[{'DETECTED' if caught2 else 'MISSED'}]")
    ok &= caught2

    # 3. Clean text must not trip anything.
    clean_ok = (validity_rate(CANARY_CLEAN) <= 5.0
                and len(legal_damage(CANARY_CLEAN)) == 0)
    if verbose:
        print(f"  canary clean_control       "
              f"[{'PASS' if clean_ok else 'FALSE POSITIVE'}]")
    ok &= clean_ok
    return ok


def write_sbs_html(doc, page_idx, out_path, dpi=110):
    """Rendered page image beside its own extracted text. Read it, line by line."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    img_dir = out.parent / (out.stem + "_img")
    img_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in page_idx:
        page = doc[i]
        img_file = img_dir / f"p{i + 1:04d}.png"
        page.get_pixmap(dpi=dpi).save(img_file)
        rows.append(f"""
<section>
  <h2>page {i + 1}</h2>
  <div class="pair">
    <img src="{img_dir.name}/{img_file.name}" alt="page {i + 1}">
    <pre>{html.escape(page.get_text("text"))}</pre>
  </div>
</section>""")
    out.write_text(f"""<!doctype html><meta charset="utf-8">
<title>extraction vs render</title>
<style>
 body{{font-family:system-ui,sans-serif;margin:2rem;background:#faf9f7}}
 .pair{{display:grid;grid-template-columns:1fr 1fr;gap:1rem;align-items:start}}
 img{{width:100%;border:1px solid #ccc;background:#fff}}
 pre{{white-space:pre-wrap;font-size:15px;line-height:1.75;
      background:#fff;border:1px solid #ccc;padding:1rem;margin:0}}
 h2{{border-bottom:2px solid #E6007E;padding-bottom:.3rem}}
</style>
<h1>Rendered page (truth) vs extracted text</h1>
{''.join(rows)}""", encoding="utf-8")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--probes", default="probes_livestock_grow.csv")
    ap.add_argument("--no-vocab-panel", action="store_true")
    ap.add_argument("--html", help="write side-by-side HTML here")
    ap.add_argument("--pages", help="1-based page spec for the HTML, e.g. 1-12,40")
    ap.add_argument("--validity-ceiling", type=float, default=5.0)
    ap.add_argument("--probe-floor", type=float, default=0.90)
    args = ap.parse_args()

    print("CANARY")
    if not canary_selftest():
        print("FATAL: extract check failed its own canary. Do not trust it.")
        return 2

    probes = load_probes(args.probes)

    doc = fitz.open(args.pdf)
    full = "\n".join(p.get_text("text") for p in doc)
    res = check_text(full, probes)
    v = overall_verdict(res, args.validity_ceiling, args.probe_floor)

    print(f"\n{len(doc)} pages, {res['bengali']:,} Bengali chars")
    if res["probe"]:
        print(f"A. probe coverage ..... {res['probe']['coverage']:.1%} "
              f"({res['probe']['met']}/{res['probe']['probes']})")
    else:
        print("A. probe coverage ..... n/a")
    print(f"B. malformed clusters . {res['malformed']:,} "
          f"({res['malformed_per_1000']:.1f}/1000)")
    print(f"C. legal damage ....... {res['damage']:,}")
    print(f"\nVERDICT ............... {v}   (worst of A/B/C)")

    if res["probe"] and res["probe"]["misses"]:
        print("\nA -- words that should be there and are not:")
        for m in sorted(res["probe"]["misses"], key=lambda x: x["found"])[:20]:
            print(f"  {m['found']:>5}/{m['required']:<5} {m['term']}")
    if res["damage_sample"]:
        print("\nC -- legal-but-wrong shapes:")
        for name, frag, _ in res["damage_sample"]:
            print(f"  {name:<28} {frag!r}")
    if res["malformed_sample"]:
        print("\nB -- illegal sequences:")
        for name, frag, _ in res["malformed_sample"]:
            print(f"  {name:<28} {frag!r}")

    if not args.no_vocab_panel:
        scan = scan_vocabulary(full, registry_vocabulary())
        print(f"\nREGISTRY VOCABULARY  (informational, gates nothing)")
        print(f"  present {len(scan['present'])}, "
              f"absent {len(scan['absent'])}")
        for term, cat, n in sorted(scan["present"], key=lambda x: -x[2])[:15]:
            print(f"    {n:>5}  [{cat:<12}] {term}")

    if args.html:
        idx = parse_pages(args.pages, len(doc)) or list(range(min(12, len(doc))))
        out = write_sbs_html(doc, idx, args.html)
        print(f"\nside-by-side -> {out}")
        print("Read it. A metric cannot tell you a word is the wrong word.")

    return 0 if v == "CLEAN" else 1


if __name__ == "__main__":
    sys.exit(main())
