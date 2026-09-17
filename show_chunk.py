"""
show_chunk.py — print one chunk in full, with table rows laid out as a table.

For reading a chunk when a flattened quote is ambiguous. candidates.md shows
a one-line excerpt; a serialised table loses its column separators, so
"band A — approver X — band B — approver Y" cannot be unpaired by eye.
table_rows keeps the original structure.

    python show_chunk.py progoti_0062
    python show_chunk.py dabi_2025_0033 dabi_2025_0058
"""

import json
import sys
from pathlib import Path

CORPUS = [
    "data/processed/chunks_dabi_2025_final.jsonl",
    "data/processed/chunks_progoti_final.jsonl",
    "data/processed/chunks_livestock_grow_2026_final.jsonl",
]


def load():
    by_id = {}
    for f in CORPUS:
        p = Path(f)
        if not p.exists():
            print(f"  [warn] not found: {f}", file=sys.stderr)
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                c = json.loads(line)
                by_id[c["chunk_id"]] = c
    return by_id


def show(c):
    print("=" * 74)
    print(f"{c['chunk_id']}   [{c.get('block_type', '?')}]"
          f"   nest_depth={c.get('nest_depth', '-')}")
    print(f"clause : {c.get('clause') or '(none)'}")
    print(f"heading: {c.get('heading_path', '')}")
    print("=" * 74)

    rows = c.get("table_rows")
    if rows:
        print(f"\nTABLE — {len(rows)} row(s). THIS is the pairing that matters:\n")
        for i, r in enumerate(rows):
            cells = r if isinstance(r, list) else [r]
            print(f"  row {i}:")
            for j, cell in enumerate(cells):
                print(f"      col {j} | {str(cell).strip()}")
            print()
    else:
        print("\n(no table_rows — this is prose)\n")

    print("FULL TEXT")
    print("-" * 74)
    print(c.get("display_text") or c.get("text") or "")
    print()


def main():
    ids = sys.argv[1:]
    if not ids:
        print(__doc__)
        return 2
    by_id = load()
    for cid in ids:
        if cid in by_id:
            show(by_id[cid])
        else:
            print(f"\n!! chunk not found: {cid}")
            near = [k for k in by_id if cid.lower() in k.lower()][:5]
            if near:
                print(f"   did you mean: {', '.join(near)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
