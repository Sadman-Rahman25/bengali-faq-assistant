import sys
from collections import Counter
from docx import Document
from docx.oxml.ns import qn


def main(path, targets):
    path = sys.argv[1]
    _targets = sys.argv[2].split(",") if len(sys.argv) > 2 else ["৭০০০০","১০০০০১","সিলিং"]
    d = Document(path); body = d.element.body

    def anc(el):
        out, p = [], el.getparent()
        while p is not None:
            out.append(p.tag.split('}')[-1]); p = p.getparent()
        return out

    def kind(a):
        depth = a.count('tbl')
        if 'txbxContent' in a: return f"TEXTBOX(tbl_depth={depth})"
        if depth == 0: return "paragraph"
        return f"table_depth_{depth}"

    ctx, hits = Counter(), []
    for t in body.iter(qn('w:t')):
        txt = t.text or ""
        a = anc(t); k = kind(a)
        ctx[k] += 1
        for g in targets:
            if g in txt:
                hits.append((g, k, txt.strip()[:70]))

    depths = Counter()
    for tb in body.iter(qn('w:tbl')):
        dd, p = 0, tb.getparent()
        while p is not None:
            if p.tag == qn('w:tbl'): dd += 1
            p = p.getparent()
        depths[dd] += 1

    print("text nodes by container:")
    for k, n in ctx.most_common(): print(f"  {n:>7}  {k}")
    print("\ntables by nesting depth (0 = top level):")
    for k, n in sorted(depths.items()): print(f"  depth {k}: {n} tables")
    print(f"\nsearched for {targets}  -> {len(hits)} hits")
    for g, k, v in hits[:25]: print(f"  {g:<10} {k:<22} {v}")
    if not hits: print("  NONE FOUND anywhere in document.xml")


if __name__ == "__main__":
    main(sys.argv[1], _targets)
