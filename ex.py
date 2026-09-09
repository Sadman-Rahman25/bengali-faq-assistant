import sys, os, re, json, csv, unicodedata
from collections import Counter
from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

TAG = sys.argv[2] if len(sys.argv) > 2 else "doc"
OUT = "data/processed"; os.makedirs(OUT, exist_ok=True)
from bnnorm import fold_digits as fold   # one fold table, shared -- see bnnorm.py
ZW = re.compile("[​‌‍﻿]")
NUM = re.compile(r"[\d০-৯][\d০-৯,\.]*")
UNUM = re.compile(r"([\d০-৯][\d০-৯,\.]*)\s*(%|শতাংশ|টাকা|মাস|বছর|দিন|হাজার|লক্ষ|কোটি|কিস্তি)")
PROD = [("progoti",["প্রগতি"]),("bcup",["বিসিইউপি"]),("ncdp",["এনসিডিপি"]),
        ("scdp",["এসসিডিপি"]),("cdp",["সিডিপি"]),("dabi",["দাবি"]),("goti",["গতি"])]

def nm(s):
    s = ZW.sub("", unicodedata.normalize("NFC", s or "")).replace("৷","।")
    return re.sub(r"[ \t ]+"," ",s).strip()
def prods(t):
    t, out = nm(t), []
    for k, vs in PROD:
        for v in vs:
            if v in t: out.append(k); t = t.replace(v," "); break
    return out
def ctext(tc, d):
    return " ".join(Paragraph(c,d).text.strip() for c in tc.iterchildren()
                    if c.tag==qn("w:p") and Paragraph(c,d).text.strip())
def rows_of(tb, d):
    rs = []
    for tr in tb._tbl.tr_lst:
        flat = []
        for tc in tr.tc_lst:
            pr = tc.tcPr; sp = 1; vm = None
            if pr is not None:
                g = pr.find(qn("w:gridSpan"))
                if g is not None: sp = int(g.get(qn("w:val")) or 1)
                v = pr.find(qn("w:vMerge"))
                if v is not None: vm = v.get(qn("w:val")) or "continue"
            t = "" if vm == "continue" else nm(ctext(tc,d)).replace("|","\\|")
            flat.append(re.sub(r"[\r\n]+"," / ",t))
            flat += [""]*(sp-1)
        rs.append(flat)
    return rs
def serial(rs, cap):
    if not rs: return ""
    h, out = list(rs[0]), ([nm(cap)] if cap else [])
    for r in rs[1:]:
        p = [f"{nm(a)}: {nm(b)}" if nm(a) else nm(b) for a,b in zip(h,r) if nm(b)]
        if p: out.append(" — ".join(p) + " ।")
    return "\n".join(out)

blocks, path, st = [], {}, Counter()
def cp(): return [path[k] for k in sorted(path) if path.get(k)]
def etab(tb, cap, nest, i, d):
    rs = rows_of(tb, d)
    if not any(any(c for c in r) for r in rs): return
    st["tables_nested" if nest else "tables"] += 1
    blocks.append(dict(type="table", hp=cp(), rows=rs, cap=cap, nest=nest, ti=i))
    pt = " ".join(c for c in rs[0] if c).strip()
    for tr in tb._tbl.tr_lst:
        for tc in tr.tc_lst:
            for sub in [Table(c,d) for c in tc.iterchildren() if c.tag==qn("w:tbl")]:
                etab(sub, " — ".join(x for x in (cap,pt,ctext(tc,d)[:100]) if x)[:300], True, i, d)

d = Document(sys.argv[1])
ti = 0
for ch in d.element.body.iterchildren():
    if ch.tag == qn("w:p"):
        p = Paragraph(ch, d); sn = p.style.name if p.style else ""; tx = p.text.strip()
        m = re.match(r"Heading (\d)", sn or "")
        if m and tx:
            # Normalise at write time so heading_path matches display_text, which
            # is stored as nm(head + "\n" + body). Kept raw, heading_path carries
            # tabs and pre-NFC ড়/য়, so any consumer that compares, groups or
            # hashes on it silently misses -- see resplit.recover_caption.
            L = int(m.group(1)); tx = nm(tx); path[L] = tx
            for k in [k for k in path if k > L]: path.pop(k)
            st[f"h{L}"] += 1; blocks.append(dict(type="head", lvl=L, text=tx, hp=cp()))
        elif tx:
            pr = p._p.pPr; il = None
            if pr is not None and pr.find(qn("w:numPr")) is not None:
                iv = pr.find(qn("w:numPr")).find(qn("w:ilvl"))
                il = int(iv.get(qn("w:val"))) if iv is not None else 0
            st["paras"] += 1
            blocks.append(dict(type="list" if il is not None else "prose",
                               lvl=il, hp=cp(), text=tx, tb=False))
        for t in ch.iter(qn("w:txbxContent")):
            for pp in t.iter(qn("w:p")):
                z = Paragraph(pp, d).text.strip()
                if z:
                    st["textbox"] += 1
                    blocks.append(dict(type="prose", lvl=None, hp=cp(), text=z, tb=True))
    elif ch.tag == qn("w:tbl"):
        ti += 1; etab(Table(ch, d), "", False, ti, d)

ck, buf = [], []
def add(hp, body, kind, tb=False, rows=None):
    head = " > ".join(hp); full = (head+"\n"+body) if head else body
    nb = nm(body)
    ck.append(dict(chunk_id=f"{TAG}_{len(ck)+1:04d}", doc=TAG, block_type=kind,
        heading_path=hp, heading_depth=len(hp), product=prods(full),
        text=fold(nm(full)), display_text=nm(full), table_rows=rows,
        in_textbox=tb, n_chars=len(nm(full)),
        numbers=[fold(m.group(0)).rstrip(".").replace(",","") for m in NUM.finditer(nb)],
        unit_numbers=[f'{fold(m.group(1)).replace(",","")} {m.group(2)}' for m in UNUM.finditer(nb)],
        source="docx"))
def flush():
    global buf
    if buf:
        add(buf[0]["hp"], "\n".join(("  "*(b["lvl"] or 0)+"• "+b["text"]) if b["type"]=="list"
            else b["text"] for b in buf), "prose", buf[0].get("tb", False))
        buf = []
for b in blocks:
    if b["type"] == "head": flush(); continue
    if b["type"] == "table":
        flush(); s = serial(b["rows"], b["cap"])
        if s.strip(): add(b["hp"], s, "table", False, b["rows"])
        continue
    if buf and b["hp"] != buf[0]["hp"]: flush()
    buf.append(b)
    if sum(len(x["text"]) for x in buf) >= 900: flush()
flush()

with open(f"{OUT}/chunks_{TAG}.jsonl","w",encoding="utf-8") as f:
    for c in ck: f.write(json.dumps(c, ensure_ascii=False)+"\n")
L = []
for b in blocks:
    if b["type"] == "head": L.append("#"*b["lvl"]+" "+b["text"])
    elif b["type"] == "table":
        L.append(f"\n<!-- table {b['ti']}{' NESTED' if b['nest'] else ''} under: {' > '.join(b['hp'])} -->")
        if b["cap"]: L.append("**"+nm(b["cap"])+"**")
        for i, r in enumerate(b["rows"]):
            L.append("| "+" | ".join(r)+" |")
            if i == 0: L.append("|"+"---|"*max(len(r),1))
    else:
        L.append("  "*(b["lvl"] or 0)+("• " if b["type"]=="list" else "")+b["text"]
                 +("  <!-- textbox -->" if b.get("tb") else ""))
open(f"{OUT}/extract_{TAG}.md","w",encoding="utf-8").write("\n\n".join(L))
with open(f"{OUT}/numbers_{TAG}.csv","w",newline="",encoding="utf-8-sig") as f:
    w = csv.writer(f); w.writerow(["chunk_id","heading_path","product","block_type","figure","context","VERIFIED"])
    for c in ck:
        keep = {u: u for u in c["unit_numbers"]}
        for x in c["numbers"]:
            if len(x) >= 3: keep.setdefault(x, x)
        for x in sorted(keep, key=len, reverse=True)[:14]:
            w.writerow([c["chunk_id"]," > ".join(c["heading_path"]),"|".join(c["product"]),
                        c["block_type"],x,c["display_text"][:150].replace("\n"," "),""])

print("BLOCKS:", dict(st))
print("CHUNKS:", len(ck), dict(Counter(c["block_type"] for c in ck)))
sz = sorted(c["n_chars"] for c in ck)
print(f"  chars min={sz[0]} median={sz[len(sz)//2]} max={sz[-1]}")
print("  depth:", dict(sorted(Counter(c["heading_depth"] for c in ck).items())))
pc = Counter()
for c in ck:
    for p in c["product"] or ["<none>"]: pc[p] += 1
print("PRODUCTS:", dict(pc.most_common()))
print("files ->", OUT)
