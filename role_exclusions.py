"""
role_exclusions.py -- per-literal exclusion contexts for role matching.

পিএ matching inside পিএলসি is the fifth instance of a shape already handled
four times in §6.3:

    দাবি      inside  বিমাদাবি          insurance claim
    গতি       inside  অগ্রগতি / গতিশীল   progress, dynamic
    স্বাধীন    inside  স্বাধীনতা          independence
    সিডিপি    inside  এনসিডিপি / এসসিডিপি different programmes
    পিএ       inside  পিএলসি            public limited company   <- this one

Handled the same way: a per-literal exclusion, owned by the registry module.
NOT a general letter-boundary guard.

The distinction matters and was the right call to hold. A boundary rule
changes behaviour for every literal at once, and Bengali role terms inflect --
সিডিওকে, সিডিওর, পিএদের are all real occurrences that a boundary guard would
drop. It would also silently move the n_dabi / n_progoti figures recorded in
roles.py's docstring, which is a worse outcome than a known collision: a
printed caveat is visible, a changed number is not.

An exclusion entry is narrow. It masks the specific longer string before
matching the shorter one, and touches nothing else.

Integration in roles.py:

    from role_exclusions import mask_exclusions

    def match(text):
        hits = set()
        for canonical, literals in ROLE_LITERALS.items():
            for lit in literals:
                if compile_pat(lit).search(mask_exclusions(text, lit)):
                    hits.add(canonical)
        return hits

Then re-run against Dabi and Progoti and record the new n_dabi / n_progoti in
the docstring as a deliberate change, keeping the old figures beside them.
Do not overwrite them.
"""

import re
import sys

from bn_shared import canon, canon_pattern

# literal -> strings in which that literal must NOT count as a match.
# Keep entries narrow and evidenced. Each one should name where it was seen.
EXCLUSIONS = {
    # Livestock Grow 2026 names two insurance পিএলসি; পিএ read 10 there.
    "পিএ": ["পিএলসি"],
    # Reserved for role terms only. Product collisions live in productmatch.
}

_MASK = "�"          # not in any Bengali text; same length per char


def mask_exclusions(text, literal):
    """
    Blank out every occurrence of this literal's exclusion contexts, then
    return the text for matching. Length is preserved so any offsets a caller
    holds stay valid.
    """
    lit = canon(literal)
    ctxs = EXCLUSIONS.get(lit)
    if not ctxs:
        return text
    out = text
    for ctx in ctxs:
        out = canon_pattern(ctx).sub(lambda m: _MASK * len(m.group(0)), out)
    return out


def count_with_exclusions(text, literal):
    return len(canon_pattern(literal).findall(mask_exclusions(text, literal)))


def count_naive(text, literal):
    """Deliberately wrong. Exists so the canary has something to fail."""
    return len(canon_pattern(literal).findall(text))


# --------------------------------------------------------------- Convention 3
# Both canaries are real historical failures, recorded 13 Sep 2026.
#
#   1. roles.py, pre-existing: পিএ inside পিএলসি. Livestock Grow 2026 names
#      two insurance পিএলসি, so programme_assistant read 10.
#   2. registry_vocabulary(), introduced by me and caught in review: it pulled
#      display names out of productmatch and matched them itself, so bare দাবি
#      matched inside বিমাদাবি and reported 51 hits in a manual that names the
#      Dabi programme twice. CONVENTIONS.md §2 defect #1, reintroduced by the
#      module whose docstring forbids it.

CANARY_PLC = (
    "গ্রীন ডেল্টা ইন্স্যুরেন্স পিএলসি এবং সেনা ইন্স্যুরেন্স পিএলসি "
    "এই বিমার আন্ডাররাইটার। শাখায় একজন পিএ দায়িত্বে থাকবেন।"
)
CANARY_CLAIM = (
    "বিমাদাবি দাখিলের পদ্ধতি। বিমাদাবি ফরম পূরণ করতে হবে। "
    "বিমাদাবি নিষ্পত্তির সময়সীমা। দাবি কর্মসূচির গ্রাহকদের জন্য প্রযোজ্য।"
)


def canary_selftest(verbose=True):
    ok = True

    naive = count_naive(CANARY_PLC, "পিএ")
    real = count_with_exclusions(CANARY_PLC, "পিএ")
    hit = (naive == 3 and real == 1)
    if verbose:
        print(f"  canary pa_inside_plc         naive={naive} "
              f"excluded={real}  (expect 3 -> 1)  "
              f"[{'DETECTED' if hit else 'MISSED'}]")
    ok &= hit

    # Mirror of the registry_vocabulary defect. দাবি's exclusion is owned by
    # productmatch, not here, so this asserts the SHAPE is detectable -- a
    # plain matcher over-counts a programme name inside a compound.
    naive_d = count_naive(CANARY_CLAIM, "দাবি")
    masked = canon_pattern("বিমাদাবি").sub(
        lambda m: _MASK * len(m.group(0)), CANARY_CLAIM)
    real_d = len(canon_pattern("দাবি").findall(masked))
    hit_d = (naive_d == 4 and real_d == 1)
    if verbose:
        print(f"  canary dabi_inside_bimadabi  naive={naive_d} "
              f"excluded={real_d}  (expect 4 -> 1)  "
              f"[{'DETECTED' if hit_d else 'MISSED'}]")
        if not hit_d:
            print("        -> productmatch owns দাবি. If this drifts, a "
                  "caller is matching it directly again.")
    ok &= hit_d

    # Inflected forms must survive. This is what a boundary guard would break,
    # and the reason the guard was correctly rejected.
    infl = "সিডিওকে জানাতে হবে এবং সিডিওর অনুমোদন লাগবে।"
    n_infl = count_with_exclusions(infl, "সিডিও")
    hit_i = n_infl == 2
    if verbose:
        print(f"  canary inflected_forms       সিডিও={n_infl}  (expect 2)  "
              f"[{'PASS' if hit_i else 'FAIL'}]")
    ok &= hit_i
    return ok


def main():
    print("CANARY")
    ok = canary_selftest()
    print(f"\n{'PASS' if ok else 'FAIL'}")
    if ok:
        print("\nExclusions registered:")
        for lit, ctxs in EXCLUSIONS.items():
            print(f"  {lit:<12} not counted inside: {', '.join(ctxs)}")
        print("\nAfter wiring this into roles.py, re-run against both manuals")
        print("and record the new n_dabi / n_progoti beside the old figures.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
