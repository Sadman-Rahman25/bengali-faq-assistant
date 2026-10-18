"""app.py -- STEP 10. The Streamlit demo interface, for field officers.

WHAT THIS IS FOR. Everything upstream reports to a terminal: a score, a
meta row, a log line. This is the first surface a field officer sees, and a
surface hides things a log does not. The three the demo must not hide:

  1. WHICH MODEL ANSWERED. One provider, one pinned model, named on screen.
  2. WHY AN ABSTENTION HAPPENED. There are THREE ways this system declines
     to answer and they are not interchangeable: the score floor refuses
     before any model call, the post-filter discards an answer the model
     did produce, and the model itself refuses under rule 4 after reading
     the clauses. All three look identical on a page that does not say
     which fired -- "the system knows nothing" instead of "the system
     refused to guess", which is the opposite of what was built. The third
     is the one that arrives as ordinary prose with no marker on it; see
     the measurement at the abstention branch in main().
  3. WHETHER A NAMED PRODUCT IS STILL SOLD. The status registry is present
     and EMPTY: 43 rows, 0 statuses. Silence there is indistinguishable
     from "available" unless the emptiness is on the page.

RETRIEVAL AND GENERATION ARE NOT REIMPLEMENTED HERE. retrieve.Index.search
and generate.answer_one are called exactly as a run calls them, and this
file adds no scoring, no filtering and no prompt of its own. It is the
configuration, the chrome and the disclosure.

CONFIGURATION IS SET HERE, NOT INHERITED. The measured configuration below
is pinned in this file: the demo's model choice must be visible and must
not move when an evaluation changes GROQ_PREFERENCE or BFA_INDEX_DIR.

Run:
  streamlit run app.py
"""

import base64
import html
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from string import Template

import streamlit as st

import generate
import embed
import retrieve
from bnnorm import canon


# ------------------------------------------------------------------- brand
# ONE PLACE TO CORRECT. Every colour, font stack and size the chrome uses
# comes from here; nothing below writes a hex or a px for brand identity.
#
# MAGENTA IS MEASURED, NOT CONFIRMED. #EC008C is the exact pixel value of
# the flat magenta in assets/brac-logo.png, the lockup supplied for this
# build -- sampled, 236/0/140, 4,440 pixels of the image. It is NOT a hex
# handed over by the brand team and has not been checked against the
# printed brand manual. The brief's approximation, #E6007E, is kept below
# because it is close enough to look right on its own and wrong enough to
# see beside the logo. Correcting this one line corrects the header, the
# chips, the divider and the footer at once.
#
# KOHINOOR BANGLA IS THE BRAND BENGALI FACE AND IS NOT WEB-AVAILABLE.
# Noto Sans Bengali is a DEVIATION, taken deliberately: the alternative is
# whatever the browser falls back to, which renders conjuncts
# inconsistently across platforms. Swap it when a licensed webfont exists.
BRAND = {
    "magenta": "#EC008C",         # measured from the supplied logo; UNVERIFIED
    "magenta_approx": "#E6007E",  # the brief's approximation, kept for the diff
    "dark": "#2b2f38",            # the top bar
    "ink": "#1d1f24",             # body text
    "band": "#f7f7f7",            # the answer band
    "rule": "#e6e6e6",            # thin borders
    "muted": "#6b7078",           # chrome labels
    # Latin. Helvetica Neue is the brand face; Arial is the brand fallback.
    "latin": ('"Helvetica Neue", Helvetica, Arial, '
              '-apple-system, "Segoe UI", sans-serif'),
    # Bengali. DEVIATION, see above.
    "bengali": ('"Noto Sans Bengali", "Kohinoor Bangla", '
                '"Nirmala UI", sans-serif'),
    # Bengali sits at 21px against 13-14px English chrome. Not a preference:
    # at a matching point size Bengali renders visually smaller than Latin,
    # the figures ARE the answer, and a wrong figure at 14px is easy to miss.
    "bengali_px": "21px",
    "chrome_px": "13px",
}

# ------------------------------------------------------- the configuration
# THE MEASURED CONFIGURATION, pinned. Each line is a decision made upstream;
# none of it is read from the environment.
PROVIDER = "groq"
# PINNED, and deliberately NOT generate.GROQ_PREFERENCE[0]. The preference
# order is the evaluation's -- it puts qwen first for a measured reason. The
# demo answers on gpt-oss-120b, which is what was measured end to end, and
# pinning also disables the fallthrough: if it fails, the demo says so
# rather than quietly answering as a different model.
MODEL = "openai/gpt-oss-120b"
K = 5
# GEMINI, dim 768, hosted. Switched 2026-10-18; see the journal (4l) for
# why on an incomplete comparison. BFA_INDEX_DIR still overrides, so
# `BFA_INDEX_DIR=out/index streamlit run app.py` puts LaBSE back on a
# machine that can load it -- which this one currently cannot.
#
# The default is the literal below rather than the env var alone, so an
# evaluation that exports BFA_INDEX_DIR cannot move the demo without the
# person running it having typed the path.
INDEX_DIR = Path(os.environ.get("BFA_INDEX_DIR", "out/index_gemini"))
POST_FILTER = True
GLOSSARY = False                      # measured, not adopted; see generate.py
# THE FLOOR BELONGS TO THE INDEX AND TO ITS FUSION WEIGHTS.
#
# generate.SCORE_FLOOR is 0.4044: the lowest top-fused score at which a gold
# chunk was actually retrieved, measured on LaBSE at the old 0.8/0.4 weights.
# It is a number on that scale and transfers to nothing else.
#
# On the Gemini index at those same old weights the equivalent came out at
# 0.7002. That figure is NOT used here, because the weights changed in the
# same session: the sweep moved this index to pure dense (1.0/1.0), so the
# fused score IS the cosine now and 0.7002 was measured on a different
# quantity. Applying it unchanged would abstain on ordinary answers --
# Gemini cosines for related Bengali text sit in the same 0.7-0.9 band --
# and a floor that silently refuses good answers is worse than no floor.
#
# 0.0 keeps exactly the guarantee the LaBSE floor actually delivered. The
# journal is explicit that at 0.4044 it "fires on nothing in this sample"
# and guards only the degenerate case; `not chunks` in generate.answer_one
# still catches an empty retrieval, which is that case.
#
# TO FINISH IT, when the embedding quota resets:
#   python <scratchpad>/derive_floor.py out/index_gemini
# and put the "lowest score with a hit" here. It could not be re-derived on
# 2026-10-18 because the day's embedding quota was spent building the index.
INDEX_FLOOR = {"out/index_gemini": 0.0}
FLOOR = INDEX_FLOOR.get(str(INDEX_DIR).replace("\\", "/"),
                        generate.SCORE_FLOOR)

# Shown when the hosted embedder cannot be reached or the day's quota is
# spent. Bengali only, and it says what the reader can do -- "try again
# later" is actionable; a 429 is not.
# THE PANEL A FIELD OFFICER READS. One Bengali sentence each, no score, no
# threshold, no English. The numbers that used to be here -- the fused
# score, the floor, the matched figure -- are operator information and are
# written to the meta row, which is where every other diagnostic already
# lives. A reader who cannot act on "0.4012 below a floor of 0.4044" is
# being shown it only because it was easy to print.
FLOOR_NOTE_BN = ("ম্যানুয়ালে এই প্রশ্নের সাথে মেলে এমন কিছু পাওয়া যায়নি। "
                 "পণ্যের নাম বা ধারা নম্বর লিখে আবার চেষ্টা করুন।")
FILTER_NOTE_BN = ("এই প্রশ্নের নির্ভরযোগ্য উত্তর দেওয়া যাচ্ছে না, "
                  "তাই কোনো সংখ্যা দেখানো হয়নি।")

EMBEDDER_DOWN_BN = ("এই মুহূর্তে সেবাটি ব্যবহার করা যাচ্ছে না। "
                    "কিছুক্ষণ পর আবার চেষ্টা করুন।")

# The gold files the post-filter draws its constraints from. The filter can
# only fire on a question it has a record for; see gold_by_text.
GOLD_FILES = ("gold/bootstrap.yaml", "gold/bootstrap_livestock_grow.yaml")
FORBIDDEN_NUMBERS = Path("out/forbidden.json")
EVAL_REPORT = Path("out/eval_report.json")
GOLD_MAIN = Path("gold/bootstrap.yaml")
# Where dedupe.py persists clause_display and the manual labels.
MANIFEST = Path("data/processed/chunk_manifest.json")

# THE CORPUS GUARD OVERRIDE, STATED IN FULL. generate.check_corpus_guard
# refuses to send real manual chunks to a hosted provider because the free
# tier may train on inputs and the data-protection decision is pending.
# This demo overrides it. The reason is written into every meta row, so a
# demo answer can never be mistaken for one produced under the guard.
DEMO_CORPUS_REASON = (
    "streamlit demo: real Dabi/Progoti manual chunks are sent to the groq "
    "free tier, which may use inputs for training; the data-protection "
    "decision is still pending and this override does not settle it"
)

OUT = Path("out")
DEMO_RETRIEVED = OUT / "demo_retrieved.jsonl"   # the context of the last query
DEMO_ANSWERS = OUT / "demo_answers.jsonl"
DEMO_META = OUT / "demo_meta.jsonl"

ASSETS = Path("assets")


# ------------------------------------------------------------------ assets
def _data_uri(path, mime):
    return f"data:{mime};base64," + base64.b64encode(
        Path(path).read_bytes()).decode("ascii")


@st.cache_data(show_spinner=False)
def assets():
    """The three brand assets, inlined. Streamlit serves no static path to
    HTML written into a markdown block, so they go in as data URIs.

    The divider carries the magenta as a literal so the file renders on its
    own; BRAND is the authority, so it is substituted here. One place to
    correct, still.
    """
    svg = (ASSETS / "brac-divider.svg").read_text(encoding="utf-8")
    svg = svg.replace("#EC008C", BRAND["magenta"])
    return {
        "logo": _data_uri(ASSETS / "brac-logo.png", "image/png"),
        # White reverse, for the magenta footer. Derived from the same
        # lockup, never a redrawn one: the mark and the wordmark stay
        # together and neither is rotated, distorted nor recoloured.
        "logo_white": _data_uri(ASSETS / "brac-logo-white.png", "image/png"),
        "divider": ("data:image/svg+xml;base64,"
                    + base64.b64encode(svg.encode("utf-8")).decode("ascii")),
    }


# --------------------------------------------------------------------- CSS
# A Template, not an f-string: CSS is made of braces.
CSS = Template("""
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+Bengali:wght@400;500;600&display=swap');

:root { --magenta: $magenta; --dark: $dark; --ink: $ink;
        --band: $band; --rule: $rule; --muted: $muted; }

html, body, [data-testid="stAppViewContainer"] { font-family: $latin;
        color: var(--ink); background: #fff; }

/* MEASURED, not assumed. Setting the face on html/body is not enough:
   Streamlit puts its own font-family on the markdown container these
   blocks live inside, so every .bfa-* element inherited Source Sans and
   the brand face never appeared. getComputedStyle said "Source Sans" on
   the chips, the flags and the header tagline while the stylesheet said
   Helvetica Neue. Named explicitly, and the Bengali rules come after so
   they still win where they apply. */
.bfa-header, .bfa-tag, .bfa-card, .bfa-label, .bfa-answer-en, .bfa-chip,
.bfa-flag, .bfa-meta, .bfa-chunk, .bfa-footer, .bfa-foot-links,
.bfa-foot-note, .bfa-budget { font-family: $latin; }

/* The page starts with an 8px bar at the very top edge, so Streamlit's own
   toolbar and the block padding go. This is the app frame, not a widget:
   no Streamlit control is restyled by it. */
header[data-testid="stHeader"] { display: none; }
.block-container { padding: 0 !important; max-width: 100% !important; }
/* THE FOOTER SITS AT THE BOTTOM OF A SHORT PAGE, and scrolls normally on
   a long one. Not fixed, not sticky -- it stays the last element in flow.
   MEASURED, because Streamlit's wrappers sit between this content and the
   viewport and only one of them is the scroll container:

     stMain                 flex column, overflow-y auto, h = 100vh  <- it
       stMainBlockContainer block, h = content
         stVerticalBlock    flex column, holds every section
           ...  last child is the footer's stElementContainer

   So stMain is already the column flexbox this needs; the two boxes
   between it and the sections just have to pass the height down, and the
   last child takes the slack.

   THE -16px FIRST, or the rest overshoots. Streamlit puts
   margin-bottom: -16px on EVERY stMarkdownContainer to trim the trailing
   margin of a markdown paragraph. These blocks are not paragraphs -- they
   are full-bleed bands -- so each one reported itself 16px shorter than it
   draws. Measured: band element 295 / container 279, footer 130 / 114,
   divider img 140 / 124. That is also why the divider overlapped the band
   by exactly 16px. Zeroed for the top-level sections only, so the chunk
   rows nested inside the expander keep Streamlit's spacing. */
[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"]
  > [data-testid="stElementContainer"] [data-testid="stMarkdownContainer"] {
    margin-bottom: 0; }
[data-testid="stMainBlockContainer"] { flex: 1; display: flex;
                                       flex-direction: column; }
[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] {
    flex: 1; }
[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"]
  > *:last-child { margin-top: auto; }

/* SCOPED TO THE TOP-LEVEL BLOCK, and the scoping is the whole point.
   `[data-testid="stVerticalBlock"] { gap: 0 }` applied to EVERY vertical
   block, including the one Streamlit creates inside the form -- measured:
   that block came back gap 0px, so the submit button's top equalled the
   input's bottom exactly (164 and 164) and the two were glued together.
   The page sections want no gap because each paints its own full-bleed
   band; the widgets inside a form want Streamlit's spacing. A direct-child
   selector gives the first without taking the second. */
[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] {
    gap: 0; }

/* Widgets keep their own shape. Colour and type only. */
.stTextInput input { font-family: $latin; font-size: 15px; color: var(--ink); }
.stTextInput input:focus { border-color: var(--magenta); box-shadow: none; }
.stTextInput label { font-size: $chrome; color: var(--muted); }
/* A CLOSED EXPANDER MUST NOT LAY OUT ITS BODY. Measured, collapsed:
   the <details> box was 40px tall -- the header alone -- while
   stExpanderDetails inside it was 97px with overflow visible, so the
   rows kept a layout box running 68px PAST the top of the footer. The
   browser does not paint them (a closed <details> hides its slot), so
   nothing looked wrong; the footer simply sat 68px higher than the
   content it was supposed to follow, mid-page, with dead space under it.
   This is a containment fix, not a restyle: it changes no colour, no
   type and no shape, only whether hidden content occupies the page. */
details:not([open]) [data-testid="stExpanderDetails"] { display: none; }
[data-testid="stExpander"] summary { font-size: 20px; color: var(--ink); }
[data-testid="stExpander"] summary svg { fill: var(--magenta); }
/* Colour and type only, still. The spinner and the submit button are
   Streamlit's own controls; neither is reshaped, only recoloured.
   MEASURED, not guessed. The spinner's arc is a <span> with
   border-radius 50% and an animated 2px top border -- there is no <svg>
   and no <i>, so rules written for those match NOTHING. That is the same
   "the rule exists but never applies" defect as the font, and the only
   way to know either way is to read the computed style off the running
   spinner. Only the arc carries a visible border, so colouring the top
   border of the spans here touches exactly one element. The label is a
   <p>, not a div. */
[data-testid="stSpinner"] span { border-top-color: var(--magenta) !important; }
[data-testid="stSpinner"] p { color: var(--muted); }

.st-key-mode { padding: 16px 22px 0; }
.st-key-ask { padding: 10px 22px 18px; }
.st-key-chunks { padding: 6px 22px 28px; }

.bfa-topbar { height: 8px; background: var(--dark); }
.bfa-header { display: flex; align-items: center; justify-content: space-between;
              padding: 22px; background: #fff; }
.bfa-header img { height: 38px; display: block; }
.bfa-tag { font-size: $chrome; color: var(--muted); text-align: right;
           line-height: 1.5; }

/* Stat cards. Four across, collapsing to two then one on a narrow
   screen -- an officer's phone should not scroll sideways. */
.bfa-stats { display: grid; grid-template-columns: repeat(4, 1fr);
             gap: 12px; padding: 0 22px 18px; }
@media (max-width: 900px) { .bfa-stats { grid-template-columns: 1fr 1fr; } }
@media (max-width: 520px) { .bfa-stats { grid-template-columns: 1fr; } }
.bfa-stat { background: #fff; border: 1px solid var(--rule);
            border-radius: 10px; padding: 14px 16px; }
.bfa-stat .v { font-family: $latin; font-size: 30px; font-weight: 700;
               line-height: 1.1; color: var(--magenta); }
.bfa-stat .l { font-family: $latin; font-size: $chrome; color: var(--ink);
               margin-top: 4px; }
.bfa-stat .n { font-family: $latin; font-size: 11px; color: var(--muted);
               margin-top: 3px; }

/* Tabs. Colour and type only; the control keeps its own shape. */
[data-testid="stTabs"] { padding: 0 22px; }
[data-testid="stTabs"] button[role="tab"] { font-family: $latin;
    font-size: 14px; color: var(--muted); }
[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
    color: var(--magenta); }
[data-testid="stTabs"] [data-baseweb="tab-highlight"] {
    background-color: var(--magenta); }

.bfa-soon { background: #fff; border: 1px dashed var(--rule);
            border-radius: 10px; padding: 22px; margin: 4px 22px 22px;
            font-family: $latin; font-size: 14px; line-height: 1.7;
            color: var(--ink); }
.bfa-soon .k { font-weight: 600; color: var(--magenta);
               text-transform: uppercase; letter-spacing: .12em;
               font-size: 12px; display: block; margin-bottom: 8px; }

.bfa-band { background: var(--band); padding: 22px; }
.bfa-card { background: #fff; border-radius: 10px; padding: 20px 22px;
            box-shadow: 0 1px 2px rgba(0,0,0,.07), 0 8px 20px rgba(0,0,0,.05); }
.bfa-label { font-size: 12px; font-weight: 600; letter-spacing: .14em;
             text-transform: uppercase; color: var(--magenta);
             margin: 0 0 12px; }
.bfa-answer { font-family: $bengali; font-size: $bengali_px; line-height: 1.8;
              color: var(--ink); }
.bfa-answer-en { font-size: 14px; line-height: 1.6; color: var(--muted); }
.bfa-chips { margin-top: 14px; }
.bfa-chip { display: inline-block; background: var(--magenta); color: #fff;
            font-size: 12px; line-height: 1.6; border-radius: 999px;
            padding: 3px 11px; margin: 0 6px 6px 0; }

/* Disclosure rows, in TWO weights, and the split is the point.
   MEASURED by looking at the rendered page: four magenta bars of identical
   weight stacked under one answer, and the one that said "this is a
   refusal, not a blank" looked exactly like the one that said "the
   registry holds 43 products". Everything emphasised is nothing
   emphasised. So:
     .bfa-flag        why THIS answer is not what the model first said --
                      the floor, the post-filter, the model's own refusal.
                      Magenta bar, full size. At most one ever shows.
     .bfa-flag.note   standing conditions true of every query -- an
                      unarmed filter, an empty status registry. Quieter,
                      grey, and they must stay ON the page: they are the
                      disclosures, they are just not the headline. */
.bfa-flag { border-left: 3px solid var(--magenta); background: #fff;
            padding: 10px 14px; margin-top: 14px; font-size: $chrome;
            line-height: 1.6; color: var(--ink); }
.bfa-flag.note { border-left: 2px solid var(--rule); font-size: 12px;
                 color: var(--muted); padding: 7px 12px; margin-top: 10px; }
.bfa-flag.note .k { color: var(--ink); }
.bfa-flag.warn { border-left-color: var(--dark); }
.bfa-flag .k { font-weight: 600; }
.bfa-flag .bn { font-family: $bengali; font-size: 16px; }

/* The token budget is a condition of the DAY, not of this answer, so it
   sits outside the answer card. Inside it, under the model line, it read
   as if it were about the answer above it. */
.bfa-budget { background: var(--band); padding: 0 22px 18px; }
.bfa-budget .inner { border-left: 3px solid var(--dark); background: #fff;
                     padding: 10px 14px; font-size: $chrome;
                     line-height: 1.6; color: var(--ink); }
.bfa-meta { margin-top: 12px; font-size: 12px; color: var(--muted); }
.bfa-meta code { font-size: 12px; color: var(--muted); }

.bfa-divider { width: 100%; height: 140px; display: block; }

.bfa-chunk { display: flex; gap: 16px; align-items: baseline;
             padding: 9px 0; border-bottom: 1px solid var(--rule);
             font-size: $chrome; color: var(--ink); }
.bfa-chunk .id { min-width: 180px; }
.bfa-chunk .cl { min-width: 190px; color: var(--muted); }
.bfa-chunk .sc { color: var(--muted); }
/* The badge, and why it is an outline rather than a solid magenta pill:
   the citation chips in the answer card are solid magenta, and a second
   solid magenta element down here would read as the same kind of thing.
   It is not -- a chip is what the model claimed, a badge is where that
   claim landed in the evidence. */
.bfa-chunk .cited { font-size: 10px; font-weight: 700; letter-spacing: .08em;
                    color: var(--magenta); border: 1px solid var(--magenta);
                    border-radius: 3px; padding: 1px 5px; margin-left: 2px; }
.bfa-orphan { border-left: 3px solid var(--magenta); background: #fff;
              padding: 10px 14px; margin: 4px 0 12px; font-size: $chrome;
              line-height: 1.6; color: var(--ink); font-family: $latin; }
.bfa-orphan .k { font-weight: 600; }

.bfa-footer { background: var(--magenta); color: #fff; padding: 18px 22px 20px; }
.bfa-foot-top { display: flex; align-items: center;
                justify-content: space-between; }
/* The lockup, reversed, at 26px. The mark and the wordmark are one image:
   scaling the lockup puts the wordmark at about 21px, which is where the
   design wants it, without separating the two. */
.bfa-foot-top img { height: 26px; display: block; }
.bfa-foot-links { font-size: $chrome; display: flex; gap: 22px; color: #fff; }
.bfa-foot-rule { height: 1px; background: rgba(255,255,255,.4);
                 margin: 14px 0 11px; }
.bfa-foot-note { font-size: 12px; line-height: 1.65;
                 color: rgba(255,255,255,.95); max-width: 820px; }
""").safe_substitute(
    magenta=BRAND["magenta"], dark=BRAND["dark"], ink=BRAND["ink"],
    band=BRAND["band"], rule=BRAND["rule"], muted=BRAND["muted"],
    latin=BRAND["latin"], bengali=BRAND["bengali"],
    bengali_px=BRAND["bengali_px"], chrome=BRAND["chrome_px"])


# --------------------------------------------------------------- the parts
@st.cache_resource(show_spinner="Loading the LaBSE index...")
def index():
    """One index per process. Given the directory explicitly."""
    return retrieve.Index(INDEX_DIR)


@st.cache_data(show_spinner=False)
def gold_by_text():
    """{canon(question text): gold question} across the gold files.

    THE POST-FILTER NEEDS A GOLD RECORD, and a typed question has no id. Its
    constraints -- must_not_contain_literals, forbidden_numbers_from_clause
    -- are per question and cannot be inferred from the text, so a question
    with no record has NOTHING to filter against. That is reported on screen
    rather than rendered as a clean pass: an unarmed filter and a filter
    that found nothing look identical from the outside.

    Variants are indexed too; a field officer is likelier to type one.
    """
    out = {}
    for path in GOLD_FILES:
        for q in generate.load_questions_by_id(path).values():
            for text in [q.get("question_bn")] + list(
                    q.get("question_variants") or []):
                if text:
                    out[canon(text).strip()] = q
    return out


@st.cache_data(show_spinner=False)
def forbidden_numbers():
    if FORBIDDEN_NUMBERS.exists():
        return json.loads(FORBIDDEN_NUMBERS.read_text(encoding="utf-8"))
    return {}


@st.cache_data(show_spinner=False)
def product_names():
    """{product_key: display name} from productmatch. Convention 2: no
    product literal is typed in this file, in a pattern or anywhere else."""
    import productmatch as pm
    return {key: name for key, name, _alias, _pat in pm.NAMED}


@st.cache_data(show_spinner=False)
def manifest(path=MANIFEST):
    """-> ({chunk_id: row}, {manual_id: display name}).

    THE DISPLAY FORMS ARE READ, NEVER DERIVED HERE. dedupe.clause_pair is
    the one parser and the manifest is where its output is persisted; this
    file holds no clause regex and no manual label, so a rename lands in
    one place and reaches the page without anyone editing the page.
    """
    if not Path(path).exists():
        return {}, {}
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return ({r["chunk_id"]: r for r in doc.get("chunks") or []},
            doc.get("manuals") or {})


def cite_display(cite, chunks):
    """One model-written citation, rendered as the manual prints it. -> str

    The model writes its own CITATIONS line -- "progoti §1.1.2" -- built
    from the corpus tag and the FOLDED clause it was shown. Both are the
    matching forms, and neither is what an officer can look up: the manual
    is titled in Bengali and prints ১.১.২.

    Resolved through the chunks this query actually retrieved, so a
    citation is only re-rendered when it corresponds to something the model
    was really given. Anything else is returned UNCHANGED and therefore
    shows on the page in its raw form -- which is the useful signal, not a
    cosmetic failure: a citation that matches no retrieved chunk is one the
    model made up, and dressing it in a Bengali title would hide that.
    """
    rows, manuals = manifest()
    c = canon(cite)
    for ch in chunks:
        r = rows.get(ch["chunk_id"])
        if not r or not r.get("clause") or not r.get("clause_display"):
            continue
        if r["manual_id"] not in c:
            continue
        # digit-bounded: 1.1.2 must not match inside 1.1.20
        if not re.search(r"(?<!\d)" + re.escape(r["clause"]) + r"(?!\d)", c):
            continue
        return (f'{manuals.get(r["manual_id"], r["manual_id"])} '
                f'§{r["clause_display"]}')
    return cite


@st.cache_data(show_spinner=False)
def stat_cards():
    """[(value, label, note)] -- every number READ, none typed.

    A card whose number cannot be read is not rendered. A dashboard that
    hardcodes a figure is a screenshot with extra steps: it keeps saying
    867 after the corpus changes, and nobody finds out from the page.
    """
    cards = []
    rows, manuals = manifest()
    if rows:
        cards.append((f"{len(rows):,}", "indexed chunks",
                      "data/processed/chunk_manifest.json"))
    if manuals:
        cards.append((str(len(manuals)), "manuals",
                      "chunk_manifest.json - manuals"))
    if EVAL_REPORT.exists():
        try:
            ev = json.loads(EVAL_REPORT.read_text(encoding="utf-8"))
        except ValueError:
            ev = None
        if ev:
            viol = sum(1 for r in ev if r.get("violations"))
            cards.append((f"{viol}/{len(ev)}", "forbidden-figure violations",
                          "out/eval_report.json"))
            # THE AGGREGATION IS IMPORTED, NOT RESTATED. This card first
            # computed its own rate by filtering on `citation_achievable`
            # and read 6/10 = 60%, where the metric's own rule gives
            # 7/11 = 63.6% -- because a PASS IS PROOF OF ACHIEVABILITY and
            # the restatement dropped demonstrated successes. One number
            # on a dashboard disagreeing with the same number in the
            # report is worse than either being wrong alone. See 4h.
            from gold_eval import citation_accuracy
            hit, scored, _unach = citation_accuracy(ev)
            if scored:
                cards.append((f"{100 * hit / scored:.1f}%",
                              "citation accuracy",
                              f"on achievable questions ({hit}/{scored})"))
    return cards


@st.cache_data(show_spinner=False)
def gold_menu():
    """[(label, question)] for the browse dropdown. Read, never typed.

    The unanswerable ones are marked. A viewer who picks one and sees a
    refusal should be able to tell that the refusal is CORRECT -- the
    manual has no answer -- rather than reading it as the demo failing.
    """
    import yaml
    if not GOLD_MAIN.exists():
        return []
    doc = yaml.safe_load(GOLD_MAIN.read_text(encoding="utf-8")) or {}
    out = []
    for q in doc.get("questions") or []:
        text = (q.get("question_bn") or "").strip()
        if not text:
            continue
        ans = str(q.get("answerable", "")).lower()
        mark = {"no": "  [no answer in the manual]",
                "partial": "  [partly answerable]"}.get(ans, "")
        out.append((f"{q['id']}  {text}{mark}", text))
    return out


def _mf_manual(c):
    """The chunk's manual, as a human names it. Falls back to the corpus
    tag, visibly, when the manifest has no label for it."""
    rows, manuals = manifest()
    mid = (rows.get(c["chunk_id"]) or {}).get("manual_id") or c["manual_id"]
    return manuals.get(mid) or mid


def _mf_clause(c):
    """The chunk's clause as printed. Falls back to the folded form rather
    than to nothing -- a number in the wrong script still beats silence."""
    r = manifest()[0].get(c["chunk_id"]) or {}
    return r.get("clause_display") or r.get("clause") or c.get("clause") or ""


def status_verification_column(path=retrieve.CATALOGUE):
    """The catalogue column recording WHO confirmed a status, or None.

    MEASURED 19 Sep 2026: there is no such column. products_catalogue.csv
    carries product_key, entity_type, name_bn, aliases, defining_chunk_id,
    chunk_ids and status -- and nothing that says a status was checked
    against anything. So every status the registry may come to hold is, on
    the evidence in the file, UNCONFIRMED, and this returning None is what
    makes the page say so instead of presenting a bare status as fact. It
    reads the header rather than asserting the absence, so the day someone
    adds the column the claim stops being made.
    """
    import csv
    if not Path(path).exists():
        return None
    with Path(path).open(encoding="utf-8-sig") as fh:
        fields = csv.DictReader(fh).fieldnames or []
    for name in ("status_verified", "status_source", "status_confirmed_by"):
        if name in fields:
            return name
    return None


def cite_match(citations, chunks):
    """Which retrieved chunks each citation names. -> (by_chunk, orphans)

    by_chunk  {chunk_id: [citation, ...]} for every chunk a citation names
    orphans   [citation, ...] naming NO retrieved chunk

    ONE COMPARISON, SHARED WITH THE METRIC. gold_eval.norm_cite is what
    cite_ok uses to decide whether a citation matched, and it is what this
    uses to decide whether to badge a chunk. A second matcher here would
    drift from the score, and the drift would be invisible in the worst
    way: the page would show CITED on a chunk the metric counted as a miss,
    which is a page confidently disagreeing with the number in the report.

    ORPHANS ARE THE POINT. A citation matching nothing retrieved is either
    fabricated or scoped to a manual the query never searched, and it is
    the one thing this view can surface that no score on its own will --
    metric-side it is just "citation wrong", with no way to see that the
    clause was never in the context. Surfaced above the list, not hidden in
    it, because an absent row is not something a reader notices.

    SEVERAL CHUNKS PER CITATION IS NORMAL HERE, not an ambiguity to resolve.
    516 of 867 chunks are byte-identical across the two manuals and the same
    clause number is often carried by several retrieved chunks -- §1.1.2
    alone can return three. Every chunk a citation legitimately names is
    badged; picking one would assert a precision the citation does not have.
    """
    from gold_eval import norm_cite
    by_chunk, orphans = {}, []
    for cite in citations or []:
        c = norm_cite(cite)
        hit = False
        for ch in chunks:
            if not ch.get("clause"):
                continue
            # The chunk's own citation, in the form the model was shown.
            own = norm_cite(f'{ch["manual_id"]} §{ch["clause"]}')
            # cite_ok's test, both directions, unchanged.
            if own and (own in c or c in own):
                by_chunk.setdefault(ch["chunk_id"], []).append(cite)
                hit = True
        if not hit:
            orphans.append(cite)
    return by_chunk, orphans


def _is_memory_exhaustion(exc):
    """Is this the machine running out of room, rather than a real fault?

    TWO DIFFERENT EXCEPTIONS, and catching only the obvious one is not
    enough. Measured on this machine, 19 Sep 2026, loading the same model
    under the same conditions:

      MemoryError                              the allocator gave up
      OSError 1455, "The paging file is too    the COMMIT ceiling was hit,
      small for this operation to complete"    in safetensors' mmap

    The second is the one that actually fired during the live incident, and
    it is an OSError -- so a handler written for MemoryError alone still
    renders a traceback for the failure most likely to happen here. §4f
    records why: the binding limit on this machine is commit charge, not
    physical RAM, and Windows reports a commit failure as 1455.

    NARROW ON PURPOSE. FileNotFoundError is an OSError too, and a missing
    or moved index must NOT be reported as "out of memory" -- that would
    send someone to close applications over a broken path. Anything that is
    not recognisably the commit ceiling is re-raised.
    """
    if isinstance(exc, MemoryError):
        return True
    return (isinstance(exc, OSError)
            and (getattr(exc, "winerror", None) == 1455
                 or "paging file is too small" in str(exc).lower()))


def _is_abstention(text):
    """Did the MODEL refuse, in its own words? -> bool

    gold_eval's detector, not a second one. The page and metric 3 must
    agree about what counts as an abstention: a page that called something
    a refusal while the score counted it as an answer would be worse than
    silent, because it would look authoritative.
    """
    from gold_eval import is_abstention
    return is_abstention(text or "")


def esc(s):
    return html.escape(s or "")


def _ts():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def ask(question):
    """Retrieve, guard, budget-check, generate, post-filter. -> render dict.

    Calls retrieve and generate as a run does. The only thing added is that
    every branch it can take is reported back, so nothing that changed the
    answer is invisible on the page.
    """
    qid = "D-" + _ts()

    # THE MACHINE, NOT THE INDEX. Loading LaBSE and encoding the query are
    # the two steps here that can exhaust memory, and on this machine they
    # do: journal §4f records the measurement -- the same Index() call that
    # succeeded at 14:53 raised MemoryError at 19:19, at 11.5 GB commit
    # charge against a 14.4 GB limit, with the editors and a browser open.
    # The index files were verified intact afterwards.
    #
    # WHY THIS IS CAUGHT RATHER THAN ALLOWED TO PROPAGATE. Uncaught, this
    # renders a full traceback in the answer area, whose top frames name
    # retrieve.Index and faiss -- so the obvious reading is "the index is
    # broken", which is precisely the wrong conclusion and the expensive
    # one. It is a property of the machine, and the page says so.
    #
    # The cache entry is dropped so the next ask retries the load: the
    # advice on screen is to close applications and reload, and that advice
    # has to actually work.
    loaded = False
    try:
        ix = index()
        loaded = True
        chunks = ix.search(question, k=K)
    except embed.EmbeddingUnavailable as exc:
        # THE HOSTED EMBEDDER IS DOWN OR THE DAY'S QUOTA IS SPENT.
        #
        # A plain Bengali sentence, never a traceback. Without an embedder
        # there is no retrieval, and without retrieval there is no answer --
        # but that is a fact about the service, not about the manual, and the
        # reader is a field officer who can only act on one of those. The
        # operator detail goes to the meta row with everything else.
        #
        # NOT an abstention. An abstention is a decision the system made
        # about the question; this is the system not having run. The meta row
        # says answered=False and names the failure so the two can never be
        # counted together.
        meta = {"id": qid, "run_id": qid, "source": "demo",
                "question_bn": question, "k": K,
                "index_dir": str(INDEX_DIR), "answered": False,
                "infrastructure_failure": "EmbeddingUnavailable",
                "failure_detail": str(exc)[:200],
                "failed_at": "retrieval: embedding the question",
                "note": "hosted embedder unreachable or daily quota spent",
                "provider": None, "model": None, "pinned_model": MODEL,
                "floor_fired": False, "filter_fired": False,
                "filter_hits": [], "top_fused": None, "n_chunks": 0,
                "prompt_tokens": None, "output_tokens": None}
        OUT.mkdir(parents=True, exist_ok=True)
        with DEMO_META.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(meta, ensure_ascii=False) + "\n")
        return {"id": qid, "chunks": [], "infra": True, "meta": meta,
                "bn_message": EMBEDDER_DOWN_BN}
    except (MemoryError, OSError) as exc:
        if not _is_memory_exhaustion(exc):
            raise               # a real fault: a missing index, a bad path
        # ONLY DROP THE CACHE IF THE LOAD ITSELF FAILED. If the index was
        # already resident and it was the query encode that ran out of
        # room, the index is fine -- clearing it would throw away a good
        # model and make the NEXT question pay a full reload it did not
        # need, on a machine that has just demonstrated it cannot afford
        # one. Streamlit does not cache a failed cache_resource call, so
        # when the load is what failed there is nothing cached anyway and
        # this is belt and braces.
        if not loaded:
            try:
                index.clear()
            except Exception:             # clearing is best-effort; the
                pass                      # message matters more
        # RECORDED AS INFRASTRUCTURE, NOT AS AN ANSWER. No answer row is
        # written at all -- there is no answer, and a placeholder one would
        # be counted by anything that reads the answers file. The meta row
        # is written because the attempt happened and should be traceable,
        # and it carries answered=False plus the failure so it can never be
        # mistaken for an abstention, which is a decision the system made.
        meta = {"id": qid, "run_id": qid, "source": "demo",
                "question_bn": question, "k": K,
                "index_dir": str(INDEX_DIR),
                "answered": False,
                "infrastructure_failure": type(exc).__name__,
                "failure_detail": str(exc)[:200],
                "winerror": getattr(exc, "winerror", None),
                "failed_at": "retrieval: loading the index or encoding "
                             "the query",
                "note": "machine commit ceiling, not the index; "
                        "see journal 4f",
                "provider": None, "model": None, "pinned_model": MODEL,
                "floor_fired": False, "filter_fired": False,
                "filter_hits": [], "top_fused": None, "n_chunks": 0,
                "prompt_tokens": None, "output_tokens": None}
        OUT.mkdir(parents=True, exist_ok=True)
        with DEMO_META.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(meta, ensure_ascii=False) + "\n")
        return {"id": qid, "chunks": [], "infra": True, "meta": meta}

    row = {"question_id": qid, "question_bn": question, "chunks": chunks}

    # The context of this query, written before anything is sent. It is also
    # what the corpus guard is asked about: the guard decides from the file,
    # so the demo goes through the same door a run does rather than
    # asserting its own answer to the question the guard asks.
    OUT.mkdir(parents=True, exist_ok=True)
    DEMO_RETRIEVED.write_text(
        json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        guard = generate.check_corpus_guard(
            DEMO_RETRIEVED, PROVIDER, DEMO_CORPUS_REASON)
    except generate.ProviderError as e:
        return {"id": qid, "chunks": chunks, "error": str(e)}

    # THE TPD LEDGER. Groq's free tier is 200,000 tokens per day per model
    # and the response headers do not carry it, so the ceiling otherwise
    # arrives as a refusal part-way through. Checked before the call: a
    # demo that dies on a provider error looks like a broken demo.
    #
    # THE FLOOR IS CHECKED FIRST, and the order is not cosmetic. A query
    # that abstains below the floor never calls the provider and costs
    # nothing, so running the preflight over it would refuse a free answer
    # on a budget it was never going to touch -- measured here on 19 Sep
    # 2026, with the day's budget at 99% and a below-floor question
    # returning "REFUSING TO START" instead of its abstention. answer_one
    # owns the floor decision; this only asks whether it is about to fire.
    top = chunks[0]["fused_score"] if chunks else 0.0
    if chunks and top >= FLOOR:
        notes = []
        ok, budget_msg = generate.tpd_preflight(
            PROVIDER, MODEL, [row], out=notes.append)
        if not ok:
            return {"id": qid, "chunks": chunks, "error": budget_msg,
                    "budget": budget_msg}
    else:
        budget_msg = (f"no provider call: top fused {top:.4f} is below the "
                      f"floor of {FLOOR}, so nothing counts against today's "
                      f"budget")

    generate.configure(PROVIDER, MODEL)
    try:
        answer, meta = generate.answer_one(row, FLOOR, glossary=GLOSSARY)
    except generate.ProviderError as e:
        return {"id": qid, "chunks": chunks, "error": str(e)}

    gold = gold_by_text().get(canon(question).strip())
    if POST_FILTER and gold is not None:
        answer, fmeta = generate.post_filter(answer, gold, forbidden_numbers())
        meta.update(fmeta)
    else:
        meta.update({"filter_fired": False, "filter_hits": [],
                     "filter_skipped": ("post-filter off" if not POST_FILTER
                                        else "question not in gold file")})
    meta["corpus_guard"] = guard
    meta["pinned_model"] = MODEL
    meta["run_id"] = qid
    meta["glossary"] = GLOSSARY
    # Demo-only additions to the run's meta shape. A demo row must be
    # findable, and must never be mistaken for an evaluation row.
    meta["source"] = "demo"
    meta["question_bn"] = question
    meta["k"] = K
    meta["index_dir"] = str(INDEX_DIR)
    meta["tpd_note"] = budget_msg

    for path, rec in ((DEMO_ANSWERS, answer), (DEMO_META, meta)):
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    return {"id": qid, "answer": answer, "meta": meta, "chunks": chunks,
            "guard": guard, "budget": budget_msg,
            "gold_id": (gold or {}).get("id")}


def product_rows(question):
    """(rows, populated, total) for the products THE QUESTION names.

    Read from the registry AT QUERY TIME, never from a chunk -- a chunk
    records what the manual said, not whether the product is still sold.

    QUESTION-NAMED ONLY, and the union with the chunks is what this used to
    do. Measured on the rendered page: a service-charge question about one
    product produced three "unknown" rows for products harvested out of the
    retrieved chunks -- none of them asked about, and the one the officer
    typed absent, because it is a programme rather than a NAMED product. A
    panel that answers a question nobody asked crowds out the one row that
    would have mattered. If the question names no product there is no panel
    at all: a status the officer did not ask for is noise, and noise in the
    disclosure is how a real disclosure stops being read.
    """
    keys = list(retrieve.products_named(question))
    if not keys:
        return [], 0, 0
    status, populated, total = retrieve.product_status()
    verified_col = status_verification_column()
    names = product_names()
    rows = []
    for k in keys:
        raw = (status.get(k) or "").strip()
        if not raw:
            rows.append((names.get(k, k), "unknown: no status recorded"))
        elif verified_col is None:
            # A status with nothing in the registry recording who checked
            # it. Shown as what it is. Dropping the qualifier would turn an
            # unconfirmed entry into an availability claim the registry
            # cannot support, which is the failure this whole panel exists
            # to prevent.
            rows.append((names.get(k, k), f"{raw} - UNVERIFIED"))
        else:
            rows.append((names.get(k, k), raw))
    return rows, populated, total


# -------------------------------------------------------------- the render
def main():
    st.set_page_config(page_title="BRAC Field Assistant",
                       layout="wide", initial_sidebar_state="collapsed")
    st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)
    a = assets()

    st.markdown(
        '<div class="bfa-topbar"></div>'
        '<div class="bfa-header">'
        f'<img src="{a["logo"]}" alt="BRAC">'
        '<div class="bfa-tag">Field Assistant &middot; '
        'Dabi &amp; Progoti operations manuals</div>'
        '</div>', unsafe_allow_html=True)

    cards = stat_cards()
    if cards:
        st.markdown(
            '<div class="bfa-stats">'
            + "".join(f'<div class="bfa-stat"><div class="v">{esc(v)}</div>'
                      f'<div class="l">{esc(l)}</div>'
                      f'<div class="n">{esc(note)}</div></div>'
                      for v, l, note in cards)
            + '</div>', unsafe_allow_html=True)

    # TABS, NOT SEGMENTED BUTTONS, and the reason is state. Streamlit's
    # tabs switch client-side: changing tab does not re-run the script, so
    # the answer on screen survives a look at the quiz and back. A
    # segmented control is a widget -- every click is a re-run, which would
    # put the ask-guard and the quiz's one-draw-per-session through a cycle
    # they do not need. The cost is that every tab's body is rendered on
    # every run; that is affordable only because two of the three make no
    # model call and the third is guarded.
    ask_tab, faq_tab, quiz_tab = st.tabs(
        ["Ask anything", "50 FAQ", "Test yourself"])
    with ask_tab:
        render_ask(a)
    with faq_tab:
        render_faq()
    with quiz_tab:
        import quiz_app
        quiz_app.render_quiz()

    links = "".join(f"<span>{t}</span>" for t in
                    ("Dabi", "Progoti", "Livestock Grow", "Corpus gaps"))
    st.markdown(
        '<div class="bfa-footer"><div class="bfa-foot-top">'
        f'<img src="{a["logo_white"]}" alt="BRAC">'
        f'<div class="bfa-foot-links">{links}</div></div>'
        '<div class="bfa-foot-rule"></div>'
        '<div class="bfa-foot-note">'
        'Answers are drawn from the operations manuals and may be '
        'incomplete. Verify against the printed manual before acting. '
        'Product status is not yet confirmed, so availability is shown as '
        'unknown. This notice stays until the status sheet comes back.'
        '</div></div>', unsafe_allow_html=True)



def render_ask(a):
    """SURFACE 1. The answer page, unchanged.

    Lifted into a function so it can sit in a tab; not one line of its
    order or behaviour is different. The mode control above it is the
    only addition, and it only decides what text reaches the same input.
    """
    gold = gold_menu()
    with st.container(key="mode"):
        mode = st.radio(
            "How would you like to ask?",
            ("Browse gold questions", "Ask your own"),
            horizontal=True, key="ask_mode",
            label_visibility="collapsed")
        if mode == "Browse gold questions" and gold:
            labels = [lbl for lbl, _t in gold]
            pick = st.selectbox(
                f"{len(gold)} gold questions - the evaluation set",
                labels, index=None, key="gold_pick",
                placeholder="Choose a question to put it in the box")
            # Set the INPUT, do not bypass it: the chosen question then
            # travels the same path a typed one does, through the same
            # guard, so nothing about the answer surface special-cases it.
            if pick and st.session_state.get("_last_pick") != pick:
                st.session_state["q"] = dict(gold)[pick]
                st.session_state["_last_pick"] = pick

    with st.container(key="ask"):
        # The placeholder is BANGLISH, not Bengali, and not because Bengali
        # would not fit. A Bengali example would put a bare product name in
        # this file for Convention 2 to trip over, and it would advertise
        # only the input a keyboard-equipped officer can type. Banglish is
        # the harder half -- embed.expand_banglish exists for it -- so it is
        # the half worth showing on an empty page.
        # A FORM, so the question is submitted once -- by Enter or by
        # the button -- rather than on every keystroke's rerun. The button
        # is type="primary", which takes primaryColor from
        # .streamlit/config.toml, i.e. the brand magenta; it is not
        # hand-styled here.
        with st.form("ask", border=False, clear_on_submit=False):
            st.text_input(
                "Ask a question - Bengali, or Banglish", key="q",
                placeholder="progoti loner service charge koto?")
            st.form_submit_button("Search the manuals", type="primary")
        question = st.session_state.get("q") or ""

    # THE BUDGET, BEFORE ANYTHING IS TYPED. Warned, not enforced here: a
    # demo should say the day is nearly spent while there is still time to
    # shorten it, not fail on the question someone came to see.
    used = generate.ledger_used(PROVIDER, MODEL)
    limit = generate.TPD_LIMIT.get(PROVIDER)
    budget_warn = ""
    if limit and used / limit >= generate.TPD_WARN_AT:
        budget_warn = (
            '<div class="bfa-budget"><div class="inner">'
            '<span class="k">Token budget nearly '
            f'spent.</span><br>{used:,} of {limit:,} tokens used today on '
            f'{esc(MODEL)}. The limit is per model per day; a query that '
            'will not fit is refused before it is sent, not part-way '
            'through.</div></div>')

    # ONLY ON A NEW QUESTION. Streamlit re-runs the script on every
    # interaction, so opening the chunk list would otherwise re-ask the
    # model and spend the budget a second time on the same question.
    if question and st.session_state.get("_asked") != question:
        with st.spinner("Searching the manuals..."):
            st.session_state["_result"] = ask(question)
        st.session_state["_asked"] = question
    result = st.session_state.get("_result") if question else None

    body = []
    if result is None:
        body.append(
            '<div class="bfa-answer-en">Ask about a product, a clause or an '
            'approval limit. Answers are drawn from the Dabi and Progoti '
            'operations manuals only.</div>')
    elif result.get("infra"):
        # THE FOURTH CARD STATE. Not an abstention and not an answer: the
        # question was never put to anything. Said in the same voice as the
        # other three, and it names the cause, because the traceback this
        # replaces pointed at the index and the index is not at fault.
        body.append(
            '<div class="bfa-answer-en"><b>No answer. The machine ran out '
            'of memory before the question could be searched.</b></div>'
            '<div class="bfa-flag"><span class="k">Nothing is wrong with '
            'the manuals or the index.</span><br>Loading the search model '
            'needs more memory than this machine can commit right now. '
            'Two things help, and on this machine the second is usually '
            'the one that works:<br>'
            '&nbsp;&nbsp;1. Close other applications and reload the page.'
            '<br>'
            '&nbsp;&nbsp;2. Raise the Windows page file, then restart.'
            '<br>The limit here is the <b>commit ceiling</b>, not free '
            'RAM - at the last failure only 2.5 GB was actually resident '
            'against a 15.6 GB commit limit, so closing applications on '
            'its own may not free enough. The question was not sent to any '
            'model, and nothing was retrieved.</div>'
            '<div class="bfa-flag note"><span class="k">Recorded as an '
            'infrastructure failure.</span><br>The meta row for '
            f'<code>{esc(result["id"])}</code> carries '
            '<code>answered: false</code>, so this cannot be read later as '
            'a refusal - a refusal is a decision the system made, and this '
            'is not one.</div>')
    elif result.get("error"):
        body.append(
            '<div class="bfa-answer-en"><b>No answer. The provider was not '
            'called.</b></div>'
            '<div class="bfa-flag"><span class="k">Reason.</span><br>'
            f'{esc(result["error"])}</div>')
    else:
        answer, meta = result["answer"], result["meta"]
        body.append(f'<div class="bfa-answer">{esc(answer["answer"])}</div>')
        if answer.get("citations"):
            chips = "".join(
                f'<span class="bfa-chip">'
                f'{esc(cite_display(c, result["chunks"]))}</span>'
                for c in answer["citations"])
            body.append(f'<div class="bfa-chips">{chips}</div>')

        # 1. THE FLOOR. No model call was made. Say which, and say why.
        if meta.get("floor_fired"):
            body.append(
                '<div class="bfa-flag"><span class="bn">'
                + FLOOR_NOTE_BN + '</span></div>')
        # 2. THE POST-FILTER. The model answered; the answer was discarded.
        if meta.get("filter_fired"):
            body.append(
                '<div class="bfa-flag"><span class="bn">'
                + FILTER_NOTE_BN + '</span></div>')
        # 3. THE MODEL'S OWN ABSTENTION, and the reason this branch exists.
        # MEASURED 19 Sep 2026. The obvious test question for the floor --
        # a loan ceiling for a product whose ceiling the manuals do not
        # state -- does NOT reach the floor. It retrieves at 0.5165 against
        # a floor of 0.4044, because the clauses that NAME the product
        # score perfectly well; they simply do not carry the figure. So the
        # model is called, reads the context, and refuses in its own words
        # under rule 4.
        #
        # That refusal is the one that arrives with NO flag on it. The
        # floor and the post-filter substitute a fixed sentence this file
        # can recognise; a model-authored refusal is just prose, and on
        # screen it is indistinguishable from a system that knows nothing.
        # Which is precisely the failure the flags above exist to prevent,
        # arriving through the one door they did not cover.
        #
        # gold_eval.is_abstention is the metric's own detector, reused
        # rather than re-written -- the page and the score must agree about
        # what an abstention is. Gated on the other two, which substitute
        # text that matches it by construction.
        if (not meta.get("floor_fired") and not meta.get("filter_fired")
                and _is_abstention(answer.get("answer"))):
            body.append(
                '<div class="bfa-flag"><span class="k">The model read the '
                'clauses and found no answer in them.</span><br>This is a '
                'refusal, not a blank. The clauses below were retrieved '
                'and read; they do not carry the figure asked for. Open '
                'them and judge for yourself.</div>')

        if meta.get("filter_skipped"):
            body.append(
                '<div class="bfa-flag note"><span class="k">Post-filter not '
                f'armed.</span><br>{esc(meta["filter_skipped"])}. It holds '
                'per-question constraints and has none for this question, '
                'so it could not have fired. This is not a clean pass.</div>')

        # 4. PRODUCT STATUS, read from the registry at query time.
        rows, populated, total = product_rows(question)
        if rows:
            lines = "".join(f'<br><span class="bn">{esc(n)}</span> - {esc(s)}'
                            for n, s in rows)
            body.append(
                '<div class="bfa-flag note"><span class="k">Product '
                'status.</span>' + lines +
                f'<br><span style="color:{BRAND["muted"]}">The registry '
                f'holds {total} products and {populated} statuses. Status is '
                'read at query time, so a withdrawn product still shows its '
                'terms - the terms bind existing clients either '
                'way.</span></div>')

        model_used = meta.get("model") or "no model - the floor fired"
        body.append(
            f'<div class="bfa-meta">Answered by <b>{esc(model_used)}</b> '
            f'&middot; provider {esc(PROVIDER)} &middot; k={K} &middot; '
            f'top fused {meta.get("top_fused") or 0:.4f} &middot; '
            f'{meta.get("prompt_tokens") or 0:,} prompt + '
            f'{meta.get("output_tokens") or 0:,} output tokens &middot; '
            f'meta row <code>{esc(result["id"])}</code> in '
            f'{esc(DEMO_META.as_posix())}</div>')

    st.markdown(
        '<div class="bfa-band"><div class="bfa-card">'
        '<div class="bfa-label">Answer</div>'
        + "".join(body) +
        '</div></div>' + budget_warn, unsafe_allow_html=True)

    st.markdown(f'<img class="bfa-divider" src="{a["divider"]}" alt="">',
                unsafe_allow_html=True)

    with st.container(key="chunks"):
        with st.expander("Retrieved chunks", expanded=False):
            chunks = (result or {}).get("chunks") or []
            cited, orphans = cite_match(
                ((result or {}).get("answer") or {}).get("citations"), chunks)
            # ABOVE THE LIST, NOT IN IT. A citation naming nothing retrieved
            # has no row to sit on, and an absent row is not something a
            # reader notices. It is also the most informative thing here.
            if orphans:
                st.markdown(
                    '<div class="bfa-orphan"><span class="k">Cited but not '
                    'retrieved.</span> '
                    + ", ".join(f"<b>{esc(cite_display(o, chunks))}</b>"
                                for o in orphans)
                    + ' - named in the answer, but no retrieved chunk '
                    'carries that clause. Either the model invented it, or '
                    'it belongs to a manual this search did not reach. '
                    'Treat the answer as unsupported on that point.</div>',
                    unsafe_allow_html=True)
            if not chunks:
                st.markdown(
                    '<div class="bfa-chunk"><span class="cl">'
                    + ('Retrieval never ran - the search model could not '
                       'be loaded.' if (result or {}).get("infra")
                       else 'Nothing retrieved yet.')
                    + '</span></div>', unsafe_allow_html=True)
            for c in chunks:
                st.markdown(
                    '<div class="bfa-chunk">'
                    f'<span class="id">{esc(c["chunk_id"])}</span>'
                    f'<span class="cl">{esc(_mf_manual(c))} '
                    + (f'&sect;{esc(_mf_clause(c))}' if _mf_clause(c)
                       else '<i>no clause number</i>') + '</span>'
                    + ('<span class="cited">CITED</span>'
                       if c["chunk_id"] in cited else '')
                    + f'<span class="sc">fused {c["fused_score"]:.2f}</span>'
                    + '</div>', unsafe_allow_html=True)



def render_faq():
    """SURFACE 2. Not started, and saying so is the point.

    An empty tab reads as broken; a stated gap reads as a plan. Nothing
    here is a placeholder number or a fake row -- there is no data yet,
    so there is nothing to render but the sentence saying what will go
    here and what it will take.
    """
    st.markdown(
        '<div class="bfa-soon"><span class="k">Not started</span>'
        '<b>Fifty questions field officers actually ask, with reviewed '
        'answers.</b><br>The 24 questions in the evaluation set were '
        'written by reading chunks, so they test retrieval mechanics '
        'rather than what an officer walks in with. This surface waits '
        'on the field questions being collected and their answers '
        'checked against the printed manual by someone who has it.'
        '</div>', unsafe_allow_html=True)

# Convention 1: importing this module must never write a file or call a
# provider. Everything above is a definition; main() does the work.
if __name__ == "__main__":
    main()
