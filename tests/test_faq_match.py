"""Reviewed answers first: a question an approved FAQ answers gets that
answer; any other question goes to the model as before.

Runs standalone (python tests/test_faq_match.py) or under pytest. No
provider call and no embedding: similarity is stubbed where a test needs
one, and the measurement test reads the cached vectors or skips.

  - coverage: every must-not-match question the test file pins to a
    tempting FAQ has a word that FAQ's answer does not contain -- except
    the one the threshold is there to stop
  - the rule: below THRESHOLD, or a word uncovered, is no match
  - the answer is in the officer's language
  - the app: a matched question shows the reviewed answer with its line
    and its link, makes no model call, and spends one embedding; a near
    miss is retrieved and generated with that same vector
  - the link opens the FAQ tab with that answer open, and the open tab
    survives a language switch
  - measured on the cached vectors: the threshold in code is the one the
    test questions give, and no question gets a wrong reviewed answer
"""
import csv
import io
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import faq  # noqa: E402
import faqmatch  # noqa: E402

# Coverage lets this one through -- the complaints answer names the call
# centre -- and the threshold stops it (0.617). test_measured pins that.
THRESHOLD_GUARDED = {"কল সেন্টারের নম্বর কত?"}


def setup_module(_module):
    # faq and faqmatch read repo-relative paths. Done here and in main(),
    # not at import (Convention 1).
    os.chdir(ROOT)


def _tests():
    return list(csv.DictReader(io.open(faqmatch.TESTS, encoding="utf-8")))


def _row(fid):
    return next(r for r in faq.load() if r["id"] == fid)


# The questions come from the test file, not typed here: a product name in
# a literal is Convention 2's to flag. F-07 is the Migration Loan answer.
def _asks(fid, kind):
    return [r["query"] for r in _tests()
            if r["expect"] == fid and r["kind"] == kind]


def _near(fid):
    return next(r["query"] for r in _tests() if r["expect"] == "none"
                and r["note"].startswith(fid + " "))


def _plain():
    """A rewording of F-07 that every word of is in its answer."""
    return _asks("F-07", "reword")[1]


def test_coverage_stops_the_near_misses():
    pinned = [r for r in _tests() if r["expect"] == "none"
              and r["note"][:2] == "F-"]
    assert len(pinned) >= 15, "the near misses lost their FAQ ids"
    for r in pinned:
        fid = r["note"].split()[0]
        missing = faqmatch.uncovered(r["query"], _row(fid))
        if r["query"] in THRESHOLD_GUARDED:
            continue
        assert missing, f"{r['query']} looks answered by {fid}"


def test_coverage_passes_a_plain_rewording():
    # every word asked is in F-07's question or answer
    assert faqmatch.uncovered(_plain(), _row("F-07")) == []
    assert faqmatch.uncovered(_asks("F-07", "english")[0],
                              _row("F-07")) == []


class _Best:
    """faqmatch.best, stubbed to a fixed (id, score, language)."""
    def __init__(self, fid, score, lang="bn"):
        self.v = (fid, score, lang)

    def __call__(self, qv, model, rows=None):
        return self.v


def _with_best(fid, score, lang, fn):
    real = faqmatch.best
    faqmatch.best = _Best(fid, score, lang)
    try:
        return fn()
    finally:
        faqmatch.best = real


def test_the_rule():
    q = _plain()
    above, below = faqmatch.THRESHOLD + 0.01, faqmatch.THRESHOLD - 0.001
    hit, _ = _with_best("F-07", above, "bn",
                        lambda: faqmatch.match(q, None, None))
    assert hit and hit["id"] == "F-07"
    hit, why = _with_best("F-07", below, "bn",
                          lambda: faqmatch.match(q, None, None))
    assert hit is None and why["faq_rejected"] == "below threshold"
    near = _near("F-07")
    hit, why = _with_best("F-07", 0.99, "bn",
                          lambda: faqmatch.match(near, None, None))
    assert hit is None and why["faq_rejected"] == "not covered"


def test_only_approved_answers_are_candidates():
    rows = faq.load()
    r = next(x for x in rows if x["id"] == "F-07")
    r["answer_bn"] += " (edited)"          # voids the approval
    assert "F-07" not in {i for i, _l, _t in faqmatch.questions(rows)}


def test_the_officers_language():
    assert faqmatch.answer_lang(_plain(), "en") == "bn"
    assert faqmatch.answer_lang(_asks("F-07", "english")[0], "en") == "en"
    # Banglish that matched a Bengali question is answered in Bengali
    assert faqmatch.answer_lang(_asks("F-07", "banglish")[0], "bn") == "bn"


def test_measured():
    """On the cached vectors: no embedding is made (model=None would
    fail), so this skips on a clone that has not run --measure."""
    try:
        rep = faqmatch.measure(None)
    except Exception as e:   # noqa: BLE001 -- an uncached text, or no cache
        print(f"  skip  test_measured: {type(e).__name__}")
        return
    assert rep["threshold"] == faqmatch.THRESHOLD, rep["threshold"]
    assert rep["shown_wrong"] == []
    assert len(rep["kept"]) >= 74
    guarded = [r for r in rep["rows"] if r["query"] in THRESHOLD_GUARDED]
    assert guarded and all(r["score"] < faqmatch.THRESHOLD for r in guarded)


# ------------------------------------------------------------------ the app
class _Ix:
    """retrieve.Index, stubbed: one vector per question, counted."""
    cfg = {"model": faqmatch.MODEL}
    model = None

    def __init__(self):
        self.encoded, self.searched = [], []

    def query_vector(self, q):
        self.encoded.append(q)
        return "VEC"

    def search(self, q, k=10, qv=None, **_kw):
        self.searched.append(qv)
        return []


def _ask(question, best):
    """app.ask with the index, similarity and model stubbed, writing to a
    scratch directory. -> (result, ix, model calls)"""
    import app
    import generate
    tmp = Path(tempfile.mkdtemp())
    saved = {k: getattr(app, k) for k in
             ("index", "OUT", "DEMO_META", "DEMO_ANSWERS", "DEMO_RETRIEVED")}
    saved_gen = (generate.answer_one, generate.check_corpus_guard,
                 generate.configure)
    ix, calls = _Ix(), []
    app.index = lambda: ix
    app.OUT, app.DEMO_META = tmp, tmp / "meta.jsonl"
    app.DEMO_ANSWERS, app.DEMO_RETRIEVED = tmp / "a.jsonl", tmp / "r.jsonl"
    generate.check_corpus_guard = lambda *a, **k: "stub"
    generate.configure = lambda *a, **k: None

    def answer_one(row, floor, **kw):
        calls.append(row)
        return {"answer": "generated", "citations": []}, {}
    generate.answer_one = answer_one
    try:
        res = _with_best(*best, lambda: app.ask(question))
    finally:
        for k, v in saved.items():
            setattr(app, k, v)
        (generate.answer_one, generate.check_corpus_guard,
         generate.configure) = saved_gen
        shutil.rmtree(tmp, ignore_errors=True)
    return res, ix, calls


def test_a_match_skips_the_model():
    res, ix, calls = _ask(_plain(), ("F-07", 0.95, "bn"))
    assert res["faq"]["id"] == "F-07" and not calls
    assert res["meta"]["answer_source"] == "reviewed FAQ"
    assert res["faq"]["view"]["answer"] == _row("F-07")["answer_bn"]
    assert len(ix.encoded) == 1 and ix.searched == []
    res, _ix, _c = _ask(_asks("F-07", "english")[0], ("F-07", 0.95, "en"))
    assert res["faq"]["view"]["answer"] == _row("F-07")["answer_en"]


def test_a_near_miss_goes_to_the_model_with_the_same_vector():
    res, ix, calls = _ask(_near("F-07"), ("F-07", 0.99, "bn"))
    assert "faq" not in res and len(calls) == 1
    assert len(ix.encoded) == 1 and ix.searched == ["VEC"]
    assert res["meta"]["faq_match"]["faq_rejected"] == "not covered"


SCRIPT = """
import os, sys
sys.path.insert(0, {root!r})
os.chdir({root!r})
import app, faq
import streamlit as st
row = next(r for r in faq.load() if r["id"] == "F-48")
def ask(q):
    hit = {{"id": "F-48", "score": 0.95, "lang": "bn", "row": row,
            "view": faq.view(row, "bn")}}
    return {{"id": "D-test", "faq": hit, "chunks": [], "meta": {{}}}}
app.ask = ask
import quiz_app
quiz_app.render_quiz = lambda lang: None
st.session_state.setdefault("lang", os.environ["FAQ_TEST_LANG"])
st.session_state.setdefault("q", "ট্রাভেলিং বিল কীভাবে পূরণ করব?")
app.main()
"""


def _app(d, lang):
    """The whole page, with ask() stubbed to a reviewed answer. The script
    lives in `d`, which must outlive every rerun."""
    from streamlit.testing.v1 import AppTest
    (d / "s.py").write_text(SCRIPT.format(root=str(ROOT)), encoding="utf-8")
    os.environ["FAQ_TEST_LANG"] = lang
    at = AppTest.from_file(str(d / "s.py"), default_timeout=120)
    at.run()
    assert not at.exception, at.exception
    return at


def test_the_page_and_its_link():
    d = Path(tempfile.mkdtemp())
    try:
        _page_and_link(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _page_and_link(d):
    import app
    row = _row("F-48")
    at = _app(d, "en")
    md = " ".join(m.value for m in at.markdown)
    assert "Reviewed answer" in md and row["question_bn"] in md
    assert row["answer_bn"].splitlines()[0][:30] in md
    assert "Nothing was searched – this is a reviewed FAQ answer." in md
    # the form the answer refers to, on the ask tab too
    assert any(b.key == "ask_dl_F-48" for b in at.get("download_button"))
    link = next(b for b in at.button if b.key == "reviewed_open")
    assert link.label == app.t("reviewed_open", "en")
    link.click().run()
    assert at.session_state["tabs"] == app.t("tab_faq", "en")
    assert at.session_state["faq_open"] == "F-48"
    opened = [e for e in at.expander if e.proto.expanded]
    assert [e.label for e in opened] == [row["question_en"]]
    # a language switch keeps the FAQ tab open
    at.session_state["lang"] = "bn"
    at.run()
    assert at.session_state["tabs"] == app.t("tab_faq", "bn")


def canary():
    """Convention 3: with coverage disarmed, the near-miss test must
    fail -- similarity alone would show F-07 for the countries question."""
    real = faqmatch.uncovered
    faqmatch.uncovered = lambda question, row: []
    try:
        test_coverage_stops_the_near_misses()
    except AssertionError:
        return True
    finally:
        faqmatch.uncovered = real
    return False


def test_canary_fires():
    assert canary(), "the near-miss test passes with coverage disarmed"


def main():
    setup_module(None)
    sys.stdout.reconfigure(encoding="utf-8")
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
    print(f"{len(tests) - bad}/{len(tests)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
