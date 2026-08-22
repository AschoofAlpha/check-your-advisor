"""
The static figure set for the advisor profile report (docs/profile-visual-spec.md).

Five figures, one per section that earns one: C-GANTT (2), C-LAG (4), C-SPAN (5),
C-YEAR (9), C-TEAM (10). Everything else stays text or a table; the refusals and their
reasons are recorded in Section 6 of the visual spec.

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
STACK_BUDGET = 260.0
COLUMN_BUDGET = 300.0

LARGE_TEAM_MIN_AUTHORS = 20  # reported as an annotation, never used as a cut-off
HYPERAUTHORSHIP_MIN_AUTHORS = 50

CHART_IDS = ("C-GANTT", "C-LAG", "C-SPAN", "C-YEAR", "C-TEAM")

# Acceptance item 23, held here so one figure cannot be paired with two caveat sets in
# two places. The HTML builder reads the text itself from `report["caveats"]`, verbatim.
FIGURE_CAVEATS: dict[str, tuple[str, ...]] = {
    "C-GANTT": ("CAV-02", "CAV-03", "CAV-09"),
    "C-LAG": ("CAV-06", "CAV-07", "CAV-08"),
    "C-SPAN": ("CAV-09", "CAV-10", "CAV-11"),
    "C-YEAR": ("CAV-17", "CAV-18"),
    "C-TEAM": ("CAV-19",),
}

# Byte-identical to `report._time_to_lead_body` (acceptance item 18): the figure and the
# prose beside it must say one sentence, not two paraphrases of it.
NO_LEAD_SENTENCE = "no person in this corpus holds a first-author slot"
NO_COHORT_SENTENCE = "no person in this corpus appears more than once and holds no senior slot"


def _figure(chart_id: str, *, svg: str, caption: str, desc: str,
            rows: list[dict[str, Any]], drawn: bool) -> dict[str, Any]:
    return {"id": chart_id, "svg": svg, "caption": caption, "desc": desc,
            "rows": rows, "drawn": drawn}


def _records(count: int) -> str:
    """"1 records" is the kind of sloppiness that makes a reader distrust the numbers
    beside it, and this document is asking to be trusted about small samples."""
    return f"{count} record" + ("" if count == 1 else "s")


def _prose(chart_id: str, sentence: str, rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """A degenerate figure is replaced by a stated sentence, never by an empty axis.

    An axis with tick labels and no marks is the worst output available here: it reads
    as a measured zero. The caller renders `caption` as prose instead.
    """
    return _figure(chart_id, svg="", caption=sentence, desc=sentence, rows=rows or [], drawn=False)


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
    head = f"{len(rows)} of {denominator} people plotted"

    if not rows:
        return _prose("C-GANTT", (
            f"{NO_COHORT_SENTENCE}: {head}. {single_count} people appear once "
            f"({STRATUM_LABEL['C']}) and are named beside this figure; {senior_count} hold a senior "
            f"slot ({STRATUM_LABEL['D']}) and sit in a separate panel. No timeline is drawn, and no "
            f"empty axis is drawn in its place."))

    # The added per-appearance fields are what turn a span into a set of marks. Without
    # them the honest figure is the connectors alone, said out loud in the title; a
    # connector drawn as a mark would assert a record in a year with none.
    detailed = all("years" in row for row in rows)
    span = ([int(row["first_year"]) for row in all_rows] + [int(row["last_year"]) for row in all_rows]
            + [int(item["year"]) for item in (s9.get("years") or [])]
            + [int(prov[key]) for key in ("window_start_year", "window_end_year") if prov.get(key)])
    sub_lines = wrap(
        f"{head}: everyone with two or more records who never holds the senior slot. "
        f"{single_count} people appear once ({STRATUM_LABEL['C']}), named beside this figure and "
        f"counted in the strip below the axis, never plotted. {senior_count} hold a senior slot "
        f"({STRATUM_LABEL['D']}) and sit in a separate panel. Strict keying finds "
        f"{s2.get('n_strict', '?')} people, loose keying finds {s2.get('n_loose', '?')}: that gap is "
        f"the error bar on every count here. {len(hyper)} records with "
        f"{HYPERAUTHORSHIP_MIN_AUTHORS} or more authors left person-level analysis; anyone visible "
        f"only through them is not on this chart (PMIDs in provenance).", 175, 4)

    header = 20.0 + 11.0 * len(sub_lines) + 26.0
    height = ROW_PITCH * len(rows) + header + 46.0
    plot_x0, plot_x1 = GANTT_LABEL_WIDTH + 18, CANVAS_WIDTH - 26
    bands = Bands(min(span), max(span), plot_x0, plot_x1)
    plot_bottom = header + ROW_PITCH * len(rows)

    title = "Person activity timeline"
    if not detailed:
        title += " — spans only, per-appearance detail unavailable"
    body = preamble(title, sub_lines)

    # Driven off the metric's own flag, never off a hardcoded year.
    lag_years = sorted(int(item["year"]) for item in (s9.get("years") or []) if item.get("indexing_lag"))
    if lag_years:
        left = bands.edge(lag_years[0])
        body += [rect(left, header, bands.edge(lag_years[-1] + 1) - left, plot_bottom - header,
                       fill=BAND),
                 text(left + 3, header - 16, "PubMed indexing lag", 9.0, fill=MUTED)]
    body += [bands.ticks(header - 5), line(plot_x0, header, plot_x1, header, stroke=HAIRLINE)]

    table: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        y = header + ROW_PITCH * index + ROW_PITCH / 2
        first, last = int(row["first_year"]), int(row["last_year"])
        marker, label = row.get("marker") or "", STRATUM_LABEL[row["stratum"]]
        # The count is printed because a mark covers a whole year, so counting marks
        # gives the wrong number. It never enters the sort (R5).
        name_text = f"{row['name']}{marker} — n={row['n_appearances']} records — {label}"
        censoring = ", ".join(side for side, flag in (("left", row.get("left_censored")),
                                                      ("right", row.get("right_censored"))) if flag) or "none"
        group = [tooltip(f"{name_text}; {first} to {last}; censoring {censoring}"),
                 text(14, y + 4, name_text, 11.0 if len(name_text) <= 58 else 9.5)]
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
            note = ("one or more records in the first-author slot" if lead
                    else "records that year, none in the first-author slot")
            group.append(tag("g", {"class": "mark lead" if lead else "mark plain"},
                              tooltip(f"{row['name']}{marker} — {year} — {note}")
                              + rect(bands.center(year) - 4.5, y - 4.5, 9.0, 9.0,
                                      fill=INK if lead else WHITE, stroke=INK,
                                      **{"stroke-width": 1.4})))
        body.append(tag("g", {"class": "row"}, "".join(group)))
        table.append({
            "name": f"{row['name']}{marker}", "position label": label,
            "records": row["n_appearances"], "first record": first, "last record": last,
            "censoring": censoring,
            "years with a record": ", ".join(str(v) for v in (row.get("years") or [])) or "not available",
            "years with a first-author record":
                ", ".join(str(v) for v in sorted(lead_years)) or "none recorded",
        })

    # Counts only, aligned to the same year columns: the structural answer to the
    # sharpest objection to the row filter — that dropping the one-offs makes the lab
    # look steadier than the record shows.
    strip_y = plot_bottom + 15
    body.append(text(14, strip_y, f"{STRATUM_LABEL['C']}, by first year — {single_count} total",
                     9.0, fill=MUTED))
    singles = Counter(int(row["first_year"]) for row in all_rows if row.get("stratum") == "C")
    body += [mid(bands.center(year), strip_y, str(count), 9.5, fill=MUTED,
                  **{"class": "single-appearance-count"}) for year, count in sorted(singles.items())]

    legend_y = plot_bottom + 33
    body += [
        legend(14, legend_y, [("filled", "year with one or more first-author records"),
                               ("hollow", "year with records, none in the first-author slot"),
                               ("band", "PubMed indexing lag")]),
        legend(14, legend_y + 11,
                [("hairline", "line = interval between first and last record, not presence in the lab"),
                 ("dashed-arrow",
                  "dashed = censored at the window edge, the record continues past it")]),
        text(CANVAS_WIDTH - 26, legend_y + 11, "a mark means one or more records in that year",
              9.0, fill=MUTED, **{"text-anchor": "end"}),
    ]

    caption = (
        f"{head}. Rows are the A+B cohort, ordered by first record then by name, never by any count. "
        f"{single_count} of {denominator} people appear once and are named beside this figure; "
        f"{senior_count} of {denominator} hold a senior slot and sit in a separate panel. "
        f"{len(hyper)} records with {HYPERAUTHORSHIP_MIN_AUTHORS} or more authors left person-level "
        f"analysis.")
    desc = caption + (
        f" Each row is one person across {bands.low} to {bands.high}; a filled square is a year with a "
        f"first-author record, a hollow square a year with records but none in that slot, and a dashed "
        f"tail marks censoring at the window edge." if detailed else
        " Per-appearance detail is unavailable, so only the interval between each person's first and "
        "last record is drawn; no mark asserts a record in any particular year.")
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
            f"{NO_LEAD_SENTENCE}, and no person is observed without one: the cohort holds 0 of "
            f"{cohort} people. Nothing is plotted, and no empty axis is drawn in its place."))

    top_values = [int(item["lag_years"]) for item in lead]
    bottom = [(item, "waiting") for item in waiting] + [(item, "recent") for item in recent]
    bottom_values = [int(item["years_observed"]) for item, _ in bottom]
    high = max(top_values + bottom_values + [lag_years])
    sub_lines = wrap(
        f"{len(lead)} of {cohort} people in the cohort hold a first-author slot and are plotted above "
        f"the axis; {len(waiting)} of {cohort} have been observed at least {lag_years} years without "
        f"one and are plotted below it; {len(recent)} of {cohort} were first seen inside the trailing "
        f"{lag_years} years and sit in the shaded zone, too recent to tell. {single_count} people "
        f"appear once and are in neither strip. The axis counts years between two records, not time "
        f"in the lab.", 175, 3)

    top_pitch, top_r, top_stack = stack(top_values, STACK_BUDGET)
    low_pitch, low_r, low_stack = stack(bottom_values, STACK_BUDGET)
    header = 20.0 + 11.0 * len(sub_lines) + 8.0
    top_height = max(34.0, 12.0 + top_pitch * top_stack)
    low_height = max(30.0, 12.0 + low_pitch * low_stack)
    axis_y = header + 22.0 + top_height
    height = axis_y + low_height + 54.0
    plot_x0, plot_x1 = 226.0, CANVAS_WIDTH - 26
    bands = Bands(0, high, plot_x0, plot_x1)

    title = "Time to a first-author slot"
    body = preamble(title, sub_lines)
    # The zone covers the lower strip only. A stratum-A person with a one-year lag is an
    # observed lead, not a case too recent to judge, so shading them would grey out the
    # very fact the upper strip reports.
    body += [rect(bands.edge(0), axis_y, bands.edge(lag_years) - bands.edge(0), low_height,
                   fill=BAND, **{"class": "too-recent-zone"}),
             text(bands.edge(0) + 4, axis_y + 12, "too recent to tell", 9.0, fill=MUTED),
             line(plot_x0, axis_y, plot_x1, axis_y, **{"stroke-width": 1}),
             bands.ticks(axis_y + low_height + 12),
             text(plot_x0, axis_y + low_height + 26,
                   "integer years between first record and first record in the first-author slot",
                   9.5, fill=MUTED)]

    # A bracket across both strips, so a screenshot that crops the lower one is visibly cut.
    bracket_x = plot_x0 - 12
    body += [line(bracket_x, axis_y - top_height, bracket_x, axis_y + low_height, stroke=MUTED),
             line(bracket_x, axis_y - top_height, bracket_x + 6, axis_y - top_height, stroke=MUTED),
             line(bracket_x, axis_y + low_height, bracket_x + 6, axis_y + low_height, stroke=MUTED),
             lane_label(14, axis_y - 8, f"held a first-author slot — {len(lead)} of {cohort}"),
             lane_label(14, axis_y + 16,
                        f"no first-author slot yet — {len(bottom)} of {cohort}")]

    table: list[dict[str, Any]] = []
    for (value, index), item in zip(positions(top_values), lead, strict=True):
        body.append(tag("g", {"class": "dot lead"},
                         tooltip(f"{item['name']}{item.get('marker', '')} — {value} year(s) to a "
                                  f"first-author record")
                         + circle(bands.center(value), axis_y - 8 - index * top_pitch, top_r, fill=INK)))
        table.append({"name": f"{item['name']}{item.get('marker', '')}",
                      "strip": "held a first-author slot", "years": value})
    for (value, index), (item, kind) in zip(positions(bottom_values), bottom, strict=True):
        cx, cy = bands.center(value), axis_y + 8 + index * low_pitch
        mark = circle(cx, cy, low_r, fill=WHITE if kind == "waiting" else BAND, stroke=INK,
                        **{"stroke-width": 1.2})
        note = "first seen too recently to tell"
        if kind == "waiting":
            mark += arrow(cx + low_r, cy, 8.0)
            note = "observed at least this long with no first-author record"
        body.append(tag("g", {"class": f"dot {'no-lead' if kind == 'waiting' else 'too-recent'}"},
                         tooltip(f"{item['name']}{item.get('marker', '')} — {value} year(s) observed "
                                  f"— {note}") + mark))
        table.append({"name": f"{item['name']}{item.get('marker', '')}",
                      "strip": ("observed without a first-author slot" if kind == "waiting"
                                else "too recent to tell"), "years": value})

    if not_computable:
        # "Nobody led" and "k people have gone N years without leading" are different
        # facts; the second survives, so the lower strip keeps rendering.
        body.append(text(plot_x0, axis_y - top_height + 16, NO_LEAD_SENTENCE, 11.0))
    else:
        body.append(text(bands.center(0) + 10, axis_y - top_height + 12,
                         f"debuted in the first-author slot: {int(s4.get('count_at_zero') or 0)} of "
                         f"{denominator}", 9.5, fill=MUTED))
    if suppressed:
        # Below the floor the raw values are the metric, so every dot stays and only the
        # aggregate is replaced.
        body.append(plate(plot_x0, header + 2,
                           f"median not computed — n={denominator}, floor {MIN_N_AGGREGATE}"))
    elif s4.get("median") is not None:
        median_x = bands.center(float(s4["median"]))
        body += [line(median_x, axis_y - top_height, median_x, axis_y,
                       **{"stroke-width": 1.2, "class": "median-tick"}),
                 mid(median_x, header + 14,
                      f"median {fmt(s4['median'])} years over {denominator} people", 10.0)]
    body.append(legend(14, height - 8, [
        ("dot", "one person who has held a first-author slot"),
        ("tail", "one person observed at least this long with none"),
        ("band", "too recent to tell")]))

    caption = (f"{len(lead)} of {cohort} people in the cohort hold a first-author slot; {len(waiting)} "
               f"of {cohort} have been observed at least {lag_years} years without one; {len(recent)} "
               f"of {cohort} are too recent to tell. {single_count} single-appearance people are in "
               f"neither strip.")
    if suppressed:
        caption += (f" Median not computed — n={denominator}, below the floor of {MIN_N_AGGREGATE}; "
                    f"the individual values are the metric at this sample size and are all plotted.")
    elif s4.get("median") is not None:
        caption += f" Median {fmt(s4['median'])} years over {denominator} people."
    desc = caption + (" Both strips share one axis of integer years between two records; the lower "
                      "strip carries a right-pointing tail meaning at least this long with no "
                      "first-author record.")
    if suppressed or not_computable:
        desc += f" The median is not computed at n={denominator}, below the floor of {MIN_N_AGGREGATE}."
    return _figure("C-LAG", svg=document("C-LAG", CANVAS_WIDTH, height, title, desc, "".join(body)),
                   caption=caption, desc=desc, rows=table, drawn=True)


# --- C-SPAN: observed activity span (report Section 5) -----------------------------

_SPAN_LANES = (
    ("complete", "complete"),
    ("right_censored", "right-censored"),
    ("left_censored", "left-censored"),
    ("both_censored", "censored at both ends"),
)


def activity_span_chart(s5: dict[str, Any]) -> dict[str, Any]:
    """
    Four lanes on one span axis, one per censoring bucket.

    Lanes make censoring the primary structure instead of a footnote: a right-censored
    span is not a short stay, and collapsing those people into a count is what lets
    "median 2 years" be read as "people leave after two years".
    """
    cohort = int(s5.get("cohort_denominator") or 0)
    values = list(s5.get("values") or [])
    buckets = s5.get("buckets") or {}
    complete_n = int(s5.get("denominator") or 0)
    suppressed = bool(s5.get("suppressed"))
    single_count = int(s5.get("single_appearance_count") or 0)

    if cohort == 0 or not values:
        return _prose("C-SPAN", (
            f"No span is observed: 0 of {cohort} people in the cohort have a first and a last record "
            f"to measure between. {single_count} people appear once and have no span by construction. "
            f"Nothing is plotted, and no zero-height axis is drawn in its place."))

    counted = ", ".join(f"{label} {int(buckets.get(key, 0))} of {cohort}" for key, label in _SPAN_LANES)
    sub_lines = wrap(
        f"One dot per person in the cohort of {cohort}. Censoring by lane: {counted}. {single_count} "
        f"people appear once and have no span by construction. The axis is the interval between two "
        f"publication dates, not time in the lab.", 175, 3)

    lanes = []
    for key, label in _SPAN_LANES:
        members = sorted((item for item in values if item["bucket"] == key),
                         key=lambda item: (item["span_years"], item.get("name") or ""))
        spans = [int(item["span_years"]) for item in members]
        pitch, radius, tallest = stack(spans, STACK_BUDGET / 2)
        head_room = 22.0 if key == "complete" else 0.0
        lanes.append((key, label, members, spans, pitch, radius,
                      max(34.0, 14.0 + pitch * tallest) + head_room))

    header = 20.0 + 11.0 * len(sub_lines) + 10.0
    height = header + sum(lane[-1] for lane in lanes) + 44.0
    plot_x0, plot_x1 = 226.0, CANVAS_WIDTH - 26
    bands = Bands(0, max(int(item["span_years"]) for item in values), plot_x0, plot_x1)

    body = preamble("Observed activity span", sub_lines)
    table: list[dict[str, Any]] = []
    lane_top = header
    for key, label, members, spans, pitch, radius, lane_height in lanes:
        baseline = lane_top + lane_height - 12
        body += [line(plot_x0, baseline + 4, plot_x1, baseline + 4, stroke=HAIRLINE),
                 lane_label(14, baseline, f"{label} — {len(members)} of {cohort}")]
        for (value, index), item in zip(positions(spans), members, strict=True):
            cx, cy = bands.center(value), baseline - index * pitch
            same_year = bool(item.get("same_year"))
            solid = key == "complete" and not same_year
            mark = circle(cx, cy, radius, fill=INK if solid else WHITE,
                            stroke="" if solid else INK, **({} if solid else {"stroke-width": 1.2}))
            if same_year:
                # Two records in one calendar year is not the same fact as one record and
                # must not borrow that glyph.
                mark += line(cx, cy - radius - 3, cx, cy + radius + 3, **{"stroke-width": 1})
            if key in ("right_censored", "both_censored"):
                mark += arrow(cx + radius, cy, 9.0)
            if key in ("left_censored", "both_censored"):
                mark += (line(cx - radius, cy, cx - radius - 9, cy,
                                **{"stroke-width": 1, "stroke-dasharray": "3 2"})
                          + line(cx - radius - 9, cy, cx - radius - 5, cy - 3, **{"stroke-width": 1}))
            note = (f"{item['name']}{item.get('marker', '')} — span {value} year(s), "
                    f"{item['first_year']} to {item['last_year']}, {label}")
            if same_year:
                note += ", two or more records in one calendar year"
            body.append(tag("g", {"class": f"dot span {key}"}, tooltip(note) + mark))
            table.append({"name": f"{item['name']}{item.get('marker', '')}", "span years": value,
                          "first record": item["first_year"], "last record": item["last_year"],
                          "censoring": label,
                          "two or more records in one year": "yes" if same_year else "no"})
        if key == "complete":
            low, high = s5.get("iqr") or (None, None)
            # The bracket is physically confined to this lane: the restriction is the
            # encoding that stops a complete-spans-only median being read as the cohort's.
            body.append(aggregate(
                bands, lane_top + 10, None if suppressed else s5.get("median"), (low, high),
                f"median {fmt(s5.get('median'))} years, IQR {fmt(low)} to {fmt(high)}, over "
                f"{complete_n} of {cohort} people — complete spans only",
                (plot_x0, lane_top + 2,
                 f"median not computed — {complete_n} complete spans, floor {MIN_N_AGGREGATE}")))
        lane_top += lane_height

    body += [bands.ticks(lane_top + 12),
             text(plot_x0, lane_top + 26, "integer years between a person's first and last record",
                   9.5, fill=MUTED),
             legend(14, height - 6, [
                 ("dot", "complete span"), ("tail", "censored: at least this long"),
                 ("dot-open", "two or more records in one calendar year, at zero")])]

    caption = (f"Censoring by lane: {counted}. {single_count} single-appearance people have no span "
               f"by construction.")
    if suppressed:
        caption += (f" Median not computed — {complete_n} complete spans, below the floor of "
                    f"{MIN_N_AGGREGATE}.")
    elif s5.get("median") is not None:
        low, high = s5.get("iqr") or (None, None)
        caption += (f" Median {fmt(s5['median'])} years, IQR {fmt(low)} to {fmt(high)}, over "
                    f"{complete_n} of {cohort} people — complete spans only.")
    desc = caption + (" Each lane is one censoring bucket on a shared span axis; censored dots carry "
                      "a directional tail meaning at least this long.")
    if suppressed:
        desc += " The median and IQR are not computed and no bracket is drawn."
    return _figure("C-SPAN", svg=document("C-SPAN", CANVAS_WIDTH, height, "Observed activity span",
                                           desc, "".join(body)),
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
        return _prose("C-YEAR", f"No year bin exists for the {denominator} records in this corpus, so "
                                f"no column is drawn and no empty axis is drawn in its place.")
    if len(flagged) == len(years):
        # A figure whose every mark is annotated as wrong is not a figure.
        listing = "; ".join(f"{item['year']}: {item['count']}" for item in years)
        return _prose("C-YEAR", (
            f"Every year bin in this corpus is partial or subject to PubMed indexing lag "
            f"({len(flagged)} of {len(years)}), so the counts are printed instead of drawn: "
            f"{listing}. {denominator} records in total."), table)

    low, high = int(years[0]["year"]), int(years[-1]["year"])
    peak = max([1] + [int(item["count"]) for item in years])
    query = prov.get("query") or {}
    sub_lines = wrap(
        f"{denominator} records, {low} to {high}, window {query.get('mindate', 'not recorded')} to "
        f"{query.get('maxdate', 'not recorded')}. {len(flagged)} of {len(years)} year bins are partial "
        f"or undercounted by PubMed indexing lag and are hatched or shaded below. One square is one "
        f"PubMed record: reviews, letters, comments and case reports are counted alongside primary "
        f"research.", 175, 3)

    unit = max(2.0, min(9.0, COLUMN_BUDGET / peak))
    gap = 1.0 if unit > 4 else 0.5
    header = 20.0 + 11.0 * len(sub_lines) + 12.0
    plot_height = max(60.0, peak * (unit + gap) + 10.0)
    baseline = header + plot_height
    height = baseline + 56.0
    plot_x0, plot_x1 = 26.0, CANVAS_WIDTH - 26
    bands = Bands(low, high, plot_x0, plot_x1)
    column = min(26.0, bands.width * 0.62)

    body = [hatch("hatch-year")] + preamble("Records per year", sub_lines)
    lag_bins = [int(item["year"]) for item in years if item.get("indexing_lag")]
    if lag_bins:
        left = bands.edge(min(lag_bins))
        body += [rect(left, header, bands.edge(max(lag_bins) + 1) - left, plot_height, fill=BAND),
                 text(left + 3, header + 10, "PubMed indexing lag — undercounted", 9.0, fill=MUTED)]

    for item in years:
        year, count = int(item["year"]), int(item["count"])
        cx = bands.center(year)
        left = cx - column / 2
        notes = []
        if item.get("partial"):
            notes.append("PARTIAL")
            body.append(rect(left, header, column, plot_height, fill="url(#hatch-year)",
                              stroke=HAIRLINE, **{"stroke-width": 0.6, "class": "flagged partial"}))
        if item.get("indexing_lag"):
            notes.append("INDEXING LAG")
        if count == 0:
            # An empty labelled column, not a gap: the two read differently.
            body.append(rect(left, baseline - 8, column, 8.0, fill="none", stroke=HAIRLINE,
                              **{"stroke-dasharray": "2 2", "class": "empty-column"}))
        body += [tag("g", {"class": "unit"}, tooltip(f"{year} — one of {count} records")
                      + rect(left + (column - unit) / 2, baseline - (index + 1) * (unit + gap),
                              unit, unit, fill=INK)) for index in range(count)]
        body += [mid(cx, header - 2, str(count), 9.0, fill=MUTED),
                 mid(cx, baseline + 14, str(year), 9.5, fill=MUTED)]
        if notes:
            body.append(mid(cx, baseline + 26, " ".join(notes), 8.0, fill=MUTED,
                             **{"class": "flag-label"}))

    body += [line(plot_x0, baseline, plot_x1, baseline),
             text(plot_x0, baseline + 40, "PubMed records per calendar year", 9.5, fill=MUTED),
             legend(plot_x0 + 220, baseline + 40, [
                 ("hatch", "partial bin — the window opens or closes mid-year"),
                 ("band", "PubMed indexing lag — undercounted")])]

    caption = (f"{denominator} records, {low} to {high}. {len(flagged)} of {len(years)} year bins are "
               f"partial or undercounted by PubMed indexing lag and are hatched or shaded. One square "
               f"is one PubMed record, not one research paper: publication type is not parsed.")
    desc = caption + (" Each column is one calendar year including years with zero records; no line "
                      "and no curve is drawn across them.")
    return _figure("C-YEAR", svg=document("C-YEAR", CANVAS_WIDTH, height, "Records per year", desc,
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
        return _prose("C-TEAM", f"0 of {denominator} records carry an author count, so no dot is drawn "
                                f"and no empty axis is drawn in its place.")

    low, high = min(values + subset_values), max(values + subset_values)
    exclusion = (f"records with {HYPERAUTHORSHIP_MIN_AUTHORS} or more authors are excluded from this "
                 f"chart ({len(hyper)} of them; PMIDs in provenance)")
    sub_lines = wrap(
        f"One dot per record over {denominator} records, {low} to {high} authors. Records with "
        f"{LARGE_TEAM_MIN_AUTHORS} or more authors: {large} of {denominator}, drawn in the same lane "
        f"inside a ring. The lower lane is the {subset_n} of {denominator} records led by a "
        f"lead-trainee or support candidate. Author count on this researcher's papers is not lab "
        f"headcount.", 175, 3)

    main_pitch, main_r, main_stack = stack(values, STACK_BUDGET)
    sub_pitch, sub_r, sub_stack = stack(subset_values, STACK_BUDGET / 2)
    header = 20.0 + 11.0 * len(sub_lines) + 26.0
    main_base = header + max(40.0, 14.0 + main_pitch * main_stack)
    sub_base = main_base + 30.0 + max(30.0, 14.0 + sub_pitch * sub_stack)
    tick_drop = 44.0 if (subset.get("suppressed") or subset.get("median") is None) else 30.0
    height = sub_base + tick_drop + 28.0
    plot_x0, plot_x1 = 226.0, CANVAS_WIDTH - 26
    bands = Bands(low, high, plot_x0, plot_x1)

    body = preamble("Team size", sub_lines)
    body.append(aggregate(
        bands, header - 14, None if s10.get("suppressed") else s10.get("median"), s10.get("iqr"),
        f"median {fmt(s10.get('median'))} authors over {denominator} records",
        (plot_x0, header - 24, f"median not computed — n={denominator}, floor {MIN_N_AGGREGATE}")))
    body += [line(plot_x0, main_base, plot_x1, main_base),
             lane_label(14, main_base - 4, f"all records — {denominator} of {denominator}")]
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
                        tooltip(f"one record with {value} authors"
                                + (f", {LARGE_TEAM_MIN_AUTHORS} or more" if heavy else "")) + dot))
    body += [text(plot_x1, main_base + 14,
                   f"records with {LARGE_TEAM_MIN_AUTHORS} or more authors: {large} of {denominator}",
                   9.5, fill=MUTED, **{"text-anchor": "end"}),
             # Provenance-derived, not a caveat: without it the figure cannot show the
             # consortium papers that are exactly the "one of twenty" fear it answers.
             text(plot_x1, main_base + 26, exclusion, 9.5, fill=MUTED,
                   **{"text-anchor": "end", "class": "exclusion-note"})]

    lane = [line(plot_x0, sub_base, plot_x1, sub_base, stroke=HAIRLINE),
            lane_label(14, sub_base - 4, f"led by a lead-trainee or support candidate — "
                                         f"{subset_n} of {denominator}")]
    lane += [tag("g", {"class": "dot subset-record"},
                  tooltip(f"one record with {value} authors, led by a lead-trainee or support "
                           f"candidate")
                  + circle(bands.center(value), sub_base - 8 - index * sub_pitch, sub_r, fill=WHITE,
                            stroke=INK, **{"stroke-width": 1.2}))
             for value, index in positions(subset_values)]
    # A median tick and never an IQR band: `team_size.subset` carries no `iqr` key.
    lane.append(aggregate(
        bands, sub_base + 4, None if subset.get("suppressed") else subset.get("median"), None,
        f"median {fmt(subset.get('median'))} authors over {subset_n} records",
        (plot_x0, sub_base + 4, f"median not computed — {_records(subset_n)} led by a lead-trainee "
                                f"or support candidate, floor {MIN_N_SUBSET_MEDIAN}"), "subset"))
    body.append(tag("g", {"class": "lane subset"}, "".join(lane)))

    body += [bands.ticks(sub_base + tick_drop),
             text(plot_x0, sub_base + tick_drop + 14, "authors per record", 9.5, fill=MUTED),
             legend(14, height - 6, [
                 ("dot", "one record"),
                 ("dot-open", "one record led by a lead-trainee or support candidate")])]

    caption = (f"One dot per record over {denominator} records. Records with {LARGE_TEAM_MIN_AUTHORS} "
               f"or more authors: {large} of {denominator}. Lower lane: {subset_n} of {denominator} "
               f"records led by a lead-trainee or support candidate. {exclusion}.")
    if s10.get("suppressed") or s10.get("median") is None:
        caption += f" Median not computed — n={denominator}, below the floor of {MIN_N_AGGREGATE}."
    else:
        caption += f" Median {fmt(s10['median'])} authors over {denominator} records."
    if subset.get("suppressed") or subset.get("median") is None:
        caption += (f" Subset median not computed — {_records(subset_n)}, below the floor of "
                    f"{MIN_N_SUBSET_MEDIAN}.")
    else:
        caption += f" Subset median {fmt(subset['median'])} authors over {subset_n} records."
    desc = caption + (" Both lanes share one integer axis of authors per record; the subset lane "
                      "carries a median tick and never an interquartile bracket.")
    table = [{"authors": value, "records": values.count(value),
              "records led by a lead-trainee or support candidate": subset_values.count(value)}
             for value in sorted(set(values))]
    return _figure("C-TEAM", svg=document("C-TEAM", CANVAS_WIDTH, height, "Team size", desc,
                                           "".join(body)),
                   caption=caption, desc=desc, rows=table, drawn=True)


# --- Assembly ----------------------------------------------------------------------


def figures_for_report(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """
    Every figure for one report, keyed by chart id.

    A refused report produces no figure at all, mirroring `render_markdown`'s refusal
    branch: a gate that fires means every number would be wrong by an unbounded amount,
    and a chart drawn from those numbers is worse than no chart.
    """
    if report.get("refused"):
        return {}
    computed = report.get("metrics") or {}
    prov = report.get("provenance") or {}
    return {
        "C-GANTT": person_timeline_chart(computed.get("s2") or {}, computed.get("s5") or {},
                                         computed.get("s9") or {}, prov),
        "C-LAG": time_to_lead_chart(computed.get("s4") or {}, computed.get("s3b") or {},
                                    computed.get("s2") or {}),
        "C-SPAN": activity_span_chart(computed.get("s5") or {}),
        "C-YEAR": records_per_year_chart(computed.get("s9") or {}, prov),
        "C-TEAM": team_size_chart(computed.get("s10") or {}, prov),
    }
