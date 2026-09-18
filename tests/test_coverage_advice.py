#!/usr/bin/env python3
"""
The coverage-gap remedy has to be a remedy for the gap that actually happened.

Why this file exists
--------------------
Section 1 prints "retrieved N of M records esearch matched", and when N < M it
printed one fixed sentence: *Raise max_records, cut years_back, or add
affiliation_keywords, then re-harvest.*

`pubmed_api` documents, in its own source, that NCBI's E-utilities returns at
most the first 10,000 records for any PubMed esearch, and pins `MAX_RECORDS` to
exactly that number for that reason. So on the run with the biggest gap — a name
matching 50,000 papers, of which 10,000 came back — the first action the report
recommended was the one action that cannot recover a single record. Raising the
budget past 10,000 only pages into empty results.

The gap has two causes and now has two texts:

  - the tool's own budget stopped it  → raising max_records genuinely helps,
    and the ceiling is named as the limit of how far that goes
  - NCBI's ceiling stopped it         → say so, say it is NCBI's interface limit
    and not a setting here, and recommend narrowing the query instead

Also covered: the boundary between them, the case where neither is responsible
(the gap is duplicates or Count drift inside a query smaller than the ceiling),
the corpus with no gap at all, and a file whose provenance never recorded a
budget.

The harvest log says the same thing, at the same time (section 6)
-----------------------------------------------------------------
There are two surfaces, not one. `pubmed_api.search_pubmed` warns the moment the
gap appears; the report is written after the whole run, twenty minutes later. The
user reads the log first. The report was fixed and the log was not, so on the
ceiling-bound run the log still opened with *调高 max_records* — the single action
that recovers nothing — while the report a third of an hour later said the
opposite. Section 6 drives a real `search_pubmed` against a fake E-utilities
endpoint that stops at 10,000 like the real one, and reads the emitted log
record. Section 7 pins the sharing itself: one cause test, one set of sentences,
so the two surfaces cannot drift apart again.

Standard library only, no network. The unit half calls `_coverage_lines`
directly; the end-to-end half builds a real report.

Run: python tests/test_coverage_advice.py
"""

from __future__ import annotations

import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor.cli import _profile_corpus  # noqa: E402
from check_your_advisor.profile import report as _report_module  # noqa: E402
from check_your_advisor.profile.report import (  # noqa: E402
    _coverage_lines,
    build_report,
    render_markdown,
)
from check_your_advisor import pubmed_api  # noqa: E402
from check_your_advisor.pubmed_api import (  # noqa: E402
    ESEARCH_MAX_RETRIEVABLE,
    MAX_RECORDS,
    coverage_remedy,
    shortfall_is_ceiling_bound,
)

_passed = 0
_failed = 0


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


def check_false(label: str, cond) -> None:
    check(label, bool(cond), False)


def gap_line(**query) -> str:
    """The shortfall sentence, or "" when the report did not print one."""
    lines = _coverage_lines(query)
    return lines[1] if len(lines) > 1 else ""


# ======================================================================
print("0. the two names for 10000 mean two different things")
# ======================================================================

check("NCBI's esearch ceiling is 10000", ESEARCH_MAX_RETRIEVABLE, 10000)
check("the default budget is pinned to it", MAX_RECORDS, ESEARCH_MAX_RETRIEVABLE)
# They are equal today and must stay separately named: one is NCBI's and one is
# ours, and the whole point of this file is that the advice differs by which of
# them stopped the harvest.


# ======================================================================
print("\n1. budget-bound gap — raising max_records is the right first action")
# ======================================================================

budget = gap_line(esearch_count=900, pmids_returned=500, max_records=500,
                  pages_fetched=1, retmax=500, duplicates_dropped=0)

check_true("the shortfall is still stated as a number over a number",
           "400 of those 900 records were never retrieved" in budget)
check_true("...and the counts below are still called floors",
           "is a floor rather than a value" in budget)
check_true("raising the budget is recommended", "Raise max_records" in budget)
check_true("...and the ceiling it can be raised to is named",
           "10,000-record esearch ceiling" in budget)
check_true("the other two knobs are still offered", "cut years_back" in budget)
check_true("...both of them", "add affiliation_keywords" in budget)
check_false("nothing claims the budget is useless here",
            "will not recover" in budget)


# ======================================================================
print("\n2. ceiling-bound gap — raising max_records recovers nothing")
# ======================================================================
# The exact run this file exists for: a very common name, the default budget,
# and 40,000 records that NCBI will never hand over at any budget.

ceiling = gap_line(esearch_count=50000, pmids_returned=10000, max_records=10000,
                   pages_fetched=20, retmax=500, duplicates_dropped=0)

check_true("the shortfall is still stated as a number over a number",
           "40000 of those 50000 records were never retrieved" in ceiling)
check_true("...and the counts below are still called floors",
           "is a floor rather than a value" in ceiling)
check_false("**the useless action is not recommended**", "Raise max_records" in ceiling)
check_true("it is named and ruled out instead",
           "Raising max_records will not recover any of them" in ceiling)
check_true("whose limit this is, is stated", "NCBI's E-utilities" in ceiling)
check_true("...with the number", "10,000 records" in ceiling)
check_true("...and that it is not a setting in this tool",
           "not a setting in this tool" in ceiling)
check_true("narrowing the query is what is recommended instead",
           "Narrow the search" in ceiling)
check_true("...by cutting the window", "cut years_back" in ceiling)
check_true("...or naming the institution", "add affiliation_keywords" in ceiling)
check_true("...or supplying an ORCID", "supply an ORCID" in ceiling)
check_true("...with the year-by-year fallback for a query that cannot be narrowed",
           "one year at a time" in ceiling)


# ======================================================================
print("\n3. which branch fires, case by case")
# ======================================================================


def is_ceiling(line: str) -> bool:
    return "will not recover any of them" in line


def is_budget(line: str) -> bool:
    return "Raise max_records" in line


base = dict(pages_fetched=1, retmax=500, duplicates_dropped=0)

# A budget below the ceiling is the tighter constraint even on a huge query:
# going from 500 to 10,000 really does recover 9,500 records.
line = gap_line(esearch_count=50000, pmids_returned=500, max_records=500, **base)
check_true("a small budget on a huge query is budget-bound", is_budget(line))
check_false("...and is not called ceiling-bound", is_ceiling(line))

# A budget the user raised ABOVE the ceiling is not the constraint; NCBI is.
line = gap_line(esearch_count=50000, pmids_returned=10000, max_records=50000, **base)
check_true("a budget raised past the ceiling is ceiling-bound", is_ceiling(line))
check_false("...and does not tell the user to raise it further", is_budget(line))

# A gap inside a query smaller than the ceiling is duplicates or Count drift.
# NCBI's limit is irrelevant and saying otherwise would be a false explanation.
line = gap_line(esearch_count=9000, pmids_returned=8000, max_records=10000, **base)
check_true("a sub-ceiling query with a gap is not blamed on NCBI", is_budget(line))
check_false("...however generous the budget was", is_ceiling(line))

# Exactly at the ceiling: 10,000 matched, 10,000 is retrievable, so any gap here
# is not the ceiling either.
line = gap_line(esearch_count=10000, pmids_returned=9000, max_records=10000, **base)
check_true("a query matching exactly 10000 is not ceiling-bound", is_budget(line))
check_false("...and is not told that raising the budget is futile", is_ceiling(line))

# One past it.
line = gap_line(esearch_count=10001, pmids_returned=10000, max_records=10000, **base)
check_true("one record past the ceiling is ceiling-bound", is_ceiling(line))

# A corpus harvested before the budget was recorded. The default is pinned to
# the ceiling, so a huge query with no recorded budget is the ceiling case; the
# alternative is telling the user to raise a setting that was already there.
line = gap_line(esearch_count=50000, pmids_returned=10000, **base)
check_true("an unrecorded budget on a huge query is treated as the ceiling",
           is_ceiling(line))
line = gap_line(esearch_count=900, pmids_returned=500, **base)
check_true("an unrecorded budget on a small query is not", is_budget(line))


# ======================================================================
print("\n4. no gap, and unusable provenance")
# ======================================================================

full = _coverage_lines(dict(esearch_count=149, pmids_returned=149, max_records=10000, **base))
check("a complete corpus prints one line and no remedy", len(full), 1)
check_true("...which still carries both numbers",
           "retrieved 149 of 149 records esearch matched" in full[0])

unknown = _coverage_lines({"esearch_count": "?", "pmids_returned": "?"})
check("a corpus with no counts prints one line and no remedy", len(unknown), 1)
check_true("...and says so rather than inventing a number",
           "retrieved ? of ? records" in unknown[0])

# The budget is echoed on the first line in both branches, because a reader
# checking the advice needs to see the number the advice is about.
check_true("the ceiling case still echoes the budget it was given",
           "budget max_records=10000" in _coverage_lines(
               dict(esearch_count=50000, pmids_returned=10000, max_records=10000, **base))[0])


# ======================================================================
print("\n5. end to end — it reaches the rendered report")
# ======================================================================


def _author(name: str, email: str = "") -> dict:
    last, fore = name.split()
    return {"name": name, "last": last, "fore": fore, "initials": fore[0],
            "affiliation": "Example University", "email": email,
            "is_corresponding": bool(email), "orcid": "", "equal_contrib": False}


REPORTABLE = [
    {"pmid": str(100 + i), "title": f"Paper {i}", "role": "通讯作者 [EmailOK jane@example.edu]",
     "journal": "Journal of Examples", "doi": f"10.1/{i}",
     "pub_date": f"202{i % 4} Mar", "pub_year": f"202{i % 4}",
     "authors": [_author(f"Trainee Number{i}"), _author("Doe Jane", "jane@example.edu")]}
    for i in range(6)
]

CFG = {"author_name": "Doe Jane", "years_back": 10,
       "author_identity": {"affiliation_keywords": ["Example University"],
                           "email_domains": ["@example.edu"], "orcid": "",
                           "require_affiliation": True}}

SEARCH = {"mindate": "2016/01/01", "maxdate": "2026/01/01", "years_back": 10,
          "retmax": 500, "narrowed_by_affiliation": False,
          "esearch_term": '("Doe Jane"[Author])', "truncated": True,
          "pages_fetched": 20, "duplicates_dropped": 0,
          "fetched": 6, "verified": 6}


def section_body(rep: dict, section_id: int) -> str:
    return "\n".join(next(s for s in rep["sections"] if s["id"] == section_id)["body"])


huge = dict(SEARCH, esearch_matched=50000, pmids_returned=10000, max_records=10000)
rep = build_report(_profile_corpus(REPORTABLE, CFG, huge))
body = section_body(rep, 1)

check("a ceiling-capped corpus is still not refused", rep["refused"], False)
check("...and no gate fires on it", rep["gate"], None)
check_true("the report prints retrieved of matched",
           "retrieved 10000 of 50000 records esearch matched" in body)
check_false("**the rendered report does not recommend raising max_records**",
            "Raise max_records" in body)
check_true("it rules that action out by name",
           "Raising max_records will not recover any of them" in body)
check_true("...and blames the right party", "NCBI's E-utilities" in body)
check_false("the whole markdown document is free of the useless advice",
            "Raise max_records" in render_markdown(rep))

small = dict(SEARCH, esearch_matched=900, pmids_returned=500, max_records=500)
rep = build_report(_profile_corpus(REPORTABLE, CFG, small))
body = section_body(rep, 1)
check_true("a budget-capped corpus still gets the action that works",
           "Raise max_records" in body)
check_false("...and is not told NCBI stopped it",
            "will not recover any of them" in body)


# ======================================================================
print("\n6. the harvest log — the surface the user reads first")
# ======================================================================
# Same two causes, same two recommendations, twenty minutes earlier. Nothing
# below reads the source: a real `search_pubmed` runs against a fake
# E-utilities endpoint and the emitted LogRecord is read back.


class _Capture(logging.Handler):
    """Collects the formatted message of every WARNING the harvest emits."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def harvest_warnings(matched: int, budget: int, ceiling: int = ESEARCH_MAX_RETRIEVABLE,
                     retmax: int = 500) -> list[str]:
    """Run one harvest against a fake NCBI and return its WARNING lines.

    The fake behaves like the documented endpoint in the one way that matters:
    it reports `matched` hits and then hands over nothing past global offset
    `ceiling`, which is what turns a large query into a ceiling-bound harvest.
    """
    def fake_eutils(endpoint, params, timeout, retries=2, min_interval=0.0):
        start = int(params.get("retstart", 0))
        want = int(params.get("retmax", 0))
        ids = [str(900000 + i) for i in range(start, min(start + want, ceiling, matched))]
        return json.dumps({"esearchresult": {"count": str(matched),
                                             "idlist": ids}}).encode()

    handler = _Capture()
    logger = logging.getLogger("check_your_advisor.pubmed")
    logger.addHandler(handler)
    previous_level, logger.level = logger.level, logging.WARNING
    real_get, real_throttle = pubmed_api._eutils_get, pubmed_api._throttle
    pubmed_api._eutils_get = fake_eutils
    pubmed_api._throttle = lambda *a, **kw: None
    try:
        pubmed_api.search_pubmed("Wang Y", 5, "", retmax=retmax,
                                 provenance={}, max_records=budget)
    finally:
        pubmed_api._eutils_get, pubmed_api._throttle = real_get, real_throttle
        logger.removeHandler(handler)
        logger.level = previous_level
    return handler.messages


def shortfall_warning(messages: list[str]) -> str:
    """The one warning that reports the gap, not the one about the empty page."""
    return next((m for m in messages if "只取回" in m), "")


# --- ceiling-bound: 50,000 matched, default budget pinned to the ceiling ------
ceiling_log = shortfall_warning(harvest_warnings(matched=50000, budget=10000))

check_true("the harvest warns about the gap at all", bool(ceiling_log))
check_true("the log states the shortfall as a number over a number",
           "只取回 10000 / 共 50000 条" in ceiling_log)
check_true("...and still says the counts below are floors",
           "报告里的每个计数都是下界" in ceiling_log)
check_false("**the log does not open with the one action that recovers nothing**",
            "下界——调高 max_records、" in ceiling_log)
check_true("it names that action and rules it out",
           "调高 max_records 一条也补不回来" in ceiling_log)
check_true("...says whose limit it is", "NCBI 的接口上限" in ceiling_log)
check_true("...and that it is not a knob in this tool",
           "不是本工具的配置" in ceiling_log)
check_true("...with the number, matching the report's", "10,000 条" in ceiling_log)
check_true("narrowing the query is what the log recommends instead",
           "把检索式收窄" in ceiling_log)
check_true("...by cutting the window", "缩小 years_back" in ceiling_log)
check_true("...or naming the institution / ORCID",
           "补 affiliation_keywords 或 orcid" in ceiling_log)
check_true("...with the year-by-year fallback", "按年份分段重跑" in ceiling_log)

# --- budget-bound: 900 matched, the user's own 500-record budget --------------
budget_log = shortfall_warning(harvest_warnings(matched=900, budget=500))

check_true("the budget-bound harvest also warns", bool(budget_log))
check_true("the log states the shortfall as a number over a number",
           "只取回 500 / 共 900 条" in budget_log)
check_true("**here raising the budget IS recommended**",
           "调高 max_records" in budget_log)
check_true("...and the ceiling it can be raised to is named, which it never was",
           f"最高到 NCBI 的 {ESEARCH_MAX_RETRIEVABLE:,} 条 esearch 硬顶" in budget_log)
check_false("...and nothing claims the budget is futile here",
            "一条也补不回来" in budget_log)

# --- a small budget on a huge query is still budget-bound in the log ----------
mixed_log = shortfall_warning(harvest_warnings(matched=50000, budget=500))
check_true("a 500-record budget on a 50000-hit query is budget-bound in the log",
           "调高 max_records（最高到" in mixed_log)
check_false("...and is not blamed on NCBI", "一条也补不回来" in mixed_log)

# --- a harvest with no gap says nothing ---------------------------------------
check("a complete harvest emits no shortfall warning",
      shortfall_warning(harvest_warnings(matched=120, budget=10000)), "")


# ======================================================================
print("\n7. one cause test and one set of sentences, shared by both surfaces")
# ======================================================================
# The defect was two copies drifting, so the fix is only a fix while there is
# one copy. `_coverage_lines` must be reading the same helper the log reads,
# and the helper must be the only place that decides which cause it is.

check_true("the ceiling case is ceiling-bound", shortfall_is_ceiling_bound(50000, 10000))
check_false("a budget below the ceiling is not", shortfall_is_ceiling_bound(50000, 500))
check_false("a query at the ceiling is not", shortfall_is_ceiling_bound(10000, 10000))
check_true("one record past it is", shortfall_is_ceiling_bound(10001, 10000))
check_true("an unrecorded budget on a huge query is",
           shortfall_is_ceiling_bound(50000, "?"))
check_false("a non-numeric match count decides nothing",
            shortfall_is_ceiling_bound("?", 10000))

# The English sentence the report prints is the same object the helper returns,
# so a report that stopped calling it would fail here rather than drift quietly.
check_true("the report's ceiling text comes from the shared helper",
           coverage_remedy(50000, 10000, "en") in gap_line(
               esearch_count=50000, pmids_returned=10000, max_records=10000, **base))
check_true("the report's budget text comes from the shared helper",
           coverage_remedy(900, 500, "en") in gap_line(
               esearch_count=900, pmids_returned=500, max_records=500, **base))
check_true("the log's ceiling text comes from the shared helper",
           coverage_remedy(50000, 10000, "zh") in ceiling_log)
check_true("the log's budget text comes from the shared helper",
           coverage_remedy(900, 500, "zh") in budget_log)

# Neither log line carries the other cause's advice. This is the defect stated
# as an assertion: the ceiling-bound harvest used to print exactly this text.
check_false("**the ceiling log does not carry the budget advice**",
            coverage_remedy(900, 500, "zh") in ceiling_log)
check_false("...nor the budget log the ceiling advice",
            coverage_remedy(50000, 10000, "zh") in budget_log)

# The two languages are two renderings of one decision, not two decisions.
for matched, budget_arg in ((50000, 10000), (900, 500), (50000, 500), (10001, 10000)):
    zh = coverage_remedy(matched, budget_arg, "zh")
    en = coverage_remedy(matched, budget_arg, "en")
    check(f"matched={matched} budget={budget_arg}: both languages agree on the cause",
          ("补不回来" in zh, "will not recover" in en).count(True) % 2, 0)

# And the report module no longer keeps a private copy of the branch.
report_src = open(_report_module.__file__, encoding="utf-8").read()
check("report.py holds no second copy of the ceiling sentence",
      report_src.count("will not recover any of them"), 0)
check("report.py holds no second copy of the budget sentence",
      report_src.count("Raise max_records ("), 0)
check_true("report.py reads the shared helper instead",
           "coverage_remedy" in report_src)


print(f"\nSummary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
sys.exit(1 if _failed else 0)
