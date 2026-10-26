"""Reviewed answers first: an officer's question matched to an approved FAQ.

When a question asked on the Ask anything tab matches an approved FAQ
question, the page shows that reviewed answer -- text a person checked
against the manuals -- instead of generating one. Only when nothing matches
does the question go to retrieval and the model.

    python faqmatch.py --measure    # the threshold's evidence

TWO TESTS, AND BOTH MUST PASS.

  1. Similarity. The question is embedded once -- same embedder, task and
     normalisation the retriever uses, so the same vector then serves
     retrieval if nothing matches -- and compared with the 48 approved
     questions in both languages (96 texts, cached by content under
     out/faq_match/). The closest must score at least THRESHOLD.
  2. Coverage. Every content word of the question must appear in that FAQ's
     question or answer, allowing one case ending (registry._term_rx, the
     registry's own word matcher). Question words and grammar are ignored.

WHY SIMILARITY ALONE IS NOT ENOUGH, measured on data/faq/match_tests.csv:
an embedding measures topic, not whether the thing asked is answered.
"মাইগ্রেশন লোন কোন কোন দেশে যাওয়ার জন্য দেওয়া হয়?" scores 0.907 against
the Migration Loan answer, which lists no countries, while the real question
"প্রত্যাশা লোনের সার্ভিস চার্জ কত?" scores 0.815 against the answer that does
state the charge. No threshold separates those. Coverage does: 28 of the 29
must-not-match questions -- 20 near misses (same product, a fact its
reviewed answer lacks) and 9 off-topic -- have a word the closest answer
does not contain (দেশে, মেয়াদ, প্রশিক্ষণ, বয়সসীমা, ...).

THE THRESHOLD then guards what coverage cannot: a question whose words a
NEIGHBOURING answer happens to contain. On the test set those were two -- a
DPS question closest to the monthly-profit scheme's answer (0.843), and the
29th, "কল সেন্টারের নম্বর কত?", against the complaints answer, which names
the call centre but not its number (0.617). THRESHOLD is 0.005 above the
higher. At it: 74 of 123 should-match questions get their reviewed answer,
0 of 29 must-not-match questions do, and no question gets the wrong one.
The other 49 are answered as before, by retrieval and the model -- a miss
here costs nothing that existed yesterday. Of the 49: 19 score below the
threshold, 3 are closest to a different FAQ, and 27 have a word the reviewed
answer puts differently (ঋণসীমা for a limit it states as a range, ডকুমেন্ট
for কাগজপত্র) -- coverage takes no synonyms, so it cannot be argued into a
wrong match.

Banglish rarely matches (0 of 8): its words are Latin script and the
answers are Bengali, so coverage fails and it goes to the model -- which is
the side of the line to fail on.

ONLY APPROVED TEXT IS EVER SHOWN: questions come from faq.shown(), answers
through faq.view(), which falls back to the approved Bengali when the
English is not approved.
"""
import argparse
import csv
import hashlib
import io
import re
import sys
from pathlib import Path

import faq
from bn_shared import canon

CACHE = Path("out/faq_match")
TESTS = Path("data/faq/match_tests.csv")
MODEL = "gemini-embedding-001"
# Measured 2026-10-26 by `python faqmatch.py --measure`; see the docstring.
THRESHOLD = 0.848

# Words that ask, or carry grammar, and so say nothing about WHAT is asked.
# Compared after canon(), so spelling variants of য়/ড় cannot slip past.
_STOP_BN = """কী কি কে কারা কাকে কার কাদের কত কতো কোন কোনো কোথায় কখন কবে কেন
কীভাবে কিভাবে কেমন এবং ও বা আর অথবা এই সেই যে যা যদি তা হয় হলে হবে করতে করে
করা করব করবো করবে করেন করবেন দিতে দেওয়া দেয়া দেয় দেন নিতে নেওয়া নেয় নেন পাওয়া
পায় পান পাবে পাবেন যায় যাবে যাবেন লাগে থাকলে থাকে আছে নেই জন্য সম্পর্কে বলুন
জানতে চাই এখন এখনও সাথে সঙ্গে চাইলে হচ্ছে হয়ে উপায় নিয়ম প্রক্রিয়া যাওয়া পারে
পারি পারব পারবে পারবেন পারেন আসে আসেন হওয়া হতে করার রাখা রাখতে রাখে যাওয়ার
যেতে চালু স্থগিত বিষয়ে"""
_STOP_EN = """a an the is are was were what who whom whose how much many do does did
i can could get gets of for to in on and or it its this that when which be there
with my me we you your take make made should would will must about tell happen
happens out their them now"""
STOP = {canon(w) for w in _STOP_BN.split()} | set(_STOP_EN.split())
_WORD = re.compile(r"[ঀ-৿]+|[A-Za-z0-9+]+")
_BN = re.compile(r"[ঀ-৿]")


def _sha(t):
    return hashlib.sha256(t.encode("utf-8")).hexdigest()[:16]


def query_text(q):
    """The text the retriever embeds for a question -- the same here, so one
    vector serves both."""
    import embed
    return embed.query_text(embed.expand_banglish(q or ""))


def encode(model, texts, name, model_name=MODEL):
    """Unit vectors for `texts`, cached by content under out/faq_match/.
    -> np.ndarray (len(texts), dim)"""
    import numpy as np
    import embed
    path = CACHE / f"{name}.{model_name}.npz"
    have = {}
    if path.exists():
        data = np.load(path)
        have = dict(zip(data["keys"].tolist(), data["vecs"]))
    qts = [query_text(t) for t in texts]
    keys = [_sha(t) for t in qts]
    todo, seen = [], set()          # each new text once, in order
    for i, k in enumerate(keys):
        if k not in have and k not in seen:
            todo.append(i)
            seen.add(k)
    for s in range(0, len(todo), 50):  # the API takes at most 100 a request
        part = todo[s:s + 50]
        V = embed._encode(model, [qts[i] for i in part], "query")
        for i, v in zip(part, V):
            have[keys[i]] = v
    if todo:
        CACHE.mkdir(parents=True, exist_ok=True)
        np.savez(path, keys=np.array(list(have)),
                 vecs=np.stack(list(have.values())))
    return np.stack([have[k] for k in keys])


def questions(rows=None):
    """[(faq id, language, question)] for every answer approved in Bengali:
    its Bengali question, and its English one when the English is approved
    too."""
    out = []
    for r in faq.shown(rows if rows is not None else faq.load()):
        out.append((r["id"], "bn", r["question_bn"]))
        if faq.approved(r, "en") and r["question_en"].strip():
            out.append((r["id"], "en", r["question_en"]))
    return out


def content_words(question):
    """The words of a question that say what is asked."""
    return [w for w in _WORD.findall(canon(question or ""))
            if len(w) >= 2 and w.lower() not in STOP]


def _covered(word, text):
    import registry
    if _BN.search(word):
        return any(registry._term_rx(s).search(text)
                   for s in registry._stems(word))
    stem = re.escape(word.lower().rstrip("s"))
    return re.search(r"(?<![a-z])" + stem, text.lower()) is not None


def uncovered(question, row):
    """Content words of `question` that the FAQ row's question and answer,
    in the question's script, do not contain. -> [word]"""
    bn = bool(_BN.search(question or ""))
    text = canon((row["question_bn"] + "\n" + row["answer_bn"]) if bn
                 else (row["question_en"] + "\n" + row["answer_en"]))
    return [w for w in content_words(question) if not _covered(w, text)]


def best(qv, model, rows=None):
    """-> (faq id, score, language of the closest question) or None."""
    qs = questions(rows)
    if not qs:
        return None
    M = encode(model, [t for _i, _l, t in qs], "faq_questions")
    sims = M @ qv
    j = int(sims.argmax())
    return qs[j][0], float(sims[j]), qs[j][1]


def match(question, qv, model, rows=None, threshold=THRESHOLD):
    """The approved FAQ answer this question asks for, or None -- with the
    reason either way, for the log. -> (hit or None, why)"""
    rows = rows if rows is not None else faq.load()
    b = best(qv, model, rows)
    if b is None:
        return None, {"faq_rejected": "no approved FAQ"}
    fid, score, qlang = b
    why = {"faq_candidate": fid, "faq_score": round(score, 4)}
    if score < threshold:
        return None, dict(why, faq_rejected="below threshold")
    row = next(r for r in rows if r["id"] == fid)
    missing = uncovered(question, row)
    if missing:
        return None, dict(why, faq_rejected="not covered",
                          faq_uncovered=missing)
    return {"id": fid, "score": round(score, 4), "lang": qlang}, why


def answer_lang(question, matched_lang):
    """The officer's language: Bengali script asks in Bengali; a question in
    Latin script gets English, unless it matched a Bengali question."""
    if _BN.search(question or ""):
        return "bn"
    return "bn" if matched_lang == "bn" else "en"


# ------------------------------------------------------------ measurement
def measure(model):
    """Score every test question by both tests; derive the threshold.
    -> report dict"""
    tests = list(csv.DictReader(io.open(TESTS, encoding="utf-8")))
    rows = faq.load()
    by = {r["id"]: r for r in rows}
    qs = questions(rows)
    M = encode(model, [t for _i, _l, t in qs], "faq_questions")
    T = encode(model, [r["query"] for r in tests], "match_tests")
    out = []
    for r, tv in zip(tests, T):
        sims = M @ tv
        j = int(sims.argmax())
        top = qs[j][0]
        out.append(dict(r, top=top, score=float(sims[j]),
                        unc=uncovered(r["query"], by[top])))
    pos = [r for r in out if r["expect"] != "none"]
    neg = [r for r in out if r["expect"] == "none"]
    # what coverage lets through that must not be shown
    leak = [r for r in out if not r["unc"]
            and (r["expect"] == "none" or r["top"] != r["expect"])]
    threshold = round(max([r["score"] for r in leak] + [0]) + 0.005, 3)
    kept = [r for r in pos if r["top"] == r["expect"] and not r["unc"]
            and r["score"] >= threshold]
    shown_wrong = [r for r in out if not r["unc"] and r["score"] >= threshold
                   and (r["expect"] == "none" or r["top"] != r["expect"])]
    return {"rows": out, "pos": pos, "neg": neg, "leak": leak,
            "threshold": threshold, "kept": kept, "shown_wrong": shown_wrong}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--measure", action="store_true")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    if not args.measure:
        ap.print_help()
        return 0
    import embed
    model = embed.load_model(MODEL)
    if hasattr(model, "max_attempts"):
        # a batch job, like the index build: wait out the per-minute cap
        model.max_attempts = model.ATTEMPTS_BATCH
    rep = measure(model)
    pos, neg, kept = rep["pos"], rep["neg"], rep["kept"]
    print(f"test questions: {len(pos)} should match, {len(neg)} must not")
    print("passing coverage although wrong:")
    for r in sorted(rep["leak"], key=lambda r: -r["score"]):
        print(f"  {r['score']:.4f} -> {r['top']} (want {r['expect']})  "
              f"{r['query']}")
    print(f"threshold: {rep['threshold']} (in code: {THRESHOLD})")
    print(f"at it: {len(kept)} of {len(pos)} should-match questions get "
          f"their reviewed answer; {len(rep['shown_wrong'])} get a wrong one")
    by_kind = {}
    for r in pos:
        k = by_kind.setdefault(r["kind"], [0, 0])
        k[1] += 1
        k[0] += r in kept
    print("by kind:", {k: f"{a}/{b}" for k, (a, b) in by_kind.items()})
    return 0 if not rep["shown_wrong"] else 1


if __name__ == "__main__":
    sys.exit(main())
