"""generate.py -- STEP 9. The answer layer.

Reads retrieved context from disk, asks a model, writes answers.jsonl in the
shape gold_eval reads. No UI.

Conventions (CONVENTIONS.md):
  1. main() behind __main__. Nothing at module level but imports, constants
     and defs. Nothing here writes on import.
  2. No product literal is matched here. The system prompt MENTIONS manual
     and programme names in prose, which is mentioning, not matching; the
     only matching this module does is gold_eval's, through its functions.
  3. canary_selftest() below, and it reuses a real check rather than
     copying one.
  4. Every Bengali literal here is prose inside a prompt or a fixture, never
     a pattern. Where this module must test Bengali text -- the judge's
     YES/NO, the fixture assertions -- it goes through bn_shared.canon_pattern
     or gold_eval.fact_present, which own that comparison.

THE FREE TIER CANNOT SUPPORT A REAL EVAL. Read this before planning a run.

    GenerateRequestsPerDayPerProjectPerModel-FreeTier = 20

Twenty requests per model per day, resetting at midnight US Pacific. One
question is one call, so:

    synthetic set,  4 questions  =   4 calls  -> 5 runs per model per day
    real gold set, 39 questions  = 156 calls  -> 8 DAYS of budget, one model

A real scored run is therefore not merely slow on the free tier, it is
impossible: the run cannot complete inside a quota window, and a run that
cannot complete produces nothing, because answer_one raises on the first
provider failure. Partial budgets are wasted budgets.

`retryDelay: 58s` in the 429 body is a GENERIC BACKOFF HINT, not the time
until a per-day quota resets. That single misleading field is what made four
sessions of quota exhaustion look like model flakiness: every failure was
read as transient, retried, and attributed to an unreliable model.

TWO ROADS, AND THEY POINT THE SAME WAY. A real scored run needs either a paid
tier or a sampled subset of the gold set. Independently, the free tier
carries a clause permitting inputs to be used for model training, which the
corpus cannot clear -- these are BRAC internal documents pending a
data-protection decision, which is what CORPUS_GUARD below exists for. Both
constraints say the same thing: no real chunk goes to this provider on this
tier, whatever the budget.

NO SAMPLED-SUBSET MODE IS IMPLEMENTED, DELIBERATELY. Sampling 5 of 39
questions would make all four metrics noisy in a way the report cannot
express -- it reports counts per provenance with no confidence interval, and
a 5-question recall figure would read exactly like a 39-question one. That is
a worse failure than waiting for the provider decision.

USAGE
    python generate.py --make-synthetic            # build the synthetic pair
    python generate.py --retrieved out/synthetic_retrieved.jsonl \\
                       --gold gold/synthetic.yaml --score --no-registry
    python retrieve.py --dump out/retrieved.jsonl  # then, once cleared:
    python generate.py --allow-corpus "reason" --score
"""
import argparse
import functools
import json
from datetime import datetime, timezone
import os
import random
import sys
import time
from pathlib import Path

from bn_shared import canon, canon_pattern

# --------------------------------------------------------------- the prompt
# NINE RULES. Each comment names the failure the rule exists to prevent, so
# nobody "simplifies" a rule whose cost is invisible in the prompt itself.
SYSTEM_PROMPT = """You answer questions about BRAC microfinance operation manuals for field officers.

1. Answer ONLY from the CONTEXT below. Never infer, never fill a gap, never
   use knowledge from outside the context.
2. Name the MANUAL and the CLAUSE for every fact, e.g. "dabi_2025 §5.4".
3. Any answer about a premium or an exclusion must name the INSURER.
4. If the context does not answer the question, reply with a single
   sentence, in the question's language, stating that the manual does not
   contain it. Do not list clauses, do not describe what any clause contains,
   do not quote any figure.
5. If a product's status is not active, lead with the status, then the terms.
   A status of "unknown" is NOT active; do not imply the product is available.
6. If the question is ambiguous across manuals or programmes, answer for each
   of them when there are up to three, and ask which one is meant only when
   there are more than three. Never answer for just one.
7. Answer in the language of the question, but keep role names, product names
   and clause citations in BENGALI exactly as the context spells them.
8. If the context gives different figures for different branch types,
   products or programmes: when there are up to three cases, give the figure
   for every one of them and say which is which -- even when the question
   asks for a maximum, a limit or a single figure. When there are more than
   three, name them and ask which one is meant. Never give only one of them.
9. Every figure must name the programme it belongs to, as the "heading:" of
   the context it came from names it; where that heading names no
   programme, name the manual. Never give a figure without one.

End your reply with a line:
CITATIONS: <manual_id> §<clause>; <manual_id> §<clause>
If you cannot answer, write CITATIONS: none
"""

# Rule 1 -- prevents: fluent invention. The whole failure mode this project
#   is built around. An officer cannot tell an invented premium from a quoted
#   one, and nothing in the pipeline throws.
# Rule 2 -- prevents: a clause citation that is wrong two thirds of the time.
#   §5.4 is fire-safety insurance in Progoti and livestock insurance in Dabi.
#   A clause number without its manual name identifies nothing.
# Rule 3 -- prevents: a premium that is right for one underwriter and wrong
#   for the other. The same product carries a different premium per insurer,
#   so an answer omitting the insurer is right about half the time and the
#   officer cannot tell which half.
# Rule 4 -- prevents: the adjacent-answer substitution. §7.1 holds
#   procurement and administrative limits formatted exactly like the
#   loan-approval ceiling that does not exist, so "offer something close"
#   produces a confident wrong ceiling. Measured: forbidden.json exists
#   precisely because that clause is full of plausible taka figures.
#
#   REWRITTEN 2026-09-17. The wording it replaced was:
#
#     "4. If the context does not contain the answer, say so plainly. Do NOT
#         offer adjacent or similar-looking content instead."
#
#   That rule PERMITTED the failure it was written to stop. "instead" makes
#   the prohibition about substitution, so a model may abstain and then
#   mention the adjacent figure as a NON-substitute -- which is what they
#   did, in almost identical words:
#
#     "...কোনো তথ্য নেই। synthetic_a §৯.২.১ অনুযায়ী জিএম (GM) এর জন্য
#      ২,৫০,০০০ টাকার যে সীমার কথা উল্লেখ আছে তা শুধুমাত্র অফিস সরঞ্জাম
#      ক্রয়ের জন্য প্রযোজ্য, ঋণ অনুমোদনের জন্য নয়।"
#
#   The abstention is correct and the forbidden figure is in the same
#   sentence as the question's subject. Metric 3 counts the abstention,
#   metric 4 counts the violation, and both are true.
#
#   MEASURED, pinned models, synthetic context, 3 runs each:
#     gemini-2.5-flash        leaked 2/2      (+1 earlier, 3/3 total)
#     gemini-3-flash-preview  leaked 5/5      across two experiments
#     gemini-3.5-flash        CLEAN  3/3      abstained with no figure
#     gemini-flash-latest     CLEAN  1/1      then quota-exhausted
#   So it is model-dependent, not universal -- which is itself the argument
#   for rewriting it. A rule whose compliance depends on which model reads
#   it charitably is not a rule; two of the four models tested read the
#   literal text and were entitled to.
#
#   WORDING 2, "STRICT", 2026-09-17. Removed the loophole by removing the
#   exception: no other figure at all, not even to disclaim it.
#
#     "4. If the context does not contain the answer, name what is missing
#         and stop. Do not mention, quote, cite or describe any other figure
#         from the context -- not even to say it does not apply. [...] State
#         only which clause you checked and what it does not contain. Never
#         state what it does contain."
#
#   It closed S-002 and BROKE S-004. Measured on gemini-3.5-flash, n=1
#   (quota), same context:
#
#     S-002 became exemplary -- clauses checked named, nothing quoted:
#       "আমি synthetic_a §9.2.1 এবং synthetic_a §9.1.1 যাচাই করেছি। এই
#        ক্লজগুলোতে জিএম কত টাকা পর্যন্ত ঋণ অনুমোদন করতে পারেন তা উল্লেখ নেই।"
#
#     S-004 (PARTIAL) regressed. Before: "নীলকমল ঋণের সর্বোচ্চ সীমা ৩ লক্ষ
#       টাকা (synthetic_a §9.1.1)। তবে ... মেয়াদ কত, তা প্রদত্ত তথ্যে উল্লেখ
#       নেই।"  After: "synthetic_a ম্যানুয়ালের 9.1.1 এবং 9.2.1 অনুচ্ছেদে
#       নীলকমল ঋণের মেয়াদ উল্লেখ নেই."
#     The ceiling -- in the context, and half the answer -- and its citation
#     both vanished. recall 3/7 -> 2/7, citation 2/2 -> 1/2, violations
#     0/4 -> 0/4, abstention 1/1 -> 1/1.
#
#   Cause: "Never state what it does contain" does not distinguish "the
#   context lacks the answer" from "the context lacks PART of the answer".
#
#   WORDING 3, "SCOPED", 2026-09-17:
#
#     "4. Two cases, and decide which you are in before answering.
#       (a) The context contains NOTHING responsive to the question. Name
#           the clauses you checked and what is missing, then stop. Do not
#           mention, quote, cite or describe any figure from the context --
#           not even to say it does not apply. [...]
#       (b) The context answers PART of the question. Give the part it
#           answers, with its clause, then name the part that is missing.
#           Withholding a correct half is as wrong as inventing one.
#       The test for (a) versus (b): does any figure or statement in the
#       context answer any part of the question as asked? If yes, you are in
#       (b). A figure about a DIFFERENT subject is not a partial answer --
#       it is case (a)."
#
#   NEVER MEASURED ON ANY MODEL. Every Gemini chat model on the key went 429
#   before it could be run, and gemma-4-* return 500 INTERNAL on a
#   system_instruction prompt. Abandoned on inspection rather than on
#   evidence, for a reason worth keeping: (a)/(b) asks the model to
#   CATEGORISE ITS OWN SITUATION, which is the judgement the leakers were
#   demonstrably bad at. A model that reads "জিএম ... ২,৫০,০০০ টাকা" as
#   partially responsive to "জিএম কত টাকা পর্যন্ত ঋণ অনুমোদন করতে পারেন"
#   lands in (b) and is then INSTRUCTED to give the figure with its clause --
#   a wider door than the original "instead", not a narrower one.
#
#   WORDING 4, in force above. Replaces the situational judgement with a
#   MECHANICAL PER-FIGURE TEST: compare each figure's own stated subject to
#   the question's subject, quote only on a match, and handle multi-part
#   questions part by part under the same test. The worked example is the
#   exact S-002 trap, so the rule carries its own counter-example.
#
#   WORDING 4 MEASURED 2026-09-18, AND IT FAILS. Both leakers, one original
#   control run the same day to rule out drift:
#
#     gemini-2.5-flash        original  1 run   viol 1/4  still leaks
#                             v4        1 run   viol 1/4  LEAKS
#     gemini-3-flash-preview  v4        3 runs  viol 1/4  LEAKS 3 of 3
#
#   Four v4 samples across two models, every one leaking, with the same two
#   violations: ৫০,০০০ and ২,৫০,০০০ on S-002. Self-consistent per model, and
#   the narration is near-verbatim identical between runs -- run 2 differed
#   only in writing the clause as §9.2.1 rather than §৯.২.১.
#
#   THE MECHANISM, and it is the reason to stop rewriting this rule. V4 asks
#   the model to run a per-figure subject test. The model runs it, gets the
#   RIGHT answer, and then writes the figure down as its justification:
#
#     "...আমি synthetic_a §৯.২.১ এবং synthetic_a §৯.১.১ ক্লজগুলো যাচাই করেছি।
#      synthetic_a §৯.২.১ ক্লজে জিএম-এর জন্য ২,৫০,০০০ টাকার যে সীমা উল্লেখ আছে
#      তার বিষয়বস্তু হলো \"অফিস সরঞ্জাম ক্রয়\", যা ঋণ অনুমোদনের জন্য প্রযোজ্য
#      নয়।"
#
#   ("its subject is 'office equipment purchase', which is not applicable to
#   loan approval.") The test was applied correctly and the conclusion was
#   correct. Narrating the test requires naming the figure being tested, so
#   the rule's own mechanism puts the forbidden figure in the answer. 2.5-flash
#   went further and added a SECOND clause's content for the same reason.
#
#   GENERALISES: any rule that asks the model to REASON ABOUT which figures
#   are disallowed will tend to surface those figures in the reasoning. Four
#   wordings have now failed in three different ways -- permitted (1),
#   over-suppressed (2), untested but structurally wider (3), and
#   self-defeating (4). A fifth wording was NOT written. The next move is not
#   prompt text: it is to stop the figure reaching the answer somewhere the
#   model does not control -- filter the context before the call so a
#   subject-mismatched figure is never in it, or post-filter the answer
#   against forbidden.json, which already exists and already knows which
#   figures belong to which clause.
#
#   S-004 DOES survive wording 4 -- ceiling and citation present on both
#   leakers, cite 2/2 -- so the over-suppression introduced by wording 2 is
#   fixed. The leak is what is not.
#
#   WORDING 4 RE-MEASURED ON GROQ, 2026-09-18, where quota is not a
#   constraint (1000 req/day/model). Three runs per wording per model:
#
#     openai/gpt-oss-120b   original  CLEAN 3/3      v4  LEAKS 3/3
#     qwen/qwen3.8-27b      original  LEAKS 3/3      v4  LEAKS 3/3
#
#   TWO RESULTS, AND THE FIRST IS THE WORSE ONE.
#
#   1. V4 CREATES LEAKS IN CLEAN MODELS. gpt-oss-120b abstains in one line
#      under the original wording:
#        "প্রশ্নে জিএম-এর ঋণ অনুমোদনের সীমা সম্পর্কে কোনো তথ্য প্রেক্ষাপটে নেই।"
#      Under v4 the same model emits a bulleted audit trail naming BOTH
#      forbidden figures -- ২,৫০,০০০ and ৩ লক্ষ -- one bullet per clause
#      checked. The rule asks for a per-figure test; the model shows its
#      working; the working is the leak.
#
#   2. V4 DOES NOT FIX A LEAKER. qwen3.8-27b leaks identically under both,
#      and under v4 states the rule's own conclusion while violating it:
#        "...তাই এই সংখ্যাটি ঋণ অনুমোদনের প্রশ্নের উত্তরে ব্যবহার করা যাবে না।"
#      ("so this number cannot be used in answering the loan-approval
#      question") -- having already printed the number.
#
#   Ten v4 samples now, four models, EVERY ONE LEAKING: gemini-2.5-flash,
#   gemini-3-flash-preview, gpt-oss-120b, qwen3.8-27b. S-004 stays correct
#   throughout on every model, cite 2/2, so there is no over-suppression
#   left to trade against -- v4 simply does not work.
#
#   THE CONDITION HAS NOW BEEN EVALUATED ON A LEAKER AND FAILED. qwen3.8-27b
#   leaks under the original wording and still leaks under v4, and unlike the
#   Gemini leakers it can be re-tested at will. The warning is no longer
#   standing for want of budget; it stands because the wording was measured
#   against the case it was written for and did not hold.
#
#   WORDING 5, in force above, and DELIBERATELY SMALLER than any of the four
#   before it:
#
#     "4. If the context does not answer the question, say only that the
#         manual does not state it. One sentence. Do not list clauses, do not
#         describe what any clause contains, do not quote any figure."
#
#   THE PATTERN ACROSS ALL FOUR FAILURES. Every wording that asked the model
#   to REASON ABOUT which figures are excluded got the figures named in the
#   reasoning:
#     1 "do not offer ... instead"       -> mentioned as a non-substitute
#     2 "never state what it contains"   -> silenced a correct half-answer
#     3 "(a) vs (b), decide which"       -> asked for self-categorisation
#     4 "name the clauses you checked"   -> a bulleted audit trail, and it
#                                           made gpt-oss-120b leak where the
#                                           ORIGINAL wording had it abstaining
#                                           in one line
#   Wording 4 is the proof: adding a constraint added output. So wording 5
#   REMOVES the instruction to enumerate rather than adding another
#   constraint. "One sentence" is the operative clause; the three "do not"s
#   exist to close the specific doors 1-4 opened.
#
#   WORDING 5 MEASURED ON GROQ, 2026-09-18, 3 runs per model, post-filter on.
#   IT CLOSES THE LEAK -- and introduces two defects the four metrics do not
#   catch.
#
#     openai/gpt-oss-120b   viol 0/4 x3   recall 2/7, 3/7, 2/7   cite 2/2
#     qwen/qwen3.8-27b      viol 0/4 x3   recall 3/7 x3          cite 2/2
#
#   qwen leaks 3/3 under the ORIGINAL wording and 0/3 here, so this is the
#   first wording to hold on a leaker. gpt-oss-120b, which wording 4 broke,
#   is clean again: "ম্যানুয়ালটি জিএম কত টাকা পর্যন্ত ঋণ অনুমোদন করতে পারেন
#   তা উল্লেখ করে না।" -- one sentence, no figure, no clause list.
#
#   DEFECT 1, gpt-oss-120b: S-004 OVER-SUPPRESSED IN 2 OF 3 RUNS. "do not
#   quote any figure" bled into the partial case, so the model asserted the
#   ceiling exists without giving it -- "সর্বোচ্চ সীমা উল্লেখ আছে, তবে
#   মেয়াদ সম্পর্কে ম্যানুয়াল উল্লেখ করে না।" Run 2 gave ৩ লক্ষ টাকা
#   correctly. recall 2/7 vs 3/7 is exactly that literal. Wording 2's failure
#   returning intermittently, at 1-in-3 rather than always.
#
#   DEFECT 2, qwen: ANSWERS IN ENGLISH, 3/3, ON BOTH QUESTIONS. "The manual
#   does not state it." verbatim -- wording 5's own English sentence copied
#   as the output language, against rule 7. S-004: "The manual does not state
#   the মেয়াদ (tenure) ... the সর্বোচ্চ সীমা (maximum limit) is ৩ লক্ষ টাকা."
#   NONE OF THE FOUR METRICS SEES THIS. Violations 0/4, recall 3/7, citation
#   2/2, abstention 1/1 -- a perfect row for an answer a Bengali-speaking
#   field officer cannot use. Rule 7 exists precisely for this and is not
#   scored. If wording 5 is kept, rule 4's text must be phrased so it cannot
#   be mistaken for the answer, and rule 7 needs a metric.
#
#   THE POST-FILTER NEVER FIRED in any of the six runs, because wording 5
#   pre-empted the leak. It is proven only by its canary -- a real captured
#   qwen leak -- not by a live run. That is the right outcome operationally
#   and one fewer confirmation than it looks like.
#
#   ALSO FIXED HERE, and it was a metric bug not a model one: gold_eval's
#   ABSTAIN_BN had no negated-verb form, though ABSTAIN_EN already matched
#   "does not state". Wording 5 steers models to "উল্লেখ করে না", so
#   gpt-oss-120b scored abstention 0/1 on three correct abstentions. The
#   numbers above are after the fix; re-scored from the saved answers, no
#   extra calls.
#
#   WORDING 6, in force above. Wording 5's text DESCRIBED the behaviour by
#   supplying a phrase -- "say only that the manual does not state it" -- and
#   qwen/qwen3.8-27b emitted that sentence verbatim, in English, on a Bengali
#   question, 3 runs of 3:
#
#     S-002: "The manual does not state it."
#     S-004: "The manual does not state the মেয়াদ (tenure) of the নীলকমল ঋণ.
#             According to synthetic_a §9.1.1, the সর্বোচ্চ সীমা (maximum
#             limit) is ৩ লক্ষ টাকা."
#
#   A prompt sentence in English is a template a model may fill rather than a
#   behaviour it describes, and the smaller the rule got the more template-like
#   it became. Wording 6 therefore states the SHAPE -- one sentence, in the
#   question's language, saying the manual does not contain it -- and supplies
#   no phrase to copy. "in the question's language" is also rule 7 restated at
#   the point of failure, because rule 7 alone did not hold here.
#
#   That failure scored a PERFECT FOUR-METRIC ROW: violations 0/4, recall
#   3/7 (the literal-only maximum), citation 2/2, abstention 1/1, on an answer
#   a Bengali-speaking field officer cannot read. gold_eval now reports
#   language_match as a fifth number for exactly this; see report().
#
#   WORDING 6 MEASURED ON GROQ, 2026-09-18, 3 runs per model, post-filter
#   on, 24 calls. IT FIXES THE LANGUAGE DEFECT AND MAKES THE OTHER WORSE.
#
#     openai/gpt-oss-120b  viol 0/4 x3  recall 2/7 x3  cite 1/2  lang 4/4
#     qwen/qwen3.8-27b     viol 0/4 x3  recall 3/7 x3  cite 2/2  lang 4/4
#
#   Both models deterministic: all three runs byte-identical per model.
#
#   FIXED -- DEFECT 2 IS GONE. qwen answers in Bengali 3/3, language_match
#   2/4 -> 4/4:
#     S-002 "সিনথেটিক_এ ম্যানুয়ালে জিএম এর ঋণ অনুমোদনের সীমা উল্লেখ করা নেই।"
#     S-004 "সিনথেটিক_এ ম্যানুয়ালে নীলকমল ঋণের মেয়াদ সম্পর্কে কোনো তথ্য নেই,
#            তবে সর্বোচ্চ সীমা ৩ লক্ষ টাকা (synthetic_a §9.1.1)।"
#   Removing the copyable English phrase removed the copying. That is the one
#   thing wording 6 was written to do, and it did it.
#
#   NOT FIXED, WORSE -- DEFECT 1 WENT FROM 2 OF 3 TO 3 OF 3. gpt-oss-120b
#   withholds the S-004 ceiling in every run:
#     S-004 "ম্যানুয়ালটি নীলকমল ঋণের মেয়াদ সম্পর্কে তথ্য দেয় না।"
#   and the SHAPE degraded too. Under wording 5 the model at least asserted
#   the ceiling existed without giving it; under wording 6 it drops the
#   ceiling entirely, so there is no longer any signal to the reader that the
#   manual holds a figure at all. That also costs the citation: cite 2/2
#   under wording 5, 1/2 here, because a dropped fact is a dropped §9.1.1.
#
#   "in the question's language" plausibly did this. It is the only clause
#   added, it makes rule 4 longer, and wording 4 already established that
#   adding to rule 4 adds suppression. Two defects, one rule, and the fixes
#   pull opposite ways -- which is the argument for the post-filter, not for
#   a seventh wording. NO SEVENTH WORDING WAS WRITTEN.
#
#   NEITHER MODEL LEAKED. violations 0/4 on all six runs, and the post-filter
#   did not fire on any of the 24 calls. qwen leaks 3/3 under the ORIGINAL
#   wording, so wording 6 holds on a leaker exactly as wording 5 did.
#
#   UNCAUGHT BY ALL FIVE METRICS, recorded because language_match was added
#   to catch its neighbour: qwen TRANSLITERATES the manual id in prose --
#   সিনথেটিক_এ for synthetic_a, and §৯.১.১ for §9.1.1 in run 1 -- while its
#   CITATIONS line stays correctly Latin, which is why cite is 2/2 and
#   language_match is 4/4. language_match only checks that Bengali gold-fact
#   literals were not translated OUT of Bengali; a Latin manual id
#   transliterated INTO Bengali is the mirror failure and nothing tests it.
#   An officer searching the manual for সিনথেটিক_এ will not find it.
#
#   AND IT IS STILL NOT THE GUARANTEE. The post-filter below is. Five
#   wordings have now been measured; prompt text is the wrong layer for a
#   hard prohibition, because every phrasing of it is advice the model may
#   comply with in a way that defeats it. The filter does not ask.
#
#   *** RULE 4 IS LOSSY ON PARTIAL ANSWERS. *** Renamed from
#   NOT-SAFE-TO-SHIP, 2026-09-18, because that name named the wrong risk and
#   six wordings of measurement now say what the real one is.
#
#   NOT UNSAFE. The leak is handled below the prompt, by the post-filter,
#   which discards any answer carrying a forbidden figure whatever the model
#   intended. Across wording 6's 24 calls on two models it did not fire once,
#   and violations were 0/4 in all six runs -- the prompt now pre-empts what
#   the filter guarantees. A prompt wording is advice; that filter is not,
#   and it is the reason this is no longer a ship blocker.
#
#   LOSSY, AND BY A MODEL-DEPENDENT AMOUNT. The cost has moved entirely onto
#   PARTIAL answers -- questions where the manual holds one half of what was
#   asked. S-004 is that case: the tenure is absent, the ৩ লক্ষ টাকা ceiling
#   is present and citable.
#
#     openai/gpt-oss-120b  drops the ceiling 3/3, and §9.1.1 with it
#                          recall 2/7   cite 1/2
#     qwen/qwen3.8-27b     keeps both 3/3
#                          recall 3/7   cite 2/2
#
#   A dropped fact is a dropped citation: gpt-oss gives no signal that the
#   manual holds a figure at all, so there is nothing left to cite.
#
#   SIX WORDINGS COULD NOT FIX BOTH DEFECTS, and the reason is structural
#   rather than a matter of finding better words. Shrinking rule 4 invites
#   COPYING -- wording 5 supplied the English sentence "say only that the
#   manual does not state it" and qwen emitted it verbatim, in English, on a
#   Bengali question, 3/3. Growing rule 4 SUPPRESSES FACTS -- wording 2
#   silenced a correct half-answer, wording 4 produced a bulleted audit trail
#   that leaked in every model tested, and wording 6's one added clause
#   ("in the question's language") took gpt-oss-120b from 2/3 to 3/3
#   over-suppression. The two defects pull in opposite directions through the
#   same sentence, so no single wording holds both ends. THAT is the finding.
#   No seventh wording was written.
#
#   DEFAULT MODEL: qwen/qwen3.8-27b, first in GROQ_PREFERENCE, chosen for
#   this reason -- it is the model that does not silently drop a fact the
#   manual contains, and a missing figure is indistinguishable to the reader
#   from a figure the manual never had.
#
#   AND THE COUNTERWEIGHT, recorded so the choice stays legible: qwen is the
#   WORSE model on rule 7. Under the mirror check added to language_match it
#   scores 1/4 against gpt-oss-120b's 3/4, because it transliterates manual
#   ids into Bengali prose (সিনথেটিক_এ for synthetic_a) in 3 of 4 answers.
#   That is a defect in a reference an officer must look up, and it is traded
#   knowingly against dropped facts, which are worse. Both models write the
#   clause number in Bengali digits on S-003, so neither is clean.
#
#   STILL OPEN, and not a blocker: the rule-4 trade-off has only ever been
#   measured on the 4 synthetic questions. The partial-answer case is a
#   single question, S-004, on one leaking model and one clean one. A second
#   partial question would tell us whether the split above is about the
#   models or about that question.
#
# Rule 5 -- prevents: implying availability. products_catalogue.csv has 43
#   rows and 0 populated status values, so every status lookup returns
#   "unknown" today. Unknown is not active, and an answer that quotes terms
#   without the status reads as an offer.
# Rule 6 -- prevents: silently picking a programme. Where the manuals
#   diverge, answering for one is wrong half the time with no signal to the
#   reader; §3.9.6's bill-voucher approver is শাখা ব্যবস্থাপক in Dabi and
#   এলাকা ব্যবস্থাপক in Progoti. Since 2026-10-26 it answers for each case up
#   to three and asks only beyond that -- the same limit as rule 8, so the
#   two rules cannot pull an answer in opposite directions.
# Rule 7 -- prevents: an unactionable answer. An officer told "the Area
#   Manager approves" cannot act on it; the form they need on the paperwork
#   is এএম. Translating a role out of Bengali destroys its usefulness while
#   looking helpful.
# Rule 8 -- prevents: one case given as the whole answer. Added 2026-10-26.
#   Asked "দাবি কর্মসূচিতে শাখা ব্যবস্থাপক কত টাকা পর্যন্ত নতুন ঋণ অনুমোদন
#   করেন?", the live chatbot answered ৭০,০০০ -- the high-demand branches'
#   ceiling -- and stopped; ordinary-demand branches' is ৬০,০০০, in the same
#   table. Each figure was right and the answer was wrong for half the
#   branches, with nothing on the page to say a second case existed. Rule 6
#   still applies when the question leaves the programme open: asking which
#   one is meant is allowed, giving only one case is not.
#
#   THE "EVEN WHEN" CLAUSE IS THE WORKING PART. The first wording -- "give
#   every case and say which figure belongs to which" -- left that answer
#   unchanged: the model read "কত টাকা পর্যন্ত" as a request for the overall
#   maximum, which ৭০,০০০ is. Naming that case moved it to both branch types,
#   labelled (1 of 1, gpt-oss-120b, 2026-10-26). A rule cannot supply a case
#   retrieval did not bring: asked what savings a member deposits before a
#   loan, the context held only Progoti's ৪%/২%, and the answer gave those.
#   UP TO THREE, THEN ASK: beyond three cases a list stops being an answer
#   an officer can use at the counter, and rule 6 uses the same limit.
# Rule 9 -- prevents: the unlabelled figure. That same ৪%/২% answer named no
#   programme, so it read as the rule for every member; the group-based
#   figure is ৫%. The heading line is in every context block, and it is the
#   only place some chunks say which programme they are; where it names none
#   (§1.2.2's savings tables), the manual is the label.

# --------------------------------------------------------------- the floor
# SET FROM THE DATA, and the data says a floor cannot do the job asked of it.
#
# Measured over the 23 answerable, recall-scoreable questions in both gold
# files, top fused score, split by whether the gold chunk is anywhere in
# top-5 (out/retrieved.jsonl, k=5):
#
#                      n    min     p25   median    p75     max    mean
#   gold RETRIEVED    14  0.4044  0.5124  0.5567  0.6111  0.7873  0.5697
#   gold NOT retrieved 9  0.4181  0.5338  0.5698  0.7199  0.7686  0.6055
#
# The misses score HIGHER on average than the hits. Lowest score with a hit
# is 0.4044; highest score with a miss is 0.7686. Not separable: at every
# candidate threshold, the good questions lost are >= the bad ones caught.
#   floor 0.5371 -> 5 good lost, 3 bad caught
#   floor 0.5698 -> 9 good lost, 5 bad caught
#   floor 0.7199 -> 12 good lost, 6 bad caught
#
# So the floor is set to the lowest fused score at which a gold chunk was
# actually retrieved. On this sample it fires on NOTHING, deliberately: any
# higher value discards more answerable questions than it protects. It still
# guards the degenerate case -- an empty retrieval, or a query whose scores
# collapse toward zero -- which is the only case the data supports guarding.
#
# NOT a round number, and not a tuned one. Re-derive it when the corpus or
# the weighting changes; n=14/9 is small and the overlap may be an artefact
# of a retriever that is currently missing section-head chunks (Phase 12).
SCORE_FLOOR = 0.4044

# ------------------------------------------------------------- model choice
# DISCOVERED, NOT HARDCODED. models.list() on this key returns names newer
# than this file: gemini-3.8-flash was live on 2026-09-17. A hardcoded list
# would silently pin the project to whatever was current when it was typed.
# Preference order, falling through on 503 or 404:
MODEL_PREFERENCE = ("gemini-flash-latest", "gemini-2.5-flash")

# DO NOT USE ANY *-flash-lite MODEL FOR GENERATION. Measured, not assumed:
# given a synthetic Bengali question with its context inline,
# gemini-flash-lite-latest replied
#   "দয়া করে তথ্যটি দিন, কারণ আপনার বার্তায় কোনো তথ্য উল্লেখ করা হয়নি"
# -- it denied having context that was in the prompt. That scores as a FALSE
# ABSTENTION on an answerable question, which is the worst possible failure
# here: it makes rule 4 look like it is working when the model simply is not
# reading the prompt. gemini-flash-latest answered the same prompt correctly,
# copying ৩ লক্ষ টাকা and শাখা ব্যবস্থাপক verbatim. Anyone "optimising" to
# Lite for cost will improve abstention accuracy and destroy fact recall.
LITE_MARKERS = ("flash-lite", "lite-latest")

# NOT GENERAL TEXT GENERATORS. models.list() returns image, speech, music,
# research-agent and embedding models alongside the chat ones, and they are
# listed as supporting generateContent. Falling through to them is worse than
# failing: measured, the judge's reachability probe walked
# antigravity-preview-05-2026, deep-research-*, and three more before its cap,
# and never reached the one reachable chat model. Ordering fixes the fallthrough
# as well as the judge.
SPECIALTY_MARKERS = ("image", "tts", "audio", "embedding", "deep-research",
                     "antigravity", "lyria", "nano-banana", "veo", "imagen",
                     "omni", "customtools", "robotics", "computer-use")

# THE FREE-TIER BUDGET IS 20 REQUESTS PER DAY PER MODEL, and knowing that
# changes how any experiment here must be planned. Measured 2026-09-17 from
# the full 429 body, which the truncated log line hides:
#
#   quotaId:    GenerateRequestsPerDayPerProjectPerModel-FreeTier
#   quotaValue: 20
#   metric:     generativelanguage.googleapis.com/
#               generate_content_free_tier_requests
#   retryDelay: 58s          <- MISLEADING. It is a generic backoff hint, not
#                               the time until a per-DAY quota resets.
#
# Consequences, all learned the hard way across one session:
#   * One synthetic run is 4 questions = 4 calls. So FIVE runs per model per
#     day, maximum. A 3-run before/after on one model is 24 calls and does
#     not fit; it must be split across days or across models.
#   * A run either fully succeeds or yields NOTHING. answer_one raises on the
#     first provider failure, so a run starting with 2 calls left writes no
#     answers at all. Partial budgets are wasted budgets.
#   * Every probe costs 1 of the 20 -- a quarter of a run. quota_report over
#     13 models spends 13 calls, i.e. three runs' worth. probe_model caches
#     per process for exactly this reason, and a reachability probe should be
#     run once per model per day, not per invocation.
#   * models.list() is FREE and unlimited. Reachability is not.
#
# Budget an experiment before running it: (runs x 4) + 1 probe <= 20.
RETRY_STATUS = (429, 503)      # rate limited, and transiently unavailable

# TRANSPORT failures are retryable too, and they carry no HTTP status at all.
# Measured on the first synthetic run: gemini-flash-latest raised
# httpx.RemoteProtocolError("Server disconnected without sending a response")
# on S-003. With only the status codes above, that fell straight through to
# the next model on attempt 1 -- a dropped connection is at least as transient
# as a 503, and falling through hides it as "the preferred model failed".
RETRY_ERRORS = ("RemoteProtocolError", "ConnectError", "ConnectTimeout",
                "ReadTimeout", "ReadError", "WriteError", "PoolTimeout",
                "TimeoutException", "ServerDisconnectedError")


def _retryable(exc, status):
    return status in RETRY_STATUS or type(exc).__name__ in RETRY_ERRORS
MAX_ATTEMPTS = 5
BACKOFF_BASE = 1.7

KEY_ENV = {"gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
           "groq": "GROQ_API_KEY"}
KEY_HELP = {
    "gemini": "https://aistudio.google.com/apikey",
    "anthropic": "https://console.anthropic.com/settings/keys",
    "groq": "https://console.groq.com/keys",
}

# GROQ. Measured 2026-09-18 on a Bengali fidelity probe: gpt-oss-120b,
# gpt-oss-20b and qwen3.8-27b all copied ৩ লক্ষ and শাখা ব্যবস্থাপক verbatim
# out of inline context, with no ASCII conversion and no denial of context --
# indistinguishable from the gemini-2.5-flash baseline on all three checks.
#
# WHY IT IS THE ONLY FREE PROVIDER THAT CAN RUN THIS EVAL. From the response
# headers, not the documentation:
#     x-ratelimit-limit-requests: 1000     per DAY, and PER MODEL
#     x-ratelimit-limit-tokens:   8000     per MINUTE (reset-tokens ~547ms)
# Per-model is demonstrated, not assumed: after exactly 2 calls to each of
# three models, all three reported remaining-requests 998. A shared bucket
# would have counted 998 -> 997 -> 996.
#
# A 39-question run is 77,904 tokens (measured, out/retrieved.jsonl), so:
#     requests:   1000 / 39  = 25 complete runs per day per model
#     throughput: 77904/8000 = ~10 minutes minimum per run
# Against Gemini's 20 requests/day/model = 0.51 runs/day. Roughly 49x.
#
# Cerebras was tested at the same time and returns 402 payment_required --
# no free tier on that key -- so it gets no backend.
# qwen FIRST, and the reason is measured, not a guess: on a partial answer
# gpt-oss-120b withholds the figure the manual does contain (S-004, 3 of 3
# runs, recall 2/7 cite 1/2) while qwen gives it (3/7, 2/2). See "RULE 4 IS
# LOSSY ON PARTIAL ANSWERS" above, including what this trades away -- qwen is
# the worse model on rule 7's identifier check.
GROQ_PREFERENCE = ("qwen/qwen3.8-27b", "openai/gpt-oss-120b",
                   "openai/gpt-oss-20b")


# MEASURED, not documented by the provider. Groq's free tier refused a request
# with "on tokens per day (TPD): Limit 200000" for openai/gpt-oss-120b on
# 19 Sep 2026. Per model, like the request limit: qwen's bucket was untouched
# while gpt-oss's was exhausted. Keyed by name so a model with a different
# limit can be added when one is observed rather than guessed.
TPD_LIMIT = {"groq": 200_000}
TPD_WARN_AT = 0.80

# The calibrated character-to-token ratio, measured against billed
# prompt_tokens over the 17-question k=5 run: 113,592 chars / 44,059 tokens.
# Predicting that run forward from it lands within 0.02%. The earlier 1.666
# over-stated every absolute by 55%; see the journal's token-proxy note.
CHARS_PER_TOKEN = 2.578

LEDGER = Path("out/token_ledger.json")


def _today():
    """UTC date. The provider's day is not this machine's timezone, and
    guessing wrong shifts the whole ledger by up to a day."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def ledger_read(path=LEDGER):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def ledger_used(provider, model, path=LEDGER):
    """Tokens already spent today on this provider:model. -> int"""
    return int((ledger_read(path).get(_today()) or {})
               .get(f"{provider}:{model}", 0))


def ledger_add(provider, model, tokens, path=LEDGER):
    """Add `tokens` to today's total. Called after a call, never on import."""
    if not tokens:
        return ledger_used(provider, model, path)
    doc = ledger_read(path)
    day = doc.setdefault(_today(), {})
    key = f"{provider}:{model}"
    day[key] = int(day.get(key, 0)) + int(tokens)
    # Keep the last 14 days; a ledger nobody prunes becomes a file nobody
    # opens.
    for old in sorted(doc)[:-14]:
        doc.pop(old, None)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")
    return day[key]


def estimate_run_tokens(rows, system=None, reserve=1024):
    """What this run will cost, in billed tokens. -> int

    Prompt characters over the calibrated ratio, plus the reserved completion
    budget per question -- `reserve` counts against the limit whether or not
    the model uses it, which is why a 7,131-token prompt was refused while a
    7,220-token one went through at k=20.
    """
    sysn = len(system if system is not None else SYSTEM_PROMPT)
    total = 0
    for r in rows:
        chunks = r.get("chunks") or []
        if not chunks:
            continue
        user = build_user_prompt(r.get("question_bn", ""), chunks, False)
        total += int((sysn + len(user)) / CHARS_PER_TOKEN) + reserve
    return total


def tpd_preflight(provider, model, rows, system=None, out=print):
    """-> (ok, message). Refuse a run that cannot finish in today's budget.

    Returns rather than raising so the caller decides; main() treats False as
    a hard stop, because the alternative is spending the remainder to produce
    nothing.
    """
    limit = TPD_LIMIT.get(provider)
    if not limit:
        return True, f"no TPD limit recorded for provider {provider!r}"
    used = ledger_used(provider, model)
    left = limit - used
    need = estimate_run_tokens(rows, system)
    pct = used / limit
    msg = (f"token budget {provider}:{model} -- used {used:,}/{limit:,} "
           f"({pct:.0%}) today, {left:,} left; this run needs ~{need:,}")
    if need > left:
        return False, (
            msg + "\n  REFUSING TO START. A run that runs out part-way "
            "writes NO answers at all (answer_one raises on the first "
            "provider failure), so starting would spend the remainder and "
            "produce nothing. Wait for the day to roll over, or use a "
            "different model -- the limit is per model.")
    if pct >= TPD_WARN_AT:
        out(f"  WARNING: {msg}")
    else:
        out(f"  {msg}")
    return True, msg


class ProviderError(RuntimeError):
    pass


class MissingKey(ProviderError):
    pass


class ReplyTruncated(ProviderError):
    """The reply hit the output limit before any text was written. A
    ProviderError, so every caller that handled the old error still does;
    complete_guarded() catches it and retries with a higher limit."""


def load_key(provider):
    """Read the key from the environment AFTER loading .env. Never a literal,
    never a default -- a default key is a key in git."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    var = KEY_ENV.get(provider)
    if not var:
        raise ProviderError(f"no key variable defined for provider {provider!r}")
    key = os.environ.get(var)
    if not key:
        raise MissingKey(
            f"{var} is not set.\n"
            f"  Put it in .env as   {var}=...\n"
            f"  Get one at          {KEY_HELP.get(provider, '(see provider)')}\n"
            f"  .env is gitignored; do not pass the key on the command line, "
            f"it lands in shell history.")
    return key


def _status_of(exc):
    """HTTP-ish status from a provider exception, or None. Providers disagree
    on where they put it, so look in several places before parsing text."""
    for attr in ("code", "status_code", "http_status"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    resp = getattr(exc, "response", None)
    v = getattr(resp, "status_code", None)
    if isinstance(v, int):
        return v
    text = str(exc)
    for s in RETRY_STATUS + (404,):
        if str(s) in text:
            return s
    if "UNAVAILABLE" in text.upper():
        return 503
    if "RESOURCE_EXHAUSTED" in text.upper():
        return 429
    return None


def _parse_reset(v):
    """Groq reset headers -> seconds. Formats seen: '547ms', '8.5s',
    '2m52.8s', '1m', '2h3m0s'. Returns None when unparseable, so a caller
    falls back to its own backoff rather than sleeping on a guess."""
    if not v:
        return None
    t = str(v).strip()
    if t.endswith("ms"):
        try:
            return float(t[:-2]) / 1000.0
        except ValueError:
            return None
    total, num = 0.0, ""
    for ch in t:
        if ch.isdigit() or ch == ".":
            num += ch
            continue
        if not num:
            return None
        mult = {"h": 3600.0, "m": 60.0, "s": 1.0}.get(ch)
        if mult is None:
            return None
        total += float(num) * mult
        num = ""
    return total if not num else None


# The token limit is PER MINUTE, so a long run hits it mid-way rather than
# failing up front. Pace from the headers the API already returns instead of
# sleeping a fixed amount: the server knows how much is left and when it
# refills, and a guess is either too slow or still 429s.
TPM_HEADROOM = 1.15        # ask for 15% more than the last call actually used


def _pace_from_headers(state, need_tokens):
    """Sleep only if the remaining token budget cannot cover `need_tokens`.
    -> seconds slept."""
    h = state.get("headers") or {}
    rem = h.get("x-ratelimit-remaining-tokens")
    reset = _parse_reset(h.get("x-ratelimit-reset-tokens"))
    try:
        rem = int(rem)
    except (TypeError, ValueError):
        return 0.0
    if rem >= need_tokens or reset is None:
        return 0.0
    nap = min(reset + 0.25, 65.0)     # never sleep longer than one TPM window
    time.sleep(nap)
    return nap


def _sleep_for(attempt):
    """Exponential backoff with jitter. Jitter matters on a shared free tier:
    synchronised retries from several runs re-create the burst that caused
    the 429."""
    return (BACKOFF_BASE ** attempt) + random.uniform(0, 0.75)


# ------------------------------------------------------------- the one door
# THE ONLY FUNCTION THAT KNOWS A PROVIDER OR A MODEL. Everything else takes
# (text, meta). Grep for a model name outside this section: there should be
# none, except the comments that explain which models were measured.
def _gemini_models(key):
    """Listed models that support generateContent, Lite excluded, in
    preference order first and then whatever else the key can see."""
    from google import genai
    client = genai.Client(api_key=key)
    listed = []
    for m in client.models.list():
        acts = getattr(m, "supported_actions", None) or []
        if "generateContent" not in acts:
            continue
        name = m.name.split("/")[-1]
        if any(mark in name for mark in LITE_MARKERS):
            continue
        if any(mark in name for mark in SPECIALTY_MARKERS):
            continue
        listed.append(name)
    ordered = [m for m in MODEL_PREFERENCE if m in listed]
    # then plain flash models, then everything else. Newest-looking first
    # within each group, so a fallthrough lands on a current chat model rather
    # than the alphabetically-first thing the key happens to expose.
    rest = [n for n in listed if n not in ordered]
    flash = sorted((n for n in rest if "flash" in n), reverse=True)
    other = sorted((n for n in rest if "flash" not in n), reverse=True)
    return client, ordered + flash + other


def _gemini_complete(system, user, cache_system, key, state):
    from google.genai import types
    client, models = state.get("client"), state.get("models")
    if client is None:
        client, models = _gemini_models(key)
        state["client"], state["models"] = client, models
    pin = _STATE.get("pin")
    if pin:
        # Listed check, then pin. A pinned name that the key cannot see is a
        # typo or a withdrawn model, and reporting it beats answering from a
        # different one.
        if pin not in models:
            raise ProviderError(
                f"pinned model {pin!r} is not in the {len(models)} listed "
                f"generateContent models for this key "
                f"(Lite models are excluded by policy). Listed: "
                f"{models[:6]}...")
        models = [pin]
    if not models:
        raise ProviderError("no listed model supports generateContent")

    retries = 0
    tried = []
    for model in models:
        for attempt in range(MAX_ATTEMPTS):
            try:
                cfg = types.GenerateContentConfig(
                    system_instruction=system, temperature=0.0)
                r = client.models.generate_content(
                    model=model, contents=user, config=cfg)
                um = getattr(r, "usage_metadata", None)
                cand = (getattr(r, "candidates", None) or [None])[0]
                fr = str(getattr(cand, "finish_reason", "") or "")
                return (r.text or ""), {
                    "model": model, "retries": retries, "tried": tried,
                    "prompt_tokens": getattr(um, "prompt_token_count", None),
                    "output_tokens": getattr(um, "candidates_token_count", None),
                    "finish_reason": ("length" if "MAX_TOKENS" in fr
                                      else (fr or None)),
                    # cache_system is accepted and recorded but not yet wired:
                    # implicit caching needs a token floor this prompt is far
                    # under, and explicit caches cost more to create than they
                    # save at 39 questions. Recorded so a later run can tell
                    # whether caching was on.
                    "system_cached": False,
                }
            except Exception as e:                       # noqa: BLE001
                st = _status_of(e)
                if _retryable(e, st) and attempt < MAX_ATTEMPTS - 1:
                    retries += 1
                    time.sleep(_sleep_for(attempt))
                    continue
                tried.append({"model": model, "status": st,
                              "error": type(e).__name__, "detail": str(e)[:160]})
                break                                   # fall through
    raise ProviderError(f"every model failed; tried {tried}")


def _anthropic_complete(system, user, cache_system, key, state):
    try:
        import anthropic
    except ImportError as e:
        raise ProviderError(
            "the anthropic package is not installed in this venv; "
            "pip install anthropic, or use --provider gemini") from e
    client = state.get("client") or anthropic.Anthropic(api_key=key)
    state["client"] = client
    models = state.get("models")
    if models is None:
        models = [m.id for m in client.models.list(limit=50).data]
        state["models"] = [m for m in models
                           if not any(k in m for k in LITE_MARKERS)]
        models = state["models"]
    pin = _STATE.get("pin")
    if pin:
        if pin not in models:
            raise ProviderError(f"pinned model {pin!r} not listed for this key")
        models = [pin]
    retries = 0
    tried = []
    for model in models:
        for attempt in range(MAX_ATTEMPTS):
            try:
                sys_block = [{"type": "text", "text": system}]
                if cache_system:
                    sys_block[0]["cache_control"] = {"type": "ephemeral"}
                r = client.messages.create(
                    model=model, max_tokens=1024, system=sys_block,
                    messages=[{"role": "user", "content": user}])
                u = getattr(r, "usage", None)
                sr = getattr(r, "stop_reason", None)
                return "".join(b.text for b in r.content
                               if getattr(b, "type", "") == "text"), {
                    "model": model, "retries": retries, "tried": tried,
                    "prompt_tokens": getattr(u, "input_tokens", None),
                    "output_tokens": getattr(u, "output_tokens", None),
                    "finish_reason": "length" if sr == "max_tokens" else sr,
                    "system_cached": bool(cache_system),
                }
            except Exception as e:                       # noqa: BLE001
                st = _status_of(e)
                if _retryable(e, st) and attempt < MAX_ATTEMPTS - 1:
                    retries += 1
                    time.sleep(_sleep_for(attempt))
                    continue
                tried.append({"model": model, "status": st,
                              "error": type(e).__name__, "detail": str(e)[:160]})
                break
    raise ProviderError(f"every model failed; tried {tried}")


def _groq_complete(system, user, cache_system, key, state,
                   max_tokens=1024, reasoning_effort=None):
    """Groq. Same retry classification as gemini, plus TPM pacing.

    cache_system is accepted and recorded but not wired: Groq exposes no
    prompt-cache control on this tier, so claiming it were cached would put a
    false field in the run metadata.
    """
    from groq import Groq
    client = state.get("client")
    if client is None:
        client = Groq(api_key=key)
        state["client"] = client

    pin = _STATE.get("pin")
    models = [pin] if pin else list(GROQ_PREFERENCE)

    retries, paced, tried = 0, 0.0, []
    for model in models:
        for attempt in range(MAX_ATTEMPTS):
            try:
                # Pace BEFORE the call, using what the last call reported
                # left. need = the last observed total, with headroom; on the
                # first call there is nothing to go on, so it does not sleep.
                need = int(state.get("last_total", 0) * TPM_HEADROOM)
                paced += _pace_from_headers(state, need)
                extra = {}
                if reasoning_effort:
                    extra["reasoning_effort"] = reasoning_effort
                raw = client.chat.completions.with_raw_response.create(
                    model=model, temperature=0.0, max_tokens=max_tokens,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}],
                    **extra)
                state["headers"] = {k.lower(): v
                                    for k, v in raw.headers.items()}
                r = raw.parse()
                u = getattr(r, "usage", None)
                pt = getattr(u, "prompt_tokens", None)
                ct = getattr(u, "completion_tokens", None)
                state["last_total"] = (pt or 0) + (ct or 0)
                h = state["headers"]
                body = r.choices[0].message.content or ""
                # THE SILENT TRUNCATION. gpt-oss spends reasoning tokens
                # before it writes anything, and they count against
                # max_tokens. Hit the cap mid-reasoning and content comes
                # back EMPTY with finish_reason "stop" -- indistinguishable
                # from a model that chose to say nothing. Say so instead.
                if not body.strip() and ct and ct >= max_tokens:
                    raise ReplyTruncated(
                        f"{model} returned no content: completion_tokens "
                        f"{ct} hit max_tokens {max_tokens}, so the reply was "
                        f"truncated during reasoning. Raise max_tokens or "
                        f"pass reasoning_effort='low'.")
                return body, {
                    "model": model, "retries": retries, "tried": tried,
                    "prompt_tokens": pt, "output_tokens": ct,
                    "max_tokens": max_tokens,
                    # "length" = stopped at max_tokens: complete_guarded()
                    # must never let that partial text reach a reader
                    "finish_reason": r.choices[0].finish_reason,
                    "reasoning_effort": reasoning_effort,
                    "system_cached": False,
                    "paced_seconds": round(paced, 2),
                    "tpm_remaining": h.get("x-ratelimit-remaining-tokens"),
                    "rpd_remaining": h.get("x-ratelimit-remaining-requests"),
                }
            except Exception as e:                           # noqa: BLE001
                st = _status_of(e)
                hdrs = getattr(getattr(e, "response", None), "headers", None)
                if hdrs:
                    state["headers"] = {k.lower(): v for k, v in hdrs.items()}
                if _retryable(e, st) and attempt < MAX_ATTEMPTS - 1:
                    retries += 1
                    # A 429 here is usually TPM, not the daily cap. Prefer the
                    # server's own reset window over exponential backoff.
                    reset = _parse_reset(
                        (state.get("headers") or {}).get(
                            "x-ratelimit-reset-tokens"))
                    nap = (min(reset + 0.25, 65.0) if reset is not None
                           else _sleep_for(attempt))
                    time.sleep(nap)
                    paced += nap
                    continue
                tried.append({"model": model, "status": st,
                              "error": type(e).__name__,
                              "detail": str(e)[:160]})
                break
    raise ProviderError(f"every model failed; tried {tried}")


def _local_complete(system, user, cache_system, key, state):
    # Would need: a served instruction-tuned model with real Bengali coverage
    # (the corpus is 100% decomposed-nukta Unicode, so a tokeniser that mangles
    # য়/ড়/ঢ় is disqualified), reachable over an OpenAI-compatible /chat
    # endpoint on localhost so no chunk leaves the machine -- which is the
    # whole reason this backend exists.
    raise NotImplementedError(
        "local provider is a stub: no served model is configured. See the "
        "comment above for what it would need.")


_BACKENDS = {"gemini": _gemini_complete,
             "groq": _groq_complete,
             "anthropic": _anthropic_complete,
             "local": _local_complete}
_STATE = {"provider": None, "pin": None, "cache": {}}


def complete(system, user, cache_system=True, **opts):
    """-> (text, meta). The only function that knows a provider or a model.

    Provider comes from configure(); meta records the model actually used,
    the retry count, what was tried and abandoned, and token counts, so a
    score can be traced to the run that produced it.
    """
    provider = _STATE["provider"]
    if provider is None:
        raise ProviderError("call configure(provider) before complete()")
    key = None if provider == "local" else load_key(provider)
    state = _STATE["cache"].setdefault(provider, {})
    fn = _BACKENDS[provider]
    if opts:
        # opts are BACKEND-SPECIFIC (max_tokens and reasoning_effort are
        # groq's). Passing one to a backend that does not take it should say
        # so, not raise TypeError from three frames down.
        import inspect
        ok = set(inspect.signature(fn).parameters)
        bad = [k for k in opts if k not in ok]
        if bad:
            raise ProviderError(
                f"provider {provider!r} does not accept {bad}; "
                f"it takes {sorted(ok - {'system', 'user', 'cache_system', 'key', 'state'})}")
    text, meta = fn(system, user, cache_system, key, state, **opts)
    meta["provider"] = provider
    # THE LEDGER IS WRITTEN HERE, at the one door every call goes through, so
    # a new backend cannot forget to account for itself.
    spent = (meta.get("prompt_tokens") or 0) + (meta.get("output_tokens") or 0)
    if spent and meta.get("model"):
        try:
            meta["tpd_used_today"] = ledger_add(provider, meta["model"], spent)
        except OSError as e:          # a ledger that cannot be written must
            meta["tpd_ledger_error"] = str(e)   # not take the run down
    return text, meta


def configure(provider, pin_model=None):
    """Set the provider, and optionally PIN one model.

    A pinned model disables the preference order AND the fallthrough: if it
    fails, the run fails and says so. That is the point of pinning -- an
    experiment comparing two models cannot have one of them silently answered
    by the other, which is exactly what happened on the first synthetic run.
    Transient retries still apply; only the fallthrough is suppressed.
    """
    if provider not in _BACKENDS:
        raise ProviderError(f"unknown provider {provider!r}; "
                            f"choose from {sorted(_BACKENDS)}")
    _STATE["provider"] = provider
    _STATE["pin"] = pin_model
    _STATE["cache"].setdefault(provider, {})
    return provider


# --------------------------------------------------------------- the corpus
CORPUS_GUARD_MSG = (
    "CORPUS GUARD: refusing to send real manual chunks to provider {p!r}.\n"
    "  The chunks are BRAC internal documents and the free tier may use\n"
    "  inputs for model training; the data-protection decision is pending.\n"
    "  Either:\n"
    "    --retrieved out/synthetic_retrieved.jsonl    (invented content)\n"
    "    --provider local                             (nothing leaves the box)\n"
    "    --allow-corpus \"<reason>\"                    (recorded in the run "
    "metadata)")


def is_synthetic(path):
    """A dump is synthetic when every chunk says so. Decided by the file, not
    by its name: renaming the real dump must not disarm the guard."""
    rows = read_retrieved(path)
    if not rows:
        return False
    return all(c.get("synthetic") is True
               for r in rows for c in r["chunks"]) and bool(rows)


def check_corpus_guard(path, provider, allow_reason):
    """-> guard state string, or raises ProviderError."""
    if provider == "local":
        return "not-applicable-local"
    if is_synthetic(path):
        return "synthetic-context"
    if allow_reason:
        return f"overridden: {allow_reason}"
    raise ProviderError(CORPUS_GUARD_MSG.format(p=provider))


# ------------------------------------------------------------------- the IO
def read_retrieved(path):
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def format_context(chunks):
    """The context block. One chunk per section, each labelled with the
    manual and clause the model is required to cite."""
    out = []
    for i, c in enumerate(chunks, 1):
        head = " > ".join(c.get("heading_path") or [])
        out.append(
            f"--- CONTEXT {i}\n"
            f"manual_id: {c['manual_id']}\n"
            f"clause: {c.get('clause') or '(none)'}\n"
            f"heading: {head}\n"
            f"text: {c['display_text']}")
    return "\n\n".join(out)


# ------------------------------------------------------------- glossary
# MEASURED, NOT ADOPTED. Off unless --glossary is passed.
#
# WHY IT MIGHT HELP. roles.py records সিডিও = Credit Development Officer and
# পিও = Programme Organiser, and the prompt has never seen the registry. On
# G-222 gpt-oss-120b invented "সিনিয়র ডিস্ট্রিবিউশন অফিসার" and "পোস্ট
# অফিসার" -- fabricated expansions for two terms the repo already knows.
#
# WHY IT MIGHT HURT. A glossary is more context to quote from, and rule 4's
# entire history is models reciting adjacent material. Hence the measurement
# includes whether an answer glosses a term the question never asked about.
#
# NO LITERAL IS TYPED HERE, per Convention 2: every term and expansion is
# read out of roles.py and productmatch, and presence is tested with
# bn_shared.count_literal so the owning registry's exclusions apply -- সিডিপি
# is not matched inside এনসিডিপি, পিএ not inside পিএলসি.
_GLOSS_TERMS = None


def glossary_terms():
    """[(bengali_term, expansion, manual_or_None)] from the registries.

    Roles carry a per-manual form: where the Dabi and Progoti columns differ,
    the term itself says which manual the asker works under, and that is
    recorded rather than flattened -- roles.py's own note for field_officer
    says "do not expand one to the other".
    """
    global _GLOSS_TERMS
    if _GLOSS_TERMS is not None:
        return _GLOSS_TERMS
    terms = []
    try:
        import roles
        for row in getattr(roles, "ROLES", []):
            if len(row) < 5:
                continue
            dabi, progoti, acronym, english = row[1], row[2], row[3], row[4]
            if not (english or "").strip():
                continue
            seen = {}
            if (dabi or "").strip():
                seen[dabi.strip()] = "dabi_2025" if dabi != progoti else None
            if (progoti or "").strip():
                seen.setdefault(progoti.strip(),
                                "progoti" if dabi != progoti else None)
            if (acronym or "").strip() and "/" not in acronym:
                seen.setdefault(acronym.strip(), None)
            for t, man in seen.items():
                terms.append((t, english.strip(), man))
    except ImportError:
        pass
    try:
        import productmatch as pm
        for row in getattr(pm, "ENTITIES", []):
            if len(row) < 4:
                continue
            bn, desc = row[2], row[3]
            if (bn or "").strip() and (desc or "").strip():
                terms.append((bn.strip(), desc.split(";")[0].strip(), None))
        for _k, pair in (getattr(pm, "PROGRAMME_NAMES", {}) or {}).items():
            bn = pair[0] if isinstance(pair, (list, tuple)) else None
            al = pair[1] if isinstance(pair, (list, tuple)) and len(pair) > 1 \
                else ""
            if bn and al:
                terms.append((bn.strip(), al.split(";")[0].strip(), None))
    except ImportError:
        pass
    # longest first, so a longer term is offered before a shorter one it
    # contains, and de-duplicated on the Bengali term
    out, have = [], set()
    for t, e, m in sorted(terms, key=lambda x: -len(x[0])):
        if t not in have:
            have.add(t)
            out.append((t, e, m))
    _GLOSS_TERMS = out
    return out


def glossary_block(chunks):
    """A glossary for ONLY the terms present in these chunks. -> str or ''

    Never the whole registry: 40-odd entries of irrelevant vocabulary is both
    a token cost and more material to misquote.
    """
    from bn_shared import count_literal
    texts = [c.get("display_text") or "" for c in chunks]
    if not any(texts):
        return ""
    lines = []
    for term, expansion, manual in glossary_terms():
        occ, _n = count_literal(term, texts, warn=False)
        if occ:
            where = f" [{manual}]" if manual else ""
            lines.append(f"{term} = {expansion}{where}")
    if not lines:
        return ""
    return ("--- GLOSSARY (terms appearing above; expansions only, not new "
            "facts)\n" + "\n".join(lines))


def build_user_prompt(question, chunks, glossary=False):
    gloss = glossary_block(chunks) if glossary else ""
    mid = f"{gloss}\n\n" if gloss else ""
    return (f"{format_context(chunks)}\n\n{mid}--- QUESTION\n{question}\n")


CITE_LINE = canon_pattern("CITATIONS")


def split_citations(text):
    """-> (answer, [citations]). The model is asked for a trailing CITATIONS
    line; parse it off rather than trusting it to be absent from the prose."""
    lines = (text or "").splitlines()
    cites, keep = [], []
    for ln in lines:
        stripped = ln.strip()
        # ...or at the END of a sentence: "… জানতে চান? CITATIONS: none"
        # reached the page that way on 2026-10-26. The part before it is
        # prose and stays; the rest is the citations line.
        at = stripped.upper().find("CITATIONS:")
        if at > 0:
            keep.append(stripped[:at].rstrip())
            stripped = stripped[at:]
        if stripped.upper().startswith("CITATIONS:"):
            raw = stripped.split(":", 1)[1].strip()
            if raw and raw.lower() != "none":
                # Drop a per-item "none" too, not just a whole-line one:
                # gpt-oss-120b emitted "CITATIONS: synthetic_a §9.1.1; none",
                # which used to yield a literal 'none' citation. cite_ok
                # tolerates extras so it scored fine, but it is noise in the
                # answers file and would count as a citation to a reader.
                cites = [c.strip() for c in raw.split(";")
                         if c.strip() and c.strip().lower() != "none"]
            continue
        keep.append(ln)
    return "\n".join(keep).strip(), cites


# THE REPLY IS BENGALI ONLY. The reason an answer was withheld is operator
# information: it belongs in the meta row, which is where every other
# diagnostic already lives. It used to be appended to the sentence itself --
# "(retrieval score below floor; no model call made)" -- and that string went
# to a field officer who reads Bengali, in an answer that was otherwise
# correct. A reply is not a log line.
# "প্রদত্ত প্রসঙ্গে" -- "in the given context" -- was machinery talking.
# A field officer has no notion of a retrieval context; what they can act
# on is whether the manual answers the question.
ABSTAIN_TEXT = "এই প্রশ্নের উত্তর ম্যানুয়ালে পাওয়া যায়নি।"
ABSTAIN_REASON = "retrieval score below floor; no model call made"


# ------------------------------------------------------------- post-filter
# THE ACTUAL GUARANTEE, and the reason it is not a sixth prompt wording.
#
# Five rule-4 wordings were measured against the same synthetic trap. Every
# one either permitted the leak, over-suppressed a correct answer, or -- in
# wording 4's case -- CREATED a leak in a model that had been clean. A prompt
# rule is advice; the model decides how to comply, and "explain why this
# figure does not apply" is a compliant reading of every prohibition tried.
#
# This is not advice. An answer carrying a forbidden figure is discarded and
# replaced with the plain abstention, whatever the model intended.
#
# ONE IMPLEMENTATION, SHARED WITH THE METRIC. gold_eval.answer_violations is
# the same function score_one uses. If the filter and the metric had separate
# matchers they would drift, and the drift would be invisible: the metric
# would report zero because the filter removed what it looks for, while a
# literal the filter missed slipped past both. Sharing means a filter miss is
# also a metric miss, and metric 4 stays a report-stopper that can still fire.
# DELIBERATELY THE SAME SENTENCE as the abstention. A filtered answer
# means the model produced a forbidden figure and it was discarded;
# saying anything more specific would tell the reader that a figure
# exists and was withheld, which is the thing being withheld. Which of
# the two fired is in the meta row, where the operator reads it.
FILTERED_TEXT = ABSTAIN_TEXT
FILTERED_REASON = "post-filter matched a forbidden figure"


def post_filter(answer_rec, question, forbidden_numbers=None):
    """-> (answer_rec, filter_meta). Substitutes the abstention on a hit.

    The original answer is kept in the metadata, never discarded: a filtered
    answer is the most informative output the run produces, and throwing it
    away would leave the leak unexaminable.
    """
    from gold_eval import answer_violations
    hits = answer_violations(question, answer_rec.get("answer") or "",
                             forbidden_numbers)
    if not hits:
        return answer_rec, {"filter_fired": False, "filter_hits": []}
    return ({"id": answer_rec["id"], "answer": FILTERED_TEXT,
             "citations": []},
            {"filter_fired": True,
             "filter_reason": FILTERED_REASON,
             "filter_hits": [f"{k}:{v}" for k, v in hits],
             "original_answer": answer_rec.get("answer"),
             "original_citations": answer_rec.get("citations")})


def load_questions_by_id(gold_path):
    """{id: question} from a gold file, for the post-filter. Empty when the
    file is missing -- the filter then cannot fire and says so."""
    import yaml
    p = Path(gold_path)
    if not p.exists():
        return {}
    g = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {q["id"]: q for q in (g.get("questions") or [])}


# ------------------------------------------------------------ reply guards
# TWO REPLIES A READER MUST NEVER SEE, both from the live chatbot. Of the 20
# answers the demo logged from 2026-09-20 to 2026-10-26, 2 stopped at the
# 1,024-token output limit -- one mid-list, "- **progoti §1.2.2** – “", with
# no citations line -- and one of those two was "দাবি দাবি দাবি …" to the
# limit. Shown as they came, the first is a half-answer that looks whole and
# the second is noise.
#
# A CUT-OFF REPLY IS NEVER SHOWN. finish_reason "length" -- or, from a
# backend that reports none, output tokens at the limit, or ReplyTruncated --
# gets one retry at LENGTH_RETRY_TOKENS. Still cut off, the reader is told
# the answer was too long and to ask something narrower. Partial text is
# worse than none: the missing half is exactly what rule 8 exists to keep.
#
# A DEGENERATE REPLY IS NEVER SHOWN. A unit of 1-4 words repeated
# REPEAT_TIMES times in a row is a decoding loop, not an answer: one retry,
# then a plain "try again". Measured before it was set: it fires on the one
# looping answer of those 20 and on none of the FAQ's 96 reviewed texts.
LENGTH_RETRY_TOKENS = 2048
REPEAT_UNIT_MAX = 4
REPEAT_TIMES = 8
TOO_LONG_TEXT = ("উত্তরটি অনেক দীর্ঘ হয়ে যাচ্ছিল, তাই দেখানো হলো না। "
                 "অনুগ্রহ করে আরও নির্দিষ্ট করে প্রশ্ন করুন।")
NO_ANSWER_TEXT = "উত্তর তৈরি করা যায়নি। অনুগ্রহ করে আবার চেষ্টা করুন।"


def degenerate(text):
    """The repeated unit if `text` loops, else None. Tokens with no letter or
    digit (table rules, bullets, dashes) are skipped, so a wide table is not
    a loop."""
    toks = [w for w in (text or "").split() if any(ch.isalnum() for ch in w)]
    for n in range(1, REPEAT_UNIT_MAX + 1):
        for i in range(len(toks) - n + 1):
            unit, k, j = toks[i:i + n], 1, i + n
            while toks[j:j + n] == unit:
                k, j = k + 1, j + n
            if k >= REPEAT_TIMES:
                return " ".join(unit)
    return None


def _cut_off(meta):
    fr = meta.get("finish_reason")
    if fr:
        return fr == "length"
    mt, ot = meta.get("max_tokens"), meta.get("output_tokens")
    return bool(mt and ot and ot >= mt)


def complete_guarded(system, user):
    """complete(), behind the two guards above. -> (text, meta)

    meta is the last call's, plus `guard` (what fired, in order),
    `guard_outcome` (retried / too_long / no_answer / None) and `attempts`;
    token counts are summed over every attempt, so a run's total agrees
    with the ledger, which complete() writes per call.
    """
    import inspect
    fn = _BACKENDS.get(_STATE.get("provider"))
    takes_limit = (fn is not None
                   and "max_tokens" in inspect.signature(fn).parameters)
    attempts, fired = [], []

    def call(**opts):
        try:
            text, meta = complete(system, user, **opts)
        except ReplyTruncated as e:
            text, meta = "", {"finish_reason": "length",
                              "truncated": str(e)[:160]}
        attempts.append(meta)
        return text, meta

    def done(text, meta, outcome):
        base = next((a for a in reversed(attempts) if a.get("model")), {})
        meta = dict(base, **meta)
        meta.update(
            guard=list(fired), guard_outcome=outcome, attempts=len(attempts),
            prompt_tokens=sum(a.get("prompt_tokens") or 0
                              for a in attempts) or None,
            output_tokens=sum(a.get("output_tokens") or 0
                              for a in attempts) or None)
        return text, meta

    text, meta = call()
    if degenerate(text):
        fired.append("repetition")
        text, meta = call()
        if degenerate(text):
            return done(NO_ANSWER_TEXT, meta, "no_answer")
    if _cut_off(meta):
        fired.append("length")
        opts = {"max_tokens": LENGTH_RETRY_TOKENS} if takes_limit else {}
        text, meta = call(**opts)
        if degenerate(text):
            return done(NO_ANSWER_TEXT, meta, "no_answer")
        if _cut_off(meta):
            return done(TOO_LONG_TEXT, meta, "too_long")
    return done(text, meta, "retried" if fired else None)


# ------------------------------------------------------- programme labels
# RULE 9 IN CODE. Asked to name the programme of every figure, the model did
# not: "প্রথম ঋণের ক্ষেত্রে … ৪ % … রিপিট ঋণের ক্ষেত্রে ২ %" came back with no
# programme, and read as the rule for every member -- the group-based figure
# is ৫%. So when a chunk the answer cites has a heading that names a
# programme, and the answer names none of them, the label is added here.
#
# A twin's heading counts. Byte-identical chunks are one passage in two
# manuals, and the Progoti manual prints headings the Dabi manual omits.
# What cannot be labelled is a chunk whose heading, in every copy, names
# no programme: the Dabi manual never prints ২.১.২'s heading, so its
# §2.1.2 chunks with no Progoti twin say nothing about whose rule they are.
# Programme names come from productmatch (Convention 2).
_BN_FIGURES = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")


@functools.lru_cache(maxsize=1)
def _twin_headings():
    """{dup_group: [heading text, ...]} from the tracked chunk manifest."""
    p = Path("data/processed/chunk_manifest.json")
    if not p.exists():
        return {}
    out = {}
    for r in json.loads(p.read_text(encoding="utf-8")).get("chunks") or []:
        if r.get("dup_group"):
            out.setdefault(r["dup_group"], []).append(
                " > ".join(r.get("heading_path") or []))
    return out


def cited_chunks(cites, chunks):
    """The retrieved chunks the citations name, matched exactly as
    app.cite_match and the citation metric match them (gold_eval.norm_cite).
    -> [chunk], in context order"""
    from gold_eval import norm_cite
    want = [norm_cite(c) for c in cites or []]
    out = []
    for ch in chunks or []:
        if not ch.get("clause"):
            continue
        own = norm_cite(f'{ch["manual_id"]} §{ch["clause"]}')
        if own and any(own in c or c in own for c in want):
            out.append(ch)
    return out


def _names_bn(keys):
    import productmatch as pm
    names = [pm.PROGRAMME_NAMES[k][0] for k in keys]
    return names[0] if len(names) == 1 else (
        ", ".join(names[:-1]) + " ও " + names[-1])


def _names_en(keys):
    import productmatch as pm
    names = []
    for k in keys:
        latin = [a.strip() for a in pm.PROGRAMME_NAMES[k][1].split(";")
                 if a.strip().isascii()]
        names.append(latin[0] if latin else pm.PROGRAMME_NAMES[k][0])
    return names[0] if len(names) == 1 else (
        ", ".join(names[:-1]) + " and " + names[-1])


def programme_label(answer, cites, chunks, question=""):
    """-> the answer, with a label line added when a cited chunk's heading
    (or its twin's) names programmes the answer does not mention."""
    import productmatch as pm
    named = set(pm.programmes_in(answer or ""))
    sets = []                            # [(programme keys, clause)], unique
    for ch in cited_chunks(cites, chunks):
        heads = [" > ".join(ch.get("heading_path") or [])]
        heads += _twin_headings().get(ch.get("dup_group"), [])
        keys = []
        for h in heads:
            keys += [k for k in pm.programmes_in(h) if k not in keys]
        order = list(pm.PROGRAMME_NAMES)     # the registry's order, always
        keys.sort(key=order.index)
        if keys and not named & set(keys):
            if keys not in [s for s, _c in sets]:
                sets.append((keys, ch.get("clause") or ""))
    if not sets:
        return answer
    bengali = any("ঀ" <= ch <= "৿" for ch in question or answer)
    if len(sets) == 1:
        keys = sets[0][0]
        label = (f"এই তথ্য {_names_bn(keys)} কর্মসূচির।" if bengali else
                 f"This information is for the {_names_en(keys)} "
                 f"programme{'s' if len(keys) > 1 else ''}.")
    elif bengali:
        label = "; ".join(f"§{c.translate(_BN_FIGURES)}-এর তথ্য "
                          f"{_names_bn(k)} কর্মসূচির" for k, c in sets) + "।"
    else:
        label = "; ".join(f"§{c}: the {_names_en(k)} programme"
                          f"{'s' if len(k) > 1 else ''}" for k, c in sets) + "."
    return (answer or "").rstrip() + "\n" + label


def answer_one(row, floor, system=SYSTEM_PROMPT, glossary=False,
               registry_rules=True):
    """-> (answer_record, meta_record). Abstains before calling the model when
    the top fused score is below the floor.

    THE REGISTRY RUNS ON BOTH SIDES OF THE MODEL, and rule 4 runs before it.

    Before: a question whose answer the field team has WITHDRAWN is answered
    from data/registry alone -- no context is built and no provider call is
    made. That is not an optimisation. The withheld rows exist because the
    manual still prints a figure that is no longer true, so the one thing
    that must not happen is the model being handed that figure and asked to
    be careful with it. generate.py already measured where that leads: see
    the post-filter note above, five wordings, every one of them leaked.

    After: rules 1-3 shape whatever was produced, including the floor
    abstention. A question naming a suspended product gets the suspension
    sentence even when retrieval found nothing -- the registry knows the
    answer there and the corpus does not.
    """
    chunks = row.get("chunks") or []
    question = row["question_bn"]
    top = chunks[0]["fused_score"] if chunks else 0.0
    base = {"id": row["question_id"], "top_fused": top,
            "n_chunks": len(chunks)}
    reg = None
    if registry_rules:
        import registry as _registry
        reg = _registry.load()
        # withheld_for, not withheld: the same reading of the chunks that
        # apply_rules makes. Passing clauses alone once let a Progoti-only
        # row skip the model on a Dabi chunk, and the reply came back empty.
        if reg.withheld_for(question, chunks):
            text, cites, rmeta = _registry.apply_rules(
                question, "", [], chunks, reg)
            return ({"id": row["question_id"], "answer": text,
                     "citations": cites},
                    dict(base, floor_fired=False, model=None, provider=None,
                         retries=0, prompt_tokens=None, output_tokens=None,
                         **rmeta))

    abstained = not chunks or top < floor
    if abstained:
        answer, cites = ABSTAIN_TEXT, []
        meta = dict(floor_fired=True, abstain_reason=ABSTAIN_REASON,
                    model=None, provider=None, retries=0,
                    prompt_tokens=None, output_tokens=None)
    else:
        user = build_user_prompt(question, chunks, glossary)
        text, mmeta = complete_guarded(system, user)
        answer, cites = split_citations(text)
        meta = dict(floor_fired=False, **mmeta)

    if registry_rules:
        import registry as _registry
        answer, cites, rmeta = _registry.apply_rules(
            question, answer, cites, chunks, reg, abstained=abstained)
        meta.update(rmeta)
    labelled = programme_label(answer, cites, chunks, question)
    meta["programme_label"] = labelled != answer
    return ({"id": row["question_id"], "answer": labelled,
             "citations": cites},
            dict(base, **meta))


# ------------------------------------------------------------- the synthetic
# INVENTED CONTENT ONLY. No product, role, insurer or clause here exists in
# the registries or the manuals -- checked by canary_selftest below against
# productmatch, so a fixture cannot quietly start naming a real product.
SYNTH_CHUNKS = [
    {
        "chunk_id": "synth_0001", "manual_id": "synthetic_a", "clause": "9.1.1",
        "heading_path": ["নবম অধ্যায়: কাল্পনিক প্রোডাক্ট",
                         "৯.১ নীলকমল ঋণ", "৯.১.১ প্রিমিয়াম ও অনুমোদন"],
        "display_text": ("নীলকমল ঋণের বিমা প্রিমিয়াম রূপসা ইন্স্যুরেন্স "
                         "কোম্পানির ক্ষেত্রে ৪.৫০%। ঋণের সর্বোচ্চ সীমা "
                         "৩ লক্ষ টাকা এবং অনুমোদন দেবেন কেএম।"),
        "synthetic": True,
    },
    {
        "chunk_id": "synth_0002", "manual_id": "synthetic_a", "clause": "9.2.1",
        "heading_path": ["নবম অধ্যায়: কাল্পনিক প্রোডাক্ট",
                         "৯.২ প্রাধিকারসূচি", "৯.২.১ ক্রয় সীমা"],
        "display_text": ("অফিস সরঞ্জাম ক্রয়ের সীমা কেএম এর জন্য ৫০,০০০ টাকা "
                         "এবং জিএম এর জন্য ২,৫০,০০০ টাকা। এই সীমা শুধুমাত্র "
                         "ক্রয় সংক্রান্ত, ঋণ অনুমোদনের জন্য প্রযোজ্য নয়।"),
        "synthetic": True,
    },
    {
        "chunk_id": "synth_0003", "manual_id": "synthetic_b", "clause": "9.1.1",
        "heading_path": ["নবম অধ্যায়: কাল্পনিক প্রোডাক্ট",
                         "৯.১ হরিতকী বিমা", "৯.১.১ আওতা"],
        "display_text": ("সিন্থেটিক-বি ম্যানুয়ালে ৯.১.১ ধারা হরিতকী বিমার "
                         "আওতা বর্ণনা করে, যা নীলকমল ঋণের সাথে সম্পর্কিত নয়।"),
        "synthetic": True,
    },
]

SYNTH_QUESTIONS = [
    # 1. answerable: premium + insurer + clause. Exercises rules 1,2,3,7.
    ("S-001", "নীলকমল ঋণের বিমা প্রিমিয়াম কত?", "yes",
     ["synth_0001", "synth_0003"]),
    # 2. unanswerable: asks for a loan-approval ceiling for জিএম. The context
    #    holds a PROCUREMENT limit for জিএম that looks exactly like one --
    #    the §7.1 trap, reproduced with invented figures. Exercises rule 4.
    ("S-002", "জিএম কত টাকা পর্যন্ত ঋণ অনুমোদন করতে পারেন?", "no",
     ["synth_0002", "synth_0001"]),
    # 3. ambiguous: ধারা ৯.১.১ exists in both synthetic manuals and means
    #    different things. Exercises rule 6.
    ("S-003", "ধারা ৯.১.১ এ কী বলা হয়েছে?", "partial",
     ["synth_0001", "synth_0003"]),
    # 4. GENUINELY PARTIAL, and the only question here that can detect rule 4
    #    OVER-suppressing. The context states the ceiling (৩ লক্ষ টাকা) and
    #    does NOT state the term (মেয়াদ), so the correct answer is "the term
    #    is not stated, but the ceiling is ৩ লক্ষ টাকা". Rule 4 must not fire
    #    on this: the context DOES contain half the answer. A rule that
    #    suppresses the ৩ লক্ষ টাকা half silences a correct answer, and the
    #    other three questions cannot see that happen.
    ("S-004", "নীলকমল ঋণের মেয়াদ কত এবং সর্বোচ্চ সীমা কত?", "partial",
     ["synth_0001", "synth_0002"]),
    # 5. THE SECOND PARTIAL, and structurally the mirror of S-004. S-004's
    #    present half is a FIGURE (৩ লক্ষ টাকা) and rule 4's operative
    #    sentence is "do not quote any figure" -- so S-004 alone cannot tell
    #    whether over-suppression attaches to NUMBERHOOD or to partial
    #    answers in general. Here the present half is a ROLE (কেএম, stated in
    #    synth_0001) and the missing half is a fee that appears nowhere. A
    #    model that keeps কেএম here but drops ৩ লক্ষ টাকা on S-004 is
    #    suppressing figures; one that drops both is suppressing the stated
    #    half of any partial answer.
    ("S-005", "নীলকমল ঋণ কে অনুমোদন করবেন এবং প্রক্রিয়াকরণ ফি কত?", "partial",
     ["synth_0001", "synth_0002"]),
]


def make_synthetic(retrieved_path, gold_path):
    """Write the synthetic dump and a matching gold file. Facts are sliced out
    of the fixture text, so the gold cannot drift from the context."""
    by_id = {c["chunk_id"]: c for c in SYNTH_CHUNKS}
    rows = []
    for qid, qbn, _ans, cids in SYNTH_QUESTIONS:
        chunks = []
        for rank, cid in enumerate(cids):
            c = dict(by_id[cid])
            c["fused_score"] = round(0.72 - 0.11 * rank, 4)
            c["dense_score"] = round(0.51 - 0.09 * rank, 4)
            c["sparse_score"] = round(14.0 - 3.5 * rank, 3)
            chunks.append(c)
        rows.append({"question_id": qid, "question_bn": qbn, "chunks": chunks})
    Path(retrieved_path).parent.mkdir(parents=True, exist_ok=True)
    Path(retrieved_path).write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8")

    t1 = by_id["synth_0001"]["display_text"]
    prem = "৪.৫০%"
    insurer = "রূপসা ইন্স্যুরেন্স"
    ceiling = "৩ লক্ষ টাকা"
    role = "কেএম"
    for lit in (prem, insurer, ceiling, role):
        assert lit in t1, lit
    gold = {
        "meta": {"version": 1, "created": "2026-09-17",
                 "note": ("SYNTHETIC. Invented products, figures, roles and "
                          "manuals. Exists to prove the generate.py plumbing "
                          "without sending a real chunk to a provider. Not a "
                          "measurement of anything.")},
        "questions": [
            {"id": "S-001", "type": "simple_lookup",
             "provenance": "chunk_derived",
             "question_bn": SYNTH_QUESTIONS[0][1],
             "manual_scope": "synthetic_a", "answerable": "yes",
             "gold_facts": [
                 {"text": prem, "kind": "literal", "ref": "synth_0001",
                  "verified_against": "corpus"},
                 {"text": insurer, "kind": "literal", "ref": "synth_0001",
                  "verified_against": "corpus"}],
             "must_cite": ["synthetic_a §9.1.1"],
             "must_not_contain_literals": [],
             "verification_status": "synthetic", "difficulty": "easy",
             "notes": "Answerable. Premium plus insurer, rules 2, 3 and 7."},
            {"id": "S-002", "type": "unanswerable_adversarial",
             "provenance": "chunk_derived",
             "question_bn": SYNTH_QUESTIONS[1][1],
             "manual_scope": "synthetic_a", "answerable": "no",
             "gold_facts": [
                 {"text": "ঋণ অনুমোদনের সীমা এই প্রসঙ্গে নেই",
                  "kind": "absent", "verified_against": "corpus"},
                 {"text": "প্রসঙ্গে শুধু ক্রয় সীমা আছে",
                  "kind": "claim", "verified_against": "corpus"}],
             "must_cite": [],
             # The trap: these are the PROCUREMENT figures. Quoting either as
             # a loan-approval ceiling is the rule-4 failure this question is
             # built to catch.
             "must_not_contain_literals": ["৫০,০০০", "২,৫০,০০০"],
             "verification_status": "synthetic", "difficulty": "hard",
             "notes": ("Unanswerable. The context holds a procurement limit "
                       "for জিএম formatted like a loan ceiling. A correct "
                       "answer abstains and does not offer it.")},
            {"id": "S-004", "type": "partial_gap",
             "provenance": "chunk_derived",
             "question_bn": SYNTH_QUESTIONS[3][1],
             "manual_scope": "synthetic_a", "answerable": "partial",
             "gold_facts": [
                 # The half that IS stated. If a prompt change drops this,
                 # fact recall on S-004 falls and over-suppression is visible.
                 {"text": ceiling, "kind": "literal", "ref": "synth_0001",
                  "verified_against": "corpus"},
                 # The half that is not.
                 {"text": "মেয়াদ প্রসঙ্গে উল্লেখ নেই",
                  "kind": "claim", "verified_against": "corpus"}],
             "must_cite": ["synthetic_a §9.1.1"],
             "must_not_contain_literals": [],
             "verification_status": "synthetic", "difficulty": "hard",
             "notes": ("PARTIAL. The context states the ceiling and not the "
                       "term. Correct: name the gap AND give the ceiling. "
                       "This is the over-suppression detector for rule 4.")},
            {"id": "S-005", "type": "partial_gap",
             "provenance": "chunk_derived",
             "question_bn": SYNTH_QUESTIONS[4][1],
             "manual_scope": "synthetic_a", "answerable": "partial",
             "gold_facts": [
                 # The half that IS stated, and deliberately NOT a number.
                 {"text": role, "kind": "literal", "ref": "synth_0001",
                  "verified_against": "corpus"},
                 # The half that is not stated anywhere in the fixture.
                 {"text": "প্রক্রিয়াকরণ ফি প্রসঙ্গে উল্লেখ নেই",
                  "kind": "claim", "verified_against": "corpus"}],
             "must_cite": ["synthetic_a §9.1.1"],
             # LEFT EMPTY ON PURPOSE. ৪.৫০% and the two procurement figures
             # are all available to be mis-offered as a "fee", and arming them
             # would make this a violation trap as well. It would then be
             # unclear whether a bad row meant over-suppression or a leak,
             # which is the one thing this question exists to isolate.
             "must_not_contain_literals": [],
             "verification_status": "synthetic", "difficulty": "hard",
             "notes": ("PARTIAL, and the non-numeric twin of S-004. The "
                       "context names the approver and states no fee. "
                       "Correct: give কেএম AND name the gap. NOTE a metric "
                       "interaction -- dropping কেএম costs BOTH fact recall "
                       "and language_match, because untranslated_literals "
                       "cannot tell a suppressed literal from a translated "
                       "one. Read metric 1 for suppression here.")},
            {"id": "S-003", "type": "cross_manual_disambiguation",
             "provenance": "chunk_derived",
             "question_bn": SYNTH_QUESTIONS[2][1],
             "manual_scope": "ambiguous", "answerable": "partial",
             "gold_facts": [
                 {"text": "কোন ম্যানুয়াল তা জানা প্রয়োজন",
                  "kind": "claim", "verified_against": "corpus"}],
             "must_cite": [],
             "must_not_contain_literals": [],
             "verification_status": "synthetic", "difficulty": "medium",
             "notes": ("Ambiguous. ধারা ৯.১.১ is নীলকমল ঋণ in synthetic_a and "
                       "হরিতকী বিমা in synthetic_b. Rule 6: ask which.")},
        ],
    }
    import yaml
    Path(gold_path).parent.mkdir(parents=True, exist_ok=True)
    Path(gold_path).write_text(
        yaml.safe_dump(gold, allow_unicode=True, sort_keys=False, width=88),
        encoding="utf-8")
    return len(rows), len(gold["questions"])


# ------------------------------------------------------------------- judge
# A DIFFERENT MODEL FROM THE GENERATOR, and it goes through complete() like
# everything else -- so this module still names no model outside that door.
# The generator takes MODEL_PREFERENCE[0]; the judge takes the next listed
# model that is not the generator's, which keeps one model from grading its
# own output. gold_eval.make_llm_judge's own default is an Anthropic model
# and that package is not installed here, so this is injected instead.
JUDGE_SYSTEM = ("Does the ANSWER assert the CLAIM? The answer may be in "
                "Bengali and the claim in English or Bengali. Reply with "
                "exactly YES or NO. Reply NO if the answer is merely "
                "consistent with the claim without asserting it.")


_PROBE_CACHE = {}


def probe_model(name, provider=None, use_cache=True):
    """-> (reachable, why). One minimal call, CACHED per process.

    Costs a request, which is the only honest way to tell a listed model from
    a usable one -- models.list() reports gemini-flash-latest as available
    while it returns 429, and gemma-4-31b-it as available while it returns
    500 INTERNAL on a system_instruction prompt.

    Cached because the probe competes with the measurement for the same
    quota. Measured 2026-09-17: repeated quota_report and make_judge calls
    spent enough of the daily allowance on probing to leave the actual
    before/after unrunnable. Pass use_cache=False only when re-probing after
    a known reset.
    """
    if use_cache and name in _PROBE_CACHE:
        return _PROBE_CACHE[name]
    saved_pin, saved_attempts = _STATE.get("pin"), globals()["MAX_ATTEMPTS"]
    try:
        _STATE["pin"] = name
        globals()["MAX_ATTEMPTS"] = 1          # a probe must not burn backoff
        complete("Reply with exactly OK.", "OK?", cache_system=False)
        _PROBE_CACHE[name] = (True, "ok")
        return _PROBE_CACHE[name]
    except ProviderError as e:
        t = str(e)
        if "429" in t or "RESOURCE_EXHAUSTED" in t.upper():
            why = "429-quota"
        elif "404" in t:
            why = "404"
        elif "500" in t or "INTERNAL" in t.upper():
            # gemma-4-* answer models.list() but 500 on a system_instruction
            # prompt. Listed, not usable.
            why = "500-internal"
        else:
            why = (type(e).__name__ if len(t) > 90 else t[:60])
        _PROBE_CACHE[name] = (False, why)
        return _PROBE_CACHE[name]
    except Exception as e:                                       # noqa: BLE001
        _PROBE_CACHE[name] = (False, type(e).__name__)
        return _PROBE_CACHE[name]
    finally:
        _STATE["pin"] = saved_pin
        globals()["MAX_ATTEMPTS"] = saved_attempts


def quota_report(names, verbose=True):
    """Probe each name and print the state. Run BEFORE generating, so a score
    is always attributable to models known to have been reachable."""
    out = {}
    for n in names:
        ok, why = probe_model(n)
        out[n] = why
        if verbose:
            print(f"  {n:<26}{'REACHABLE' if ok else 'unavailable':<13}{why}")
    return out


def make_judge(generator_models):
    """-> (judge_callable, judge_model_name) or (None, reason).

    Excludes EVERY model the generator used, not just the preferred one. A run
    that falls through mid-way uses two models, and picking the judge against
    only the first lets the fallthrough model grade its own output -- measured
    on the first synthetic run, where S-003 came from gemini-2.5-flash and that
    was also the judge.
    """
    provider = _STATE["provider"]
    if provider == "local":
        return None, "local provider cannot judge"
    try:
        key = load_key(provider)
        if provider == "gemini":
            _c, models = _gemini_models(key)
        else:
            return None, f"no judge wired for provider {provider!r}"
    except ProviderError as e:
        return None, str(e).splitlines()[0]
    used = {m for m in (generator_models or []) if m}
    others = [m for m in models if m not in used]
    if not others:
        return None, "no second model available to judge with"
    # PROBE BEFORE SELECTING. Listed != reachable: gemini-flash-latest is
    # listed and 429 quota-exhausted, and picking it made --judge llm fail
    # every claim fact with an error that looked like a judging problem.
    # One minimal call per candidate, stopping at the first that answers.
    judge_model, probe_log = None, []
    for cand in others[:6]:
        ok, why = probe_model(cand)
        probe_log.append(f"{cand}={why}")
        if ok:
            judge_model = cand
            break
    if judge_model is None:
        return None, ("no reachable judge model; probed " + ", ".join(probe_log))
    yes = canon_pattern("YES")

    def judge(fact, answer):
        text, _meta = complete(
            JUDGE_SYSTEM, f"CLAIM: {fact}\n\nANSWER: {answer}",
            cache_system=False)
        # canon both sides rather than a bare upper() == "YES": the reply may
        # arrive with stray whitespace or a zero-width character.
        return bool(yes.search(canon(text.strip().upper()[:8])))

    return judge, judge_model


# ------------------------------------------------------------------ canary
# A REAL LEAK, captured verbatim from qwen/qwen3.8-27b on 2026-09-18 under
# the ORIGINAL rule-4 wording, question S-002. qwen leaks 3/3 on that wording
# and is reachable at 1000 req/day, so this canary is a real observed failure
# that can be re-captured at will -- not an invented one.
QWEN_LEAK_S002 = (
    "প্রদত্ত কনটেক্সটে জিএম (GM) এর ঋণ অনুমোদনের সীমা উল্লেখ করা নেই।\n\n"
    "কনটেক্সট ১ (synthetic_a §9.2.1) অনুযায়ী, জিএম এর ২,৫০,০০০ টাকার সীমাটি "
    "শুধুমাত্র অফিস সরঞ্জাম ক্রয়ের জন্য প্রযোজ্য এবং এই সীমাটি \"ঋণ "
    "অনুমোদনের জন্য প্রযোজ্য নয়\"।\n\n"
    "কনটেক্সট ২ (synthetic_a §9.1.1) অনুযায়ী, নীলকমল ঋণের সর্বোচ্চ সীমা "
    "৩ লক্ষ টাকা এবং অনুমোদন দেবেন কেএম (KM), জিএম নয়।")

# A correct abstention from the same model and question, for the inverse
# control: the filter must NOT fire on it. A filter that fires on everything
# would show a perfect violation score and silence every answer.
QWEN_CLEAN_S002 = "প্রদত্ত কনটেক্সটে জিএম এর ঋণ অনুমোদনের সীমা উল্লেখ করা নেই।"


def canary_selftest(verbose=True):
    """Convention 3. Four canaries, none invented.

    1. FORBIDDEN OVERBROAD, reused. validate_gold already owns a canary that
       asserts a §7.1 figure quoted as a loan ceiling scores a violation while
       a correct abstention does not. Copying it here would be a second
       implementation of the same rule, which is how the two copies of
       resolve_ref diverged. It is imported and run.
    2. PROMPT RULES INTACT. Nine numbered rules must be present. A prompt
       silently truncated to eight still produces fluent answers, and the score
       moves for a reason nobody can see.
    3. SYNTHETIC IS SYNTHETIC. Every fixture literal is checked against
       productmatch: if a fixture ever names a real product, "the first run is
       synthetic" stops being true and real vocabulary reaches the provider.

    4. THE POST-FILTER CATCHES A REAL LEAK, and does not fire on a correct
       abstention. The fixture is qwen/qwen3.8-27b's actual S-002 answer under
       the original wording -- five prompt wordings failed to stop this and
       the filter is the only thing that does, so a filter that silently
       stopped matching would restore the leak with nothing to show it. The
       inverse half matters as much: a filter that fired on everything would
       report a perfect violation score and silence every answer.
    """
    ok = True

    try:
        import yaml
        from validate_gold import canary_forbidden_not_overbroad
        gold_path = Path("gold/bootstrap.yaml")
        if not gold_path.exists():
            if verbose:
                print("  canary forbidden_overbroad  gold/bootstrap.yaml "
                      "missing; NOT BUILDABLE")
        else:
            gold = yaml.safe_load(gold_path.read_text(encoding="utf-8"))
            ok1 = canary_forbidden_not_overbroad(gold, verbose=verbose)
            ok &= ok1
    except ImportError as e:
        if verbose:
            print(f"  canary forbidden_overbroad  NOT BUILDABLE: {e}")

    n_rules = sum(1 for i in range(1, 10)
                  if f"\n{i}. " in SYSTEM_PROMPT)
    ok2 = n_rules == 9
    ok &= ok2
    if verbose:
        print(f"  canary prompt_rules         {n_rules} of 9 numbered rules "
              f"present  [{'PASS' if ok2 else 'FAIL'}]")

    real = []
    try:
        import productmatch as pm
        names = [n for n, _a in pm.PROGRAMME_NAMES.values()]
        names += [e[1] for e in pm.NAMED]
        for c in SYNTH_CHUNKS:
            blob = canon(c["display_text"] + " ".join(c["heading_path"]))
            for nm in names:
                if canon_pattern(nm).search(blob):
                    real.append((c["chunk_id"], nm))
    except ImportError as e:
        if verbose:
            print(f"  canary synthetic_is_fake    NOT BUILDABLE: {e}")
        return bool(ok)
    ok3 = not real
    ok &= ok3
    if verbose:
        print(f"  canary synthetic_is_fake    {len(SYNTH_CHUNKS)} fixture "
              f"chunks name {len(real)} real product(s) "
              f"{real[:3]}  [{'PASS' if ok3 else 'FAIL'}]")
    import yaml as _yaml
    gp = Path("gold/synthetic.yaml")
    if not gp.exists():
        if verbose:
            print("  canary post_filter          gold/synthetic.yaml missing; "
                  "NOT BUILDABLE -- run --make-synthetic")
    else:
        qs = {q["id"]: q for q in
              (_yaml.safe_load(gp.read_text(encoding="utf-8")) or {}
               ).get("questions") or []}
        q2 = qs.get("S-002")
        if q2 is None:
            if verbose:
                print("  canary post_filter          S-002 not in the "
                      "synthetic gold; NOT BUILDABLE")
        else:
            leak_rec = {"id": "S-002", "answer": QWEN_LEAK_S002,
                        "citations": []}
            clean_rec = {"id": "S-002", "answer": QWEN_CLEAN_S002,
                         "citations": []}
            got_leak, m_leak = post_filter(leak_rec, q2)
            got_clean, m_clean = post_filter(clean_rec, q2)
            ok4 = (m_leak["filter_fired"]
                   and got_leak["answer"] == FILTERED_TEXT
                   and m_leak.get("original_answer") == QWEN_LEAK_S002
                   and not m_clean["filter_fired"]
                   and got_clean["answer"] == QWEN_CLEAN_S002)
            ok &= ok4
            if verbose:
                print(f"  canary post_filter          real qwen leak "
                      f"fired={m_leak['filter_fired']} hits="
                      f"{m_leak['filter_hits']}; clean abstention "
                      f"fired={m_clean['filter_fired']}  "
                      f"[{'DETECTED' if ok4 else 'MISSED'}]")

    return bool(ok)


# -------------------------------------------------------------------- score
def score(gold_path, answers_path, forbidden_path, judge):
    """Drive gold_eval with an injected judge. gold_eval owns every scoring
    rule; this only supplies the answers and the judge."""
    import yaml
    from gold_eval import canary_selftest as fact_canary, report, score_one
    print("\nGOLD_EVAL CANARY")
    if not fact_canary():
        print("FATAL: fact matcher failed its canary. Scores are meaningless.")
        return 2, []
    gold = yaml.safe_load(Path(gold_path).read_text(encoding="utf-8"))
    qs = {q["id"]: q for q in gold["questions"]}
    answers = {}
    for line in Path(answers_path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            a = json.loads(line)
            answers[a["id"]] = a
    forbidden = {}
    if forbidden_path and Path(forbidden_path).exists():
        forbidden = json.loads(
            Path(forbidden_path).read_text(encoding="utf-8"))
    missing = [i for i in qs if i not in answers]
    if missing:
        print(f"\n[warn] no answer for: {', '.join(missing)}")
    rows = [score_one(qs[i], answers[i], forbidden, judge)
            for i in qs if i in answers]
    return report(rows), rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="gemini",
                    choices=sorted(_BACKENDS))
    ap.add_argument("--model", metavar="NAME",
                    help="pin generation to exactly this model: no preference "
                         "order, no fallthrough. Use it for any comparison "
                         "between models; without it a flaky preferred model "
                         "silently hands the run to the next one")
    ap.add_argument("--retrieved", default="out/retrieved.jsonl")
    ap.add_argument("--gold", default="gold/bootstrap.yaml")
    ap.add_argument("--answers", default="out/answers.jsonl")
    ap.add_argument("--meta", default="out/answers_meta.jsonl")
    ap.add_argument("--forbidden-numbers", default="out/forbidden.json")
    ap.add_argument("--floor", type=float, default=SCORE_FLOOR)
    ap.add_argument("--allow-corpus", metavar="REASON",
                    help="override the corpus guard; the reason is recorded "
                         "in every meta row")
    ap.add_argument("--make-synthetic", action="store_true",
                    help="write out/synthetic_retrieved.jsonl and "
                         "gold/synthetic.yaml, then exit")
    ap.add_argument("--score", action="store_true",
                    help="run gold_eval on the answers just written")
    ap.add_argument("--judge", choices=["literal", "llm"], default="literal")
    ap.add_argument("--quota-report", action="store_true",
                    help="probe each model for reachability before "
                         "generating, so the numbers are attributable")
    ap.add_argument("--glossary", action="store_true",
                    help="append a glossary of the role/product terms that "
                         "appear in the retrieved chunks, expanded from "
                         "roles.py and productmatch. MEASURED, not a "
                         "default: a glossary is more context to misquote")
    ap.add_argument("--run-id", metavar="ID",
                    help="identifies this invocation in the run log's "
                         "filename and in every meta row. Defaults to a UTC "
                         "timestamp, which is what makes a retry stop "
                         "destroying the record of the attempt before it.")
    ap.add_argument("--run-log-dir", default="out/runs", metavar="DIR",
                    help="where the per-invocation run log goes "
                         "(default out/runs). One file per run id.")
    ap.add_argument("--no-registry", action="store_true",
                    help="synthetic dumps only: answer without data/registry. "
                         "The synthetic set tests faithfulness to an invented "
                         "manual and the registry describes real products, so "
                         "its refusals there measure the wrong thing. Refused "
                         "on any dump that is not synthetic, and recorded as "
                         "registry=off in every meta row")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if not args.run_id:
        args.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    print("CANARY")
    if not canary_selftest():
        print("FATAL: generate failed its own canary. Not calling a model.")
        return 2
    if args.selftest:
        return 0

    if args.make_synthetic:
        n, g = make_synthetic("out/synthetic_retrieved.jsonl",
                              "gold/synthetic.yaml")
        print(f"\n-> out/synthetic_retrieved.jsonl  ({n} questions)")
        print(f"-> gold/synthetic.yaml            ({g} questions)")
        return 0

    if not Path(args.retrieved).exists():
        print(f"\nno retrieved context at {args.retrieved}.\n"
              f"  python retrieve.py --dump {args.retrieved}\n"
              f"  or: python generate.py --make-synthetic")
        return 1

    # THE REGISTRY IS ON unless a caller turns it off, and only a synthetic
    # dump may. Checked before configure(), so a refused run calls nothing.
    if args.no_registry and not is_synthetic(args.retrieved):
        print(f"\n--no-registry is for the synthetic set only, and "
              f"{args.retrieved} is not synthetic. A real dump is always "
              f"answered with the registry.")
        return 1
    registry_state = "off" if args.no_registry else "on"

    configure(args.provider, args.model)
    try:
        guard = check_corpus_guard(args.retrieved, args.provider,
                                   args.allow_corpus)
    except ProviderError as e:
        print(f"\n{e}")
        return 1
    print(f"\nprovider {args.provider}  corpus-guard: {guard}")
    print(f"floor {args.floor}  context {args.retrieved}  "
          f"registry {registry_state}")

    if args.quota_report:
        print()
        print("QUOTA STATE (one probe call per model, before generating)")
        quota_report([args.model] if args.model else list(MODEL_PREFERENCE))

    rows = read_retrieved(args.retrieved)
    # The post-filter needs the questions and forbidden.json up front, so a
    # missing gold file is reported before any model call rather than after.
    questions = load_questions_by_id(args.gold)
    forbidden = {}
    if args.forbidden_numbers and Path(args.forbidden_numbers).exists():
        forbidden = json.loads(
            Path(args.forbidden_numbers).read_text(encoding="utf-8"))
    print(f"post-filter: {len(questions)} question(s) from {args.gold}, "
          f"{sum(1 for k in forbidden if not k.startswith(chr(95)))}"
          f" forbidden clause(s)")
    # TPD PREFLIGHT. Before spending anything, check the run fits in what is
    # left of today's per-day token budget -- the limit the response headers
    # do not carry. Skipped when a model is not pinned, because the estimate
    # is per model and preference-order fallthrough could land anywhere.
    if args.model:
        ok_budget, budget_msg = tpd_preflight(args.provider, args.model, rows)
        if not ok_budget:
            print("\n" + budget_msg)
            return 1

    # THE RUN LOG. Opened before the first call and flushed after every line,
    # so a run that dies part-way still leaves everything it managed to do.
    run_log = None
    try:
        Path(args.run_log_dir).mkdir(parents=True, exist_ok=True)
        run_log = open(Path(args.run_log_dir) / f"{args.run_id}.log",
                       "w", encoding="utf-8", newline="\n")
        run_log.write(f"run_id {args.run_id}\n"
                      f"provider {args.provider}  model {args.model}\n"
                      f"retrieved {args.retrieved}\n"
                      f"answers {args.answers}\n"
                      f"registry {registry_state}\n")
        run_log.flush()
    except OSError as e:               # a log that cannot be opened must not
        print(f"  [warn] no run log: {e}")   # take the run down

    def _log(line):
        print(line)
        if run_log:
            run_log.write(line + "\n")
            run_log.flush()

    answers, metas = [], []
    for row in rows:
        try:
            a, m = answer_one(row, args.floor, glossary=args.glossary,
                              registry_rules=not args.no_registry)
        except MissingKey as e:
            print(f"\n{e}")
            return 1
        except NotImplementedError as e:
            print(f"\n{e}")
            return 1
        except ProviderError as e:
            msg = f"\nprovider failed on {row['question_id']}: {e}"
            print(msg)
            if run_log:
                run_log.write(msg + "\n  ABORTED. The answers above were "
                              "produced but NOT written to the answers file; "
                              "this log is the only record of them.\n")
                run_log.flush()
                run_log.close()
            return 1
        q = questions.get(a["id"])
        if q is not None:
            a, fmeta = post_filter(a, q, forbidden)
            m.update(fmeta)
        else:
            m.update({"filter_fired": False, "filter_hits": [],
                      "filter_skipped": "question not in gold file"})
        m["corpus_guard"] = guard
        m["pinned_model"] = args.model
        m["run_id"] = args.run_id
        m["glossary"] = bool(args.glossary)
        m["registry"] = registry_state
        answers.append(a)
        metas.append(m)
        tag = "FLOOR" if m["floor_fired"] else (m.get("model") or "?")
        filt = "  FILTERED" if m.get("filter_fired") else ""
        _log(f"  {a['id']:<7}{tag:<22}top={m['top_fused']:.4f} "
             f"retries={m.get('retries', 0)} cites={a['citations']}{filt}"
             f"\n      ANSWER: {(a.get('answer') or '')[:400]}")
        if m.get("filter_fired"):
            print(f"          hits: {m['filter_hits']}")

    if run_log:
        run_log.write(f"COMPLETE. {len(answers)} answer(s) written to "
                      f"{args.answers}" + "\n")
        run_log.close()
    Path(args.answers).parent.mkdir(parents=True, exist_ok=True)
    Path(args.answers).write_text(
        "\n".join(json.dumps(a, ensure_ascii=False) for a in answers) + "\n",
        encoding="utf-8")
    Path(args.meta).write_text(
        "\n".join(json.dumps(m, ensure_ascii=False) for m in metas) + "\n",
        encoding="utf-8")
    print(f"\n-> {args.answers}  ({len(answers)} answers)")
    print(f"-> {args.meta}")

    if not args.score:
        return 0

    judge, judge_note = (None, "literal only")
    if args.judge == "llm":
        gen_models = sorted({m.get("model") for m in metas if m.get("model")})
        judge, judge_note = make_judge(gen_models)
        gen_model = ", ".join(gen_models) or "none"
        if judge is None:
            print(f"\n[warn] no llm judge: {judge_note}. Falling back to "
                  f"literal matching; claim facts will score 0.")
        else:
            print(f"\njudge model {judge_note}  (generator was {gen_model})")
    code, _rows = score(args.gold, args.answers, args.forbidden_numbers, judge)
    return code


if __name__ == "__main__":
    sys.exit(main())
