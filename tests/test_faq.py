"""The FAQ shows only what a person approved, as it now stands.

Runs standalone (python tests/test_faq.py) or under pytest. Works on a
scratch copy; data/faq/answers.csv is read, never written.

  - an approved row shows; a fix_needed row does not
  - editing an approved answer, question or citation voids the approval
  - English shows only with its own approval, and an edit to the Bengali
    voids the English approval too -- a translation is approved as a
    translation of particular text
  - an English approval cannot be recorded on unapproved Bengali
  - rendered for real, in both languages: the English fallback line, the
    download button above the answer that refers to it, and citations
    in the interface language
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import faq  # noqa: E402

REAL = faq.PATH


def _rows():
    return faq.load(REAL)


def test_only_approved_rows_show():
    rows = _rows()
    shown = {r["id"] for r in faq.shown(rows)}
    assert shown == {r["id"] for r in rows if r["status"] == "approved"}
    assert not {r["id"] for r in rows if r["status"] != "approved"} & shown


def test_an_edit_voids_the_approval():
    for field in faq.SEEN_BN:
        rows = _rows()
        r = next(x for x in rows if faq.approved(x, "bn"))
        r[field] = (r[field] or "") + " x"
        assert not faq.approved(r, "bn"), f"editing {field} kept approval"


def test_status_and_notes_are_not_content():
    rows = _rows()
    r = next(x for x in rows if faq.approved(x, "bn"))
    r["note"] = "a reviewer's remark"
    r["review_date"] = "2099-01-01"
    assert faq.approved(r, "bn")


def test_english_needs_its_own_approval():
    rows = _rows()
    r = next(x for x in rows if faq.approved(x, "bn"))
    assert not faq.approved(r, "en")
    assert faq.view(r, "en")["fell_back"]
    faq.approve(rows, r["id"], "TEST", "en")
    assert faq.approved(r, "en")
    v = faq.view(r, "en")
    assert v["answer"] == r["answer_en"] and not v["fell_back"]
    # Bengali mode always shows Bengali
    assert faq.view(r, "bn")["answer"] == r["answer_bn"]


def test_a_bengali_edit_voids_the_english_approval():
    rows = _rows()
    r = next(x for x in rows if faq.approved(x, "bn"))
    faq.approve(rows, r["id"], "TEST", "en")
    r["answer_bn"] += " x"
    assert not faq.approved(r, "en")


def test_english_cannot_be_approved_first():
    rows = _rows()
    r = next(x for x in rows if x["status"] != "approved")
    try:
        faq.approve(rows, r["id"], "TEST", "en")
    except SystemExit:
        return
    raise AssertionError("an English approval was recorded on unapproved "
                         "Bengali")


SCRIPT = """
import os, sys
sys.path.insert(0, {root!r})
os.chdir({root!r})
import app
app.render_faq(os.environ["FAQ_TEST_LANG"])
"""


def _render(path, lang):
    from streamlit.testing.v1 import AppTest
    d = Path(tempfile.mkdtemp())
    (d / "s.py").write_text(SCRIPT.format(root=str(ROOT)), encoding="utf-8")
    os.environ["FAQ_TEST_LANG"] = lang
    saved = faq.PATH
    faq.PATH = Path(path)
    try:
        at = AppTest.from_file(str(d / "s.py"), default_timeout=120)
        at.run()
    finally:
        faq.PATH = saved
        shutil.rmtree(d, ignore_errors=True)
    assert not at.exception, at.exception
    return at


def test_the_tab_renders_both_languages():
    tmp = Path(tempfile.mkdtemp()) / "answers.csv"
    rows = _rows()
    att = next(r for r in rows if r["attachment"])
    faq.approve(rows, att["id"], "TEST")
    en = next(r for r in rows if faq.approved(r, "bn") and r is not att)
    faq.approve(rows, en["id"], "TEST", "en")
    faq.save(rows, tmp)
    for lang in ("bn", "en"):
        at = _render(tmp, lang)
        labels = [e.label for e in at.expander]
        md = " ".join(m.value for m in at.markdown)
        exp = next(e for e in at.expander if e.label == att["question_bn"])
        first = next(iter(exp.children.values()))
        assert type(first).__name__ == "DownloadButton", "button not first"
        if lang == "en":
            assert en["question_en"] in labels
            assert "waiting for review" in md
            assert "Operations Manual" in md
        else:
            assert not any(r["question_en"] in labels for r in rows)
    shutil.rmtree(tmp.parent, ignore_errors=True)


def test_the_real_file_is_untouched():
    before = REAL.read_bytes()
    test_english_needs_its_own_approval()
    assert REAL.read_bytes() == before


def canary():
    """Convention 3: with the fingerprint check disarmed, an edited answer
    would show -- so the edit test must fail against it."""
    real = faq.approved
    faq.approved = lambda row, lang="bn": row.get("status") == "approved"
    try:
        test_an_edit_voids_the_approval()
    except AssertionError:
        return True
    finally:
        faq.approved = real
    return False


def test_canary_fires():
    assert canary(), "the edit test passes even with fingerprints ignored"


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
