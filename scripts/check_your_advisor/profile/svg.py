"""
SVG primitives for the profile figures. No domain knowledge lives here.

Split out of `charts.py` under the implementation layout in docs/profile-visual-spec.md
Section 3, and because both files must stay under 800 lines (acceptance item 35).

Everything is a pure string function, so a figure is testable by string assertion under
this repo's offline plain-script convention. Two properties are load-bearing rather than
incidental:

- Coordinates are rounded at emission, so two runs over one corpus produce byte-identical
  markup and a diff between them means the data changed.
- `Bands` positions everything on integer values only. `analysis._date_iso` fabricates
  January when PubMed omits the month, so a sub-year x position would be precision the
  record does not carry.

Colour is greyscale throughout. Section 8 of the visual spec: no hue carries a
distinction on its own, and any shading that separated better from worse would be a
grade — which this product does not emit at any sample size.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

INK = "#1a1a1a"
MUTED = "#767676"  # 4.5:1 on white as text, 3:1 as a mark
HAIRLINE = "#b8b8b8"
BAND = "#ececec"
PLATE_FILL = "#f4f4f4"
WHITE = "#ffffff"


def escape(value: Any) -> str:
    """XML-escape. Author names are arbitrary PubMed text and one unescaped `&` voids
    the document, so nothing reaches the output without passing through here."""
    return (str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def num(value: float) -> str:
    """Two-decimal coordinates. Rounding at emission is what makes two runs over one
    corpus byte-identical; raw float repr depends on the arithmetic path taken."""
    text_value = f"{float(value):.2f}"
    return text_value.rstrip("0").rstrip(".") if "." in text_value else text_value


def attrs(pairs: dict[str, Any]) -> str:
    return "".join(
        f' {key}="{escape(num(value) if isinstance(value, float) else value)}"'
        for key, value in pairs.items() if value is not None and value != ""
    )


def tag(name: str, pairs: dict[str, Any], children: str = "") -> str:
    if children:
        return f"<{name}{attrs(pairs)}>{children}</{name}>"
    return f"<{name}{attrs(pairs)}/>"


def rect(x: float, y: float, w: float, h: float, **pairs: Any) -> str:
    return tag("rect", {"x": x, "y": y, "width": max(0.0, w), "height": max(0.0, h), **pairs})


def line(x1: float, y1: float, x2: float, y2: float, **pairs: Any) -> str:
    return tag("line", {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "stroke": INK, **pairs})


def circle(cx: float, cy: float, r: float, **pairs: Any) -> str:
    return tag("circle", {"cx": cx, "cy": cy, "r": r, **pairs})


def text(x: float, y: float, content: str, size: float = 11.0, **pairs: Any) -> str:
    return tag("text", {"x": x, "y": y, "font-size": num(size), "fill": INK,
                        "font-family": "sans-serif", **pairs}, escape(content))


def mid(x: float, y: float, content: str, size: float = 9.5, **pairs: Any) -> str:
    return text(x, y, content, size, **{"text-anchor": "middle", **pairs})


def tooltip(content: str) -> str:
    """Native `<title>`. Mouse-only, which is why every fact it carries also exists in
    the figure's data table (visual spec 7.4)."""
    return f"<title>{escape(content)}</title>"


def fmt(value: float | None) -> str:
    """Same formatting as `report._fmt_number`, so a figure and the prose beside it
    never print one median two ways."""
    if value is None:
        return "n/a"
    return str(int(value)) if float(value).is_integer() else f"{float(value):.1f}"


def wrap(content: str, width: int, max_lines: int) -> list[str]:
    """Greedy wrap whose last line absorbs the overflow instead of adding another.

    Figure height is a budget an acceptance check measures, so the line count is capped
    rather than the text truncated: a dropped disclosure is worse than a long line.
    """
    lines: list[str] = []
    current = ""
    for word in content.split():
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > width and len(lines) < max_lines - 1:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


class Bands:
    """One column per integer value.

    Integer positions only, everywhere: `analysis._date_iso` fabricates January when
    PubMed omits the month, so any sub-year position would be manufactured precision.
    """

    def __init__(self, low: int, high: int, x0: float, x1: float) -> None:
        self.low, self.high = int(low), max(int(low), int(high))
        self.x0, self.x1 = x0, x1
        self.count = self.high - self.low + 1
        self.width = (x1 - x0) / self.count

    def center(self, value: float) -> float:
        return self.x0 + (float(value) - self.low + 0.5) * self.width

    def edge(self, value: float) -> float:
        return self.x0 + (float(value) - self.low) * self.width

    def ticks(self, y: float, max_labels: int = 26) -> str:
        stride = max(1, -(-self.count // max_labels))
        values = range(self.low, self.high + 1)
        return "".join(mid(self.center(value), y, str(value), 9.5, fill=MUTED)
                       for index, value in enumerate(values) if index % stride == 0)


def stack(values: list[int], budget: float) -> tuple[float, float, int]:
    """Pitch, radius and tallest stack for one dot per value inside `budget` pixels.

    The stack is bounded in pixels rather than in dots: a fixed pitch is what turned one
    crowded axis into a 21506 px image, which is the defect this figure set exists to fix.
    """
    tallest = max(Counter(values).values(), default=0)
    pitch = max(2.0, min(11.0, budget / max(1, tallest)))
    return pitch, max(1.0, min(4.0, pitch / 2 - 0.5)), tallest


def positions(values: list[int]) -> list[tuple[int, int]]:
    """(value, stack index) per dot. Within a bin the order is the caller's, which is
    name order everywhere: stacking by any count would rank people inside the bin."""
    seen: dict[int, int] = {}
    out = []
    for value in values:
        seen[value] = seen.get(value, 0) + 1
        out.append((value, seen[value] - 1))
    return out


def hatch(pattern_id: str) -> str:
    """Diagonal hatch built from `<line>`, not `<path>`, so a figure carrying it still
    contains no path element at all (acceptance item 14)."""
    return tag("defs", {}, tag("pattern", {
        "id": pattern_id, "width": 6, "height": 6, "patternUnits": "userSpaceOnUse",
        "patternTransform": "rotate(45)",
    }, line(0.0, 0.0, 0.0, 6.0, stroke=HAIRLINE, **{"stroke-width": 1.4})))


def plate(x: float, y: float, content: str) -> str:
    """The visible replacement for a suppressed aggregate, drawn where the aggregate
    would have been: an absence there reads as zero or as a broken chart. It carries the
    n and the floor, so nobody has to look outside the figure to learn why."""
    return rect(x, y, len(content) * 5.5 + 16, 18.0, fill=PLATE_FILL, stroke=MUTED,
                **{"stroke-width": 1, "stroke-dasharray": "3 2", "class": "suppression-plate"}) \
        + text(x + 8, y + 12.5, content, 10.0)


def aggregate(bands: Bands, y: float, median: float | None, iqr: Any, label: str,
              plate_at: tuple[float, float, str], cls: str = "") -> str:
    """Median tick, optional IQR bracket, or the plate that replaces both.

    One implementation for C-SPAN and C-TEAM so a floor cannot fire two ways. `iqr=None`
    draws a tick and nothing else: `team_size.subset` carries no `iqr` key and inventing
    one would be a statistic this figure set is not entitled to add.
    """
    if median is None:
        return plate(*plate_at)
    parts = []
    if iqr and iqr[0] is not None and iqr[1] is not None:
        low, high = bands.center(iqr[0]), bands.center(iqr[1])
        parts.append(line(low, y, high, y, **{"stroke-width": 1.2, "class": "iqr-bracket"}))
        parts += [line(edge, y - 4, edge, y + 4, **{"stroke-width": 1.2}) for edge in (low, high)]
    tick = bands.center(median)
    parts.append(line(tick, y - 6, tick, y + 6,
                      **{"stroke-width": 2, "class": ("median-tick " + cls).strip()}))
    # Anchored past the bracket's right end, not past the tick: the median sits inside
    # the IQR, so anchoring on the tick strikes the label through the bracket line.
    anchor = max(tick, bands.center(iqr[1])) if iqr and iqr[1] is not None else tick
    parts.append(text(anchor + 10, y + 4, label, 9.5))
    return "".join(parts)


def arrow(x: float, y: float, length: float, dash: str = "") -> str:
    """A censoring tail. A right-censored observation has a lower bound, not "no value",
    so it keeps a position and gains a direction rather than being dropped."""
    tip = x + length
    return (line(x, y, tip, y, **{"stroke-width": 1, "stroke-dasharray": dash})
            + line(tip - 4, y - 3, tip, y, **{"stroke-width": 1}))


def glyph(kind: str, x: float, y: float) -> str:
    """One legend key. Shape and pattern carry every distinction; colour carries none."""
    if kind == "filled":
        return rect(x - 4.5, y - 4.5, 9.0, 9.0, fill=INK)
    if kind == "hollow":
        return rect(x - 4.5, y - 4.5, 9.0, 9.0, fill=WHITE, stroke=INK, **{"stroke-width": 1.4})
    if kind == "dot":
        return circle(x, y, 4.0, fill=INK)
    if kind == "dot-open":
        return circle(x, y, 4.0, fill=WHITE, stroke=INK, **{"stroke-width": 1.4})
    if kind == "tail":
        return (circle(x - 4, y, 4.0, fill=WHITE, stroke=INK, **{"stroke-width": 1.4})
                + arrow(x, y, 9.0))
    if kind == "dashed-arrow":
        # The gantt censors with a dashed tail and no dot, so its legend key must not
        # show a circle: a legend that draws a mark the figure never draws is a lie.
        return line(x - 8, y, x + 3, y, **{"stroke-width": 1, "stroke-dasharray": "3 2"})             + line(x - 1, y - 3, x + 3, y, **{"stroke-width": 1})
    if kind == "hairline":
        return line(x - 7, y, x + 7, y, stroke=HAIRLINE, **{"stroke-width": 1})
    if kind == "hatch":
        return rect(x - 7, y - 5, 14.0, 10.0, fill="url(#hatch-year)", stroke=HAIRLINE)
    return rect(x - 7, y - 5, 14.0, 10.0, fill=BAND)


def legend(x: float, y: float, entries: list[tuple[str, str]], size: float = 9.0) -> str:
    """Legend keys placed left to right at estimated text widths — deterministic, and
    never dependent on the browser font metrics a client-side layout would need."""
    parts, cursor = [], x
    for kind, label in entries:
        parts += [glyph(kind, cursor + 6, y - 3), text(cursor + 16, y, label, size, fill=MUTED)]
        cursor += 16 + len(label) * size * 0.52 + 18
    return "".join(parts)


def lane_label(x: float, y: float, content: str, gutter_chars: int = 36) -> str:
    """A lane or strip label in the left gutter, wrapped rather than allowed to run.

    Every lane label carries its own `k of N`, so it is long by design; letting it
    overflow puts the denominator on top of the first dot, which is the one place a
    reader must be able to count.
    """
    lines = wrap(content, gutter_chars, 2)
    if len(lines) == 1:
        return text(x, y, lines[0], 10.0)
    return text(x, y - 6, lines[0], 9.5) + text(x, y + 5, lines[1], 9.5)


def preamble(title: str, sub_lines: list[str]) -> list[str]:
    """Figure title plus the subtitle lines that carry the denominators. Every figure
    states its own population, because a chart gets screenshotted away from its caption."""
    return [text(14, 15, title, 14.0, **{"font-weight": "600"})] + [
        text(14, 29 + index * 11, content, 10.0, fill=MUTED)
        for index, content in enumerate(sub_lines)
    ]


def document(chart_id: str, width: float, height: float, title: str, desc: str, body: str) -> str:
    """`role="img"` plus `aria-labelledby` pointing at an in-SVG `<title>` and `<desc>`.

    The `<desc>` repeats the denominators and the suppression state in words, so a
    screen-reader user gets the same signal a sighted reader gets from the plate.
    """
    slug = chart_id.lower()
    head = (f'<title id="{slug}-title">{escape(title)}</title>'
            f'<desc id="{slug}-desc">{escape(desc)}</desc>')
    return tag("svg", {
        "xmlns": "http://www.w3.org/2000/svg", "width": num(width), "height": num(height),
        "viewBox": f"0 0 {num(width)} {num(height)}", "role": "img",
        "aria-labelledby": f"{slug}-title {slug}-desc", "class": f"figure {slug}",
    }, head + body)
