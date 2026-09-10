# Corpus gaps — Progoti 2025 & Dabi 2025 operation manuals

**Status:** findings for review. Nothing here has been changed in the source
documents; `data/raw/` is untouched.

## What this is

A record of places where the two operation manuals are incomplete, internally
inconsistent, or contradict each other. Every entry was found while building
the retrieval corpus, and every one is evidenced by chunk IDs you can look up.

**This output does not depend on how the chatbot performs.** These are
properties of the manuals themselves. If the chatbot were abandoned tomorrow,
the list below would still be worth acting on, because each item is something
a colleague reading the manual could also get wrong. Several are the kind of
thing that only shows up when you try to answer a question mechanically and
find there is no text to answer it from.

Corpus as built: **831 chunks** — 431 from Progoti 2025, 400 from Dabi 2025.

## How to read the evidence

Chunk IDs (`progoti_0213`, `dabi_2025_0186`) refer to the processed corpus in
`data/processed/chunks_<tag>_final.jsonl`. Section numbers are the manuals'
own. To look one up:

```
python -c "import json;[print(c['display_text']) for c in map(json.loads, open('data/processed/chunks_progoti_final.jsonl',encoding='utf-8')) if c['chunk_id']=='progoti_0213']"
```

---

## 1. Named but never defined

Ten entries in `products_catalogue.csv` have an empty `defining_chunk_id`: the
manuals use the name, sometimes to attach a rule to it, but never say what it
is. A reader who does not already know the product cannot find out from these
documents.

### 1.1 আরোগ্য ঋণ — the sharpest case

| | |
|---|---|
| Appears in | `progoti_0003`, `progoti_0213`, `dabi_2025_0003`, `dabi_2025_0186` |
| Defined in | nowhere, in either manual |

The introduction lists it among what BRAC offers:

> …চাকরিজীবীদের জন্য বিশেষ ঋণ, এছাড়াও রয়েছে **আরোগ্য ঋণ**, বিদেশ গমনের জন্য
> মাইগ্রেশন ঋণ…

Then §5.1.2 attaches an operational rule to it:

> • **আরোগ্য ঋণের** গ্রাহক এই বিমা সুবিধার আওতায় আসবেনা।
> *(Customers of the Arogya loan will not come under this insurance cover.)*

So the manuals **exclude a product's customers from loan security insurance
without ever defining the product**. Chapter 1 lists every other loan product
with its ceiling, term, service charge and conditions; আরোগ্য ঋণ has none of
that. A field officer asked "is my আরোগ্য ঋণ customer covered?" can find the
answer. Asked "what is আরোগ্য ঋণ and who qualifies?", they cannot.

**Suggested action:** either add আরোগ্য ঋণ to §1.1 with the same fields as the
other loan products, or — if it is discontinued or belongs to another
programme — say so where it is named, so the §5.1.2 exclusion still makes
sense.

### 1.2 Programme acronyms with no expansion

| Acronym | Chunks | Expansion given anywhere |
|---|---|---|
| বিসিইউপি | 77 | none |
| সিডিপি | 37 | none |
| এনসিডিপি | 40 | none |
| এসসিডিপি | 40 | none |

These four appear throughout both manuals — in authority tables, complaint
routing, cash-management rules — but neither manual expands the acronym or
describes the programme. They appear in §1.1.3's heading only as a list of
programmes a rule applies to, which defines none of them.

**Suggested action:** a short glossary entry for each, ideally in Chapter 1.
This is the highest-volume gap in the list: 194 chunk appearances between
them.

### 1.3 Other named-but-undefined entries

| Name | Type | Chunks | Note |
|---|---|---|---|
| বিশেষ সঞ্চয় | product | 36 | Used as collateral for প্রত্যাশা লোন and referenced as "বিশেষ সঞ্চয় (ডিপিএস এবং মাসিক মুনাফা)", but never defined as a product in its own right |
| ক্রেডিট শিল্ড ইন্স্যুরেন্স | product | 2 | Named once in সূচনা only. **May be the English name for ঋণ নিরাপত্তা বিমা — please confirm.** If so it should be recorded as an alias, not a separate product |
| গার্ডিয়ান লাইফ ইন্স্যুরেন্স লিমিটেড | underwriter | 12 | Carries the risk on ঋণ নিরাপত্তা বিমা and ছায়া-সঞ্চয় নিরাপত্তা বিমা; never introduced |
| পাইওনিয়ার ইন্স্যুরেন্স কোম্পানি | underwriter | 4 | Co-underwrites ফায়ার সেফটি with সেনা; never introduced |
| ব্র্যাক মাইক্রোইন্স্যুরেন্স | BRAC unit | 18 | Receives claim documents; its role is never stated in one place |

The two underwriters matter operationally: an officer asking where to file a
claim needs to know which company carries the policy.

---

## 2. Spelling errors in the source

Both are in the source documents, not introduced by processing. Both survived
the Bijoy-to-Unicode conversion because they are faithful renderings of what
the source says.

### 2.1 শস্য নি**া**পত্তা — missing র

Should be **নিরাপত্তা**. The correct spelling appears in 158 chunks; this
misspelling appears in 4.

| Where | Chunks |
|---|---|
| §5.3.1 heading | `progoti_0264`, `dabi_2025_0236` |
| পরিবর্তন সমূহ (changelog) | `progoti_0391_p4`, `dabi_2025_0366_p4` |

The parent and child headings disagree with each other inside the same
document:

```
৫.৩ শস্য নিরাপত্তা বিমা          <- correct
৫.৩.১ শস্য নিাপত্তা বিমার গুরুত্ব ও সুবিধা:   <- typo
```

**Why it matters for search:** a reader searching the PDF for
"শস্য নিরাপত্তা" will not find the §5.3.1 subsection. This is the one
malformed cluster remaining after conversion, and it cannot be fixed
automatically — the র is simply absent from the source.

### 2.2 **অরগানোগ্রাম** — missing hasant

Should be **অর্গানোগ্রাম** (অর্‌-গা-নো-গ্রাম). The correct spelling appears
**zero** times; the misspelling appears in **66 chunks**, 32 per manual.

This is the Chapter 10 title, so every subsection in the chapter inherits it:

```
অরগানোগ্রাম > ১০.১ বিজনেস ডেভেলমেন্ট ইউনিট
অরগানোগ্রাম > ১০.২ দাবি অপারেশনাল স্ট্র্যাটেজি অ্যান্ড ইমপ্লিমেন্টেশন ইউনিট
…
অরগানোগ্রাম > ১০.১৪ সাব-ইউনিট সমূহ
```

Because it is consistent, it reads as a house spelling rather than a slip —
worth a decision either way rather than a silent fix. Note this was originally
typed in the legacy Bijoy encoding (`AiMv‡bvMÖvg`), where the reph mark is
genuinely absent, so the error predates any conversion.

---

## 3. Clause numbering that does not line up

### 3.1 §5.4 means different products in the two manuals

| Manual | §5.4 |
|---|---|
| Progoti 2025 | ফায়ার সেফটি ইন্স্যুরেন্স |
| Dabi 2025 | গবাদিপ্রাণি সুরক্ষা বিমা (লাইভস্টক গ্রো) |

This is the most serious numbering problem in the set. A citation of
"clause 5.4" without naming the manual does not merely lose precision — it
points at a **different insurance product** depending on which document the
reader picks up. The two products have different underwriters, different
premiums and different exclusions.

**Suggested action:** always cite manual + clause together in training
material, guidance notes and any system that quotes these documents. Longer
term, consider aligning Chapter 5 numbering across manuals.

### 3.2 Identical rules under different clause numbers

**15 passages** are word-for-word identical between the manuals but sit under
different numbers. Examples:

| Dabi 2025 | Progoti 2025 |
|---|---|
| §1.1.2 | §1.1.3 |
| §4.2 | §4.5.2 |
| §4.4.3 | §4.5.3 |
| §4.4.4 | §4.5.4 |
| §9.1.6 | §9.1.7 |
| §9.1.8 | §9.1.9 |

The text is the same, so the rule is the same; only the reference differs.
Anyone citing a clause number across teams should state which manual it comes
from.

---

## 4. The same passage under two clause numbers in one manual

One passage appears **twice within each manual**, under two different
subsections:

| Manual | Chunks | Clauses |
|---|---|---|
| Progoti 2025 | `progoti_0229`, `progoti_0243` | §5.1.8 and §5.1.9 |
| Dabi 2025 | `dabi_2025_0201`, `dabi_2025_0215` | §5.1.8 and §5.1.9 |

The passage begins:

> ৩য়-ধাপ: সিস্টেমে ইনপুট ও বিমাদাবি ফর্ম ডাউনলোড — শাখা হিসাব কর্মকর্তার কাজ…

§5.1.8 is *বিমাদাবি পদ্ধতি* and §5.1.9 is *ইনসিডেন্ট নোটিফিকেশন ও সেটআপ*.
The identical step-3 procedure is printed under both, in both manuals.

**Suggested action:** confirm whether the duplication is intentional (the same
step genuinely belonging to two procedures) or a copy-paste error. If
intentional, a cross-reference would be clearer than a repeat; if not, one
copy should go.

---

## 5. Not gaps — fixed during processing

Recorded so nobody re-reports them as source problems:

| Issue | Resolution |
|---|---|
| Legacy Bijoy (SutonnyMJ) text, ~4.2% of both manuals | Converted to Unicode; 1.68 (Progoti) / 1.70 (Dabi) malformed clusters per 1000, against a tolerance of 3 |
| গার্ডিয়ান লাইফ spelled three ways (`গার্ডিান`, `ইইস্যুরেন্স`, `ইনশুরেন্স`) | All variants matched onto one registry row |
| Mixed Bengali and ASCII digits for the same figures | Normalised at index and query time |
| স্বাধীন লোন vs স্বাধীণ ঋণ (§1.1.1 vs changelog) | Both forms matched onto one product key |

The spelling variants above are worth a light copy-edit pass in the source,
but they do not block anything.

---

## Summary of suggested actions

| Priority | Item | Owner |
|---|---|---|
| High | Define আরোগ্য ঋণ, or explain the §5.1.2 exclusion | Product team |
| High | Always cite manual + clause; §5.4 differs between manuals | All authors |
| Medium | Glossary for বিসিইউপি / সিডিপি / এনসিডিপি / এসসিডিপি | Product team |
| Medium | Resolve the §5.1.8 / §5.1.9 duplicate passage | Insurance unit |
| Medium | Confirm whether ক্রেডিট শিল্ড is ঋণ নিরাপত্তা বিমা | Insurance unit |
| Low | Fix শস্য নি**া**পত্তা in §5.3.1 | Copy-edit |
| Low | Decide on অরগানোগ্রাম vs অর্গানোগ্রাম | Copy-edit |
| Low | Introduce the underwriters where first named | Insurance unit |

---

## Reproducing this

```
python products.py        # -> products_catalogue.csv (defining_chunk_id blank = undefined)
python dupscan.py         # cross-manual duplicates and clause divergence
python dedupe.py --check  # duplicate groups, including the §5.1.8/§5.1.9 case
python pattern_audit.py   # confirms the name matching behind these counts
```

Counts in this document were taken from the corpus built on 2026-09-10.
