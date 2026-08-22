#!/usr/bin/env python3
"""
Regression tests for PubMed search-query construction.

These exist because of a real recall bug: the original query was
`"{author}"[Author]`, always quoted. Quoting suppresses PubMed's automatic
term expansion, and the "Surname Forename" index only exists for records where
the full given name was deposited. Measured against the live API:

    "Stockwell Brent"[Author]     ->     6 records
    "Stockwell B*"[Author]        ->   253 records   (same person)
    "Stockwell"[Author]  (quoted) ->     0 records
    Stockwell[Author]  (unquoted) ->  1803 records

So the tool was silently returning ~2% of a Western-named author's papers.
Unit tests could not catch it; only a live run did. These tests lock in the
query shape so it cannot silently regress.

Run: python tests/test_search_query.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor.pubmed_api import author_query_variants, build_search_query  # noqa: E402

_passed = 0
_failed = 0


def check(label: str, actual, expected) -> None:
    global _passed, _failed
    ok = actual == expected
    if ok:
        _passed += 1
    else:
        _failed += 1
    detail = "" if ok else f"  (expected {expected!r}, got {actual!r})"
    print(f"  {'[PASS]' if ok else '[FAIL]'} {label}{detail}")


def check_true(label: str, actual) -> None:
    check(label, bool(actual), True)


print("\n--- author query variants ---")

v = author_query_variants("Stockwell Brent")
check("two variants generated", len(v), 2)
check("exact full-name form kept", v[0], '"Stockwell Brent"[Author]')
check("wildcard initials form added", v[1], '"Stockwell B*"[Author]')

# The wildcard is what recovers the lost records; without the star, PubMed
# matches only the literal "Stockwell B" index entry and misses "Stockwell BR".
check_true("wildcard star present", v[1].endswith('*"[Author]'))

v = author_query_variants("Chen Xiuying")
check("pinyin name wildcards on first initial", v[1], '"Chen X*"[Author]')

v = author_query_variants("Li Wen Hao")
check("multi-token given name uses first initial only", v[1], '"Li W*"[Author]')

v = author_query_variants("Stockwell")
check("surname only produces one variant", len(v), 1)
check("surname only is unquoted so PubMed expands it", v[0], "Stockwell[Author]")

check("empty author yields nothing", author_query_variants(""), [])
check("whitespace author yields nothing", author_query_variants("   "), [])


print("\n--- full query assembly ---")

q = build_search_query("Stockwell Brent")
check_true("name-only query has no AND", " AND " not in q)
check_true("name-only query ORs the variants", " OR " in q)

q = build_search_query("Stockwell Brent", orcid="0000-0002-1825-0097")
check_true("orcid term present", "0000-0002-1825-0097[auid]" in q)
check_true("orcid is ORed, not ANDed", q.startswith("0000-0002-1825-0097[auid] OR "))

q = build_search_query("Chen Xiuying", affiliation_keywords=["Example University", "Example Univ"])
check_true("affiliation clause present", '"Example University"[Affiliation]' in q)
check_true("both affiliation keywords present", '"Example Univ"[Affiliation]' in q)
check_true("affiliation is ANDed with the name block", ") AND (" in q)

# The important structural property: ORCID must stay outside the affiliation
# AND, otherwise a paper matched by ORCID whose affiliation string is not
# indexed by PubMed would be dropped.
q = build_search_query(
    "Chen Xiuying",
    orcid="0000-0002-1825-0097",
    affiliation_keywords=["Example University"],
)
orcid_pos = q.index("0000-0002-1825-0097[auid]")
and_pos = q.index(") AND (")
check_true("orcid appears before the affiliation-restricted block", orcid_pos < and_pos)
check_true("orcid is not inside the AND group", q.startswith("0000-0002-1825-0097[auid] OR ("))

check("empty everything yields empty query", build_search_query("", orcid=""), "")
q = build_search_query("", orcid="0000-0002-1825-0097")
check("orcid alone still queryable", q, "0000-0002-1825-0097[auid]")


print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(0 if _failed == 0 else 1)
