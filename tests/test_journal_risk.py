#!/usr/bin/env python3
"""
Tests for `journal_risk`: public risk signals, and the verdict it refuses to give.

Every HTTP call is faked. The client seam is the same one `citations.py` uses — a
`RobustHTTPClient` is passed in, so a stub with a `.get()` reaches every branch
without a socket. `test_citations.py` is the model for the fixture style.

What these protect, in order of how badly it would hurt to lose it:

  1. **No verdict, at any number of signals.** Nothing here produces a grade, a
     tier, a score, a letter or a colour, and no signal reaches the composite
     score. That is the difference between this feature and the thing it would
     be trivial and wrong to turn into.
  2. **Every source is queried and none wins.** DOAJ, Crossref and OpenAlex
     answer different questions and disagree; the disagreement is shown.
  3. **Four absences stay four absences.** Never collected, no ISSN to collect
     with, collected-but-this-journal-missing, and collected-and-silent are four
     different cells, and collapsing any two would print "we did not look" as
     "we looked and found nothing".
  4. **A dated measurement never merges into an undated one.** Its own file, its
     own per-record timestamp, and nothing written back into the corpus.

Fully offline: no network, no pytest.

Run: python tests/test_journal_risk.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor import journal_risk, journals  # noqa: E402

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


ROOT = tempfile.mkdtemp(prefix="cya-risk-")
HEP = "0168-8278"
NAN = "2049-3258"


# ============================================================
# Fixtures — a client whose .get() returns canned bodies
# ============================================================


class FakeResponse:
    def __init__(self, status_code: int, payload) -> None:
        self.status_code = status_code
        self.content = (payload if isinstance(payload, bytes)
                        else json.dumps(payload).encode("utf-8"))


class FakeClient:
    """Stands in for RobustHTTPClient. Records every URL it was asked for."""

    def __init__(self, routes: dict) -> None:
        self.routes = routes
        self.seen: list[str] = []
        self.stats = {"total_requests": 0}

    def get(self, url, **kwargs):
        self.seen.append(url)
        self.stats["total_requests"] += 1
        for fragment, response in self.routes.items():
            if fragment in url:
                return response
        return FakeResponse(404, {})


DOAJ_HIT = FakeResponse(200, {
    "total": 1,
    "results": [{"bibjson": {
        "editorial": {"review_process": ["Double anonymous peer review"]},
        "apc": {"has_apc": True, "max": [{"price": 2500, "currency": "USD"}]},
    }}],
})
DOAJ_MISS = FakeResponse(200, {"total": 0, "results": []})
CROSSREF_HIT = FakeResponse(200, {"message": {
    "publisher": "Elsevier",
    "counts": {"total-dois": 18422},
    "coverage": {
        "abstracts-current": 0.0,
        "references-current": 0.98,
        "licenses-current": 1.0,
        "funders-current": 0.0,
        "orcids-current": 0.41,
        "update-policies-current": 1.0,
        "similarity-checking-current": 1.0,
        "resource-links-current": 1.0,
        "award-numbers-current": 0.0,
        "ror-ids-current": 0.0,
    },
}})
CROSSREF_MISS = FakeResponse(404, {})
OPENALEX_HIT = FakeResponse(200, {
    "id": "https://openalex.org/S123",
    "is_in_doaj": False,
    "is_indexed_in_scopus": None,
    "works_count": 18422,
    "host_organization_name": "Elsevier BV",
    "country_code": "NL",
})


# ============================================================
# 1. The boundary this module exists to hold
# ============================================================

print("no verdict, at any number of signals")

names = [n for n in dir(journal_risk) if not n.startswith("_")]
for banned in ("predatory", "grade", "tier", "rating", "score", "verdict", "rank"):
    check(f"the module exposes nothing called *{banned}*",
          [n for n in names if banned in n.lower()], [])

source = open(journal_risk.__file__, encoding="utf-8").read()
check_true("the module says outright that predatory is an accusation, not a measurement",
           "an accusation, not a measurement" in source)
check("five caveats, in the register style journals.JOURNAL_CAVEATS uses",
      sorted(journal_risk.JOURNAL_RISK_CAVEATS),
      [f"JRN-{i:02d}" for i in range(8, 13)])
check("...continuing that register's numbering without colliding with it",
      sorted(set(journal_risk.JOURNAL_RISK_CAVEATS) & set(journals.JOURNAL_CAVEATS)), [])
check("every caveat is a paragraph, not a label",
      [k for k, v in journal_risk.JOURNAL_RISK_CAVEATS.items() if len(v) < 120], [])
check_true("JRN-08 refuses the word and says why",
           "does not call any journal predatory"
           in journal_risk.JOURNAL_RISK_CAVEATS["JRN-08"].replace("Nothing in this block calls",
                                                                  "does not call"))
check_true("...and says no grade is produced at any number of signals",
           "at any number of them" in journal_risk.JOURNAL_RISK_CAVEATS["JRN-08"])
check_true("...and that none of it reaches the composite score",
           "composite score" in journal_risk.JOURNAL_RISK_CAVEATS["JRN-08"])
check_true("JRN-09 says absence from DOAJ is not a finding",
           "not a finding" in journal_risk.JOURNAL_RISK_CAVEATS["JRN-09"])
check_true("JRN-10 names the real journal whose Crossref field reads zero",
           "Journal of Hepatology" in journal_risk.JOURNAL_RISK_CAVEATS["JRN-10"])
check_true("JRN-11 says the CAS warning list has no endpoint and stays hand-filled",
           "是否预警" in journal_risk.JOURNAL_RISK_CAVEATS["JRN-11"]
           and "no JSON or CSV endpoint" in journal_risk.JOURNAL_RISK_CAVEATS["JRN-11"])
check_true("...and that a blank cell means nobody checked",
           "means nobody checked" in journal_risk.JOURNAL_RISK_CAVEATS["JRN-11"])
check_true("JRN-12 says the sources disagree and none is preferred",
           "neither is preferred" in journal_risk.JOURNAL_RISK_CAVEATS["JRN-12"])

# journals.py stays network-free. This module exists precisely so it can.
journals_source = open(journals.__file__, encoding="utf-8").read()
for banned in ("import requests", "urllib.request", "urlopen", "RobustHTTPClient"):
    check(f"journals.py still contains no `{banned}`", banned in journals_source, False)


# ============================================================
# 2. The three sources
# ============================================================

print("\nDOAJ")

client = FakeClient({"doaj.org": DOAJ_MISS})
miss = journal_risk.fetch_doaj_journal(client, HEP)
check("a journal DOAJ does not hold produces exactly one signal", len(miss), 1)
check("...named as not indexed", miss[0]["signal"], journal_risk.SIGNAL_DOAJ_NOT_INDEXED)
check_true("...whose statement is the Chinese phrase the report prints",
           miss[0]["statement"].startswith("未被 DOAJ 收录"))
check_true("...and which immediately says this is not a finding",
           "is not a finding about it" in miss[0]["statement"])
check("...and which names the endpoint it came from", miss[0]["source"], "doaj")

client = FakeClient({"doaj.org": DOAJ_HIT})
hit = {s["signal"]: s for s in journal_risk.fetch_doaj_journal(client, HEP)}
check("a journal DOAJ holds is reported as indexed",
      journal_risk.SIGNAL_DOAJ_INDEXED in hit, True)
check_true("...and membership is stated as membership, not as a certificate",
           "not a quality certificate" in hit[journal_risk.SIGNAL_DOAJ_INDEXED]["statement"])
check_true("an APC is reported with its amount", "2500 USD" in hit[journal_risk.SIGNAL_DOAJ_APC]["statement"])
check_true("...and immediately says an APC is not on its own a warning sign",
           "not on its own a warning sign" in hit[journal_risk.SIGNAL_DOAJ_APC]["statement"])
check_true("the declared review process is reported as declared, not verified",
           "Declared, not verified" in hit[journal_risk.SIGNAL_DOAJ_REVIEW]["statement"])

# A transport failure is not an answer. "Nobody asked successfully" and "asked,
# and the answer was no" must never collapse into one cell.
check("an unreachable DOAJ returns no signal at all, rather than 'not indexed'",
      journal_risk.fetch_doaj_journal(FakeClient({"doaj.org": FakeResponse(503, {})}), HEP), [])
check("...and so does a body that is not JSON",
      journal_risk.fetch_doaj_journal(
          FakeClient({"doaj.org": FakeResponse(200, b"<html>rate limited</html>")}), HEP), [])
check("...and so does a record with no ISSN to ask about",
      journal_risk.fetch_doaj_journal(FakeClient({}), ""), [])

print("\nCrossref")

client = FakeClient({"api.crossref.org": CROSSREF_HIT})
cross = {s["signal"]: s for s in journal_risk.fetch_crossref_journal(client, HEP)}
coverage = cross[journal_risk.SIGNAL_CROSSREF_COVERAGE]
check("four of the ten tracked fields are at zero in the fixture",
      coverage["observed"]["missing"], 4)
check("...counted against the number actually present, which is the denominator",
      coverage["observed"]["tracked"], len(journal_risk.TRACKED_COVERAGE_FIELDS))
check_true("...and the statement carries both numbers, in Chinese and in English",
           "Crossref 元数据缺失 4 项（共查 10 项）" in coverage["statement"]
           and "4 of 10 tracked" in coverage["statement"])
check("...and names which fields, so the count can be checked",
      coverage["observed"]["missing_fields"],
      ["abstracts-current", "award-numbers-current", "funders-current", "ror-ids-current"])
check_true("...and says what the number measures and what it does not",
           "not what the journal is worth" in coverage["statement"])
check_true("the deposited DOI count is reported with the publisher beside it",
           "18422" in cross[journal_risk.SIGNAL_CROSSREF_DOIS]["statement"]
           and "Elsevier" in cross[journal_risk.SIGNAL_CROSSREF_DOIS]["statement"])
check("only current-content fields are tracked — backfile measures a migration",
      [f for f in journal_risk.TRACKED_COVERAGE_FIELDS if not f.endswith("-current")], [])

client = FakeClient({"api.crossref.org": FakeResponse(200, {"status": "ok"})})
unknown = journal_risk.fetch_crossref_journal(client, HEP)
check("a Crossref response with no journal record says so", len(unknown), 1)
check("...as its own signal", unknown[0]["signal"], journal_risk.SIGNAL_CROSSREF_UNKNOWN)
check_true("...and says it is not a statement about the journal's standing",
           "not a statement about the journal's standing" in unknown[0]["statement"])

print("\nOpenAlex")

client = FakeClient({"api.openalex.org": OPENALEX_HIT})
alex = {s["signal"]: s for s in journal_risk.fetch_openalex_source(client, HEP)}
check_true("OpenAlex's own DOAJ flag is reported as OpenAlex's, not as DOAJ's",
           "OpenAlex's copy can lag" in alex[journal_risk.SIGNAL_OPENALEX_DOAJ]["statement"])
# The single most dangerous coercion available here: OpenAlex returns null for
# is_indexed_in_scopus on a great many real journals, and printing that as "not
# indexed" invents a finding out of a missing field.
check_true("a null Scopus flag is printed as null, never as 'not indexed'",
           "the field is null" in alex[journal_risk.SIGNAL_OPENALEX_SCOPUS]["statement"])
check("...and the observed value stays None rather than becoming False",
      alex[journal_risk.SIGNAL_OPENALEX_SCOPUS]["observed"].get("is_indexed_in_scopus"), None)
check_true("the works count is reported with the publisher and country",
           "Elsevier BV" in alex[journal_risk.SIGNAL_OPENALEX_WORKS]["statement"]
           and "NL" in alex[journal_risk.SIGNAL_OPENALEX_WORKS]["statement"])
check("a source OpenAlex does not hold produces one stated signal",
      [s["signal"] for s in journal_risk.fetch_openalex_source(
          FakeClient({"api.openalex.org": FakeResponse(200, {})}), HEP)],
      [journal_risk.SIGNAL_OPENALEX_UNKNOWN])

print("\nthe user's email never lands in the file")

client = FakeClient({"api.crossref.org": CROSSREF_HIT})
mailed = journal_risk.fetch_crossref_journal(client, HEP, mailto="someone@example.org")
check_true("mailto is sent on the request", "mailto=someone" in client.seen[0])
check("...and stripped from the endpoint that is recorded",
      [s for s in mailed if "mailto" in s["endpoint"]], [])
check("...leaving a URL a reader can still paste to check the claim",
      mailed[0]["endpoint"], f"https://api.crossref.org/journals/{HEP}")


# ============================================================
# 3. One record per journal — every source queried, none decisive
# ============================================================

print("\nall three sources are queried, and none of them wins")

client = FakeClient({"doaj.org": DOAJ_MISS, "api.crossref.org": CROSSREF_HIT,
                     "api.openalex.org": OPENALEX_HIT})
record = journal_risk.risk_record(client, "Journal of Hepatology", HEP, paper_count=7)
check("three requests went out for one journal — no first-hit-wins", len(client.seen), 3)
check("...and all three are recorded as having answered",
      record["sources_answered"], list(journal_risk.SOURCE_ORDER))
check("the ISSN is normalised on the way in", record["issn"], HEP)
check("...and the corpus paper count travels with the record",
      record["corpus_paper_count"], 7)
check_true("every record carries its own timestamp, not the file's",
           record["fetched_at"].startswith(str(datetime.now().year)))
check_true("signals from all three sources are kept side by side",
           {s["source"] for s in record["signals"]} == set(journal_risk.SOURCE_ORDER))

# DOAJ says no, OpenAlex says no too — but they are two separate lines with two
# separate endpoints, never merged into one answer.
doaj_lines = [s for s in record["signals"] if s["source"] == "doaj"]
alex_lines = [s for s in record["signals"]
              if s["signal"] == journal_risk.SIGNAL_OPENALEX_DOAJ]
check("DOAJ's own answer and OpenAlex's copy of it are two lines, not one",
      (len(doaj_lines), len(alex_lines)), (1, 1))
check("...from two different endpoints",
      doaj_lines[0]["endpoint"] == alex_lines[0]["endpoint"], False)

# One bad payload costs one source on one journal, never the batch.
class ExplodingClient(FakeClient):
    def get(self, url, **kwargs):
        if "doaj.org" in url:
            raise RuntimeError("connection reset by peer")
        return super().get(url, **kwargs)


partial = journal_risk.risk_record(
    ExplodingClient({"api.crossref.org": CROSSREF_HIT, "api.openalex.org": OPENALEX_HIT}),
    "Journal of Hepatology", HEP)
check("one source raising costs that source and not the other two",
      partial["sources_answered"], ["crossref", "openalex"])

# No ISSN means no lookup at all: every endpoint keys on ISSN and nothing else.
none = journal_risk.risk_record(FakeClient({}), "Nanhai Reports", "")
check("a journal with no ISSN is not queried at all", none["signals"], [])
check("...and records that no source answered, rather than an empty result",
      none["sources_answered"], [])
check("a failing ISSN checksum is recorded rather than silently accepted",
      journal_risk.risk_record(FakeClient({}), "X", "0168-8279")["issn_checksum_ok"], False)


# ============================================================
# 4. Which journals can be looked up at all
# ============================================================

print("\nscoping the lookup")


def paper(pmid, journal, issn=""):
    return {"pmid": str(pmid), "journal": journal, "issn": issn, "authors": []}


work = journals.journal_worklist([
    paper(1, "Journal of Hepatology", HEP), paper(2, "Journal of Hepatology", HEP),
    paper(3, "Nanhai Reports", NAN), paper(4, "No ISSN Journal", ""),
])
scope = journal_risk.risk_targets(work)
check("the denominator is every journal the corpus uses", scope["journal_denominator"], 3)
check("only journals with a usable ISSN can be looked up", len(scope["targets"]), 2)
check("...and the rest are named, not merely counted",
      scope["journals_without_issn"], ["No ISSN Journal"])
check("the paper count travels so the highest-leverage lookups come first",
      [(t["journal"], t["paper_count"]) for t in scope["targets"]],
      [("Journal of Hepatology", 2), ("Nanhai Reports", 1)])
check("an empty corpus scopes to nothing without raising",
      journal_risk.risk_targets(journals.journal_worklist([]))["targets"], [])


# ============================================================
# 5. The payload, and the file it goes in
# ============================================================

print("\nthe payload and its file")

client = FakeClient({"doaj.org": DOAJ_MISS, "api.crossref.org": CROSSREF_HIT,
                     "api.openalex.org": OPENALEX_HIT})
payload = journal_risk.fetch_journal_risk(
    scope["targets"], client=client, source_papers_json="/x/papers_20260822_010101.json",
    max_workers=1, journals_without_issn=scope["journals_without_issn"])
check("one record per target", len(payload["records"]), 2)
check("records stay in target order, never re-sorted by how many signals they got",
      [r["journal"] for r in payload["records"]],
      [t["journal"] for t in scope["targets"]])
check("the denominator carries all three counts",
      payload["denominator"],
      {"journals_total": 2, "journals_with_signals": 2, "journals_without_issn": 1})
check("the corpus filename is stored bare, so the payload stays portable",
      payload["source_papers_json"], "papers_20260822_010101.json")
check("the tracked Crossref field list is stored, so an old file stays readable",
      payload["tracked_coverage_fields"], list(journal_risk.TRACKED_COVERAGE_FIELDS))

path = journal_risk.save_risk_json(payload, ROOT, timestamp="20260822_120000")
check("the file is named like its siblings", os.path.basename(path),
      "journal_risk_20260822_120000.json")
check("the newest one is findable", journal_risk.find_latest_risk_json(ROOT), path)
reloaded = journal_risk.load_risk_json(path)
check("a saved payload round-trips", len(reloaded["records"]), 2)
check("...keeping every record's own timestamp",
      [r["fetched_at"] for r in reloaded["records"]],
      [r["fetched_at"] for r in payload["records"]])
check("nothing was written back into the corpus file name",
      reloaded["source_papers_json"], "papers_20260822_010101.json")

index = journal_risk.index_by_issn(reloaded)
check("records index by ISSN", sorted(index), sorted([HEP, NAN]))
check("...and a bare record list indexes the same way",
      sorted(journal_risk.index_by_issn(reloaded["records"])), sorted([HEP, NAN]))
check("a record with no ISSN is dropped rather than collapsing onto an empty key",
      journal_risk.index_by_issn([{"issn": "", "journal": "X"},
                                  {"issn": "", "journal": "Y"}]), {})

print("\nreuse")

check("the default reuses nothing, because these values move",
      journal_risk.reusable_records(ROOT, 0)["records"], {})
fresh = journal_risk.reusable_records(ROOT, 30)
check("a recent file is reusable", sorted(fresh["records"]), sorted([HEP, NAN]))
check("...from the file it names", fresh["path"], path)
check("an old file is not",
      journal_risk.reusable_records(ROOT, 1,
                                    now=datetime.now() + timedelta(days=5))["records"], {})

# A record no source answered is never carried forward: DOAJ and Crossref index
# continuously, so an empty answer is a gap that may close on its own.
silent_payload = {
    "generated_at": "2026-08-22T12:00:00", "source_papers_json": "", "denominator": {},
    "records": [{"journal": "Q", "issn": HEP, "signals": [], "sources_answered": [],
                 "fetched_at": datetime.now().isoformat(timespec="seconds")}],
}
silent_dir = os.path.join(ROOT, "silent")
os.makedirs(silent_dir, exist_ok=True)
journal_risk.save_risk_json(silent_payload, silent_dir, timestamp="20260822_130000")
check("a journal no source answered for is refetched rather than carried forward",
      journal_risk.reusable_records(silent_dir, 30)["records"], {})

reused = journal_risk.fetch_journal_risk(
    scope["targets"], client=FakeClient({}), max_workers=1, reuse=fresh["records"])
check("a carried record keeps its own timestamp, never today's",
      [r["fetched_at"] for r in reused["records"]],
      [r["fetched_at"] for r in payload["records"]])


# ============================================================
# 5b. The fetch cache — one source's answer, kept with its date
# ============================================================
#
# `reuse` above carries a whole record out of the last output file. This carries
# one *source's* answer out of SQLite, which matters more here than in `cite`:
# this module queries all three every time and never stops at the first hit, so
# a journal DOAJ answered for last week costs a request on every run.
#
# Two rules the assertions below exist for:
#   - a hit opens no socket, and `FakeClient.seen` proves it
#   - a record assembled from cached answers is dated when those answers were
#     really collected. Mixed ages take the oldest, so the row never claims to
#     be fresher than its stalest part.

print("\nthe fetch cache")

from check_your_advisor.cache import FetchCache  # noqa: E402

# Relative to the wall clock: `risk_record` has no `now` seam — it asks the
# cache, which asks the clock — so a hard-coded date would rot.
CACHE_NOW = datetime.now()


def ago(days: float) -> str:
    return (CACHE_NOW - timedelta(days=days)).isoformat(timespec="seconds")


ALL_THREE = {"doaj.org": DOAJ_HIT, "api.crossref.org": CROSSREF_HIT,
             "api.openalex.org": OPENALEX_HIT}

cache = FetchCache(":memory:")
cold = FakeClient(dict(ALL_THREE))
first = journal_risk.risk_record(cold, "Journal of Hepatology", HEP,
                                 cache=cache, max_age_days=30)
check("a cold cache queries all three, as always", len(cold.seen), 3)
check("...and files one row per source, keyed on the ISSN", cache.stats()["rows"], 3)
check("each source's answer is under its own key, not merged into one",
      [cache.get(f"journal_risk.{s}", f"issn:{HEP}", 30) is not None
       for s in journal_risk.SOURCE_ORDER], [True, True, True])
check("a DOAJ row is not served to Crossref",
      cache.get("journal_risk.crossref", f"issn:{HEP}", 30)["value"]
      == cache.get("journal_risk.doaj", f"issn:{HEP}", 30)["value"], False)

warm = FakeClient({})
second = journal_risk.risk_record(warm, "Journal of Hepatology", HEP,
                                  cache=cache, max_age_days=30)
check("a warm cache makes no request at all", warm.seen, [])
check("...and all three sources still count as having answered",
      second["sources_answered"], list(journal_risk.SOURCE_ORDER))
check("...with the same signals, verbatim", second["signals"], first["signals"])
check("...dated when they were collected, not when the record was written",
      second["fetched_at"], first["fetched_at"])

# The date that matters: written into a record today, carrying last week.
old = FetchCache(":memory:")
for source in journal_risk.SOURCE_ORDER:
    old.put(f"journal_risk.{source}", f"issn:{HEP}",
            [{"signal": f"{source}_x", "source": source, "endpoint": "https://e/",
              "statement": "s", "observed": {}}], fetched_at=ago(6))
dated = journal_risk.risk_record(FakeClient({}), "Journal of Hepatology", HEP,
                                 cache=old, max_age_days=30)
check("a fully cached record carries the original collection date", dated["fetched_at"], ago(6))
check("...which is not today", dated["fetched_at"].startswith(
    datetime.now().strftime("%Y-%m-%d")), False)

# Mixed ages: the record takes the oldest contributing stamp, so a reader asking
# "how stale is this row" is told about its stalest part rather than its newest.
mixed = FetchCache(":memory:")
mixed.put(f"journal_risk.{journal_risk.SOURCE_DOAJ}", f"issn:{HEP}",
          [{"signal": "doaj_not_indexed", "source": "doaj", "endpoint": "https://e/",
            "statement": "s", "observed": {}}], fetched_at=ago(9))
mixed.put(f"journal_risk.{journal_risk.SOURCE_CROSSREF}", f"issn:{HEP}",
          [{"signal": "crossref_deposited_dois", "source": "crossref",
            "endpoint": "https://e/", "statement": "s", "observed": {}}], fetched_at=ago(2))
mixed_client = FakeClient({"api.openalex.org": OPENALEX_HIT})
blend = journal_risk.risk_record(mixed_client, "Journal of Hepatology", HEP,
                                 cache=mixed, max_age_days=30)
check("only the uncached source is asked", len(mixed_client.seen), 1)
check("...and it is the one with no row", "api.openalex.org" in mixed_client.seen[0], True)
check("all three still answered", blend["sources_answered"], list(journal_risk.SOURCE_ORDER))
check("a mixed-age record takes the oldest stamp, never the newest",
      blend["fetched_at"], ago(9))

# Expiry is --max-age-days. At the default the cache is never consulted, which
# keeps "the default re-collects everything" true of this command too.
off_client = FakeClient(dict(ALL_THREE))
journal_risk.risk_record(off_client, "Journal of Hepatology", HEP, cache=cache, max_age_days=0)
check("max_age_days=0 does not consult the cache", len(off_client.seen), 3)

stale = FetchCache(":memory:")
for source in journal_risk.SOURCE_ORDER:
    stale.put(f"journal_risk.{source}", f"issn:{HEP}", [{"signal": "x"}], fetched_at=ago(400))
stale_client = FakeClient(dict(ALL_THREE))
journal_risk.risk_record(stale_client, "Journal of Hepatology", HEP,
                         cache=stale, max_age_days=30)
check("rows older than the window are re-collected", len(stale_client.seen), 3)
check("...and the re-collected rows are redated to today",
      stale.get(f"journal_risk.{journal_risk.SOURCE_DOAJ}", f"issn:{HEP}",
                30)["fetched_at"].startswith(datetime.now().strftime("%Y-%m-%d")), True)

# A source that answered nothing is never cached — the same rule `reuse` follows,
# and for the same reason: DOAJ and Crossref index continuously, so an empty
# answer is a gap that may close on its own.
silent = FetchCache(":memory:")
quiet = journal_risk.risk_record(FakeClient({}), "Journal of Hepatology", HEP,
                                 cache=silent, max_age_days=30)
check("a source with nothing to say is not cached", silent.stats()["rows"], 0)
check("...and the record records that nobody answered", quiet["sources_answered"], [])
check("...dated today, because that is when nobody answered",
      quiet["fetched_at"].startswith(datetime.now().strftime("%Y-%m-%d")), True)

# A journal with no usable ISSN has no key to cache under and is not queried.
nokey = FetchCache(":memory:")
journal_risk.risk_record(FakeClient(dict(ALL_THREE)), "No ISSN Journal", "",
                         cache=nokey, max_age_days=30)
check("a journal with no ISSN writes no cache row", nokey.stats()["rows"], 0)

# The batch path threads both arguments through without moving the payload.
batch_cache = FetchCache(":memory:")
warm_batch = FakeClient(dict(ALL_THREE))
journal_risk.fetch_journal_risk(scope["targets"], client=warm_batch, max_workers=1,
                                cache=batch_cache, max_age_days=30,
                                journals_without_issn=scope["journals_without_issn"])
cached_batch = FakeClient({})
again = journal_risk.fetch_journal_risk(
    scope["targets"], client=cached_batch, max_workers=1, cache=batch_cache,
    max_age_days=30, journals_without_issn=scope["journals_without_issn"])
check("a second batch run makes no request", cached_batch.seen, [])
check("...and still reports every journal as answered",
      again["denominator"],
      {"journals_total": 2, "journals_with_signals": 2, "journals_without_issn": 1})
check("a cache does not change the payload's top-level keys",
      sorted(again), ["denominator", "generated_at", "journals_without_issn", "records",
                      "source_papers_json", "sources", "tracked_coverage_fields"])

# No cache at all is still the default and still behaves as it did.
plain = FakeClient(dict(ALL_THREE))
journal_risk.fetch_journal_risk(scope["targets"], client=plain, max_workers=1)
check("cache=None queries every source for every journal, as before", len(plain.seen), 6)

for handle in (cache, old, mixed, stale, silent, nokey, batch_cache):
    handle.close()


# ============================================================
# 6. The join — ISSN only, and four distinct absences
# ============================================================

print("\nthe join")

CORPUS = [paper(1, "Journal of Hepatology", HEP), paper(2, "Journal of Hepatology", HEP),
          paper(3, "Nanhai Reports", NAN), paper(4, "No ISSN Journal", "")]
joined_journals = journals.join_journals(CORPUS, None)

absent = journal_risk.join_risk(joined_journals, None)
check_true("no payload at all is a supported call", absent["risk_missing"])
check("...and it still returns the journals it would have annotated",
      absent["journal_denominator"], 3)
check("...with nothing claimed as checked", absent["journals_checked"], 0)
check("...and the un-lookup-able ones named",
      absent["journals_without_issn"], ["No ISSN Journal"])

joined = journal_risk.join_risk(joined_journals, reloaded)
check_false("a payload makes it not missing", joined["risk_missing"])
check("two of the three journals carry a collected record", joined["journals_checked"], 2)
check("...and the third is counted as unchecked, not as clean",
      joined["journals_unchecked"], 1)
by_journal = {row["journal"]: row for row in joined["rows"]}
check_true("a matched journal carries its signals", by_journal["Journal of Hepatology"]["signals"])
check_true("...and the day they were read", by_journal["Journal of Hepatology"]["fetched_at"])
check_false("a journal with no ISSN is never claimed as checked",
            by_journal["No ISSN Journal"]["checked"])
check("...and carries no signals rather than an empty-looking zero",
      by_journal["No ISSN Journal"]["signals"], [])

# No name fallback, on purpose: a fuzzy name match would attach a real-looking
# statement about one journal to another.
renamed = [dict(r) for r in reloaded["records"]]
renamed[0]["issn"] = "1234-5679"
by_name = journal_risk.join_risk(joined_journals, {"records": renamed})
check("a record whose ISSN does not match is not rescued by its journal name",
      by_name["journals_checked"], 1)

check("signal counts are counts against the journal denominator, never a share",
      joined["signal_counts"][journal_risk.SIGNAL_DOAJ_NOT_INDEXED], 2)
check("...listed in the module's declared signal order, not by size",
      list(joined["signal_counts"]),
      [s for s in journal_risk.SIGNAL_ORDER if s in joined["signal_counts"]])
check_false("no percentage is computed in the join",
            any("%" in str(v) for v in joined["signal_counts"].values()))
check("the provenance names every source that was queried",
      joined["provenance"]["sources"], list(journal_risk.SOURCE_ORDER))
check_true("...and the span of days the signals were read",
           joined["provenance"]["fetched_at_range"] is not None)
check("...and the tracked Crossref field list, so the denominator is checkable",
      joined["provenance"]["tracked_coverage_fields"],
      list(journal_risk.TRACKED_COVERAGE_FIELDS))
check("joining against nothing at all does not raise",
      journal_risk.join_risk(None, None)["journal_denominator"], 0)


# ============================================================
# 7. The worklist cells
# ============================================================

print("\nthe worklist column")

cells = journal_risk.worklist_cells(reloaded)
check("one entry per journal that was actually checked", sorted(cells),
      ["Journal of Hepatology", "Nanhai Reports"])
check_true("the cell holds signal names, not paragraphs",
           all(len(c["risk_signals"]) < 200 for c in cells.values()))
check_true("...semicolon-joined so a CSV cell survives it",
           ";" in cells["Journal of Hepatology"]["risk_signals"])
check("...and the day beside them, which is what stops it becoming an assertion",
      len(cells["Journal of Hepatology"]["risk_checked_on"]), 10)
check("a journal nothing answered for gets no cell at all, rather than a blank one",
      journal_risk.worklist_cells(silent_payload), {})
check("no payload means no cells and no crash", journal_risk.worklist_cells(None), {})

# The two columns exist in the schema and survive the loader. `corpus_paper_count`
# and `alias_group` were documented as carried and were not, because `_parse_row`
# builds its return value by hand; these two are asserted rather than assumed.
schema_keys = [c.key for c in journals.SCHEMA]
check_true("风险信号 is a schema column", "risk_signals" in schema_keys)
check_true("...with its collection date beside it", "risk_checked_on" in schema_keys)
check_false("...and neither is required, so every table written before them still loads",
            any(c.required for c in journals.SCHEMA
                if c.key in ("risk_signals", "risk_checked_on")))

import csv  # noqa: E402

worklist_path = os.path.join(ROOT, "worklist.csv")
journals.write_worklist_csv(work, worklist_path, cells)
with open(worklist_path, encoding="utf-8-sig", newline="") as handle:
    rows = list(csv.reader(handle))
by_zh = {c.zh: i for i, c in enumerate(journals.SCHEMA)}
check("the template header is still the whole schema",
      rows[0], [c.zh for c in journals.SCHEMA])
check("the risk column is pre-filled for a journal that was checked",
      rows[1][by_zh["风险信号"]], cells["Journal of Hepatology"]["risk_signals"])
check("...and so is the date, never one without the other",
      rows[1][by_zh["风险信号采集日期"]], cells["Journal of Hepatology"]["risk_checked_on"])
check("a journal with no collected record is left blank, not zero-filled",
      rows[3][by_zh["风险信号"]], "")
check("the columns the user looks up by hand are still blank",
      [rows[1][by_zh[n]] for n in ("影响因子", "JCR分区", "版本来源", "是否预警")],
      ["", "", "", ""])

# Round trip: the filled-in file loads and the two columns survive `_parse_row`,
# whose return dict is hand-written and therefore the place a new column is lost.
filled = os.path.join(ROOT, "filled.csv")
with open(filled, "w", newline="", encoding="utf-8-sig") as handle:
    writer = csv.writer(handle)
    writer.writerow(rows[0])
    for row in rows[1:]:
        row = list(row)
        row[by_zh["版本来源"]] = "官方版"
        row[by_zh["数据获取日期"]] = "2026-08-22"
        writer.writerow(row)
loaded = journals.load_journal_table(filled)
carried = {r["journal"]: r for r in loaded["rows"]}
check("the risk signal column survives the loader rather than being silently dropped",
      carried["Journal of Hepatology"]["risk_signals"],
      cells["Journal of Hepatology"]["risk_signals"])
check("...and so does its date", carried["Journal of Hepatology"]["risk_checked_on"],
      cells["Journal of Hepatology"]["risk_checked_on"])

# Written before these columns existed: it must still load, and the two keys must
# be present and empty rather than absent.
legacy = os.path.join(ROOT, "legacy.csv")
with open(legacy, "w", newline="", encoding="utf-8-sig") as handle:
    writer = csv.writer(handle)
    writer.writerow(["ISSN", "刊名", "版本来源", "数据获取日期"])
    writer.writerow([HEP, "Journal of Hepatology", "官方版", "2025-01-01"])
old = journals.load_journal_table(legacy)
check("a table written before the risk columns existed still loads", old["row_count"], 1)
check("...with the new keys present and empty rather than missing",
      (old["rows"][0]["risk_signals"], old["rows"][0]["risk_checked_on"]), ("", ""))


print(f"\nSummary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
sys.exit(1 if _failed else 0)
