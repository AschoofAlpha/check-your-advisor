#!/usr/bin/env python3
"""
`profile/cohesion.py`: does this corpus look like one person's network or several?

Gate G3 refuses a corpus harvested with no identity evidence at all. The failure
this module addresses is the one that passes G3: a corpus harvested with weak
evidence — an affiliation keyword that half a province matches — which renders a
complete, normal-looking report describing five different researchers. A real
run of `Zhu Guangwei` with `--affiliation-keyword "Fujian"` returned 28 records
covering gastrointestinal surgery, analytical chemistry, structural biology,
soil microbiology and machine learning, and scored 78.2 out of 100.

The signal is collaboration, not subject matter: remove the PI, who is on every
record by construction, and ask which records are still tied together by a
shared co-author. What is asserted here is the partition and its edges — the
PI's exclusion above all, since including them merges every corpus into one
cluster and silently turns the section into a constant.

What is deliberately *not* asserted is any threshold, because the module states
that it has none and the measurements do not support one. The last block tests
that absence: no score, no verdict, no ordering of people.

All data is synthetic. Fully offline.

Run: python tests/test_cohesion.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor.profile.cohesion import (  # noqa: E402
    CLUSTER_DETAIL_MIN,
    MIN_N_COHESION,
    coauthor_clusters,
)

_passed = 0
_failed = 0

PI = "Zhu Guangwei"


def check(label: str, actual, expected) -> None:
    global _passed, _failed
    ok = actual == expected
    if ok:
        _passed += 1
    else:
        _failed += 1
    detail = "" if ok else f"  (expected {expected!r}, got {actual!r})"
    print(f"  {'[PASS]' if ok else '[FAIL]'} {label}{detail}")


def check_true(label: str, cond) -> None:
    check(label, bool(cond), True)


def paper(pmid: int, names: list[str], journal: str = "J", year: int = 2024,
          pi_at: int | None = None) -> dict:
    """One prepared record. `pi_at` is the PI's index in `persons`, as
    `roles.prepare_paper` sets it."""
    return {
        "pmid": str(pmid),
        "journal": journal,
        "year": year,
        "persons": [{"name": n} for n in names],
        "pi_person_index": pi_at,
    }


# ======================================================================
# The partition
# ======================================================================

print("\n--- partition ---")

# Two groups that share nobody. The PI is on every record and is excluded, so
# they must not merge.
TWO_GROUPS = [
    paper(1, ["Alpha One", PI], "Oncogene", 2023, pi_at=1),
    paper(2, ["Alpha One", "Alpha Two", PI], "Cancer research", 2024, pi_at=2),
    paper(3, ["Alpha Two", PI], "Cancer letters", 2024, pi_at=1),
    paper(4, ["Beta One", PI], "Analytica chimica acta", 2023, pi_at=1),
    paper(5, ["Beta One", "Beta Two", PI], "Chemical communications", 2024, pi_at=2),
]
two = coauthor_clusters(TWO_GROUPS, PI)
check("two disjoint co-author groups stay two clusters", two["n_clusters"], 2)
check("every record is placed", sum(c["size"] for c in two["clusters"]), 5)
check("the denominator is the corpus, not the cluster count", two["denominator"], 5)
check("clusters are ordered largest first",
      [c["size"] for c in two["clusters"]], [3, 2])

# The PI is the only thing linking the two groups. If the PI were counted as a
# co-author this would collapse to one cluster and the section would be a
# constant that always says "one person".
check("the PI does not link records", two["n_clusters"], 2)
_with_pi_as_person = coauthor_clusters(TWO_GROUPS, "")
check("...and naming no PI is what would merge them, via pi_person_index only",
      _with_pi_as_person["n_clusters"], 2)

# One shared person is enough to merge, and merging is transitive.
BRIDGED = TWO_GROUPS + [paper(6, ["Alpha One", "Beta One", PI], "Bridge J", 2025, pi_at=2)]
check("one shared co-author merges two groups", coauthor_clusters(BRIDGED, PI)["n_clusters"], 1)

CHAIN = [
    paper(1, ["A", "B", PI], pi_at=2),
    paper(2, ["B", "C", PI], pi_at=2),
    paper(3, ["C", "D", PI], pi_at=2),
    paper(4, ["X", "Y", PI], pi_at=2),
    paper(5, ["Z", PI], pi_at=1),
]
chain = coauthor_clusters(CHAIN, PI)
check("linking is transitive: A-B-C-D is one cluster", chain["clusters"][0]["size"], 3)
check("...and unrelated records stay out of it", chain["n_clusters"], 3)


# ======================================================================
# What each cluster reports
# ======================================================================

print("\n--- cluster contents ---")

first = two["clusters"][0]
check("a cluster carries its record ids", sorted(first["pmids"]), ["1", "2", "3"])
check("a cluster carries its year range", first["year_range"], (2023, 2024))
check("journals are counted, not just listed",
      [j["journal"] for j in first["journals"]],
      ["Cancer letters", "Cancer research", "Oncogene"])

# `recurring_people` is what makes the section readable: the names that made
# this a cluster. Someone on one record explains nothing and is left out.
DUPLICATED = [
    paper(1, ["Recur Person", "Once A", PI], pi_at=2),
    paper(2, ["Recur Person", "Once B", PI], pi_at=2),
    paper(3, ["Recur Person", "Once C", PI], pi_at=2),
    paper(4, ["Other One", PI], pi_at=1),
    paper(5, ["Other Two", PI], pi_at=1),
]
dup = coauthor_clusters(DUPLICATED, PI)
big = dup["clusters"][0]
check("only people on 2+ records in the cluster are named as holding it together",
      [p["name"] for p in big["recurring_people"]], ["Recur Person"])
check("...with the count they were seen on", big["recurring_people"][0]["n_records"], 3)

# Records with no located PI contribute all their authors rather than being
# dropped: dropping them would shrink the denominator this section is about.
NO_PI_INDEX = [
    paper(1, ["Solo A", "Shared"], pi_at=None),
    paper(2, ["Solo B", "Shared"], pi_at=None),
    paper(3, ["Solo C"], pi_at=None),
    paper(4, ["Solo D"], pi_at=None),
    paper(5, ["Solo E"], pi_at=None),
]
no_pi = coauthor_clusters(NO_PI_INDEX, PI)
check("a record with no located PI is still partitioned, not dropped",
      no_pi["denominator"], 5)
check("...and its authors still link it", no_pi["clusters"][0]["size"], 2)

# The PI name is removed even when pi_person_index missed them, which is the
# case this argument exists for.
PI_UNLOCATED = [
    paper(1, ["Solo A", PI], pi_at=None),
    paper(2, ["Solo B", PI], pi_at=None),
    paper(3, ["Solo C", PI], pi_at=None),
    paper(4, ["Solo D", PI], pi_at=None),
    paper(5, ["Solo E", PI], pi_at=None),
]
check("the PI name is excluded even when their index was not resolved",
      coauthor_clusters(PI_UNLOCATED, PI)["n_clusters"], 5)
check("...and without the name they would all merge",
      coauthor_clusters(PI_UNLOCATED, "")["n_clusters"], 1)


# ======================================================================
# Singletons and the floor
# ======================================================================

print("\n--- singletons and the floor ---")

singles = coauthor_clusters([paper(i, [f"P{i}", PI], pi_at=1) for i in range(1, 8)], PI)
check("records sharing nobody are one cluster each", singles["n_clusters"], 7)
check("single-record clusters are counted", singles["singleton_clusters"], 7)
check("...and marked as not worth listing in full",
      [c["detailed"] for c in singles["clusters"]], [False] * 7)
check("a cluster at the detail floor is listed in full",
      coauthor_clusters(TWO_GROUPS, PI)["clusters"][1]["detailed"], True)
check("the detail floor is what the module says it is", CLUSTER_DETAIL_MIN, 2)

# Below the floor the partition says nothing, so it says so rather than
# printing a cluster count that reads as a finding.
small = coauthor_clusters([paper(i, [f"P{i}", PI], pi_at=1) for i in range(1, MIN_N_COHESION)], PI)
check("below the floor the section suppresses", small["suppressed"], True)
check("...and reports no cluster count at all", small["n_clusters"], None)
check("...but still states the denominator it declined to partition",
      small["denominator"], MIN_N_COHESION - 1)
check("...and the floor it was measured against", small["min_n"], MIN_N_COHESION)
check("at the floor it partitions",
      coauthor_clusters([paper(i, [f"P{i}", PI], pi_at=1)
                         for i in range(1, MIN_N_COHESION + 1)], PI)["suppressed"], False)
check("an empty corpus suppresses rather than raising",
      coauthor_clusters([], PI)["suppressed"], True)


# ======================================================================
# The line that does not move: this section decides nothing
# ======================================================================

print("\n--- no verdict ---")

# The module documents that it applies no threshold, because three real corpora
# — one known to hold five researchers, two better filtered — split into 15, 16
# and 10 clusters and the count separated none of them. If a score, a verdict or
# a flag ever appears in this return value, that argument has been abandoned
# without the docstring being updated.
_keys = set(two)
for _forbidden in ("score", "verdict", "flag", "suspicious", "threshold",
                   "likely_multiple", "is_one_person", "confidence", "rank",
                   "percentile", "grade", "stars"):
    check(f"the partition emits no '{_forbidden}'", _forbidden in _keys, False)

# Ordering clusters by size is for readability and is stated on the page. The
# people inside a cluster are never ordered by anything about them.
check("people inside a cluster are ordered by record count then name, not ranked",
      [p["name"] for p in coauthor_clusters(
          [paper(1, ["Zed", "Ann", PI], pi_at=2),
           paper(2, ["Zed", "Ann", PI], pi_at=2),
           paper(3, ["Zed", PI], pi_at=1),
           paper(4, ["Q", PI], pi_at=1),
           paper(5, ["R", PI], pi_at=1)], PI)["clusters"][0]["recurring_people"]],
      ["Zed", "Ann"])

check("the return value is JSON-serialisable",
      isinstance(__import__("json").dumps(two, default=str), str), True)


print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(1 if _failed else 0)
