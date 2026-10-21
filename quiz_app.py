"""quiz_app.py -- STEP 11b. The quiz surface. No model, no index.

A SEPARATE APP, NOT A PAGE OR A TAB, and the reason is containment. A
Streamlit `pages/` entry would put a navigation sidebar on the answer page,
and a tab would change main()'s layout -- either way the answer surface
changes to accommodate the quiz. Run this one on its own port instead:

    streamlit run quiz_app.py --server.port 8502

app.py is imported for BRAND, the stylesheet and the manifest, so the two
surfaces cannot drift apart visually; importing it runs nothing, per
Convention 1. Nothing here calls a provider and nothing loads the index:
the bank is a YAML file, scoring is string comparison, and the citation is
read from the manifest. A run of this costs zero tokens by construction,
not by discipline.
"""

import collections
import json
import random
from pathlib import Path

import streamlit as st

# bnnorm imports nothing local, so -- unlike `app` -- it cannot make a cycle
# and is safe at module scope. answer_key needs canon here; see MAX_SAME_ANSWER.
from bnnorm import canon

# NO MODULE-LEVEL `import app`. app.py imports THIS module to put the quiz
# in a tab, so importing app here at module level would be a cycle. The
# helpers are fetched at CALL time instead, by which point app is already
# in sys.modules whichever way round the two were started.
def _app():
    import app
    return app

# The REVIEWED bank. quiz/bank.yaml is the raw build output; this is what
# survived validation. Only its questions a person has reviewed as they now
# stand are ever shown -- see shippable().
BANK = Path("quiz/bank_validated.yaml")
N_PER_SESSION = 10

# NO MORE THAN TWO QUESTIONS WITH THE SAME CORRECT ANSWER IN ONE SESSION.
#
# A DRAW CONSTRAINT, NOT A BANK CONSTRAINT. The bank is allowed to be
# lopsided -- it is a transcript of the manuals, and the manuals really do
# give the Branch Accountant ten separate duties. What may not happen is one
# SESSION reading as though the quiz were broken, and unconstrained sampling
# does that often: measured over 20,000 draws of ten from the 74-question
# bank, 3.69% held four or more questions with the identical correct answer
# and the worst draw held six. হিসাব কর্মকর্তা answers 10 of the 74 and
# সিডিও 7, so those two alone carry most of it.
MAX_SAME_ANSWER = 2

# RIGHT AND WRONG ARE STATUS COLOURS, by meaning (BRAND.md 3.2): the teal
# pair for a correct answer, the brick-red risk pair for a wrong one. Both
# are official codes and both are read through app.py's theme roles, so the
# quiz follows the light/dark switch with the rest of the page. The old
# #1f7a4d / #8a5157 were not in the brand table and are gone.
def quiz_css():
    BRAND = _app().BRAND
    return f"""
.qz-band {{ padding: 4px 22px 22px; }}
.qz-card {{ background: var(--card); border: 1px solid var(--card-line);
           border-radius: 10px; padding: 18px 22px; margin-bottom: 14px; }}
.qz-num {{ font-family: {BRAND['latin']}; font-size: var(--small);
          font-weight: 700; letter-spacing: .14em; text-transform: uppercase;
          color: var(--accent); margin: 0 0 10px; }}
.qz-q {{ font-family: {BRAND['bengali']}; font-size: {BRAND['bengali_px']};
        line-height: 1.8; color: var(--ink); }}
.qz-score {{ font-family: {BRAND['latin']}; font-size: 15px;
            color: var(--ink); }}
.qz-score b {{ font-size: 26px; }}
.qz-fb {{ font-family: {BRAND['latin']}; font-size: var(--chrome);
         line-height: 1.7; padding: 10px 14px; margin-top: 12px;
         border-left: 3px solid; }}
.qz-fb.right {{ border-left-color: var(--ok-ink); background: var(--ok-bg);
               color: var(--ok-ink); }}
.qz-fb.wrong {{ border-left-color: var(--risk-ink); background: var(--risk-bg);
               color: var(--risk-ink); }}
.qz-fb .bn {{ font-family: {BRAND['bengali']}; font-size: 17px; }}
.qz-exp {{ font-family: {BRAND['bengali']}; font-size: 16px; line-height: 1.8;
          color: var(--ink); margin-top: 8px; }}
.qz-cite {{ display: inline-block; background: var(--chip-bg);
           border: 1px solid var(--chip-line); color: var(--chip-ink);
           font-family: {BRAND['latin']}; font-size: var(--small);
           border-radius: 999px; padding: 2px 11px; margin-top: 10px; }}
.qz-note {{ font-family: {BRAND['latin']}; font-size: var(--small);
           color: var(--muted); margin-top: 8px; }}
.qz-note code {{ color: var(--muted); background: transparent; }}
.st-key-quiz {{ padding: 0 22px; }}
"""


def answer_key(q):
    """The identity one session may not repeat more than MAX_SAME_ANSWER times.

    canon()'d, not raw. The bank's `correct` values are verbatim corpus
    spans, so a percentage written in Bengali digits and the same percentage
    in ASCII are two spellings of one answer; canon folds digits and strips
    separators, which makes them one key. It also decomposes the nukta
    letters, so two spans differing only in nukta form cannot pass as two
    different answers.

    IT IS THE ANSWER STRING, NOT THE ROLE, AND THAT IS A KNOWN GAP.
    roles.py gives most roles two names -- the Branch Manager is both
    শাখা ব্যবস্থাপক and বিএম, and the same holds for the Area, Regional and
    Divisional Managers and for the field officer (সিডিও in Dabi, পিও in
    Progoti). Keyed on the string, a ten may therefore hold two
    শাখা ব্যবস্থাপক plus two বিএম: four questions a reader sees as four
    Branch Managers. Measured over 20,000 constrained draws, the worst
    same-role count is 4 where the worst same-string count is 2.

    Collapsing the aliases here is one lookup -- resolve the key through a
    map built from roles.ROLES before returning it. It is NOT done by
    default because the two halves of that map are not equally safe. The
    Branch Manager pair is plainly one answer under two spellings, but
    roles.py records সিডিও/পিও as a term that identifies WHICH MANUAL the
    asker works under, so two Dabi questions beside two Progoti questions
    are arguably four distinguishable correct answers rather than one
    repeated four times. Capacity permits either choice: 44 under string
    keying, 37 under role keying, against the 10 a session needs.
    """
    return canon(q["correct"])


def answer_capacity(qs, cap=MAX_SAME_ANSWER):
    """Largest session `cap` can fill from `qs`. -> int

    Exactly sum(min(count, cap)) over the distinct answers: each answer may
    contribute at most `cap` questions and no other constraint couples them,
    so the sum is both an upper bound and, by draw()'s single pass, reachable.
    Below N_PER_SESSION the bank cannot deal a full session. That is a fact
    about the BANK and has to be said out loud rather than absorbed by
    handing back a shorter quiz -- see draw() and render_quiz().
    """
    counts = collections.Counter(answer_key(q) for q in qs)
    return sum(min(n, cap) for n in counts.values())


def draw(qs, n=N_PER_SESSION, cap=MAX_SAME_ANSWER, rng=random):
    """`n` questions, no correct answer used more than `cap` times. -> list

    GREEDY OVER A FULL SHUFFLE, NOT REJECTION SAMPLING, and the difference is
    termination. Re-sampling ten until the ten happen to satisfy the cap has
    no bound on how long it runs, and the bound degrades exactly as the bank
    gets more lopsided -- which is the case this exists for. Walking one
    shuffled pass and taking every question that still fits is O(len(qs)) and
    always returns min(n, answer_capacity(qs)): a pass that runs to the end
    leaves every answer either at `cap` or exhausted, which is
    answer_capacity by definition, and stopping early at `n` can only be
    reached sooner.

    So a short return means the bank cannot do better, never that the draw
    gave up. The caller must SAY so; it is not an internal detail.

    The cost is that sampling is no longer uniform over questions -- a
    question whose answer is over-represented is likelier to arrive after the
    cap is spent and be skipped. That is the constraint working, and it is
    why a figure question, nearly all of which have unique answers, is drawn
    more often than any one role question.
    """
    pool = list(qs)
    rng.shuffle(pool)
    used, out = collections.Counter(), []
    for q in pool:
        if len(out) >= n:
            break
        k = answer_key(q)
        if used[k] < cap:
            used[k] += 1
            out.append(q)
    return out


def shippable(qs, meta):
    """Only the questions a PERSON has reviewed as they now stand.

    A Claude or automated check does not count, and neither does a review
    of an earlier wording: the bank's review_state holds each review with
    the question's fingerprint, and an edit since voids it. A voided
    question leaves the quiz until someone reviews it again -- it does not
    stay visible on the strength of a review of different text. The rule is
    quiz_recheck.review_status, imported rather than restated, so the quiz
    and the unread sheet cannot disagree about what is reviewed."""
    from quiz_recheck import review_status
    state = meta.get("review_state") or {}
    return [q for q in qs if review_status(q, state) == "reviewed"]


@st.cache_data(show_spinner=False)
def _load_bank(path, mtime_ns):
    """The parsed file, cached per path and modification time."""
    import yaml
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return (doc.get("questions") or []), (doc.get("meta") or {})


def bank():
    """-> (reviewed questions, meta). A YAML file; no model, no index.

    KEYED ON THE FILE'S MTIME. The cache once had no key, so an edit to the
    bank -- which voids that question's review -- was not seen until the
    app restarted, and the question stayed in the quiz meanwhile."""
    if not BANK.exists():
        return [], {}
    qs, meta = _load_bank(str(BANK), BANK.stat().st_mtime_ns)
    return shippable(qs, meta), meta


def bank_size():
    """Questions in the bank file, reviewed or not. -> int"""
    if not BANK.exists():
        return 0
    qs, _meta = _load_bank(str(BANK), BANK.stat().st_mtime_ns)
    return len(qs)


def empty_state(qs):
    """Which empty message the tab needs, given bank()'s reviewed questions.
    -> None (there is a quiz), "quiz_awaiting_review" or "quiz_no_bank".

    WAITING, NOT MISSING. When the file holds questions and none is
    reviewed as it now stands, "run quiz_build.py" is the wrong advice:
    nothing is missing, the questions are waiting for a person."""
    if qs:
        return None
    return "quiz_awaiting_review" if bank_size() else "quiz_no_bank"


def citation(q):
    """দাবি অপারেশন ম্যানুয়াল ২০২৫ §১.১.২ -- manual label from the manifest,
    clause in the form the manual prints. Neither is typed here."""
    _rows, manuals = _app().manifest()
    name = manuals.get(q["manual_id"], q["manual_id"])
    disp = q.get("clause_display") or q.get("clause") or ""
    return f"{name} §{disp}" if disp else name


def render_quiz(lang=None):
    """SURFACE 3. The quiz, with no page chrome of its own.

    Everything that made this a separate app -- set_page_config, the
    stylesheet, the header, the footer -- belonged to the PAGE, not to the
    quiz. Removing it is the whole merge. The two properties that matter
    are untouched: nothing here calls a provider and nothing loads the
    index, so this still renders with the model unreachable.

    `lang` is the interface language; questions, options, explanations and
    citations are bank data and stay as the bank holds them.
    """
    app = _app()
    esc, t, n = app.esc, app.t, app.n
    lang = lang or app.current_lang()
    st.markdown(f"<style>{quiz_css()}</style>", unsafe_allow_html=True)
    qs, meta = bank()
    empty = empty_state(qs)
    if empty == "quiz_awaiting_review":
        st.markdown('<div class="qz-band"><div class="qz-card">'
                    '<div class="qz-num">'
                    f'{esc(t("quiz_awaiting_review", lang))}</div>'
                    '<div class="qz-note">'
                    + esc(t("quiz_awaiting_review_body_dev" if app.DEV
                            else "quiz_awaiting_review_body", lang,
                            total=bank_size()))
                    + '</div></div></div>', unsafe_allow_html=True)
        return
    if empty == "quiz_no_bank":
        st.markdown('<div class="qz-band"><div class="qz-card">'
                    f'<div class="qz-num">{esc(t("quiz_no_bank", lang))}</div>'
                    '<div class="qz-note">'
                    + (t("quiz_no_bank_body", lang,
                         path=f"<code>{esc(str(BANK))}</code>",
                         cmd="<code>python quiz_build.py</code>")
                       if app.DEV else esc(t("quiz_not_ready", lang)))
                    + '</div></div></div>', unsafe_allow_html=True)
        return

    # ONE DRAW PER SESSION. Streamlit re-runs the whole script on every radio
    # click, so sampling at render time would deal a new ten each time an
    # answer is picked. The draw is stored, not the randomness.
    if "quiz_ids" not in st.session_state:
        st.session_state["quiz_ids"] = [q["id"] for q in draw(qs)]
    chosen = [q for qid in st.session_state["quiz_ids"]
              for q in qs if q["id"] == qid]

    with st.container(key="quiz"):
        # NO PHOTO HERE. Ten questions on one page is copy-heavy, and
        # BRAND.md 6 says to leave images out of copy-heavy pages; the empty
        # slot for brac-members.webp was removed in the brand-audit pass.
        # A SHORT DRAW IS REPORTED, NOT ABSORBED. draw() returns fewer than
        # ten only when the cap and the bank together cannot do better, and a
        # quiz that quietly serves eight looks like a quiz with two questions
        # missing. Name the number and the reason instead.
        st.markdown(
            '<div class="qz-note">'
            + esc(t("quiz_intro_dev" if app.DEV else "quiz_intro", lang,
                    n=len(chosen), total=len(qs), cap=MAX_SAME_ANSWER))
            + '</div>', unsafe_allow_html=True)
        if len(chosen) < N_PER_SESSION:
            st.markdown(
                '<div class="qz-note"><b>'
                + esc(t("quiz_short_head", lang, n=len(chosen),
                        want=N_PER_SESSION))
                + '</b> '
                + (esc(t("quiz_short_body", lang, cap=MAX_SAME_ANSWER,
                         total=len(qs), capacity=answer_capacity(qs)))
                   if app.DEV else '')
                + '</div>', unsafe_allow_html=True)

        # ALL TEN ON ONE PAGE, ONE SUBMIT. There is no model call and so no
        # latency to hide, and a single submit gives one score instead of
        # ten. It also means the review below can be read end to end: the
        # point of the quiz is the explanation and the citation, and those
        # are more useful side by side than one at a time.
        with st.form("quiz", border=False):
            picks = {}
            for i, q in enumerate(chosen, 1):
                st.markdown(
                    f'<div class="qz-card"><div class="qz-num">'
                    f'{esc(t("quiz_question", lang, i=i))}'
                    f'</div><div class="qz-q">{esc(q["question_bn"])}</div>'
                    '</div>', unsafe_allow_html=True)
                picks[q["id"]] = st.radio(
                    t("quiz_question", lang, i=i), q["options"], index=None,
                    key=f"pick_{q['id']}", label_visibility="collapsed")
            submitted = st.form_submit_button(t("quiz_submit", lang),
                                              type="primary")

    if not submitted:
        return

    score = sum(1 for q in chosen if picks.get(q["id"]) == q["correct"])
    body = [f'<div class="qz-card"><div class="qz-num">'
            f'{esc(t("quiz_score", lang))}</div>'
            '<div class="qz-score">'
            + t("quiz_score_of", lang, score=f"<b>{n(score, lang)}</b>",
                n=len(chosen))
            + '</div></div>']
    for i, q in enumerate(chosen, 1):
        got = picks.get(q["id"])
        ok = got == q["correct"]
        cls = "right" if ok else "wrong"
        verdict = t("quiz_right" if ok else (
            "quiz_unanswered" if got is None else "quiz_wrong"), lang)
        chosen_line = ("" if got is None else
                       f'<br>{esc(t("quiz_you_chose", lang))} '
                       f'<span class="bn">{esc(got)}</span>')
        body.append(
            f'<div class="qz-card">'
            f'<div class="qz-num">{esc(t("quiz_question", lang, i=i))}</div>'
            f'<div class="qz-q">{esc(q["question_bn"])}</div>'
            f'<div class="qz-fb {cls}"><b>{esc(verdict)}</b>{chosen_line}'
            f'<br>{esc(t("quiz_correct_answer", lang))} '
            f'<span class="bn">{esc(q["correct"])}</span>'
            '</div>'
            f'<div class="qz-exp">{esc(q["explanation"])}</div>'
            f'<div class="qz-cite">{esc(citation(q))}</div>'
            '</div>')

    st.markdown('<div class="qz-band">' + "".join(body) + '</div>',
                unsafe_allow_html=True)

def main():
    """Standalone entry point, kept working: `streamlit run quiz_app.py`.

    The quiz is a TAB in app.py now, but it is still the only surface that
    needs neither provider nor index, which makes it the one to run when
    either is unavailable. Page chrome lives here, not in render_quiz.
    """
    app = _app()
    lang, theme = app.current_lang(), app.current_theme()
    st.set_page_config(page_title=app.t("quiz_page_title", lang),
                       layout="wide", initial_sidebar_state="collapsed")
    st.markdown(f"<style>{app.theme_vars(theme, lang)}{app.CSS}</style>",
                unsafe_allow_html=True)
    app.render_prefs(lang)
    lang, theme = app.current_lang(), app.current_theme()
    st.markdown(f"<style>{app.theme_vars(theme, lang)}</style>",
                unsafe_allow_html=True)
    a = app.assets()
    st.markdown(
        '<div class="bfa-header">'
        f'<img src="{a[app.THEMES[theme]["logo"]]}" '
        f'alt="{app.esc(app.t("logo_alt", lang))}">'
        f'<div class="bfa-title">{app.esc(app.t("title", lang))}</div>'
        f'<div class="bfa-sub">{app.esc(app.t("quiz_subtitle", lang))}</div>'
        '</div>', unsafe_allow_html=True)
    render_quiz(lang)


# Convention 1: importing must never write a file or call a provider.
if __name__ == "__main__":
    main()
