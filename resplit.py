import sys, json, re, unicodedata
from collections import Counter
from pathlib import Path

_SRC = Path(sys.argv[1] if len(sys.argv) > 1 else "data/processed/chunks_progoti.jsonl")
_TARGET = int(sys.argv[2]) if len(sys.argv) > 2 else 800
from bnnorm import fold_digits as fold   # one fold table, shared -- see bnnorm.py
NUM = re.compile(r"[\d০-৯][\d০-৯,\.]*")
UNUM = re.compile(r"([\d০-৯][\d০-৯,\.]*)\s*(%|শতাংশ|টাকা|মাস|বছর|দিন|হাজার|লক্ষ|কোটি|কিস্তি)")
nm = lambda s: re.sub(r"[ \t]+", " ", unicodedata.normalize("NFC", s or "")).strip()

def serial(rows, cap=""):
    if not rows: return ""
    h, out = list(rows[0]), ([nm(cap)] if cap else [])
    for r in rows[1:]:
        p = [f"{nm(a)}: {nm(b)}" if nm(a) else nm(b) for a, b in zip(h, r) if nm(b)]
        if p: out.append(" — ".join(p) + " \u0964")
    return "\n".join(out)

UNRECOVERED = []

def recover_caption(c):
    """display_text = headingline + caption + rowlines. Peel back exactly.

    display_text was written as nm(head + "\\n" + body), so the heading must be
    normalised before comparing -- heading_path still holds raw tabs and
    pre-NFC ড়/য়, which made a raw startswith() miss and re-prepend the heading.
    """
    body = c["display_text"]
    head = nm(" > ".join(c["heading_path"]))
    if head and body.startswith(head):
        body = body[len(head):].lstrip("\n")
    rt = serial(c["table_rows"], "")
    if rt and body.endswith(rt):
        return body[:-len(rt)].strip()
    UNRECOVERED.append(c["chunk_id"])  # caption cannot be isolated -> say so
    return ""


def main(SRC, TARGET):
    chunks = [json.loads(l) for l in SRC.open(encoding="utf-8")]
    sizes_before = sorted(c["n_chars"] for c in chunks)
    big = [c for c in chunks if c["n_chars"] > 2000]
    print("=" * 72)
    print(f"OVERSIZED CHUNKS BEFORE: {len(big)}  "
          f"(holding {sum(c['n_chars'] for c in big) * 100 // max(sum(sizes_before),1)}% of corpus text)")
    for c in sorted(big, key=lambda x: -x["n_chars"])[:8]:
        rows = len(c["table_rows"]) if c.get("table_rows") else 0
        print(f"  {c['chunk_id']}  {c['n_chars']:>6} chars  {c['block_type']:<6} "
              f"rows={rows}  {' > '.join(c['heading_path'])[:52]}")

    out = []
    split_count = 0
    for c in chunks:
        rows = c.get("table_rows")
        if c["block_type"] != "table" or not rows or c["n_chars"] <= TARGET * 1.6:
            out.append(c); continue

        cap = recover_caption(c)
        header = rows[0]
        groups, cur, acc = [], [], 0
        for r in rows[1:]:
            cur.append(r)
            # Budget against the SERIALISED line, not the raw cells: serial() repeats
            # every header label on every row, so a wide table's real size is a
            # multiple of its raw cell sum. Measuring raw cells let wide tables
            # (e.g. progoti_0386) never reach TARGET and pass through unsplit.
            acc += len(serial([header, r], ""))
            if acc >= TARGET:
                groups.append(cur); cur, acc = [], 0
        if cur: groups.append(cur)
        if len(groups) <= 1:
            out.append(c); continue

        split_count += 1
        for i, g in enumerate(groups, 1):
            body = serial([header] + g, cap)
            head = nm(" > ".join(c["heading_path"]))
            full = (head + "\n" + body) if head else body
            nb = nm(body)
            d = dict(c)
            d.update(
                chunk_id=f"{c['chunk_id']}_p{i}",
                parent_chunk_id=c["chunk_id"],
                part=f"{i}/{len(groups)}",
                table_rows=[header] + g,
                text=fold(nm(full)), display_text=nm(full), n_chars=len(nm(full)),
                numbers=[fold(m.group(0)).rstrip(".").replace(",", "")
                         for m in NUM.finditer(nb)],
                unit_numbers=[f'{fold(m.group(1)).replace(",", "")} {m.group(2)}'
                              for m in UNUM.finditer(nb)],
            )
            out.append(d)

    sizes_after = sorted(c["n_chars"] for c in out)
    still = [c for c in out if c["n_chars"] > 2000]
    print("\n" + "=" * 72)
    print("RESULT")
    print("=" * 72)
    print(f"  chunks      {len(chunks)} -> {len(out)}   ({split_count} tables split)")
    print(f"  max chars   {sizes_before[-1]} -> {sizes_after[-1]}")
    print(f"  median      {sizes_before[len(sizes_before)//2]} -> "
          f"{sizes_after[len(sizes_after)//2]}")
    print(f"  over 2000   {len(big)} -> {len(still)}")
    if still:
        print("  remaining oversized:")
        for c in still[:8]:
            why = ("prose -- this script only splits tables"
                   if c["block_type"] != "table"
                   else "a single row already exceeds the target")
            print(f"    {c['chunk_id']:<18} {c['n_chars']:>5} chars  {c['block_type']:<6} {why}")
    print(f"  block types {dict(Counter(c['block_type'] for c in out))}")
    if UNRECOVERED:
        print(f"  WARNING: caption could not be isolated for {len(UNRECOVERED)} split "
              f"table(s) -- those parts carry no caption: {UNRECOVERED[:8]}")

    dst = SRC.with_name(SRC.stem.replace("_retagged", "") + "_final.jsonl")
    with dst.open("w", encoding="utf-8") as f:
        for c in out: f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"\n  -> {dst}")
    print("  Check one split table: every part must repeat the header row and")
    print("  the caption, or the pieces stop being self-describing.")


if __name__ == "__main__":
    main(_SRC, _TARGET)
