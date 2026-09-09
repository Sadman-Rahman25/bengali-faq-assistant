"""Find legacy (Bijoy / SutonnyMJ-family) ASCII-encoded Bengali runs in a .docx.

Two independent detectors, because either alone has a blind spot:

  font name  -- catches runs explicitly set in SutonnyMJ etc, but MISSES runs
                that inherit a legacy font from their paragraph style.
  glyph score -- catches the inherited case (e.g. "cwieZ©Y: FYwenxb ...", which
                carries no direct font name at all) by looking at the byte
                soup itself: high latin ratio, no Bengali, Bijoy digraphs.

Font resolution walks run -> run style -> paragraph style -> base styles, and
reads all four w:rFonts slots (ascii/hAnsi/cs/eastAsia), since Bengali is often
tagged only in the complex-script slot.

Usage:  python bijoy_scan.py data/raw/progoti_manual_2025.docx
"""
import sys, re
from collections import Counter
from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

LEGACY = ("sutonny", "boishakhi", "chandrabati", "modhumati", "shreelipi", "bijoy",
          "proshika", "lekhoni", "borno", "amarbangla", "bangsree")
MARKERS = ("Av", "Zv", "Kv", "bv", "gv", "iv", "hv", "`v", "wU", "wi", "‡", "†",
           "Ó", "Ò", "¨", "¸", "‰", "„", "©", "Ö", "FY", "‡Z", "Kx")

nfont = lambda n: re.sub(r"[^a-z0-9]", "", (n or "").lower())


def is_legacy_font(n):
    x = nfont(n)
    return bool(x) and (bool(re.search(r"mj$|mj[0-9]*$|ansi", x))
                        or any(h in x for h in LEGACY))


def rfonts(el):
    """Every font name on an element's rPr, across all four script slots."""
    out = []
    if el is None:
        return out
    pr = el.find(qn("w:rPr"))
    rf = pr.find(qn("w:rFonts")) if pr is not None else None
    if rf is not None:
        for a in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            v = rf.get(qn(a))
            if v:
                out.append(v)
    return out


def style_fonts(style, seen=None):
    """Font names on a style and its base-style chain."""
    seen = seen or set()
    out = []
    while style is not None and id(style) not in seen:
        seen.add(id(style))
        try:
            if style.font is not None and style.font.name:
                out.append(style.font.name)
        except (AttributeError, NotImplementedError):
            pass
        out += rfonts(getattr(style, "_element", None))
        style = getattr(style, "base_style", None)
    return out


def effective_fonts(run, para):
    """(direct fonts, inherited fonts) for a run."""
    direct = ([run.font.name] if run.font is not None and run.font.name else [])
    direct += rfonts(run._r)
    inherited = style_fonts(getattr(run, "style", None)) + style_fonts(para.style)
    return [f for f in direct if f], [f for f in inherited if f]


def ratios(t):
    v = [c for c in t if not c.isspace()]
    if not v:
        return 0.0, 0.0, 0
    bn = sum(1 for c in v if 0x980 <= ord(c) <= 0x9FF)
    # Bijoy output lives in ASCII, Latin-1 supplement, and the General
    # Punctuation block (‡ † „ ‰ Ö are U+2020..U+2030), so count all three.
    la = sum(1 for c in v if (c.isascii() and c.isalpha())
             or 0x80 <= ord(c) <= 0xFF or 0x2018 <= ord(c) <= 0x2030)
    return bn / len(v), la / len(v), len(v)


def score(t):
    bn, la, n = ratios(t)
    if n < 6 or bn > 0.15:
        return 0.0
    hits = sum(1 for m in MARKERS if m in t)
    latin_n = max(la * n, 1)
    return round(min(0.45 * (la > 0.5)
                     + 0.30 * min(hits / 5, 1)
                     + 0.25 * (t.count("v") / latin_n > 0.08), 1.0), 2)


def all_paras(doc):
    def walk(el, where, depth=0):
        for ch in el.iterchildren():
            if ch.tag == qn("w:p"):
                yield Paragraph(ch, doc), where
                for tb in ch.iter(qn("w:txbxContent")):
                    for pp in tb.iter(qn("w:p")):
                        yield Paragraph(pp, doc), "textbox"
            elif ch.tag == qn("w:tbl"):
                for tr in Table(ch, doc)._tbl.tr_lst:
                    for tc in tr.tc_lst:
                        yield from walk(tc, f"table_d{depth + 1}", depth + 1)

    yield from walk(doc.element.body, "body")


FONT_DIRECT = "direct legacy font"
FONT_INHERITED = "inherited legacy font"
SCORE_ONLY = "glyph score only (no legacy font anywhere)"


def scan(doc):
    """Yield one record per suspect run, plus the total visible char count.

    Returns (records, total_visible_chars). Each record keeps a live reference
    to the python-docx run so a caller can convert it in place -- see
    bijoy_convert.py, which reuses this rather than forking the detection.
    """
    recs, tot_visible = [], 0
    for p, where in all_paras(doc):
        style = p.style.name if p.style is not None else ""
        for run in p.runs:
            txt = run.text
            if not txt.strip():
                continue
            _, _, n = ratios(txt)
            tot_visible += n
            s = score(txt)
            direct, inherited = effective_fonts(run, p)
            df = next((f for f in direct if is_legacy_font(f)), None)
            inf = next((f for f in inherited if is_legacy_font(f)), None)
            if s < 0.5 and not df and not inf:
                continue
            recs.append(dict(
                run=run, para=p, where=where, style=style, text=txt,
                score=s, direct_font=df, inherited_font=inf, n_chars=n,
                evidence=(FONT_DIRECT if df else
                          FONT_INHERITED if inf else SCORE_ONLY)))
    return recs, tot_visible


def main(path):
    doc = Document(path)
    recs, tot_visible = scan(doc)
    susp, fonts, byloc, chars, how = [], Counter(), Counter(), Counter(), Counter()
    for r in recs:
        how[r["evidence"]] += 1
        fonts[r["direct_font"] or r["inherited_font"]
              or "<none - inherited/default>"] += 1
        byloc[r["where"]] += 1
        chars[r["where"]] += r["n_chars"]
        susp.append((r["score"], r["direct_font"], r["inherited_font"],
                     r["style"], r["where"], r["text"].strip()[:72]))
    report(path, susp, fonts, byloc, chars, how, tot_visible)


def report(path, susp, fonts, byloc, chars, how, tot_visible):
    print("=" * 74)
    print(f"BIJOY / LEGACY SCAN :: {path}")
    print(f"{tot_visible} visible chars scanned")
    print("=" * 74)
    aff = sum(chars.values())
    pct = 100 * aff / max(tot_visible, 1)
    print(f"\nSUSPECT RUNS: {len(susp)}   affected chars: {aff} "
          f"({pct:.2f}% of document)")
    if not susp:
        print("\n  None. No legacy-encoded runs found.")
        return

    print(f"\nHOW DETECTED:")
    for k, n in how.most_common():
        print(f"  {n:>5}  {k}")
    print(f"\nBY LOCATION: {dict(byloc)}")
    print(f"CHARS BY LOCATION: {dict(chars)}")
    print(f"\nFONTS ON SUSPECT RUNS:")
    for f, n in fonts.most_common(10):
        print(f"  {n:>5}  {f}{'  [LEGACY NAME]' if is_legacy_font(f) else ''}")
    print(f"\nSAMPLES (score, how, style, where):")
    for s, df, inf, st, w, t in sorted(susp, key=lambda x: -x[0])[:20]:
        tag = "FONT" if df else ("STYL" if inf else "    ")
        print(f"  {s:.2f} {tag} {st[:14]:<14} {w:<10} {t!r}")

    print("\nWHAT TO DO")
    if pct < 0.5:
        print(f"  {pct:.2f}% is tiny -- retyping these {len(susp)} runs by hand")
        print("  will beat converting, and risks nothing in the Unicode text.")
    else:
        print("  Run bijoy_convert.py. It converts font-evidenced runs only and")
        print("  holds back score-only runs for review -- the score gives 0.45 to")
        print("  any mostly-latin run, so ordinary English clears 0.50 on one")
        print("  accidental marker ('Av' in Available, 'iv' in Activity).")
    print("  Verify conjuncts (ক্ষ ঞ্জ ন্ত্র) and reph placement by hand after.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1
         else "data/raw/progoti_manual_2025.docx")
