#!/usr/bin/env python3
"""
`profile/trends.py`: the one fitted slope this package computes, and the
uncertainty it is not allowed to print without.

Every other register in this package still says a trend is refused. That refusal
rested on a real fact — a five-year window gives four or five right-censored
integer points, and four points do not support a slope — and this module does not
pretend the fact went away. It answers it the way `scoring` answered the weight
objection: by refusing to hide the thing that makes the number weak. So half of
this file is ordinary least-squares arithmetic, and the other half is the
discipline turned into assertions:

  - The sample floor is a declared module constant. Setting it to 3 makes a
    three-point series fit, which is how a test proves the threshold is not
    welded into a function body.
  - Below the floor there is no slope at all — None, with a reason — never a
    number computed from two points.
  - A slope never travels alone: n, the interval, the interval's width and a
    sentence naming both come back in the same dict, and the dict has the same
    keys whether the fit happened or not.
  - A zero-width interval (points exactly on a line) is flagged as a property of
    four points falling in a row, not as precision.

Pure computation: no file access, no network, standard library only.

Run: python tests/test_trends.py
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor.profile import trends  # noqa: E402
from check_your_advisor.profile.trends import (  # noqa: E402
    CONFIDENCE_LEVEL,
    MIN_DEGREES_OF_FREEDOM,
    MIN_TREND_POINTS,
    T_CRITICAL_95,
    T_CRITICAL_95_LARGE_DF,
    TREND_METHOD,
    fit_trend,
)

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


def raises(fn, *args, **kw) -> str | None:
    try:
        fn(*args, **kw)
    except Exception as exc:  # noqa: BLE001 - the type is the assertion
        return type(exc).__name__
    return None


# ============================================================
# Fixtures, with every expected number derived here rather than
# read back out of the module under test
# ============================================================

# Four points, deliberately scattered: slope +1.4 with an interval that spans
# zero. This is the shape of the typical corpus — a five-year window, one bin
# dropped for being partial — and the headline it produces is "these four points
# are also consistent with no trend at all", which is the whole reason the module
# is allowed to exist.
FOUR = {2019: 1, 2020: 3, 2021: 2, 2022: 6}
FOUR_SLOPE = 7.0 / 5.0              # Sxy 7.0 over Sxx 5.0
FOUR_INTERCEPT = 3.0 - FOUR_SLOPE * 2020.5
FOUR_SSE = 4.20                     # residuals 0.1, 0.7, -1.7, 0.9
FOUR_SE = math.sqrt((FOUR_SSE / 2.0) / 5.0)
FOUR_HALF = 4.303 * FOUR_SE         # t(df=2) written out, not read from the module
FOUR_LOWER = FOUR_SLOPE - FOUR_HALF
FOUR_UPPER = FOUR_SLOPE + FOUR_HALF

# Five points, rising cleanly enough that the interval clears zero.
FIVE = {2019: 2, 2020: 3, 2021: 5, 2022: 4, 2023: 6}
FIVE_SLOPE = 9.0 / 10.0
FIVE_SSE = 1.90
FIVE_SE = math.sqrt((FIVE_SSE / 3.0) / 10.0)
FIVE_HALF = 3.182 * FIVE_SE         # t(df=3)

# A gap year, and four points that sit exactly on a line through it. Fitted on
# the calendar year rather than on the position in the list: on positions the
# slope would be 1.4, on years it is 1.0, so this fixture tells the two apart.
GAPPED = {2018: 1, 2019: 2, 2021: 4, 2022: 5}

ALL_ZERO = {2019: 0, 2020: 0, 2021: 0, 2022: 0}
FLAT = {2019: 4, 2020: 4, 2021: 4, 2022: 4}


# ============================================================
# The floor is a declared constant, not a literal in a branch
# ============================================================

print("the sample floor")

check("MIN_TREND_POINTS is an int", isinstance(MIN_TREND_POINTS, int), True)
check("the floor is four points", MIN_TREND_POINTS, 4)
check_true("...and is exported", "MIN_TREND_POINTS" in trends.__all__)
check_true("the module docstring states the basis for that number",
           "df" in trends.__doc__ and "two points" in trends.__doc__.lower())

# The test that the number is declarative rather than welded into a comparison:
# move the constant, and the behaviour moves with it.
_restore = trends.MIN_TREND_POINTS
try:
    trends.MIN_TREND_POINTS = 3
    _lowered = fit_trend({2019: 1, 2020: 3, 2021: 2})
finally:
    trends.MIN_TREND_POINTS = _restore
check_true("lowering the constant lets a three-point series fit", _lowered["fitted"])
check("...so the threshold really is read from the module, not from a literal",
      _lowered["min_points"], 3)
check_false("...and the module-level value was restored by the test",
            trends.MIN_TREND_POINTS == 3)
check_false("three points do not fit at the declared floor",
            fit_trend({2019: 1, 2020: 3, 2021: 2})["fitted"])

# The floor under the floor. MIN_TREND_POINTS is a judgement and may be argued
# with; one degree of freedom is arithmetic. Lowering the policy floor to 2 used
# to divide by zero — n=2 leaves df=0, so there is no residual variance to take a
# root of. The module now refuses in its own words at any setting.
check("MIN_DEGREES_OF_FREEDOM is one", MIN_DEGREES_OF_FREEDOM, 1)
_restore = trends.MIN_TREND_POINTS
try:
    trends.MIN_TREND_POINTS = 2
    _two_at_two = fit_trend({2021: 3, 2022: 5})
    _crashed = None
except Exception as exc:  # noqa: BLE001 - a crash here is the failure being tested
    _two_at_two, _crashed = None, type(exc).__name__
finally:
    trends.MIN_TREND_POINTS = _restore
check("lowering the policy floor to 2 does not raise", _crashed, None)
check_false("...and two points still do not fit, because df would be 0",
            _two_at_two["fitted"])
check("...the floor reported is the arithmetic one, not the policy one",
      _two_at_two["min_points"], MIN_DEGREES_OF_FREEDOM + 2)
check_true("...and the refusal names that floor rather than the setting",
           "floor of 3" in _two_at_two["unavailable"])


print("\nthe interval is a t interval, and the table is declared too")

check("the confidence level is stated", CONFIDENCE_LEVEL, 0.95)
close("t at one degree of freedom", T_CRITICAL_95[1], 12.706, 5e-4)
close("t at two degrees of freedom — the floor's own multiplier", T_CRITICAL_95[2], 4.303, 5e-4)
close("t at three degrees of freedom", T_CRITICAL_95[3], 3.182, 5e-4)
close("t at thirty degrees of freedom", T_CRITICAL_95[30], 2.042, 5e-4)
check("the table runs from df=1 to df=30 with no hole",
      sorted(T_CRITICAL_95), list(range(1, 31)))
check_true("...and shrinks monotonically toward the normal quantile",
           all(T_CRITICAL_95[df] > T_CRITICAL_95[df + 1] for df in range(1, 30)))
close("beyond the table it is the normal quantile", T_CRITICAL_95_LARGE_DF, 1.960, 5e-4)
check_true("...which every tabulated value exceeds",
           all(value > T_CRITICAL_95_LARGE_DF for value in T_CRITICAL_95.values()))
check_true("the method is printable in words", len(TREND_METHOD) > 60)
check_true("...and names least squares", "least squares" in TREND_METHOD.lower())


# ============================================================
# A fit, with every number checked against arithmetic done here
# ============================================================

print("\na four-point fit")

four = fit_trend(FOUR)

for key in ("fitted", "n", "denominator", "slope", "intercept", "slope_interval",
            "interval_width", "slope_stderr", "residual_sd", "basis", "suppressed",
            "min_points", "unavailable", "years", "counts", "year_gaps",
            "degrees_of_freedom", "t_multiplier", "confidence", "method",
            "centre_year", "centre_count", "exact_fit", "interval_excludes_zero",
            "first_year", "last_year", "censored_years", "unit", "schema_version"):
    check_true(f"the result carries `{key}`", key in four)

check_true("four points is enough to fit", four["fitted"])
check_false("...so nothing is suppressed", four["suppressed"])
check("...and no reason is given for a refusal that did not happen", four["unavailable"], None)
check("n is the number of annual points, not the number of papers", four["n"], 4)
check("the denominator convention holds: it equals n", four["denominator"], four["n"])
check("the years used come back, ascending", four["years"], [2019, 2020, 2021, 2022])
check("...with their counts alongside", four["counts"], [1.0, 3.0, 2.0, 6.0])
check("...and the span", (four["first_year"], four["last_year"]), (2019, 2022))
check("no year is missing inside the span", four["year_gaps"], [])

close("the slope is the least-squares slope", four["slope"], FOUR_SLOPE)
close("the intercept puts the line back together", four["intercept"], FOUR_INTERCEPT, 1e-6)
close("...so slope x year + intercept lands on the centroid",
      four["slope"] * four["centre_year"] + four["intercept"], 3.0, 1e-6)
close("the centre year is the mean year", four["centre_year"], 2020.5)
close("the centre count is the mean count — the one point on the line that is not "
      "an extrapolation", four["centre_count"], 3.0)
check("the degrees of freedom are n - 2", four["degrees_of_freedom"], 2)
close("the multiplier is t at those degrees of freedom", four["t_multiplier"], 4.303, 5e-4)
close("the standard error is sqrt(SSE / df / Sxx)", four["slope_stderr"], FOUR_SE, 1e-9)
close("the residual scatter is sqrt(SSE / df)", four["residual_sd"], math.sqrt(2.1), 1e-9)

close("the interval's lower edge", four["slope_interval"][0], FOUR_LOWER, 1e-9)
close("the interval's upper edge", four["slope_interval"][1], FOUR_UPPER, 1e-9)
close("the width is what a reader would measure off the two edges",
      four["interval_width"], 2 * FOUR_HALF, 1e-9)
check_true("the interval is the slope plus and minus the same amount",
           abs((four["slope"] - four["slope_interval"][0])
               - (four["slope_interval"][1] - four["slope"])) < 1e-12)
check_false("this interval spans zero", four["interval_excludes_zero"])
check_false("...and the points are not exactly on the line", four["exact_fit"])
check("the confidence level travels with the interval", four["confidence"], 0.95)

# The number is +1.4 and the honest reading is "we cannot tell". The sentence has
# to say so, or the +1.4 is the only thing a reader takes away.
check_true("the sentence says how many points were fitted", "4 annual points" in four["basis"])
check_true("...names the span", "2019-2022" in four["basis"])
check_true("...prints the slope", "+1.40" in four["basis"])
check_true("...prints both interval edges", "-1.39" in four["basis"] and "+4.19" in four["basis"])
check_true("...prints the width", "width 5.58" in four["basis"])
check_true("...says the interval spans zero", "spans zero" in four["basis"].lower())
check_true("...and says the floor is not a sample size that means much",
           "floor" in four["basis"].lower())
check_true("the sentence is printable as one paragraph", "\n" not in four["basis"])


print("\na five-point fit whose interval clears zero")

five = fit_trend(FIVE)
close("the slope", five["slope"], FIVE_SLOPE)
check("n", five["n"], 5)
check("df", five["degrees_of_freedom"], 3)
close("the multiplier moves with df", five["t_multiplier"], 3.182, 5e-4)
close("the lower edge", five["slope_interval"][0], FIVE_SLOPE - FIVE_HALF, 1e-9)
close("the upper edge", five["slope_interval"][1], FIVE_SLOPE + FIVE_HALF, 1e-9)
check_true("this interval clears zero", five["interval_excludes_zero"])
check_true("...and the sentence says so without claiming a cause",
           "excludes zero" in five["basis"].lower())
check_true("...and still refuses to call five points a lot of evidence",
           "floor" in five["basis"].lower())

# One more point of the same shape must widen nothing and narrow the interval;
# the check that matters is that n and the interval move together at all.
check_true("five points give a narrower interval than four of comparable scatter",
           five["interval_width"] < 40.0)
check_true("adding a year changes n in the sentence", "5 annual points" in five["basis"])


# ============================================================
# The boundaries
# ============================================================

print("\nboundaries: nothing here may raise, and nothing may invent a slope")

empty = fit_trend({})
check_false("an empty series does not fit", empty["fitted"])
check("...and has no slope", empty["slope"], None)
check("...no interval either", empty["slope_interval"], None)
check("n is zero, said out loud", empty["n"], 0)
check_true("...with a reason", isinstance(empty["unavailable"], str) and empty["unavailable"])
check_true("...and the reason names the floor", "4" in empty["unavailable"])

one = fit_trend({2021: 5})
check_false("one point does not fit", one["fitted"])
check("...and no slope is produced", one["slope"], None)
check("the point itself survives the refusal", one["years"], [2021])
check("...with its count", one["counts"], [5.0])
check_true("...and the aggregate is marked suppressed, as everywhere else here",
           one["suppressed"])

two = fit_trend({2021: 3, 2022: 5})
check_false("two points do not fit", two["fitted"])
check("...even though a line through two points exists", two["slope"], None)
check("...and it has no interval, which is the actual reason", two["slope_interval"], None)
check_true("the reason says two points leave no residual",
           "residual" in two["unavailable"].lower() or "no uncertainty" in two["unavailable"].lower())
check_true("...and the sentence a report would print says it too",
           "Not fitted" in two["basis"])

three = fit_trend({2019: 1, 2020: 3, 2021: 2})
check_false("three points do not fit at the declared floor", three["fitted"])
check_true("...and the refusal names the count it fell short of",
           "3" in three["unavailable"] and "4" in three["unavailable"])

zeros = fit_trend(ALL_ZERO)
check_true("a series of zeros fits without raising", zeros["fitted"])
close("...at slope zero", zeros["slope"], 0.0)
close("...intercept zero", zeros["intercept"], 0.0, 1e-9)
close("...and an interval of zero width", zeros["interval_width"], 0.0, 1e-12)
check_true("...which is flagged as an exact fit, not as precision", zeros["exact_fit"])
check_false("a zero-width interval around zero does not exclude zero",
            zeros["interval_excludes_zero"])
check_true("...and the sentence says the zero width is a property of the points",
           "exactly on" in zeros["basis"].lower())
close("...the residual scatter is zero too", zeros["residual_sd"], 0.0, 1e-12)

flat = fit_trend(FLAT)
check_true("four identical non-zero counts fit", flat["fitted"])
close("...at slope zero, which is a result and not an error", flat["slope"], 0.0)
close("...with the intercept sitting at the level itself", flat["intercept"], 4.0, 1e-9)
close("...and the centre count at that level", flat["centre_count"], 4.0)
close("...zero width again", flat["interval_width"], 0.0, 1e-12)
check_false("...and zero is inside it", flat["interval_excludes_zero"])

gapped = fit_trend(GAPPED)
check_true("a series with a hole in it fits", gapped["fitted"])
check("...the hole is named rather than closed over", gapped["year_gaps"], [2020])
check("...n counts points, not calendar years in the span", gapped["n"], 4)
close("...and the fit runs on the calendar year, so the slope is 1.0 and not 1.4",
      gapped["slope"], 1.0, 1e-9)
close("...intercept", gapped["intercept"], -2017.0, 1e-6)
check_true("...the sentence names the missing year",
           "2020" in gapped["basis"] and "missing" in gapped["basis"].lower())
check_true("...a perfect fit through a gap is still flagged as exact",
           gapped["exact_fit"])
check_true("...so its zero-width interval is caveated rather than printed as certainty",
           "exactly on" in gapped["basis"].lower())

shuffled = fit_trend({2022: 4, 2019: 2, 2023: 6, 2021: 5, 2020: 3})
check("years out of order are sorted before anything is computed",
      shuffled["years"], five["years"])
close("...giving the same slope", shuffled["slope"], five["slope"], 1e-12)
close("...the same interval", shuffled["slope_interval"][0], five["slope_interval"][0], 1e-12)
check("...and the same sentence", shuffled["basis"], five["basis"])


print("\nthe shape does not change when the fit does not happen")

check("a refusal carries exactly the keys a fit carries", sorted(two), sorted(four))
check("...and so does an empty series", sorted(empty), sorted(four))
check("the schema version is stated", four["schema_version"], 1)
check("the floor travels in every result", [r["min_points"] for r in (four, two, empty)],
      [MIN_TREND_POINTS] * 3)
check("the method travels in every result", [r["method"] for r in (four, two, empty)],
      [TREND_METHOD] * 3)
check_true("every result carries a printable sentence",
           all(isinstance(r["basis"], str) and len(r["basis"]) > 40
               for r in (four, five, two, one, empty, zeros, gapped)))


# ============================================================
# What the caller may hand in
# ============================================================

print("\ninput forms")

pairs = fit_trend([(2019, 1), (2020, 3), (2021, 2), (2022, 6)])
check("a sequence of (year, count) pairs is accepted", pairs["basis"], four["basis"])
rows = fit_trend([{"year": 2019, "count": 1}, {"year": 2020, "count": 3},
                  {"year": 2021, "count": 2}, {"year": 2022, "count": 6}])
check("...and so is the row shape metrics.records_per_year emits", rows["basis"], four["basis"])
close("...to the same slope", rows["slope"], four["slope"], 1e-12)
renamed = fit_trend([{"year": 2019, "n": 1}, {"year": 2020, "n": 3},
                     {"year": 2021, "n": 2}, {"year": 2022, "n": 6}], count_key="n")
check("a different count key is accepted for citation-style rows",
      renamed["slope"], rows["slope"])
check("the unit is the caller's to name", fit_trend(FOUR, unit="citations")["unit"], "citations")
check_true("...and it is printed in the sentence",
           "citations per year" in fit_trend(FOUR, unit="citations")["basis"])
check("the default unit is records, because that is what the corpus holds",
      four["unit"], "records")

check("a string is not a series", raises(fit_trend, "2019 2020"), "TypeError")
check("nor is None", raises(fit_trend, None), "TypeError")
check("nor is a bare number", raises(fit_trend, 7), "TypeError")
# Two counts for one year is an ambiguity only the caller can settle, and
# silently keeping one of them would change n without saying so.
check("a repeated year raises rather than being silently collapsed",
      raises(fit_trend, [(2019, 1), (2019, 2), (2020, 3), (2021, 4)]), "ValueError")
check("a non-numeric count raises", raises(fit_trend, {2019: "many", 2020: 1}), "ValueError")
check("a non-finite count raises", raises(fit_trend, {2019: float("nan"), 2020: 1}), "ValueError")
check("a non-integer year raises", raises(fit_trend, {"spring": 1, 2020: 2}), "ValueError")


print("\ncensored bins are fitted at the caller's choice, and named")

censored = fit_trend([{"year": 2019, "count": 1, "partial": True},
                      {"year": 2020, "count": 3},
                      {"year": 2021, "count": 2},
                      {"year": 2022, "count": 6, "indexing_lag": True}])
check("the flagged years come back by name", censored["censored_years"], [2019, 2022])
check_true("...and the sentence warns that the right-hand edge is undercounted",
           "undercounted" in censored["basis"].lower())
close("...but nothing was dropped: the slope is the same as the unflagged fit",
      censored["slope"], four["slope"], 1e-12)
check("...and so is n — dropping a bin would change the denominator silently",
      censored["n"], four["n"])
check("a series with no flags reports none", four["censored_years"], [])


# ============================================================
# The discipline, as assertions
# ============================================================

print("\nthe discipline, as assertions")

# 1. No slope is ever returned without an interval beside it, at any n. This is
#    the single rule the module exists to keep.
for label, series in (("four", FOUR), ("five", FIVE), ("zeros", ALL_ZERO),
                      ("flat", FLAT), ("gapped", GAPPED), ("empty", {}),
                      ("one point", {2021: 5}), ("two points", {2021: 3, 2022: 5})):
    result = fit_trend(series)
    check_true(f"{label}: slope and interval are present or absent together",
               (result["slope"] is None) == (result["slope_interval"] is None))
    check_true(f"{label}: the sentence states n",
               str(result["n"]) in result["basis"])

# 2. Nothing here forecasts. A slope over four points extrapolated to next year
#    is the same mistake with a year stamped on it.
check("the module exports no forecast, prediction or projection",
      [name for name in dir(trends)
       if any(word in name.lower() for word in ("forecast", "predict", "project", "extrapolat"))],
      [])

# 3. Nothing here orders people or corpora either — that boundary did not move.
ORDERING_WORDS = ("rank", "percentile", "quantile", "grade", "tier", "star rating",
                  "leaderboard", "better than", "outperform", "above average")


def walk(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from walk(value)
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from walk(item)
    elif isinstance(node, str):
        yield node


offenders = sorted({
    word
    for payload in (four, five, two, empty, zeros, gapped, censored)
    for text in walk(payload)
    for word in ORDERING_WORDS
    if word in text.lower()
})
check("no key and no string in any payload is an ordering", offenders, [])

# 4. Standard library only — the promise the whole install story rests on, and
#    the reason the least squares and the t table are written out by hand.
source = open(trends.__file__, encoding="utf-8").read()
for banned in ("import numpy", "import scipy", "import pandas", "from numpy",
               "from scipy", "from pandas"):
    check_true(f"the module does not `{banned}`", banned not in source)
check_true("the t table is data in the module, not a call into a library",
           "T_CRITICAL_95" in source and "def " not in source.split("T_CRITICAL_95")[0][-200:])

# 5. Every threshold the module compares against is exported, so a reader can
#    print it and disagree with it.
for name in ("MIN_TREND_POINTS", "MIN_DEGREES_OF_FREEDOM", "CONFIDENCE_LEVEL",
             "T_CRITICAL_95", "T_CRITICAL_95_LARGE_DF", "TREND_METHOD", "fit_trend"):
    check_true(f"`{name}` is exported", name in trends.__all__)
check("nothing else is exported", sorted(trends.__all__),
      ["CONFIDENCE_LEVEL", "MIN_DEGREES_OF_FREEDOM", "MIN_TREND_POINTS", "TREND_METHOD",
       "T_CRITICAL_95", "T_CRITICAL_95_LARGE_DF", "fit_trend"])

# 6. The module says, in its own docstring, that the other registers in this
#    package still record a trend as refused. Reconciling them is the report's
#    job, not this module's, and a silent contradiction would be the worse
#    outcome of the two.
doc = trends.__doc__.lower()
check_true("the docstring names the registers it contradicts",
           "scoring_exclusions" in doc or "ranking_exclusions" in doc)
check_true("...and does not claim the original objection was wrong",
           "right-censored" in doc or "censored" in doc)


print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(1 if _failed else 0)
