"""The registry is ON unless a caller turns it off, and only the synthetic set may.

WHAT THIS IS GUARDING. The synthetic set tests faithfulness to an invented
manual; the registry describes real products, so it refuses নীলকমল ঋণ as a
name no manual has, and S-001, S-004 and S-005 score as misses for a reason
that has nothing to do with the model. generate.py --no-registry answers
without it, for the synthetic run only. Three things must hold:

  1. Without the argument, a নীলকমল question still gets the refusal -- the
     default stayed on. Checked twice: through main(), as the CLI runs it,
     and through answer_one(), which other callers use directly.
  2. With it, the registry is off and every meta row says registry=off, so
     a synthetic result can never be read as a production one.
  3. On a dump that is not synthetic the argument is refused before
     anything is called.

No model is called and nothing touches the network: generate.complete is
replaced, and the dump is the fixture in generate.SYNTH_CHUNKS, written to a
temporary directory. gold/synthetic.yaml and out/ are not touched.

CONVENTION 3. The canaries are the defect itself: the argument's default
flipped to off, and answer_one's default flipped to off. Each must turn the
refusal into the model's answer, or test 1 is not testing anything.

Runs standalone (python tests/test_registry_synthetic.py) or under pytest.
"""
import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import generate  # noqa: E402
import registry  # noqa: E402

MODEL_SAYS = "(মডেলের উত্তর)"
QID = "S-001"                          # নীলকমল ঋণের বিমা প্রিমিয়াম কত?


@contextlib.contextmanager
def stubbed():
    """generate.complete replaced, cwd at the repo root (registry.load() and
    the canaries read relative paths). Yields the list of model calls."""
    calls = []

    def fake_complete(system, user, **_kw):
        calls.append(user)
        return MODEL_SAYS, {}

    real, cwd = generate.complete, os.getcwd()
    generate.complete = fake_complete
    os.chdir(ROOT)
    try:
        yield calls
    finally:
        generate.complete = real
        os.chdir(cwd)


def run_cli(*extra, dump=None):
    """-> (exit code, {id: answer}, [meta rows], stdout, model calls), from
    generate.main() on the synthetic dump, or on `dump` when given."""
    with tempfile.TemporaryDirectory() as tmp, stubbed() as calls:
        t = Path(tmp)
        gold = t / "gold.yaml"
        if dump is None:
            dump = t / "synthetic_retrieved.jsonl"
            generate.make_synthetic(str(dump), str(gold))
        answers, meta = t / "answers.jsonl", t / "meta.jsonl"
        argv = ["generate.py", "--retrieved", str(dump), "--gold", str(gold),
                "--answers", str(answers), "--meta", str(meta),
                "--forbidden-numbers", str(t / "none.json"),
                "--run-log-dir", str(t / "runs"), "--run-id", "test",
                *extra]
        out, real_argv = io.StringIO(), sys.argv
        sys.argv = argv
        try:
            with contextlib.redirect_stdout(out):
                code = generate.main()
        finally:
            sys.argv = real_argv
        recs = {}
        if answers.exists():
            for line in answers.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    recs[r["id"]] = r["answer"]
        metas = ([json.loads(x) for x in
                  meta.read_text(encoding="utf-8").splitlines() if x.strip()]
                 if meta.exists() else [])
        return code, recs, metas, out.getvalue(), list(calls)


def s001_row():
    """S-001 as the synthetic dump carries it, built without writing a file."""
    by_id = {c["chunk_id"]: c for c in generate.SYNTH_CHUNKS}
    qid, qbn, _ans, cids = next(q for q in generate.SYNTH_QUESTIONS
                                if q[0] == QID)
    chunks = [dict(by_id[cid], fused_score=0.72 - 0.11 * i)
              for i, cid in enumerate(cids)]
    return {"question_id": qid, "question_bn": qbn, "chunks": chunks}


def test_without_the_argument_nilkamal_still_refuses():
    code, recs, metas, _out, _calls = run_cli()
    assert code == 0
    assert recs[QID] == registry.NO_SUCH_NAME_BN, "reply was %r" % recs[QID]
    m = next(m for m in metas if m["id"] == QID)
    assert "6:unknown_product" in m.get("rules_fired", []), m
    assert metas and all(m["registry"] == "on" for m in metas), metas


def test_answer_one_default_still_refuses():
    with stubbed():
        rec, meta = generate.answer_one(s001_row(), floor=0.0)
    assert rec["answer"] == registry.NO_SUCH_NAME_BN, rec["answer"]
    assert "6:unknown_product" in meta.get("rules_fired", []), meta


def test_argument_turns_it_off_and_every_row_says_so():
    code, recs, metas, _out, calls = run_cli("--no-registry")
    assert code == 0
    assert recs[QID] == MODEL_SAYS, "reply was %r" % recs[QID]
    assert len(metas) == len(generate.SYNTH_QUESTIONS) == len(calls)
    assert all(m["registry"] == "off" for m in metas), metas
    assert not any(m.get("rules_fired") for m in metas), metas


def test_argument_refused_on_a_real_dump():
    """A dump with one real chunk is not synthetic. The run stops before the
    corpus guard, before configure(), before any model call. --provider
    local makes the corpus guard wave it through, so nothing but the
    --no-registry check can be what stops it."""
    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "real.jsonl"
        row = s001_row()
        row["chunks"][0] = dict(row["chunks"][0], synthetic=False)
        dump.write_text(json.dumps(row, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        code, recs, metas, out, calls = run_cli("--no-registry",
                                                "--provider", "local",
                                                dump=dump)
    assert code == 1
    assert "--no-registry is for the synthetic set only" in out, out
    assert not calls and not recs and not metas


# ------------------------------------------------------------------ canaries
def canary_argument_default_off():
    """The argument's default flipped: every parse comes back registry-off."""
    real = argparse.ArgumentParser.parse_args

    def flipped(self, *a, **kw):
        ns = real(self, *a, **kw)
        if hasattr(ns, "no_registry"):
            ns.no_registry = True
        return ns

    argparse.ArgumentParser.parse_args = flipped
    try:
        _code, recs, _metas, _out, _calls = run_cli()
    finally:
        argparse.ArgumentParser.parse_args = real
    hit = recs.get(QID) == MODEL_SAYS
    print("  canary argument_default_off   S-001 reply=%r  [%s]"
          % (recs.get(QID), "DETECTED" if hit else "MISSED"))
    return hit


def canary_answer_one_default_off():
    """answer_one's registry_rules default flipped to False."""
    real = generate.answer_one.__defaults__
    generate.answer_one.__defaults__ = real[:-1] + (False,)  # registry_rules
    try:
        with stubbed():
            rec, _meta = generate.answer_one(s001_row(), floor=0.0)
    finally:
        generate.answer_one.__defaults__ = real
    hit = rec["answer"] == MODEL_SAYS
    print("  canary answer_one_default_off reply=%r  [%s]"
          % (rec["answer"], "DETECTED" if hit else "MISSED"))
    return hit


def test_canaries_fire():
    assert canary_argument_default_off()
    assert canary_answer_one_default_off()


def main():
    print("registry-off synthetic canaries")
    if not (canary_argument_default_off() and canary_answer_one_default_off()):
        print("SELF-CHECK FAILED -- this check is not testing anything")
        return 1
    for fn in (test_without_the_argument_nilkamal_still_refuses,
               test_answer_one_default_still_refuses,
               test_argument_turns_it_off_and_every_row_says_so,
               test_argument_refused_on_a_real_dump):
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
