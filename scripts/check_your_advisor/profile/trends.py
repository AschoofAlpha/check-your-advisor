"""
One fitted slope per annual series, and the interval it may not be printed
without.

Every other register in this package still records a fitted trend as refused.
`SCORING_EXCLUSIONS["refused_by_design"]` says "a handful of right-censored
integer points do not support a slope"; the same sentence is in
`RANKING_EXCLUSIONS` and in `caveats.DROPPED_REGISTER`. This module does not
claim that objection was wrong. It is true, and it is the reason for nearly every
line below. Reconciling those registers with this module is the report's job and
not this module's — a module that quietly contradicted them would be the worse of
the two outcomes, so the contradiction is stated here instead.

What changed is the answer, not the fact. The old answer was to print nothing,
which leaves a reader to eyeball five bars and draw the line themselves, without
an interval, without a denominator, and with no record of having done it. The new
answer is `scoring`'s: refuse to hide the thing that makes the number weak. So
every result here carries the number of points it was fitted over, the width of
the interval around the slope, whether that interval spans zero, and a sentence
saying all three that a report can print verbatim.

The floor, and why it is where it is
------------------------------------

`MIN_TREND_POINTS` is 4. The argument is about degrees of freedom, because that
is what decides whether an interval exists at all:

- n <= 1: there is no line.
- n = 2: exactly one line passes through two points. The residual sum of squares
  is identically zero and df = 0, so the interval is 0/0 — undefined, not merely
  wide. A slope with no interval beside it is the single most misleading number
  this module could emit, and two points are the only case that produces one
  while still looking like a fit.
- n = 3: df = 1. An interval exists, but the residual scatter is estimated from
  one degree of freedom and the multiplier is 12.706, so the interval routinely
  runs to ten times the slope. Honest, and a trap: the slope still prints, and it
  is the part a reader keeps.
- n = 4: df = 2. The first n at which the scatter is estimated from more than a
  single degree of freedom, and the multiplier drops to 4.303.

Four is also what the default five-year window actually leaves. That window opens
mid-year, so its first bin is partial (CAV-17); dropping it leaves four or five
calendar bins and a fit is possible. A caller who also drops the two
indexing-lag bins, as `scoring._records_per_year` does, is left with three and
this module refuses — deliberately. A slope over the three middle years of a
five-year window is precisely what CAV-17 exists to prevent.

The floor is a module constant and nothing compares against a literal, so a
caller who disagrees can move it and see the behaviour move with it. It can only
be moved down as far as 3: `MIN_DEGREES_OF_FREEDOM` is arithmetic rather than
policy, and below three points there is no interval to print at any setting.

What is not here
----------------

No forecast and no extrapolation: a slope over four points carried forward to
next year is the same mistake with a date on it. No year-over-year percentage
change — 3 papers to 5 is not "+67%" and never was. Nothing that orders people or
corpora; that boundary did not move.

Pure, like `metrics` and `scoring`: a sequence in, one dict out, no file access,
no network, standard library only. The least squares and the t table are written
out by hand because this package installs with nothing.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "CONFIDENCE_LEVEL",
    "MIN_DEGREES_OF_FREEDOM",
    "MIN_TREND_POINTS",
    "TREND_METHOD",
    "T_CRITICAL_95",
    "T_CRITICAL_95_LARGE_DF",
    "fit_trend",
]


# ------------------------------------------------------------------
# Declared thresholds
# ------------------------------------------------------------------
#
# Same rule as `scoring` and `ranking`: every number this module compares against
# is a named constant, exported, so a report can print it and a reader can
# disagree with it. There is no second set hidden in a function body.

# Fewer points than this and no slope is produced at all. The argument is in the
# module docstring; the short form is that df = n - 2 must be at least 2 for the
# residual scatter to rest on more than one degree of freedom.
MIN_TREND_POINTS = 4

# The floor under the floor, and a different kind of number from the one above.
# MIN_TREND_POINTS is a judgement about what sample size is worth fitting and a
# caller may disagree with it; this one is arithmetic. A residual variance needs
# at least one degree of freedom to exist, so at n = 2 there is no interval to
# compute rather than a wide one. `_floor` below is therefore the larger of the
# two, which means lowering MIN_TREND_POINTS can move the floor down to 3 and no
# further — and at 2 the module refuses in its own words instead of dividing by
# zero.
MIN_DEGREES_OF_FREEDOM = 1

# Two-sided. Not configurable: a caller who could set it to 0.50 would get a
# narrow interval that means nothing, printed in the same sentence shape as one
# that means something.
CONFIDENCE_LEVEL = 0.95

# Student's t, two-sided 95%, by degrees of freedom. A table rather than a call
# into a library, because this package installs with nothing. It matters most at
# the small end: at df = 2 the multiplier is 4.303 against the normal's 1.960, so
# using the normal quantile here would understate the interval by more than half
# at exactly the sample sizes this module is for.
T_CRITICAL_95: dict[int, float] = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
    6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
    11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
    16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
    21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
    26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042,
}

# Past df = 30 the table has converged to the normal quantile to within 0.05, and
# no annual series this package builds will ever get there — 31 degrees of
# freedom is 33 years of publications.
T_CRITICAL_95_LARGE_DF = 1.960

TREND_METHOD = (
    "Ordinary least squares of the count on the calendar year, with a two-sided 95% interval "
    "on the slope from Student's t at n-2 degrees of freedom. The interval covers the sampling "
    "uncertainty of the line given these points and nothing else: it does not cover the "
    "undercounting at the recent edge of a PubMed window, and it is not a claim that the points "
    "were produced by a straight line in the first place."
)


def _floor() -> int:
    """
    The number of points a fit actually needs, read fresh on every call.

    The declared policy floor, or the arithmetic one if that is higher. Computed
    rather than stored so that moving `MIN_TREND_POINTS` moves the behaviour —
    a threshold captured at import time is a threshold a reader cannot inspect
    by changing it.
    """
    return max(MIN_TREND_POINTS, MIN_DEGREES_OF_FREEDOM + 2)


# ------------------------------------------------------------------
# Reading the series
# ------------------------------------------------------------------


def _points(series: Any, count_key: str) -> list[tuple[int, float, Mapping[str, Any] | None]]:
    """
    (year, count, source row) triples, sorted by year, validated loudly.

    Three input shapes, because three are already in use: a {year: count}
    mapping, a sequence of (year, count) pairs, and the row shape
    `metrics.records_per_year` emits, which carries the censoring flags this
    module reports but does not act on.
    """
    if isinstance(series, Mapping):
        raw: list[tuple[Any, Any, Mapping[str, Any] | None]] = [
            (year, count, None) for year, count in series.items()
        ]
    elif isinstance(series, (str, bytes)) or not isinstance(series, Sequence):
        raise TypeError(
            "fit_trend takes a {year: count} mapping, a sequence of (year, count) pairs, or the "
            f"row shape metrics.records_per_year emits, not a {type(series).__name__}"
        )
    else:
        raw = []
        for entry in series:
            if isinstance(entry, Mapping):
                if "year" not in entry or count_key not in entry:
                    raise ValueError(
                        f"a row must carry both 'year' and {count_key!r}; got keys "
                        f"{sorted(str(key) for key in entry)}"
                    )
                raw.append((entry["year"], entry[count_key], entry))
            elif (isinstance(entry, Sequence) and not isinstance(entry, (str, bytes))
                    and len(entry) == 2):
                raw.append((entry[0], entry[1], None))
            else:
                raise TypeError(
                    f"each entry must be a (year, count) pair or a row mapping, got "
                    f"{type(entry).__name__}"
                )

    points: list[tuple[int, float, Mapping[str, Any] | None]] = []
    seen: set[int] = set()
    for year, count, row in raw:
        if isinstance(year, bool) or not isinstance(year, int):
            raise ValueError(f"a year must be an integer calendar year, got {year!r}")
        if year in seen:
            # Two counts for one year is an ambiguity only the caller can settle.
            # Keeping one of them silently would change n without saying so, and
            # n is the number this whole module is built to print.
            raise ValueError(
                f"year {year} appears more than once; fit_trend will not choose between two "
                "counts for one year — sum or drop them in the caller, where the choice is visible"
            )
        seen.add(year)
        try:
            value = float(count)
        except (TypeError, ValueError):
            raise ValueError(f"the count for {year} must be a number, got {count!r}") from None
        if not math.isfinite(value):
            raise ValueError(f"the count for {year} must be finite, got {count!r}")
        points.append((year, value, row))

    points.sort(key=lambda item: item[0])
    return points


def _censored(points: Sequence[tuple[int, float, Mapping[str, Any] | None]]) -> list[int]:
    """
    Years the caller's own rows flagged `partial` or `indexing_lag` (CAV-17).

    Reported, never dropped. Dropping them here would change n behind the
    caller's back; the flag exists so the sentence can say the fit includes bins
    that are known to be undercounted.
    """
    return [
        year for year, _count, row in points
        if row is not None and (row.get("partial") or row.get("indexing_lag"))
    ]


# ------------------------------------------------------------------
# The one shape a result comes back in, fitted or not
# ------------------------------------------------------------------


def _result(**fields: Any) -> dict[str, Any]:
    """
    Built in one place so a refusal cannot quietly carry fewer keys than a fit.

    The same convention `ranking._rating` uses, and for the same reason: a
    renderer must not have to tell "this run produced no interval" apart from
    "this key was never in the payload".
    """
    base: dict[str, Any] = {
        "schema_version": 1,
        "fitted": False,
        "n": 0,
        # R1, as everywhere in this package: the population the value rests on.
        # Here it is the annual points, which is not the number of papers — a
        # 30-paper corpus over five years is five points.
        "denominator": 0,
        "min_points": _floor(),
        "suppressed": True,
        "unavailable": None,
        "years": [],
        "counts": [],
        "first_year": None,
        "last_year": None,
        "year_gaps": [],
        "censored_years": [],
        "slope": None,
        "intercept": None,
        "centre_year": None,
        "centre_count": None,
        "slope_stderr": None,
        "slope_interval": None,
        "interval_width": None,
        "interval_excludes_zero": None,
        "residual_sd": None,
        "exact_fit": False,
        "degrees_of_freedom": None,
        "t_multiplier": None,
        "confidence": CONFIDENCE_LEVEL,
        "method": TREND_METHOD,
        "unit": "records",
        "basis": "",
    }
    base.update(fields)
    return base


# ------------------------------------------------------------------
# The sentence a report prints
# ------------------------------------------------------------------


def _refusal_basis(n: int, unit: str) -> tuple[str, str]:
    """(reason, printable sentence) for a series below the floor. Both, always."""
    if n == 0:
        why = "the series is empty, so there is nothing to fit"
    elif n == 1:
        why = "a single point has no direction"
    elif n == 2:
        why = (
            "exactly one line passes through two points, leaving no residual to estimate an "
            "interval from, so the slope would arrive with no uncertainty attached to it"
        )
    else:
        why = (
            f"the residual scatter would rest on {n - 2} degree(s) of freedom and the interval "
            "would run to several times the slope"
        )
    reason = (
        f"{n} annual point(s) of {unit}, below the floor of {_floor()}: {why}"
    )
    sentence = (
        f"Not fitted: {reason}. The points themselves are in this result; the slope is not, "
        "because there is no honest interval to print beside it."
    )
    return reason, sentence


def _fit_basis(
    n: int,
    first: int,
    last: int,
    gaps: Sequence[int],
    censored: Sequence[int],
    slope: float,
    lower: float,
    upper: float,
    width: float,
    excludes_zero: bool,
    exact: bool,
    unit: str,
) -> str:
    """
    The self-describing sentence: how many points, over what span, how wide.

    Written to survive being quoted on its own, because that is what happens to a
    sentence beside a number. Everything a reader needs to discount the slope is
    in it — n, the span, the missing years, the interval, its width, whether it
    spans zero — so the slope cannot travel without them.
    """
    span = (
        f"{first}-{last}, missing {', '.join(str(year) for year in gaps)}"
        if gaps else f"{first}-{last}, no missing years"
    )
    ratio = f", {width / abs(slope):.1f}x the slope itself" if slope else ""
    parts = [
        f"Least-squares fit over {n} annual points ({span}): slope {slope:+.2f} {unit} per year, "
        f"{CONFIDENCE_LEVEL:.0%} interval {lower:+.2f} to {upper:+.2f} (width {width:.2f}{ratio})."
    ]
    if exact:
        parts.append(
            "The points sit exactly on the line, so the interval has zero width. That is a "
            "property of this many integer counts falling in a row, not evidence that the slope "
            "is known precisely."
        )
    elif excludes_zero:
        parts.append(
            "The interval excludes zero, which is the most this many points can say and is not a "
            "statement about any cause."
        )
    else:
        parts.append(
            "The interval spans zero, so these points are as consistent with no trend at all as "
            "with the slope printed above."
        )
    if censored:
        parts.append(
            f"Year(s) {', '.join(str(year) for year in censored)} were flagged partial or "
            "indexing-lag by the caller and were fitted anyway, at the caller's choice: those "
            "bins are undercounted, so a slope that leans on them leans on when the data was "
            "pulled."
        )
    parts.append(
        f"{_floor()} points is the floor this module will fit at, and n={n} is not a "
        "sample size at which a slope means much; the interval is the part of this line worth "
        "reading."
    )
    return " ".join(parts)


# ------------------------------------------------------------------
# The fit
# ------------------------------------------------------------------


def fit_trend(
    series: Any,
    *,
    unit: str = "records",
    count_key: str = "count",
) -> dict[str, Any]:
    """
    Least-squares slope of an annual count series, with its interval, or a
    refusal that says why.

    `series` is a {year: count} mapping, a sequence of (year, count) pairs, or a
    sequence of row mappings carrying `year` and `count_key` — the shape
    `metrics.records_per_year` emits, whose `partial` and `indexing_lag` flags
    are reported here and acted on nowhere. `unit` names what is being counted
    and appears in the printed sentence. Years out of order are sorted; years
    missing from the span are listed, not filled in, and the fit runs on the
    calendar year rather than on the position in the list, so a gap widens the
    run rather than disappearing into it.

    Returns:
      fitted        the verdict this module exists to make. False below
                    MIN_TREND_POINTS, and then `slope` and `slope_interval` are
                    both None — never a number computed from two points.
      n             annual points, which is not the paper count.
      denominator   equal to `n`, under the R1 convention the rest of this
                    package follows.
      slope         counts per year, or None.
      intercept     the fitted line's value at year 0, so that
                    `slope * year + intercept` reproduces the line by hand. On
                    its own it is a two-thousand-year extrapolation and means
                    nothing; `centre_year` and `centre_count` are the one point
                    on the line that is not extrapolated.
      slope_interval  (lower, upper) at CONFIDENCE_LEVEL, with `interval_width`
                    beside it and `interval_excludes_zero` saying whether the
                    points can distinguish the slope from no trend at all.
      slope_stderr, residual_sd, degrees_of_freedom, t_multiplier
                    every input to that interval, so it can be recomputed by
                    hand without rerunning anything.
      exact_fit     True when the residual sum of squares is exactly zero, which
                    makes the interval zero-width. Flagged rather than smoothed
                    over: on four integer counts it is a coincidence about the
                    points, not precision.
      suppressed    the metrics-layer convention, True whenever `fitted` is
                    False: the aggregate goes, the parts remain.
      basis         one paragraph, printable verbatim, naming n, the span, the
                    interval and its width. A slope quoted without it is the
                    failure this module was written to avoid.

    Raises ValueError on a repeated year, a non-integer year, or a count that is
    not a finite number; TypeError on anything that is not one of the three
    accepted shapes. A repeated year is not resolved here on purpose — summing
    or dropping is a decision that changes n, and n is what this result is for.
    """
    points = _points(series, count_key)
    n = len(points)
    years = [year for year, _count, _row in points]
    counts = [count for _year, count, _row in points]
    censored = _censored(points)
    gaps = (
        [year for year in range(years[0], years[-1] + 1) if year not in set(years)]
        if n else []
    )
    common: dict[str, Any] = {
        "n": n,
        "denominator": n,
        "years": years,
        "counts": counts,
        "first_year": years[0] if n else None,
        "last_year": years[-1] if n else None,
        "year_gaps": gaps,
        "censored_years": censored,
        "unit": unit,
    }

    if n < _floor():
        reason, sentence = _refusal_basis(n, unit)
        return _result(**common, unavailable=reason, basis=sentence)

    centre_year = sum(years) / n
    centre_count = sum(counts) / n
    x_dev = [year - centre_year for year in years]
    y_dev = [count - centre_count for count in counts]
    sxx = sum(dx * dx for dx in x_dev)
    sxy = sum(dx * dy for dx, dy in zip(x_dev, y_dev))

    slope = sxy / sxx
    intercept = centre_count - slope * centre_year
    # Residuals in centred form: exact for integer counts on a perfect line, so
    # `exact_fit` below needs no tolerance and therefore declares no hidden one.
    sse = sum((dy - slope * dx) ** 2 for dx, dy in zip(x_dev, y_dev))
    df = n - 2
    residual_var = sse / df
    stderr = math.sqrt(residual_var / sxx)
    multiplier = T_CRITICAL_95.get(df, T_CRITICAL_95_LARGE_DF)
    half = multiplier * stderr
    lower, upper = slope - half, slope + half
    exact = sse == 0.0
    excludes_zero = lower > 0.0 or upper < 0.0

    return _result(
        **common,
        fitted=True,
        suppressed=False,
        slope=slope,
        intercept=intercept,
        centre_year=centre_year,
        centre_count=centre_count,
        slope_stderr=stderr,
        slope_interval=(lower, upper),
        interval_width=upper - lower,
        interval_excludes_zero=excludes_zero,
        residual_sd=math.sqrt(residual_var),
        exact_fit=exact,
        degrees_of_freedom=df,
        t_multiplier=multiplier,
        basis=_fit_basis(n, years[0], years[-1], gaps, censored, slope, lower, upper,
                         upper - lower, excludes_zero, exact, unit),
    )
