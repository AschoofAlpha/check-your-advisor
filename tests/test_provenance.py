#!/usr/bin/env python3
"""
Tests for search provenance carried through papers_*.json.

Why this exists: `search_pubmed` knew the esearch term, the hit count and
whether the result was cut off at `retmax`, but all of it stayed inside the
function and went out with the log. `papers_*.json` was a bare list, so the
profile report had to rebuild a guess from whatever config it was run with
later, and reported the truncation gate as `unknown` — a gate that could never
fire. A corpus silently cut off at retmax was indistinguishable from a complete
one, and every count in the report would have been wrong by an unbounded amount
with nothing saying so.

Run: python tests/test_provenance.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor.cli import _corpus_counts, _profile_corpus  # noqa: E402
from check_your_advisor.export import load_papers_json, save_to_json  # noqa: E402

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


PAPERS = [{"pmid": "1", "title": "A", "role": "第一作者"},
          {"pmid": "2", "title": "B", "role": "通讯作者"}]

SEARCH = {
    "mindate": "2016/01/01", "maxdate": "2026/01/01", "years_back": 10,
    "retmax": 500, "narrowed_by_affiliation": False,
    "esearch_term": '("Doe Jane"[Author] OR "Doe J*"[Author])',
    "esearch_matched": 149, "pmids_returned": 149, "truncated": False,
    "fetched": 149, "verified": 63,
}

CFG = {"author_name": "Doe Jane", "years_back": 10,
       "author_identity": {"affiliation_keywords": ["Example University"],
                           "email_domains": ["@example.edu"], "orcid": "",
                           "require_affiliation": True}}


# ======================================================================
print("\n--- envelope round trip ---")
# ======================================================================
with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "papers.json")

    save_to_json(PAPERS, path, provenance=SEARCH)
    raw = json.loads(open(path, encoding="utf-8").read())
    check("provenance produces an envelope, not a list", isinstance(raw, dict), True)
    check("envelope keeps the papers", len(raw["papers"]), 2)
    check("envelope carries the search", raw["search"]["esearch_matched"], 149)
    check("envelope is versioned", raw["schema_version"], 1)

    papers, search = load_papers_json(path)
    check("round trip returns the papers", len(papers), 2)
    check("round trip returns the search", search["truncated"], False)

    # A file written before this change is a bare list. It must still load, and
    # must report an EMPTY provenance rather than a fabricated one: the caller
    # has to be able to tell "not recorded" from "recorded as zero".
    legacy = os.path.join(tmp, "legacy.json")
    save_to_json(PAPERS, legacy)
    raw_legacy = json.loads(open(legacy, encoding="utf-8").read())
    check("without provenance the file stays a bare list", isinstance(raw_legacy, list), True)

    papers, search = load_papers_json(legacy)
    check("legacy file still loads its papers", len(papers), 2)
    check("legacy file yields empty provenance, not invented values", search, {})


# ======================================================================
print("\n--- provenance reaches the corpus contract ---")
# ======================================================================
corpus = _profile_corpus(PAPERS, CFG, SEARCH)
q = corpus["query"]

# The renderer reads `term` and `esearch_count`; search_pubmed emits
# `esearch_term` and `esearch_matched`. A rename on either side silently
# reintroduces the "?" this change removed, so the mapping is asserted.
check("term is exposed under the contract's key", q["term"], SEARCH["esearch_term"])
check("hit count is exposed as esearch_count", q["esearch_count"], 149)
check("pmids_returned carried", q["pmids_returned"], 149)
check("retmax carried", q["retmax"], 500)
check("date range carried", (q["mindate"], q["maxdate"]), ("2016/01/01", "2026/01/01"))
check("truncated is a real boolean", q["truncated"], False)

check("fetched carried", corpus["counts"]["fetched"], 149)
check("verified carried", corpus["counts"]["verified"], 63)
check("rejected is derived", corpus["counts"]["rejected"], 86)


# ======================================================================
print("\n--- absence is never filled in ---")
# ======================================================================
legacy_corpus = _profile_corpus(PAPERS, CFG)
check("legacy corpus reports truncation as unknown",
      legacy_corpus["query"]["truncated"], "unknown")
check("legacy corpus invents no term", "term" in legacy_corpus["query"], False)
check("legacy corpus invents no counts", legacy_corpus["counts"], {})

# rejected is fetched - verified; with either side missing it must not appear,
# because a defaulted zero reads as "nobody was rejected".
check("rejected absent when fetched is missing",
      "rejected" in _corpus_counts({"verified": 63}), False)
check("rejected absent when verified is missing",
      "rejected" in _corpus_counts({"fetched": 149}), False)
check("no counts at all from an empty search", _corpus_counts({}), {})


# ======================================================================
print("\n--- which identity signal actually carried each kept paper ---")
# ======================================================================
# `by_evidence` was read by the provenance block and written by nobody, so the
# report printed "verified by evidence tier: not recorded" on every real run.
# The tier is the difference between a corpus held together by ORCID and one
# resting entirely on affiliation keywords — and an over-broad keyword is the
# documented way a same-named stranger gets in. Leaving it blank hid exactly
# the failure the identity gate exists to prevent.
from check_your_advisor.pubmed_api import evidence_tier_from_role  # noqa: E402

check("an ORCID hit is recognised", evidence_tier_from_role("第一作者 [ORCIDOK]"), "orcid")
check("an email hit is recognised",
      evidence_tier_from_role("通讯作者 [EmailOK a@b.edu]"), "email")
check("an affiliation hit is recognised",
      evidence_tier_from_role("末位作者 [机构OK Example Univ]"), "affiliation")
check("an unmarked role claims no tier", evidence_tier_from_role("待确认"), "")
check("an empty role claims no tier", evidence_tier_from_role(""), "")

_papers = (
    [{"role": "第一作者 [ORCIDOK]"}] * 7
    + [{"role": "末位作者 [机构OK Example Univ]"}] * 30
)
_c = _corpus_counts({"fetched": 116, "verified": 37}, _papers)
check("tiers are counted", _c["by_evidence"], {"orcid": 7, "affiliation": 30})
check("T06 contract holds: tiers sum to verified",
      sum(_c["by_evidence"].values()), _c["verified"])

# The whole point of _corpus_counts is that a missing operand stays missing
# rather than becoming a confident zero.
check("no papers means no tier block", "by_evidence" in _corpus_counts({"verified": 3}), False)
check("unmarked papers mean no tier block",
      "by_evidence" in _corpus_counts({"verified": 2}, [{"role": "待确认"}] * 2), False)
check("papers arg stays optional for older callers",
      "by_evidence" in _corpus_counts({"fetched": 149, "verified": 63}), False)


# ======================================================================
print("\n--- the truncation gate can now fire ---")
# ======================================================================
from check_your_advisor.profile.report import build_report  # noqa: E402

truncated = dict(SEARCH, esearch_matched=900, pmids_returned=149, truncated=True)
report = build_report(_profile_corpus(PAPERS, CFG, truncated))
check("a truncated corpus is refused", report["refused"], True)
check("the gate is identified as G1", report["gate"]["id"], "G1")
check("a refused report exits non-zero", report["exit_code"], 1)

clean = build_report(_profile_corpus(PAPERS, CFG, SEARCH))
check("a complete corpus is not refused by G1",
      (clean["gate"] or {}).get("id") != "G1", True)

# The whole point: before provenance the gate was structurally unable to fire.
legacy_report = build_report(_profile_corpus(PAPERS, CFG))
check("a legacy corpus cannot fire G1",
      (legacy_report["gate"] or {}).get("id") != "G1", True)


print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(0 if _failed == 0 else 1)
