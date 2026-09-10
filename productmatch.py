"""The single place a product or programme name becomes a pattern.

CONVENTION: no other module may match a product name directly. Import from
here instead. See CONVENTIONS.md; conventions_check.py enforces it.

WHY THIS MODULE EXISTS
Ad-hoc product matching has produced four separate defects:

  1. ex.py matched bare "দাবি", so every বিমাদাবি ("insurance claim") tagged
     the Dabi loan product.
  2. products.py re-introduced the same bug with re.escape("দাবি") while
     picking defining sections, making §5.2.6 (claim procedure) look like the
     definition of the দাবি programme.
  3. bare "গতি" matched inside প্রগতি, অগ্রগতি and গতিশীল, so a real product
     (গতি লোন) was first over-tagged, then wrongly dropped altogether.
  4. স্বাধীন লোন is spelled স্বাধীণ ঋণ in the changelog, so a single-spelling
     pattern found only part of it.

Each was a separate module deciding for itself what a product name looks
like. One table, imported everywhere, is the fix.

Every pattern is compiled through bnnorm.canon_pattern and matched against
bnnorm.canon'd text, so a literal is never defeated by NFC decomposing
য়/ড়/ঢ় into base + nukta.
"""
import re

from bnnorm import canon, canon_pattern


def compile_pat(pat):
    """Compile a pattern normalised the way corpus text is normalised.

    canon_pattern, not canon: canon strips separators, which rewrites regex
    quantifiers ({0,4} -> {04}).
    """
    return re.compile(canon_pattern(pat))


# --------------------------------------------------------------------------
# Programmes. Order matters: an earlier entry's name may be the tail of a
# later one, and the lookbehinds below assume nothing about ordering, so both
# guards are explicit.
# --------------------------------------------------------------------------
PROGRAMME_PATTERNS = [
    ("progoti", r"প্রগতি"),
    ("bcup",    r"বিসিইউপি"),
    ("ncdp",    r"এনসিডিপি"),
    ("scdp",    r"এসসিডিপি"),
    # সিডিপি only when not the tail of এনসিডিপি / এসসিডিপি
    ("cdp",     r"(?<![নস])সিডিপি"),
    # দাবি only when not an insurance/death claim compound, joined or spaced
    ("dabi",    r"(?<!বিমা)(?<!বীমা)(?<!বিম)(?<!মৃত্যু)"
                r"(?<!বিমা )(?<!বীমা )(?<!মৃত্যু )দাবি"),
    # গতি only as a product: a product word must follow, and প্রগতি/অগ্রগতি/
    # সংগতি must not be what we are looking at. The lookahead keeps it off the
    # common nouns (গতিশীল, গতিধারা, গতানুগতিক) without enumerating them.
    ("goti",    r"(?<!প্র)(?<!অগ্র)(?<!সং)(?<!সঙ্)"
                r"গতি\s*(?=লোন|কর্মসূচি|প্রোডাক্ট)"),
]

PROGRAMMES = [(k, compile_pat(p)) for k, p in PROGRAMME_PATTERNS]

PROGRAMME_NAMES = {
    "dabi":    ("দাবি",       "দাবি কর্মসূচি; দাবি+; Dabi; Group Financing"),
    "progoti": ("প্রগতি",     "প্রগতি কর্মসূচি; Progoti; Individual Lending; MELA"),
    "bcup":    ("বিসিইউপি",   "BCUP"),
    "cdp":     ("সিডিপি",     "CDP"),
    "ncdp":    ("এনসিডিপি",   "NCDP"),
    "scdp":    ("এসসিডিপি",   "SCDP"),
}


def programmes_in(text):
    """Programme keys named in `text`, in PROGRAMMES order."""
    t = canon(text)
    return [k for k, rx in PROGRAMMES if rx.search(t)]


# --------------------------------------------------------------------------
# Named products: (key, name_bn, aliases, pattern)
# --------------------------------------------------------------------------
NAMED = [
    # --- দাবি loan products (§1.1.1, group financing)
    ("dabi_general_loan", "দাবি + সাধারণ লোন", "দাবি সাধারণ লোন",
     r"দাবি\s*\+?\s*সাধারণ\s*লোন"),
    # the changelog spells it স্বাধীণ ঋণ where §1.1.1 writes স্বাধীন লোন
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
    # the source misspells নিরাপত্তা as নিাপত্তা in §5.3.1 -- see corpus_gaps
    ("crop_insurance", "শস্য নিরাপত্তা বিমা", "শস্য বিমা; শস্য নিাপত্তা বিমা (sic)",
     r"শস্য\s*নি(?:রা)?পত্তা\s*বিমা|শস্য\s*বিমা"),
    ("fire_safety_insurance", "ফায়ার সেফটি ইন্স্যুরেন্স", "Fire Safety Insurance",
     r"ফায়ার\s*সেফটি\s*ইন্স্যুরেন্স"),
    ("livestock_insurance", "গবাদিপ্রাণি সুরক্ষা বিমা", "লাইভস্টক গ্রো; Livestock Grow",
     r"গবাদিপ্রাণি\s*সুরক্ষা\s*বিমা|লাইভস্টক\s*গ্রো"),
    ("dairy_cow_insurance", "দুগ্ধজাত গাভীর বিমা", "", r"দুগ্ধজাত\s*গাভীর\s*বিমা"),
    ("cattle_fattening_insurance", "গরু মোটাতাজাকরণ বিমা", "",
     r"গরু\s*মোটাতাজাকরণ\s*বিমা"),

    # --- named but never defined in either manual (see data/corpus_gaps.md)
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

# --------------------------------------------------------------------------
# Non-product entities: (key, entity_type, name_bn, aliases, pattern)
# --------------------------------------------------------------------------
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
