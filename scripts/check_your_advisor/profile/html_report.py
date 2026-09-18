"""
Self-contained HTML rendering of the advisor profile report.

One file, no CDN, no external font, no network at runtime, no build step: it
opens from `file://` on a machine that has never seen this repository. That is a
hard requirement rather than a preference — the page is a disclosure document
that gets re-saved, mailed and printed to PDF, and every one of those paths
breaks a page that fetches anything.

What is drawn where:

- Numbers, charts and suppression states are decided in Python. `MIN_N_AGGREGATE`
  and friends exist once, in `metrics.py`; a JavaScript renderer would be a
  second implementation of those floors and is how a 4-person median eventually
  ships as a number. The embedded JSON block is a copy-out surface and the sort
  key source for the roster controls, never the source of a rendered value.
- Charts arrive already rendered. `render_html` accepts the artifacts
  `profile.charts` produces — `{"svg", "caption", "desc", "rows", "drawn"}` per
  figure — and places them. This module never computes a figure and never
  imports the chart module, so the page builds with `charts=None`.

Escaping: `_esc` for element content, `_attr` for attribute values. Everything
interpolated from the report goes through one of them — author names,
affiliation strings and titles come from PubMed and legitimately contain `<`,
`&` and quotes. The single exception is a chart artifact's `svg`, which is
markup by contract and must arrive already escaped by the chart module; its
`caption` and `rows` are treated as text and escaped here.

Two deliberate divergences from docs/profile-visual-spec.md, both required by
the product owner and both recorded rather than silently taken:

- Section 14 is never collapsed (spec 7.2 collapses it). Section 0 and Section
  14 are the honesty of the document; a reader who has to click to find the
  qualifications ends up with the numbers and none of them. Sections 15 to 18
  join them for the same reason from the other side: they hold the citation
  numbers, the composite score, the star band, the graduate counts and the
  journal partitions, and their qualifying material — the coverage denominator,
  the weight table, the star band edges, the three nested populations, the
  edition and retrieval date of every partition — has to be on screen with the
  number it qualifies.

The page prints a score, a star band derived from it, and no ranking of people.
The header says exactly that. The distinction is the whole boundary this report
is built on: a value is a statement about one corpus, a position is a statement
about people relative to each other, and only the first is derivable from what
PubMed holds. Ranks exist on the side-by-side page, where the position is among
corpora a user chose to load and says so; nothing on this page places one person
above another, and the roster's order control still offers no count column.

Section 17 arrives second in the section list rather than in numeric order, and
this module renders sections in list order for that reason. It does not sort by
`id` and must not start: `report._build_sections` decides the sequence, and the
one section that repairs Section 0's limit belongs directly beneath it.
- The roster table is filterable and sortable client-side (spec 6 refuses both
  under a zero-JS premise). The refusal's actual reason — "one click on
  `appearances` turns the roster into a productivity leaderboard" — is preserved
  structurally: the order control offers name and year only. No count column is
  sortable at any point, because no such option exists to click.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from .report import STRATUM_LABEL, json_record

# Figure id -> (report section id, caveat ids that travel with the figure).
# The caveat ids are the visual spec's assignment, not the section's own list:
# CAV-09 is first registered at Section 5 but is needed under the Section 2
# timeline, because that figure is where a reader forms the tenure belief
# CAV-09 exists to deny.
#
# C-POS and C-NET carry no caveat ids. Section 7's qualifications are conditional
# on `measured` and Section 19's are prose rather than register entries, so
# pinning one to a figure would print a wording its own section does not.
FIGURE_PLACEMENT: tuple[tuple[str, int, tuple[str, ...]], ...] = (
    ("C-GANTT", 2, ("CAV-02", "CAV-03", "CAV-09")),
    ("C-LAG", 4, ("CAV-06", "CAV-07", "CAV-08")),
    ("C-SPAN", 5, ("CAV-09", "CAV-10", "CAV-11")),
    ("C-POS", 7, ()),
    ("C-YEAR", 9, ("CAV-17", "CAV-18")),
    ("C-TEAM", 10, ("CAV-19",)),
    ("C-NET", 19, ()),
)

# Sections whose body is never put behind a disclosure, whatever its length.
# 0 states what the report is not and 14 states what was refused; both are the
# qualifications on every number above them.
#
# 15 and 16 are here for the mirror-image reason. They carry the three numbers
# most likely to be quoted out of context — an h-index, a score out of 100 and a
# row of stars — and their qualifying material is the coverage denominator, the
# weight table, the band edges and the exclusion registers. A score that renders
# open above a weight table behind a click is a magic number with an alibi, so
# neither is ever collapsed.
#
# 17 and 18 are the same argument once more. 17 is the only section whose
# denominator is not conditioned on having published, which makes it the one
# section a reader most needs and the one they would never think to click; 18
# prints partitions whose meaning depends entirely on the edition and the
# retrieval date sitting beside them, so the number and its provenance are never
# separated by a disclosure.
#
# 20 is here for a reason of its own. Its per-source table is the only thing on
# the page tying each collected statement to a site and a day, and its row count
# is exactly the shape that trips COLLAPSE_TABLE_ROWS below — a reader who
# expands the statements while the sources stay folded away is reading anonymous
# claims about a named person. The two never separate.
ALWAYS_OPEN_SECTIONS = frozenset({0, 14, 15, 16, 17, 18, 20})

# Section 13 is every title in the corpus, verbatim. It is a reading exercise,
# not a scanning one, and it is the longest block on the page.
COLLAPSE_WHOLE_BODY = frozenset({13})

# A table longer than this is a lookup surface rather than something read in
# passing, so it collapses behind a summary that states its own row count.
COLLAPSE_TABLE_ROWS = 15

_IMAGE_LINE = re.compile(r"^!\[[^\]]*\]\([^)]*\)$")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`]+)`")
_NON_SLUG = re.compile(r"[^a-z0-9]+")


# --- escaping -------------------------------------------------------------


def _esc(value: Any) -> str:
    """
    Escape for element content.

    Quotes are deliberately left alone: caveat text contains them, and the
    caveats must appear on the page byte-identical to `caveats.CAVEATS` so a
    reader comparing the two finds no difference to wonder about.
    """
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _attr(value: Any) -> str:
    """Escape for a double-quoted attribute value. Quotes escaped here, unlike `_esc`."""
    return _esc(value).replace('"', "&quot;").replace("'", "&#39;")


def _slug(value: str) -> str:
    return _NON_SLUG.sub("-", str(value).lower()).strip("-") or "x"


def _inline(text: str) -> str:
    """
    The inline Markdown the report bodies actually use: `code` and **bold**.

    Escaping runs first, so a title containing `<b>` becomes text; the two
    patterns below can only match characters the report renderer put there.
    """
    out = _esc(text)
    out = _CODE.sub(r"<code>\1</code>", out)
    return _BOLD.sub(r"<strong>\1</strong>", out)


# --- report body parsing --------------------------------------------------


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_rule(cells: Sequence[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-+:?", cell) for cell in cells)


def _parse_blocks(lines: Sequence[str]) -> list[dict[str, Any]]:
    """
    Turn a section's Markdown lines into paragraph / list / table blocks.

    `report.py` emits a small, fixed subset, so this is a reader for that subset
    and not a Markdown implementation. Image lines are dropped: embedding the
    old PNG timeline reimports the 1934x21506 problem this page exists to fix,
    and `_roster_body` prints the same path as text on the following line.
    """
    blocks: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped or _IMAGE_LINE.match(stripped):
            index += 1
            continue
        if stripped.startswith("|"):
            grid = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                grid.append(_cells(lines[index]))
                index += 1
            header = grid[0] if grid else []
            body = [row for row in grid[1:] if not _is_rule(row)]
            blocks.append({"kind": "table", "header": header, "rows": body})
            continue
        if stripped.startswith("- "):
            items = []
            while index < len(lines) and lines[index].strip().startswith("- "):
                items.append(lines[index].strip()[2:])
                index += 1
            blocks.append({"kind": "ul", "items": items})
            continue
        paragraph = []
        while index < len(lines):
            current = lines[index].strip()
            if not current or current.startswith(("|", "- ")) or _IMAGE_LINE.match(current):
                break
            paragraph.append(current)
            index += 1
        blocks.append({"kind": "p", "lines": paragraph})
    return blocks


# --- block rendering ------------------------------------------------------


def _render_table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    head = "".join('<th scope="col">' + _inline(cell) + "</th>" for cell in header)
    body = "".join(
        "<tr>" + "".join(f"<td>{_inline(cell)}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return (
        '<div class="tablescroll"><table><thead><tr>'
        f"{head}</tr></thead><tbody>{body}</tbody></table></div>"
    )


def _render_block(block: Mapping[str, Any], collapsible: bool) -> str:
    kind = block["kind"]
    if kind == "p":
        return "".join(f"<p>{_inline(line)}</p>" for line in block["lines"])
    if kind == "ul":
        items = "".join(f"<li>{_inline(item)}</li>" for item in block["items"])
        return f"<ul>{items}</ul>"
    table = _render_table(block["header"], block["rows"])
    if collapsible and len(block["rows"]) > COLLAPSE_TABLE_ROWS:
        # The summary states the row count so a reader who never opens it, and a
        # print engine that fails to expand it, still learns the true magnitude.
        label = _esc(block["header"][0] if block["header"] else "rows")
        return (
            f"<details><summary>Table — {len(block['rows'])} rows, first column "
            f"{label}</summary>{table}</details>"
        )
    return table


# --- caveats --------------------------------------------------------------


def _caveat_html(text: str, caveat_id: str = "") -> str:
    """
    One caveat, always visible.

    A caveat behind a toggle is a caveat nobody reads, so this never emits a
    `<details>`, and the text is never trimmed to fit a layout.
    """
    marker = f'<span class="caveat-id">{_esc(caveat_id)}</span>' if caveat_id else ""
    attr = f' data-caveat="{_attr(caveat_id)}"' if caveat_id else ""
    return f'<blockquote class="caveat"{attr}><p>{marker}{_esc(text)}</p></blockquote>'


def _caveat_index(report: Mapping[str, Any]) -> dict[str, str]:
    """Caveat text -> id, so a section's caveat strings can be labelled and de-duplicated."""
    return {text: key for key, text in (report.get("caveats") or {}).items()}


# --- figures --------------------------------------------------------------


def _figure_rows_table(rows: Sequence[Mapping[str, Any]]) -> str:
    """
    The per-figure text equivalent.

    It exists so every fact a mouse-only SVG `<title>` shows also exists as DOM
    text, which is the condition for keeping those tooltips at all.
    """
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    header = [column.replace("_", " ") for column in columns]
    body = [[_flat(row.get(column, "")) for column in columns] for row in rows]
    return _render_table(header, body)


def _flat(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return "" if value is None else str(value)


def _render_figure(
    figure_id: str,
    artifact: Mapping[str, Any],
    caveat_ids: Sequence[str],
    caveats: Mapping[str, str],
) -> str:
    slug = _slug(figure_id)
    caption = str(artifact.get("caption") or "")
    svg = str(artifact.get("svg") or "")
    drawn = bool(artifact.get("drawn", True))
    rows = list(artifact.get("rows") or [])

    if drawn:
        # tabindex makes a figure wider than the viewport scrollable from the
        # keyboard; without it the right-hand years are mouse-only.
        content = (
            f'<div class="figscroll" tabindex="0" role="region" '
            f'aria-label="{_attr(figure_id)} figure, scrollable">{svg}</div>'
        )
    else:
        # A degenerate state ships prose instead of an axis. Never an empty frame.
        content = f'<div class="figure-note">{svg}</div>'

    parts = [
        # No explicit role: <figure> already maps to the figure role, and naming
        # it `group` would discard that. The caption is the accessible name.
        f'<figure id="fig-{slug}" aria-labelledby="fig-{slug}-cap">',
        content,
        f'<figcaption id="fig-{slug}-cap">{_esc(caption)}</figcaption>',
    ]
    parts += [_caveat_html(caveats[key], key) for key in caveat_ids if key in caveats]
    if rows:
        parts.append(
            f"<details><summary>Data table for this figure — {len(rows)} rows</summary>"
            f"{_figure_rows_table(rows)}</details>"
        )
    parts.append("</figure>")
    return "".join(parts)


def _normalise_charts(charts: Any) -> dict[str, Mapping[str, Any]]:
    """
    Accept the chart bundle under any reasonable key spelling.

    The chart module is written separately; keying on `C-GANTT` versus `gantt`
    is not worth a wiring failure that silently drops every figure.
    """
    if not isinstance(charts, Mapping):
        return {}
    out: dict[str, Mapping[str, Any]] = {}
    for key, value in charts.items():
        if isinstance(value, Mapping):
            out[str(key).upper().replace("_", "-").removeprefix("C-")] = value
    return out


# --- roster table ---------------------------------------------------------

_ROSTER_COLUMNS: tuple[tuple[str, str], ...] = (
    ("person", "name"),
    ("position label", "stratum"),
    ("affiliation signal", "affiliation_signal"),
    ("appearances", "n_appearances"),
    ("lead slots", "n_first_slots"),
    ("equal-contribution flags", "n_equal_contrib"),
    ("first", "first_year"),
    ("last", "last_year"),
    ("censoring", "censoring"),
    ("notes", "flags"),
)

# The order control's whole option list. Appearances, lead slots and
# equal-contribution flags are absent on purpose: ordering people by a count is
# a productivity ranking, and the cheapest way to guarantee the page never does
# it is to make the option non-existent rather than merely unused.
_ROSTER_ORDERS: tuple[tuple[str, str], ...] = (
    ("report", "report order — first appearance, then name"),
    ("name", "name A to Z"),
    ("name-desc", "name Z to A"),
    ("first", "first appearance, earliest first"),
    ("first-desc", "first appearance, latest first"),
    ("last", "last appearance, earliest first"),
    ("last-desc", "last appearance, latest first"),
)


def _roster_cell(row: Mapping[str, Any], field: str) -> str:
    if field == "name":
        return f"{row.get('name', '')}{row.get('marker', '')}"
    if field == "stratum":
        return STRATUM_LABEL.get(str(row.get("stratum")), str(row.get("stratum", "")))
    if field == "censoring":
        marks = [
            label
            for label, flag in (("left", row.get("left_censored")), ("right", row.get("right_censored")))
            if flag
        ]
        return ", ".join(marks) or "none"
    if field == "flags":
        return ", ".join(row.get("flags") or []) or "-"
    return _flat(row.get(field, ""))


def _render_roster(roster: Mapping[str, Any]) -> str:
    """
    The 277-row person table: collapsed, then filterable and orderable.

    This is the single worst artifact in the Markdown output, and the one thing
    the page has to fix. Rows are rendered server-side in report order, so the
    table is complete with JavaScript disabled and complete on paper; the script
    only reorders and hides nodes that are already in the DOM.
    """
    rows = list(roster.get("rows") or [])
    total = len(rows)
    strata = [key for key in ("A", "B", "C", "D") if any(r.get("stratum") == key for r in rows)]

    head = "".join(f'<th scope="col">{_esc(label)}</th>' for label, _ in _ROSTER_COLUMNS)
    body = []
    for index, row in enumerate(rows):
        cells = "".join(f"<td>{_esc(_roster_cell(row, field))}</td>" for _, field in _ROSTER_COLUMNS)
        body.append(f'<tr data-i="{index}">{cells}</tr>')

    options = "".join(
        f'<option value="{_attr(value)}">{_esc(label)}</option>' for value, label in _ROSTER_ORDERS
    )
    strata_options = '<option value="">every position label</option>' + "".join(
        f'<option value="{_attr(key)}">{_esc(STRATUM_LABEL[key])}</option>' for key in strata
    )

    return (
        f'<details id="roster"><summary>Full roster table — {total} rows, '
        "one per person, ordered by first appearance</summary>"
        # Hidden until the script un-hides it: a filter box that does nothing
        # because JavaScript is off would be worse than no filter box.
        '<div class="roster-controls" id="roster-controls" hidden>'
        '<p class="control"><label for="roster-q">Find a person '
        "(name, position label, affiliation signal)</label>"
        '<input type="search" id="roster-q" autocomplete="off"></p>'
        '<p class="control"><label for="roster-stratum">Show position label</label>'
        f'<select id="roster-stratum">{strata_options}</select></p>'
        '<p class="control"><label for="roster-order">Order rows by</label>'
        f'<select id="roster-order">{options}</select></p>'
        f'<p class="status" id="roster-status" role="status" aria-live="polite">'
        f"Showing {total} of {total} people.</p>"
        '<p class="note">Ordering by appearances, lead slots or equal-contribution flags '
        "is not offered. Ordering people by a count is a productivity ranking, and this "
        "report does not rank people. Filtering hides rows on screen only; the counts in "
        "this section and every figure are over the whole roster, and printing restores "
        "every row.</p>"
        "</div>"
        f'<div class="tablescroll"><table id="roster-table"><thead><tr>{head}</tr></thead>'
        f'<tbody id="roster-body">{"".join(body)}</tbody></table></div>'
        "</details>"
    )


# --- sections -------------------------------------------------------------


def _render_section(
    section: Mapping[str, Any],
    report: Mapping[str, Any],
    charts: Mapping[str, Mapping[str, Any]],
    caveat_ids: Mapping[str, str],
) -> str:
    section_id = int(section["id"])
    always_open = section_id in ALWAYS_OPEN_SECTIONS
    caveats = report.get("caveats") or {}
    parts = [
        f'<section class="rep" id="s{section_id}" aria-labelledby="s{section_id}-h">',
        f'<h2 id="s{section_id}-h">{section_id}. {_esc(section["title"])}</h2>',
    ]

    # Identity warnings first, above the prose and outside every disclosure.
    # Each one stands where a refusal page used to stand, so it gets a callout
    # of its own rather than another blockquote: a reader who has learned to
    # skim caveat blocks would skim this too, and this is the one line that says
    # the section may not be about the person named at the top of the page.
    for text in section.get("warnings") or []:
        parts.append(
            f'<div class="warnbox" role="note"><p>{_inline(text)}</p></div>'
        )

    # Prose is never collapsed. It is the section's own statement of what it can
    # and cannot say, which is exactly the part that must not need a click.
    for block in _parse_blocks(section.get("prose") or []):
        parts.append(_render_block(block, collapsible=False))

    shown_here: list[str] = []
    for figure_id, target, figure_caveats in FIGURE_PLACEMENT:
        artifact = charts.get(figure_id.removeprefix("C-"))
        if target != section_id or artifact is None:
            continue
        parts.append(_render_figure(figure_id, artifact, figure_caveats, caveats))
        shown_here += [key for key in figure_caveats if key in caveats]

    body_blocks = _parse_blocks(section.get("body") or [])
    roster = (report.get("metrics") or {}).get("s2") if section_id == 2 else None
    rendered_body = []
    for block in body_blocks:
        if roster and block["kind"] == "table" and block["header"][:1] == ["person"]:
            # Rendered from the metric rows instead of from the Markdown table, so
            # the sort keys are typed and the row indices line up with the
            # embedded JSON the controls read. If `_roster_body` ever renames its
            # first column this falls back to the plain table below: still every
            # row, still report order, just without the controls.
            rendered_body.append(_render_roster(roster))
            continue
        rendered_body.append(_render_block(block, collapsible=not always_open))
    if section_id in COLLAPSE_WHOLE_BODY and rendered_body and not always_open:
        count = ((report.get("metrics") or {}).get("s13") or {}).get("denominator", "all")
        parts.append(
            f"<details><summary>All {count} record titles, verbatim, by year</summary>"
            f"{''.join(rendered_body)}</details>"
        )
    else:
        parts += rendered_body

    for text in section.get("caveats") or []:
        key = caveat_ids.get(text, "")
        if key and key in shown_here:
            # Already printed under this section's figure; printing it twice
            # trains the reader to skip caveat blocks.
            continue
        parts.append(_caveat_html(text, key))
        if key:
            shown_here.append(key)

    parts.append("</section>")
    return "".join(parts)


def _render_refusal(report: Mapping[str, Any]) -> str:
    """A fired gate renders the gate, the observed values and the fix. Nothing else."""
    gate = report.get("gate") or {}
    observed = "".join(
        f"<li><strong>{_esc(key)}</strong>: {_esc(_flat(value))}</li>"
        for key, value in (gate.get("observed") or {}).items()
    )
    return (
        '<section class="rep" id="gate">'
        f'<h2>Report refused — gate {_esc(gate.get("id", "?"))} '
        f'({_esc(gate.get("name", ""))})</h2>'
        f'<p>{_esc(gate.get("message", ""))}</p>'
        "<h3>Observed</h3>"
        f'<ul>{observed or "<li>(none)</li>"}</ul>'
        "</section>"
    )


def _render_nav(sections: Sequence[Mapping[str, Any]]) -> str:
    items = "".join(
        f'<li><a href="#s{int(s["id"])}">{int(s["id"])}. {_esc(s["title"])}</a></li>'
        for s in sections
    )
    return f'<nav class="toc" aria-label="Sections"><ol>{items}</ol></nav>'


def _json_script(report: Mapping[str, Any]) -> str:
    """
    The report numbers, embedded for copy-out.

    `<`, `>` and `&` become their `\\uXXXX` escapes: they can only occur inside
    JSON string values, the escape is still valid JSON on both sides, and it
    makes a PubMed title containing `</script>` unable to terminate the element.
    """
    payload = json.dumps(json_record(dict(report)), ensure_ascii=False, indent=2, default=str)
    for raw, escaped in (
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("&", "\\u0026"),
        (" ", "\\u2028"),
        (" ", "\\u2029"),
    ):
        payload = payload.replace(raw, escaped)
    return f'<script type="application/json" id="report-data">{payload}</script>'


# --- page -----------------------------------------------------------------


def render_html(report: Mapping[str, Any], charts: Any = None) -> str:
    """
    The whole page as one string.

    `charts` maps figure id to an artifact dict `{"svg", "caption", "desc",
    "rows", "drawn"}` as produced by `profile.charts`. Missing or None means the
    page renders every section without figures, which is what lets this module
    ship before the chart module does.
    """
    name = str(report.get("author_name") or "").strip() or "(unnamed researcher)"
    title = f"Observed publication pattern — {name}"
    refused = bool(report.get("refused"))
    sections = list(report.get("sections") or [])
    bundle = _normalise_charts(charts)

    parts = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_esc(title)}</title>",
        f"<style>{_CSS}</style>",
        "</head>",
        "<body>",
        '<a class="skip" href="#main">Skip to content</a>',
        '<header class="page">',
        f"<h1>{_esc(title)}</h1>",
        f'<p class="stamp">Generated {_esc(report.get("generated_at", ""))}. '
        "This report describes publication metadata. Section 16 states one composite score out "
        "of 100 beside every input and the full weight table that produced it, and the star band "
        "that score falls in beside the band edges that decided it — a star count is that same "
        "score coarsened and nothing else. It contains no ranking of people: nobody here is "
        "placed above anybody, no roster is ordered by a count, and there is no percentile, no "
        "quantile position and no letter grade anywhere on the page. Section 17 counts graduates "
        "who published nothing at all, which is the one population every other section is blind "
        "to. Section 18 carries journal-level numbers only when you supplied the table yourself, "
        "with the edition and the retrieval date printed beside every one, and beneath them the "
        "public risk signals three open APIs returned about those journals on a stated day — "
        "statements with their source attached, never a rating: nothing on this page calls any "
        "journal predatory and no count of those signals becomes a grade. Section 20 reproduces "
        "student evaluations you collected by hand, each beside its source and the day it was "
        "read; they are printed as given and nothing is computed over them — no sentiment "
        "analysis, no average, no rating, and no contribution to any score on this page.</p>",
        "</header>",
    ]

    if refused:
        parts.append('<main id="main">')
        parts.append(_render_refusal(report))
    else:
        caveat_ids = _caveat_index(report)
        parts.append(_render_nav(sections))
        parts.append('<main id="main">')
        parts += [_render_section(section, report, bundle, caveat_ids) for section in sections]

    parts += [
        '<section class="rep" id="data">',
        "<h2>Report data</h2>",
        "<p>The same numbers, embedded verbatim as JSON so they can be copied out. Every value "
        "and every suppression decision on this page was rendered in Python from this record; "
        "the block below is not the render source for any of them. The roster controls read it "
        "for sort keys only.</p>",
        _json_script(report),
        "</section>",
        "</main>",
    ]
    if not refused:
        parts.append(f"<script>{_JS}</script>")
    parts += ["</body>", "</html>"]
    return "\n".join(parts) + "\n"


def write_html(
    report: Mapping[str, Any],
    output_dir: str | Path,
    charts: Any = None,
) -> str:
    """Write the page beside the Markdown and JSON, using the same timestamp stem."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(str(report["generated_at"])).strftime("%Y%m%d_%H%M%S")
    path = directory / f"advisor_profile_{stamp}.html"
    path.write_text(render_html(report, charts), encoding="utf-8")
    return str(path)


# --- inline assets --------------------------------------------------------

# System fonts only: a webfont is a network fetch, and this page must render
# identically from a thumb drive. No colour carries meaning on its own anywhere,
# and there is no ramp, threshold band or good/bad region on the page.
_CSS = """
:root{--ink:#1a1a1a;--muted:#454545;--rule:#c4c4c4;--bg:#fff;--accent:#12457f;--soft:#f2f2f2}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-size:1rem;line-height:1.55;
 font-family:ui-sans-serif,system-ui,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.skip{position:absolute;left:-9999px;top:0}
.skip:focus{position:static;display:inline-block;margin:.5rem;padding:.5rem;background:var(--soft)}
header.page{padding:1.5rem 1rem .9rem;border-bottom:3px solid var(--ink)}
h1{font-size:1.5rem;margin:0 0 .4rem;line-height:1.25}
.stamp{margin:0;color:var(--muted);font-size:.85rem;max-width:78ch}
nav.toc{position:sticky;top:0;z-index:2;background:var(--bg);border-bottom:1px solid var(--rule);
 padding:.4rem 1rem}
nav.toc ol{list-style:none;display:flex;flex-wrap:wrap;gap:.1rem .9rem;margin:0;padding:0;font-size:.8rem}
nav.toc a{color:var(--accent)}
main{display:block}
section.rep{max-width:1140px;margin:0 auto;padding:1.1rem 1rem 1.4rem;border-top:1px solid var(--rule)}
section.rep>p,section.rep>ul,section.rep>ol,section.rep>blockquote{max-width:78ch}
section.rep:target{box-shadow:inset 5px 0 0 var(--accent)}
h2{font-size:1.15rem;margin:.2rem 0 .6rem}
h3{font-size:1rem;margin:.9rem 0 .4rem}
p{margin:.5rem 0}
ul{margin:.4rem 0;padding-left:1.3rem}
li{margin:.15rem 0}
code{font-family:ui-monospace,SFMono-Regular,Consolas,"Liberation Mono",monospace;font-size:.85em;
 background:var(--soft);padding:.05rem .25rem;border-radius:2px;overflow-wrap:anywhere}
blockquote.caveat{margin:.7rem 0;padding:.55rem .8rem;border-left:5px solid var(--accent);
 background:var(--soft);font-size:.9rem}
blockquote.caveat p{margin:0}
.caveat-id{font-weight:700;font-size:.72rem;letter-spacing:.05em;margin-right:.45rem;
 text-transform:uppercase}
.warnbox{margin:.2rem 0 1rem;padding:.7rem .9rem;border:3px solid var(--ink);
 background:var(--soft);max-width:78ch}
.warnbox p{margin:0;font-size:.95rem}
figure{margin:1rem 0;max-width:1100px}
.figscroll{overflow-x:auto;border:1px solid var(--rule);padding:.3rem;background:var(--bg)}
.figure-note{border:1px solid var(--rule);padding:.6rem;background:var(--soft);max-width:78ch}
figcaption{margin:.45rem 0 0;font-size:.88rem;color:var(--ink);max-width:78ch}
.tablescroll{overflow-x:auto}
table{border-collapse:collapse;font-size:.82rem;width:100%}
th,td{border:1px solid var(--rule);padding:.22rem .4rem;text-align:left;vertical-align:top}
thead th{background:var(--soft);font-weight:700}
details{margin:.7rem 0;border:1px solid var(--rule);border-radius:3px;background:var(--bg)}
summary{padding:.45rem .6rem;background:var(--soft);cursor:pointer;font-weight:600;font-size:.9rem}
details>*:not(summary){margin:.5rem .6rem .6rem}
.roster-controls{display:flex;flex-wrap:wrap;gap:.6rem 1.2rem;align-items:flex-end}
.roster-controls .control{display:flex;flex-direction:column;gap:.2rem;margin:0;font-size:.82rem}
.roster-controls input,.roster-controls select{font:inherit;font-size:.85rem;padding:.2rem .3rem;
 border:1px solid var(--rule);background:var(--bg);color:var(--ink);min-width:14rem}
.roster-controls .status{margin:0;font-size:.85rem;font-weight:600;flex-basis:100%}
.roster-controls .note{margin:0;font-size:.8rem;color:var(--muted);flex-basis:100%;max-width:78ch}
tr.is-filtered-out{display:none}
a{color:var(--accent)}
:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
@page{margin:15mm}
@media print{
 nav.toc{position:static}
 .roster-controls{display:none}
 details:not([open])>*:not(summary){display:revert}
 tr.is-filtered-out{display:table-row}
 figure,blockquote.caveat,.warnbox,tr{break-inside:avoid}
 h2{break-after:avoid}
 thead{display:table-header-group}
 #fig-c-gantt{break-before:page}
 section.rep{border-top:1px solid #000}
 a{color:inherit;text-decoration:none}
}
@media (max-width:600px){
 .roster-controls input,.roster-controls select{min-width:0;width:100%}
}
"""

# The only script on the page that runs. It reorders and hides table rows that
# Python already rendered; it never formats a number, never computes a floor and
# never writes markup, so there is no path from report data into the DOM through
# it. With JavaScript off the table is complete and in report order.
_JS = """
(function(){
"use strict";
var data=null;
try{data=JSON.parse(document.getElementById("report-data").textContent);}catch(e){data=null;}

/* A collapsed disclosure prints inconsistently across engines, and a caveat or a
   name missing from the paper copy is the failure this page exists to avoid. */
var reopened=[];
window.addEventListener("beforeprint",function(){
 reopened=[];
 var all=document.getElementsByTagName("details");
 for(var i=0;i<all.length;i++){if(!all[i].open){all[i].open=true;reopened.push(all[i]);}}
});
window.addEventListener("afterprint",function(){
 for(var i=0;i<reopened.length;i++){reopened[i].open=false;}
 reopened=[];
});

var tbody=document.getElementById("roster-body");
if(!tbody||!data||!data.metrics||!data.metrics.s2){return;}
var rows=data.metrics.s2.rows||[];
var trs=Array.prototype.slice.call(tbody.rows);
if(trs.length!==rows.length){return;}
var query=document.getElementById("roster-q");
var stratum=document.getElementById("roster-stratum");
var order=document.getElementById("roster-order");
var status=document.getElementById("roster-status");
var controls=document.getElementById("roster-controls");
if(controls){controls.hidden=false;}

function nameKey(i){var r=rows[i]||{};return String(r.name||"").toLowerCase();}
function yearKey(i,k){var v=(rows[i]||{})[k];return typeof v==="number"?v:0;}
function labelOf(i){var c=trs[i].cells[1];return c?c.textContent:"";}
function haystack(i){
 var r=rows[i]||{};
 return [r.name||"",labelOf(i),r.affiliation_signal||"",(r.flags||[]).join(" ")]
  .join(" ").toLowerCase();
}
function comparator(mode){
 if(mode==="name"){return function(a,b){return nameKey(a).localeCompare(nameKey(b))||a-b;};}
 if(mode==="name-desc"){return function(a,b){return nameKey(b).localeCompare(nameKey(a))||a-b;};}
 if(mode==="first"){return function(a,b){return yearKey(a,"first_year")-yearKey(b,"first_year")||a-b;};}
 if(mode==="first-desc"){return function(a,b){return yearKey(b,"first_year")-yearKey(a,"first_year")||a-b;};}
 if(mode==="last"){return function(a,b){return yearKey(a,"last_year")-yearKey(b,"last_year")||a-b;};}
 if(mode==="last-desc"){return function(a,b){return yearKey(b,"last_year")-yearKey(a,"last_year")||a-b;};}
 return function(a,b){return a-b;};
}
function apply(){
 var text=(query?query.value:"").trim().toLowerCase();
 var want=stratum?stratum.value:"";
 var indices=[];
 for(var i=0;i<trs.length;i++){indices.push(i);}
 indices.sort(comparator(order?order.value:"report"));
 var frag=document.createDocumentFragment();
 var visible=0;
 for(var k=0;k<indices.length;k++){
  var i2=indices[k];
  var row=rows[i2]||{};
  var ok=(!want||row.stratum===want)&&(!text||haystack(i2).indexOf(text)>=0);
  trs[i2].className=ok?"":"is-filtered-out";
  if(ok){visible++;}
  frag.appendChild(trs[i2]);
 }
 tbody.appendChild(frag);
 if(status){
  var total=trs.length;
  var msg="Showing "+visible+" of "+total+" people.";
  if(visible!==total){
   msg+=" "+(total-visible)+" hidden by the filter. Every count in this report is over all "+
    total+" and does not change.";
  }
  status.textContent=msg;
 }
}
if(query){query.addEventListener("input",apply);}
if(stratum){stratum.addEventListener("change",apply);}
if(order){order.addEventListener("change",apply);}
})();
"""
