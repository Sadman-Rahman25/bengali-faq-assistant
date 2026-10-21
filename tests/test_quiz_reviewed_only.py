"""The quiz shows only questions a person has reviewed as they now stand.

WHAT THIS IS GUARDING. Every question in quiz/bank_validated.yaml carries a
review record holding its fingerprint. Editing a question voids the review,
and the question must then leave the quiz until someone reviews it again --
not stay visible on the strength of a review of different text.

On a SCRATCH COPY of the bank, never the real one, two reviews are
cancelled the two ways that can happen:

  * EDITED: a question's wording changes, so its fingerprint no longer
    matches its review;
  * REMOVED: a question's review record is gone.

Neither may appear in quiz_app.bank(), nor in any of 400 seeded draws, and
the draw must still deal a full ten with no correct answer used more than
twice. The real bank is checked too: today every question is reviewed, so
all of them ship.

No model, no index, no network. NO BENGALI LITERAL: the edit appends an
ASCII marker to an existing question.

CONVENTION 3. The canary turns the filter off -- quiz_app.shippable passes
everything -- and the cancelled questions must then turn up in the draws,
or this file is not testing anything.

Runs standalone (python tests/test_quiz_reviewed_only.py) or under pytest.
"""
import collections
import random
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import quiz_app  # noqa: E402
import quiz_build  # noqa: E402

SEEDS = range(400)


def scratch_bank(tmp):
    """A copy of the real bank with two reviews cancelled.
    -> (path, edited id, removed id)"""
    path = Path(tmp) / "bank_validated.yaml"
    shutil.copy(ROOT / quiz_app.BANK, path)
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    reviewed = doc["meta"]["review_state"]["reviewed"]
    qs = [q for q in doc["questions"] if q["id"] in reviewed]
    edited, removed = qs[0], qs[1]
    edited["question_bn"] += " (edited)"
    del reviewed[removed["id"]]
    path.write_text(quiz_build.BANK_HEADER + yaml.safe_dump(
        doc, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8", newline=chr(10))
    return path, edited["id"], removed["id"]


def with_bank(path, fn):
    real = quiz_app.BANK
    quiz_app.BANK = path
    try:
        return fn()
    finally:
        quiz_app.BANK = real


def drawn_ids(qs):
    seen = set()
    for s in SEEDS:
        chosen = quiz_app.draw(qs, rng=random.Random(s))
        assert len(chosen) == quiz_app.N_PER_SESSION, len(chosen)
        worst = max(collections.Counter(
            quiz_app.answer_key(q) for q in chosen).values())
        assert worst <= quiz_app.MAX_SAME_ANSWER, worst
        seen |= {q["id"] for q in chosen}
    return seen


def test_cancelled_reviews_never_drawn():
    with tempfile.TemporaryDirectory() as tmp:
        path, edited, removed = scratch_bank(tmp)
        qs, _meta = with_bank(path, quiz_app.bank)
        ids = {q["id"] for q in qs}
        assert edited not in ids and removed not in ids, (edited, removed)
        seen = drawn_ids(qs)
        assert edited not in seen and removed not in seen


def test_edit_leaves_the_quiz_without_a_restart():
    """bank() is cached. Load a bank, edit one question in the same file,
    and the next load must already have dropped it: the cache is keyed on
    the file's modification time, not held for the life of the app."""
    import os
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "bank_validated.yaml"
        shutil.copy(ROOT / quiz_app.BANK, path)
        first, _m = with_bank(path, quiz_app.bank)
        target = first[0]["id"]
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        q = next(x for x in doc["questions"] if x["id"] == target)
        q["question_bn"] += " (edited)"
        path.write_text(quiz_build.BANK_HEADER + yaml.safe_dump(
            doc, allow_unicode=True, sort_keys=False, width=100),
            encoding="utf-8", newline=chr(10))
        st = path.stat()                  # a coarse clock must not hide it
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
        after, _m = with_bank(path, quiz_app.bank)
        assert target in {x["id"] for x in first}
        assert target not in {x["id"] for x in after}, target


def test_no_review_says_waiting_not_missing():
    """Every review removed: the bank is there, so the tab must say the
    questions are waiting for review -- not that the bank is missing and
    must be rebuilt. A missing file still gets the old message."""
    import ui_strings
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "bank_validated.yaml"
        shutil.copy(ROOT / quiz_app.BANK, path)
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        total = len(doc["questions"])
        doc["meta"]["review_state"]["reviewed"] = {}
        path.write_text(quiz_build.BANK_HEADER + yaml.safe_dump(
            doc, allow_unicode=True, sort_keys=False, width=100),
            encoding="utf-8", newline=chr(10))
        qs, _m = with_bank(path, quiz_app.bank)
        assert qs == []
        assert with_bank(path, lambda: quiz_app.empty_state(qs)) \
            == "quiz_awaiting_review"
        assert with_bank(path, quiz_app.bank_size) == total
        gone = Path(tmp) / "absent.yaml"
        assert with_bank(gone, lambda: quiz_app.empty_state([])) \
            == "quiz_no_bank"
    assert quiz_app.empty_state(quiz_app.bank()[0]) is None
    for lang in ui_strings.LANGS:
        for key in ("quiz_awaiting_review", "quiz_awaiting_review_body"):
            text = ui_strings.t(key, lang, total=total)
            assert text and "{" not in text, (key, lang)
    assert not ui_strings.missing(), ui_strings.missing()


def test_changed_manual_text_cancels_the_review():
    """The §2.5.4 re-render case. On a SCRATCH COPY of the manifest, one
    cited chunk's content hash changes while the question stays exactly as
    reviewed: that question's review must be cancelled, it must leave the
    quiz, and no other question may be affected. A missing manifest cancels
    every review -- the quiz fails closed."""
    import json
    import quiz_recheck
    man_path = ROOT / quiz_recheck.MANIFEST
    qs_all, meta = quiz_app._load_bank(str(ROOT / quiz_app.BANK), 0)
    state = meta["review_state"]
    target = qs_all[0]
    with tempfile.TemporaryDirectory() as tmp:
        man = json.loads(man_path.read_text(encoding="utf-8"))
        row = next(r for r in man["chunks"]
                   if r["chunk_id"] == target["chunk_id"])
        row["sha256"] = "0" * 64
        scratch = Path(tmp) / "chunk_manifest.json"
        scratch.write_text(json.dumps(man, ensure_ascii=False),
                           encoding="utf-8")
        hashes = quiz_recheck.chunk_hashes(scratch)
        assert quiz_recheck.review_status(target, state, hashes) \
            == "reviewed, changed since"
        sharing = {q["id"] for q in qs_all
                   if q["chunk_id"] == target["chunk_id"]}
        real = quiz_recheck.MANIFEST
        quiz_recheck.MANIFEST = scratch
        try:
            qs, _m = quiz_app.bank()
            ids = {q["id"] for q in qs}
            assert not (sharing & ids), sharing & ids
            assert ids == {q["id"] for q in qs_all} - sharing
            assert not (sharing & drawn_ids(qs))
            quiz_recheck.MANIFEST = Path(tmp) / "absent.json"
            assert quiz_app.bank()[0] == []
        finally:
            quiz_recheck.MANIFEST = real
    assert (ROOT / quiz_recheck.MANIFEST).read_text(encoding="utf-8") \
        == man_path.read_text(encoding="utf-8")


def test_real_bank_ships_every_reviewed_question():
    qs, meta = quiz_app.bank()
    raw = yaml.safe_load((ROOT / quiz_app.BANK).read_text(
        encoding="utf-8"))["questions"]
    reviewed = meta["review_state"]["reviewed"]
    assert {q["id"] for q in qs} == {q["id"] for q in raw if q["id"] in reviewed}
    assert quiz_app.answer_capacity(qs) >= quiz_app.N_PER_SESSION
    drawn_ids(qs)


def test_the_real_bank_is_untouched():
    """The scratch copy is the only thing edited."""
    before = (ROOT / quiz_app.BANK).read_bytes()
    with tempfile.TemporaryDirectory() as tmp:
        scratch_bank(tmp)
    assert (ROOT / quiz_app.BANK).read_bytes() == before


def canary():
    """With the filter off, the cancelled questions must be drawn."""
    real = quiz_app.shippable
    quiz_app.shippable = lambda qs, meta, hashes: qs
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path, edited, removed = scratch_bank(tmp)
            qs, _meta = with_bank(path, quiz_app.bank)
            seen = drawn_ids(qs)
    finally:
        quiz_app.shippable = real
    hit = edited in seen and removed in seen
    print("  canary filter_off  edited %s drawn=%s, removed %s drawn=%s  [%s]"
          % (edited, edited in seen, removed, removed in seen,
             "DETECTED" if hit else "MISSED"))
    return hit


def test_canary_fires():
    assert canary(), "with the filter off the cancelled questions were not drawn"


def main():
    print("reviewed-only canary")
    if not canary():
        print("SELF-CHECK FAILED -- this check is not testing anything")
        return 1
    for fn in (test_cancelled_reviews_never_drawn,
               test_edit_leaves_the_quiz_without_a_restart,
               test_no_review_says_waiting_not_missing,
               test_changed_manual_text_cancels_the_review,
               test_real_bank_ships_every_reviewed_question,
               test_the_real_bank_is_untouched):
        try:
            fn()
        except AssertionError as e:
            print("  FAIL %s: %s" % (fn.__name__, e))
            return 1
        print("  PASS %s" % fn.__name__)
    print("\nPASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
