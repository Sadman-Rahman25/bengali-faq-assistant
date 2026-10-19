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
| ক্রেডিট শিল্ড ইন্স্যুরেন্স | product | 2 | **ANSWERED — see §1.4. It is the English name for ঋণ নিরাপত্তা বিমা, and is now an alias rather than a separate product** |
| গার্ডিয়ান লাইফ ইন্স্যুরেন্স লিমিটেড | underwriter | 12 | Carries the risk on ঋণ নিরাপত্তা বিমা and ছায়া-সঞ্চয় নিরাপত্তা বিমা; never introduced |
| পাইওনিয়ার ইন্স্যুরেন্স কোম্পানি | underwriter | 4 | Co-underwrites ফায়ার সেফটি with সেনা; never introduced |
| ব্র্যাক মাইক্রোইন্স্যুরেন্স | BRAC unit | 18 | Receives claim documents; its role is never stated in one place |

The two underwriters matter operationally: an officer asking where to file a
claim needs to know which company carries the policy.

### 1.4 ক্রেডিট শিল্ড ইন্স্যুরেন্স — answered, 2026-10-17

**The open question in §1.3 is closed.** ক্রেডিট শিল্ড ইন্স্যুরেন্স and
ঋণ নিরাপত্তা বিমা are **one product under two names**, not two products.

| | |
|---|---|
| Source | BRAC field-team review of the product registry, 2026-10-17 |
| Recorded in | `data/registry/products.csv`, row `loan_security_insurance` (`name_en: Credit Shield Insurance`) |
| Applied in | `productmatch.NAMED` — folded into `loan_security_insurance` as an alias; the separate `credit_shield_insurance` key is gone |

**Why it mattered more than a naming tidy.** The two keys were not equal
halves of an ambiguity. `loan_security_insurance` carried
`defining_chunk_id: progoti_0210`; `credit_shield_insurance` carried none,
because সূচনা names it once and never describes it. Anything joining on the
English name reached a row with no definition behind it, and anything joining
on the Bengali name reached the real one. The registry as first delivered
keyed the product on the empty one, so an id join and a name join disagreed
about the same insurance. That is now a single row.

**Still open, and not the same question:** `পাইওনিয়ার ইন্স্যুরেন্স` and
`চার্টার্ড লাইফ` remain undescribed. The field team added চার্টার্ড লাইফ to
`insurers.csv` and it appears in neither manual, so it has no catalogue row
at all — `python registry.py --check` reports it, by design, rather than
inventing one.

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

## 3.3 No loan-approval ceiling exists for সিডিও or পিও

Neither manual states how much a সিডিও (Dabi) or পিও (Progoti) may approve.
Verified by line-by-line search of both corpora.

**What §7.1 প্রাধিকারসূচি actually covers.** Despite its name — টেবিল অব
অথরিটি — chapter 7 contains **zero** loan-approval rows. Every row is
procurement or administration:

| Row | Subject |
|---|---|
| ১ | স্থায়ী সম্পদ: টেবিল, চেয়ার, র‍্যাক, ফ্যান, আলমারী |
| ৯ | বাউন্ডারী ওয়াল মেরামত, রং করা, টিউবওয়েল স্থাপন |
| ১২ | নতুন অফিস ভাড়ার অনুমোদন |
| ১৫ | জরুরিভিত্তিতে কর্মসূচির প্রয়োজনে খরচ |

Its own preamble says so: *ক্রয় ও অন্যান্য অনুমোদনে কিছু পরিবর্তন ও
আধুনিকীকরণ করা হয়েছে*.

**The manual points at a clause it does not contain.** The পরিবর্তন সমূহ
changelog records:

> পরিবর্তণ: **ঋণ অনুমোদন সিলিং বৃদ্ধি — ২.১.১(ট) — ১৮**

So the ceiling exists and was raised. But **no ২.১.১(ট) heading is present in
either manual.** §2.1.1 runs (ক) through (ছ) and stops; there is no (ট). The
document cites its own clause that isn't there.

The nearest related content, none of which answers the question:

| Section | What it says |
|---|---|
| §2.1.2(গ) | সিডিও/সিও notifies the member **after** approval — does not approve |
| §4.4.6 ঋণ অনুমোদন | covers the এলাকা ব্যবস্থাপক, no taka figure |
| §2.1.2(গ), Progoti | *প্রগতি কর্মসূচির ঋণ অনুমোদন সিলিং* … refers out to পরিপত্র প/০২/২০১৮, প/২১/২০১৯, প/০৯/২০২২, প/৮১/২০২২ |

**Why this one is dangerous rather than merely missing.** A reader looking for
lending authority in a chapter called "Table of Authority" will find taka
figures against named roles — ২ লক্ষ টাকা পর্যন্ত বিভাগীয় ব্যবস্থাপক, and so
on — and they are the wrong figures. They authorise buying furniture and
renting offices. Nothing on the page says it is not about lending. This is the
one gap in this document where the plausible wrong answer is sitting in the
manual, formatted like the right one.

**Suggested action:** either add the ২.১.১(ট) clause the changelog already
references, or state in §7.1 that lending authority is governed separately and
name the পরিপত্র. Until then, an answer to "how much can a সিডিও approve?"
should be a pointer to the circular, not a figure. This case is carried in
`data/eval/bootstrap_eval.json` as category `unanswerable_in_corpus`, with a
seeded `must_not` against quoting the §7.1 procurement limits.

---

## 3.4 §2.1.1(চ) carries two different group-loan approval tables

Both manuals print **two** new-loan approval tables under the same clause
§2.1.1(চ), and they disagree about the branch manager's lower bound. Neither
table is labelled in a way that tells a reader which one applies to them.

| chunks | new-loan row for বিএম |
|---|---|
| `progoti_0056` / `dabi_2025_0049` (twins) | `২০০০০-৭০০০০` under `উচচ চাহিদা`, and `১৫০০০-৬০০০০` under `সাধারণ চাহিদা` |
| `progoti_0057` / `dabi_2025_0050` (twins) | `১৫০০০-৭০০০০`, with no demand category given |

The first table is split by branch demand category (`উচচ চাহিদা` /
`সাধারণ চাহিদা`); the second is not, and its band is neither of the first
table's. An officer asking "who approves a new loan up to ৭০,০০০?" gets
**বিএম** from both, so the authority is unambiguous — but the lower bound
they would quote is ২০,০০০ or ১৫,০০০ depending on which chunk they happen to
read, and nothing in either chunk says which table governs.

**How it surfaced.** The retriever returned `progoti_0057` for G-207 and the
model answered `১৫,০০০ – ৭০,০০০ — বিএম`, quoting it verbatim. That was
recorded in the project journal as a fabricated figure on the grounds that
`১৫,০০০` appears in neither manual. It appears 12 times in 4 chunks. The
journal entry is corrected; this is the underlying document defect.

**Suggested action.** Label the second table, or state which branch types it
covers. Medium priority — the authority is right either way, but a quoted
lower bound can be wrong by ৫,০০০ with no way for the reader to tell.

Reproduce:

```
python -c "import sys;sys.path.insert(0,'.');from bn_shared import count_literal;from embed import load_corpus;print(count_literal('১৫০০০',[c['display_text'] for c in load_corpus()],warn=False))"
python show_chunk.py progoti_0056 progoti_0057
```

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

## 6. Cross-manual duplication makes text-match gold location unusable

Not a gap in the manuals — a measured property of the corpus that changes how
a gold question can be scored. Recorded here because it is the reason a
retrieval number can look fine and mean nothing.

**516 of 867 chunks (60%) have byte-identical `index_text` across the two
manuals, in 258 cross-manual duplicate groups.**

| | |
|---|---|
| Measured on | corpus of 2026-09-17 (867 chunks; the 831 figure above is the 2026-09-10 build) |
| Duplicate groups spanning >1 manual | 258 |
| Chunks in such a group | 516 of 867 |
| Worked example | `dabi_2025_0049` and `progoti_0056` — identical `index_text`, so identical dense and BM25 scores, always returned adjacently |

### Why it matters for gold scoring

A gold question that carries `manual_scope: progoti` and
`must_cite: ["progoti_2025"]` names one manual on purpose. Locating its gold
chunk by searching the corpus for the fact's text cannot honour that: for 60%
of the corpus the Dabi twin matches the same string equally well, so a
retrieval that returns the wrong manual scores as a hit. Text-match
**structurally cannot resolve a manual-scoped question.**

Two further consequences, both measured on `gold/bootstrap.yaml`:

- The gold set inflates. Locating by text gave G-222 **153** candidate chunks
  (18% of the corpus) and G-213/G-214 **53** each. Recall@5 against a set that
  size is close to meaningless. Locating by `source_refs.chunk_id` gives 2, 1
  and 1.
- Recall becomes a function of chunking. Re-splitting the corpus changes how
  many chunks contain a given string, so the number moves without retrieval
  changing.

It also cannot express a constraint the gold file already states. G-206's
notes require its ref to be a nested-table chunk, "not a prose chunk that
happens to mention the figure" — a distinction a text match cannot make and a
`chunk_id` makes for free.

### What was done

`embed.locate_gold_chunks` now resolves a gold fact through
`source_refs.chunk_id`, mirroring `validate_gold.py`, and expands a ref only
to duplicates **explicitly declared** by `dedupe.py` (`dup_members`, threshold
exactly 1.000). Nothing is inferred by comparing text.

One follow-up remains: `dedupe.py`'s `dup_*` annotations are present in
`chunks_<tag>_dedup.jsonl` but **not** in the `chunks_<tag>_final.jsonl` files
that `CORPUS_FILES` actually loads — they were dropped when the final files
were regenerated on 2026-09-16. Until `dedupe.py` is re-run over the final
corpus, the duplicate expansion is inert and a ref resolves to exactly one
chunk. That is safe (it under-counts rather than over-counts) but it means a
legitimately duplicated citation currently reads as a miss.

---

## 7. §3.9.6 is asymmetric — the Progoti manual carries both programmes' rule

A real gap in the manuals, not a pipeline artefact, and the only place in
§3.9 where the programme divergence is stated at all.

§3.9.6 (থানা পুলিশ, উকিল নোটিশ, মামলা দায়ের ও হাজিরা এবং মামলা পরিচালনা
সংক্রান্ত খরচ) sets out who approves litigation costs. It does so at two
levels, and the second level differs by programme:

| Level | Approver | Same in both manuals? |
|---|---|---|
| Provisional expense (সাময়িক খরচ) | আঞ্চলিক ব্যবস্থাপক | yes |
| Bill-voucher (বিল-ভাউচার) | **শাখা ব্যবস্থাপক** for দাবি, **এলাকা ব্যবস্থাপক** for প্রগতি | **no — see below** |

### The asymmetry

| | states the দাবি rule | states the প্রগতি rule |
|---|---|---|
| `progoti_0152` | yes | yes |
| `dabi_2025_0136` | yes | **no** |

`progoti_0152`:

> আঞ্চলিক ব্যবস্থাপক কর্তৃক সাময়িক অনুমোদন প্রাপ্তি সাপেক্ষে দাবি কর্মসূচির
> ক্ষেত্রে সংশ্লিষ্ট শাখা ব্যবস্থাপক **এবং প্রগতি কর্মসূচির ক্ষেত্রে সংশ্লিষ্ট
> এলাকা ব্যবস্থাপক** এতদসংক্রান্ত বিল-ভাউচার অনুমোদন করবেন।

`dabi_2025_0136`:

> আঞ্চলিক ব্যবস্থাপক কর্তৃক সাময়িক অনুমোদন প্রাপ্তি সাপেক্ষে দাবি কর্মসূচির
> ক্ষেত্রে সংশ্লিষ্ট শাখা ব্যবস্থাপক বিল-ভাউচার অনুমোদন করবেন।

**A Dabi officer reading the Dabi manual cannot learn the Progoti rule. The
reverse is not true** — the Progoti manual states both. The Dabi text also
narrows the first sentence to "(দাবি)" where Progoti writes "(দাবি/প্রগতি)".

### Why it matters beyond the two clauses

**These are the only two chunks in §3.9 that are not a cross-manual twin.**
Every other approval clause in the section is either byte-identical across
the manuals or carries the same sentence in both:

| clause | who approves | cross-manual twin? |
|---|---|---|
| §3.9.1(ক) staff | পরিচালক | no, but same sentence |
| §3.9.1(খ) borrowers | ডিসিএমসি কমিটি | yes — `99b1bafd2ed1` |
| §3.9.2 | ডিসিএমসি (delegated by ED circular, 7 June 2014) | yes — `2432ac1dd2b0` |
| §3.9.5 withdrawal | no approver named | yes — `aaf3f5974199` |
| **§3.9.6 costs** | **আঞ্চলিক ব্যবস্থাপক, then splits by programme** | **NO** |
| §3.9.7 panel-lawyer bills | আঞ্চলিক ব্যবস্থাপক / জেলা ব্যবস্থাপক | yes — `4c1789d50677` |

So the whole programme divergence in approval authority for administrative
action lives in one clause, in one manual, in one sentence. Any answer about
bill-voucher authority that does not name the programme is right half the
time, and the officer cannot tell which half.

### A related trap in the same chunk

`dabi_2025_0136` **contains the string এলাকা ব্যবস্থাপক** — inside
"সংশ্লিষ্ট শাখা/এলাকা ব্যবস্থাপক (দাবি)" in its first sentence, where it is
part of a combined job title, not the bill-voucher approver. A system that
retrieves the correct Dabi chunk and reads the wrong sentence will hand a Dabi
officer the Progoti answer. G-212 carries `এলাকা ব্যবস্থাপক` in
`must_not_contain_literals` for exactly this reason.

### Suggested action

Insurance/operations to decide whether the Dabi manual should state the
Progoti rule too. Either is defensible — a manual may legitimately scope
itself to its own programme — but the current state is not symmetric, and a
reader cannot tell that a rule exists elsewhere. Anchored in the gold set by
G-212, G-213 and G-214, all three on §3.9.6.

---

## 8. The table of contents is not in the corpus — known, and deliberate by accident

Not a gap in the manuals. A **coverage** fact about the corpus, recorded here
because anyone auditing what was ingested will find several hundred paragraphs
missing from each manual and should not have to work out why from scratch.

**What is missing.** Every table-of-contents line in both manuals.

| Manual | Paragraphs in the ToC block | ToC-styled, with text | Appearing in any chunk |
|---|---|---|---|
| Dabi 2025 | 234 | 232 | **0** |
| Progoti 2025 | 247 | 245 | **0** |

(Progoti shows one apparent match on a naive substring test. It is a ToC line
whose entire text is the page number `1`, which trivially occurs inside other
chunks. No ToC *entry* is ingested in either manual.)

**Why.** The whole ToC sits inside a single `w:sdt` — a Word content control,
the element Word writes around a generated field — and that `w:sdt` is a
direct child of `w:body`. `ex.py` walks the body's children and handles two
tags:

```python
for ch in d.element.body.iterchildren():
    if ch.tag == qn("w:p"):      ...
    elif ch.tag == qn("w:tbl"):  ...
```

`w:sdt` is neither, so the block is skipped whole. Nobody decided to exclude
the ToC; it falls out of the walk. Hence *deliberate by accident* — the
outcome is right, the reason it happens is not a reason anyone chose.

**Why it is not a retrieval loss, and should stay excluded.** A ToC line is a
heading plus a page number, and both halves are already handled better
elsewhere:

- The heading half is a **duplicate**. 225 of Dabi's 232 ToC lines and 235 of
  Progoti's 245 are, once the trailing page number is removed, exactly a
  `heading_path` value already carried by the chunks of that section. Ingesting
  them would add several hundred chunks of bare heading text with no body —
  strong lexical matches that answer nothing, competing in the top-k with the
  sections they name.
- The page-number half is **actively misleading**. As recorded elsewhere in
  this repo, a `.docx` has no pagination until it is rendered, and 831 of 867
  chunks carry no page number for exactly that reason. The ToC's numbers come
  from whatever Word last computed, which is why the field team's marked copy
  of Dabi prints `৯.১.৬ … 84` where the corpus source prints `… 90` for the
  same line. A retrievable chunk asserting a page number that does not match
  the printed manual is the plausible-wrong-answer failure this project is
  built around.

The remainder — 7 Dabi and 10 Progoti ToC lines that are *not* an exact
heading duplicate — differ only by the heading defects already catalogued
above (the missing `র` in §2.1, trailing colons, and the headings in
`out/heading_loss.md` that reach no chunk).

**If anyone ever does ingest it,** the fix is to descend into `w:sdt` in the
body walk, and it should be accompanied by a rule that drops ToC-styled
paragraphs rather than chunking them. Ingesting the block without that rule is
the harmful version of this change.

**Confirm it:**

```
python -c "import zipfile,re;d=zipfile.ZipFile('data/processed/dabi_manual_2025_unicode.docx').read('word/document.xml').decode();print('sdt blocks in body:',d.count('<w:sdt>'))"
python -c "import json;print(sum(1 for c in map(json.loads,open('data/processed/chunks_dabi_2025_final.jsonl',encoding='utf-8')) if 'ঊর্ধ্বতন পরিচালকের বাণী 3' in c['display_text']))"   # -> 0
```

---

## Summary of suggested actions

| Priority | Item | Owner |
|---|---|---|
| High | Add the ২.১.১(ট) loan-approval ceiling the changelog cites, or say in §7.1 that lending authority is governed separately | Operations |
| High | Define আরোগ্য ঋণ, or explain the §5.1.2 exclusion | Product team |
| High | Always cite manual + clause; §5.4 differs between manuals | All authors |
| Medium | Glossary for বিসিইউপি / সিডিপি / এনসিডিপি / এসসিডিপি | Product team |
| Medium | Resolve the §5.1.8 / §5.1.9 duplicate passage | Insurance unit |
| ~~Medium~~ DONE | ~~Confirm whether ক্রেডিট শিল্ড is ঋণ নিরাপত্তা বিমা~~ — answered 2026-10-17, see §1.4 | Field team |
| High | Re-run `dedupe.py` over `*_final.jsonl` so `dup_*` survives (see §6) | Pipeline |
| Medium | Decide whether the Dabi manual should state the Progoti bill-voucher rule in §3.9.6 (see §7) | Operations |
| Medium | Label the second §2.1.1(চ) approval table, or say which branches it covers (see §3.4) | Operations |
| Low | Fix শস্য নি**া**পত্তা in §5.3.1 | Copy-edit |
| Low | Decide on অরগানোগ্রাম vs অর্গানোগ্রাম | Copy-edit |
| Low | Introduce the underwriters where first named | Insurance unit |
| High | Registry rule 9: do existing এককালীন স্থায়ী সঞ্চয় প্রকল্প accounts still earn ৩ মাসে ৮%, ৬ মাসে ৯%, ১২ মাসে ১০%? `products.csv` lists the product as suspended; rule 9 says still_valid. Not in `not_available.csv`: a match there replaces the suspension line instead of following it | Field team |
| High | Registry rule 11 (temporary charge now এবিএম or এএম) is scoped to Dabi. Progoti §3.1.3(ক) prints the same bullet but for one word: the last-resort stand-in is সিনিয়র সিডিও in Dabi and সিনিয়র পিও in Progoti -- the same role under each manual's title. Does the change apply to Progoti? Until answered, Progoti answers quote the manual. (Rule 10's Progoti text, §2.1.2(গ), was word-for-word identical and is now scoped to both) | Field team |
| High | Registry rule 23: does "এখন যেকোনো বয়সে" mean the ছায়া-সঞ্চয় insurance benefit now covers DPS holders of any age? §5.2.3's limit is a condition of the insurance benefit, not of opening a DPS; the row's old wording, "ডিপিএস বয়সসীমা", read as the latter. Set to not_confirmed until answered: the bot now says the current age limit for the benefit could not be confirmed | Field team |

---

## Reproducing this

```
python products.py        # -> products_catalogue.csv (defining_chunk_id blank = undefined)
python dupscan.py         # cross-manual duplicates and clause divergence
python dedupe.py --check  # duplicate groups, including the §5.1.8/§5.1.9 case
python pattern_audit.py   # confirms the name matching behind these counts
python -c "import sys;sys.path.insert(0,'.');from embed import index_text,load_corpus;from collections import defaultdict;g=defaultdict(list);[g[index_text(c)].append(c['chunk_id']) for c in load_corpus()];d=[v for v in g.values() if len(v)>1];print(f'{len(d)} groups, {sum(map(len,d))} chunks')"  # -> the 258 / 516 figures in section 6
```

Counts in this document were taken from the corpus built on 2026-09-10.
