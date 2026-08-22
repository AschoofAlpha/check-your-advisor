"""
Citation-side metrics for the advisor profile — the companion to `metrics.py`.

Pure computation: plain dicts in, plain dicts out, no file access, no network.
Counts are fetched elsewhere and arrive here already resolved, because a metric
function that could reach the network could not be checked against a fixture.

What changed, and what did not
------------------------------
Citation counts and the h-index are computed here and are written to disk.
Ordering is not. This module returns absolute values with the denominator each
was computed over, and nothing that places one corpus, one person or one paper
above another: no sort key over entities, no percentile, no grade, no tier, no
"top N". A value may be printed; a position may not.

Journal Impact Factor, JCR quartile and CAS partition are absent for a
different reason, and the difference is one the reader has to be able to see:
there is no free, licence-clean source for those tables, so they are *not
obtainable* here rather than *refused* here. Nothing in this module's design
would be violated by a journal-level number; the data simply cannot be had
legitimately. That is a supply problem, not a principle.

Two properties of a citation count the return value has to carry
----------------------------------------------------------------
- It is a measurement with a date. Counts move every week, so the payload's
  `generated_at` is echoed back and no caller has an excuse for printing a
  citation figure without the day it was taken. This is also why the counts
  live in their own file and are never merged back into `papers_*.json`: a
  corpus of bylines does not expire, a citation count does.
- It is incomplete. Coverage is whatever the citation source managed to match,
  so `covered` travels beside `denominator`, and `lower_bound` marks the case
  where `total_citations`, `h_index` and `i10_index` are floors rather than
  values. The median is not a floor — partial coverage moves it in an unknown
  direction — which is exactly why it is reported with its own denominator
  instead of as a summary of the corpus.

The two conventions from `metrics.py` hold unchanged: every result carries its
denominator (R1), and below the minimum sample size the aggregate is None and
the underlying values are returned in its place (R2).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from .metrics import MIN_N_AGGREGATE, median, percent, quantile

# Google Scholar's threshold, reused unchanged so the number means what a reader
# already takes it to mean. It counts papers, it does not grade them.
I10_THRESHOLD = 10

# DOIs arrive from three different citation APIs in three different dresses.
# Normalising both sides of the join is the difference between a coverage gap
# that is real and one that is a string-formatting artefact.
_DOI_PREFIXES = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "doi:",
)


def _norm_pmid(value: Any) -> str:
    return str(value or "").strip()


def _norm_doi(value: Any) -> str:
    text = str(value or "").strip().lower()
    for prefix in _DOI_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    return text.strip()


def _as_count(value: Any) -> int | None:
    """
    A citation count, or None if the field cannot be read as one.

    Rejection is deliberate and visible: an unreadable count is tallied under
    `record_counts["invalid"]` rather than coerced to zero, because a zero here
    is indistinguishable from an uncited paper and would silently drag the
    median down. `bool` is excluded before `int` because `True` is an `int` in
    Python and would otherwise be read as one citation.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 and value.is_integer() else None
    text = str(value).strip()
    return int(text) if text.isdigit() else None


def _payload(
    citations: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None,
) -> tuple[list[Mapping[str, Any]], str | None, str | None]:
    """
    Accept either the whole `citations_<timestamp>.json` object or its records.

    The full object is the normal case and is the only one that carries
    `generated_at`; a bare list is accepted so a test fixture does not have to
    fake a file header, and reports `generated_at` as None so the caller cannot
    mistake an undated list for a dated fetch.
    """
    if citations is None:
        return [], None, None
    if isinstance(citations, Mapping):
        raw = citations.get("records") or []
        generated_at = citations.get("generated_at") or None
        source_papers_json = citations.get("source_papers_json") or None
    else:
        raw = citations
        generated_at = None
        source_papers_json = None
    return [item for item in raw if isinstance(item, Mapping)], generated_at, source_papers_json


def h_index(values: Sequence[int]) -> int:
    """
    The standard definition: the largest h such that h papers have >= h citations.

    It is bounded above by `len(values)`, so on a partially covered corpus it is
    a lower bound on the person's h-index and never an estimate of it. It is
    also bounded by the corpus window: a career metric computed over one
    harvest of one date range is a property of that harvest.
    """
    ordered = sorted(values, reverse=True)
    result = 0
    for position, count in enumerate(ordered, start=1):
        if count >= position:
            result = position
        else:
            break
    return result


def i10_index(values: Sequence[int], threshold: int = I10_THRESHOLD) -> int:
    """Papers with at least `threshold` citations. A count, with the same ceiling."""
    return sum(1 for count in values if count >= threshold)


def citation_metrics(
    papers: Sequence[dict[str, Any]],
    citations: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """
    Join fetched citation counts onto the corpus and summarise what matched.

    Matching is by PMID first, then by normalised DOI. Each fetched record is
    consumed at most once: if a second paper resolves to a record already used
    — which means the corpus was not deduplicated — that paper stays uncovered
    instead of counting its citations twice. The failure mode is an undercount,
    which is the direction `lower_bound` already warns about.

    `values` is the sorted list of counts and carries no PMIDs. That is not an
    oversight: pairing a descending count with the paper it belongs to is a
    within-corpus league table of papers, which is the one thing this module
    does not produce. The corpus-level numbers are here; the ordering of
    individual works is not offered.

    `sources` is returned per source name because an h-index computed across a
    mixture of OpenAlex, Semantic Scholar and Europe PMC counts is built on
    three different citation graphs with three different coverage profiles, and
    is not comparable with an h-index from any one of them.
    """
    records, generated_at, source_papers_json = _payload(citations)

    # Index the fetched records. First writer wins per identifier, so a repeated
    # PMID cannot overwrite the count that was already joined.
    by_pmid: dict[str, int] = {}
    by_doi: dict[str, int] = {}
    counts: list[int] = []
    sources: list[str] = []
    invalid = 0
    duplicate = 0

    for record in records:
        count = _as_count(record.get("citation_count"))
        pmid = _norm_pmid(record.get("pmid"))
        doi = _norm_doi(record.get("doi"))
        if count is None or not (pmid or doi):
            invalid += 1
            continue
        position = len(counts)
        claimed = False
        if pmid and pmid not in by_pmid:
            by_pmid[pmid] = position
            claimed = True
        if doi and doi not in by_doi:
            by_doi[doi] = position
            claimed = True
        if not claimed:
            duplicate += 1
            continue
        counts.append(count)
        sources.append(str(record.get("source") or "unknown"))

    # Join onto the corpus.
    used: set[int] = set()
    values: list[int] = []
    matched_sources: Counter[str] = Counter()
    uncovered_pmids: list[str] = []

    for paper in papers:
        pmid = _norm_pmid(paper.get("pmid"))
        doi = _norm_doi(paper.get("doi"))
        position = by_pmid.get(pmid) if pmid else None
        if position is None and doi:
            position = by_doi.get(doi)
        if position is None or position in used:
            uncovered_pmids.append(pmid)
            continue
        used.add(position)
        values.append(counts[position])
        matched_sources[sources[position]] += 1

    denominator = len(papers)
    covered = len(values)
    enough = covered >= MIN_N_AGGREGATE

    return {
        # R1: the population every number below was computed over.
        "denominator": denominator,
        "covered": covered,
        # Suppressed below n=20 by the shared rule, so a coverage figure from a
        # handful of papers is not rendered as a percentage.
        "coverage_percent": percent(covered, denominator),
        # A sum of the observed values, and only of those. With covered == 0 it
        # is 0 because nothing was seen, which is what `not_computable` is for.
        "total_citations": sum(values),
        # R2: below the floor these are None and `values` stands in their place.
        "h_index": h_index(values) if enough else None,
        "i10_index": i10_index(values) if enough else None,
        "median_citations": median(values) if enough else None,
        # Citation distributions are heavy-tailed, so a median without a spread
        # beside it hides the shape that makes the median unrepresentative.
        "iqr": (quantile(values, 0.25), quantile(values, 0.75)) if enough else None,
        # Bare counts, ascending, deliberately not tied back to any paper.
        "values": sorted(values),
        "suppressed": not enough,
        "not_computable": covered == 0,
        # True when coverage is partial: total_citations, h_index and i10_index
        # are then floors. median_citations is not — it moves in an unknown
        # direction — and must not be read as a floor.
        "lower_bound": covered < denominator,
        # A citation count without its date is not a fact about anything.
        "generated_at": generated_at,
        "source_papers_json": source_papers_json,
        "sources": dict(sorted(matched_sources.items())),
        "mixed_sources": len(matched_sources) > 1,
        "record_counts": {
            "total": len(records),
            "matched": covered,
            "unmatched": len(counts) - len(used),
            "duplicate": duplicate,
            "invalid": invalid,
        },
        # The coverage gap, named, so it can be printed instead of implied.
        "uncovered_pmids": uncovered_pmids,
    }
