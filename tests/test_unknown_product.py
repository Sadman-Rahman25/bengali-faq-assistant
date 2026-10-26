"""The unknown-product refusal: names it must refuse, words it must not.

WHAT THIS IS GUARDING. registry.unknown_product_name() answers "এই নামে
কোনো প্রোডাক্ট ম্যানুয়ালে নেই" when a question names a product nobody has.
Until 2026-10-26 it took any rare word before ঋণ/লোন/সঞ্চয় for a name, and
refused ordinary questions: "… কাঙ্খিত ঋণের কত শতাংশ সঞ্চয় …" on কাঙ্খিত
(the manual's own phrase) and "… কত শতাংশ সঞ্চয় …" on শতাংশ. On the 199 real
questions it refused 7, five of them wrongly.

Now a describing word is a name only if the manuals never use it, and
"কত X" asks a quantity. Real unknown names still refuse: নীলকমল (in no
manual), গুড কাস্টমার (in the manual, but as a report: a reviewed row in
data/registry/not_products.csv), and goat insurance (the insurance
exception, with the livestock insurances listed).

CONVENTION 3. The canary switches the two new guards off and checks that the
false refusals come back -- otherwise these tests could pass on a rule that
never refused anything.

Runs standalone (python tests/test_unknown_product.py) or under pytest.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import registry  # noqa: E402

# the two false refusals found on 2026-10-26, as asked
FALSE_REFUSALS = [
    "ঋণ নিতে সদস্যকে কাঙ্খিত ঋণের কত শতাংশ সঞ্চয় জমা দিতে হয়?",
    "ঋণের আবেদনের সময় সদস্যকে ঋণের কত শতাংশ সঞ্চয় জমা দিতে হয়?",
]
# the five the real-question set refused wrongly, verbatim from the banks
REAL_FALSE_REFUSALS = [
    "ঋণ গ্রহণে ইচ্ছুক সদস্যদের থেকে কাঙ্খিত প্রথম ঋণের পরিমাণের কত শতাংশ "
    "সঞ্চয় জমা করতে হবে?",
    "পুরাতন লোন বিতরণের সময় প্রস্তাবিত লোনের কত শতাংশ সঞ্চয় স্থিতি থাকলে "
    "নতুন সঞ্চয় নেওয়া বাধ্যতামূলক নয়?",
    "দাবি পাইলট প্রোডাক্টের পুরাতন লোন বিতরণের সময় প্রস্তাবিত লোনের কত "
    "শতাংশ সঞ্চয় স্থিতি প্রয়োজন?",
    "দাবি পাইলট প্রোডাক্টের মধ্যে স্যানিটেশন উদ্যোক্তাদের জন্য নির্ধারিত "
    "সর্বনিম্ন লোন পরিমাণ কত টাকা?",
    "দ্বিতীয় অধ্যায়ের গ্রুপ ভিত্তিক সেবা (ঋণ বিতরণ ও আদায় প্রক্রিয়া) অনুযায়ী "
    "১৫,০০০‑৭০,০০০ টাকা নতুন ঋণ ও ১৫,০০০‑১২০,০০০ টাকা রিপিট ঋণ "
    "অনুমোদনকারী কে?",
]
MUST_REFUSE = [
    "গ্রাহক কি নীলকমল ঋণ নিতে পারবেন?",       # in no manual
    "নীলকমল বিমা কী?",
    "“গুড কাস্টমার লোন” সম্পর্কে বলুন।",       # listed: a report, not a product
    "ছাগলের বিমা করা যাবে কি? প্রিমিয়াম কত?",   # the insurance exception
]


def _reg():
    # the root is passed, not taken from the working directory: importing a
    # test must not chdir (Convention 1)
    return registry.load(str(ROOT))


def test_false_refusals_are_silent():
    reg = _reg()
    loud = {q: registry.unknown_product_name(q, reg) for q in FALSE_REFUSALS}
    assert not any(loud.values()), loud


def test_real_question_false_refusals_are_silent():
    reg = _reg()
    loud = {q: registry.unknown_product_name(q, reg)
            for q in REAL_FALSE_REFUSALS}
    assert not any(loud.values()), loud


def test_real_unknown_names_still_refused():
    reg = _reg()
    quiet = [q for q in MUST_REFUSE if not registry.unknown_product_name(q, reg)]
    assert not quiet, quiet


def test_goat_refusal_lists_the_livestock_insurances():
    reg = _reg()
    found = registry._unknown_phrase(MUST_REFUSE[3], reg)
    assert found, "goat insurance not refused"
    _qual, words, head = found
    assert registry._related_insurances(words, head, reg), \
        "goat refusal lists no insurances"


def test_the_listed_name_refuses_only_as_a_product():
    # the §9.2.2 report itself is a fair question; only "<name> লোন" refuses
    reg = _reg()
    assert not registry.unknown_product_name(
        "গুড কাস্টমার রিপোর্ট কোথায় পাওয়া যায়?", reg)


def canary():
    """With the two guards off -- rarity alone decides, and "কত X" is a name
    like any other -- the false refusals must come back."""
    saved = registry._name_like, registry._HOW_MUCH
    registry._name_like = registry._could_name
    registry._HOW_MUCH = ()
    try:
        reg = _reg()
        back = [q for q in FALSE_REFUSALS
                if registry.unknown_product_name(q, reg)]
    finally:
        registry._name_like, registry._HOW_MUCH = saved
    return len(back) == len(FALSE_REFUSALS)


def test_canary_fires():
    assert canary(), "the old rule does not reproduce the false refusals"


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
