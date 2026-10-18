"""The session draw must not deal the same correct answer more than twice.

WHAT THIS IS GUARDING. quiz_app.draw replaced random.sample, and the bank it
draws from is lopsided by nature: 53 of its 74 questions are role questions
answered out of a 16-name vocabulary, হিসাব কর্মকর্তা answers 10 of them and
সিডিও 7. Unconstrained, 3.69% of ten-question draws held four or more
questions with the identical correct answer -- which reads to an officer as a
broken quiz, not as a lopsided manual.

Three properties, and the third is the one with teeth:

  * the cap holds on every draw from the real bank;
  * answer_capacity is exact -- an unbounded draw returns precisely that
    many questions, so the number the UI quotes when it cannot fill ten is
    the number the bank can actually support;
  * a draw that CANNOT fill ten returns what it can and nothing pretends
    otherwise. Exercised against a deliberately starved bank, because the
    real one has capacity 44 against a need of 10 and can never reach that
    path.

CONVENTION 3. The canary is the code this change removed: random.sample over
the same bank, which must be caught exceeding the cap. Without it, a draw()
that silently stopped constraining anything would leave every assertion here
passing -- the cap assertion is only meaningful if the same assertion fails
on unconstrained sampling.

NO BENGALI LITERAL APPEARS IN THIS FILE. Every fixture is sliced out of
quiz/bank_validated.yaml, per Convention 3: the starved bank below is real
questions that really do share one answer, so it cannot disagree with the
artefact about spelling, digits or nukta form.

Runs standalone (python tests/test_quiz_draw.py) or under pytest.
"""
import collections
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from quiz_app import (MAX_SAME_ANSWER, N_PER_SESSION, answer_capacity,  # noqa: E402
                      answer_key, bank, draw)

SEEDS = range(400)


def questions():
    qs, _meta = bank()
    assert qs, "quiz/bank_validated.yaml is empty or missing"
    return qs


def worst_repeat(chosen):
    """Most questions in `chosen` sharing one correct answer. -> int"""
    counts = collections.Counter(answer_key(q) for q in chosen)
    return max(counts.values()) if counts else 0


def starved_bank(qs):
    """A bank whose capacity is below N_PER_SESSION, built from real rows.

    Every question with the bank's single most common answer, plus one
    question with a different answer. Capacity is therefore
    MAX_SAME_ANSWER + 1, which is 3 -- far short of the ten a session wants,
    so draw() has to take the short-return path.
    """
    counts = collections.Counter(answer_key(q) for q in qs)
    top, _n = counts.most_common(1)[0]
    same = [q for q in qs if answer_key(q) == top]
    other = next(q for q in qs if answer_key(q) != top)
    return same + [other]


def test_cap_holds_on_every_draw():
    qs = questions()
    bad = []
    for s in SEEDS:
        chosen = draw(qs, rng=random.Random(s))
        if worst_repeat(chosen) > MAX_SAME_ANSWER:
            bad.append((s, worst_repeat(chosen)))
    assert not bad, (
        f"{len(bad)} of {len(SEEDS)} draws exceeded the cap of "
        f"{MAX_SAME_ANSWER}: {bad[:5]}")


def test_a_full_session_is_still_possible():
    """The question the constraint had to answer before it could ship."""
    qs = questions()
    cap = answer_capacity(qs)
    assert cap >= N_PER_SESSION, (
        f"the bank supports only {cap} questions under a cap of "
        f"{MAX_SAME_ANSWER}; a session of {N_PER_SESSION} cannot be dealt")
    short = [s for s in SEEDS
             if len(draw(qs, rng=random.Random(s))) != N_PER_SESSION]
    assert not short, f"draws that came up short: {short[:5]}"


def test_capacity_is_exact():
    """An unbounded draw must return answer_capacity, not merely at most it.

    This is what lets render_quiz quote answer_capacity as the reason a
    short session is short. If capacity were only an upper bound, the UI
    would be naming a number the bank cannot reach.
    """
    qs = questions()
    cap = answer_capacity(qs)
    for s in SEEDS:
        chosen = draw(qs, n=len(qs), rng=random.Random(s))
        assert len(chosen) == cap, (
            f"seed {s}: unbounded draw returned {len(chosen)}, "
            f"answer_capacity says {cap}")
        assert worst_repeat(chosen) <= MAX_SAME_ANSWER


def test_short_draw_returns_what_the_bank_can_support():
    """A starved bank yields its capacity -- not ten, and not zero."""
    qs = questions()
    starved = starved_bank(qs)
    cap = answer_capacity(starved)
    assert cap == MAX_SAME_ANSWER + 1, (
        f"fixture is not starved as intended: capacity {cap}")
    assert cap < N_PER_SESSION
    for s in SEEDS:
        chosen = draw(starved, rng=random.Random(s))
        assert len(chosen) == cap, (
            f"seed {s}: starved draw returned {len(chosen)}, expected {cap}")
        assert worst_repeat(chosen) <= MAX_SAME_ANSWER


def test_draw_returns_real_questions_unmodified():
    """The draw selects; it must not rebuild rows or reorder options."""
    qs = questions()
    by_id = {q["id"]: q for q in qs}
    chosen = draw(qs, rng=random.Random(7))
    assert len({q["id"] for q in chosen}) == len(chosen), "duplicate ids drawn"
    for q in chosen:
        assert q is by_id[q["id"]], "draw returned a copy, not the bank row"


def canary():
    """random.sample over the same bank must be caught exceeding the cap.

    The code this change replaced, run against the same fixture. If this
    stops firing, test_cap_holds_on_every_draw is passing on a property that
    nothing can violate, and a draw() that quietly dropped the constraint
    would look clean.
    """
    qs = questions()
    over = 0
    for s in SEEDS:
        chosen = random.Random(s).sample(qs, N_PER_SESSION)
        if worst_repeat(chosen) > MAX_SAME_ANSWER:
            over += 1
    print(f"  canary unconstrained_sample  {over}/{len(SEEDS)} draws exceed "
          f"a cap of {MAX_SAME_ANSWER}  [{'DETECTED' if over else 'MISSED'}]")
    return over > 0


def test_canary_fires():
    assert canary(), (
        "unconstrained sampling did not exceed the cap on any seed -- this "
        "file is not testing anything")


def main():
    print("quiz draw canary")
    if not canary():
        print("SELF-CHECK FAILED -- this check is not testing anything")
        return 1

    qs = questions()
    counts = collections.Counter(answer_key(q) for q in qs)
    print(f"\n  bank {len(qs)} questions, {len(counts)} distinct answers")
    print(f"  capacity at a cap of {MAX_SAME_ANSWER}: "
          f"{answer_capacity(qs)}   session needs {N_PER_SESSION}")

    for fn in (test_cap_holds_on_every_draw,
               test_a_full_session_is_still_possible,
               test_capacity_is_exact,
               test_short_draw_returns_what_the_bank_can_support,
               test_draw_returns_real_questions_unmodified):
        try:
            fn()
        except AssertionError as e:
            print(f"  FAIL {fn.__name__}: {e}")
            return 1
        print(f"  PASS {fn.__name__}")
    print("\nPASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
