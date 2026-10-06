"""
Three more static figures for the advisor profile report: C-SPAN, C-POS and C-NET.

Split from `charts.py` because that file is held under 800 lines (acceptance item
35), not because these are a different kind of artifact. Same contract, same
primitives, same three rules `charts.py` states at length and this module obeys
without restating them:

  1. A degenerate figure ships a sentence, never an empty axis.
  2. Every figure carries its own denominator inside the SVG.
  3. Shape and text carry every distinction; colour carries none.

No figure here computes anything. C-SPAN reads `metrics.activity_span` (Section
5); C-POS reads `metrics.pi_byline_positions`
(Section 7) and `metrics.records_per_year` (Section 9); C-NET reads
`cohesion.coauthor_clusters` (Section 19), including the `edges` list that module
emits, and re-derives no part of the partition. That is the whole reason the edge
list lives in `cohesion.py`: a drawing that walked the corpus a second time could
disagree with the section printed beside it about who is in which cluster, and the
drawing is the half nobody would check.

Nothing here ranks people. C-NET orders its panels the way `coauthor_clusters`
ordered them — largest first, which Section 19 already states on the page is for
readability and not a claim — and orders the people inside a panel by name.
"""

from __future__ import annotations

import math
from typing import Any

from ..i18n import en
from .metrics import MIN_N_AGGREGATE
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
    circle,
    document,
    fmt,
    hatch,
    lane_label,
    legend,
    line,
    mid,
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
STACK_BUDGET = 260.0

# --- C-POS ---------------------------------------------------------------

# Report order, and the labels the figure prints. `metrics.pi_byline_positions`
# emits exactly these five keys; a sixth appearing there without appearing here
# would be silently dropped, so the assembly asserts the two agree.
BYLINE_LANES: tuple[tuple[str, str], ...] = (
    ("first", "first author"),
    ("last", "last author"),
    ("sole", "sole author"),
    ("middle", "a middle slot"),
    ("unlocated", "not located on the byline"),
)

# Why this figure often refuses. A corpus harvested with a byline-position filter
# has had the answer imposed on it, so the counts would restate the filter.
NOT_MEASURED_SENTENCE = (
    "byline position is not measured on this corpus: it was harvested with a position filter, so "
    "every record matches that filter by construction and counting them would restate the search "
    "rather than describe the person"
)

# --- C-NET ---------------------------------------------------------------

# Panels drawn before the rest are stated as a count. A polluted corpus can
# partition into thirty groups, and thirty panels is a page nobody reads.
MAX_NETWORK_PANELS = 12
# Above this many nodes the ring is drawn but the names move to the data table.
# Labels round a circle collide long before the marks do, and an unreadable name
# on the page is worse than a readable one in the table below it.
MAX_NETWORK_LABELS = 12
NETWORK_COLUMNS = 3


def _records(count: int) -> str:
    """"1 records" is the kind of sloppiness that makes a reader distrust the numbers
    beside it, and this document is asking to be trusted about small samples."""
    return en("{count} record", count=count) if count == 1 else en("{count} records", count=count)


# --- C-POS: byline position by year (report Section 7) ---------------------


def byline_year_chart(s7: dict[str, Any], s9: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    One lane per byline position, one unit square per record, on the year axis.

    Read down a column for that year's output and across the lanes for where the
    PI stood on it. Lanes rather than one stacked column because five positions
    cannot be told apart by five greyscale fills, and a stack also hides the one
    thing worth seeing: which years hold a first-author record at all.

    The year axis is `records_per_year`'s, not the byline rows' own range, so a
    year with records but no located byline still gets a labelled column and the
    partial and indexing-lag bins line up with C-YEAR above it.
    """
    s9 = s9 or {}
    denominator = int(s7.get("denominator") or 0)

    if not s7.get("measured"):
        return prose_artifact("C-POS", en("{sentence}. Nothing is plotted, and no empty axis is drawn "
                                          "in its place.", sentence=en(NOT_MEASURED_SENTENCE)))

    rows = [row for row in (s7.get("rows") or []) if isinstance(row.get("year"), int)]
    if not rows:
        return prose_artifact("C-POS", (
            en("0 of {denominator} records carry both a year and a byline position, so no "
               "column is drawn and no empty axis is drawn in its place.",
               denominator=denominator)))

    year_bins = list(s9.get("years") or [])
    span = [int(item["year"]) for item in year_bins] + [int(row["year"]) for row in rows]
    low, high = min(span), max(span)
    flagged = {int(item["year"]): item for item in year_bins
               if item.get("partial") or item.get("indexing_lag")}
    lag_years = sorted(int(item["year"]) for item in year_bins if item.get("indexing_lag"))

    counts = s7.get("counts") or {}
    # Counted from the rows rather than read from `counts`, because a row without
    # a usable year cannot be drawn and must not be claimed in a lane label.
    per_lane: dict[str, dict[int, int]] = {key: {} for key, _ in BYLINE_LANES}
    unknown_positions: dict[str, int] = {}
    for row in rows:
        position = str(row.get("position") or "")
        year = int(row["year"])
        if position not in per_lane:
            unknown_positions[position] = unknown_positions.get(position, 0) + 1
            continue
        per_lane[position][year] = per_lane[position].get(year, 0) + 1

    drawn_records = sum(sum(bins.values()) for bins in per_lane.values())
    per_year_total: dict[int, int] = {}
    for bins in per_lane.values():
        for year, count in bins.items():
            per_year_total[year] = per_year_total.get(year, 0) + count
    # The tallest single *column*, not the tallest year. A lane's height is set
    # by the most records it holds in one year, and sizing off the year's total
    # across all five lanes makes every lane five times taller than its own
    # content — which, multiplied by five lanes, is the runaway image height this
    # figure set exists to prevent. One shared height for all lanes, so a lane
    # with two records cannot look as tall as a lane with twenty.
    peak = max([1] + [count for bins in per_lane.values() for count in bins.values()])

    lane_counts = ", ".join(
        en("{label} {count} of {denominator}", label=en(label), count=sum(per_lane[key].values()),
           denominator=denominator) for key, label in BYLINE_LANES
    )
    sub_lines = wrap(
        en("{drawn_records} of {denominator} records carry a year and are drawn, one "
           "square each. By lane: {lane_counts}. {n_flagged} of {n_year_bins} year "
           "bins are partial or undercounted by PubMed indexing lag and are marked "
           "below. Byline position is a convention that differs by field and by "
           "journal, so it is reported and not interpreted: no slot here is called "
           "senior, junior, better or worse.",
           drawn_records=drawn_records, denominator=denominator, lane_counts=lane_counts, n_flagged=len(flagged), n_year_bins=len(year_bins) or len(per_year_total)), 175, 4)

    unit = max(2.0, min(9.0, 84.0 / peak))
    gap = 1.0 if unit > 4 else 0.5
    lane_height = max(30.0, peak * (unit + gap) + 16.0)
    header = 20.0 + 11.0 * len(sub_lines) + 18.0
    # Room for two stacked flag lines under the busiest bin, plus the legend.
    height = header + lane_height * len(BYLINE_LANES) + 70.0
    plot_x0, plot_x1 = 262.0, CANVAS_WIDTH - 26
    bands = Bands(low, high, plot_x0, plot_x1)
    column = min(24.0, bands.width * 0.62)

    body = [hatch("hatch-pos")] + preamble(en("Records per year by byline position"), sub_lines)
    if lag_years:
        left = bands.edge(lag_years[0])
        body += [rect(left, header, bands.edge(lag_years[-1] + 1) - left,
                      lane_height * len(BYLINE_LANES), fill=BAND),
                 text(left + 3, header - 5, en("PubMed indexing lag — undercounted"), 9.0, fill=MUTED)]

    table: list[dict[str, Any]] = []
    lane_top = header
    for key, label in BYLINE_LANES:
        bins, label = per_lane[key], en(label)
        lane_total = sum(bins.values())
        baseline = lane_top + lane_height - 8
        body += [line(plot_x0, baseline, plot_x1, baseline, stroke=HAIRLINE),
                 lane_label(14, baseline - 4, en("{label} — {lane_total} of {denominator}", label=label, lane_total=lane_total, denominator=denominator))]
        for year in range(low, high + 1):
            count = bins.get(year, 0)
            cx = bands.center(year)
            left = cx - column / 2
            if count == 0:
                # An empty labelled slot, not a gap: the two read differently, and
                # "no first-author record that year" is the fact this lane exists
                # to make visible.
                body.append(rect(left, baseline - 6, column, 6.0, fill="none", stroke=HAIRLINE,
                                 **{"stroke-dasharray": "2 2", "class": "empty-column"}))
                continue
            body += [tag("g", {"class": "unit"},
                         tooltip(en("{year} — one of {count} record(s) with the PI as {label}", year=year, count=count, label=label))
                         + rect(left + (column - unit) / 2, baseline - (index + 1) * (unit + gap),
                                unit, unit, fill=INK))
                     for index in range(count)]
            body.append(mid(cx, baseline - count * (unit + gap) - 3, str(count), 9.0, fill=MUTED))
        lane_top += lane_height

    axis_y = header + lane_height * len(BYLINE_LANES)
    for year in range(low, high + 1):
        item = flagged.get(year)
        notes = []
        if item and item.get("partial"):
            notes.append(en("PARTIAL"))
            body.append(rect(bands.center(year) - column / 2, header, column,
                             lane_height * len(BYLINE_LANES), fill="url(#hatch-pos)",
                             stroke=HAIRLINE, **{"stroke-width": 0.6, "class": "flagged partial"}))
        if item and item.get("indexing_lag"):
            notes.append(en("INDEXING LAG"))
        body.append(mid(bands.center(year), axis_y + 14, str(year), 9.5, fill=MUTED))
        body.append(mid(bands.center(year), axis_y + 25,
                        str(per_year_total.get(year, 0)), 9.0, fill=MUTED,
                        **{"class": "year-total"}))
        # One flag per line rather than joined with a space. A bin carrying both
        # is wider than its column, and two adjacent bins carrying both ran their
        # labels into each other — which reads as one long unrelated word rather
        # than as two flags on two years.
        body += [mid(bands.center(year), axis_y + 36 + offset * 9, note, 8.0, fill=MUTED,
                     **{"class": "flag-label"})
                 for offset, note in enumerate(notes)]

    body += [line(plot_x0, axis_y, plot_x1, axis_y),
             text(14, axis_y + 14, en("calendar year"), 9.5, fill=MUTED),
             text(14, axis_y + 25, en("records that year"), 9.0, fill=MUTED),
             legend(plot_x0, height - 8, [
                 ("filled", en("one record")),
                 ("hatch", en("partial bin — the window opens or closes mid-year")),
                 ("band", en("PubMed indexing lag — undercounted"))])]

    for year in range(low, high + 1):
        row: dict[str, Any] = {"year": year, "records": per_year_total.get(year, 0)}
        for key, label in BYLINE_LANES:
            row[label] = per_lane[key].get(year, 0)
        item = flagged.get(year)
        row["partial"] = bool(item and item.get("partial"))
        row["indexing lag"] = bool(item and item.get("indexing_lag"))
        table.append(row)

    caption = (en("{drawn_records} of {denominator} records carry a year and are drawn. By "
                  "lane: {lane_counts}. {n_flagged} year bins are partial or undercounted by "
                  "PubMed indexing lag. Byline position is reported, never interpreted: no "
                  "slot here is called senior, junior, better or worse, and no rate is "
                  "computed over these counts.",
                  drawn_records=drawn_records, denominator=denominator, lane_counts=lane_counts, n_flagged=len(flagged)))
    if unknown_positions:
        # A position the metric emitted and this figure has no lane for. Stated
        # rather than dropped: a silently missing record is the failure mode this
        # whole figure set exists to remove.
        caption += en(" Positions with no lane in this figure, counted and not drawn: {items}.",
                      items=", ".join(f"{name} {count}" for name, count
                                      in sorted(unknown_positions.items())))
    if counts and sum(counts.values()) != denominator:
        caption += (en(" The metric counted {values} positions over {denominator} records; the "
                       "difference is records this figure could not place on a year.",
                       values=sum(counts.values()), denominator=denominator))
    desc = caption + (en(" Each lane is one byline position on a shared axis of calendar years; one "
                         "square is one record, a dashed slot is a year with none in that lane, and "
                         "no line and no curve is drawn across the years."))
    return artifact("C-POS",
                    svg=document("C-POS", CANVAS_WIDTH, height,
                                 en("Records per year by byline position"), desc, "".join(body)),
                    caption=caption, desc=desc, rows=table, drawn=True)


# --- C-NET: co-author network (report Section 19) --------------------------


def _ring(count: int) -> float:
    """Ring radius for `count` nodes at a readable arc spacing.

    Grows with the node count rather than being fixed, so a crowded cluster
    spreads instead of overplotting. Bounded below so a two-person panel does not
    collapse to a point and above so one big cluster cannot set the page height.
    """
    return max(26.0, min(96.0, count * 13.0 / (2 * math.pi) + 18.0))


def coauthor_network_chart(
    s19: dict[str, Any],
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    One panel per co-author cluster: the people who recur in it, joined where they
    share a byline.

    Nothing is computed here. The partition, the recurring people and the edges
    all arrive from `cohesion.coauthor_clusters`, which is what Section 19 prints
    beside this figure — so the picture and the prose cannot disagree about who is
    in which group.

    The diagnostic is not the number of clusters. It is whether a person appears
    in two of them: one researcher's output is tied together by the people they
    work with, and a name bridging two panels is the strongest evidence on this
    page that the panels belong to one person. Bridging names are ringed and
    listed in the caption.
    """
    prov = provenance or {}
    denominator = int(s19.get("denominator") or 0)

    if s19.get("suppressed"):
        return prose_artifact("C-NET", (
            en("Not partitioned: {denominator} records, below the floor of {min_n}. Below "
               "that floor the partition says nothing — four records sharing no co-author "
               "are four clusters whether or not they are four people — so no network is "
               "drawn and no empty frame is drawn in its place.",
               denominator=denominator, min_n=s19.get('min_n', '?'))))

    clusters = list(s19.get("clusters") or [])
    drawable = [c for c in clusters if c.get("recurring_people")]
    singletons = int(s19.get("singleton_clusters") or 0)
    n_clusters = int(s19.get("n_clusters") or len(clusters))

    if not drawable:
        return prose_artifact("C-NET", (
            en("No person recurs inside any of the {n_clusters} cluster(s) over "
               "{denominator} records, so there is no co-authorship to draw: every non-PI "
               "name in this corpus appears on exactly one record. {singletons} "
               "cluster(s) hold a single record. Nothing is plotted, and no empty frame "
               "is drawn in its place.",
               n_clusters=n_clusters, denominator=denominator, singletons=singletons)))

    shown = drawable[:MAX_NETWORK_PANELS]
    hidden = drawable[len(shown):]

    # A name in two drawn panels. Counted over the panels actually on the page so
    # the ring on a node and the sentence in the caption cannot disagree.
    appearances: dict[str, list[int]] = {}
    for panel_index, cluster in enumerate(shown, start=1):
        for person in cluster["recurring_people"]:
            appearances.setdefault(person["name"], []).append(panel_index)
    bridging = sorted(name for name, panels in appearances.items() if len(panels) > 1)

    hyper = list((prov.get("exclusions") or {}).get("hyperauthorship") or [])
    sub_lines = wrap(
        en("{n_shown} of {n_clusters} cluster(s) drawn, over {denominator} records. A "
           "panel is one group of records joined by a shared co-author; a node is a "
           "person on two or more of that group's records; a line is a shared byline, "
           "and its weight is printed rather than drawn. {singletons} cluster(s) hold "
           "a single record and have nobody to join. {value} further cluster(s) with "
           "recurring people are listed in the data table instead of drawn. {n_hyper} "
           "record(s) left person-level analysis before this partition was built, so "
           "anyone visible only through them is on no panel. Panels are ordered "
           "largest first for readability and that order is not a ranking.",
           n_shown=len(shown), n_clusters=n_clusters, denominator=denominator, singletons=singletons, value=len(drawable) - len(shown), n_hyper=len(hyper)), 175, 4)

    header = 20.0 + 11.0 * len(sub_lines) + 16.0
    panel_width = (CANVAS_WIDTH - 28.0) / NETWORK_COLUMNS
    radii = [_ring(len(c["recurring_people"])) for c in shown]
    rows_of_panels = [range(i, min(i + NETWORK_COLUMNS, len(shown)))
                      for i in range(0, len(shown), NETWORK_COLUMNS)]
    row_heights = [max(radii[i] for i in indices) * 2 + 66.0 for indices in rows_of_panels]
    height = header + sum(row_heights) + 46.0

    body = preamble(en("Co-author clusters — who joins which records"), sub_lines)
    table: list[dict[str, Any]] = []
    panel_top = header
    for row_index, indices in enumerate(rows_of_panels):
        for column_index, panel_index in enumerate(indices):
            cluster = shown[panel_index]
            radius = radii[panel_index]
            # People in name order inside a panel. `recurring_people` arrives
            # ordered by record count, and placing them by that count would make
            # the ring position a ranking of the people on it.
            people = sorted(cluster["recurring_people"], key=lambda person: person["name"])
            cx = 14.0 + panel_width * column_index + panel_width / 2
            cy = panel_top + row_heights[row_index] / 2 + 6.0
            label_bearing = len(people) <= MAX_NETWORK_LABELS

            span = ""
            if cluster.get("year_range"):
                first, last = cluster["year_range"]
                span = f", {first}" if first == last else f", {first}–{last}"
            venue = (cluster["journals"][0]["journal"] if cluster.get("journals") else "")
            panel = [
                rect(14.0 + panel_width * column_index + 4, panel_top + 2,
                     panel_width - 8, row_heights[row_index] - 8, fill="none", stroke=HAIRLINE,
                     **{"stroke-width": 1, "class": "cluster-panel"}),
                mid(cx, panel_top + 18,
                    en("cluster {value} — {size} of {denominator} records{span}", value=panel_index + 1, size=cluster['size'], denominator=denominator, span=span),
                    10.0),
                mid(cx, panel_top + 29, (venue[:44] or en("no journal recorded")), 9.0, fill=MUTED),
            ]

            at: dict[str, tuple[float, float]] = {}
            for node_index, person in enumerate(people):
                angle = -math.pi / 2 + 2 * math.pi * node_index / max(1, len(people))
                at[person["name"]] = (cx + radius * math.cos(angle),
                                      cy + radius * math.sin(angle))
            # Whatever room is left between the ring and the panel edge, at the
            # estimated character width `svg.legend` uses. A fixed truncation
            # would fit a two-node panel and run a twelve-node one into its
            # neighbour, because the ring grows with the node count.
            label_chars = max(6, int((panel_width / 2 - radius - 16.0) / 5.0))

            for edge in cluster.get("edges") or []:
                start, end = at.get(edge["a"]), at.get(edge["b"])
                if start is None or end is None:
                    continue
                panel.append(tag("g", {"class": "edge"},
                                 tooltip(en("{a} and {b} — {records} together in this cluster", a=edge['a'], b=edge['b'], records=_records(int(edge['n_records']))))
                                 + line(start[0], start[1], end[0], end[1], stroke=HAIRLINE,
                                        **{"stroke-width": 1})))

            for person in people:
                x, y = at[person["name"]]
                also = [p for p in appearances.get(person["name"], []) if p != panel_index + 1]
                mark = circle(x, y, 4.0, fill=WHITE if also else INK,
                              stroke=INK, **{"stroke-width": 1.4})
                if also:
                    # A concentric ring, never a fill or a hue: this is the one
                    # distinction on the figure that changes the reading, so it
                    # has to survive greyscale and print.
                    mark += circle(x, y, 7.0, fill="none", stroke=INK, **{"stroke-width": 1.2})
                note = (en("{name} — on {records} of this cluster's {size}", name=person['name'], records=_records(int(person['n_records'])), size=cluster['size']))
                if also:
                    note += en("; also recurs in cluster {clusters}", clusters=", ".join(str(p) for p in also))
                panel.append(tag("g", {"class": "node bridging" if also else "node"},
                                 tooltip(note) + mark))
                if label_bearing:
                    anchor = "start" if x >= cx else "end"
                    offset = 8.0 if x >= cx else -8.0
                    panel.append(text(x + offset, y + 3.5, person["name"][:label_chars], 9.0,
                                      **{"text-anchor": anchor}))
                table.append({
                    "cluster": panel_index + 1,
                    "person": person["name"],
                    "records in this cluster": person["n_records"],
                    "cluster size": cluster["size"],
                    "also recurs in cluster": ", ".join(str(p) for p in also) or "-",
                })
            if not label_bearing:
                panel.append(mid(cx, cy + radius + 16,
                                 en("{n_people} names — listed in the data table below", n_people=len(people)), 9.0,
                                 fill=MUTED))
            body.append(tag("g", {"class": "cluster"}, "".join(panel)))
        panel_top += row_heights[row_index]

    for extra_index, cluster in enumerate(hidden, start=len(shown) + 1):
        for person in sorted(cluster["recurring_people"], key=lambda p: p["name"]):
            table.append({
                "cluster": extra_index,
                "person": person["name"],
                "records in this cluster": person["n_records"],
                "cluster size": cluster["size"],
                "also recurs in cluster": en("- (cluster not drawn)"),
            })

    body.append(legend(14, height - 22, [
        ("dot", en("a person on two or more of that cluster's records")),
        ("dot-open", en("the same person recurs in another cluster too — ringed")),
        ("hairline", en("line = the two share a byline on at least one record"))]))
    body.append(text(14, height - 8,
                     en("A line is a shared byline, not a working relationship, and nothing here is "
                        "ordered by any count."), 9.0, fill=MUTED))

    caption = (en("{n_shown} of {n_clusters} cluster(s) drawn over {denominator} records; "
                  "{singletons} cluster(s) hold a single record. A node is a person on two "
                  "or more of a cluster's records; a line is a shared byline on at least one "
                  "of them.",
                  n_shown=len(shown), n_clusters=n_clusters, denominator=denominator, singletons=singletons))
    if bridging:
        caption += en(" {n_bridging} name(s) recur in more than one drawn cluster and are "
                      "ringed: {names}. A name bridging two panels is evidence they are one "
                      "person's records; a page of panels sharing nobody is the pattern that says "
                      "re-harvest with --orcid before believing any number in this report.",
                      n_bridging=len(bridging),
                      names=", ".join(bridging[:8]) + ("..." if len(bridging) > 8 else ""))
    else:
        caption += (en(" No name recurs in more than one drawn cluster. That is the pattern that says "
                       "re-harvest with --orcid before believing any number in this report — it is "
                       "not, on its own, proof of anything, because a broad-ranging researcher "
                       "accumulates unconnected collaborations too."))
    if hidden:
        caption += (en(" {n_hidden} further cluster(s) with recurring people are in the data "
                       "table and not drawn.",
                       n_hidden=len(hidden)))
    desc = caption + (en(" Each panel is one cluster; people sit on a ring in name order and lines "
                         "join people who share a byline. Ring position carries no meaning and "
                         "nothing on this figure is ordered by a count."))
    return artifact("C-NET",
                    svg=document("C-NET", CANVAS_WIDTH, height,
                                 en("Co-author clusters — who joins which records"), desc,
                                 "".join(body)),
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
        return prose_artifact("C-SPAN", (
            en("No span is observed: 0 of {cohort} people in the cohort have a first and "
               "a last record to measure between. {single_count} people appear once and "
               "have no span by construction. Nothing is plotted, and no zero-height axis "
               "is drawn in its place.",
               cohort=cohort, single_count=single_count)))

    counted = ", ".join(en("{label} {count} of {cohort}", label=en(label), count=int(buckets.get(key, 0)),
                           cohort=cohort) for key, label in _SPAN_LANES)
    sub_lines = wrap(
        en("One dot per person in the cohort of {cohort}. Censoring by lane: "
           "{counted}. {single_count} people appear once and have no span by "
           "construction. The axis is the interval between two publication dates, not "
           "time in the lab.",
           cohort=cohort, counted=counted, single_count=single_count), 175, 3)

    lanes = []
    for key, label in _SPAN_LANES:
        members = sorted((item for item in values if item["bucket"] == key),
                         key=lambda item: (item["span_years"], item.get("name") or ""))
        spans = [int(item["span_years"]) for item in members]
        pitch, radius, tallest = stack(spans, STACK_BUDGET / 2)
        head_room = 22.0 if key == "complete" else 0.0
        lanes.append((key, en(label), members, spans, pitch, radius,
                      max(34.0, 14.0 + pitch * tallest) + head_room))

    header = 20.0 + 11.0 * len(sub_lines) + 10.0
    height = header + sum(lane[-1] for lane in lanes) + 44.0
    plot_x0, plot_x1 = 226.0, CANVAS_WIDTH - 26
    bands = Bands(0, max(int(item["span_years"]) for item in values), plot_x0, plot_x1)

    body = preamble(en("Observed activity span"), sub_lines)
    table: list[dict[str, Any]] = []
    lane_top = header
    for key, label, members, spans, pitch, radius, lane_height in lanes:
        baseline = lane_top + lane_height - 12
        body += [line(plot_x0, baseline + 4, plot_x1, baseline + 4, stroke=HAIRLINE),
                 lane_label(14, baseline, en("{label} — {n_members} of {cohort}", label=label, n_members=len(members), cohort=cohort))]
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
            note = (en("{name}{marker} — span {value} year(s), {first_year} to {last_year}, "
                       "{label}",
                       name=item['name'], marker=item.get('marker', ''), value=value, first_year=item['first_year'], last_year=item['last_year'], label=label))
            if same_year:
                note += en(", two or more records in one calendar year")
            body.append(tag("g", {"class": f"dot span {key}"}, tooltip(note) + mark))
            table.append({"name": f"{item['name']}{item.get('marker', '')}", "span years": value,
                          "first record": item["first_year"], "last record": item["last_year"],
                          "censoring": label,
                          "two or more records in one year": en("yes") if same_year else en("no")})
        if key == "complete":
            low, high = s5.get("iqr") or (None, None)
            # The bracket is physically confined to this lane: the restriction is the
            # encoding that stops a complete-spans-only median being read as the cohort's.
            body.append(aggregate(
                bands, lane_top + 10, None if suppressed else s5.get("median"), (low, high),
                en("median {fmt} years, IQR {fmt_2} to {fmt_3}, over {complete_n} of {cohort} "
                   "people — complete spans only",
                   fmt=fmt(s5.get('median')), fmt_2=fmt(low), fmt_3=fmt(high), complete_n=complete_n, cohort=cohort),
                (plot_x0, lane_top + 2,
                 en("median not computed — {complete_n} complete spans, floor {min_n_aggregate}", complete_n=complete_n, min_n_aggregate=MIN_N_AGGREGATE))))
        lane_top += lane_height

    body += [bands.ticks(lane_top + 12),
             text(plot_x0, lane_top + 26, en("integer years between a person's first and last record"),
                   9.5, fill=MUTED),
             legend(14, height - 6, [
                 ("dot", en("complete span")), ("tail", en("censored: at least this long")),
                 ("dot-open", en("two or more records in one calendar year, at zero"))])]

    caption = (en("Censoring by lane: {counted}. {single_count} single-appearance people "
                  "have no span by construction.",
                  counted=counted, single_count=single_count))
    if suppressed:
        caption += (en(" Median not computed — {complete_n} complete spans, below the floor of "
                       "{min_n_aggregate}.",
                       complete_n=complete_n, min_n_aggregate=MIN_N_AGGREGATE))
    elif s5.get("median") is not None:
        low, high = s5.get("iqr") or (None, None)
        caption += (en(" Median {fmt} years, IQR {fmt_2} to {fmt_3}, over {complete_n} of "
                       "{cohort} people — complete spans only.",
                       fmt=fmt(s5['median']), fmt_2=fmt(low), fmt_3=fmt(high), complete_n=complete_n, cohort=cohort))
    desc = caption + (en(" Each lane is one censoring bucket on a shared span axis; censored dots carry "
                         "a directional tail meaning at least this long."))
    if suppressed:
        desc += en(" The median and IQR are not computed and no bracket is drawn.")
    return artifact("C-SPAN", svg=document("C-SPAN", CANVAS_WIDTH, height, en("Observed activity span"),
                                           desc, "".join(body)),
                   caption=caption, desc=desc, rows=table, drawn=True)
