#!/usr/bin/env python3
"""
Tests for search provenance carried through papers_*.json.

Why this exists: `search_pubmed` knew the esearch term, the hit count and how
much of it came back, but all of it stayed inside the function and went out with
the log. `papers_*.json` was a bare list, so the profile report had to rebuild a
guess from whatever config it was run with later. A corpus silently cut off at
retmax was indistinguishable from a complete one, and every count in the report
would have been wrong by an unbounded amount with nothing saying so.

The harvest now pages with `retstart`, so the shortfall that gate G1 used to
refuse on is printed as "retrieved N of M" instead. Both numbers still have to
survive the trip through the file and the rename layer, which is what the last
block below asserts.

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
    "retmax": 500, "max_records": 10000, "narrowed_by_affiliation": False,
    "esearch_term": '("Doe Jane"[Author] OR "Doe J*"[Author])',
    "esearch_matched": 149, "pmids_returned": 149, "truncated": False,
    "pages_fetched": 1, "duplicates_dropped": 0,
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
# The paging trio. Without them "retrieved N of M" cannot say how the harvest
# got there, and a shortfall reads as an unexplained hole.
check("max_records carried", q["max_records"], 10000)
check("pages_fetched carried", q["pages_fetched"], 1)
check("duplicates_dropped carried", q["duplicates_dropped"], 0)
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
check("legacy corpus invents no page count", "pages_fetched" in legacy_corpus["query"], False)
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
# The fourth tier. Written by `openalex.work_to_paper`, never by the PubMed
# filter — efetch carries no OpenAlex id — but read back here, which is why the
# marker constant lives beside the other three.
check("an OpenAlex author-id hit is recognised",
      evidence_tier_from_role("第一作者 [OpenAlexOK A5023888391]"), "openalex")
check("an unmarked role claims no tier", evidence_tier_from_role("待确认"), "")
check("an empty role claims no tier", evidence_tier_from_role(""), "")

# Every marker the writers emit has to round-trip. A marker reworded on one side
# would reclassify a whole corpus as unattributed with nothing failing.
from check_your_advisor import pubmed_api as _pubmed_api  # noqa: E402

check("every declared marker maps to a tier",
      sorted(tier for _marker, tier in _pubmed_api._EVIDENCE_MARKERS),
      ["affiliation", "email", "openalex", "orcid"])

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
print("\n--- name_only: the zero bucket of the same histogram ---")
# ======================================================================
# `counts["name_only"]` was read by Section 1 and by CAV-01 and written by
# nobody, so both fell through to a hard-coded 0 on every run this tool ever
# made. The most visible shape of it: five bare name matches plus one record
# from the OpenAlex merge printed `name_only 0` beside `evidence tier: openalex
# 1`, and the five records with no evidence at all appeared in neither number.
_five_name_only = [{"role": "第一作者 [机构未验证⚠]"}] * 5
_one_openalex = [{"role": "第一作者 [OpenAlexOK A5023888391]"}]
_mixed = _corpus_counts({"fetched": 5, "verified": 5}, _five_name_only + _one_openalex)
check("name_only is produced, not defaulted", _mixed.get("name_only"), 5)
check("...beside the tiers it shares a denominator with",
      _mixed.get("by_evidence"), {"openalex": 1})
check("...and the two add up to the harvested corpus",
      _mixed.get("name_only", 0) + sum((_mixed.get("by_evidence") or {}).values()), 6)

# A fallback corpus is name-only by construction: nothing passed, everything was
# kept, and 待确认 says so on every record.
check("the fallback stamp counts as a recorded name-only match",
      _corpus_counts({"verified": 0}, [{"role": "待确认"}] * 3).get("name_only"), 3)
check("a fully verified corpus reports a true zero",
      _corpus_counts({"verified": 2}, [{"role": "第一作者 [ORCIDOK]"}] * 2).get("name_only"), 0)

# The rule that separates a zero from an absence. A role written before the
# markers existed says nothing either way, and one such record withholds the
# whole count rather than printing a floor as a value.
check("an unmarked legacy role withholds the count entirely",
      "name_only" in _corpus_counts({"verified": 1}, [{"role": "第一作者"}]), False)
check("...even when other records in the same corpus are marked",
      "name_only" in _corpus_counts(
          {"verified": 2}, [{"role": "第一作者 [ORCIDOK]"}, {"role": "第一作者"}]), False)
check("no papers at all means no count", "name_only" in _corpus_counts({"verified": 3}), False)

from check_your_advisor.pubmed_api import is_name_only_role  # noqa: E402

check("the loose-mode marker is recognised", is_name_only_role("第一作者 [机构未验证⚠]"), True)
check("the fallback stamp is recognised", is_name_only_role("待确认"), True)
check("a verified role is not name-only", is_name_only_role("第一作者 [ORCIDOK]"), False)
check("an unmarked legacy role is not name-only", is_name_only_role("第一作者"), False)
check("an empty role is not name-only", is_name_only_role(""), False)


# ======================================================================
print("\n--- which bibliographic source holds each record ---")
# ======================================================================
# A merged corpus has two denominators — the PubMed corpus and the merged one —
# and printing one number for both is how a reader ends up comparing a merged
# run against an earlier PubMed-only run without knowing it.
_merged_papers = (
    [{"role": "第一作者 [ORCIDOK]", "confirmed_by": ["pubmed"]}] * 4
    + [{"role": "第一作者 [ORCIDOK]", "confirmed_by": ["openalex", "pubmed"]}] * 3
    + [{"role": "第一作者 [OpenAlexOK A5023888391]", "confirmed_by": ["openalex"]}] * 2
)
_m = _corpus_counts({"fetched": 20, "verified": 9}, _merged_papers)
check("the three populations are counted separately",
      _m["by_source"], {"pubmed": 4, "both": 3, "openalex": 2})
check("...and sum to the merged corpus", sum(_m["by_source"].values()), 9)
check("the OpenAlex tier is counted alongside the other three",
      _m["by_evidence"], {"orcid": 7, "openalex": 2})

# Absent, not defaulted: "one source because that is all there was" and "one
# source because the merge never ran" are different facts about the run.
check("a corpus harvested before the merge existed claims no source split",
      "by_source" in _corpus_counts({"verified": 2}, [{"role": "第一作者 [ORCIDOK]"}] * 2),
      False)
check("...and neither does an empty corpus",
      "by_source" in _corpus_counts({"verified": 0}, []), False)


# ======================================================================
print("\n--- an incomplete harvest is printed, not refused ---")
# ======================================================================
# Gate G1 refused every corpus where esearch matched more records than came
# back. Before paging that meant one thing — cut off at retmax — and refusing
# was right. Paging made it mean something much smaller: the max_records budget
# stopped a very common name, the pages repeated a PMID, or PubMed's own Count
# moved mid-harvest. So the shortfall is now a numerator and a denominator in
# Section 1, and the report is still produced.
from check_your_advisor.profile.report import GATES, build_report  # noqa: E402


def _author(name: str, email: str = "") -> dict:
    last, fore = name.split()
    return {"name": name, "last": last, "fore": fore, "initials": fore[0],
            "affiliation": "Example University", "email": email,
            "is_corresponding": bool(email), "orcid": "", "equal_contrib": False}


# PAPERS above carries no author list, which is all the counts assertions need.
# A report needs structured authors or gate G4 fires first and hides everything
# this block is about.
REPORTABLE = [
    {"pmid": str(100 + i), "title": f"Paper {i}", "role": "通讯作者 [EmailOK jane@example.edu]",
     "journal": "Journal of Examples", "doi": f"10.1/{i}",
     "pub_date": f"202{i % 4} Mar", "pub_year": f"202{i % 4}",
     "authors": [_author(f"Trainee Number{i}"), _author("Doe Jane", "jane@example.edu")]}
    for i in range(6)
]


def section_body(rep: dict, section_id: int) -> str:
    return "\n".join(next(s for s in rep["sections"] if s["id"] == section_id)["body"])


check("G1 is not a gate any more", "G1" in GATES, False)

capped = dict(SEARCH, esearch_matched=900, pmids_returned=500, truncated=True,
              pages_fetched=1, duplicates_dropped=0, max_records=500)
report = build_report(_profile_corpus(REPORTABLE, CFG, capped))
check("a capped corpus is not refused", report["refused"], False)
check("no gate fires on it", report["gate"], None)
# Not a gate, but not silent either: G1 was deleted outright in one round and is
# back as a warning in this one, so the report renders in full and the exit code
# is 1 again. A corpus retrieved in part must not look like a corpus retrieved in
# full on a `compare` page.
check("but it warns", [w["id"] for w in report["warnings"]], ["G1"])
check("and it exits one, as it did when G1 was a gate", report["exit_code"], 1)

capped_body = section_body(report, 1)
check("the report prints retrieved of matched",
      "retrieved 500 of 900 records esearch matched" in capped_body, True)
check("the report names the shortfall",
      "400 of those 900 records were never retrieved" in capped_body, True)
check("the report says the counts below are floors",
      "is a floor rather than a value" in capped_body, True)
check("the report names the budget that stopped it",
      "budget max_records=500" in capped_body, True)

clean = build_report(_profile_corpus(REPORTABLE, CFG, SEARCH))
clean_body = section_body(clean, 1)
check("a complete corpus prints the same two numbers",
      "retrieved 149 of 149 records esearch matched" in clean_body, True)
check("...and claims no shortfall", "never retrieved" in clean_body, False)
check("...and says how many pages it took", "esearch pages fetched 1" in clean_body, True)

# A legacy file records neither number, and neither is invented: "?" is the
# honest answer, and a fabricated 0 of 0 would read as an empty search.
legacy_report = build_report(_profile_corpus(REPORTABLE, CFG))
legacy_body = section_body(legacy_report, 1)
check("a legacy corpus is not refused either", legacy_report["refused"], False)
check("a legacy corpus prints ? on both sides",
      "retrieved ? of ? records esearch matched" in legacy_body, True)


# ======================================================================
print("\n--- the middle count is named for what it counts ---")
# ======================================================================
# `search["verified"]` is `len(matched_papers)` — every record
# `is_first_or_corresponding` returned True for. With require_affiliation=false
# that includes a record where the name matched a lead slot and *nothing else
# did*, stamped `[机构未验证⚠]`. Section 1 printed that number under the word
# "verified", glossed as "passed identity verification", and then printed
# `name_only 4` two lines below it. Both lines described the same six records
# and denied each other, and the word was the one making the false claim: the
# filter checks a byline slot, and only sometimes an identity.
#
# The corpus below is the reported reproduction: four loose-mode name-only
# records, two carried by an affiliation keyword.

LOOSE = [
    {"pmid": str(300 + i), "title": f"Loose {i}", "journal": "Journal of Examples",
     "doi": f"10.3/{i}", "pub_date": f"202{i % 4} Mar", "pub_year": f"202{i % 4}",
     "role": "第一作者 [机构未验证⚠]" if i < 4 else "第一作者 [机构OK Example University]",
     "authors": [_author("Doe Jane"), _author(f"Trainee Number{i}")]}
    for i in range(6)
]
loose_search = dict(SEARCH, fetched=6, verified=6, esearch_matched=6, pmids_returned=6)
loose_body = section_body(build_report(_profile_corpus(LOOSE, CFG, loose_search)), 1)

check("the middle count is printed", "kept 6 / rejected 0" in loose_body, True)
check("...and is not claimed to be verified", "verified 6" in loose_body, False)
check("...and the old gloss is gone with it",
      "passed identity verification" in loose_body, False)
check("...the gloss names the mode that lets a bare name through",
      "require_affiliation=false" in loose_body, True)
check("...and says outright that the number is not a count of identities",
      "not a count of confirmed identities" in loose_body, True)
# The two lines that used to contradict each other, now both on the page. A
# reader taking each at face value gets one consistent story: six records
# survived the filter, four of them on a name alone.
check("the evidence line still reports the four bare name matches",
      "name_only 4" in loose_body, True)
check("...and the tier that carried the other two", "affiliation 2" in loose_body, True)
# The corpus file keeps the key it has always had — renaming it would strand
# every papers_*.json already on disk — and the line says where the number lives.
check("the line names the key the file records it under",
      "under its original key `verified`" in loose_body, True)

# Nothing above may reach the fallback case, which the previous round fixed by
# moving where the count is taken. A fired fallback still prints 0 kept.
fallback_papers = [dict(p, role="待确认") for p in LOOSE[:3]]
fallback_body = section_body(
    build_report(_profile_corpus(fallback_papers, CFG,
                                 dict(SEARCH, fetched=3, verified=0, fallback_fired=True,
                                      esearch_matched=3, pmids_returned=3))), 1)
check("a fired fallback still prints zero kept against all three fetched",
      "fetched 3 / kept 0 / rejected 3" in fallback_body, True)


# ======================================================================
print("\n--- a malformed count prints 'not recorded', it does not raise ---")
# ======================================================================
# Both halves of the evidence histogram are read out of a JSON file an earlier
# run wrote, and `build_report` is public API — the profile CLI always writes
# ints, but a hand-built corpus need not. `sum(by_evidence.values())` and
# `with_evidence + name_only` had no type guard, so a string in either field
# raised TypeError out of build_report, past main(), which has no handler: one
# bad field replaced the entire report with a traceback.

CLEAN_COUNTS = {"fetched": 6, "verified": 6, "rejected": 0,
                "by_evidence": {"orcid": 4}, "name_only": 2}


def evidence_line(counts: dict) -> str:
    corpus = _profile_corpus(REPORTABLE, CFG, dict(SEARCH, fetched=6, verified=6))
    corpus["counts"] = counts
    body = section_body(build_report(corpus), 1)
    return next(ln for ln in body.splitlines() if ln.startswith("- identity evidence on records:"))


check("a well-formed histogram still prints its two numbers",
      "4 of 6 harvested record(s)" in evidence_line(CLEAN_COUNTS), True)

for label, bad, shown in (
    ("name_only as a string", dict(CLEAN_COUNTS, name_only="four"), "name_only='four'"),
    ("name_only as a float", dict(CLEAN_COUNTS, name_only=2.5), "name_only=2.5"),
    ("name_only as a bool", dict(CLEAN_COUNTS, name_only=True), "name_only=True"),
    ("by_evidence as a string", dict(CLEAN_COUNTS, by_evidence="orcid"), "by_evidence='orcid'"),
    ("by_evidence as a list", dict(CLEAN_COUNTS, by_evidence=["orcid"]), "by_evidence=['orcid']"),
    ("a tier count as a string",
     dict(CLEAN_COUNTS, by_evidence={"orcid": "four"}), "by_evidence={'orcid': 'four'}"),
    ("a non-string tier key",
     dict(CLEAN_COUNTS, by_evidence={1: 4}), "by_evidence={1: 4}"),
):
    try:
        line = evidence_line(bad)
    except Exception as exc:  # noqa: BLE001 - not raising is the whole assertion
        line = f"RAISED {type(exc).__name__}: {exc}"
    check(f"{label}: the report is still produced",
          line.startswith("- identity evidence on records: not recorded"), True)
    check(f"{label}: ...and the unreadable value is quoted back", shown in line, True)
    check(f"{label}: ...and no number is invented in its place",
          "harvested record(s) carry identity evidence" in line, False)

# The reason has to match the fault. An absent `name_only` means the corpus
# predates the markers and re-harvesting settles it; a malformed one means the
# file is wrong. Printing the first explanation for the second sends a reader
# after the wrong thing.
absent = evidence_line({k: v for k, v in CLEAN_COUNTS.items() if k != "name_only"})
malformed = evidence_line(dict(CLEAN_COUNTS, name_only="four"))
check("an absent count still blames the markers",
      "predates the evidence markers" in absent, True)
check("...and a malformed one does not", "predates the evidence markers" in malformed, False)
check("...it says the file carries a value that cannot be added",
      "is not a number this line can add up" in malformed, True)
check("the markers that were recorded are still printed either way",
      ("orcid 4" in absent, "orcid 4" in malformed), (True, True))


print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(0 if _failed == 0 else 1)
