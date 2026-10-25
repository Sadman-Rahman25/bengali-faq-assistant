"""Checks on data/faq/answers.csv. Each one reports; none edits.

    python faq_check.py

  numbers    every figure in the Bengali answer is in the English one, and
             every figure in the English is in the Bengali -- compared as
             VALUES, so ২০ হাজার and 20,000 agree and ১ লক্ষ and 1 lakh agree.
             The English is a translation of approved text; a number that
             appears on one side only is a mistranslation or an addition.
  products   a product the Bengali names is named in English by the
             registry's name_en, with the Bengali name in brackets on its
             first mention, so an officer can match it to a Bengali form.
  roles      a post the Bengali names is named in English from ONE
             glossary, built from roles.py, so the same post never has two
             English names in two answers.
  words      the English never says "beneficiary" or "victim" (BRAND.md
             10.1: clients or members; survivor).
  latin      the Bengali uses no Latin-script word the manuals themselves do
             not print -- except the travelling-bill form's own labels, in
             the one answer about that form.
  nfc        every text field is NFC.
  cites      every citation names a known manual and a clause that exists
             in that manual's chunk manifest.

Every check proves it can fail on a fixture before it scans the file.
"""
import csv
import io
import json
import re
import sys
import unicodedata
from pathlib import Path

import faq

BN = "০১২৩৪৫৬৭৮৯"
FOLD = str.maketrans(BN, "0123456789")


# ============================================================ the glossary
def _initials(english):
    return "".join(w[0] for w in english.split() if w[:1].isupper())


def role_glossary():
    """{bengali form: english form}, built from roles.py.

    roles.py owns the posts and their Bengali names in each manual. The
    English abbreviation is the initials of its English title -- BM, AM,
    CDO -- and is added only where roles.py gives the post a Bengali
    abbreviation, because a post the manuals never abbreviate should not
    acquire one in translation. A Bengali abbreviation maps to the SAME
    English as the full title, so বিএম and শাখা ব্যবস্থাপক cannot drift.
    """
    from roles import ROLES
    out = {}
    for _key, dabi, progoti, abbr, english, _note in ROLES:
        # "Credit Development Officer (Dabi) / Programme Organiser (Progoti)"
        # is one post with a name per manual; split it the same way the
        # Bengali names are split.
        titles = [re.sub(r"\s*\((Dabi|Progoti)\)", "", s).strip()
                  for s in english.split(" / ")]
        names = [dabi] if dabi == progoti else [dabi, progoti]
        abbrs = [a.strip() for a in abbr.split("/") if a.strip()]
        for i, b in enumerate(names):
            e = titles[min(i, len(titles) - 1)]
            full = f"{e} ({_initials(e)})" if abbrs else e
            out[b] = full
            if len(abbrs) == len(names):
                out[abbrs[i]] = full
    # POSTS roles.py does not define, met in the approved answers. Kept in
    # this one table so the glossary stays single; each is a candidate to
    # move into roles.py when that file is next opened.
    out.update(EXTRA_ROLES)
    return out


EXTRA_ROLES = {
    "সহকারী এলাকা "
    "ব্যবস্থাপক":
        "Assistant Area Manager",
    "সহকারী আঞ্চলিক "
    "ব্যবস্থাপক":
        "Assistant Regional Manager",
    # the same post as বিভাগীয় ব্যবস্থাপক, written in English letters
    "ডিভিশনাল ম্যানেজার":
        "Divisional Manager (DM)",
    "সিনিয়র ম্যানেজার":
        "Senior Manager",
    # the manuals print "CSA (Customer Service Assistance)"; Assistant is
    # the post, and the typo is not carried into the glossary
    "সিএসএ": "Customer Service Assistant (CSA)",
    "স্টাফ লইয়ার":
        "Staff Lawyer",
    "তত্ত্বাবধায়ক":
        "supervisor",
}


# Case endings a post's name takes in running text. Without them the check
# saw "শাখা ব্যবস্থাপক" but not "শাখা ব্যবস্থাপকের" or "সিওর", so F-23 and
# F-31 -- which only ever use the inflected forms -- appeared to name no
# post at all.
SUFFIX = ("(?:ের|এর|র|কে|দের|"
          "রা|গণ|ও)?")


def roles_in(bengali, glossary):
    """Glossary English for every post the Bengali names, longest Bengali
    first so সহকারী এলাকা ব্যবস্থাপক is not also read as এলাকা ব্যবস্থাপক."""
    t = unicodedata.normalize("NFC", bengali or "")
    found = []
    for b in sorted(glossary, key=len, reverse=True):
        nb = unicodedata.normalize("NFC", b)
        if not nb:
            continue
        rx = re.compile(r"(?<![ঀ-৿])" + re.escape(nb) + SUFFIX
                        + r"(?![ঀ-৿])")
        if rx.search(t):
            found.append(glossary[b])
            t = rx.sub(" ", t)
    return sorted(set(found))


# ============================================================== the values
MULT_BN = {"হাজার": 1000,
           "লক্ষ": 100000, "লাখ": 100000,
           "কোটি": 10000000}
MULT_EN = {"thousand": 1000, "lakh": 100000, "crore": 10000000}
NUM = r"(\d+(?:\.\d+)?)"
CLAUSE = re.compile(r"\d+(?:\.\d+){2,}")
ORD_BN = re.compile(r"(\d+)(?:ম|য়|র্থ|"
                    r"ষ্ঠ)")
ORD_EN = re.compile(r"(\d+)(?:st|nd|rd|th)\b")


def values(text, lang):
    """The figures in `text`, as a multiset of numbers. -> sorted list

    Multipliers are applied (২০ হাজার -> 20000, 1 lakh -> 100000), digit
    grouping is ignored, ordinals keep their number (১ম and 1st are both
    1), and dotted clause numbers are compared as strings so §1.2.2 is one
    token on both sides rather than three.
    """
    t = unicodedata.normalize("NFC", text or "")
    if lang == "en":
        # Bengali inside an English answer is a bracketed original name --
        # "18 Mash Meyadi Loan (১৮ মাস মেয়াদি লোন)" -- and its digits are
        # the same figure stated twice, not a second one.
        t = re.sub(r"[ঀ-৿]+", " ", t)
    t = t.translate(FOLD)
    clauses = CLAUSE.findall(t)
    t = CLAUSE.sub(" ", t)
    t = ORD_BN.sub(r"\1", t) if lang == "bn" else ORD_EN.sub(r"\1", t)
    t = re.sub(r"(?<=\d),(?=\d)", "", t)
    mult = MULT_BN if lang == "bn" else MULT_EN
    out = []
    word = "|".join(map(re.escape, mult))
    for m in re.finditer(NUM + r"(?:\s*(" + word + r"))?", t):
        n = float(m.group(1))
        if m.group(2):
            n *= mult[m.group(2)]
        out.append(("%.4f" % n).rstrip("0").rstrip("."))
    return sorted(out + clauses)


def number_diff(bn, en):
    """-> (only in Bengali, only in English), each a list of values."""
    from collections import Counter
    a, b = Counter(values(bn, "bn")), Counter(values(en, "en"))
    return sorted((a - b).elements()), sorted((b - a).elements())


# ============================================================ the checks
BANNED = re.compile(r"\b(beneficiar\w*|victims?)\b", re.I)
LATIN = re.compile(r"[A-Za-z][A-Za-z.'@\-]*")
# The travelling-bill form's printed labels. Allowed in the answer about
# that form and nowhere else.
FORM_LABELS = {"F-48": {"Other", "Expenses"}}


def corpus_latin():
    import embed
    seen = set()
    for c in embed.load_corpus():
        for m in LATIN.finditer(c.get("display_text") or ""):
            seen.add(m.group(0).strip(".'-"))
    return seen


def manifest_clauses():
    doc = json.loads(Path("data/processed/chunk_manifest.json")
                     .read_text(encoding="utf-8"))
    out = {}
    for r in doc.get("chunks") or []:
        out.setdefault(r["manual_id"], set()).add(r.get("clause") or "")
    return out


def products_in(bengali, registry):
    """[(product_id, name_bn, name_en)] the Bengali names, via productmatch
    (Convention 2: no product literal here)."""
    import productmatch as pm
    return [(p, registry[p]["name_bn"], registry[p]["name_en"])
            for p in pm.products_in(bengali or "") if p in registry]


def registry():
    with io.open("data/registry/products.csv", encoding="utf-8") as f:
        return {r["product_id"]: r for r in csv.DictReader(f)}


def short(name_en):
    return name_en.split(" / ")[0].strip()


def canaries(glossary):
    """Convention 3: each check must be seen to fail before it is trusted."""
    ok = True
    bad = [
        ("numbers: a dropped figure",
         bool(number_diff("২৪% ও ১৮ মাস",
                          "24%")[0])),
        ("numbers: an added figure",
         bool(number_diff("২৪%", "24% and 18 months")[1])),
        ("numbers: ২০ হাজার equals 20,000",
         number_diff("২০ হাজার",
                     "Tk 20,000") == ([], [])),
        ("numbers: ১ লক্ষ equals 1 lakh",
         number_diff("১ লক্ষ", "Tk 1 lakh") == ([], [])),
        ("numbers: ১ লক্ষ is not 1 thousand",
         number_diff("১ লক্ষ", "1 thousand") != ([], [])),
        ("roles: শাখা ব্যবস্থাপক is found",
         "Branch Manager (BM)" in roles_in(
             "শাখা ব্যবস্থাপক",
             glossary)),
        ("roles: সহকারী এলাকা ব্যবস্থাপক is not an Area Manager",
         roles_in("সহকারী এলাকা "
                  "ব্যবস্থাপক",
                  glossary) == ["Assistant Area Manager"]),
        ("words: 'beneficiaries' is caught",
         bool(BANNED.search("loans for beneficiaries"))),
    ]
    for name, good in bad:
        print("  canary %-48s %s" % (name, "OK" if good else "FAILED"))
        ok = ok and good
    return ok


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    rows = faq.load()
    glossary = role_glossary()
    reg = registry()
    print("CANARIES")
    if not canaries(glossary):
        print("a check failed its own canary; nothing below is trusted")
        return 2
    print()
    hits = 0

    def report(rid, what, detail):
        nonlocal hits
        hits += 1
        print("  %-6s %-9s %s" % (rid, what, detail))

    allowed = corpus_latin()
    known = manifest_clauses()
    info = []
    for r in rows:
        rid = r["id"]
        bn, en = r["answer_bn"], r["answer_en"]
        for f in ("question_bn", "answer_bn", "question_en", "answer_en",
                  "note"):
            if r[f] != unicodedata.normalize("NFC", r[f]):
                report(rid, "nfc", f"{f} is not NFC")
        if en:
            only_bn, only_en = number_diff(r["question_bn"] + "\n" + bn,
                                           r["question_en"] + "\n" + en)
            if only_bn:
                report(rid, "numbers", f"in Bengali only: {only_bn}")
            if only_en:
                report(rid, "numbers", f"in English only: {only_en}")
            for e in roles_in(bn, glossary):
                if e not in en:
                    report(rid, "roles", f"needs \"{e}\"")
            # THE PRODUCT THE QUESTION ASKS ABOUT is held to the rule. A
            # product the body mentions in passing is not: productmatch
            # reads "বাধ্যতামূলক সাধারণ সঞ্চয়" -- a loan's compulsory
            # savings -- as the Sadharon Shonchoi product, and F-31's
            # "ট্রেড লোন" as the Progoti General Loan. Renaming those in
            # English would put a product name in a sentence that is not
            # about the product. Passing mentions are listed for reading.
            subject = {p for p, _b, _e in products_in(r["question_bn"], reg)}
            for pid, name_bn, name_en in products_in(bn, reg):
                if pid not in subject:
                    if short(name_en) not in en:
                        info.append(f"  {rid:<6} passing mention of "
                                    f"{name_bn}, not renamed")
                    continue
                subject.discard(pid)
                if short(name_en) not in en:
                    report(rid, "products", f"needs \"{short(name_en)}\"")
                elif f"({name_bn})" not in en:
                    report(rid, "products",
                           f"first mention needs \"({name_bn})\"")
            for m in BANNED.finditer(en + " " + r["question_en"]):
                report(rid, "words", f"\"{m.group(0)}\"")
        for m in LATIN.finditer(r["question_bn"] + " " + bn):
            tok = m.group(0).strip(".'-")
            if len(tok) < 2 or tok in allowed:
                continue
            if tok in FORM_LABELS.get(rid, ()):
                continue
            report(rid, "latin", f"\"{tok}\" in the Bengali")
        for c in faq.cite_list(r):
            mid, sep, clause = c.partition(" §")
            if not sep or mid not in known:
                report(rid, "cites", f"\"{c}\" names no known manual")
            elif clause not in known[mid]:
                report(rid, "cites", f"\"{c}\": no such clause in {mid}")
    if info:
        print("FOR READING (not hits)")
        print("\n".join(sorted(set(info))))
    print("RESULT")
    print(f"  {len(rows)} rows, {hits} hit(s)")
    return 1 if hits else 0


# Convention 1: importing this module never reads, writes or prints.
if __name__ == "__main__":
    sys.exit(main())
