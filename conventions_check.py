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
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MATCHER = "productmatch.py"          # the one module allowed product literals

# Modules that legitimately hold Bengali product words, with the reason.
LITERAL_EXEMPT = {
    MATCHER: "owns the product tables by definition",
    "dcheck.py": "GOOD/BAD lists are ENCODING PROBES -- 'did readable Bengali "
                 "come out of the docx at all'. Not product identification, "
                 "so substring semantics do not matter.",
    "conventions_check.py": "names the words it searches for",
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


def self_check():
    """The check must catch a synthetic violation, or it is testing nothing."""
    tree = parse(CANARY, "<canary>")
    lits = check_product_literals(tree)
    work = check_import_safety(tree)
    ok_lit = any(w == "দাবি" for _, w, _ in lits)
    ok_doc = not any("prose" in s for _, _, s in lits)
    ok_work = bool(work)
    print("SELF-CHECK")
    print(f"  product literal in code detected : {'yes' if ok_lit else 'NO'}")
    print(f"  docstring prose correctly ignored: {'yes' if ok_doc else 'NO'}")
    print(f"  module-level work detected       : {'yes' if ok_work else 'NO'}")
    return ok_lit and ok_doc and ok_work


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
        bad = check_product_literals(parse(p))
        if bad:
            lit_fails += 1
            print(f"  FAIL {p.name}")
            for line, word, snippet in bad[:4]:
                print(f"         line {line}: {word!r} in {snippet!r}")
    if not lit_fails:
        print(f"  none outside {MATCHER} and the listed exemptions")
    for name, why in sorted(LITERAL_EXEMPT.items()):
        print(f"    exempt: {name} -- {why[:62]}")
    fails += lit_fails

    print("\n" + "=" * 78)
    print("CLEAN" if not fails else f"{fails} VIOLATION(S)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
