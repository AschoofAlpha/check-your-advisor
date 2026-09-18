#!/usr/bin/env python3
"""
`profile/scoring.py`: the composite score, and the line it is not allowed to
cross.

The old rule refused a composite score outright, on two grounds welded together:
that no weighting is justified by this data, and that a score is a ranking of
people in disguise. Only the first survived, and this module answers it by
refusing to hide the weights rather than by claiming to have found better ones.
So half of this file is ordinary arithmetic — components, weights, contributions,
suppression — and the other half is the second objection turned into assertions:

  - `composite_score` takes one bundle. A list raises TypeError, so two
    researchers cannot be scored in one call.
  - Every normalisation anchor is a declared module constant, never a population
    of researchers, which is what makes a percentile uncomputable here rather
    than merely disallowed.
  - `components` comes back in registration order, never in contribution order.
  - Nothing in the returned payload — no key, no string — is a rank, a
    percentile, a grade, a tier or a star rating. That is checked by walking the
    whole structure, so a new field cannot quietly introduce one.

"Score, but never rank" is a rule, and a rule that is not executable is a
comment. `print("\\nthe rule, as assertions")` below is where it becomes a test.

Pure computation: no file access, no network, standard library only.

Run: python tests/test_scoring.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor.profile import scoring  # noqa: E402
from check_your_advisor.profile.scoring import (  # noqa: E402
    COMPONENT_NAMES,
    DEFAULT_WEIGHTS,
    SCORING_EXCLUSIONS,
    composite_score,
    resolve_weights,
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


def raises(fn, *args, **kw) -> str | None:
    try:
        fn(*args, **kw)
    except Exception as exc:  # noqa: BLE001 - the type is the assertion
        return type(exc).__name__
    return None


# ============================================================
# A bundle whose six components normalise to six different values
# ============================================================
#
# Distinct on purpose: with all six equal, every weight table would produce the
# same score and the weight assertions below would pass without meaning anything.

S3A = {"denominator": 20, "counts": {"A": 10, "B": 5, "D": 3, "unclassified": 2},
       "suppressed": False, "not_computable": False}                      # 10/20 = 0.50
S3B = {"denominator": 15, "lag_years": 2,
       "counts": {"holds_lead": 9, "observed_without_lead": 3, "too_recent": 3}}  # 9/12 = 0.75
S4 = {"denominator": 8, "median": 1.0, "count_at_zero": 2, "still_without_lead": ["x"],
      "suppressed": False, "not_computable": False}                       # 1 - 1/4 = 0.75
S9 = {"denominator": 30,
      "years": [{"year": 2020, "count": 99, "partial": True, "indexing_lag": False},
                {"year": 2021, "count": 2, "partial": False, "indexing_lag": False},
                {"year": 2022, "count": 2, "partial": False, "indexing_lag": False},
                {"year": 2023, "count": 2, "partial": False, "indexing_lag": False},
                {"year": 2025, "count": 99, "partial": False, "indexing_lag": True}]}  # 2/8 = 0.25
IMPACT = {"denominator": 10, "covered": 10, "suppressed": False, "not_computable": False,
          "h_index": 3, "i10_index": 2, "total_citations": 60, "median_citations": 5.0,
          "iqr": (2.0, 8.0), "lower_bound": False, "mixed_sources": False,
          "sources": {"openalex": 10}, "generated_at": "2026-01-01T09:30:00"}
#                                        h 3/15 = 0.20, median 5/20 = 0.25

BUNDLE = {"s3a": S3A, "s3b": S3B, "s4": S4, "s9": S9, "impact": IMPACT}

NORMALISED = {
    "lead_slot_share": 0.50,
    "people_with_lead_slot": 0.75,
    "time_to_lead": 0.75,
    "records_per_year": 0.25,
    "citation_h_index": 0.20,
    "citation_median": 0.25,
}


def expected_score(weights: dict[str, float]) -> float:
    """The score, recomputed here the way the report tells a reader to."""
    total = sum(weights[name] for name in NORMALISED)
    return round(100.0 * sum(weights[name] * NORMALISED[name] for name in NORMALISED) / total, 1)


def by_name(result) -> dict[str, dict]:
    return {item["name"]: item for item in result["components"]}


# ============================================================
# The weight table
# ============================================================

print("the weight table")

check("one weight per registered component", sorted(DEFAULT_WEIGHTS), sorted(COMPONENT_NAMES))
# Flat is the only default that asserts nothing. A reader who leaves it alone has
# chosen "count everything equally", which is a position they can defend.
check("the default table is flat", set(DEFAULT_WEIGHTS.values()), {1.0})
check("six components are registered", len(COMPONENT_NAMES), 6)
check("...and the citation pair is two of them",
      [name for name in COMPONENT_NAMES if name.startswith("citation")],
      ["citation_h_index", "citation_median"])

check("no config means the defaults", resolve_weights(None), DEFAULT_WEIGHTS)
check("an empty config means the defaults", resolve_weights({}), DEFAULT_WEIGHTS)
check("a top-level score_weights override is read",
      resolve_weights({"score_weights": {"time_to_lead": 2.0}})["time_to_lead"], 2.0)
check("an advisor.score_weights override is read",
      resolve_weights({"advisor": {"score_weights": {"time_to_lead": 3.0}}})["time_to_lead"], 3.0)
check("an override leaves the untouched components at their defaults",
      resolve_weights({"score_weights": {"time_to_lead": 2.0}})["records_per_year"], 1.0)

# A typo that silently left the default table in place would let a reader believe
# they had changed the score when they had not.
check("an unknown component name raises rather than being ignored",
      raises(composite_score, BUNDLE, {"h_indx": 2.0}), "ValueError")
try:
    composite_score(BUNDLE, {"h_indx": 2.0})
    typo_message = ""
except ValueError as exc:
    typo_message = str(exc)
check_true("...and the message lists the names that would have worked",
           all(name in typo_message for name in COMPONENT_NAMES))
# A negative weight inverts a component's direction without saying so, and can
# drive the total weight to zero and the score outside [0, 100].
check("a negative weight raises", raises(composite_score, BUNDLE, {"time_to_lead": -1.0}),
      "ValueError")
check("a non-numeric weight raises", raises(composite_score, BUNDLE, {"time_to_lead": "heavy"}),
      "ValueError")
check("an infinite weight raises", raises(composite_score, BUNDLE, {"time_to_lead": float("inf")}),
      "ValueError")
check("a weight table that is not a mapping raises",
      raises(composite_score, BUNDLE, [1.0, 1.0]), "TypeError")
check("zero is allowed, because that is how a component is dropped",
      raises(composite_score, BUNDLE, {"citation_median": 0.0}), None)


# ============================================================
# The score
# ============================================================

print("\nthe score")

result = composite_score(BUNDLE)

for key in ("score", "components", "weights_used", "suppressed", "unavailable"):
    check_true(f"the result carries `{key}`", key in result)

check("the score is the flat-weighted mean of the six components",
      result["score"], expected_score(DEFAULT_WEIGHTS))
check("it is a number out of 100", 0.0 <= result["score"] <= 100.0, True)
check("all six components were available", len(result["components"]), 6)
check("...so none is listed as unavailable", result["unavailable"], [])
check_false("...and nothing is suppressed", result["suppressed"])

# The weight table has to be printable verbatim, including the entries nobody
# touched, or a reader cannot see what the untouched half of it was.
check("weights_used is the full table, not just the overrides",
      result["weights_used"], DEFAULT_WEIGHTS)
check("weights_used is a copy, so a caller cannot mutate the module default",
      result["weights_used"] is DEFAULT_WEIGHTS, False)

for name, normalised in NORMALISED.items():
    check(f"{name} normalises to {normalised}", round(by_name(result)[name]["normalised"], 4),
          normalised)

for item in result["components"]:
    for key in ("name", "raw", "normalised", "weight", "contribution", "basis", "raw_inputs"):
        check_true(f"{item['name']} reports its `{key}`", key in item)
    check_true(f"{item['name']} normalises into [0, 1]", 0.0 <= item["normalised"] <= 1.0)

# Contributions sum to the unrounded score, so the arithmetic is checkable by
# hand without rerunning anything.
check_true("the contributions sum to the score",
           abs(sum(item["contribution"] for item in result["components"]) - result["score"]) < 0.05)
check_true("each contribution is weight x normalised, rescaled by the total weight",
           all(abs(item["contribution"]
                   - 100.0 * item["weight"] * item["normalised"] / result["weight_total"]) < 1e-9
               for item in result["components"]))
check("the denominator is the number of components that carried data and weight",
      result["denominator"], 6)
check("the registry size travels with it", result["components_registered"], 6)

# The raw value and the basis it was normalised against both travel, so a reader
# who disagrees with an anchor can see exactly what it did.
check("the raw h-index is carried, not only its normalised form",
      by_name(result)["citation_h_index"]["raw"], 3.0)
check("...beside the anchor it was divided by",
      by_name(result)["citation_h_index"]["raw_inputs"]["anchor_h"], scoring.H_INDEX_ANCHOR)
check("...and the coverage it was computed over",
      by_name(result)["citation_h_index"]["raw_inputs"]["papers_covered"], 10)
check_true("every component states its basis in words",
           all(len(item["basis"]) > 30 for item in result["components"]))


print("\nweights change the score, and are the only thing that does")

heavier = composite_score(BUNDLE, {"people_with_lead_slot": 5.0})
check("weighting the strongest component up raises the score",
      heavier["score"], expected_score({**DEFAULT_WEIGHTS, "people_with_lead_slot": 5.0}))
check_true("...which is a different number from the flat one",
           heavier["score"] != result["score"])
check("the overridden weight is reported as applied",
      heavier["weights_used"]["people_with_lead_slot"], 5.0)

dropped = composite_score(BUNDLE, {"citation_h_index": 0.0, "citation_median": 0.0})
check("zeroing the citation pair scores the corpus without citations",
      dropped["score"],
      expected_score({**DEFAULT_WEIGHTS, "citation_h_index": 0.0, "citation_median": 0.0}))
check("a zero-weight component is still reported, with its raw value",
      by_name(dropped)["citation_h_index"]["raw"], 3.0)
check("...but is not counted in the denominator", dropped["denominator"], 4)

check("scaling the whole table changes nothing — only the ratios matter",
      composite_score(BUNDLE, {name: 7.0 for name in COMPONENT_NAMES})["score"],
      result["score"])

check("two runs over one bundle give one answer",
      composite_score(BUNDLE)["score"], composite_score(BUNDLE)["score"])


# ============================================================
# Missing data is missing, never zero
# ============================================================

print("\nmissing data")

no_citations = composite_score({"s3a": S3A, "s3b": S3B, "s4": S4, "s9": S9})
check("a corpus with no citation lookup scores on what it has",
      sorted(no_citations["unavailable"]), ["citation_h_index", "citation_median"])
check("...with a reason attached to each",
      sorted(no_citations["unavailable_reasons"]), ["citation_h_index", "citation_median"])
check_true("...naming the bundle keys it looked for",
           "impact" in no_citations["unavailable_reasons"]["citation_h_index"])

# The distinction the whole "missing, never zero" rule exists for: a lab with no
# citation data retrieved is not a lab with no citations.
four_only = {name: 1.0 for name in
             ("lead_slot_share", "people_with_lead_slot", "time_to_lead", "records_per_year")}
four_only.update({"citation_h_index": 0.0, "citation_median": 0.0})
check("an unavailable component is excluded from the denominator, not scored as zero",
      no_citations["score"], expected_score(four_only))
# What it would have been if a missing lookup were entered as a zero: the same
# numerator over a denominator of six. A lab whose citations nobody fetched is
# not a lab with no citations, and the gap between these two numbers is the
# size of that mistake.
as_if_zero = round(100.0 * sum(NORMALISED[name] for name in four_only
                               if four_only[name]) / len(COMPONENT_NAMES), 1)
check_true("...which is a materially different number from scoring the gap as zero",
           no_citations["score"] > as_if_zero)
check("the denominator says how many components the score rests on",
      no_citations["denominator"], 4)

thin = composite_score({**BUNDLE, "impact": {**IMPACT, "covered": 4, "denominator": 10}})
check("citation coverage below the floor makes both citation components unavailable",
      sorted(thin["unavailable"]), ["citation_h_index", "citation_median"])
check_true("...for a stated reason about the lookup, not about the papers",
           "covers 4 of 10" in thin["unavailable_reasons"]["citation_h_index"])

nothing_found = composite_score({**BUNDLE,
                                 "impact": {**IMPACT, "covered": 0, "denominator": 10}})
check_true("a lookup that matched nothing says so in those words",
           "not about the papers" in nothing_found["unavailable_reasons"]["citation_median"])

suppressed_source = composite_score({**BUNDLE, "s4": {**S4, "suppressed": True, "median": None}})
check("a metric that suppressed its own aggregate does not become a score input",
      "time_to_lead" in suppressed_source["unavailable"], True)
check_true("...and the reason names the floor it fell under",
           "floor" in suppressed_source["unavailable_reasons"]["time_to_lead"])

short_window = composite_score({**BUNDLE, "s9": {"denominator": 5, "years": [
    {"year": 2024, "count": 3, "partial": False, "indexing_lag": False},
    {"year": 2025, "count": 3, "partial": False, "indexing_lag": True}]}})
check_true("too few fully observed years drops the volume component",
           "records_per_year" in short_window["unavailable"])
check_true("...and says which knob widens it",
           "years_back" in short_window["unavailable_reasons"]["records_per_year"])


print("\nsuppression")

# Below three inputs a "composite" is one or two metrics with a change of scale,
# and printing it out of 100 implies more evidence than exists.
check("the floor", scoring.MIN_SCORED_COMPONENTS, 3)

two_only = composite_score({"s3a": S3A, "s3b": S3B})
check_true("two components suppress the aggregate", two_only["suppressed"])
check("the score is None, not a number computed from two inputs", two_only["score"], None)
check("every contribution is None too",
      [item["contribution"] for item in two_only["components"]], [None, None])
# R2, as everywhere else in this package: the aggregate goes, the parts remain.
check("the raw values survive suppression",
      [item["raw"] for item in two_only["components"]], [0.5, 0.75])
check("the normalised values survive suppression",
      [item["normalised"] for item in two_only["components"]], [0.5, 0.75])
check("the weights survive suppression",
      [item["weight"] for item in two_only["components"]], [1.0, 1.0])

three = composite_score({"s3a": S3A, "s3b": S3B, "s4": S4})
check_false("three components is exactly enough", three["suppressed"])
check_true("...and a score renders", isinstance(three["score"], float))

all_zero = composite_score(BUNDLE, {name: 0.0 for name in COMPONENT_NAMES})
check_true("a table of zero weights suppresses rather than dividing by zero",
           all_zero["suppressed"])
check("...and returns no score", all_zero["score"], None)

two_weighted = composite_score(BUNDLE, {name: 0.0 for name in COMPONENT_NAMES[2:]})
check_true("only two non-zero weights suppresses, however much data exists",
           two_weighted["suppressed"])

check("an empty bundle is suppressed, not a crash", composite_score({})["suppressed"], True)
check("...and reports every component as unavailable",
      sorted(composite_score({})["unavailable"]), sorted(COMPONENT_NAMES))


print("\nwhat the bundle may be")

check("a report dict is unwrapped to its metrics",
      composite_score({"refused": False, "metrics": BUNDLE})["score"], result["score"])
check("a bundle keyed by metric function name is accepted too",
      composite_score({"first_author_slots": S3A, "lead_slot_partition": S3B,
                       "time_to_lead": S4, "records_per_year": S9,
                       "citation_metrics": IMPACT})["score"], result["score"])


# ============================================================
# The rule, as assertions
# ============================================================

print("\nthe rule, as assertions")

# 1. One corpus per call. A caller holding two researchers has to call it twice
#    and write the comparison themselves, in their own code, under their own name.
check("a list of bundles raises rather than being scored as a cohort",
      raises(composite_score, [BUNDLE, BUNDLE]), "TypeError")
check("a tuple of bundles raises too", raises(composite_score, (BUNDLE, BUNDLE)), "TypeError")
try:
    composite_score([BUNDLE, BUNDLE])
    message = ""
except TypeError as exc:
    message = str(exc)
check_true("the refusal explains that the comparison is the caller's to justify",
           "one corpus at a time" in message and "justify" in message)

# 2. Nothing in the payload is a position. Walking the whole structure means a
#    field added later cannot introduce one without failing here.
ORDERING_WORDS = ("rank", "percentile", "quantile", "grade", "tier", "star",
                  "rating", "leaderboard", "league", "top ", "best ", "better than",
                  "outperform", "position in", "above average", "below average")


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
    for payload in (result, no_citations, two_only, thin, composite_score({}))
    for text in walk(payload)
    for word in ORDERING_WORDS
    if word in text.lower()
})
check("no key and no string anywhere in the payload is an ordering", offenders, [])

# 3. Registration order is output order. Ordering the inputs by importance would
#    be a ranking of the factors, and the reader can see the contributions and
#    draw their own conclusion without one being drawn for them.
emitted = [item["name"] for item in result["components"]]
check("components come back in registration order", emitted, list(COMPONENT_NAMES))
check("...which is not the order their contributions would give",
      emitted == sorted(emitted, key=lambda n: -NORMALISED[n]), False)
check("...nor the ascending one",
      emitted == sorted(emitted, key=lambda n: NORMALISED[n]), False)
check("the fixture's contributions really do disagree with registration order",
      sorted(emitted, key=lambda n: -NORMALISED[n])[0], "people_with_lead_slot")

# 4. Structural, not textual: a module with no sort call cannot order anything,
#    now or after an edit that forgets why.
source = open(scoring.__file__, encoding="utf-8").read()
check("the module calls no sort", ("sorted(" in source or ".sort(" in source), False)
check("the module compares nothing against a score threshold",
      any(token in source for token in ("score >", "score <", "score >=", "score <=")), False)
check("the module exposes no ordering helper",
      [name for name in dir(scoring)
       if any(word in name.lower()
              for word in ("rank", "percentile", "quantile", "grade", "tier", "star",
                           "rating", "compare"))], [])

# 5. Every anchor is a declared constant, which is what makes a percentile
#    uncomputable here rather than merely refused: the function never holds more
#    than one corpus to compute a position within.
for anchor in ("TIME_TO_LEAD_ANCHOR_YEARS", "RECORDS_PER_YEAR_ANCHOR", "H_INDEX_ANCHOR",
               "MEDIAN_CITATIONS_ANCHOR"):
    check_true(f"{anchor} is a declared module constant",
               isinstance(getattr(scoring, anchor), float))

# 6. The two reasons a thing can be absent are kept apart, machine-readably, so a
#    renderer can print them under their own headings. "We cannot get this data"
#    and "we will not do this" are different sentences and a reader has to be
#    able to tell which one they are reading.
check("the exclusion register has exactly the two headings",
      sorted(SCORING_EXCLUSIONS), ["not_implemented", "refused_by_design"])
not_implemented = " ".join(f"{name} {reason}"
                           for name, reason in SCORING_EXCLUSIONS["not_implemented"]).lower()
refused = " ".join(f"{name} {reason}"
                   for name, reason in SCORING_EXCLUSIONS["refused_by_design"]).lower()
check_true("impact factor and the quartile tables sit under `not_implemented`",
           "journal impact factor" in not_implemented and "jcr quartile" in not_implemented
           and "cas partition" in not_implemented)
check_true("...with unavailability given as the reason",
           "no free, redistributable source" in not_implemented)
check_true("...and are not filed as a refusal",
           "impact factor" not in refused)
check_true("rank, percentile and star rating sit under `refused_by_design`",
           all(word in refused for word in ("rank", "percentile", "star rating", "tier")))
# Round four: the entry is still here and its original objection is quoted
# verbatim inside it, but the heading is narrower than it was. Section 9 prints a
# fitted slope now; what this register refuses is a slope reaching the *score*.
# The old label on this assertion said "unchanged from the original register",
# which stopped being true the moment the heading was scoped — the strings below
# would have kept passing and said so anyway.
check_true("...and so do fitted trends, as inputs to the score",
           "fitted slopes" in refused and "year-over-year" in refused)
check_true("...with the scope stated rather than implied",
           "as inputs to the score" in refused)
check_true("...and round one's objection kept word for word inside it",
           "do not support a slope" in refused)
check_true("the refusal is stated as surviving a better data source",
           "refused" in refused)


print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(1 if _failed else 0)
