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

USAGE
    python generate.py --make-synthetic            # build the synthetic pair
    python generate.py --retrieved out/synthetic_retrieved.jsonl \\
                       --gold gold/synthetic.yaml --score
    python retrieve.py --dump out/retrieved.jsonl  # then, once cleared:
    python generate.py --allow-corpus "reason" --score
"""
import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

from bn_shared import canon, canon_pattern

# --------------------------------------------------------------- the prompt
# SEVEN RULES. Each comment names the failure the rule exists to prevent, so
# nobody "simplifies" a rule whose cost is invisible in the prompt itself.
SYSTEM_PROMPT = """You answer questions about BRAC microfinance operation manuals for field officers.

1. Answer ONLY from the CONTEXT below. Never infer, never fill a gap, never
   use knowledge from outside the context.
2. Name the MANUAL and the CLAUSE for every fact, e.g. "dabi_2025 §5.4".
3. Any answer about a premium or an exclusion must name the INSURER.
4. Two cases, and decide which you are in before answering.

   (a) The context contains NOTHING responsive to the question. Name the
       clauses you checked and what is missing, then stop. Do not mention,
       quote, cite or describe any figure from the context -- not even to say
       it does not apply. A figure and the question's subject in one sentence
       is what the reader retains; the caveat is what they lose.

   (b) The context answers PART of the question. Give the part it answers,
       with its clause, then name the part that is missing. Withholding a
       correct half is as wrong as inventing one.

   The test for (a) versus (b): does any figure or statement in the context
   answer any part of the question as asked? If yes, you are in (b). A figure
   about a DIFFERENT subject is not a partial answer -- it is case (a).
5. If a product's status is not active, lead with the status, then the terms.
   A status of "unknown" is NOT active; do not imply the product is available.
6. If the question is ambiguous across manuals or programmes, ask which one
   is meant instead of answering for one.
7. Answer in the language of the question, but keep role names, product names
   and clause citations in BENGALI exactly as the context spells them.

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
#   WORDING 3, "SCOPED", in force above. Splits the rule into case (a)
#   nothing responsive and case (b) partially responsive, with an explicit
#   test between them, and says outright that withholding a correct half is
#   as wrong as inventing one.
#
#   *** THE NOT-SAFE-TO-SHIP WARNING STANDS. *** The condition for removing
#   it is S-002 AND S-004 both passing on a model that previously LEAKED --
#   gemini-2.5-flash or gemini-3-flash-preview. As of this edit all four
#   previously-tested models are 429 quota-exhausted, so the scoped wording
#   has not been tested on any of them. Anything measured on a model that
#   never leaked cannot discharge this: the risk the scoped rule carries is
#   that its (a)/(b) test reopens the door, and only a leaker can show that.
#
# Rule 5 -- prevents: implying availability. products_catalogue.csv has 43
#   rows and 0 populated status values, so every status lookup returns
#   "unknown" today. Unknown is not active, and an answer that quotes terms
#   without the status reads as an offer.
# Rule 6 -- prevents: silently picking a programme. Where the manuals
#   diverge, answering for one is wrong half the time with no signal to the
#   reader; §3.9.6's bill-voucher approver is শাখা ব্যবস্থাপক in Dabi and
#   এলাকা ব্যবস্থাপক in Progoti.
# Rule 7 -- prevents: an unactionable answer. An officer told "the Area
#   Manager approves" cannot act on it; the form they need on the paperwork
#   is এএম. Translating a role out of Bengali destroys its usefulness while
#   looking helpful.

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

KEY_ENV = {"gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
KEY_HELP = {
    "gemini": "https://aistudio.google.com/apikey",
    "anthropic": "https://console.anthropic.com/settings/keys",
}


class ProviderError(RuntimeError):
    pass


class MissingKey(ProviderError):
    pass


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
                return (r.text or ""), {
                    "model": model, "retries": retries, "tried": tried,
                    "prompt_tokens": getattr(um, "prompt_token_count", None),
                    "output_tokens": getattr(um, "candidates_token_count", None),
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
                return "".join(b.text for b in r.content
                               if getattr(b, "type", "") == "text"), {
                    "model": model, "retries": retries, "tried": tried,
                    "prompt_tokens": getattr(u, "input_tokens", None),
                    "output_tokens": getattr(u, "output_tokens", None),
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
             "anthropic": _anthropic_complete,
             "local": _local_complete}
_STATE = {"provider": None, "pin": None, "cache": {}}


def complete(system, user, cache_system=True):
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
    text, meta = _BACKENDS[provider](system, user, cache_system, key, state)
    meta["provider"] = provider
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


def build_user_prompt(question, chunks):
    return (f"{format_context(chunks)}\n\n--- QUESTION\n{question}\n")


CITE_LINE = canon_pattern("CITATIONS")


def split_citations(text):
    """-> (answer, [citations]). The model is asked for a trailing CITATIONS
    line; parse it off rather than trusting it to be absent from the prose."""
    lines = (text or "").splitlines()
    cites, keep = [], []
    for ln in lines:
        stripped = ln.strip()
        if stripped.upper().startswith("CITATIONS:"):
            raw = stripped.split(":", 1)[1].strip()
            if raw and raw.lower() != "none":
                cites = [c.strip() for c in raw.split(";") if c.strip()]
            continue
        keep.append(ln)
    return "\n".join(keep).strip(), cites


ABSTAIN_TEXT = ("প্রদত্ত প্রসঙ্গে এই প্রশ্নের উত্তর নেই। "
                "(retrieval score below floor; no model call made)")


def answer_one(row, floor, system=SYSTEM_PROMPT):
    """-> (answer_record, meta_record). Abstains before calling the model when
    the top fused score is below the floor."""
    chunks = row.get("chunks") or []
    top = chunks[0]["fused_score"] if chunks else 0.0
    base = {"id": row["question_id"], "top_fused": top,
            "n_chunks": len(chunks)}
    if not chunks or top < floor:
        return ({"id": row["question_id"], "answer": ABSTAIN_TEXT,
                 "citations": []},
                dict(base, floor_fired=True, model=None, provider=None,
                     retries=0, prompt_tokens=None, output_tokens=None))
    user = build_user_prompt(row["question_bn"], chunks)
    text, meta = complete(system, user)
    answer, cites = split_citations(text)
    return ({"id": row["question_id"], "answer": answer, "citations": cites},
            dict(base, floor_fired=False, **meta))


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
def canary_selftest(verbose=True):
    """Convention 3. Three canaries, none invented.

    1. FORBIDDEN OVERBROAD, reused. validate_gold already owns a canary that
       asserts a §7.1 figure quoted as a loan ceiling scores a violation while
       a correct abstention does not. Copying it here would be a second
       implementation of the same rule, which is how the two copies of
       resolve_ref diverged. It is imported and run.
    2. PROMPT RULES INTACT. Seven numbered rules must be present. A prompt
       silently truncated to six still produces fluent answers, and the score
       moves for a reason nobody can see.
    3. SYNTHETIC IS SYNTHETIC. Every fixture literal is checked against
       productmatch: if a fixture ever names a real product, "the first run is
       synthetic" stops being true and real vocabulary reaches the provider.
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

    n_rules = sum(1 for i in range(1, 8)
                  if f"\n{i}. " in SYSTEM_PROMPT)
    ok2 = n_rules == 7
    ok &= ok2
    if verbose:
        print(f"  canary prompt_rules         {n_rules} of 7 numbered rules "
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
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

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

    configure(args.provider, args.model)
    try:
        guard = check_corpus_guard(args.retrieved, args.provider,
                                   args.allow_corpus)
    except ProviderError as e:
        print(f"\n{e}")
        return 1
    print(f"\nprovider {args.provider}  corpus-guard: {guard}")
    print(f"floor {args.floor}  context {args.retrieved}")

    if args.quota_report:
        print()
        print("QUOTA STATE (one probe call per model, before generating)")
        quota_report([args.model] if args.model else list(MODEL_PREFERENCE))

    rows = read_retrieved(args.retrieved)
    answers, metas = [], []
    for row in rows:
        try:
            a, m = answer_one(row, args.floor)
        except MissingKey as e:
            print(f"\n{e}")
            return 1
        except NotImplementedError as e:
            print(f"\n{e}")
            return 1
        except ProviderError as e:
            print(f"\nprovider failed on {row['question_id']}: {e}")
            return 1
        m["corpus_guard"] = guard
        m["pinned_model"] = args.model
        answers.append(a)
        metas.append(m)
        tag = "FLOOR" if m["floor_fired"] else (m.get("model") or "?")
        print(f"  {a['id']:<7}{tag:<22}top={m['top_fused']:.4f} "
              f"retries={m.get('retries', 0)} cites={a['citations']}")

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
