"""The live answer's guards, with the model stubbed: nothing leaves the
machine and no token is spent.

WHAT THIS IS GUARDING (generate.complete_guarded, generate.programme_label,
app.display_answer):

  - a reply that loops -- one word repeated to the output limit, as the live
    chatbot once returned -- is retried once, then replaced by a plain
    "try again"; it is never shown
  - a reply cut off at the output limit is retried once with a higher
    limit, then replaced by "too long, ask a narrower question"; the
    partial text is never shown
  - a figure from a chunk whose heading names a programme carries that
    programme when the answer itself names none
  - the answer the page shows contains no raw citation id

CONVENTION 3. The canary switches the guards off and checks that the
looping text then comes straight through -- otherwise these tests could
pass on a guard that never fired.

Runs standalone (python tests/test_answer_guards.py) or under pytest.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import generate as G  # noqa: E402
import productmatch as pm  # noqa: E402

# The failure as the live chatbot produced it: one programme name, looped.
# Read from productmatch, not typed (Convention 2).
WORD = pm.PROGRAMME_NAMES["dabi"][0]
LOOP = " ".join([WORD] * 300)
OK = "উত্তর: ৪% সঞ্চয় জমা দিতে হয়।\nCITATIONS: progoti §2.1.2"
PARTIAL = "- **progoti §1.2.2** – “"


def _meta(finish="stop"):
    return {"model": "stub", "prompt_tokens": 10, "output_tokens": 5,
            "finish_reason": finish}


class _Stub:
    """complete() replaced by a script of replies; records each call's
    options. The provider is set to one whose backend takes max_tokens."""

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, system, user, cache_system=True, **opts):
        self.calls.append(opts)
        r = self.replies[len(self.calls) - 1]
        if isinstance(r, Exception):
            raise r
        return r

    def __enter__(self):
        self.saved = G.complete, G._STATE.get("provider")
        G.complete, G._STATE["provider"] = self, "groq"
        return self

    def __exit__(self, *exc):
        G.complete, G._STATE["provider"] = self.saved


def test_a_loop_is_retried_then_refused():
    with _Stub([(LOOP, _meta("length")), (LOOP, _meta("length"))]) as s:
        text, meta = G.complete_guarded("s", "u")
    assert text == G.NO_ANSWER_TEXT, text[:60]
    assert len(s.calls) == 2 and meta["guard_outcome"] == "no_answer"
    assert WORD not in text


def test_a_loop_then_a_good_reply_shows_the_good_one():
    with _Stub([(LOOP, _meta("length")), (OK, _meta())]) as s:
        text, meta = G.complete_guarded("s", "u")
    assert text == OK and meta["guard"] == ["repetition"]
    assert meta["output_tokens"] == 10, "tokens of both attempts not summed"
    assert len(s.calls) == 2


def test_a_cut_off_reply_is_retried_with_a_higher_limit():
    with _Stub([(PARTIAL, _meta("length")), (OK, _meta())]) as s:
        text, meta = G.complete_guarded("s", "u")
    assert text == OK and meta["guard"] == ["length"]
    assert s.calls[1].get("max_tokens") == G.LENGTH_RETRY_TOKENS


def test_cut_off_twice_says_too_long_and_never_shows_the_part():
    with _Stub([(PARTIAL, _meta("length")), (PARTIAL, _meta("length"))]):
        text, meta = G.complete_guarded("s", "u")
    assert text == G.TOO_LONG_TEXT and meta["guard_outcome"] == "too_long"
    assert PARTIAL not in text


def test_truncated_during_reasoning_is_retried():
    with _Stub([G.ReplyTruncated("no content at the limit"), (OK, _meta())]):
        text, meta = G.complete_guarded("s", "u")
    assert text == OK and meta["guard"] == ["length"]


def test_no_reviewed_faq_text_counts_as_a_loop():
    import faq
    loops = [(r["id"], f) for r in faq.load(ROOT / faq.PATH)
             for f in ("answer_bn", "answer_en", "question_bn", "question_en")
             if G.degenerate(r[f])]
    assert not loops, loops


def _row():
    progoti = pm.PROGRAMME_NAMES["progoti"][0]
    return {"question_id": "X", "question_bn": "সঞ্চয় কত জমা দিতে হয়?",
            "chunks": [{"chunk_id": "x", "manual_id": "progoti",
                        "clause": "2.1.2", "fused_score": 0.9,
                        "heading_path": ["২.১ ঋণ সেবা",
                                         f"২.১.২ ইন্ডিভিজুয়াল সেবা "
                                         f"({progoti} কর্মসূচি)"],
                        "display_text": "…"}]}


def test_answer_one_labels_the_programme():
    progoti = pm.PROGRAMME_NAMES["progoti"][0]
    with _Stub([(OK, _meta())]):
        ans, meta = G.answer_one(_row(), 0.0, registry_rules=False)
    assert ans["answer"].endswith(f"এই তথ্য {progoti} কর্মসূচির।"), ans
    assert meta["programme_label"] is True


def test_no_label_when_the_answer_names_the_programme():
    progoti = pm.PROGRAMME_NAMES["progoti"][0]
    named = OK.replace("উত্তর:", f"{progoti} কর্মসূচিতে")
    with _Stub([(named, _meta())]):
        ans, meta = G.answer_one(_row(), 0.0, registry_rules=False)
    assert meta["programme_label"] is False, ans


def test_a_refused_loop_gets_no_label():
    with _Stub([(LOOP, _meta("length")), (LOOP, _meta("length"))]):
        ans, _m = G.answer_one(_row(), 0.0, registry_rules=False)
    assert ans["answer"] == G.NO_ANSWER_TEXT


def test_the_page_shows_no_raw_citation_id():
    import os
    cwd = os.getcwd()
    os.chdir(ROOT)                     # app reads the manifest by path
    try:
        import app
        _rows, manuals = app.manifest()
        # the forms seen live on 2026-10-26: in brackets, in 【】, with a
        # narrow no-break space, and the tag alone as a heading
        raw = ("উত্তর (dabi_2025 §2.1.2)। আরও【progoti §6.3】দেখুন। "
               "**dabi_2025 §6.1** এবং **dabi_2025**")
        shown = app.display_answer(raw, [])
    finally:
        os.chdir(cwd)
    assert not any(m in shown for m in manuals), shown
    assert "২.১.২" in shown and "৬.৩" in shown and "৬.১" in shown, shown


def test_an_inline_citations_line_never_reaches_the_page():
    text, cites = G.split_citations("কোন প্রকল্প জানতে চান? CITATIONS: none")
    assert text == "কোন প্রকল্প জানতে চান?" and cites == [], (text, cites)
    text, cites = G.split_citations("উত্তর ১৮%। CITATIONS: progoti §1.1.3")
    assert "CITATIONS" not in text and cites == ["progoti §1.1.3"]


def test_the_label_follows_the_registry_order():
    keys = list(pm.PROGRAMME_NAMES)[:4]
    names = [pm.PROGRAMME_NAMES[k][0] for k in keys]
    row = _row()
    row["chunks"][0]["heading_path"] = [
        "১.১.৩ " + ", ".join(reversed(names)) + "’র সকল কর্মসূচির জন্য প্রযোজ্য"]
    with _Stub([(OK, _meta())]):
        ans, _m = G.answer_one(row, 0.0, registry_rules=False)
    want = ", ".join(names[:-1]) + " ও " + names[-1]
    assert ans["answer"].endswith(f"এই তথ্য {want} কর্মসূচির।"), ans["answer"]


def canary():
    """With both guards off, the loop and the cut-off text come through."""
    saved = G.degenerate, G._cut_off
    G.degenerate, G._cut_off = (lambda _t: None), (lambda _m: False)
    try:
        with _Stub([(LOOP, _meta("length"))]):
            text, _m = G.complete_guarded("s", "u")
    finally:
        G.degenerate, G._cut_off = saved
    return text == LOOP


def test_canary_fires():
    assert canary(), "the guard tests would pass with the guards off"


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    bad = 0
    for t in tests:
        try:
            t()
            print(f"  ok    {t.__name__}")
        except AssertionError as e:
            bad += 1
            print(f"  FAIL  {t.__name__}: {e}")
    print(f"{len(tests) - bad} of {len(tests)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
