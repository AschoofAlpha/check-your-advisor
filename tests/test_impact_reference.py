#!/usr/bin/env python3
"""
`impact_reference.py`: one paper's citation count placed inside an external
reference population, and the five separate ways that can fail to happen.

This file exists mostly to protect one distinction. `ranking.RANKING_EXCLUSIONS`
still refuses "a position inside a reference population" on the grounds that the
corpora on a page are the ones a user chose to load. That objection is about a
position among the loaded few and it is still true — a test below reads the
sentence out of `profile/ranking.py` to prove it was not quietly edited away.
What this module adds is a position inside an OpenAlex topic-year cell, which
exists whether or not anybody loaded anything, so the two coexist.

What these assertions protect, in order of how badly it would hurt to lose it:

  1. **"Could not place" is never printed as "placed low".** Six statuses, five
     of them unlocatable, every one of them carrying `percentile is None` and a
     sentence. A 0.0 percentile is a real finding — no paper in the cell has
     fewer citations — and it may only ever appear with `located` True.
  2. **Every number carries its denominator.** The population a position was
     taken in, how much of the cell OpenAlex actually returned, and how many
     papers reached each status.
  3. **The percentile definition is fixed and stated.** Strictly-below, so a
     zero-citation paper in a cell that is 37% zeros scores 0.0 rather than
     being credited with the whole tie block.
  4. **The floor is a declared constant, not a literal in a function body.**
     Moving it moves the behaviour, which is what the test does.
  5. **One request per (topic, year) per run**, and no request at all for a
     paper there is nothing to place.

Fully offline: every HTTP call is faked through the same `.get()` seam
`test_journal_risk.py` and `test_citations.py` use. No network, no pytest.

Run: python tests/test_impact_reference.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date
from urllib.parse import unquote_plus

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor import impact_reference as ir  # noqa: E402
from check_your_advisor.profile import ranking  # noqa: E402

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


def close(label: str, actual, expected, tol: float = 1e-9) -> None:
    """Float equality is never `==` here: every numeric claim carries a tolerance."""
    global _passed, _failed
    ok = isinstance(actual, (int, float)) and abs(float(actual) - float(expected)) <= tol
    if ok:
        _passed += 1
        print(f"  [PASS] {label}")
    else:
        _failed += 1
        print(f"  [FAIL] {label}  (expected {expected!r} +/- {tol}, got {actual!r})")


TODAY = date.today().isoformat()


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

    def __init__(self, routes: dict, default=None) -> None:
        self.routes = routes
        self.default = default if default is not None else FakeResponse(404, {})
        self.seen: list[str] = []
        self.stats = {"total_requests": 0}

    def get(self, url, **kwargs):
        self.seen.append(url)
        self.stats["total_requests"] += 1
        for fragment, response in self.routes.items():
            if fragment in url:
                return response
        return self.default


def group_payload(counts: dict, reported_total=None, extra_groups=()) -> dict:
    """The shape `/works?...&group_by=cited_by_count` actually returns.

    `key` is a string in the real payload — that is the reason `counts` keys are
    asserted to be ints below.
    """
    groups = [{"key": str(k), "key_display_name": str(k), "count": n}
              for k, n in sorted(counts.items())]
    groups.extend(extra_groups)
    return {
        "meta": {"count": sum(counts.values()) if reported_total is None else reported_total,
                 "groups_count": len(groups)},
        "group_by": groups,
    }


def work_payload(work_id: str, topic_id: str, topic_name: str, year, score=0.98) -> dict:
    topic = None
    if topic_id:
        topic = {"id": f"https://openalex.org/{topic_id}",
                 "display_name": topic_name, "score": score}
    return {
        "id": f"https://openalex.org/{work_id}",
        "publication_year": year,
        "primary_topic": topic,
        "cited_by_count": 7,
    }


# The distribution the main line actually measured on 2026-09-17:
# T10091 / 2023, meta.count = 3808 across 86 groups, 1399 of them at zero.
# Reconstructed here to the same three published facts — total, group count, and
# the zero bucket — with a flat tail standing in for the 84 unpublished groups.
MEASURED = {0: 1399, 1: 379}
for _key in range(2, 86):
    MEASURED[_key] = 24
MEASURED[2] += 14
MEASURED_TOTAL = sum(MEASURED.values())


print("\n" + "=" * 70)
print("1. The percentile definition: strictly below, and why that matters at zero")
print("=" * 70)

check("the fixture reproduces the measured total", MEASURED_TOTAL, 3808)
check("...and the measured group count", len(MEASURED), 86)

cell = ir.fetch_reference_distribution(
    FakeClient({"T10091": FakeResponse(200, group_payload(MEASURED))}),
    "T10091", 2023,
)
check("a distribution that answered is available", cell["available"], True)
check("the population is the sum of the returned groups", cell["population"], 3808)
check("...and it is the denominator, stated as such", cell["denominator"], 3808)
check("the group keys are parsed to ints, not left as API strings",
      sorted(cell["counts"])[:3], [0, 1, 2])
check("the zero bucket survives verbatim", cell["counts"][0], 1399)
check("the top of the cell is reported", cell["max_citation_count"], 85)

zero = ir.percentile_in_distribution(0, cell)
check("a zero-citation paper is located, not refused", zero["located"], True)
close("...at the 0th percentile exactly", zero["percentile"], 0.0)
check("...with nothing below it", zero["papers_below"], 0)
check("...tied with the whole zero bucket", zero["tied_count"], 1399)
close("...which is 36.7% of the cell", zero["tied_fraction"], 1399 / 3808)
close("the 'at or below' reading is reported beside it, not instead of it",
      zero["percentile_at_or_below"], 100.0 * 1399 / 3808)
check("a located 0.0 is emphatically not an unlocatable record",
      (zero["status"], zero["unlocatable"]), (ir.STATUS_LOCATED, None))

one = ir.percentile_in_distribution(1, cell)
close("one citation places a paper above the zero bucket and nothing else",
      one["percentile"], 100.0 * 1399 / 3808)
check("...on a denominator that is the whole cell", one["denominator"], 3808)

top = ir.percentile_in_distribution(85, cell)
close("the top bucket does not reach the 100th percentile",
      top["percentile"], 100.0 * (3808 - 24) / 3808)
check("...because its own 24 ties are not counted as below it", top["tied_count"], 24)
check_false("...and it is not flagged as above the cell", top["above_distribution_max"])

# The single-top-paper case: the one paper at the top of its cell.
lonely = ir.fetch_reference_distribution(
    FakeClient({"T20002": FakeResponse(200, group_payload({0: 500, 1: 300, 2: 199, 40: 1}))}),
    "T20002", 2023,
)
lonely_top = ir.percentile_in_distribution(40, lonely)
close("the single most-cited paper in a 1000-paper cell sits at 99.9, not 100",
      lonely_top["percentile"], 99.9)
check("...and 1000 papers give a resolution of 0.1 points per paper",
      round(lonely_top["resolution_points_per_paper"], 6), 0.1)

above = ir.percentile_in_distribution(41, lonely)
close("a count above everything in the cell does reach 100", above["percentile"], 100.0)
check_true("...and says so, because that count came from somewhere else",
           above["above_distribution_max"])
check("...with no ties to report", above["tied_count"], 0)

flat = ir.fetch_reference_distribution(
    FakeClient({"T20003": FakeResponse(200, group_payload({0: 1000}))}),
    "T20003", 2023,
)
flat_zero = ir.percentile_in_distribution(0, flat)
close("in a cell where every paper has zero citations, zero is the 0th percentile",
      flat_zero["percentile"], 0.0)
close("...and the tie block is the entire cell", flat_zero["tied_fraction"], 1.0)
check_true("the basis sentence names the tie block",
           "1000" in flat_zero["basis"] and "tie" in flat_zero["basis"].lower())

check_true("the method string fixes the definition in words",
           "strictly" in ir.PERCENTILE_METHOD.lower())
check_true("...and names the alternative it rejected",
           "tie" in ir.PERCENTILE_METHOD.lower())


print("\n" + "=" * 70)
print("2. Five ways this cannot be computed, none of them a low percentile")
print("=" * 70)

check("the status vocabulary is exactly the documented one",
      list(ir.STATUS_ORDER),
      [ir.STATUS_LOCATED, ir.STATUS_NO_CITATION_COUNT, ir.STATUS_NO_IDENTIFIER,
       ir.STATUS_TOPIC_LOOKUP_FAILED, ir.STATUS_NO_TOPIC, ir.STATUS_NO_YEAR,
       ir.STATUS_DISTRIBUTION_UNAVAILABLE, ir.STATUS_DISTRIBUTION_TOO_SMALL])
check("exactly one of them is a located result",
      [s for s in ir.STATUS_ORDER if s not in ir.UNLOCATABLE_STATUSES],
      [ir.STATUS_LOCATED])
check("...so the other seven are unlocatable", len(ir.UNLOCATABLE_STATUSES), 7)
check("every status has a stated reason",
      sorted(ir.STATUS_REASONS), sorted(ir.STATUS_ORDER))
for _status in ir.UNLOCATABLE_STATUSES:
    check_true(f"the reason for `{_status}` is a sentence, not a label",
               len(ir.STATUS_REASONS[_status]) >= 40)

no_count = ir.percentile_in_distribution(None, cell)
check("no citation count is its own status", no_count["status"], ir.STATUS_NO_CITATION_COUNT)
check("...with no percentile at all", no_count["percentile"], None)
check_false("...and it is not located", no_count["located"])
check_true("...and it says why", no_count["unlocatable"])

gone = ir.fetch_reference_distribution(
    FakeClient({}, default=FakeResponse(500, {})), "T30003", 2023)
check_false("a failed group_by request is not an empty distribution", gone["available"])
check("...it is its own status", gone["status"], ir.STATUS_DISTRIBUTION_UNAVAILABLE)
check("...with a population of zero and the key still present", gone["population"], 0)
no_dist = ir.percentile_in_distribution(5, gone)
check("a paper against an unavailable distribution carries that status forward",
      no_dist["status"], ir.STATUS_DISTRIBUTION_UNAVAILABLE)
check("...and still no percentile", no_dist["percentile"], None)
check("a missing distribution object is the same refusal, not a crash",
      ir.percentile_in_distribution(5, None)["status"], ir.STATUS_DISTRIBUTION_UNAVAILABLE)

thin_counts = {0: 40, 1: 30, 2: 29}
thin = ir.fetch_reference_distribution(
    FakeClient({"T30004": FakeResponse(200, group_payload(thin_counts))}), "T30004", 2023)
check("a 99-paper cell answered", thin["population"], 99)
check_false("...but is not a reference population", thin["sufficient"])
check("...which is its own status", thin["status"], ir.STATUS_DISTRIBUTION_TOO_SMALL)
thin_place = ir.percentile_in_distribution(2, thin)
check("a paper in a cell that small is not placed", thin_place["percentile"], None)
check("...and the refusal names the floor it fell under",
      thin_place["min_population"], ir.MIN_REFERENCE_POPULATION)
check_true("...in words as well as in a number",
           str(ir.MIN_REFERENCE_POPULATION) in thin_place["unlocatable"])

# The one assertion that proves the floor is not welded into a function body.
_floor_was = ir.MIN_REFERENCE_POPULATION
try:
    ir.MIN_REFERENCE_POPULATION = 99
    moved = ir.fetch_reference_distribution(
        FakeClient({"T30004": FakeResponse(200, group_payload(thin_counts))}), "T30004", 2023)
    check_true("lowering the exported floor makes the same cell usable", moved["sufficient"])
    check("...and the same paper is then placed",
          ir.percentile_in_distribution(2, moved)["status"], ir.STATUS_LOCATED)
    check("...against the floor the module currently declares",
          moved["min_population"], 99)
finally:
    ir.MIN_REFERENCE_POPULATION = _floor_was
check("the floor is restored for the rest of this run",
      ir.MIN_REFERENCE_POPULATION, _floor_was)

# Key parity: a refusal may not carry fewer keys than a success.
located_keys = sorted(ir.percentile_in_distribution(3, cell))
for label, payload in (("no count", no_count), ("no distribution", no_dist),
                       ("cell too small", thin_place)):
    check(f"a refusal for `{label}` carries the same keys as a success",
          sorted(payload), located_keys)

# No unlocatable record may carry a number a renderer could print as a position.
for label, payload in (("no count", no_count), ("no distribution", no_dist),
                       ("cell too small", thin_place)):
    check(f"`{label}`: percentile is None, never 0", payload["percentile"], None)
    check(f"`{label}`: the 'at or below' reading is None too",
          payload["percentile_at_or_below"], None)
    check(f"`{label}`: the bounds are None too", payload["percentile_bounds"], None)
    check(f"`{label}`: papers_below is None, not 0", payload["papers_below"], None)


print("\n" + "=" * 70)
print("3. What the cell does not contain, said out loud")
print("=" * 70)

truncated = ir.fetch_reference_distribution(
    FakeClient({"T40004": FakeResponse(200, group_payload(MEASURED, reported_total=5000))}),
    "T40004", 2023)
check("the population is what came back", truncated["population"], 3808)
check("...and the reported total is kept beside it", truncated["reported_total"], 5000)
check_true("...with the gap named", truncated["truncated"])
check("...and counted", truncated["missing"], 1192)
band = ir.percentile_in_distribution(1, truncated)
lo, hi = band["percentile_bounds"]
check_true("an incomplete cell produces a band, not a point pretending to be one", lo < hi)
check_true("...that brackets the point estimate", lo <= band["percentile"] <= hi)
close("...whose low end assumes every missing paper is above this one",
      lo, 100.0 * 1399 / 5000)
close("...and whose high end assumes every missing paper is below it",
      hi, 100.0 * (1399 + 1192) / 5000)
check_true("the basis sentence says the cell is incomplete",
           "1192" in band["basis"])

whole = ir.percentile_in_distribution(1, cell)
check("a complete cell brackets the point with itself",
      whole["percentile_bounds"], (whole["percentile"], whole["percentile"]))
check("...and reports nothing missing", whole["missing"], 0)

junk = ir.fetch_reference_distribution(
    FakeClient({"T40005": FakeResponse(200, group_payload(
        {0: 200, 1: 100},
        reported_total=310,
        extra_groups=({"key": "unknown", "key_display_name": "unknown", "count": 10},)))}),
    "T40005", 2023)
check("a group whose key is not a citation count is skipped", junk["population"], 300)
check("...and counted as skipped", junk["groups_skipped"], 1)
check("...so the papers it held show up as missing, not as zero-citation papers",
      junk["missing"], 10)
check("the two integer groups are the ones that survived", junk["groups_returned"], 2)


print("\n" + "=" * 70)
print("4. Getting the topic: four outcomes, four names")
print("=" * 70)

TOPIC_ROUTES = {
    "W101": FakeResponse(200, work_payload("W101", "T10091", "Cardiac imaging", 2023)),
    "W102": FakeResponse(200, work_payload("W102", "T10091", "Cardiac imaging", 2023)),
    "10.5555/c-paper": FakeResponse(200, work_payload("W103", "T99999", "Thin topic", 2019)),
    "W105": FakeResponse(500, {}),
    "W106": FakeResponse(200, work_payload("W106", "", "", 2023)),
    "W107": FakeResponse(200, work_payload("W107", "T10091", "Cardiac imaging", None)),
}

client = FakeClient(dict(TOPIC_ROUTES))
topics = ir.fetch_topics(
    [
        {"openalex_work_id": "W101", "pub_year": "2023", "citation_count": 5},
        {"doi": "https://doi.org/10.5555/c-paper", "citation_count": 1},
        {"title": "no identifier at all", "citation_count": 2},
        {"openalex_work_id": "W105", "citation_count": 3},
        {"openalex_work_id": "W106", "citation_count": 4},
        {"openalex_work_id": "W107", "citation_count": 6},
    ],
    client,
)
check("one record per paper, in corpus order", len(topics), 6)
check("a work id resolves to a topic", topics[0]["status"], ir.STATUS_TOPIC_FOUND)
check("...normalised out of the OpenAlex URI form", topics[0]["topic_id"], "T10091")
check("...with the human-readable name kept", topics[0]["topic_display_name"], "Cardiac imaging")
check("...and the corpus's own year preferred over the API's",
      (topics[0]["year"], topics[0]["year_source"]), (2023, "corpus"))
check("the lookup says which identifier it used", topics[0]["queried_by"], "openalex_work_id")

check("a DOI-only paper is looked up by DOI", topics[1]["queried_by"], "doi")
check("...and gets its topic", topics[1]["topic_id"], "T99999")
check("...and its year from OpenAlex, since the corpus had none",
      (topics[1]["year"], topics[1]["year_source"]), (2019, "openalex"))
check_true("...with the DOI normalised out of its resolver prefix before the request",
           any("10.5555/c-paper" in url and "doi.org" not in url for url in client.seen))

check("no identifier is its own status", topics[2]["status"], ir.STATUS_NO_IDENTIFIER)
check("...and costs no request", [u for u in client.seen if "no identifier" in u], [])
check("an HTTP failure is its own status", topics[3]["status"], ir.STATUS_TOPIC_LOOKUP_FAILED)
check("a work OpenAlex files under no topic is a third thing",
      topics[4]["status"], ir.STATUS_NO_TOPIC)
check_false("...and it is not confused with the request having failed",
            topics[4]["status"] == ir.STATUS_TOPIC_LOOKUP_FAILED)
check("a topic with no year anywhere still reports the topic",
      (topics[5]["topic_id"], topics[5]["year"]), ("T10091", None))
for _index, _record in enumerate(topics):
    check(f"topic record {_index} is stamped with the day it was collected",
          _record["fetched_at"][:10], TODAY)
check("every topic record carries the same keys, found or not",
      sorted(topics[0]), sorted(topics[2]))

dup_client = FakeClient(dict(TOPIC_ROUTES))
ir.fetch_topics([{"openalex_work_id": "W101", "citation_count": 1},
                 {"openalex_work_id": "W101", "citation_count": 2}], dup_client)
check("the same work is not looked up twice in one run", len(dup_client.seen), 1)


print("\n" + "=" * 70)
print("5. The whole batch: one payload, every number with its denominator")
print("=" * 70)

ROUTES = dict(TOPIC_ROUTES)
ROUTES.update({
    "W108": FakeResponse(200, work_payload("W108", "T88888", "Broken cell", 2023)),
    "T10091": FakeResponse(200, group_payload(MEASURED)),
    "T99999": FakeResponse(200, group_payload({0: 20, 1: 20})),
    "T88888": FakeResponse(503, {}),
})
batch = FakeClient(ROUTES)
papers = [
    {"pmid": "1", "openalex_work_id": "W101", "pub_year": "2023", "citation_count": 5},
    {"pmid": "2", "openalex_work_id": "W102", "pub_year": "2023", "citation_count": 0},
    {"pmid": "3", "doi": "10.5555/c-paper", "citation_count": 1},
    {"pmid": "4", "title": "no identifier", "citation_count": 2},
    {"pmid": "5", "openalex_work_id": "W105", "citation_count": 3},
    {"pmid": "6", "openalex_work_id": "W106", "citation_count": 4},
    {"pmid": "7", "openalex_work_id": "W107", "citation_count": 6},
    {"pmid": "8", "openalex_work_id": "W108", "pub_year": "2023", "citation_count": 9},
    {"pmid": "9", "openalex_work_id": "W109", "pub_year": "2023"},
]
payload = ir.locate_corpus(papers, batch)

by_pmid = {r["pmid"]: r for r in payload["records"]}
check("one record per paper, in corpus order",
      [r["pmid"] for r in payload["records"]], [str(i) for i in range(1, 10)])
check("paper 1 is placed", by_pmid["1"]["status"], ir.STATUS_LOCATED)
close("...at the percentile its count earns", by_pmid["1"]["percentile"],
      100.0 * (1399 + 379 + 38 + 24 + 24) / 3808)
check("paper 2 has zero citations and is placed at 0.0", by_pmid["2"]["percentile"], 0.0)
check("...which is a finding, not a failure", by_pmid["2"]["located"], True)
check("paper 3 lands in a 40-paper cell", by_pmid["3"]["status"],
      ir.STATUS_DISTRIBUTION_TOO_SMALL)
check("paper 4 has nothing to look up with", by_pmid["4"]["status"], ir.STATUS_NO_IDENTIFIER)
check("paper 5's lookup failed", by_pmid["5"]["status"], ir.STATUS_TOPIC_LOOKUP_FAILED)
check("paper 6 has no topic on file", by_pmid["6"]["status"], ir.STATUS_NO_TOPIC)
check("paper 7 has no year", by_pmid["7"]["status"], ir.STATUS_NO_YEAR)
check("paper 8's cell could not be fetched", by_pmid["8"]["status"],
      ir.STATUS_DISTRIBUTION_UNAVAILABLE)
check("paper 9 has no citation count to place", by_pmid["9"]["status"],
      ir.STATUS_NO_CITATION_COUNT)
check("...and cost no request, because there was nothing to place",
      [u for u in batch.seen if "W109" in u], [])

for _pmid, _record in by_pmid.items():
    if _record["status"] != ir.STATUS_LOCATED:
        check(f"paper {_pmid} is unlocatable and carries no percentile",
              (_record["located"], _record["percentile"]), (False, None))
        check_true(f"paper {_pmid} says why in a sentence", len(_record["unlocatable"] or "") > 20)
    check(f"paper {_pmid} is stamped with the day it was collected",
          _record["fetched_at"][:10], TODAY)
check("every record carries the same keys whatever happened",
      {tuple(sorted(r)) for r in payload["records"]}, {tuple(sorted(by_pmid["1"]))})

den = payload["denominator"]
check("the corpus total is stated", den["papers_total"], 9)
check("...and how many had a count to place", den["papers_with_citation_count"], 8)
check("...and how many got a topic", den["papers_with_topic"], 5)
check("...and how many got a reference cell", den["papers_with_reference_cell"], 3)
check("...and how many were finally placed", den["papers_located"], 2)
check("...and how many were not", den["papers_unlocatable"], 7)
check("located plus unlocatable is the whole corpus",
      den["papers_located"] + den["papers_unlocatable"], den["papers_total"])
check("every status has a count, including the zeros",
      sorted(den["by_status"]), sorted(ir.STATUS_ORDER))
check("the status counts sum to the corpus", sum(den["by_status"].values()), 9)
check("...and match the records one for one",
      den["by_status"][ir.STATUS_LOCATED], 2)
check("the cells actually requested are counted", den["reference_cells_requested"], 3)
check("...as are the ones that produced a usable population",
      den["reference_cells_usable"], 1)
check("topic requests are counted", den["topic_requests"], 7)
check("distribution requests are counted", den["distribution_requests"], 3)
check("...and the reuse that saved one is counted too",
      den["distribution_cache_hits"], 1)

check("the same (topic, year) cell is fetched once for the whole run",
      len([u for u in batch.seen if "T10091" in u and "group_by" in u]), 1)
check("...and two papers still share it",
      [r["pmid"] for r in payload["records"]
       if r["topic_id"] == "T10091" and r["year"] == 2023 and r["located"]], ["1", "2"])

cells = {(c["topic_id"], c["year"]): c for c in payload["cells"]}
check("one row per cell touched", len(cells), 3)
check("...naming how many of this corpus's papers fell in it",
      cells[("T10091", 2023)]["papers_in_corpus"], 2)
check("...and the population it was measured over",
      cells[("T10091", 2023)]["population"], 3808)
check("a cell that could not be fetched is still listed, with its status",
      cells[("T88888", 2023)]["status"], ir.STATUS_DISTRIBUTION_UNAVAILABLE)
check("...and a population of zero rather than a missing key",
      cells[("T88888", 2023)]["population"], 0)

check("the payload states the floor it used",
      payload["min_reference_population"], ir.MIN_REFERENCE_POPULATION)
check("...the percentile definition", payload["method"], ir.PERCENTILE_METHOD)
check("...and where the reference population came from",
      payload["reference_population_source"], ir.REFERENCE_POPULATION_SOURCE)
check("the payload is dated today", payload["generated_at"][:10], TODAY)
check("the caveats travel with the numbers", payload["caveats"], ir.IMPACT_REFERENCE_CAVEATS)

check("an empty corpus is a payload, not a crash",
      ir.locate_corpus([], FakeClient({}))["denominator"]["papers_total"], 0)
check("...with every status key still present and zero",
      sorted(ir.locate_corpus([], FakeClient({}))["denominator"]["by_status"]),
      sorted(ir.STATUS_ORDER))


print("\n" + "=" * 70)
print("6. The request this module makes, and the request it does not")
print("=" * 70)

dist_urls = [u for u in batch.seen if "group_by" in u]
decoded = unquote_plus(dist_urls[0])
check_true("the distribution request is the verified one: group_by=cited_by_count",
           "group_by=cited_by_count" in decoded)
check_true("...filtered by primary_topic.id", "primary_topic.id:T" in decoded)
check_true("...and by publication_year", "publication_year:2023" in decoded)
check_true("...against api.openalex.org/works",
           decoded.startswith("https://api.openalex.org/works?"))
check_true("the query is recorded in the cell row so a reader can repeat it",
           "group_by=cited_by_count" in unquote_plus(cells[("T10091", 2023)]["query"]))

check("no other host is contacted",
      sorted({u.split("/")[2] for u in batch.seen}), ["api.openalex.org"])


print("\n" + "=" * 70)
print("7. What this module does not claim, and what it did not overturn")
print("=" * 70)

source = open(ir.__file__, encoding="utf-8").read()
for banned in ("import requests", "import numpy", "import pandas", "import urllib3",
               "from requests", "from numpy", "from pandas"):
    check_true(f"the module does not `{banned}`", banned not in source)
check_true("all HTTP goes through the package's own client",
           "from .http_client import" in source)

# The four `*_impact_reference_json` names and the glob were added when `cite
# --percentile` was wired up: the module produced a payload and had nowhere to
# put it, which is the same shape `journal_risk` solved with `save_risk_json` /
# `find_latest_risk_json` / `load_risk_json`. Listing them here rather than
# loosening the assertion to a subset check keeps this a statement about the
# module's surface, which is what makes it worth failing on.
check("every exported name is in `__all__` and nothing else is",
      sorted(ir.__all__),
      ["CITATION_COUNT_FIELD", "CITATION_COUNT_SOURCE_FIELD", "DISTRIBUTION_METHOD",
       "IMPACT_REFERENCE_CAVEATS", "IMPACT_REFERENCE_GLOB", "MIN_REFERENCE_POPULATION",
       "PERCENTILE_METHOD", "REFERENCE_POPULATION_SOURCE", "STATUS_DISTRIBUTION_READY",
       "STATUS_DISTRIBUTION_TOO_SMALL", "STATUS_DISTRIBUTION_UNAVAILABLE",
       "STATUS_LOCATED", "STATUS_NO_CITATION_COUNT", "STATUS_NO_IDENTIFIER",
       "STATUS_NO_TOPIC", "STATUS_NO_YEAR", "STATUS_ORDER", "STATUS_REASONS",
       "STATUS_TOPIC_FOUND", "STATUS_TOPIC_LOOKUP_FAILED", "UNLOCATABLE_STATUSES",
       "fetch_reference_distribution", "fetch_topics",
       "find_latest_impact_reference_json", "load_impact_reference_json",
       "locate_corpus", "percentile_in_distribution",
       "save_impact_reference_json"])

# The round-trip the three new names exist for. `save` then `load` has to give
# back what went in, and `load` has to survive a file whose `records` key is
# missing rather than raising on it — a run that died halfway writes exactly that.
_rt_dir = tempfile.mkdtemp()
_rt_payload = {"schema_version": 1, "records": [{"pmid": "1", "percentile": 12.5}],
               "denominator": {"papers_total": 1, "papers_located": 1}}
_rt_path = ir.save_impact_reference_json(_rt_payload, _rt_dir, timestamp="20240101_000000")
check_true("save writes the dated name the glob looks for",
           os.path.basename(_rt_path) == "impact_reference_20240101_000000.json")
check("find_latest returns it", ir.find_latest_impact_reference_json(_rt_dir), _rt_path)
check("load gives back the records", ir.load_impact_reference_json(_rt_path)["records"],
      _rt_payload["records"])
check("...and the denominator", ir.load_impact_reference_json(_rt_path)["denominator"],
      _rt_payload["denominator"])

_half = os.path.join(_rt_dir, "impact_reference_20240102_000000.json")
with open(_half, "w", encoding="utf-8") as _handle:
    json.dump({"schema_version": 1}, _handle)
check("a file with no records loads as empty rather than raising",
      ir.load_impact_reference_json(_half)["records"], [])
check("...and its missing denominator does not become a fabricated one",
      ir.load_impact_reference_json(_half)["denominator"], {})
check("find_latest with none present is None",
      ir.find_latest_impact_reference_json(tempfile.mkdtemp()), None)
for _name in ("MIN_REFERENCE_POPULATION", "PERCENTILE_METHOD", "DISTRIBUTION_METHOD",
              "REFERENCE_POPULATION_SOURCE"):
    check_true(f"`{_name}` is exported so a report can print it", _name in ir.__all__)

# The objection this module is not allowed to have overturned.
old = ranking.RANKING_EXCLUSIONS["not_computable_here"]
check("the old refusal is still one entry, untouched", len(old), 1)
check_true("...still headed by the position it refuses",
           old[0][0] == "A position inside a reference population")
check_true("...and still says the corpora on a page are the ones a user loaded",
           "the ones a user chose to load" in old[0][1])

doc = (ir.__doc__ or "").lower()
check_true("this module names the register it sits beside",
           "ranking_exclusions" in doc)
check_true("...and says that objection is about the loaded corpora, not this",
           "loaded" in doc or "on a page" in doc)
check_true("...and does not sort or rank anybody",
           "sort" not in [w.strip(".,`") for w in doc.split()])

caveat_text = " ".join(ir.IMPACT_REFERENCE_CAVEATS.values()).lower()
check_true("a caveat says in words that unplaced is not low",
           "not a low percentile" in caveat_text)
check_true("...that the cell is one topic in one year in one database",
           "openalex" in caveat_text)
check_true("...and that the paper is inside its own reference population",
           "itself" in caveat_text)
check("the caveat codes do not collide with the existing registers",
      [code for code in ir.IMPACT_REFERENCE_CAVEATS if not code.startswith("IMP-")], [])


print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(1 if _failed else 0)
