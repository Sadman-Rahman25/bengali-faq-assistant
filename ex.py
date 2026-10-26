import sys, os, re, json, csv, unicodedata
from collections import Counter
from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

TAG = "doc"   # rebound by main()
OUT = "data/processed"
from bnnorm import fold_digits as fold   # one fold table, shared -- see bnnorm.py
ZW = re.compile("[​‌‍﻿]")
NUM = re.compile(r"[\d০-৯][\d০-৯,\.]*")
UNUM = re.compile(r"([\d০-৯][\d০-৯,\.]*)\s*(%|শতাংশ|টাকা|মাস|বছর|দিন|হাজার|লক্ষ|কোটি|কিস্তি)")
# Product matching goes through the shared matcher -- see CONVENTIONS.md.
# ex.py used to carry its own plain-substring table, which tagged every
# বিমাদাবি as the দাবি product and every প্রগতি as গতি. retag.py existed to
# undo exactly that. Both stages now ask the same module, so retag reports
# 0 changes and stands as a check that the two still agree.
from productmatch import programmes_in as prods

HEAD_RX = re.compile(r"Heading (\d)")
# A clause number at the start of a heading, and the (ক)/(খ)/… that may
# follow it. Digits only on both sides of the fold -- no Bengali letter is
# matched, so rule 4 does not arise.
CLAUSE_RX = re.compile(r"[\d০-৯]+(?:\.[\d০-৯]+)*")
SUBCLAUSE_RX = re.compile(r"^[\d০-৯]+(?:\.[\d০-৯]+)*\s*(\([^)]{1,4}\))")

def head_lvl(p):
    """The Word heading level of a paragraph, or None. One definition, used
    by the pre-scan and by the walk, so the two cannot disagree about what a
    heading is."""
    sn = p.style.name if p.style else ""
    tx = p.text.strip()
    m = HEAD_RX.match(sn or "")
    return int(m.group(1)) if (m and tx) else None

def clause_of(t):
    m = CLAUSE_RX.match((t or "").strip())
    return m.group(0) if m else ""

def letter_of(t):
    m = SUBCLAUSE_RX.match((t or "").strip())
    return m.group(1) if m else ""

# A PARENT HEADING ONE MANUAL NEVER PRINTS, copied from the other manual.
#
# The Dabi manual prints ২.১.২(ক)-(ঘ) but never ২.১.২ itself. In the Progoti
# manual that heading is "২.১.২ ইন্ডিভিজুয়াল সেবা (প্রগতি কর্মসূচি)" -- the
# only place either manual says these clauses are the individual (Progoti)
# programme's. Without it, a Dabi §2.1.2 chunk named no programme in any
# heading, so neither the answering rules nor programme_label() could say
# whose ৪%/২% deposit rule it was, and the answer read as everyone's.
#
# SCOPE IS A REVIEWED LIST, as CAPTION_CLAUSES is. Surveyed 2026-10-26: this
# is the only lettered clause in either manual with no parent heading. The
# heading TEXT is never typed here: it is read from the named manual's own
# .docx, in the same folder, at extraction. {folded clause: that .docx}
BORROWED_PARENTS = {"2.1.2": "progoti_manual_2025_unicode.docx"}


def borrowed_parents(src, d):
    """{folded clause: heading text} for each BORROWED_PARENTS clause this
    manual prints only as lettered sub-clauses. Raises if the other manual
    has no such heading -- a missing parent cannot be guessed."""
    heads = [nm(Paragraph(ch, d).text) for ch in d.element.body.iterchildren()
             if ch.tag == qn("w:p") and head_lvl(Paragraph(ch, d)) is not None]
    have = {fold(clause_of(t)) for t in heads if clause_of(t) and not letter_of(t)}
    lettered = {fold(clause_of(t)) for t in heads if letter_of(t)}
    out = {}
    for clause, other in BORROWED_PARENTS.items():
        if clause in have or clause not in lettered:
            continue
        o = Document(os.path.join(os.path.dirname(src), other))
        for ch in o.element.body.iterchildren(qn("w:p")):
            P = Paragraph(ch, o)
            t = nm(P.text)
            if (head_lvl(P) is not None and fold(clause_of(t)) == clause
                    and not letter_of(t)):
                out[clause] = t
                break
        else:
            raise SystemExit(f"ex.py: {other} has no heading for {clause}")
    return out


def subclause_demotions(d, borrowed=()):
    """Heading ordinals to push one level deeper: lettered sub-clauses whose
    own parent is printed at the SAME Word level.

    WHY THIS EXISTS. `path[L] = tx` keeps one slot per heading level, so a
    heading survives into a chunk only while its slot is occupied when a
    content block is emitted. Both manuals print

        ২.১.১ গ্রুপভিত্তিক সেবা …      Heading 3
        ২.১.১(ক) শাখার কর্মএলাকা …     Heading 3

    -- a parent and its child at the same level. The child overwrites the
    slot before any content is emitted, and the parent never reaches the
    corpus at all. Measured 2026-09-25: 13 headings across the two manuals
    were lost this way, 5 of them to this pattern.

    THE TEST IS ASYMMETRIC ON PURPOSE. It fires only when a bare numeric
    clause exists as its own heading and another heading carries that same
    clause WITH a letter. ২.১.১ -> ২.১.১(ক) is parent-and-child; ২.১.২(ক) ->
    ২.১.২(খ) is two siblings, and nesting those would invent a structure the
    manual does not have. A lettered heading with no bare parent heading is
    therefore left alone -- Dabi prints ২.১.২(ক) through (ঘ) but never prints
    ২.১.২ itself, so there is nothing to nest it under and a level would be a
    guess.

    EVERY lettered child moves, not only the one printed next to the parent.
    Demoting just the adjacent one recovers 4 of the 13 rather than 5,
    because the next sibling is still at the parent's level and overwrites
    the slot again.

    Costed before it was applied: 0 chunks added or removed, 0 chunk_ids
    changed, 0 chunk bodies changed, 80 of 867 display_texts lengthened by
    one ancestor heading. dup_group re-derives identical, because
    dedupe.norm_body peels the heading path off before hashing.
    """
    heads = []
    for ch in d.element.body.iterchildren():
        if ch.tag != qn("w:p"):
            continue
        p = Paragraph(ch, d)
        L = head_lvl(p)
        if L is not None:
            heads.append(nm(p.text.strip()))
    bare = {clause_of(t) for t in heads if clause_of(t) and not letter_of(t)}
    # a parent borrowed from the other manual is a parent like any other:
    # its lettered children move one level down beneath it
    return {i for i, t in enumerate(heads)
            if letter_of(t) and (clause_of(t) in bare
                                 or fold(clause_of(t)) in borrowed)}

def nm(s):
    s = ZW.sub("", unicodedata.normalize("NFC", s or "")).replace("৷","।")
    return re.sub(r"[ \t ]+"," ",s).strip()
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
# serial() moved to tablerender.py, which ex.py and resplit.py now share.
# It was defined identically in both files, and a renderer that has to be
# changed in two places is one that will eventually be changed in one.
from tablerender import serial, clause_of_path

# A TABLE'S OWN CAPTION, carried into the table's chunk.
#
# Word keeps a caption as an ordinary paragraph beside the table, so it was
# emitted wherever that paragraph fell: a top-level table's caption into
# the prose chunk before it, a nested table's into its parent cell's text.
# §2.1.1(চ) holds two approval tables in one cell, one per pair of
# programmes, and both captions landed in the parent chunk, ahead of both
# tables -- so the second table's chunk, read alone, gave its BM ceiling as
# a rule for every programme in the heading. That table is also FLOATING:
# Word positions it against the paragraph after it, and that paragraph is
# its caption.
#
# The caption is found by position and formatting, never by wording:
#   - the nearest title paragraph above the table (every run bold, or
#     centred; not a heading, list item or sentence-length text), with at
#     most one ordinary paragraph between, in the same body or cell;
#   - and the lettered sub-section title -- "(ক) …", "খ) …" -- the table
#     sits under. §1.2.2 prints five savings schemes as lettered bold
#     paragraphs, not headings, and its seven tables' own lead-ins ("…
#     নিম্নরূপ:") never say which scheme. Without the letter title the
#     table names no product at all;
#   - for a floating table with nothing above, the anchor paragraph after
#     it -- unless that is the NEXT lettered title. §1.2.2's floating table
#     is anchored to the following scheme's title; reading the anchor there
#     would file one scheme's withdrawal rates under the next.
#
# SCOPE IS BY CLAUSE, as tablerender's is. The survey found captions outside
# their tables in more clauses than these, but elsewhere the caption repeats
# what the chunk already says (§7.1's lead-in restates its heading; a loan's
# feature table carries the loan's name in its own banner row). Changing
# those chunks would name nothing new and would void the quiz reviews that
# cite them. Listed here: the clauses where the caption is the only place
# the programme or product is named.
CAPTION_CLAUSES = frozenset({"1.2.2", "2.1.1", "2.1.2"})
LETTERED_RX = re.compile(r"^\(?[^\s()]{1,2}\)\s*\S")

def title_text(p, d):
    """A paragraph's text if it is set as a title, else ""."""
    P = Paragraph(p, d); t = nm(P.text)
    if not t or len(t) > 150 or head_lvl(P) is not None: return ""
    pr = p.find(qn("w:pPr"))
    if pr is not None and pr.find(qn("w:numPr")) is not None: return ""
    jc = pr.find(qn("w:jc")) if pr is not None else None
    runs = [r for r in P.runs if (r.text or "").strip()]
    if runs and all(r.bold for r in runs): return t
    return t if (jc is not None and jc.get(qn("w:val")) == "center") else ""

def own_caption(tbl, d, hp):
    """The caption lines of one table (a w:tbl element), "\\n"-joined."""
    if clause_of_path(hp) not in CAPTION_CLAUSES: return ""
    def above():
        e = tbl.getprevious()
        while e is not None:
            if e.tag == qn("w:p") and head_lvl(Paragraph(e, d)) is not None: return
            yield e
            e = e.getprevious()
    lines, gap = [], 0
    for e in above():
        if e.tag == qn("w:tbl"): break
        if e.tag != qn("w:p") or not nm(Paragraph(e, d).text): continue
        t = title_text(e, d)
        if t: lines.append(t); break
        gap += 1
        if gap > 1: break
    for e in above():
        t = title_text(e, d) if e.tag == qn("w:p") else ""
        if t and LETTERED_RX.match(t):
            if t not in lines: lines.insert(0, t)
            break
    pr = tbl.find(qn("w:tblPr"))
    if not lines and pr is not None and pr.find(qn("w:tblpPr")) is not None:
        e = tbl.getnext()
        while e is not None and e.tag == qn("w:p") and not nm(Paragraph(e, d).text):
            e = e.getnext()
        t = title_text(e, d) if e is not None and e.tag == qn("w:p") else ""
        if t and not LETTERED_RX.match(t): lines = [t]
    return "\n".join(lines)

blocks, path, st = [], {}, Counter()
def cp(): return [path[k] for k in sorted(path) if path.get(k)]
def etab(tb, cap, nest, i, d):
    # nest is a DEPTH, not a flag: 0 for a top-level table, parent+1 for a
    # table living inside a cell. Truthiness is unchanged, so the counter
    # below and the NESTED marker in the extract still read the same.
    # `cap` is the context a nested table inherits from its parent; `own`
    # is the table's own caption, printed first. Children get `cap` as
    # before, so a caption never leaks into a table inside the table.

    rs = rows_of(tb, d)
    if not any(any(c for c in r) for r in rs): return
    st["tables_nested" if nest else "tables"] += 1
    own = own_caption(tb._tbl, d, cp())
    if own: st["captions"] += 1
    blocks.append(dict(type="table", hp=cp(), rows=rs, cap=cap, own=own, nest=nest, ti=i))
    pt = " ".join(c for c in rs[0] if c).strip()
    for tr in tb._tbl.tr_lst:
        for tc in tr.tc_lst:
            for sub in [Table(c,d) for c in tc.iterchildren() if c.tag==qn("w:tbl")]:
                etab(sub, " — ".join(x for x in (cap,pt,ctext(tc,d)[:100]) if x)[:300], nest + 1, i, d)


def main(src, tag):
    # helpers above mutate these; rebind rather than restructure
    global TAG, blocks, path, st, ck, buf
    TAG = tag
    blocks, path, st = [], {}, Counter()
    ck, buf = [], []
    os.makedirs(OUT, exist_ok=True)

    d = Document(src)
    borrowed = borrowed_parents(src, d)   # see BORROWED_PARENTS
    demote = subclause_demotions(d, set(borrowed))   # see its docstring
    placed = set()
    ti = 0
    hn = -1
    for ch in d.element.body.iterchildren():
        if ch.tag == qn("w:p"):
            p = Paragraph(ch, d); tx = p.text.strip()
            L = head_lvl(p)
            if L is not None:
                # Normalise at write time so heading_path matches display_text, which
                # is stored as nm(head + "\n" + body). Kept raw, heading_path carries
                # tabs and pre-NFC ড়/য়, so any consumer that compares, groups or
                # hashes on it silently misses -- see resplit.recover_caption.
                hn += 1
                if hn in demote:
                    L += 1; st["h_demoted"] += 1
                tx = nm(tx)
                c = fold(clause_of(tx))
                if letter_of(tx) and c in borrowed and c not in placed:
                    # the parent this manual never prints, at the level its
                    # first lettered child had before moving down beneath it
                    path[L - 1] = borrowed[c]
                    for k in [k for k in path if k > L - 1]: path.pop(k)
                    placed.add(c); st["h_borrowed"] += 1
                    blocks.append(dict(type="head", lvl=L - 1, text=borrowed[c], hp=cp()))
                path[L] = tx
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
            ti += 1; etab(Table(ch, d), "", 0, ti, d)

    ck, buf = [], []
    def add(hp, body, kind, tb=False, rows=None, nest=0):
        head = " > ".join(hp); full = (head+"\n"+body) if head else body
        nb = nm(body)
        ck.append(dict(chunk_id=f"{TAG}_{len(ck)+1:04d}", doc=TAG, block_type=kind,
            heading_path=hp, heading_depth=len(hp), product=prods(full),
            text=fold(nm(full)), display_text=nm(full), table_rows=rows,
            in_textbox=tb, nest_depth=nest, n_chars=len(nm(full)),
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
            flush(); s = serial(b["rows"], "\n".join(x for x in (b["own"], b["cap"]) if x), b["hp"])
            if s.strip(): add(b["hp"], s, "table", False, b["rows"], b["nest"])
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
            for c in (b["own"], b["cap"]):
                if c: L.append("**"+nm(c)+"**")
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



if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "doc")
