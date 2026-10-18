"""Role vocabulary across the two manuals, with the সিডিও↔পিও equivalence.

THIS IS METADATA, NOT QUERY EXPANSION. Read that as a constraint on how it may
be used.

The manuals name the same field role differently and with perfect
complementary distribution:

    সিডিও   127 in Dabi,   0 in Progoti
    পিও       0 in Dabi, 142 in Progoti

COUNT CHANGE, 14 Sep 2026 -- DELIBERATE. Both figures are kept.

Counting now masks per-literal collisions before the substring count, via
role_exclusions.EXCLUSIONS. One row moved:

    programme_assistant (পিএ)    was  95 Dabi / 76 Progoti / 10 Livestock Grow
                                 now  71 Dabi / 71 Progoti /  0 Livestock Grow

পিএ was matching inside পিএলসি ("public limited company"). The collision was
live in the EXISTING corpus, not only in the new manual -- Dabi names a
পিএলসি 24 times and Progoti 5 -- so the old 95 and 76 were both wrong and
had been since the registry was first built. Livestock Grow is simply where
it became impossible to miss: it names two insurance পিএলসি throughout and
never mentions a Programme Assistant at all, so the count read 10 against a
true zero.

Every other row is unchanged, সিডিও 127 / পিও 142 included. That is the point
of masking a specific string rather than adding a letter-boundary guard: a
guard would also have dropped সিডিওকে and সিডিওর and moved figures that were
correct. See role_exclusions.py.

The term tracks the MANUAL, not the product: Progoti writes "পিও (দাবি)" for
the Dabi-scoped case, and Dabi writes "সিডিও (দাবি)" for the same thing. So a
question phrased with সিডিও is already, implicitly, a question about the Dabi
manual, and one phrased with পিও is about Progoti.

Expanding সিডিও -> পিও at query time would destroy exactly that signal: the
two manuals' copies of a rule would become interchangeable, and an answer
could quote the Progoti wording to an officer who works under the Dabi manual.
That matters because the manuals do NOT always agree about the role -- the
approval chain differs:

    Dabi     এএম(দাবি)
    Progoti  এএম(দাবি)/আরএম(প্রগতি)     <- an extra approver

The intended use is the opposite of erasure: retrieval matches the term the
user actually typed, and the ANSWER cites this table to say "the Dabi manual
calls this role সিডিও; the Progoti manual calls it পিও."

Usage:  python roles.py [out.csv]
"""
import csv
import re
import sys
import json
import unicodedata
from pathlib import Path

from bnnorm import canon
from role_exclusions import mask_exclusions

P = Path("data/processed")

# Every corpus the registry counts against. The two that DECLARE names in
# ROLES below come first; anything after them is counted but declares no
# name of its own yet, which is the point -- see report_new_corpora().
DECLARING = ("dabi_2025", "progoti")
CORPORA = DECLARING + ("livestock_grow_2026",)
DEFAULT_OUT = "roles_registry.csv"

# NB: OUT is resolved inside main(), not here. Reading sys.argv at module
# level meant that importing this module for its ROLES table -- probe_words
# does exactly that, to pull role literals -- rebound OUT to the IMPORTING
# script's first argument. Nothing wrote on import, so it never showed up;
# it was one roles.main() call away from writing the registry to a path
# taken from an unrelated command line.

# role_key, name in Dabi, name in Progoti, abbrev, english, note
ROLES = [
    ("field_officer", "সিডিও", "পিও", "সিডিও / পিও",
     "Credit Development Officer (Dabi) / Programme Organiser (Progoti)",
     "SAME ROLE, DIFFERENT NAME PER MANUAL. Do not expand one to the other; "
     "the term identifies which manual the asker works under."),
    ("credit_officer", "সিও", "সিও", "সিও", "Credit Officer",
     "appears in both, often as the pair সিডিও/সিও or পিও/সিও"),
    ("branch_manager", "শাখা ব্যবস্থাপক", "শাখা ব্যবস্থাপক", "বিএম", "Branch Manager",
     ""),
    ("area_manager", "এলাকা ব্যবস্থাপক", "এলাকা ব্যবস্থাপক", "এএম", "Area Manager",
     "APPROVAL CHAIN DIFFERS: Dabi writes এএম(দাবি) where Progoti writes "
     "এএম(দাবি)/আরএম(প্রগতি) -- Progoti adds the RM as an approver."),
    ("regional_manager", "আঞ্চলিক ব্যবস্থাপক", "আঞ্চলিক ব্যবস্থাপক", "আরএম",
     "Regional Manager", "see area_manager: RM appears in the Progoti "
     "approval chain where Dabi names only the AM"),
    ("divisional_manager", "বিভাগীয় ব্যবস্থাপক", "বিভাগীয় ব্যবস্থাপক", "ডিএম",
     "Divisional Manager", ""),
    ("programme_head", "কর্মসূচি প্রধান", "কর্মসূচি প্রধান", "", "Programme Head", ""),
    ("branch_accountant", "হিসাব কর্মকর্তা", "হিসাব কর্মকর্তা", "", "Branch Accountant",
     ""),
    ("programme_assistant", "পিএ", "পিএ", "পিএ", "Programme Assistant", ""),
    ("programme_sohokari", "পিএস", "পিএস", "পিএস", "Programme Sohokari", ""),
    ("adc", "এডিসি", "এডিসি", "এডিসি", "Assistant District Coordinator", ""),
]

# canon() includes NFC. Role names must go through it too: বিভাগীয় contains
# য়, which NFC decomposes, so a precomposed literal would silently count 0.
nrm = lambda s: re.sub(r"\s+", " ", canon(s or ""))


def load_text():
    """{tag: joined display_text}. A corpus not yet ingested is skipped LOUDLY.

    A silent skip would make the new manual look like a manual in which no
    role name occurs -- which is also what a corrupted text layer looks like.
    """
    text = {}
    for tag in CORPORA:
        f = P / f"chunks_{tag}_final.jsonl"
        if not f.exists():
            print(f"  [warn] {f} not present; {tag} not counted. "
                  f"Re-run after it is ingested.", file=sys.stderr)
            continue
        cs = [json.loads(l) for l in f.open(encoding="utf-8")]
        text[tag] = "\n".join(nrm(c["display_text"]) for c in cs)
    return text


def build_rows(text):
    """One row per role, with a per-corpus count for every corpus present."""
    tags = [t for t in CORPORA if t in text]

    def counts(term):
        """Per-corpus count of one role literal, with its collisions masked.

        mask_exclusions blanks out the specific longer strings a literal must
        not count inside -- পিএ inside পিএলসি -- before the substring count
        runs. Per-literal and evidenced, NOT a letter-boundary guard: Bengali
        role terms inflect, and a boundary rule would drop সিডিওকে and
        সিডিওর. See role_exclusions.py.
        """
        if not term:
            return {t: 0 for t in tags}
        q = nrm(term)
        return {t: mask_exclusions(text[t], term).count(q) for t in tags}

    rows = []
    for key, nd, npg, abbrev, eng, note in ROLES:
        cd, cp = counts(nd), counts(npg)
        # Cross counts expose whether a name really is manual-specific.
        nd_in_prog = cd.get("progoti", 0)
        npg_in_dabi = cp.get("dabi_2025", 0)
        manual_specific = nd != npg and nd_in_prog == 0 and npg_in_dabi == 0

        row = dict(
            role_key=key, name_dabi_2025=nd, name_progoti=npg, abbrev=abbrev,
            english=eng,
            equivalence="manual_specific_name" if manual_specific else
                        ("identical_name" if nd == npg else "differs"),
            n_dabi=cd.get("dabi_2025", 0), n_progoti=cp.get("progoti", 0),
            n_dabi_name_in_progoti=nd_in_prog,
            n_progoti_name_in_dabi=npg_in_dabi)
        # Per-corpus totals for any corpus that declares no name of its own:
        # the highest count among the declared names it does use.
        for t in tags:
            if t not in DECLARING:
                row[f"n_{t}"] = max(cd.get(t, 0), cp.get(t, 0))
        row.update(expand_in_query="NO", note=note, status="")
        rows.append(row)
    return rows


def report_new_corpora(rows, text):
    """For each corpus that declares no name in ROLES, say what it uses.

    A role whose every declared name reads 0 in the new manual is the signal
    that the manual uses a THIRD title for it. That title then joins the
    registry under the same expand_in_query = NO rule -- it identifies which
    manual the asker works under, which is the whole reason the equivalence
    is metadata and never query expansion.
    """
    extra = [t for t in CORPORA if t in text and t not in DECLARING]
    for t in extra:
        print(f"\n  {t}: counts for the names declared in ROLES")
        unseen = []
        for r in rows:
            n = r.get(f"n_{t}", 0)
            print(f"    {r['role_key']:<22}{n:>6}")
            if n == 0:
                unseen.append(r["role_key"])
        if unseen:
            print(f"\n    !! no declared name occurs in {t} for: "
                  f"{', '.join(unseen)}")
            print(f"    Either {t} uses a different title for these roles, or "
                  f"its text\n    layer is damaged. Read the rendered pages "
                  f"before adding a name:\n    inventing one is how a role "
                  f"vocabulary acquires a title nobody uses.")
        print("\n    Counts are substring counts with per-literal collisions "
              "masked first\n    (role_exclusions.EXCLUSIONS). পিএ is not "
              "counted inside পিএলসি, which is\n    what had "
              "programme_assistant reading 10 here. Any NEW collision needs "
              "its\n    own evidenced entry there -- deliberately not a "
              "letter-boundary guard,\n    which would drop inflected forms "
              "like সিডিওকে and সিডিওর.")


def main():
    OUT = Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT)
    text = load_text()
    rows = build_rows(text)

    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print(f"{len(rows)} roles -> {OUT}\n")
    print(f"  {'role_key':<20}{'dabi':<20}{'progoti':<20}"
          f"{'n_d':>6}{'n_p':>6}  equivalence")
    for r in rows:
        print(f"  {r['role_key']:<20}{r['name_dabi_2025']:<20}"
              f"{r['name_progoti']:<20}"
              f"{r['n_dabi']:>6}{r['n_progoti']:>6}  {r['equivalence']}")

    ms = [r for r in rows if r["equivalence"] == "manual_specific_name"]
    print(f"\n  manual-specific role names: {[r['role_key'] for r in ms]}")
    for r in ms:
        print(f"    {r['role_key']}: {r['name_dabi_2025']} appears "
              f"{r['n_dabi_name_in_progoti']}x in Progoti; {r['name_progoti']} "
              f"appears {r['n_progoti_name_in_dabi']}x in Dabi  <- complementary, "
              f"so the term identifies the manual")
    report_new_corpora(rows, text)

    print("\n  expand_in_query is NO for every row, by design. "
          "See module docstring.")


if __name__ == "__main__":
    main()
