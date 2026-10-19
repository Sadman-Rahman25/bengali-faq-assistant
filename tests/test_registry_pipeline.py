"""generate.answer_one and the registry must read retrieved chunks the same way.

WHAT THIS IS GUARDING. answer_one runs a registry pre-check before calling the
model: if a not_available row applies, it skips the model and hands
apply_rules an empty answer. The pre-check once passed clauses but not
manuals, so a Progoti-only row (not_available item 10, who keeps Progoti's
loan papers) fired on a DABI chunk of §3.11.2. The model was skipped;
apply_rules, which did pass the manuals, found nothing to say; the officer got
an empty reply. The scenarios in registry.py test registry functions, and none
of them could see generate.py, which is why this lives here.

No model is called and nothing touches the network: generate.complete is
replaced, and the test asserts whether it was reached.

CONVENTION 3. The canary is the defect itself: answer_one run with the old
pre-check -- clauses only -- must come back empty on the Dabi chunk, or this
file is not testing anything.

Runs standalone (python tests/test_registry_pipeline.py) or under pytest.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import generate  # noqa: E402
import registry  # noqa: E402

QUESTION = "ঋণের ফাইল ও রেজিস্টার কে সংরক্ষণ করবেন?"
MODEL_SAYS = "(মডেলের উত্তর)"


def chunk_at(manual_id, clause):
    """A real chunk of this manual and clause, shaped as retrieval returns it."""
    man = json.loads((ROOT / "data/processed/chunk_manifest.json")
                     .read_text(encoding="utf-8"))
    ids = {r["chunk_id"]: r for r in man["chunks"]
           if r["manual_id"] == manual_id and r.get("clause") == clause}
    p = ROOT / "data/processed" / ("chunks_%s_final.jsonl" % manual_id)
    for line in p.read_text(encoding="utf-8").splitlines():
        c = json.loads(line)
        if c["chunk_id"] in ids:
            return dict(c, manual_id=manual_id, clause=clause, fused_score=1.0)
    raise AssertionError("no %s chunk at §%s" % (manual_id, clause))


def run(manual_id, clause):
    """-> (answer, model_was_called), with the model stubbed out."""
    calls = []

    def fake_complete(system, user, **_kw):
        calls.append(user)
        return MODEL_SAYS, {}

    real, cwd = generate.complete, os.getcwd()
    generate.complete = fake_complete
    os.chdir(ROOT)                       # registry.load() reads ./data/registry
    try:
        row = {"question_id": "T", "question_bn": QUESTION,
               "chunks": [chunk_at(manual_id, clause)]}
        rec, _meta = generate.answer_one(row, floor=0.0)
    finally:
        generate.complete = real
        os.chdir(cwd)
    return rec["answer"], bool(calls)


def test_dabi_chunk_reaches_the_model():
    """Row 10 is Progoti-only: on a Dabi chunk the model must answer."""
    answer, called = run("dabi_2025", "3.11.2")
    assert called, "the model was skipped on a Dabi chunk"
    assert answer == MODEL_SAYS, "reply was %r" % answer


def test_progoti_chunk_is_withheld_without_the_model():
    """On a Progoti chunk the row applies, the model is not called, and the
    reply is the row's sentence -- not empty."""
    answer, called = run("progoti", "3.11.2")
    row10 = next(r for r in registry.load(str(ROOT)).not_available
                 if r["item"] == "10")
    assert not called
    assert answer == row10["bot_says_bn"], "reply was %r" % answer


def canary():
    """The old pre-check, clauses only, must reproduce the empty reply.

    THE DEFECT WAS A DISAGREEMENT, so only the pre-check is reverted: the
    first withheld_for() call -- generate.py's -- reads clauses alone, and
    apply_rules' own call reads clauses and manuals, as it always did.
    Reverting both together would make them agree again and hide the bug."""
    real = registry.Registry.withheld_for
    calls = []

    def clauses_only(self, question, chunks=()):
        calls.append(1)
        if len(calls) > 1:
            return real(self, question, chunks)
        return self.withheld(question, [c.get("clause") for c in chunks
                                        if c.get("clause")])

    registry.Registry.withheld_for = clauses_only
    try:
        answer, called = run("dabi_2025", "3.11.2")
    finally:
        registry.Registry.withheld_for = real
    hit = answer == "" and not called
    print("  canary clauses_only_precheck  reply=%r model_called=%s  [%s]"
          % (answer, called, "DETECTED" if hit else "MISSED"))
    return hit


def test_canary_fires():
    assert canary(), "the old pre-check no longer produces the empty reply"


def main():
    print("registry pipeline canary")
    if not canary():
        print("SELF-CHECK FAILED -- this check is not testing anything")
        return 1
    for fn in (test_dabi_chunk_reaches_the_model,
               test_progoti_chunk_is_withheld_without_the_model):
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
