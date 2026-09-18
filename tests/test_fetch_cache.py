#!/usr/bin/env python3
"""
`cache.FetchCache`: a dated store of what a keyless API answered.

`PaperCache` next to it caches PDF *files*, keyed on PMID, and a hit is checked
against the filesystem. This one caches what an API *said*, keyed on which API
was asked and what it was asked about, and a hit is checked against a date. The
two share a file and nothing else.

The rules these assertions hold, in order of how badly it would hurt to lose one:

  1. **A hit carries the day it was really collected.** `get` hands back the
     `fetched_at` that was written when the answer arrived, never today's. Every
     record in this package travels with its own collection date; a cache that
     restamped a hit would turn that date into a lie the moment it worked.
  2. **One source's answer is never served as another's.** The key is
     (api, query). OpenAlex's answer about a DOI and Semantic Scholar's answer
     about the same DOI are two rows, and `journal_risk.openalex` and
     `citations.openalex` are two namespaces even though both mean one host.
  3. **Expiry is `--max-age-days`, not a second clock.** The same three rules
     `citations.reusable_records` states: `<= 0` reuses nothing, a stamp older
     than the cutoff is not reusable, and a stamp that will not parse is not
     reusable either.

Fully offline; no pytest; SQLite only, which is standard library.

Run: python tests/test_fetch_cache.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor import citations  # noqa: E402
from check_your_advisor.cache import FetchCache  # noqa: E402

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


def check_true(label: str, actual) -> None:
    check(label, bool(actual), True)


def check_false(label: str, actual) -> None:
    check(label, bool(actual), False)


ROOT = tempfile.mkdtemp(prefix="cya-fetchcache-")
NOW = datetime(2026, 9, 17, 12, 0, 0)


def stamp(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")


# ============================================================
# 1. Storing and serving one answer
# ============================================================

print("one answer in, one answer out")

cache = FetchCache(":memory:")

check("an empty cache is a miss, not an error",
      cache.get("citations.openalex", "doi:10.1/x", 30, now=NOW), None)

written = cache.put("citations.openalex", "doi:10.1/x", 42, fetched_at=stamp(1))
check("put reports the stamp it wrote, so the caller can date its record",
      written, stamp(1))

hit = cache.get("citations.openalex", "doi:10.1/x", 30, now=NOW)
check("a stored answer comes back", hit["value"], 42)
check("...carrying the day it was really collected, not today",
      hit["fetched_at"], stamp(1))
check("a hit has exactly the two keys a caller needs", sorted(hit), ["fetched_at", "value"])

# JSON is the storage format, so anything a keyless API returns has to survive it.
cache.put("journal_risk.doaj", "issn:0168-8278",
          [{"signal": "doaj_not_indexed", "observed": {"total": 0}}])
check("a list of signal dicts round-trips unchanged",
      cache.get("journal_risk.doaj", "issn:0168-8278", 30, now=NOW)["value"],
      [{"signal": "doaj_not_indexed", "observed": {"total": 0}}])

# Zero is a real citation count and None is a real "field was null". Neither may
# come back looking like the cache had nothing.
cache.put("citations.openalex", "doi:10.1/zero", 0)
zero = cache.get("citations.openalex", "doi:10.1/zero", 30, now=NOW)
check("a cached 0 is a hit whose value is 0, not a miss", (zero is not None, zero["value"]),
      (True, 0))
cache.put("citations.openalex", "doi:10.1/null", None)
null = cache.get("citations.openalex", "doi:10.1/null", 30, now=NOW)
check("a cached null is a hit whose value is None, not a miss",
      (null is not None, null["value"]), (True, None))

# Without an explicit stamp the cache dates the row itself, in the same format
# every record on disk uses — `datetime.isoformat(timespec="seconds")`.
auto = cache.put("citations.europe_pmc", "pmid:1", 3)
check("an unstamped put is dated now, in the format the records use",
      auto, datetime.fromisoformat(auto).isoformat(timespec="seconds"))
check_true("...and that is today", auto.startswith(datetime.now().strftime("%Y-%m-%d")))


# ============================================================
# 2. The key is (api, query) — no source pollutes another
# ============================================================

print("\nno source is served another source's answer")

cache = FetchCache(":memory:")
cache.put("citations.openalex", "doi:10.1/x", 42, fetched_at=stamp(1))

check("the same query under a different api is a miss",
      cache.get("citations.semantic_scholar", "doi:10.1/x", 30, now=NOW), None)
check("the same api with a different query is a miss",
      cache.get("citations.openalex", "doi:10.1/y", 30, now=NOW), None)

# Both modules talk to api.openalex.org, about different things, with different
# response shapes. One namespace for both would hand a citation count to the
# journal-risk path.
cache.put("journal_risk.openalex", "issn:0168-8278", [{"signal": "openalex_in_doaj"}],
          fetched_at=stamp(1))
check("citations' OpenAlex row and journal_risk's are two different rows",
      (cache.get("citations.openalex", "doi:10.1/x", 30, now=NOW)["value"],
       cache.get("journal_risk.openalex", "issn:0168-8278", 30, now=NOW)["value"]),
      (42, [{"signal": "openalex_in_doaj"}]))

# Same ISSN, three sources, three different answers — the case the key exists for.
for api, value in (("journal_risk.doaj", "d"), ("journal_risk.crossref", "c"),
                   ("journal_risk.openalex", "o")):
    cache.put(api, "issn:1111-2222", value)
check("three sources asked about one ISSN keep three separate answers",
      [cache.get(api, "issn:1111-2222", 30, now=NOW)["value"]
       for api in ("journal_risk.doaj", "journal_risk.crossref", "journal_risk.openalex")],
      ["d", "c", "o"])

# A re-put replaces the row rather than accumulating rows, so the table is bounded
# by how many distinct things were ever asked about.
cache.put("citations.openalex", "doi:10.1/x", 99, fetched_at=stamp(0))
check("a re-put overwrites the answer",
      cache.get("citations.openalex", "doi:10.1/x", 30, now=NOW)["value"], 99)
check("...and its date", cache.get("citations.openalex", "doi:10.1/x", 30, now=NOW)["fetched_at"],
      stamp(0))
check("...without leaving the old row behind", cache.stats()["rows"], 5)


# ============================================================
# 3. Expiry is --max-age-days, the one this package already has
# ============================================================

print("\nexpiry follows --max-age-days and invents no second clock")

cache = FetchCache(":memory:")
cache.put("citations.openalex", "doi:10.1/x", 42, fetched_at=stamp(10))

check("max_age_days=0 serves nothing, which is the default everywhere",
      cache.get("citations.openalex", "doi:10.1/x", 0, now=NOW), None)
check("a negative max_age_days serves nothing either",
      cache.get("citations.openalex", "doi:10.1/x", -1, now=NOW), None)
check("...and that is the same answer citations.reusable_records gives at 0",
      citations.reusable_records(ROOT, 0, now=NOW)["records"], {})

check_true("a row inside the window is served",
           cache.get("citations.openalex", "doi:10.1/x", 30, now=NOW) is not None)
check("a row older than the window is not",
      cache.get("citations.openalex", "doi:10.1/x", 5, now=NOW), None)
check_true("the boundary day itself is still inside, as in reusable_records",
           cache.get("citations.openalex", "doi:10.1/x", 10, now=NOW) is not None)

# Time moves; the row does not. Same row, same max_age_days, a later `now`.
check("the same row goes stale as now advances",
      cache.get("citations.openalex", "doi:10.1/x", 30, now=NOW + timedelta(days=25)), None)

# A stamp whose age cannot be established is not a stamp whose age is acceptable.
cache.put("citations.openalex", "doi:10.1/bad", 7, fetched_at="sometime last week")
check("an unparseable stamp is a miss, never 'recent enough'",
      cache.get("citations.openalex", "doi:10.1/bad", 3650, now=NOW), None)
cache.put("citations.openalex", "doi:10.1/empty", 7, fetched_at="")
check("an empty stamp is a miss too",
      cache.get("citations.openalex", "doi:10.1/empty", 3650, now=NOW), None)

check("now defaults to the wall clock rather than raising",
      cache.get("citations.openalex", "doi:10.1/x", 1), None)


# ============================================================
# 4. It is a cache: it survives the process that filled it
# ============================================================

print("\nit outlives the run that filled it")

db = os.path.join(ROOT, "sub", "paper_cache.db")
first = FetchCache(db)
first.put("citations.openalex", "doi:10.1/x", 42, fetched_at=stamp(1))
first.close()
check_true("the database file is created, parent directory and all", os.path.exists(db))

second = FetchCache(db)
carried = second.get("citations.openalex", "doi:10.1/x", 30, now=NOW)
check("a new process reads what the last one wrote", carried["value"], 42)
check("...with the original collection date intact, not the new run's",
      carried["fetched_at"], stamp(1))
second.close()

# PaperCache owns `paper_cache` in the same file. Opening both must not have
# either drop the other's table.
from check_your_advisor.cache import PaperCache  # noqa: E402

papers = PaperCache(db)
papers.update("34265844", doi="10.1/x", status="downloaded", pdf_path="", source="x")
papers.close()
third = FetchCache(db)
check("PaperCache sharing the file leaves the fetch rows alone",
      third.get("citations.openalex", "doi:10.1/x", 30, now=NOW)["value"], 42)
third.close()
with sqlite3.connect(db) as conn:
    tables = sorted(r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall())
check("...because they are two tables in one file", tables, ["fetch_cache", "paper_cache"])


# ============================================================
# 5. A damaged row costs one lookup, never the run
# ============================================================

print("\na damaged row costs one lookup")

db2 = os.path.join(ROOT, "broken.db")
broken = FetchCache(db2)
broken.put("citations.openalex", "doi:10.1/x", 42)
broken.close()
with sqlite3.connect(db2) as conn:
    conn.execute("UPDATE fetch_cache SET payload = ? WHERE query = ?",
                 ("{not json", "doi:10.1/x"))
    conn.commit()
reopened = FetchCache(db2)
check("a payload that will not decode is a miss rather than an exception",
      reopened.get("citations.openalex", "doi:10.1/x", 30, now=NOW), None)
reopened.close()


# ============================================================
# 6. Counters, for the line a run prints at the end
# ============================================================

print("\nwhat the cache can report about itself")

cache = FetchCache(":memory:")
check("a fresh cache reports itself empty",
      cache.stats(), {"rows": 0, "hits": 0, "misses": 0})

cache.put("citations.openalex", "doi:10.1/x", 42)
cache.get("citations.openalex", "doi:10.1/x", 30)      # hit
cache.get("citations.openalex", "doi:10.1/y", 30)      # miss
cache.get("citations.openalex", "doi:10.1/x", 0)       # cache off: neither
check("hits and misses are counted, and a disabled lookup is neither",
      cache.stats(), {"rows": 1, "hits": 1, "misses": 1})
cache.close()



# ----------------------------------------------------------------------
# 5. The wiring, which is the part a user actually receives.
#
# Everything above tests the store. None of it would fire if `cmd_cite` and
# `cmd_journal_risk` never built one — the class would pass its own suite in
# full while the shipped command re-fetched everything, and README would say
# there is a cache. That failure has a history in this repository, so the two
# call sites are asserted here rather than trusted.
#
# `cmd_cite` imports `fetch_citations` *inside* the function, so replacing the
# module attribute before the call is enough; no network is reached.
# ----------------------------------------------------------------------
print("\n[wiring] the subcommands build a cache and hand it down")

import json as _json                                    # noqa: E402
import tempfile                                         # noqa: E402

from check_your_advisor import citations as _citations   # noqa: E402
from check_your_advisor import cli as _cli               # noqa: E402
from check_your_advisor import journal_risk as _risk     # noqa: E402

_seen: dict[str, Any] = {}


def _record_citations(papers, **kwargs):
    _seen.update(kwargs)
    return {"records": [], "denominator": {"papers_total": 0, "papers_with_citations": 0},
            "source_papers_json": kwargs.get("source_papers_json", "")}


def _corpus_dir() -> str:
    """An output dir holding one papers_*.json, which is all these commands read."""
    directory = tempfile.mkdtemp()
    paper = {"pmid": "1", "doi": "10.1/x", "title": "T", "journal": "J",
             "issn": "1234-5678", "pub_year": "2024", "pub_date": "2024 Jan",
             "authors": [], "authors_str": ""}
    with open(os.path.join(directory, "papers_20240101_000000.json"),
              "w", encoding="utf-8") as handle:
        _json.dump({"papers": [paper], "search": {}}, handle)
    return directory


_blank_cfg = os.path.join(tempfile.mkdtemp(), "config.json")
with open(_blank_cfg, "w", encoding="utf-8") as _handle:
    _json.dump({}, _handle)

_real_fetch_citations = _citations.fetch_citations
_citations.fetch_citations = _record_citations
try:
    _dir = _corpus_dir()
    _cli.cmd_cite(["--config", _blank_cfg, "--output-dir", _dir, "--max-age-days", "17"])
finally:
    _citations.fetch_citations = _real_fetch_citations

check_true("cmd_cite hands fetch_citations a cache", _seen.get("cache") is not None)
check("...and it is a FetchCache", type(_seen.get("cache")).__name__, "FetchCache")
check("...and --max-age-days reaches it unchanged", _seen.get("max_age_days"), 17)

_seen.clear()


def _record_risk(targets, **kwargs):
    _seen.update(kwargs)
    return {"records": [], "denominator": {"journals_total": 0, "journals_with_signals": 0},
            "source_papers_json": kwargs.get("source_papers_json", "")}


_real_fetch_risk = _risk.fetch_journal_risk
_risk.fetch_journal_risk = _record_risk
try:
    _dir = _corpus_dir()
    _cli.cmd_journal_risk(["--config", _blank_cfg, "--output-dir", _dir,
                           "--max-age-days", "23"])
finally:
    _risk.fetch_journal_risk = _real_fetch_risk

check_true("cmd_journal_risk hands fetch_journal_risk a cache", _seen.get("cache") is not None)
check("...and it is a FetchCache", type(_seen.get("cache")).__name__, "FetchCache")
check("...and --max-age-days reaches it unchanged", _seen.get("max_age_days"), 23)



# ----------------------------------------------------------------------
# 6. `cite --percentile`, wired in the same place and asserted the same way.
#
# The first version of this wiring read `payload["statuses"]` as a mapping of
# counts and called `.items()` on it. It is a list of status *names*; the counts
# live in `denominator["by_status"]`. Nothing failed at import, nothing failed in
# `impact_reference`'s own 210 assertions, and the command would have raised
# AttributeError on the first real corpus. These assertions are what would have
# caught it.
# ----------------------------------------------------------------------
print("\n[wiring] cite --percentile locates counts in a reference cell")

from check_your_advisor import impact_reference as _impact  # noqa: E402

_located: dict[str, Any] = {}


def _record_locate(papers, client=None, source_papers_json="", mailto=""):
    _located["papers"] = list(papers)
    return {
        "statuses": list(_impact.STATUS_ORDER),
        "status_reasons": dict(_impact.STATUS_REASONS),
        "denominator": {"papers_total": len(papers), "papers_located": 0,
                        "by_status": {"no_citation_count": len(papers)}},
        "records": [], "cells": [], "caveats": {},
    }


_saved: dict[str, Any] = {}


def _record_save(payload, output_dir, timestamp=None):
    _saved["payload"] = payload
    _saved["dir"] = output_dir
    return os.path.join(output_dir, "impact_reference_20240101_000000.json")


_real_locate = _impact.locate_corpus
_real_save = _impact.save_impact_reference_json
_real_fetch_citations = _citations.fetch_citations
_citations.fetch_citations = _record_citations
_impact.locate_corpus = _record_locate
_impact.save_impact_reference_json = _record_save
try:
    _dir = _corpus_dir()
    _code = _cli.cmd_cite(["--config", _blank_cfg, "--output-dir", _dir, "--percentile"])
finally:
    _citations.fetch_citations = _real_fetch_citations
    _impact.locate_corpus = _real_locate
    _impact.save_impact_reference_json = _real_save

check("cmd_cite --percentile still returns success", _code, 0)
check_true("...and reaches locate_corpus", "papers" in _located)
check("...with the corpus it just fetched counts for", len(_located.get("papers", [])), 1)
check_true("...each paper carrying the field impact_reference reads",
           all(_impact.CITATION_COUNT_FIELD in paper for paper in _located["papers"]))
check_true("...and the payload is written to the output dir", _saved.get("dir") == _dir)

# The join must not mutate the corpus. `papers_*.json` is what harvest wrote; a
# count taken today does not belong inside it.
check_false("the corpus records are not given a citation_count in place",
            any(_impact.CITATION_COUNT_FIELD in paper
                for paper in _json.load(open(
                    os.path.join(_dir, "papers_20240101_000000.json"), encoding="utf-8"))["papers"]))

_seen.clear()
_dir2 = _corpus_dir()
_citations.fetch_citations = _record_citations
try:
    _cli.cmd_cite(["--config", _blank_cfg, "--output-dir", _dir2])
finally:
    _citations.fetch_citations = _real_fetch_citations
check_false("without --percentile nothing is located at all",
            any(name.startswith("impact_reference_") for name in os.listdir(_dir2)))


print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(1 if _failed else 0)
