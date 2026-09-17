"""
gold_status.py — read the gold set as a checklist, not as YAML.

Shows every question, what is filled, what is still TODO, and how far
through you are. Run it between questions while writing.

    python3 gold_status.py                      # progress summary
    python3 gold_status.py --show               # full question text
    python3 gold_status.py --block A            # one write-block at a time
    python3 gold_status.py --md out/gold.md     # readable markdown

No side effects beyond --md. Nothing here writes to the gold file.
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

# Write-blocks from the file's own ordering: A needs no figures, B is
# transcription from divergence.md, C needs figures read off the page.
BLOCKS = {
    "A": ["G-209", "G-210", "G-211", "G-217", "G-218"],
    "B": ["G-212", "G-213", "G-214", "G-215", "G-216", "G-221", "G-222"],
    "C": ["G-201", "G-202", "G-203", "G-204", "G-205", "G-206", "G-207",
          "G-208", "G-219", "G-220", "G-223", "G-224"],
}


def block_of(qid):
    for name, ids in BLOCKS.items():
        if qid in ids:
            return name
    return "?"


def todos(obj):
    return json.dumps(obj, ensure_ascii=False, default=str).count("TODO")


def fact_text(f):
    return f["text"] if isinstance(f, dict) else f


def audit(q):
    facts = q.get("gold_facts") or []
    filled = [f for f in facts if "TODO" not in str(fact_text(f))]
    refs = q.get("source_refs") or []
    refs_filled = [r for r in refs
                   if "TODO" not in json.dumps(r, ensure_ascii=False,
                                               default=str)]
    trap = bool(q.get("forbidden_numbers_from_clause")
                or q.get("must_not_contain_literals"))
    return {
        "id": q["id"],
        "block": block_of(q["id"]),
        "type": q.get("type", ""),
        "question": q.get("question_bn", ""),
        "answerable": str(q.get("answerable", "yes")),
        "facts": len(facts),
        "facts_filled": len(filled),
        "refs": len(refs),
        "refs_filled": len(refs_filled),
        "trap": trap,
        "todos": todos(q),
        "status": q.get("verification_status", "unverified"),
        "done": len(filled) == len(facts) and len(refs_filled) == len(refs)
                and len(facts) > 0,
    }


def bar(done, total, width=22):
    n = int(width * done / total) if total else 0
    return "█" * n + "·" * (width - n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gold", nargs="?", default="gold/bootstrap.yaml")
    ap.add_argument("--show", action="store_true",
                    help="print the full Bengali question text")
    ap.add_argument("--block", choices=["A", "B", "C"],
                    help="only this write-block")
    ap.add_argument("--md", help="write a readable markdown view here")
    args = ap.parse_args()

    path = Path(args.gold)
    if not path.exists():
        print(f"not found: {path}", file=sys.stderr)
        return 2

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    rows = [audit(q) for q in data["questions"]]
    if args.block:
        rows = [r for r in rows if r["block"] == args.block]

    done = sum(1 for r in rows if r["done"])
    total = len(rows)
    tds = sum(r["todos"] for r in rows)

    print(f"{path}   {done}/{total} complete   {bar(done, total)}   "
          f"{tds} TODO left\n")

    for bname in ("A", "B", "C"):
        brows = [r for r in rows if r["block"] == bname]
        if not brows:
            continue
        bdone = sum(1 for r in brows if r["done"])
        label = {"A": "no figures needed — forbidden.json + documented gaps",
                 "B": "transcribe from divergence.md",
                 "C": "needs figures read off the page"}[bname]
        print(f"BLOCK {bname}  ({bdone}/{len(brows)})  {label}")
        for r in brows:
            tick = "✓" if r["done"] else " "
            ans = {"no": "UNANSWERABLE", "partial": "partial"}.get(
                r["answerable"], "")
            trap = "" if r["trap"] or r["answerable"] != "no" else "  ⚠ no trap"
            print(f"  [{tick}] {r['id']}  facts {r['facts_filled']}/{r['facts']}"
                  f"  refs {r['refs_filled']}/{r['refs']}"
                  f"   {r['type']:<28} {ans}{trap}")
            if args.show:
                print(f"       {r['question']}")
        print()

    unver = [r for r in rows if r["done"] and r["status"] != "page_checked"]
    if unver:
        print(f"{len(unver)} question(s) filled but not yet page-checked: "
              f"{', '.join(r['id'] for r in unver)}")
        print("Keep --require-render OFF until those are verified against "
              "the printed manual.")

    if args.md:
        out = [f"# Gold questions — {done}/{total} complete\n"]
        for bname in ("A", "B", "C"):
            brows = [r for r in rows if r["block"] == bname]
            if not brows:
                continue
            out.append(f"\n## Block {bname}\n")
            out.append("| | id | question | type | answerable | facts | refs |")
            out.append("|---|---|---|---|---|---|---|")
            for r in brows:
                out.append(
                    f"| {'✓' if r['done'] else ''} | {r['id']} | "
                    f"{r['question']} | {r['type']} | {r['answerable']} | "
                    f"{r['facts_filled']}/{r['facts']} | "
                    f"{r['refs_filled']}/{r['refs']} |")
        Path(args.md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.md).write_text("\n".join(out) + "\n", encoding="utf-8")
        print(f"\nmarkdown -> {args.md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
