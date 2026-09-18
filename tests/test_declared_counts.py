#!/usr/bin/env python3
"""
Every number and every absolute claim the prose makes about the code, checked
against the code.

Five times in this repository a sentence stopped being true and was printed to a
reader anyway: a warning's wording, the gate documentation, a dead-branch
comment in `roles.py`, `http_client.get`'s docstring, and a README asserting an
assertion count that had not been true for several rounds — labelled "实测" and
dated. Each was correct when written. None was attached to anything that would
notice when it stopped being correct.

This file attaches them. Two rules kept it small:

  - Only claims with a live constant on the other side. "Seven figures" is here
    because `CHART_IDS` is seven things. "Nothing here calls any journal
    predatory" is not, because there is no constant that means it — the strings
    themselves are the claim, and `test_journal_risk.py` already reads them.
  - No inventory that has to be re-listed by hand when the code grows. Every
    expected value below is computed from the package, never typed out; what is
    typed out is the sentence in the docs, which is the thing under test.

The suite's own totals are not here. Those need a measurement, so they live in
`run_all.py::check_declared_counts`, which runs after the last file and compares
what the docs declare against what the run just produced.

All checks are static reads of files already on disk. Fully offline.

Run: python tests/test_declared_counts.py
"""

from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_all  # noqa: E402  (the gate this file guards, and the count parser)
from check_your_advisor.cli import SUBCOMMANDS  # noqa: E402
from check_your_advisor.profile.charts import CHART_IDS  # noqa: E402
from check_your_advisor.profile.html_report import FIGURE_PLACEMENT  # noqa: E402
from check_your_advisor.profile.report import (  # noqa: E402
    COVERAGE_WARNING_SECTIONS,
    NAME_WARNING_SECTIONS,
    WARNING_SECTIONS,
)

_passed = 0
_failed = 0

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "scripts" / "check_your_advisor"


def check(label: str, actual, expected) -> None:
    global _passed, _failed
    ok = actual == expected
    if ok:
        _passed += 1
    else:
        _failed += 1
    detail = "" if ok else f"  (expected {expected!r}, got {actual!r})"
    print(f"  {'[PASS]' if ok else '[FAIL]'} {label}{detail}")


def check_true(label: str, cond) -> None:
    check(label, bool(cond), True)


def text(name: str) -> str:
    return (REPO / name).read_text(encoding="utf-8")


def where(name: str, needle: str) -> str:
    """`file:line` for the first line containing `needle`, for failure messages."""
    for i, line in enumerate(text(name).splitlines(), 1):
        if needle in line:
            return f"{name}:{i}"
    return f"{name}:?"


WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "twenty-one": 21,
}
CN_WORDS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
            "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def as_int(word: str) -> int | None:
    w = word.strip().lower()
    if w.isdigit():
        return int(w)
    return WORDS.get(w, CN_WORDS.get(word.strip()))


# ======================================================================
# Figures: how many the docs promise vs how many the package draws
# ======================================================================

print("\n[figures] every 'N figures' sentence against CHART_IDS")

check("the figure id list and the placement table hold the same figures",
      sorted(FIGURE_PLACEMENT_IDS := [fid for fid, _s, _c in FIGURE_PLACEMENT]),
      sorted(CHART_IDS))

n_figures = len(CHART_IDS)

# SKILL.md is the file a model reads before it says anything to a user, so a
# stale figure count there is the one that reaches a reader fastest.
_figure_claims = re.findall(r"\b([a-z]+|\d+)\s+(?:inline-SVG\s+)?figures\b",
                            text("SKILL.md"), re.I)
_figure_numbers = [n for n in (as_int(w) for w in _figure_claims) if n is not None]
check_true("SKILL.md states a figure count at all", _figure_numbers)
check(f"every figure count in SKILL.md is {n_figures}"
      f"  [if this fails, edit {where('SKILL.md', 'figures')} — do not edit CHART_IDS]",
      sorted(set(_figure_numbers)), [n_figures])

# The ImportError guard in `cmd_profile` degrades to a placeholder per figure,
# so its docstring's count is the same number and drifted once already.
_cli_src = (PKG / "cli.py").read_text(encoding="utf-8")
check(f"cli.py's degraded-install docstring says {n_figures} pictures"
      f"  [edit _profile_figures in cli.py]",
      as_int(re.search(r"over\s+([a-z]+)\s+pictures", _cli_src).group(1)), n_figures)

check(f"test_cli_profile.py's docstring says {n_figures} figures"
      f"  [edit the docstring at the top of tests/test_cli_profile.py]",
      as_int(re.search(r"costs all (\w+) figures",
                       (REPO / "tests" / "test_cli_profile.py").read_text(
                           encoding="utf-8")).group(1)),
      n_figures)

# charts.py's docstring enumerates figure-to-section pairs. It is the only place
# that mapping is written in prose, and renumbering a section would leave it
# describing a report that no longer exists.
_charts_doc = ast.get_docstring(ast.parse(
    (PKG / "profile" / "charts.py").read_text(encoding="utf-8"))) or ""
check("every figure-to-section pair in charts.py's docstring matches FIGURE_PLACEMENT"
      "  [edit the docstring at the top of profile/charts.py]",
      sorted((fid, int(sec)) for fid, sec in re.findall(r"(C-[A-Z]+)\s*\((\d+)\)", _charts_doc)),
      sorted((fid, sec) for fid, sec, _c in FIGURE_PLACEMENT))

_profile_init = (PKG / "profile" / "__init__.py").read_text(encoding="utf-8")
check_true("profile/__init__.py's module map names the module holding the other two"
           "  [edit the 'Layout:' block in profile/__init__.py]",
           re.search(r"^\s*figures\s", _profile_init, re.M))
check("profile/__init__.py does not still call them 'the five figures'"
      "  [edit the 'Layout:' block in profile/__init__.py]",
      "five figures" in _profile_init, False)


# ======================================================================
# Verbs: what the docs enumerate vs what main() dispatches
# ======================================================================

print("\n[verbs] the subcommand list, three places it is written down")

# `fetch` is the name harvest shipped under and is documented as an alias, not
# as a ninth verb. Everything else in SUBCOMMANDS is a verb a user can type.
ALIASES = {"fetch"}
verbs = sorted(SUBCOMMANDS - ALIASES)

check_true("`fetch` is still described as an alias rather than a verb"
           "  [if the alias was dropped, remove it from ALIASES here]",
           "also accepted as `fetch`" in _cli_src)

_frontmatter_verbs = re.search(r"\b([A-Za-z]+)\s+verbs\b", text("SKILL.md"))
check_true("SKILL.md's frontmatter states a verb count", _frontmatter_verbs)
check(f"SKILL.md's verb count is {len(verbs)}"
      f"  [edit {where('SKILL.md', ' verbs')}]",
      as_int(_frontmatter_verbs.group(1)), len(verbs))

# cli.py's own `Subcommands` block is the list a maintainer reads first, and it
# silently lost `journal-risk` when that verb was added.
_docstring = ast.get_docstring(ast.parse(_cli_src)) or ""
_listed = {v for v in verbs if re.search(rf"^\s*{re.escape(v)}\s{{2,}}", _docstring, re.M)}
check("cli.py's docstring lists every verb main() dispatches"
      "  [add the missing verb to the Subcommands block at the top of cli.py]",
      sorted(verbs), sorted(_listed))

check("SKILL.md names every verb"
      "  [add the missing verb to SKILL.md]",
      sorted(v for v in verbs if f"`{v}`" in text("SKILL.md")), verbs)


# ======================================================================
# Warning placement: prose that names section numbers
# ======================================================================

print("\n[warnings] the sections SKILL.md says each warning lands on")

_skill = text("SKILL.md")


def sections_after(phrase: str) -> tuple[int, ...]:
    """The `Sections 0, 1 and 19` list that follows `phrase` in SKILL.md."""
    m = re.search(re.escape(phrase) + r"[^.;]*?Sections?((?:\s+\d+,?)+(?:\s+and\s+\d+)?)", _skill)
    return tuple(sorted(int(n) for n in re.findall(r"\d+", m.group(1)))) if m else ()


check("SKILL.md's 'G2 and G3 land on ...' matches WARNING_SECTIONS"
      "  [edit the sentence in SKILL.md, or WARNING_SECTIONS in profile/report.py]",
      sections_after("G2 and G3 land on"), tuple(sorted(WARNING_SECTIONS)))
check("SKILL.md's 'G1 lands on ...' matches COVERAGE_WARNING_SECTIONS"
      "  [edit the sentence in SKILL.md, or COVERAGE_WARNING_SECTIONS]",
      sections_after("G1 lands on"), tuple(sorted(COVERAGE_WARNING_SECTIONS)))
check("SKILL.md's 'G7 lands on ...' matches NAME_WARNING_SECTIONS"
      "  [edit the sentence in SKILL.md, or NAME_WARNING_SECTIONS]",
      sections_after("G7 lands on"), tuple(sorted(NAME_WARNING_SECTIONS)))


# ======================================================================
# "The standard library only" — the claim the whole install story rests on
# ======================================================================

print("\n[imports] what README and pyproject promise about dependencies")

STDLIB = set(sys.stdlib_module_names)


def third_party_imports() -> tuple[list[str], dict[str, list[str]], int]:
    """(module-scope offenders, {name: [locations]}, how many sit under ImportError).

    "Inside a function" and "under `except ImportError`" are different facts and
    pyproject states both, so both are measured. Four of the six are directly
    guarded; `save_to_excel`'s two are not, and its callers are.
    """
    at_module: list[str] = []
    in_function: dict[str, list[str]] = {}
    directly_guarded = 0
    for path in sorted(PKG.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                child.parent = node  # type: ignore[attr-defined]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for name in names:
                if not name or name in STDLIB or name == "check_your_advisor":
                    continue
                loc = f"{path.name}:{node.lineno}"
                cur, enclosed, guarded = node, False, False
                while hasattr(cur, "parent"):
                    cur = cur.parent  # type: ignore[attr-defined]
                    if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        enclosed = True
                    if isinstance(cur, ast.Try) and any(
                            "ImportError" in ([h.type.id] if isinstance(h.type, ast.Name)
                                              else [getattr(e, "id", "") for e in h.type.elts]
                                              if isinstance(h.type, ast.Tuple) else [])
                            for h in cur.handlers):
                        guarded = True
                if enclosed:
                    in_function.setdefault(name, []).append(loc)
                    directly_guarded += guarded
                else:
                    at_module.append(f"{loc} {name}")
    return at_module, in_function, directly_guarded


_at_module, _guarded, _directly_guarded = third_party_imports()

# This is what "nothing to install" means operationally: importing any module in
# the package must not need a third party present.
check("no module in the package imports a third party at module scope"
      "  [README.md, README.zh-CN.md, SKILL.md and pyproject.toml all promise this]",
      _at_module, [])

# pyproject enumerates the guarded ones by name. If a third one appears, that
# enumeration became a lie and the extras table probably needs a row.
check("the only guarded third-party imports are the two declared extras"
      "  [edit the comment above [project.optional-dependencies] in pyproject.toml]",
      sorted(_guarded), ["fitz", "openpyxl"])

# pyproject used to say "Every import in this package is from the standard
# library", which six guarded imports had already falsified. The replacement
# states a count, so the count can be checked instead of trusted.
_n_guarded = sum(len(v) for v in _guarded.values())
_declared_guarded = re.search(r"(\w+) non-stdlib imports exist", text("pyproject.toml"))
check_true("pyproject states how many non-stdlib imports exist"
           "  [restore the sentence above [project.optional-dependencies]]",
           _declared_guarded)
check(f"pyproject's non-stdlib import count is {_n_guarded}"
      f"  [edit the comment above [project.optional-dependencies] in pyproject.toml]",
      as_int(_declared_guarded.group(1)) if _declared_guarded else None, _n_guarded)
for _name in sorted(_guarded):
    check(f"pyproject names {_name} as one of them"
          f"  [add it to that comment in pyproject.toml]",
          f"`{_name}`" in text("pyproject.toml"), True)

# "Inside a function" and "under except ImportError" are separate facts and the
# comment states both. Stating only the first would have been true and useless;
# stating the second for all six would have been false.
_declared_direct = re.search(r"(\w+) of the six sit\s*\n?#?\s*directly under",
                             text("pyproject.toml"))
check_true("pyproject says how many of them sit directly under except ImportError"
           "  [restore the sentence in pyproject.toml]", _declared_direct)
check(f"pyproject's directly-guarded count is {_directly_guarded}"
      f"  [edit the comment above [project.optional-dependencies] in pyproject.toml]",
      as_int(_declared_direct.group(1)) if _declared_direct else None, _directly_guarded)

_extras = re.findall(r"^(\w+)\s*=\s*\[", text("pyproject.toml").split(
    "[project.optional-dependencies]")[1].split("[project.urls]")[0], re.M)
check("README names every extra pyproject declares"
      "  [add the extra to the 'Optional extras' section of README.md]",
      sorted(e for e in _extras if f"[{e}]" in text("README.md")), sorted(_extras))

# PyMuPDF is an extra, not a dependency. `pdf_utils` said the opposite for a
# while, which turned "False means you did not install an extra" into "False
# means your install is broken".
check("pdf_utils does not call PyMuPDF a declared dependency"
      "  [edit pdf_text_extraction_available's docstring in pdf_utils.py]",
      "PyMuPDF is a\n    declared dependency" in
      (PKG / "pdf_utils.py").read_text(encoding="utf-8"), False)


# ======================================================================
# The blocker's module list, quoted in four files
# ======================================================================

print("\n[blocker] the module list --block-third-party actually installs")

_blocked = set(re.search(r"BLOCKED = \{(.*?)\}", run_all.BOOTSTRAP, re.S).group(1).replace(
    '"', "").replace("\n", " ").split(","))
_blocked = {b.strip() for b in _blocked if b.strip()}
# `pymupdf` is `fitz`'s distribution name, so a doc naming one has named both.
_documented_expected = sorted(_blocked - {"pymupdf"})

for _doc in ("README.md", "README.zh-CN.md", "SKILL.md", "pyproject.toml"):
    _body = text(_doc)
    check(f"{_doc} names every module the blocker blocks"
          f"  [edit the --block-third-party sentence in {_doc}]",
          sorted(m for m in _documented_expected if m in _body), _documented_expected)


# ======================================================================
# The declared totals: present, parseable, and agreeing with each other
# ======================================================================

print("\n[totals] the declarations run_all.py compares this run against")

_found, _missing = run_all.read_declarations()
check("every file that should declare a count still does"
      "  [restore the deleted sentence; DECLARED in tests/run_all.py lists the shape]",
      _missing, [])
check("all three declarations name the same assertion total"
      "  [make README.md, README.zh-CN.md and pyproject.toml agree]",
      len({c["full"] for _n, _l, c in _found if "full" in c}), 1)
check("all three declarations name the same file count",
      len({c["files"] for _n, _l, c in _found if "files" in c}), 1)

# "Exactly three assertions behave differently" is the only sentence tying the
# two runs together, and it is stated as a word in three files while the numbers
# behind it are digits in one. Pin the arithmetic.
_pyproject = next((c for n, _l, c in _found if n == "pyproject.toml"), {})
_delta = _pyproject.get("full", 0) - _pyproject.get("blocked", 0)
_DELTA_RX = r"exactly\s+(\w+)\s+assertions\s+behave\s+differently"
for _doc, _rx in (("README.md", _DELTA_RX),
                  ("SKILL.md", _DELTA_RX),
                  ("README.zh-CN.md", r"恰好(.)条断言")):
    _m = re.search(_rx, text(_doc))
    check_true(f"{_doc} still states how many assertions the blocker changes", _m)
    if _m:
        check(f"{_doc}'s spelled-out delta equals pyproject's subtraction"
              f"  [edit {where(_doc, _m.group(0)[:12])}, or the two numbers in pyproject.toml]",
              as_int(_m.group(1)), _delta)

# The gate itself. A defence nobody can delete by accident is worth one line.
check_true("run_all.py still carries the measured-vs-declared gate",
           callable(getattr(run_all, "check_declared_counts", None)))


# ----------------------------------------------------------------------
# What this file could not see until round four.
#
# The rule at the top — only claims with a live constant on the other side —
# left the four loudest sentences in the documentation unguarded, because
# "produces no percentile", "no letter grade", "no fitted trend" and "never
# orders people" had no constant to be checked against. They were prose, and
# prose is what drifts. Round four turned all four on, and the sentences saying
# otherwise sat in README.md, README.zh-CN.md and SKILL.md's frontmatter, where
# a model reads them before it says anything to a user.
#
# Each capability now *does* have a constant, so the claim becomes checkable in
# the negative: if the constant is importable, no document may still say the
# feature is refused. Anchored on an importable name rather than on the feature's
# output, so this fires at the moment somebody deletes the capability too — in
# which case the refusal sentences should come back and this list should shrink.
# ----------------------------------------------------------------------
print("\n[round four] no document still refuses a capability that now exists")

from check_your_advisor.impact_reference import (  # noqa: E402
    MIN_REFERENCE_POPULATION,
)
from check_your_advisor.profile.ranking import (  # noqa: E402
    LETTER_BANDS,
    RANKING_EXCLUSIONS,
)
from check_your_advisor.profile.roles import RANKABLE  # noqa: E402
from check_your_advisor.profile.trends import MIN_TREND_POINTS  # noqa: E402

#: (capability, the constant proving it exists, phrases no doc may still carry).
#: The phrases are the exact wordings that were in these files before round four.
NO_LONGER_REFUSED = (
    ("letter bands", LETTER_BANDS,
     ("no letter grade", "letter grade: stars are produced and letters are not",
      "字母等级拒绝")),
    ("fitted slopes", MIN_TREND_POINTS,
     ("and no fitted trend", "趋势拟合、任何对")),
    ("ordering people", RANKABLE,
     ("it never orders people", "no ordering of **people**, anywhere")),
    ("citation percentiles", MIN_REFERENCE_POPULATION,
     ("It still produces no percentile or quantile position,",
      "百分位不是\"不给\"，是**算不出来**")),
)

for _capability, _constant, _phrases in NO_LONGER_REFUSED:
    check_true(f"{_capability} is live (constant present)", bool(_constant))
    for _doc in ("README.md", "README.zh-CN.md", "SKILL.md"):
        _body = text(_doc)
        _stale = [phrase for phrase in _phrases if phrase in _body]
        check(f"{_doc} no longer refuses {_capability}"
              f"  [delete or rewrite the sentence at {where(_doc, _stale[0]) if _stale else _doc}]",
              _stale, [])

# The other half of the same rule, and the one that keeps this from becoming a
# licence to delete every caveat: the quantity that is *still* refused has to
# still be refused in writing. `ranking` holds the original words, so the check
# is that they survive rather than that some paraphrase of them does.
check_true("ranking still refuses a position among the loaded corpora",
           any("reference population" in name or "reference population" in reason
               for name, reason in RANKING_EXCLUSIONS["not_computable_here"]))
check_true("...and README.md still says so in prose",
           "position among the corpora" in text("README.md"))
check_true("...and README.zh-CN.md too",
           "在你加载的那几份语料里的位置" in text("README.zh-CN.md"))


print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(1 if _failed else 0)
