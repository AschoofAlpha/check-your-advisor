"""
The composite score, and the boundary that replaces the old blanket ban.

`caveats.DROPPED_REGISTER` still carries the line "Any composite score, weighted
index, grade, or star rating — encodes weights the data cannot justify and
recreates the people-ranking this tool excludes." That entry welded two separate
objections together. Only one of them survived review.

The first objection stands as stated: nothing in this data justifies any
particular weight. This module does not answer it by claiming to have found
better weights. It answers it by refusing to hide them. `DEFAULT_WEIGHTS` is
flat — every component counts the same — because a flat table is the only
default that asserts nothing. Every component returns the `raw` value it
consumed, the `basis` it was normalised against, the `weight` it was multiplied
by, and a `contribution` that is arithmetic the reader can redo by hand. A
weight the reader can see and edit is a stated assumption. A weight buried
inside a function is a claim being smuggled.

The second objection — that a score is a ranking of people wearing a different
hat — does not follow, and this file is arranged so that it cannot become one:

- `composite_score` takes one bundle. There is no second positional parameter
  and no sequence form; passing a list raises TypeError. A caller holding two
  researchers has to call it twice and write the comparison themselves, in their
  own code, under their own name. The comparison is not unavailable — it is
  simply not something this function will do on the caller's behalf.
- Every normalisation anchor here is a declared module constant. Nothing is
  normalised against a population of researchers, so a percentile or a quantile
  position is not merely forbidden, it is uncomputable: the function never holds
  more than one corpus.
- There is no mapping from `score` to a letter, a tier, a star count, a label or
  a verdict, and no threshold is compared against anywhere in this file.
- Nothing in this module sorts. `median` is imported from `metrics` for exactly
  that reason, and `components` comes back in registration order, never in
  contribution order.

So a printed 61.4 is a statement about one corpus under one weight table, and it
travels with the six numbers that produced it. It is not a position. Two of them
side by side are still not an ordering until a reader supplies the judgement,
which is where that judgement belonged in the first place.

Two further boundaries, worded differently on purpose, because the difference
between them is the whole point:

- Ranking, percentile, grade, star rating and fitted trends are refused by
  design. A better data source would not change that answer.
- Journal Impact Factor, JCR quartile and CAS partition are not implemented,
  which is a different sentence. There is no free, redistributable source for
  those tables and this toolkit ships no licensed data. That is a supply fact,
  not a verdict.

Both lists are machine-readable in `SCORING_EXCLUSIONS` so a renderer can print
them under their own headings rather than flattening them into one.

Pure, like `metrics`: dicts in, dict out, no file access, no network, standard
library only.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .metrics import MIN_N_AGGREGATE, median

__all__ = [
    "COMPONENT_NAMES",
    "DEFAULT_WEIGHTS",
    "H_INDEX_ANCHOR",
    "MEDIAN_CITATIONS_ANCHOR",
    "MIN_CITATION_COVERAGE",
    "MIN_SCORED_COMPONENTS",
    "MIN_SCORED_YEARS",
    "RECORDS_PER_YEAR_ANCHOR",
    "SCORING_EXCLUSIONS",
    "TIME_TO_LEAD_ANCHOR_YEARS",
    "composite_score",
    "resolve_weights",
]


# ------------------------------------------------------------------
# Normalisation anchors
# ------------------------------------------------------------------
#
# Every anchor below is a declared constant. None of them is estimated from a
# population of researchers, and that is deliberate: it is what makes a
# percentile uncomputable here rather than merely disallowed. Each one is a
# legitimate thing for a reader to disagree with, so each is named, exported and
# repeated in the `basis` string of the component that consumes it.

# Section 7.4 measures the gap between someone's first appearance and their
# first lead slot. Four years is one doctorate's worth of waiting, chosen as a
# round convention rather than an observed norm, and deliberately generous
# because CAV-07 puts one to two years of publication lag inside every value.
TIME_TO_LEAD_ANCHOR_YEARS = 4.0

# Section 7.9 counts PubMed records, not research papers (CAV-18), and volume is
# confounded with headcount (CAV-04, CAV-12). Eight records in a fully observed
# year is a declared saturation point, not a target. It is the anchor most
# readers will want to move, because it is the one most sensitive to field.
RECORDS_PER_YEAR_ANCHOR = 8.0

# The corpus is a fixed recent window, so `h_index` here is bounded by the
# number of papers inside that window and is not a career h-index. Fifteen is a
# declared saturation point for a windowed corpus; against a lifetime corpus it
# would be far too low, which is the sort of mismatch the exposed weight table
# exists to let a reader correct.
H_INDEX_ANCHOR = 15.0

# Median citations per covered paper. Citation rates differ between fields by an
# order of magnitude and no field normalisation is applied — see
# SCORING_EXCLUSIONS["not_implemented"] — so this anchor is field-specific in
# effect even though it is written as a single number.
MEDIAN_CITATIONS_ANCHOR = 20.0

# Below half coverage an h-index is a floor rather than an estimate: the missing
# papers can only push it up. Both citation components go unavailable rather
# than scoring a number that is known to be wrong in a known direction.
MIN_CITATION_COVERAGE = 0.5

# A median over one or two fully observed years is not a median. Three is also
# exactly what the default five-year window leaves after the partial start bin
# and the two indexing-lag bins are dropped, so a shorter window makes this
# component unavailable rather than quietly noisier.
MIN_SCORED_YEARS = 3

# Below three inputs a "composite" is one or two metrics with a change of scale,
# and printing it out of 100 implies more evidence than exists.
MIN_SCORED_COMPONENTS = 3


# ------------------------------------------------------------------
# What is not a component, and why — the two reasons are not the same
# ------------------------------------------------------------------

SCORING_EXCLUSIONS: dict[str, tuple[tuple[str, str], ...]] = {
    # Absent because the input does not exist in reachable form. If that changed,
    # the question would be reopened on its merits. Do not read these as verdicts.
    "not_implemented": (
        (
            "Journal Impact Factor, JCR quartile, CAS partition",
            "Not built. There is no free, redistributable source for any of these tables and "
            "this toolkit ships no licensed data, so the input is unobtainable rather than "
            "unwanted. Whether a journal-level number should ever stand in for an individual "
            "paper is a separate argument, and this module does not settle it — it simply has "
            "nothing to compute from.",
        ),
        (
            "Field-normalised citation impact",
            "Not built. It needs a subject classification plus per-field citation "
            "distributions, and no free source supplies both at record level. Its absence is "
            "why the two citation components carry a field-confound note instead of a "
            "correction, and why this score is not comparable across fields.",
        ),
    ),
    # Absent by decision. A better data source would not change these.
    "refused_by_design": (
        (
            "Rank, percentile, quantile position, letter grade, star rating, tier",
            "Refused. `composite_score` sees one corpus, so there is no population to place "
            "anyone in and no second corpus to sit above or below. The absolute score is the "
            "whole output; the ordering is the reader's to make and to own.",
        ),
        (
            "Trends, fitted slopes, year-over-year percentage change — as inputs to the score",
            "Refused here, unchanged from the original register: a handful of right-censored "
            "integer points do not support a slope. `records_per_year` therefore enters as a "
            "median over fully observed years, which is order-free by construction. Round four "
            "did not touch this: Section 9 now prints a fitted slope beside its interval, but "
            "`composite_score` reads no slope and no direction, so nothing on this page moves "
            "because output rose or fell. The narrowing in the heading is the whole change — "
            "what is refused is the slope as an *input here*, not the slope's existence.",
        ),
    ),
}


# ------------------------------------------------------------------
# Component extractors
# ------------------------------------------------------------------
#
# Each returns either {"unavailable": reason} or a dict carrying `raw`,
# `normalised` and `raw_inputs`. A missing component is missing, never zero: a
# lab with no citation data retrieved is not a lab with no citations.


def _clamp01(value: float) -> float:
    """Normalised values live in [0, 1], so the score cannot leave [0, 100]."""
    return max(0.0, min(1.0, float(value)))


def _lead_slot_share(source: Mapping[str, Any]) -> dict[str, Any]:
    """
    Section 7.2, paper side. Share of eligible papers whose lead slot is held by
    a lead-holding lab person (stratum A).

    Already a proportion, so the normalisation is the identity and `normalised`
    equals `raw`; it is still returned separately so the shape of every
    component is the same and the reader does not have to check which ones were
    rescaled. The denominator is `first_author_slots`' own: papers where the PI
    is the lead and papers with a collective in slot 0 are already out. Leads
    that could not be classified stay in the denominator, so this share is a
    lower bound rather than a number tuned by dropping awkward rows.
    """
    denominator = int(source.get("denominator") or 0)
    if source.get("not_computable") or denominator <= 0:
        return {"unavailable": "no paper has a lead slot that is neither collective nor the PI's own"}
    if source.get("suppressed"):
        return {
            "unavailable": (
                f"metrics.first_author_slots suppressed its aggregate at {denominator} eligible "
                f"papers (floor {MIN_N_AGGREGATE})"
            )
        }
    counts = source.get("counts") or {}
    trainee = int(counts.get("A", 0))
    share = trainee / denominator
    return {
        "raw": share,
        "normalised": _clamp01(share),
        "raw_inputs": {
            "trainee_led_slots": trainee,
            "eligible_papers": denominator,
            "senior_shaped_leads": int(counts.get("D", 0)),
            "unclassified_leads": int(counts.get("unclassified", 0)),
        },
    }


def _people_with_lead_slot(source: Mapping[str, Any]) -> dict[str, Any]:
    """
    Section 7.3, person side. `holds_lead / (holds_lead + observed_without_lead)`.

    The `too_recent` bucket is excluded from the denominator rather than counted
    as a failure — someone inside the publication lag has not been passed over,
    they have not been observed yet. Their count travels in `raw_inputs` so the
    exclusion is visible.

    This is the ratio the report itself refuses to print as a headline: it is
    the "lead-author conversion rate" from the dropped register, and CAV-06
    applies to it in full. Its denominator is conditioned on having published at
    least once, so anyone who joined this lab and left without a paper is in
    neither bucket, and the ratio is optimistic by an amount this data cannot
    measure. It survives here as a score input, never as a rate on its own line,
    and the three bucket counts are returned beside it so the conditioning
    cannot be lost on the way to the page.
    """
    counts = source.get("counts") or {}
    holds = int(counts.get("holds_lead", 0))
    without = int(counts.get("observed_without_lead", 0))
    too_recent = int(counts.get("too_recent", 0))
    denominator = holds + without
    if denominator <= 0:
        return {
            "unavailable": (
                f"every one of the {too_recent} people in the cohort is still inside the "
                "publication lag, so there is nobody to compute this over"
            )
        }
    if denominator < MIN_N_AGGREGATE:
        return {
            "unavailable": (
                f"only {denominator} people are outside the publication lag (floor "
                f"{MIN_N_AGGREGATE})"
            )
        }
    share = holds / denominator
    return {
        "raw": share,
        "normalised": _clamp01(share),
        "raw_inputs": {
            "holds_lead": holds,
            "observed_without_lead": without,
            "too_recent_excluded": too_recent,
            "cohort_denominator": int(source.get("denominator") or 0),
            "lag_years": source.get("lag_years"),
        },
    }


def _time_to_lead(source: Mapping[str, Any]) -> dict[str, Any]:
    """
    Section 7.4. `1 - median_lag_years / TIME_TO_LEAD_ANCHOR_YEARS`, clamped.

    The only component whose direction is inverted: a longer wait normalises
    lower. The anchor is four years, declared rather than measured, and generous
    because CAV-07 puts one to two years of publication lag inside every value —
    a median of zero does not mean people publish on arrival.

    CAV-08 conditions this on people who reached a lead slot at all, so the
    count of people still without one is carried in `raw_inputs`: a short median
    computed over the two people who made it is not a fast lab.
    """
    if source.get("not_computable"):
        return {"unavailable": "nobody in the corpus has reached a lead slot"}
    value = source.get("median")
    if source.get("suppressed") or value is None:
        return {
            "unavailable": (
                f"metrics.time_to_lead suppressed its median at "
                f"{int(source.get('denominator') or 0)} people (floor {MIN_N_AGGREGATE})"
            )
        }
    value = float(value)
    return {
        "raw": value,
        "normalised": _clamp01(1.0 - value / TIME_TO_LEAD_ANCHOR_YEARS),
        "raw_inputs": {
            "median_lag_years": value,
            "people_with_lead_slot": int(source.get("denominator") or 0),
            "count_at_zero": int(source.get("count_at_zero") or 0),
            "still_without_lead": len(source.get("still_without_lead") or []),
            "anchor_years": TIME_TO_LEAD_ANCHOR_YEARS,
        },
    }


def _records_per_year(source: Mapping[str, Any]) -> dict[str, Any]:
    """
    Section 7.9. Median records in fully observed years / RECORDS_PER_YEAR_ANCHOR.

    Years flagged `partial` (the window opens and closes mid-year) or
    `indexing_lag` (CAV-17: the most recent 18 months are undercounted) are
    dropped before the median, because every lab looks like it is winding down
    at the right-hand edge and a score should not reward having started earlier.
    The years used and the years dropped both travel in `raw_inputs`.

    A median across years is a location statistic and carries no ordering, which
    is what keeps this component clear of the trend prohibition. No slope, no
    year-over-year change, no first-year-to-last-year comparison is computed
    from this series here or anywhere else.
    """
    years = source.get("years") or []
    usable = [year for year in years if not year.get("partial") and not year.get("indexing_lag")]
    dropped = [
        int(year["year"]) for year in years if year.get("partial") or year.get("indexing_lag")
    ]
    if len(usable) < MIN_SCORED_YEARS:
        return {
            "unavailable": (
                f"only {len(usable)} fully observed years after partial and indexing-lag bins "
                f"are dropped (floor {MIN_SCORED_YEARS}); widen years_back"
            )
        }
    counts = [int(year.get("count") or 0) for year in usable]
    value = median(counts)
    if value is None:
        return {"unavailable": "no fully observed year carried a record count"}
    return {
        "raw": value,
        "normalised": _clamp01(value / RECORDS_PER_YEAR_ANCHOR),
        "raw_inputs": {
            "median_records_per_year": value,
            "years_used": [int(year["year"]) for year in usable],
            "counts_used": counts,
            "years_dropped": dropped,
            "records_total": int(source.get("denominator") or 0),
            "anchor_records": RECORDS_PER_YEAR_ANCHOR,
        },
    }


def _citation_gate(source: Mapping[str, Any]) -> str | None:
    """Shared availability check for both citation components. Reason, or None."""
    denominator = int(source.get("denominator") or 0)
    covered = int(source.get("covered") or 0)
    if denominator <= 0:
        return "the citation record carries no papers"
    if covered <= 0:
        return (
            f"no citation count was retrieved for any of the {denominator} papers, which is a "
            "statement about the lookup, not about the papers"
        )
    if source.get("suppressed"):
        return (
            f"impact.citation_metrics suppressed its aggregates at {covered} covered papers"
        )
    if covered / denominator < MIN_CITATION_COVERAGE:
        return (
            f"citation data covers {covered} of {denominator} papers, below the "
            f"{MIN_CITATION_COVERAGE:.0%} floor; below it the metric is a lower bound rather "
            "than an estimate"
        )
    return None


def _citation_h_index(source: Mapping[str, Any]) -> dict[str, Any]:
    """
    From `impact.citation_metrics`. `h_index / H_INDEX_ANCHOR`, clamped.

    The corpus is a fixed recent window, so this h is bounded by the papers
    inside that window: it is not a career h-index and must not be printed as
    one. It is also size-confounded — a large lab reaches a given h faster —
    which is why the paper count and the coverage it was computed over are both
    in `raw_inputs`.

    Unavailable rather than scored whenever coverage falls below
    MIN_CITATION_COVERAGE, because missing citation records can only push an
    h-index down, so a thin lookup produces a number that is wrong in a known
    direction and would be read as a low result. Above that floor the same
    thing is still true to a smaller degree, so `impact`'s own `lower_bound`
    flag is carried through untouched: partial coverage means this h is a floor
    and the component says so rather than implying otherwise by scoring it.

    `mixed_sources` travels too. An h-index assembled from OpenAlex, Semantic
    Scholar and Europe PMC counts sits on three citation graphs with three
    coverage profiles, and `impact.citation_metrics` flags that for a reason.
    """
    reason = _citation_gate(source)
    if reason:
        return {"unavailable": reason}
    value = source.get("h_index")
    if value is None:
        return {"unavailable": "impact.citation_metrics returned no h_index"}
    value = float(value)
    return {
        "raw": value,
        "normalised": _clamp01(value / H_INDEX_ANCHOR),
        "raw_inputs": {
            "h_index": value,
            "i10_index": source.get("i10_index"),
            "total_citations": source.get("total_citations"),
            "papers_covered": int(source.get("covered") or 0),
            "papers_total": int(source.get("denominator") or 0),
            "lower_bound": bool(source.get("lower_bound")),
            "mixed_sources": bool(source.get("mixed_sources")),
            "sources": source.get("sources"),
            "generated_at": source.get("generated_at"),
            "anchor_h": H_INDEX_ANCHOR,
        },
    }


def _citation_median(source: Mapping[str, Any]) -> dict[str, Any]:
    """
    From `impact.citation_metrics`. `median_citations / MEDIAN_CITATIONS_ANCHOR`,
    clamped.

    Per-paper rather than aggregate, so unlike the h-index it does not grow with
    lab size — it is here so that volume and per-paper reception can be weighted
    apart instead of being fused into one number. The median is taken over
    covered papers only: a paper with no citation record is absent from the
    calculation and is never entered as a zero.

    Field is the dominant confound and nothing corrects for it, so this
    component is not comparable across fields even under an identical weight
    table. See SCORING_EXCLUSIONS["not_implemented"].

    Unlike the h-index this is not a floor under partial coverage — a missing
    record moves a median in an unknown direction — so `lower_bound` is carried
    for the reader's eye but must not be read as "the real value is higher".
    """
    reason = _citation_gate(source)
    if reason:
        return {"unavailable": reason}
    value = source.get("median_citations")
    if value is None:
        return {"unavailable": "impact.citation_metrics returned no median_citations"}
    value = float(value)
    return {
        "raw": value,
        "normalised": _clamp01(value / MEDIAN_CITATIONS_ANCHOR),
        "raw_inputs": {
            "median_citations": value,
            "iqr": source.get("iqr"),
            "papers_covered": int(source.get("covered") or 0),
            "papers_total": int(source.get("denominator") or 0),
            "lower_bound": bool(source.get("lower_bound")),
            "mixed_sources": bool(source.get("mixed_sources")),
            "generated_at": source.get("generated_at"),
            "anchor_citations": MEDIAN_CITATIONS_ANCHOR,
        },
    }


# ------------------------------------------------------------------
# The registry
# ------------------------------------------------------------------


@dataclass(frozen=True)
class _Component:
    """One scored input: where it comes from, how it is normalised, how to read it."""

    name: str
    sources: tuple[str, ...]
    basis: str
    extract: Callable[[Mapping[str, Any]], dict[str, Any]]


# Registration order is output order. `components` is never reordered by
# contribution, weight or value: ordering the inputs by importance would be a
# ranking of the factors, and the reader can see the contributions and draw
# their own conclusion without one being drawn for them.
#
# `sources` lists the bundle keys accepted for each component, in priority
# order: the section code used by `report.build_report`, then the metric
# function's own name, so a caller can pass either the report's `metrics` dict
# or a hand-assembled bundle keyed by function name.
COMPONENTS: tuple[_Component, ...] = (
    _Component(
        name="lead_slot_share",
        sources=("s3a", "first_author_slots"),
        basis=(
            "share of eligible papers whose lead slot is held by a lead-holding lab person "
            "(stratum A); already a proportion, so normalised equals raw"
        ),
        extract=_lead_slot_share,
    ),
    _Component(
        name="people_with_lead_slot",
        sources=("s3b", "lead_slot_partition"),
        basis=(
            "holds_lead / (holds_lead + observed_without_lead); the too_recent bucket is "
            "excluded from the denominator rather than counted as a failure"
        ),
        extract=_people_with_lead_slot,
    ),
    _Component(
        name="time_to_lead",
        sources=("s4", "time_to_lead"),
        basis=(
            f"1 - median_lag_years / {TIME_TO_LEAD_ANCHOR_YEARS:g}, clamped to [0, 1]; the "
            "anchor is a declared convention, not an observed norm"
        ),
        extract=_time_to_lead,
    ),
    _Component(
        name="records_per_year",
        sources=("s9", "records_per_year"),
        basis=(
            f"median records in fully observed years / {RECORDS_PER_YEAR_ANCHOR:g}, clamped to "
            "[0, 1]; partial and indexing-lag years are dropped first"
        ),
        extract=_records_per_year,
    ),
    _Component(
        name="citation_h_index",
        sources=("impact", "s14", "citation_metrics"),
        basis=(
            f"h_index / {H_INDEX_ANCHOR:g}, clamped to [0, 1]; a windowed h, not a career h, "
            f"and only when citation coverage reaches {MIN_CITATION_COVERAGE:.0%}"
        ),
        extract=_citation_h_index,
    ),
    _Component(
        name="citation_median",
        sources=("impact", "s14", "citation_metrics"),
        basis=(
            f"median citations per covered paper / {MEDIAN_CITATIONS_ANCHOR:g}, clamped to "
            "[0, 1]; uncovered papers are absent, never zeros"
        ),
        extract=_citation_median,
    ),
)

COMPONENT_NAMES: tuple[str, ...] = tuple(component.name for component in COMPONENTS)

# Flat on purpose. Any other table would be a claim about relative importance
# that this data cannot support, so the default asserts nothing and the report
# prints it verbatim: a reader who leaves it alone has chosen "count everything
# equally", which is a position they can defend, rather than inherited a set of
# magic numbers they never saw.
#
# One consequence worth stating rather than leaving to be discovered: two of the
# six components are citation-based, so a flat table hands citations a third of
# the total. That is an artifact of how many components exist, not a judgement.
# Set `citation_median` to 0.0 to score on the h-index alone, or both to 0.0 to
# score the corpus without citations at all.
DEFAULT_WEIGHTS: dict[str, float] = {name: 1.0 for name in COMPONENT_NAMES}


# ------------------------------------------------------------------
# Weights
# ------------------------------------------------------------------


def _merge_weights(override: Any) -> dict[str, float]:
    """
    DEFAULT_WEIGHTS updated by `override`, validated loudly.

    An unknown key raises instead of being ignored: a typo that silently leaves
    the default table in place would let a reader believe they had changed the
    score when they had not. Negative weights raise too — a negative weight
    inverts a component's direction without saying so, and it can drive the
    total weight to zero or below and put the score outside [0, 100]. Only the
    ratios between weights matter, so scaling the whole table changes nothing.
    """
    merged = dict(DEFAULT_WEIGHTS)
    if not override:
        return merged
    if not isinstance(override, Mapping):
        raise TypeError(f"weights must be a mapping of component name to number, got {type(override).__name__}")
    unknown = [key for key in override if key not in DEFAULT_WEIGHTS]
    if unknown:
        raise ValueError(
            f"unknown score component(s): {', '.join(unknown)}. "
            f"Known components: {', '.join(COMPONENT_NAMES)}"
        )
    for key, raw in override.items():
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"weight for {key!r} must be a number, got {raw!r}") from None
        if not math.isfinite(value):
            raise ValueError(f"weight for {key!r} must be finite, got {raw!r}")
        if value < 0:
            raise ValueError(
                f"weight for {key!r} must be >= 0, got {value}. A negative weight would flip "
                "the component's direction without saying so; set it to 0.0 to drop the "
                "component from the score instead."
            )
        merged[key] = value
    return merged


def resolve_weights(config: Mapping[str, Any] | None) -> dict[str, float]:
    """
    The weight table for a run: DEFAULT_WEIGHTS overridden by config.

    Looked for at `config["advisor"]["score_weights"]` first, then at
    `config["score_weights"]`. Validation is the same as `composite_score`'s, so
    a bad table fails at load time rather than halfway through a report.
    """
    config = config or {}
    advisor = config.get("advisor")
    override = advisor.get("score_weights") if isinstance(advisor, Mapping) else None
    if override is None:
        override = config.get("score_weights")
    return _merge_weights(override)


# ------------------------------------------------------------------
# The score
# ------------------------------------------------------------------


def _resolve_bundle(bundle: Mapping[str, Any]) -> Mapping[str, Any]:
    """Accept either a metric bundle or a whole report dict, unambiguously."""
    for component in COMPONENTS:
        for key in component.sources:
            if key in bundle:
                return bundle
    nested = bundle.get("metrics")
    if isinstance(nested, Mapping):
        return nested
    return bundle


def _source(bundle: Mapping[str, Any], keys: tuple[str, ...]) -> tuple[str, Mapping[str, Any]] | None:
    for key in keys:
        value = bundle.get(key)
        if isinstance(value, Mapping):
            return key, value
    return None


def composite_score(
    bundle: Mapping[str, Any],
    weights: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """
    One corpus, one weight table, one number between 0 and 100 — plus everything
    needed to rebuild that number by hand.

    `bundle` is the metric dict from `report.build_report` (keys `s3a`, `s3b`,
    `s4`, `s9`, plus `impact` from `impact.citation_metrics`), a bundle keyed by
    metric function name, or a whole report dict, which is unwrapped to its
    `metrics`. `weights` overrides `DEFAULT_WEIGHTS` per component; unknown or
    negative entries raise.

    Returns:
      score        weighted mean of the normalised components, rescaled to
                   0-100 and rounded to one decimal. None when suppressed.
      components   one entry per available component, in registration order,
                   each with `name`, `source`, `basis`, `raw`, `raw_inputs`,
                   `normalised`, `weight` and `contribution`. Contributions sum
                   to the unrounded score, so the arithmetic is checkable
                   without rerunning anything.
      weights_used the full table actually applied, including untouched
                   defaults, so the report can print it verbatim.
      unavailable  names of components with no usable data, with the reason for
                   each in `unavailable_reasons`. These are excluded from the
                   weighted denominator entirely — never scored as zero, because
                   a lookup that returned nothing is not a result of nothing.
      suppressed   True when fewer than MIN_SCORED_COMPONENTS components carried
                   both data and a non-zero weight, or when the total weight is
                   zero. Then `score` and every `contribution` are None while
                   `raw`, `normalised` and `weight` survive: the parts remain,
                   the aggregate does not, which is the convention `metrics`
                   uses everywhere.

    The signature takes one bundle and there is no sequence form. A list raises
    TypeError, because a list here would be an attempt to score several people
    in a single call, and this function has no opinion on how two of these
    numbers relate to each other.
    """
    if not isinstance(bundle, Mapping):
        raise TypeError(
            "composite_score scores one corpus at a time and takes a single metric bundle, not "
            f"a {type(bundle).__name__} of them. Call it once per corpus; any comparison "
            "between researchers is the caller's to write and to justify."
        )

    weights_used = _merge_weights(weights)
    metrics = _resolve_bundle(bundle)

    components: list[dict[str, Any]] = []
    unavailable: list[str] = []
    reasons: dict[str, str] = {}

    for component in COMPONENTS:
        found = _source(metrics, component.sources)
        if found is None:
            unavailable.append(component.name)
            reasons[component.name] = (
                f"the bundle carries none of: {', '.join(component.sources)}"
            )
            continue
        key, source = found
        extracted = component.extract(source)
        if "unavailable" in extracted:
            unavailable.append(component.name)
            reasons[component.name] = extracted["unavailable"]
            continue
        components.append({
            "name": component.name,
            "source": key,
            "basis": component.basis,
            "raw": extracted["raw"],
            "raw_inputs": extracted["raw_inputs"],
            "normalised": extracted["normalised"],
            "weight": weights_used[component.name],
            "contribution": None,
        })

    total_weight = sum(item["weight"] for item in components)
    scored = [item for item in components if item["weight"] > 0]
    suppressed = len(scored) < MIN_SCORED_COMPONENTS or total_weight <= 0

    score: float | None = None
    if not suppressed:
        for item in components:
            item["contribution"] = 100.0 * item["weight"] * item["normalised"] / total_weight
        score = round(sum(item["contribution"] for item in components), 1)

    return {
        "score": score,
        "components": components,
        "weights_used": weights_used,
        "suppressed": suppressed,
        "unavailable": unavailable,
        "unavailable_reasons": reasons,
        # Denominator convention (R1): the population the value was computed
        # over is the set of components that carried both data and weight.
        "denominator": len(scored),
        "components_registered": len(COMPONENTS),
        "weight_total": total_weight,
        "min_components": MIN_SCORED_COMPONENTS,
    }
