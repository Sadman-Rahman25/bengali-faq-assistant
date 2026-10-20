"""Serialise a Word table into text that keeps every figure with its label.

WHY THIS EXISTS. The old renderer -- `serial()`, defined identically in
ex.py and resplit.py -- zipped `rows[0]` as the header and prefixed every
cell with it. That is right for a table whose first row labels each column
individually, and wrong for one whose first row is a merged banner. In the
second case every cell in the table got labelled with the banner and the
column identity was gone:

    progoti §1.1.2, as it rendered
      লোনের পরিমাণ — প্রগতি লোন প্রোডাক্টসমূহের বৈশিষ্ট্য: নতুন লোনের
      ক্ষেত্রে- ১ লক্ষ থেকে ২০ লক্ষ টাকা — নতুন ভিসা- ১ লক্ষ - ৪ লক্ষ
      টাকা — ...

Five products' loan ceilings in a row, separated by dashes, with the
product names on a different line four rows up. To answer "what is
এগ্রিবিজনেস লোন's ceiling" a reader has to count dashes and align them to
a header elsewhere in the text. Measured consequence: six confidently
wrong figures in the FAQ, every one a neighbouring column's value, and
retrieval puts these same chunks in the live chatbot's top 5 for the same
questions.

WHAT CHANGES. The header may be more than one row, the row key may be more
than one column, and the output is COLUMN-MAJOR -- each column's figures
gathered under that column's full name:

    প্রত্যাশা লোন — লোনের পরিমাণ: ...; লোনের মেয়াদ: ৪, ৬, ১২, ১৮, ২৪ ও
    ৩৬ মাস; সার্ভিস চার্জ (ক্রমহ্রাসমান): ১৮% ।

WHAT DOES NOT CHANGE. Cell text is copied verbatim; only labels move.
That is a hard requirement, not a preference: gold facts of kind `literal`
assert that a string occurs verbatim in a named chunk, and four of them
live in these tables.

SCOPE IS BY CLAUSE, DELIBERATELY. §2.5.4 (কমপ্লেইন্ট ডাটা) has exactly the
same two-level header shape as §5.1.3 and would be improved by the same
treatment, so no structural predicate can separate them -- I checked the
raw rows of both. It is left out because this change is being validated by
comparing two indexes question by question, and a change that also moves
chunks nobody is asking about makes that comparison harder to read, not
easier. It is the obvious next candidate once this one is proven.
"""
import re
import unicodedata

ZW = re.compile("[​‌‍﻿]")

# Clauses whose tables are rendered column-major. Everything else keeps the
# old output byte for byte -- see serial().
#
# A  a column per product or programme; the six wrong figures came from the
#    first three of these.
# B  a two-level header over a numeric matrix: the group label survived the
#    old renderer and the sub-label did not, so ০.২৫ টাকা — ০.৫০ টাকা gave
#    no way to tell একক from দ্বৈত except position.
COLUMN_MAJOR_CLAUSES = frozenset({
    "1.1.1", "1.1.2", "1.1.3", "2.3.1",     # A
    "1.2.2", "5.1.3", "5.2.4", "11.1",      # B
})


SPACES = re.compile("[ 	 ]+")


def nm(s):
    """ex.py's normaliser, character for character.

    It is copied rather than approximated because approximating it already
    cost a chunk: leaving out the space-collapse put a single trailing
    space into progoti_0062 -- a table in §2.1.2, nowhere near the clauses
    this change is scoped to -- and the scope check caught it as an
    out-of-scope edit. The space ran through recover_caption, which peels a
    caption off a body by exact suffix match, so one space moved a chunk
    that nothing in this change was supposed to touch.
    """
    s = ZW.sub("", unicodedata.normalize("NFC", s or "")).replace(
        "৷", "।")
    return SPACES.sub(" ", s).strip()


def clause_of_path(heading_path):
    """The deepest numbered clause in a heading path, digit-folded.

    Delegates to dedupe.clause_pair so there is ONE clause parser in the
    project; ex.py only has a heading path at this point, not a chunk, so
    it hands over the shape clause_pair expects.
    """
    from dedupe import clause_pair
    return clause_pair({"heading_path": list(heading_path or [])})[0]


def header_depth(rows):
    """Number of leading rows that are header, i.e. the header is rows[:h].

    A merged cell arrives from rows_of() as its text followed by empty
    padding, so a banner row is one that leaves a column blank which the
    row below fills. Structural: no wording, no product names.

    THE THIRD CONDITION IS NOT OPTIONAL. Row 1 must name at least TWO
    columns. Without it the গতি table -- §1.1.2, one banner across three
    columns, then `ঋণের পরিমাণ | | ১ লক্ষ - ৫ লক্ষ টাকা` -- reads row 1 as
    a header and promotes a loan amount to a column name:

        নতুন লোনের ক্ষেত্রে ১ লক্ষ - ৫ লক্ষ টাকা পর্যন্ত। — ঋণের মেয়াদ: ...

    which is a worse error than the one being fixed, in a table that never
    had the problem: গতি is a single product, so its figures were never
    ambiguous. A table needs two columns before columns can be confused.
    """
    if len(rows) < 2:
        return 1
    top, nxt = rows[0], rows[1]
    spans = any(nm(top[j]) and j + 1 < len(top) and not nm(top[j + 1])
                for j in range(len(top)))
    fills = any(not nm(top[j]) and nm(nxt[j])
                for j in range(min(len(top), len(nxt))))
    named = sum(1 for j in range(1, len(nxt)) if nm(nxt[j]))
    return 2 if (spans and fills and named >= 2) else 1


def _width(rows):
    return max((len(r) for r in rows), default=0)


def _cell(rows, i, j):
    r = rows[i] if i < len(rows) else []
    return nm(r[j]) if j < len(r) else ""


def split_columns(rows):
    """-> ([label column indices], [data column indices]).

    A data column is one the LAST header row names. Column 0 is always a
    label: where the last header row does name it (§11.1's মেয়াদ/সুদ) it is
    naming the row key, not a column of values.
    """
    h = header_depth(rows) - 1
    w = _width(rows)
    data = [j for j in range(1, w) if _cell(rows, h, j)]
    label = [j for j in range(w) if j not in data]
    return label, data


def _banner_rows(rows):
    """Leading header rows that say the same thing about every data column.

    §1.1.2's row 0 is one merged cell -- প্রগতি লোন প্রোডাক্টসমূহের
    বৈশিষ্ট্য -- spanning all five products. Resolved per column it becomes
    the same group label on every line: 40 characters of table title
    repeated five times, distinguishing nothing.

    It is lifted OUT of the column labels and printed once, as a title
    line. Not deleted: §11.1's banner is প্রতি ১,০০০/— লোনের জন্য কিস্তির
    পরিমাণ, and without it "২৪% — ৩ মাস: ৩৪৭" does not say what ৩৪৭ is per.
    Shared is not the same as redundant.
    """
    h = header_depth(rows)
    _lab, data = split_columns(rows)
    n = 0
    for i in range(h - 1):            # never drop the LAST header row
        vals = set()
        for j in data:
            v = _cell(rows, i, j)
            if not v:
                for k in range(j, -1, -1):
                    if _cell(rows, i, k):
                        v = _cell(rows, i, k)
                        break
            vals.add(v)
        if len(vals) == 1:
            n += 1
        else:
            break
    return n


def column_label(rows, j, only_row=None):
    """Column j's full name: the group it sits under, then its own.

    The group is carried leftwards across the merge padding, which is how
    a gridSpan reaches the columns it covers.
    """
    h = header_depth(rows)
    parts = []
    span = ([only_row] if only_row is not None
            else range(_banner_rows(rows), h))
    for i in span:
        v = _cell(rows, i, j)
        if not v:
            for k in range(j, -1, -1):          # walk back to the merge head
                if _cell(rows, i, k):
                    v = _cell(rows, i, k)
                    break
        if v and v not in parts:
            parts.append(v)
    return " — ".join(parts)


def row_label(rows, i, label_cols):
    """Row i's key, two-level where the table has two.

    §1.1.2 puts সার্ভিস চার্জ (ক্রমহ্রাসমান) in c0 and its slab -- < ৫,০০,০০০
    -- in c1. Taking c0 alone gives three rows that all read "সার্ভিস চার্জ"
    with different percentages under them, which is a row error traded for
    the column error this file exists to remove.
    """
    parts = []
    for j in label_cols:
        v = _cell(rows, i, j)
        if v and v not in parts:
            parts.append(v)
    return " — ".join(parts)


def serial_columns(rows, cap="", columns=None):
    """Column-major rendering. One line per data column, plus any row that
    holds only a label (a section heading inside the table, e.g. §2.3.1's
    ফরম্যাটস) kept in place so nothing is dropped.

    `columns` renders a SUBSET of the data columns, which is how resplit
    divides one of these tables: a part is a slice of COLUMNS, not of rows,
    because a row-slice would cut each product's figures in half again.
    The grid handed in is always the whole table, so a column keeps its
    group label however the parts fall.
    """
    if not rows:
        return ""
    h = header_depth(rows)
    label_cols, data_cols = split_columns(rows)
    if columns is not None:
        data_cols = [j for j in data_cols if j in set(columns)]
    if not data_cols:
        return ""
    out = [nm(cap)] if cap else []
    for i in range(_banner_rows(rows)):
        banner = column_label(rows, data_cols[0], only_row=i)
        if banner and banner not in out:
            out.append(banner + " ।")
    for i in range(h, len(rows)):
        if any(_cell(rows, i, j) for j in data_cols):
            continue
        key = row_label(rows, i, label_cols)
        if key:
            out.append(key + " ।")
    for j in data_cols:
        col = column_label(rows, j)
        bits, seen = [], set()
        for i in range(h, len(rows)):
            v = _cell(rows, i, j)
            if not v:
                continue
            key = row_label(rows, i, label_cols)
            if (key, v) in seen:
                continue
            seen.add((key, v))
            bits.append(f"{key}: {v}" if key else v)
        if not bits:
            continue
        line = "; ".join(bits)
        out.append((f"{col} — {line}" if col else line) + " ।")
    return "\n".join(out)


def serial_rows(rows, cap=""):
    """The ORIGINAL renderer, unchanged, for every table out of scope.

    Kept here rather than left in two files so that "out of scope means
    byte-identical" is checkable by reading one function.
    """
    if not rows:
        return ""
    h, out = list(rows[0]), ([nm(cap)] if cap else [])
    for r in rows[1:]:
        p = [f"{nm(a)}: {nm(b)}" if nm(a) else nm(b)
             for a, b in zip(h, r) if nm(b)]
        if p:
            out.append(" — ".join(p) + " ।")
    return "\n".join(out)


def serial(rows, cap="", heading_path=None, columns=None):
    """The one entry point. Column-major for a table that is BOTH in a
    listed clause and shaped like the defect; the original rendering for
    everything else."""
    if heading_path is not None and column_major_table(rows, heading_path):
        return serial_columns(rows, cap, columns)
    return serial_rows(rows, cap)


def in_scope(heading_path):
    """Clause-level scope only. Callers that have the table should ask
    column_major_table() instead -- a listed clause holds tables of more
    than one shape (§1.1.2 has the five-product grid AND গতি's and
    উন্মেষ's single-product lists)."""
    return clause_of_path(heading_path) in COLUMN_MAJOR_CLAUSES


def column_major_table(rows, heading_path):
    """Both tests: a listed clause, and a real two-level header over two or
    more data columns."""
    if not rows or not in_scope(heading_path):
        return False
    if header_depth(rows) < 2:
        return False
    return len(split_columns(rows)[1]) >= 2
