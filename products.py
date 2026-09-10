"""Registry of every product and insurance entity named in either manual.

entity_type separates things that are NOT the same kind of thing:

    brac_programme  দাবি, প্রগতি ... the lending programmes
    brac_product    a loan / savings / insurance product BRAC sells
    brac_unit       a BRAC team, e.g. ব্র্যাক মাইক্রোইন্স্যুরেন্স -- it
                    processes claims but is not a product and not an insurer
    underwriter     the external insurer carrying the risk. An officer asking
                    "গরু বিমার দাবি কোথায় করব" needs THIS row, not the
                    product row: the answer is সেনা ইন্স্যুরেন্স পিএলসি or
                    গ্রীন ডেল্টা ইন্স্যুরেন্স পিএলসি depending on which
                    variant was sold.

defining_chunk_id marks the section that DEFINES the product, as opposed to
the many chunks that merely mention it. Empty means the manuals name the
product but never define it -- আরোগ্য ঋণ is named in সূচনা and excluded from
insurance cover in §5.1.2, yet has no section of its own. That is a real gap
in the corpus, not a bug in this script, and an answer about আরোগ্য ঋণ can
only cite a mention.

Covers three kinds of mention, because a product does not have to own a
heading to exist:

  * programme level -- দাবি, প্রগতি, বিসিইউপি ... taken from the retagged
    `product` field, which is authoritative (see retag.py).
  * named products  -- matched by name, including ones that live only INSIDE
    another product's section (গতি লোন is item 6 of ১.১.২ প্রগতি কর্মসূচি,
    উন্মেষ ঋণ is a প্রগতি pilot) and ones whose only mention is the
    পরিবর্তন সমূহ changelog.
  * spelling variants -- the changelog writes স্বাধীণ ঋণ where §1.1.1 writes
    স্বাধীন লোন, and the source misspells শস্য নিরাপত্তা as শস্য নিাপত্তা.
    Both forms are matched and folded onto one key.

Text is canonicalised with bnnorm.canon before matching, so ১৮ and 18 are the
same token -- otherwise "১৮ মাস মেয়াদি লোন" would miss in any chunk whose
digits came through in ASCII.

`status` is left blank for a human to fill.

Usage:  python products.py [out.csv]
"""
import csv
import json
import re
import sys
import unicodedata
from pathlib import Path

from bnnorm import canon, canon_pattern
# retag.PROD is the authority on programme-name matching. A naive
# re.escape("দাবি") matches inside বিমাদাবি and made §5.2.6 (insurance claim
# procedure) look like the defining section for the দাবি programme -- the same
# false positive retag.py exists to prevent.
from productmatch import (ENTITIES, NAMED, PROGRAMME_NAMES, PROGRAMMES,
                          compile_pat)

P = Path("data/processed")
CORPORA = ("progoti", "dabi_2025")
_args = [a for a in sys.argv[1:] if not a.startswith("--")]
OUT = Path(_args[0] if _args else "products_catalogue.csv")




# --- defining-section detection -------------------------------------------
# A definition looks different from a mention: it sits under the product's own
# heading, or introduces the name as a labelled list item, or is followed by
# one of the manual's stock definition verbs. Rate tables, the changelog and
# the intro are mentions however often they name the product.
DEF_VERB = re.compile(
    r"(?:সূচিত\s*হয়|সূচনা\s*হয়|প্রদান\s*করা\s*হয়|চালু\s*করা\s*হয়"
    r"|যাত্রা\s*শুরু|শুরু\s*হয়|বলা\s*হয়|খুলতে\s*পারেন|কি\s*\?"
    r"|কি\s*ও\s*কিভাবে|প্রকল্পের\s*আওতায়|এ\s*লোনটি|নামেও\s*পরিচিত)")


MIN_DEF_SCORE = 3          # below this, every hit is a mention
LIST_LABEL = re.compile(r"(?:^|\n)\s*[ক-ঞa-z0-9]{1,3}[).]\s*$")


def score_chunk(rx, c, require_heading):
    head, score, why = c["_head"], 0, []
    hms = list(rx.finditer(head))
    head_ok = bool(hms) and c["_head_products"] < 3
    if head_ok:
        score += 4
        why.append("own-heading+4")
        # "<product> কি?" / "<product> কি ও কিভাবে কাজ করে?" is the manual's
        # own definition heading -- distinguishes §5.4.1 (what it is) from
        # §5.4.3 (benefits), which otherwise tie on the heading bonus alone.
        # Must check EVERY occurrence: _head is the full path, so the parent
        # "§5.4 ফায়ার সেফটি ইন্স্যুরেন্স >" matches first and hides the
        # "কি ও কিভাবে" in the leaf heading.
        if any(re.match(r"\s*কি(?:\s|\?|$)", head[m.end():]) for m in hms):
            score += 2
            why.append("def-heading+2")
    if require_heading and not head_ok:
        # A programme is defined by having its own section. Without that, body
        # coincidences were inventing definitions: বিসিইউপি scored on §1.1.1,
        # the দাবি section, purely because the acronym appears in its prose.
        return -1, ["no own heading (programme)"]
    m = rx.search(c["_body"])
    if m:
        after = c["_body"][m.end():m.end() + 220]
        before = c["_body"][max(0, m.start() - 12):m.start()]
        # In-body labelling only signals a definition for products that do NOT
        # own a heading (গতি লোন inside §1.1.2, দ্বিগুণ সঞ্চয় inside §1.2.2).
        # When the product does own its heading, a numbered row that happens to
        # repeat the name is noise -- it promoted §5.1.3 (premium rates) over
        # §5.1 for ঋণ নিরাপত্তা বিমা.
        if not head_ok:
            # allow a type noun between name and colon: "... সঞ্চয় প্রকল্প:"
            if re.match(r"[^:ঃ\n]{0,14}[:ঃ]", after):
                score += 3
                why.append("labelled+3")
            # a list label with no colon: "গ) দ্বিগুণ মুনাফাভিত্তিক সঞ্চয় প্রকল্প"
            elif LIST_LABEL.search(before):
                score += 3
                why.append("list-item+3")
        if DEF_VERB.search(after):
            score += 2
            why.append("def-verb+2")
    if c["block_type"] == "table":
        score -= 2
        why.append("table-2")
    if "পরিবর্তন সমূহ" in head:
        score -= 3
        why.append("changelog-3")
    if head.strip().startswith("সূচনা"):
        score -= 1
        why.append("intro-1")
    return score, why


def defining_chunk(rx, cands, require_heading=False, debug=False):
    """Best-scoring definition candidate, or None if every hit is a mention.

    Two corrections that matter:

    * the ':' and definition-verb bonuses are scored against the BODY, never
      the heading line. Scored against display_text they fired on the heading
      occurrence, so every chunk under one heading got them and the winner was
      decided by unrelated body text.

    * a heading that enumerates three or more products defines none of them.
      §1.1.3 "দাবি, প্রগতি, বিসিইউপি, সিডিপি'র সকল কর্মসূচির" was being read
      as the definition of বিসিইউপি and সিডিপি alike.

    Ties keep the earliest candidate (corpus order, then chunk_id), which is
    the first section under a defining heading.
    """
    scored = [(score_chunk(rx, c, require_heading), c) for c in cands]
    if debug:
        for (s, why), c in sorted(scored, key=lambda x: -x[0][0])[:3]:
            print(f"        {s:>3}  {c['chunk_id']:<20} {','.join(why):<34} "
                  f"{c['_head'][-42:]}")
    best, best_score = None, MIN_DEF_SCORE - 1
    for (score, _), c in scored:
        if score > best_score:
            best, best_score = c, score
    return best

# canon() now includes NFC, so this is NFC + digit fold + separator strip.
# It must be applied to PATTERNS too, not only text -- see bnnorm.
nrm = lambda s: re.sub(r"\s+", " ", canon(s or ""))




def load_chunks():
    chunks = []
    for tag in CORPORA:
        for l in (P / f"chunks_{tag}_final.jsonl").open(encoding="utf-8"):
            c = json.loads(l)
            c["_norm"] = nrm(c["display_text"])
            c["_head"] = nrm(" > ".join(c["heading_path"]))
            c["_body"] = (c["_norm"][len(c["_head"]):].lstrip()
                          if c["_head"] and c["_norm"].startswith(c["_head"])
                          else c["_norm"])
            chunks.append(c)
    # how many distinct product patterns does each heading name? 3+ means the
    # heading is a list, not a definition.
    all_pats = ([compile_pat(p) for *_, p in NAMED]
                + [rx for k, rx in PROGRAMMES
                   if k in {key for key, *_ in PROGRAMMES}])
    for c in chunks:
        c["_head_products"] = sum(1 for rx in all_pats if rx.search(c["_head"]))
    return chunks


def build_rows(chunks, debug=False):
    rows = []
    retag_rx = dict(PROGRAMMES)
    specs = ([("brac_programme", k, n, a, retag_rx[k], True)
              for k, (n, a) in PROGRAMME_NAMES.items()]
             + [("brac_product", k, n, a, compile_pat(p), False)
                for k, n, a, p in NAMED]
             + [(t, k, n, a, compile_pat(p), False)
                for k, t, n, a, p in ENTITIES])
    for etype, key, name, aliases, rx, req_head in specs:
        if etype == "brac_programme":
            hits = [c for c in chunks if key in c["product"]]
        else:
            hits = [c for c in chunks if rx.search(c["_norm"])]
        if debug:
            print(f"  [{key}]")
        rows.append((key, etype, name, aliases,
                     defining_chunk(rx, hits, require_heading=req_head,
                                    debug=debug),
                     [c["chunk_id"] for c in hits]))
    return rows


def main(debug=False):
    chunks = load_chunks()
    rows = build_rows(chunks, debug)

    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["product_key", "entity_type", "name_bn", "aliases",
                    "defining_chunk_id", "chunk_ids", "status"])
        for key, etype, name, aliases, dc, ids in rows:
            w.writerow([key, etype, name, aliases,
                        dc["chunk_id"] if dc else "", ";".join(ids), ""])

    print(f"{len(rows)} rows -> {OUT}\n")
    print(f"  {'key':<26}{'type':<15}{'n':>5}{'prog':>6}{'dabi':>6}  "
          f"defining_chunk_id")
    empty, mention_only = [], []
    for key, etype, name, aliases, dc, ids in rows:
        p = sum(1 for i in ids if i.startswith("progoti"))
        d = len(ids) - p
        note = ""
        if not ids:
            note = "  <-- NO MATCH"
            empty.append(key)
        elif not dc:
            note = "  <-- MENTION ONLY"
            mention_only.append(key)
        elif not p or not d:
            note = "  (one manual only)"
        print(f"  {key:<26}{etype:<15}{len(ids):>5}{p:>6}{d:>6}  "
              f"{(dc['chunk_id'] if dc else '-'):<20}{note}")

    if mention_only:
        print(f"\n  MENTION-ONLY (named but never defined): {mention_only}")
    if empty:
        print(f"\n  PATTERN MISS -- fix these: {empty}")


if __name__ == "__main__":
    main("--debug" in sys.argv)
