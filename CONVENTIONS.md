# Project conventions

Three rules:

1. `main()` behind `__main__`. Importing must never write files.
2. No module matches a product name directly.
3. Every check must prove it can fail.

Rules 1 and 2 are enforced by `conventions_check.py`, which exits non-zero on
a violation. Rule 3 is enforced by each check's own self-check. Run before
committing:

```
python conventions_check.py     # rules 1 and 2, plus its own canary
python pattern_audit.py         # product/role patterns, plus its canary
```

All three exist because they were broken repeatedly — twelve times between
them — by people who knew the rule. Prose in a README does not survive that.
A check that fails does.

---

## 1. `main()` behind `__main__`. Importing must never write files.

Every script keeps its work inside a function. Module level holds imports,
constants, and definitions — nothing else.

```python
# yes
def main(src):
    data = load(src)
    write(data)

if __name__ == "__main__":
    main(sys.argv[1])
```

```python
# no
data = load(sys.argv[1])      # runs on import
write(data)                   # writes on import
```

### Why

Broken three times, each time with the same symptom: something imported a
module for one function and silently triggered its whole job.

| When | What happened |
|---|---|
| `retag.py` | `products.py` imported it for `PROD`; the retagger ran and rewrote `chunks_*_retagged.jsonl` |
| `products.py` | `pattern_audit.py` imported it for the pattern tables; **the registry CSV was rewritten as a side effect of auditing it** |
| `roles.py` | same, in the same audit run |

The third case is the instructive one. The audit existed to *check* the
registries and quietly *regenerated* them instead — so it was validating its
own fresh output rather than what was on disk. Nothing failed; the numbers
just meant something different from what they appeared to mean.

### What counts as allowed at module level

- imports, constants, `def`, `class`
- `try:`/`except ImportError:` around an optional import
- `sys.path.insert(...)` in `tests/`, which must run before local imports
- the `if __name__ == "__main__":` block

Anything else — a loop, a `with`, a bare call — is a violation. Pure library
modules such as `bnnorm.py` and `productmatch.py` need no `main()`; they
define things and stop.

---

## 2. No module matches a product name directly.

`productmatch.py` owns every product and programme literal. Import from it.

```python
# yes
from productmatch import programmes_in
tags = programmes_in(text)
```

```python
# no
if "দাবি" in text:            # matches বিমাদাবি too
    tags.append("dabi")
```

### Why

Broken four times, and every instance was a correctness bug that produced
plausible output:

| # | Where | Bug |
|---|---|---|
| 1 | `ex.py` | bare `দাবি` matched inside **বিমাদাবি** ("insurance claim"), tagging insurance-claim text as the Dabi loan product |
| 2 | `products.py` | `re.escape("দাবি")` re-introduced the identical bug while picking defining sections, making §5.2.6 (claim procedure) look like the definition of the দাবি programme |
| 3 | `ex.py` | bare `গতি` matched inside **প্রগতি**, **অগ্রগতি**, **গতিশীল** — so a real product (গতি লোন) was first over-tagged, then wrongly deleted altogether on the evidence of those false positives |
| 4 | `productmatch` | **স্বাধীন লোন** is spelled **স্বাধীণ ঋণ** in the changelog; a single-spelling pattern found only part of it |

Number 2 is the one that should worry you: the bug was already known, already
fixed once, and documented in `retag.py` — and a second module reproduced it
anyway, because it wrote its own matcher.

These names are ordinary Bengali words. দাবি is "claim", গতি is "speed". They
cannot be matched by substring, and the correct pattern is not obvious. It
belongs in one place, with the reasoning attached.

### Pattern rules

- compile with `productmatch.compile_pat`, which normalises via
  `bnnorm.canon_pattern`
- match against text normalised with `bnnorm.canon`
- **`canon` for text, `canon_pattern` for regexes.** `canon` strips digit
  separators, which rewrites a quantifier: `\S{0,4}` becomes `\S{04}`, "up to
  four" becomes "exactly four". That cost গার্ডিয়ান লাইফ all 12 of its
  matches.
- allow for NFC decomposition. য় ড় ঢ় are composition exclusions, so NFC
  splits them into base + nukta. A pattern written as if য় were one character
  silently under-matches — `গার্ডিয়ান লাইফ` found 2 of 12 chunks that way.
  `pattern_audit.py` probes for this.

### What is not a violation

Writing a product name in a **log message, a comment, a docstring, or a
human-readable note** is fine. The rule is about matching, not mentioning.
The check distinguishes these: it flags a literal only when it is an argument
to a matching call, sits in a pattern-ish table, or is a bare Bengali name
rather than an English sentence.

### Registered exemptions

| Module | Why |
|---|---|
| `productmatch.py` | owns the tables by definition |
| `dcheck.py` | its `GOOD`/`BAD` lists are **encoding probes** — "did readable Bengali come out of the docx at all". Not product identification, so substring semantics genuinely do not matter |
| `conventions_check.py` | names the words it searches for |

Add an exemption only with a reason, in `LITERAL_EXEMPT`.

### Roles are a separate vocabulary

`roles.py` owns role names (সিডিও, পিও, …). They are not products and are not
in `productmatch`. The সিডিও↔পিও equivalence is recorded as **metadata, never
query expansion** — see that module's docstring for why erasing the
difference would be wrong.

---

## 3. Every check must prove it can fail.

A check runs a known-bad input through itself, in the same run, **before** it
scans anything real. If the canary is not caught, the check reports failure
and stops — it does not report "clean".

```python
# yes
def self_check():
    return detects(KNOWN_BAD)

if not self_check():
    print("SELF-CHECK FAILED -- this check is not testing anything")
    return 1
# ... only now scan the real inputs
```

A green result means nothing unless you have watched the same code go red.

### Why

Because a check that silently loses its own input reports "clean", and
"clean" is exactly what you were hoping to see. This is the fifth time
something has passed on data it could not actually see:

| # | What | How it looked |
|---|---|---|
| 1 | digit folding | a query for ৫০,০০০ against a folded index returns a plausible answer from the wrong chunk — no error |
| 2 | `recover_caption` | returned `""` when it could not isolate a caption; the caption vanished silently |
| 3 | `pattern_audit` importing `products`/`roles` | the audit **regenerated the registries** and then validated its own fresh output rather than what was on disk |
| 4 | `গার্ডিয়ান লাইফ` | matched 2 of 12 chunks; a nukta-blind pattern under-matches without throwing |
| 5 | `conventions_check` | its Bengali word list was mojibaked by a PowerShell round-trip, so it reported "no product literals" while searching for strings that could never occur |

Number 5 is the reason this is a convention rather than a habit. The check
was written *specifically* to catch silent failures, and it failed silently.

### The two implementations

**`pattern_audit.py`** carries the গার্ডিয়ান pattern as it was when the bug
was live, and asserts the nukta probe still flags it:

```
canary   : guardian_life as originally written
pattern  : গার্ড[িী]?[য]?ান\s*লাইফ
hits     : 2 plain vs 12 nukta-tolerant
DETECTED
```

**`conventions_check.py`** runs a synthetic module containing a product
literal in code, the same product name in a docstring, and a module-level
call. It must flag the first and third and ignore the second.

Both exit non-zero if the canary stops firing.

### Choosing a canary

Use a **real** historical failure, not an invented one. The গার্ডিয়ান canary
is the exact pattern that was in the repo. A synthetic canary tests what you
imagined the bug was; a real one tests what actually happened.

### A Bengali canary fixture must be sliced from a stored artefact

Never hand-type it. Read it out of the saved run, the chunk file, or the gold
file, and assert it is a substring of that artefact in the same breath:

```python
# yes -- generated from the saved answer, and checked against it
_MIRROR = _slice_of(SAVED_ANSWER)
assert SAVED_ANSWER.startswith(_MIRROR)

# no -- typed from looking at the glyphs
_MIRROR = "সিনথেটিক_এ ম্যানুয়ালের §৯.১.১ অনুযায়ী"
```

**Because the two things that decide whether a Bengali fixture is the real
failure are both invisible in the rendered glyph.** The measured case is
`gold_eval.language_match_ids`, 2026-09-18. Its fixture was hand-typed from
qwen's answer and carried two errors that no amount of re-reading would have
shown:

| Typed | Should have been | Renders as |
|---|---|---|
| `\u0938` — Devanagari `स` | `\u09b8` — Bengali `স` | স, identically |
| `\u09df` — composed `য়` | `\u09af\u09bc` — decomposed, as the model emits it | য়, identically |

The canary went **green**, on a string no model ever produced. It satisfied
this convention's letter — a real observed failure, a check watched going red
— and broke its intent, because the thing being watched was not the failure.
It surfaced only because a reported hit read `িনথেটিক_এ` with the leading স
missing: the Devanagari character fell outside the Bengali range the check
scans, so the token silently started one character late.

This is the same asymmetry as Convention 4, one level up. Convention 4 keeps a
*pattern* and the text it matches in agreement about nukta form; this keeps a
*fixture* and the artefact it claims to come from in agreement about
everything. A fixture you cannot diff against a stored artefact is a
transcription, and transcriptions of Bengali are exactly what this repo has
learned not to trust.

---

## 4. A Bengali literal and the text it matches must agree on nukta form.

Preference order:

1. **`canon()` the text.** Where the text side is yours to normalise, do
   that and match plainly. It is the cheapest correct answer and it is
   sufficient — see *Why* below.
2. **`canon_pattern()` the literal.** Where the text side is not yours —
   raw extractor output, a model's answer, anything you did not normalise —
   build the pattern so it matches either form.

Never a bare Bengali literal against un-normalised text. That is the one
case the lint fails on.

```python
# yes
from bn_shared import canon_pattern
CHAPTER_WORD_RX = canon_pattern("অধ্যায়")

# yes -- splice the built pattern into a larger regex
_ADHYAY = canon_pattern("অধ্যায়").pattern
CHAPTER_RX = re.compile(rf"^\s*(\S+)\s*{_ADHYAY}\s*[:ঃ：]?\s*(.*)$")
```

```python
# no
CHAPTER_WORD_RX = re.compile(r"অধ্যায়")      # nukta-blind
if "অধ্যায়" in text:                         # same failure, no regex
n = text.count("অধ্যায়")                      # same failure again
```

### Why

য় (U+09DF), ড় (U+09DC) and ঢ় (U+09DD) are on the Unicode **composition
exclusion** list. Composition *into* them is excluded, so NFC **does** unify
the two spellings — by decomposing the precomposed form:

```
NFC(U+09DF)        -> U+09AF U+09BC
NFC(U+09AF U+09BC) -> U+09AF U+09BC     # already there, unchanged
```

Both forms converge on the decomposed one. **`canon()` on both sides is
therefore sufficient**, and a plain substring test works afterwards.

The failure is the *asymmetric* case: a literal in one form matched against
text nobody normalised. Then the two never meet, and **nothing throws** — it
matches fewer occurrences, or none.

Three instances, none caught by the checks around it:

| # | Where | How it looked |
|---|---|---|
| 1 | `গার্ডিয়ান লাইফ` | matched 2 of 12 chunks. Found because 2-of-12 looked implausible, not because anything flagged it |
| 2 | `gold_eval` fact matcher | caught only because that canary was built from explicit codepoints |
| 3 | `CHAPTER_WORD_RX` / `CHAPTER_RX` | both carried decomposed য় and matched **raw** PDF lines, which emit precomposed U+09DF. Found 0 where a canon'd comparison finds 29 |

All three matched text that had not been through `canon()`. Patterns matched
against canon'd *chunks* were never broken: measured 14 Sep 2026, the corpus
is 100% decomposed on both ingestion paths (10,017 / 0 docx, 296 / 0 ocr),
because `ex.py`'s `nm()` and `ex_pdf.py`'s `canon()` both apply NFC.

Instance 3 is the instructive one. It killed **two** signals at once — real
chapter headings were never detected in any manual, and the over-detection
gate's ratio check was permanently inert — and the gate still reported a
verdict on three signals as though it had four.

### Why a lint and not just "always canon the text"

Because you cannot always reach the text. A pattern run over raw extractor
output, or over a model's answer, has no normalised side to lean on — and
that is precisely where all three failures were. The guard therefore has to
find *patterns*, which is an AST question, not a runtime one.

### Two tiers

`bengali_literal_lint.py` splits findings, and only one tier fails the build:

- **ERROR** — the literal contains a nukta character. It is broken now and
  silent about it.
- **ADVISORY** — Bengali, but no nukta. Works today; breaks the day someone
  edits the literal. Reported, does not fail.

Blocking on both would train people to add exemptions, which is how a
convention dies.

### Its canary, and what the first version got wrong

The canary runs real code from the files where the bug lived, and the two
nukta forms are built with `chr(0x09DF)` and `chr(0x09AF) + chr(0x09BC)` —
never typed. Typing them is the very ambiguity being linted: whichever form
the editor saved would be used for both sides, the asymmetry would vanish,
and the canary would pass while proving nothing.

That is exactly how `heading_gate`'s own canary passed while
`CHAPTER_WORD_RX` found 0 on every manual: its `_synth` fixture wrote the
text with the same literal the pattern used, so both sides always agreed.

---

## Consequence worth knowing

Once `ex.py` used the shared matcher, `retag.py` became a no-op — it reports
**0 of 393 chunks changed**. That is the intended end state, not a redundancy
to delete. `retag` now verifies that extraction and tagging still agree; if it
ever reports a non-zero change count again, two matchers have diverged.

---

## The check

`conventions_check.py` runs three things:

1. **static** — AST scan for module-level work
2. **dynamic** — imports each module in a subprocess and fails if any file's
   mtime changed, which catches writes the AST scan cannot see
3. **literals** — AST scan for product names in matching contexts

It begins with a **self-check**: a synthetic module containing a known
violation is run through the checks, which must catch it. That guard exists
because the word list was once mojibaked by a PowerShell round-trip, after
which the check reported "clean" while testing nothing.

Verified against real history — given the original `ex.py` from commit
`e0f63e5`, it reports 6 product literals and 15 module-level statements.
