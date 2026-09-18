"""
Two more static figures for the advisor profile report: C-POS and C-NET.

Split from `charts.py` because that file is held under 800 lines (acceptance item
35), not because these are a different kind of artifact. Same contract, same
primitives, same three rules `charts.py` states at length and this module obeys
without restating them:

  1. A degenerate figure ships a sentence, never an empty axis.
  2. Every figure carries its own denominator inside the SVG.
  3. Shape and text carry every distinction; colour carries none.

Neither figure computes anything. C-POS reads `metrics.pi_byline_positions`
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

from .svg import (
    BAND,
    HAIRLINE,
    INK,
    MUTED,
    WHITE,
    Bands,
    artifact,
    circle,
    document,
    hatch,
    lane_label,
    legend,
    line,
    mid,
    preamble,
    prose_artifact,
    rect,
    tag,
    text,
    tooltip,
    wrap,
)

CANVAS_WIDTH = 1100.0

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
    return f"{count} record" + ("" if count == 1 else "s")


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
        return prose_artifact("C-POS", NOT_MEASURED_SENTENCE + ". Nothing is plotted, and no empty "
                                       "axis is drawn in its place.")

    rows = [row for row in (s7.get("rows") or []) if isinstance(row.get("year"), int)]
    if not rows:
        return prose_artifact("C-POS", (
            f"0 of {denominator} records carry both a year and a byline position, so no column is "
            f"drawn and no empty axis is drawn in its place."))

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
        f"{label} {sum(per_lane[key].values())} of {denominator}" for key, label in BYLINE_LANES
    )
    sub_lines = wrap(
        f"{drawn_records} of {denominator} records carry a year and are drawn, one square each. By "
        f"lane: {lane_counts}. {len(flagged)} of {len(year_bins) or len(per_year_total)} year bins "
        f"are partial or undercounted by PubMed indexing lag and are marked below. Byline position "
        f"is a convention that differs by field and by journal, so it is reported and not "
        f"interpreted: no slot here is called senior, junior, better or worse.", 175, 4)

    unit = max(2.0, min(9.0, 84.0 / peak))
    gap = 1.0 if unit > 4 else 0.5
    lane_height = max(30.0, peak * (unit + gap) + 16.0)
    header = 20.0 + 11.0 * len(sub_lines) + 18.0
    # Room for two stacked flag lines under the busiest bin, plus the legend.
    height = header + lane_height * len(BYLINE_LANES) + 70.0
    plot_x0, plot_x1 = 262.0, CANVAS_WIDTH - 26
    bands = Bands(low, high, plot_x0, plot_x1)
    column = min(24.0, bands.width * 0.62)

    body = [hatch("hatch-pos")] + preamble("Records per year by byline position", sub_lines)
    if lag_years:
        left = bands.edge(lag_years[0])
        body += [rect(left, header, bands.edge(lag_years[-1] + 1) - left,
                      lane_height * len(BYLINE_LANES), fill=BAND),
                 text(left + 3, header - 5, "PubMed indexing lag — undercounted", 9.0, fill=MUTED)]

    table: list[dict[str, Any]] = []
    lane_top = header
    for key, label in BYLINE_LANES:
        bins = per_lane[key]
        lane_total = sum(bins.values())
        baseline = lane_top + lane_height - 8
        body += [line(plot_x0, baseline, plot_x1, baseline, stroke=HAIRLINE),
                 lane_label(14, baseline - 4, f"{label} — {lane_total} of {denominator}")]
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
                         tooltip(f"{year} — one of {count} record(s) with the PI as {label}")
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
            notes.append("PARTIAL")
            body.append(rect(bands.center(year) - column / 2, header, column,
                             lane_height * len(BYLINE_LANES), fill="url(#hatch-pos)",
                             stroke=HAIRLINE, **{"stroke-width": 0.6, "class": "flagged partial"}))
        if item and item.get("indexing_lag"):
            notes.append("INDEXING LAG")
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
             text(14, axis_y + 14, "calendar year", 9.5, fill=MUTED),
             text(14, axis_y + 25, "records that year", 9.0, fill=MUTED),
             legend(plot_x0, height - 8, [
                 ("filled", "one record"),
                 ("hatch", "partial bin — the window opens or closes mid-year"),
                 ("band", "PubMed indexing lag — undercounted")])]

    for year in range(low, high + 1):
        row: dict[str, Any] = {"year": year, "records": per_year_total.get(year, 0)}
        for key, label in BYLINE_LANES:
            row[label] = per_lane[key].get(year, 0)
        item = flagged.get(year)
        row["partial"] = bool(item and item.get("partial"))
        row["indexing lag"] = bool(item and item.get("indexing_lag"))
        table.append(row)

    caption = (f"{drawn_records} of {denominator} records carry a year and are drawn. By lane: "
               f"{lane_counts}. {len(flagged)} year bins are partial or undercounted by PubMed "
               f"indexing lag. Byline position is reported, never interpreted: no slot here is "
               f"called senior, junior, better or worse, and no rate is computed over these counts.")
    if unknown_positions:
        # A position the metric emitted and this figure has no lane for. Stated
        # rather than dropped: a silently missing record is the failure mode this
        # whole figure set exists to remove.
        caption += (" Positions with no lane in this figure, counted and not drawn: "
                    + ", ".join(f"{name} {count}" for name, count
                                in sorted(unknown_positions.items())) + ".")
    if counts and sum(counts.values()) != denominator:
        caption += (f" The metric counted {sum(counts.values())} positions over {denominator} "
                    f"records; the difference is records this figure could not place on a year.")
    desc = caption + (" Each lane is one byline position on a shared axis of calendar years; one "
                      "square is one record, a dashed slot is a year with none in that lane, and "
                      "no line and no curve is drawn across the years.")
    return artifact("C-POS",
                    svg=document("C-POS", CANVAS_WIDTH, height,
                                 "Records per year by byline position", desc, "".join(body)),
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
            f"Not partitioned: {denominator} records, below the floor of {s19.get('min_n', '?')}. "
            f"Below that floor the partition says nothing — four records sharing no co-author are "
            f"four clusters whether or not they are four people — so no network is drawn and no "
            f"empty frame is drawn in its place."))

    clusters = list(s19.get("clusters") or [])
    drawable = [c for c in clusters if c.get("recurring_people")]
    singletons = int(s19.get("singleton_clusters") or 0)
    n_clusters = int(s19.get("n_clusters") or len(clusters))

    if not drawable:
        return prose_artifact("C-NET", (
            f"No person recurs inside any of the {n_clusters} cluster(s) over {denominator} "
            f"records, so there is no co-authorship to draw: every non-PI name in this corpus "
            f"appears on exactly one record. {singletons} cluster(s) hold a single record. Nothing "
            f"is plotted, and no empty frame is drawn in its place."))

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
        f"{len(shown)} of {n_clusters} cluster(s) drawn, over {denominator} records. A panel is one "
        f"group of records joined by a shared co-author; a node is a person on two or more of that "
        f"group's records; a line is a shared byline, and its weight is printed rather than drawn. "
        f"{singletons} cluster(s) hold a single record and have nobody to join. "
        f"{len(drawable) - len(shown)} further cluster(s) with recurring people are listed in the "
        f"data table instead of drawn. {len(hyper)} record(s) left person-level analysis before "
        f"this partition was built, so anyone visible only through them is on no panel. Panels are "
        f"ordered largest first for readability and that order is not a ranking.", 175, 4)

    header = 20.0 + 11.0 * len(sub_lines) + 16.0
    panel_width = (CANVAS_WIDTH - 28.0) / NETWORK_COLUMNS
    radii = [_ring(len(c["recurring_people"])) for c in shown]
    rows_of_panels = [range(i, min(i + NETWORK_COLUMNS, len(shown)))
                      for i in range(0, len(shown), NETWORK_COLUMNS)]
    row_heights = [max(radii[i] for i in indices) * 2 + 66.0 for indices in rows_of_panels]
    height = header + sum(row_heights) + 46.0

    body = preamble("Co-author clusters — who joins which records", sub_lines)
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
                    f"cluster {panel_index + 1} — {cluster['size']} of {denominator} records{span}",
                    10.0),
                mid(cx, panel_top + 29, (venue[:44] or "no journal recorded"), 9.0, fill=MUTED),
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
                                 tooltip(f"{edge['a']} and {edge['b']} — "
                                         f"{_records(int(edge['n_records']))} together in this "
                                         f"cluster")
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
                note = (f"{person['name']} — on {_records(int(person['n_records']))} of this "
                        f"cluster's {cluster['size']}")
                if also:
                    note += "; also recurs in cluster " + ", ".join(str(p) for p in also)
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
                                 f"{len(people)} names — listed in the data table below", 9.0,
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
                "also recurs in cluster": "- (cluster not drawn)",
            })

    body.append(legend(14, height - 22, [
        ("dot", "a person on two or more of that cluster's records"),
        ("dot-open", "the same person recurs in another cluster too — ringed"),
        ("hairline", "line = the two share a byline on at least one record")]))
    body.append(text(14, height - 8,
                     "A line is a shared byline, not a working relationship, and nothing here is "
                     "ordered by any count.", 9.0, fill=MUTED))

    caption = (f"{len(shown)} of {n_clusters} cluster(s) drawn over {denominator} records; "
               f"{singletons} cluster(s) hold a single record. A node is a person on two or more of "
               f"a cluster's records; a line is a shared byline on at least one of them.")
    if bridging:
        caption += (f" {len(bridging)} name(s) recur in more than one drawn cluster and are ringed: "
                    + ", ".join(bridging[:8]) + ("..." if len(bridging) > 8 else "")
                    + ". A name bridging two panels is evidence they are one person's records; a "
                      "page of panels sharing nobody is the pattern that says re-harvest with "
                      "--orcid before believing any number in this report.")
    else:
        caption += (" No name recurs in more than one drawn cluster. That is the pattern that says "
                    "re-harvest with --orcid before believing any number in this report — it is "
                    "not, on its own, proof of anything, because a broad-ranging researcher "
                    "accumulates unconnected collaborations too.")
    if hidden:
        caption += (f" {len(hidden)} further cluster(s) with recurring people are in the data table "
                    f"and not drawn.")
    desc = caption + (" Each panel is one cluster; people sit on a ring in name order and lines "
                      "join people who share a byline. Ring position carries no meaning and "
                      "nothing on this figure is ordered by a count.")
    return artifact("C-NET",
                    svg=document("C-NET", CANVAS_WIDTH, height,
                                 "Co-author clusters — who joins which records", desc,
                                 "".join(body)),
                    caption=caption, desc=desc, rows=table, drawn=True)
