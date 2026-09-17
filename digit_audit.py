"""digit_audit.py -- STEP 6. The highest-consequence check in the PDF path.

A wrong service charge is a wrong answer an officer acts on. PDFs shatter
numbers across line breaks constantly and the result looks plausible:
১০০০০\\n১-১৫০০\\n০০ is really ১০০০০১-১৫০০০০.

Four signals:
  1. digit run adjacent to a line break        (from needs_figure_check)
  2. range whose two endpoints have different digit counts
  3. range where the low endpoint exceeds the high endpoint
  4. money figure with an implausible magnitude for its unit

Nothing here auto-corrects. It produces a worklist and an HTML page so each
flagged figure is read against the rendered page. Auto-joining a shattered
number is how you get a confident wrong ceiling.

READS display_text, NOT text
`text` is canon()'d, so its digits are folded to ASCII and its thousands
separators are stripped. Every figure in this worklist is going to be
compared by eye against a rendered page printed in Bengali numerals, so the
worklist has to show what the page shows.

Convention 3: canary_selftest() runs the recorded shattered ceiling through
audit_chunk() before any real scanning. The pasted draft of this module had
no canary at all, which for the highest-consequence check in the path is the
one place it cannot be skipped.

Usage:
  python digit_audit.py out/chunks.jsonl --pdf livestock_grow.pdf \\
      --html out/figs.html
"""

import argparse
import html
import json
import re
import sys
from pathlib import Path

from bn_shared import BN_DIGITS, fold_digits

RANGE_RX = re.compile(
    rf"([{BN_DIGITS}0-9][{BN_DIGITS}0-9,]*)\s*[-–—]\s*"
    rf"([{BN_DIGITS}0-9][{BN_DIGITS}0-9,]*)"
)
MONEY_RX = re.compile(
    rf"([{BN_DIGITS}0-9][{BN_DIGITS}0-9,]*)\s*(টাকা|%|শতাংশ)"
)

# Plausibility envelopes. Deliberately wide -- this flags absurdity, not
# unusual-but-real values.
ENVELOPE = {
    "টাকা": (100, 100_000_000),
    "%": (0, 100),
    "শতাংশ": (0, 100),
}


def to_int(s):
    try:
        return int(fold_digits(s).replace(",", ""))
    except ValueError:
        return None


def audit_chunk(c):
    """Return [(issue_kind, fragment, why)] for one chunk."""
    issues = []
    t = c.get("display_text") or c.get("text") or ""

    if c.get("needs_figure_check"):
        issues.append(("digit_run_touched_linebreak", "",
                       "source span had a digit run split by a newline"))

    for m in RANGE_RX.finditer(t):
        lo_s, hi_s = m.group(1), m.group(2)
        lo, hi = to_int(lo_s), to_int(hi_s)
        if lo is None or hi is None:
            continue
        dlo = len(fold_digits(lo_s).replace(",", ""))
        dhi = len(fold_digits(hi_s).replace(",", ""))
        if abs(dlo - dhi) >= 2:
            issues.append(("range_digit_count_mismatch", m.group(0),
                           f"{dlo} digits vs {dhi} -- a shattered figure "
                           f"looks exactly like this"))
        elif lo > hi:
            issues.append(("range_inverted", m.group(0),
                           f"low {lo} > high {hi}"))

    for m in MONEY_RX.finditer(t):
        v, unit = to_int(m.group(1)), m.group(2)
        if v is None or unit not in ENVELOPE:
            continue
        lo, hi = ENVELOPE[unit]
        if not (lo <= v <= hi):
            issues.append(("magnitude_implausible", m.group(0),
                           f"{v} outside {lo}-{hi} for {unit}"))
    return issues


def canary_selftest(verbose=True):
    """Convention 3. The recorded shattered ceiling must flag; clean must not."""
    ok = True

    shattered = {
        "display_text": "ঋণসীমা ১০০০০ ১-১৫০০ ০০ টাকা, সার্ভিস চার্জ ২৫০%।",
        "needs_figure_check": True,
    }
    kinds = {k for k, _, _ in audit_chunk(shattered)}
    want = {"digit_run_touched_linebreak", "range_digit_count_mismatch",
            "magnitude_implausible"}
    caught = want <= kinds
    if verbose:
        print(f"  canary shattered_ceiling    {sorted(kinds)}  "
              f"[{'DETECTED' if caught else 'MISSED'}]")
    ok &= caught

    clean = {"display_text": "ঋণসীমা ১০০০০১-১৫০০০০ টাকা, সার্ভিস চার্জ ২৫%।",
             "needs_figure_check": False}
    clean_issues = audit_chunk(clean)
    clean_ok = not clean_issues
    if verbose:
        print(f"  canary clean_control        "
              f"{[k for k, _, _ in clean_issues] or 'no issues'}  "
              f"[{'PASS' if clean_ok else 'FALSE POSITIVE'}]")
    ok &= clean_ok

    # An inverted range must be caught even when both ends are the same width.
    inv = {"display_text": "১৫০০০০-১০০০০১ টাকা", "needs_figure_check": False}
    inv_ok = any(k == "range_inverted" for k, _, _ in audit_chunk(inv))
    if verbose:
        print(f"  canary inverted_range       "
              f"[{'DETECTED' if inv_ok else 'MISSED'}]")
    ok &= inv_ok
    return ok


def write_html(flagged, out_path, pdf_path=None, dpi=110):
    imgs = {}
    if pdf_path:
        try:
            import fitz
            doc = fitz.open(pdf_path)
            img_dir = Path(out_path).parent / (Path(out_path).stem + "_img")
            img_dir.mkdir(parents=True, exist_ok=True)
            for c, _ in flagged:
                p = c.get("page")
                if p and p not in imgs and 1 <= p <= len(doc):
                    f = img_dir / f"p{p:04d}.png"
                    doc[p - 1].get_pixmap(dpi=dpi).save(f)
                    imgs[p] = f"{img_dir.name}/{f.name}"
        except Exception as exc:
            print(f"  [warn] page render unavailable: {exc}", file=sys.stderr)

    cards = []
    for c, issues in flagged:
        img = imgs.get(c.get("page"))
        rows = "".join(
            f"<li><b>{html.escape(k)}</b> "
            f"<code>{html.escape(frag)}</code> -- {html.escape(why)}</li>"
            for k, frag, why in issues)
        body = html.escape((c.get("display_text") or "")[:1800])
        pane = (f'<img src="{img}">' if img
                else '<div class="noimg">no render</div>')
        cards.append(f"""
<article>
  <h3>{html.escape(str(c.get('chunk_id')))} &middot; page {c.get('page')}
      &middot; {html.escape(" > ".join(c.get('heading_path') or []))}</h3>
  <ul>{rows}</ul>
  <div class="pair">{pane}<pre>{body}</pre></div>
  <label><input type="checkbox"> verified against render</label>
</article>""")

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(f"""<!doctype html><meta charset="utf-8">
<title>figure verification worklist</title>
<style>
 body{{font-family:system-ui,sans-serif;margin:2rem;background:#faf9f7}}
 article{{background:#fff;border:1px solid #ddd;padding:1rem;margin:1.5rem 0}}
 h3{{margin:0 0 .5rem;color:#E6007E;font-size:15px}}
 .pair{{display:grid;grid-template-columns:1fr 1fr;gap:1rem}}
 img{{width:100%;border:1px solid #ccc}}
 .noimg{{background:#f0f0f0;display:grid;place-items:center;color:#888}}
 pre{{white-space:pre-wrap;font-size:14px;line-height:1.7;margin:0}}
 code{{background:#fff0f7;padding:1px 4px}}
 label{{display:block;margin-top:.75rem;font-size:14px}}
</style>
<h1>Figures to verify against the rendered page</h1>
<p>{len(flagged)} chunks. Nothing here is auto-corrected. Read each one.</p>
{''.join(cards)}""", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("chunks")
    ap.add_argument("--pdf", help="render pages into the worklist")
    ap.add_argument("--html", default="out/figure_worklist.html")
    ap.add_argument("--fail-on-flag", action="store_true",
                    help="exit non-zero if anything is unverified")
    args = ap.parse_args()

    print("CANARY")
    if not canary_selftest():
        print("FATAL: digit audit failed its own canary. Do not trust it.")
        return 2

    chunks = [json.loads(l) for l in
              Path(args.chunks).read_text(encoding="utf-8").splitlines() if l]

    flagged, counts = [], {}
    for c in chunks:
        issues = audit_chunk(c)
        if issues:
            flagged.append((c, issues))
            for k, _, _ in issues:
                counts[k] = counts.get(k, 0) + 1

    total_figs = sum(len(c.get("numbers") or []) for c in chunks)
    print(f"\nchunks ............. {len(chunks):,}")
    print(f"figures ............ {total_figs:,}")
    print(f"chunks flagged ..... {len(flagged):,}")
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {k:<32} {v}")

    write_html(flagged, args.html, args.pdf)
    print(f"\nworklist -> {args.html}")
    print("Every flagged figure gets read against the render before embedding.")

    if flagged and args.fail_on_flag:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
