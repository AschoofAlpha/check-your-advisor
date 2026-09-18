#!/usr/bin/env python3
"""
Chart tests for the advisor profile figure set (docs/profile-visual-spec.md).

Every fixture is synthetic and every metric dict is produced by calling the real
functions in `profile.metrics`, so the figures are tested against the shapes they will
actually be handed — including the `suppressed` flags, which are computed by the metric
layer and never restated here.

The three properties each figure is checked for are the ones that stop a chart lying:

  1. A suppressed aggregate renders a visible plate that states the actual n and the
     floor, and every underlying dot still renders. Below the floor the rows are the
     metric, so a figure that loses them is worse than one that loses the median.
  2. The denominator appears inside the SVG, not only in the caption. A chart gets
     screenshotted and separated from its surrounding prose.
  3. Empty input produces a stated sentence, never an empty axis and never a crash.

Fully offline: no network, no matplotlib, no pytest.

Run: python tests/test_charts.py
"""

from __future__ import annotations

import os
import re
import sys
from xml.etree import ElementTree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor.profile import charts, metrics  # noqa: E402

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
# Fixture builders — plain dicts in the shapes metrics.py expects
# ============================================================


def person(
    name: str,
    *,
    stratum: str,
    first_year: int,
    last_year: int | None = None,
    appearances: int = 2,
    first_slots: int = 0,
    first_lead_year: int | None = None,
    left: bool = False,
    right: bool = False,
    years: list[int] | None = None,
    lead_years: list[int] | None = None,
) -> dict:
    last_year = first_year if last_year is None else last_year
    return {
        "name": name,
        "marker": "",
        "stratum": stratum,
        "affiliation_signal": "unknown",
        "n_appearances": appearances,
        "n_first_slots": first_slots,
        "n_equal_contrib": 0,
        "first_year": first_year,
        "last_year": last_year,
        "left_censored": left,
        "right_censored": right,
        "flags": [],
        "span_years": last_year - first_year,
        "years": years or sorted({first_year, last_year}),
        "lead_years": lead_years or [],
        "first_date": f"{first_year}-03-01",
        "first_lead_year": first_lead_year,
    }


def enrich(roster: dict, people: list[dict]) -> dict:
    """Restate `years`, `lead_years` and `first_date` on a roster from the source people.

    `metrics.person_roster` now copies all three itself, so this is a no-op on a roster
    it built. It stays because the fixtures below hand `person_timeline_chart` rows they
    assembled by hand, and the figure must read the fields off the row it was given.
    """
    by_name = {item["name"]: item for item in people}
    for row in roster["rows"]:
        source = by_name[row["name"]]
        row["years"] = list(source["years"])
        row["lead_years"] = list(source["lead_years"])
        row["first_date"] = source["first_date"]
    return roster


def without_detail(roster: dict) -> dict:
    """Remove the per-appearance fields, leaving the roster shape that predates them.

    Still reachable in production: `person_timeline_chart` is a pure function and a
    caller can hand it rows read back from an older report's JSON. The figure must
    degrade to connectors rather than invent a mark for a year it cannot see.
    """
    for row in roster["rows"]:
        for field in ("years", "lead_years", "first_date"):
            row.pop(field, None)
    return roster


def paper(pmid: str, year: int, n_authors: int) -> dict:
    return {"pmid": pmid, "year": year, "n_authors": n_authors}


def svg_of(figure: dict) -> str:
    return figure["svg"]


def all_text(figure: dict) -> str:
    return figure["svg"] + figure["caption"] + figure["desc"]


def count(haystack: str, needle: str) -> int:
    return haystack.count(needle)


def group_of(svg: str, opening: str) -> str:
    """Slice one `<g>` and everything nested inside it.

    Counting tags rather than splitting on the first `</g>`: a naive split stops at the
    first nested dot, which would let "the subset lane contains no IQR bracket" pass by
    accident on a lane that contained one.
    """
    start = svg.index(opening) + len(opening)
    depth, index = 1, start
    while depth:
        opened = svg.find("<g", index)
        closed = svg.find("</g>", index)
        if closed == -1:
            return svg[start:]
        if opened != -1 and opened < closed:
            depth += 1
            index = opened + 2
        else:
            depth -= 1
            index = closed + 4
    return svg[start:index - 4]


# A cohort large enough to clear every floor: 8 people who led, 6 who have not,
# 2 too recent, 5 single-appearance, 2 senior collaborators.
def full_people() -> list[dict]:
    people = []
    for index in range(8):
        first = 2015 + index
        people.append(person(
            f"Lead {chr(65 + index)}", stratum="A", first_year=first, last_year=first + 2 + (index % 3),
            appearances=3 + index % 2, first_slots=1, first_lead_year=first + (index % 3),
            years=[first, first + 1, first + 2], lead_years=[first + (index % 3)],
        ))
    for index in range(6):
        first = 2014 + index
        people.append(person(
            f"Support {chr(65 + index)}", stratum="B", first_year=first, last_year=first + 1 + index % 2,
            appearances=2, years=[first, first + 1],
        ))
    for index in range(2):
        people.append(person(f"Recent {index}", stratum="B", first_year=2024, last_year=2025,
                             appearances=2, years=[2024, 2025], right=True))
    for index in range(5):
        people.append(person(f"Once {index}", stratum="C", first_year=2018 + index, appearances=1,
                             years=[2018 + index]))
    for index in range(2):
        people.append(person(f"Senior {index}", stratum="D", first_year=2016, last_year=2022,
                             appearances=4, years=[2016, 2019, 2022]))
    return people


FULL = full_people()
PROVENANCE = {
    "window_start_year": 2014,
    "window_end_year": 2025,
    "query": {"mindate": "2014/01/01", "maxdate": "2025/12/31"},
    "exclusions": {"hyperauthorship": ["30000001", "30000002", "30000003"]},
}

S2 = enrich(metrics.person_roster(FULL, n_strict=27, n_loose=23), FULL)
S3B = metrics.lead_slot_partition(FULL, window_end_year=2025, lag_years=3)
S4 = metrics.time_to_lead(FULL, S3B)
S5 = metrics.activity_span(FULL)
S9 = metrics.records_per_year(
    [paper(str(9000 + i), 2016 + i % 9, 3 + i % 6) for i in range(30)], [], 2014, 2025)
S10 = metrics.team_size(
    [paper(str(8000 + i), 2018, 2 + i % 9) for i in range(14)] + [paper("8999", 2019, 22)],
    {str(8000 + i): "A" for i in range(11)},
)

# The same corpus cut below every floor: 3 leads, 3 complete spans, 4 records.
SMALL = [
    person("Lead A", stratum="A", first_year=2020, last_year=2022, first_slots=1, first_lead_year=2021,
           years=[2020, 2021, 2022], lead_years=[2021]),
    person("Lead B", stratum="A", first_year=2019, last_year=2021, first_slots=1, first_lead_year=2019,
           years=[2019, 2021], lead_years=[2019]),
    person("Lead C", stratum="A", first_year=2018, last_year=2020, first_slots=1, first_lead_year=2020,
           years=[2018, 2020], lead_years=[2020]),
    person("Support A", stratum="B", first_year=2018, last_year=2019, years=[2018, 2019]),
    person("Once 0", stratum="C", first_year=2019, appearances=1, years=[2019]),
]
SMALL_S2 = enrich(metrics.person_roster(SMALL, n_strict=6, n_loose=5), SMALL)
SMALL_S3B = metrics.lead_slot_partition(SMALL, window_end_year=2024, lag_years=3)
SMALL_S4 = metrics.time_to_lead(SMALL, SMALL_S3B)
SMALL_S5 = metrics.activity_span(SMALL)
SMALL_S10 = metrics.team_size([paper(str(7000 + i), 2020, 3 + i) for i in range(4)],
                              {"7000": "A", "7001": "B"})

EMPTY_S2 = metrics.person_roster([], n_strict=0, n_loose=0)
EMPTY_S3B = metrics.lead_slot_partition([], window_end_year=2025)
EMPTY_S4 = metrics.time_to_lead([], EMPTY_S3B)
EMPTY_S5 = metrics.activity_span([])
EMPTY_S9 = metrics.records_per_year([], [], 2020, 2020)
EMPTY_S10 = metrics.team_size([], {})


# ============================================================
# Universal rules — every figure, every state
# ============================================================

print("\n[universal] rules that hold for every figure in every state")

DRAWN = {
    "C-GANTT": charts.person_timeline_chart(S2, S5, S9, PROVENANCE),
    "C-LAG": charts.time_to_lead_chart(S4, S3B, S2),
    "C-SPAN": charts.activity_span_chart(S5),
    "C-YEAR": charts.records_per_year_chart(S9, PROVENANCE),
    "C-TEAM": charts.team_size_chart(S10, PROVENANCE),
}
SUPPRESSED = {
    "C-LAG": charts.time_to_lead_chart(SMALL_S4, SMALL_S3B, SMALL_S2),
    "C-SPAN": charts.activity_span_chart(SMALL_S5),
    "C-TEAM": charts.team_size_chart(SMALL_S10, PROVENANCE),
}
EMPTY = {
    "C-GANTT": charts.person_timeline_chart(EMPTY_S2, EMPTY_S5, EMPTY_S9, {}),
    "C-LAG": charts.time_to_lead_chart(EMPTY_S4, EMPTY_S3B, EMPTY_S2),
    "C-SPAN": charts.activity_span_chart(EMPTY_S5),
    "C-YEAR": charts.records_per_year_chart(EMPTY_S9, {}),
    "C-TEAM": charts.team_size_chart(EMPTY_S10, {}),
}

# The id list is longer than this file's function list on purpose. C-POS and
# C-NET live in `profile.figures` because `charts.py` is held under 800 lines
# (acceptance item 35), not because they are a different kind of artifact — they
# use the same `svg.artifact` contract and are exercised in test_figures.py. What
# is asserted here is that the split is exactly that and nothing has gone
# missing: every id is drawn by one module or the other, and neither claims one
# the other also claims.
FROM_FIGURES = ("C-POS", "C-NET")
check("one function in this module per chart it owns",
      sorted(DRAWN), sorted(set(charts.CHART_IDS) - set(FROM_FIGURES)))
check("...and the id list is those plus the two in profile.figures",
      sorted(charts.CHART_IDS), sorted(set(DRAWN) | set(FROM_FIGURES)))
check("the sibling module supplies exactly those two, and by name",
      [name for name in ("byline_year_chart", "coauthor_network_chart")
       if not callable(getattr(charts, name, None))], [])
for chart_id, figure in DRAWN.items():
    svg = svg_of(figure)
    check_true(f"{chart_id} draws", figure["drawn"])
    check_true(f"{chart_id} declares its denominator inside the svg, not only in the caption",
               re.search(r"\d+ of \d+", svg))
    check_true(f"{chart_id} figcaption carries at least one k of N",
               re.search(r"\d+ of \d+", figure["caption"]))
    check_true(f"{chart_id} is an accessible image", 'role="img"' in svg and "aria-labelledby=" in svg)
    check_true(f"{chart_id} labels itself from an in-svg title and desc",
               f'id="{chart_id.lower()}-title"' in svg and f'id="{chart_id.lower()}-desc"' in svg)
    check(f"no percent sign reaches {chart_id} at any n", "%" in all_text(figure), False)
    check_true(f"{chart_id} returns the collapsed data table rows", isinstance(figure["rows"], list))
    check_true(f"{chart_id} emits a height attribute", 'height="' in svg)
    check_true(f"{chart_id} escapes markup rather than emitting it", "<script" not in svg)
    # A figure that is subtly malformed renders as a blank box in a browser, which is
    # the failure this whole module exists to prevent, and no string assertion sees it.
    try:
        ElementTree.fromstring(svg)
        well_formed = True
    except ElementTree.ParseError as exc:
        well_formed = f"{exc}"
    check(f"{chart_id} is well-formed XML", well_formed, True)

# R6/R7: nothing on any figure may read as a rank or a fitted trend.
#
# "score", "grade", "h-index" and "impact factor" came off this list when the
# package started computing citation numbers and a composite score. They are no
# longer prohibited words — but none of the five figures here plots any of them,
# so their removal loosens a rule this module was never testing anyway. What
# stays is the part that did not change: a figure may show a quantity, and may
# not show a position or a fitted direction. `rating` stays alongside `rank` and
# `percentile` because a star rating is an ordering wearing a different glyph.
for chart_id, figure in {**DRAWN, **SUPPRESSED}.items():
    blob = all_text(figure).lower()
    for word in ("trend", "slope", "rank", "rating", "percentile",
                 "top ", "best ", "productivity"):
        check(f"{chart_id} contains no '{word.strip()}'", word in blob, False)
    check(f"{chart_id} draws no polyline", "<polyline" in figure["svg"], False)
    check(f"{chart_id} draws no path", "<path" in figure["svg"], False)

check("two runs over one corpus emit byte-identical svg",
      charts.person_timeline_chart(S2, S5, S9, PROVENANCE)["svg"], DRAWN["C-GANTT"]["svg"])
check("every figure is paired with its caveat ids", sorted(charts.FIGURE_CAVEATS), sorted(charts.CHART_IDS))
# Two carry none, and that is a decision rather than an oversight: Section 7's
# qualifications are conditional on `measured` and Section 19's are prose rather
# than register entries, so pinning one to a figure would print a wording its own
# section does not.
check("C-POS and C-NET carry no caveat id, and the empty tuple is explicit",
      [charts.FIGURE_CAVEATS[key] for key in FROM_FIGURES], [(), ()])
check("C-GANTT carries the three caveats the spec assigns it",
      charts.FIGURE_CAVEATS["C-GANTT"], ("CAV-02", "CAV-03", "CAV-09"))
check("C-LAG carries the three caveats the spec assigns it",
      charts.FIGURE_CAVEATS["C-LAG"], ("CAV-06", "CAV-07", "CAV-08"))
check("C-SPAN carries the three caveats the spec assigns it",
      charts.FIGURE_CAVEATS["C-SPAN"], ("CAV-09", "CAV-10", "CAV-11"))
check("C-YEAR carries the two caveats the spec assigns it",
      charts.FIGURE_CAVEATS["C-YEAR"], ("CAV-17", "CAV-18"))
check("C-TEAM carries the one caveat the spec assigns it", charts.FIGURE_CAVEATS["C-TEAM"], ("CAV-19",))


# ============================================================
# Empty input — a stated sentence, never an empty axis, never a crash
# ============================================================

print("\n[empty] every figure survives empty input and says what is missing")

for chart_id, figure in EMPTY.items():
    check(f"{chart_id} draws nothing rather than an empty axis", figure["drawn"], False)
    check(f"{chart_id} emits no svg element in its degenerate state", figure["svg"], "")
    check_true(f"{chart_id} states why in the caption", len(figure["caption"]) > 40)
    check_true(f"{chart_id} states a count in the degenerate caption",
               re.search(r"\d", figure["caption"]))
    check(f"{chart_id} degenerate text carries no percent sign", "%" in all_text(figure), False)

check_true("C-GANTT names the empty cohort in the report's own words",
           charts.NO_COHORT_SENTENCE in EMPTY["C-GANTT"]["caption"])
check_true("C-LAG names the missing lead slots in the report's own words",
           charts.NO_LEAD_SENTENCE in EMPTY["C-LAG"]["caption"])
check_true("C-SPAN refuses a zero-height axis in words",
           "zero-height axis" in EMPTY["C-SPAN"]["caption"])
check_true("C-YEAR states that every bin is flagged rather than drawing all-wrong ink",
           "Every year bin in this corpus is partial or subject to PubMed indexing lag"
           in EMPTY["C-YEAR"]["caption"])
# A corpus with no year bin at all is a different degenerate state from one whose every
# bin is flagged, and it gets its own sentence rather than falling through to a blank.
no_bins = charts.records_per_year_chart({"years": [], "denominator": 0}, {})
check_false("C-YEAR with no year bin draws nothing", no_bins["drawn"])
check_true("C-YEAR refuses an empty axis in words", "empty axis" in no_bins["caption"])
check_true("C-TEAM refuses an empty axis in words", "empty axis" in EMPTY["C-TEAM"]["caption"])

# A figure function must survive a caller that hands it nothing at all.
check_false("C-GANTT survives a bare dict", charts.person_timeline_chart({})["drawn"])
check_false("C-LAG survives a bare dict", charts.time_to_lead_chart({})["drawn"])
check_false("C-SPAN survives a bare dict", charts.activity_span_chart({})["drawn"])
check_false("C-YEAR survives a bare dict", charts.records_per_year_chart({})["drawn"])
check_false("C-TEAM survives a bare dict", charts.team_size_chart({})["drawn"])
check("a refused report produces no figure at all", charts.figures_for_report({"refused": True}), {})
check("figures_for_report builds the whole set from one report",
      sorted(charts.figures_for_report({
          "refused": False,
          "metrics": {"s2": S2, "s3b": S3B, "s4": S4, "s5": S5, "s9": S9, "s10": S10},
          "provenance": PROVENANCE,
      })), sorted(charts.CHART_IDS))


# ============================================================
# C-GANTT — the row filter, the geometry and the degraded mode
# ============================================================

print("\n[C-GANTT] row selection, geometry, censoring")

gantt = DRAWN["C-GANTT"]
gantt_svg = svg_of(gantt)
n_rows = S2["by_stratum"]["A"] + S2["by_stratum"]["B"]
check("the timeline plots exactly the A+B cohort", len(gantt["rows"]), n_rows)
check("the plotted row count equals the span cohort denominator", len(gantt["rows"]),
      S5["cohort_denominator"])
check("the strata partition the roster", sum(S2["by_stratum"].values()), S2["denominator"])
for row in gantt["rows"]:
    check_false(f"no single-appearance row for {row['name']}", row["position label"] == "single appearance")
    check_false(f"no senior-collaborator row for {row['name']}", row["position label"] == "senior collaborator")
check_false("no single-appearance person is given a timeline row",
            any("Once" in row["name"] for row in gantt["rows"]))
check_false("no senior collaborator is given a timeline row",
            any("Senior" in row["name"] for row in gantt["rows"]))

height = float(re.search(r'<svg[^>]* height="([\d.]+)"', gantt_svg).group(1))
chrome = height - charts.ROW_PITCH * n_rows
check_true(f"chrome stays inside its budget (measured {chrome:.0f} px)", chrome <= charts.GANTT_CHROME_MAX)
check("height is exactly the row pitch times the rows plus chrome",
      round(charts.ROW_PITCH * n_rows + chrome, 2), round(height, 2))
# The measured defect: 277 rows at 21506 px. The same shape at the cohort filter must be
# an order of magnitude shorter, and the check is the formula, not a constant.
projected = charts.ROW_PITCH * 77 + charts.GANTT_CHROME_MAX
check_true(f"a 77-row cohort fits in {projected:.0f} px, over 10x under the measured 21506",
           projected <= 1988 and 21506 / projected >= 10)

check_true("names are printed in full so find-in-page reaches inside the figure",
           "Lead A" in gantt_svg and "Support A" in gantt_svg)
check_true("the row label prints the record count it cannot encode in marks", "n=3 records" in gantt_svg)
check_true("the subtitle declares the plotted count on the chart face",
           f"{n_rows} of {S2['denominator']} people plotted" in gantt_svg)
check_true("the subtitle declares the single-appearance count",
           f"{S2['by_stratum']['C']} people appear once" in gantt_svg)
check_true("the subtitle declares the senior-collaborator count",
           f"{S2['by_stratum']['D']} hold a senior slot" in gantt_svg)
check_true("the subtitle declares the strict/loose keying gap",
           "Strict keying finds 27 people, loose keying finds 23" in gantt_svg)
check_true("the subtitle declares the hyperauthorship records that left person-level analysis",
           "3 records with 50 or more authors" in gantt_svg)
check_true("the connector is disclaimed in the legend, not left to read as tenure",
           "not presence in the lab" in gantt_svg)
check_true("censoring is drawn as a dashed tail, not as a bounded segment",
           'class="censor right"' in gantt_svg)
check_true("the indexing-lag band is labelled on the chart", "PubMed indexing lag" in gantt_svg)

# Row order is the arrival cascade, and permuting the appearance counts must not move it.
order = [row["name"] for row in gantt["rows"]]
permuted = [dict(item) for item in FULL]
for index, item in enumerate(permuted):
    item["n_appearances"] = 20 - index if item["stratum"] in ("A", "B") else item["n_appearances"]
permuted_rows = enrich(metrics.person_roster(permuted, 27, 23), permuted)
check("row order does not change when appearance counts are permuted",
      [row["name"] for row in charts.person_timeline_chart(permuted_rows, S5, S9, PROVENANCE)["rows"]],
      order)
check("rows are ordered by first record then by name",
      order, [row["name"] for row in sorted(
          [r for r in S2["rows"] if r["stratum"] in ("A", "B")],
          key=lambda r: (r["first_date"], r["name"]))])

# The stratum-C strip is the mitigation for dropping the one-offs, so its counts must
# account for every one of them.
strip = re.findall(r'class="single-appearance-count"[^>]*>(\d+)<', gantt_svg)
check("the single-appearance strip accounts for every single-appearance person",
      sum(int(value) for value in strip), S2["by_stratum"]["C"])

marks = count(gantt_svg, 'class="mark lead"') + count(gantt_svg, 'class="mark plain"')
check_true("marks are drawn once the per-appearance fields are present", marks > 0)
check("a filled mark is drawn for every year holding a first-author record",
      count(gantt_svg, 'class="mark lead"'),
      sum(len(p["lead_years"]) for p in FULL if p["stratum"] in ("A", "B")))

# Without the per-appearance fields the honest figure is connectors only, said out loud.
bare = without_detail(metrics.person_roster(FULL, 27, 23))
bare_figure = charts.person_timeline_chart(bare, S5, S9, PROVENANCE)
check_true("a roster without per-appearance fields still draws", bare_figure["drawn"])
check_true("and titles itself spans only", "spans only" in bare_figure["svg"])
check("and draws no mark that would assert a record in a year it cannot see",
      count(bare_figure["svg"], 'class="mark'), 0)
check_true("and says so in the desc for a screen reader",
           "Per-appearance detail is unavailable" in bare_figure["desc"])


# ============================================================
# C-LAG — both strips, the zone, the floor
# ============================================================

print("\n[C-LAG] two strips on one axis, and what happens below the floor")

lag = DRAWN["C-LAG"]
lag_svg = svg_of(lag)
dots = count(lag_svg, 'class="dot lead"') + count(lag_svg, 'class="dot no-lead"') \
    + count(lag_svg, 'class="dot too-recent"')
check("every person in the cohort is one dot on this figure", dots, S3B["denominator"])
check("the dot count equals the timeline row count", dots, len(gantt["rows"]))
check("the upper strip is exactly the people who hold a first-author slot",
      count(lag_svg, 'class="dot lead"'), S3B["counts"]["holds_lead"])
check("the lower strip is exactly the people observed without one",
      count(lag_svg, 'class="dot no-lead"'), S3B["counts"]["observed_without_lead"])
check("the too-recent people are drawn, not dropped",
      count(lag_svg, 'class="dot too-recent"'), S3B["counts"]["too_recent"])
check_true("the too-recent zone is drawn and labelled",
           'class="too-recent-zone"' in lag_svg and "too recent to tell" in lag_svg)
check_true("the axis names records, not time in the lab",
           "integer years between first record and first record in the first-author slot" in lag_svg)
check_true("the zero bin is annotated in place", "debuted in the first-author slot:" in lag_svg)
check_true("the lower strip declares its own denominator",
           f"no first-author slot yet — {S3B['counts']['observed_without_lead'] + S3B['counts']['too_recent']}"
           f" of {S3B['denominator']}" in lag_svg)
check_true("single-appearance people are declared absent from both strips",
           "are in neither strip" in lag_svg)
check("exactly one median tick is drawn above the floor", count(lag_svg, 'class="median-tick"'), 1)

lag_small = SUPPRESSED["C-LAG"]
check_true("below the floor the median is refused in words",
           "median not computed" in lag_small["svg"])
check_true("and the plate carries the actual n and the floor",
           f"n={SMALL_S4['denominator']}, floor {metrics.MIN_N_AGGREGATE}" in lag_small["svg"])
check("and no median tick is drawn", count(lag_small["svg"], 'class="median-tick"'), 0)
check_true("and a plate is drawn where the aggregate would have been",
           'class="suppression-plate"' in lag_small["svg"])
check("and every dot stays, because below the floor the rows are the metric",
      count(lag_small["svg"], 'class="dot lead"'), SMALL_S4["denominator"])
check_true("the desc tells a screen reader the floor fired",
           "not computed" in lag_small["desc"])
check_true("the caption tells the same reader the same thing",
           "not computed" in lag_small["caption"])

# not_computable is a different state from suppressed: nobody led, but the people who
# have waited N years without leading are still a fact and still render.
none_led = [
    person("Support A", stratum="B", first_year=2015, last_year=2018, years=[2015, 2018]),
    person("Support B", stratum="B", first_year=2016, last_year=2019, years=[2016, 2019]),
    person("Recent A", stratum="B", first_year=2024, last_year=2025, years=[2024, 2025]),
]
none_s3b = metrics.lead_slot_partition(none_led, window_end_year=2025, lag_years=3)
none_s4 = metrics.time_to_lead(none_led, none_s3b)
none_figure = charts.time_to_lead_chart(none_s4, none_s3b, metrics.person_roster(none_led, 3, 3))
check_true("with nobody leading the figure still draws", none_figure["drawn"])
check_true("and prints the report's own sentence byte for byte",
           charts.NO_LEAD_SENTENCE in none_figure["svg"])
check("and the lower strip still renders one dot per waiting person",
      count(none_figure["svg"], 'class="dot no-lead"'), len(none_s4["still_without_lead"]))
check("and no median tick is drawn", count(none_figure["svg"], 'class="median-tick"'), 0)


# ============================================================
# C-SPAN — four censoring lanes, the confined bracket
# ============================================================

print("\n[C-SPAN] censoring is the structure, not a footnote")

span = DRAWN["C-SPAN"]
span_svg = svg_of(span)
check("one dot per person in the cohort", len(span["rows"]), S5["cohort_denominator"])
for key, label in charts._SPAN_LANES:
    check(f"the {label} lane draws every one of its people",
          count(span_svg, f'class="dot span {key}"'), S5["buckets"][key])
    check_true(f"the {label} lane declares its own count",
               f"{label} — {S5['buckets'][key]} of {S5['cohort_denominator']}" in span_svg)
check_true("single-appearance people are declared to have no span",
           "have no span by construction" in span_svg)
check_true("the median is labelled with the population it was computed over",
           "complete spans only" in span_svg)
check("exactly one median tick above the floor", count(span_svg, 'class="median-tick"'), 1)
check("exactly one IQR bracket above the floor", count(span_svg, 'class="iqr-bracket"'), 1)

span_small = SUPPRESSED["C-SPAN"]
check_true("below the floor the median is refused in words", "median not computed" in span_small["svg"])
check_true("and the plate names the complete spans it had and the floor it needed",
           f"{SMALL_S5['denominator']} complete spans, floor {metrics.MIN_N_AGGREGATE}"
           in span_small["svg"])
check("and no median tick survives", count(span_small["svg"], 'class="median-tick"'), 0)
check("and no IQR bracket survives", count(span_small["svg"], 'class="iqr-bracket"'), 0)
check("and the dot count is unchanged by the suppression",
      len(span_small["rows"]), SMALL_S5["cohort_denominator"])
check_true("the desc carries the suppression state", "not computed" in span_small["desc"])

same_year = [
    person("Twice A", stratum="B", first_year=2020, last_year=2020, appearances=2, years=[2020]),
    person("Twice B", stratum="B", first_year=2021, last_year=2021, appearances=2, years=[2021]),
]
same_figure = charts.activity_span_chart(metrics.activity_span(same_year))
check_true("two records in one calendar year get their own label",
           "two or more records in one calendar year" in same_figure["svg"])


# ============================================================
# C-YEAR — unit columns, flagged bins, zero-count years
# ============================================================

print("\n[C-YEAR] the most dangerous figure in the set")

year = DRAWN["C-YEAR"]
year_svg = svg_of(year)
check("one column per year in the window, including the empty ones",
      len(year["rows"]), len(S9["years"]))
check("one square per record", count(year_svg, 'class="unit"'),
      sum(row["count"] for row in S9["years"]))
zero_years = [row for row in S9["years"] if row["count"] == 0]
check("a zero-count year draws an empty labelled column rather than a gap",
      count(year_svg, 'class="empty-column"'), len(zero_years))
check_true("and its count is printed as zero", len(zero_years) == 0 or ">0<" in year_svg)
for row in S9["years"]:
    if row["partial"]:
        check_true(f"the partial bin {row['year']} carries a hatch", "url(#hatch-year)" in year_svg)
check("every flagged bin carries a visible text label",
      count(year_svg, 'class="flag-label"'),
      sum(1 for row in S9["years"] if row["partial"] or row["indexing_lag"]))
check_true("the partial label is spelled out", "PARTIAL" in year_svg)
check_true("the indexing-lag band is labelled and shaded",
           "PubMed indexing lag — undercounted" in year_svg)
check_true("the axis title says PubMed records, never papers",
           "PubMed records per calendar year" in year_svg and "papers per year" not in year_svg)
check_true("and the record-versus-paper distinction is stated for the reader",
           "not one research paper: publication type is not parsed" in year["caption"])
check_true("the panel title carries no direction word",
           "Records per year" in year_svg and "growth" not in year_svg.lower())
check("no line, curve or fit is drawn across the columns",
      "<polyline" in year_svg or "<path" in year_svg, False)

# A corpus where every bin is flagged is not a figure; it is a list of numbers.
all_flagged = metrics.records_per_year([paper("1", 2024, 3), paper("2", 2025, 4)], [], 2024, 2025)
flagged_figure = charts.records_per_year_chart(all_flagged, PROVENANCE)
check_false("with every bin flagged the counts are printed instead of drawn", flagged_figure["drawn"])
check_true("and the caption still carries every count",
           "2024: 1" in flagged_figure["caption"] and "2025: 1" in flagged_figure["caption"])
check("and the rows survive for the data table", len(flagged_figure["rows"]), 2)


# ============================================================
# C-TEAM — two lanes, two floors, one truncation
# ============================================================

print("\n[C-TEAM] two floors made visible, and the tail that is missing")

team = DRAWN["C-TEAM"]
team_svg = svg_of(team)
check("one dot per record in the main lane", count(team_svg, 'class="dot record'), S10["denominator"])
check("one dot per record in the subset lane",
      count(team_svg, 'class="dot subset-record"'), S10["subset"]["denominator"])
check_true("the large-team records are annotated rather than cut off",
           f"records with 20 or more authors: {S10['large_team_count']} of {S10['denominator']}"
           in team_svg)
check_true("the hyperauthorship exclusion is stated on the chart face with its count",
           "records with 50 or more authors are excluded from this chart (3 of them; PMIDs in provenance)"
           in team_svg)
check_true("the exclusion sentence is not styled as a caveat", 'class="exclusion-note"' in team_svg)
check("exactly one median tick and one IQR bracket in the main lane",
      (count(team_svg, 'class="median-tick"'), count(team_svg, 'class="iqr-bracket"')), (1, 1))

subset_lane = group_of(team_svg, '<g class="lane subset">')
check("the subset lane never draws an IQR bracket", "iqr-bracket" in subset_lane, False)
check("the subset lane above its own higher floor draws a median tick and nothing more",
      count(subset_lane, 'class="median-tick subset"'), 1)
check_true("and the subset lane declares the records it was computed over",
           f"over {S10['subset']['denominator']} records" in subset_lane)

team_small = SUPPRESSED["C-TEAM"]
check_true("below the main floor the median is refused in words",
           "median not computed" in team_small["svg"])
check_true("and the plate carries the actual n and the floor",
           f"n={SMALL_S10['denominator']}, floor {metrics.MIN_N_AGGREGATE}" in team_small["svg"])
check("and no median tick or IQR bracket is drawn in the main lane",
      (count(team_small["svg"], 'class="median-tick"'), count(team_small["svg"], 'class="iqr-bracket"')),
      (0, 0))
check("and every dot stays", count(team_small["svg"], 'class="dot record'), SMALL_S10["denominator"])
small_lane = group_of(team_small["svg"], '<g class="lane subset">')
check_true("the subset lane is never omitted, because an absent lane reads as no such papers",
           '<g class="lane subset">' in team_small["svg"])
check_true("and it names what it could not summarise and the floor it needed",
           f"records led by a lead-trainee or support candidate, floor {metrics.MIN_N_SUBSET_MEDIAN}"
           in small_lane)
check("and draws no median tick below its own floor", count(small_lane, "median-tick"), 0)
check("and never an IQR bracket in either state", "iqr-bracket" in small_lane, False)
check("and still draws every subset dot", count(small_lane, 'class="dot subset-record"'),
      SMALL_S10["subset"]["denominator"])
check_true("the desc carries both suppression states", "not computed" in team_small["desc"])
# "1 records" is the kind of sloppiness that makes a reader distrust the numbers beside it.
one_led = charts.team_size_chart(
    metrics.team_size([paper(str(4000 + i), 2022, 3 + i) for i in range(6)], {"4000": "A"}), PROVENANCE)
check_true("a one-record subset is described in the singular",
           "median not computed — 1 record led by" in one_led["svg"])


print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(0 if _failed == 0 else 1)
