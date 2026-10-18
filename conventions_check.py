"""Enforce the two project conventions. See CONVENTIONS.md.

  1. Importing a module must never do work or write files.
  2. No module may match a product name directly; productmatch owns them.

Both are checked mechanically, because both have been broken repeatedly by
people (and models) who knew the rule and forgot it anyway -- convention 1
three times, convention 2 four times. Prose in a README does not survive
that; a check that exits non-zero does.

SELF-CHECK: convention 2 works by searching for Bengali words. If that word
list were ever corrupted -- and it was, once, by a PowerShell round-trip that
mojibake'd it -- the check would report "clean" while testing nothing. So it
runs a synthetic violation through itself first and fails loudly if that is
not caught.

Usage:  python conventions_check.py
Exit:   0 clean, 1 violations
"""
import ast
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from bengali_literal_lint import scan_paths as scan_bengali_literals

ROOT = Path(__file__).resolve().parent
MATCHER = "productmatch.py"          # the one module allowed product literals

# Modules that legitimately hold Bengali product words, with the reason.
LITERAL_EXEMPT = {
    MATCHER: "owns the product tables by definition",
    "dcheck.py": "GOOD/BAD lists are ENCODING PROBES -- 'did readable Bengali "
                 "come out of the docx at all'. Not product identification, "
                 "so substring semantics do not matter.",
    "conventions_check.py": "names the words it searches for",
    "probe_words.py": "CANARY_CORRUPT / CANARY_CLEAN / CANARY_PROBES are "
                      "ENCODING PROBES in dcheck.py's sense -- 'did the "
                      "right Bengali come out of this text layer'. They are "
                      "matched only against the two canary constants in this "
                      "module, never against the corpus. Corpus-side product "
                      "probes come from registry_vocabulary(), which pulls "
                      "the literals from productmatch and roles at runtime.",
    "role_exclusions.py": "the dabi_inside_bimadabi canary matches দাবি and "
                          "বিমাদাবি on purpose, to prove a plain matcher "
                          "over-counts a programme name inside a compound. "
                          "It runs only against CANARY_CLAIM in this module, "
                          "never against the corpus, and productmatch still "
                          "owns দাবি's real exclusion -- EXCLUSIONS here is "
                          "restricted to role literals. If this canary ever "
                          "stops firing, a caller is matching দাবি directly "
                          "again.",
}


# Statement types allowed at module level. Anything else is work-on-import.
ALLOWED_TOP = (ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign,
               ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
               ast.Expr)          # Expr covers docstrings; checked below

PRODUCT_WORDS = [
    "দাবি", "প্রগতি", "বিসিইউপি", "সিডিপি", "এনসিডিপি", "এসসিডিপি",
    "স্বাধী", "ওয়াশ", "প্রযুক্তি লোন", "মাইগ্রেশন", "রেমিটেন্স",
    "এগ্রিবিজনেস", "নির্ভরতা", "উন্মেষ", "প্রত্যাশা", "নিরাপত্তা বিমা",
    "সঞ্চয় প্রকল্প", "গবাদিপ্রাণি", "ফায়ার সেফটি",
]


def py_files():
    return sorted(list(ROOT.glob("*.py")) + list((ROOT / "tests").glob("*.py")))


def parse(path_or_src, name="<src>"):
    # utf-8-sig: a BOM is invisible to the interpreter but breaks ast.parse,
    # and Windows editors add them freely
    if isinstance(path_or_src, Path):
        return ast.parse(path_or_src.read_text(encoding="utf-8-sig"),
                         str(path_or_src))
    return ast.parse(path_or_src, name)


def is_main_guard(node):
    return (isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "__name__")


def _guarded_import(node):
    """try/except around an optional import is fine; it does no work."""
    return isinstance(node, ast.Try) and all(
        isinstance(x, (ast.Import, ast.ImportFrom, ast.Assign, ast.Pass))
        for x in node.body)


def _syspath_setup(node):
    """sys.path.insert(...) before a local import -- required in tests/."""
    return (isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and isinstance(node.value.func.value, ast.Attribute)
            and isinstance(node.value.func.value.value, ast.Name)
            and node.value.func.value.value.id == "sys"
            and node.value.func.value.attr == "path")


def check_import_safety(tree):
    """Static: any module-level statement that is real work."""
    bad = []
    for node in tree.body:
        if is_main_guard(node) or _guarded_import(node) or _syspath_setup(node):
            continue
        if isinstance(node, ast.Expr):
            # a docstring is fine; a bare call at module level is not
            if isinstance(node.value, ast.Constant) and isinstance(
                    node.value.value, str):
                continue
            bad.append((node.lineno, "call at module level"))
        elif isinstance(node, (ast.For, ast.While, ast.With, ast.Try)):
            bad.append((node.lineno,
                        f"{type(node).__name__.lower()} at module level"))
        elif not isinstance(node, ALLOWED_TOP):
            bad.append((node.lineno, type(node).__name__))
    return bad


def check_import_writes(path):
    """Dynamic: import the module and see whether any file changed."""
    def snapshot():
        return {p: p.stat().st_mtime_ns for p in ROOT.rglob("*")
                if p.is_file() and ".git" not in p.parts
                and ".venv" not in p.parts and p.suffix != ".pyc"}

    before = snapshot()
    mod = path.stem if path.parent == ROOT else f"tests.{path.stem}"
    r = subprocess.run(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, r'{ROOT}'); import {mod}"],
        capture_output=True, text=True, cwd=ROOT, timeout=180)
    after = snapshot()
    touched = sorted(str(p.relative_to(ROOT)) for p in after
                     if before.get(p) != after[p])
    err = r.stderr.strip().split("\n")[-1] if r.stderr else ""
    return touched, r.returncode, err


# Calls whose string argument is being matched against text.
MATCH_CALLS = {"compile", "search", "match", "fullmatch", "findall", "finditer",
               "sub", "subn", "split", "count", "find", "index", "startswith",
               "endswith", "replace", "compile_pat", "canon_pattern"}
# Names that announce a table of patterns.
PATTERNISH = ("PROD", "PATTERN", "_PAT", "_RX", "RX_", "MATCH", "WORDS",
              "TERMS", "NAMES", "ALIAS")


def _is_bare_name(s):
    """A bare product name, as opposed to an English sentence mentioning one."""
    t = s.strip()
    if len(t) > 40 or not t:
        return False
    chars = [c for c in t if not c.isspace()]
    bengali = sum(1 for c in chars if "ঀ" <= c <= "৿")
    return chars and bengali / len(chars) >= 0.6


def check_product_literals(tree):
    """Product names used for MATCHING. Prose that merely mentions one is fine.

    The convention forbids re-deriving product matching, not writing the word
    দাবি in a log message. So a literal is flagged only when it is
      (a) an argument to a matching call, or
      (b) inside an assignment to a pattern-ish name, or
      (c) a bare Bengali name rather than an English sentence.
    """
    docstrings = set()
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                docstrings.add(id(node.body[0].value))

    def assigned_name(node):
        """Walk up to the enclosing assignment and return its target name."""
        cur = node
        for _ in range(8):
            p = parents.get(id(cur))
            if p is None:
                return None
            if isinstance(p, ast.Assign):
                t = p.targets[0]
                return t.id if isinstance(t, ast.Name) else None
            cur = p
        return None

    bad = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in docstrings:
            continue
        word = next((w for w in PRODUCT_WORDS if w in node.value), None)
        if not word:
            continue

        parent = parents.get(id(node))
        why = None
        if isinstance(parent, ast.Call):
            f = parent.func
            name = (f.attr if isinstance(f, ast.Attribute)
                    else f.id if isinstance(f, ast.Name) else "")
            if name in MATCH_CALLS:
                why = f"matched via {name}()"
        if why is None and isinstance(parent, ast.Compare) and any(
                isinstance(o, (ast.In, ast.NotIn)) for o in parent.ops):
            why = "used with `in`"
        if why is None:
            target = assigned_name(node) or ""
            if any(k in target.upper() for k in PATTERNISH):
                why = f"in pattern table {target}"
        if why is None and _is_bare_name(node.value):
            why = "bare product name"

        if why:
            bad.append((node.lineno, word, f"{node.value[:36]}  [{why}]"))
    return bad


CANARY = '''
"""A docstring naming দাবি is prose and must NOT be flagged."""
import os
BAD_PATTERN = "দাবি"
print("work at import time")
'''


# LINE-LEVEL EXEMPTION, and why it exists alongside LITERAL_EXEMPT.
#
# LITERAL_EXEMPT switches rule 2 off for a WHOLE MODULE. That is right for
# productmatch, which owns the tables, and for the encoding probes, whose
# every literal is a probe. It is wrong for a module that holds one or two
# output labels and a great deal of ordinary code: exempting dedupe.py so
# that MANUAL_DISPLAY can name two manuals would also stop the check ever
# noticing a real matcher added to that file later, which is precisely the
# failure mode this convention was written for.
#
# So: a per-line marker, carrying a REASON. Same shape and same rule as
# bengali_literal_lint's `# noqa: BN4` -- one idiom for suppressing a lint
# in this repo, not two. A reasonless marker suppresses NOTHING and is
# reported, because "why" is the only part of an exemption that survives
# contact with the next reader.
NOQA_RX = re.compile(r"#\s*noqa:\s*C2(?P<reason>[^\n]*)")


def noqa_lines(src):
    """-> ({line_no: reason}, [line_no with a reasonless marker])."""
    excused, bare = {}, []
    for i, line in enumerate(src.splitlines(), 1):
        m = NOQA_RX.search(line)
        if not m:
            continue
        reason = m.group("reason").strip(" \t-:")
        if reason:
            excused[i] = reason
        else:
            bare.append(i)
    return excused, bare


def literals_with_noqa(path):
    """check_product_literals, minus lines carrying a reasoned marker.

    -> (findings, {line: reason}, [reasonless line]).
    """
    src = Path(path).read_text(encoding="utf-8-sig")
    excused, bare = noqa_lines(src)
    found = [f for f in check_product_literals(parse(path))
             if f[0] not in excused]
    return found, excused, bare


# A module-level fixture, so the canary below tests the real function on a
# real file rather than a re-implementation of it. Line 3 carries a marker
# with a reason; line 4 carries one without; line 5 carries none. Only the
# first may be suppressed.
NOQA_CANARY = (
    "# fixture\n"
    "BARE = দাবি_PLACEHOLDER\n"
    "A = \"দাবি\"  # noqa: C2 a stated reason\n"
    "B = \"দাবি\"  # noqa: C2\n"
    "C = \"দাবি\"\n"
)


def self_check():
    """The check must catch a synthetic violation, or it is testing nothing."""
    tree = parse(CANARY, "<canary>")
    lits = check_product_literals(tree)
    work = check_import_safety(tree)
    ok_lit = any(w == "দাবি" for _, w, _ in lits)
    ok_doc = not any("prose" in s for _, _, s in lits)
    ok_work = bool(work)

    # THE MARKER MUST BE LINE-LEVEL, NOT FILE-LEVEL. A suppression that
    # silently covers the rest of the file is worse than no suppression: it
    # reports clean while the module it was added to stops being checked.
    # Measured on NOQA_CANARY, which carries all three cases at once.
    excused, bare = noqa_lines(NOQA_CANARY)
    hits = [ln for ln, _w, _s in check_product_literals(
        parse(NOQA_CANARY, "<noqa-canary>")) if ln not in excused]
    ok_noqa = (excused.keys() == {3} and bare == [4]
               and 3 not in hits and {4, 5} <= set(hits))

    print("SELF-CHECK")
    print(f"  product literal in code detected : {'yes' if ok_lit else 'NO'}")
    print(f"  docstring prose correctly ignored: {'yes' if ok_doc else 'NO'}")
    print(f"  module-level work detected       : {'yes' if ok_work else 'NO'}")
    print(f"  noqa is line-level, reason needed: "
          f"{'yes' if ok_noqa else 'NO'}  "
          f"(excused={sorted(excused)} reasonless={bare} still_flagged={hits})")
    return ok_lit and ok_doc and ok_work and ok_noqa


def main():
    files = py_files()
    print("=" * 78)
    print(f"CONVENTIONS CHECK -- {len(files)} modules")
    print("=" * 78)

    if not self_check():
        print("\n  SELF-CHECK FAILED -- this check is not testing anything.")
        return 1

    fails = 0
    print("\n1. IMPORT SAFETY -- importing must not do work or write files")
    print("-" * 78)
    static_fails = 0
    for p in files:
        bad = check_import_safety(parse(p))
        if bad:
            static_fails += 1
            print(f"  FAIL {p.name}")
            for line, what in bad[:4]:
                print(f"         line {line}: {what}")
    if not static_fails:
        print("  all modules: no work at module level")
    fails += static_fails

    print("\n   dynamic -- import each module and watch for file writes")
    write_fails = 0
    for p in files:
        touched, rc, err = check_import_writes(p)
        if rc != 0:
            write_fails += 1
            print(f"  FAIL {p.name} does not import cleanly: {err[:60]}")
        elif touched:
            write_fails += 1
            print(f"  FAIL {p.name} wrote on import: {touched[:4]}")
    if not write_fails:
        print("   all modules import without writing anything")
    fails += write_fails

    print("\n2. PRODUCT LITERALS -- only productmatch may hold them")
    print("-" * 78)
    lit_fails = 0
    for p in files:
        if p.name in LITERAL_EXEMPT:
            continue
        bad, excused, reasonless = literals_with_noqa(p)
        for line in reasonless:
            lit_fails += 1
            print(f"  FAIL {p.name}")
            print(f"         line {line}: '# noqa: C2' with no reason "
                  f"suppresses nothing")
        if bad:
            lit_fails += 1
            print(f"  FAIL {p.name}")
            for line, word, snippet in bad[:4]:
                print(f"         line {line}: {word!r} in {snippet!r}")
        for line, why in sorted(excused.items()):
            print(f"    line-exempt: {p.name}:{line} -- {why[:58]}")
    if not lit_fails:
        print(f"  none outside {MATCHER} and the listed exemptions")
    for name, why in sorted(LITERAL_EXEMPT.items()):
        print(f"    exempt: {name} -- {why[:62]}")
    fails += lit_fails

    print("\n4. BENGALI LITERALS -- every one goes through canon_pattern()")
    print("-" * 78)
    hits = scan_bengali_literals(files)
    errs = [h for h in hits if h["nukta"]]
    warns = [h for h in hits if not h["nukta"]]
    if errs:
        print(f"  FAIL {len(errs)} broken pattern(s) -- nukta present, so NFC "
              f"cannot unify the\n       two forms and the pattern finds "
              f"nothing without telling you:")
        for h in errs:
            print(f"         {Path(h['file']).name}:{h['line']}  "
                  f"{h['kind']}  {h['literal']!r}")
    else:
        print("  no nukta-bearing literal reaches a matching operation")
    if warns:
        print(f"\n  ADVISORY {len(warns)} raw literal(s) with no nukta "
              f"character. These work\n  today and do not fail the build; "
              f"they break the day the literal changes.")
        for h in warns[:8]:
            print(f"         {Path(h['file']).name}:{h['line']}  "
                  f"{h['kind']}  {h['literal']!r}")
        if len(warns) > 8:
            print(f"         ... and {len(warns) - 8} more "
                  f"(bengali_literal_lint.py lists all)")
    fails += len(errs)

    print("\n" + "=" * 78)
    print("CLEAN" if not fails else f"{fails} VIOLATION(S)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
