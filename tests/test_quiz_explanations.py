"""Two validator rules: joined explanations, and distractors as whole words.

1. JOINED EXPLANATIONS. An explanation may join pieces of its OWN cited
   chunk with quiz_recheck.EXPLANATION_JOIN, as long as every piece is
   verbatim in that chunk. Q-043 and Q-069 join the programme label to a
   flattened table row; the label is what stops the p6 mix-up, where the
   same role sat under another programme's column. A joined explanation
   whose pieces are all in the chunk passes; one with a piece from a
   different chunk fails.

2. DISTRACTORS AS WORDS. quiz_build.validate flagged Q-043's পিএস as
   "occurs in the chunk" for the letters inside ডিপিএসসহ (DPS).
   quiz_build.occurs_as_word uses the registry topic gate's Bengali word
   boundaries instead. A figure with digit separators must still be found:
   the registry's own canon_pattern keeps separators, and canon'd text does
   not, so using it would have silently stopped matching ১,০০,০০০ টাকা.

And the real bank: zero validate failures and zero 4e hits across every
question, against the corpus as it is now.

No model, no index, no network. Role names come from the vocabulary, and
every fixture is sliced from a stored chunk; no Bengali literal is typed.

CONVENTION 3. Each canary is the old behaviour: the substring check must
flag progoti_0090_p4, and the whole-line check must fail Q-043.

Runs standalone (python tests/test_quiz_explanations.py) or under pytest.
"""
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import quiz_build as qb  # noqa: E402
import quiz_recheck as qr  # noqa: E402
from bnnorm import canon  # noqa: E402

JOIN = qr.EXPLANATION_JOIN
_CHUNKS = None


def chunks():
    global _CHUNKS
    if _CHUNKS is None:
        _CHUNKS = {c["chunk_id"]: c for c in qb.load_chunks()}
    return _CHUNKS


def text(cid):
    return chunks()[cid]["display_text"]


def bank():
    doc = yaml.safe_load((ROOT / "quiz/bank_validated.yaml")
                         .read_text(encoding="utf-8"))
    return {q["id"]: q for q in doc["questions"]}


def ok(explanation, cid):
    return qr.explanation_in_chunk({"explanation": explanation}, text(cid),
                                   canon)


def name_of(key):
    return next(n for n, k in qb.role_vocabulary() if k == key)


# ------------------------------------------------------- joined explanations
def test_joined_pieces_of_the_same_chunk_pass():
    t = text("dabi_2025_0256")
    head, tail = t[:40].strip(), t[-40:].strip()
    assert ok(head + JOIN + tail, "dabi_2025_0256")
    assert not ok(head + " " + tail, "dabi_2025_0256"), "run together"


def test_a_piece_from_another_chunk_fails():
    head = text("dabi_2025_0256")[:40].strip()
    other = text("progoti_0139")[:40].strip()
    assert other not in text("dabi_2025_0256")
    assert not ok(head + JOIN + other, "dabi_2025_0256")


def test_the_three_joined_bank_explanations_pass():
    qs = bank()
    for i in ("Q-040", "Q-043", "Q-069"):
        q = qs[i]
        assert JOIN in q["explanation"], i
        assert ok(q["explanation"], q["chunk_id"]), i


def test_q043_in_the_wrong_programme_chunk_fails():
    """Q-043's explanation against progoti_0090_p6, the chunk it used to
    cite: the programme-label piece is not there, so it fails."""
    q = bank()["Q-043"]
    assert not ok(q["explanation"], "progoti_0090_p6")


# ------------------------------------------------------ distractors as words
def test_ps_is_not_found_inside_dps():
    ps = name_of("programme_sohokari")
    t = text("progoti_0090_p4")
    assert ps in t                                # the letters are there
    assert not qb.occurs_as_word(ps, t)           # the post is not


def test_a_post_written_as_a_word_is_found():
    """A chunk where the name stands between non-Bengali characters, found
    by plain character tests, so the check is not graded by itself."""
    ps = name_of("programme_sohokari")

    def standalone(t):
        i = t.find(ps)
        while i != -1:
            before = t[i - 1] if i else " "
            after = t[i + len(ps): i + len(ps) + 1] or " "
            if not qr._bn_letter(before) and not qr._bn_letter(after):
                return True
            i = t.find(ps, i + 1)
        return False
    cid = next(c for c, ch in chunks().items()
               if standalone(ch.get("display_text") or ""))
    assert qb.occurs_as_word(ps, text(cid)), cid


def test_figures_with_separators_are_found():
    spans = [(cid, sp) for cid, c in chunks().items()
             for sp, _u in qb.figure_spans(c.get("display_text") or "")
             if "," in sp]
    assert spans
    missed = [(cid, sp) for cid, sp in spans
              if not qb.occurs_as_word(sp, text(cid))]
    assert not missed, missed[:5]


def test_q043_validates():
    q = bank()["Q-043"]
    assert qb.validate(q, text(q["chunk_id"])) == []


# ------------------------------------------------------------ the real bank
def test_bank_has_no_failures():
    qs = bank()
    bad = {i: qb.validate(q, text(q["chunk_id"])) for i, q in qs.items()
           if q["chunk_id"] in chunks()}
    bad = {i: v for i, v in bad.items() if v}
    missing = [i for i, q in qs.items() if q["chunk_id"] not in chunks()]
    not_verbatim = [i for i, q in qs.items() if q["chunk_id"] in chunks()
                    and not ok(q["explanation"], q["chunk_id"])]
    assert not bad and not missing and not not_verbatim, \
        (bad, missing, not_verbatim)


# ------------------------------------------------------------------ canaries
def canary():
    """The old checks must reproduce both failures."""
    q = bank()["Q-043"]
    ps = name_of("programme_sohokari")
    substring_flags = ps in text("progoti_0090_p4")
    def norm(s):
        return " ".join(canon(s).split())
    whole_line_fails = norm(q["explanation"].rstrip(" ।")) not in norm(
        text(q["chunk_id"]))
    hit = substring_flags and whole_line_fails
    print("  canary old_checks  substring flags PS-in-DPS=%s, whole line "
          "fails Q-043=%s  [%s]" % (substring_flags, whole_line_fails,
                                    "DETECTED" if hit else "MISSED"))
    return hit


def test_canary_fires():
    assert canary(), "the old checks no longer reproduce the failures"


def main():
    print("validator rules canary")
    if not canary():
        print("SELF-CHECK FAILED -- this check is not testing anything")
        return 1
    for fn in (test_joined_pieces_of_the_same_chunk_pass,
               test_a_piece_from_another_chunk_fails,
               test_the_three_joined_bank_explanations_pass,
               test_q043_in_the_wrong_programme_chunk_fails,
               test_ps_is_not_found_inside_dps,
               test_a_post_written_as_a_word_is_found,
               test_figures_with_separators_are_found,
               test_q043_validates,
               test_bank_has_no_failures):
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
