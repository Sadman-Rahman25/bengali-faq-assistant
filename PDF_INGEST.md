# Adding a manual from PDF — Livestock Grow

Ingestion and evaluation for a manual that arrives as a PDF instead of a
`.docx`. Added for **Livestock Grow** (`manual_id: livestock_grow_2026`),
but every script takes the PDF and the manual id as arguments, so the same
path serves the next one.

Same conventions as the docx path: `main()` behind `__main__`, no writes at
import, product literals owned by `productmatch`, every check carries a
canary built from a real failure. `conventions_check.py` passes CLEAN with
these files in place.

```
pip install pymupdf pdfplumber pypdf pdfminer.six pyyaml
# optional: apt-get install poppler-utils   (pdftotext route in the shootout)
```

---

## Status: ingested from a transcription, figures NOT yet human-verified

`livestock_grow_2026` is in the corpus: 36 chunks, 8 pages,
`source_type: "ocr"`, `extraction_confidence: "unverified"`. The PDF text
layer is unrecoverable (below), no `.docx` exists — it was requested — and
the renders are perfectly legible, so the 8 pages were transcribed from
200dpi renders into `data/raw/livestock_grow_2026_transcribed.md` and chunked
by `ex_text.py`.

```bash
python probe_words.py data/raw/livestock_grow_2026_transcribed.md \
    --probes probes_livestock_grow.csv            # 41/41 = 100% CLEAN
python ex_text.py data/raw/livestock_grow_2026_transcribed.md \
    --manual-id livestock_grow_2026 \
    --out out/livestock_grow_2026/chunks.jsonl    # 36 chunks, 0 orphan
python digit_audit.py out/livestock_grow_2026/chunks.jsonl \
    --pdf "data/raw/Operational Manual_Livestock (2026).pdf" \
    --html out/livestock_grow_2026/figure_worklist.html
python retag.py   out/livestock_grow_2026/chunks.jsonl   # 0 of 36 changed
python resplit.py out/livestock_grow_2026/chunks.jsonl   # 0 oversized
```

**Before this is embedded, someone other than the transcriber must read
`out/livestock_grow_2026/figure_worklist.html` against the renders and flip
`extraction_confidence` to `verified`.** A transcription is OCR output, not a
source document. 127 figures were extracted and one is flagged
(`২০২২-২৩`, a fiscal year — a false positive to clear by eye), but the audit
only flags *implausible* figures; a premium silently transcribed as ২.৪৫%
instead of ২.৩৫% would pass every check in this repo.

Evidence the transcription is sound, independent of the probe set:
**0.0 malformed clusters per 1000 and 0 legal-damage hits**, against 56.3/1000
for the PDF text layer. Note that `probes_livestock_grow.csv` is calibrated
*from* the transcription, so its 100% coverage proves nothing about accuracy —
it is a regression guard for a future re-transcription or a repaired text
layer. That circularity is stated in the CSV's own header.

### Why the PDF text layer could not be used

Two independent reasons, both established by running stages 1–4.

**1. The text layer is broken.** All six working extraction routes return
*identical* corruption, which is the signature of a wrong ToUnicode CMap
rather than a library problem:

| route | bn chars | probe | mal/1k | verdict |
|---|---|---|---|---|
| pymupdf:text / blocks / rawdict | 14,956 | 25.0% | 56.3 | CORRUPT |
| pdfplumber | 15,236 | 25.0% | 56.3 | CORRUPT |
| pypdf | 15,236 | 25.0% | 56.2 | CORRUPT |
| pdfminer.six | 15,236 | 25.0% | 56.2 | CORRUPT |

Spread 0.0%. **0 of 60** registry literals are present — `ঋণ` included.
Triage reports no legacy font (Kalpurush throughout, `UNICODE_TEXT`), so
`bijoy_convert.py` does not apply, and it is docx-only regardless. What the
extraction actually looks like:

```
extracted:  “লাইভস্টক গ্রা” প্রকল্পের অপাল্পরশিাল মযািুয়াল
should be:  “লাইভস্টক গ্রো” প্রকল্পের অপারেশনাল ম্যানুয়াল

extracted:  বাাংলাদেদে ম াট গবাদেপ্রাদির সাংখ্যা ২৪.৮ দ দলয়ন
should be:  বাংলাদেশে মোট গবাদিপ্রাণির সংখ্যা ২৪.৮ মিলিয়ন
```

Glyphs come out in **visual rather than logical order** (pre-base matras
precede their consonant) *and* land on wrong codepoints. Kalpurush is
embedded four times — twice as `Type0/Identity-H` and twice as
`TrueType/WinAnsiEncoding`, in two different subsets (110,636 and 92,456
bytes). Two interleaved wrong mappings is why the substitution pattern looks
self-inconsistent, and why a single find-and-replace table will not fix it.

**2. It is not a clause-numbered manual.** 8 pages of narrative. Of 220
non-empty lines, the only numbered ones are an insurance exclusions list.
`pdf_structure.py` fails it at a **21.9% orphan rate** against a 2% gate.
Even with perfect text there is no clause spine here, so the `must_cite`
fields in the gold set — written for `§5.4`-style citations — do not apply to
this document as it stands.

### Why not a font-based repair, and why not `ocr_bench.py`

**Font repair is impossible for this file.** The fonts *are* embedded, so
recovering the true glyph→Unicode mapping looked promising — but the
Kalpurush subsets name **497 of their 586 glyphs `glyph000NN`** and `GSUB`
has been stripped. Only the 61 basic letters and matras carry recoverable
identity, which is exactly why simple words like `লাইভস্টক` survive and every
conjunct mangles. The information needed to rebuild the conjuncts is not in
the file. (Extract them yourself with `doc.extract_font(xref)` and
`fontTools` if you want to confirm before trusting this paragraph.)

**`ocr_bench.py` is heavy for 8 pages.** It needs an OCR engine installed
(`pytesseract` + `tesseract-ocr-ben`, or `easyocr`, or a `GEMINI_API_KEY`)
**and** a hand-typed ground-truth gold set — `init` only scaffolds the
folders. Someone has to type correct Bengali for the sample pages before any
engine can be scored. For a 100-page manual that is the right investment;
for 8 legible pages, transcribing them directly is cheaper than building the
apparatus to grade a machine doing the same job.

It remains the right tool if this manual grows, or if a second PDF-only
manual arrives. The transcription route does not scale and should not be
treated as the default.

---

## Before you run anything

Ask the programme team for the `.docx`. Dabi and Progoti were both authored
in Word. One email versus several days of OCR benchmarking, and the docx
gives back real Unicode *and* real heading styles — the two things this path
has to reconstruct. Bundle the request with the outstanding Dabi 2026 one.

Everything here is the fallback.

---

## Run order

```bash
./run_livestock_grow.sh data/raw/livestock_grow.pdf livestock_grow_2026
```

Or stage by stage:

| Step | Command | Gate |
|---|---|---|
| 1 | `pdf_triage.py <pdf> --dump out/raw.txt` | legacy font present → Bijoy conversion first |
| 2 | `pdf_extract_check.py <pdf> --html out/sbs.html --pages 1-12` | `CLEAN` → step 4, else step 3 |
| 3 | `pdf_shootout.py <pdf> --save-best out/best.txt` | all routes equal and bad → `ocr_bench.py` |
| 4 | `pdf_structure.py <pdf> --out out/structure.json` | orphan rate > 2% → fix numbering, do not proceed |
| 5 | `ex_pdf.py <pdf> --manual-id livestock_grow_2026` | oversized chunks must be 0 |
| 5b | `ex_text.py <md> --manual-id …` — only when the text layer is unrecoverable and the pages were transcribed | 0 orphans, 0 oversized |
| 6 | `digit_audit.py out/…/chunks.jsonl --pdf <pdf>` | read every flagged figure |
| 7 | `retag` / `resplit` / `dupscan` / `dedupe` / `roles` | `conventions_check.py` exits 0 |

---

## Settle this before you embed anything

`productmatch.NAMED` already contains Livestock Grow — as an **alias**:

```python
("livestock_insurance", "গবাদিপ্রাণি সুরক্ষা বিমা", "লাইভস্টক গ্রো; Livestock Grow",
 r"গবাদিপ্রাণি\s*সুরক্ষা\s*বিমা|লাইভস্টক\s*গ্রো"),
```

So the repo currently believes Livestock Grow *is* গবাদিপ্রাণি সুরক্ষা বিমা,
an insurance product defined in Dabi §5.4.3 and underwritten by সেনা and
গ্রীন ডেল্টা. If this manual defines Livestock Grow as a **loan**, or as a
loan-plus-insurance package, that entry is wrong, and:

- every chunk in the new manual inherits the wrong product identity;
- retrieval answers Livestock Grow questions out of Dabi §5.4;
- **no check in this path would notice.** The figures would all be
  internally consistent and attached to the wrong product. This is the
  `গতি`-inside-`প্রগতি` failure again, one level up: a plausible tag on real
  text.

Gold question **G-101** is written to settle it. Answer it from the manual
first. Per the `গতি লোন` lesson, look at the instances before deciding.

Note also: `reconcile.py` is referenced by the ingestion record and by the
old run script, but **does not exist in this repo**. Until it does, product
reconciliation is a manual step.

---

## What is new versus the docx path, and why

**`probe_words.py` — the positive control you don't currently have.**
Every existing check is negative: it looks for the absence of bad shapes.
All six of them passed on data they could not see. This one asserts that
words which must be present *are* present.

```bash
python probe_words.py out/livestock_grow_2026/raw.txt
```

Its canary is the recorded Dabi 2026 extraction: 0.40 coverage → `CORRUPT`,
while the same sentences spelled correctly score 1.00 → `CLEAN`. Both
numbers are asserted, not just the verdicts — a check that called everything
CORRUPT would pass a verdict-only canary while being useless.

**Two panels, and only one of them gates.** `probes_livestock_grow.csv`
gates. The registry vocabulary panel — every product and role literal pulled
live from `productmatch` and `roles` — is printed and gates nothing.

That split is load-bearing. A blanket "every known product literal must
appear at least once" rule scores a *clean* Livestock Grow manual as
CORRUPT, because it does not mention প্রত্যাশা লোন or মাইগ্রেশন লোন and has
no reason to. Coverage collapses for a vocabulary reason and reads exactly
like a CMap fault. A check that cries corruption on good input gets switched
off, and then it protects nothing.

`probes_livestock_grow.csv` holds domain, livestock and structural
vocabulary. Product and role literals come from `productmatch` / `roles` at
runtime — Convention 2 has been broken four times and hardcoding them here
is how it happens a fifth. (One exception, noted in the CSV: `লাইভস্টক গ্রো`
is a gating probe, because it lives in `NAMED`'s free-text *alias* column
rather than a name field, so `registry_vocabulary()` cannot pull it.)

**`min_count` is 1 for every term, deliberately.** Nobody has read this
manual yet, so any frequency threshold would be invented — and with
`--probe-floor 0.90`, a handful of too-high thresholds fail a clean text
layer. Presence still catches the failure this check exists for: দাবি
extracting as দাকি on every page reads 0, not "fewer than expected". After
the first clean run, raise each `min_count` to roughly half its observed
count and fill in `verified_on_page`. That is what upgrades the check from
"the word exists" to "the word is not half damaged".

**Three signals, verdict is the worst of the three, never averaged.**
Probe coverage catches substitution. Validity catches illegal sequences.
Legal-damage rules catch `ললোন` / `প্রপ্রোডাক্ট` / shattered digit runs — the
Progoti damage that scored 2.0/1000 `CLEAN`. Each is blind where another
sees, and the canary asserts that asymmetry still holds: on the doubling
canary, validity reports 0.00/1000 while legal-damage reports 6 hits.

**Heading spine from clause numbers, not font size.**
Word gave you 16/54/174 free. A PDF gives font sizes, and those break across
a document edited by several people over several versions. `pdf_structure.py`
uses `^১.১.২` numbering as primary and font size only as a
marked-low-confidence fallback, and it prints how many headings rest on the
fallback. Letter enumerations `(ক) (খ) (গ)` are never promoted to headings —
§2.1.1 runs ক to ঞ and the changelog cites a ট that does not exist, so
promoting them would invent structure that isn't there.

The orphan-rate gate is a build failure, not a warning.

**Heading labels keep the digits as printed.** `path` entries use
`display_norm`, so ৫.৪ stays ৫.৪. `ex.py` builds the docx `heading_path`
from raw Word heading text, which keeps Bengali digits, and
`dupscan`/`dedupe`/`resplit` compare heading paths across manuals. A PDF
path carrying `5.4 …` where the docx path carries `৫.৪ …` would never group
with its docx twin, and the 3-way overlap report would look clean because
the comparison could not fire. The folded ASCII form is kept separately as
`clause`, which is what citations use.

**Provenance fields on every chunk.**

```json
{"manual_id": "livestock_grow_2026",
 "source_type": "pdf_text",
 "extraction_confidence": "probed",
 "page": 42,
 "clause": "5.4",
 "needs_figure_check": true}
```

`manual_id` is mandatory, not optional: Progoti §5.4 is fire-safety
insurance, Dabi §5.4 is livestock protection insurance, and this manual has
its own §5.x on the same subject. A clause citation without a manual name is
wrong two thirds of the time. `source_type` lets retrieval prefer the docx
chunk in a duplicate group and lets evaluation be segmented, so you find out
this manual is dragging accuracy down here rather than in the field. When
the `.docx` arrives you know exactly what to replace.

**`digit_audit.py` is the highest-consequence check in this path.**
`১০০০০\n১-১৫০০\n০০` is one ceiling shattered across three lines, and it looks
entirely plausible once joined wrong. Nothing is auto-corrected — auto-joining
is how you get a confident wrong ceiling. It reads `display_text`, not
`text`, because every figure in the worklist gets compared by eye against a
page printed in Bengali numerals.

---

## Downstream consequences of a third manual

- **Overlap.** Re-run `dupscan.py` across all three. Hold the collapse
  threshold at exactly 1.000. The 35-pair finding at 0.95 only widens with a
  third role vocabulary.
- **Roles.** `roles.py` now counts `livestock_grow_2026` alongside Dabi and
  Progoti and prints, per role, how often each declared name occurs in it.
  A role whose every declared name reads 0 is the signal that this manual
  uses a **third title**; it then joins the registry under the same
  `expand_in_query = NO` rule, because the term identifies which manual the
  asker works under. Read the rendered pages before adding a name.
  Until the manual is ingested, `roles.py` warns and skips it rather than
  reporting zero counts — a corpus in which no role name occurs and a corpus
  that isn't there yet look identical otherwise.
- **Products.** Resolved for this manual — see `meta.product_identity` in
  the gold file. `লাইভস্টক গ্রো` is a **project** with four pillars; this
  manual covers only the insurance pillar, so `productmatch`'s alias
  `লাইভস্টক গ্রো → livestock_insurance` is right for its scope. The loan
  pillar is `স্বাধীন ঋণ` (`shadhin_loan`), named once in the overview
  diagram — `productmatch`'s pattern catches it there via the `স্বাধী[নণ]`
  alternation. Two things still to decide: the registry flattens a
  project > product > sub-product hierarchy, and this manual serves **both**
  programmes, so `programmes_in` tags only 2 of 36 chunks and a
  programme-scoped retrieval filter would exclude nearly all of it.
- **New vocabulary, none of it registered.** `সিএসএ` (CSA, p6) and
  `উপজেলা হিসাব ব্যবস্থাপক` (p7) are not in `roles.py`'s `ROLES`. The
  referrer codes `UPG / IDP / CCP / AI / WVB / SS lead` (p6) are in no
  registry and are never expanded — gold question G-132 makes that
  checkable.
- **A counting bug this manual exposed.** `roles.py` counts role names by
  plain substring, so `পিএ` matches inside `পিএলসি` and
  `programme_assistant` reads 10 in a manual that never mentions a Programme
  Assistant — it names two insurance **পিএলসি** throughout. Not fixed by a
  letter-boundary guard: that would drop inflected forms (`সিডিওকে`) and
  silently change the `n_dabi` / `n_progoti` figures `roles.py`'s docstring
  records. The caveat is printed instead.
- **The same bug, in code added here.** `probe_words.registry_vocabulary()`
  first probed `productmatch`'s display *names* with a plain literal
  matcher, so bare `দাবি` matched inside `বিমাদাবি` and the panel reported
  `দাবি` **51** times in a manual that names the Dabi programme twice — that
  is `CONVENTIONS.md` §2 defect #1, reintroduced by the module whose
  docstring forbids it. It now uses `productmatch.PROGRAMMES` and
  `compile_pat`, and the count is 2.

---

## Evaluation

`gold/bootstrap_livestock_grow.yaml` holds 13 questions. Distribution:
7 answerable, 3 partial, 3 unanswerable (~23%). For the full 50-question
field set aim for roughly that unanswerable share — it will feel high and it
is correct, because every corpus gap is a question an officer will actually
ask and the failure mode is confident invention.

```bash
python gold_eval.py --gold gold/bootstrap_livestock_grow.yaml \
    --answers out/answers.jsonl \
    --forbidden-numbers out/forbidden.json
```

`answers.jsonl`: `{"id": "G-131", "answer": "…", "citations": ["dabi_2025 §7.1"]}`

`forbidden.json` maps a clause to the numbers appearing in it, built from
your own chunks. It is what turns "don't invent a ceiling" into something
checkable: G-131 asks what a CDO can approve, no such ceiling exists, and
§7.1 is a table of authority full of taka figures formatted exactly like the
right answer.

### Four scores, reported separately, never averaged

1. **fact recall** — atomic, per `gold_facts` entry
2. **citation accuracy** — manual name *and* clause; missing manual name is wrong, not partial
3. **abstention accuracy** — on `answerable: "no"` only, and invisible in any aggregate
4. **violation rate** — `must_not_contain` hits; non-zero is a report-stopper regardless of the rest

RAGAS faithfulness and context precision are a fine secondary panel for
retrieval tuning. They cannot be the headline: faithfulness measures whether
the answer matches the retrieved context, so if the context says দাকি a
wrong answer scores perfectly.

### Three ways the gold file can lie to you, and what stops each

**`TODO:` facts are not scored.** Most figures in this manual cannot be
written down until someone reads the rendered pages, so they are recorded as
`TODO:`. `gold_eval.py` skips any question holding one and names it. Left
scored, an unfilled question reports 0 recall — indistinguishable from a
retrieval regression, and it quietly drags the headline down.

**English prose facts are counted separately.** `fact_present` matches a
Bengali literal or a figure. A fact written as English prose — "the manual
does not state a ceiling" — contains neither, so under `--judge literal` it
scores as a miss no matter how good the answer is. The report now says how
many misses are of that kind and tells you to rerun with `--judge llm`
before reading the number. Prefer writing the checkable Bengali literal and
putting the English in `notes`.

**Quote `yes` / `no` / `partial`.** YAML 1.1 parses bare `no` as boolean
`False`, which silently drops every unanswerable question out of the
abstention bucket — the one score you cannot recover from an aggregate.
`norm_answerable()` handles it anyway, belt and braces.

### Provenance is recorded per question and scores never merge across it

`chunk_derived` questions are written while looking at a chunk, copying
corpus spelling. They test retrieval mechanics and share assumptions with the
code, so they measure agreement. `field_sourced` questions are an officer's
own wording and spelling, unedited — those are what reveal alias gaps. The
digit-fold classifier scored 7/7 against self-written sentences using বীমা
where the corpus says বিমা. A merged score is what hides that.

---

## The Unicode finding, and how it actually applies here

Bengali য় (U+09DF), ড় (U+09DC) and ঢ় (U+09DD) are on the Unicode
**composition exclusion** list. Composition *into* them is excluded, so NFC
**does unify** the two spellings — by decomposing the precomposed form onto
`base + nukta`. Verified: `NFC("য়")` → `U+09AF U+09BC`, and the decomposed
form is already a fixed point.

So **`canon()` on both sides is sufficient**, and the older claim that
"`canon()` alone could never have fixed the গার্ডিয়ান bug" is wrong.
`bnnorm.canon` applies NFC to text and `bn_shared.canon` to literals; once
both have run, a plain substring test works.

`bn_shared.canon_pattern` still emits an explicit alternation for each of the
three pairs, and that is not redundant — it is the whole answer for the case
where the text side is **not** yours to normalise. Every real failure in this
repo was of that shape: a literal matched against raw extractor output.
`CHAPTER_WORD_RX` and `CHAPTER_RX` matched raw PDF lines and found 0 where a
canon'd comparison finds 29. Patterns matched against canon'd *chunks* were
never broken — measured, the corpus is 100% decomposed on both ingestion
paths (10,017/0 docx, 296/0 ocr).

Hence Convention 4's preference order: canon the text where you can,
`canon_pattern` the literal where you can't.

### Three normalisers, three jobs. Do not collapse them.

| Call | Input | Use for |
|---|---|---|
| `bnnorm.canon` | text | documents and queries |
| `bn_shared.canon_pattern` | a **literal** | compile it to a regex matching `canon()`'d text |
| `productmatch.compile_pat` | a hand-written **regex** | product and role patterns |

`bn_shared.canon_pattern` deliberately does **not** use
`bnnorm.canon_pattern`, for two independent reasons:

1. `bnnorm.canon_pattern` returns a normalised pattern *string*, not a
   compiled regex. Every call site here does `canon_pattern(x).search(…)`.
2. Its contract is different. It normalises an author-written regex and so
   omits `strip_groups`, because deleting a comma between two digits rewrites
   `\S{0,4}` into `\S{04}` — "up to four" becomes "exactly four", which cost
   গার্ডিয়ান লাইফ all 12 of its matches. A **literal** has no quantifier to
   mangle and *must* be group-stripped, because the text side is. Otherwise
   the literal `৪০,০০০` normalises to `40,000` while the corpus reads
   `40000`, and the probe silently finds nothing.

---

## Canary status

| Tool | Canary | Real failure? |
|---|---|---|
| `probe_words.py` | Dabi 2026 CMap substitution, asserted at 0.40/1.00 | yes, 8 Sep |
| `pdf_extract_check.py` | Dabi 2026 + Progoti doubling + clean control | yes, 8–9 Sep |
| `ex_pdf.py` | pre-joined `heading_path`, dead `needs_figure_check`, `ex.py` number format | yes — both were live in the draft |
| `digit_audit.py` | shattered ceiling, inverted range, clean control | yes |
| `gold_eval.py` | গার্ডিয়ান nukta-blind matcher, wrong figure, grouped figure, citation-without-manual | yes |
| `gold_eval.py` abstention | **not yet backfilled** | no — generation hasn't run |
| `ex_text.py` | none of its own; schema is guarded by `ex_pdf`'s canary, which it imports | n/a |

Two caveats stated rather than papered over:

**The `probe_words` / `pdf_extract_check` canary strings were transcribed
from a mojibaked copy of the record, not read back from the original
extraction.** The substitutions are the recorded ones and the pair
reproduces the recorded 0.40 / 1.00 split exactly, which is the evidence
they behave like the original. Re-record them from the real Dabi 2026
extraction when it is to hand. This matters because `CONVENTIONS.md` §3
failure #5 is precisely a Bengali word list mojibaked by a PowerShell
round-trip, after which the check reported "clean" while testing nothing.

**The abstention canary is still empty.** Backfill it from the first real
generation failure. A synthetic canary tests what you imagined the bug was.
