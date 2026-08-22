"""
Metric functions for the advisor profile report (Section 7 of the spec).

Every function here is pure: plain dicts in, plain dicts out, no file access, no
network, no matplotlib. That is what lets the whole metric layer be checked
against synthetic fixtures offline.

Two conventions hold throughout, because they are what keep the output honest
rather than merely correct:

- Every result carries the denominator it was computed over, so a caller cannot
  render a number without the population it came from (R1).
- Every result carries `suppressed`. Below the minimum sample size the aggregate
  is None and the underlying rows are returned in its place, rather than a
  confident figure computed from four data points (R2).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Any

# R2. A percentage below n=20 reads as precision the sample cannot support; an
# aggregate below n=5 is replaced by the rows it was built from.
MIN_N_PERCENT = 20
MIN_N_AGGREGATE = 5

# Section 7.10. The team-size subset is a second cut of an already small corpus,
# so it needs its own, higher floor before a median means anything.
MIN_N_SUBSET_MEDIAN = 10


def percent(numerator: int, denominator: int) -> int | None:
    """R2: a percentage exists only at n >= 20, otherwise None."""
    if denominator < MIN_N_PERCENT or denominator <= 0:
        return None
    return round(100 * numerator / denominator)


def quantile(values: Sequence[float], q: float) -> float | None:
    """Linear-interpolation quantile, so the result does not depend on numpy."""
    ordered = sorted(values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return float(ordered[low] + (ordered[high] - ordered[low]) * (position - low))


def median(values: Sequence[float]) -> float | None:
    """The only central-tendency statistic this report computes. Never a mean."""
    return quantile(values, 0.5)


def _strata(people: Sequence[dict[str, Any]], wanted: str) -> list[dict[str, Any]]:
    """People in any of the strata named by `wanted`, e.g. "A" or "AB"."""
    return [person for person in people if person["stratum"] in wanted]


# ------------------------------------------------------------------
# S2 — person roster
# ------------------------------------------------------------------


def person_roster(people: Sequence[dict[str, Any]], n_strict: int, n_loose: int) -> dict[str, Any]:
    """
    Section 7.1. The raw roster; no minimum sample size, no per-row percentage.

    The strict/loose gap is carried alongside because it is the error bar on
    every person-level count in the report, not a footnote about method.
    """
    rows = [
        {
            "name": person["name"],
            "marker": person["marker"],
            "stratum": person["stratum"],
            "affiliation_signal": person["affiliation_signal"],
            "n_appearances": person["n_appearances"],
            "n_first_slots": person["n_first_slots"],
            "n_equal_contrib": person["n_equal_contrib"],
            "first_year": person["first_year"],
            "last_year": person["last_year"],
            "left_censored": person["left_censored"],
            "right_censored": person["right_censored"],
            "flags": list(person["flags"]),
            # Per-appearance detail, so a row is self-describing: the timeline can be
            # drawn, and its row order asserted, from the roster alone without reaching
            # back into `build_people`. Additive — no existing key changes.
            "years": list(person["years"]),
            "lead_years": list(person["lead_years"]),
            "first_date": person["first_date"],
        }
        for person in people
    ]
    return {
        "rows": rows,
        "denominator": len(people),
        "n_strict": n_strict,
        "n_loose": n_loose,
        "by_stratum": {key: len(_strata(people, key)) for key in ("A", "B", "C", "D")},
        "suppressed": False,
    }


# ------------------------------------------------------------------
# S3a — first-author slot accounting, paper side
# ------------------------------------------------------------------


def first_author_slots(
    papers: Sequence[dict[str, Any]],
    lead_stratum_by_pmid: dict[str, str],
) -> dict[str, Any]:
    """
    Section 7.2. Who occupies the lead slot on this lab's papers.

    Counts slots, not people, and says so: a large count can come from three
    prolific people in a twenty-person lab.
    """
    eligible = []
    dropped_slot0_collective = []
    dropped_pi_is_lead = []
    for paper in papers:
        if paper["slot0_collective"]:
            dropped_slot0_collective.append(paper["pmid"])
            continue
        if paper["pi_index"] == 0:
            dropped_pi_is_lead.append(paper["pmid"])
            continue
        eligible.append(paper)

    counts = {"A": 0, "B": 0, "C": 0, "D": 0, "unclassified": 0}
    rows = []
    for paper in eligible:
        stratum = lead_stratum_by_pmid.get(paper["pmid"], "unclassified")
        counts[stratum] += 1
        lead = paper["persons"][0] if paper["persons"] else {}
        rows.append({
            "pmid": paper["pmid"],
            "year": paper["year"],
            "lead_name": (lead.get("name") or "").strip(),
            "stratum": stratum,
        })

    denominator = len(eligible)
    percentages = None
    if denominator >= MIN_N_PERCENT:
        percentages = {key: percent(value, denominator) for key, value in counts.items()}

    return {
        "denominator": denominator,
        "counts": counts,
        "percentages": percentages,
        "suppressed": denominator < MIN_N_AGGREGATE,
        "not_computable": denominator == 0,
        "rows": rows,
        "dropped_slot0_collective": dropped_slot0_collective,
        "dropped_pi_is_lead": dropped_pi_is_lead,
    }


# ------------------------------------------------------------------
# S3b — lead-slot holders, person side
# ------------------------------------------------------------------


def lead_slot_partition(
    people: Sequence[dict[str, Any]],
    window_end_year: int,
    lag_years: int = 3,
) -> dict[str, Any]:
    """
    Section 7.3. A three-bucket count partition, never a rate.

    The denominator is conditioned on having published at all, so any ratio
    built from it is optimistic by an unmeasurable amount and reads as personal
    odds. Bucket 3 exists so a lab that just expanded does not read as
    exploitative; excluding those people instead would change n silently.
    """
    cohort = _strata(people, "AB")
    holds_lead, observed_without_lead, too_recent = [], [], []
    for person in cohort:
        entry = {
            "name": person["name"],
            "marker": person["marker"],
            "first_year": person["first_year"],
            "years_observed": window_end_year - person["first_year"],
        }
        if person["stratum"] == "A":
            holds_lead.append(entry)
        elif entry["years_observed"] >= lag_years:
            observed_without_lead.append(entry)
        else:
            too_recent.append(entry)

    return {
        "denominator": len(cohort),
        "lag_years": lag_years,
        "counts": {
            "holds_lead": len(holds_lead),
            "observed_without_lead": len(observed_without_lead),
            "too_recent": len(too_recent),
        },
        "buckets": {
            "holds_lead": holds_lead,
            "observed_without_lead": observed_without_lead,
            "too_recent": too_recent,
        },
        # A percentage is forbidden here at every n, so there is nothing to suppress.
        "percentages": None,
        "suppressed": False,
    }


# ------------------------------------------------------------------
# S4 — time from first appearance to first lead slot
# ------------------------------------------------------------------


def time_to_lead(people: Sequence[dict[str, Any]], partition: dict[str, Any]) -> dict[str, Any]:
    """
    Section 7.4. Distribution of `first_lead_year - first_appearance_year`.

    The count at lag 0 is returned separately because the distribution is
    heavily zero-inflated — many people debut as first author — and a median
    alone hides exactly that.
    """
    cohort = _strata(people, "A")
    values = [
        {
            "name": person["name"],
            "marker": person["marker"],
            "lag_years": max(0, person["first_lead_year"] - person["first_year"]),
        }
        for person in cohort
    ]
    lags = [item["lag_years"] for item in values]
    distribution = dict(sorted(Counter(lags).items()))
    enough = len(cohort) >= MIN_N_AGGREGATE

    return {
        "denominator": len(cohort),
        "values": values,
        "distribution": distribution,
        "count_at_zero": distribution.get(0, 0),
        "median": median(lags) if enough else None,
        "suppressed": not enough,
        "not_computable": len(cohort) == 0,
        # Mandatory companion: the censored "not yet" cases, so the reader sees
        # both numbers or neither.
        "still_without_lead": list(partition["buckets"]["observed_without_lead"]),
    }


# ------------------------------------------------------------------
# S5 — observed activity span
# ------------------------------------------------------------------


def activity_span(people: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """
    Section 7.5. Span between first and last appearance, in integer years.

    Median and IQR cover the `complete` bucket only, which biases them toward
    short stays; the censoring counts are returned beside them so the bias is
    visible rather than implied.
    """
    cohort = _strata(people, "AB")
    values = []
    buckets = {"complete": 0, "left_censored": 0, "right_censored": 0, "both_censored": 0}
    complete_values = []
    for person in cohort:
        left, right = person["left_censored"], person["right_censored"]
        if left and right:
            bucket = "both_censored"
        elif left:
            bucket = "left_censored"
        elif right:
            bucket = "right_censored"
        else:
            bucket = "complete"
            complete_values.append(person["span_years"])
        buckets[bucket] += 1
        values.append({
            "name": person["name"],
            "marker": person["marker"],
            "span_years": person["span_years"],
            "first_year": person["first_year"],
            "last_year": person["last_year"],
            "bucket": bucket,
            # Two appearances in one year is not the same fact as one
            # appearance, and must not be rendered as if it were.
            "same_year": person["span_years"] == 0 and person["n_appearances"] > 1,
        })

    enough = len(complete_values) >= MIN_N_AGGREGATE
    return {
        "cohort_denominator": len(cohort),
        "denominator": len(complete_values),
        "buckets": buckets,
        "values": values,
        "complete_values": sorted(complete_values),
        "median": median(complete_values) if enough else None,
        "iqr": (quantile(complete_values, 0.25), quantile(complete_values, 0.75)) if enough else None,
        "suppressed": not enough,
        "single_appearance_count": len(_strata(people, "C")),
    }


# ------------------------------------------------------------------
# S6 — roster size and turnover per year
# ------------------------------------------------------------------


def roster_turnover(
    people: Sequence[dict[str, Any]],
    window_start_year: int,
    window_end_year: int,
) -> dict[str, Any]:
    """
    Section 7.6. Active / arriving / departing counts per calendar year.

    No figure is derived from this series (R7). The last two years' departures
    are marked because a person whose most recent paper is recent has not left.
    """
    years = []
    for year in range(window_start_year, window_end_year + 1):
        active = [person for person in people if year in person["years"]]
        years.append({
            "year": year,
            "active": len(active),
            "arrivals": sum(1 for person in people if person["first_year"] == year),
            "departures": sum(1 for person in people if person["last_year"] == year),
            "departures_right_censored": year >= window_end_year - 1,
        })
    return {"denominator": len(people), "years": years, "suppressed": False}


# ------------------------------------------------------------------
# S7 — PI byline position
# ------------------------------------------------------------------


def pi_byline_positions(papers: Sequence[dict[str, Any]], position_filtered: bool) -> dict[str, Any]:
    """
    Section 7.7. Where the PI sits on their own papers.

    On a role-filtered corpus the filter *is* the metric, so nothing is computed
    at all — `measured` is False and the caller prints CAV-13 instead.
    """
    if position_filtered:
        return {
            "measured": False,
            "denominator": 0,
            "counts": {},
            "percentages": None,
            "suppressed": True,
            "rows": [],
            "corresponding": None,
            "email_coverage": {"covered": 0, "denominator": 0},
        }

    counts = {"first": 0, "last": 0, "sole": 0, "middle": 0, "unlocated": 0}
    rows = []
    corresponding = 0
    email_papers = 0
    for paper in papers:
        index = paper["pi_person_index"]
        n_authors = paper["n_authors"]
        if index is None:
            position = "unlocated"
        elif n_authors == 1:
            position = "sole"
        elif index == 0:
            position = "first"
        elif index == n_authors - 1:
            position = "last"
        else:
            position = "middle"
        counts[position] += 1
        if index is not None and paper["persons"][index].get("is_corresponding"):
            corresponding += 1
        if paper["any_email"]:
            email_papers += 1
        rows.append({"pmid": paper["pmid"], "year": paper["year"], "position": position})

    denominator = len(papers)
    percentages = None
    if denominator >= MIN_N_PERCENT:
        percentages = {key: percent(value, denominator) for key, value in counts.items()}

    return {
        "measured": True,
        "denominator": denominator,
        "counts": counts,
        "percentages": percentages,
        "suppressed": denominator < MIN_N_AGGREGATE,
        "rows": rows,
        # With zero email coverage this flag is False for everyone for reasons
        # that have nothing to do with the PI, so it is not reported at all.
        "corresponding": (
            {"count": corresponding, "denominator": denominator} if email_papers > 0 else None
        ),
        "email_coverage": {"covered": email_papers, "denominator": denominator},
    }


# ------------------------------------------------------------------
# S8 — co-first / shared authorship flags
# ------------------------------------------------------------------


def equal_contrib_occurrences(papers: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """
    Section 7.8. Where the equal-contribution attribute appears, as a count.

    The position breakdown also fixes a real display defect: a flagged group at
    the end of the byline is shared *senior* authorship, which
    `analysis._author_role_label` currently renders as co-first.
    """
    categories = {"shared_first": 0, "shared_senior": 0, "shared_first_and_senior": 0, "other": 0}
    flagged = []
    for paper in papers:
        indices = paper["equal_contrib_indices"]
        if not indices:
            continue
        includes_first = 0 in indices
        includes_last = (paper["n_authors"] - 1) in indices
        if includes_first and includes_last:
            category = "shared_first_and_senior"
        elif includes_first:
            category = "shared_first"
        elif includes_last:
            category = "shared_senior"
        else:
            category = "other"
        categories[category] += 1
        flagged.append({
            "pmid": paper["pmid"],
            "year": paper["year"],
            "group_size": len(indices),
            "includes_first": includes_first,
            "includes_last": includes_last,
            "category": category,
        })

    return {
        "denominator": len(papers),
        "count": len(flagged),
        # Absence is not evidence of absence: a journal that marks co-first
        # authorship in a footnote and deposits nothing produces a zero here.
        "not_measurable": len(flagged) == 0,
        "papers": flagged,
        "categories": categories,
        "percentages": None,
        "suppressed": False,
    }


# ------------------------------------------------------------------
# S9 — records per calendar year
# ------------------------------------------------------------------


def records_per_year(
    papers: Sequence[dict[str, Any]],
    records_only: Sequence[dict[str, Any]],
    window_start_year: int,
    window_end_year: int,
) -> dict[str, Any]:
    """
    Section 7.9. Integer counts per year, with the censoring boundaries attached
    to each affected bin rather than deferred to a footnote (R4).
    """
    counts = Counter(paper["year"] for paper in list(papers) + list(records_only))
    years = []
    for year in range(window_start_year, window_end_year + 1):
        years.append({
            "year": year,
            "count": counts.get(year, 0),
            # The window opens and closes mid-year, so both end bins are partial.
            "partial": year in (window_start_year, window_end_year),
            # 18 months back from the window end lands inside the previous
            # calendar year, so both years carry the indexing-lag warning.
            "indexing_lag": year >= window_end_year - 1,
        })
    return {
        "denominator": len(papers) + len(records_only),
        "years": years,
        "suppressed": False,
    }


# ------------------------------------------------------------------
# S10 — team size per record
# ------------------------------------------------------------------


def team_size(
    papers: Sequence[dict[str, Any]],
    lead_stratum_by_pmid: dict[str, str],
) -> dict[str, Any]:
    """Section 7.10. Author count per record, after consortium entries are removed."""
    sizes = [paper["n_authors"] for paper in papers]
    enough = len(sizes) >= MIN_N_AGGREGATE

    subset_sizes = [
        paper["n_authors"]
        for paper in papers
        if lead_stratum_by_pmid.get(paper["pmid"]) in ("A", "B")
    ]
    subset_enough = len(subset_sizes) >= MIN_N_SUBSET_MEDIAN

    return {
        "denominator": len(sizes),
        "values": sorted(sizes),
        "median": median(sizes) if enough else None,
        "iqr": (quantile(sizes, 0.25), quantile(sizes, 0.75)) if enough else None,
        "min": min(sizes) if sizes else None,
        "max": max(sizes) if sizes else None,
        "large_team_count": sum(1 for size in sizes if size >= 20),
        "large_team_pmids": [p["pmid"] for p in papers if p["n_authors"] >= 20],
        "suppressed": not enough,
        "subset": {
            "denominator": len(subset_sizes),
            "values": sorted(subset_sizes),
            "median": median(subset_sizes) if subset_enough else None,
            "suppressed": not subset_enough,
        },
    }


# ------------------------------------------------------------------
# S11 — venue repetition
# ------------------------------------------------------------------


def venue_repetition(papers: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """
    Section 7.11. Exact-string journal frequency, verbatim and unnormalised.

    No impact factor, quartile, tier or partition is joined in, computed, stored
    or displayed — but that is now a supply fact rather than a rule. Each of
    those tables is a licensed product with no free, redistributable source, so
    this function has nothing to join. Whether a journal-level number should
    ever stand in for an individual paper is a separate argument, and one this
    function does not settle: it has no data to settle it with. What it returns
    is how often a string occurs, which is a fact about the corpus and not a
    statement about the papers inside any of these venues — CAV-20 carries that
    to the reader.
    """
    counts = Counter(paper["journal"].strip() for paper in papers if paper["journal"].strip())
    repeated = sorted(
        ((name, count) for name, count in counts.items() if count >= 2),
        key=lambda item: (-item[1], item[0]),
    )
    return {
        "denominator": len(papers),
        "repeated": repeated,
        "singleton_count": sum(1 for count in counts.values() if count == 1),
        "missing_journal_count": sum(1 for paper in papers if not paper["journal"].strip()),
        "suppressed": False,
    }


# ------------------------------------------------------------------
# S12 — affiliation strings, verbatim
# ------------------------------------------------------------------

AFFILIATION_MIN_PAPERS = 3


def affiliation_strings(
    papers: Sequence[dict[str, Any]],
    window_start_year: int,
    window_end_year: int,
) -> dict[str, Any]:
    """
    Section 7.12. Distinct affiliation strings on >= 3 papers, printed as
    recorded, plus per-year coverage.

    No count of distinct institutions is produced: three spellings of one
    university would be counted as three, which measures string variance rather
    than collaboration.
    """
    papers_per_string: dict[str, set[str]] = defaultdict(set)
    coverage: dict[int, dict[str, int]] = {
        year: {"covered": 0, "total": 0} for year in range(window_start_year, window_end_year + 1)
    }
    for paper in papers:
        year_bucket = coverage.setdefault(paper["year"], {"covered": 0, "total": 0})
        for author in paper["persons"]:
            affiliation = (author.get("affiliation") or "").strip()
            year_bucket["total"] += 1
            if affiliation:
                year_bucket["covered"] += 1
                papers_per_string[affiliation].add(paper["pmid"])

    strings = sorted(
        (
            (text, len(pmids))
            for text, pmids in papers_per_string.items()
            if len(pmids) >= AFFILIATION_MIN_PAPERS
        ),
        key=lambda item: (-item[1], item[0]),
    )
    return {
        "denominator": len(papers),
        "min_papers": AFFILIATION_MIN_PAPERS,
        "strings": strings,
        "coverage_by_year": [
            {"year": year, "covered": data["covered"], "total": data["total"]}
            for year, data in sorted(coverage.items())
        ],
        "suppressed": False,
    }


# ------------------------------------------------------------------
# S13 — titles grouped by year
# ------------------------------------------------------------------


def titles_by_year(papers: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """
    Section 7.13. Deliberately a non-metric: every title, verbatim, by year.

    It replaces the dropped topic-stability metric and hands the judgement to
    the reader, because research direction cannot be measured honestly from the
    fields this toolkit parses.
    """
    grouped: dict[int, list[dict[str, str]]] = defaultdict(list)
    for paper in papers:
        grouped[paper["year"]].append({"pmid": paper["pmid"], "title": paper["title"]})
    years = [
        {"year": year, "records": sorted(records, key=lambda r: r["pmid"])}
        for year, records in sorted(grouped.items())
    ]
    return {"denominator": len(papers), "years": years, "suppressed": False}
