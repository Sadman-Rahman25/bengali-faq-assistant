"""Role vocabulary across the two manuals, with the সিডিও↔পিও equivalence.

THIS IS METADATA, NOT QUERY EXPANSION. Read that as a constraint on how it may
be used.

The manuals name the same field role differently and with perfect
complementary distribution:

    সিডিও   127 in Dabi,   0 in Progoti
    পিও       0 in Dabi, 142 in Progoti

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

P = Path("data/processed")
CORPORA = ("dabi_2025", "progoti")
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "roles_registry.csv")

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

nrm = lambda s: re.sub(r"\s+", " ", canon(unicodedata.normalize("NFC", s or "")))

text, chunks = {}, {}
for tag in CORPORA:
    cs = [json.loads(l) for l in
          (P / f"chunks_{tag}_final.jsonl").open(encoding="utf-8")]
    chunks[tag] = [nrm(c["display_text"]) for c in cs]
    text[tag] = "\n".join(chunks[tag])


def counts(term):
    if not term:
        return 0, 0
    return (text["dabi_2025"].count(term),
            text["progoti"].count(term))


rows = []
for key, nd, npg, abbrev, eng, note in ROLES:
    d_dabi, _ = counts(nd)
    _, p_prog = counts(npg)
    # cross counts expose whether a name really is manual-specific
    _, nd_in_prog = counts(nd)
    npg_in_dabi, _ = counts(npg)
    manual_specific = nd != npg and nd_in_prog == 0 and npg_in_dabi == 0
    rows.append(dict(
        role_key=key, name_dabi_2025=nd, name_progoti=npg, abbrev=abbrev,
        english=eng,
        equivalence="manual_specific_name" if manual_specific else
                    ("identical_name" if nd == npg else "differs"),
        n_dabi=d_dabi, n_progoti=p_prog,
        n_dabi_name_in_progoti=nd_in_prog, n_progoti_name_in_dabi=npg_in_dabi,
        expand_in_query="NO", note=note, status=""))

with OUT.open("w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)

print(f"{len(rows)} roles -> {OUT}\n")
print(f"  {'role_key':<20}{'dabi':<20}{'progoti':<20}"
      f"{'n_d':>6}{'n_p':>6}  equivalence")
for r in rows:
    print(f"  {r['role_key']:<20}{r['name_dabi_2025']:<20}{r['name_progoti']:<20}"
          f"{r['n_dabi']:>6}{r['n_progoti']:>6}  {r['equivalence']}")

ms = [r for r in rows if r["equivalence"] == "manual_specific_name"]
print(f"\n  manual-specific role names: {[r['role_key'] for r in ms]}")
for r in ms:
    print(f"    {r['role_key']}: {r['name_dabi_2025']} appears "
          f"{r['n_dabi_name_in_progoti']}x in Progoti; {r['name_progoti']} appears "
          f"{r['n_progoti_name_in_dabi']}x in Dabi  <- complementary, so the "
          f"term identifies the manual")
print("\n  expand_in_query is NO for every row, by design. See module docstring.")
