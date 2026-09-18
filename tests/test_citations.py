#!/usr/bin/env python3
"""
`citations.py`: the three-source fallback chain, and the shape it writes to disk.

This module is the reversal point. Citation counts used to be refused outright;
they are now fetched, counted and persisted. The half of the old rule that did
*not* move is the one most of the assertions below are about: a count may be
printed, a position may not. So `records` comes back in corpus order and is
never reordered by `citation_count`, and this file proves that with a fixture
whose counts and whose corpus order disagree.

The fallback chain is asserted per source and per failure mode. Each source has
its own way of not answering — a 404, a dead socket, a body that is not JSON, a
field that is missing, a field that is a boolean — and the chain has to tell
them apart from a genuine zero, which is a real citation count and not a miss.

No socket is opened. `StubClient` below serves scripted replies keyed by host and
records every URL it was asked for, so "OpenAlex answered, so Semantic Scholar
was never called" is checkable rather than assumed. That is the same technique
tests/test_http_client.py uses on the layer underneath.

Run: python tests/test_citations.py
"""

from __future__ import annotations

import copy
import json
import logging
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor import citations  # noqa: E402
from check_your_advisor.http_client import Response  # noqa: E402

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


def check_false(label: str, cond) -> None:
    check(label, bool(cond), False)


# The module logs progress in Chinese at INFO and coverage problems at WARNING.
# Neither is under test, and with no handler installed Python's last-resort
# handler would put the WARNING lines on a locale-encoded stderr.
logging.getLogger("check_your_advisor").setLevel(logging.CRITICAL)


# ============================================================
# The stub client
# ============================================================

OPENALEX = "api.openalex.org"
S2 = "api.semanticscholar.org"
EPMC = "ebi.ac.uk"

SHORT = {OPENALEX: "openalex", S2: "semantic_scholar", EPMC: "europe_pmc"}


class StubClient:
    """`RobustHTTPClient.get` with a script instead of a socket.

    `replies` maps a host fragment to what that host does: a `Response`, `None`
    for a transport failure (which is what the real client returns rather than
    raising), or an exception instance to raise. A host absent from the map
    returns `default`, which defaults to a 404 — an unconfigured source is a
    source that has nothing, not a source that hangs.
    """

    def __init__(self, replies=None, default=None):
        self.replies = dict(replies or {})
        self.default = default if default is not None else Response(404, {}, b"")
        self.calls: list[str] = []
        # `fetch_citations` reads this when it owns the client. It never owns a
        # stub, but a missing attribute would be a confusing way to find out.
        self.stats = {"total_requests": 0}

    def get(self, url, accept_type="pdf", timeout=None, allow_redirects=True,
            extra_headers=None):
        self.calls.append(url)
        self.stats["total_requests"] += 1
        for token, reply in self.replies.items():
            if token in url:
                if isinstance(reply, BaseException):
                    raise reply
                return reply
        return self.default

    @property
    def sources(self) -> list[str]:
        """Which sources were actually asked, in order."""
        out = []
        for url in self.calls:
            for token, name in SHORT.items():
                if token in url:
                    out.append(name)
                    break
        return out


def ok(payload) -> Response:
    return Response(200, {"Content-Type": "application/json"},
                    json.dumps(payload).encode("utf-8"))


def epmc(count) -> Response:
    """A Europe PMC search body carrying one result."""
    return ok({"resultList": {"result": [{"id": "1", "citedByCount": count}]}})


DOI = "10.1038/s41586-021-03819-2"
PMID = "34265844"


def paper(pmid=PMID, doi=DOI, **extra):
    record = {"pmid": pmid, "doi": doi, "title": "A paper", "authors": []}
    record.update(extra)
    return record


# ============================================================
# DOI normalisation
# ============================================================

print("DOI normalisation")

check("a bare DOI is left alone", citations.normalise_doi(DOI), DOI)
check("the https resolver prefix is stripped",
      citations.normalise_doi("https://doi.org/10.1/x"), "10.1/x")
check("the http resolver prefix is stripped",
      citations.normalise_doi("http://doi.org/10.1/x"), "10.1/x")
# Longest-first matching: the `https://` prefix must not half-strip this one.
check("the dx.doi.org form is stripped whole, not down to 'dx.doi.org/...'",
      citations.normalise_doi("https://dx.doi.org/10.1/x"), "10.1/x")
check("the bare dx.doi.org form is stripped",
      citations.normalise_doi("dx.doi.org/10.1/x"), "10.1/x")
check("the doi: scheme is stripped", citations.normalise_doi("doi:10.1/x"), "10.1/x")
check("prefix matching ignores case but preserves the DOI's own case",
      citations.normalise_doi("HTTPS://DOI.ORG/10.1/XyZ"), "10.1/XyZ")
check("a trailing sentence period is dropped", citations.normalise_doi("10.1/x."), "10.1/x")
check("surrounding whitespace is dropped", citations.normalise_doi("  10.1/x  "), "10.1/x")
check("None becomes the empty string, not 'None'", citations.normalise_doi(None), "")


# ============================================================
# The fallback chain, source by source
# ============================================================

print("\nFallback chain: first hit wins, and stops there")

client = StubClient({OPENALEX: ok({"cited_by_count": 42})})
check("OpenAlex answering ends the chain",
      citations.fetch_citation_count(client, doi=DOI, pmid=PMID), (42, "openalex"))
check("...and the other two are never called", client.sources, ["openalex"])

client = StubClient({OPENALEX: Response(404, {}, b""), S2: ok({"citationCount": 7})})
check("a 404 from OpenAlex falls through to Semantic Scholar",
      citations.fetch_citation_count(client, doi=DOI, pmid=PMID), (7, "semantic_scholar"))
check("...and Europe PMC is not reached", client.sources, ["openalex", "semantic_scholar"])

client = StubClient({OPENALEX: Response(404, {}, b""), S2: Response(429, {}, b""),
                     EPMC: epmc(3)})
check("both upstream sources missing reaches Europe PMC",
      citations.fetch_citation_count(client, doi=DOI, pmid=PMID), (3, "europe_pmc"))
check("...having called all three, in order",
      client.sources, ["openalex", "semantic_scholar", "europe_pmc"])

client = StubClient()
check("all three missing is (None, None), not an exception and not a zero",
      citations.fetch_citation_count(client, doi=DOI, pmid=PMID), (None, None))
check("...after trying every source once",
      client.sources, ["openalex", "semantic_scholar", "europe_pmc"])


print("\nFallback chain: every way a source can fail to answer")

# The real client returns None for a dead socket rather than raising, so this is
# the transport-failure path and not the same thing as an HTTP error.
client = StubClient({OPENALEX: None, S2: ok({"citationCount": 5})})
check("a network-layer failure (client.get -> None) falls through",
      citations.fetch_citation_count(client, doi=DOI), (5, "semantic_scholar"))

client = StubClient({OPENALEX: Response(200, {}, b"<html>not json</html>"),
                     S2: ok({"citationCount": 5})})
check("a 200 carrying something that is not JSON falls through",
      citations.fetch_citation_count(client, doi=DOI), (5, "semantic_scholar"))

client = StubClient({OPENALEX: ok([1, 2, 3]), S2: ok({"citationCount": 5})})
check("a JSON body whose top level is a list falls through",
      citations.fetch_citation_count(client, doi=DOI), (5, "semantic_scholar"))

client = StubClient({OPENALEX: ok({"id": "W1"}), S2: ok({"citationCount": 5})})
check("a 200 with the count field absent falls through",
      citations.fetch_citation_count(client, doi=DOI), (5, "semantic_scholar"))

client = StubClient({OPENALEX: ok({"cited_by_count": None}), S2: ok({"citationCount": 5})})
check("an explicit null count falls through",
      citations.fetch_citation_count(client, doi=DOI), (5, "semantic_scholar"))

client = StubClient({OPENALEX: ok({"cited_by_count": -4}), S2: ok({"citationCount": 5})})
check("a negative count is refused rather than recorded",
      citations.fetch_citation_count(client, doi=DOI), (5, "semantic_scholar"))

# True is an int in Python. Recorded as 1 it would be indistinguishable from a
# paper cited once.
client = StubClient({OPENALEX: ok({"cited_by_count": True}), S2: ok({"citationCount": 5})})
check("a boolean is not read as the integer 1",
      citations.fetch_citation_count(client, doi=DOI), (5, "semantic_scholar"))

client = StubClient({OPENALEX: RuntimeError("malformed payload"), S2: ok({"citationCount": 5})})
check("one source raising costs that source, not the paper and not the batch",
      citations.fetch_citation_count(client, doi=DOI), (5, "semantic_scholar"))

# Zero is the correct answer for a paper published last month. Treating it as a
# miss would walk the whole chain for the newest papers and then file them as
# uncovered, understating coverage exactly where the corpus is youngest.
client = StubClient({OPENALEX: ok({"cited_by_count": 0}), S2: ok({"citationCount": 99})})
check("zero is an answer, not a miss",
      citations.fetch_citation_count(client, doi=DOI), (0, "openalex"))
check("...so the chain stops at it", client.sources, ["openalex"])

client = StubClient({OPENALEX: Response(404, {}, b""), S2: ok({"citationCount": "12"})})
check("a count arriving as a digit string is coerced",
      citations.fetch_citation_count(client, doi=DOI), (12, "semantic_scholar"))

client = StubClient({OPENALEX: Response(404, {}, b""), S2: ok({"citationCount": "many"}),
                     EPMC: epmc(1)})
check("a count that is a non-numeric string falls through",
      citations.fetch_citation_count(client, doi=DOI), (1, "europe_pmc"))


# ============================================================
# What each source is actually asked
# ============================================================

print("\nRequest construction")

client = StubClient({OPENALEX: ok({"cited_by_count": 1})})
citations.fetch_openalex(client, DOI)
check_true("OpenAlex is addressed by DOI on the works path",
           client.calls[0].startswith(f"https://api.openalex.org/works/doi:{DOI}"))
check_false("no mailto is sent when none was configured", "mailto=" in client.calls[0])

client = StubClient({OPENALEX: ok({"cited_by_count": 1})})
citations.fetch_openalex(client, DOI, mailto="someone@example.edu")
check_true("a configured mailto is appended", "mailto=someone%40example.edu" in client.calls[0])

client = StubClient({OPENALEX: ok({"cited_by_count": 1})})
citations.fetch_openalex(client, "10.1002/(SICI)1097")
check_true("a DOI carrying URL-special characters is percent-encoded",
           "%28SICI%29" in client.calls[0])

check("OpenAlex is not called at all without a DOI",
      (citations.fetch_openalex(StubClient(), ""), StubClient().calls), (None, []))
check("Semantic Scholar is not called at all without a DOI",
      citations.fetch_semantic_scholar(StubClient(), ""), None)

client = StubClient({S2: ok({"citationCount": 1})})
citations.fetch_semantic_scholar(client, DOI)
check_true("Semantic Scholar is asked for one field only",
           "fields=citationCount" in client.calls[0])

# An unqualified Europe PMC search matches the DOI string wherever it appears,
# including inside another paper's reference list.
client = StubClient({EPMC: epmc(1)})
citations.fetch_europe_pmc(client, doi=DOI, pmid=PMID)
check_true("Europe PMC is asked a field-qualified PMID query when a PMID exists",
           "EXT_ID%3A" + PMID in client.calls[0] and "SRC%3AMED" in client.calls[0])

client = StubClient({EPMC: epmc(1)})
citations.fetch_europe_pmc(client, doi=DOI, pmid="")
check_true("...and a quoted DOI query when there is no PMID",
           "DOI%3A%22" in client.calls[0])

client = StubClient({EPMC: epmc(1)})
check("Europe PMC is not called with neither identifier",
      (citations.fetch_europe_pmc(client, doi="", pmid=""), client.calls), (None, []))

client = StubClient({EPMC: ok({"resultList": {"result": [{"id": "1"}, {"id": "2",
                                                                      "citedByCount": 8}]}})})
check("a result without a count is skipped, not read as zero",
      citations.fetch_europe_pmc(client, doi=DOI), 8)
client = StubClient({EPMC: ok({"resultList": {"result": []}})})
check("an empty result list is a miss", citations.fetch_europe_pmc(client, doi=DOI), None)
client = StubClient({EPMC: ok({"resultList": "not a dict"})})
check("a malformed resultList is a miss, not a crash",
      citations.fetch_europe_pmc(client, doi=DOI), None)


# ============================================================
# One record
# ============================================================

print("\nOne record")

client = StubClient({OPENALEX: ok({"cited_by_count": 11})})
record = citations.citation_record(client, paper())
check("the record carries exactly the contract keys",
      sorted(record), ["citation_count", "doi", "fetched_at", "pmid", "source"])
check("the count and its source travel together",
      (record["citation_count"], record["source"]), (11, "openalex"))
check_true("the record is stamped with its own fetch time",
           len(record["fetched_at"]) >= len("2026-01-01T00:00:00"))

# A count is a measurement with a date; a paper is not. Per-record timestamps are
# why a 500-paper batch is not misdated by one file-level clock reading.
missed = citations.citation_record(StubClient(), paper())
check("a total miss is still a record, with a null count",
      (missed["citation_count"], missed["source"]), (None, None))
check_true("...and is still stamped", bool(missed["fetched_at"]))

client = StubClient({OPENALEX: ok({"cited_by_count": 1})})
stored = citations.citation_record(client, paper(doi="https://doi.org/10.1/x"))
check("the stored DOI is the normalised one, not the resolver URL", stored["doi"], "10.1/x")


# ============================================================
# A batch: the payload contract, and the ordering that is not produced
# ============================================================

print("\nA batch, and the order it comes back in")

# Corpus order and citation order disagree on purpose: sorted by count these
# would come back exactly reversed.
BATCH = [paper(pmid="101", doi="10.1/a"), paper(pmid="102", doi="10.1/b"),
         paper(pmid="103", doi="10.1/c"), paper(pmid="104", doi="10.1/d")]
COUNTS = {"10.1/a": 1, "10.1/b": 50, "10.1/c": 9, "10.1/d": 400}


class CountingStub(StubClient):
    """OpenAlex answers from COUNTS; a DOI absent from it misses everywhere."""

    def get(self, url, **kw):
        self.calls.append(url)
        self.stats["total_requests"] += 1
        if OPENALEX in url:
            for doi, count in COUNTS.items():
                if doi in url:
                    return ok({"cited_by_count": count})
        return Response(404, {}, b"")


before = copy.deepcopy(BATCH)
payload = citations.fetch_citations(BATCH, client=CountingStub(),
                                    source_papers_json="/tmp/out/papers_20260101_000000.json",
                                    max_workers=1)

check("the payload carries exactly the contract keys",
      sorted(payload), ["denominator", "generated_at", "records", "source_papers_json"])
check("the source corpus is named as a bare filename, not a machine-local path",
      payload["source_papers_json"], "papers_20260101_000000.json")
check("the denominator names both populations",
      payload["denominator"], {"papers_total": 4, "papers_with_citations": 4})
check("every paper produced a record", len(payload["records"]), 4)

# The rule, as an assertion: a value may be printed, a position may not.
check("records come back in corpus order",
      [r["pmid"] for r in payload["records"]], ["101", "102", "103", "104"])
emitted = [r["pmid"] for r in payload["records"]]
league_table = sorted(emitted, key=lambda pmid: -COUNTS["10.1/" + "abcd"[int(pmid) - 101]])
check("...which is not the order a league table would put them in",
      emitted == league_table, False)
check("...nor the ascending one",
      [r["citation_count"] for r in payload["records"]] ==
      sorted(r["citation_count"] for r in payload["records"]), False)
check("the fixture's counts really do disagree with its corpus order",
      league_table, ["104", "102", "103", "101"])
check("no record carries a rank, a percentile or a position field",
      sorted({key for record in payload["records"] for key in record}),
      ["citation_count", "doi", "fetched_at", "pmid", "source"])

# Merging counts into papers_*.json would give a corpus that stays valid for
# years the shelf life of a number that is stale next month.
check("the corpus this was computed from is not written to", BATCH, before)
check_false("no citation_count key is grafted onto a paper",
            any("citation_count" in item for item in BATCH))

payload = citations.fetch_citations(BATCH, client=CountingStub(), max_workers=4)
check("concurrency does not reorder the records",
      [r["pmid"] for r in payload["records"]], ["101", "102", "103", "104"])

partial = citations.fetch_citations(
    BATCH + [paper(pmid="105", doi="10.1/unknown")], client=CountingStub(), max_workers=1)
check("an uncovered paper is counted against the denominator, not dropped",
      partial["denominator"], {"papers_total": 5, "papers_with_citations": 4})
check("...and keeps its place in the record list",
      [r["pmid"] for r in partial["records"]], ["101", "102", "103", "104", "105"])
check("...with a null count rather than a zero",
      partial["records"][-1]["citation_count"], None)

check("an empty corpus is a payload, not a crash",
      citations.fetch_citations([], client=StubClient())["denominator"],
      {"papers_total": 0, "papers_with_citations": 0})


# ============================================================
# Disk
# ============================================================

print("\nDisk")

with tempfile.TemporaryDirectory() as tmp:
    path = citations.save_citations_json(payload, tmp, timestamp="20260101_093000")
    check("the filename pairs with its harvest",
          os.path.basename(path), "citations_20260101_093000.json")
    check("nothing else is written", sorted(os.listdir(tmp)), ["citations_20260101_093000.json"])

    reloaded = citations.load_citations_json(path)
    check("a round trip preserves the record order",
          [r["pmid"] for r in reloaded["records"]], [r["pmid"] for r in payload["records"]])
    check("a round trip preserves the denominator",
          reloaded["denominator"], payload["denominator"])
    check("a round trip preserves the fetch date", reloaded["generated_at"],
          payload["generated_at"])

    older = citations.save_citations_json(payload, tmp, timestamp="20250101_000000")
    # Rerunning a fetch against an old corpus is normal, so the newest file wins
    # even when its name carries the older stamp.
    os.utime(older, (time.time() + 60, time.time() + 60))
    check("the latest citations file is the newest by mtime, not by name",
          citations.find_latest_citations_json(tmp), older)

with tempfile.TemporaryDirectory() as tmp:
    check("an empty directory has no latest file",
          citations.find_latest_citations_json(tmp), None)

    broken = os.path.join(tmp, "citations_20260101_093000.json")
    with open(broken, "w", encoding="utf-8") as handle:
        json.dump({"generated_at": "2026-01-01T09:30:00"}, handle)
    loaded = citations.load_citations_json(broken)
    check("a file with no records loads as empty rather than raising", loaded["records"], [])
    check("...and reports no denominator rather than inventing one",
          loaded["denominator"], {})

    lying = os.path.join(tmp, "citations_20260102_093000.json")
    with open(lying, "w", encoding="utf-8") as handle:
        json.dump({"denominator": {"papers_total": 9, "papers_with_citations": 9},
                   "records": [{"pmid": "1", "citation_count": 3}]}, handle)
    loaded = citations.load_citations_json(lying)
    # A recomputed denominator cannot show that a run died halfway, so a
    # disagreement is reported and left standing rather than quietly corrected.
    check("a denominator that disagrees with the records is preserved, not repaired",
          loaded["denominator"]["papers_with_citations"], 9)

    not_an_object = os.path.join(tmp, "citations_20260103_093000.json")
    with open(not_an_object, "w", encoding="utf-8") as handle:
        json.dump([1, 2, 3], handle)
    try:
        citations.load_citations_json(not_an_object)
        raised = None
    except ValueError as exc:
        raised = type(exc).__name__
    check("a citations file whose top level is not an object raises", raised, "ValueError")


print("\nIndexing")

index = citations.index_by_pmid(payload)
check("the whole payload can be indexed", sorted(index), ["101", "102", "103", "104"])
check("a bare record list can be indexed too",
      sorted(citations.index_by_pmid(payload["records"])), ["101", "102", "103", "104"])
check("a record with no PMID is dropped rather than filed under an empty key",
      citations.index_by_pmid([{"pmid": "", "citation_count": 1}]), {})
check("a repeated PMID keeps the last record",
      citations.index_by_pmid([{"pmid": "7", "citation_count": 1},
                               {"pmid": "7", "citation_count": 2}])["7"]["citation_count"], 2)
check("something that is not a record collection indexes to nothing",
      citations.index_by_pmid("not records"), {})


print("\nThe boundary this module keeps")

# Absolute values are produced. Orderings are not, and neither is anything that
# would need a licensed table nobody can redistribute.
source = open(citations.__file__, encoding="utf-8").read().lower()
check("the module exposes no ranking helper",
      [name for name in dir(citations)
       if any(word in name.lower()
              for word in ("rank", "percentile", "quantile", "grade", "tier", "star",
                           "top_", "best_"))], [])
check("the module exposes no impact-factor or quartile helper",
      [name for name in dir(citations)
       if any(word in name.lower() for word in ("impact_factor", "quartile", "jcr", "cas_"))],
      [])
check("the fallback order is documented as coverage, not as a quality judgement",
      "not a ranking of the providers" in source, True)
check("the three sources are the three the contract names",
      citations.SOURCE_ORDER, ("openalex", "semantic_scholar", "europe_pmc"))



# ----------------------------------------------------------------------
# Reuse: --max-age-days
#
# Re-running `cite` on a 500-paper corpus refetched all 500, at roughly a second
# of deliberate rate-limit sleep each. Reuse is the opt-out, and the three rules
# below are what keep it from becoming a quiet lie:
#
#   - opt-in only, because a citation count moves every week
#   - a carried record keeps its OWN fetched_at, so a mixed-age file stays
#     readable row by row and the report can print a range instead of one date
#     that is true of only some of it
#   - a previous miss is never carried, because coverage is the one thing that
#     improves on its own as sources index more
# ----------------------------------------------------------------------
print("\nReuse (--max-age-days)")

import tempfile as _tempfile  # noqa: E402
from datetime import datetime as _dt, timedelta as _td  # noqa: E402

_NOW = _dt(2026, 8, 21, 12, 0, 0)


def _write_citations(directory: str, records: list[dict], stamp: str = "20260801_120000") -> str:
    path = os.path.join(directory, f"citations_{stamp}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({
            "generated_at": _NOW.isoformat(timespec="seconds"),
            "source_papers_json": "papers_20260801_120000.json",
            "denominator": {"papers_total": len(records),
                            "papers_with_citations": sum(1 for r in records
                                                         if r["citation_count"] is not None)},
            "records": records,
        }, handle)
    return path


def _record(pmid: str, count, age_days: float, source: str = "openalex") -> dict:
    return {
        "pmid": pmid, "doi": f"10.1/{pmid}", "citation_count": count,
        "source": source if count is not None else None,
        "fetched_at": (_NOW - _td(days=age_days)).isoformat(timespec="seconds"),
    }


with _tempfile.TemporaryDirectory() as _tmp:
    _write_citations(_tmp, [
        _record("1", 10, age_days=1),        # fresh
        _record("2", 0, age_days=5),         # fresh, and zero is a real count
        _record("3", 99, age_days=400),      # stale
        _record("4", None, age_days=1),      # a fresh miss: never carried
    ])

    check("max_age_days=0 reuses nothing, whatever is on disk",
          citations.reusable_records(_tmp, 0, now=_NOW)["records"], {})
    check("a negative age reuses nothing either",
          citations.reusable_records(_tmp, -1, now=_NOW)["records"], {})

    _fresh = citations.reusable_records(_tmp, 30, now=_NOW)
    check("records inside the window are offered", sorted(_fresh["records"]), ["1", "2"])
    check("...naming the file they came from", os.path.basename(_fresh["path"]).startswith("citations_"), True)
    check("a count of zero is carried, because zero is an answer",
          _fresh["records"]["2"]["citation_count"], 0)
    check("a record older than the window is not carried", "3" in _fresh["records"], False)
    check("a previous miss is never carried, however fresh", "4" in _fresh["records"], False)
    check("an empty directory offers nothing rather than raising",
          citations.reusable_records(os.path.join(_tmp, "nope"), 30, now=_NOW)["records"], {})

    # An unparseable stamp means "refetch": a record whose age cannot be
    # established is not a record whose age is acceptable.
    with _tempfile.TemporaryDirectory() as _tmp2:
        _write_citations(_tmp2, [{"pmid": "9", "doi": "", "citation_count": 5,
                                  "source": "openalex", "fetched_at": "sometime last week"}])
        check("an unparseable fetched_at is not reused",
              citations.reusable_records(_tmp2, 30, now=_NOW)["records"], {})

    # The whole point: a carried record must not be restamped with today.
    _papers = [{"pmid": "1", "doi": "10.1/1"}, {"pmid": "2", "doi": "10.1/2"},
               {"pmid": "5", "doi": "10.1/5"}]
    _stub = StubClient({"openalex": ok({"cited_by_count": 77})})
    _payload = citations.fetch_citations(_papers, client=_stub, reuse=_fresh["records"])
    _by_pmid = {r["pmid"]: r for r in _payload["records"]}
    check("a carried record keeps its own fetched_at, not today's",
          _by_pmid["1"]["fetched_at"], _fresh["records"]["1"]["fetched_at"])
    check("...and its own count", _by_pmid["1"]["citation_count"], 10)
    check("...and its own source", _by_pmid["1"]["source"], "openalex")
    check("the paper not on file is actually fetched", _by_pmid["5"]["citation_count"], 77)
    check("only the unfetched paper hit the network", len(_stub.calls), 1)
    check("records still come back in corpus order",
          [r["pmid"] for r in _payload["records"]], ["1", "2", "5"])

    # Reuse must not change the file contract: the report and four test files
    # read these keys by name.
    check("reuse leaves the payload's top-level keys alone",
          sorted(_payload), ["denominator", "generated_at", "records", "source_papers_json"])
    check("...and the denominator's shape",
          _payload["denominator"], {"papers_total": 3, "papers_with_citations": 3})
    check("a carried record is a copy, not the offered dict",
          _by_pmid["1"] is _fresh["records"]["1"], False)

    # Reuse with nothing on file is the same code path as no reuse at all.
    _stub2 = StubClient({"openalex": ok({"cited_by_count": 3})})
    check("reuse={} fetches everything",
          [r["citation_count"] for r in
           citations.fetch_citations(_papers, client=_stub2, reuse={})["records"]],
          [3, 3, 3])


# ----------------------------------------------------------------------
# The fetch cache (`--max-age-days N`, per source)
#
# `reuse` above carries a whole *record* forward out of the last output file.
# This carries one *source's answer* forward out of SQLite, which is a different
# saving: a paper whose count OpenAlex supplied last week costs no request at
# all, and `journal-risk`'s three-per-journal walk can hit on one source and
# still ask the other two.
#
# Two things are non-negotiable and most of what follows is about them:
#   - a cached answer arrives with the day it was really collected, so the
#     record written today is dated last week if that is when the count was read
#   - a cache hit opens no socket, which is checkable here because StubClient
#     records every URL it was asked for
# ----------------------------------------------------------------------
print("\nFetch cache (--max-age-days N, per source)")

from check_your_advisor.cache import FetchCache  # noqa: E402

# Relative to the wall clock, not to a fixed date: `citation_record` has no
# `now` seam — it asks the cache, which asks the clock — so a hard-coded 2026
# fixture would start failing the day the window it sits in slides past it.
_CACHE_NOW = _dt.now()


def _stamp(days_ago: float) -> str:
    return (_CACHE_NOW - _td(days=days_ago)).isoformat(timespec="seconds")


# A first run with an empty cache behaves exactly as it always did, and leaves
# the answer behind under a key naming the source that gave it.
_cache = FetchCache(":memory:")
_client = StubClient({OPENALEX: ok({"cited_by_count": 42})})
_rec = citations.citation_record(_client, paper(), cache=_cache, max_age_days=30)
check("a cold cache fetches normally", _rec["citation_count"], 42)
check("...and the request did go out", _client.sources, ["openalex"])
check("the answer is filed under the source that gave it",
      _cache.get("citations.openalex", f"doi:{DOI}", 30)["value"], 42)
check("...and not under a source that was never asked",
      _cache.get("citations.semantic_scholar", f"doi:{DOI}", 30), None)

# The second run is the whole point: same paper, a client that would answer
# differently, and no request made.
_client2 = StubClient({OPENALEX: ok({"cited_by_count": 999})})
_rec2 = citations.citation_record(_client2, paper(), cache=_cache, max_age_days=30)
check("a warm cache serves the count", _rec2["citation_count"], 42)
check("...without opening a socket", _client2.calls, [])
check("...and still names the source the answer came from", _rec2["source"], "openalex")
check("...and the record is dated when the count was collected, not now",
      _rec2["fetched_at"], _rec["fetched_at"])

# A stamp written by an earlier run survives into a record written today. This
# is the same rule `reuse` follows, enforced one layer down.
_cache.put("citations.openalex", "doi:10.1/old", 7, fetched_at=_stamp(3))
_old = citations.citation_record(StubClient(), paper(pmid="77", doi="10.1/old"),
                                 cache=_cache, max_age_days=30)
check("a record served from cache carries the original collection date",
      _old["fetched_at"], _stamp(3))
check("...which is not today", _old["fetched_at"].startswith(
    _dt.now().strftime("%Y-%m-%d")), False)
check("...and the count is the cached one", _old["citation_count"], 7)

# Per-source keys, seen from the chain rather than from the cache: an answer
# filed under Semantic Scholar does not stop OpenAlex being asked first.
_cache2 = FetchCache(":memory:")
_cache2.put("citations.semantic_scholar", f"doi:{DOI}", 5, fetched_at=_stamp(1))
_mixed = StubClient({OPENALEX: Response(404, {}, b"")})
_mrec = citations.citation_record(_mixed, paper(), cache=_cache2, max_age_days=30)
check("OpenAlex is still asked when only Semantic Scholar is cached",
      _mixed.sources, ["openalex"])
check("...and Semantic Scholar answers from cache without being asked",
      (_mrec["citation_count"], _mrec["source"]), (5, "semantic_scholar"))
check("...carrying its own date", _mrec["fetched_at"], _stamp(1))

# Europe PMC is keyed on the PMID when there is one, because that is what the
# request is actually built from.
_cache3 = FetchCache(":memory:")
_cache3.put("citations.europe_pmc", f"pmid:{PMID}", 8, fetched_at=_stamp(2))
_epmc_client = StubClient({OPENALEX: Response(404, {}, b""), S2: Response(404, {}, b"")})
_erec = citations.citation_record(_epmc_client, paper(), cache=_cache3, max_age_days=30)
check("Europe PMC's row is keyed on the PMID the query is built from",
      (_erec["citation_count"], _erec["source"]), (8, "europe_pmc"))
check("...and its stamp travels too", _erec["fetched_at"], _stamp(2))

# Expiry is `--max-age-days` and nothing else. At the default the cache is not
# consulted at all, which keeps "the default refetches everything" true.
_cache4 = FetchCache(":memory:")
_cache4.put("citations.openalex", f"doi:{DOI}", 42, fetched_at=_stamp(1))
_off = StubClient({OPENALEX: ok({"cited_by_count": 999})})
check("max_age_days=0 does not consult the cache",
      citations.citation_record(_off, paper(), cache=_cache4,
                                max_age_days=0)["citation_count"], 999)
check("...so the request went out", len(_off.calls), 1)

_stale = StubClient({OPENALEX: ok({"cited_by_count": 999})})
_cache4.put("citations.openalex", f"doi:{DOI}", 42, fetched_at=_stamp(400))
check("a row older than the window is refetched",
      citations.citation_record(_stale, paper(), cache=_cache4,
                                max_age_days=30)["citation_count"], 999)
check("...and the refetched row is redated to today",
      _cache4.get("citations.openalex", f"doi:{DOI}", 30)["fetched_at"].startswith(
          _dt.now().strftime("%Y-%m-%d")), True)

# A miss is never cached, for the reason `reusable_records` gives: coverage is
# the one thing that improves on its own as the sources index more.
_cache5 = FetchCache(":memory:")
_missed = citations.citation_record(StubClient(), paper(), cache=_cache5, max_age_days=30)
check("three sources answering nothing is still a miss",
      (_missed["citation_count"], _missed["source"]), (None, None))
check("...and nothing was written to the cache", _cache5.stats()["rows"], 0)
check("...so the record is dated today, because that is when the miss happened",
      _missed["fetched_at"].startswith(_dt.now().strftime("%Y-%m-%d")), True)

# Zero is a real count, so it is cached like any other and served like any other.
_cache6 = FetchCache(":memory:")
citations.citation_record(StubClient({OPENALEX: ok({"cited_by_count": 0})}),
                          paper(), cache=_cache6, max_age_days=30)
_zero_client = StubClient({OPENALEX: ok({"cited_by_count": 500})})
check("a cached zero is served rather than refetched",
      citations.citation_record(_zero_client, paper(), cache=_cache6,
                                max_age_days=30)["citation_count"], 0)
check("...without a request", _zero_client.calls, [])

# The batch path threads both arguments through, and the payload shape does not
# move because a cache was handed in.
_cache7 = FetchCache(":memory:")
_cache7.put("citations.openalex", "doi:10.1/1", 11, fetched_at=_stamp(4))
_cache7.put("citations.openalex", "doi:10.1/2", 22, fetched_at=_stamp(4))
_batch_papers = [{"pmid": "1", "doi": "10.1/1"}, {"pmid": "2", "doi": "10.1/2"},
                 {"pmid": "3", "doi": "10.1/3"}]
_batch_client = StubClient({OPENALEX: ok({"cited_by_count": 33})})
_batch = citations.fetch_citations(_batch_papers, client=_batch_client,
                                   cache=_cache7, max_age_days=30, max_workers=1)
check("cached papers cost no requests, the rest are fetched",
      len(_batch_client.calls), 1)
check("counts come back per paper, in corpus order",
      [r["citation_count"] for r in _batch["records"]], [11, 22, 33])
check("cached records keep their own date",
      [r["fetched_at"] for r in _batch["records"][:2]], [_stamp(4), _stamp(4)])
check("the freshly fetched one is dated today",
      _batch["records"][2]["fetched_at"].startswith(_dt.now().strftime("%Y-%m-%d")), True)
check("a cache does not change the payload's top-level keys",
      sorted(_batch), ["denominator", "generated_at", "records", "source_papers_json"])
check("...nor the denominator", _batch["denominator"],
      {"papers_total": 3, "papers_with_citations": 3})

# No cache at all is still the default, and behaves exactly as before.
_nocache = StubClient({OPENALEX: ok({"cited_by_count": 4})})
check("cache=None fetches everything, as it always did",
      [r["citation_count"] for r in
       citations.fetch_citations(_batch_papers, client=_nocache, max_workers=1)["records"]],
      [4, 4, 4])
check("...with one request per paper", len(_nocache.calls), 3)

_cache.close()
_cache2.close()
_cache3.close()
_cache4.close()
_cache5.close()
_cache6.close()
_cache7.close()


print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(1 if _failed else 0)
