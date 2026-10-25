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
  BFA_DEV=1 streamlit run app.py     # with the developer diagnostics
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
import faq
import retrieve
from bnnorm import canon
import ui_strings


# ------------------------------------------------------------------- brand
# ONE PLACE TO CORRECT. Every colour, font stack and size the chrome uses
# comes from here; nothing below writes a hex or a px for brand identity.
#
# OFFICIAL CODES ONLY: the brand book's colour table, BRAND.md section 3.1,
# as quoted in the restyle brief. BRAND.md itself was NOT on this machine
# when this was written (journal 4m), so these are the brief's transcription
# of the table and have not been read off the table directly. Nothing that
# is not in that list appears here -- the old #2b2f38 top bar, #f7f7f7 band,
# #e6e6e6 rule and #6b7078 grey are gone for that reason, and yellow
# #FBD00F is deliberately absent.
PALETTE = {
    "magenta": "#EC008C",        # 100%: logo area, tab underline, large
                                 # headings, the footer bar. 4.25:1 on white,
                                 # so never small text.
    "magenta_text": "#B8006B",   # approved tint for magenta TEXT on light
    "magenta_hi": "#FDE6F2",     # magenta highlight tint (fill)
    "magenta_hi_line": "#F6B3D6",  # its border; also small magenta text on dark
    "ink": "#27281C",            # body text, near-black
    "grey": "#4D4F53",           # supporting text
    "card": "#F3F3F4",           # neutral card
    "card_line": "#D9D9DC",      # its border
    "white": "#FFFFFF",
    "teal": "#00594C",           # status: active (text)
    "teal_tint": "#E4F2EE",      # status: active (fill)
    "brick": "#A41E22",          # status: suspended (text)
    "risk_tint": "#F8E6E6",      # status: suspended (fill)
}


def _mix(a, b, share):
    """`share` of colour b laid over colour a. -> '#RRGGBB'

    The ONE way the dark theme gets a colour the table does not hold: as a
    stated blend of two official codes. Each use below names both parents
    and the share, so a reviewer can rebuild it -- and reject it."""
    a, b = a.lstrip("#"), b.lstrip("#")
    return "#" + "".join(
        f"{round(int(a[i:i + 2], 16) * (1 - share) + int(b[i:i + 2], 16) * share):02X}"
        for i in (0, 2, 4))


# ROLES, PER THEME. CSS reads these as custom properties, so the stylesheet
# names a role (--ink, --chip-bg) and never a hex.
#
# THE DARK THEME IS DERIVED AND UNAPPROVED. BRAND.md has no dark palette.
# Every value below is an official code, or a blend of two official codes
# stated in place; none is a new colour. It needs brand-team approval before
# it is anything more than a reading aid -- journal 4m.
#
# Contrast, measured (WCAG 2.x), for the pairs that carry text:
#   light  ink/white 14.93  grey/card 7.40  magenta_text/white 6.46
#          chip text/fill 5.47  active 7.20  suspended 6.26
#          button white on #B8006B 6.46   (white on #EC008C is 4.25 -- fails)
#   dark   ink/ground 13.46  grey/card 8.83  #F6B3D6 on ground 8.77, on card
#          7.31, on chip fill 7.68  button #27281C on #F6B3D6 8.77
#          #EC008C on ground 3.52 -- large headings only; on a card 2.93,
#          which is why a stat figure on a dark card is #F6B3D6, not magenta.
THEMES = {
    "light": {
        "bg": PALETTE["white"], "ink": PALETTE["ink"], "muted": PALETTE["grey"],
        "card": PALETTE["card"], "card-line": PALETTE["card_line"],
        "raised": PALETTE["white"],           # a flag inside a card
        "magenta": PALETTE["magenta"],        # non-text and large text only
        "heading": PALETTE["magenta"],        # large headings, on the ground
        "stat": PALETTE["magenta"],           # 30px bold figure, on a card
        "accent": PALETTE["magenta_text"],    # SMALL magenta text
        "chip-bg": PALETTE["magenta_hi"], "chip-line": PALETTE["magenta_hi_line"],
        "chip-ink": PALETTE["magenta_text"],
        "ok-bg": PALETTE["teal_tint"], "ok-ink": PALETTE["teal"],
        "risk-bg": PALETTE["risk_tint"], "risk-ink": PALETTE["brick"],
        "upd-bg": PALETTE["magenta_hi"], "upd-ink": PALETTE["magenta_text"],
        "input-bg": PALETTE["white"], "input-line": PALETTE["grey"],
        "focus": PALETTE["magenta"],
        "btn-bg": PALETTE["magenta_text"], "btn-ink": PALETTE["white"],
        "logo": "logo",
    },
    "dark": {
        "bg": PALETTE["ink"], "ink": PALETTE["card"],
        "muted": PALETTE["card_line"],
        "card": _mix(PALETTE["ink"], PALETTE["white"], 0.06),     # #34352A
        "card-line": PALETTE["grey"],
        "raised": PALETTE["ink"],
        "magenta": PALETTE["magenta"],
        "heading": PALETTE["magenta"],
        "stat": PALETTE["magenta_hi_line"],
        "accent": PALETTE["magenta_hi_line"],
        "chip-bg": _mix(PALETTE["ink"], PALETTE["magenta"], 0.22),  # #521F35
        "chip-line": PALETTE["magenta"],
        "chip-ink": PALETTE["magenta_hi_line"],
        # Status in dark: the SAME official pair, inverted -- the deep colour
        # becomes the fill and its tint the text. No new hue.
        "ok-bg": PALETTE["teal"], "ok-ink": PALETTE["teal_tint"],
        "risk-bg": PALETTE["brick"], "risk-ink": PALETTE["risk_tint"],
        "upd-bg": PALETTE["magenta_text"], "upd-ink": PALETTE["magenta_hi"],
        "input-bg": _mix(PALETTE["ink"], PALETTE["white"], 0.12),  # #414237
        "input-line": PALETTE["card_line"],
        "focus": PALETTE["magenta_hi_line"],
        "btn-bg": PALETTE["magenta_hi_line"], "btn-ink": PALETTE["ink"],
        "logo": "logo_white",
    },
}
DEFAULT_THEME = "light"

# FONTS. BRAND.md: Kohinoor Bangla for Bengali, Helvetica Neue then Arial for
# English.
#
# ONE STACK FOR MIXED TEXT, AND THE ORDER IS THE MECHANISM. Arial has no
# Bengali glyphs, so the browser falls through it, per character, to the
# Bengali faces listed after it -- Latin lands on Helvetica Neue or Arial
# and Bengali on Kohinoor Bangla or its fallback, inside one sentence.
# Without a Bengali face in the chrome stack, a Bengali label falls to
# whatever the OS has (Nirmala UI on Windows), which is neither.
#
# KOHINOOR BANGLA IS NOT SELF-HOSTED. No BRAC font pack -- no web font files
# and no licence permitting web use -- was found on this machine, so there is
# nothing to put behind an @font-face. Kohinoor is named first so the brand
# face is used wherever it is installed (it ships with macOS and iOS); Noto
# Sans Bengali, loaded from Google Fonts, is the DEVIATION everywhere else.
# Recorded in journal 4m. Helvetica Neue is in the same position: named,
# used where installed, Arial otherwise -- which is the brand's own fallback.
BRAND = {
    "magenta": PALETTE["magenta"],   # must equal primaryColor in config.toml
    "latin": ('"Helvetica Neue", Arial, '
              '"Kohinoor Bangla", "Noto Sans Bengali", sans-serif'),
    "bengali": ('"Kohinoor Bangla", "Noto Sans Bengali", '
                '"Helvetica Neue", Arial, sans-serif'),
    # Bengali sits at 21px against 13-14px English chrome. Not a preference:
    # at a matching point size Bengali renders visually smaller than Latin,
    # the figures ARE the answer, and a wrong figure at 14px is easy to miss.
    "bengali_px": "21px",
    # The chrome, per interface language, for the same reason: Bengali
    # chrome at the English 13px reads as a size smaller.
    "chrome_px": {"en": "13px", "bn": "15px"},
    "small_px": {"en": "12px", "bn": "14px"},
    # LINE HEIGHT, per interface language. BRAND.md 4: "Line height is 1.4
    # for body text and 1.0-1.25 for headings." English interface text now
    # takes that 1.4.
    #
    # Bengali does not, and the reason is the same one that puts Bengali at
    # 21px against 13px English chrome: the script carries ascenders,
    # descenders and the matra bar above the x-height, so glyphs collide
    # vertically at a leading Latin handles comfortably. 1.6 is the floor of
    # the exception and the value most of this stylesheet already used; the
    # Bengali ANSWER body stays at 1.8, its ceiling, because it is 21px.
    "leading": {"en": "1.4", "bn": "1.6"},
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
INDEX_DIR = Path(os.environ.get("BFA_INDEX_DIR", "out/index_gemini_v2"))
POST_FILTER = True
# THE DEVELOPER SWITCH. Off unless BFA_DEV=1. A field officer reads answers,
# not diagnostics: "k=5", "top fused", "meta row", "post-filter", chunk ids,
# token counts, file paths and raw provider errors are operator information
# (BRAND.md 1 and 10.1: jargon-free). With the switch on, every one of them
# is back exactly as before; with it off, the page says the same things in
# plain words, or -- for pure diagnostics -- not at all. Nothing about how
# an answer is produced changes either way.
DEV = os.environ.get("BFA_DEV", "").strip() == "1"
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
# DERIVED 2026-10-20 on the 1.0/1.0 weights, 23 scoreable questions, k=5:
#   gold retrieved      n=19  min=0.6253  median=0.7516  max=0.8103
#   gold NOT retrieved  n=4   min=0.7412  median=0.7636  max=0.7761
#   -> lowest score with a hit = 0.6253
#
# THE INVERSION SURVIVED THE EMBEDDER. On LaBSE the misses scored higher
# than the hits, which is why that floor could only ever guard the
# degenerate case. On Gemini they still do -- median miss 0.7636 against
# median hit 0.7516, and the highest-scoring MISS (0.7761) is above every
# hit but one. So this floor cannot separate a found answer from a lost
# one here either; no threshold on this distribution can. It is set to the
# measured value because that is what the method says to do, and the thing
# it actually buys is unchanged: it catches a retrieval that came back
# near-empty, and nothing else.
#
# SET BELOW THE LOWEST HIT, NOT AT IT. 0.6253 was the measured lowest hit
# and it is the wrong number to ship, for the reason the distribution
# already gives: the floor cannot separate a found answer from a lost one
# here, so every point of it above the degenerate case is pure cost. At
# 0.6253 two of the eleven live tests (T-08 0.6018, T-09 0.6246) stopped
# before the model, and a legitimate question landing at 0.61 would stop
# too -- refusing real questions to catch nothing.
#
# 0.55 is below every observed hit (min 0.6253) with room to spare, so it
# refuses nothing the retriever actually found, and it still does the one
# job a floor can do on this distribution: catch a search that came back
# with nothing related. `not chunks` in generate.answer_one catches the
# empty case; this catches the near-empty one.
#
# Re-derive it when the corpus, the chunking or the weighting changes --
# and re-derive it on MORE questions than 23. With n=19 hits the minimum
# is one question's score, not a property of the index.
INDEX_FLOOR = {"out/index_gemini": 0.55,
               # The v2 corpus: same embedder, same weights, same
               # question set, re-derived after the table re-render
               # changed the chunking. Kept at 0.55 because the
               # measurement says it is still below every hit, not
               # because it was the number already here.
               "out/index_gemini_v2": 0.55}
FLOOR = INDEX_FLOOR.get(str(INDEX_DIR).replace("\\", "/"),
                        generate.SCORE_FLOOR)

# THE PANEL A FIELD OFFICER READS. The floor, post-filter and embedder-down
# notes are one sentence each, no score, no threshold. The numbers that used
# to be here -- the fused score, the floor, the matched figure -- are
# operator information and are written to the meta row, which is where
# every other diagnostic already lives. A reader who cannot act on "0.4012
# below a floor of 0.4044" is being shown it only because it was easy to
# print. The sentences themselves live in ui_strings (floor_note,
# filter_note, embedder_down), with the rest of the interface text.

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
    """The two logo files, inlined. Streamlit serves no static path to HTML
    written into a markdown block, so they go in as data URIs.

    MAGENTA ON LIGHT, WHITE ON DARK, never on a tint. brac-logo.png carries
    an opaque white ground and its own clear space -- the mark sits 32-35px
    inside a 284x130 canvas -- so it is placed on the white page as it is.
    brac-logo-white.png is the same lockup reversed out, for the dark ground.

    NEITHER FILE IS CONFIRMED AS THE BRAND BOOK'S EXPORT (BRAND.md 2.1).
    Measured: the white file is a pixel-exact alpha conversion of the magenta
    one -- every non-white pixel of one is the opaque pixel of the other --
    so it was derived from the supplied lockup here, not delivered. It is the
    same drawing, not a redrawn one, but swap both for the official PNGs
    when they arrive. Journal 4m.

    THE MOTIF IS NOT HERE. assets/brac-divider.svg was a hand-drawn running
    stitch, and BRAND.md section 5 says the motif is never redrawn. No
    official export from Brand motif.indd is in assets/, so the band it
    occupied is white space. The file itself was deleted in the brand-audit
    pass: a redrawn motif should not sit in the repo for someone to reuse.
    """
    return {
        "logo": _data_uri(ASSETS / "brac-logo.png", "image/png"),
        "logo_white": _data_uri(ASSETS / "brac-logo-white.png", "image/png"),
    }


# PHOTOGRAPHS, by file name. Web versions only -- each is already a single
# WebP encode, and re-encoding one again is a second lossy pass.
#   brac-enterprise.webp  FAQ header       textile workshop
#   brac-river.webp       Ask, welcome     woman rowing on a narrow river
#   brac-members.webp     Test yourself    members' meeting -- NOT YET SUPPLIED
PHOTOS = {"enterprise": "brac-enterprise.webp", "river": "brac-river.webp",
          "members": "brac-members.webp"}


@st.cache_data(show_spinner=False)
def _photo_uri(path, mtime):
    """Inline one photo. `mtime` is in the cache key on purpose: a photo
    that is added or replaced is picked up on the next run, not after a
    restart -- assets()'s cache would keep serving 'absent' forever."""
    return _data_uri(path, "image/webp")


def photo(key):
    """data URI for PHOTOS[key], or None when the file is not there. None
    means NO <img> at all -- never a broken-image icon in a slot whose
    photo has not arrived."""
    p = ASSETS / PHOTOS[key]
    return _photo_uri(str(p), p.stat().st_mtime) if p.exists() else None


def photo_html(key, lang):
    """The one way a photo reaches the page: same class, same alt-text
    source. '' when the file is absent."""
    uri = photo(key)
    if not uri:
        return ""
    return (f'<img class="bfa-photo photo-{key}" src="{uri}" '
            f'alt="{esc(t(f"{key}_photo_alt", lang))}">')


# --------------------------------------------------------------------- CSS
# A Template, not an f-string: CSS is made of braces.
#
# TWO PARTS. CSS is the stylesheet and names ROLES only (var(--ink)); it is
# the same for every session. theme_vars() is the :root block that binds
# those roles to one theme and one interface language, and it is the only
# part that differs between sessions. A switch therefore changes nine-odd
# custom properties, not the stylesheet.
#
# STREAMLIT'S OWN WIDGETS ARE RESTYLED HERE, AND THAT IS NEW. config.toml
# pins base = "light", so the widgets no longer follow the OS -- that was
# the black-input-in-a-white-page defect. But a pinned light base cannot
# follow a per-session dark switch either: Streamlit's theme is a server
# option shared by every session, and changing it for one viewer would
# change it for all of them. So the input, select, radio, button, tabs,
# expander and the switches themselves are given the role colours here,
# under both themes. Colour and type only; no widget is reshaped. Every
# selector below was checked against the computed style of the running page
# (journal 4m has the measurements) -- the lesson of 4e is that a rule that
# exists is not a rule that applies.
CSS = Template("""
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+Bengali:wght@400;500;600;700&display=swap');

html, body, [data-testid="stApp"], [data-testid="stAppViewContainer"],
[data-testid="stMain"] { font-family: $latin; color: var(--ink);
        background: var(--bg); }

/* MEASURED, not assumed (4e). Streamlit puts its own font-family on the
   markdown container and on every widget, so the face is named on each
   explicitly. Never `*`: the expander arrow and the select chevron are
   Material Symbols glyphs, and a blanket rule turns them into words. */
.bfa-header, .bfa-title, .bfa-sub, .bfa-card, .bfa-label, .bfa-answer-en,
.bfa-faq-en,
.bfa-chip, .bfa-flag, .bfa-meta, .bfa-chunk, .bfa-footer, .bfa-foot-note,
.bfa-budget, .bfa-stat, .bfa-soon, .bfa-orphan, .bfa-badge,
[data-testid="stMarkdownContainer"], [data-testid="stWidgetLabel"],
[data-testid="stTextInputField"], [data-testid="stSelectbox"] input,
[data-testid="stRadio"] label, [data-testid="stTab"] p,
[data-testid="stForm"] button,
[data-testid="stExpander"] summary, [data-testid="stButtonGroup"] button,
[data-testid="stSpinner"] { font-family: $latin; }
.bfa-answer, .bfa-flag .bn, .bfa-name, .bfa-footer .name {
    font-family: $bengali; }

/* The page starts at the logo, so Streamlit's own toolbar and the block
   padding go. This is the app frame, not a widget. */
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
   draws. Zeroed for the top-level sections only, so the chunk rows nested
   inside the expander keep Streamlit's spacing. */
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
   A bare `[data-testid="stVerticalBlock"] { gap: 0 }` reached the block
   inside the form too and glued the submit button to the input. The page
   sections want no gap because each sets its own spacing; the widgets
   inside a form want Streamlit's. */
[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] {
    gap: 0; }

/* ---------------------------------------------------- Streamlit widgets */
[data-testid="stWidgetLabel"], [data-testid="stWidgetLabel"] p {
    color: var(--muted) !important; font-size: var(--chrome); }

/* SELECTORS ARE STREAMLIT 1.64's DOM, and that is a dependency. 1.64
   renders its widgets with react-aria, not BaseWeb: every data-baseweb
   selector written for the older DOM matched NOTHING when measured. Only
   data-testid, role and aria-* attributes are used below -- the emotion
   class names are hashes and change between builds. After a Streamlit
   upgrade, re-run the measurement in journal 4m before trusting this. */

/* Text input. The fill, the border and the text, all three, in both
   themes: setting only the text is how a dark page gets a white box. */
[data-testid="stTextInputRootElement"] {
    background-color: var(--input-bg) !important;
    border: 1px solid var(--input-line) !important; }
[data-testid="stTextInputRootElement"]:focus-within {
    border-color: var(--focus) !important; }
[data-testid="stTextInputField"] { font-size: 15px;
    color: var(--ink) !important; -webkit-text-fill-color: var(--ink);
    caret-color: var(--ink); background-color: transparent !important; }
[data-testid="stTextInputField"]::placeholder { color: var(--muted);
    -webkit-text-fill-color: var(--muted); opacity: 1; }

/* Select box, and its menu -- which renders in a portal under <body>, not
   under the select, so it is targeted on its own. */
[data-testid="stSelectbox"] [role="group"] {
    background-color: var(--input-bg) !important;
    border: 1px solid var(--input-line) !important; }
[data-testid="stSelectbox"] [role="group"]:focus-within {
    border-color: var(--focus) !important; }
[data-testid="stSelectbox"] input[role="combobox"] {
    color: var(--ink) !important; -webkit-text-fill-color: var(--ink); }
[data-testid="stSelectbox"] input[role="combobox"]::placeholder {
    color: var(--muted); -webkit-text-fill-color: var(--muted); opacity: 1; }
[data-testid="stSelectbox"] svg { color: var(--muted); }
[data-testid="stSelectboxVirtualDropdown"] {
    background-color: var(--card) !important;
    border: 1px solid var(--card-line) !important; }
[data-testid="stSelectboxVirtualDropdown"] [role="option"] {
    color: var(--ink) !important; font-family: $latin; }
[data-testid="stSelectboxVirtualDropdown"] [role="option"][data-focused],
[data-testid="stSelectboxVirtualDropdown"] [role="option"]:hover,
[data-testid="stSelectboxVirtualDropdown"] [role="option"][aria-selected="true"] {
    background-color: var(--input-bg) !important; }

/* Radio. The text, and the ring: Streamlit draws an unselected ring at 20%
   of textColor, which is near-black and so vanishes on the dark ground.
   Ring muted, selected ring the brand magenta (4.25:1 on white, 3.52:1 on
   the dark ground, both above the 3:1 a control needs), centre the page. */
[data-testid="stRadioOption"] p { color: var(--ink) !important;
    font-size: var(--chrome); }
[data-testid="stRadioOption"] > div > div:first-child {
    background-color: var(--muted) !important; }
[data-testid="stRadioOption"]:has(input:checked) > div > div:first-child {
    background-color: var(--magenta) !important; }
[data-testid="stRadioOption"] > div > div:first-child > div {
    background-color: var(--bg) !important; }

/* Primary buttons. NOT plain magenta: white on #EC008C is 4.25:1, under
   the 4.5:1 a 14px label needs. Light uses the approved text tint #B8006B
   as the fill (6.46:1); dark uses the light tint #F6B3D6 with near-black
   text (8.77:1). */
[data-testid="stBaseButton-primaryFormSubmit"],
[data-testid="stBaseButton-primary"] {
    background-color: var(--btn-bg) !important;
    border-color: var(--btn-bg) !important; color: var(--btn-ink) !important; }
[data-testid="stBaseButton-primaryFormSubmit"] p,
[data-testid="stBaseButton-primary"] p { color: var(--btn-ink) !important;
    font-size: var(--chrome); font-weight: 600; }
[data-testid="stBaseButton-primaryFormSubmit"]:hover,
[data-testid="stBaseButton-primary"]:hover,
[data-testid="stBaseButton-primaryFormSubmit"]:focus-visible {
    box-shadow: 0 0 0 2px var(--bg), 0 0 0 4px var(--magenta) !important; }

/* The two switches. Segmented controls: the selected half is the button
   colour, the other is the page. */
[data-testid="stButtonGroup"] button[role="radio"] {
    background-color: var(--bg) !important; color: var(--ink) !important;
    border-color: var(--input-line) !important; }
[data-testid="stButtonGroup"] button[role="radio"] * {
    color: inherit !important; font-size: var(--small); }
[data-testid="stButtonGroup"] button[role="radio"][aria-checked="true"] {
    background-color: var(--btn-bg) !important; color: var(--btn-ink) !important;
    border-color: var(--btn-bg) !important; font-weight: 600; }

/* Tabs. The underline is 100% magenta; the selected label is small text,
   so it is the text tint -- Streamlit's default puts #EC008C on it, 4.25:1
   at 14px, which is the failure the tint exists to fix. */
[data-testid="stTabs"] { padding: 0 22px; }
[data-testid="stTab"] p { font-size: 14px; color: var(--muted) !important; }
[data-testid="stTab"][aria-selected="true"] p { color: var(--accent) !important;
    font-weight: 600; }
[data-testid="stTab"] .react-aria-SelectionIndicator,
[data-testid="stTabs"] [role="tablist"] .react-aria-SelectionIndicator {
    background-color: var(--magenta) !important; }
[data-testid="stTabs"] [role="tablist"] {
    border-bottom-color: var(--card-line) !important; }

/* Expander. A CLOSED EXPANDER MUST NOT LAY OUT ITS BODY: measured, the
   collapsed <details> kept a 97px body box running past the footer, so
   the footer sat mid-page. Containment, not restyling. */
details:not([open]) [data-testid="stExpanderDetails"] { display: none; }
[data-testid="stExpander"] details { background-color: var(--bg) !important;
    border-color: var(--card-line) !important; }
[data-testid="stExpander"] summary { color: var(--ink) !important;
    background-color: var(--bg) !important; }
[data-testid="stExpander"] summary p { font-size: 20px; }
[data-testid="stExpander"] summary:hover { color: var(--accent) !important; }
[data-testid="stExpander"] summary [data-testid="stIconMaterial"] {
    color: var(--accent) !important; }

/* The spinner's arc is a <span> with an animated top border -- no <svg>,
   no <i> -- so that is what is coloured. The label is a <p>. */
[data-testid="stSpinner"] span { border-top-color: var(--magenta) !important; }
[data-testid="stSpinner"] p { color: var(--muted); }

.st-key-prefs { padding: 10px 22px 0; }
.st-key-mode { padding: 16px 22px 0; }
.st-key-ask { padding: 10px 22px 18px; }
.st-key-chunks { padding: 40px 22px 28px; }

/* ------------------------------------------------------------- the page */
/* THE LOGO STANDS ALONE. Top left, on the page ground, with nothing in its
   row: the canvas already holds its clear space (the mark sits ~25% in
   from every edge), and the title starts below it rather than beside it. */
.bfa-header { padding: 0 22px; background: var(--bg); }
.bfa-header img { height: 64px; display: block; margin-left: -15px; }
.bfa-title { font-size: 28px; font-weight: 700; line-height: 1.2;
             color: var(--heading); margin: 6px 0 2px; }
.bfa-sub { font-size: var(--chrome); color: var(--muted);
           line-height: var(--leading);
           margin-bottom: 18px; }

/* Stat cards. Four across, collapsing to two then one on a narrow
   screen -- an officer's phone should not scroll sideways. */
.bfa-stats { display: grid; grid-template-columns: repeat(4, 1fr);
             gap: 12px; padding: 0 22px 18px; }
@media (max-width: 900px) { .bfa-stats { grid-template-columns: 1fr 1fr; } }
@media (max-width: 520px) { .bfa-stats { grid-template-columns: 1fr; } }
.bfa-stat { background: var(--card); border: 1px solid var(--card-line);
            border-radius: 10px; padding: 14px 16px; }
.bfa-stat .v { font-size: 30px; font-weight: 700; line-height: 1.1;
               color: var(--stat); }
.bfa-stat .l { font-size: var(--chrome); color: var(--ink); margin-top: 4px; }
.bfa-stat .n { font-size: 11px; color: var(--muted); margin-top: 3px;
               overflow-wrap: anywhere; }

/* PHOTOGRAPHS. One per screen, as a header and never inside a list or a
   citation: a photo beside an answer would read as evidence for it. 16px
   corners and a soft shadow. Cropped, not squashed, to a header height --
   object-position keeps the two faces and the hands in frame. */
.bfa-photo { display: block; width: calc(100% - 44px); margin: 8px 22px 18px;
             height: 360px; max-width: none !important;
             /* !important, MEASURED: Streamlit's markdown image rule won
                and letterboxed the photo inside a correctly sized, rounded,
                shadowed box -- the corners were on the frame, not the
                picture. */
             object-fit: cover !important; object-position: 50% 48% !important;
             border-radius: 16px;
             box-shadow: 0 2px 6px rgba(0,0,0,.08), 0 12px 28px rgba(0,0,0,.10); }
@media (max-width: 700px) { .bfa-photo { height: 220px; } }
/* THE CROP'S FOCAL POINT IS PER PHOTO. One shared value fitted the
   textile workers (faces mid-frame) and, measured on the rendered page, cut
   the rower's head off: her head sits in the top tenth of the frame. */
.bfa-photo.photo-river { object-position: 50% 10% !important; }
/* The quiz container already carries the 22px gutter; without this the
   photo would sit 22px further in than the cards below it. */
.st-key-quiz .bfa-photo { width: 100%; margin-left: 0; margin-right: 0; }
/* THE FAQ (render_faq). One expander per approved question, in the same
   22px gutter as every other section. The answer keeps its line breaks:
   a figure list is lines, and run together it is a paragraph of numbers. */
.st-key-faq { padding: 4px 22px 28px; }
.st-key-faq .bfa-label { margin-top: 18px; }
.st-key-faq [data-testid="stExpander"] summary p { font-size: 17px; }
.bfa-faq-body { white-space: pre-line; }
.bfa-faq-en { font-size: 15px; line-height: var(--leading);
              color: var(--ink); }
.bfa-soon { background: var(--card); border: 1px solid var(--card-line);
            border-radius: 10px; padding: 22px; margin: 4px 22px 22px;
            font-size: 14px; line-height: var(--leading);
            color: var(--ink); }
.bfa-soon .k { font-weight: 700; color: var(--accent);
               text-transform: uppercase; letter-spacing: .12em;
               font-size: var(--small); display: block; margin-bottom: 8px; }

.bfa-section { padding: 4px 22px 18px; }
.bfa-card { background: var(--card); border: 1px solid var(--card-line);
            border-radius: 10px; padding: 20px 22px; }
.bfa-label { font-size: var(--small); font-weight: 700; letter-spacing: .14em;
             text-transform: uppercase; color: var(--accent);
             margin: 0 0 12px; }
/* THE ONE LITERAL LEADING LEFT. The answer body is Bengali whatever
   the interface language is, so it does not follow --leading: 1.8 is
   the top of the exception and this is 21px text. */
.bfa-answer { font-size: $bengali_px; line-height: 1.8;
              color: var(--ink); }
.bfa-answer-en { font-size: 14px; line-height: var(--leading);
                 color: var(--muted); }
.bfa-chips { margin-top: 14px; }
/* Citation chips: the magenta highlight tint, not white on magenta, which
   is 4.25:1 and under the 4.5:1 a 12px label needs. */
.bfa-chip { display: inline-block; background: var(--chip-bg);
            border: 1px solid var(--chip-line); color: var(--chip-ink);
            /* 1.6 LITERAL, not --leading: a chip is one line inside a
               pill, so this sets the pill's height, not the leading of a
               paragraph. Tying it to the language would change the
               chip's geometry between en and bn for no reason.
               .bfa-badge below is left alone for the same reason. */
            font-size: var(--small); line-height: 1.6;
            border-radius: 999px;
            padding: 2px 11px; margin: 0 6px 6px 0; }

/* Disclosure rows, in TWO weights, and the split is the point (4e).
     .bfa-flag        why THIS answer is not what the model first said.
                      Magenta bar, full size. At most one ever shows.
     .bfa-flag.note   standing conditions true of every query. Quieter,
                      grey, and they stay ON the page. */
.bfa-flag { border-left: 3px solid var(--magenta); background: var(--raised);
            padding: 10px 14px; margin-top: 14px; font-size: var(--chrome);
            line-height: var(--leading); color: var(--ink); }
.bfa-flag.note { border-left: 2px solid var(--card-line);
                 font-size: var(--small); color: var(--muted);
                 padding: 7px 12px; margin-top: 10px; }
.bfa-flag.note .k { color: var(--ink); }
.bfa-flag .k { font-weight: 600; }
.bfa-flag .bn { font-size: 16px; }
.bfa-flag code, .bfa-meta code { font-size: var(--small);
    color: var(--muted); background: transparent; }

/* Status badges, by MEANING (BRAND.md 3.2): active teal, suspended risk,
   updated rule magenta highlight. Anything else -- including the empty
   registry's "unknown" -- is neutral, because colouring a status nobody
   recorded would be a claim. */
.bfa-status { margin-top: 6px; }
.bfa-name { font-size: 16px; color: var(--ink); margin-right: 8px; }
.bfa-badge { display: inline-block; font-size: var(--small); font-weight: 600;
             line-height: 1.5; border-radius: 999px; padding: 1px 10px;
             border: 1px solid transparent; }
.bfa-badge.ok { background: var(--ok-bg); color: var(--ok-ink); }
.bfa-badge.risk { background: var(--risk-bg); color: var(--risk-ink); }
.bfa-badge.upd { background: var(--upd-bg); color: var(--upd-ink);
                 border-color: var(--chip-line); }
.bfa-badge.neutral { background: var(--card); color: var(--muted);
                     border-color: var(--card-line); }

/* The token budget is a condition of the DAY, not of this answer, so it
   sits outside the answer card. */
.bfa-budget { padding: 0 22px 18px; }
.bfa-budget .inner { border-left: 3px solid var(--muted);
                     background: var(--card); padding: 10px 14px;
                     font-size: var(--chrome);
                     line-height: var(--leading);
                     color: var(--ink); }
.bfa-budget .k { font-weight: 600; }
.bfa-meta { margin-top: 12px; font-size: var(--small); color: var(--muted); }

.bfa-chunk { display: flex; flex-wrap: wrap; gap: 4px 16px;
             align-items: baseline; padding: 9px 0;
             border-bottom: 1px solid var(--card-line);
             font-size: var(--chrome); color: var(--ink); }
.bfa-chunk .id { min-width: 180px; }
.bfa-chunk .cl { min-width: 190px; color: var(--muted); }
.bfa-chunk .sc { color: var(--muted); }
/* An outline, not a fill: a chip is what the model claimed, this badge is
   where that claim landed in the evidence. Small text, so the tint. */
.bfa-chunk .cited { font-size: 10px; font-weight: 700; letter-spacing: .08em;
                    color: var(--accent); border: 1px solid var(--accent);
                    border-radius: 3px; padding: 1px 5px; margin-left: 2px; }
.bfa-orphan { border-left: 3px solid var(--magenta); background: var(--card);
              padding: 10px 14px; margin: 4px 0 12px; font-size: var(--chrome);
              line-height: var(--leading); color: var(--ink); }
.bfa-orphan .k { font-weight: 600; }

/* THE NOTE IS OFF THE BAR. Small text on the page ground, in grey, where
   it reads at 8.21:1 (light) / 10.60:1 (dark) -- on magenta it was 4.25:1. */
.bfa-foot-note { font-size: var(--small); line-height: var(--leading);
                 color: var(--muted); max-width: 820px;
                 padding: 32px 22px 16px; }
/* THE BAR: the programme name and nothing else, left-aligned, 21px white,
   in a 52px bar (BRAND.md 11). BOLD 700, not BRAND.md's semibold -- a
   RECORDED EXCEPTION (journal 4p): white on #EC008C is 4.25:1, a pass only
   as WCAG large text, and at 21px that needs bold. */
.bfa-footer { background: var(--magenta); height: 52px;
               box-sizing: border-box; padding: 0 22px;
               display: flex; align-items: center; }
.bfa-footer .name { font-size: 21px; font-weight: 700; line-height: 1;
                    color: var(--on-magenta); text-align: left; }
""").safe_substitute(latin=BRAND["latin"], bengali=BRAND["bengali"],
                     bengali_px=BRAND["bengali_px"])


def theme_vars(theme, lang):
    """The :root block binding the stylesheet's roles to one theme and one
    interface language. -> str"""
    roles = {k: v for k, v in THEMES[theme].items() if k != "logo"}
    roles["on-magenta"] = PALETTE["white"]
    roles["chrome"] = BRAND["chrome_px"][lang]
    roles["small"] = BRAND["small_px"][lang]
    roles["leading"] = BRAND["leading"][lang]
    body = " ".join(f"--{k}: {v};" for k, v in roles.items())
    # color-scheme tells the browser which way to draw what CSS cannot
    # reach -- scrollbars, autofill, the caret.
    scheme = "dark" if theme == "dark" else "light"
    return f":root {{ {body} color-scheme: {scheme}; }}"


# --------------------------------------------------------------- the parts
# No spinner text of its own: index() is only called inside ask(), which
# already runs under the translated "Searching the manuals" spinner. (The
# old text also named LaBSE, which is no longer the default index.)
@st.cache_resource(show_spinner=False)
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

    THE LABEL IS A KEY, NOT TEXT. The figures are cached and the language is
    not, so the card carries a ui_strings key and the render translates it;
    the note is a file path, which is the same in both languages, or a
    (key, numbers) pair when it is a sentence.
    """
    cards = []
    rows, manuals = manifest()
    if rows:
        cards.append((f"{len(rows):,}", "stat_chunks", MANIFEST.as_posix()))
    if manuals:
        cards.append((str(len(manuals)), "stat_manuals",
                      f'{MANIFEST.name} ["manuals"]'))
    if EVAL_REPORT.exists():
        try:
            ev = json.loads(EVAL_REPORT.read_text(encoding="utf-8"))
        except ValueError:
            ev = None
        if ev:
            viol = sum(1 for r in ev if r.get("violations"))
            cards.append((f"{viol}/{len(ev)}", "stat_violations",
                          EVAL_REPORT.as_posix()))
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
                              "stat_citation",
                              ("stat_citation_note",
                               {"hit": hit, "scored": scored})))
    return cards


@st.cache_data(show_spinner=False)
def gold_menu():
    """[(id, question, mark_key)] for the browse dropdown. Read, never typed.

    The unanswerable ones are marked. A viewer who picks one and sees a
    refusal should be able to tell that the refusal is CORRECT -- the
    manual has no answer -- rather than reading it as the demo failing.

    The mark is a ui_strings key, translated at render: this is cached
    and the interface language is not. The widget's VALUE is the id, so
    switching language relabels the options without changing the pick.
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
        mark = {"no": "gold_mark_no",
                "partial": "gold_mark_partial"}.get(ans)
        out.append((q["id"], text, mark))
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
        # "infra" names WHICH failure. The card used to read only the flag,
        # so this branch rendered the out-of-memory card: a spent quota was
        # reported to the reader as a machine fault, with advice (close
        # applications, raise the page file) that cannot help.
        return {"id": qid, "chunks": [], "infra": "embedder", "meta": meta}
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
        return {"id": qid, "chunks": [], "infra": "memory", "meta": meta}

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

    Read from data/registry/products.csv AT QUERY TIME, never from a chunk --
    a chunk records what the manual said, not whether the product is still
    sold. Each row is (name_bn, status), status "" when unknown.

    THE REGISTRY, NOT THE CATALOGUE. This panel used to read
    products_catalogue.csv, whose status column is empty (0 of 43), so it
    said "unknown" for every product while generate.py -- which applies the
    registry's rules -- was already leading the answer with the registry's
    status sentence. The page and the answer disagreed about the same
    product. Both now read the same file, through registry.py.

    THE REGISTRY IS THE FIELD TEAM'S SHEET, so a status from it is shown as
    a status. The old "unverified" qualifier existed because the catalogue
    recorded no one who had checked; the registry is the check.

    QUESTION-NAMED ONLY. If the question names no product there is no panel:
    a status the officer did not ask for is noise, and noise in the
    disclosure is how a real disclosure stops being read. A product
    productmatch recognises but the registry does not list gets a row saying
    so -- silence there would read as "available".
    """
    import registry
    reg = registry.load()
    # registry.products_named puts suspended first, so the row that changes
    # what an officer may offer is the one read first.
    hits = {p["product_id"]: p for p in reg.products_named(question)}
    keys = list(hits) + [k for k in retrieve.products_named(question)
                         if k not in hits]
    if not keys:
        return [], 0, 0
    by_id = {p["product_id"]: p for p in reg.products}
    names = product_names()
    rows = []
    for k in keys:
        p = hits.get(k) or by_id.get(k)
        rows.append((p["name_bn"], (p.get("status") or "").strip()) if p
                    else (names.get(k, k), ""))
    populated = sum(1 for p in reg.products if (p.get("status") or "").strip())
    return rows, populated, len(reg.products)


# STATUS BY MEANING (BRAND.md 3.2), keyed by the registry's OWN values --
# data/registry/README.md: active · suspended · pilot. active is the teal
# pair and suspended the risk pair. pilot has no colour in the brief's list
# (its magenta highlight is for an UPDATED RULE, which is a different
# table), so it is neutral; no product is in pilot today. A value outside
# this list is shown as written, neutral, never coloured as if understood.
STATUS_CLASS = {"active": "ok", "suspended": "risk", "pilot": "neutral"}


def status_badge(raw, lang):
    """One registry status, as a badge in the colour of what it means. -> html"""
    if not raw:
        return (f'<span class="bfa-badge neutral">'
                f'{esc(t("status_unknown", lang))}</span>')
    key = raw.lower()
    label = (t(f"status_{key}", lang) if f"status_{key}" in ui_strings.STRINGS
             else raw)
    return (f'<span class="bfa-badge {STATUS_CLASS.get(key, "neutral")}">'
            f'{esc(label)}</span>')


# -------------------------------------------------------------- the render
def current_lang():
    """The interface language for this session. The segmented control owns
    the value (key "lang"); this only reads it."""
    v = st.session_state.get("lang")
    return v if v in ui_strings.LANGS else ui_strings.DEFAULT_LANG


def current_theme():
    v = st.session_state.get("theme")
    return v if v in THEMES else DEFAULT_THEME


def programme_names():
    """{dabi, progoti} for the strings that name them. Read from productmatch,
    never typed here (Convention 2)."""
    import productmatch as pm
    return {k: pm.PROGRAMME_NAMES[k][0] for k in ("dabi", "progoti")}


def t(key, lang, **kw):
    """ui_strings.t, with the programme names always available to fill."""
    return ui_strings.t(key, lang, **{**programme_names(), **kw})


def n(value, lang):
    """A number already formatted as text, in the interface's numerals."""
    return ui_strings.num(value) if lang == "bn" else str(value)


def render_prefs(lang):
    """The language and display switches, top right, in their own strip
    above the logo -- so nothing sits beside the logo.

    REMEMBERED FOR THE SESSION, in session_state, and NOT in the URL.
    Streamlit's bind="query-params" was tried and rejected on measurement:
    it writes the option's DISPLAYED label to the URL, and the display
    switch's label is translated -- so after a language switch the URL's
    ?theme=Dark no longer names an option, and Streamlit silently drops it. A reload starts a new Streamlit session
    anyway, so session_state is exactly "for the session".

    Seeded with setdefault rather than `default=`, so the value has one
    owner and never trips Streamlit's "default and Session State both set"
    warning. persist_state="session" keeps it when the strip is not drawn.
    required=True, because a segmented control otherwise deselects on a
    second click and leaves the page with no language at all."""
    st.session_state.setdefault("lang", ui_strings.DEFAULT_LANG)
    st.session_state.setdefault("theme", DEFAULT_THEME)
    with st.container(key="prefs", horizontal=True,
                      horizontal_alignment="right", gap="small"):
        st.segmented_control(
            t("lang_label", lang), ui_strings.LANGS,
            format_func=lambda v: t(f"lang_{v}", lang),
            required=True, key="lang", persist_state="session",
            label_visibility="collapsed")
        st.segmented_control(
            t("theme_label", lang), tuple(THEMES),
            format_func=lambda v: t(f"theme_{v}", lang),
            required=True, key="theme", persist_state="session",
            label_visibility="collapsed")


def main():
    lang, theme = current_lang(), current_theme()
    st.set_page_config(page_title=t("page_title", lang),
                       layout="wide", initial_sidebar_state="collapsed")
    st.markdown(f"<style>{theme_vars(theme, lang)}{CSS}</style>",
                unsafe_allow_html=True)
    a = assets()

    render_prefs(lang)
    # The control has run, so read again: on the run a switch is clicked,
    # session_state already holds the new value and the page should use it.
    lang, theme = current_lang(), current_theme()
    st.markdown(f"<style>{theme_vars(theme, lang)}</style>",
                unsafe_allow_html=True)

    st.markdown(
        '<div class="bfa-header">'
        f'<img src="{a[THEMES[theme]["logo"]]}" alt="{esc(t("logo_alt", lang))}">'
        f'<div class="bfa-title">{esc(t("title", lang))}</div>'
        f'<div class="bfa-sub">{esc(t("subtitle", lang))}</div>'
        '</div>', unsafe_allow_html=True)

    # Corpus and evaluation figures: developer information only.
    cards = stat_cards() if DEV else []
    if cards:
        def note(x):
            return t(x[0], lang, **x[1]) if isinstance(x, tuple) else x
        st.markdown(
            '<div class="bfa-stats">'
            + "".join(f'<div class="bfa-stat"><div class="v">{esc(n(v, lang))}'
                      f'</div><div class="l">{esc(t(label, lang))}</div>'
                      f'<div class="n">{esc(note(nt))}</div></div>'
                      for v, label, nt in cards)
            + '</div>', unsafe_allow_html=True)

    # TABS, NOT SEGMENTED BUTTONS, and the reason is state. Streamlit's
    # tabs switch client-side: changing tab does not re-run the script, so
    # the answer on screen survives a look at the quiz and back. A
    # segmented control is a widget -- every click is a re-run, which would
    # put the ask-guard and the quiz's one-draw-per-session through a cycle
    # they do not need. The cost is that every tab's body is rendered on
    # every run; that is affordable only because two of the three make no
    # model call and the third is guarded.
    #
    # KEYED, so a language switch -- which renames all three -- is the same
    # widget with new labels rather than three new tabs.
    #
    # KNOWN LIMIT, MEASURED: A LANGUAGE SWITCH REOPENS THE FIRST TAB. The
    # switch renames all three tabs, and with on_change="ignore" the browser
    # never tells the server which tab is open -- session_state["tabs"] read
    # None after a click on the quiz tab -- so there is nothing to carry the
    # selection across the rename. A theme switch keeps the tab (labels
    # unchanged). Fixing it needs on_change="rerun", i.e. a rerun on every
    # tab click, which is the cost this design chose not to pay; not changed
    # in a restyle. Journal 4m.
    ask_tab, faq_tab, quiz_tab = st.tabs(
        [t("tab_ask", lang), t("tab_faq", lang), t("tab_quiz", lang)],
        key="tabs")
    with ask_tab:
        render_ask(a, lang)
    with faq_tab:
        render_faq(lang)
    with quiz_tab:
        import quiz_app
        quiz_app.render_quiz(lang)

    # The note sits on the page, above the bar, where small grey text is
    # readable; the bar carries the programme name and nothing else.
    st.markdown(
        f'<div class="bfa-foot-note">{esc(t("footer_note", lang))}</div>'
        '<div class="bfa-footer"><div class="name">'
        f'{esc(t("footer_bar", lang))}</div></div>', unsafe_allow_html=True)



def render_ask(a, lang):
    """SURFACE 1. The answer page, unchanged in order and behaviour.

    Lifted into a function so it can sit in a tab. The mode control above it
    only decides what text reaches the same input. `lang` changes the words
    around the answer, never the answer: that is in the language of the
    question, as generate.py has always produced it.
    """
    gold = gold_menu()
    with st.container(key="mode"):
        # The VALUES are fixed keys and the labels come from format_func, so
        # a language switch relabels the control without changing the
        # selection -- comparing against a label would break on the switch.
        mode = st.radio(
            t("mode_label", lang), ("browse", "own"),
            format_func=lambda v: t(f"mode_{v}", lang),
            horizontal=True, key="ask_mode",
            label_visibility="collapsed")
        if mode == "browse" and gold:
            texts = {qid: text for qid, text, _m in gold}
            marks = {qid: m for qid, _text, m in gold}
            pick = st.selectbox(
                t("gold_select", lang, n=len(gold)),
                list(texts), index=None, key="gold_pick",
                # The question id is an evaluation handle: developer only.
                format_func=lambda qid: (
                    (f"{qid}  " if DEV else "") + texts[qid]
                    + (f"  {t(marks[qid], lang)}" if marks[qid] else "")),
                placeholder=t("gold_placeholder", lang))
            # Set the INPUT, do not bypass it: the chosen question then
            # travels the same path a typed one does, through the same
            # guard, so nothing about the answer surface special-cases it.
            if pick and st.session_state.get("_last_pick") != pick:
                st.session_state["q"] = texts[pick]
                st.session_state["_last_pick"] = pick

    with st.container(key="ask"):
        # The placeholder is BANGLISH, not Bengali, and not because Bengali
        # would not fit. A Bengali example would put a bare product name in
        # this file for Convention 2 to trip over, and it would advertise
        # only the input a keyboard-equipped officer can type. Banglish is
        # the harder half -- embed.expand_banglish exists for it -- so it is
        # the half worth showing on an empty page.
        # A FORM, so the question is submitted once -- by Enter or by
        # the button -- rather than on every keystroke's rerun.
        with st.form("ask", border=False, clear_on_submit=False):
            st.text_input(
                t("input_label", lang), key="q",
                placeholder=t("input_placeholder", lang))
            st.form_submit_button(t("submit", lang), type="primary")
        question = st.session_state.get("q") or ""

    # THE BUDGET, BEFORE ANYTHING IS TYPED. Warned, not enforced here: a
    # demo should say the day is nearly spent while there is still time to
    # shorten it, not fail on the question someone came to see.
    used = generate.ledger_used(PROVIDER, MODEL)
    limit = generate.TPD_LIMIT.get(PROVIDER)
    budget_warn = ""
    # Token accounting is the operator's problem, not the officer's: when
    # the budget actually runs out, the officer sees the plain error card.
    if DEV and limit and used / limit >= generate.TPD_WARN_AT:
        budget_warn = (
            '<div class="bfa-budget"><div class="inner">'
            f'<span class="k">{esc(t("budget_head", lang))}</span><br>'
            + esc(t("budget_body", lang, used=n(f"{used:,}", lang),
                    limit=n(f"{limit:,}", lang), model=MODEL))
            + '</div></div>')

    # ONLY ON A NEW QUESTION. Streamlit re-runs the script on every
    # interaction, so opening the chunk list -- or switching language or
    # theme -- would otherwise re-ask the model and spend the budget a
    # second time on the same question.
    if question and st.session_state.get("_asked") != question:
        with st.spinner(t("spinner", lang)):
            st.session_state["_result"] = ask(question)
        st.session_state["_asked"] = question
    result = st.session_state.get("_result") if question else None

    body = []
    if result is None:
        body.append(f'<div class="bfa-answer-en">{esc(t("empty", lang))}</div>')
    elif result.get("infra") == "embedder":
        # THE HOSTED EMBEDDER IS DOWN OR THE DAY'S QUOTA IS SPENT. One
        # sentence the reader can act on -- "try again in a while" -- and the
        # same note as the memory card: this was not a refusal.
        body.append(
            f'<div class="bfa-answer-en"><b>{esc(t("embedder_down", lang))}'
            '</b></div>')
        if DEV:
            body.append(
                f'<div class="bfa-flag note"><span class="k">'
                f'{esc(t("infra_note_head", lang))}</span><br>'
                + t("infra_note_body", lang,
                    id=f'<code>{esc(result["id"])}</code>',
                    flag='<code>answered: false</code>')
                + '</div>')
    elif result.get("infra"):
        # THE FOURTH CARD STATE. Not an abstention and not an answer: the
        # question was never put to anything. Said in the same voice as the
        # other three, and it names the cause, because the traceback this
        # replaces pointed at the index and the index is not at fault.
        body.append(
            f'<div class="bfa-answer-en"><b>{esc(t("infra_head", lang))}'
            '</b></div>')
        if not DEV:
            # The officer's version: what to do, and who to tell.
            body.append(f'<div class="bfa-flag">'
                        f'{esc(t("infra_simple", lang))}</div>')
        else:
            body.append(
                f'<div class="bfa-flag"><span class="k">'
                f'{esc(t("infra_flag_head", lang))}</span><br>'
                # infra_flag_body carries its own <br> and <b>; it is a
                # string from the table, not data, so it is not escaped.
                + t("infra_flag_body", lang, one=1, two=2, resident=2.5,
                    ceiling=15.6)
                + '</div>'
                f'<div class="bfa-flag note"><span class="k">'
                f'{esc(t("infra_note_head", lang))}</span><br>'
                + t("infra_note_body", lang,
                    id=f'<code>{esc(result["id"])}</code>',
                    flag='<code>answered: false</code>')
                + '</div>')
    elif result.get("error") and not DEV:
        # The raw reason ("groq: 429 ...") is a diagnostic; the officer gets
        # what it means for them.
        body.append(f'<div class="bfa-answer-en"><b>'
                    f'{esc(t("error_simple", lang))}</b></div>')
    elif result.get("error"):
        hint = t("error_untranslated", lang)
        body.append(
            f'<div class="bfa-answer-en"><b>{esc(t("error_head", lang))}'
            '</b></div>'
            f'<div class="bfa-flag"><span class="k">'
            f'{esc(t("error_reason", lang))}</span>'
            + (f' {esc(hint)}' if hint else '')
            + f'<br>{esc(result["error"])}</div>')
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
                + esc(t("floor_note", lang)) + '</span></div>')
        # 2. THE POST-FILTER. The model answered; the answer was discarded.
        if meta.get("filter_fired"):
            body.append(
                '<div class="bfa-flag"><span class="bn">'
                + esc(t("filter_note", lang)) + '</span></div>')
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
        #
        # gold_eval.is_abstention is the metric's own detector, reused
        # rather than re-written -- the page and the score must agree about
        # what an abstention is. Gated on the other two, which substitute
        # text that matches it by construction.
        if (not meta.get("floor_fired") and not meta.get("filter_fired")
                and _is_abstention(answer.get("answer"))):
            body.append(
                f'<div class="bfa-flag"><span class="k">'
                f'{esc(t("abstain_head", lang))}</span><br>'
                f'{esc(t("abstain_body", lang))}</div>')

        if DEV and meta.get("filter_skipped"):
            # The meta row keeps generate's English reason; the page shows
            # the same reason in the interface language.
            reason = (t("filter_skip_off", lang) if not POST_FILTER
                      else t("filter_skip_nogold", lang))
            body.append(
                f'<div class="bfa-flag note"><span class="k">'
                f'{esc(t("filter_skip_head", lang))}</span><br>'
                f'{esc(t("filter_skip_body", lang, reason=reason))}</div>')

        # 4. PRODUCT STATUS, read from the registry at query time.
        rows, populated, total = product_rows(question)
        if rows:
            lines = "".join(
                f'<div class="bfa-status"><span class="bfa-name">{esc(nm)}'
                f'</span>{status_badge(raw, lang)}</div>'
                for nm, raw in rows)
            body.append(
                f'<div class="bfa-flag note"><span class="k">'
                f'{esc(t("status_head", lang))}</span>' + lines +
                f'<br>{esc(t("status_note", lang, total=total, populated=populated))}'
                '</div>')

        model_used = meta.get("model") or t("meta_no_model", lang)
        if DEV:
            body.append(
                '<div class="bfa-meta">'
                + t("meta_line", lang,
                    model=f'<b>{esc(model_used)}</b>', provider=esc(PROVIDER),
                    k=K, top=n(f'{meta.get("top_fused") or 0:.4f}', lang),
                    prompt=n(f'{meta.get("prompt_tokens") or 0:,}', lang),
                    output=n(f'{meta.get("output_tokens") or 0:,}', lang),
                    id=f'<code>{esc(result["id"])}</code>',
                    path=esc(DEMO_META.as_posix()))
                + '</div>')

    # THE WELCOME PHOTO, AND ONLY ON WELCOME. Shown while nothing has been
    # asked; gone the moment any card state replaces the empty one -- an
    # answer, a refusal, an error. The answer area is copy-heavy, and the
    # brand guidance is to keep images out of it.
    if result is None:
        welcome = photo_html("river", lang)
        if welcome:
            st.markdown(welcome, unsafe_allow_html=True)

    st.markdown(
        '<div class="bfa-section"><div class="bfa-card">'
        f'<div class="bfa-label">{esc(t("answer_label", lang))}</div>'
        + "".join(body) +
        '</div></div>' + budget_warn, unsafe_allow_html=True)

    # THE DIVIDER IS GONE (BRAND.md 5: the motif is never redrawn, and no
    # official export is in assets/). The 40px of white space that replaces
    # it is .st-key-chunks' top padding.
    with st.container(key="chunks"):
        with st.expander(t("chunks_title", lang), expanded=False):
            chunks = (result or {}).get("chunks") or []
            cited, orphans = cite_match(
                ((result or {}).get("answer") or {}).get("citations"), chunks)
            # ABOVE THE LIST, NOT IN IT. A citation naming nothing retrieved
            # has no row to sit on, and an absent row is not something a
            # reader notices. It is also the most informative thing here.
            if orphans:
                st.markdown(
                    f'<div class="bfa-orphan"><span class="k">'
                    f'{esc(t("orphan_head", lang))}</span> '
                    + ", ".join(f"<b>{esc(cite_display(o, chunks))}</b>"
                                for o in orphans)
                    + f' – {esc(t("orphan_body", lang))}</div>',
                    unsafe_allow_html=True)
            if not chunks:
                st.markdown(
                    '<div class="bfa-chunk"><span class="cl">'
                    + esc(t({"embedder": "chunks_embedder",
                             "memory": "chunks_infra"}.get(
                                 (result or {}).get("infra"), "chunks_none"),
                            lang))
                    + '</span></div>', unsafe_allow_html=True)
            for c in chunks:
                st.markdown(
                    '<div class="bfa-chunk">'
                    # The chunk id and the fused score are developer
                    # information; the manual and clause are what an
                    # officer can look up.
                    + (f'<span class="id">{esc(c["chunk_id"])}</span>'
                       if DEV else '')
                    + f'<span class="cl">{esc(_mf_manual(c))} '
                    + (f'&sect;{esc(_mf_clause(c))}' if _mf_clause(c)
                       else f'<i>{esc(t("no_clause", lang))}</i>') + '</span>'
                    + (f'<span class="cited">{esc(t("cited", lang))}</span>'
                       if c["chunk_id"] in cited else '')
                    + (('<span class="sc">'
                        + esc(t("fused", lang,
                                score=n(f'{c["fused_score"]:.2f}', lang)))
                        + '</span>') if DEV else '')
                    + '</div>', unsafe_allow_html=True)



@st.cache_data(show_spinner=False)
def faq_rows(path, mtime):
    """The FAQ file, re-read when it changes on disk. `mtime` is the cache
    key, so an approval recorded with `python faq.py approve` shows on the
    next rerun without restarting the app -- the quiz bank is keyed the
    same way, for the same reason.

    The path is passed, not defaulted: faq.load's default is bound when
    faq is imported, so the key and the load could name different files
    the moment anything changed faq.PATH. The render test did exactly
    that and was served the real file."""
    return faq.load(path)


def render_faq(lang):
    """SURFACE 2. The reviewed FAQ.

    ONLY WHAT A PERSON APPROVED, AS IT NOW STANDS (faq.shown): an answer
    edited after its approval drops out until it is approved again, the
    same rule as the quiz. Bengali mode shows the Bengali. English mode
    shows the English only where that translation is approved too, and
    otherwise the reviewed Bengali with a line saying why -- never an
    unreviewed translation.

    The header photo sits above the list, alone: one photograph per screen,
    and never inside the FAQ list or a citation.
    """
    header = photo_html("enterprise", lang)
    if header:
        st.markdown(header, unsafe_allow_html=True)
    p = faq.PATH
    rows = faq.shown(faq_rows(str(p), p.stat().st_mtime if p.exists() else 0))
    with st.container(key="faq"):
        if not rows:
            st.markdown(f'<div class="bfa-sub">{esc(t("faq_empty", lang))}'
                        '</div>', unsafe_allow_html=True)
            return
        st.markdown(f'<div class="bfa-sub">'
                    f'{esc(t("faq_intro", lang, n=len(rows)))}</div>',
                    unsafe_allow_html=True)
        manuals = manifest()[1]
        for cat in faq.CATEGORIES:
            items = [r for r in rows if r["category"] == cat]
            if not items:
                continue
            st.markdown(f'<div class="bfa-label">'
                        f'{esc(t(f"faq_cat_{cat}", lang))}</div>',
                        unsafe_allow_html=True)
            for r in items:
                render_faq_item(faq.view(r, lang, manuals), lang)


def render_faq_item(v, lang):
    """One question. A form the answer refers to is offered ABOVE the
    answer, because the answer's first lines point at it."""
    import mimetypes
    with st.expander(v["question"]):
        att = Path(v["attachment"]) if v["attachment"] else None
        if att and att.exists():
            st.download_button(
                t(f"faq_dl_{att.stem}", lang), data=att.read_bytes(),
                file_name=att.name, key=f"faq_dl_{v['id']}",
                mime=mimetypes.guess_type(att.name)[0]
                or "application/octet-stream")
        body = "bfa-answer" if v["lang_used"] == "bn" else "bfa-faq-en"
        parts = [f'<div class="{body} bfa-faq-body">{esc(v["answer"])}</div>']
        if v["cites"]:
            chips = "".join(f'<span class="bfa-chip">{esc(c)}</span>'
                            for c in v["cites"])
            parts.append(f'<div class="bfa-chips">{chips}</div>')
        if v["fell_back"]:
            parts.append(f'<div class="bfa-flag note">'
                         f'{esc(t("faq_en_pending", lang))}</div>')
        st.markdown("".join(parts), unsafe_allow_html=True)

# Convention 1: importing this module must never write a file or call a
# provider. Everything above is a definition; main() does the work.
if __name__ == "__main__":
    main()
