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
(twelve, as of 2026-09-17 -- see 5.3; seven of them in the evaluation
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

**Clause-level expansion: a measured negative. Do not rebuild it.** The idea
was to pull in sibling chunks sharing a retrieved chunk's `manual_id` and
clause, so that a section arrives whole. Measured on the 17 recall-scoreable
questions, k=5, token cap 6,000:

| | result |
|---|---|
| unachievable facts recovered | **0 of 14** |
| unachievable questions recovered | **0 of 6** |
| median context tokens | 3,473 → 5,886 (**1.69x**) |
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

Twelve instances of a check passing on data it could not see:

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

**Not done**

- **Generation has not run.** No `answers.jsonl` exists, so fact recall,
  citation accuracy, abstention accuracy and violation rate are all unmeasured.
- **`gold_eval.py`'s abstention canary is empty** — to be backfilled from the
  first real generation failure, not invented.
- **The BGE-M3 benchmark did not finish.** LaBSE is the incumbent by default,
  not by measurement.
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

# PDF ingestion
./run_livestock_grow.sh data/raw/livestock_grow.pdf livestock_grow_2026

# Cross-manual
python dupscan.py dabi_2025 progoti 0.95
python dedupe.py --check

# Index and retrieval
python embed.py --benchmark
python embed.py --build sentence-transformers/LaBSE
python retrieve.py "দুগ্ধজাত গাভীর প্রিমিয়াম কত?" --insurer green_delta_insurance
python retrieve.py --selftest

# Evaluation
python validate_gold.py gold/bootstrap.yaml
python gold_status.py --show
python retrieval_eval.py
python gold_eval.py --gold gold/bootstrap.yaml \
    --answers out/answers.jsonl --forbidden-numbers out/forbidden.json

# Reading a chunk by eye
python show_chunk.py progoti_0062 dabi_2025_0033
```

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

11. **One definition of every shared thing.** One fold table, one chunk schema,
    one product matcher, one normaliser per job. Every duplicate in this repo
    became a divergence, and every divergence was silent.

12. **Convention that is enforced survives; convention that is written down
    does not.** Fifteen breakages by people who knew the rule is the evidence.
