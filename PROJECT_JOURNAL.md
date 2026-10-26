# Project journal — Bengali FAQ / operational-manual RAG

A learning record of how this repository was built: what we did at each stage,
what went wrong, how each problem was found and fixed, and **why** each tool
and design choice was picked over the alternative.

Companion documents, which this one links to rather than repeats:

| File | What it holds |
|---|---|
| [CONVENTIONS.md](CONVENTIONS.md) | The four rules, their enforcement, and the fifteen breakages that produced them |
| [PDF_INGEST.md](PDF_INGEST.md) | The PDF ingestion path, stage by stage |
| [data/corpus_gaps.md](data/corpus_gaps.md) | Defects found *in the manuals themselves* |
| [out/divergence.md](out/divergence.md) | Where the two manuals disagree |

---

## 1. What we are building, and the one failure mode that shapes everything

A retrieval-augmented question-answering system over BRAC's microfinance
operational manuals, in Bengali, for field officers.

The governing constraint is this: **a wrong answer here is an action taken in
the field.** If an officer asks what the premium is on a dairy-cow insurance
policy and the system confidently returns ২.৪০% when the applicable insurer
charges ২.৩৫%, nothing crashes, no log shows an error, and the officer quotes
the wrong figure to a client. Every design decision below is downstream of
that: we prefer a loud failure to a plausible one, and we prefer measurement
to reputation.

That is also why the project is heavy on *checks* relative to *features*. The
ratio looks strange until you notice that twelve separate checks in this repo
have, at some point, reported "clean" on data they could not actually see
(seventeen, as of 2026-09-25 -- see 5.3; seven of them in the evaluation
layer, and three of them measurements rather than checks).

### Inputs

| File | Format | Size | Notes |
|---|---|---|---|
| `dabi_manual_2025.docx` | Word | 2.5 MB | Contained legacy Bijoy-encoded runs |
| `progoti_manual_2025.docx` | Word | 2.6 MB | Same |
| `dabi_manual_2026.pdf` | PDF | 2.8 MB | Text layer corrupt; used only as a corruption canary |
| `Operational Manual_Livestock (2026).pdf` | PDF | 0.8 MB | Text layer unrecoverable; transcribed by hand |

### Current corpus

**867 chunks** — 400 Dabi 2025, 431 Progoti 2025, 36 Livestock Grow 2026.

---

## 2. The pipeline, end to end

```
 .docx ──▶ bijoy_scan ──▶ bijoy_convert ──▶ ex.py ──┐
                                                    │
 .pdf  ──▶ pdf_triage ──▶ pdf_extract_check ──▶     ├──▶ retag ──▶ resplit ──▶ dupscan ──▶ dedupe
           pdf_shootout ──▶ pdf_structure ──▶       │                                        │
           ex_pdf.py / ex_text.py ──────────────────┘                                        │
                                                                                             ▼
                                        embed.py (benchmark → build) ──▶ retrieve.py ──▶ retrieval_eval.py
                                                                                             │
                                                              gold/*.yaml ──▶ gold_eval.py ───┘
```

Two ingestion paths converge on one chunk schema. That convergence is
deliberate and it is guarded — see §5.4.

---

## 3. Phase-by-phase build log

### Phase 1 — Diagnose the documents before writing any pipeline

**What we did.** Wrote `bucket_test.py` first, before any extraction code. It
answers exactly one question: which of three problems does this document have?

| Bucket | Meaning | Route |
|---|---|---|
| `UNICODE_TEXT` | Real Bengali codepoints in the text layer | Chunk directly |
| `LEGACY_ANSI` | Bijoy / SutonnyMJ ASCII-mapped font encoding | Glyph conversion first |
| `SCANNED` | No usable text layer | OCR |

**Why this first.** The three buckets need completely different pipelines, and
the cost of guessing wrong is weeks. Text that reads `Avgvi bvg` on screen is
*not* an OCR problem — it is perfectly good bytes under the wrong encoding, and
throwing OCR at it is both expensive and worse than the free answer.

**Challenge that emerged immediately.** `bucket_test` said `UNICODE_TEXT` for a
PDF whose every `দাবি` extracted as `দাকি`. The diagnosis was *true and
useless*: there were real Bengali codepoints, they were simply the wrong ones.

**How we overcame it.** Split the question in two and built a second tool,
`extract_check.py`, to answer the one `bucket_test` could not: *is the text
layer correct?* This distinction — "is there Bengali" versus "is it the right
Bengali" — became the backbone of the whole PDF ladder.

---

### Phase 2 — Bijoy → Unicode conversion

**What we did.** `bijoy_scan.py` finds legacy-encoded runs in a `.docx`;
`bijoy_convert.py` converts them and writes a **new** file. `data/raw/` is never
touched.

**Why two detectors in the scanner.** Each alone has a blind spot:

- *Font-name detection* catches runs explicitly set in SutonnyMJ, but misses
  runs that **inherit** a legacy font from their paragraph style.
- *Glyph scoring* catches the inherited case by examining the byte soup itself
  — high Latin ratio, zero Bengali, Bijoy digraphs present.

Font resolution therefore walks `run → run style → paragraph style → base
styles`, and reads all four `w:rFonts` slots, because Bengali is frequently
tagged only in the complex-script slot.

**The challenge: run-boundary shredding.** Word splits a single Bijoy word
across several runs. Converting run by run cuts words apart — a run holding
just `vwe` converts to `াবি`, a leading vowel sign that cannot begin a word.
Measured, the malformed-cluster rate came out at **45 per 1000, fifteen times
tolerance**.

**How we overcame it.** Convert *spans*, not runs: join consecutive legacy runs
into one span, convert the span, write the result back into the span's first
run. A Unicode run always terminates a span, so Unicode text is never fed to
the Bijoy map — which preserves the original "never convert whole paragraphs"
safety rule while fixing the shredding.

**Why we did not just use a library.** `bijoy2unicode` is installed and was
evaluated. The mapping is the easy half; the hard half is *which spans to feed
it*, and no library knows about Word's run structure or style inheritance.

---

### Phase 3 — Extraction and chunking from `.docx`

**What we did.** `ex.py` walks the document body in order, handling paragraphs
and tables, and emits `chunks_<tag>.jsonl`.

**Why `.docx` is strongly preferred over the PDF of the same manual.** Word
gives two things free that a PDF makes you reconstruct:

1. **Real Unicode**, not glyph indices filtered through a ToUnicode CMap.
2. **Real heading styles** — Dabi gave 16 chapters / 54 / 174 headings
   directly. In a PDF this has to be rebuilt from clause numbering, and font
   size is unreliable across a document edited by several people.

This is why [PDF_INGEST.md](PDF_INGEST.md) opens with *"ask the programme team
for the `.docx`"*. One email versus several days of OCR benchmarking.

**Table handling, and why it needed real work.** Tables in these manuals carry
the operationally critical content — approval authority bands, premium rates,
ceilings. `rows_of()` resolves `gridSpan` (horizontal merges) and `vMerge`
(vertical merges) so a merged cell does not silently shift every column to its
right. `serial()` then flattens each row to `header: value — header: value ।`
so the text is embeddable, while `table_rows` keeps the original structure for
`show_chunk.py` to print when a flattened quote is ambiguous.

**Why both `text` and `display_text`.** `text` is normalised for matching;
`display_text` keeps the figures exactly as printed, in Bengali numerals. Every
downstream check that a human reads by eye — the figure worklist above all —
reads `display_text`, because the page they are comparing against is printed in
Bengali numerals.

---

### Phase 4 — Normalisation, and the digit-fold trap

`bnnorm.py` defines the canonical normaliser. Three operations, all of which
must be applied symmetrically:

| Operation | Effect |
|---|---|
| `nfc` | Unicode NFC |
| `fold_digits` | ০১২৩৪৫৬৭৮৯ → 0123456789 |
| `strip_groups` | thousands separators inside a number: 40,000 → 40000 |

**The single rule the module exists to enforce: index side and query side must
call the same function.** A query for ৫০,০০০ against a digit-folded index
returns no error and no warning — just a plausible answer from the wrong chunk.

**The challenge that made this subtle: `canon` is not safe everywhere.**

- `strip_groups` deletes a comma between two digits. Applied to a *regex*, that
  rewrites `\S{0,4}` into `\S{04}` — "up to four" silently becomes "exactly
  four". That one transformation cost গার্ডিয়ান লাইফ **all twelve** of its
  matches.
- `fold_digits` erases ০-৯ entirely. Measured on 14 Sep 2026, switching a
  matcher from raw to canon'd text took bare-Bengali-number matches from
  **27/80 to 0/80**, because the Bengali digit character class then matched
  nothing at all.

**How we overcame it.** Three normalisers, three jobs, never collapsed:

| Call | Input | Use for |
|---|---|---|
| `bnnorm.canon` | text | documents and queries in the matching path |
| `bn_shared.canon_pattern` | a **literal** | compile to a regex matching canon'd text |
| `productmatch.compile_pat` | a hand-written **regex** | product and role patterns |
| `bn_shared.display_norm` | text | the **index**: NFC + strip zero-width + collapse spaces, digits untouched |

`canon_pattern` group-strips (a literal has no quantifier to mangle, and the
text side is stripped, so it must be too). `bnnorm.canon_pattern` deliberately
does not (an author-written regex *does* have quantifiers).

---

### Phase 5 — The nukta problem

This is the most instructive bug class in the repository, and it recurred three
times.

**The mechanism.** Bengali য় (U+09DF), ড় (U+09DC) and ঢ় (U+09DD) are on the
Unicode **composition exclusion** list. Composition *into* them is excluded, so
NFC resolves both spellings by **decomposing** the precomposed form:

```
NFC(U+09DF)        -> U+09AF U+09BC
NFC(U+09AF U+09BC) -> U+09AF U+09BC     # already a fixed point
```

Both forms converge. So `canon()` on **both** sides is sufficient, and a plain
substring test works afterwards.

**The failure is always the asymmetric case**: a literal saved in one form,
matched against text nobody normalised. The two never meet, and *nothing
throws* — it simply matches fewer occurrences, or none.

| # | Where | How it presented |
|---|---|---|
| 1 | `গার্ডিয়ান লাইফ` | matched 2 of 12 chunks. Found because 2-of-12 looked implausible by eye, not because anything flagged it |
| 2 | `gold_eval` fact matcher | caught only because that canary was built from explicit codepoints |
| 3 | `CHAPTER_WORD_RX` / `CHAPTER_RX` | carried decomposed য় and matched **raw** PDF lines, which emit precomposed U+09DF. Found 0 where a canon'd comparison finds 29 |

**Instance 3 is the one to learn from.** It killed two signals at once — real
chapter headings were never detected in *any* manual, and the over-detection
gate's ratio check was permanently inert — and the gate still printed a verdict
on three signals as though it had four. A dead check does not look dead; it
looks clean.

**How we overcame it.** Convention 4, plus `bengali_literal_lint.py`, an AST
scan that finds Bengali literals reaching a matching operation directly. Two
tiers, and only one blocks the build:

- **ERROR** — the literal contains a nukta character. Broken now, silently.
- **ADVISORY** — Bengali, no nukta. Works today; breaks when someone edits it.

Blocking on both would train people to add exemptions, which is how a
convention dies.

**The canary's own trap.** The lint's canary builds the two nukta forms with
`chr(0x09DF)` and `chr(0x09AF) + chr(0x09BC)` — never typed. Typing them *is*
the ambiguity being linted: whichever form the editor saved would be used on
both sides, the asymmetry would vanish, and the canary would pass while proving
nothing. That is exactly how `heading_gate`'s first canary passed while
`CHAPTER_WORD_RX` found 0 on every manual.

---

### Phase 6 — Product and role matching

**The challenge.** The product names are ordinary Bengali words. দাবি is
"claim". গতি is "speed". They cannot be matched by substring, and the correct
pattern is not obvious.

Four separate defects, each producing plausible output:

| # | Where | Bug |
|---|---|---|
| 1 | `ex.py` | bare `দাবি` matched inside **বিমাদাবি** ("insurance claim"), tagging insurance-claim text as the Dabi loan product |
| 2 | `products.py` | `re.escape("দাবি")` reintroduced the identical bug while picking defining sections, making §5.2.6 (claim procedure) look like the *definition* of the দাবি programme |
| 3 | `ex.py` | bare `গতি` matched inside **প্রগতি**, **অগ্রগতি**, **গতিশীল** — so a real product was first over-tagged, then wrongly **deleted altogether** on the evidence of those false positives |
| 4 | `productmatch` | **স্বাধীন লোন** is spelled **স্বাধীণ ঋণ** in the changelog; a single-spelling pattern found only part of it |

**The collision table is §6.3's, and it was already complete.** Reproduced in
`role_exclusions.py`'s docstring, which is where it is maintained:

| # | literal | collides inside | meaning of the longer word |
|---|---|---|---|
| 1 | `দাবি` | `বিমাদাবি` | insurance claim |
| 2 | `গতি` | `অগ্রগতি` / `গতিশীল` | progress, dynamic |
| 3 | `স্বাধীন` | `স্বাধীনতা` | independence |
| 4 | `সিডিপি` | `এনসিডিপি` / `এসসিডিপি` | different programmes |
| 5 | `পিএ` | `পিএলসি` | public limited company |

All five are handled structurally already: `productmatch.PROGRAMME_PATTERNS`
carries `(?<![নস])সিডিপি` and the দাবি and গতি guards, and
`role_exclusions.EXCLUSIONS` carries `"পিএ": ["পিএলসি"]`. Nothing was missing.

**So the 2026-09-17 mis-count is not a new collision -- it is row 4, bypassed.**
A one-off `str.count` in a throwaway script reported 440 occurrences of the
four programme acronyms where the guarded figure is 282, and `সিডিপি` alone
read 180 against a true 22. That is the stronger finding: the guard existed,
was correct, and simply was not on the path. A collision you have not yet
found is a gap in the table; a collision you have already fixed and then
walk around is a gap in how the fix is reached.

**Fixed by convenience, not by rule.** `bn_shared.count_literal(literal,
texts) -> (occurrences, n_chunks)` resolves the owning registry itself --
programme, product or role -- and uses that registry's pattern, so the
exclusions already encoded apply. It is one call, shorter than the loop it
replaces:

```python
occ, n = count_literal("সিডিপি", (c["display_text"] for c in corpus))   # 22, 22
```

A literal no registry owns still counts, nukta-tolerantly, but warns once on
stderr naming it -- silence there would make the helper indistinguishable from
`str.count` for exactly the literals nobody has guarded yet.
`bn_shared.canary_selftest()` asserts the guarded count still differs from the
naive one on row 4 (1 vs 3 on a fixture sentence), so the guard cannot be
removed quietly.

**Convention 2 was deliberately NOT extended to cover counting.** The rule
would have to reach one-off measurement scripts, and a rule that costs more
than the wrong thing collects exemptions instead of compliance. Making the
correct call shorter than the incorrect one needs no enforcement.

**Two errors in the note that prompted this, recorded because both are the
kind that survives review.** The list of prior instances given to me
conflated `স্বাধীন লোন` / `স্বাধীণ ঋণ` -- a **spelling variant**, which is
this table's own #4 in the Convention 2 "Why" list above and a different
failure that merely shares the fix -- with the substring collisions; row 3 of
§6.3 is `স্বাধীন` inside `স্বাধীনতা`, which is the collision. And it cited
`পিএ`/`পিএলসি` as recorded only in the livestock YAML: it is in
`role_exclusions.py`'s table as row 5 and in `roles.py`'s docstring with the
measured effect, though it is indeed absent from this journal. My own error
was worse and came first: I recorded `সিডিপি` as a *new fifth* collision and
proposed a new convention for it, without checking `productmatch.py`, where
the guard had been sitting since Phase 6. The row above is the correction.

Corpus evidence for #1 and #3: in 393 Progoti chunks, দাবি occurs 468 times, of
which **215** are বিমা-/বীমা-/মৃত্যু- compounds; গতি occurs 322 times, **291**
of them প্রগতি/প্রগতির/প্রগতিসহ.

**Number 2 is the one that should worry you.** The bug was already known,
already fixed once, and documented in `retag.py` — and a second module
reproduced it anyway, because it wrote its own matcher.

**How we overcame it.** `productmatch.py` became the single place a product or
programme name becomes a pattern, and Convention 2 forbids any other module
from matching one directly. `conventions_check.py` enforces it with an AST
scan that distinguishes *matching* from *mentioning* — a product name in a log
message, comment or docstring is fine.

**A consequence worth knowing.** Once `ex.py` used the shared matcher,
`retag.py` became a no-op — it reports **0 of 393 chunks changed**. That is the
intended end state, not redundancy to delete. `retag` now *verifies* that
extraction and tagging still agree; a non-zero change count means two matchers
have diverged.

**The fifth instance, found in the livestock manual.** `পিএ` matches inside
`পিএলসি` ("public limited company"), so `programme_assistant` read **10** in a
manual that never mentions a Programme Assistant — it names two insurance
পিএলসি throughout. Handled the same way as the other four: a per-literal
exclusion in `role_exclusions.py`, owned by the registry module. **Not** a
general letter-boundary guard, which would drop inflected forms like `সিডিওকে`
and silently change recorded counts.

**Roles are a separate vocabulary, and the equivalence is metadata only.** The
manuals name the same field role differently, with perfect complementary
distribution:

```
সিডিও   127 in Dabi,   0 in Progoti
পিও       0 in Dabi, 142 in Progoti
```

`roles.py` records the সিডিও↔পিও equivalence as **metadata, never query
expansion** — because the term identifies *which manual the asker works under*,
and erasing that difference erases the routing signal.

---

### Phase 7 — Deduplication, and the threshold that is not a knob

**The challenge.** The two manuals share large amounts of text. Two distinct
problems follow:

1. **Retrieval** — if the same rule sits in both manuals, a top-5 can be three
   copies of one answer, crowding out genuinely different content.
2. **Attribution** — where the manuals *agree*, a duplicate is harmless. Where
   they *diverge* on a figure, retrieval that silently picks either one is
   wrong, and the answer **must** name its manual.

**The finding.** `dupscan.py` measured it: at identical normalised body there
is zero semantic divergence. Every pair **below** 1.000 differs by something an
answer must not erase:

```
সিডিও / পিও                           the same field role, named differently
(দাবি) / (দাবি/প্রগতি)                 the scope a rule applies to
এএম(দাবি) / এএম(দাবি)/আরএম(প্রগতি)     WHO MAY APPROVE
```

**How we overcame it.** The collapse threshold is **exactly 1.000**, and that
is a design decision, not a tunable. Collapsing at the intuitive 0.95 would
merge **35 pairs that disagree about approval authority**. Duplicates are
grouped offline and collapsed at query time, so nothing is deleted.

Measured across the full set: 351 pairs at Jaccard ≥ 0.90 — 260 identical in
text and clause, 16 renumbered but identical, 91 lexically divergent, and
**0 differing in any number**.


**Confirmed independently, on a corpus that was not part of the original
measurement (2026-09-17).** `livestock_grow_2026` was added to `CORPORA` and
re-run at the same 1.000. Dabi 5.4 and the livestock manual cover the same
insurance, so cross-manual duplicates were expected -- and **zero** were found:

```
livestock chunks gaining dup_group : 0 of 36
single-source chunks               : 285 -> 321   (+36, the whole manual)
best similarity to any dabi/progoti chunk : 0.992   (mean 0.262)
```

The six near-misses are all against Dabi 5.4, which is the right place:

| livestock | dabi | ratio |
|---|---|---|
| `..._0018` | `dabi_2025_0262` (5.4.10 enrolment) | 0.992 |
| `..._0017` | `dabi_2025_0261` (5.4.10) | 0.988 |
| `..._0014` | `dabi_2025_0259` (5.4.8 who may be a client) | 0.975 |

What keeps them off 1.000 is a mix of the trivial and the load-bearing.
`..._0018` differs from `0262` by three inserted spaces -- cosmetic.
`..._0017` differs by straight `"` against curly quotes, plus `বা ` -> `/`.
But `..._0014` differs from `0259` by **ভর্তি** against **সদস্য হিসেবে
নিবন্ধন** -- "admission" against "registration as a member", a real difference
in how a client is enrolled.

At 0.95 all three would have merged, and that third merge would have erased the
enrolment distinction. This is the threshold argument reproduced on new data by
a different route: the original evidence was 35 pairs disagreeing about
approval authority in the two docx manuals; this is a hand transcription of a
third document landing in the same place. A transcription being byte-identical
to a docx-sourced chunk would have been the surprising result -- its absence is
what the threshold predicts.

---

### Phase 8 — The livestock PDF, and why we transcribed it by hand

This is the most involved single investigation in the project.

**The ladder we ran.** Five stages, each with a defined gate:

| Step | Tool | Gate |
|---|---|---|
| 1 | `pdf_triage.py` | legacy font present → Bijoy conversion first |
| 2 | `pdf_extract_check.py` | `CLEAN` → step 4, else step 3 |
| 3 | `pdf_shootout.py` | all routes equal and bad → OCR |
| 4 | `pdf_structure.py` | orphan rate > 2% → do not proceed |
| 5 | `ex_pdf.py` / `ex_text.py` | oversized chunks must be 0 |

**What stage 3 found.** Seven extraction routes; the six that worked returned
*identical* corruption:

| Route | bn chars | probe | mal/1k | Verdict |
|---|---|---|---|---|
| pymupdf: text / blocks / rawdict | 14,956 | 25.0% | 56.3 | CORRUPT |
| pdfplumber | 15,236 | 25.0% | 56.3 | CORRUPT |
| pypdf | 15,236 | 25.0% | 56.2 | CORRUPT |
| pdfminer.six | 15,236 | 25.0% | 56.2 | CORRUPT |

Spread **0.0%**. **0 of 60** registry literals present — `ঋণ` included. Identical
results from six independent libraries is the signature of a wrong ToUnicode
CMap, not a library problem.

```
extracted:  "লাইভস্টক গ্রা" প্রকল্পের অপাল্পরশিাল মযািুয়াল
should be:  "লাইভস্টক গ্রো" প্রকল্পের অপারেশনাল ম্যানুয়াল
```

Glyphs emerge in **visual rather than logical order** (pre-base matras precede
their consonant) *and* land on wrong codepoints. Kalpurush is embedded four
times — twice `Type0/Identity-H`, twice `TrueType/WinAnsiEncoding`, in two
different subsets. Two interleaved wrong mappings is why no single
find-and-replace table can fix it.

**Why we did not repair the font.** The fonts *are* embedded, so recovering the
true glyph→Unicode mapping looked promising. But the subsets name **497 of 586
glyphs `glyph000NN`** and `GSUB` has been stripped. Only the 61 basic letters
and matras carry recoverable identity — which is precisely why a simple word
like `লাইভস্টক` survives and every conjunct mangles. The information needed to
rebuild the conjuncts **is not in the file**.

**Why we did not run `ocr_bench.py`.** It needs an OCR engine installed *and* a
hand-typed ground-truth gold set — `init` only scaffolds folders. Someone has
to type correct Bengali for the sample pages before any engine can be scored.
For a 100-page manual that is the right investment. For **8 legible pages,
transcribing them directly is cheaper than building the apparatus to grade a
machine doing the same job.**

**What we did instead.** Transcribed the 8 pages from 200dpi renders into
`data/raw/livestock_grow_2026_transcribed.md`, chunked with `ex_text.py`, and
marked every chunk `source_type: "ocr"`, `extraction_confidence: "unverified"`.

**The evidence that the transcription is sound**, independent of the probe set:
**0.0 malformed clusters per 1000 and 0 legal-damage hits**, against **56.3/1000**
for the PDF text layer.

**The circularity we stated rather than hid.** `probes_livestock_grow.csv` is
calibrated *from* the transcription, so its 100% coverage proves nothing about
accuracy. It is a regression guard for a future re-transcription or a repaired
text layer. That is written into the CSV's own header.

**The honest limitation, also stated.** 127 figures were extracted and one
flagged. But the audit only flags *implausible* figures — a premium silently
transcribed as ২.৪৫% instead of ২.৩৫% would pass every check in this
repository. Hence the standing instruction: someone other than the transcriber
must read `figure_worklist.html` against the renders before this is embedded.

**Also found at stage 4**: it is not a clause-numbered manual. 8 pages of
narrative; of 220 non-empty lines, the only numbered ones are an insurance
exclusions list. `pdf_structure.py` fails it at a **21.9% orphan rate** against
a 2% gate. So the `must_cite` fields in the gold set, written for §5.4-style
citations, do not apply to this document as it stands.

---

### Phase 9 — Positive controls

**The realisation.** Every check we had built was a **negative control**: it
looks for the *absence* of bad shapes. And all six of them had, at some point,
passed on data they could not see.

`দাবি` extracting as `দাকি` on every page is orthographically legal. It passes
`bucket_test`. It passes `extract_check`'s validity metric. Nothing in the
ladder objects.

**How we overcame it.** `probe_words.py` — the first **positive** control.
It asserts that words which *must* be present *are* present. `দাকি` reads 0,
not "fewer than expected", and the check fails in four seconds.

Its canary is the recorded Dabi 2026 extraction: **0.40 coverage → CORRUPT**,
while the same sentences spelled correctly score **1.00 → CLEAN**. Both numbers
are asserted, not just the verdicts — a check that called everything CORRUPT
would pass a verdict-only canary while being useless.

**The design decision inside it: two panels, only one of which gates.**
A blanket "every known product literal must appear at least once" rule scores a
*clean* Livestock Grow manual as CORRUPT, because it does not mention
প্রত্যাশা লোন or মাইগ্রেশন লোন and has no reason to. Coverage would collapse
for a vocabulary reason and read exactly like a CMap fault. **A check that cries
corruption on good input gets switched off, and then it protects nothing.** So
the curated domain vocabulary gates; the registry vocabulary panel is printed
and gates nothing.

**And it reproduced defect #1 on its way in.** `registry_vocabulary()` first
probed `productmatch`'s display *names* with a plain literal matcher — so bare
`দাবি` matched inside `বিমাদাবি` and the panel reported দাবি **51** times in a
manual that names the Dabi programme twice. That is Convention 2 defect #1,
reintroduced *by the module whose own docstring forbids it*. It now uses
`productmatch.PROGRAMMES` and `compile_pat`, and the count is 2.

---

### Phase 10 — Over-detection, the direction orphan rate cannot see

**The challenge.** The orphan-rate gate detects **under**-detection: body lines
with no heading above them. It is structurally blind to **over**-detection,
because inventing a heading does not create an orphan — it creates false
structure, and every line underneath gets a path that looks perfectly
well-formed.

`CLAUSE_RX` reading bare `১.` enumerators as clause numbers produced **237
depth-1 headings in Progoti** while the orphan rate sat unchanged at **0.04%**.
16 chapters, 237 depth-1 headings, and no gate said a word.

**How we overcame it.** `heading_gate.py`, with four independent signals, of
which the strongest is **RESETS**: a real chapter spine counts up — ১, ২, ৩ —
whereas enumerators reset. And letter enumerations `(ক) (খ) (গ)` are never
promoted to headings: §2.1.1 runs ক through ঞ and the changelog cites a ট that
does not exist, so promoting them would invent structure that is not there.

**And the gate itself was broken when built** — its `CHAPTER_WORD_RX` carried
decomposed য় and matched raw PDF lines, finding 0 on every manual. See Phase 5.

---

### Phase 11 — Embedding and index design

**What we did.** `embed.py` deliberately **does not pick a model**. It scores
three against the gold set and prints a table; choosing is a decision for
whoever reads it.

| Model | Type |
|---|---|
| `BAAI/bge-m3` | dense + native sparse |
| `intfloat/multilingual-e5-large` | dense only, needs `query:`/`passage:` prefixes |
| `sentence-transformers/LaBSE` | dense only |

E5's prefixes are applied **per model**, not globally, because prefixing BGE-M3
or LaBSE would be wrong.

**Design decision 1 — normalise with `display_norm`, not `canon`.** An index
built on canon'd text cannot distinguish ২.৩৫ from 2.35, and cannot represent
০-৯ at all — which is how *every figure in these manuals is written*.
`display_norm` does NFC, strips zero-width, collapses spaces, and leaves digits
alone. Because NFC already unifies the nukta forms, once corpus and query both
go through `display_norm`, no per-pattern nukta handling is needed at all.

**Design decision 2 — the digit twin.** Because `display_norm` leaves digits
alone, an officer typing `2.35` would miss a chunk that says `২.৩৫`. So each
chunk is indexed as its display form **plus** the digit-folded form appended.
Both spellings live in one vector and one sparse posting list, and neither
query form is privileged.

**Design decision 3 — hybrid, not dense-only, and why.** Dense retrieval treats
২.৩৫ and ২.৪০ as near-identical: they differ in one digit and share every other
feature. That is exactly the per-insurer premium trap the gold set is built
around — G-102 (২.৩৫% Green Delta vs ২.৪০% Sena) and G-103 (১.৮০% flat vs
১.৯০%/১.৬০% tiered on an ৮০,০০০ threshold). A dense index will happily return
the wrong insurer's rate with high confidence.

So queries carrying a clause number or a figure weight the sparse side up,
where an exact token match beats semantic similarity:

```
W_DENSE_TOPIC,  W_SPARSE_TOPIC  = 0.8, 0.2
W_DENSE_FIGURE, W_SPARSE_FIGURE = 0.4, 0.6
```

These are **stated, not tuned** — two values, and the benchmark prints both so
the effect is visible.

**Design decision 4 — the heading path is embedded with the body**, because a
clause number lives in the heading, not the body. `৫.৪ গবাদিপ্রাণি সুরক্ষা বিমা`
is the only place §5.4 appears at all.

**Design decision 5 — FAISS on disk, local only.** No embedding API is called.
The corpus is small (867 chunks), the data is internal, and a local index is
reproducible from the repository alone.

**A practical constraint we hit.** The machine has 7.7 GB RAM with ~1.3 GB
free, and BGE-M3 is XLM-R-large (560M params, ~2.2 GB in fp32). The first
benchmark attempt died inside the Rust tokenizer with
`memory allocation of 67088977 bytes failed`. Fixed with `BATCH = 4`,
`MAX_SEQ = 512` and a subprocess-per-model benchmark — a **memory budget, not
performance tuning**, and labelled as such in the source.

**Current state:** LaBSE is benchmarked and built (`out/index/model.json`);
the BGE-M3 run was still in progress when the log was last written.

---

**A section-head chunk loses to its own children.** Measured 2026-09-17 on
G-215, which asks *which insurance does clause 5.4 mean?* -- the cross-manual
ambiguity this corpus exists to expose. The correct chunks are the two 5.4
section heads, `dabi_2025_0247` (gabadipraani insurance) and `progoti_0275`
(fire safety). Dense ranks them **457** and **101**. Dense rank 1 is
`progoti_0276` -- clause 5.4.1, a **child of the correct section**.

A section head is short and mostly heading: `progoti_0275` is 604 characters,
its children run to several thousand. Every scorer here is length-normalised
(BM25 by `b=0.75`, cosine inherently), so a head competes against its own
sub-clauses with a fraction of their content and loses. G-222 is the milder
form: its two refs rank 7 and 9 behind a structurally similar chunk elsewhere.

**This is chunk granularity, not weighting.** A sweep of
`W_DENSE_TOPIC`/`W_SPARSE_TOPIC` from 1.0/0.0 to 0.0/1.0 in 0.1 steps moved
recall@1/@3/@5 on these questions **not at all** -- 0/4 at all eleven weights.
Neither component ranks the chunk, so no mixture of them can. Candidate fixes
are structural and none is applied yet: retrieve at section level and narrow,
or let a head inherit its children's text for scoring, or accept a child as a
hit when the question is about the section. The last is a gold-semantics
decision, not a retrieval one.

**Chunk boundaries fall mid-list, so a step's header and its content are in
different chunks.** Second instance of the same granularity class, measured
2026-09-17 on §5.1.8 (বিমাদাবি পদ্ধতি), a six-step procedure spread over
fourteen chunks:

| step header | chunk | header offset in chunk |
|---|---|---|
| ১ম-ধাপ | `dabi_2025_0198` | 0 |
| ২য়-ধাপ | `dabi_2025_0199` | **196** -- after two step-1 bullets |
| ৩য়-ধাপ | `dabi_2025_0201` | 0 |
| ৪র্থ-ধাপ | `dabi_2025_0202` | **64** -- after a step-3 bullet |
| ৫ম-ধাপ | `dabi_2025_0204` | 0 |
| ৬ষ্ঠ-ধাপ | `dabi_2025_0205` | **279** |

`dabi_2025_0200`, `0203`, `0206` and `0207` carry no step marker at all --
they are markerless continuations.

Two consequences, and both bite any procedural answer:

- **Retrieving a header does not get you its steps.** The bullets under
  ৩য়-ধাপ are split across `0201` and `0202`, and the tail lands in the chunk
  that introduces ৪র্থ-ধাপ.
- **Retrieving a continuation does not tell you which step it belongs to.**
  `0200`, `0203`, `0206` and `0207` contain instructions with no marker, so a
  chunk retrieved on its own cannot be attributed to a step. An answer built
  from one of them can state a real instruction under the wrong step number.

This is the section-head finding one level down. There the head lost to its
children; here the header is *inside* a chunk whose other content belongs to
the previous step, so no scoring change can separate them -- the unit of
retrieval does not match the unit of meaning. It also made a gold question
wrong rather than merely unlucky: G-223 originally tested three step headers
because three was what the first three chunks happened to expose, and a
complete answer was indistinguishable from one that stopped at step 3. It now
carries all six headers, each with its own ref.

**The fused score does not separate a retrieval hit from a miss, so it is not
a confidence signal anywhere.** Measured 2026-09-17 over the 23 answerable,
recall-scoreable questions in both gold files, top fused score at k=5,
split by whether the gold chunk is anywhere in the top 5:

| | n | min | p25 | median | p75 | max | mean |
|---|---|---|---|---|---|---|---|
| gold **retrieved** | 14 | 0.4044 | 0.5124 | 0.5567 | 0.6111 | 0.7873 | 0.5697 |
| gold **NOT** retrieved | 9 | 0.4181 | 0.5338 | **0.5698** | 0.7199 | 0.7686 | **0.6055** |

The misses score **higher** than the hits, on both median and mean. The lowest
score with a hit is 0.4044; the highest score with a miss is 0.7686. The two
distributions do not merely overlap, they are inverted.

At every candidate threshold the answerable questions discarded exceed the
bad retrievals caught:

```
floor 0.5371 ->  5 good lost, 3 bad caught
floor 0.5698 ->  9 good lost, 5 bad caught
floor 0.7199 -> 12 good lost, 6 bad caught
```

**Consequences, all three of which matter:**

- **The score floor cannot do its job.** `generate.SCORE_FLOOR` is set to
  0.4044, the lowest score at which a gold chunk was actually retrieved, so it
  fires on nothing in this sample. That is deliberate: it guards only the
  degenerate case -- an empty retrieval, or scores collapsing toward zero --
  because that is the only case the data supports guarding.
- **It cannot be answer confidence.** A high fused score is, if anything,
  weak evidence AGAINST the gold chunk being present. Gating an answer on it,
  or ranking answers by it, would be worse than not gating at all.
- **It cannot be a UI indicator.** Showing a user "confidence 0.77" on a
  question whose gold chunk was never retrieved is the project's core failure
  mode with a number attached to make it persuasive.

Why it is inverted is a separate question and Phase 12's other two findings
are the likely cause: section-head chunks lose to their own children, and a
step header sits in a different chunk from its content. A query that lands
confidently in the right *neighbourhood* and misses the right *chunk* scores
high and retrieves nothing useful -- which is exactly the shape of a
high-scoring miss.

n=14/9 is small. Re-derive both the distribution and the floor when the
corpus, the chunking or the weighting changes; the floor is a measured
constant, not a tuned one, and it is the first thing that becomes wrong.

### Phase 12 — Retrieval filters

`retrieve.py` offers three combinable filters, each justified by a measured
failure:

| Filter | Behaviour | Why |
|---|---|---|
| `manual_id` | exact | §5.4 is fire-safety in Progoti and livestock insurance in Dabi. A clause citation without a manual name is wrong two thirds of the time |
| `insurer` | exact, **required** for any premium or exclusion query | The same product carries a different premium per insurer. An answer omitting the insurer is right half the time, and the officer cannot tell which half |
| `programme` | **permissive** — the named programme *or* programme-neutral | See below |

**Why `programme` must be permissive.** Measured 14 Sep 2026:

| Manual | Chunks | Programme-tagged | Neutral |
|---|---|---|---|
| dabi_2025 | 400 | 124 | 69% |
| progoti | 431 | 160 | 63% |
| livestock_grow_2026 | 36 | 2 | 94% |

`programmes_in` tags a chunk only where the programme is **named**. A strict
filter would discard 94% of the livestock manual from both programmes. The
manual serves both — its enrolment step names শাখা ব্যবস্থাপক for one and
এলাকা ব্যবস্থাপক for the other — so a neutral chunk is in scope for *every*
programme, not none.

**Product status is joined at query time, never baked in.** A withdrawn
product's chunks stay in the corpus untouched: the terms still bind existing
clients, and deleting them would answer *"what were the terms?"* with silence.
Status comes from `products_catalogue.csv` at query time, so a suspended
product surfaces **as suspended, with its terms**.

**One subtlety worth copying.** `product_status()` returns
`(mapping, populated_count, total_rows)` and the caller **must** surface
`populated_count` — because a registry that is present but *empty* answers every
status question with "no status", which is indistinguishable from "active"
unless the emptiness is reported.

---

### Phase 13 — Evaluation

**Two gold sets.** `gold/bootstrap.yaml` (24 questions, Dabi + Progoti) and
`gold/bootstrap_livestock_grow.yaml` (13 questions).

**Four scores, reported separately, never averaged:**

1. **fact recall** — atomic, per `gold_facts` entry
2. **citation accuracy** — manual name *and* clause; a missing manual name is
   **wrong, not partial**
3. **abstention accuracy** — on `answerable: no` only, and invisible in any
   aggregate
4. **violation rate** — `must_not_contain` hits; non-zero is a report-stopper
   regardless of the other three

**Why not RAGAS as the headline.** RAGAS faithfulness measures whether the
answer matches the *retrieved context*. If the context says `দাকি`, a wrong
answer scores perfectly. It is a fine secondary panel for retrieval tuning; it
cannot be the number you report.

**Why ~21–23% of questions are deliberately unanswerable.** It feels high and
it is correct: every corpus gap is a question an officer will actually ask, and
the failure mode is **confident invention, not silence**. `forbidden.json` maps
each clause to the numbers appearing in it, built from our own chunks — that is
what turns "don't invent a ceiling" into something checkable. G-131 asks what a
CDO can approve; no such ceiling exists; and §7.1 is a table of authority full
of taka figures formatted exactly like the right answer.

**Provenance is recorded per question and scores never merge across it.**

- `chunk_derived` questions were written while looking at a chunk, copying its
  spelling. They share assumptions with the code, so they measure **agreement**.
- `field_sourced` questions are an officer's own wording and spelling,
  unedited. Those are what reveal alias gaps.

The digit-fold classifier once scored **7/7** against self-written sentences
using বীমা where the corpus says বিমা. A merged score is what hides that.

**Five ways a gold file can lie to you, and what stops each:**

| Trap | Consequence | Guard |
|---|---|---|
| Unfilled `TODO:` facts | score 0 recall, indistinguishable from a retrieval regression | `gold_eval.py` skips and names any question holding one |
| English-prose facts | `fact_present` matches a Bengali literal or a figure, so prose scores as a miss however good the answer | Report counts them separately and says to rerun with `--judge llm` |
| Bare `no` in YAML | YAML 1.1 parses it as boolean `False`, silently dropping every unanswerable question out of the abstention bucket | Quote them; `norm_answerable()` handles it anyway |
| A gold fact that is a bare **token** | the verbatim check passes on any chunk containing the string, which is not the same as a chunk that answers the question | Write the clause's own sentence as the fact, not a role or product name. See below |
| A question whose **scope** came from a candidate pack | the chunking's errors are inherited as facts about the manual, and the question cannot fail in the direction it is wrong | Read the clause in the source before fixing the question's extent. See below |

**The verbatim check does not verify that a chunk ANSWERS the question.** It
verifies that a string appears in it. With a multi-word clause sentence those
are close to the same thing. With a one-token fact they are nearly unrelated,
and the check goes green on a chunk that cannot answer anything.

Measured on G-213 (2026-09-17). The question is *who approves taking
administrative action in the Progoti programme?* The gold fact is the bare role
token `আরএম`. Its ref, `progoti_0133`, is the opening chunk of 3.9.1(ka) -- the
preamble listing **what to check before** acting against a member of staff. It
contains `আরএম`, so `validate_gold` passes it. It never says who approves.

G-214 is the same failure with the answer visible next door. Its ref,
`progoti_0142`, holds items 3-5 of 3.9.2, where `আরএম` appears only as an
assistant -- "এই কাজটি করবেন বাদী এবং এলসি ল'ইয়ার, **সহযোগিতা করবেন** এএম(দাবি)
এবং আরএম(প্রগতি)". The approval actually sits in `progoti_0140`, the first chunk
of the same clause: "কমিটি ... মামলা করার **অনুমোদন দিবেন**" -- the DCMC
committee. No chunk in 3.9.2 contains `অনুমোদন করবেন` at all.

Both refs pass every check in the repo. Both are wrong. The token was almost
certainly what the ref was chosen BY: search the corpus for `আরএম`, take a chunk
that contains it. That is the text-match provenance failure (5.3, instance 7)
reappearing in the hand-authored refs after it was removed from the code.

**Rule.** A gold fact is the clause's own sentence, or the figure with its unit
-- never a role name, product name, or other single token.

**A question written from a candidate pack inherits the chunking's errors as
facts about the manual.** Distinct from the bare-token trap: there the fact was
wrong, here the question's *extent* is wrong, and nothing in the gold file
looks unfinished.

Measured on G-223 (2026-09-17). It tested a three-step procedure, with three
verbatim step headers and three refs, and passed every check. §5.1.8 has
**six** steps. Three was simply what the first three chunks of the candidate
pack exposed -- `dabi_2025_0198`, `0199`, `0201` -- and the later headers sit
at offsets 64, 0 and 279 inside chunks whose leading content belongs to the
*previous* step (Phase 12). So the pack made a six-step procedure look like a
three-step one, and the question recorded that as a property of the manual.

The failure this hides is the one that matters for a procedural answer: a
system that stops at step 3 scores **identically** to one that completes all
six, because the gold set contains nothing after step 3. The question could
not fail in the direction it was wrong.

**Rule.** If a question's extent -- how many steps, how many bands, how many
roles -- was inferred from retrieved chunks rather than read from the clause,
treat it as unverified in that dimension specifically. `gold_status.py`'s
"filled but not yet page-checked" list is exactly this set, and it is why
`--require-render` stays off until a human has read the printed clause. Every
question in this file derived from `out/candidates.md` is suspect in the same
way; G-223 is the one that has been caught. A role token is
evidence that a chunk MENTIONS a role, and mention is not authority: 3.9 names
`আরএম` as applicant, as assistant, and as approver, in different clauses.

**The free tier cannot support a scored run, and the quota error says so in
a way that reads as something else.** Measured 2026-09-17/18 from the full
429 body, which the truncated log line hides:

```
quotaId    GenerateRequestsPerDayPerProjectPerModel-FreeTier
quotaValue 20
metric     generativelanguage.googleapis.com/generate_content_free_tier_requests
retryDelay 58s
```

**Twenty requests per model per day**, resetting at midnight US Pacific. One
question is one call:

| set | questions | calls | budget |
|---|---|---|---|
| synthetic | 4 | 4 | 5 runs per model per day |
| real gold | 39 | **156** | **8 days on one model** |

A real scored run cannot complete inside a quota window, and a run that
cannot complete yields nothing -- `answer_one` raises on the first provider
failure, so a run beginning with two calls left writes no answers at all.

**`retryDelay: 58s` is what cost four sessions.** On a per-DAY quota it is a
generic backoff hint, not a time-to-reset. Every exhaustion therefore looked
transient: it was retried, the retry failed the same way, and the failure was
attributed to an unreliable model. Three separate findings in this journal
were shaped by that misreading before the full error body was ever printed --
`gemini-flash-latest` was recorded as "flaky" when it was out of budget, a
model comparison was run with one arm silently answered by the other, and an
A/B was planned at 24 calls per model against a 20-call ceiling.

Two operational consequences worth keeping:

- **Every reachability probe costs 1 of the 20** -- a quarter of a synthetic
  run. A `quota_report` over 13 models spends three runs' worth. `probe_model`
  caches per process for this reason. `models.list()` is free; reachability is
  not.
- **Budget before running:** `(runs x questions) + probes <= 20`.

**THE CONSEQUENCE FOR THE PLAN.** A real scored run needs either a paid tier
or a sampled subset. Independently, the free tier carries a clause permitting
inputs to be used for model training, and the corpus cannot clear it -- these
are BRAC internal documents pending a data-protection decision, which is what
`generate.CORPUS_GUARD` exists to enforce. **Both constraints point the same
way**: no real chunk goes to this provider on this tier, whatever the budget.
The provider decision is therefore not a cost question that can be deferred
until the eval is built; it gates the eval.

**No sampled-subset mode has been built, deliberately.** Sampling 5 of 39
questions would make all four metrics noisy in a way the report cannot
express: it prints counts per provenance with no interval, so a 5-question
recall figure would read exactly like a 39-question one. Given this project's
failure mode -- a number that looks like a measurement and is not -- that is
worse than waiting.

**Retrieval results so far** (`out/retrieval_eval.json`, 15 questions / 17 query
variants — 10 field-sourced, 5 chunk-derived; 8 answerable, 4 partial, 3 not):

| Metric | Result |
|---|---|
| recall@1 | 12 / 17 |
| recall@3 | 15 / 17 |
| recall@5 | 16 / 17 |
| recall@10 | 16 / 17 |

`retrieval_eval.py` reports **four splits, never averaged, with no headline
number** — by provenance among them, for the reason above.

**A recall figure without its random baseline is not a result.** Added to
`retrieval_eval.py` permanently on 2026-09-17, after the figures above were
found to be measuring the gold file rather than the retriever.

The baseline is the chance that k chunks drawn at random from the corpus
include at least one gold chunk:

```
baseline_at(G, N, k) = 1 - C(N-G, k) / C(N, k)          # = G/N at k=1
```

**The worked example, and why this is not a nicety.** G-108 scored **100%
recall@3**. Its gold set, located by text match, was **396 of 867 chunks** --
46% of the corpus, for a question scoped to a 36-chunk manual, because the
fact strings recurred across all three manuals. A retriever drawing three
chunks at random scores **84%** on it. The 100% was true and almost
uninformative.

Across the twelve then-scoreable livestock questions the mean baseline was
**24% at @3 and 10% at @1**; G-111's was 55% and G-101's 36%. The headline
"recall@3 15/17" above is a figure of that kind and should not be quoted.

Once gold location moved to `source_refs.chunk_id`, gold sets fell to 1-2
chunks, baselines to ~0-1%, and the honest number for the same manual is:

| | @1 | @3 | @5 | @10 |
|---|---|---|---|---|
| recall | 29% | 43% | 57% | 100% |
| baseline | 0% | 0% | 1% | 1% |
| above chance | +28% | +42% | +56% | +99% |

Lower, and worth far more: 43% at @3 against 0% chance is a measurement, where
100% against 84% was a coin toss with extra steps. `_block` now prints
`recall`, `baseline` and `above chance` on every split; there are also
per-question and per-language tables, the per-question one sorted by gold-set
size so the weakest-signal questions come first. Read the gap, never the recall
alone -- and recall BELOW baseline means the retriever is doing worse than
chance, which no un-baselined report can show you.

---

## 4. Why each tool, in one table

| Choice | Alternative considered | Why this one |
|---|---|---|
| `.docx` source over `.pdf` | PDF for all manuals | Word gives real Unicode **and** real heading styles — the two things the PDF path must reconstruct |
| `python-docx` + raw `w:` XML | `docx2txt`, `mammoth` | Needed `gridSpan`/`vMerge` and style inheritance; the convenience wrappers drop both |
| Custom span-wise Bijoy conversion | `bijoy2unicode` alone | The mapping is easy; knowing *which spans to feed it* is the hard part, and needs Word's run structure |
| Six PDF libraries in a shootout | Trusting one | Identical output from six libraries is the proof that the *file* is broken, not the library. That conclusion is unavailable from one library |
| Hand transcription | `ocr_bench.py` | 8 legible pages. OCR benchmarking needs an engine **and** a hand-typed gold set — i.e. doing the transcription anyway, plus apparatus |
| FAISS, local | Hosted vector DB / embedding API | 867 chunks, internal data, and full reproducibility from the repo |
| Hybrid dense + BM25 | Dense only | Dense treats ২.৩৫ and ২.৪০ as near-identical — precisely the premium trap |
| `display_norm` for the index | `canon` | `canon` folds ০-৯ away, and every figure in these manuals is written in Bengali numerals |
| Digit twin in the index | Normalising queries to one form | Neither ২.৩৫ nor 2.35 should be the privileged spelling |
| Threshold exactly 1.000 | 0.95 | 0.95 merges 35 pairs that disagree about **who may approve a loan** |
| Metadata equivalence, not query expansion | Expanding সিডিও→পিও | The term identifies which manual the asker works under; expansion erases the routing signal |
| Per-literal exclusions | General letter-boundary guard | A boundary guard drops inflected forms like `সিডিওকে` and silently changes recorded counts |
| AST-based lint | Runtime assertions | A pattern's nukta form is a *source* property; there is no runtime moment at which to check it |

**A fabricated figure looks like a paraphrase to a similarity metric.**
G-207 asks which approver signs a new loan in a given band. The gold fact is
`নতুন ঋণ: ২০০০০-৭০০০০ — বিএম`, and it is the same in both manuals —
`dabi_2025_0049` and `progoti_0056` are byte-identical twins and both print
`২০০০০-৭০০০০ — বিএম`. Both models answered with a lower bound of **১৫,০০০**,
which occurs in neither manual:

```
gold    নতুন ঋণ: ২০০০০-৭০০০০ — বিএম
gpt-oss নতুন ঋণ ১৫০০০‑৭০০০০ টাকা পর্যন্ত অনুমোদনের ক্ষমতা শাখা ব্যবস্থাপক (বিএম) এর।
qwen    dabi_2025 §2.1.1 অনুযায়ী, ১৫,০০০ থেকে ৭০,০০০ টাকা পর্যন্ত নতুন ঋণের
        অনুমোদনের ক্ষমতা শাখা ব্যবস্থাপক (বিএম)-এর।
```

**My token-overlap proxy scored this 0.8 and classified it a paraphrase.**
`৭০০০০`, `বিএম`, `নতুন` and `ঋণ` all match; only the lower bound is invented,
and it is one token out of five. A similarity score cannot distinguish "said
it differently" from "said it wrongly", because the token that decides
correctness is exactly the one a majority vote outweighs. The llm judge
failed it correctly; the proxy did not.

So **class A cannot be trusted without reading the fact.** The classifier was
useful for separating retrieval misses from everything else — class C is
keyed on chunk ids and is exact — but its A/B split is a hypothesis, not a
measurement, and at least one of its A cases was the most serious failure in
the set. A confidently wrong figure is worse than a missing one: the officer
has no signal that anything is off.

**A context glossary: a measured negative. Default stays off.** The idea was
to append expansions for the role and product terms appearing in the
retrieved chunks, read from `roles.py` and `productmatch`, because
`gpt-oss-120b` had invented সিনিয়র ডিস্ট্রিবিউশন অফিসার and পোস্ট অফিসার for
two terms the repo already knows correctly. One run per model over the 17
recall-scoreable questions.

**G-222 could never test the idea.** `roles.py` row 4 is the combined string
`Credit Development Officer (Dabi) / Programme Organiser (Progoti)` — it
*states the equivalence the gold fact asks the model to discover*. So a
correct answer on that question is a transcription, not an inference, and the
phrasing shows it. qwen reproduced all eight gloss fragments verbatim
including both parentheticals, wrote **গ্লসারি অনুযায়ী** ("according to the
glossary"), and **cited the glossary instead of the manual** — losing both its
citations. Copying and reasoning are indistinguishable in the score and
distinguishable in the text; only the text was checked, and only the text
showed it.

**Reasoning is not safer than copying — it fails differently.** gpt-oss did
restate rather than copy: it dropped both parentheticals and Programme
Organiser entirely and framed the manual-to-term mapping in its own words. In
doing so it asserted that *both* terms mean "Credit Development Officer",
which is wrong for পিও. The gloss did not contain that error; the restatement
introduced it.

**The over-suppression trade arrives through context too.** G-205, gpt-oss:

```
WITHOUT  সঞ্চয় ফেরত সেবা হল … শাখা ব্যবস্থাপকের অনুমোদন সাপেক্ষে, সদস্যকে
         স্বশরীরে অফিসে উপস্থিত হতে হবে …【progoti §1.2.3】
         cites: progoti §1.2.3, dabi_2025 §2.2.1
WITH     আপনি কোন ম্যানুয়ালের (progoti বা dabi_2025) সঞ্চয় ফেরত সেবা সম্পর্কে
         জানতে চান?
         cites: none
```

A substantive answer carrying two facts and two citations became "which
manual do you mean?". Showing both manuals' terms side by side triggered
rule-6 behaviour on a question that had an answer. Six rule-4 wordings failed
on the same trade — constrain the model and it withholds what it does know —
and this is the first time that trade has arrived through CONTEXT rather than
through a rule's wording. Context is not a safer place to put an instruction;
it is the same instruction with no wording to audit.

**The ledger: 26 of 34 answers perturbed, one metric better and four worse.**

| | changed | metric moves |
|---|---|---|
| qwen | 11 of 17 | G-222 citation **worse** |
| gpt-oss | 15 of 17 | G-214 recall better; G-205 recall 2→0 and citation **worse**; G-219 language **worse** |

**THE ONE CLEAN RESULT, and the design detail behind it.** No
cross-programme role leakage: 18 of 18 single-manual-scoped answers across
both models, no new leaks and none pre-existing. A dabi-scoped answer never
named পিও and a progoti-scoped one never named সিডিও — the failure
`expand_in_query = NO` exists to prevent did not arrive through context.
**The reason is that only terms PRESENT IN THE RETRIEVED CHUNKS are emitted.**
A whole-registry glossary would put both halves of every per-manual pair in
front of the model on every question and would not have this property. If
anyone revisits this, that is the detail to keep.

The post-filter also never fired: 0/17 on all four runs, and
`answer_violations` is empty for G-212 and G-213 on both models even though
those two arm `must_not_contain_literals` on exactly the role terms the
glossary supplies.

**A token proxy that over-predicted by half again — and a wrong inference
drawn from that.** The char-based estimate said +5.1%; the billed figures were
+3.0% (qwen, 56,480 → 58,172) and +3.3% (gpt-oss, 44,059 → 45,521).

**CALIBRATED 19 Sep 2026: the constant is 2.578 chars/token, not 1.666.**
Measured against billed `prompt_tokens` on the 17-question k=5 run:
113,592 characters over 44,059 billed tokens. Predicting the same run
forward from 2.578 gives 44,048 against 44,059 actual — 0.02% out. The old
constant over-states every absolute token figure by **+54.7%**.

**WHAT ACTUALLY MOVES, and the reasoning error above.** The paragraph as
first written argued that because the same constant produced the **1.69x**
clause-expansion figure, that figure was "likely an overestimate on the same
scale". That does not follow, and it is worth keeping as an error. 1.69x is a
RATIO OF TWO ESTIMATES MADE WITH THE SAME CONSTANT, so the constant cancels
exactly: 5,886/3,473 = 1.695 before rescaling and 1.695 after. The same is
true of the +5.1% glossary estimate and of "4 of 17 questions over 2x the k=5
context" — all three are ratios and all three are untouched.

**SO THE GLOSSARY OVER-PREDICTION IS STILL UNEXPLAINED, AND THAT QUESTION IS
OPEN.** Calibrating the constant did not close it and could not have: the
estimate said +5.1% and the billed figures were +3.0% and +3.3%, and both the
estimate and the outcome are ratios, so every chars/token constant on earth
predicts the same +5.1%. Whatever makes the glossary cost less than its
character count implies is a property of the TOKENISER, not of the constant —
the most likely candidate is that the glossary's content (Latin role
expansions, repeated registry phrasing) tokenises at a different rate from
the Bengali manual prose the constant was fitted on, so a single global
chars/token figure cannot price it. **Not measured. Do not record this
paragraph as the explanation.** Measuring it means tokenising the glossary
block on its own and comparing its own chars/token against 2.578.

What does change is every ABSOLUTE token count derived from the constant:

| figure | as published | rescaled |
|---|---|---|
| clause expansion, median k=5 context | 3,473 | **2,244** |
| clause expansion, median expanded context | 5,886 | **3,804** |
| clause-expansion growth **1.69x** | 1.69x | **1.69x — unchanged, a ratio** |
| glossary cost **+5.1%** | +5.1% | **+5.1% — unchanged, a ratio** |
| questions over 2x the k=5 context, 4 of 17 | 4 of 17 | **unchanged, a ratio** |

None of it changes the clause-expansion conclusion — 0 of 14 facts recovered
at any cap — and the cost side is now a calibrated estimate rather than an
upper bound. The one place the correction bites is any comparison of an
absolute estimate against the **8,000 TPM** ceiling, because that ceiling is
an absolute number: at 1.666 the k=20 prompts would have been projected ~55%
larger than they are. The k=20 projection in Phase 15 uses 2.578 throughout.

**THE RULE.** A proxy constant is invisible in every ratio and decisive in
every comparison against an absolute limit. Before rescaling a figure, ask
which of the two it is.

**Clause-level expansion: a measured negative. Do not rebuild it.** The idea
was to pull in sibling chunks sharing a retrieved chunk's `manual_id` and
clause, so that a section arrives whole. Measured on the 17 recall-scoreable
questions, k=5, token cap 6,000:

| | result |
|---|---|
| unachievable facts recovered | **0 of 14** |
| unachievable questions recovered | **0 of 6** |
| median context tokens | 2,244 → 3,804 (**1.69x**) — rescaled 19 Sep to the calibrated 2.578 chars/token; the published 3,473 → 5,886 used 1.666 and over-stated both by 54.7%. The RATIO is unaffected. |
| questions over 2x the k=5 context | 4 of 17 |

Raising the cap to 9,000 recovers nothing — the cap is not the binding
constraint. Adding parent-clause awareness recovers exactly one fact.

**MY PREMISE WAS WRONG, and the reason is worth keeping.** I expected G-223's
six step headers to arrive together off any single §5.1.8 hit. *None of them
is retrieved* — the top-5 for G-223 holds §5.2.6, §5.4.14 and §6.3, so there
is no §5.1.8 anchor to expand from. Expansion can only widen around a chunk
already found; it cannot find a clause the retriever missed.

The 14 facts split three ways, and only the middle one is addressable:

| cause | questions | facts | reachable by a clause rule? |
|---|---|---|---|
| gold clause never retrieved — no anchor | G-216, G-217, G-208, G-223, G-222 | 11 | **no** |
| parent/child mismatch — gold is §5.4, retrieved are §5.4.1…§5.4.12 | G-215 | 2 | yes, with parent awareness |
| anchored but never reached | G-207 | 1 | not by raising the cap |

A bug worth recording because it nearly produced a false negative: `ix.meta`
rows carry **no `clause` field** — `search()` derives it with `clause_of`. The
first version keyed on `m.get("clause")`, so every chunk fell into the `''`
bucket and the 0/14 was an artefact of the measurement, not of the idea. The
corrected version also refuses `''` as an expansion key: an empty clause is
the *absence* of one, and expanding on it pulled 397 chunks of a single
manual into the candidate pool.

**RETRACTED 19 Sep 2026: every reranker RECALL figure in this project is
void.** Anything reporting that `bge-reranker-v2-m3` moved recall@5 --
including the "9/17 -> 1/17 collapse" reported in session notes -- measured a
model whose tokenizer never loaded and which was therefore ranking by
sequence length. See Convention 3, instance 15. Those numbers must not be
cited as a reason for anything, in either direction: they are not evidence
that the reranker is bad, they are evidence that the harness was broken.

**THE RERANKER IS STILL REJECTED, ON TWO REASONS THAT SURVIVE THE
RETRACTION.** Neither depends on any recall number:

1. **It cannot be resident.** Loading it beside the embedding model raises
   `MemoryError` on the second load; this machine has 7.9G with ~2-3G free in
   practice. Serving needs the embedder for the query and the reranker for
   the pool, in the same process, at the same time. It does not fit.
   **Re-measured from the other direction, 19 Sep 2026: the FIRST model now
   fails to load when the ordinary toolchain is open** — LaBSE died in
   `transformers`' tensor materialisation at 11.5 GB commit charge against a
   14.4 GB limit. See §4f, which is the same ceiling stated as a standing
   constraint on the machine rather than as a fact about this one model.
2. **BGE-M3 addresses the same weakness and is deployable.** The reranker was
   proposed to compensate for a dense side that barely works -- LaBSE dense
   finds the gold chunk first on 1 of 16 questions. BGE-M3 fixes that at the
   embedding layer (dense@5 1/16 -> 10/16, §4a), costs nothing extra at query
   time, and is a drop-in index swap. A second model in the serving path is
   the expensive way to buy what a better first model gives for free.

The pool-rank data gathered during that work IS still usable, because it came
from the retriever and not from the reranker: the base ranks in
`pools30.jsonl` are LaBSE fused ranks, and §4a independently reproduces all
sixteen of them.

**G-223 cannot be completed from a top-30 pool, and that is structural.** Its
six gold chunks sit at fused ranks **9, 11, 22, 39, 53 and 57**. A reranker
over the top 30 sees four of the six, so fact recall for that question is
capped at 4/6 however good the reordering is. Question-level recall@30 reads
100% only because its *best* gold chunk is at rank 9 — which is why
question-level recall flatters a question whose answer is spread across six
chunks. **Do not widen k for one question:** k is paid on every query, and the
median context is already 3,473 tokens against an 8,000 TPM ceiling. The
honest options are a clause-aware fetch that expansion failed to deliver, or
accepting 4/6 and recording it.

**Six rule-4 wordings, and the two defects pull opposite ways.** Rule 4
forbids quoting a figure the context does not support. Measured on Groq
(1000 req/day/model, so budget is not the constraint), 3 runs per model per
wording, post-filter on:

| Wording | Leak | S-004 over-suppressed | `language_match` |
|---|---|---|---|
| 1 original | qwen 3/3, gpt-oss 0/3 | no | not yet measured |
| 4 per-figure | **all 4 models, 10/10** | no | not yet measured |
| 5 minimal | none | gpt-oss **2 of 3** | qwen **2/4** — English |
| 6 shape-only | none | gpt-oss **3 of 3** | script 4/4, ids 1/4–3/4 |

Wording 6 removed the copyable English phrase and the copying stopped — qwen
answers in Bengali 3/3. The same edit made gpt-oss-120b withhold the S-004
ceiling in every run instead of two, and drop it entirely rather than
asserting it exists unquoted, which costs the citation as well (2/2 → 1/2).
**Adding to rule 4 adds suppression; shrinking it invites copying.** No
seventh wording was written. This is the argument for the post-filter: a
prompt rule is advice, and every phrasing of a prohibition is something a
model can comply with in a way that defeats it.

**Rule 4 is lossy, not unsafe — and the block was renamed to say so.** The
`NOT-SAFE-TO-SHIP` warning named the wrong risk. Leaking is handled below the
prompt by the post-filter, which did not fire in any of wording 6's 24 calls
with violations 0/4 throughout. What survives is a loss on **partial**
answers, and it is model-dependent: on S-004, where the manual holds the
ceiling but not the tenure, `gpt-oss-120b` drops `৩ লক্ষ টাকা` 3/3 and the
`§9.1.1` citation with it (recall 2/7, cite 1/2), while qwen keeps both
(3/7, 2/2). A dropped fact is a dropped citation, and a missing figure is
indistinguishable to the reader from a figure the manual never had — which is
why **qwen is now the default model**, first in `GROQ_PREFERENCE`.

**A metric catches its neighbour's failure, not its mirror.**
`language_match` was added to catch a Bengali role literal translated *out*
of Bengali. The reverse went unseen: qwen transliterates the Latin manual id
*into* Bengali prose (`সিনথেটিক_এ` for `synthetic_a`, `§৯.১.১` for `§9.1.1`)
while keeping the `CITATIONS:` line correctly Latin — so citation scored 2/2
and `language_match` 4/4 on an answer whose reference cannot be looked up.
`language_match` now carries the mirror check as a third component rather
than a sixth metric, and it immediately found failures on **both** models,
not just the one that prompted it: both write the S-003 clause number in
Bengali digits (`ধারা ৯.১.১`). qwen falls to 1/4, `gpt-oss-120b` to 3/4. So
the model chosen as default for keeping facts is the worse one on rule 7;
the trade is recorded at both sites rather than resolved. **Every new metric
should be asked what its mirror image looks like.**

**What the mirror check cannot see, written down because it matters.** It
keys on characters, not a word list: an underscore beside Bengali text, or a
clause number in Bengali digits. So `synthetic_a` → `সিনথেটিক_এ` and
`dabi_2025` → `দাবি_২০২৫` are caught, and `progoti` → `প্রগতি` never can be —
প্রগতি is also the programme's name in ordinary prose, and flagging it would
fail every correct answer that names the programme. Two-component Bengali
decimals are deliberately not flagged either: `২.৩৫` and `২.৪০` are interest
rates in these manuals, and a percentage is fact recall's business.

**A canary fixture typed by hand is not the failure it claims to be.** The
mirror canary's first version hand-typed the codepoints of qwen's answer and
got two wrong — `\u0938` (Devanagari स) for `\u09b8` (স), and composed
`\u09df` where qwen emits য় **decomposed** as `\u09af\u09bc`. It still went
green, on a string the model never produced. The fixture is now generated
from the saved answer and asserted to be a verbatim prefix of it. Both
things that mattered — the exact codepoints and the nukta form — are
invisible in the rendered glyph.

---

## 4a. The embedding benchmark, finished — LaBSE's dense side barely works

Queued in the original benchmark and never run for want of RAM. Run
19 Sep 2026 as an isolated batch job: BGE-M3 encoded the 867 chunks alone in
its own process, fp32, in 18 resumable shards, ~50 minutes. n=16 — the
benchmark excludes `answerable: "no"`, so G-217 is out; the other 16 are the
recall-scoreable set.

| model | mode | @1 | @3 | @5 | @10 | @30 |
|---|---|---|---|---|---|---|
| LaBSE | dense | 1/16 | 1/16 | **1/16** | 6/16 | 9/16 |
| BGE-M3 | dense | 6/16 | 9/16 | **10/16** | 13/16 | 13/16 |
| LaBSE | hybrid | 5/16 | 8/16 | **9/16** | 12/16 | 16/16 |
| BGE-M3 | hybrid | 8/16 | 11/16 | **11/16** | 12/16 | 16/16 |

**THE HEADLINE: BGE-M3's DENSE SIDE ALONE BEATS LaBSE's FULL HYBRID** at
k=5, 10/16 against 9/16. LaBSE dense finds the gold chunk first for exactly
one question in sixteen. The fusion was not adding a complementary signal to
a working dense retriever; it was carrying one that barely functions.

Dense-only rank of the first gold chunk, over the whole corpus:

| qid | LaBSE | BGE-M3 | | qid | LaBSE | BGE-M3 |
|---|---|---|---|---|---|---|
| G-201 | 19 | **1** | | G-212 | 28 | **1** |
| G-202 | 29 | **1** | | G-213 | 10 | **1** |
| G-203 | 7 | *31* | | G-214 | 1 | *2* |
| G-204 | 38 | **1** | | G-215 | 101 | *195* |
| G-205 | 35 | **3** | | G-216 | 214 | **33** |
| G-206 | 10 | **2** | | G-219 | 33 | **5** |
| G-207 | 32 | **10** | | G-222 | 7 | *8* |
| G-208 | 41 | **8** | | G-223 | 9 | **1** |

Better on 12 of 16, and the improvements are not marginal: 19→1, 29→1, 38→1,
41→8, 214→33. Four regress, three of them trivially (7→8, 1→2, 7→31).

**THE ONE REAL REGRESSION IS G-215, AND IT IS INFORMATIVE.** 101 → 195, the
worst dense rank in the set under either model. G-215 asks what insurance
§5.4 covers, and §5.4 means fire-safety insurance in Progoti and livestock
insurance in Dabi. There is no embedding of that question that can rank the
right chunk first, because the question does not determine which chunk is
right. A better encoder cannot fix a question whose answer depends on a
manual the asker did not name — that is what rule 6 and the cross-manual
citation requirement are for.

**WHAT THIS DOES AND DOES NOT BUY.** The hybrid path is what ships, and there
the gain is real but much smaller: **+3 questions at k=1 (5→8) and +2 at k=5
(9→11), and nothing at k=10 or k=30.** Sparse is already finding what dense
misses at the larger cut-offs. So BGE-M3's value is concentrated exactly
where it matters most for generation — the top of the list, where the model
reads — and it is worth re-indexing for on that basis, not on the dense
table's much larger numbers.

**NOT YET MEASURED, and needed before adopting.** These figures come from
`benchmark_one`, which ranks the raw fused scores. The deployed `search()`
additionally collapses 1.000 duplicate groups and applies manual-scope
preference, and neither is in this path. The fact-level and citation-level
gain therefore is NOT known yet; only the question-level chunk recall is.
Adopting means rebuilding `out/index` on BGE-M3 (dim 1024, not 768) and
re-running the k=5 generation, and until that is done the honest statement is
that the retrieval floor rises and the downstream effect is unquantified.

**A CONSISTENCY CHECK WORTH RECORDING.** LaBSE's per-question hybrid ranks
here reproduce the pre-collapse pool ranks measured independently for the
reranker work — G-212 2, G-213 1, G-214 1, G-215 18, G-216 12, G-222 11,
G-201 3, G-202 4, G-203 1, G-204 3, G-205 6, G-206 1, G-207 10, G-208 30,
G-219 1, G-223 9 — all sixteen identical. Two code paths written days apart
for different purposes agree exactly, which is the only reason to believe
either.

**The cached LaBSE row was stale and the guard caught it.** The 15 Sep row
answered @1/@3/@5/@10 on n=12 from an older gold set, and reusing it would
have printed a number from a different denominator under a new heading.
`benchmark_subprocs` now refuses a cache whose `ks` or `per_question` do not
match the request and re-runs instead. The document matrix cache is safe to
keep and was verified byte-identical to `out/index/dense.faiss`
(max abs diff 0.0), so the LaBSE arm scores the deployed retriever exactly.

---

## 4a-2. BGE-M3 end to end: index built and switchable, generation NOT measured

**STATUS: NOT ADOPTED. `out/index` is still LaBSE.** The candidate index
exists and one variable switches to it, but the end-to-end result the
adoption decision depends on was not obtained, and the reason is a quota
limit this project had never recorded.

```
out/index          LaBSE, dim  768   <- live, unchanged
out/index_labse    LaBSE, dim  768   <- explicit copy, so the live dir can be swapped
out/index_bge_m3   BGE-M3, dim 1024  <- candidate
BFA_INDEX_DIR=out/index_bge_m3       <- the one flag; embed.py also takes --out-dir
```

Building the candidate cost no re-encode: `build()` already reuses
`out/benchmark/<slug>.docs.npy`, so the 3.5-hour BGE-M3 encode was paid once.

### What WAS measured, model-free: the retrieval gain is one question

Achievability depends only on the retrieved context, so it is measurable
without generating. At k=5, LaBSE vs BGE-M3:

| | LaBSE | BGE-M3 |
|---|---|---|
| facts in the denominator | 15 | **16** |
| facts unachievable | 14 | **13** |
| citation-achievable questions | 10 | **11** |
| questions whose context changed at all | — | **16 of 17** |

**The benchmark's +2 questions at k=5 became +1 fact.** Sixteen of seventeen
contexts changed and the achievability needle moved once: G-223 went from
0 of 6 gold chunks retrieved to 1 of 6. The other question BGE-M3 newly
reaches at k=5, G-205, was already fully achievable under LaBSE, so its
better rank (hybrid 6 → 1) buys nothing the metric can see.

**This is exactly the gap the benchmark could not show.** `benchmark_one`
counts "any gold chunk in the top k" over raw fused ranks. The deployed
`search()` collapses duplicate groups and prefers the scoped manual, and
achievability is per-FACT with twins. A question-level recall gain lands as a
fact-level gain only when the newly-found chunk carries a fact that was not
already reachable through a twin. Here, mostly it did not.

### Citations: no gain, and one real regression hidden by the exclusion rule

The generation run completed 16 of 17 questions before failing, so the
answer text is lost but the emitted citations were printed. Scored offline —
no model call, `cite_ok` is a pure function:

| | LaBSE | BGE-M3 |
|---|---|---|
| citation accuracy | 12/12 = 100% **[UNVERIFIED]** | 11/11 = 100% **[UNVERIFIED]** |

**UNVERIFIED — 22 Sep 2026.** The 12/12 figure came from a chat report, never from a file. The artefacts it named — `scratchpad/real/oss.jsonl`, `scratchpad/retrieved17.jsonl`, `out/answers.jsonl`, `out/answers_meta.jsonl`, `out/runs/` — are all absent, and `out/` is gitignored, so it cannot be recovered or re-scored. The last file-backed citation figure is **7/11 = 63.6%** from `out/eval_report.json` (19 Sep). That file matches this k=5 column on fact recall (8/15) and violations (0/17) but NOT on citation, 2b or language (17/17 there, 15/17 here), so it is a different scoring pass of the same question set, not this run re-scored. Do not quote 12/12. See §4j.


Both read 100%, and **that conceals a regression.** G-222 cited
`dabi_2025 §4.2.5` and `progoti §4.2.5` correctly under LaBSE; under BGE-M3
it emitted **no citations at all**. Because `gold_eval` counts a citation
failure as unachievable when the gold chunk was not retrieved — and only
failures can be unachievable, a pass being proof of achievability — G-222
leaves the denominator instead of scoring zero. The rate is unchanged and a
question that was correctly cited is now uncited. **A metric that excludes
the impossible will hide a regression INTO impossibility.** Read 2b's count
alongside 2: it went 5 → 5 while the population changed.

### Why it stopped: Groq's per-DAY token limit, which the headers cannot see

```
429 ... on tokens per day (TPD): Limit 200000, Used 197523, Requested 3199
```

The project had recorded two Groq limits — 1000 requests/day/model and 8000
tokens/minute — both taken from response headers. **There is a third, and it
is the binding one for a day of measurement.** 200,000 tokens per day per
model. The k sweep alone spent ~139,000 (44k at k=5, 81k at k=10, ~14k on the
k=20 subsets), and the BGE-M3 run's 16 completed questions took most of the
rest.

**THE HEADERS DO NOT CARRY IT.** Probed at the moment generation was being
refused:

```
x-ratelimit-limit-requests    1000      x-ratelimit-remaining-requests  983
x-ratelimit-limit-tokens      8000      x-ratelimit-remaining-tokens   7924
```

Everything looks healthy. `_pace_from_headers` paces on exactly these numbers,
so it cannot see the limit that actually stops the run, and it will pace
perfectly into a wall. The TPD figure appears only in the body of the 429.
This is the repo's recurring failure in a new place: **an instrument that
reports green because it is not measuring the thing that fails.**

Consequence for planning: a day's budget is ~200,000 tokens per model, which
at the k=5 context size is **about four 17-question runs per model per day**,
not the 25 the requests-per-day figure implies. Budget experiments in tokens,
not requests. qwen has a separate, nearly untouched bucket — but switching
generator mid-comparison would confound the very thing being measured, so the
BGE-M3 run should be finished on gpt-oss-120b when the window rolls over.

### What remains to declare BGE-M3

One command, once the token budget refills:

```
python generate.py --provider groq --model openai/gpt-oss-120b \
  --retrieved <scratchpad>/k/retrieved17_bge_k5.jsonl \
  --gold <scratchpad>/bootstrap_scoreable.yaml --judge llm ...
```

Then facts, violations and language against the LaBSE k=5 baseline
(8/15, 0/17, 15/17). Until that exists, the honest statement is: **the
retrieval floor rises by one fact and one citation-achievable question, one
previously-correct citation is lost, and the generation-side effect is
unknown.** On the evidence so far the case for adopting is weaker than the
benchmark table suggested.

### A smaller thing, fixed in passing

`generate.py` printed "N forbidden clause(s)" using `len(forbidden)`, which
counts the metadata keys as well. It read 7 when 3 clauses were armed, and 9
once `_roles`/`_roles_note` were added. It now counts keys without a leading
underscore, which is how `forbidden_build` documents the file.

---

## 4a-3. Three defects the BGE-M3 comparison exposed

None of these is about BGE-M3. All three were latent and the A/B is what made
them visible.

### 1. Achievability was not sticky across an A/B, so a regression vanished

metric 2 excludes a citation failure when the gold chunk was never retrieved.
Within one run that is right: charging a model for an impossible citation
measures retrieval, not citation. **Across an A/B it is wrong, because
retrieval config is exactly what moves achievability.**

G-222 is the case. `must_cite` is the bare manual ids, mode `all`.

| | cited | gold chunk in context | scored |
|---|---|---|---|
| LaBSE k=5 | `dabi_2025 §4.2.5`, `progoti §4.2.5` | no | **PASS** — a pass is proof of achievability |
| BGE-M3 k=5 | *nothing* | no | **excluded** — unachievable |

Both runs read **100%**. A question that was correctly cited became uncited
and the metric improved its own denominator to hide it. *A metric that
excludes the impossible will hide a regression INTO impossibility.*

`--baseline REPORT` fixes it. A question the baseline could cite stays in the
candidate's denominator whatever the candidate's own context holds. Nothing
is ever removed by the anchor, only added back. Both numbers print and are
never merged — the self-scored one answers "how did this run do on what it
could reach", the anchored one answers "did changing retrieval lose
anything", and one figure cannot carry both.

Anchored on the LaBSE k=5 run, over the 16 questions the BGE-M3 run
completed:

| | citation |
|---|---|
| LaBSE k=5, self-scored | 12/12 = 100% **[UNVERIFIED]** |
| BGE-M3 k=5, self-scored | 11/11 = 100% |
| **BGE-M3 k=5, baseline-anchored** | **11/12 = 91.7%** |

The canary is the real G-222 shape and was watched going **MISSED** with
`baseline_reachable` changed to trust the baseline's own `citation_achievable`
flag instead of counting a pass as proof — which is the way this would most
plausibly be got wrong.

### 2. Groq has THREE limits and the headers carry two

```
x-ratelimit-limit-requests    1000   remaining  983    per DAY, per model
x-ratelimit-limit-tokens      8000   remaining 7924    per MINUTE
(no header at all)          200000   remaining 2477    per DAY, per model
```

Those numbers were read at the moment generation was being refused. The third
limit appears only in the body of the 429:
`on tokens per day (TPD): Limit 200000, Used 197523`.

`_pace_from_headers` paces on the two visible numbers, so **it paced
perfectly into a wall** — sleeping for the per-minute budget while the
per-day budget ran out, then failing at question 16 of 17 and, because a run
either fully succeeds or yields nothing, writing no answers at all.

**200,000 tokens/day/model is the binding constraint**, and at the k=5
context size that is about **four 17-question runs per model per day** — not
the 25 the requests-per-day figure implies. Budget experiments in tokens.

The fix is a local ledger, because there is nothing to read:
`out/token_ledger.json`, keyed by UTC date and `provider:model`, written at
`complete()` — the one door every call goes through, so a new backend cannot
forget to account for itself. Before a pinned-model run, `tpd_preflight`
estimates the run from the calibrated 2.578 chars/token plus the reserved
`max_tokens` per question and **refuses to start if it cannot finish**:

```
token budget groq:openai/gpt-oss-120b -- used 197,523/200,000 (99%) today,
2,477 left; this run needs ~62,671
  REFUSING TO START. A run that runs out part-way writes NO answers at all ...
```

Refusing is the point. The failure being prevented is not "the run stops", it
is "the run stops at question 16 and throws away the 15 that worked". Warns
at 80%.

### 3. A retry destroyed the only record of the run before it

The BGE-M3 run produced 16 answers and wrote none of them. The retry reused
the same `--answers` path AND the same shell redirect, so the log went too.
Their citations survived only because they were still in a terminal
scrollback — which is not a storage medium.

`--run-id` (default a UTC timestamp) now names a per-invocation log under
`out/runs/`, and the log carries **the answer text**, written and flushed
line by line as the run proceeds. A log written at the end is no use to the
run that does not reach the end.

Verified by killing a run mid-flight: no answers file was produced — exactly
the BGE failure mode — and the log held the completed answer in full.

**The deeper issue is NOT fixed and is recorded as open.** "A run either
fully succeeds or yields nothing" is a deliberate invariant, and a partial
run still writes no answers file. What changed is that the evidence is no
longer lost with it. Whether the invariant should be relaxed to write partial
answers is a separate decision and has not been taken.

---

## 4b. Reading an artefact without checking when it was produced

**`out/read_answers.md` was built from a context the code had already
superseded.** The page reproduces the 17 gpt-oss answers in
`scratchpad/real/oss.jsonl`, generated **18 Sep 17:10**. The manual-scope fix
to `retrieve.py` landed **18 Sep 17:33** and `dedupe.py` at 17:57. Re-dumping
the same 17 questions with today's code reproduces
`retrieved17_after.jsonl` byte-for-byte and differs from `retrieved17.jsonl`
on **7 of 17 questions**: a different member of a 1.000 duplicate group
survives the collapse, so the answer is cited from the other manual.

**Two of the page's four headline findings were already fixed before it was
written.** Regenerating at k=5 on the current context, same model, same
prompt:

| | read_answers.md (17:10 context) | current code |
|---|---|---|
| citation accuracy | 8/11 = 72.7% | **12/12 = 100%** **[UNVERIFIED]** |
| G-201 | cited `progoti §1.1.1` — wrong manual | cites `dabi_2025 §1.1.1` |
| G-206 | cited `dabi_2025 §2.1.1` — wrong manual | cites `progoti §2.1.1` |
| G-219 | cited `progoti §5.3.1` — wrong manual | cites `dabi_2025 §5.3.1` |
| G-208 | abstained on an answerable question | answers, with 2 citations |
| fact recall | 9/15 = 60.0% | 8/15 = 53.3% |
| language match | 16/17 | 15/17 |

The page's own framing invited this: it states its provenance in a table at
the top -- `generated 18 Sep 2026` -- and nobody compared that timestamp
against the mtimes of the code it was describing. **A generated artefact
carries a date; use it.** `ls -la` on `retrieve.py` was the whole check.

**THE READING WAS STILL WORTH DOING, and the split is the point.** The
findings that were archaeology are exactly the ones keyed to WHICH CHUNK was
retrieved -- the three wrong-manual citations and the false abstention.
Everything the reading found by looking at what the model SAID survives the
context change:

- **G-222 still concludes that সিডিও and পিও are different posts.** They are
  one post under two programme names -- `roles_registry.csv` row 2,
  `field_officer`, `manual_specific_name`. The fabricated expansions are
  gone, because the new context supplies `CDO/CO Sync Status` and
  `PO/CO Sync Status` in Latin and the model quoted them instead of inventing
  সিনিয়র ডিস্ট্রিবিউশন অফিসার. The wrong conclusion did not need the
  fabrication to survive, and it now argues for itself from the parallel
  clause numbering -- which is the evidence for the OPPOSITE conclusion.
- **Metric 3's gap is unchanged and now worse.** G-208 is `answerable: yes`,
  so abstention accuracy never scored it; its gold chunk is unretrieved, so
  metric 1 has an empty denominator and metric 2 excludes it. The page said
  its false abstention "appears in no metric at all". It still appears in no
  metric -- but it no longer abstains. It now answers **`সর্বোচ্চ স্তরের ঋণ
  অনুমোদন শাখা ব্যবস্থাপকের অনুমোদন দ্বারা করা হয়।`** and cites
  `dabi_2025 §2.1.1`, `progoti §2.1.1`. §2.1.2(গ) lists seven tiers topping
  out at `৪০,০০,০০১ থেকে উপরে — কর্মসূচি প্রধান`; the answer names the
  BRANCH MANAGER, the bottom of that ladder. The likely source is in its
  context: `শাখা ব্যবস্থাপক সদস্যর সাক্ষাৎকার গ্রহণ ও ফাইনাল চেক পয়েন্ট (এফসিপি)
  করবেন` -- a final check point read as a final approval. **Rule 4's failure
  mode arriving through a ROLE instead of a figure**, which `forbidden.json`
  holds only figures and therefore cannot see. A false abstention was
  replaced by a confident wrong answer with two citations, and every metric
  scored the same.

**The lesson is not "check provenance" in the abstract.** It is that a
finding keyed to retrieval is invalidated by a retrieval change and a finding
keyed to generation is not, and you can tell which is which BEFORE re-running
anything. Sorting the four findings that way would have cost nothing and
saved the two that were already dead.

---

## 4c. Two changes G-208 forced, one adopted and one rejected

G-208 asks which is the highest loan-approval tier and who approves it.
§2.1.2(গ) answers it in seven rows, topping out at
`৪০,০০,০০১ থেকে উপরে — কর্মসূচি প্রধান`. Across two runs the model gave two
different wrong answers and **not one of the five metrics moved either time.**

| | answer | what the metrics said |
|---|---|---|
| 18 Sep | `প্রশ্নে উল্লিখিত তথ্য ম্যানুয়ালে নেই` — declined | all five unchanged |
| 19 Sep, k=5 | the BRANCH MANAGER approves the top tier | all five unchanged |
| 19 Sep, k=10 | the AREA MANAGER approves the top tier | all five unchanged |

Metric 1 has an empty denominator (its gold chunk is never retrieved),
metric 2 excludes it as unachievable for the same reason, and metric 3 only
ever looked at `answerable: "no"`. Three wrong answers, three different
failure modes, one of them a refusal, and the scoreboard was flat.

### ADOPTED: metric 3 scores answerable questions too

Two numbers, reported separately and never averaged:

```
3a. abstained correctly   over answerable:"no"
3b. answered correctly    over answerable:"yes"/"partial"
```

Averaging them would let a model buy back a wrongly-declined answerable
question with a correctly-declined unanswerable one. Those are opposite
failures: one withholds what the manual has, the other would invent what it
has not. `partial` counts as answerable — the manual holds part of the
answer, and refusing the whole thing loses the part it holds.

**A CLARIFICATION REQUEST IS A REFUSAL HERE, and it needed its own detector.**
Rule 6 tells the model to ask which manual is meant when a question is
ambiguous, so the behaviour is sometimes right — but `answerable` is the
gold's judgement that the manual DOES answer it, and handing the question
back is then a non-answer. It matches no abstention pattern, so it had been
scoring as a pass. `is_clarification()` is separate from `is_abstention()`
because the two assert different things: an abstention says the manual does
not hold the answer, a clarification request asserts nothing at all.

**ONE MARKER, CHOSEN BY MEASUREMENT.** Over all 305 stored answers in the
scratchpad, `জানতে চান` fires on 10 of the G-* answers and every one is a
pure refusal with no citations and no content. `উল্লেখ করুন` is a strict
subset of those 10 and adds nothing.

**AND ONE MARKER DELIBERATELY REJECTED, which is the part worth keeping.**
`স্পষ্ট নয়` also fires only on refusals in every run on disk — and it is the
**gold fact for G-215**, `ম্যানুয়ালের নাম ছাড়া ধারা নম্বরটি স্পষ্ট নয়`. An
answer that says the clause number is ambiguous and then gives both readings
is CORRECT, and that marker would have failed it. *A marker that is clean on
the runs you happen to have is not thereby safe; check it against the gold
as well.* The gold is the list of things a right answer is allowed to say.

The effect at k=5: **3b = 14/16**, catching G-216 and G-223, both of which
handed an answerable question back. 3a is unchanged at 1/1.

The canary is built from two real 18 Sep failures — G-223's clarification and
G-208's false abstention — with G-202's real substantive answer as the
control, all three sliced from `real/oss.jsonl` and escaped by a script, never
hand-typed (Convention 3, instance 13). It was watched going **MISSED** with
`CLARIFY_BN` emptied and **DETECTED** with it restored.

### REJECTED: forbidden_roles_from_clause

metric 4 holds figures only, so G-208 naming the branch manager is invisible
to it. The obvious fix is the figure mechanism applied to roles: the gold
names a DECOY CLAUSE, `forbidden_build` reads the role literals occurring in
it out of `roles.py` (no literal typed in the gold file), and an answer
quoting one of them violates. Roles named in the question's own `gold_facts`
are exempt at check time, which is what lets G-208's gold span keep
সহকারী এলাকা ব্যবস্থাপক without the question contradicting itself.

**It works on the case it was built for and fails in both directions, so it
is off.** Armed with §2.1.1 — the group-loan table the model actually read —
measured on the 19 Sep runs:

| | result |
|---|---|
| the real k=5 answer (wrong) | **fires** on শাখা ব্যবস্থাপক — correct catch |
| the gold fact verbatim | clean |
| a natural correct answer | clean |
| **correct answer that also lists §2.1.2(গ)'s lower rows** | **FIRES on আঞ্চলিক ব্যবস্থাপক — FALSE POSITIVE** |
| **the real k=10 answer (also wrong)** | **clean — FALSE NEGATIVE** |

The false positive: আঞ্চলিক ব্যবস্থাপক appears in the decoy clause AND in the
correct one, and it is not in G-208's single gold fact, which covers only the
top row. The exemption cannot be widened to fix it — the gold fact is one row
and a complete answer is seven.

The false negative: §2.1.1 writes the area manager only as the abbreviation
এএম, never in full, so the decoy list does not contain এলাকা ব্যবস্থাপক — the
surface form the k=10 answer used to give its wrong tier.

**WHY IT CANNOT BE PATCHED INTO WORKING, which is the transferable part.** A
clause is the right key for FIGURES because a figure is its own identity:
২০০০০ in the decoy is the same token as ২০০০০ in the answer. A ROLE has
several surface forms — `roles.py` row `area_manager` declares both
এলাকা ব্যবস্থাপক and এএম — and the same form appears in the decoy clause and
the correct one. Keying on role IDENTITY instead of surface form fixes the
false negative and makes the false positive strictly worse, because identity
collapses এএম into এলাকা ব্যবস্থাপক and both tables use it. The information
that discriminates is *which row of which table*, and a clause-level list
cannot carry it.

**AND THE REPO HAD ALREADY FOUND THIS, BY A DIFFERENT ROUTE.** `validate_gold`
carries a standing exemption for G-220 whose stated reason is the same one
measured here: *"the wrong answer names branch posts, but its second gold
fact says branch posts exist elsewhere -- so a correct answer may name them
too while redirecting, and must_not_contain_literals seeded with role names
would flag both alike."* That was written about the hand-written literal
route; this measurement reached the identical conclusion about the
clause-derived route. Two independent arrivals at the same wall is the
strongest evidence available that it is a wall and not a bug — and it is a
reason to check the existing exemptions before building a mechanism, because
the answer was already in the repo.

The code is kept, unarmed and documented, in
`gold_eval.forbidden_role_violations`. No gold question carries
`forbidden_roles_from_clause`, so it returns `[]` everywhere and
`forbidden.json`'s `_roles` is empty. Same treatment as `--glossary`: the
measurement is the artefact, not the feature.

**So G-208 still scores in no metric except 3b, and only when it refuses.**
Its two confident wrong answers remain invisible, and the honest reason is
that its gold chunk is never retrieved — that is a retrieval problem wearing
a metric problem's clothes.

---

## 4d. Raising k: measured at 5, 10 and 20. No k adopted.

Question-level recall says the misses are reachable: recall@5 is 9/17, @10 is
12/17, @20 is 16/17, and the 8 missing gold chunks sit at fused ranks 6, 9,
10, 11, 12, 18, 18 and 30. So the material is there. The question is what it
costs to put it in front of the model, and whether the model uses it.

All three contexts were dumped from **one** `Index()` load, so the only thing
that differs between them is k. Verified nested: the k=5 chunk list is a
prefix of k=10's, and k=10's of k=20's, for all 17 questions.

| | k=5 | k=10 | k=20 |
|---|---|---|---|
| 1 fact recall | **8/15 = 53%** | **8/20 = 40%** | not runnable |
| 1b facts unachievable | 14 | 9 | **4** |
| facts in the denominator | 15 | 20 | **25** |
| 2 citation accuracy | **12/12 = 100%** **[UNVERIFIED]** | **12/15 = 80%** **[UNVERIFIED]** | not runnable |
| 2b citation unachievable | 5 | 2 | **1** |
| 3a abstained correctly | 1/1 | 1/1 | — |
| 3b answered correctly | 14/16 | 14/16 | — |
| 4 **violations** | **0/17** | **0/17** | 0/2 |
| 5 language match | 15/17 | 14/17 | 2/2 |
| billed prompt tokens | 44,055 | 81,174 | ~154,000 projected |
| median / max prompt | 2,538 / 3,866 | 4,804 / 6,720 | ~8,600 / ~11,100 |
| paced sleep | 331 s | 669 s | — |

**THE DECIDING RISK DID NOT MATERIALISE.** Rule 4's whole history is models
quoting adjacent figures, and k=10 is twice the adjacent material. Violations
are **0/17 at k=10**, unchanged. On the evidence available, widening the
window did not make the model quote more of it.

**BUT THE MODEL RECOVERED NOTHING FROM THE EXTRA CONTEXT.** This is the
result. Going from k=5 to k=10:

- 5 facts moved out of unachievable into the denominator — and the hit count
  stayed at **8**. Every newly reachable fact was missed.
- 3 questions' citations became achievable — and the correct count stayed at
  **12**. Recall and citation both fall as percentages purely because their
  denominators grew.

Two questions went backwards. G-205 lost a fact it had at k=5
(`সদস্যকে স্বশরীরে অফিসে উপস্থিত হতে হবে`, 2/3 → 1/3); G-207 and G-216
acquired rule-7 identifier failures they did not have. One went forwards:
G-206 recovered its fact, 0/1 → 1/1.

So the honest summary of k=10 is: **1.84x the tokens, 2.0x the wall clock, no
violations, one fact gained, one fact lost, and three more citations made
possible and not taken.** The extra chunks are not being read; they are being
paid for. Raising k does not fix a model that is not using the context it
already has.

**k=20 IS REFUSED BY THE PROVIDER, on 15 of 17 questions.** Groq returns
`413 Request too large` and `generate.py` returns 1 on the first provider
error, so the first attempt wrote nothing at all — it died on G-217, question
one. The ceiling is the 8,000 TPM window, and it counts the RESERVED
COMPLETION BUDGET as well as the prompt: `max_tokens=1024` is part of the
request, so the usable prompt is roughly 7,000 tokens, not 8,000. G-203 was
refused at an estimated 7,131 while G-215 went through at 7,220 billed, which
puts the practical boundary just above 7,200 and confirms it is not the
prompt alone. **Waiting cannot help** — the limit is per minute and a single
request exceeds one minute's entire budget.

Only G-215 and G-222 fit, and they are the two most informative ones: both
have facts that become achievable ONLY at k=20. Scored:
**3/4 facts, citation 1/2, violations 0/2, language 2/2.** So the newly
reachable facts ARE recovered when they arrive — which is the opposite of
what happened at k=10, and on a sample of two.

**NO k IS ADOPTED.** k=5 stays. The case against k=10 is that it costs double
and returned nothing; the case against k=20 is that the provider forbids it.
The k=20 sample of two is the only sign that more context can help, and two
questions is not a basis for changing the default. The finding that matters
more is §4a's: the retrieval floor is low because LaBSE's dense side barely
works, and raising k is paying to paper over that rather than fixing it.

---

## 4e. The demo page: three defects every check passed, found by rendering it

`app.py` was built, linted and tested, and all four conventions were green.
Convention 1 passed statically and dynamically. Convention 2 found no product
literal. Convention 4 reported no raw Bengali literal in a matching operation.
`pattern_audit.py` was 55/55. The Streamlit `AppTest` harness ran every branch
without an exception and the extracted HTML contained every element the design
asked for.

The page was still wrong in three ways, and **not one of them was visible to
any of that**. They were found by opening the page in a real browser
(Playwright, Chromium), screenshotting it, looking at it, and asking the
browser what it had actually done.

**1. The stylesheet said Helvetica Neue and the page rendered Source Sans.**

`BRAND["latin"]` is the brand stack and the rule setting it was real:

```css
html, body, [data-testid="stAppViewContainer"] { font-family: $latin; }
```

Streamlit sets its own `font-family` on the markdown container that every
`.bfa-*` block lives inside. That selector is *more specific and closer to the
element* than `html`/`body`, so it won every time. The chips, the flags, the
header tagline and the meta line all rendered in Source Sans while the
stylesheet, read by eye, plainly said otherwise.

`getComputedStyle` is what settled it:

```
.bfa-chip   12px / 19.2px / "Source Sans"       <- before
.bfa-chip   12px / 19.2px / "Helvetica Neue"    <- after naming .bfa-* directly
```

The fix is one rule naming the `.bfa-*` classes. The lesson is not the fix.
**A CSS rule that exists is not a CSS rule that applies**, and nothing short
of measuring the computed style on a rendered page can tell the two apart. A
reviewer reading the stylesheet would agree the brand font was set. It was
set. It never arrived.

**2. The token-budget warning rendered inside the answer card.**

It was concatenated into the card body, so it appeared under the model line,
inside the same white box as the answer — reading as though it were a
statement about *that answer*. It is a statement about the day. Correct text,
correct data, wrong container; moved to its own band below the card. No check
has an opinion about which box a true sentence appears in.

**3. Four magenta bars of equal weight made the refusal unreadable.**

This is the one that mattered. The page's whole purpose is that an abstention
must not look like an empty answer — §4c and the abstention canary exist for
exactly that. So a refusal got a flag saying "This is a refusal, not a blank."

It also got a flag for the unarmed post-filter, and a flag for product status,
and a flag for the token budget. Four bars, same 3px magenta border, same
size, same weight, stacked. The sentence carrying the system's most important
guarantee looked exactly like the sentence reporting how many rows are in the
product registry.

**Everything emphasised is nothing emphasised.** The flags are now two
weights: magenta and full size for *why this answer is not what the model
first said* (floor, post-filter, model's own refusal — at most one ever
shows), grey and quiet for standing conditions true of every query. The
quiet ones stay on the page; they are disclosures, they are just not the
headline.

### What this says about the check suite

Every check in this repo answers a question about the **source**. Does this
module write at import. Does this literal reach a matcher. Does this pattern
under-match nukta forms. Does this branch raise. They are good checks and they
have caught real defects.

None of them can answer *what did the user actually see*, because that is a
property of the rendered page — of CSS cascade, of visual hierarchy, of which
box a sentence sits in. It is measurable, but only by rendering and then
measuring, which is a different instrument from reading source.

Two of the three defects above (the font, the placement) are cosmetic in the
sense that no figure was wrong. The third is not cosmetic at all: it degraded
the one guarantee this project is built around, and it degraded it *on the
page only* — the meta row said `floor_fired` correctly the entire time.
Convention 3's rule generalises: a check must prove it can fail, and a check
that only reads source cannot fail on a defect that only exists once rendered.

**Carry this forward: if the deliverable is a page, render it and measure it.
Reading the source is not review.**
---

## 4f. This machine cannot hold the toolchain and an embedding model at once

Not a tuning problem and not a transient. It is a standing property of the
development machine, and it decides what can be built here.

**The machine.** 7.9 GB physical. Commit limit 14.4 GB (RAM plus pagefile).

**Measured 19 Sep 2026, across one session.** Free physical memory swung
between roughly **1.0 and 3.6 GiB** depending on nothing but what else was
open. Two readings taken twenty minutes apart while diagnosing: **2,741 MB**
free, then **3,093 MB** free.

**LaBSE loaded, and then stopped loading.** The same `retrieve.Index()` call,
in the same session, against untouched index files:

```
14:53   Loading weights: 100%|██████████| 199/199   [ok]
...
19:19   MemoryError
        transformers/core_model_loading.py:1238 in _materialize_copy
            tensor = tensor[...]
```

At the point of failure: **commit charge 11,487 MB against a 14,378 MB
limit** — under 3 GB of headroom — with the convention run's subprocesses,
two Chromium instances from the Playwright render, and the editors open. No
single process was large; the largest working set on the machine was 432 MB.
It is the *sum*, and it is mostly commit that is reserved rather than
resident, which is why "free memory looks fine" and the load still dies.

The failure is in `transformers`' tensor materialisation, which realises
weights in parallel worker threads — so the peak is higher than the model's
nominal fp32 footprint, and it arrives all at once rather than gradually.
Nothing in the repo caused it and nothing in the repo can avoid it. The index
files were verified intact afterwards: `dense.faiss` loads, `ntotal 867`,
`dim 768`.

### THE LIVE INSTANCE, and what it corrected

It happened again during the demo build, this time on the page rather than in
a terminal — the app rendered fine and died on the first ask. Measured at the
moment of failure:

| | |
|---|---|
| commit charge | **13.4 GB** of a **15.6 GB** limit |
| free physical | **1.8 GB** |
| total working sets, all processes | **2.5 GB** |

**Only 2.5 GB was actually resident.** The machine was not short of RAM; it
was short of *commit*. That distinction is the whole finding, and it changes
the advice: closing applications frees working set, and working set was never
the binding constraint. **Raising the page file is the lever that works.**
Closing things helps only insofar as it drops their commit reservations,
which are much larger than their footprints.

Note also that the commit **limit itself moves** — 14.4 GB earlier in the
session, 15.6 GB here, 16.0 GB twenty minutes later. Windows grows the
pagefile on demand, and it grows it *more slowly than a model load allocates*.
A limit read before a run does not predict the limit during it.

**AND IT IS NOT ALWAYS `MemoryError`.** The live failure raised:

```
OSError: The paging file is too small for this operation to complete.
         (os error 1455)          <- ERROR_COMMITMENT_LIMIT
  transformers/modeling_utils.py:343 in load_state_dict
      with safe_open(checkpoint_path, framework="pt") as f:
```

The commit ceiling surfaces through safetensors' mmap as **WinError 1455**,
an `OSError`, not a `MemoryError`. A handler written for `MemoryError` alone
catches the allocator-side failure and misses the commit-side one — which is
the more likely of the two here. `app.py`'s `_is_memory_exhaustion` now
recognises both, and recognises 1455 narrowly: `FileNotFoundError` is also an
`OSError`, and a missing index must never be reported as "out of memory".

### THE RULE

**Any run that loads a model needs a clear machine first.** Close the editors
and the browser, let the convention run finish, then start it. A model load
that succeeded an hour ago is not evidence that it will succeed now, so a
`MemoryError` from `retrieve.Index()` or `embed.py` should be read as *this
machine is busy*, not as *the index is broken* — check the index files before
concluding anything about them.

Corollaries that have already cost time:

- **Never run `conventions_check.py` and anything model-loading at the same
  time.** It spawns one subprocess per module, 45 of them, several loading
  torch or faiss. It is also the run whose dynamic stage needs a quiescent
  tree for an unrelated reason — see CONVENTIONS.md, rule 1's known defect.
- **Playwright leaves commit charge behind.** Two headless Chromium launches
  were enough to matter.
- **Budget the machine like the token ledger.** Both are per-day resources
  that fail late and take the run's output with them.

### This is the same constraint that rejects the reranker

§4a's first surviving reason for rejecting `bge-reranker-v2-m3` is "it cannot
be resident": loading it beside the embedding model raises `MemoryError` on
the second load. That reason was written from the same machine and the same
ceiling, and this session re-measured it from the other direction — **one**
model now fails to load when the ordinary toolchain is open. Serving needs
the embedder for the query and the reranker for the pool, in one process, at
once. It did not fit when the reason was written and it fits less now.

The generalisation worth carrying: **this repo's model choices are
constrained by the development machine, not only by accuracy.** BGE-M3 is
preferred over LaBSE-plus-reranker partly because it is one resident model
instead of two. Any proposal that adds a second model to the serving path has
to answer this section before it answers a recall number.
---

## 4g. Banglish retrieval was not weak, it was absent — and the eval could not see it

Two questions an officer typed, both of which answer correctly in Bengali:

```
progoti loner service charge koto?            top chunk livestock_grow_2026_0006 at 0.42
Sadharon Shonchoi er upor munafa har koto?    floor fired at 0.3063
```

### The sparse side returned literally nothing

Measured 20 Sep 2026 on the live index, BM25 only (no model needed, so this
survives the memory ceiling of §4f):

| | Banglish | Bengali |
|---|---|---|
| gold BM25, `progoti_0016_p2` | **0.0000**, rank 11 | 20.5039, **rank 1** |
| gold BM25, `dabi_2025_0017` | **0.0000**, rank 9 | 9.9041, **rank 1** |
| corpus chunks scoring above 0 | **10** and **8** of 867 | 196 and 159 |

Rank 11 and rank 9 are not near misses. They are ties at zero with ~855
other chunks — the rank counts only strictly-greater scores.

Document frequency says why. Not one content word exists in the corpus:

```
progoti 0   loner 0   service 0   charge 0     Sadharon 0  Shonchoi 0  munafa 0
প্রগতি 158  লোনের 19  সার্ভিস 49  চার্জ 18      সাধারণ 58   সঞ্চয়ের 33   হার 189
```

`expand_banglish` **was firing**, and appended exactly one gloss to each
query — `koto → কত`, the interrogative. Every content word passed through
untouched. So BM25 scored both queries on a single token.

**Every Banglish query therefore degenerated toward one chunk.** With `কত`
as the only surviving term, BM25 ranks whichever chunk uses it most:
`dabi_2025_0251` was top-1 for unrelated queries about service charges and
about savings alike. The failure was not that Banglish retrieved badly; it
is that Banglish retrieved *the same thing every time*, and the fused score
then rested entirely on the dense side, which §4a records as barely working.

### Three candidate fixes. Only one did anything

Gold-chunk BM25, measured cumulatively:

| stage | Q1 `progoti_0016_p2` | Q2 `dabi_2025_0017` |
|---|---|---|
| baseline | 0.0000 rank 11 | 0.0000 rank 9 |
| + registry routing | 3.8217 **rank 24** | 0.0000 rank 9 |
| + stemming | 3.8217 rank 24 | 0.0000 rank 9 |
| + vocabulary | 18.2739 rank 5 | 19.4825 rank 1 |
| **adopted: vocabulary only** | **14.4521 rank 6 raw / 4 collapsed** | **19.4825 rank 1** |

**ROUTING MADE IT WORSE AND IS OFF.** Routing romanised registry names into
the query — reading every Bengali form and Latin alias out of productmatch,
the catalogue and roles, so no literal is copied — lifted Q1's score off
zero and pushed its RANK from 11 to 24. The query gained প্রগতি, and প্রগতি
occurs in 158 of 867 chunks because one of the two manuals is entirely about
that programme. A term carried by 18% of the corpus is not evidence about
which chunk answers the question; it lifts every chunk in that manual past
the one that does. Q2 gained nothing — it names no product, programme or
role. Kept behind `ROUTE_REGISTRY_NAMES = False`, because the argument
against it is specific to a programme name that is a whole manual's subject;
a rare product name would behave the opposite way and is worth re-testing.

**STEMMING BOUGHT NOTHING BY ITSELF, AND THE REASON IS NOT MORPHOLOGY.**
`loner` is loan + the genitive `-er`, and `loan` was already a table key —
but stripping gives `lon`, and the table romanises the stem `loan`:

```
_stems('loner') -> ['loner', 'lon', 'lone']
'lon' a key? False        'loan' a key? True
```

A **romanisation mismatch, not a morphology gap**. Stemming is kept only
because `lon → লোন` was then added as vocabulary, and stripping is what
reaches it; on its own it moved neither query. Anyone re-deriving this
should not conclude that Bengali case endings are the problem.

**VOCABULARY DID ALL THE WORK.** Nine ordinary words the table lacked —
service, charge, munafa, har, sadharon, shonchoi, upor, lon and spelling
variants. No product, role or insurer name among them: সাধারণ is "ordinary"
and সঞ্চয় is "savings"; the *product* সাধারণ সঞ্চয় remains productmatch's.

### An alias column is not all aliases

22 of 43 catalogue rows carry a Latin-bearing alias, but several of those
entries are prose notes written into the column:

```
'§5.1.2 excludes its customers from ঋণ নিরাপত্তা বিমা cover'
'umbrella for ডিপিএস + মাসিক মুনাফা'
'underwrites ফায়ার সেফটি (Progoti §5.4.6) and গবাদিপ্রাণি সুরক্ষা'
```

Used as lookup keys those map the ordinary English words *umbrella*,
*cover*, *underwrites* and *excludes* onto product names — injecting a
product into a query that never mentioned one. `_alias_shaped` keeps names
and drops notes (no `§`, no parentheses, ≤4 words). A wrong expansion is
invisible where a missing one is merely unhelpful.

### A BANGLISH SCORE IS NOT COMPARABLE TO A BENGALI ONE

Q2's Banglish query now scores **19.4825 against its Bengali original's
9.9041** — nearly double, on the same gold chunk. Not better retrieval. The
expansion appends *uninflected stems* (সঞ্চয়, মুনাফা) while the Bengali
question is written with *inflected forms* (সঞ্চয়ের, মুনাফার), which match
fewer postings. Never read a cross-language BM25 comparison as a quality
signal; compare rank within one language.

### Measure the pipeline, not the component

Raw BM25 put Q1's gold chunk at rank 6 — outside k=5. After
`dedupe.collapse`, which `retrieve.search` applies and a component probe
does not, it is at **rank 4**:

```
Q1 banglish  collapsed top-5: progoti_0391_p2, dabi_2025_0015_p1,
                              progoti_0018, progoti_0016_p2, progoti_0391_p1
Q1 bengali   rank 1      Q2 banglish rank 1      Q2 bengali rank 1
```

Four of the top six raw hits were cross-manual duplicate pairs occupying two
slots each. A probe that skips the collapse under-reports every rank.

### The changelog chunk is a decoy, not an attractor

`dabi_2025_0366_p2` / `progoti_0391_p2` — the twin "পরিবর্তন সমূহ" changelog
rows — take top-1 on Q1. Their text is a list of changed clauses:

```
পরিবর্তণ: প্রগতি ঋণীর সার্ভিস চার্জ % — ১.১.২ — ৭
```

It contains প্রগতি, সার্ভিস চার্জ and the clause number ১.১.২, and **not the
figure**. Maximum lexical overlap with the question, zero answer content —
the G-210 trap shape, a changelog row citing a clause.

**It is not systematic.** Across ten Banglish queries it was top-1 on
**0 of 10**; it wins only where the question's exact terms are the terms of
a changelog line. Worth watching, not worth a rule.

### WHY THE EVAL NEVER SAW ANY OF THIS

**24 of 24 gold questions are written in Bengali.** At the time this broke,
the set held exactly **one** romanised string — `G-209`'s
`'PO koto taka loan approve korte pare?'` — and the Banglish path was
otherwise measured only against a synthetic fixture. A retrieval mode used
by real officers had no gold coverage at all, so no score could move when it
broke, and none did — every metric was green throughout.

**FIXED THE SAME DAY, narrowly.** Both live failures are now
`question_variants` on the questions they belong to — `G-202` and `G-203` —
so the set carries three romanised strings rather than one. That is a
regression test, **not coverage**: three variants across 24 questions still
cannot tell you how Banglish performs in general, only whether these two
specific failures have returned.

**Only the retrieval harness sees them.** `retrieval_eval.py` scores every
variant separately and tags each with `variant_language` (bn | banglish), so
a return to BM25 0.0000 shows up as recall@k on those lines. **`gold_eval.py`
does not read `question_variants` at all** — it scores `question_bn` only, so
the answer-quality side of the report is still entirely Bengali and adding
variants buys it nothing. Worth knowing before anyone reads a gold_eval
number as evidence about romanised input.

The general form is §4e's, one layer down: **a check can only fail on
inputs it is given.** The conventions checks read source and cannot see a
rendered page; the gold set is Bengali and cannot see a romanised query.
Both were green while the thing they were meant to protect was broken.
---

## 4h. The selector and the validator disagreed, and it cost 18,019 tokens

The quiz builder has two places that decide whether a chunk can carry a role
question: `candidates()` picks them, and `recheck_kind()` validates what came
back from the model. The role rule was relaxed — from "the chunk names
exactly one role" to "some segment scopes one role to a task" — and it was
relaxed in the selector only.

`recheck_kind` still held the old chunk-level count. So the selector admitted
the responsibilities tables, the model wrote questions about them, the tokens
were billed, and the validator then refused the same chunks for naming more
than one role:

```
role questions added : 6
refused              : 26
  Q-149 dabi_2025_0262: chunk names 3 roles; ambiguous
  Q-152 dabi_2025_0232: chunk names 3 roles; ambiguous
  ...
tokens spent         : 18,019
```

**26 of 32 questions discarded after being paid for.** 18,019 tokens for six
keepers, out of a 200,000/day budget — 9% of a day, spent on output that was
generated correctly, validated by a rule its own selector no longer used, and
thrown away.

### The same shape as every other divergence in this repo

| | one thing | the second copy | what it cost |
|---|---|---|---|
| `embed.py` vs `validate_gold` | gold-ref resolution | embed filtered TODOs before counting | ২.৪০% attributed to a chunk it is not in |
| `locate_gold_chunks` vs `resolve_ref` | which chunk a fact names | two resolution paths | recall counted against the wrong chunk |
| `canon` vs `display_norm` | which normaliser | one folds digits, one does not | 27/80 matches went to 0/80 |
| `generate.post_filter` vs `gold_eval` | what counts as a leak | shared deliberately | *prevented* — the one that was designed not to diverge |
| **selector vs validator** | **which chunks can carry a role question** | **relaxed in one place** | **18,019 tokens** |

Every previous instance cost correctness and was invisible until something
was measured. This one was visible immediately — the refusal list printed
itself — and is the first with a **direct, countable token cost**. That makes
it the cheapest instance to learn from and the easiest to explain: the two
copies did not drift over weeks, they disagreed the moment one was edited.

### What fixed it

`recheck_kind` now calls `role_scoped_facts` — the same function the selector
calls — and asks whether the correct answer is among the roles that function
admits. There is no second statement of the rule to fall out of step:

```python
askable = {r for r, _seg in role_scoped_facts(text, names, heading)}
if q["correct"] not in askable:
    bad.append("role not scoped to a task in any segment ...")
```

The re-run kept 22 of 30.

### Carry this

**One rule, one place — and when you relax a rule, grep for who else
enforces it.** The convention this repo already has ("one definition of every
shared thing") is usually stated about data: one fold table, one chunk
schema, one product matcher. This instance is about a *predicate*. A
selection rule and a validation rule that describe the same admissibility are
one thing wearing two hats, and editing one hat is how you pay for output
twice.

It is also an argument for making the validator call the selector's function
rather than re-stating its test, even when re-stating is two lines and
calling is a refactor. Two lines is exactly how long it takes to diverge.
---

## 4i. A character range that omits the virama, and the near-miss with rule 4

Instance 16 in Convention 3's table is worth its own note, because of what
it says about where Convention 4 stops.

The quiz builder's rule 4 asks whether a question names something that
distinguishes its own evidence from a sibling question's. It split both into
Bengali words with

```python
_re.findall(r"[অ-হ়-ৌ]{4,}", text)
```

That range covers Bengali letters (অ–হ) and the vowel signs (় – ৌ). It
**omits U+09CD, the virama** — the hasant, the character that joins
consonants into conjuncts. Bengali cannot be written without it.

So every conjunct split the word:

```
আঞ্চলিক      ->  ['চলিক']
ব্যবস্থাপক   ->  ['থাপক']
```

Fragments like `চলিক` appear in no real question, so the test found no
distinctive token in any question, concluded that every question was
adequately scoped, and refused nothing. **Rule 4 ran on every revalidate,
printed its line, and enforced nothing.** Measured: it should have dropped
four questions — two §7.1 authority-table pairs, each giving the same role a
different approval limit — and dropped zero. Widening to the full Bengali
block `[ঀ-৿]` fired all four.

### The near-miss with Convention 4

Convention 4 exists for exactly this family of failure: *a Bengali literal
and the text it matches must agree on nukta form*. Its whole subject is
character-level assumptions in Bengali patterns that fail silently and
under-match. It has a lint, two tiers, and a canary.

**It would not have caught this.** The rule covers a LITERAL reaching a
matcher — `গার্ডিয়ান লাইফ` written with a precomposed য় where the corpus has
the decomposed form. This was not a literal. It was a character **RANGE**,
containing no Bengali word at all, dropping a character that Bengali
requires. The lint's advisory tier even lists this file's other raw ranges
and says they "work today"; this one did not work today and was not on the
list, because a range with no nukta in it is exactly what the advisory tier
is designed to consider harmless.

The generalisation the convention is missing: **rule 4 is about the
characters you wrote; this is about the characters you left out.** A
matcher can be wrong by including the wrong form of a character, which
Convention 4 covers, or by excluding a character the script cannot do
without, which nothing covers.

### Whether the lint can be extended — measured before deciding

Candidate rule: *a character class containing a Bengali LETTER range must
admit U+09CD*. Counted over every string literal in the repo:

```
character classes with ANY Bengali-block range : 56
  of those, with a LETTER range                :  4
  of those, omitting U+09CD  -> would flag     :  4
```

**Four, not the thirty-five the advisory tier holds.** The noise worry was
unfounded: almost every Bengali range in this repo is a digit range
(`[০-৯]`) or the whole block (`[ঀ-৿]`), and neither is touched.

But three of the four flags would be **false positives**:

| site | pattern | verdict |
|---|---|---|
| `page_audit.py:78` | `[ক-হ]` inside `\(\s*(?:[ক-হ]|[০-৯\d]{1,3})\s*\)` | correct — a single-letter list marker `(ক)`, where a conjunct cannot occur |
| `pdf_structure.py:78` | `\(?[ক-হ]\)` | correct — same list marker |
| `products.py:99` | `[ক-ঞa-z0-9]{1,3}` | correct — same, an enumerator |
| `quiz_build.py:178` | `[অ-হ়-ৌড়-য়]` | **a real bug**, same family: a tail test that would not recognise a virama as part of a word |

So the rule is 1-for-4 as a blocking check and would need a `# noqa` on
three sites the day it landed. **Not adopted.** Recorded here with its
measurement so the next person does not re-derive the count. If it is ever
added it belongs in the advisory tier, where a reader decides, not in the
tier that fails a build — and the three list-marker sites are the argument
for that placement, not against the rule existing.
---

## 4j. A number in a report and not in a file is a memory, not a result

`12/12 = 100%` citation accuracy was quoted in five places in this journal,
including the current-status summary — the line someone reads to know where
the project stands. It was never written to a file.

Searched 22 Sep 2026: the string `12/12` appears in `PROJECT_JOURNAL.md` and
**in no `.json`, `.jsonl` or `.csv` anywhere in the tree**. Every artefact
the entries name is gone:

```
scratchpad/real/oss.jsonl       the answers          absent
scratchpad/retrieved17.jsonl    the k=5 context      absent
out/answers.jsonl               the gold-run answers absent
out/answers_meta.jsonl                               absent
out/runs/                                            empty
```

`out/` is gitignored — deliberately and correctly, it holds regenerable
artefacts and ~15 MB of them — so `git log -- out/` is empty and there is
nothing to recover. The run happened. Its result is unreconstructible.

**This is the second time.** The BGE-M3 comparison lost its answers the same
way when a log path was reused: §4a-2 records "the answer text is lost but
the emitted citations were printed", and that run was then scored from what
had scrolled past in a terminal. Twice now, a number that a later decision
leaned on existed only in prose.

### What survives, and why it does not match

`out/eval_report.json` (19 Sep) is the only eval artefact on disk. Under the
metric's own rule it gives **7/11 = 63.6%**, not 12/12. It is not the same
run re-scored:

| | eval_report.json | journal's k=5 column |
|---|---|---|
| fact recall | 8/15 | 8/15 |
| violations | 0/17 | 0/17 |
| **citation** | **7/11 = 63.6%** | **12/12 = 100%** |
| citation unachievable (2b) | 6 | 5 |
| language match | 17/17 | 15/17 |

Recall and violations agree exactly; citation, 2b and language do not. So it
is a different scoring pass over the same 17 questions — close enough to be
mistaken for the same run, different enough that it cannot stand in for it.

### The rule

**Anything a report will cite has to be written where it survives.** Either
the number goes into a tracked file, or the report names the file that holds
it and that file still exists when someone checks. A figure quoted in prose
with no path beside it is a memory, and memories are what this journal exists
to replace.

`out/` being gitignored is not the defect and should not change; the defect
is quoting from it as though it were permanent. Two cheap habits follow:

* **Name the file in the sentence.** "citation 12/12" is unciteable;
  "citation 12/12, `out/eval_report.json`" is checkable and would have
  failed loudly the first time someone looked.
* **A number that will be quoted gets copied out of `out/`.** The eval
  report behind a headline figure belongs beside the claim, not in the
  directory whose whole purpose is to be disposable.

### The same shape, one layer up

§4h recorded a predicate restated in two places and drifting. This is the
same failure in the other direction: a result **stated once and stored
nowhere**. Both are the single-source rule — one definition of every shared
thing — and both cost the same way, by making a later reader confident in a
number nobody can check.

It cost again while this was being written: `app.py`'s stat card restated
metric 2's aggregation instead of importing it, filtered on
`citation_achievable` alone, and rendered **60%** where the metric's own
rule gives **63.6%** — because a pass is proof of achievability and the
restatement dropped demonstrated successes. The card now imports
`gold_eval.citation_accuracy`, which is the only place that rule is written.
---

## 4l. Switched to a hosted embedder on an incomplete comparison

**Decided 2026-10-18. The comparison was NOT completed, and the switch was
made anyway. The reasoning is here so it can be overturned.**

### What was measured, and what was not

`out/index_gemini` was built with `gemini-embedding-001` at 768 dimensions,
867 chunks, 191 requests, no 429s. It was then scored on the 23 answerable
recall-scoreable gold questions plus 2 Banglish variants.

**LaBSE could not be scored at all.** Loading it died with
`OSError 1455, the paging file is too small` -- the ceiling recorded in 4f --
in a fresh process and again with `low_cpu_mem_usage=True`. The machine was
at 12.4G committed against a 15.5G limit with 1.4G physical free. So there is
no same-day, same-corpus LaBSE number to compare against.

The only LaBSE figures that exist are from `out/benchmark/`, **2026-09-19,
n=16, on the PRE-R1 corpus**:

| | dense@5 | hybrid@1 | hybrid@3 | hybrid@5 |
|---|---|---|---|---|
| LaBSE, 2026-09-19, n=16, pre-R1 | **0.062** | 0.312 | 0.500 | 0.562 |
| Gemini, 2026-10-18, n=23/25, current corpus | **0.800** | 0.478 | 0.652 | 0.696 |

Different question set, different corpus revision, different n. Under 4b's
own rule -- never read an artefact without checking when it was produced --
these two rows are not a comparison and are not presented as one.

### Why we switched regardless

Three reasons, in order of weight:

1. **LaBSE does not fit.** Staying on it does not mean keeping the old
   behaviour; it means the app does not run on this machine. A measured
   comparison between a model that works and a model that cannot load is not
   available at any n.
2. **The gap is too large for the caveats to explain.** Dense recall@5 of
   0.062 against 0.800 is not a corpus-revision artefact or an n=16/n=23
   artefact. 0.062 means the dense side found the gold chunk in one question
   out of sixteen; the journal's own 4a is titled "LaBSE's dense side barely
   works".
3. **The score inversion is gone.** On LaBSE the misses scored HIGHER than
   the hits (median 0.5698 against 0.5567), which is why the floor could
   only ever guard the degenerate case. On Gemini the hits lead: median
   0.8031 against 0.7285. The distributions still overlap -- highest miss
   0.9062 beats lowest hit 0.7002 -- so the floor is still not a confidence
   signal, but it is no longer pointing the wrong way.

**What would overturn this:** LaBSE scored on the current corpus with the
same 23 questions, on a machine with ~2.5G free. `BFA_INDEX_DIR=out/index`
switches back without a rebuild.

### The weights were tuned for a weakness that is gone

LaBSE's weak dense side is why the fusion was 0.8/0.2 on topic queries and
0.4/0.6 on figure queries: BM25 had to carry the retrieval. Swept on the
Gemini index over the same 25 questions, dense share 1.0 / 0.8 / 0.6 for
topic and figure independently:

| topic / figure | bengali @1/@3/@5 | banglish @5 | all @5 |
|---|---|---|---|
| **1.0 / 1.0** | **12/17/19** | **2/2** | **21** |
| 1.0 / 0.8 | 14/17/18 | 2/2 | 20 |
| 1.0 / 0.6 | 14/17/18 | 2/2 | 20 |
| 0.8 / 0.8 | 11/15/16 | 2/2 | 18 |
| 0.6 / 0.6 | 10/16/17 | 1/2 | 19 |

**Pure dense was adopted.** It is best at k=5 -- the k the app actually uses,
and the one that decides whether the right chunk reaches the prompt -- best
on the five figure queries (3 hits against 2 at every weighted setting), and
the simplest cell in the grid. At n=23 that last point is the tie-break:
1.0/0.8 is two questions better at @1 and one worse at @5, and neither
difference is evidence.

**The Banglish worry did not materialise.** BM25 is where the vocabulary fix
from 4g lives, so pure dense could have lost it. It held 2/2 at every setting
with topic weight >= 0.8 and only dropped at 0.6. n=2 is too small to call
this settled, and it is the first thing to re-check when more Banglish
variants exist.

**The weights now live in the index, not in the module.** `model.json`
carries `w_dense_topic` / `w_dense_figure`, and `rank_hybrid` takes them as
an argument. A single global pair would have meant that switching
`BFA_INDEX_DIR` back to LaBSE silently applied pure-dense weights to the
model whose dense side scores 0.062 -- nothing would raise, and retrieval
would simply get much worse. Indexes built before this carry no weights and
fall back to the module constants, so their behaviour is unchanged.

### The floor is NOT re-derived, and that is a loose end

At the old weights the Gemini floor came out at 0.7002. That number is not
used, because the weights changed in the same session: at 1.0/1.0 the fused
score IS the cosine, and 0.7002 was measured on a different quantity.
Gemini cosines for related Bengali text sit in the same 0.7-0.9 band, so
applying it unchanged would abstain on ordinary answers.

`app.INDEX_FLOOR` therefore sets **0.0** for this index, which preserves
exactly what the LaBSE floor actually delivered -- the journal records that
at 0.4044 it "fires on nothing in this sample" -- while `not chunks` still
catches the degenerate case. It could not be re-derived on the day because
the embedding quota was spent building the index. Re-run
`derive_floor.py out/index_gemini` when it resets.

### Two things the hosted path got wrong first

**The 768-dimension output is not unit length.** It arrives at ~0.589; only
the full 3072 is pre-normalised. `IndexFlatIP` is cosine ONLY for unit
vectors, and the floor is a number on that scale. Unnormalised, nothing
raises -- every score is simply rescaled and the floor moves out from under
itself. Canaried both ways.

**The retry ladder was a batch decision applied to an interactive path.**
Six attempts on a 1.8x ladder from 20s is 7.5 minutes, which is right for a
build and absurd in front of a person asking a question -- and a DAILY quota
will not recover inside any ladder, so every one of those waits is spent on
an outcome already decided. Found by a probe that outran its own timeout and
printed nothing. Interactive callers now get 2 attempts; `build()` opts into
6.

---

## 4k. A clause number is not a topic identifier

The product registry arrived with four rules to enforce (see `registry.py`),
and two of them key on the clause of a retrieved chunk: rule 2 rewrites a
rule the field team has changed, rule 4 withholds a figure they have
withdrawn. Clause looked like the natural key -- it is what the manual
numbers, what the manifest records, and what a citation names.

**It is not a key, because one clause can carry more than one topic.**

### The case that found it

`ডিপিএস এর বয়সসীমা কত?` -- what is the DPS age limit. §5.2.3 was retrieved,
and §5.2.3 appears twice in the registry:

| row | topic | verdict |
|---|---|---|
| `not_available` item 7 | insurance on more than one DPS | withdrawn |
| `rule_updates` rule 23 | the DPS age limit, ১৮-৬২ / ১৮-৬০ | changed to "এখন যেকোনো বয়সে" |

Rule 4 outranks rule 2, so the clause match fired first and the answer was
**"বর্তমান নিয়মের তথ্য পাওয়া যায়নি"** -- the current rule is not known.
That passed the test it was written against ("must not say 18-62 as
current") and was still wrong: the registry holds the answer, and the system
refused to give it. A refusal that looks like caution is the most expensive
kind of wrong answer here, because nothing about it reads as a defect.

### Why it is the same shape as §5.4

`corpus_gaps.md` §3.1 records that **§5.4 is ফায়ার সেফটি ইন্স্যুরেন্স in
Progoti and গবাদিপ্রাণি সুরক্ষা বিমা in Dabi** -- one number, two different
insurance products, different underwriters and premiums. That was filed as a
citation hazard: cite manual + clause together or the reference is ambiguous.

This is the same defect one level in. There the clause is ambiguous ACROSS
manuals; here it is ambiguous WITHIN one. Both say the same thing:

> A clause number locates a passage. It does not identify what the passage is
> about, and nothing enforces that one clause holds one topic -- the manuals
> were not written to be indexed.

### What routes it instead

The topic, taken from the registry's own Bengali text. Each row already
carries a statement of its subject -- `rule_summary` and `current_rule_bn`
for a rule update, `bot_says_bn` for a withheld one -- so the row whose words
the question actually shares is the row the question is about. Measured on
the case above: rule 23 shares ডিপিএস and বয়সসীমা with the question, item 7
shares nothing, and the answer becomes **"এখন যেকোনো বয়সে।"** with §5.2.3
cited as outdated.

Two guards came out of it, both in `registry.apply_rules`:

- **Withholding wins a tie.** If no row matches the question better than any
  other, the withheld row keeps the clause. Silence is the safe direction
  when the system cannot tell which fact is being asked for.
- **Best fit, not any fit.** Both §5.2.3 rules mention ডিপিএস, so "keep every
  row that overlaps" kept both and answered a question about the age limit
  with the one-policy rule stapled on. Only the best-scoring row survives; a
  genuine tie keeps both, which is honest when the question does not separate
  them.

### The general form

This is the cheap-property failure from 5.3 instance 17 again, in a different
place. The guard reads the property that is easy to compare -- a clause
string -- and passes while the property that matters, what the question is
about, disagrees. Clause equality is necessary and not sufficient, and the
sufficient test has to touch the content.

---

## 4m. Restyled to the brand book, without the brand book

`app.py` was restyled to BRAND.md and given an English/বাংলা switch and a
light/dark switch. Answer behaviour is unchanged: the same retrieval, the
same guard, the same prompt, the same post-filter, and answers still come
back in the language of the question with roles, products and clause
numbers in Bengali. What changed is the chrome, the strings and the colours.

### The source document was not here

**`docs/BRAND.md` does not exist on this machine.** It is not in the
project, and a search of E: and the user profile for the brand book, a BRAC
font pack, Kohinoor or Helvetica Neue files, or an export of
`Brand motif.indd` found nothing. Every brand value in this restyle
therefore comes from the restyle brief's quotation of BRAND.md, not from
BRAND.md. Where the brief quoted a code, that code is used. Where the brief
only pointed at a section (2.1 logo files, 3.2 status meanings, 5 motif,
10.2 spelling), nothing could be checked against it. **Re-read this entry
against BRAND.md when it is to hand.**

The brief also said to replace `#B5006A` everywhere. It occurs nowhere in
the code, so there was nothing to replace.

### What is now where

- **`ui_strings.py`** holds every interface string, en and bn, in one
  table. `t(key, lang)` raises on a missing key rather than falling back to
  English, because a silent fallback is how one English sentence ends up on a
  Bengali page. Programme names are filled from `productmatch.PROGRAMME_NAMES`
  (Convention 2). Ints and floats come out as Bengali numerals in bn;
  identifiers that merely contain digits (model name, chunk id) do not.
- **`app.py` `PALETTE`** holds the official codes only. **`THEMES`** maps
  roles (`--ink`, `--chip-bg`, …) to codes per theme. The stylesheet names
  roles, never hexes, so a switch changes one `:root` block.
- **`.streamlit/config.toml`** still pins `base = "light"`, now with
  official fills. Dark mode is **not** done there: theme options are server
  config, shared by every session, so one viewer flipping it would flip
  everyone.

### Colour, as specified

| Role | Light | Dark |
|---|---|---|
| ground | `#FFFFFF` | `#27281C` |
| body text | `#27281C` | `#F3F3F4` |
| supporting text | `#4D4F53` | `#D9D9DC` |
| card / border | `#F3F3F4` / `#D9D9DC` | `#34352A`* / `#4D4F53` |
| magenta, large and non-text | `#EC008C` | `#EC008C` |
| magenta, small text | `#B8006B` | `#F6B3D6` |
| citation chip fill / border / text | `#FDE6F2` / `#F6B3D6` / `#B8006B` | `#521F35`* / `#EC008C` / `#F6B3D6` |
| status active | `#E4F2EE` / `#00594C` | `#00594C` / `#E4F2EE` |
| status suspended | `#F8E6E6` / `#A41E22` | `#A41E22` / `#F8E6E6` |
| status updated rule | `#FDE6F2` / `#B8006B` | `#B8006B` / `#FDE6F2` |
| input fill / border | `#FFFFFF` / `#4D4F53` | `#414237`* / `#D9D9DC` |
| primary button fill / label | `#B8006B` / `#FFFFFF` | `#F6B3D6` / `#27281C` |

\* **Derived, not official.** Each is a stated blend of two official codes:
`#34352A` = `#27281C` + 6% `#FFFFFF`; `#414237` = `#27281C` + 12% `#FFFFFF`;
`#521F35` = `#27281C` + 22% `#EC008C` (`_mix()` in app.py).

**THE WHOLE DARK PALETTE NEEDS BRAND APPROVAL.** BRAND.md has no dark
palette. Everything in the dark column is an official code or a stated
blend of two, and in dark mode the status pairs are inverted (the deep
colour becomes the fill, its tint becomes the text) so that no new hue is
introduced. Until the brand team signs it off, it is a reading aid.

Removed because they are not in the table: the `#2b2f38` top bar, the
`#f7f7f7` band, the `#e6e6e6` rule, the `#6b7078` grey, and the quiz's
`#1f7a4d` / `#8a5157`. The quiz's right/wrong colours are now the teal and
risk status pairs. Yellow `#FBD00F` is not used.

### Contrast, measured (WCAG 2.x)

| Pair | Ratio | |
|---|---|---|
| white on `#EC008C` | **4.25** | large text only |
| `#EC008C` on white / on `#F3F3F4` | 4.25 / 3.83 | large only: page title 28px bold, stat figure 30px bold |
| `#B8006B` on white / on chip `#FDE6F2` | 6.46 / 5.47 | small text |
| `#4D4F53` on `#F3F3F4` | 7.40 | |
| active / suspended | 7.20 / 6.26 | both themes, inverted pairs equal |
| dark: `#F3F3F4` on `#27281C` | 13.46 | |
| dark: `#F6B3D6` on ground / card / chip | 8.77 / 7.31 / 7.68 | |
| dark: `#EC008C` on ground / on card | **3.52 / 2.93** | ground: large only. Card: fails even as large text, hence `#F6B3D6` for the dark stat figure |
| white on `#B8006B` (light button) | 6.46 | |
| `#27281C` on `#F6B3D6` (dark button) | 8.77 | |

### Streamlit's widgets, measured in all four states

Playwright on Chromium, with the browser told the OS prefers **dark** for every
run, which is the condition that produced black inputs in a white page last
time. Light-mode results are the regression check; dark-mode results are the
feature. Values are computed styles, identical in en and bn:

| Widget | Light | Dark |
|---|---|---|
| page `stApp` | `#FFFFFF` | `#27281C` |
| text input root: fill / border | `#FFFFFF` / `#4D4F53` | `#414237` / `#D9D9DC` |
| text input: text | `#27281C` | `#F3F3F4` |
| select: fill / border / text | `#FFFFFF` / `#4D4F53` / `#27281C` | `#414237` / `#D9D9DC` / `#F3F3F4` |
| select menu (open): fill / option text | `#F3F3F4` / `#27281C` | `#34352A` / `#F3F3F4` |
| radio label / ring off / ring on | `#27281C` / `#4D4F53` / `#EC008C` | `#F3F3F4` / `#D9D9DC` / `#EC008C` |
| submit button: fill / label | `#B8006B` / `#FFFFFF` | `#F6B3D6` / `#27281C` |
| tab selected / other / underline | `#B8006B` / `#4D4F53` / `#EC008C` | `#F6B3D6` / `#D9D9DC` / `#EC008C` |
| switch, selected / unselected | `#B8006B`+`#FFFFFF` / `#FFFFFF`+`#27281C` | `#F6B3D6`+`#27281C` / `#27281C`+`#F3F3F4` |
| expander summary | `#27281C` on `#FFFFFF` | `#F3F3F4` on `#27281C` |
| footer bar / name | `#EC008C` / `#FFFFFF` 21px 600 left | same |

**THE FIRST MEASUREMENT FOUND EVERY WIDGET RULE DEAD.** Streamlit 1.64
renders its widgets with react-aria, not BaseWeb. All the `data-baseweb`
selectors (input, select, tab highlight) matched nothing, and the tab rule
targeted `button[role=tab]`, which is now `div[data-testid=stTab]`. The
first pass had the selected tab label at `#EC008C` on 14px text (4.25:1),
which is exactly what the tint exists to prevent. This is the same failure
as 4e, *a rule that exists is not a rule that applies*, and once again only
the computed style could tell. The CSS now uses only `data-testid`, `role`
and `aria-*`, never the emotion class hashes. **After any Streamlit upgrade,
re-run this measurement before trusting the dark theme.**

The radio needed one more fix. Streamlit draws the unselected ring at 20% of
`textColor` (near-black), which disappears on the dark ground. It is now
`--muted`, and the selected ring is magenta, picked out with
`:has(input:checked)`.

### The switches

Each switch is a segmented control with `required=True`. Without it, a
second click deselects and leaves the page with no language. Each is seeded
with `setdefault` and remembered in `session_state` with
`persist_state="session"`.

**URL binding was tried and rejected.** `bind="query-params"` writes the
option's *displayed label* to the URL, and the theme's label is translated.
After a language switch, `?theme=Dark` no longer names an option, and
Streamlit silently strips it. A reload is a new Streamlit session anyway,
so session state is exactly "for the session".

Clicked through as a reader would: en→bn→dark→quiz tab→en→light. The
answer stayed on screen throughout with no re-ask (the `_asked` guard
covers switch reruns) and the input followed the theme every time.

**KNOWN LIMIT: a language switch reopens the first tab.** The switch
renames all three tabs. With `on_change="ignore"` the browser never tells
the server which tab is open (`session_state["tabs"]` read `None` after a
click on the quiz tab), so nothing can carry the selection across the
rename. A theme switch keeps the tab. Fixing this needs `on_change="rerun"`,
a rerun on every tab click, which is the cost the tabs were chosen to avoid.
Not changed in a restyle.

**Also:** any rerun clears a submitted quiz's score display, as it always
has (the score shows only on the submitting run). A switch is a rerun, so
switching after submitting hides the score. The selected answers stay, and
pressing submit again shows it.

### Deviations and things not matched

1. **Kohinoor Bangla is not self-hosted.** There is no font pack and no web
   licence on this machine. The stack names Kohinoor first, so it is used
   where installed (macOS, iOS), then **Noto Sans Bengali from Google Fonts:
   a deviation.** Helvetica Neue is in the same position: named first, with
   Arial, the brand's own fallback, everywhere else (which includes all of
   Windows). The chrome stack puts the Bengali faces after Arial, so the
   browser falls through per character: Latin gets Helvetica Neue or Arial,
   and Bengali gets Kohinoor or Noto, inside one string.
2. **Logo files are not confirmed as BRAND.md 2.1 exports.** Measured:
   `brac-logo-white.png` is a pixel-exact alpha conversion of
   `brac-logo.png`, so it was derived here, not delivered. Swap both for the
   official PNGs.
3. **No motif.** The hand-drawn running-stitch divider is gone (BRAND.md 5:
   never redraw the stitch). No official export of `Brand motif.indd` exists
   in `assets/`, so its place is 40px of white space.
   `assets/brac-divider.svg` is left on disk, unreferenced. **The official
   motif file is needed.**
4. **Footer: programme name only, no logo.** The brief asks both for "only
   মাইক্রোফাইন্যান্স কর্মসূচি" on the bar and for the white logo "on the
   magenta footer". These conflict, and the more specific footer instruction
   was followed. One line restores the logo if that reading is wrong. The
   name is Bengali in both interface languages, because it is the brand
   descriptor, not a sentence to translate.
5. **Footer weight is 700, not the specified semibold.** White on `#EC008C`
   is 4.25:1, a pass only as WCAG *large* text, and at 21px that requires
   bold. 600 was borderline, so it was raised to 700 in the follow-up
   (measured: 700 / 21px / `#FFFFFF`).
6. **Primary buttons are not plain magenta.** A white 14px label on
   `#EC008C` is 4.25:1. Light buttons use the approved text tint `#B8006B`
   as their fill; dark buttons use `#F6B3D6` with near-black text. Using
   the text tint as a fill is a judgement call, not something the brief
   specifies.
7. **Status vocabulary is the registry's own** (follow-up, below):
   `active` (teal), `suspended` (risk), `pilot` (neutral, because the brief
   gives the magenta highlight to an *updated rule*, which lives in a
   different table). No product is in pilot today, so nothing on the page
   uses the "updated rule" colour yet.
8. **Bengali copy not checked against 10.2.** ~~The new Bengali strings aim
   at Prothom Alo style (e.g. সঙ্গে).~~ **CORRECTED (4p): that was wrong.**
   BRAND.md 10.2's confirmed word list says use সাথে, not সঙ্গে; every
   সঙ্গে, including একসঙ্গে, is now সাথে / একসাথে. "লাইট" / "ডার্ক" for the
   theme switch still need a Bengali reviewer.
9. **Upstream error text stays English.** Provider and budget refusals
   arrive from `generate.py` as English sentences. The lead-in is
   translated, and in bn a note says the message below is shown as
   received.
10. **Logo exclusion zone is the file's own.** The PNG holds the mark about
    25% in from each edge. BRAND.md's exact clear-space rule could not be
    read. The logo sits alone in its row, top-left, and the switches are in
    a strip above it, not beside it.

### Follow-up: the registry, and the two bugs found in passing

**The status panel read the wrong file.** It read `products_catalogue.csv`,
whose status column holds 0 of 43, so every product showed *unknown*. Meanwhile
`generate.py`, which applies the registry's rules, was leading the answer with
the registry's status sentence. The page and the answer disagreed about the
same product. The panel now reads `data/registry/products.csv` (30 products:
26 active, 4 suspended) through `registry.py`, and suspended products are
listed first. A product `productmatch` knows but the registry does not list
gets an explicit *not in the registry* row rather than silence.

**The "unverified" qualifier is gone.** It existed because the catalogue
recorded nobody who had checked a status. The registry is the field team's
curated sheet, so it is the check. The footer note had said status was
unconfirmed "until the status sheet comes back". The sheet is back, so that
sentence was replaced with: *Product status comes from the field team's
registry, not from the manuals.*

**The embedder-down card said "out of memory". FIXED.** `ask()` now records
which failure happened (`infra: "embedder"` or `"memory"`), and the card and
the empty chunk list each branch on it. A spent quota now reads "The service
cannot be used at the moment. Please try again in a little while."

**১৮ মাস মেয়াদি লোন was not matched. FIXED, and the cause was two defects,
not one.** `retrieve.products_named` matched *raw* text. Measured:

| question text | raw | canon'd |
|---|---|---|
| Bengali digits `১৮`, either য় form | miss | hit |
| ASCII `18`, precomposed য় U+09DF | **miss** | hit |
| ASCII `18`, decomposed য় U+09AF U+09BC | hit | hit |

The nukta was half of it. The other half was the digits: the pattern carries
ASCII `18`, and only `canon()` folds `১৮` to it. Convention 4's remedy, canon
the text where you can, fixes both. With it, 0 of the NAMED products miss
their own display name (1 did before). `registry.products_named` already
canon'd, which is why the registry matched this product and the catalogue
path did not. `pattern_audit.py`: clean 54 of 54. The chunk-side caller only
feeds metadata (`products`, `product_status`), which nothing in generation
reads, so no answer can change.

### Follow-up: the synthetic set runs without the registry

**Why.** The synthetic set tests one thing: whether the model stays faithful
to the manual it was given. That manual is invented on purpose. নীলকমল ঋণ,
রূপসা ইন্স্যুরেন্স, `synthetic_a` and `synthetic_b` exist nowhere else, and
canary 3 in `generate.py` checks that no fixture names a real product. The
registry describes the real products. Applied to the synthetic set, it does
exactly what it should do with a real question: no manual has a product
called নীলকমল ঋণ, so the registry refuses the name. S-001, S-004 and S-005
all expect an answer, so all three score as misses, and none of those misses
says anything about the model. With the registry on, the synthetic score
measures what the registry knows about real products against a product that
is made up. That is the wrong thing to measure.

**What changed.**

- `generate.py --no-registry` answers without the registry. It is off by
  default: the registry is on unless a caller turns it off explicitly.
  `answer_one`'s own default, `registry_rules=True`, is unchanged.
- Only a synthetic dump may pass it. The test is the corpus guard's: every
  chunk says `synthetic`, decided by the file and not by its name. On any
  other dump the run stops before `configure()`, so a refused run calls
  nothing.
- Every meta row now records `registry`: `off` for this run and `on` for
  every other. The run log and the header line say the same. A synthetic
  result can no longer be read as a production one.
- The synthetic command in the `generate.py` docstring passes the argument.
  No other caller does.
- The registry itself is unchanged.

**What it does not do.** A synthetic score with the registry off says nothing
about the registry. The registry is tested by its own scenarios
(`registry.py --check`), by `tests/test_registry_pipeline.py`, and against
the real questions.

**Checks.** `tests/test_registry_synthetic.py`: four tests, two canaries, no
model call.

- A run without the argument, on S-001, still gets the registry's refusal,
  through `main()` and through `answer_one()`. That proves the default
  stayed on.
- With the argument, every row says `registry=off` and no rule fires.
- On a dump that is not synthetic, the argument is refused.
- The canaries flip each default to off, and the refusal turns into the
  model's answer.
- Five disarms were each tried in turn: the argument's default, the synthetic
  check, the meta field, the argument being ignored, and `answer_one`'s
  default. Each fails its own test and no other.

---

## 4n. A table flattened into prose, and six confidently wrong figures

**The symptom.** Six FAQ answers carried a figure that belonged to a
neighbouring product. প্রত্যাশা লোন's service charge was given as ২৪% when the
manual says **১৮%** — ২৪% is রি-ফাইন্যান্স's, the next column. রি-সিডিউল's term
was given as প্রত্যাশা's. Four loan ceilings were swapped around §1.1.2. Every
one of them is a plausible number, correctly formatted, with a correct
citation. Nothing in the pipeline could tell them from right answers.

**The cause is one line.** `serial()` — defined identically in `ex.py` and
`resplit.py` — serialised a Word table by zipping **`rows[0]`** as the header
and prefixing every cell with it:

```python
h, out = list(rs[0]), ([nm(cap)] if cap else [])
for r in rs[1:]:
    p = [f"{nm(a)}: {nm(b)}" if nm(a) else nm(b) for a, b in zip(h, r) if nm(b)]
```

That is correct for a table whose first row labels each column. It is wrong
for one whose first row is a **merged banner**, which is how every feature
table in both manuals is laid out: row 0 spans the width with a title, row 1
carries the product names. The result labelled every cell with the banner:

```
লোনের পরিমাণ — প্রগতি লোন প্রোডাক্টসমূহের বৈশিষ্ট্য: নতুন লোনের ক্ষেত্রে-
১ লক্ষ থেকে ২০ লক্ষ টাকা — নতুন ভিসা- ১ লক্ষ - ৪ লক্ষ টাকা — ...
```

Five products' ceilings in a row, separated by dashes, with the product names
four lines above. Answering "what is এগ্রিবিজনেস's ceiling" means counting
dashes and aligning them to a header elsewhere in the text. The model got the
alignment wrong in both directions.

**It was never only a FAQ problem.** Running the retrieval step alone — no
model — on the six questions, in the live wording a user would actually type,
put the flattened chunk in the top 5 of **6 of 6** searches, at rank 1 or 2 in
four of them, every one above the abstention floor. The Ask-anything tab was
reading the same text.

### What the survey found, and what reading it changed

A first pass flagged 58 chunks by asking which tables name two or more
products, programmes or insurers across one row. Reading them cut it to 29.
§7.1 প্রাধিকারসূচি and §2.5.4 কমপ্লেইন্ট were **false positives**: their row 0
is a genuine header, so `serial()` already labelled every cell correctly, and
the flag came from a body row that happened to mention programme names. The
rest fell into three classes:

| | what is lost | chunks |
|---|---|---|
| **A** entity columns — §1.1.1, §1.1.2, §1.1.3, §2.3.1 | which product a figure belongs to | 17 |
| **B** two-level headers — §1.2.2, §5.1.3, §5.2.4, §11.1 | which sub-column: একক vs দ্বৈত premium | 32 |
| **C** change-log tables | sub-labels of a change record | 12 |

§2.5.4 has **exactly** B's shape — I compared the raw rows — so no structural
test can separate them. It is left out deliberately, not because it differs,
but because this change is being validated by comparing two indexes question
by question, and moving chunks nobody is asking about makes that comparison
harder to read. See 4k: the cheap property was the clause, and the clause is
not the shape.

### The fix, and the second defect it introduced on the way

`tablerender.py` now owns one renderer for both callers. It finds the header
row instead of assuming row 0, joins two-level row labels (`সার্ভিস চার্জ` plus
its slab `< ৫,০০,০০০`), and renders **column-major**:

```
প্রত্যাশা লোন — লোনের পরিমাণ: ...; লোনের মেয়াদ: ৪, ৬, ১২, ১৮, ২৪ ও ৩৬ মাস;
সার্ভিস চার্জ (ক্রমহ্রাসমান): ১৮% ।
```

Two things had to be got right that were not obvious.

**Scope by clause alone promoted a loan amount to a column name.** §1.1.2 also
holds গতি's and উন্মেষ's single-product tables: one banner across three
columns, then `ঋণের পরিমাণ | | ১ লক্ষ - ৫ লক্ষ টাকা`. Treating row 1 as a header
made the ceiling into a column heading — a worse error than the one being
fixed, in a table that never had the problem, because one product's figures
were never ambiguous. The header test now also requires row 1 to name **two or
more** columns: a table needs two columns before columns can be confused.

**The splitter would have undone the renderer.** `resplit.py` cuts an oversize
table into parts by rows. On a column-major rendering that cuts each product's
figures apart again — the same defect, reintroduced one stage later. Parts are
now slices of **columns**, and each part carries the whole grid so a column
keeps its group label wherever the cut falls.

**And one of mine, caught by the gate.** `tablerender.nm` was written as an
approximation of `ex.py`'s normaliser and left out its space-collapse. One
trailing space went into `progoti_0062` — §2.1.2, nowhere near this change —
because `recover_caption()` peels a caption off a body by exact suffix match.
The scope check caught it as an out-of-scope edit. Convention 12 again: the
normaliser is now copied character for character, not approximated.

### What it was accepted against

Four conditions, all before any rebuild:

| | result |
|---|---|
| no chunk outside A and B changes | 0 out of scope; 21 changed, 16 new, 8 gone, all in the eight listed clauses |
| every figure under its own column, checked against the **.docx cell** | 237 figures across 37 chunks, 0 wrong |
| gold `kind: literal` facts survive verbatim | 30 facts, 0 lost; G-204's second literal moved to `progoti_0016_p3` and its ref was updated |
| dabi/progoti twins still collapse | 0 twins render differently; **the dup_group count moved**, 546→554 chunks in 272→276 groups |

The count condition failed **as written** and holds as intended. It cannot stay
fixed while the chunk count does not, and the chunk count had to move because
the table is now rendered whole before it is split. What the count stood in for
— that no pair stops collapsing, so the same text cannot fill two of the top
five slots — is checked directly: 0 chunks stopped collapsing, and every group
is still a clean pair. Reported both ways rather than quietly redefined.

**Measured afterwards, on 12 searches across both indexes:** the figure is
attributed to its own product in **0 of 12** on the old index and **12 of 12**
on the new one. Merely present somewhere in the top 5: 10 of 12 → 12 of 12.
`quiz/bank_validated.yaml`, the human-reviewed bank, needs **0 of 52**
re-reviewed; the two draft banks need 9 each, and every one of those answers is
still in the same table, in a different part.

## 4o. A cache that was asked but never written

The rebuild was expected to cost 867 embedding items against a quota of 1000 a
day. It cost **53**, and the gap is two separate bugs in the same cache.

**`build()` called `cache_load` and never `cache_save`.** Only `benchmark_one`
saved. So `out/benchmark/` held a document matrix exactly when the benchmark
happened to have run — and it had not, so every index build since had
re-encoded all 867 chunks against a cache that was never there. The symptom was
a slow build, which is the kind of symptom nobody reports.

**And reuse was all-or-nothing.** `cache_diff` already computed *which* chunks
had moved — correctly, per chunk — and then `cache_load` threw the whole matrix
away and printed `re-encoding all 867 chunks`. The information needed to do
better was computed and discarded.

The fingerprint now carries a per-chunk `doc_sha256` of the **encoder input**,
not of `display_text`. That distinction is the safety of the whole thing:
`display_text` answers "did the chunk change", and a change to `index_text` —
the normaliser, the digit twin, a prefix — moves every vector while leaving
`display_text` untouched. A version-2 fingerprint has no such digest and is
therefore never partially reused: it cannot prove the normaliser did not move
underneath it.

Five conditions, against the real 867-chunk corpus with a counting stub in
place of the model, so testing a quota fix cost no quota:

| | encoded |
|---|---|
| cold, no cache | 867 |
| warm, nothing changed | 0 — and the matrix comes back byte-identical |
| **one chunk's text edited** | **1**, and the other 866 vectors unchanged |
| `index_text` changed, `display_text` not | 867 |
| version-2 fingerprint beside a good matrix | 867 |

The third is the one that was asked for; the fourth is the one that would have
made partial reuse unsafe if it had quietly reused.

**The vectors were not lost, either.** `out/index_gemini` is an `IndexFlatIP`,
which stores its vectors verbatim, and its `meta.jsonl` records every row's
`chunk_id` and `index_text` in index order. The cache was reconstructed from it
rather than re-bought — 867 unit vectors, all 867 encoder inputs identical to
today's, so nothing had drifted. The fingerprint was written from the **stored**
`index_text`, not today's: if an input had drifted, `cache_reuse` should notice
and re-encode, and writing today's digest would have asserted the one thing
that file was not entitled to assert.

---

## 4p. The BRAND.md audit, applied; and the exceptions, recorded

`docs/BRAND.md` arrived, and `CLAUDE.md` makes it the authority: where it
conflicts with anything else, it wins. Auditing the app against it found 16
differences. This pass applies the ones that were decided, and records the
ones kept as **exceptions**. An exception is a deliberate departure, with a
reason, that can be reviewed; an unrecorded difference is a mistake nobody
has noticed yet.

### Applied

- **Jargon behind a developer switch.** `BFA_DEV=1` turns it on; it is off
  by default. Officers no longer see `k=5`, "top fused", "meta row",
  "post-filter", chunk ids, scores, token counts, file paths, question ids,
  the corpus/evaluation stat cards, the token-budget banner or a raw
  provider error. Where an officer still needs the fact, it is said in
  plain words: the memory card says what to do and who to tell, the error
  card says to try again, and the clause list stays (the refusal text tells
  the reader to open it) but shows only manual and clause. With the switch
  on, every diagnostic is back as before. Nothing about how an answer is
  produced changes either way.
- **"Gold" is now "Sample questions" / "নমুনা প্রশ্ন"** in both languages.
- **সঙ্গে → সাথে** throughout `ui_strings.py`, including একসঙ্গে → একসাথে in
  the photo alt text (BRAND.md 10.2's confirmed word list). This reverses
  4m item 8, which wrongly gave সঙ্গে as the Prothom Alo choice.
- **`assets/brac-divider.svg` deleted.** It was a redrawn motif (BRAND.md 5).
- **Footer bar 52px** (BRAND.md 11).
- **Section labels bold 700** (ANSWER, NOT STARTED, the quiz's QUESTION n and
  YOUR SCORE), still `#B8006B` in light mode.
- **The empty photo slot on Test yourself is removed.** Ten questions on one
  page is copy-heavy, and BRAND.md 6 says to leave images out of such pages.

### Recorded exceptions

| # | BRAND.md says | The app does | Why |
|---|---|---|---|
| 1 | Footer: Semibold (4.1) | **Bold 700** | Contrast. White on `#EC008C` is 4.25:1, a pass only as WCAG large text, and at 21px that needs bold. On a phone screen outdoors, semibold is the weight that fails. |
| 2 | The main call-to-action is magenta (3.2) | **`#B8006B`** button fill (light) | Contrast. A white 14px label on `#EC008C` is 4.25:1, under the 4.5:1 that small text needs; on `#B8006B` it is 6.46:1. `#B8006B` is BRAND.md's own approved text tint (3.3). |
| 3 | Body line height 1.4 (4.1) | **1.6–1.8 for Bengali** | Bengali vowel signs and conjuncts sit above and below the line and need room on screen; at 1.4 they crowd the next line. This covers Bengali text only. The English chrome's 1.5–1.7 is *not* covered by this exception and remains an open difference. |
| 4 | One accent colour per section (3.2) | **Teal and brick red in one status card** | 3.2 also gives the meanings: teal marks the positive, brick red the risk. A product that is active and one that is suspended, side by side, need both. |
| 5 | Photos show real emotion (7) | **The river photo shows a woman from behind** | Consent. With no face visible, nobody in it is identifiable, so it can be published while signed consent is still outstanding for the photos that do show faces. Like the other photo, it is also below the 4000 × 3000 px source size, because no larger original was supplied. |
| 6 | Kohinoor Bangla for all Bengali (4) | **Noto Sans Bengali**, with Kohinoor named first | No Kohinoor web licence yet, so there is nothing to self-host. Where Kohinoor is installed (macOS, iOS) it is used; everywhere else, Noto. Revisit when the licence arrives. |
| 7 | Only official hex codes (3.1, 14) | **A dark theme** built from official codes plus three stated blends (`#34352A`, `#414237`, `#521F35`), with the tints `#F6B3D6` / `#00594C` / `#B8006B` used as text and fills | BRAND.md has no dark palette. **Awaiting brand approval**; until then it is a reading aid, not a brand-approved theme. |

### Still open (not decided in this pass)

From the audit, not changed here: the logo is not the official 1181 × 392 px
file and its clear space looks breached by the title; signed consent for the
textile workers is not on file; Helvetica Neue 45 Light / 65 Medium weights
are not used; the English chrome's line height (see exception 3).

---

## 4q. A caption in the wrong chunk — the third table defect

The third way a table has lost its meaning on the way into the corpus, after
**flattened columns** (4n: a banner row labelling every cell) and the
**merged header row** (§2.5.4's two-level header, whose sub-labels labelled
nothing). This one loses the table's *subject*: which programme or product
the figures are for.

**Found by the quiz chat.** `dabi_2025_0050` and its twin `progoti_0057` hold
the NCDP/SCDP approval table. Its caption, `শাখার ধরন অনুযায়ী ঋণ অনুমোদনের
সিলিং (এনসিডিপি ও এসসিডিপি)`, sat in `dabi_2025_0048_p2` — the parent
process table's text — **ahead of both** approval tables, next to the Dabi/BCUP
table's caption. Read alone, the chunk was a table of BM ceilings under a
heading naming দাবি, so the chatbot could give `১৫,০০০–৭০,০০০` as the Dabi
rule. Validated quiz question Q-003 was written that way: "গ্রুপ ভিত্তিক সেবা
অনুযায়ী ১৫,০০০ থেকে ৭০,০০০ টাকার নতুন ঋণ…".

**This corrects §5's instance #14 and `data/corpus_gaps.md` §3.4.** Both
called the two §2.1.1(চ) tables a corpus ambiguity that the programme team
should label. The manual had labelled them. The ambiguity was ours; the
corpus-gaps entry is withdrawn.

### How a caption gets lost

Word keeps a caption as an ordinary paragraph beside its table, and `ex.py`
emitted it wherever that paragraph fell:

- a **top-level** table's caption went into the prose chunk before it — a
  table was given no caption at all (`cap=""`);
- a **nested** table's caption went into its parent cell's text, which is
  rendered as part of the parent table; the nested table got only a context
  line built from the parent's row 0 and the cell's first 100 characters;
- a **floating** table is positioned against the paragraph *after* it. The
  NCDP/SCDP table is floating, and that anchor paragraph is its caption.

### The survey: 89 tables, every one read

Both `_unicode.docx` files, every `w:tbl` including nested ones (5) and
floating ones (6), each with the paragraphs either side of it, its chunk
matched by cell contents, and the chunk each neighbour actually landed in.

**Fixed — the caption is the only place the programme or product is named
(19 chunks):**

| clause | table chunks (Dabi / Progoti) | caption now first in the chunk |
|---|---|---|
| §1.2.2 | `0019` / `0026` | (ক) মাসিক জমা প্রকল্প/ DPS- · এই সঞ্চয় স্কীমে … মোট প্রাপ্য: |
| §1.2.2 | `0021` / `0028` | (ক) মাসিক জমা প্রকল্প/ DPS- · মেয়াদ পূর্তির পূর্বে … নিম্নরূপ: |
| §1.2.2 | `0023` / `0030` | খ) মাসিক মুনাফাভিত্তিক সঞ্চয় প্রকল্প: · মুনাফা বা লভ্যাংশ … নিম্নরূপ: |
| §1.2.2 | `0025` / `0032` (floating) | খ) মাসিক মুনাফাভিত্তিক সঞ্চয় প্রকল্প: · … মেয়াদান্তের পূর্বে আমানত উত্তোলন: |
| §1.2.2 | `0027` / `0034` | গ) দ্বিগুণ মুনাফাভিত্তিক সঞ্চয় প্রকল্প · মেয়াদ পূর্তির পূর্বে … নিম্নরূপ: |
| §1.2.2 | `0029` / `0036` | ঘ) দেড়গুণ মুনাফাভিত্তিক সঞ্চয় প্রকল্প: · মেয়াদ পূর্তির পূর্বে … নিম্নরূপ: |
| §1.2.2 | `0031` / `0038` | ঙ) মানি-প্লান্ট সঞ্চয় প্রকল্প: · মেয়াদান্তের পূর্বে আমানত উত্তোলন: |
| §2.1.1(চ), nested | `0049` / `0056` | শাখার ধরন অনুযায়ী ঋণ অনুমোদনের সিলিং (দাবি ও বিসিইউপি) |
| §2.1.1(চ), nested, floating | `0050` / `0057` | শাখার ধরন অনুযায়ী ঋণ অনুমোদনের সিলিং (এনসিডিপি ও এসসিডিপি) |
| §2.1.2(গ), nested | — / `progoti_0062` | প্রগতি কর্মসূচির ঋণ অনুমোদন সিলিং |

**§1.2.2 was the bigger case, and nobody had reported it.** Its five savings
schemes are printed as lettered **bold paragraphs**, not headings, so no
heading path carries them. The seven tables' own lead-ins ("…লভ্যাংশের হার
হবে নিম্নরূপ:") never name the scheme either. The early-withdrawal tables of
the দ্বিগুণ and দেড়গুণ schemes print the same first rows, and nothing in
either chunk said which scheme it was. So for these tables the caption
includes the lettered scheme title.

**The trap in it:** §1.2.2's floating table (`0025`/`0032`) is anchored to
the *next* scheme's title, `গ) দ্বিগুণ…`. A rule that read a floating
table's anchor as its caption would file the monthly-profit scheme's
withdrawal rates under the doubling scheme. The anchor is read only when
nothing is found above the table, and never when it is a lettered title.

**Found, and deliberately left — the caption repeats what the chunk already
says:** §1.1.1–§1.1.3's product feature tables (each loan is named in the
table's own banner row); §3.5 `মিটিংয়ের সময়সীমা`; §5.1.8 `বিমাদাবি সংক্রান্ত
টাইমলাইন` and `ঋণ নিরাপত্তা বিমার হটলাইন নম্বরসমুহ` (the heading line
already names ঋণ নিরাপত্তা বিমা); §7.1's lead-in (restates its heading).
Adding them would name nothing new and would void the human reviews of
every quiz question citing those chunks — §7.1 alone is 17 parts.

**Captions printed below an inline table: none.** Every title paragraph
below a table was the next section's title or the next table's lead-in.

### The fix

`ex.py`'s `own_caption()` finds a table's caption by position and
formatting, never by wording. It takes the nearest title paragraph above
the table (every run bold, or centred), with at most one ordinary paragraph
between. It adds the lettered sub-section title the table sits under. For a
floating table with nothing above, it takes the anchor paragraph, unless
that is a lettered title. Scope is a reviewed clause list, `CAPTION_CLAUSES =
{1.2.2, 2.1.1, 2.1.2}`, as `tablerender` scopes the column fix. The caption
is printed after the heading line. Everything else in the chunk stays
exactly where it was, including the original caption text in its old chunk
and the nested tables' old context line. Child tables inherit the old
context, so a caption cannot leak into a table nested inside the table.

### What it was accepted against

| | result |
|---|---|
| unchanged pipeline reproduces today's files | 11 of 11 byte-identical, run first, so the diff below is the change and nothing else |
| nothing outside the listed tables changes | 19 changed, 0 outside the list, 0 chunks added, removed or renumbered; livestock untouched |
| every figure still matches its Word cell | each changed chunk = old chunk + 1–2 caption lines, the rest byte-identical; `table_rows` equal the cells read fresh from the `.docx`; every row figure is in the cells. Canary: one digit changed is caught |
| twins still render identically | 276 groups, 554 chunks before and after; 0 stopped collapsing, 0 newly merged |
| `validate_gold` | `gold/bootstrap.yaml` consistent. `bootstrap_livestock_grow.yaml` (23 problems) and `synthetic.yaml` (`UnboundLocalError` in `canary_selftest`) fail identically on the **old** corpus — pre-existing, not this change |

Two programme tags moved as intended: `0050`/`0057` now carry `ncdp, scdp`.

**Rebuilt `out/index_gemini_v2`: 19 encoded, 854 reused.** The 854 vectors
are byte-identical to the previous index; the 19 re-encoded are exactly the
changed chunks. Cost: 19 embedding items.

**The live question**, through `app.ask` on the rebuilt index —
"দাবি কর্মসূচিতে শাখা ব্যবস্থাপক কত টাকা পর্যন্ত নতুন ঋণ অনুমোদন করেন?":

> দাবি কর্মসূচিতে শাখা ব্যবস্থাপক সর্বোচ্চ ৭০,০০০ টাকা পর্যন্ত নতুন ঋণ
> অনুমোদন করতে পারেন। — dabi_2025 §2.1.1

It no longer gives `১৫,০০০–৭০,০০০` as the Dabi rule. It is **incomplete**:
৭০,০০০ is the high-demand ceiling; in an ordinary-demand branch the BM's
new-loan band is ১৫,০০০–৬০,০০০, and the answer does not say so. Retrieval
returned `progoti_0062`, `dabi_2025_0049`, `dabi_2025_0050` as its top 3.

### What cites a changed chunk

- **Validated quiz bank:** Q-002 (`dabi_2025_0021`), Q-003 (`dabi_2025_0050`),
  Q-040 (`progoti_0062`). `quiz_recheck`'s fingerprint includes the cited
  chunk's manifest hash, so all three now read as changed since review.
  **Q-003's question is the defect itself**: it presents the NCDP/SCDP band
  as the group-service rule.
- **Draft banks:** Q-035 (`0021`), Q-056 (`0050`, the same wording as Q-003),
  Q-026 (`progoti_0038`), Q-061 (`progoti_0057`).
- **Gold:** G-206, G-207 (`progoti_0056`), G-208 (`progoti_0062`); still
  consistent.
- **FAQ** (cites clauses, and an FAQ approval covers only the FAQ's own
  text, so none is voided): **F-35 is wrong in scope.** It answers "গ্রুপ
  ঋণে" from the Dabi/BCUP table and says nothing about NCDP/SCDP's
  different one. Its 2026-10-25 source note — "§2.1.1 is গ্রুপভিত্তিক সেবা,
  so 'গ্রুপ ঋণে' stays" — read the clause heading and missed the two
  captions. F-36's table carries a note printed below it, `নিম্ন চাহিদার
  অঞ্চলের জন্য পূর্বের ঋণ অনুমোদন সিলিং অপরিবর্তিত থাকবে।`, which F-36 does not
  mention. F-18–F-22 and F-24 cite §1.2.2; their content is unaffected.

### Also found, not fixed here

**A single-row table is dropped from the corpus entirely.** `serial()` takes
row 0 as the header and renders rows 1 onward, so a one-row table renders as
nothing and no chunk is written. Dabi table 36 and its Progoti twin — the
eight data categories of the branch dashboard module (`১. ভিও পরিদর্শনের তথ্য …`)
— are in neither corpus. A fourth table defect, and a cheaper one to fix.

**A process slip.** `python conventions_check.py --help` ran the full check:
the script takes no arguments and ignores them. It was stopped within the
2-minute timeout. It only reads files, and `git status` showed nothing
written, but it is the run I was asked not to start while the quiz chat was
writing: its import-writes rule compares the timestamp of every file in the
repository around each import. Rules 1 and 2 were then run on `ex.py` alone,
from the parsed source: both clean, self-check passing.

---

## 5. The four conventions, and why they are checks rather than prose

All four exist because they were broken repeatedly — **fifteen times between
them** — by people who knew the rule. Full detail in
[CONVENTIONS.md](CONVENTIONS.md); the summary is:

### 5.1 `main()` behind `__main__`. Importing must never write files.

Broken three times with the same symptom: something imported a module for one
function and silently triggered its whole job.

The instructive case: `pattern_audit.py` imported `products.py` and `roles.py`
for their pattern tables, and **the registry CSVs were rewritten as a side
effect of auditing them**. The audit existed to *check* the registries and
quietly *regenerated* them instead — so it was validating its own fresh output
rather than what was on disk. Nothing failed. The numbers simply meant
something other than what they appeared to mean.

### 5.2 No module matches a product name directly.

Four defects, detailed in Phase 6. Defect #2 is the argument for mechanical
enforcement: the bug was already known, already fixed, and already documented
— and a second module reproduced it anyway.

### 5.3 Every check must prove it can fail.

A check runs a known-bad input through itself, **before** it scans anything
real. If the canary is not caught, the check reports failure and stops; it does
not report "clean".

> A green result means nothing unless you have watched the same code go red.

Seventeen instances of a check passing on data it could not see:

| # | What | How it looked |
|---|---|---|
| 1 | digit folding | a query for ৫০,০০০ against a folded index returns a plausible answer from the wrong chunk — no error |
| 2 | `recover_caption` | returned `""` when it could not isolate a caption; the caption vanished silently |
| 3 | `pattern_audit` importing `products`/`roles` | regenerated the registries, then validated its own fresh output |
| 4 | `গার্ডিয়ান লাইফ` | matched 2 of 12 chunks; a nukta-blind pattern under-matches without throwing |
| 5 | `conventions_check` | its Bengali word list was mojibaked by a PowerShell round-trip, so it reported "no product literals" while searching for strings that could never occur |
| 6 | `locate_gold_chunks` skipping TODO facts | a TODO fact was `continue`d, so a question whose facts were all TODO dropped out of the benchmark **denominator** with nothing said. G-206/207/208 read as healthy gold entries and contributed nothing to any score |
| 7 | text-matched gold location | a fact was located by searching the whole corpus for its text, so G-108's gold set was **396 of 867 chunks** and its 100% recall@3 was near-unfalsifiable. See the random-baseline note in Phase 13 |
| 8 | `dup_*` dropped by `resplit.py` | `dedupe.py` computed `dup_group`/`dup_members` into `*_dedup.jsonl`, but `CORPUS_FILES` loads `*_final.jsonl`, which `resplit.py` regenerates without them. The declared-duplicate expansion was **inert while appearing configured** — same shape as `nest_depth` computed in `ex.py` and dropped by `add()` |
| 9 | `locate_gold_chunks` filtering TODO **before counting** | it counted only usable refs, so `[real, TODO]` read as one unambiguous ref and every unref'd fact silently resolved to the real entry. G-102's ২.৪০% was attributed to chunk `0007`, where it does not occur. `validate_gold.resolve_ref` counts TODO entries and caught it; the copy in `embed.py` did not |
| 10 | the duplicate-crowding **metric itself** | it counted distinct `chunk_id`s, and twins always have different ids -- so it reported 5.00 distinct in every top-5 and said the crowding was gone when it had not been measured at all. It has to key on `dup_group`. Worse, the original finding was taken from the **dense-only top-3** and generalised to the fused path without rechecking: the real figure is 4.57 -> 5.00 distinct for livestock and **5.00 -> 5.00 for bootstrap**, where there was no crowding to remove. A measurement can be false-clean exactly like a check |
| 11 | the gold-span **extractor**, verified against its own copy | it sliced facts out of a body built with `.replace("\n", " ")`, then asserted `literal in that same flattened body` -- so the check agreed with the bug and passed. The stored fact was NOT byte-exact in the chunk: G-216's span glued the heading line `১. প্রত্যাশা লোন:` to the following sentence across a newline. `validate_gold` caught it only because it reads the RAW `display_text` independently. Same shape as instance 3 -- a check that imports the thing it is asserting against verifies self-consistency, not correctness |
| 12 | ad-hoc acronym **counting**, going around a guard that already existed | a one-off `str.count` reported **440** occurrences of the four programme acronyms where the guarded figure is **282** across 134 chunks; `সিডিপি` alone read 180 against a true 22, because it is a substring of `এনসিডিপি` and `এসসিডিপি`. NOT a new collision -- row 4 of §6.3, guarded in `productmatch` since Phase 6 by `(?<![নস])সিডিপি`. The script simply never reached the registry. Nothing threw, and the number was written into a gold-file note as evidence. Fixed by `bn_shared.count_literal`, which is shorter than the loop it replaces; Convention 2 was NOT extended, because a rule costing more than the wrong thing collects exemptions |
| 13 | a **canary fixture** hand-typed instead of sliced from the stored run | A DISTINCT CLASS from every row above: not a check blind to its data, but a check **passing on data that resembled the real data**. `language_match_ids` was given qwen's answer as hand-typed codepoints and got two wrong -- `\u0938` (Devanagari `स`) for `\u09b8` (Bengali `স`), and composed `\u09df` where qwen emits `য়` **decomposed** as `\u09af\u09bc`. Both render identically; neither is visible by re-reading. It went green on a string no model ever produced, satisfying Convention 3's letter (a real failure, watched going red) while breaking its intent. Surfaced only because a hit printed as `িনথেটিক_এ` -- the Devanagari character sat outside the Bengali range being scanned, so the token started one character late. Fixture now generated from the saved answer and asserted to be a verbatim prefix of it; rule recorded in CONVENTIONS.md under Convention 3 |
| 14 | the **class-A classifier** I proposed -- and then the correction to my own correction | G-207's gold fact is `নতুন ঋণ: ২০০০০-৭০০০০ — বিএম`; **both** models answered `১৫,০০০-৭০,০০০`. I recorded this as a fabricated figure on the grounds that `১৫,০০০` "appears in NEITHER manual". **THAT IS FALSE, and checking it took one query.** `১৫০০০` occurs 12 times in 4 chunks, and `progoti_0057` / `dabi_2025_0050` -- a declared twin pair in the SAME clause §2.1.1(চ) -- print the row `নতুন ঋণ: ১৫০০০-৭০০০০ — বিএম` verbatim. §2.1.1(চ) carries TWO approval tables: one split by branch demand category (`উচচ চাহিদা` → `২০০০০-৭০০০০`, `সাধারণ চাহিদা` → `১৫০০০-৬০০০০`, chunks `progoti_0056`/`dabi_2025_0049`) and one that is not (`১৫০০০-৭০০০০`, chunks `progoti_0057`/`dabi_2025_0050`). **`progoti_0057` was IN the model's context and `progoti_0056` was not** -- so the model quoted its context correctly and the gold ref points at the other table. Not a fabrication: a corpus ambiguity, now in `corpus_gaps.md`. The classifier's 0.8 overlap was *right* and my reading of it was wrong. What survives is the narrower lesson -- **a similarity proxy cannot be the last word on a figure** -- and a new one that cost more: **I asserted a negative about the corpus without running the count.** `bn_shared.count_literal` answers it in one line and exists for exactly this. **Corrected again in §4q (2026-10-26):** the corpus was not ambiguous either. The manual captions the second table `(এনসিডিপি ও এসসিডিপি)`, and the extraction had left that caption in another chunk |

| 15 | the **reranker measurement**, which scored a model whose tokenizer never loaded | `AutoTokenizer.from_pretrained("BAAI/bge-reranker-v2-m3", local_files_only=True)` **did not raise** against a cache holding only `config.json`, `model.safetensors`, `adapter_config.json` and `modules.json` -- **no `tokenizer.json`, no `sentencepiece.bpe.model`, no `tokenizer_config.json`**. It silently built an `XLMRobertaTokenizer` with **`vocab_size 5`**. Every real token became `<unk>` (id 3), so Bengali text and ASCII gibberish of the same token count produce **byte-identical `input_ids`** -- verified: `tok([q],['গোলাপ ফুল সুন্দর'])` and `tok([q],['xyzzy plugh frobnitz'])` both give `[0,3,3,2,2,3,3,3,2]`. The ONLY surviving signal is token count, so the "reranker" was ranking by **sequence length**. Every recall figure produced from it is void. It went undetected because the load was run through `grep -vE "Warning|warn"`, which removed the one line that said so -- see CONVENTIONS.md rule 3, "never filter the output of a check you have not read once in full". **A model that loads is not a model that works, and `local_files_only=True` will assemble a tokenizer out of nothing rather than tell you the files are missing** |
| 16 | the **quiz builder's rule 4**, disabled entirely while reporting clean | The distinguishing-token test split questions and evidence into Bengali words with `[অ-হ়-ৌ]{4,}` -- a range that covers letters and vowel signs and **omits the virama U+09CD**. Bengali cannot be written without it: every conjunct broke, so `আঞ্চলিক` came out as `চলিক` and `ব্যবস্থাপক` as `থাপক`. Fragments match nothing in a real question, so the test found no distinctive token anywhere, concluded every question was adequately scoped, and **refused nothing**. Rule 4 ran, printed clean, and enforced nothing -- measured: it should have dropped 4 questions (Q-003/Q-024 and Q-031/Q-037, two §7.1 authority rows each giving the same role a different limit) and dropped 0. Widening to the full Bengali block `[ঀ-৿]` fired all four. **A character class over Bengali that omits U+09CD does not match Bengali words, it matches the pieces between their conjuncts** |
| 17 | the **document-matrix cache**, guarded by a count | `embed.build()` reused `out/benchmark/<slug>.docs.npy` behind one line -- `if D.shape[0] != len(docs)`. On 2026-09-25 the R1 heading fix rewrote **80 of 867** chunks' `display_text` and left the count at **867 on both sides**, so the guard saw `867 == 867` and would have handed back the pre-fix matrix. `build()` would then have written a *rebuilt* index carrying **stale vectors for those 80 chunks**, printed `index -> out/index (867 chunks, dim 768)`, and returned 0. Nothing raises, nothing logs, and every later retrieval number is scored against text that no longer exists. Caught by hand while planning the rebuild -- by no check. Now keyed on **content**: `sha256` of each chunk's `display_text` (imported from `dedupe.manifest_sha`, so the cache and the manifest cannot drift), plus one digest over the exact strings handed to the encoder, which also catches a change in `display_norm` or the digit twin that leaves `display_text` alone. A refusal **names the chunks** |

**Number 5 is why this is a convention rather than a habit.** The check was
written *specifically* to catch silent failures, and it failed silently.

**Number 9 is the Convention 2 lesson again, in a new place.** Two functions
claimed the same rule -- which chunk a gold fact may be cited from -- and the
weaker copy invented provenance. It was not caught by a test; it was caught by
a single question whose facts happened to live in two chunks. `embed.py` now
imports `resolve_ref`, `fact_text` and `is_todo` from `validate_gold` rather
than reimplementing them: one owner, one implementation. The import direction
is safe because `validate_gold` depends only on stdlib, `yaml` and
`bn_shared`, and has no import-time side effects.

**Number 10 is the one to remember when reading this document.** It was not a
check that went green -- it was a *measurement* reported in a status update,
and it was wrong in two independent ways at once: the metric could not see the
thing it named, and the finding it appeared to confirm had been carried over
from a different code path. Everything in section 3 is a measurement. Treat a
number with no stated denominator and no stated code path as unverified.

**Number 17 is the mechanism, stated plainly, and it is the one that
generalises.** A guard tests the property that is cheap to check and passes
while the property that is expensive to check is wrong. Counting rows is free;
comparing 867 chunks of text is not, so the guard counted rows -- and a change
that holds the count constant walks straight through it. Rewriting text is
exactly such a change, and so is renaming, reordering, or re-normalising.

Every entry above shares that shape once you look for it. Number 10 counted
distinct `chunk_id`s because that is cheap, when the question was whether the
CONTENT was distinct and twins always carry different ids. Number 3 validated
freshly regenerated registries because reading them was cheap, when the
question was what was on disk. Number 15 asked whether a tokenizer loaded,
which is cheap, when the question was whether it had a vocabulary.

**The test: name the property the guard actually reads, and the property you
meant. If they differ, the guard is decorative.** For a cache the meant
property is "is this the same content", and the only honest key is a hash of
the content. It costs one pass over the corpus, against twenty minutes of
re-encoding and an index nobody can trust.

**Its canary is the near-miss itself**, not a stand-in: the live corpus with
one chunk's sha put back to the value the manifest held before R1, count
identical on both sides. It asserts four things -- a clean cache is ACCEPTED
(a guard that refuses everything is equally broken and far less likely to be
noticed), the pre-R1 sha is REFUSED, the refusal NAMES `dabi_2025_0034`, and a
changed count is still refused so the old guard's one real power is not lost.
It refuses to run at all if that chunk is missing, or if its sha has drifted
back to the fixture value, because then the fixture would be testing nothing.

**Numbers 6-12 all sit in the EVALUATION layer, not the ingestion layer.** The
checks that guard extraction were hardened first, and the gold/scoring path
inherited none of that scepticism. A wrong score is not a louder failure than a
wrong chunk -- it is quieter, because it looks like progress.

**Choose a canary from real history, not imagination.** The গার্ডিয়ান canary is
the exact pattern that was in the repo. A synthetic canary tests what you
imagined the bug was; a real one tests what actually happened.

### 5.4 A Bengali literal and the text it matches must agree on nukta form.

Preference order: **canon the text** where the text side is yours;
**`canon_pattern` the literal** where it is not (raw extractor output, a model's
answer). Never a bare Bengali literal against un-normalised text. Full
mechanism in Phase 5.

### The check itself

`conventions_check.py` runs four things, after first running a **synthetic
violation through itself**:

1. **static** — AST scan for module-level work
2. **dynamic** — imports each module in a subprocess and fails if any file's
   mtime changed, catching writes the AST scan cannot see
3. **literals** — AST scan for product names in matching contexts
4. **bengali literals** — `bengali_literal_lint.scan_paths`, failing on the
   ERROR tier only

Verified against real history: given the original `ex.py` from commit
`e0f63e5`, it reports 6 product literals and 15 module-level statements.

---

## 6. What the project produced beyond the chatbot

`data/corpus_gaps.md` records defects **in the manuals themselves**, found while
building the corpus. This output does not depend on how the chatbot performs —
if the chatbot were abandoned tomorrow, this list would still be worth acting
on, because each item is something a colleague reading the manual could also
get wrong.

Highlights:

- **আরোগ্য ঋণ** — §5.1.2 *excludes its customers from loan security insurance*,
  and neither manual ever defines the product. An officer can find out whether
  their client is covered; they cannot find out what the product is.
- **Four programme acronyms** (বিসিইউপি, সিডিপি, এনসিডিপি, এসসিডিপি) appear
  across **194 chunks** — in authority tables, complaint routing, cash rules —
  and are expanded nowhere.
- **No loan-approval ceiling exists for সিডিও or পিও**, though the changelog
  cites a §2.1.1(ট) that is not in the document.
- **§5.4 means different products in the two manuals** — fire-safety insurance
  in Progoti, livestock insurance in Dabi. This is the single strongest reason
  every citation must name its manual.
- Two source spelling errors, faithfully preserved through Bijoy conversion:
  শস্য নি**া**পত্তা (missing র, 4 chunks against 158 correct) and
  **অরগানোগ্রাম** (missing hasant).

---

## 7. Current status

**Done**

- Both `.docx` manuals converted from Bijoy and ingested — 831 chunks
- Livestock Grow transcribed and ingested — 36 chunks
- Registries built: products, entities, roles, with exclusions
- Deduplication grouped; divergence points documented
- Four conventions codified and mechanically enforced
- Hybrid index built on LaBSE with digit twins
- Retrieval filters implemented; retrieval evaluated at recall@5 = 16/17
- Two gold sets drafted; corpus gaps documented for the programme team
- **Generation has run** on `openai/gpt-oss-120b` over the 17-question gold
  set at k=5 and k=10. Current numbers, k=5, current code (§4b explains why
  the 18 Sep figures in `out/read_answers.md` differ): fact recall 8/15,
  citation **7/11 = 63.6% (file-backed)**, abstained correctly 1/1, answered
  correctly 14/16, violations 0/17, language 15/17.
  <br>**The citation figure previously written here was 12/12 = 100% and is
  UNVERIFIED** — it came from a chat report whose answer files no longer
  exist and cannot be re-scored. 7/11 is what `out/eval_report.json` holds.
  See §4j.
- **The k sweep is measured.** k=10 doubles the token cost and converts 5
  facts and 3 citations from unachievable to achievable while recovering
  none of them; violations stay 0/17. k=20 is refused by the provider on 15
  of 17 questions. See §4d.
- **The embedding benchmark is finished.** BGE-M3 dense beats LaBSE hybrid
  at k=5 (10/16 vs 9/16); LaBSE dense finds the gold chunk first on 1 of 16.
  See §4a.
- **Metric 3 scores both directions** — declining an answerable question is
  no longer invisible. See §4c.

**Not done**

- **Every service-charge figure in the FAQ rests on the regulator's 24% cap
  — and the regulator has said it will revisit it.** Recorded 2026-10-25
  from the project owner: the Microcredit Regulatory Authority (MRA) has
  capped microcredit service charges at 24% since 2019, and said in April
  2026 that it plans to "rationalise" service charges. Neither statement
  is checked against an MRA document in this repository; they are
  recorded here because the FAQ depends on them, not because this repo
  can vouch for them.

  **What depends on it.** 13 answers in `data/faq/answers.csv` state a
  service-charge rate: F-01, F-02, F-03, F-12 (24%); F-06, F-07, F-08, F-09,
  F-10, F-11, F-14 (the 24/22/20% declining slab); F-13 (18%); F-22 (20% on
  the unpaid balance). 24% is the top rate in eleven of them, so it is the
  figure a lower cap would make wrong first. The English versions carry
  the same figures, and `faq_check.py` holds the two languages to each
  other, so an update to one without the other is caught.

  **If the cap changes, update the registry and those answers together,**
  in one change: the registry first (a `rule_updates.csv` row for the
  affected clauses, so the chatbot stops quoting the old rate the moment
  the manual is out of date), then each answer above, then a fresh review
  — `python faq.py approve` — because editing an approved answer voids
  its approval and it leaves the FAQ tab until someone re-approves it.
  That is the intended behaviour: the alternative is a reviewed-looking
  answer quoting a rate the regulator no longer allows.

- **A question asking "কত শতাংশ সঞ্চয়" is refused as an unknown product.**
  Found 2026-10-26. `registry.unknown_product_name()` takes the word before
  ঋণ/সঞ্চয় for a product name: "ঋণ নিতে সদস্যকে কাঙ্খিত ঋণের কত শতাংশ সঞ্চয়
  জমা দিতে হয়?" returned "এই নামে কোনো প্রোডাক্ট ম্যানুয়ালে নেই।" with
  `unknown_product: কাঙ্খিত`, and two rewordings fired on শতাংশ. কাঙ্খিত ঋণ is
  the manual's own phrase. A false refusal on an ordinary question; not
  fixed.
- **Answering rule 8, added 2026-10-26** (`generate.SYSTEM_PROMPT`): where
  the context gives different figures for different branch types, products
  or programmes, give every one, labelled, even when the question asks for a
  maximum. Answers produced before this date ran on seven rules, so gold
  runs either side of it are not one-for-one comparable. Tested on three
  questions: the branch-type case now gives both ceilings; a product case
  asked which scheme was meant (rule 6) instead of listing them; a programme
  case gave only Progoti's figure because retrieval missed the group-based
  one. Whether rule 8 should override rule 6's "ask which one" is open.
- **Three follow-ups from the caption fix (§4q, 2026-10-26).** F-35 now opens
  by saying its table is Dabi/BCUP's and NCDP/SCDP have their own (awaiting
  review). Q-003 (validated) and Q-056 (draft) present the
  NCDP/SCDP band as the group-service rule; Q-002, Q-003 and Q-040 need
  re-review because their cited chunks changed (quiz chat). Single-row
  tables are dropped from the corpus (Dabi table 36 and its Progoti twin).

- **§2.5.4 puts the resolution time and the resolver in separate chunks, so
  a question asking both gets half an answer.** The column-major rendering
  fixed the labelling — every value now names its programme and its
  sub-column — but §2.5.4 is wide enough that each (programme × sub-label)
  column becomes its own `resplit` part. `প্রগতি — সমাধানের সময়` is
  `progoti_0090_p3` and `প্রগতি — সমাধানকারী` is `progoti_0090_p4`, where one
  chunk used to carry both.

  **Measured, not assumed.** Each quiz question as actually asked retrieves
  what it needs (2 of 2 complete, both at k=5). A question asking *both* —
  "প্রগতি ঋণে এই অভিযোগ কে কত দিনে সমাধান করেন?" — returned only one of the
  two needed chunks, in both forms tried. The answer is attributable now and
  it was not before; it is also, for that shape of question, half there.

  **The trade this records.** The old single chunk held both values and
  labelled neither, so a complete answer was available and an attributable
  one was not. Neither state is right, and the fix is not to go back: it is
  to pack each programme's two sub-columns into ONE part, so `সমাধানের সময়`
  and `সমাধানকারী` travel together under the same programme. That is a
  change to the packing loop in `resplit.py`, not to the renderer.

  **Deferred on purpose.** It would renumber the §2.5.4 parts again and
  re-point Q-043 and Q-069 for a third time — both are human-reviewed
  questions in `quiz/bank_validated.yaml`, and each re-point costs a review.
  Worth doing in the same pass as the next §2.5.4 change, not on its own.
  See §4n.

- **§2.5.4 কমপ্লেইন্ট ডাটা still loses its sub-labels — the same defect as
  Class B, deliberately left.** Its header is two rows, exactly as §5.1.3's
  is: row 0 carries the programme groups (দাবি/বিসিইউপি, প্রগতি,
  এনসিডিপি/এসসিডিপি) with gridSpan padding, row 1 carries সমাধানের সময় and
  সমাধানকারী under each. I compared the raw `table_rows` of both before
  deciding, and no structural test separates them: anything that selects
  §5.1.3 selects §2.5.4 too. It is excluded by the clause list in
  `tablerender.COLUMN_MAJOR_CLAUSES`, not by the shape test.

  **What is lost, and what is not.** The programme attribution SURVIVES —
  `অভিযোগের ধরণ: ১.১ ... — দাবি/বিসিইউপি: ১৫ — বিএম, এএম, আরএম — প্রগতি: ২১ —
  ...` puts ১৫ under দাবি/বিসিইউপি correctly. That is why §2.5.4 was cleared
  in the survey and why this is not urgent. What is lost is the second level:
  ১৫ is not labelled সমাধানের সময় and বিএম, এএম, আরএম is not labelled
  সমাধানকারী. A reader recovers both from position and from the units; a
  model asked "who resolves it, and in how long" has to do the same, and that
  inference is what produced §1.1.2's six wrong figures.

  **Why it was left, and what it would cost.** The renderer change is being
  validated by comparing `out/index_gemini` against `out/index_gemini_v2`
  question by question, and a change that also moves chunks nobody is asking
  about makes that comparison harder to read rather than safer. The code
  change is one line — add `"2.5.4"` to `COLUMN_MAJOR_CLAUSES` — and the same
  four acceptance conditions apply unaltered. The cost is 12 chunks
  (`dabi_2025_0079_p1..p5`, `progoti_0090_p1..p7`, as they now stand; the
  column split will renumber them) **and two human reviews**: Q-043 and Q-069
  in `quiz/bank_validated.yaml` both quote a §2.5.4 line verbatim, and both
  ask exactly the question the missing sub-label makes ambiguous — `প্রগতি: ৭
  — এএম`, where ৭ is the resolution time in days and এএম the resolver, and
  neither is labelled. So the fix would improve the two questions whose
  reviews it costs. **This is the obvious next candidate.** See §4n.
- **The abstention floor 0.4044 was derived on LaBSE's score distribution**
  and BGE-M3's fused scores run materially higher (G-217 0.7113 vs 0.5531),
  so the floor is effectively looser under it. It must be re-derived before
  BGE-M3 is adopted. **Deliberately not re-derived yet** — doing so before
  the end-to-end comparison would change two things at once.
- **BGE-M3 is built and switchable but NOT adopted.** `out/index` is still
  LaBSE; `out/index_bge_m3` holds the dim-1024 candidate and
  `BFA_INDEX_DIR` switches. Measured on the retrieval side: +1 fact into the
  denominator, +1 citation-achievable question, and G-222's correct citation
  LOST. The generation side is **not measured** — the run stopped 16 of 17 in
  on Groq's per-day token limit. See §4a-2 for what remains.
- **Groq's real budget is 200,000 tokens/day/model**, which the response
  headers do not carry — about four 17-question runs per model per day, not
  the 25 the requests-per-day figure implies. Now tracked locally in
  `out/token_ledger.json`; a run that cannot finish in the remainder refuses
  to start. See §4a-2 and §4a-3.
- **`gold_eval.py --baseline`** anchors achievability across an A/B, so a
  regression INTO unachievability is scored rather than excluded. See §4a-3.
- **`generate.py --run-id`** writes a per-invocation log carrying the answer
  text, flushed per question, so an aborted run leaves its evidence.
- **`gold_eval.py`'s abstention canary is no longer empty** — it carries
  G-223's real clarification refusal and G-208's real false abstention, with
  G-202's substantive answer as the control. Backfilled from real failures,
  as intended.
- **The livestock figure worklist is unverified.** Someone other than the
  transcriber must read `out/livestock_grow_2026/figure_worklist.html` against
  the renders and flip `extraction_confidence` to `verified` before that manual
  is trusted.
- **`probe_words` / `pdf_extract_check` canary strings** were transcribed from a
  mojibaked copy of the record rather than read back from the original
  extraction. They reproduce the recorded 0.40 / 1.00 split exactly, which is
  the evidence they behave like the original — but re-record them when the real
  Dabi 2026 extraction is to hand. Convention 3, failure #5 is exactly a word
  list mojibaked by a PowerShell round-trip.
- **`reconcile.py`** is referenced by the ingestion record and the old run
  script but **does not exist**. Product reconciliation is a manual step until
  it does.
- **The Dabi 2026 `.docx`** has been requested and not received. Bundle any
  further requests with it.

---

## 8. How to run it

```bash
# Conventions — run before every commit
python conventions_check.py                       # rules 1, 2, 4 + self-check
python pattern_audit.py                           # product/role patterns + canary
python bengali_literal_lint.py *.py tests/*.py    # rule 4, all advisories

# docx ingestion
python bijoy_scan.py    data/raw/dabi_manual_2025.docx
python bijoy_convert.py data/raw/dabi_manual_2025.docx
python ex.py            data/processed/dabi_manual_2025_unicode.docx dabi_2025
python retag.py         data/processed/chunks_dabi_2025.jsonl    # expect 0 changed
python resplit.py       data/processed/chunks_dabi_2025_retagged.jsonl

# PDF ingestion -- quote the path, the filename has spaces and brackets
./run_livestock_grow.sh "data/raw/Operational Manual_Livestock (2026).pdf" \
    livestock_grow_2026

# Cross-manual
python dupscan.py dabi_2025 progoti 0.95
python dedupe.py --check

# Index and retrieval
python embed.py --benchmark
python embed.py --build sentence-transformers/LaBSE      # the live index
# out/index_bge_m3 IS STALE: it holds the pre-R1 corpus (see 8.1). Re-encode
# before any BGE-vs-LaBSE comparison, or the comparison measures the heading
# change and not the model.
python embed.py --build BAAI/bge-m3 --out-dir out/index_bge_m3
python retrieve.py "দুগ্ধজাত গাভীর প্রিমিয়াম কত?" --insurer green_delta_insurance
python retrieve.py --selftest

# Evaluation
python validate_gold.py --gold gold/bootstrap.yaml   # --gold, NOT positional
python gold_status.py --show
python retrieval_eval.py
# --answers is whatever your run produced. out/answers.jsonl is a placeholder
# and is not in the repo; the real ones are out/answers_synth.jsonl and
# out/demo_answers.jsonl.
python gold_eval.py --gold gold/bootstrap.yaml \
    --answers out/answers_synth.jsonl --forbidden-numbers out/forbidden.json
# The synthetic set, registry off -- the only run that may pass --no-registry
python generate.py --make-synthetic
python generate.py --retrieved out/synthetic_retrieved.jsonl \
    --gold gold/synthetic.yaml --score --no-registry

# Reading a chunk by eye
python show_chunk.py progoti_0062 dabi_2025_0033
```

### 8.1 `out/index_bge_m3` is stale, deliberately

**State as of 2026-09-25:** `out/index` and `out/index_labse` hold the current
corpus. `out/index_bge_m3` holds the corpus **as it was before the R1 heading
fix** — 80 of its 867 vectors are for text that no longer exists, and its
`meta.jsonl` carries the old `heading_path` for those chunks.

It was left that way on purpose. It is a benchmark artefact, not the live
index, and re-encoding BGE-M3 costs ~20 minutes on a machine that had already
run out of memory once that day loading the smaller LaBSE (7.7G total, ~2.4G
free; the first LaBSE build died in `materialize_tensors` with `MemoryError`
and succeeded only on a retry with nothing else running).

**The trap, which is the reason this section exists.** A BGE-vs-LaBSE
comparison run now would put a **pre-R1 candidate** against a **post-R1
baseline**. Any difference it reported would be dominated by 80 chunks gaining
an ancestor heading, not by the models. It would look exactly like a model
result: two numbers, one bigger. Same family as instance 17 in 5.3 — the
cheap property (both directories exist, both hold 867 vectors) agrees while
the expensive one (same corpus) does not.

**So: re-encode before comparing.** One command, and the cache now refuses to
help you skip it:

```bash
python embed.py --build BAAI/bge-m3 --out-dir out/index_bge_m3
```

The stale `.docs.npy` matrices for both models were moved out of
`out/benchmark/` when the corpus changed, so nothing can silently reuse them.
Since 2026-09-25 the cache is content-keyed and would refuse them anyway,
naming the 80 chunks — but the cache guard is a second line of defence here,
not the reason this is safe.

---

## 9. What to carry to the next project

1. **Diagnose before you build.** `bucket_test.py` was written before any
   extraction code, and it is why we never ran OCR on a Bijoy document.

2. **"Is there text" and "is the text right" are different questions.** Every
   ratio-based check answers the first and reads like an answer to the second.

3. **Silent under-matching is the dominant failure mode in multilingual text.**
   Nothing throws. The count is simply lower than it should be, and the only
   signal is a number that looks implausible *to someone who knows what to
   expect*.

4. **A check that cannot fail is worse than no check**, because it produces the
   green result you were hoping for. Run a real historical failure through every
   check, in the same run, before it scans anything real.

5. **Balance your controls.** We built six negative controls before noticing we
   had zero positive ones. Absence-of-bad and presence-of-good are different
   guarantees.

6. **A check that cries wolf on good input gets switched off**, and then it
   protects nothing. Hence: two panels, only one gates; two lint tiers, only one
   blocks.

7. **Thresholds derived from measurement, not intuition.** 0.95 "feels right"
   for near-duplicates and merges 35 pairs that disagree about approval
   authority. 1.000 was measured.

8. **Never average scores that answer different questions.** Abstention accuracy
   is invisible in any aggregate, and it is the number that matters most.

9. **Record provenance and never merge across it.** Questions written while
   looking at a chunk measure agreement with yourself, not correctness.

10. **Write down what a check cannot see.** The figure audit flags implausible
    figures; a plausible wrong one passes everything. Saying so is what makes
    the human verification step non-optional instead of merely recommended.

11. **A clause number locates a passage; it does not say what the passage is
    about.** §5.4 is a different insurance in each manual, and §5.2.3 carries
    two topics inside one manual. Any routing keyed on clause alone is
    reading the cheap property -- see 4k.

12. **One definition of every shared thing.** One fold table, one chunk schema,
    one product matcher, one normaliser per job. Every duplicate in this repo
    became a divergence, and every divergence was silent.

13. **Convention that is enforced survives; convention that is written down
    does not.** Fifteen breakages by people who knew the rule is the evidence.
