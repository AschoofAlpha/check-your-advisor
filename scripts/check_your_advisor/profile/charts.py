"""
The static figure set for the advisor profile report (docs/profile-visual-spec.md).

The report carries seven figures, one per section that earns one. Four are drawn
here — C-GANTT (2), C-LAG (4), C-YEAR (9), C-TEAM (10) — and the other three, C-SPAN
(5), C-POS (7) and C-NET (19), are in the sibling `figures` module only to hold this
file under 800 lines. C-SPAN moved there when every caption became translatable,
because that change made each sentence one line longer. `figures_for_report` below assembles all seven. Everything else
stays text or a table; the refusals and their reasons are recorded in Section 6 of the
visual spec.

Every function is pure — metric dict in, figure dict out — and every figure is a
hand-emitted SVG string. Suppression is a rendering decision that already lives in
Python, so `MIN_N_AGGREGATE` and `MIN_N_SUBSET_MEDIAN` are imported rather than
restated: a floor cannot then drift between the number and the picture of the number.
SVG `<text>` is real text, so find-in-page reaches a person inside a figure and a screen
reader reads it. There is no matplotlib here, so the `profile` command has no
import-dependent drawing path to degrade.

Three rules are enforced here rather than left to the caller, because they are what
stop a chart from lying:

1. A suppressed aggregate is replaced by a visible plate carrying the actual n and the
   floor. An empty axis, a zero-height bar or a missing element all read as "the value
   is zero" or "the chart is broken", and below the floor the rows *are* the metric, so
   every dot stays.
2. Every figure states its own denominator inside the SVG. A chart gets screenshotted
   and separated from its caption.
3. Censoring, partial bins and indexing-lag bins carry a shape or a hatch and a text
   label, never colour alone, so the figures survive greyscale and print.

Nothing here ranks people, sorts anyone by a count, computes a rate, or emits the
percent sign at any sample size.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from ..i18n import en, using
from .figures import STACK_BUDGET, activity_span_chart, byline_year_chart, coauthor_network_chart
from .metrics import MIN_N_AGGREGATE, MIN_N_SUBSET_MEDIAN
from .report import STRATUM_LABEL
from .svg import (
    BAND,
    HAIRLINE,
    INK,
    MUTED,
    WHITE,
    Bands,
    aggregate,
    arrow,
    artifact,
    cells,
    circle,
    document,
    fmt,
    hatch,
    lane_label,
    legend,
    line,
    mid,
    plate,
    positions,
    preamble,
    prose_artifact,
    rect,
    stack,
    tag,
    text,
    tooltip,
    wrap,
)

CANVAS_WIDTH = 1100.0
# Section 4.5: `height = ROW_PITCH * n_rows + chrome`, with chrome <= GANTT_CHROME_MAX.
ROW_PITCH = 24.0
GANTT_CHROME_MAX = 140.0
GANTT_LABEL_WIDTH = 340.0
# A dot stack is bounded in pixels, not in dots: a fixed pitch is what turned one
# crowded axis into a 21506 px image, which is the defect this module exists to fix.
COLUMN_BUDGET = 300.0

LARGE_TEAM_MIN_AUTHORS = 20  # reported as an annotation, never used as a cut-off
HYPERAUTHORSHIP_MIN_AUTHORS = 50

CHART_IDS = ("C-GANTT", "C-LAG", "C-SPAN", "C-YEAR", "C-TEAM", "C-POS", "C-NET")

# Acceptance item 23, held here so one figure cannot be paired with two caveat sets in
# two places. The HTML builder reads the text itself from `report["caveats"]`, verbatim.
# C-POS and C-NET carry none: Section 7's qualifications are conditional on `measured`
# and Section 19's are prose, so pinning one would print a wording twice.
FIGURE_CAVEATS: dict[str, tuple[str, ...]] = {
    "C-GANTT": ("CAV-02", "CAV-03", "CAV-09"),
    "C-LAG": ("CAV-06", "CAV-07", "CAV-08"),
    "C-SPAN": ("CAV-09", "CAV-10", "CAV-11"),
    "C-YEAR": ("CAV-17", "CAV-18"),
    "C-TEAM": ("CAV-19",),
    "C-POS": (),
    "C-NET": (),
}

# Byte-identical to `report._time_to_lead_body` (acceptance item 18): the figure and the
# prose beside it must say one sentence, not two paraphrases of it.
NO_LEAD_SENTENCE = "no person in this corpus holds a first-author slot"
NO_COHORT_SENTENCE = "no person in this corpus appears more than once and holds no senior slot"


_figure = artifact


def _records(count: int) -> str:
    """"1 records" is the kind of sloppiness that makes a reader distrust the numbers
    beside it, and this document is asking to be trusted about small samples."""
    return en("{count} record", count=count) if count == 1 else en("{count} records", count=count)


_prose = prose_artifact


# --- C-GANTT: person activity timeline (report Section 2) --------------------------


def person_timeline_chart(
    s2: dict[str, Any],
    s5: dict[str, Any] | None = None,
    s9: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    One row per person in the A+B cohort: appears at least twice, never holds the senior
    slot. That is the population every aggregate in the report is computed over, so the
    chart and the numbers finally describe the same people.

    Single-appearance people are named beside the figure and counted in the per-year
    strip along its foot, never given a row: plotting them is what produced 277 rows of
    which 190 carried one dot, and the spec excludes them from every aggregate.
    """
    s9, prov = s9 or {}, provenance or {}
    by_stratum = s2.get("by_stratum") or {}
    all_rows = list(s2.get("rows") or [])
    rows = [row for row in all_rows if row.get("stratum") in ("A", "B")]
    # `person_roster` preserves `build_people`'s (first_date, name) order, so this sort
    # is a no-op there; it runs anyway so the order is a property of the figure and not
    # an assumption about the caller. Without `first_date` the incoming order is the
    # only correct one available and is left alone.
    if rows and all("first_date" in row for row in rows):
        rows.sort(key=lambda row: (row.get("first_date") or "", row.get("name") or ""))

    denominator = int(s2.get("denominator") or len(all_rows))
    single_count, senior_count = int(by_stratum.get("C", 0)), int(by_stratum.get("D", 0))
    hyper = list((prov.get("exclusions") or {}).get("hyperauthorship") or [])
    head = en("{n_rows} of {denominator} people plotted", n_rows=len(rows), denominator=denominator)

    if not rows:
        return _prose("C-GANTT", (
            en("{no_cohort_sentence}: {head}. {single_count} people appear once ({c}) and "
               "are named beside this figure; {senior_count} hold a senior slot ({d}) and "
               "sit in a separate panel. No timeline is drawn, and no empty axis is drawn "
               "in its place.",
               no_cohort_sentence=en(NO_COHORT_SENTENCE), head=head, single_count=single_count, c=en(STRATUM_LABEL['C']), senior_count=senior_count, d=en(STRATUM_LABEL['D']))))

    # The added per-appearance fields are what turn a span into a set of marks. Without
    # them the honest figure is the connectors alone, said out loud in the title; a
    # connector drawn as a mark would assert a record in a year with none.
    detailed = all("years" in row for row in rows)
    span = ([int(row["first_year"]) for row in all_rows] + [int(row["last_year"]) for row in all_rows]
            + [int(item["year"]) for item in (s9.get("years") or [])]
            + [int(prov[key]) for key in ("window_start_year", "window_end_year") if prov.get(key)])
    sub_lines = wrap(
        en("{head}: everyone with two or more records who never holds the senior "
           "slot. {single_count} people appear once ({c}), named beside this figure "
           "and counted in the strip below the axis, never plotted. {senior_count} "
           "hold a senior slot ({d}) and sit in a separate panel. Strict keying finds "
           "{n_strict} people, loose keying finds {n_loose}: that gap is the error "
           "bar on every count here. {n_hyper} records with "
           "{hyperauthorship_min_authors} or more authors left person-level analysis; "
           "anyone visible only through them is not on this chart (PMIDs in "
           "provenance).",
           head=head, single_count=single_count, c=en(STRATUM_LABEL['C']), senior_count=senior_count, d=en(STRATUM_LABEL['D']), n_strict=s2.get('n_strict', '?'), n_loose=s2.get('n_loose', '?'), n_hyper=len(hyper), hyperauthorship_min_authors=HYPERAUTHORSHIP_MIN_AUTHORS), 175, 4)

    header = 20.0 + 11.0 * len(sub_lines) + 26.0
    height = ROW_PITCH * len(rows) + header + 46.0
    plot_x0, plot_x1 = GANTT_LABEL_WIDTH + 18, CANVAS_WIDTH - 26
    bands = Bands(min(span), max(span), plot_x0, plot_x1)
    plot_bottom = header + ROW_PITCH * len(rows)

    title = en("Person activity timeline")
    if not detailed:
        title += en(" — spans only, per-appearance detail unavailable")
    body = preamble(title, sub_lines)

    # Driven off the metric's own flag, never off a hardcoded year.
    lag_years = sorted(int(item["year"]) for item in (s9.get("years") or []) if item.get("indexing_lag"))
    if lag_years:
        left = bands.edge(lag_years[0])
        body += [rect(left, header, bands.edge(lag_years[-1] + 1) - left, plot_bottom - header,
                       fill=BAND),
                 text(left + 3, header - 16, en("PubMed indexing lag"), 9.0, fill=MUTED)]
    body += [bands.ticks(header - 5), line(plot_x0, header, plot_x1, header, stroke=HAIRLINE)]

    table: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        y = header + ROW_PITCH * index + ROW_PITCH / 2
        first, last = int(row["first_year"]), int(row["last_year"])
        marker, label = row.get("marker") or "", en(STRATUM_LABEL[row["stratum"]])
        # The count is printed because a mark covers a whole year, so counting marks
        # gives the wrong number. It never enters the sort (R5).
        name_text = en("{name}{marker} — n={n_appearances} records — {label}", name=row['name'], marker=marker, n_appearances=row['n_appearances'], label=label)
        censoring = en(", ").join(en(side) for side, flag in (("left", row.get("left_censored")),
                                                          ("right", row.get("right_censored"))) if flag) or en("none")
        group = [tooltip(en("{name_text}; {first} to {last}; censoring {censoring}", name_text=name_text, first=first, last=last, censoring=censoring)),
                 text(14, y + 4, name_text, 11.0 if cells(name_text) <= 58 else 9.5)]
        if last > first:
            # Deliberately the weakest ink here: it is the interval between two
            # publication dates, not evidence of presence in the lab (CAV-09).
            group.append(line(bands.center(first), y, bands.center(last), y, stroke=HAIRLINE,
                               **{"stroke-width": 1, "class": "interval"}))
        if row.get("left_censored"):
            group += [line(bands.center(first), y, plot_x0 - 14, y,
                            **{"stroke-width": 1, "stroke-dasharray": "3 2", "class": "censor left"}),
                      line(plot_x0 - 14, y, plot_x0 - 8, y - 3.5, **{"stroke-width": 1}),
                      line(plot_x0 - 14, y, plot_x0 - 8, y + 3.5, **{"stroke-width": 1})]
        if row.get("right_censored"):
            group += [line(bands.center(last), y, plot_x1 + 14, y,
                            **{"stroke-width": 1, "stroke-dasharray": "3 2", "class": "censor right"}),
                      line(plot_x1 + 14, y, plot_x1 + 8, y - 3.5, **{"stroke-width": 1}),
                      line(plot_x1 + 14, y, plot_x1 + 8, y + 3.5, **{"stroke-width": 1})]
        lead_years = {int(value) for value in (row.get("lead_years") or [])}
        # Union rather than `years` alone: a year holding a first-author record is a year
        # holding a record, so a lead year missing from `years` is a plumbing fault that
        # must show as a mark rather than be silently dropped.
        for year in sorted({int(value) for value in (row.get("years") or [])} | lead_years) if detailed else []:
            lead = year in lead_years
            note = (en("one or more records in the first-author slot") if lead
                    else en("records that year, none in the first-author slot"))
            group.append(tag("g", {"class": "mark lead" if lead else "mark plain"},
                              tooltip(en("{name}{marker} — {year} — {note}", name=row['name'], marker=marker, year=year, note=en(note)))
                              + rect(bands.center(year) - 4.5, y - 4.5, 9.0, 9.0,
                                      fill=INK if lead else WHITE, stroke=INK,
                                      **{"stroke-width": 1.4})))
        body.append(tag("g", {"class": "row"}, "".join(group)))
        table.append({
            "name": f"{row['name']}{marker}", "position label": label,
            "records": row["n_appearances"], "first record": first, "last record": last,
            "censoring": censoring,
            "years with a record": ", ".join(str(v) for v in (row.get("years") or [])) or en("not available"),
            "years with a first-author record":
                ", ".join(str(v) for v in sorted(lead_years)) or en("none recorded"),
        })

    # Counts only, aligned to the same year columns: the structural answer to the
    # sharpest objection to the row filter — that dropping the one-offs makes the lab
    # look steadier than the record shows.
    strip_y = plot_bottom + 15
    body.append(text(14, strip_y, en("{c}, by first year — {single_count} total", c=en(STRATUM_LABEL['C']), single_count=single_count),
                     9.0, fill=MUTED))
    singles = Counter(int(row["first_year"]) for row in all_rows if row.get("stratum") == "C")
    body += [mid(bands.center(year), strip_y, str(count), 9.5, fill=MUTED,
                  **{"class": "single-appearance-count"}) for year, count in sorted(singles.items())]

    legend_y = plot_bottom + 33
    body += [
        legend(14, legend_y, [("filled", en("year with one or more first-author records")),
                               ("hollow", en("year with records, none in the first-author slot")),
                               ("band", en("PubMed indexing lag"))]),
        legend(14, legend_y + 11,
                [("hairline", en("line = interval between first and last record, not presence in the lab")),
                 ("dashed-arrow",
                  en("dashed = censored at the window edge, the record continues past it"))]),
        text(CANVAS_WIDTH - 26, legend_y + 11, en("a mark means one or more records in that year"),
              9.0, fill=MUTED, **{"text-anchor": "end"}),
    ]

    caption = (
        en("{head}. Rows are the A+B cohort, ordered by first record then by name, "
           "never by any count. {single_count} of {denominator} people appear once "
           "and are named beside this figure; {senior_count} of {denominator} hold a "
           "senior slot and sit in a separate panel. {n_hyper} records with "
           "{hyperauthorship_min_authors} or more authors left person-level analysis.",
           head=head, single_count=single_count, denominator=denominator, senior_count=senior_count, n_hyper=len(hyper), hyperauthorship_min_authors=HYPERAUTHORSHIP_MIN_AUTHORS))
    desc = caption + (
        en(" Each row is one person across {low} to {high}; a filled square is a year "
           "with a first-author record, a hollow square a year with records but none "
           "in that slot, and a dashed tail marks censoring at the window edge.",
           low=bands.low, high=bands.high) if detailed else
        en(" Per-appearance detail is unavailable, so only the interval between each person's first and "
           "last record is drawn; no mark asserts a record in any particular year."))
    return _figure("C-GANTT", svg=document("C-GANTT", CANVAS_WIDTH, height, title, desc, "".join(body)),
                   caption=caption, desc=desc, rows=table, drawn=True)


# --- C-LAG: time to a first-author slot (report Section 4) -------------------------


def time_to_lead_chart(
    s4: dict[str, Any],
    s3b: dict[str, Any] | None = None,
    s2: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Two strips on one shared axis: people who reached a first-author slot above it,
    people who have not yet below it.

    The lower strip is not optional and not separable. The upper strip alone reads as
    "I will lead a paper in about a year", the exact misreading CAV-08 exists to
    prevent. This figure renders all three lead-slot buckets, which is why the partition
    gets no chart of its own.
    """
    s3b = s3b or {}
    buckets = s3b.get("buckets") or {}
    by_name = lambda item: (item.get("lag_years", item.get("years_observed")), item.get("name") or "")  # noqa: E731
    lead = sorted(s4.get("values") or [], key=by_name)
    waiting = sorted(s4.get("still_without_lead") or [], key=by_name)
    recent = sorted(buckets.get("too_recent") or [], key=by_name)
    lag_years = int(s3b.get("lag_years") or 3)
    cohort = int(s3b.get("denominator") or (len(lead) + len(waiting) + len(recent)))
    single_count = int(((s2 or {}).get("by_stratum") or {}).get("C", 0))
    denominator = int(s4.get("denominator") or 0)
    suppressed, not_computable = bool(s4.get("suppressed")), bool(s4.get("not_computable"))

    if not (lead or waiting or recent):
        return _prose("C-LAG", (
            en("{no_lead_sentence}, and no person is observed without one: the cohort "
               "holds 0 of {cohort} people. Nothing is plotted, and no empty axis is "
               "drawn in its place.",
               no_lead_sentence=en(NO_LEAD_SENTENCE), cohort=cohort)))

    top_values = [int(item["lag_years"]) for item in lead]
    bottom = [(item, "waiting") for item in waiting] + [(item, "recent") for item in recent]
    bottom_values = [int(item["years_observed"]) for item, _ in bottom]
    high = max(top_values + bottom_values + [lag_years])
    sub_lines = wrap(
        en("{n_lead} of {cohort} people in the cohort hold a first-author slot and "
           "are plotted above the axis; {n_waiting} of {cohort} have been observed at "
           "least {lag_years} years without one and are plotted below it; {n_recent} "
           "of {cohort} were first seen inside the trailing {lag_years} years and sit "
           "in the shaded zone, too recent to tell. {single_count} people appear once "
           "and are in neither strip. The axis counts years between two records, not "
           "time in the lab.",
           n_lead=len(lead), cohort=cohort, n_waiting=len(waiting), lag_years=lag_years, n_recent=len(recent), single_count=single_count), 175, 3)

    top_pitch, top_r, top_stack = stack(top_values, STACK_BUDGET)
    low_pitch, low_r, low_stack = stack(bottom_values, STACK_BUDGET)
    header = 20.0 + 11.0 * len(sub_lines) + 8.0
    top_height = max(34.0, 12.0 + top_pitch * top_stack)
    low_height = max(30.0, 12.0 + low_pitch * low_stack)
    axis_y = header + 22.0 + top_height
    height = axis_y + low_height + 54.0
    plot_x0, plot_x1 = 226.0, CANVAS_WIDTH - 26
    bands = Bands(0, high, plot_x0, plot_x1)

    title = en("Time to a first-author slot")
    body = preamble(title, sub_lines)
    # The zone covers the lower strip only. A stratum-A person with a one-year lag is an
    # observed lead, not a case too recent to judge, so shading them would grey out the
    # very fact the upper strip reports.
    body += [rect(bands.edge(0), axis_y, bands.edge(lag_years) - bands.edge(0), low_height,
                   fill=BAND, **{"class": "too-recent-zone"}),
             text(bands.edge(0) + 4, axis_y + 12, en("too recent to tell"), 9.0, fill=MUTED),
             line(plot_x0, axis_y, plot_x1, axis_y, **{"stroke-width": 1}),
             bands.ticks(axis_y + low_height + 12),
             text(plot_x0, axis_y + low_height + 26,
                   en("integer years between first record and first record in the first-author slot"),
                   9.5, fill=MUTED)]

    # A bracket across both strips, so a screenshot that crops the lower one is visibly cut.
    bracket_x = plot_x0 - 12
    body += [line(bracket_x, axis_y - top_height, bracket_x, axis_y + low_height, stroke=MUTED),
             line(bracket_x, axis_y - top_height, bracket_x + 6, axis_y - top_height, stroke=MUTED),
             line(bracket_x, axis_y + low_height, bracket_x + 6, axis_y + low_height, stroke=MUTED),
             lane_label(14, axis_y - 8, en("held a first-author slot — {n_lead} of {cohort}", n_lead=len(lead), cohort=cohort)),
             lane_label(14, axis_y + 16,
                        en("no first-author slot yet — {n_bottom} of {cohort}", n_bottom=len(bottom), cohort=cohort))]

    table: list[dict[str, Any]] = []
    for (value, index), item in zip(positions(top_values), lead, strict=True):
        body.append(tag("g", {"class": "dot lead"},
                         tooltip(en("{name}{marker} — {value} year(s) to a first-author record", name=item['name'], marker=item.get('marker', ''), value=value))
                         + circle(bands.center(value), axis_y - 8 - index * top_pitch, top_r, fill=INK)))
        table.append({"name": f"{item['name']}{item.get('marker', '')}",
                      "strip": en("held a first-author slot"), "years": value})
    for (value, index), (item, kind) in zip(positions(bottom_values), bottom, strict=True):
        cx, cy = bands.center(value), axis_y + 8 + index * low_pitch
        mark = circle(cx, cy, low_r, fill=WHITE if kind == "waiting" else BAND, stroke=INK,
                        **{"stroke-width": 1.2})
        note = en("first seen too recently to tell")
        if kind == "waiting":
            mark += arrow(cx + low_r, cy, 8.0)
            note = en("observed at least this long with no first-author record")
        body.append(tag("g", {"class": f"dot {'no-lead' if kind == 'waiting' else 'too-recent'}"},
                         tooltip(en("{name}{marker} — {value} year(s) observed — {note}", name=item['name'], marker=item.get('marker', ''), value=value, note=en(note))) + mark))
        table.append({"name": f"{item['name']}{item.get('marker', '')}",
                      "strip": (en("observed without a first-author slot") if kind == "waiting"
                                else en("too recent to tell")), "years": value})

    if not_computable:
        # "Nobody led" and "k people have gone N years without leading" are different
        # facts; the second survives, so the lower strip keeps rendering.
        body.append(text(plot_x0, axis_y - top_height + 16, en(NO_LEAD_SENTENCE), 11.0))
    else:
        body.append(text(bands.center(0) + 10, axis_y - top_height + 12,
                         en("debuted in the first-author slot: {count_at_zero} of {denominator}", count_at_zero=int(s4.get('count_at_zero') or 0), denominator=denominator), 9.5, fill=MUTED))
    if suppressed:
        # Below the floor the raw values are the metric, so every dot stays and only the
        # aggregate is replaced.
        body.append(plate(plot_x0, header + 2,
                           en("median not computed — n={denominator}, floor {min_n_aggregate}", denominator=denominator, min_n_aggregate=MIN_N_AGGREGATE)))
    elif s4.get("median") is not None:
        median_x = bands.center(float(s4["median"]))
        body += [line(median_x, axis_y - top_height, median_x, axis_y,
                       **{"stroke-width": 1.2, "class": "median-tick"}),
                 mid(median_x, header + 14,
                      en("median {fmt} years over {denominator} people", fmt=fmt(s4['median']), denominator=denominator), 10.0)]
    body.append(legend(14, height - 8, [
        ("dot", en("one person who has held a first-author slot")),
        ("tail", en("one person observed at least this long with none")),
        ("band", en("too recent to tell"))]))

    caption = (en("{n_lead} of {cohort} people in the cohort hold a first-author slot; "
                  "{n_waiting} of {cohort} have been observed at least {lag_years} years "
                  "without one; {n_recent} of {cohort} are too recent to tell. "
                  "{single_count} single-appearance people are in neither strip.",
                  n_lead=len(lead), cohort=cohort, n_waiting=len(waiting), lag_years=lag_years, n_recent=len(recent), single_count=single_count))
    if suppressed:
        caption += (en(" Median not computed — n={denominator}, below the floor of "
                       "{min_n_aggregate}; the individual values are the metric at this sample "
                       "size and are all plotted.",
                       denominator=denominator, min_n_aggregate=MIN_N_AGGREGATE))
    elif s4.get("median") is not None:
        caption += en(" Median {fmt} years over {denominator} people.", fmt=fmt(s4['median']), denominator=denominator)
    desc = caption + (en(" Both strips share one axis of integer years between two records; the lower "
                         "strip carries a right-pointing tail meaning at least this long with no "
                         "first-author record."))
    if suppressed or not_computable:
        desc += en(" The median is not computed at n={denominator}, below the floor of "
                   "{min_n_aggregate}.",
                   denominator=denominator, min_n_aggregate=MIN_N_AGGREGATE)
    return _figure("C-LAG", svg=document("C-LAG", CANVAS_WIDTH, height, title, desc, "".join(body)),
                   caption=caption, desc=desc, rows=table, drawn=True)


# --- C-YEAR: records per year (report Section 9) -----------------------------------


def records_per_year_chart(s9: dict[str, Any], provenance: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    One small square per record, stacked in its year's column.

    Unit columns rather than bars, no connecting line and nothing fitted: the eye
    extends a length difference into a slope whether or not one is drawn, and the last
    two bins are censored by indexing lag, so anything read as a direction here is an
    artifact (CAV-17). Zero-count years draw as empty labelled columns because an
    omitted column reads as "no data" and an empty one reads as "zero".
    """
    prov = provenance or {}
    years = list(s9.get("years") or [])
    denominator = int(s9.get("denominator") or sum(int(item["count"]) for item in years))
    flagged = [item for item in years if item.get("partial") or item.get("indexing_lag")]
    table = [{"year": int(item["year"]), "records": int(item["count"]),
              "partial": bool(item.get("partial")), "indexing lag": bool(item.get("indexing_lag"))}
             for item in years]

    if not years:
        return _prose("C-YEAR", en("No year bin exists for the {denominator} records in this corpus, so no "
                                   "column is drawn and no empty axis is drawn in its place.",
                                   denominator=denominator))
    if len(flagged) == len(years):
        # A figure whose every mark is annotated as wrong is not a figure.
        listing = en("; ").join(en("{year}: {count}", year=item['year'], count=item['count']) for item in years)
        return _prose("C-YEAR", (
            en("Every year bin in this corpus is partial or subject to PubMed indexing "
               "lag ({n_flagged} of {n_years}), so the counts are printed instead of "
               "drawn: {listing}. {denominator} records in total.",
               n_flagged=len(flagged), n_years=len(years), listing=listing, denominator=denominator)), table)

    low, high = int(years[0]["year"]), int(years[-1]["year"])
    peak = max([1] + [int(item["count"]) for item in years])
    query = prov.get("query") or {}
    sub_lines = wrap(
        en("{denominator} records, {low} to {high}, window {mindate} to {maxdate}. "
           "{n_flagged} of {n_years} year bins are partial or undercounted by PubMed "
           "indexing lag and are hatched or shaded below. One square is one PubMed "
           "record: reviews, letters, comments and case reports are counted alongside "
           "primary research.",
           denominator=denominator, low=low, high=high, mindate=query.get('mindate', en('not recorded')), maxdate=query.get('maxdate', en('not recorded')), n_flagged=len(flagged), n_years=len(years)), 175, 3)

    unit = max(2.0, min(9.0, COLUMN_BUDGET / peak))
    gap = 1.0 if unit > 4 else 0.5
    header = 20.0 + 11.0 * len(sub_lines) + 12.0
    plot_height = max(60.0, peak * (unit + gap) + 10.0)
    baseline = header + plot_height
    height = baseline + 56.0
    plot_x0, plot_x1 = 26.0, CANVAS_WIDTH - 26
    bands = Bands(low, high, plot_x0, plot_x1)
    column = min(26.0, bands.width * 0.62)

    body = [hatch("hatch-year")] + preamble(en("Records per year"), sub_lines)
    lag_bins = [int(item["year"]) for item in years if item.get("indexing_lag")]
    if lag_bins:
        left = bands.edge(min(lag_bins))
        body += [rect(left, header, bands.edge(max(lag_bins) + 1) - left, plot_height, fill=BAND),
                 text(left + 3, header + 10, en("PubMed indexing lag — undercounted"), 9.0, fill=MUTED)]

    for item in years:
        year, count = int(item["year"]), int(item["count"])
        cx = bands.center(year)
        left = cx - column / 2
        notes = []
        if item.get("partial"):
            notes.append(en("PARTIAL"))
            body.append(rect(left, header, column, plot_height, fill="url(#hatch-year)",
                              stroke=HAIRLINE, **{"stroke-width": 0.6, "class": "flagged partial"}))
        if item.get("indexing_lag"):
            notes.append(en("INDEXING LAG"))
        if count == 0:
            # An empty labelled column, not a gap: the two read differently.
            body.append(rect(left, baseline - 8, column, 8.0, fill="none", stroke=HAIRLINE,
                              **{"stroke-dasharray": "2 2", "class": "empty-column"}))
        body += [tag("g", {"class": "unit"}, tooltip(en("{year} — one of {count} records", year=year, count=count))
                      + rect(left + (column - unit) / 2, baseline - (index + 1) * (unit + gap),
                              unit, unit, fill=INK)) for index in range(count)]
        body += [mid(cx, header - 2, str(count), 9.0, fill=MUTED),
                 mid(cx, baseline + 14, str(year), 9.5, fill=MUTED)]
        if notes:
            body.append(mid(cx, baseline + 26, " ".join(notes), 8.0, fill=MUTED,
                             **{"class": "flag-label"}))

    body += [line(plot_x0, baseline, plot_x1, baseline),
             text(plot_x0, baseline + 40, en("PubMed records per calendar year"), 9.5, fill=MUTED),
             legend(plot_x0 + 220, baseline + 40, [
                 ("hatch", en("partial bin — the window opens or closes mid-year")),
                 ("band", en("PubMed indexing lag — undercounted"))])]

    caption = (en("{denominator} records, {low} to {high}. {n_flagged} of {n_years} year "
                  "bins are partial or undercounted by PubMed indexing lag and are hatched "
                  "or shaded. One square is one PubMed record, not one research paper: "
                  "publication type is not parsed.",
                  denominator=denominator, low=low, high=high, n_flagged=len(flagged), n_years=len(years)))
    desc = caption + (en(" Each column is one calendar year including years with zero records; no line "
                         "and no curve is drawn across them."))
    return _figure("C-YEAR", svg=document("C-YEAR", CANVAS_WIDTH, height, en("Records per year"), desc,
                                           "".join(body)),
                   caption=caption, desc=desc, rows=table, drawn=True)


# --- C-TEAM: team size (report Section 10) -----------------------------------------


def team_size_chart(s10: dict[str, Any], provenance: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Two lanes on one author-count axis: every record above, the records led by a
    lead-trainee or support candidate below.

    Two lanes rather than an overlay because the cuts carry different floors (5 and 10)
    and the subset carries no IQR, so an overlay would imply a comparability the metric
    cannot supply. The subset lane always draws its dots: an absent lane reads as "no
    trainee-led papers", a different and much worse claim than "too few to summarise".
    """
    prov = provenance or {}
    values = sorted(int(value) for value in (s10.get("values") or []))
    subset = s10.get("subset") or {}
    subset_values = sorted(int(value) for value in (subset.get("values") or []))
    denominator = int(s10.get("denominator") or len(values))
    subset_n = int(subset.get("denominator") or len(subset_values))
    hyper = list((prov.get("exclusions") or {}).get("hyperauthorship") or [])
    large = int(s10.get("large_team_count") or 0)

    if not values:
        return _prose("C-TEAM", en("0 of {denominator} records carry an author count, so no dot is drawn and "
                                   "no empty axis is drawn in its place.",
                                   denominator=denominator))

    low, high = min(values + subset_values), max(values + subset_values)
    exclusion = (en("records with {hyperauthorship_min_authors} or more authors are excluded "
                    "from this chart ({n_hyper} of them; PMIDs in provenance)",
                    hyperauthorship_min_authors=HYPERAUTHORSHIP_MIN_AUTHORS, n_hyper=len(hyper)))
    sub_lines = wrap(
        en("One dot per record over {denominator} records, {low} to {high} authors. "
           "Records with {large_team_min_authors} or more authors: {large} of "
           "{denominator}, drawn in the same lane inside a ring. The lower lane is "
           "the {subset_n} of {denominator} records led by a lead-trainee or support "
           "candidate. Author count on this researcher's papers is not lab headcount.",
           denominator=denominator, low=low, high=high, large_team_min_authors=LARGE_TEAM_MIN_AUTHORS, large=large, subset_n=subset_n), 175, 3)

    main_pitch, main_r, main_stack = stack(values, STACK_BUDGET)
    sub_pitch, sub_r, sub_stack = stack(subset_values, STACK_BUDGET / 2)
    header = 20.0 + 11.0 * len(sub_lines) + 26.0
    main_base = header + max(40.0, 14.0 + main_pitch * main_stack)
    sub_base = main_base + 30.0 + max(30.0, 14.0 + sub_pitch * sub_stack)
    tick_drop = 44.0 if (subset.get("suppressed") or subset.get("median") is None) else 30.0
    height = sub_base + tick_drop + 28.0
    plot_x0, plot_x1 = 226.0, CANVAS_WIDTH - 26
    bands = Bands(low, high, plot_x0, plot_x1)

    body = preamble(en("Team size"), sub_lines)
    body.append(aggregate(
        bands, header - 14, None if s10.get("suppressed") else s10.get("median"), s10.get("iqr"),
        en("median {fmt} authors over {denominator} records", fmt=fmt(s10.get('median')), denominator=denominator),
        (plot_x0, header - 24, en("median not computed — n={denominator}, floor {min_n_aggregate}", denominator=denominator, min_n_aggregate=MIN_N_AGGREGATE))))
    body += [line(plot_x0, main_base, plot_x1, main_base),
             lane_label(14, main_base - 4, en("all records — {denominator} of {denominator}", denominator=denominator))]
    for value, index in positions(values):
        heavy = value >= LARGE_TEAM_MIN_AUTHORS
        cy = main_base - 8 - index * main_pitch
        dot = circle(bands.center(value), cy, main_r, fill=INK)
        if heavy:
            # A concentric ring, not a heavier fill: at 20+ authors the record is
            # annotated, never binned away, and the ring survives greyscale.
            dot += circle(bands.center(value), cy, main_r + 2.5, fill="none", stroke=INK,
                          **{"stroke-width": 1})
        body.append(tag("g", {"class": "dot record" + (" large" if heavy else "")},
                        tooltip(en("one record with {value} authors", value=value)
                                + (en(", {large_team_min_authors} or more", large_team_min_authors=LARGE_TEAM_MIN_AUTHORS) if heavy else "")) + dot))
    body += [text(plot_x1, main_base + 14,
                   en("records with {large_team_min_authors} or more authors: {large} of "
                      "{denominator}",
                      large_team_min_authors=LARGE_TEAM_MIN_AUTHORS, large=large, denominator=denominator),
                   9.5, fill=MUTED, **{"text-anchor": "end"}),
             # Provenance-derived, not a caveat: without it the figure cannot show the
             # consortium papers that are exactly the "one of twenty" fear it answers.
             text(plot_x1, main_base + 26, exclusion, 9.5, fill=MUTED,
                   **{"text-anchor": "end", "class": "exclusion-note"})]

    lane = [line(plot_x0, sub_base, plot_x1, sub_base, stroke=HAIRLINE),
            lane_label(14, sub_base - 4, en("led by a lead-trainee or support candidate — {subset_n} of {denominator}", subset_n=subset_n, denominator=denominator))]
    lane += [tag("g", {"class": "dot subset-record"},
                  tooltip(en("one record with {value} authors, led by a lead-trainee or support "
                             "candidate",
                             value=value))
                  + circle(bands.center(value), sub_base - 8 - index * sub_pitch, sub_r, fill=WHITE,
                            stroke=INK, **{"stroke-width": 1.2}))
             for value, index in positions(subset_values)]
    # A median tick and never an IQR band: `team_size.subset` carries no `iqr` key.
    lane.append(aggregate(
        bands, sub_base + 4, None if subset.get("suppressed") else subset.get("median"), None,
        en("median {fmt} authors over {subset_n} records", fmt=fmt(subset.get('median')), subset_n=subset_n),
        (plot_x0, sub_base + 4, en("median not computed — {records} led by a lead-trainee or support "
                                   "candidate, floor {min_n_subset_median}",
                                   records=_records(subset_n), min_n_subset_median=MIN_N_SUBSET_MEDIAN)), "subset"))
    body.append(tag("g", {"class": "lane subset"}, "".join(lane)))

    body += [bands.ticks(sub_base + tick_drop),
             text(plot_x0, sub_base + tick_drop + 14, en("authors per record"), 9.5, fill=MUTED),
             legend(14, height - 6, [
                 ("dot", en("one record")),
                 ("dot-open", en("one record led by a lead-trainee or support candidate"))])]

    caption = (en("One dot per record over {denominator} records. Records with "
                  "{large_team_min_authors} or more authors: {large} of {denominator}. Lower "
                  "lane: {subset_n} of {denominator} records led by a lead-trainee or "
                  "support candidate. {exclusion}.",
                  denominator=denominator, large_team_min_authors=LARGE_TEAM_MIN_AUTHORS, large=large, subset_n=subset_n, exclusion=exclusion))
    if s10.get("suppressed") or s10.get("median") is None:
        caption += en(" Median not computed — n={denominator}, below the floor of "
                      "{min_n_aggregate}.",
                      denominator=denominator, min_n_aggregate=MIN_N_AGGREGATE)
    else:
        caption += en(" Median {fmt} authors over {denominator} records.", fmt=fmt(s10['median']), denominator=denominator)
    if subset.get("suppressed") or subset.get("median") is None:
        caption += (en(" Subset median not computed — {records}, below the floor of "
                       "{min_n_subset_median}.",
                       records=_records(subset_n), min_n_subset_median=MIN_N_SUBSET_MEDIAN))
    else:
        caption += en(" Subset median {fmt} authors over {subset_n} records.", fmt=fmt(subset['median']), subset_n=subset_n)
    desc = caption + (en(" Both lanes share one integer axis of authors per record; the subset lane "
                         "carries a median tick and never an interquartile bracket."))
    table = [{"authors": value, "records": values.count(value),
              "records led by a lead-trainee or support candidate": subset_values.count(value)}
             for value in sorted(set(values))]
    return _figure("C-TEAM", svg=document("C-TEAM", CANVAS_WIDTH, height, en("Team size"), desc,
                                           "".join(body)),
                   caption=caption, desc=desc, rows=table, drawn=True)


# --- Assembly ----------------------------------------------------------------------


def figures_for_report(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """
    Every figure for one report, keyed by chart id.

    A refused report produces no figure at all, mirroring `render_markdown`'s refusal
    branch: a gate that fires means every number would be wrong by an unbounded amount,
    and a chart drawn from those numbers is worse than no chart. Drawn in the report's
    language (`report.localize`), like the page they are placed on.
    """
    if report.get("refused"):
        return {}
    with using(report.get("language") or "en"):
        return _figures(report.get("metrics") or {}, report.get("provenance") or {})


def _figures(computed: dict[str, Any], prov: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        "C-GANTT": person_timeline_chart(computed.get("s2") or {}, computed.get("s5") or {},
                                         computed.get("s9") or {}, prov),
        "C-LAG": time_to_lead_chart(computed.get("s4") or {}, computed.get("s3b") or {},
                                    computed.get("s2") or {}),
        # C-SPAN, C-POS and C-NET are in `figures.py` only because this file is held
        # under 800 lines (acceptance item 35); same artifact contract, from `svg.artifact`.
        "C-SPAN": activity_span_chart(computed.get("s5") or {}),
        "C-YEAR": records_per_year_chart(computed.get("s9") or {}, prov),
        "C-TEAM": team_size_chart(computed.get("s10") or {}, prov),
        "C-POS": byline_year_chart(computed.get("s7") or {}, computed.get("s9") or {}),
        "C-NET": coauthor_network_chart(computed.get("s19") or {}, prov),
    }
