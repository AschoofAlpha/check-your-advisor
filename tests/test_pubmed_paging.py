#!/usr/bin/env python3
"""
Tests for the PubMed harvest: retstart paging, the record budget, and the
shared NCBI rate limit.

Why this exists: `_eutils_get`, `search_pubmed` and `fetch_details` had no
assertions at all. `search_pubmed` issued exactly one data request and stopped,
so a common surname matched 9,000 records, brought back 500, and the profile
report refused with gate G1 — the fix the refusal text suggested ("raise
retmax") did not exist as a flag and could not be raised past NCBI's own
per-request ceiling anyway. Paging removes the refusal, and everything it adds
is off by one in a way a live run would hide: retstart advancing by the deduped
count silently skips records, and a page that comes back empty spins forever.

`test_search_query.py` opens with the note that the last recall bug in this file
was invisible to unit tests and only showed up on a real run. That is precisely
why the fake below answers at the `urlopen` seam rather than at `_eutils_get`:
the URL, the retstart arithmetic, the rate limit and the JSON parse are all
inside the test.

No socket is opened and no real sleep is taken.

Run: python tests/test_pubmed_paging.py
"""

from __future__ import annotations

import json
import os
import sys
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor import pubmed_api  # noqa: E402

_passed = 0
_failed = 0


def check(label: str, actual, expected) -> None:
    global _passed, _failed
    if actual == expected:
        _passed += 1
        print(f"  [PASS] {label}")
    else:
        _failed += 1
        print(f"  [FAIL] {label}  (expected {expected!r}, got {actual!r})")


def check_true(label: str, cond) -> None:
    check(label, bool(cond), True)


def near(wait: float, interval: float) -> bool:
    """One recorded wait, allowing for the elapsed time the throttle subtracts.

    The upper bound is not `interval`: the wait is computed as
    `last + interval - now`, and that subtraction loses enough precision on a
    monotonic clock reading tens of thousands of seconds to land a few
    nanoseconds above the interval it was built from.
    """
    return interval - 0.02 <= wait <= interval + 1e-3


# Never sleep for real: the throttle waits 0.34s before every anonymous request,
# so a 20-page harvest would take seven seconds of wall clock to prove nothing.
# The waits are recorded instead and asserted on directly.
_slept: list[float] = []
pubmed_api.time.sleep = lambda seconds: _slept.append(seconds)


class FakeBody:
    """The context manager `_eutils_get` opens around `urlopen`."""

    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _params(req) -> dict:
    parsed = urlparse(req.full_url)
    out = {key: value[0] for key, value in parse_qs(parsed.query).items()}
    out["_endpoint"] = parsed.path.rsplit("/", 1)[-1]
    return out


class FakeNCBI:
    """esearch that slices its own id list by retstart/retmax, as PubMed does.

    `narrowed` is the second, smaller result set served when the term carries an
    [Affiliation] clause, which is how the automatic narrowing branch is
    exercised without a second fake.
    """

    def __init__(self, pmids, count=None, narrowed=None, narrowed_count=None):
        self.pmids = list(pmids)
        self.count = len(self.pmids) if count is None else count
        self.narrowed = list(narrowed or [])
        self.narrowed_count = len(self.narrowed) if narrowed_count is None else narrowed_count
        self.calls: list[dict] = []

    def __call__(self, req, timeout=None):
        params = _params(req)
        self.calls.append(params)
        is_narrowed = "[Affiliation]" in params.get("term", "")
        pool = self.narrowed if is_narrowed else self.pmids
        total = self.narrowed_count if is_narrowed else self.count
        start = int(params.get("retstart", 0))
        want = int(params.get("retmax", 20))
        page = pool[start:start + want]
        body = json.dumps({"esearchresult": {"count": str(total), "idlist": page}})
        return FakeBody(body.encode("utf-8"))

    @property
    def data_calls(self) -> list[dict]:
        """The paging requests, without the retmax=0 count probes."""
        return [call for call in self.calls if call.get("retmax") != "0"]


class PagedNCBI:
    """esearch that serves a fixed script of pages, whatever retstart asks for.

    Two things PubMed does that a well-behaved slicer cannot reproduce: repeat a
    record across pages when the result set shifts under the harvest, and answer
    with nothing at all past its 10,000-record ceiling.
    """

    def __init__(self, count, pages):
        self.count = count
        self.pages = list(pages)
        self.calls: list[dict] = []

    def __call__(self, req, timeout=None):
        params = _params(req)
        self.calls.append(params)
        if params.get("retmax") == "0":
            page: list[str] = []
        else:
            page = self.pages.pop(0) if self.pages else []
        body = json.dumps({"esearchresult": {"count": str(self.count), "idlist": page}})
        return FakeBody(body.encode("utf-8"))


def ids(start: int, stop: int) -> list[str]:
    return [str(n) for n in range(start, stop)]


def harvest(fake, **kwargs) -> tuple[list[str], dict]:
    """Run one search against a fake endpoint and return (pmids, provenance)."""
    pubmed_api.urlopen = fake
    prov: dict = {}
    result = pubmed_api.search_pubmed(
        kwargs.pop("author", "Doe Jane"), kwargs.pop("years_back", 5),
        kwargs.pop("api_key", ""), provenance=prov, **kwargs,
    )
    return result, prov


# ======================================================================
print("\n--- one page is enough ---")
# ======================================================================
fake = FakeNCBI(ids(0, 40))
found, prov = harvest(fake, retmax=500)

check("every matched record comes back", found, ids(0, 40))
check("one count probe plus one data page", len(fake.calls), 2)
check("the probe asks for no records", fake.calls[0]["retmax"], "0")
check("the data page starts at 0", fake.data_calls[0]["retstart"], "0")
check("it is one page", prov["pages_fetched"], 1)
check("nothing was dropped as a duplicate", prov["duplicates_dropped"], 0)
check("the two numbers the report prints agree",
      (prov["pmids_returned"], prov["esearch_matched"]), (40, 40))
check("so the corpus is not truncated", prov["truncated"], False)
check("the budget is recorded for the report", prov["max_records"], pubmed_api.MAX_RECORDS)


# ======================================================================
print("\n--- more matches than one page holds ---")
# ======================================================================
# This is the case that used to end at gate G1: 250 matched, 100 per page.
fake = FakeNCBI(ids(0, 250))
found, prov = harvest(fake, retmax=100)

check("all 250 records are retrieved, in order", found, ids(0, 250))
check("across three pages", prov["pages_fetched"], 3)
check("retstart advances by the page actually returned",
      [call["retstart"] for call in fake.data_calls], ["0", "100", "200"])
check("the last page asks only for what is left",
      [call["retmax"] for call in fake.data_calls], ["100", "100", "50"])
check("the report's numerator and denominator match",
      (prov["pmids_returned"], prov["esearch_matched"]), (250, 250))
check("and nothing is reported as missing", prov["truncated"], False)


# ======================================================================
print("\n--- nothing matched ---")
# ======================================================================
fake = FakeNCBI([], count=0)
found, prov = harvest(fake, retmax=500)

check("an empty result set is an empty list", found, [])
check("no data page is fetched at all", fake.data_calls, [])
check("the probe still happened", len(fake.calls), 1)
check("zero pages are reported", prov["pages_fetched"], 0)
check("0 of 0 is not a truncation", prov["truncated"], False)
check("both numbers are a real 0, not a '?'",
      (prov["pmids_returned"], prov["esearch_matched"]), (0, 0))

# The other empty case: no author name and no ORCID. It must not reach the
# network at all, and must still record the pair of numbers the report prints.
pubmed_api.urlopen = FakeNCBI(ids(0, 10))
prov = {}
check("a nameless search returns nothing",
      pubmed_api.search_pubmed("", 5, "", provenance=prov), [])
check("...without a single request", pubmed_api.urlopen.calls, [])
check("...and still records the coverage pair",
      (prov["pmids_returned"], prov["esearch_matched"], prov["pages_fetched"]), (0, 0, 0))


# ======================================================================
print("\n--- the budget stops a very common name ---")
# ======================================================================
fake = FakeNCBI(ids(0, 4000))
found, prov = harvest(fake, retmax=100, max_records=250)

check("the harvest stops at the budget", len(found), 250)
check("having fetched exactly the pages that fill it", prov["pages_fetched"], 3)
check("no page is requested past the budget",
      [call["retstart"] for call in fake.data_calls], ["0", "100", "200"])
check("the report is told how many were retrieved", prov["pmids_returned"], 250)
check("...and out of how many", prov["esearch_matched"], 4000)
check("...and that the corpus is incomplete", prov["truncated"], True)
check("...and what the budget was", prov["max_records"], 250)

# The budget is a stopping rule, not a gate: `search_pubmed` returns the records
# it did get rather than raising, and the caller gets a usable corpus.
check("the retrieved records are the first 250, in order", found, ids(0, 250))

# With affiliation keywords configured, a result set past the budget is narrowed
# server-side first. The trigger is the budget, not the page size: 900 matches
# at retmax=100 pages cleanly and must not touch the query.
fake = FakeNCBI(ids(0, 900))
found, prov = harvest(fake, retmax=100, max_records=1000,
                      identity={"affiliation_keywords": ["Example University"]})
check("a result set inside the budget is never narrowed",
      prov["narrowed_by_affiliation"], False)
check("...and is retrieved whole", len(found), 900)

fake = FakeNCBI(ids(0, 4000), narrowed=ids(9000, 9030))
found, prov = harvest(fake, retmax=100, max_records=1000,
                      identity={"affiliation_keywords": ["Example University"]})
check("past the budget the query is narrowed", prov["narrowed_by_affiliation"], True)
check("...and the narrowed set is retrieved whole", found, ids(9000, 9030))
check("...with the broad count kept for the record", prov["broad_matched"], 4000)
check("...and the denominator now describes the narrowed query",
      prov["esearch_matched"], 30)


# ======================================================================
print("\n--- what PubMed does that a clean slice does not ---")
# ======================================================================
# The same PMID on two pages. Paging re-runs the query, so a record entering the
# index mid-harvest shifts the result set. A repeat must be dropped here: two
# copies would be fetched twice, parsed into two papers, inflate fetched and
# verified, and only be caught at profile time by roles.py — where it reads as
# PubMed's fault.
fake = PagedNCBI(300, [ids(0, 100), ids(90, 190), ids(190, 300)])
found, prov = harvest(fake, retmax=100)

check("repeated PMIDs are dropped, not carried", len(found), len(set(found)))
check("the drop is counted for the report", prov["duplicates_dropped"], 10)
# 190 records had been kept when the third page was requested, and it was
# requested from 200. Advancing by the deduped count instead would have re-read
# records 190-199 and left the ten at the far end of the set unreachable.
check("retstart followed the raw page length, not the deduped count",
      [call["retstart"] for call in fake.calls if call.get("retmax") != "0"],
      ["0", "100", "200"])
check("so no record was skipped", found, ids(0, 300))

# A page that comes back empty ends the harvest. Without this the loop spins on
# the same retstart forever, which is what NCBI's 10,000-record ceiling on
# esearch would otherwise cause on any name matching more than that.
fake = PagedNCBI(5000, [ids(0, 100), []])
found, prov = harvest(fake, retmax=100, max_records=5000)

check("an empty page ends the harvest", len(found), 100)
check("after two pages, not five thousand", prov["pages_fetched"], 2)
check("and the shortfall is reported honestly",
      (prov["pmids_returned"], prov["esearch_matched"], prov["truncated"]),
      (100, 5000, True))


# ======================================================================
print("\n--- the rate limit is one limit, shared ---")
# ======================================================================
# NCBI allows 3 requests per second without an API key and 10 with one
# (E-utilities documentation, retrieved 2026-08-22). Before paging, the harvest
# had no active throttle at all: the only sleep on the whole path was
# fetch_details' 0.15s between batches, which is 6.7/s and already over the
# anonymous limit.
_slept.clear()
harvest(FakeNCBI(ids(0, 250)), retmax=100)
anonymous = list(_slept)
# Four requests, four waits — including the first. The throttle's last-request
# time is module state, not per-search state, because NCBI counts per IP: two
# searches back to back are the same quota as one.
check("one probe plus three pages, every one of them throttled", len(anonymous), 4)
check_true("...at the anonymous interval",
           all(near(wait, pubmed_api._MIN_INTERVAL_NO_KEY) for wait in anonymous))

_slept.clear()
harvest(FakeNCBI(ids(0, 250)), retmax=100, api_key="deadbeef")
keyed = list(_slept)
check("an API key still throttles", len(keyed), 4)
check_true("...but at the higher rate",
           all(near(wait, pubmed_api._MIN_INTERVAL_WITH_KEY) for wait in keyed))
check_true("the key travels with the request",
           all(call.get("api_key") == "deadbeef" for call in pubmed_api.urlopen.calls))


# ======================================================================
print("\n--- efetch shares that same limit ---")
# ======================================================================
ARTICLE = (
    "<PubmedArticle><MedlineCitation><PMID>{pmid}</PMID><Article>"
    "<ArticleTitle>Title {pmid}</ArticleTitle><Journal><Title>J</Title></Journal>"
    "</Article></MedlineCitation></PubmedArticle>"
)


class FakeEfetch:
    def __init__(self):
        self.batches: list[list[str]] = []

    def __call__(self, req, timeout=None):
        params = _params(req)
        batch = params["id"].split(",")
        self.batches.append(batch)
        body = "<PubmedArticleSet>" + "".join(
            ARTICLE.format(pmid=pmid) for pmid in batch
        ) + "</PubmedArticleSet>"
        return FakeBody(body.encode("utf-8"))


fake_efetch = FakeEfetch()
pubmed_api.urlopen = fake_efetch
_slept.clear()
papers = pubmed_api.fetch_details(ids(0, 120), api_key="", delay=0.5)

check("every PMID comes back parsed", len(papers), 120)
check("in batches of 50", [len(batch) for batch in fake_efetch.batches], [50, 50, 20])
check("one wait per efetch request", len(_slept), 3)
# The point of routing `delay` into the throttle: fetch_details used to sleep
# `delay` itself, and would now sleep the throttle interval on top, so the
# actual request rate was the sum of two independent waits and could not be
# computed from either. A wait above the floor here means both fired.
check_true("the caller's delay is honoured as a floor, and applied once",
           all(near(wait, 0.5) for wait in _slept))

_slept.clear()
pubmed_api.fetch_details(ids(0, 60), api_key="", delay=0.0)
check_true("a zero delay falls back to the NCBI interval, never to no wait",
           _slept and all(near(wait, pubmed_api._MIN_INTERVAL_NO_KEY) for wait in _slept))

check("an empty PMID list fetches nothing", pubmed_api.fetch_details([], ""), [])


print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(0 if _failed == 0 else 1)
