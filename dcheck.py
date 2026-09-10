import sys, re, unicodedata
from collections import Counter
import docx
from docx.table import Table
from docx.text.paragraph import Paragraph

GOOD = ["সূচিপত্র","দাবি","প্রগতি","প্রোডাক্ট","কর্মসূচি","সেবা","দ্বিতীয়",
        "সাধারণ","মেয়াদী","ফেরত","প্রযোজ্য","অনুমোদনকারী","লোন","অধ্যায়"]
BAD  = ["ললোন","প্রপ্রোডাক্ট","পাসপপোর্ট","অনুমমোদন","প্রযযোজ্য","কর্মসূি",
        "প্রগি","সিিপি","দাচি","প্সবা","সাধােণ"]
M = set(range(0x09BE,0x09C5))|{0x09C7,0x09C8,0x09CB,0x09CC,0x09D7}
DEP = M|{0x09CD,0x09BC}
N = lambda s: unicodedata.normalize("NFC", s or "")


def main(path):
    path = sys.argv[1]
    d = docx.Document(path)
    styles, texts, md, tables = Counter(), [], [], []
    for ch in d.element.body.iterchildren():
        t = ch.tag.split("}")[-1]
        if t == "p":
            p = Paragraph(ch, d)
            if not p.text.strip(): continue
            s = p.style.name if p.style else "?"
            styles[s] += 1
            texts.append(p.text)
            h = re.match(r"Heading (\d)", s)
            md.append(("#"*int(h.group(1))+" " if h else "") + p.text.strip())
        elif t == "tbl":
            tb = Table(ch, d); tables.append(len(tb.rows))
            for i, row in enumerate(tb.rows):
                c = [x.text.strip() for x in row.cells]
                texts.append(" | ".join(c)); md.append("| " + " | ".join(c) + " |")
                if i == 0: md.append("|" + "---|"*len(c))

    txt = N("\n".join(texts))
    bn = sum(1 for c in txt if 0x980 <= ord(c) <= 0x9FF)
    good = [w for w in GOOD if N(w) in txt]
    bad  = [w for w in BAD  if N(w) in txt]
    v, prev = 0, " "
    for c in txt:
        o = ord(c)
        if o in DEP and prev in " \n\t": v += 1
        elif o in M and ord(prev) in M: v += 1
        prev = c

    print(f"chars={len(txt)}  bengali={bn}  paras={sum(styles.values())}  tables={len(tables)}")
    print(f"GOOD {len(good)}/{len(GOOD)}: {good}")
    print(f"BAD  {len(bad)}: {bad}")
    print(f"malformed={v}  rate={1000*v/max(bn,1):.1f}/1000")
    print("VERDICT:", "CLEAN - use it" if len(good)>=8 and not bad and 1000*v/max(bn,1)<5
          else ("CORRUPT - " + str(bad) if bad else "INSPECT the dump"))
    print("\nSTYLES:")
    for s, n in styles.most_common(12):
        print(f"  {n:>5}  {s}{'   <-- HEADING' if re.match(r'Heading .',s) else ''}")
    open("progoti_extracted.md","w",encoding="utf-8").write("\n\n".join(md))
    print(f"\nWrote progoti_extracted.md ({len(md)} blocks)")


if __name__ == "__main__":
    main(sys.argv[1])
