"""The FAQ: one file of reviewed answers, and the rule for what may show.

data/faq/answers.csv holds every question in Bengali and English. A row is
SHOWN only when a person has approved it AS IT NOW STANDS -- the same rule
the quiz follows (quiz_recheck.review_status), restated here rather than
imported so the two surfaces do not share a moving part.

  status = approved      a person approved the BENGALI text
  fingerprint            sha256 of what that person saw: question_bn,
                         answer_bn, cites and attachment. Any edit after the
                         approval changes it, and the row stops showing
                         until someone approves the new text. An edit voids
                         a review; it never inherits one.

  en_status = approved   a person checked that the ENGLISH says the same as
                         the Bengali
  en_fingerprint         sha256 of the Bengali AND the English. A
                         translation is approved as a translation OF
                         something: if the Bengali changes, the English
                         approval lapses with it.

In English mode a row shows its English only when both approvals are
current; otherwise it shows the approved Bengali. An unreviewed
translation is never shown -- a wrong figure in fluent English is more
convincing than the same figure in a language the reader may check.

Approve from the command line, which records who, when and the
fingerprint, exactly as quiz_recheck --mark-reviewed does:

    python faq.py approve F-33 --by "Pilot review"
    python faq.py approve F-01 --by "Pilot review" --en
"""
import argparse
import csv
import hashlib
import io
import sys
from datetime import date
from pathlib import Path

PATH = Path("data/faq/answers.csv")

COLUMNS = ["id", "category", "question_bn", "answer_bn", "cites", "status",
           "reviewed_by", "review_date", "note", "fingerprint", "attachment",
           "question_en", "answer_en", "en_status", "en_reviewed_by",
           "en_review_date", "en_fingerprint"]

CATEGORIES = ("loan", "savings", "insurance", "procedure", "policy")

# THE MANUALS' ENGLISH TITLES. The Bengali titles are read from the chunk
# manifest, which is where dedupe persists them; the manifest carries no
# English, so the English lives here until it does. Two places for one
# title is the thing to remove first if a third language arrives.
MANUAL_EN = {"dabi_2025": "Dabi Operations Manual 2025",
             "progoti": "Progoti Operations Manual 2025",
             "livestock_grow_2026": "Livestock Grow 2026"}

BN_DIGITS = str.maketrans("0123456789", "০১২৩৪"
                          "৫৬৭৮৯")

# What each reviewer actually sees. Status, notes and the reviewer's own
# name are not content, so editing them never voids an approval.
SEEN_BN = ("question_bn", "answer_bn", "cites", "attachment")
SEEN_EN = SEEN_BN + ("question_en", "answer_en")


def load(path=PATH):
    """-> [row], every column present (blank where the file lacks it)."""
    p = Path(path)
    if not p.exists():
        return []
    with io.open(p, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return [{c: (r.get(c) or "") for c in COLUMNS} for r in rows]


def save(rows, path=PATH):
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in COLUMNS})


def fingerprint(row, lang="bn"):
    """sha256 over the fields a reviewer of `lang` sees. -> hex str

    Fields are joined with a separator no answer contains, so moving text
    from one field to the next is still a change.
    """
    fields = SEEN_EN if lang == "en" else SEEN_BN
    h = hashlib.sha256()
    for f in fields:
        h.update((row.get(f) or "").encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


def approved(row, lang="bn"):
    """True when `lang`'s approval exists and still matches the text."""
    if lang == "en":
        return (approved(row, "bn")
                and row.get("en_status") == "approved"
                and row.get("en_fingerprint") == fingerprint(row, "en"))
    return (row.get("status") == "approved"
            and row.get("fingerprint") == fingerprint(row, "bn"))


def shown(rows):
    """The rows the FAQ tab may show at all: Bengali approved, current."""
    return [r for r in rows if approved(r, "bn")]


def view(row, lang, manuals_bn=None):
    """What one row looks like in interface language `lang`. -> dict

    `lang_used` is the language actually shown, which differs from `lang`
    when the English is not yet approved -- the page says so, rather than
    letting an English reader wonder why the answer is in Bengali.
    """
    use_en = lang == "en" and approved(row, "en")
    used = "en" if use_en else "bn"
    if lang == "bn" and manuals_bn is None:
        manuals_bn = _manuals_bn()
    # Citations follow the INTERFACE language, not the answer's: a
    # citation is a manual title and a clause number rendered by code, so
    # there is no translation in it to review, and an English reader shown
    # a Bengali fallback can still find the clause.
    return {"id": row["id"], "category": row["category"],
            "question": row["question_en"] if use_en else row["question_bn"],
            "answer": row["answer_en"] if use_en else row["answer_bn"],
            "lang_used": used, "fell_back": lang == "en" and not use_en,
            "cites": [cite_text(c, lang, manuals_bn) for c in cite_list(row)],
            "attachment": row.get("attachment") or ""}


def cite_list(row):
    return [c.strip() for c in (row.get("cites") or "").split(";")
            if c.strip()]


def cite_text(cite, lang, manuals_bn=None):
    """'dabi_2025 §1.1.1' as an officer can look it up. -> str

    English: "Dabi Operations Manual 2025 §1.1.1". Bengali: the manual's
    printed title from the manifest and Bengali numerals, as the page
    shows it. Anything not in that shape comes back unchanged, so a
    malformed citation is visible rather than dressed up.
    """
    mid, sep, clause = cite.partition(" §")
    if not sep:
        return cite
    if lang == "en":
        return f"{MANUAL_EN.get(mid, mid)} §{clause}"
    if manuals_bn is None:
        manuals_bn = _manuals_bn()
    return f"{manuals_bn.get(mid, mid)} §{clause.translate(BN_DIGITS)}"


def _manuals_bn():
    import json
    p = Path("data/processed/chunk_manifest.json")
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8")).get("manuals") or {}


def approve(rows, rid, by, lang="bn", on=None):
    """Record an approval of `rid` as it stands now. -> the row"""
    row = next((r for r in rows if r["id"] == rid), None)
    if row is None:
        raise SystemExit(f"{rid}: no such row")
    on = on or date.today().isoformat()
    if lang == "en":
        if not approved(row, "bn"):
            raise SystemExit(f"{rid}: approve the Bengali first -- an "
                             "English approval is a statement about a "
                             "translation of approved text")
        row.update(en_status="approved", en_reviewed_by=by,
                   en_review_date=on, en_fingerprint=fingerprint(row, "en"))
    else:
        row.update(status="approved", reviewed_by=by, review_date=on,
                   fingerprint=fingerprint(row, "bn"))
    return row


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("approve", help="record a person's approval")
    a.add_argument("ids", nargs="+")
    a.add_argument("--by", required=True)
    a.add_argument("--en", action="store_true",
                   help="approve the English translation, not the Bengali")
    a.add_argument("--path", default=str(PATH))
    sub.add_parser("status", help="what shows, in each language")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    if args.cmd == "approve":
        rows = load(args.path)
        for rid in args.ids:
            approve(rows, rid, args.by, "en" if args.en else "bn")
            print(f"{rid}: {'English' if args.en else 'Bengali'} approved "
                  f"by {args.by}")
        save(rows, args.path)
        return 0

    rows = load()
    bn = sum(approved(r, "bn") for r in rows)
    en = sum(approved(r, "en") for r in rows)
    print(f"{len(rows)} rows; Bengali shown {bn}; English shown {en}; "
          f"English mode falls back to Bengali for {bn - en}")
    for r in rows:
        if r["status"] == "approved" and not approved(r, "bn"):
            print(f"  {r['id']}: approved, but edited since -- not shown")
    return 0


# Convention 1: importing this module never reads, writes or prints.
if __name__ == "__main__":
    sys.exit(main())
