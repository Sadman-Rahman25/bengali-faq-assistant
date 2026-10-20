"""quiz_build.pick_distractors must never offer one post twice.

WHAT THIS IS GUARDING. Distractors were de-duplicated by spelling, so a full
name and its abbreviation counted as two options. Q-006 had বিভাগীয়
ব্যবস্থাপক as its answer and ডিএম beside it -- the same post, two right
answers. Q-009 had শাখা ব্যবস্থাপক and বিএম as two wrong options. Both
shapes are reproduced here with a pool that leaves the old code no way to
avoid them.

Role names come from quiz_build.role_vocabulary(), which reads roles.py;
none is typed here. No model, no index, no network.

CONVENTION 3. The canary puts the old spelling key back and the Q-006 test
must fail, or this file is not testing anything.

Runs standalone (python tests/test_quiz_distractors.py) or under pytest.
"""
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import quiz_build as qb  # noqa: E402
from bnnorm import canon  # noqa: E402


def names():
    """{post key: [names]} from the role vocabulary."""
    out = {}
    for n, k in qb.role_vocabulary():
        out.setdefault(k, []).append(n)
    return out


def pick(correct, pool, seed=0):
    return qb.pick_distractors("role", correct, None, "", {}, set(pool),
                               random.Random(seed))


def posts(options):
    """Post keys read from the vocabulary here, not through option_key: the
    test must not trust the function it is testing."""
    key = {canon(n): k for n, k in qb.role_vocabulary()}
    return [key.get(canon(o), canon(o)) for o in options]


def test_answer_post_not_offered_again():
    """Q-006: the answer's full name, and its abbreviation in the pool. The
    pool holds exactly three names, so the old code had to take all three."""
    nm = names()
    dm_full, dm_abbr = nm["divisional_manager"][0], nm["divisional_manager"][-1]
    assert canon(dm_full) != canon(dm_abbr)
    pool = [dm_abbr, nm["credit_officer"][0], nm["programme_sohokari"][0]]
    for seed in range(5):
        got = pick(dm_full, pool, seed)
        assert dm_abbr not in got, "Q-006's pair offered: %r" % got


def test_one_distractor_post_offered_once():
    """Q-009: two names of one wrong post in a three-name pool."""
    nm = names()
    bm = nm["branch_manager"]
    pool = [bm[0], bm[-1], nm["credit_officer"][0]]
    for seed in range(5):
        got = pick(nm["area_manager"][0], pool, seed)
        assert len(set(posts(got))) == len(got), "one post twice: %r" % got


def test_field_officer_names_are_one_post():
    """Q-052: সিডিও and পিও are the same post under each programme's name
    (gold G-222 records it)."""
    fo = names()["field_officer"]
    assert len(fo) == 2 and len(set(posts(fo))) == 1, fo


def test_figures_keep_their_spelling_key():
    """Not a role name: the key is the canon'd text, so figures behave as
    before."""
    assert qb.option_key("৫%") == canon("৫%")


def canary():
    """The old key -- spelling -- must reproduce Q-006's pair."""
    real = qb.option_key
    qb.option_key = canon
    try:
        try:
            test_answer_post_not_offered_again()
            hit = False
        except AssertionError:
            hit = True
    finally:
        qb.option_key = real
    print("  canary spelling_key  Q-006 pair %s  [%s]"
          % ("reproduced" if hit else "NOT reproduced",
             "DETECTED" if hit else "MISSED"))
    return hit


def test_canary_fires():
    assert canary(), "the spelling key no longer reproduces Q-006's pair"


def main():
    print("pick_distractors canary")
    if not canary():
        print("SELF-CHECK FAILED -- this check is not testing anything")
        return 1
    for fn in (test_answer_post_not_offered_again,
               test_one_distractor_post_offered_once,
               test_field_officer_names_are_one_post,
               test_figures_keep_their_spelling_key):
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
