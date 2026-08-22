#!/usr/bin/env python3
"""
`profile/impact.py`: the h-index, the i10-index, and what partial coverage does
to both.

The h-index used to be on the refused list. It is computed and written to disk
now, so the interesting questions moved: not "may we", but "what exactly is this
number a statement about". Two answers are asserted here.

  1. It is bounded by the corpus. `h_index` cannot exceed `len(values)`, so an
     h computed over one harvest of one date range is a property of that
     harvest, never a career figure.
  2. Under partial coverage it is a *floor*. Every citation record the lookup
     failed to match can only push h and i10 up, never down — so a partially
     covered h is a lower bound and is marked as one. The median is deliberately
     not a floor: a missing record moves it in an unknown direction, and the
     fixture below shows it moving both ways from the same corpus, which is the
     whole reason the two are flagged differently.

What is still not produced is an ordering. `values` is a bare list of counts
with no PMIDs attached, because pairing a descending count with the paper it
belongs to is a league table of papers.

Pure computation: no file access, no network, standard library only.

Run: python tests/test_impact.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor.profile import impact  # noqa: E402
from check_your_advisor.profile.impact import citation_metrics, h_index, i10_index  # noqa: E402
from check_your_advisor.profile.metrics import MIN_N_AGGREGATE  # noqa: E402

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


def papers(*specs) -> list[dict]:
    """`specs` are (pmid, doi) pairs, or bare pmids."""
    out = []
    for spec in specs:
        pmid, doi = spec if isinstance(spec, tuple) else (spec, "")
        out.append({"pmid": str(pmid), "doi": doi, "title": f"Paper {pmid}"})
    return out


def records(*specs, source="openalex") -> list[dict]:
    """`specs` are (pmid, count) pairs, or (pmid, doi, count) triples."""
    out = []
    for spec in specs:
        if len(spec) == 2:
            pmid, count = spec
            doi = ""
        else:
            pmid, doi, count = spec
        out.append({"pmid": str(pmid), "doi": doi, "citation_count": count,
                    "source": source, "fetched_at": "2026-01-01T09:30:00"})
    return out


def payload(record_list, generated_at="2026-01-01T09:30:00"):
    return {"generated_at": generated_at,
            "source_papers_json": "papers_20260101_000000.json",
            "denominator": {"papers_total": len(record_list),
                            "papers_with_citations": len(record_list)},
            "records": record_list}


# ============================================================
# h_index as a pure function
# ============================================================

print("h_index")

check("no papers is h=0", h_index([]), 0)
check("one uncited paper is h=0", h_index([0]), 0)
check("one paper cited once is h=1", h_index([1]), 1)
check("one paper cited a hundred times is still h=1 — h is capped by the count",
      h_index([100]), 1)
check("the textbook example", h_index([10, 8, 5, 4, 3]), 4)
check("order of the input does not matter", h_index([3, 10, 4, 8, 5]), 4)
check("five papers cited five times each is h=5", h_index([5, 5, 5, 5, 5]), 5)
check("five papers cited once each is h=1", h_index([1, 1, 1, 1, 1]), 1)
check("three papers cited three times each is h=3", h_index([3, 3, 3]), 3)
check("a long tail of zeroes does not raise h", h_index([3, 3, 3, 0, 0, 0, 0]), 3)
check("h stops at the first position that fails, not at the last that passes",
      h_index([9, 9, 1, 1, 1, 1, 1, 1, 1, 1]), 2)
check("h can never exceed the number of papers", h_index([100, 100]), 2)
check_true("h is bounded by len(values) for every prefix of a real corpus",
           all(h_index(v) <= len(v)
               for v in ([], [0], [7], [7, 7], [7, 7, 7], [50, 40, 30, 20, 10])))


print("\ni10_index")

check("no papers is i10=0", i10_index([]), 0)
check("nine citations is under the line", i10_index([9]), 0)
check("exactly ten citations is on it — the threshold is inclusive", i10_index([10]), 1)
check("eleven is over it", i10_index([11]), 1)
check("it counts papers, it does not sum citations", i10_index([10, 10, 10]), 3)
check("uncited papers are counted, as zero, not dropped", i10_index([50, 0, 0, 0]), 1)
check("the threshold is a parameter, and it is Google Scholar's ten by default",
      (i10_index([5, 5, 5], threshold=5), impact.I10_THRESHOLD), (3, 10))
check_true("i10 is bounded by the number of papers, like h",
           all(i10_index(v) <= len(v) for v in ([], [0], [99], [99, 99, 0])))


# ============================================================
# citation_metrics: the contract shape
# ============================================================

print("\ncitation_metrics: the contract shape")

CORPUS = papers(1, 2, 3, 4, 5, 6, 7, 8)
FULL = records((1, 50), (2, 40), (3, 30), (4, 20), (5, 10), (6, 0), (7, 0), (8, 0))

full = citation_metrics(CORPUS, payload(FULL))

for key in ("denominator", "covered", "total_citations", "h_index", "i10_index",
            "median_citations", "values", "suppressed"):
    check_true(f"the result carries `{key}`", key in full)

check("denominator is the corpus, not the lookup", full["denominator"], 8)
check("covered is what the lookup matched", full["covered"], 8)
check("total_citations sums the observed values", full["total_citations"], 150)
check("h_index over the whole corpus", full["h_index"], 5)
check("i10_index over the whole corpus", full["i10_index"], 5)
check("median_citations over the whole corpus", full["median_citations"], 15.0)
check("values are the counts, ascending", full["values"], [0, 0, 0, 10, 20, 30, 40, 50])
check_false("nothing is suppressed at full coverage", full["suppressed"])
check_false("...and nothing is a floor", full["lower_bound"])
check_false("...and it is computable", full["not_computable"])

# Pairing a descending count with the paper it belongs to would be a league table
# of papers, which is the one ordering this module does not produce.
check_true("values carry counts only, never the PMIDs they belong to",
           all(isinstance(v, int) for v in full["values"]))
check("the result exposes no rank, percentile, grade or tier field",
      [key for key in full
       if any(word in key for word in ("rank", "percentile", "quantile", "grade",
                                       "tier", "star", "rating", "top", "best"))], [])
check("the fetch date is echoed back, because a count without its date is not a fact",
      full["generated_at"], "2026-01-01T09:30:00")
check("the corpus the counts were taken against is echoed too",
      full["source_papers_json"], "papers_20260101_000000.json")
check("a bare record list reports no date rather than inventing one",
      citation_metrics(CORPUS, FULL)["generated_at"], None)
check("no citation payload at all is handled",
      (citation_metrics(CORPUS, None)["covered"],
       citation_metrics(CORPUS, None)["not_computable"]), (0, True))


# ============================================================
# Partial coverage: h and i10 are floors, the median is not
# ============================================================

print("\npartial coverage: a floor, and a thing that is not a floor")

# Both subsets are five papers out of the same eight, so both clear the n=5
# aggregate floor and differ only in which half of the corpus the lookup matched.
TOP_HALF = records((1, 50), (2, 40), (3, 30), (4, 20), (5, 10))
BOTTOM_HALF = records((4, 20), (5, 10), (6, 0), (7, 0), (8, 0))

top = citation_metrics(CORPUS, payload(TOP_HALF))
bottom = citation_metrics(CORPUS, payload(BOTTOM_HALF))

check("a partially covered corpus keeps the corpus denominator", top["denominator"], 8)
check("...and reports how much of it was matched", top["covered"], 5)
check_true("...and says the aggregates are floors", top["lower_bound"])
check_true("...on both subsets", bottom["lower_bound"])

check("h from the well-cited half", top["h_index"], 5)
check("h from the uncited half", bottom["h_index"], 2)
check_true("h under partial coverage never exceeds h under full coverage",
           top["h_index"] <= full["h_index"] and bottom["h_index"] <= full["h_index"])
check_true("i10 under partial coverage never exceeds i10 under full coverage",
           top["i10_index"] <= full["i10_index"] and bottom["i10_index"] <= full["i10_index"])
check_true("total_citations under partial coverage never exceeds the full total",
           top["total_citations"] <= full["total_citations"])

# The point of separating the two flags. Completing the lookup moved this median
# down from one subset and up from the other, so a partially covered median is
# not a lower bound on anything and must not be read as one.
check("the median from the well-cited half", top["median_citations"], 30.0)
check("the median from the uncited half", bottom["median_citations"], 0.0)
check_true("completing coverage moved the median down from one subset",
           full["median_citations"] < top["median_citations"])
check_true("...and up from the other",
           full["median_citations"] > bottom["median_citations"])

check("the coverage gap is named rather than implied",
      sorted(top["uncovered_pmids"]), ["6", "7", "8"])
check("the record ledger accounts for every fetched record",
      top["record_counts"], {"total": 5, "matched": 5, "unmatched": 0,
                             "duplicate": 0, "invalid": 0})


# ============================================================
# Suppression: R2, unchanged from metrics.py
# ============================================================

print("\nsuppression below the aggregate floor")

check("the floor this module inherits", MIN_N_AGGREGATE, 5)

small = citation_metrics(papers(1, 2, 3, 4), payload(records((1, 9), (2, 9), (3, 9), (4, 9))))
check_true("four covered papers suppress the aggregates", small["suppressed"])
check("h_index is None, not zero and not a small number", small["h_index"], None)
check("i10_index is None", small["i10_index"], None)
check("median_citations is None", small["median_citations"], None)
check("iqr is None", small["iqr"], None)
# R2: the aggregate goes, the underlying values stay, so a reader can see the
# four numbers and decide for themselves rather than being handed nothing.
check("the underlying values stand in place of the aggregate",
      small["values"], [9, 9, 9, 9])
check("the denominator survives suppression", small["denominator"], 4)
check("total_citations is not an aggregate under this rule and survives",
      small["total_citations"], 36)

edge = citation_metrics(papers(1, 2, 3, 4, 5),
                        payload(records((1, 9), (2, 9), (3, 9), (4, 9), (5, 9))))
check_false("five covered papers is exactly enough", edge["suppressed"])
check("...and the h-index renders", edge["h_index"], 5)

empty = citation_metrics(papers(1, 2, 3, 4, 5, 6), payload([]))
check_true("a lookup that matched nothing is not computable", empty["not_computable"])
check_true("...and is suppressed", empty["suppressed"])
check("...and says so with zero, not with a fabricated total",
      (empty["covered"], empty["total_citations"], empty["values"]), (0, 0, []))
check("an empty corpus is handled", citation_metrics([], payload([]))["denominator"], 0)


# ============================================================
# The join
# ============================================================

print("\njoining counts onto the corpus")

by_doi = citation_metrics(
    papers(("", "10.1/a"), ("", "10.1/b"), ("", "10.1/c"), ("", "10.1/d"), ("", "10.1/e")),
    payload(records(("", "10.1/a", 1), ("", "10.1/b", 2), ("", "10.1/c", 3),
                    ("", "10.1/d", 4), ("", "10.1/e", 5))))
check("papers with no PMID join by DOI", by_doi["covered"], 5)

# The three citation APIs hand DOIs back in three different dresses. Normalising
# only one side turns a formatting artefact into a coverage gap.
dressed = citation_metrics(
    papers(("", "10.1/a"), ("", "10.1/B"), ("", "10.1/c"), ("", "10.1/d"), ("", "10.1/e")),
    payload(records(("", "https://doi.org/10.1/a", 1), ("", "10.1/b", 2),
                    ("", "doi:10.1/c", 3), ("", "http://dx.doi.org/10.1/d", 4),
                    ("", "10.1/E", 5))))
check("resolver prefixes and case are normalised on both sides of the join",
      dressed["covered"], 5)

mixed = citation_metrics(
    papers((1, "10.1/a"), (2, ""), (3, ""), (4, ""), (5, "")),
    payload(records((1, 99), (2, 1), (3, 1), (4, 1), (5, 1))))
check("PMID is tried before DOI", mixed["covered"], 5)

# A corpus that was not deduplicated must not have one record's citations counted
# twice. The failure mode is an undercount, which is the direction lower_bound
# already warns about.
duped = citation_metrics(papers(1, 1, 2, 3, 4, 5),
                         payload(records((1, 10), (2, 10), (3, 10), (4, 10), (5, 10))))
check("a repeated paper consumes its record once", duped["covered"], 5)
check("...and the duplicate stays uncovered rather than double-counting",
      duped["total_citations"], 50)
check("...and appears in the coverage gap", duped["uncovered_pmids"], ["1"])

unmatched = citation_metrics(papers(1, 2, 3, 4, 5),
                             payload(records((1, 5), (2, 5), (3, 5), (4, 5), (5, 5),
                                             (99, 5000))))
check("a fetched record that matches no paper is not folded into the totals",
      unmatched["total_citations"], 25)
check("...and is counted as unmatched", unmatched["record_counts"]["unmatched"], 1)


print("\nunreadable counts are rejected, never coerced")

# A zero here would be indistinguishable from an uncited paper and would drag the
# median down, so an unreadable count is tallied rather than defaulted.
bad = citation_metrics(
    papers(1, 2, 3, 4, 5, 6, 7),
    payload(records((1, 10), (2, 10), (3, 10), (4, 10), (5, 10),
                    (6, None), (7, "not a number"))))
check("a null and an unparsable count are both refused", bad["covered"], 5)
check("...and are tallied as invalid rather than as zeroes",
      bad["record_counts"]["invalid"], 2)
check("...so they cannot move the median", bad["median_citations"], 10.0)

check("a negative count is refused",
      citation_metrics(papers(1), payload(records((1, -3))))["covered"], 0)
# True is an int in Python; read as one citation it would be invisible.
check("a boolean is refused",
      citation_metrics(papers(1), payload(records((1, True))))["covered"], 0)
check("zero is a real count and is covered",
      citation_metrics(papers(1), payload(records((1, 0))))["covered"], 1)
check("an integral float is accepted",
      citation_metrics(papers(1), payload(records((1, 7.0))))["values"], [7])
check("a fractional float is not",
      citation_metrics(papers(1), payload(records((1, 7.5))))["values"], [])
check("a digit string is accepted",
      citation_metrics(papers(1), payload(records((1, "7"))))["values"], [7])
check("a record with neither identifier is invalid, whatever its count",
      citation_metrics(papers(1), payload(records(("", "", 5))))["record_counts"]["invalid"], 1)
check("junk in the records list is skipped rather than crashing the join",
      citation_metrics(papers(1), {"records": ["not a record", 7, None]})["covered"], 0)


print("\nwhich citation graph the number came from")

# An h-index assembled from three providers sits on three citation graphs with
# three coverage profiles and is not comparable with an h from any one of them.
one_source = citation_metrics(CORPUS, payload(FULL, generated_at="2026-01-01T09:30:00"))
check("a single-source lookup names it", one_source["sources"], {"openalex": 8})
check_false("...and is not flagged as mixed", one_source["mixed_sources"])

blended = citation_metrics(
    papers(1, 2, 3, 4, 5),
    payload(records((1, 5), (2, 5), source="openalex")
            + records((3, 5), (4, 5), source="semantic_scholar")
            + records((5, 5), source="europe_pmc")))
check("a blended lookup reports the mixture",
      blended["sources"], {"europe_pmc": 1, "openalex": 2, "semantic_scholar": 2})
check_true("...and flags it", blended["mixed_sources"])


print("\nthe boundary this module keeps")

check("the module exposes no ranking helper",
      [name for name in dir(impact)
       if any(word in name.lower()
              for word in ("rank", "percentile", "quantile_position", "grade", "tier",
                           "star", "rating"))], [])
check("the module exposes no impact-factor or quartile helper",
      [name for name in dir(impact)
       if any(word in name.lower()
              for word in ("impact_factor", "quartile", "jcr", "cas_partition"))], [])
# Journal-level tables are absent for a supply reason, not a verdict, and the
# module has to be readable as saying that.
# Whitespace is collapsed first: the sentence is wrapped across lines in the
# module docstring and this assertion is about the wording, not the wrapping.
prose = " ".join(open(impact.__file__, encoding="utf-8").read().split())
check_true("...and the reason given for their absence is availability, not principle",
           "no free, licence-clean source" in prose
           and "*not obtainable* here rather than *refused* here" in prose)


print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(1 if _failed else 0)
