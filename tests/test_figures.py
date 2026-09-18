#!/usr/bin/env python3
"""
Tests for the two figures in `profile.figures`: C-POS and C-NET.

Written against the shapes the real metric functions produce, not hand-typed
dicts: `metrics.pi_byline_positions` and `cohesion.coauthor_clusters` are called
here so that a change to either is caught by these figures rather than by a user.

The properties checked are the three `charts.py` states and one more that belongs
to these two figures specifically:

  1. A degenerate input produces a stated sentence, never an empty axis.
  2. The denominator appears inside the SVG, not only in the caption.
  3. Nothing is ordered by a count, anywhere.
  4. C-NET reads `cohesion`'s own edge list and re-derives no part of the
     partition — the whole reason that list exists in `cohesion.py`.

Fully offline: no network, no matplotlib, no pytest.

Run: python tests/test_figures.py
"""

from __future__ import annotations

import os
import sys
from xml.etree import ElementTree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor.profile import cohesion, figures, metrics  # noqa: E402

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


# ============================================================
# Fixtures — the shapes the metric layer actually emits
# ============================================================

PI = "Wei Zhang"


def author(name: str) -> dict:
    return {"name": name, "affiliation": "Nanhai University", "email": "",
            "orcid": "", "equal_contrib": False, "is_corresponding": False}


def prepared(pmid: int, year: int, names: list[str], pi_index, journal="Hepatology Reports") -> dict:
    """One record in the shape `roles.prepare_paper` hands to the metric layer."""
    persons = [author(name) for name in names]
    return {
        "pmid": str(pmid),
        "year": year,
        "journal": journal,
        "persons": persons,
        "authors": persons,
        "n_authors": len(persons),
        "pi_person_index": pi_index,
        "any_email": False,
    }


def svg_text(markup: str) -> str:
    """Every `<text>` node's content, concatenated. The figure as a reader sees it."""
    root = ElementTree.fromstring(markup)
    return " ".join(node.text or "" for node in root.iter()
                    if node.tag.endswith("text") or node.tag.endswith("title")
                    or node.tag.endswith("desc"))


# ============================================================
# 1. C-POS — records per year by byline position
# ============================================================

print("C-POS: the position-filtered corpus refuses rather than restating the filter")

FILTERED = metrics.pi_byline_positions([prepared(1, 2020, ["A B", PI], 1)], True)
POS_REFUSED = figures.byline_year_chart(FILTERED, {})
check_false("a position-filtered corpus draws nothing", POS_REFUSED["drawn"])
check("...and the artifact is empty markup, not an axis", POS_REFUSED["svg"], "")
check_true("...and says the filter is the metric",
           "harvested with a position filter" in POS_REFUSED["caption"])
check_true("...and says no empty axis was drawn in its place",
           "no empty axis is drawn in its place" in POS_REFUSED["caption"])
check("the refusal wording is the module constant, not a paraphrase",
      POS_REFUSED["caption"].startswith(figures.NOT_MEASURED_SENTENCE), True)

print("\nC-POS: an empty corpus states the zero rather than drawing it")

EMPTY = metrics.pi_byline_positions([], False)
POS_EMPTY = figures.byline_year_chart(EMPTY, {})
check_false("no record means no figure", POS_EMPTY["drawn"])
check_true("...and the sentence carries the denominator", "0 of 0" in POS_EMPTY["caption"])

print("\nC-POS: a real corpus draws one lane per position")

RECORDS = (
    # PI first on two records, last on four, middle on one, sole on one,
    # unlocated on one. Five lanes exercised, and a year (2019) with output in
    # only one of them.
    [prepared(1, 2018, [PI, "A B", "C D"], 0),
     prepared(2, 2019, [PI, "A B"], 0),
     prepared(3, 2020, ["A B", "C D", PI], 2),
     prepared(4, 2020, ["A B", PI], 1),
     prepared(5, 2021, ["A B", "C D", PI], 2),
     prepared(6, 2021, ["A B", PI], 1),
     prepared(7, 2021, ["A B", PI, "C D"], 1),
     prepared(8, 2022, [PI], 0),
     prepared(9, 2022, ["A B", "C D"], None)]
)
S7 = metrics.pi_byline_positions(RECORDS, False)
S9 = metrics.records_per_year(RECORDS, [], 2018, 2022)
POS = figures.byline_year_chart(S7, S9)

check_true("a measured corpus draws", POS["drawn"])
check("the metric and the figure agree on the denominator", S7["denominator"], 9)
INK = svg_text(POS["svg"])
check_true("every lane label carries its own k of N",
           all(f"{label} — " in INK for _, label in figures.BYLINE_LANES))
check_true("the sole-author lane is present even at n=1", "sole author — 1 of 9" in INK)
check_true("the unlocated lane is present rather than dropped",
           "not located on the byline — 1 of 9" in INK)
check_true("the denominator is inside the svg, not only in the caption", "of 9" in INK)
check_true("the caption says byline position is reported and not interpreted",
           "reported, never interpreted" in POS["caption"])
check_true("...and names the four words it will not use",
           all(word in POS["caption"] for word in ("senior", "junior", "better", "worse")))
check_false("no percent sign anywhere in the figure", "%" in POS["svg"])
check_false("...and none in the caption", "%" in POS["caption"])

check("one data-table row per year in the window, including the empty ones",
      [row["year"] for row in POS["rows"]], [2018, 2019, 2020, 2021, 2022])
check("the lane counts in the table sum to the drawn records",
      sum(row[label] for row in POS["rows"] for _, label in figures.BYLINE_LANES), 9)
check("the per-year totals match records_per_year's own bins",
      [row["records"] for row in POS["rows"]],
      [item["count"] for item in S9["years"]])
check_true("a partial bin is flagged in the table, not silently drawn as complete",
           POS["rows"][0]["partial"] and POS["rows"][-1]["partial"])
check_true("...and the indexing-lag bins are flagged too",
           POS["rows"][-1]["indexing lag"])
check_true("the lag band is named inside the svg", "PubMed indexing lag" in INK)

# The axis is records_per_year's, not the byline rows' own range. A year with
# records but nothing the metric could place must still get a labelled column,
# or the figure would silently shorten the window the section above it printed.
WIDE = figures.byline_year_chart(S7, metrics.records_per_year(RECORDS, [], 2016, 2024))
check("the year axis follows the window, not the rows",
      [row["year"] for row in WIDE["rows"]], list(range(2016, 2025)))

# A position the metric grew that this figure has no lane for is counted and
# named. Dropping it silently is the failure mode the whole figure set exists to
# remove, and a fixture is the only way to reach the branch.
INVENTED = dict(S7)
INVENTED["rows"] = list(S7["rows"]) + [{"pmid": "99", "year": 2020, "position": "co_first"}]
ODD = figures.byline_year_chart(INVENTED, S9)
check_true("a position with no lane is stated in the caption",
           "Positions with no lane in this figure" in ODD["caption"])
check_true("...and named with its count", "co_first 1" in ODD["caption"])

print("\nC-POS: lane height follows the tallest column, not the tallest year")

# The runaway-image failure this figure set exists to prevent, one lane deeper.
# A lane's height is set by the most records it holds in one year; sizing off the
# year's total across all five lanes makes every lane five times taller than its
# own content, and five lanes multiplies that again.
SPREAD = [prepared(6000 + i, 2020, [PI] + [f"A{i:03d} B"], 0) for i in range(20)]
SPREAD += [prepared(7000 + i, 2020, [f"A{i:03d} B", PI], 1) for i in range(20)]
SPREAD += [prepared(8000 + i, 2020, [f"A{i:03d} B", PI, f"C{i:03d} D"], 1) for i in range(20)]
SPREAD_FIG = figures.byline_year_chart(
    metrics.pi_byline_positions(SPREAD, False),
    metrics.records_per_year(SPREAD, [], 2020, 2020))
STACKED = [prepared(9000 + i, 2020, [PI] + [f"A{i:03d} B"], 0) for i in range(60)]
STACKED_FIG = figures.byline_year_chart(
    metrics.pi_byline_positions(STACKED, False),
    metrics.records_per_year(STACKED, [], 2020, 2020))


def svg_height(figure):
    return float(figure["svg"].split('height="')[1].split('"')[0])


check("both fixtures hold the same number of records", len(SPREAD), len(STACKED))
check_true("60 records spread over three lanes is shorter than 60 stacked in one",
           svg_height(SPREAD_FIG) < svg_height(STACKED_FIG))
check_true("...and the spread one stays a page rather than a poster",
           svg_height(SPREAD_FIG) < 900)
check_true("...and even the worst case does", svg_height(STACKED_FIG) < 1400)
check("every record is still drawn in the spread case — none is dropped to save height",
      sum(row[label] for row in SPREAD_FIG["rows"] for _, label in figures.BYLINE_LANES), 60)
check("...and in the stacked case", sum(row["records"] for row in STACKED_FIG["rows"]), 60)

print("\nC-POS: the svg is well-formed and carries its own accessible name")

ElementTree.fromstring(POS["svg"])  # raises if the markup is broken
check_true("the figure declares role=img", 'role="img"' in POS["svg"])
check_true("...and points at an in-svg title and desc",
           'aria-labelledby="c-pos-title c-pos-desc"' in POS["svg"])
check_true("the desc repeats the denominators in words", "of 9" in POS["desc"])


# ============================================================
# 2. C-NET — the co-author network, read off cohesion's own partition
# ============================================================

print("\nC-NET: below the cohesion floor nothing is drawn")

TINY = cohesion.coauthor_clusters([prepared(1, 2020, ["A B", PI], 1)], PI)
NET_TINY = figures.coauthor_network_chart(TINY, {})
check_false("under the floor the figure refuses", NET_TINY["drawn"])
check_true("...and prints the floor with the count that missed it",
           f"below the floor of {cohesion.MIN_N_COHESION}" in NET_TINY["caption"])
check_true("...and says no empty frame was drawn in its place",
           "no empty frame is drawn in its place" in NET_TINY["caption"])

print("\nC-NET: a corpus where nobody recurs says so instead of drawing dots")

LONERS = cohesion.coauthor_clusters(
    [prepared(i, 2020, [f"Solo{i:02d} Person", PI], 1) for i in range(1, 8)], PI)
NET_LONERS = figures.coauthor_network_chart(LONERS, {})
check_false("no recurring person means no network", NET_LONERS["drawn"])
check_true("...and the sentence says why, with both counts",
           "appears on exactly one record" in NET_LONERS["caption"])

print("\nC-NET: two disconnected groups, drawn as two panels")

# Six records in one collaboration, five in another that shares nobody. This is
# the shape that says "re-harvest with --orcid", and the figure has to make it
# visible without asserting it.
SPLIT_RECORDS = (
    [prepared(i, 2018 + i, ["Alpha Person", "Beta Person", PI], 2) for i in range(1, 7)]
    + [prepared(10 + i, 2020, ["Gamma Person", "Delta Person", PI], 2) for i in range(1, 6)]
)
SPLIT = cohesion.coauthor_clusters(SPLIT_RECORDS, PI)
check("the partition found the two groups", SPLIT["n_clusters"], 2)
NET = figures.coauthor_network_chart(SPLIT, {})
check_true("two disconnected groups draw", NET["drawn"])

NET_INK = svg_text(NET["svg"])
check_true("every panel prints its size against the corpus denominator",
           "cluster 1 — 6 of 11 records" in NET_INK and "cluster 2 — 5 of 11 records" in NET_INK)
check_true("the denominator is inside the svg", "of 11" in NET_INK)
check("one table row per person per panel",
      sorted({row["person"] for row in NET["rows"]}),
      ["Alpha Person", "Beta Person", "Delta Person", "Gamma Person"])
check("...and each row carries the cluster's size beside the person's own count",
      sorted((row["person"], row["records in this cluster"], row["cluster size"])
             for row in NET["rows"]),
      [("Alpha Person", 6, 6), ("Beta Person", 6, 6),
       ("Delta Person", 5, 5), ("Gamma Person", 5, 5)])
check_true("no name bridges the two panels, and the caption says what that means",
           "No name recurs in more than one drawn cluster" in NET["caption"])
check_true("...and immediately says it is not proof on its own",
           "not, on its own, proof of anything" in NET["caption"])
check_true("the caption names the fix rather than implying one",
           "--orcid" in NET["caption"])
check_true("the panel order is stated as not a ranking",
           "not a ranking" in NET_INK)
check_false("no percent sign in the network figure", "%" in NET["svg"])

print("\nC-NET: the edges come from cohesion, not from a second walk of the corpus")

edges = {(e["a"], e["b"]): e["n_records"] for e in SPLIT["clusters"][0]["edges"]}
check("cohesion emits the pair that holds the first cluster together",
      edges, {("Alpha Person", "Beta Person"): 6})
check("a line is drawn for every edge cohesion reported",
      NET["svg"].count('class="edge"'),
      sum(len(c["edges"]) for c in SPLIT["clusters"]))
check("...and a node for every recurring person cohesion reported",
      NET["svg"].count('class="node'),
      sum(len(c["recurring_people"]) for c in SPLIT["clusters"]))
# One threshold, not two. If the node filter and the edge filter could drift, a
# figure could draw an edge to a person it does not draw a node for.
_ENDPOINTS = ({e["a"] for c in SPLIT["clusters"] for e in c["edges"]}
              | {e["b"] for c in SPLIT["clusters"] for e in c["edges"]})
_NODES = {p["name"] for c in SPLIT["clusters"] for p in c["recurring_people"]}
check("every edge endpoint is also a node — one recurrence threshold, not two",
      sorted(_ENDPOINTS - _NODES), [])
check("...and the threshold is the module's, not a literal in two places",
      cohesion.RECURRENCE_MIN_RECORDS, 2)
# Non-recurring people are deliberately absent: one record explains nothing about
# why that record joined a cluster, and drawing them puts the corpus's whole
# author list on the page.
SOME_ONE_OFFS = cohesion.coauthor_clusters(
    SPLIT_RECORDS + [prepared(30, 2021, ["Alpha Person", "Passer By", PI], 2)], PI)
check_false("a person on one record of a cluster is not a node",
            "Passer By" in svg_text(figures.coauthor_network_chart(SOME_ONE_OFFS, {})["svg"]))
check("...and is not in the data table either",
      [row for row in figures.coauthor_network_chart(SOME_ONE_OFFS, {})["rows"]
       if row["person"] == "Passer By"], [])

print("\nC-NET: a name in two clusters is the diagnostic and is marked as one")

BRIDGED_RECORDS = SPLIT_RECORDS + [
    prepared(20, 2022, ["Alpha Person", "Gamma Person", PI], 2),
    prepared(21, 2022, ["Alpha Person", "Gamma Person", PI], 2),
]
BRIDGED = cohesion.coauthor_clusters(BRIDGED_RECORDS, PI)
check("a shared co-author merges the two groups into one", BRIDGED["n_clusters"], 1)
NET_BRIDGED = figures.coauthor_network_chart(BRIDGED, {})
check_true("...and the single panel now holds everyone who recurs",
           len({row["person"] for row in NET_BRIDGED["rows"]}) == 4)
check_true("Alpha and Gamma are joined by an edge because they share a byline",
           any({e["a"], e["b"]} == {"Alpha Person", "Gamma Person"}
               for e in BRIDGED["clusters"][0]["edges"]))

# Bridging is measured over the panels actually on the page, so the ring on a
# node and the sentence in the caption can never disagree. Construct it directly:
# two clusters that each contain a person of the same name is impossible through
# the union-find, so the ring branch is reached with a partition fixture.
FORGED = {
    "denominator": 12, "min_n": cohesion.MIN_N_COHESION, "suppressed": False,
    "n_clusters": 2, "largest_size": 7, "singleton_clusters": 0,
    "clusters": [
        {"size": 7, "pmids": [], "journals": [{"journal": "J One", "count": 7}],
         "year_range": (2018, 2021),
         "recurring_people": [{"name": "Shared Name", "n_records": 4},
                              {"name": "Only Here", "n_records": 2}],
         "edges": [{"a": "Only Here", "b": "Shared Name", "n_records": 2}], "detailed": True},
        {"size": 5, "pmids": [], "journals": [], "year_range": None,
         "recurring_people": [{"name": "Shared Name", "n_records": 3}],
         "edges": [], "detailed": True},
    ],
}
NET_RING = figures.coauthor_network_chart(FORGED, {})
check_true("a name in two drawn panels is called out in the caption",
           "1 name(s) recur in more than one drawn cluster and are ringed: Shared Name"
           in NET_RING["caption"])
check("...and both of its nodes carry the bridging class",
      NET_RING["svg"].count('class="node bridging"'), 2)
check("...while a name in one panel does not",
      NET_RING["svg"].count('class="node"'), 1)
check_true("the data table names the other cluster for each side",
           {row["also recurs in cluster"] for row in NET_RING["rows"]
            if row["person"] == "Shared Name"} == {"1", "2"})
check_true("a cluster with no journal recorded says so rather than printing a blank",
           "no journal recorded" in svg_text(NET_RING["svg"]))

print("\nC-NET: the page stays bounded when the corpus does not")

MANY = {
    "denominator": 200, "min_n": cohesion.MIN_N_COHESION, "suppressed": False,
    "n_clusters": 40, "largest_size": 5, "singleton_clusters": 0,
    "clusters": [
        {"size": 5, "pmids": [], "journals": [{"journal": f"J {i}", "count": 5}],
         "year_range": (2020, 2021),
         "recurring_people": [{"name": f"P{i:02d}-{j} Person", "n_records": 2}
                              for j in range(20)],
         "edges": [], "detailed": True}
        for i in range(20)
    ],
}
NET_MANY = figures.coauthor_network_chart(MANY, {})
check("only the panel budget is drawn", NET_MANY["svg"].count('class="cluster"'),
      figures.MAX_NETWORK_PANELS)
check_true("...and the rest are stated, not dropped",
           f"{20 - figures.MAX_NETWORK_PANELS} further cluster(s)" in NET_MANY["caption"])
check("every person is still in the data table, drawn panel or not",
      len(NET_MANY["rows"]), 20 * 20)
check_true("a crowded panel moves its names to the table rather than overprinting",
           "listed in the data table below" in svg_text(NET_MANY["svg"]))
check_true("the figure height stays a page rather than a poster",
           float(NET_MANY["svg"].split('height="')[1].split('"')[0]) < 2000)

# A label is truncated to whatever room is left between the ring and the panel
# edge. The ring grows with the node count, so a fixed truncation fits a two-node
# panel and runs a twelve-node one into its neighbour.
def _labels(cluster_size):
    partition = {
        "denominator": 40, "min_n": cohesion.MIN_N_COHESION, "suppressed": False,
        "n_clusters": 1, "largest_size": 8, "singleton_clusters": 0,
        "clusters": [{
            "size": 8, "pmids": [], "journals": [], "year_range": None,
            "recurring_people": [
                {"name": f"Averylongsurname{i:02d} Given Name", "n_records": 2}
                for i in range(cluster_size)],
            "edges": [], "detailed": True}],
    }
    svg = figures.coauthor_network_chart(partition, {})["svg"]
    import re as _re
    # `<text>` only. The `<title>` tooltip beside each node carries the whole
    # name by design — it is the mouse-only copy of what the data table holds —
    # and counting it here would measure the wrong string.
    return [t for t in _re.findall(r"<text[^>]*>([^<>]*)</text>", svg)
            if "Averylongsurname" in t]


_SMALL, _BIG = _labels(2), _labels(12)
check("both panels label every node they draw", (len(_SMALL), len(_BIG)), (2, 12))
check_true("a crowded panel truncates its labels harder than a sparse one",
           max(len(t) for t in _BIG) < max(len(t) for t in _SMALL))
check_true("...and never past the point of being useless", min(len(t) for t in _BIG) >= 6)

print("\nC-NET: the svg is well-formed and carries its own accessible name")

ElementTree.fromstring(NET["svg"])
check_true("the figure declares role=img", 'role="img"' in NET["svg"])
check_true("...and points at an in-svg title and desc",
           'aria-labelledby="c-net-title c-net-desc"' in NET["svg"])
check_true("the desc says ring position carries no meaning",
           "Ring position carries no meaning" in NET["desc"])


# ============================================================
# 3. Determinism — two runs over one corpus are byte-identical
# ============================================================

print("\nboth figures are deterministic")

check("C-POS renders identically twice",
      figures.byline_year_chart(S7, S9)["svg"], POS["svg"])
check("C-NET renders identically twice",
      figures.coauthor_network_chart(SPLIT, {})["svg"], NET["svg"])
# The partition is rebuilt from the corpus, not reused, so this also proves the
# figure does not depend on dict iteration order anywhere in cohesion.
check("...including when the partition is recomputed from the corpus",
      figures.coauthor_network_chart(
          cohesion.coauthor_clusters(SPLIT_RECORDS, PI), {})["svg"], NET["svg"])


print(f"\nSummary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
sys.exit(1 if _failed else 0)
