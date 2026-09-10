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
from retag import PROD as RETAG_PROD

P = Path("data/processed")
CORPORA = ("progoti", "dabi_2025")
_args = [a for a in sys.argv[1:] if not a.startswith("--")]
OUT = Path(_args[0] if _args else "products_catalogue.csv")

# programme-level keys come from the retag `product` field
PROGRAMMES = [
    ("dabi",    "দাবি",       "দাবি কর্মসূচি; দাবি+; Dabi; Group Financing"),
    ("progoti", "প্রগতি",     "প্রগতি কর্মসূচি; Progoti; Individual Lending; MELA"),
    ("bcup",    "বিসিইউপি",   "BCUP"),
    ("cdp",     "সিডিপি",     "CDP"),
    ("ncdp",    "এনসিডিপি",   "NCDP"),
    ("scdp",    "এসসিডিপি",   "SCDP"),
]

# Non-product entities. Kept in the same registry so one lookup answers
# "who carries the risk on this product", but typed so they are never
# mistaken for something BRAC sells.
ENTITIES = [
    ("shena_insurance", "underwriter", "সেনা ইন্স্যুরেন্স পিএলসি",
     "Sena Insurance PLC; underwrites ফায়ার সেফটি (Progoti §5.4.6) and "
     "গবাদিপ্রাণি সুরক্ষা (Dabi §5.4.3)",
     r"সেনা\s*ইন্স্যুরেন্স"),
    ("green_delta_insurance", "underwriter", "গ্রীন ডেল্টা ইন্স্যুরেন্স পিএলসি",
     "Green Delta Insurance PLC; underwrites গবাদিপ্রাণি সুরক্ষা বিমা (Dabi only)",
     r"গ্রীন\s*ডেল্টা"),
    ("guardian_life", "underwriter", "গার্ডিয়ান লাইফ ইন্স্যুরেন্স লিমিটেড",
     "গার্ডিান লাইফ (sic); ইইস্যুরেন্স (sic, Progoti); ইনশুরেন্স (Dabi); "
     "underwrites ঋণ নিরাপত্তা বিমা and ছায়া-সঞ্চয় নিরাপত্তা বিমা",
     # NFC decomposes য় to য + ়, so match loosely between ড and ান
     r"গার্ড\S{0,4}ান\s*লাইফ"),
    ("pioneer_insurance", "underwriter", "পাইওনিয়ার ইন্স্যুরেন্স কোম্পানি লিমিটেড",
     "পাইওনিয়র (sic); co-underwrites ফায়ার সেফটি with সেনা (Progoti only)",
     r"পাইওনিয়া?র"),
    ("brac_microinsurance", "brac_unit", "ব্র্যাক মাইক্রোইন্স্যুরেন্স",
     "BRAC Microinsurance central team; receives claim documents. "
     "Not an insurer and not a product.",
     r"(?:ব্র্যাক\s*)?মাইক্রোইন্স্যুরেন্স"),
]

# named products: (key, name_bn, aliases, pattern over canon'd text)
NAMED = [
    # --- দাবি loan products (§1.1.1, group financing)
    ("dabi_general_loan", "দাবি + সাধারণ লোন", "দাবি সাধারণ লোন",
     r"দাবি\s*\+?\s*সাধারণ\s*লোন"),
    ("shadhin_loan", "স্বাধীন লোন", "স্বাধীণ ঋণ",
     r"স্বাধী[নণ]\s*(?:লোন|ঋণ)"),
    ("loan_18_month", "১৮ মাস মেয়াদি লোন", "১৮ মাস মেয়াদী ঋণ",
     r"18\s*মাস\s*মেয়াদ[িী]\s*(?:লোন|ঋণ)"),
    ("wash_loan", "ওয়াশ লোন", "WASH loan", r"ওয়াশ\s*লোন"),
    ("projukti_loan", "প্রযুক্তি লোন", "technology loan", r"প্রযুক্তি\s*লোন"),

    # --- প্রগতি loan products (§1.1.2, individual financing)
    ("progoti_general_loan", "প্রগতি সাধারণ লোন", "ট্রেড লোন",
     r"প্রগতি\s*সাধারণ\s*লোন|ট্রেড\s*লোন"),
    ("migration_loan", "মাইগ্রেশন লোন", "", r"মাইগ্রেশন\s*লোন"),
    # corpus spells it রেমিটেন্স only; ্যা alternative kept for future docs
    ("remittance_loan", "রেমিটেন্স লোন", "রেমিট্যান্স লোন",
     r"রেমিট(?:ে|্যা)ন্স\s*লোন"),
    ("agribusiness_loan", "এগ্রিবিজনেস লোন", "", r"এগ্রিবিজনেস\s*লোন"),
    ("nirvorota_loan", "নির্ভরতা লোন", "", r"নির্ভরতা\s*লোন"),
    # গতি never owns a heading -- item 6 inside §1.1.2, plus the changelog.
    # Lookbehind keeps it off the tail of প্রগতি লোন.
    ("goti_loan", "গতি লোন", "goti loan", r"(?<!প্র)গতি\s*লোন"),
    ("unmesh_loan", "উন্মেষ ঋণ", "উন্মেষ লোন", r"উন্মেষ\s*(?:লোন|ঋণ)"),

    # --- common products (§1.1.3, all programmes)
    ("prottasha_loan", "প্রত্যাশা লোন", "", r"প্রত্যাশা\s*লোন"),
    # require লোন: bare রিফাইন্যান্সিং/রিশিডিউলিং is the PROCESS, discussed in
    # §5.1.6 about insurance cover, and matching it stole the defining section.
    ("refinance_loan", "রি-ফাইন্যান্স লোন", "রিফাইন্যান্সিং (process, §5.1.6)",
     r"রি\s*-?\s*ফাইন্যান্স\s*লোন"),
    ("rescheduled_loan", "রি-সিডিউলড লোন", "রি-সিডিউল লোন; রিশিডিউলিং (process)",
     r"রি\s*-?\s*সিডিউল(?:ড)?\s*লোন"),

    # --- savings products (§1.2)
    ("general_savings", "সাধারণ সঞ্চয়", "পাশবই সঞ্চয়; বাধ্যতামূলক সাধারণ সঞ্চয়",
     r"সাধারণ\s*সঞ্চয়|পাশবই\s*সঞ্চয়"),
    ("term_savings", "মেয়াদী সঞ্চয়", "মেয়াদি সঞ্চয়; সঞ্চয় স্কীম",
     r"মেয়াদ[িী]\s*সঞ্চয়"),
    ("monthly_profit_savings", "মাসিক মুনাফাভিত্তিক সঞ্চয় প্রকল্প", "",
     r"মাসিক\s*মুনাফা\s*ভিত্তিক\s*সঞ্চয়|মাসিক\s*মুনাফাভিত্তিক\s*সঞ্চয়"),
    ("double_profit_savings", "দ্বিগুণ মুনাফাভিত্তিক সঞ্চয় প্রকল্প", "",
     r"দ্বিগুণ\s*মুনাফা\s*ভিত্তিক\s*সঞ্চয়|দ্বিগুণ\s*মুনাফাভিত্তিক\s*সঞ্চয়"),
    ("onehalf_profit_savings", "দেড়গুণ মুনাফাভিত্তিক সঞ্চয় প্রকল্প", "",
     r"দেড়গুণ\s*মুনাফা\s*ভিত্তিক\s*সঞ্চয়|দেড়গুণ\s*মুনাফাভিত্তিক\s*সঞ্চয়"),
    ("money_plant_savings", "মানি-প্লান্ট সঞ্চয় প্রকল্প",
     "মানিপ্ল্যান্ট সঞ্চয় প্রকল্প; মানি প্লান্ট সঞ্চয় প্রকল্প",
     r"মানি\s*-?\s*প্ল[া্]?[যা]?ান্ট\s*সঞ্চয়"),
    ("lumpsum_fixed_savings", "এককালীন স্থায়ী সঞ্চয় প্রকল্প", "",
     r"এককালীন\s*স্থায়ী\s*সঞ্চয়"),

    # --- insurance (ch. 5)
    ("loan_security_insurance", "ঋণ নিরাপত্তা বিমা", "",
     r"ঋণ\s*নিরাপত্তা\s*বিমা"),
    ("chaya_savings_insurance", "ছায়া-সঞ্চয় নিরাপত্তা বিমা", "",
     r"ছায়া\s*-?\s*সঞ্চয়\s*নিরাপত্তা\s*বিমা"),
    ("crop_insurance", "শস্য নিরাপত্তা বিমা", "শস্য বিমা; শস্য নিাপত্তা বিমা (sic)",
     r"শস্য\s*নি(?:রা)?পত্তা\s*বিমা|শস্য\s*বিমা"),
    ("fire_safety_insurance", "ফায়ার সেফটি ইন্স্যুরেন্স", "Fire Safety Insurance",
     r"ফায়ার\s*সেফটি\s*ইন্স্যুরেন্স"),
    ("livestock_insurance", "গবাদিপ্রাণি সুরক্ষা বিমা", "লাইভস্টক গ্রো; Livestock Grow",
     r"গবাদিপ্রাণি\s*সুরক্ষা\s*বিমা|লাইভস্টক\s*গ্রো"),
    ("dairy_cow_insurance", "দুগ্ধজাত গাভীর বিমা", "", r"দুগ্ধজাত\s*গাভীর\s*বিমা"),
    ("cattle_fattening_insurance", "গরু মোটাতাজাকরণ বিমা", "",
     r"গরু\s*মোটাতাজাকরণ\s*বিমা"),

    # --- named but never defined in either manual (expect empty
    #     defining_chunk_id). Real products with real operational rules
    #     attached elsewhere, so they must be findable.
    ("arogya_loan", "আরোগ্য ঋণ", "health loan; §5.1.2 excludes its customers "
     "from ঋণ নিরাপত্তা বিমা cover", r"আরোগ্য\s*(?:ঋণ|লোন)"),
    ("bishesh_savings", "বিশেষ সঞ্চয়",
     "ডিপিএস; umbrella for ডিপিএস + মাসিক মুনাফা; collateral for প্রত্যাশা লোন",
     r"বিশেষ\s*সঞ্চয়|ডিপিএস"),
    ("credit_shield_insurance", "ক্রেডিট শিল্ড ইন্স্যুরেন্স",
     "named once in সূচনা only; may be the English name for "
     "ঋণ নিরাপত্তা বিমা -- CONFIRM before treating as distinct",
     r"ক্রেডিট\s*শিল্ড"),
]

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


def compile_pat(pat):
    """Compile a pattern normalised the same way as the corpus text.

    canon_pattern, not canon: the latter's separator stripping rewrites regex
    quantifiers ({0,4} -> {04}). Without any normalisation, a literal saved
    with precomposed য়/ড়/ঢ় cannot match NFC text and silently under-matches.
    """
    return re.compile(canon_pattern(pat))


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
                + [rx for k, rx in RETAG_PROD
                   if k in {key for key, *_ in PROGRAMMES}])
    for c in chunks:
        c["_head_products"] = sum(1 for rx in all_pats if rx.search(c["_head"]))
    return chunks


def build_rows(chunks, debug=False):
    rows = []
    retag_rx = dict(RETAG_PROD)
    specs = ([("brac_programme", k, n, a, retag_rx[k], True)
              for k, n, a in PROGRAMMES]
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
