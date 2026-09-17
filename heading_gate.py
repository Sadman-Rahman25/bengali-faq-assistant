"""
heading_gate.py -- the missing direction of the orphan-rate check.

Orphan rate detects UNDER-detection: body lines with no heading above them.
It is structurally blind to OVER-detection, because inventing a heading does
not create an orphan -- it creates false structure, and every line underneath
gets a path that looks perfectly well-formed.

That is why CLAUSE_RX reading bare ১. enumerators as clause numbers produced
237 depth-1 headings in Progoti while the orphan rate sat unchanged at 0.04%.
16 chapters, 237 depth-1 headings, and no gate said a word.

Four signals, strongest first:

  1. RESETS   a real chapter spine counts up: ১, ২, ৩ ... Enumerators reset
              constantly, because every list starts again at ১. Counting how
              often the depth-1 sequence fails to increase is the single
              cleanest separator between the two, and it needs no threshold
              tuned to document length.
  2. RATIO    depth-1 headings against lines containing the word অধ্যায়.
              Independent of the numbering regex, which is the point.
  3. DENSITY  headings per page. A manual does not have four chapters a page.
  4. DUPES    the same depth-1 number appearing many times over.

Run standalone or import check_spine() from pdf_structure.py.

Usage:  python3 heading_gate.py out/structure.json --pages 136
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from bn_shared import canon, canon_pattern, fold_digits

# Convention 4. This was re.compile(r"অধ্যায়") with a decomposed য়, matched
# against raw extracted text. Both PDFs emit precomposed U+09DF, so it found
# 0 on every manual and the RATIO signal below was permanently inert -- the
# gate ran on three signals, not four, and nothing said so. Its own canary
# could not see it: _synth writes the text with this same literal, so both
# sides carried the same form.
CHAPTER_WORD_RX = canon_pattern("অধ্যায়")


def _leading_int(number):
    if not number:
        return None
    head = fold_digits(str(number)).split(".")[0]
    try:
        return int(head)
    except ValueError:
        return None


def measure(walked):
    """Compute the four signals from a pdf_structure.py structure.json."""
    heads = [l for l in walked if l.get("is_heading")]
    d1 = [h for h in heads if h["heading"]["depth"] == 1]

    seq = [n for n in (_leading_int(h["heading"].get("number")) for h in d1)
           if n is not None]
    resets = sum(1 for i in range(1, len(seq)) if seq[i] <= seq[i - 1])

    # canon() the text as well as the pattern: index side and query side must
    # call the same normaliser, which is the rule bn_shared exists to enforce.
    chapter_lines = sum(1 for l in walked
                        if CHAPTER_WORD_RX.search(canon(l.get("text", "")))
                        and len(l.get("text", "").strip()) < 90)

    pages = max((l.get("page", 1) for l in walked), default=1)
    dupes = Counter(seq)
    repeated = sum(v for v in dupes.values() if v > 1)

    return {
        "lines": len(walked),
        "pages": pages,
        "headings": len(heads),
        "depth1": len(d1),
        "depth1_numbered": len(seq),
        "resets": resets,
        "reset_rate": resets / max(len(seq) - 1, 1),
        "chapter_word_lines": chapter_lines,
        "headings_per_page": len(heads) / max(pages, 1),
        "depth1_per_page": len(d1) / max(pages, 1),
        "repeated_depth1": repeated,
        "most_common_depth1": dupes.most_common(5),
        "by_source": Counter(h["heading"].get("source") for h in heads),
    }


def check_spine(walked, max_reset_rate=0.10, max_depth1_per_page=0.30,
                max_ratio=3.0):
    """
    Returns (ok, signals, failures). Thresholds are deliberately loose -- this
    gate exists to catch absurdity, not to second-guess a plausible spine.
    """
    s = measure(walked)
    fails = []

    if s["depth1_numbered"] >= 5 and s["reset_rate"] > max_reset_rate:
        fails.append(
            f"depth-1 numbering resets {s['resets']} times "
            f"({s['reset_rate']:.0%} of transitions). A chapter spine counts "
            f"up; list enumerators reset. The numbering regex is almost "
            f"certainly eating bare enumerators -- require a dot, or require "
            f"the number to continue the previous chapter's sequence.")

    if s["chapter_word_lines"] >= 3:
        ratio = s["depth1"] / max(s["chapter_word_lines"], 1)
        if ratio > max_ratio:
            fails.append(
                f"{s['depth1']} depth-1 headings against "
                f"{s['chapter_word_lines']} lines containing অধ্যায় "
                f"(ratio {ratio:.1f}x). The two signals are independent; when "
                f"they disagree this far, the numbering one is wrong.")

    if s["depth1_per_page"] > max_depth1_per_page:
        fails.append(
            f"{s['depth1_per_page']:.2f} depth-1 headings per page. A manual "
            f"does not start a chapter every "
            f"{1 / max(s['depth1_per_page'], 1e-9):.1f} pages.")

    if s["repeated_depth1"] > s["depth1_numbered"] * 0.5 >= 5:
        fails.append(
            f"{s['repeated_depth1']} of {s['depth1_numbered']} depth-1 "
            f"numbers are repeats: {s['most_common_depth1']}")

    return (not fails), s, fails


# --------------------------------------------------------------- Convention 3
# Real historical failure: Progoti 2025, CLAUSE_RX reading bare ১. enumerators
# as clause numbers. 237 depth-1 headings, orphan rate unchanged at 0.04%.
# Recorded 13 Sep 2026.

def _synth(depth1_numbers, pages, chapter_lines=16):
    """Minimal structure.json shape carrying only what measure() reads."""
    walked = []
    for i, n in enumerate(depth1_numbers):
        walked.append({"is_heading": True, "text": f"{n}. শিরোনাম",
                       "page": 1 + (i * pages) // max(len(depth1_numbers), 1),
                       "heading": {"depth": 1, "number": str(n),
                                   "source": "clause_number"}})
    for i in range(chapter_lines):
        walked.append({"is_heading": False,
                       "text": f"{i + 1}ম অধ্যায়: বিষয়", "page": 1 + i})
    for i in range(400):
        walked.append({"is_heading": False, "text": "সাধারণ বাক্য।",
                       "page": 1 + (i * pages) // 400})
    return walked


def canary_selftest(verbose=True):
    # The real shape: chapters 1-16, but every list inside them reset to 1.
    broken_seq = []
    for chapter in range(1, 17):
        broken_seq.append(chapter)
        broken_seq += list(range(1, 15))          # an enumerated list, ১..১৪
    broken = _synth(broken_seq[:237], pages=136)
    ok_b, s_b, f_b = check_spine(broken)

    good = _synth(list(range(1, 17)), pages=136)
    ok_g, s_g, f_g = check_spine(good)

    if verbose:
        print(f"  canary enumerator_as_clause  depth1={s_b['depth1']} "
              f"resets={s_b['resets']} ratio="
              f"{s_b['depth1'] / max(s_b['chapter_word_lines'], 1):.1f}x  "
              f"[{'DETECTED' if not ok_b else 'MISSED'}]")
        print(f"  canary real_spine_control    depth1={s_g['depth1']} "
              f"resets={s_g['resets']}  "
              f"[{'PASS' if ok_g else 'FALSE POSITIVE'}]")
    return (not ok_b) and ok_g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("structure", nargs="?",
                    help="out/structure.json; omit to self-test only")
    ap.add_argument("--max-reset-rate", type=float, default=0.10)
    ap.add_argument("--max-depth1-per-page", type=float, default=0.30)
    ap.add_argument("--max-ratio", type=float, default=3.0)
    args = ap.parse_args()

    print("CANARY")
    if not canary_selftest():
        print("FATAL: heading gate failed its own canary.")
        return 2
    if not args.structure:
        print("\nself-test only")
        return 0

    walked = json.loads(Path(args.structure).read_text(encoding="utf-8"))
    ok, s, fails = check_spine(walked, args.max_reset_rate,
                               args.max_depth1_per_page, args.max_ratio)

    print(f"\nlines ................. {s['lines']:,}  over {s['pages']} pages")
    print(f"headings .............. {s['headings']:,} "
          f"({s['headings_per_page']:.2f}/page)")
    print(f"  depth-1 ............. {s['depth1']:,} "
          f"({s['depth1_per_page']:.2f}/page)")
    print(f"  by source ........... "
          + " / ".join(f"{k}:{v}" for k, v in s["by_source"].items()))
    print(f"depth-1 resets ........ {s['resets']} "
          f"({s['reset_rate']:.0%} of transitions)")
    print(f"lines with অধ্যায় ...... {s['chapter_word_lines']}")
    print(f"repeated depth-1 ...... {s['repeated_depth1']} "
          f"{s['most_common_depth1']}")

    if fails:
        print("\nFAIL: the spine is over-detected.")
        for f in fails:
            print(f"  - {f}")
        print("\nOver-detection does not raise the orphan rate. That check "
              "cannot see this and never could.")
        return 1
    print("\nspine plausible")
    return 0


if __name__ == "__main__":
    sys.exit(main())
