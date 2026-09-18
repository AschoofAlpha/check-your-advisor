#!/usr/bin/env python3
"""
Advisor profile tests.

Covers every (input -> expected) case listed in docs/profile-metrics-spec.md
Section 11, plus the suppression behaviour that keeps small samples from being
rendered as confident aggregates. Spec test IDs (T01-T62) appear in the labels
so a failure points straight at the rule it violates.

All data is fictional. The only real identifier used is 0000-0002-1825-0097,
ORCID's own published example for the fictional Josiah Carberry, matching
tests/test_identity_filter.py. Where a second, deliberately different ORCID is
needed, 0000-0000-0000-0000 is used: it fails the ORCID checksum and can
therefore never belong to a real person.

Fully offline: synthetic fixtures only, no network, no matplotlib.

Run: python tests/test_profile.py
"""

from __future__ import annotations

import csv
import inspect
import json
import os
import re
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor.journals import load_journal_table  # noqa: E402
from check_your_advisor.profile import caveats, metrics, report, roles, scoring  # noqa: E402

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


def check_false(label: str, actual) -> None:
    check(label, bool(actual), False)


# ============================================================
# Fixture builders
# ============================================================

TARGET = "Chen Xiuying"
ORCID = "0000-0002-1825-0097"
# Checksum-invalid, so it can never be a real person's identifier.
OTHER_ORCID = "0000-0000-0000-0000"
INTERNAL = "Department of Hepatobiliary Surgery, Nanhai Medical University, Nanhai"
INTERNAL_VARIANT = "Nanhai Med Univ, Nanhai"
EXTERNAL = "Institute of Applied Physics, Beihai Polytechnic"

IDENTITY = {
    "author_name": TARGET,
    "orcid": ORCID,
    "affiliation_keywords": ["Nanhai Medical University", "Nanhai Med Univ"],
    "email_domains": ["@nanhai-med.example.edu"],
    "require_affiliation_effective": False,
}

FIXED_NOW = datetime(2026, 7, 22, 20, 47, 11)


def author(name, *, affiliation="", email="", orcid="", equal_contrib=False,
           corresponding=None, last=None, fore=None, initials=None):
    """One byline entry in the shape `pubmed_api._author_record` produces."""
    parts = name.split()
    last_value = last if last is not None else (parts[0] if parts else "")
    fore_value = fore if fore is not None else (" ".join(parts[1:]) if len(parts) > 1 else "")
    initials_value = initials if initials is not None else (fore_value[:1] if fore_value else "")
    return {
        "name": name,
        "last": last_value,
        "fore": fore_value,
        "initials": initials_value,
        "affiliation": affiliation,
        "email": email,
        "orcid": orcid,
        "equal_contrib": equal_contrib,
        # Mirrors the real parser: corresponding status is inferred from an email.
        "is_corresponding": bool(email) if corresponding is None else corresponding,
    }


def distinct(prefix, index, given="Person"):
    """
    A name guaranteed not to share a person key with any other `distinct` name.

    The disambiguating digits go in the surname on purpose. Putting them in the
    forename ("Lead Person01", "Lead Person02") produces one loose key and two
    incompatible forenames, which the tool correctly reports as a suspected
    collision — correct behaviour, but it makes a fixture mean something other
    than what it looks like.
    """
    return f"{prefix}{index:02d} {given}"


def collective(name):
    """A <CollectiveName> byline entry: a name with no name parts."""
    return {"name": name, "last": "", "fore": "", "initials": "", "affiliation": "",
            "email": "", "orcid": "", "equal_contrib": False, "is_corresponding": False}


def pi(affiliation=INTERNAL, email="", orcid=ORCID):
    return author(TARGET, affiliation=affiliation, email=email, orcid=orcid)


def paper(pmid, authors, pub_date="2023 Jun", *, title=None, journal="Hepatology Reports",
          doi="", pi_index=None, pi_evidence="orcid", pi_ambiguous=False):
    record = {
        "pmid": str(pmid),
        "title": title if title is not None else f"Study {pmid} of hepatic stellate cell activation",
        "authors": authors,
        "authors_str": ", ".join(a["name"] for a in authors),
        "journal": journal,
        "pub_date": pub_date,
        "pub_year": pub_date.split()[0] if pub_date else "",
        "volume": "", "issue": "", "pages": "", "doi": doi, "pmc_id": "", "abstract": "",
    }
    if pi_index is not None:
        record["pi_index"] = pi_index
        record["pi_evidence"] = pi_evidence
        record["pi_ambiguous"] = pi_ambiguous
    return record


def corpus(papers, **overrides):
    data = {
        "schema_version": 1,
        "generated_at": "2026-07-22T20:47:11",
        "position_filtered": False,
        "query": {
            "term": '"Chen Xiuying"[Author] OR "Chen X*"[Author]',
            "mindate": "2021/07/22", "maxdate": "2026/07/22",
            "years_back": 5, "retmax": 500,
            "esearch_count": len(papers), "pmids_returned": len(papers), "truncated": False,
        },
        "identity": dict(IDENTITY),
        "counts": {
            "fetched": len(papers), "verified": len(papers), "name_only": 0, "rejected": 0,
            "by_evidence": {"orcid": len(papers)},
        },
        "fallback_fired": False,
        "papers": papers,
    }
    data.update(overrides)
    return data


def build_from(data, config=None, gantt=None):
    return report.build_report(data, config or {}, gantt, FIXED_NOW)


def build(papers, config=None, gantt=None, **overrides):
    return build_from(corpus(papers, **overrides), config, gantt)


def section(rep, section_id):
    return next(s for s in rep["sections"] if s["id"] == section_id)


def body_text(rep, section_id):
    return "\n".join(section(rep, section_id)["body"])


def caveat_text(rep, section_id):
    return "\n".join(section(rep, section_id)["caveats"])


def all_body_lines(rep):
    return [line for sec in rep["sections"] for line in sec["body"]]


def person_named(rep, name):
    for row in rep["metrics"]["s2"]["rows"]:
        if row["name"] == name:
            return row
    return None


# ============================================================
# 11.1 Gates and provenance
# ============================================================

print("\n--- gates and provenance (T01-T08) ---")

# T01. The harvest pages, so "esearch matched more than came back" no longer
# means "cut off at retmax" — it means the max_records budget stopped a very
# common name, or the pages repeated a PMID, or PubMed's Count moved while they
# were being fetched. Gate G1 refused all three, and it is not a gate any more:
# the report is built in full and the shortfall is printed as a numerator over a
# denominator.
#
# What deleting the gate outright also removed was any mark on the page: a corpus
# 40% retrieved rendered clean, exited 0, and took a rank on a `compare` page
# beside corpora retrieved in full. G1 is back at warning strength — banner,
# exit 1, no rank — which is where G2 and G3 already sit.
capped = corpus([paper(1, [author("Liu Hua"), pi()], pi_index=1)])
capped["query"]["esearch_count"] = 900
capped["query"]["pmids_returned"] = 500
capped["query"]["max_records"] = 500
capped["query"]["pages_fetched"] = 1
capped["query"]["duplicates_dropped"] = 0
rep = build_from(capped)
check("T01 an incomplete harvest is not refused", rep["refused"], False)
check("T01 no gate fires on it", rep["gate"], None)
check("T01 it is a warning instead", [w["id"] for w in rep["warnings"]], ["G1"])
check("T01 exit code is 1, as it was when G1 refused", rep["exit_code"], 1)
check("T01 the warning lands on sections 0, 1 and 9",
      (rep["warnings"] or [{}])[0].get("sections"), [0, 1, 9])
check("T01 ...and nowhere else",
      [s["id"] for s in rep["sections"] if s["warnings"]], [0, 1, 9])
check("T01 the observed values name both sides of the shortfall",
      (rep["warnings"] or [{}])[0].get("observed", {}).get("never_retrieved"), 400)
check_true("T01 the banner opens the section it affects",
           (section(rep, 9)["warnings"] or [""])[0].startswith("**Warning G1"))
check_true("T01 the report prints retrieved of matched",
           "retrieved 500 of 900 records esearch matched" in body_text(rep, 1))
check_true("T01 and names the shortfall",
           "400 of those 900 records were never retrieved" in body_text(rep, 1))
check_true("T01 and warns the counts below are floors",
           "is a floor rather than a value" in report.render_markdown(rep))
check("T01 G1 is not a gate any more", "G1" in report.GATES, False)
check_true("T01 Section 14 records that it was deleted and then restored",
           any("removed, then restored as a warning" in line
               for line in section(rep, 14)["prose"]))
# Two unknowns are not a shortfall. The old gate did `int(query.get(...) or 0)`
# and died on the "?" a legacy corpus carries in both slots.
_unknown = corpus([paper(1, [author("Liu Hua"), pi()], pi_index=1)])
_unknown["query"]["esearch_count"] = "?"
_unknown["query"]["pmids_returned"] = "?"
check("T01 an unrecorded coverage pair raises no warning and no exception",
      [w["id"] for w in report.check_coverage_warnings(_unknown)], [])
_equal = corpus([paper(1, [author("Liu Hua"), pi()], pi_index=1)])
_equal["query"].update({"esearch_count": 40, "pmids_returned": 40})
check("T01 a complete harvest raises nothing",
      [w["id"] for w in report.check_coverage_warnings(_equal)], [])

# T02/T03. G2 and G3 stopped being gates. They refused a corpus whose identity
# could not be verified, which destroyed sixteen sections of computed fact —
# including Section 19, the one section whose whole job is to show the reader
# whether the corpus holds several people — to prevent one unwarranted claim.
# They are now warnings printed at the top of Sections 0, 1 and 19; the report
# is built in full and the exit code is still 1.
fallback = corpus([paper(i, [author(distinct("Liu", i, "Hua")), pi()], pi_index=1) for i in range(40)])
fallback["fallback_fired"] = True
for _p in fallback["papers"]:
    _p["role"] = "待确认"
rep = build_from(fallback)
check("T02 identity fallback no longer refuses", rep["refused"], False)
check("T02 no gate fires on it", rep["gate"], None)
check("T02 it is a warning instead", [w["id"] for w in rep["warnings"]], ["G2"])
check("T02 the warning keeps the gate's name", rep["warnings"][0]["name"], "identity fallback")
check("T02 the exit code is unchanged", rep["exit_code"], 1)
check("T02 every section is rendered", len(rep["sections"]), 21)
check("T02 the warning lands on sections 0, 1 and 19", rep["warnings"][0]["sections"], [0, 1, 19])
check("T02 the observed count is the real one, not a flag",
      rep["warnings"][0]["observed"]["papers_stamped_unverified"], 40)
for _sid in (0, 1, 19):
    check_true(f"T02 section {_sid} carries the warning first",
               section(rep, _sid)["warnings"][0].startswith("**Warning G2"))
check("T02 no other section carries it",
      [s["id"] for s in rep["sections"] if s["warnings"]], [0, 19, 1])
check_true("T02 the warning states the fix", "Fix: Configure orcid" in section(rep, 0)["warnings"][0])
# One rendering of the observed values, reused by the callout, by Section 14 and
# by the command-line log. Three call sites formatting one dict three ways is how
# the same observation ends up looking like two different ones.
check("T02 the observed values are rendered once",
      rep["warnings"][0]["observed_text"],
      "fallback_fired=True; papers_stamped_unverified=40; papers_harvested=40")
check_true("T02 ...and that string is what the callout prints",
           rep["warnings"][0]["observed_text"] in section(rep, 0)["warnings"][0])
check_true("T02 ...and what Section 14 prints",
           any(rep["warnings"][0]["observed_text"] in line for line in section(rep, 14)["prose"]))
check("T02 an empty list is shown as a count, not as an empty bracket",
      "affiliation_keywords=0" in report._warning("G3", {"affiliation_keywords": []})["observed_text"],
      True)
_md = report.render_markdown(rep)
# Headings became bilingual in round four (`report.SECTION_TITLES_ZH`), so these
# match on the two things actually under test — which section, and that the
# warning is the very next thing inside it — rather than on one exact heading
# string. The English half is asserted separately, because four other modules and
# SKILL.md cross-reference sections by it and would break silently without it.
_head_0 = next(line for line in _md.splitlines() if line.startswith("## 0."))
_head_19 = next(line for line in _md.splitlines() if line.startswith("## 19."))
check_true("T02 section 0's heading carries both languages",
           "What this report is and is not" in _head_0 and "这份报告" in _head_0)
# The promise `SECTION_TITLES_ZH`'s docstring makes. Without this, adding a
# section gives it an English-only heading and nothing says so — the reader just
# finds one row of the table of contents in the wrong language.
check("T02b every section emitted has a Chinese title",
      sorted(s["id"] for s in rep["sections"] if s["id"] not in report.SECTION_TITLES_ZH), [])
check("T02b ...and the table names no section that is not emitted",
      sorted(set(report.SECTION_TITLES_ZH) - {s["id"] for s in rep["sections"]}), [])
check("T02b every rendered heading carries both languages",
      [line for line in _md.splitlines()
       if line.startswith("## ") and not any("一" <= c <= "鿿" for c in line)], [])
check_true("T02 the markdown prints it directly under the section heading",
           f"{_head_0}\n\n**Warning G2" in _md)
check_true("T02 and again above Section 19's own prose",
           f"{_head_19}\n\n**Warning G2" in _md)
check_true("T02 Section 14 records the downgrade",
           any("G2 (identity fallback)" in line and "used to refuse the whole report" in line
               for line in section(rep, 14)["prose"]))
check_true("T02 the standing register keeps the reversal line",
           any("Refusing the whole report over an unverifiable identity" in line
               for line in section(rep, 14)["prose"]))

weak = corpus([paper(1, [author("Liu Hua"), pi()], pi_index=1)])
weak["identity"] = {"author_name": TARGET, "orcid": "", "affiliation_keywords": [], "email_domains": []}
rep = build_from(weak)
check("T03 weak identity config no longer refuses", rep["refused"], False)
check("T03 it is a warning instead", [w["id"] for w in rep["warnings"]], ["G3"])
check("T03 the exit code is unchanged", rep["exit_code"], 1)
check_true("T03 the warning names the missing evidence",
           "openalex_author_id=(none)" in section(rep, 1)["warnings"][0])

# Both at once. As gates the first to fire hid the second; two different
# problems with two different fixes were reported as one.
both = corpus([paper(1, [author("Liu Hua"), pi()], pi_index=1)])
both["identity"] = {"author_name": TARGET, "orcid": "", "affiliation_keywords": [], "email_domains": []}
both["fallback_fired"] = True
rep = build_from(both)
check("T03 both conditions are reported, not just the first",
      [w["id"] for w in rep["warnings"]], ["G2", "G3"])
check("T03 and each section carries both lines", len(section(rep, 0)["warnings"]), 2)

# A resolved OpenAlex author id is evidence only for the records that carry it.
# `pubmed_api.parse_article` never writes the field, so on a PubMed-only corpus
# every byline entry is `name_only` and the id clears nothing — it used to clear
# G3 anyway, certifying a bare name match on the strength of a fuzzy
# `display_name.search` hit that had touched no record in the corpus.
by_openalex = corpus([paper(1, [author("Liu Hua"), pi()], pi_index=1)])
by_openalex["identity"] = {"author_name": TARGET, "orcid": "", "affiliation_keywords": [],
                           "email_domains": [], "openalex_author_id": "A5023888391"}
check("T03 an OpenAlex author id clears nothing on a corpus whose records lack it",
      [w["id"] for w in report.check_identity_warnings(by_openalex)], ["G3"])
check("T03 ...and the warning says the id was configured but reached no record",
      "openalex_author_id=A5023888391; openalex_id_on_records=0/1; "
      "openalex_id_record_share=0.0; min_openalex_record_share=0.5"
      in (report.check_identity_warnings(by_openalex) or [{}])[0].get("observed_text", ""), True)
check("T03 ...and every byline entry agrees it is name_only",
      roles.evidence_tier(by_openalex["papers"][0]["authors"][1], by_openalex["identity"]),
      "name_only")

# Same id, same config — but now a record actually carries it, which is what the
# OpenAlex works merge produces. Only then is it evidence.
merged_in = corpus([paper(1, [author("Liu Hua"),
                              dict(pi(), openalex_author_id="A5023888391")], pi_index=1)])
merged_in["identity"] = dict(by_openalex["identity"])
check("T03 an OpenAlex id carried by a record in this corpus does count",
      report.check_identity_warnings(merged_in), [])
check("T03 ...and the URL spelling of the same id still matches",
      report.check_identity_warnings(
          {**merged_in,
           "identity": {**merged_in["identity"],
                        "openalex_author_id": "https://openalex.org/A5023888391"}}), [])

# T03b. How much of the corpus the id has to reach. `_corpus_carries_openalex_id`
# answered "does any record carry it" and returned on the first hit, which is a
# threshold of one record: five bare name matches plus one record from the
# OpenAlex merge silenced G3 outright and the run exited 0. The boundary is now a
# declared constant, compared as a share, and printed on the page whether or not
# it fires.
def openalex_corpus(n_name_only: int, n_carried: int) -> dict:
    papers = [paper(6000 + i, [author(distinct("Trainee", i)), pi()], "2023 Mar", pi_index=1)
              for i in range(n_name_only)]
    papers += [paper(6100 + i, [author(distinct("Other", i)),
                                dict(pi(), openalex_author_id="A5023888391")],
                     "2023 Mar", pi_index=1)
               for i in range(n_carried)]
    built = corpus(papers)
    built["identity"] = {"author_name": TARGET, "orcid": "", "affiliation_keywords": [],
                         "email_domains": [], "openalex_author_id": "A5023888391"}
    built["query"]["esearch_count"] = len(papers)
    built["query"]["pmids_returned"] = len(papers)
    return built

_thin = openalex_corpus(5, 1)
check("T03b one record in six no longer clears the identity warning",
      [w["id"] for w in report.check_identity_warnings(_thin)], ["G3"])
check("T03b the exit code says so too", build_from(_thin)["exit_code"], 1)
check("T03b the observed values print the ratio and the boundary it missed",
      (report.check_identity_warnings(_thin) or [{}])[0].get("observed", {})
      .get("openalex_id_on_records"), "1/6")
_half = openalex_corpus(3, 3)
check("T03b half the corpus clears it", report.check_identity_warnings(_half), [])
check("T03b ...and the boundary is exactly where the constant says",
      report.MIN_OPENALEX_RECORD_SHARE, 0.5)
check("T03b a config override moves it, and nothing else does",
      [w["id"] for w in report.check_identity_warnings(_thin, 0.1)], [])
check("T03b ...read from the config block the report resolves",
      report.resolve_openalex_record_share(
          {"identity_evidence": {"min_openalex_record_share": 0.1}}), 0.1)
check("T03b an absent config block falls back to the declared constant",
      report.resolve_openalex_record_share({}), report.MIN_OPENALEX_RECORD_SHARE)
for _bad in (2.0, -0.1, "half", None):
    try:
        report.resolve_openalex_record_share({"identity_evidence": {"min_openalex_record_share": _bad}})
        check(f"T03b {_bad!r} is refused rather than clamped", "no error", "ValueError")
    except ValueError:
        check(f"T03b {_bad!r} is refused rather than clamped", "ValueError", "ValueError")
# The number on the page is the number the code compared against. A boundary a
# reader cannot see is a boundary a reader cannot argue with — the whole defect
# here was a threshold of 1/N that nobody declared and nothing printed.
_thin_body = body_text(build_from(_thin), 1)
check_true("T03b Section 1 prints the ratio it measured",
           "openalex author id on records: 1 of 6 (share 0.17)" in _thin_body)
check_true("T03b ...and the boundary in effect, verbatim",
           "min_openalex_record_share = 0.50" in _thin_body)
check_true("T03b ...and where to change it",
           "identity_evidence.min_openalex_record_share" in _thin_body)
check_true("T03b the line is printed even when the warning does not fire",
           "openalex author id on records: 3 of 6 (share 0.50)" in body_text(build_from(_half), 1))
check("T03b a corpus with no id configured prints no such line",
      "openalex author id on records" in body_text(build_from(clean_openalex_free := corpus(
          [paper(1, [author("Liu Hua"), pi()], pi_index=1)])), 1), False)

# T03c. Which question G3 asks, and whether its banner describes the corpus it
# fired on.
#
# It used to ask what was *configured*. An ORCID sitting in a config file
# cleared the warning whether or not one byline in the corpus carried it, so a
# corpus with `orcid` set and matched against nothing rendered clean and exited
# 0, while the identical corpus with the field left blank warned and exited 1 —
# the same evidence, none, judged two ways on the strength of a string nobody
# checked. The silent one is the one a reader would trust. It now asks how far
# each key actually reached, which is how the OpenAlex id was already judged.
#
# The banner moved with the rule for a reason of its own. One sentence covered
# every way of failing the test and read "No identity evidence reached a single
# record in this corpus" — printed, verbatim, over corpora whose Observed values
# on the same line read `openalex_id_on_records=2/6` and whose Section 1 two
# lines above counted `openalex 2`. The block below pins the split, and then
# pins the invariant that keeps a future copy-edit from re-merging it.


def _reach_identity(**overrides):
    base = {"author_name": TARGET, "orcid": "", "affiliation_keywords": [],
            "email_domains": [], "openalex_author_id": ""}
    base.update(overrides)
    return base


def _reach_corpus(identity, papers):
    built = corpus(papers)
    built["identity"] = identity
    built["query"]["esearch_count"] = len(papers)
    built["query"]["pmids_returned"] = len(papers)
    return built


def _bare_records(first_pmid, n, **pi_kwargs):
    """`n` records the target is on and nothing but the name says so."""
    settings = {"affiliation": EXTERNAL, "orcid": ""}
    settings.update(pi_kwargs)
    return [paper(first_pmid + i, [author(distinct("Other", i)), pi(**settings)], pi_index=1)
            for i in range(n)]


_no_config = _reach_corpus(_reach_identity(), _bare_records(7000, 6))
_orcid_unmatched = _reach_corpus(_reach_identity(orcid=ORCID), _bare_records(7010, 6))
_affil_unmatched = _reach_corpus(
    _reach_identity(affiliation_keywords=["Nanhai Medical University"]), _bare_records(7020, 6))
_email_unmatched = _reach_corpus(
    _reach_identity(email_domains=["@nanhai-med.example.edu"]),
    _bare_records(7030, 6, email="chen@beihai-poly.example.org"))

check("T03c an ORCID configured and matched against no byline no longer exits 0 in silence",
      [w["id"] for w in report.check_identity_warnings(_orcid_unmatched)], ["G3"])
check("T03c ...nor does an affiliation keyword that matches no affiliation in the corpus",
      [w["id"] for w in report.check_identity_warnings(_affil_unmatched)], ["G3"])
check("T03c ...nor an email domain no byline carries",
      [w["id"] for w in report.check_identity_warnings(_email_unmatched)], ["G3"])
check("T03c the exit code follows the warning, as it does for every other G3",
      build_from(_orcid_unmatched)["exit_code"], 1)
check("T03c and a corpus with nothing configured still warns exactly as before",
      [w["id"] for w in report.check_identity_warnings(_no_config)], ["G3"])

# The scope of the reversal, stated as a test so it cannot drift by accident:
# the share boundary belongs to the OpenAlex id and was not extended to the
# other three. One record an ORCID reaches clears the warning.
_orcid_reaches_one = _reach_corpus(
    _reach_identity(orcid=ORCID),
    [paper(7040, [author(distinct("Other", 0)), pi(affiliation=EXTERNAL, orcid=ORCID)],
           pi_index=1)] + _bare_records(7041, 5))
check("T03c one record an ORCID does reach clears it — the share boundary is the id's alone",
      report.check_identity_warnings(_orcid_reaches_one), [])

# Why the reach counter asks each key separately instead of looping
# `roles.evidence_tier`. That function answers "what is the strongest evidence
# on this entry", so an entry carrying both the id and a matching affiliation
# reports `openalex` and the affiliation match vanishes. Here the keyword
# matches only the two records the id also reached, and the id's share (2/6) is
# below the boundary — so a strongest-wins count would report that the keyword
# reached nothing and print that sentence over a corpus it reached twice.
_shadowed = _reach_corpus(
    _reach_identity(affiliation_keywords=["Nanhai Medical University"],
                    openalex_author_id="A5023888391"),
    [paper(7050 + i, [author(distinct("Other", i)),
                      dict(pi(affiliation=INTERNAL, orcid=""),
                           openalex_author_id="A5023888391")], pi_index=1)
     for i in range(2)] + _bare_records(7060, 4))
check("T03c a keyword matching only the records the id also reached still counts as reached",
      report.check_identity_warnings(_shadowed), [])
check("T03c ...because the reach counter asks each key, not which key is strongest",
      report.identity_evidence_record_reach(_shadowed["papers"], _shadowed["identity"]),
      (2, 2, 6))
check("T03c ...and the id on its own still reaches only the two it is on",
      report.openalex_id_record_share(_shadowed["papers"], "A5023888391"), (2, 6))

# The invariant. For every corpus that fires, the sentence in the banner and the
# reach printed on that same banner line have to be the same statement. Written
# as a loop over a matrix rather than as one assertion per wording, so a future
# rewrite of the prose is checked by the same rule.
_G3_TEXT_MATRIX = [
    ("nothing configured", _no_config),
    ("orcid set, matched nothing", _orcid_unmatched),
    ("affiliation keyword set, matched nothing", _affil_unmatched),
    ("email domain set, matched nothing", _email_unmatched),
    ("an id reaching a minority of the corpus", _thin),
]
_situation_of = {message: (key, claims_zero)
                 for key, (claims_zero, message) in report.G3_SITUATIONS.items()}
_situations_seen = set()
for _label, _fixture in _G3_TEXT_MATRIX:
    _rep = build_from(_fixture)
    _fired = [w for w in _rep["warnings"] if w["id"] == "G3"]
    check(f"T03c [{_label}] G3 fires", len(_fired), 1)
    if not _fired:
        continue
    _banner = next((line for line in section(_rep, 1)["warnings"]
                    if line.startswith("**Warning G3")), "")
    check_true(f"T03c [{_label}] the sentence and the numbers are on one printed line",
               _fired[0]["message"] in _banner and _fired[0]["observed_text"] in _banner)
    _seen = re.search(r"identity_evidence_on_records=(\d+)/(\d+)", _banner)
    check_true(f"T03c [{_label}] the banner prints how far the evidence reached", _seen)
    if not _seen:
        continue
    _reached = int(_seen.group(1))
    check(f"T03c [{_label}] a zero-reach claim in the text iff the number beside it is zero",
          any(phrase in _banner for phrase in report.G3_ZERO_REACH_PHRASES), _reached == 0)
    check_true(f"T03c [{_label}] the sentence is one of the declared situations",
               _fired[0]["message"] in _situation_of)
    if _fired[0]["message"] in _situation_of:
        _key, _claims_zero = _situation_of[_fired[0]["message"]]
        _situations_seen.add(_key)
        check(f"T03c [{_label}] ...and that situation's declared flag agrees with the count",
              _claims_zero, _reached == 0)
check("T03c every declared situation is exercised by the matrix above",
      _situations_seen, set(report.G3_SITUATIONS))
check("T03c the two zero-reach situations and the partial one are told apart",
      len({report.G3_SITUATIONS[k][1] for k in report.G3_SITUATIONS}), 3)

# The flag each situation declares has to agree with its own prose, or the loop
# above is checking a claim the reader never sees.
for _key, (_claims_zero, _message) in report.G3_SITUATIONS.items():
    check(f"T03c the {_key} sentence and its declared flag say the same thing",
          any(phrase in _message for phrase in report.G3_ZERO_REACH_PHRASES), _claims_zero)
check_false("T03c the fallback sentence claims no reach figure it was not given",
            any(phrase in report.WARNINGS["G3"][1]
                for phrase in report.G3_ZERO_REACH_PHRASES))
check("T03c the one-size sentence that denied its own Observed numbers is gone",
      any("reached a single record" in _text
          for _, _text in list(report.G3_SITUATIONS.values()) + [(True, report.WARNINGS["G3"][1])]),
      False)
check_true("T03c the fix names matching rather than setting",
           "a key that matches nothing is worth exactly what no key is worth"
           in report.WARNINGS["G3"][2])
check_true("T03c Section 14 records the reversal of the judgement",
           any("read off the config" in name and "what the evidence reached" in name
               for name, _reason in caveats.DROPPED_REGISTER))
check_true("T03c ...and says which way the noise went",
           any("warns and exits 1 where it used to be silent" in reason
               for _name, reason in caveats.DROPPED_REGISTER))

no_authors = corpus([paper(1, [author("Liu Hua"), pi()], pi_index=1), paper(2, [], pi_index=None)])
rep = build_from(no_authors)
check("T04 empty author list refuses", rep["gate"]["id"], "G4")

missing_key = corpus([paper(1, [author("Liu Hua"), pi()], pi_index=1)])
del missing_key["papers"][0]["authors"]
check("T04 absent authors key refuses", build_from(missing_key)["gate"]["id"], "G4")

all_excluded = build([
    paper(1, [author("Liu Hua"), pi()], "", pi_index=1),
    paper(2, [author("Liu Hua"), pi()], "", pi_index=1),
    paper(3, [author("Liu Hua"), pi()], "", pi_index=1),
])
check("T05 corpus emptied by exclusions refuses", all_excluded["gate"]["id"], "G5")

clean = corpus([
    paper(1000 + i, [author(distinct("Trainee", i), affiliation=INTERNAL), pi()],
          f"202{2 + i % 4} Mar", pi_index=1)
    for i in range(25)
])
clean["counts"] = {"fetched": 31, "verified": 25, "name_only": 6, "rejected": 0,
                   "by_evidence": {"orcid": 12, "email": 9, "affiliation": 4}}
clean["query"]["esearch_count"] = 31
clean["query"]["pmids_returned"] = 31
rep = build_from(clean)
check("T06 clean corpus fires no gate", rep["gate"], None)
check_true("T06 provenance prints kept=25", "kept 25" in body_text(rep, 1))
# The word is `kept`, not `verified`, because the number is what the
# first/corresponding-author filter kept — which under require_affiliation=false
# includes a bare name match. This corpus prints `name_only 6` two lines below,
# and `verified 25` beside it was two lines denying each other.
check("T06 ...and does not claim they were verified",
      "verified 25" in body_text(rep, 1), False)
check_true("T06 provenance prints name_only=6", "name_only 6" in body_text(rep, 1))
check("T06 evidence tiers sum to verified", 12 + 9 + 4, rep["provenance"]["counts"]["verified"])
check_true("T06 evidence breakdown rendered", "orcid 12" in body_text(rep, 1))
# `name_only` shares its denominator with `by_evidence`, not with `verified`:
# both count harvested records, and Section 1 states that denominator on the
# same line so the two can be checked against each other.
check_true("T06 the evidence line names the denominator it was counted over",
           "identity evidence on records: 25 of 31 harvested record(s)" in body_text(rep, 1))
# CAV-01 is about what the corpus is *missing* — name matches that were dropped —
# so it takes `rejected`, not `name_only`. It used to take `name_only`, a key
# nothing produced, and read "0 further papers" on every run ever printed.
check_true("T06 CAV-01 takes the rejected count, matching its own wording",
           rep["caveats"]["CAV-01"].startswith("0 further papers"))
_missing = dict(clean, counts={"by_evidence": {"orcid": 3}})
check_true("T06 CAV-01 says so rather than printing 0 when nothing was recorded",
           build_from(_missing)["caveats"]["CAV-01"].startswith("An unrecorded number of"))
check_true("T06 ...and the evidence line says not recorded rather than name_only 0",
           "identity evidence on records: not recorded" in body_text(build_from(_missing), 1))

unfiltered_unknown = corpus([
    paper(2000 + i, [author(distinct("Trainee", i), affiliation=INTERNAL), pi()], "2023 Mar", pi_index=1)
    for i in range(6)
])
del unfiltered_unknown["position_filtered"]
rep = build_from(unfiltered_unknown)
check("T07 absent position_filtered treated as true", rep["provenance"]["position_filtered"], True)
check_false("T07 section 7 is not measured", rep["metrics"]["s7"]["measured"])
check_true("T07 section 7 carries CAV-13", caveats.CAVEATS["CAV-13"] in caveat_text(rep, 7))
# 0-14 are the original spec sections; 15 (citation impact) and 16 (composite
# score and star band) were added when the no-citation, no-score rule was
# reversed; 17 (graduates on record), 18 (journal-level metrics) and 20 (student
# evaluations) came with the three hand-filled external tables. All three render
# whether or not their table was supplied — without one they print the refusal
# and what it costs — so the count is the same for every corpus, not only for a
# fully supplied one. 19 (co-author clusters) needs no external input at all and
# always renders; below its floor of 5 records it prints why it did not
# partition rather than vanishing, for the same reason 17, 18 and 20 do.
check("T07 every other section still renders", len(rep["sections"]), 21)
check("T07 the three external-data sections render without their tables",
      sorted(s["id"] for s in rep["sections"] if s["id"] in (17, 18, 20)), [17, 18, 20])

rep = report.build_report_from_path("pubmed_results/papers_20260722_120000.xlsx", {}, None, FIXED_NOW)
check("T08 spreadsheet path refuses", rep["gate"]["id"], "G4")
check_true("T08 the spreadsheet was never opened", rep["refused"])
check("T08 a refused report still carries the warnings key, empty", rep["warnings"], [])


# ============================================================
# 11.1b Two sources, two denominators
# ============================================================

print("\n--- PubMed corpus versus merged corpus (T09) ---")

# A PubMed-only harvest must say so in words. A blank where the second source
# would be reads as "the merge found nothing", which is a claim about the author
# rather than about the run.
solo = corpus([paper(1, [author("Liu Hua"), pi()], pi_index=1)])
check_true("T09 a PubMed-only corpus says the corpus is PubMed only",
           "corpus sources: PubMed only" in body_text(build_from(solo), 1))
check_true("T09 ...and says how a second source would be added",
           "--openalex-works" in body_text(build_from(solo), 1))
check_true("T09 the coverage line is labelled as PubMed's, not the corpus's",
           "PubMed corpus coverage: retrieved" in body_text(build_from(solo), 1))
check_false("T09 no OpenAlex line appears when there was no OpenAlex",
            "openalex author id in use" in body_text(build_from(solo), 1))

_merged_papers = [
    paper(2000 + i, [author(distinct("Trainee", i), affiliation=INTERNAL), pi()],
          "2023 Mar", pi_index=1)
    for i in range(9)
]
merged = corpus(_merged_papers)
merged["counts"] = {"fetched": 20, "verified": 9, "name_only": 0, "rejected": 11,
                    "by_evidence": {"orcid": 7, "openalex": 2},
                    "by_source": {"pubmed": 4, "both": 3, "openalex": 2}}
merged["identity"] = {**IDENTITY, "openalex_author_id": "A5023888391"}
merged["query"]["openalex"] = {
    "resolution": "unique", "candidates": [
        {"openalex_author_id": "A5023888391", "display_name": "Guangwei Zhu",
         "orcid": "0000-0002-1825-0097", "works_count": 43,
         "institutions": [{"display_name": "Example University"}]},
    ],
    "query": "display_name.search:Zhu Guangwei", "source": "openalex-authors-api",
    "retrieved_at": "2026-08-22T10:00:00",
}
merged["query"]["openalex_works"] = {
    "merged": True, "works_matched": 43, "works_returned": 43, "pages_fetched": 1,
    "max_works": 2000, "works_usable": 5, "works_without_lead_slot": 38,
    "matched_on": {"doi": 3, "pmid": 0, "title_year": 0},
}
rep = build_from(merged)
_body = body_text(rep, 1)

check_true("T09 the merged split is printed",
           "9 harvested record(s) = 4 PubMed only + 2 OpenAlex only + 3 held by both" in _body)
check_true("T09 both denominators are named as different numbers",
           "the PubMed corpus is 7 record(s), the merged corpus is 9" in _body)
# The three source counts are over the harvested file; corpus_size is what
# survives the exclusions listed just below them. Printing the second as the sum
# of the first three is an equation that stops adding up on any real corpus.
check_true("T09 and the post-exclusion denominator is named separately",
           f"every count elsewhere in this report is over the "
           f"{rep['provenance']['corpus_size']} record(s) that survived them" in _body)
check_true("T09 the OpenAlex author id is printed", "A5023888391" in _body)
check_true("T09 ...attributed to OpenAlex rather than to the researcher",
           "not the researcher's assertion" in _body)
check_true("T09 ...with the query, the source and the date it was fetched",
           "source openalex-authors-api; retrieved 2026-08-22T10:00:00" in _body)
check_true("T09 the works coverage is a numerator over a denominator",
           "retrieved 43 of 43 works OpenAlex files under this author id" in _body)
check_true("T09 ...and says how many were dropped for holding no slot",
           "dropping 38 where this author holds no first / last / corresponding slot" in _body)
check_true("T09 the merge keys that matched are printed",
           "DOI 3, PMID 0, title+year 0" in _body)
check_true("T09 ...with the one that can be wrong named as such",
           "title+year is the only one that can be wrong" in _body)
check_true("T09 the new evidence tier reaches the histogram", "openalex 2" in _body)
check("T09 the OpenAlex id reaches the machine-readable provenance",
      rep["provenance"]["identity"]["openalex_author_id"], "A5023888391")
check("T09 a corpus resolved by OpenAlex raises no identity warning", rep["warnings"], [])

# Ambiguity is carried through to the page rather than resolved on the way.
ambiguous = corpus([paper(1, [author("Liu Hua"), pi()], pi_index=1)])
ambiguous["query"]["openalex"] = {
    "resolution": "ambiguous", "openalex_author_id": "",
    "candidates": [
        {"openalex_author_id": "A111", "display_name": "Wei Zhang", "orcid": "",
         "works_count": 8, "institutions": [{"display_name": "Provincial Hospital"}]},
        {"openalex_author_id": "A222", "display_name": "Wei Zhang", "orcid": "",
         "works_count": 250, "institutions": [{"display_name": "Institute of Chemistry"}]},
    ],
    "query": "display_name.search:Zhang Wei", "source": "openalex-authors-api",
    "retrieved_at": "2026-08-22T10:00:00",
}
_amb = body_text(build_from(ambiguous), 1)
check_true("T09 an unresolved name says so in bold",
           "**the name resolved to more than one OpenAlex author and none was adopted.**" in _amb)
check_true("T09 ...lists every candidate for the reader to pick from",
           "candidate 1/2" in _amb and "candidate 2/2" in _amb)
check_true("T09 ...names the flag that settles it", "--openalex-author-id <id>" in _amb)
check_true("T09 ...and says why nothing was picked automatically",
           "decides an identity question on a proxy" in _amb)
check_true("T09 the id in use is stated as absent rather than guessed",
           "openalex author id in use: (none)" in _amb)

# A failed works lookup and an author with no works are the same empty list and
# completely different facts. Only one of them says anything about the author.
failed_lookup = corpus([paper(1, [author("Liu Hua"), pi()], pi_index=1)])
failed_lookup["query"]["openalex_works"] = {
    "merged": True, "request_failed": True, "works_matched": 0, "works_returned": 2,
    "pages_fetched": 1, "max_works": 2000, "works_usable": 2,
    "works_without_lead_slot": 0, "matched_on": {"doi": 0, "pmid": 0, "title_year": 0},
}
_failed_body = body_text(build_from(failed_lookup), 1)
check_true("T09 a failed works lookup says so in bold",
           "**the OpenAlex works lookup failed after 1 page(s)" in _failed_body)
check_true("T09 ...and refuses to be read as an author with no works",
           "not an author with no works" in _failed_body)
check_false("T09 ...so it never prints retrieved 0 of 0",
            "retrieved 2 of 0 works" in _failed_body)

# T09b. `by_source` is read out of a JSON file and gets `int()` and `+` applied
# to it. `by_evidence` and `name_only` — added in the same change, three lines
# away — were guarded and this one was not: `int("six")` raised ValueError and
# `"pubmed".get` raised AttributeError, both of them out through `build_report`
# and out of `main()`, which has no handler. `cli.py`'s profile path passes a
# corpus file's own `counts` block straight through when the file carries no
# `search` key, so both were one command line away.


def _by_source(value):
    """Section 1's body for a corpus whose `by_source` is `value`.

    Returns the traceback's class name in place of the body when one escapes, so
    a suite run against a build without the guard reports every check rather than
    dying at the first one — which is what the defect being asserted here does.
    """
    built = corpus([paper(1, [author("Liu Hua"), pi()], pi_index=1)])
    built["counts"] = dict(built["counts"], by_source=value)
    try:
        return body_text(build_from(built), 1)
    except Exception as exc:  # noqa: BLE001 — the point is that nothing escapes
        return f"RAISED {type(exc).__name__}"


for _label, _bad in (("a string", "pubmed"), ("a list", ["pubmed"]), ("an int", 6),
                     ("string values", {"pubmed": "six"}),
                     ("float values", {"pubmed": 2.5}),
                     ("bool values", {"pubmed": True}),
                     ("null values", {"pubmed": None}),
                     ("nested values", {"pubmed": {"n": 6}}),
                     ("non-string keys", {7: 6})):
    _line = _by_source(_bad)
    check_true(f"T09b by_source as {_label} prints not recorded instead of raising",
               "corpus sources: not recorded" in _line)
    check_true(f"T09b ...and quotes the value back so the file can be found",
               repr(_bad) in _line)
    check_false(f"T09b ...and states no split it could not compute",
                "harvested record(s) =" in _line)
check_true("T09b ...and says the record counts elsewhere are unaffected",
           "counted from the records themselves" in _by_source("pubmed"))
# A numeric string is not coerced, matching `_is_count`'s rule for `name_only`:
# a count this module is allowed to add is a whole number and nothing else.
check_true("T09b a numeric string is not silently read as a number",
           "corpus sources: not recorded" in _by_source({"pubmed": "6"}))

# The other half of the same function, and the same disease as a raised
# exception: a source name the merge does not write was dropped out of an
# equation that still printed as if it balanced.
_unknown_source = _by_source({"pubmed": 4, "scopus": 6})
check_true("T09b a source name the merge does not write is counted into the total",
           "10 harvested record(s) = 4 PubMed only + 0 OpenAlex only + 0 held by both" in
           _unknown_source)
check_true("T09b ...and named rather than folded into one of the three",
           "6 under source name(s) the merge does not write (scopus 6)" in _unknown_source)
check_false("T09b ...so the equation never prints 0 = 0 + 0 + 0 over a file that says six",
            "0 harvested record(s) = 0 PubMed only" in _by_source({"scopus": 6}))
check_true("T09b a well-formed histogram still prints exactly as before",
           "7 harvested record(s) = 4 PubMed only + 0 OpenAlex only + 3 held by both" in
           _by_source({"pubmed": 4, "both": 3}))

# The block itself, one level up. Every reader of `counts` did `(...) or {}`,
# which turns None into a mapping and leaves a string to reach `.get`.
def _counts_block(value):
    built = corpus([paper(1, [author("Liu Hua"), pi()], pi_index=1)])
    built["counts"] = value
    try:
        return build_from(built), ""
    except Exception as exc:  # noqa: BLE001 — the point is that nothing escapes
        return None, type(exc).__name__


_bad_block_rep, _bad_block_raised = _counts_block("six")
check("T09b a counts block that is not a mapping raises nothing", _bad_block_raised, "")
check("T09b ...and does not refuse either", (_bad_block_rep or {}).get("gate", "?"), None)
_bad_block_body = body_text(_bad_block_rep, 1) if _bad_block_rep else ""
check_true("T09b ...it says what the file held",
           "provenance counts: not recorded" in _bad_block_body
           and "counts='six'" in _bad_block_body)
check_true("T09b ...and every number taken from it reads as unrecorded",
           "identity evidence on records: not recorded" in _bad_block_body)
check_true("T09b ...including the caveat that used to print a confident 0",
           (_bad_block_rep or {"caveats": {"CAV-01": ""}})["caveats"]["CAV-01"]
           .startswith("An unrecorded number of"))
check_false("T09b a well-formed block prints no such line",
            "provenance counts: not recorded" in body_text(build_from(solo), 1))
check("T09b a counts block that is a list is treated the same way",
      _counts_block([1, 2])[1], "")

# The last key under `counts` that was read without being checked first.
# `check_corpus_gates` did `_gate("G6", observed, **observed)`, splatting a block
# read straight out of a JSON file into `str.format`: a block missing one of the
# three operands raised KeyError and a block carrying a key named `gate_id` or
# `observed` raised TypeError — both out through `build_report` and out of
# `main()`, which has no handler, and both one command line away because `cli.py`
# passes a corpus file's own `counts` block through untouched when the file
# carries no `search` key.
_G6_RECORD = {"fetched": 10, "verified": 30, "rejected_would_be": -20}


def _inconsistent(value):
    """(report, raised) for a corpus whose `counts["inconsistent"]` is `value`."""
    built = corpus([paper(1, [author("Liu Hua"), pi()], pi_index=1)])
    built["counts"] = dict(built["counts"], inconsistent=value)
    try:
        return build_from(built), ""
    except Exception as exc:  # noqa: BLE001 — the point is that nothing escapes
        return None, type(exc).__name__


for _label, _bad in (("one operand missing", {"fetched": 10, "verified": 30}),
                     ("two operands missing", {"verified": 30}),
                     ("no operand at all", {"pubmed": 6}),
                     ("null operands", {"fetched": None, "verified": None,
                                        "rejected_would_be": None}),
                     ("string operands", {"fetched": "ten", "verified": "thirty",
                                          "rejected_would_be": "minus twenty"}),
                     ("bool operands", {"fetched": True, "verified": True,
                                        "rejected_would_be": True}),
                     ("list operands", {"fetched": [10], "verified": [30],
                                        "rejected_would_be": [-20]})):
    _rep, _raised = _inconsistent(_bad)
    check(f"T09b inconsistent as {_label} raises nothing", _raised, "")
    check(f"T09b ...and still refuses, because the block is the record that they contradict",
          ((_rep or {}).get("gate") or {}).get("id"), "G6")
    _msg = ((_rep or {}).get("gate") or {}).get("message", "")
    check_true(f"T09b ...with a sentence that claims no number it does not have",
               "not all present as whole numbers" in _msg)
    check_true(f"T09b ...and the block quoted back so the file can be found",
               repr(_bad) in ((_rep or {}).get("gate") or {}).get("observed", {})
               .get("inconsistent", ""))

# The other half of the same defect, and the reason only the three named slots
# are passed to `str.format` rather than the whole block: a file whose operands
# are perfectly readable could still carry a key that collided with `_gate`'s own
# parameters. `**observed` then raised TypeError before the sentence was built,
# and a non-string key raised it too. The three numbers survive all three.
for _label, _bad in (("a key named gate_id", dict(_G6_RECORD, gate_id="X")),
                     ("a key named observed", dict(_G6_RECORD, observed="X")),
                     ("a key named message", dict(_G6_RECORD, message="X")),
                     ("a non-string key", {7: 1, **_G6_RECORD})):
    _rep, _raised = _inconsistent(_bad)
    check(f"T09b inconsistent carrying {_label} raises nothing", _raised, "")
    check(f"T09b ...and still refuses", ((_rep or {}).get("gate") or {}).get("id"), "G6")
    check_true(f"T09b ...with the three operands still stated",
               "verified=30 against fetched=10"
               in ((_rep or {}).get("gate") or {}).get("message", ""))

# The shape `cli._corpus_counts` actually writes is untouched: the three numbers
# are still stated, and extra keys beside them stay visible without reaching
# `str.format`.
_good_rep, _ = _inconsistent(_G6_RECORD)
check("T09b the shape harvest writes still refuses",
      (_good_rep["gate"] or {})["id"], "G6")
check_true("T09b ...and still states both operands and the difference",
           "verified=30 against fetched=10" in _good_rep["gate"]["message"]
           and "would be -20" in _good_rep["gate"]["message"])
_extra_rep, _ = _inconsistent(dict(_G6_RECORD, corpus_total=30))
check_true("T09b an extra key beside the three does not cost the three numbers",
           "verified=30 against fetched=10" in _extra_rep["gate"]["message"])
check("T09b ...and stays visible in observed",
      _extra_rep["gate"]["observed"].get("corpus_total"), 30)

# A non-mapping under the same key fires nothing — `_corpus_counts` writes a
# mapping and only a mapping, so refusing a whole report over a value whose
# meaning is unknown would invent a verdict. It is named rather than dropped.
for _label, _bad in (("a string", "yes"), ("a list", ["yes"]), ("an int", 1),
                     ("a bool", True)):
    _rep, _raised = _inconsistent(_bad)
    check(f"T09b inconsistent as {_label} raises nothing", _raised, "")
    check(f"T09b ...and invents no verdict", (_rep or {}).get("gate", "?"), None)
    _line = body_text(_rep, 1) if _rep else ""
    check_true(f"T09b ...but says the file carries something there",
               "recorded count inconsistency: not recorded" in _line)
    check_true(f"T09b ...quoting it back", f"counts.inconsistent={_bad!r}" in _line)
check_false("T09b a corpus with no such key prints no such line",
            "recorded count inconsistency" in body_text(build_from(solo), 1))
check("T09b an empty mapping is not a recorded inconsistency",
      (_inconsistent({})[0] or {}).get("gate", "?"), None)
check_false("T09b ...and prints no line about it",
            "recorded count inconsistency" in body_text(_inconsistent({})[0], 1))


# ============================================================
# 11.1b-2 The blocks beside `counts`, read out of the same file
#
# `counts` was guarded key by key and the blocks next to it were not. `identity`,
# `query` and the four nested OpenAlex blocks under `query` come out of the same
# JSON file, are read by the same report layer, and reach it on the same command
# line — `cli.py` hands a corpus file straight to `build_report` whenever the
# file carries no `search` key. Every one of them raised out through `main()`,
# which has no handler: `.strip()` on a float, `len()` on a None, `list()` on an
# int, `.get` on a string, `len()` on a bool.
#
# The list below is not the four that were reported. It is every path a sweep of
# a fully-populated corpus found — temp/cya_verify/h8_report_field_sweep.py, 104
# paths crossed with 14 shapes — which is how the fields adjacent to the reported
# ones are in here too.
#
# None of these refuses, and that is the decision rather than an omission. A
# refusal is for a corpus with nothing to compute over (G4, G5) or one whose own
# counts make every denominator wrong (G6). An identity field that cannot be read
# costs the identity claim and not one count: G3 already raises exactly that, at
# warning strength, exiting 1. Refusing would discard twenty sections of true
# counts over a field none of them is computed from.
# ============================================================

print("\n--- unreadable identity / query blocks (T09d) ---")

_D_PAPERS = [paper(3000 + i, [author(distinct("Tr", i), affiliation=INTERNAL), pi()],
                   "2023 Mar", pi_index=1) for i in range(3)]
_D_RESOLUTION = {
    "resolution": "unique", "query": "display_name.search:Chen Xiuying",
    "source": "openalex-authors-api", "retrieved_at": "2026-08-22T10:00:00",
    "candidates": [{"openalex_author_id": "A5023888391", "display_name": "Xiuying Chen",
                    "orcid": ORCID, "works_count": 43,
                    "institutions": [{"display_name": "Nanhai Medical University"}]}],
}
_D_WORKS = {"merged": True, "works_matched": 43, "works_returned": 43, "pages_fetched": 1,
            "max_works": 2000, "works_usable": 5, "works_without_lead_slot": 38,
            "matched_on": {"doi": 3, "pmid": 0, "title_year": 0}}


def _d_corpus():
    """A corpus with every optional block present, rebuilt from literals each call."""
    built = corpus([dict(p) for p in _D_PAPERS])
    built["identity"] = {**IDENTITY, "openalex_author_id": "A5023888391"}
    built["query"] = {**built["query"],
                      "openalex": {**_D_RESOLUTION,
                                   "candidates": [dict(_D_RESOLUTION["candidates"][0])]},
                      "openalex_works": {**_D_WORKS, "matched_on": dict(_D_WORKS["matched_on"])}}
    return built


def _at(path, value):
    """`_d_corpus()` with the dotted `path` set to `value`. Digits index a list."""
    built = _d_corpus()
    node = built
    parts = path.split(".")
    for part in parts[:-1]:
        node = node[int(part)] if part.isdigit() else node[part]
    leaf = parts[-1]
    node[int(leaf) if leaf.isdigit() else leaf] = value
    return built


def _built(path, value):
    """(report, raised) for one mutated corpus.

    Returns the traceback's class name rather than propagating, so a suite run
    against a build without the guards reports every check instead of dying at
    the first one — the same rule `_by_source` follows above.
    """
    try:
        return build_from(_at(path, value)), ""
    except Exception as exc:  # noqa: BLE001 — the point is that nothing escapes
        return None, type(exc).__name__


#: Shapes a JSON file can hold where a string belongs.
_BAD_TEXT = (("a bool", True), ("an int", 7), ("a float", 3.5),
             ("a list", [ORCID]), ("a mapping", {"id": ORCID}))
#: ...where a list of strings belongs. A bare string is in here on purpose: it is
#: iterable, so it never raised, and `list("Nanhai Med Univ")` silently became
#: fifteen single-character keywords, each of which matches nearly every
#: affiliation string in PubMed. A typo rendered as a verified identity is worse
#: than the traceback beside it.
_BAD_LIST = (("a bool", True), ("an int", 7), ("a float", 3.5), ("a null", None),
             ("a bare string", "Nanhai Med Univ"), ("a mapping", {"kw": "Nanhai"}),
             ("a list holding an int", ["Nanhai", 7]),
             ("a list holding a mapping", [{"kw": "Nanhai"}]))
#: ...where a mapping belongs. `or {}` covered only the first of these.
_BAD_BLOCK = (("a bool", True), ("an int", 7), ("a float", 3.5),
              ("a string", "unique"), ("a list", [1, 2]))
#: ...where a list of mappings belongs.
_BAD_RECORDS = (("a bool", True), ("an int", 7), ("a string", "unique"),
                ("a mapping", {"display_name": "X"}), ("a list of ints", [1, 2]),
                ("a list of strings", ["A111"]), ("a list holding a null", [None]))

# --- identity, field by field ---
for _field in ("orcid", "openalex_author_id", "author_name"):
    for _label, _bad in _BAD_TEXT:
        _rep, _raised = _built(f"identity.{_field}", _bad)
        check(f"T09d identity.{_field} as {_label} raises nothing", _raised, "")
        check(f"T09d ...and does not refuse", (_rep or {}).get("gate", "?"), None)
        _line = body_text(_rep, 1) if _rep else ""
        check_true(f"T09d ...it says the field was not recorded",
                   "corpus fields not recorded" in _line)
        check_true(f"T09d ...and quotes the value back so the file can be found",
                   f"identity.{_field}={_bad!r}" in _line)

for _field in ("affiliation_keywords", "email_domains"):
    for _label, _bad in _BAD_LIST:
        _rep, _raised = _built(f"identity.{_field}", _bad)
        check(f"T09d identity.{_field} as {_label} raises nothing", _raised, "")
        check(f"T09d ...and does not refuse", (_rep or {}).get("gate", "?"), None)
        _line = body_text(_rep, 1) if _rep else ""
        if _bad is None:
            # Absent is not a defect. `None` is what every reader of these keys
            # already meant by "not configured", and reporting it would print a
            # line about a file that is merely incomplete.
            check_false(f"T09d ...a null is treated as absent, not as unreadable",
                        "corpus fields not recorded" in _line)
        else:
            check_true(f"T09d ...it says the field was not recorded",
                       "corpus fields not recorded" in _line)
            check_true(f"T09d ...and quotes the value back",
                       f"identity.{_field}={_bad!r}" in _line)
        check_true(f"T09d ...and the count line prints a number rather than raising",
                   f"{_field}=0" in _line)

# --- the identity block itself ---
for _label, _bad in _BAD_BLOCK:
    _rep, _raised = _built("identity", _bad)
    check(f"T09d an identity block that is {_label} raises nothing", _raised, "")
    check(f"T09d ...and does not refuse", (_rep or {}).get("gate", "?"), None)
    _line = body_text(_rep, 1) if _rep else ""
    check_true(f"T09d ...it names the whole block", f"identity={_bad!r}" in _line)
    check_true(f"T09d ...and the identity line still prints",
               "- identity: orcid=(none)" in _line)

# The one decision an unreadable field changes, and the reason the raw value is
# carried rather than the field being emptied in silence: G3 tells its three
# situations apart by whether anything was configured, and a file holding
# `orcid: 3.5` is not a harvest that was configured with nothing.
def _only_evidence(**identity_fields):
    """A corpus whose only configured identity evidence is `identity_fields`.

    Built without keywords, domains or an author id so that G3's situation is
    decided by the one field under test rather than by whatever else happened to
    match a byline.
    """
    built = corpus([paper(3100 + i, [author(distinct("Nb", i)), pi(affiliation="", orcid="")],
                          "2023 Mar", pi_index=1) for i in range(3)])
    built["identity"] = {"author_name": TARGET, "orcid": "", "affiliation_keywords": [],
                         "email_domains": [], "openalex_author_id": "",
                         "require_affiliation_effective": False, **identity_fields}
    return report.build_report(built, {}, None, FIXED_NOW)


_bad_orcid = _only_evidence(orcid=3.5)
_g3 = next((w for w in _bad_orcid["warnings"] if w["id"] == "G3"), {})
check("T09d an unreadable identity field still raises G3", _g3.get("id"), "G3")
check_true("T09d ...as 'configured and reached no record'",
           "Identity evidence was configured and reached no record" in _g3.get("message", ""))
check_false("T09d ...never as 'no identity evidence was configured'",
            "No identity evidence was configured" in _g3.get("message", ""))
check_true("T09d ...with the raw value in the warning's own observed values",
           "identity_fields_unreadable=orcid=3.5" in _g3.get("observed_text", ""))
check("T09d ...and the report still exits 1 rather than refusing",
      (_bad_orcid["exit_code"], _bad_orcid["gate"]), (1, None))
# A list is quoted with repr, never summarised: `_flat_observed` renders a list
# as its length, which would print `affiliation_keywords=2` over a file holding
# `[1, 2]` and turn the defect into a plausible measurement.
_bad_kw = _only_evidence(affiliation_keywords=[1, 2])
check_true("T09d an unreadable keyword list is quoted, not counted",
           "identity_fields_unreadable=affiliation_keywords=[1, 2]"
           in next(w for w in _bad_kw["warnings"] if w["id"] == "G3")["observed_text"])
# The reverse case, which is the reason the raw values are kept at all: a file
# that really did configure nothing keeps the sentence about configuring nothing.
check_true("T09d a corpus that configured nothing still says so",
           "No identity evidence was configured"
           in next(w for w in _only_evidence()["warnings"] if w["id"] == "G3")["message"])
check_false("T09d ...and reports no unreadable field",
            "identity_fields_unreadable"
            in next(w for w in _only_evidence()["warnings"] if w["id"] == "G3")["observed_text"])

# --- query, and the four blocks nested under it ---
for _label, _bad in _BAD_BLOCK:
    _rep, _raised = _built("query", _bad)
    check(f"T09d a query block that is {_label} raises nothing", _raised, "")
    check(f"T09d ...and does not refuse", (_rep or {}).get("gate", "?"), None)
    _line = body_text(_rep, 1) if _rep else ""
    check_true(f"T09d ...it names the block", f"query={_bad!r}" in _line)
    check_true(f"T09d ...and the window falls back to the records themselves",
               "window used: 2023 to 2023" in _line)

for _block, _name in (("openalex", "author resolution"), ("openalex_works", "works lookup")):
    for _label, _bad in _BAD_BLOCK:
        _rep, _raised = _built(f"query.{_block}", _bad)
        check(f"T09d query.{_block} as {_label} raises nothing", _raised, "")
        check(f"T09d ...and does not refuse", (_rep or {}).get("gate", "?"), None)
        _line = body_text(_rep, 1) if _rep else ""
        check_true(f"T09d ...it says the block was not recorded",
                   f"openalex {_name}: not recorded" in _line)
        check_true(f"T09d ...and quotes it back under the key the file spells",
                   f"query.{_block}={_bad!r}" in _line)
        check_true(f"T09d ...while the id the corpus was harvested under still prints",
                   "openalex author id in use: A5023888391" in _line)

for _label, _bad in _BAD_RECORDS:
    _rep, _raised = _built("query.openalex.candidates", _bad)
    check(f"T09d openalex candidates as {_label} raises nothing", _raised, "")
    _line = body_text(_rep, 1) if _rep else ""
    check_true(f"T09d ...it says the list was not recorded",
               "openalex candidates: not recorded" in _line)
    check_true(f"T09d ...and quotes it back", f"candidates={_bad!r}" in _line)
    # `?` and not 0: a printed 0 is a claim that OpenAlex returned nothing, which
    # would contradict the line directly above it.
    check_true(f"T09d ...and the resolution line counts them as ?, never as 0",
               "unique (? candidate(s))" in _line)
    check_false(f"T09d ...and writes no per-candidate line", "  - candidate " in _line)

for _label, _bad in _BAD_RECORDS:
    _rep, _raised = _built("query.openalex.candidates.0.institutions", _bad)
    check(f"T09d a candidate's institutions as {_label} raises nothing", _raised, "")
    _line = body_text(_rep, 1) if _rep else ""
    check_true(f"T09d ...the candidate is still listed",
               "candidate 1/1: Xiuying Chen" in _line)
    check_true(f"T09d ...with institutions stated as not recorded rather than blank",
               "institutions not recorded)" in _line)

for _label, _bad in _BAD_BLOCK:
    _rep, _raised = _built("query.openalex_works.matched_on", _bad)
    check(f"T09d a works block's matched_on as {_label} raises nothing", _raised, "")
    _line = body_text(_rep, 1) if _rep else ""
    check_true(f"T09d ...and every key under it reads as unrecorded",
               "DOI ?, PMID ?, title+year ?" in _line)

# The happy path is untouched: a well-formed corpus prints none of these lines,
# and the numbers it does print are the ones the block above already pinned.
_clean = build_from(_d_corpus())
_clean_line = body_text(_clean, 1)
check("T09d a well-formed corpus raises no identity warning", _clean["warnings"], [])
check_false("T09d ...and prints no 'fields not recorded' line",
            "corpus fields not recorded" in _clean_line)
check_false("T09d ...nor an unreadable openalex block line",
            "not recorded — this corpus file carries openalex" in _clean_line)
check_true("T09d ...and still counts its one candidate as 1",
           "unique (1 candidate(s))" in _clean_line)
check_true("T09d ...with the institution printed",
           "institutions Nanhai Medical University)" in _clean_line)
check_true("T09d ...and matched_on printed", "DOI 3, PMID 0, title+year 0" in _clean_line)
check("T09d ...and the identity block reaches provenance unchanged",
      (_clean["provenance"]["identity"]["orcid"],
       _clean["provenance"]["identity"]["affiliation_keywords"]),
      (ORCID, IDENTITY["affiliation_keywords"]))
check("T09d ...with nothing recorded as unreadable",
      _clean["provenance"]["unreadable"], {})


# ============================================================
# 11.1c The target name, and whether it is on these records at all
# ============================================================

print("\n--- target name reach (T09c) ---")

# The hole none of the three readers of a corpus covered. `cli._corpus_counts`
# derives the evidence histogram from the role string and
# `openalex_id_record_share` counts an author id at record level; neither
# consults the name. Profile a corpus under a name nobody on it holds and both
# went on printing `N of N` while `roles.resolve_pi` rejected every record — and
# the page said nothing at all: exit 0, no banner, and the only visible
# difference was one extra person in a roster nobody can check.
_wrong_name = corpus([paper(3100 + i, [author(distinct("Trainee", i)), pi()], pi_index=None)
                      for i in range(6)])
_wrong_rep = report.build_report(dict(_wrong_name, identity={**IDENTITY,
                                                             "author_name": "Nakamura Hiroshi"}),
                                 {}, None, FIXED_NOW)
# Indexed through a default, matching T01's `(rep["warnings"] or [{}])[0]`: a
# build where the warning does not fire is exactly the state these checks exist
# to catch, and it must report as a list of failures rather than as an IndexError
# that hides every check below it.
_g7 = (_wrong_rep["warnings"] or [{}])[0]
check("T09c a name that is on no byline is not refused", _wrong_rep["refused"], False)
check("T09c it is a warning instead", [w["id"] for w in _wrong_rep["warnings"]], ["G7"])
check("T09c the exit code reflects it", _wrong_rep["exit_code"], 1)
check("T09c the warning keeps one name", _g7.get("name"), "target name not located")
check("T09c it lands on sections 0, 1, 2 and 7", _g7.get("sections"), [0, 1, 2, 7])
check("T09c ...and nowhere else",
      sorted(s["id"] for s in _wrong_rep["sections"] if s["warnings"]), [0, 1, 2, 7])
check("T09c the observed values name the ratio the sentence claims",
      _g7.get("observed", {}).get("target_name_on_records"), "0/6")
check("T09c ...and the name that was looked for",
      _g7.get("observed", {}).get("target_name"), "Nakamura Hiroshi")
check_true("T09c the banner opens each section it affects",
           all((section(_wrong_rep, sid)["warnings"] or [""])[0].startswith("**Warning G7")
               for sid in (0, 1, 2, 7)))
check_true("T09c the fix is to correct the name rather than to re-harvest",
           "Nothing needs re-harvesting" in _g7.get("fix", ""))

# The line prints on every run, warning or not. A corpus the name reached two of
# forty records of raises nothing — there is no boundary to raise it against —
# and the ratio is the only thing that makes it visible.
_right_rep = build_from(corpus([paper(3200 + i, [author(distinct("Trainee", i)), pi()],
                                      pi_index=1) for i in range(6)]))
check("T09c a name that is on every byline raises nothing", _right_rep["warnings"], [])
check_true("T09c ...and the ratio is printed anyway",
           "target name on records: 6 of 6 harvested record(s) carry `Chen Xiuying` on a byline"
           in body_text(_right_rep, 1))
check_true("T09c a name on no byline prints the zero over the same denominator",
           "target name on records: 0 of 6 harvested record(s) carry `Nakamura Hiroshi` on a "
           "byline" in body_text(_wrong_rep, 1))
check_true("T09c ...and says why the two ratios beside it disagree with it",
           "the only count in this section that looks at the name at all"
           in body_text(_wrong_rep, 1))
check("T09c the ratio reaches the machine-readable provenance",
      _wrong_rep["provenance"].get("target_name_records"), [0, 6])
_partial = corpus([paper(3300, [author("Liu Hua"), pi()], pi_index=1)]
                  + [paper(3301 + i, [author(distinct("Other", i)), author("Sun Qi")],
                           pi_index=None) for i in range(5)])
_partial_rep = build_from(_partial)
check("T09c a name reaching a minority of the corpus raises nothing",
      _partial_rep["warnings"], [])
check_true("T09c ...and the ratio is what makes it visible",
           "target name on records: 1 of 6" in body_text(_partial_rep, 1))

# No name at all is a different situation with a different sentence.
_nameless = dict(corpus([paper(3400 + i, [author(distinct("Trainee", i)), pi()], pi_index=None)
                         for i in range(6)]),
                 identity={**IDENTITY, "author_name": ""})
_nameless_rep = report.build_report(_nameless, {}, None, FIXED_NOW)
check("T09c no configured name raises G7 too", [w["id"] for w in _nameless_rep["warnings"]], ["G7"])
check("T09c ...with the sentence written for it",
      (_nameless_rep["warnings"] or [{}])[0].get("message"),
      report.G7_SITUATIONS["no_name_configured"])
check_true("T09c ...and the line says no name was configured rather than printing an empty pair",
           "(no target name was configured)" in body_text(_nameless_rep, 1))
check("T09c the two situations are told apart", len(set(report.G7_SITUATIONS.values())), 2)

# `target_name_record_reach` counts with the matcher `resolve_pi` builds its
# candidates with, so the printed number and the records that got a located PI
# cannot disagree — including on the reversed name order PubMed also publishes.
_reach_papers = [paper(3500, [author("Liu Hua"), pi()], pi_index=1),
                 paper(3501, [author("Liu Hua"), author("Xiuying Chen")], pi_index=None),
                 paper(3502, [author("Liu Hua"), author("Sun Qi")], pi_index=None)]
check("T09c the reverse name order counts, as it does for resolve_pi",
      report.target_name_record_reach(_reach_papers, TARGET), (2, 3))
check("T09c an empty name reaches nothing but keeps the corpus denominator",
      report.target_name_record_reach(_reach_papers, ""), (0, 3))
check("T09c a papers value that is not a list is no records rather than an exception",
      report.target_name_record_reach("six papers", TARGET), (0, 0))
check("T09c a record whose authors key is not a list is counted as not carrying the name",
      report.target_name_record_reach([{"pmid": "1", "authors": "Chen Xiuying"}], TARGET), (0, 1))
check("T09c an empty corpus raises no warning — G5 answers that better",
      report.check_name_warnings({"papers": []}, TARGET), [])
check("T09c ...and so does a corpus whose papers key is missing",
      report.check_name_warnings({}, TARGET), [])

# G7 was never a gate, and the two places that record a warning's history must
# not say it was. This register is the one place a reader can check that claim.
check("T09c the warning records that it was never downgraded", _g7.get("downgraded"), False)
check_true("T09c the banner does not claim it used to refuse reports",
           "has never refused a report" in (section(_wrong_rep, 0)["warnings"] or [""])[0])
check_false("T09c ...which is what every other warning's banner says",
            "used to refuse the whole report" in (section(_wrong_rep, 0)["warnings"] or [""])[0])
check_true("T09c Section 14 files it under conditions that were never gates",
           any("Raised on this run — conditions that were never gates" in line
               for line in section(_wrong_rep, 14)["prose"]))
check_false("T09c ...and not under the downgraded heading",
            any("Downgraded on this run" in line for line in section(_wrong_rep, 14)["prose"]))
check_true("T09c a downgraded warning still gets the downgraded heading",
           any("Downgraded on this run" in line
               for line in section(build_from(weak), 14)["prose"]))
check_true("T09c ...and still says it used to refuse",
           "used to refuse the whole report" in (section(build_from(weak), 0)["warnings"] or [""])[0])
# Both headings on one run, because a corpus can carry both kinds at once.
_both_kinds = dict(_wrong_name, identity={"author_name": "Nakamura Hiroshi", "orcid": "",
                                          "affiliation_keywords": [], "email_domains": []})
_both_rep = report.build_report(_both_kinds, {}, None, FIXED_NOW)
check("T09c a corpus can carry a never-gate and a downgraded warning at once",
      [w["id"] for w in _both_rep["warnings"]], ["G7", "G3"])
check_true("T09c ...and Section 14 prints both headings",
           any("Raised on this run" in line for line in section(_both_rep, 14)["prose"])
           and any("Downgraded on this run" in line for line in section(_both_rep, 14)["prose"]))
# The name question is settled before the other two, because a reader who
# settles them first has spent the effort on a corpus about somebody else.
check("T09c the name warning is printed before coverage and identity",
      ([w["id"] for w in _both_rep["warnings"]] or [None])[0], "G7")

# Same standing as every other warning on the comparison page: the row keeps its
# score and holds no position. Read through `ranking` directly because that layer
# treats the warning list generically, which is the property being asserted.
from check_your_advisor.profile import ranking as _ranking  # noqa: E402

_ranked = _ranking.rank_corpora([
    {"label": "wrong name", "score": _wrong_rep["score"], "warnings": _wrong_rep["warnings"],
     "refused": False},
])
check("T09c a corpus warned for its name takes no rank", _ranked["n_ranked"], 0)
check("T09c ...and is not dropped from the page either", len(_ranked["unranked"]), 1)
check_true("T09c ...and the row says which warning withheld it",
           "G7 (target name not located)" in _ranked["unranked"][0]["reason"])

# Which name the whole question is asked about. The config carries `--pi-name`,
# the corpus carries whatever name the harvest that wrote it was run under, and
# they are not always the same name. `cli._profile_corpus` states the precedence
# — the config wins, the recorded name fills the gap — and applies it, but only
# to a file that carries a `search` key. A file already in the Section 4 shape is
# handed to `build_report` untouched, and `build_report` had the precedence
# backwards, so `--pi-name` was read, logged and then discarded: the report
# measured, warned and exited on the name baked into the file, and every line
# that said "target name" named a person the user had not asked about.
_recorded_as = corpus([paper(3600 + i, [author(distinct("Trainee", i)), pi()], pi_index=1)
                       for i in range(6)])
_typed_rep = report.build_report(_recorded_as, {"author_name": "Nakamura Hiroshi"},
                                 None, FIXED_NOW)
check("T09c the name on the command line wins over the one baked into the corpus",
      _typed_rep["author_name"], "Nakamura Hiroshi")
check("T09c ...so a name that reached nothing is warned about, not swallowed",
      [w["id"] for w in _typed_rep["warnings"]], ["G7"])
check("T09c ...and the exit code reflects it", _typed_rep["exit_code"], 1)
check("T09c ...over the corpus's real denominator",
      (_typed_rep["warnings"] or [{}])[0].get("observed", {}).get("target_name_on_records"), "0/6")
check_true("T09c ...and Section 1 names the name that was asked for",
           "target name on records: 0 of 6 harvested record(s) carry `Nakamura Hiroshi`"
           in body_text(_typed_rep, 1))
check_false("T09c ...rather than the one the file records",
            "carry `Chen Xiuying`" in body_text(_typed_rep, 1))
# The other half of the same rule: with no name on the command line the recorded
# one still fills the gap, which is what lets `compare` read several corpora
# about different people without a name per directory.
_gap_rep = report.build_report(_recorded_as, {}, None, FIXED_NOW)
check("T09c with no configured name the corpus's own name is used", _gap_rep["author_name"], TARGET)
check("T09c ...and nothing is warned about", _gap_rep["warnings"], [])
_blank_rep = report.build_report(_recorded_as, {"author_name": ""}, None, FIXED_NOW)
check("T09c an empty configured name is not a name either", _blank_rep["author_name"], TARGET)
# A refusal is decided before the name is used for anything, and reports it under
# the same name the successful path would have.
_refused_typed = report.build_report(
    dict(_recorded_as, counts=dict(_recorded_as["counts"],
                                   inconsistent={"fetched": 1, "verified": 9,
                                                 "rejected_would_be": -8})),
    {"author_name": "Nakamura Hiroshi"}, None, FIXED_NOW)
check("T09c a refusal is filed under the name that was asked for",
      _refused_typed["author_name"], "Nakamura Hiroshi")


# ============================================================
# 11.2 PI resolution
# ============================================================

print("\n--- PI resolution (T09-T16) ---")

middle_paper = paper(1, [
    author("Liu Hua"), author("Wang Li"), author("Zhao Min"),
    pi(affiliation="", orcid=ORCID),
    author("Sun Qi"), author("Guo Yan"), author("Xu Bo"),
])
resolved = roles.resolve_pi(middle_paper, TARGET, IDENTITY)
check("T09 middle-author paper is verified", resolved["disposition"], "verified")
check("T09 PI index recorded", resolved["pi_index"], 3)
check("T09 evidence is ORCID", resolved["pi_evidence"], "orcid")

bare = paper(2, [author("Liu Hua"), pi(affiliation="", orcid="")])
resolved = roles.resolve_pi(bare, TARGET, IDENTITY)
check("T10 no identity evidence is name_only", resolved["disposition"], "name_only")
check("T10 name_only papers are not verified", resolved["disposition"] == "verified", False)

two_tiers = paper(3, [
    author(TARGET, affiliation=INTERNAL),
    author("Liu Hua"),
    author(TARGET, affiliation="", orcid=ORCID),
])
resolved = roles.resolve_pi(two_tiers, TARGET, IDENTITY)
check("T11 ORCID beats affiliation regardless of index", resolved["pi_index"], 2)
check("T11 evidence recorded as ORCID", resolved["pi_evidence"], "orcid")
check_false("T11 not ambiguous when tiers differ", resolved["pi_ambiguous"])

same_tier = paper(4, [
    author(TARGET, affiliation=INTERNAL),
    author("Liu Hua"),
    author(TARGET, affiliation=INTERNAL_VARIANT),
])
resolved = roles.resolve_pi(same_tier, TARGET, IDENTITY)
check("T12 tie breaks to the lowest index", resolved["pi_index"], 0)
check_true("T12 tie is flagged ambiguous", resolved["pi_ambiguous"])

sole_other = build([
    paper(1, [author("Liu Hua", affiliation=INTERNAL)], "2023 Feb", pi_index=None),
    paper(2, [author("Wang Li"), pi()], "2023 Mar", pi_index=1),
])
check("T13 sole author lands in the senior stratum", person_named(sole_other, "Liu Hua")["stratum"], "D")
check("T13 sole-author record is logged by PMID",
      sole_other["provenance"]["sole_author_papers"], ["1"])

sole_pi = build([
    paper(1, [pi()], "2023 Feb", pi_index=0),
    paper(2, [author("Wang Li"), pi()], "2023 Mar", pi_index=1),
])
check("T14 sole PI paper leaves the eligible set",
      sole_pi["metrics"]["s3a"]["dropped_pi_is_lead"], ["1"])
check("T14 PI byline position is sole", sole_pi["metrics"]["s7"]["counts"]["sole"], 1)

trailing_group = build([
    paper(1, [author("Wang Li"), pi(), author("Zhao Min"), collective("Nanhai Liver Study Group")],
          "2023 Mar", pi_index=1),
    paper(2, [author("Wang Li"), pi()], "2024 Mar", pi_index=1),
])
check("T15 person at n-2 becomes senior once the consortium is removed",
      person_named(trailing_group, "Zhao Min")["stratum"], "D")
check("T15 the consortium is not a person", person_named(trailing_group, "Nanhai Liver Study Group"), None)

leading_group = build([
    paper(1, [collective("Nanhai Liver Study Group"), author("Wang Li"), pi()], "2023 Mar", pi_index=2),
    paper(2, [author("Zhao Min"), pi()], "2024 Mar", pi_index=1),
])
check("T16 consortium in the lead slot leaves the eligible set",
      leading_group["metrics"]["s3a"]["dropped_slot0_collective"], ["1"])
check("T16 consortium lead slot is counted separately",
      leading_group["provenance"]["slot0_collective_pmids"], ["1"])
check("T16 nobody is credited with that lead slot",
      person_named(leading_group, "Wang Li")["n_first_slots"], 0)


# ============================================================
# 11.3 Record exclusions
# ============================================================

print("\n--- record exclusions (T17-T23) ---")

undated = build([
    paper(1, [author("Liu Hua"), pi()], "", pi_index=1),
    paper(2, [author("Liu Hua"), pi()], "2023 Mar", pi_index=1),
    paper(3, [author("Liu Hua"), pi()], "2024 Mar", pi_index=1),
])
check("T17 undated record excluded", undated["provenance"]["exclusions"]["unparseable_date"], ["1"])
check("T17 no fabricated century-long span", person_named(undated, "Liu Hua")["first_year"], 2023)

corrections = build([
    paper(1, [author("Liu Hua"), pi()], "2023 Mar", pi_index=1,
          title="Correction to: Hepatic stellate cell activation"),
    paper(2, [author("Liu Hua"), pi()], "2024 Mar", pi_index=1,
          title="Corrective surgery outcomes in biliary atresia"),
])
check("T18 correction record excluded",
      corrections["provenance"]["exclusions"]["correction_or_comment"], ["1"])
check("T19 word-anchored regex spares 'Corrective'", corrections["provenance"]["corpus_size"], 1)

hyper_authors = [author(distinct("Consort", i, "Member")) for i in range(60)]
hyper = build([
    paper(1, [*hyper_authors, pi(), author("Tail Person")], "2023 Mar", pi_index=60),
    paper(2, [author("Liu Hua"), pi()], "2024 Mar", pi_index=1),
    paper(3, [author("Wang Li"), pi()], "2025 Mar", pi_index=1),
])
check("T20 hyperauthorship record excluded and listed",
      hyper["provenance"]["exclusions"]["hyperauthorship"], ["1"])
check("T20 its authors leave person-level analysis", person_named(hyper, "Consort Member00"), None)
check("T20 it leaves the team-size denominator", hyper["metrics"]["s10"]["denominator"], 2)
check("T20 it leaves the eligible set", hyper["metrics"]["s3a"]["denominator"], 2)
check("T20 it still counts in records per year", hyper["metrics"]["s9"]["denominator"], 3)

large_team = build([
    paper(1, [author("Lead Person"), pi(), *[author(distinct("Team", i, "Member")) for i in range(20)]],
          "2023 Mar", pi_index=1),
    *[paper(10 + i, [author("Liu Hua"), pi()], "2024 Mar", pi_index=1) for i in range(4)],
])
check("T21 a 22-author record is retained", large_team["provenance"]["corpus_size"], 5)
check("T21 counted on the 20-or-more line", large_team["metrics"]["s10"]["large_team_count"], 1)

doi_dupes = build([
    paper(200, [author("Liu Hua"), pi()], "2023 Mar", pi_index=1, doi="10.1234/ABC.001"),
    paper(100, [author("Liu Hua"), pi()], "2023 Apr", pi_index=1, doi="10.1234/abc.001"),
    paper(300, [author("Wang Li"), pi()], "2024 Mar", pi_index=1),
])
check("T22 duplicate DOI keeps the lower PMID",
      doi_dupes["provenance"]["exclusions"]["duplicate_doi"], ["200"])
check("T22 one record survives the DOI pair", doi_dupes["provenance"]["corpus_size"], 2)

title_dupes = build([
    paper(1, [author("Liu Hua"), pi()], "2023 Mar", pi_index=1, doi="10.1/a",
          title="Hepatic stellate cell activation in fibrosis"),
    paper(2, [author("Liu Hua"), pi()], "2023 Sep", pi_index=1, doi="10.1/b",
          title="Hepatic stellate cell activation in fibrosis."),
])
check("T23 identical titles are both retained", title_dupes["provenance"]["corpus_size"], 2)
check("T23 identical titles are flagged, not merged",
      title_dupes["provenance"]["title_duplicates"], [["1", "2"]])


# ============================================================
# 11.4 Person keys
# ============================================================

print("\n--- person keys (T24-T31) ---")


def two_author_corpus(names_by_paper, **kw):
    """One paper per entry; each listed person is a middle author, the PI is last."""
    papers = []
    for index, (names, date) in enumerate(names_by_paper):
        entries = [author(distinct("Lead", index)), *names, pi()]
        papers.append(paper(500 + index, entries, date, pi_index=len(entries) - 1))
    return build(papers, **kw)


prefix_merge = two_author_corpus([
    ([author("Wang Wei")], "2022 Mar"),
    ([author("Wang Weiwei")], "2023 Mar"),
])
merged = person_named(prefix_merge, "Wang Weiwei") or person_named(prefix_merge, "Wang Wei")
check("T24 prefix-compatible forenames merge", merged["n_appearances"], 2)
check("T24 the merge is flagged benign drift", merged["flags"], ["drift_benign"])
check_true("T24 CAV-02 is rendered", caveats.CAVEATS["CAV-02"][:40] in caveat_text(prefix_merge, 2))

suspected = two_author_corpus([
    ([author("Wang Wei")], "2022 Mar"),
    ([author("Wang Wenjie")], "2023 Mar"),
])
row = person_named(suspected, "Wang Wei") or person_named(suspected, "Wang Wenjie")
check("T25 incompatible forenames flag a suspected collision", row["flags"], ["collision_suspected"])
check("T25 every derived value carries the uncertainty marker", row["marker"], "[?]")

initials_only = two_author_corpus([
    ([author("Zhang Wei")], "2022 Mar"),
    ([author("Zhang W", fore="", initials="W")], "2023 Mar"),
])
row = person_named(initials_only, "Zhang Wei")
check("T26 initials merge into the full forename", row["n_appearances"], 2)
check("T26 the merge is flagged benign drift", row["flags"], ["drift_benign"])

incomplete = two_author_corpus([
    ([author("Zhang", fore="", initials="")], "2022 Mar"),
    ([author("Zhang Wei")], "2023 Mar"),
])
check("T27 a nameless-forename entry stays separate",
      person_named(incomplete, "Zhang")["flags"], ["incomplete_name"])
check("T27 it is not merged into Zhang Wei", person_named(incomplete, "Zhang Wei")["n_appearances"], 1)

two_orcids = two_author_corpus([
    ([author("Li Ming", orcid=ORCID)], "2022 Mar"),
    ([author("Li Ming", orcid=OTHER_ORCID)], "2023 Mar"),
])
li_rows = [r for r in two_orcids["metrics"]["s2"]["rows"] if r["name"] == "Li Ming"]
check("T28 two ORCIDs under one name stay two people", len(li_rows), 2)
check_true("T28 both are flagged as a confirmed collision",
           all(r["flags"] == ["collision_confirmed"] for r in li_rows))

orcid_partial = two_author_corpus([
    ([author("Li Ming", orcid=ORCID)], "2022 Mar"),
    ([author("Li Ming")], "2023 Mar"),
])
check("T29 an ORCID-less entry joins its unique ORCID group",
      person_named(orcid_partial, "Li Ming")["n_appearances"], 2)

orcid_clash = two_author_corpus([
    ([author("Li Ming", orcid=ORCID)], "2022 Mar"),
    ([author("Li Ming", orcid=OTHER_ORCID)], "2023 Mar"),
    ([author("Li Ming")], "2024 Mar"),
])
clash_rows = [r for r in orcid_clash["metrics"]["s2"]["rows"] if r["name"] == "Li Ming"]
check("T30 a contested loose key merges into nothing", len(clash_rows), 3)
check_true("T30 all three groups are flagged",
           all(r["flags"] == ["collision_confirmed"] for r in clash_rows))

budget = two_author_corpus([
    ([author("Liu Hua")], "2022 Mar"),
    ([author("Liu Huan")], "2023 Mar"),
    ([author("Sun Qi")], "2024 Mar"),
])
check("T31 loose keying finds fewer people than strict", budget["provenance"]["n_loose"], 5)
check("T31 strict keying splits the drifted name", budget["provenance"]["n_strict"], 6)
check_true("T31 the ambiguity budget is printed",
           "strict keying finds 6, loose keying finds 5" in body_text(budget, 1))
check_true("T31 it is printed with zero collision flags",
           all("collision" not in flag
               for row in budget["metrics"]["s2"]["rows"] for flag in row["flags"]))


# ============================================================
# 11.5 Strata
# ============================================================

print("\n--- strata (T32-T39) ---")

strata_corpus = build([
    paper(1, [author("Lead One"), author("Support One"), author("Senior One"), pi()], "2022 Mar", pi_index=3),
    paper(2, [author("Lead One"), author("Support One"), pi(), author("Senior One")], "2023 Mar", pi_index=2),
    paper(3, [author("Other Lead"), author("Support One"), author("Senior One"), pi()], "2024 Mar", pi_index=3),
    paper(4, [author("Third Lead"), author("Solo Person"), author("Senior One"), pi()], "2025 Mar", pi_index=3),
])
check("T32 one senior slot in four papers is enough", person_named(strata_corpus, "Senior One")["stratum"], "D")
check_true("T32 senior collaborators leave the lead-slot partition",
           all(e["name"] != "Senior One"
               for bucket in strata_corpus["metrics"]["s3b"]["buckets"].values() for e in bucket))
check_true("T32 they leave the time-to-lead cohort",
           all(e["name"] != "Senior One" for e in strata_corpus["metrics"]["s4"]["values"]))
check_true("T32 they leave the span cohort",
           all(e["name"] != "Senior One" for e in strata_corpus["metrics"]["s5"]["values"]))
check("T33 two lead slots and never senior is stratum A",
      person_named(strata_corpus, "Lead One")["stratum"], "A")
check("T34 three appearances with no lead slot is stratum B",
      person_named(strata_corpus, "Support One")["stratum"], "B")
check("T35 a single appearance is stratum C", person_named(strata_corpus, "Solo Person")["stratum"], "C")
check_true("T35 single-appearance people are still on the roster",
           person_named(strata_corpus, "Solo Person") is not None)
check_true("T35 they are absent from the lead-slot partition",
           all(e["name"] != "Solo Person"
               for bucket in strata_corpus["metrics"]["s3b"]["buckets"].values() for e in bucket))
check_true("T35 they are absent from the span cohort",
           all(e["name"] != "Solo Person" for e in strata_corpus["metrics"]["s5"]["values"]))

co_pi = build([
    paper(600 + i, [author(distinct("Lead", i)), author("Co Investigator"), pi()],
          f"202{1 + i} Mar", pi_index=2)
    for i in range(6)
])
row = person_named(co_pi, "Co Investigator")
check("T36 a never-senior co-investigator stays a support candidate", row["stratum"], "B")
check("T36 six appearances do not promote anyone", row["n_appearances"], 6)
check_true("T36 CAV-03 accompanies the roster", caveats.CAVEATS["CAV-03"] in caveat_text(co_pi, 2))

flip = build([
    paper(1, [author("Rising Person"), pi()], "2021 Mar", pi_index=1),
    paper(2, [author("Rising Person"), pi()], "2022 Mar", pi_index=1),
    paper(3, [author("New Lead"), pi(), author("Rising Person")], "2025 Mar", pi_index=1),
])
check("T37 taking a senior slot reassigns the stratum", person_named(flip, "Rising Person")["stratum"], "D")
check("T37 the flip is reported by name and year", flip["provenance"]["flips"],
      [{"name": "Rising Person", "marker": "", "first_lead_year": 2021, "first_last_year": 2025}])
check_true("T37 section 5 names the person and both years",
           "Rising Person" in body_text(flip, 5) and "2025" in body_text(flip, 5))

excluded_cfg = build(
    [
        paper(1, [author("Lead One"), author("Blocked Person"), pi()], "2022 Mar", pi_index=2),
        paper(2, [author("Lead One"), author("Blocked Person"), pi()], "2023 Mar", pi_index=2),
    ],
    config={"advisor": {"exclude_names": ["Blocked Person"]}},
)
check("T38 configured exclusions leave the roster", person_named(excluded_cfg, "Blocked Person"), None)
check_true("T38 they leave every metric section",
           all("Blocked Person" not in line
               for sec in excluded_cfg["sections"] if sec["id"] != 1
               for line in sec["body"]))
check_true("T38 the exclusion itself is disclosed in provenance",
           "configured name exclusions: Blocked Person" in body_text(excluded_cfg, 1))

default_exclusions = roles.default_gantt_exclude_names(TARGET)
check("T39 the default timeline exclusion set is the PI and the empty name",
      default_exclusions, {TARGET, ""})
# The exact-equality assertion above already proves that no leftover name
# survives, whatever it was. Listing the six real people who used to be
# hardcoded would republish exactly the personal data this fix removes.
check("T39 no third-party name survives in the default set",
      default_exclusions - {TARGET, ""}, set())
check("T39 configured names are added to it",
      roles.default_gantt_exclude_names(TARGET, {"exclude_names": ["Blocked Person"]}),
      {TARGET, "", "Blocked Person"})


# ============================================================
# 11.6 Metrics
# ============================================================

print("\n--- metrics (T40-T62) ---")

# |E| = 23 with exactly 9 lead slots held by stratum-A people.
e23 = []
for i in range(8):
    e23.append(paper(700 + i, [author(distinct("Lead", i)), pi()], "2022 Mar", pi_index=1))
for i in range(14):
    e23.append(paper(720 + i, [author("Senior One"), pi()], "2023 Mar", pi_index=1))
e23.append(paper(740, [author("Other Lead"), pi(), author("Senior One")], "2024 Mar", pi_index=1))
rep23 = build(e23)
slots = rep23["metrics"]["s3a"]
check("T40 the eligible set is 23 records", slots["denominator"], 23)
check("T40 nine lead slots are held by lead-trainee candidates", slots["counts"]["A"], 9)
check("T40 a percentage is permitted at n>=20", slots["percentages"]["A"], 39)
check_true("T40 rendered as a count with its denominator and percentage",
           "9 of 23 records (39%)" in body_text(rep23, 3))
check_true("T40 the word 'share' does not appear", "share" not in body_text(rep23, 3).lower())

e12 = [paper(800 + i, [author(distinct("Lead", i)), pi()], "2023 Mar", pi_index=1) for i in range(12)]
rep12 = build(e12)
check("T41 the eligible set is 12 records", rep12["metrics"]["s3a"]["denominator"], 12)
check("T41 no percentage is computed below n=20", rep12["metrics"]["s3a"]["percentages"], None)
check_true("T41 no percentage is rendered", "%" not in body_text(rep12, 3))

e3 = [paper(850 + i, [author(distinct("Lead", i)), pi()], "2023 Mar", pi_index=1) for i in range(3)]
rep3 = build(e3)
check_true("T42 the aggregate is suppressed below n=5", rep3["metrics"]["s3a"]["suppressed"])
check_true("T42 the records are printed instead", "| 850 | 2023 |" in body_text(rep3, 3))

pi_leads = build([paper(870 + i, [pi(), author(distinct("Support", i))], "2023 Mar", pi_index=0)
                  for i in range(14)])
check_true("T43 an empty eligible set is not computable", pi_leads["metrics"]["s3a"]["not_computable"])
check_true("T43 the exact sentence is printed",
           "not computable: the PI is first author on every corpus paper" in body_text(pi_leads, 3))
# The person-side partition below always prints its three buckets including
# zeros, by design; the "no zeros" rule applies to the paper-side counts.
check_true("T43 no zero slot counts are emitted",
           not any(line.startswith("- lead-trainee candidate:") or line.startswith("- senior collaborator:")
                   for line in section(pi_leads, 3)["body"]))

partition_corpus = build([
    paper(1, [author("LeadA1 Person"), author("SupportB1 Person"), author("SupportB2 Person"),
              author("SupportB3 Person"), pi()], "2022 Mar", pi_index=4),
    paper(2, [author("LeadA2 Person"), author("SupportB1 Person"), author("SupportB2 Person"),
              author("SupportB3 Person"), pi()], "2023 Mar", pi_index=4),
    paper(3, [author("LeadA3 Person"), author("SupportB4 Person"), author("SupportB5 Person"), pi()],
          "2025 Mar", pi_index=3),
    paper(4, [author("LeadA4 Person"), author("SupportB4 Person"), author("SupportB5 Person"), pi()],
          "2025 Jun", pi_index=3),
])
partition = partition_corpus["metrics"]["s3b"]
check("T44 the partition denominator is A plus B", partition["denominator"], 9)
check("T44 lead-slot holders", partition["counts"]["holds_lead"], 4)
check("T44 observed without a lead slot", partition["counts"]["observed_without_lead"], 3)
check("T44 too recent to tell", partition["counts"]["too_recent"], 2)
check("T44 no percentage exists at any n", partition["percentages"], None)
check_true("T44 CAV-06 accompanies the partition",
           caveats.CAVEATS["CAV-06"] in caveat_text(partition_corpus, 3))

lag_corpus = build([
    paper(1, [author("ZeroA Person"), pi()], "2022 Mar", pi_index=1),
    paper(2, [author("ZeroB Person"), pi()], "2022 Mar", pi_index=1),
    paper(3, [author("ZeroC Person"), pi()], "2022 Mar", pi_index=1),
    paper(4, [pi(), author("LagOne Person"), author("FillerA Person")], "2022 Mar", pi_index=0),
    paper(5, [author("LagOne Person"), pi()], "2023 Mar", pi_index=1),
    paper(6, [pi(), author("LagTwo Person"), author("LagFour Person"), author("FillerB Person")],
          "2021 Mar", pi_index=0),
    paper(7, [author("LagTwo Person"), pi()], "2023 Mar", pi_index=1),
    paper(8, [author("LagFour Person"), pi()], "2025 Mar", pi_index=1),
])
lag = lag_corpus["metrics"]["s4"]
check("T45 six people reached a lead slot", lag["denominator"], 6)
check("T45 the lag values", sorted(item["lag_years"] for item in lag["values"]), [0, 0, 0, 1, 2, 4])
check("T45 the median is computed at n=6", lag["median"], 0.5)
check("T45 the count at lag zero is explicit", lag["count_at_zero"], 3)
check_true("T45 the zero count is rendered",
           "at 0 years (debuted in the lead slot): 3 of 6" in body_text(lag_corpus, 4))

lag3 = build([paper(900 + i, [author(distinct("Lead", i)), pi()], "2023 Mar", pi_index=1)
              for i in range(3)])
check_true("T46 the median is suppressed at n=3", lag3["metrics"]["s4"]["suppressed"])
check("T46 no median is produced", lag3["metrics"]["s4"]["median"], None)
check_true("T46 the raw values are printed", "Lead00 Person: 0 year(s)" in body_text(lag3, 4))


def span_corpus(spec, config=None):
    """One paper per (person, year); the person leads, the PI closes the byline."""
    papers = []
    counter = 0
    for name, years in spec:
        for year in years:
            counter += 1
            papers.append(paper(1000 + counter, [author(name), pi()], f"{year} Mar", pi_index=1))
    return build(papers, config=config)


spans47 = span_corpus([
    ("CompleteOne Person", [2022, 2023]),
    ("CompleteTwo Person", [2022, 2024]),
    ("CompleteThree Person", [2023, 2024]),
    ("CompleteFour Person", [2022, 2022]),
    ("CensoredOne Person", [2023, 2025]),
    ("CensoredTwo Person", [2023, 2026]),
    ("CensoredThree Person", [2024, 2025]),
    ("CensoredFour Person", [2024, 2026]),
    ("CensoredFive Person", [2022, 2025]),
])
span47 = spans47["metrics"]["s5"]
check("T47 four spans are uncensored", span47["buckets"]["complete"], 4)
check("T47 five spans are right-censored", span47["buckets"]["right_censored"], 5)
check("T47 no median below n=5", span47["median"], None)
check_true("T47 the aggregate is suppressed", span47["suppressed"])
check_true("T47 the raw values are printed", "CompleteOne Person: span 1 year(s)" in body_text(spans47, 5))
check_true("T47 the censoring counts are printed", "right_censored 5" in body_text(spans47, 5))

spans48 = span_corpus([
    ("CompleteOne Person", [2022, 2023]),
    ("CompleteTwo Person", [2022, 2024]),
    ("CompleteThree Person", [2023, 2024]),
    ("CompleteFour Person", [2022, 2022]),
    ("CompleteFive Person", [2023, 2023]),
    ("CompleteSix Person", [2022, 2024]),
])
span48 = spans48["metrics"]["s5"]
check("T48 six uncensored spans", span48["denominator"], 6)
check("T48 the median is computed", span48["median"], 1.0)
check_true("T48 an IQR is computed", span48["iqr"] is not None)
check_true("T48 no arithmetic mean appears in section 5",
           "mean" not in (body_text(spans48, 5) + caveat_text(spans48, 5)).lower())
check_true("T48 no metric key is a mean",
           not any("mean" in key for key in spans48["metrics"]["s5"]))

check("T49 two records in one year give a zero span",
      person_named(spans48, "CompleteFour Person")["first_year"],
      person_named(spans48, "CompleteFour Person")["last_year"])
check_true("T49 it is rendered as a same-year zero, not as a single appearance",
           "CompleteFour Person: span 0 (same year)" in body_text(spans48, 5))
check("T49 a single-appearance person is not in the span cohort",
      person_named(strata_corpus, "Solo Person")["stratum"], "C")

censoring = span_corpus([
    ("EdgeStart Person", [2021, 2023]),
    ("EdgeEnd Person", [2023, 2026]),
])
check_true("T50 first appearance at the window start is left-censored",
           person_named(censoring, "EdgeStart Person")["left_censored"])
check_true("T51 last appearance in the current year is right-censored",
           person_named(censoring, "EdgeEnd Person")["right_censored"])
check("T50/T51 neither is in the uncensored bucket",
      censoring["metrics"]["s5"]["buckets"]["complete"], 0)

gap_years = build([
    paper(1, [author("Liu Hua"), pi()], "2022 Mar", pi_index=1),
    paper(2, [author("Liu Hua"), pi()], "2024 Mar", pi_index=1),
])
years9 = {row["year"]: row for row in gap_years["metrics"]["s9"]["years"]}
check("T52 an empty year is a zero, not an omitted row", years9[2023]["count"], 0)
check_true("T52 the zero is rendered", "- 2023: 0" in body_text(gap_years, 9))
check_true("T53 the first bin is marked partial", years9[2021]["partial"])
check_true("T53 the last bin is marked partial", years9[2026]["partial"])
check_true("T53 PARTIAL is rendered at the point of display",
           "- 2021: 0  (PARTIAL)" in body_text(gap_years, 9))
check_true("T53 CAV-17 is rendered", caveats.CAVEATS["CAV-17"] in caveat_text(gap_years, 9))

no_equal = build([paper(1, [author("Liu Hua"), pi()], "2023 Mar", pi_index=1)])
check_true("T54 an absent attribute is not measurable", no_equal["metrics"]["s8"]["not_measurable"])
check("T54 the exact sentence is printed", body_text(no_equal, 8), "not measurable in this corpus")
check_true("T54 no zero percentage is emitted", "0%" not in body_text(no_equal, 8))
check_true("T54 'no co-first' is never claimed", "no co-first" not in body_text(no_equal, 8).lower())

shared_first = build([
    paper(1, [author("Liu Hua", equal_contrib=True), author("Wang Li", equal_contrib=True),
              author("Zhao Min"), pi()], "2023 Mar", pi_index=3),
])
check("T55a a flagged group including slot 0 is shared first",
      shared_first["metrics"]["s8"]["categories"]["shared_first"], 1)
check("T55a the flagged group size is reported",
      shared_first["metrics"]["s8"]["papers"][0]["group_size"], 2)

shared_middle = build([
    paper(1, [author("Liu Hua"), author("Wang Li"),
              author("Zhao Min", equal_contrib=True), author("Sun Qi", equal_contrib=True),
              pi()], "2023 Mar", pi_index=4),
])
check("T55b a flagged group short of the final index is not co-first",
      shared_middle["metrics"]["s8"]["categories"]["shared_first"], 0)
check("T55b nor is it shared senior authorship",
      shared_middle["metrics"]["s8"]["categories"]["shared_senior"], 0)
check("T55b it is neither", shared_middle["metrics"]["s8"]["categories"]["other"], 1)

shared_senior_tail = build([
    paper(1, [author("Liu Hua"), author("Wang Li"),
              author("Zhao Min", equal_contrib=True), author("Sun Qi", equal_contrib=True)],
          "2023 Mar", pi_index=None),
])
check("T55b the final two indices are shared senior authorship",
      shared_senior_tail["metrics"]["s8"]["categories"]["shared_senior"], 1)
check("T55b and are not counted as co-first",
      shared_senior_tail["metrics"]["s8"]["categories"]["shared_first"], 0)

no_email = build([paper(1000 + i, [author(distinct("Lead", i)), pi(email="")], "2023 Mar", pi_index=1)
                  for i in range(6)])
check("T56 the corresponding-author figure is suppressed", no_email["metrics"]["s7"]["corresponding"], None)
check("T56 email coverage is zero of N", no_email["metrics"]["s7"]["email_coverage"],
      {"covered": 0, "denominator": 6})
check_true("T56 CAV-15 prints the coverage", "0 of 6 papers" in caveat_text(no_email, 7))

venues = build([
    *[paper(1100 + i, [author(distinct("Lead", i)), pi()], "2023 Mar", pi_index=1,
            journal="J Hepatol") for i in range(2)],
    *[paper(1110 + i, [author(distinct("Other", i)), pi()], "2024 Mar", pi_index=1,
            journal="Journal of Hepatology") for i in range(3)],
])
check("T57 abbreviation and full title stay separate rows",
      venues["metrics"]["s11"]["repeated"], [("Journal of Hepatology", 3), ("J Hepatol", 2)])
check_true("T57 both are rendered verbatim",
           "J Hepatol: 2 of 5" in body_text(venues, 11) and "Journal of Hepatology: 3 of 5" in body_text(venues, 11))
check_true("T57 CAV-20 is rendered", caveats.CAVEATS["CAV-20"] in caveat_text(venues, 11))

affil = build([
    *[paper(1200 + i, [author(distinct("Lead", i), affiliation=INTERNAL), pi()], "2023 Mar", pi_index=1)
      for i in range(4)],
    *[paper(1210 + i, [author(distinct("Other", i), affiliation=EXTERNAL), pi()], "2022 Mar", pi_index=1)
      for i in range(2)],
    paper(1220, [author("Blank Person"), pi(affiliation="")], "2024 Mar", pi_index=1),
])
check("T58 only strings on three or more records are listed",
      [text for text, _ in affil["metrics"]["s12"]["strings"]], [INTERNAL])
check_true("T58 per-year coverage is printed beside it",
           "2023: 8 of 8 author entries" in body_text(affil, 12))
check_true("T59 a year with no affiliation data says so", "- 2024: no affiliation data" in body_text(affil, 12))
check_true("T59 it is not rendered as an empty list with a zero rate",
           "2024: 0 of" not in body_text(affil, 12))

# T60: what may not be printed as a computed value, asserted as a text scan of
# the computed half of the report. The caveats and the dropped-metric register
# name these same quantities in order to rule them out, so they are excluded
# from the scan by construction rather than by a keyword exception.
#
# The list has now been cut twice and the two cuts are not the same cut.
#
# Round one took off "h-index", "impact factor", "citation", "score" and
# "grade" — absolute values, which are now computed, printed and written to
# disk. What survived was the whole of the ordering vocabulary.
#
# Round two takes off the ordering vocabulary itself: "rank", "rating", "top "
# and "best " are gone, because the compare page now ranks the corpora on it,
# Section 16 prints a star band, and `ranking.comparative_statement` writes a
# directional sentence. Banning the words would ban the features.
#
# What is left is not a remainder. Each survivor is here for its own reason:
#
#   productivity           ordering people by how much they published. Section 2
#                          says in prose why the roster is not sortable by a
#                          count, and the computed half must not do it anyway.
#                          `roles.rank_people` exists as of round four and does
#                          order people, but it is a call a caller has to make
#                          by name; nothing in this report makes it, so the
#                          scanned half is still free of it and this token stays.
#
# Round four takes off "trend" and "slope". Read the reason they were here
# carefully before reading the removal as a reversal: the objection was that a
# handful of right-censored integer points do not support a fitted direction,
# and that objection is still true. It has not been answered, it has been
# *printed* — `profile/trends.py` refuses outright below four points, and above
# them puts the confidence interval and the point count in the same sentence as
# the slope, so the reader sees the width rather than being asked to trust a
# number. Section 9 is where it lands. If a later round ever prints a bare slope
# with no interval beside it, these two tokens belong back on this list.
#
# Journal impact factor and JCR / CAS quartile are a third case and are on
# neither list, because they are absent for a reason neither list can express:
# no free, redistributable source exists, so they arrive only when the user
# supplies the table by hand. Section 18 prints them then, with the edition.
# Round four also takes off "percentile" and "quantile", and this is the one
# removal that answers its objection rather than reframing it. The objection was
# that there was no reference population — "the corpora on a page are the ones a
# user loaded", so a position inside that set moves when an unrelated corpus is
# added. `impact_reference` does not place anyone in that set. It places one
# paper's citation count inside every OpenAlex work sharing its topic and year,
# which is the same population whether this run loaded one corpus or nine.
#
# `ranking.RANKING_EXCLUSIONS["not_computable_here"]` still refuses the original
# thing in the original words, and test_impact_reference.py reads that entry back
# and asserts it was not touched. Two different positions; only one was ever
# uncomputable, and it still is.
FORBIDDEN = ["productivity"]
scan_report = build(e23 + [paper(1300, [author("Liu Hua", affiliation=INTERNAL), pi()], "2024 Mar", pi_index=1)],
                    gantt="pubmed_results/student_activity_gantt.png")
scan_text = "\n".join(all_body_lines(scan_report)).lower()
for token in FORBIDDEN:
    check(f"T60 '{token.strip()}' never appears in a computed value", token in scan_text, False)

# The condition the two removed tokens were traded for. A ban is self-enforcing;
# this is not, so it is asserted rather than left in the comment above: wherever
# the report says "slope", the same line has to carry the interval around it and
# the number of points it was fitted over. A slope printed bare is the failure
# the ban used to make impossible.
# T60d: what the percentile removal is traded for. `scan_report` is built with no
# reference payload at all, which is the common case and the dangerous one — a
# reader who sees the words "reference population" and no numbers must be told
# that nothing was fetched, not left to infer that nothing was found.
_ref_lines = [line for line in all_body_lines(scan_report)
              if "reference population" in line.lower()]
check_true("T60d the report says what a missing reference population means", _ref_lines)
check_true("T60d ...naming it as not computed rather than leaving a blank",
           any("Not computed" in line for line in _ref_lines))
check_true("T60d ...and saying in words that this is not a low count",
           any("is not a low count" in line for line in all_body_lines(scan_report)))

_slope_lines = [line for line in all_body_lines(scan_report) if "slope" in line.lower()]
check_true("T60c the report does print a slope now", _slope_lines)
check("T60c ...and never one without its interval on the same line",
      [line for line in _slope_lines if "interval" not in line.lower()], [])
check("T60c ...nor one without the point count it was fitted over",
      [line for line in _slope_lines if "point" not in line.lower()], [])

# T60b: 字母等级. The one prohibition round two *adds*, and the only one, so it
# gets its own assertion rather than a line on a list.
#
# Stars are produced and letters are not. Those are two coarsenings of one
# number and the split is deliberate — it was made by the user, in those words,
# and `ranking.RANKING_EXCLUSIONS["refused_by_design"]` records it so that a
# later reader does not tidy the inconsistency away by unifying them.
#
# The guard matches a letter *emitted as a grade*, never the word "grade": the
# dropped register and Section 16's exclusion block both have to name letter
# grades in order to refuse them, and a bare-word match would read the refusal
# as the offence. Three shapes are caught — a letter under a grading label, a
# letter carrying a grading suffix, and an A/B/C scale being declared.
LETTER_GRADE = re.compile(
    r"(?i:grade|tier|band|等级|评级)\s*\d*\s*[:：=]\s*[\"'“]?[A-DF][+\-]?(?![A-Za-z])"
    r"|(?<![A-Za-z])[A-DF][+\-]?\s*(?:级|档)(?![A-Za-z])"
    r"|(?<![A-Za-z])A\s*[/、,]\s*B\s*[/、,]\s*C(?![A-Za-z])"
)
# A guard nobody has fired is a guard nobody has checked. These four are what a
# letter mapping looks like if one is ever added, in the four places it could be
# added from.
for offending in ("grade: B", "评级：A+", "band 3 = C-", "the scale is A/B/C"):
    check(f"T60b the guard catches {offending!r}", bool(LETTER_GRADE.search(offending)), True)
# ...and the report body is scanned with it. Section 1 prints "verified by
# evidence tier: orcid", which is an identity-evidence tier and not a band, so
# the pattern requires an actual letter after the label rather than banning the
# word "tier".
check("T60b no letter grade is emitted anywhere in the computed half",
      LETTER_GRADE.findall("\n".join(all_body_lines(scan_report))), [])
check("T60b no letter grade survives into the rendered document",
      LETTER_GRADE.findall(report.render_markdown(scan_report)), [])

# The reverse of the same rule, in both directions. The FORBIDDEN list above is
# only meaningful if the vocabulary it stopped banning is genuinely reachable —
# otherwise it would be passing because the feature is missing rather than
# because the boundary holds.
score_body = "\n".join(section(scan_report, 16)["body"]).lower()
check_true("T60 the composite score is a printed value, not a withheld one",
           "score" in score_body and "weight" in score_body)
check_true("T60c the star band is printed, and printed as a band of the score",
           "star band" in score_body and "★" in "\n".join(section(scan_report, 16)["body"]))
check_true("T60c the band edges that decided it are printed beside it",
           "1★ = 0-20" in "\n".join(section(scan_report, 16)["body"]))
percent_sections = {sec["id"] for sec in scan_report["sections"] if any("%" in line for line in sec["body"])}
# Section 9 joins 3 and 7 in round four, on a narrower licence: its only "%" is
# the confidence level attached to the fitted slope, which is not a share of the
# corpus at all. The second assertion is what keeps that licence narrow — any
# other percentage in section 9 is exactly what R2 was written to stop.
check("T60 percentages appear only where R2 permits them", percent_sections - {3, 7, 9}, set())
check("T60 section 9's only percentage is a confidence level, not a share",
      [line for line in section(scan_report, 9)["body"]
       if "%" in line and "interval" not in line.lower()], [])
check_true("T60 no per-person row carries a percentage",
           not any("%" in line for line in section(scan_report, 2)["body"]))
# The word is banned in the *computed* half because no PubMed field separates a
# PhD student from a postdoc, a technician or a visiting scholar, so no number
# this report prints may be labelled as being about students. It is not banned
# from a title or from prose, which is why Section 20 is called "Student
# evaluations" on the page and still keeps the word out of every body line —
# `report._NO_EVALUATION_TABLE_NOTE` and `cli._load_evaluation_table` both say
# "evaluation table" for that reason, and each carries a comment saying so.
check_true("T60 the banned word 'student' appears only in the supplied image path",
           all("student" not in line.lower() or ".png" in line
               for line in all_body_lines(scan_report)))

# The most prolific person appears last in time, so ordering by first
# appearance and ordering by count give different answers.
order_corpus = build([
    paper(1, [author("Zeta Person"), pi()], "2024 Mar", pi_index=1),
    paper(2, [author("Zeta Person"), pi()], "2025 Mar", pi_index=1),
    paper(3, [author("Zeta Person"), pi()], "2026 Mar", pi_index=1),
    paper(4, [author("Alpha Person"), pi()], "2022 Mar", pi_index=1),
    paper(5, [author("Beta Person"), pi()], "2023 Mar", pi_index=1),
])
rows61 = order_corpus["metrics"]["s2"]["rows"]
names = [row["name"] for row in rows61]
check("T61 rows are ordered by first appearance then name",
      names, ["Alpha Person", "Beta Person", "Zeta Person"])
check("T61 the ordering is not by appearance count",
      names == [row["name"] for row in sorted(rows61, key=lambda r: -r["n_appearances"])], False)
check("T61 the most prolific person is not promoted to the top", names.index("Zeta Person"), 2)

# T61b: the round-four leaderboard. It is a *second* view, and the assertions
# that matter are the ones separating it from the table above: T61 already holds
# that table to first-appearance order, and these hold the ranked block to being
# visibly a ranking, with the denominator attached, rather than a resorting of
# the same rows that a reader might mistake for the roster.
_s2_body = section(order_corpus, 2)["body"]
_rank_rows = [line for line in _s2_body if line.startswith("| 1 ") or line.startswith("| 1 (tied)")]
check_true("T61b section 2 prints a ranked view as well as the roster",
           any("By first-author slots" in line for line in _s2_body))
check("T61b ...and it is a different order from the roster above",
      len(_rank_rows), 1)
check_true("T61b the ranked view names its denominator",
           any("3 people" in line for line in _s2_body))
check_true("T61b ...and says absence is not a low rank",
           any("are absent" in line for line in _s2_body))
check_true("T61b ...and says ties share a rank",
           any("Ties share a rank" in line for line in _s2_body))
# Zeta leads three papers to Alpha's and Beta's one each, so the leaderboard
# inverts the roster: last by first appearance, first by lead slots. If these two
# ever agree, this corpus stopped testing the distinction.
check_true("T61b the leaderboard and the roster disagree, which is the point",
           _s2_body.index("| 1 | Zeta Person | 3 | 3 |") > _s2_body.index(
               [line for line in _s2_body if line.startswith("| Alpha Person")][0]))

check("T62 section 0 comes first", scan_report["sections"][0]["id"], 0)
check("T62 section 0 is CAV-00 verbatim", scan_report["sections"][0]["prose"], [caveats.CAVEATS["CAV-00"]])
markdown = report.render_markdown(scan_report)
check_true("T62 CAV-00 precedes the provenance block",
           markdown.index(caveats.CAVEATS["CAV-00"]) < markdown.index("Corpus provenance"))


# ============================================================
# Contract checks the spec implies but does not number
# ============================================================

print("\n--- module contract ---")

check_true("no metric draws a chart", "matplotlib" not in inspect.getsource(report))
check_true("no metric touches the network",
           all(token not in inspect.getsource(metrics) for token in ("requests", "urllib", "open(")))
check_true("the supplied timeline image is referenced, not drawn",
           "![Person activity timeline](student_activity_gantt.png)" in markdown)
# A score is emitted now, so the old "no overall score" contract is gone, and a
# rank and a star band are emitted too, so "rank:" and "rating:" came off this
# list in round two. What is left is narrower and is about labels: nothing in
# the rendered document introduces a value under a heading that makes it a
# position inside a population nobody measured. The tokens carry their colon on
# purpose — Sections 14-16 name percentile and quantile position in prose in
# order to refuse them, and a bare-word match would read the refusal as the
# offence. The computed half is scanned word-by-word at T60 instead.
# "tier:" is not on the list: Section 1 prints "verified by evidence tier:",
# which is the identity-evidence tier and has nothing to do with journals or
# with placing people in bands.
check_true("no percentile or quantile is emitted as a labelled value",
           not any(word in markdown.lower().split()
                   for word in ("percentile:", "quantile:")))

for key, result in scan_report["metrics"].items():
    denominator = result.get("denominator", result.get("cohort_denominator"))
    check_true(f"{key} returns a denominator", denominator is not None)
    check_true(f"{key} returns a suppression flag", "suppressed" in result)

with tempfile.TemporaryDirectory() as tmp:
    paths = report.write_report(scan_report, tmp)
    check_true("markdown is written", os.path.exists(paths["markdown"]))
    check_true("json is written", os.path.exists(paths["json"]))
    with open(paths["json"], encoding="utf-8") as handle:
        record = json.load(handle)
    # A JSON round trip turns tuples into lists and integer dict keys into
    # strings, so the comparison is against the serialised form.
    check_true("the json record carries the same metrics",
               record["metrics"] == json.loads(json.dumps(scan_report["metrics"])))
    check_true("the json record omits the rendered sections", "sections" not in record)
    check("the json record repeats the denominators",
          record["metrics"]["s3a"]["denominator"], scan_report["metrics"]["s3a"]["denominator"])

    # Any fired gate would do here; what is under test is the refusal document,
    # not the gate. G4 because G1 was retired and G2/G3 became warnings, leaving
    # "no structured author records" as the first thing `check_corpus_gates`
    # still refuses on — there is nothing to compute over, so there is no
    # degraded report to print.
    refused_corpus = corpus([paper(1, [author("Liu Hua"), pi()], pi_index=1),
                             paper(2, [], pi_index=None)])
    refusal_paths = report.write_report(
        report.build_report(refused_corpus, {}, None, FIXED_NOW), tmp
    )
    with open(refusal_paths["markdown"], encoding="utf-8") as handle:
        refusal_markdown = handle.read()
    check_true("a refusal document names its gate", "gate G4" in refusal_markdown)
    check_true("a refusal document renders no section",
               "Corpus provenance" not in refusal_markdown)

check("suppressed aggregates return None, not a number",
      (rep3["metrics"]["s3a"]["percentages"], lag3["metrics"]["s4"]["median"],
       span47["median"], span47["iqr"]),
      (None, None, None, None))
small_team = build([paper(1400 + i, [author(distinct("Lead", i)), pi()], "2023 Mar", pi_index=1)
                    for i in range(3)])
check("the team-size median is suppressed below n=5", small_team["metrics"]["s10"]["median"], None)
# The trainee-led subset carries a higher floor than the main figure, so it can
# be suppressed while the corpus-wide median still renders.
mixed_leads = build(
    [paper(1500 + i, [pi(), author(distinct("Support", i))], "2023 Mar", pi_index=0) for i in range(5)]
    + [paper(1600 + i, [author(distinct("Lead", i)), pi()], "2024 Mar", pi_index=1) for i in range(3)]
)
check("the corpus-wide team-size median renders at n=8", mixed_leads["metrics"]["s10"]["median"], 2.0)
check("the trainee-led subset median needs its own higher floor",
      mixed_leads["metrics"]["s10"]["subset"]["median"], None)
check("the subset still reports its denominator",
      mixed_leads["metrics"]["s10"]["subset"]["denominator"], 3)
check("percent() refuses to compute below n=20", metrics.percent(3, 19), None)
check("percent() computes at n=20", metrics.percent(5, 20), 25)


# ============================================================
# The rendered side-by-side page
# ============================================================
#
# `render_comparison_markdown` had no test at all, which is how it shipped a page
# that contradicted itself: the narration line counted scored corpora from
# `ranking["n_ranked"]`, which is 0 whenever the ranking is suppressed, so the
# page read "Positions were taken over the 0 corpora that carried a score"
# directly above "only 1 corpus/corpora on this page carried a score, floor 2".
# Rendering is the only place that defect was visible, so it is asserted on the
# rendered string rather than on the dict behind it.

print("\nthe rendered comparison page")

from check_your_advisor.profile.scoring import composite_score  # noqa: E402

_S3B = {"denominator": 15, "lag_years": 2,
        "counts": {"holds_lead": 9, "observed_without_lead": 3, "too_recent": 3}}
_S4 = {"denominator": 8, "median": 1.0, "count_at_zero": 2, "still_without_lead": ["x"],
       "suppressed": False, "not_computable": False}
_S9 = {"denominator": 30,
       "years": [{"year": y, "count": 2, "partial": False, "indexing_lag": False}
                 for y in (2021, 2022, 2023)]}
_IMPACT = {"denominator": 10, "covered": 10, "suppressed": False, "not_computable": False,
           "h_index": 3, "i10_index": 2, "total_citations": 60, "median_citations": 5.0,
           "iqr": (2.0, 8.0), "lower_bound": False, "mixed_sources": False,
           "sources": {"openalex": 10}, "generated_at": "2026-01-01T09:30:00"}


def _cmp_score(lead: int) -> dict:
    return composite_score({
        "s3a": {"denominator": 20, "suppressed": False, "not_computable": False,
                "counts": {"A": lead, "B": 20 - lead, "D": 0, "unclassified": 0}},
        "s3b": _S3B, "s4": _S4, "s9": _S9, "impact": _IMPACT,
    })


def _cmp_entry(label: str, lead: int | None, gate: dict | None = None) -> dict:
    return {
        "label": label,
        "source": f"dir/{label}",
        "report": {
            "refused": gate is not None,
            "gate": gate,
            "provenance": {"corpus_size": 20},
            "score": None if lead is None else _cmp_score(lead),
            "impact": _IMPACT,
            "citations_note": "",
            "generated_at": "2026-01-01T09:30:00",
        },
    }


_suppressed_page = report.render_comparison_markdown(report.build_comparison(
    [_cmp_entry("Scored", 12),
     _cmp_entry("Refused", None, {"id": "G3", "name": "weak identity config"})]
))
_narrated = re.search(r"Positions were taken over the (\d+) corpora that carried a score",
                      _suppressed_page)
_reasoned = re.search(r"only (\d+) corpus/corpora on this page carried a score", _suppressed_page)
check_true("the suppressed page states how many corpora carried a score", _narrated is not None)
check_true("...and the unranked row gives its own count", _reasoned is not None)
check("...and the two counts on one page agree", _narrated.group(1), _reasoned.group(1))
check("...on the true number of scored corpora, which is one", _narrated.group(1), "1")
check_true("a suppressed page still says nothing was placed",
           "No position was assigned to anything." in _suppressed_page)

_ranked_page = report.render_comparison_markdown(report.build_comparison(
    [_cmp_entry("Higher", 16), _cmp_entry("Lower", 4)]
))
check_true("a real ranking prints positions with the count they were taken among",
           "| 1 of 2 |" in _ranked_page and "| 2 of 2 |" in _ranked_page)
check_true("...prints a star band beside each", "of 5)" in _ranked_page and "★" in _ranked_page)
check_true("...and one directional sentence", "scores higher than" in _ranked_page)
check("...and the narrated count matches the two scored corpora",
      re.search(r"Positions were taken over the (\d+) corpora", _ranked_page).group(1), "2")
# The bans that did not move. Both words appear on the page only inside the
# statements refusing them, so the assertion is that no *value* is attached.
check("no percentile value is printed",
      re.findall(r"\d+(?:\.\d+)?\s*(?:th|st|nd|rd)?\s*percentile", _ranked_page.lower()), [])
check("no quantile position is printed",
      re.findall(r"\d+(?:\.\d+)?\s*(?:th|st|nd|rd)?\s*quantile", _ranked_page.lower()), [])
check("no letter grade is printed in the rank table",
      re.findall(r"\|\s*[A-DF][+-]?\s*\|", _ranked_page), [])
check_true("...and the refusal of percentiles is still stated on the page",
           "No percentile and no quantile position." in _ranked_page)
check_true("...as is the refusal of letter tiers", "No letter tier." in _ranked_page)


# ============================================================
# 11.20 Section 18's risk block — printed as statements, never as a verdict
#
# The signals themselves are tested in test_journal_risk.py. What is tested here
# is the seam: the top-level keys, the note that stands in for a missing file,
# the column in the table above the block, and the guarantee that nothing
# collected reaches the score.
# ============================================================

print("\n--- Section 18: public risk signals (T61) ---")

_RISK_CORPUS = [
    paper(1400 + i, [author(distinct("Coll", i)), pi()], "2023 Mar", pi_index=1,
          journal="Journal of Hepatology")
    for i in range(4)
]
_no_risk = build(_RISK_CORPUS)
_s18 = body_text(_no_risk, 18)

check_true("T61 the risk join is a top-level key, like journals beside it",
           isinstance(_no_risk["journal_risk"], dict))
check("T61 ...and never appears inside metrics",
      [k for k in _no_risk["metrics"] if "risk" in k.lower()], [])
check_true("T61 no collected file means risk_missing, not an empty result",
           _no_risk["journal_risk"]["risk_missing"])
check_true("T61 the note names the verb that produces the file",
           "check-your-advisor journal-risk" in _no_risk["journal_risk_note"])
check_true("T61 the section prints 未采集 rather than a blank column",
           "未采集" in _s18)
check_true("T61 ...and prints the reason under a 'Reason:' line",
           f"Reason: {_no_risk['journal_risk_note']}" in _s18)
check_true("T61 ...and says an empty cell is a lookup nobody ran",
           "a lookup nobody has run yet" in _s18)
check_true("T61 ...and that it is not a clean bill of health",
           "not a clean bill of health" in _s18)

# The two columns are never one. 预警 is the CAS list, copied by hand from a page
# with no API; 风险信号 is what three open APIs returned. Merging them would put
# a hand-copied list and a DOAJ membership flag in one cell as one kind of claim.
#
# The per-journal table only exists once a journal table was supplied, so the
# column-count assertions run over a report that has one. A header, a separator
# row and three row emitters all carry the column count by hand in report.py, and
# a mismatch between any two of them silently shifts every cell after it.
_TABLE_CSV = os.path.join(tempfile.mkdtemp(prefix="cya-s18-"), "journals.csv")
with open(_TABLE_CSV, "w", newline="", encoding="utf-8-sig") as _handle:
    _writer = csv.writer(_handle)
    _writer.writerow(["ISSN", "刊名", "版本来源", "数据获取日期", "影响因子", "是否预警"])
    _writer.writerow(["0168-8278", "Journal of Hepatology", "官方版", "2026-08-01", "26.8", "否"])
_TABLE = load_journal_table(_TABLE_CSV)
_tabled = report.build_report(corpus([dict(p, issn="0168-8278") for p in _RISK_CORPUS]),
                              {}, None, FIXED_NOW, journal_table=_TABLE)
_s18_body = section(_tabled, 18)["body"]
_header = [line for line in _s18_body if line.startswith("| journal (")][0]
check_true("T61 the table carries a 风险信号 column", "| 风险信号 |" in _header)
check_true("T61 ...beside the hand-filled 预警 column, not instead of it",
           "| 预警 | 风险信号 |" in _header)
_rule = _s18_body[_s18_body.index(_header) + 1]
check("T61 the separator row matches the header's column count",
      _rule.count("|"), _header.count("|"))
_rows = [line for line in _s18_body if line.startswith("| Journal of Hepatology")]
check("T61 the matched journal emitted a row", len(_rows), 1)
check("T61 ...whose column count matches the header",
      sorted({line.count("|") for line in _rows}), [_header.count("|")])
check_true("T61 with no signals collected the cell reads 未采集, not blank",
           _rows[0].rstrip().endswith("| 未采集 |"))
check_true("T61 ...and the hand-filled 预警 cell beside it is untouched",
           "| 否 | 未采集 |" in _rows[0])
# The other two row emitters: a journal the table does not hold, and a corpus in
# which no record carries a journal string at all.
_unlisted = report.build_report(
    corpus([paper(1600, [author("Solo Person"), pi()], "2023 Mar", pi_index=1,
                  journal="Beihai Reports")]),
    {}, None, FIXED_NOW, journal_table=_TABLE)
_unlisted_row = [line for line in section(_unlisted, 18)["body"]
                 if line.startswith("| Beihai Reports")][0]
check("T61 the 本表未收录 row carries the same column count",
      _unlisted_row.count("|"), _header.count("|"))
_no_journal = report.build_report(
    corpus([paper(1700, [author("Solo Person"), pi()], "2023 Mar", pi_index=1, journal="")]),
    {}, None, FIXED_NOW, journal_table=_TABLE)
_empty_row = [line for line in section(_no_journal, 18)["body"]
              if line.startswith("| (no record in this corpus")][0]
check("T61 the empty-corpus row carries it too",
      _empty_row.count("|"), _header.count("|"))

# A collected file, joined in. The payload is built by hand rather than fetched:
# this asserts the seam, and test_journal_risk.py asserts the signals.
_RISK_PAYLOAD = {
    "generated_at": "2026-08-22T09:00:00",
    "source_papers_json": "papers_20260822_010101.json",
    "sources": ["doaj", "crossref", "openalex"],
    "tracked_coverage_fields": ["abstracts-current", "references-current"],
    "denominator": {"journals_total": 1, "journals_with_signals": 1},
    "journals_without_issn": [],
    "records": [{
        "journal": "Journal of Hepatology", "issn": "0168-8278", "issn_raw": "0168-8278",
        "issn_checksum_ok": True, "corpus_paper_count": 4,
        "sources_answered": ["doaj", "crossref"],
        "fetched_at": "2026-08-22T09:00:00",
        "signals": [
            {"signal": "doaj_not_indexed", "source": "doaj",
             "endpoint": "https://doaj.org/api/search/journals/issn%3A0168-8278",
             "statement": "未被 DOAJ 收录 — this ISSN is not in the Directory of Open Access "
                          "Journals.", "observed": {"total": 0}},
            {"signal": "crossref_metadata_missing", "source": "crossref",
             "endpoint": "https://api.crossref.org/journals/0168-8278",
             "statement": "Crossref 元数据缺失 1 项（共查 2 项）", "observed": {"missing": 1}},
        ],
    }],
}
# The corpus carries the ISSN, because the join is by ISSN and nothing else. The
# journal table is supplied too, so the per-journal table exists and its 风险信号
# cell can be read; the risk block itself renders either way.
_with_issn = [dict(p, issn="0168-8278") for p in _RISK_CORPUS]
_risked = report.build_report(corpus(_with_issn), {}, None, FIXED_NOW,
                              journal_table=_TABLE, journal_risk=_RISK_PAYLOAD)
_s18r = body_text(_risked, 18)

check_false("T61 a supplied payload is not missing", _risked["journal_risk"]["risk_missing"])
check("T61 the note is blank when a payload was supplied", _risked["journal_risk_note"], "")
check("T61 the journal was matched by ISSN", _risked["journal_risk"]["journals_checked"], 1)
check_true("T61 the table cell carries the count and points at the block below",
           "| 2 项（见下） |" in _s18r)
check_true("T61 each statement is printed with the endpoint family that returned it",
           "| doaj_not_indexed | doaj |" in _s18r)
check_true("T61 ...and the day it was read", "2026-08-22T09:00:00" in _s18r)
check_true("T61 the Chinese statement the task asked for is printed verbatim",
           "未被 DOAJ 收录" in _s18r and "Crossref 元数据缺失 1 项" in _s18r)
check_true("T61 the counts print against the journal denominator, never as a share",
           "doaj_not_indexed: 1 of 1" in _s18r)
check_true("T61 the tracked Crossref field list is printed so its denominator is checkable",
           "abstracts-current, references-current" in _s18r)
check_true("T61 the section says absence from DOAJ is not a finding",
           "未被 DOAJ 收录 is not a finding" in _s18r)
check_true("T61 ...and that a Crossref zero is not one either",
           "A Crossref field at zero is not a finding either" in _s18r)
check_true("T61 ...and that a blank 是否预警 cell still means nobody checked",
           "still means nobody checked the CAS list" in _s18r)

# The refusal every line of this feature turns on. A count of signals is not a
# grade, and the page must not contain one under any spelling.
check("T61 no verdict word reaches the computed half of any section",
      [word for word in ("predatory", "掠夺", "risk grade", "risk score", "risk tier")
       if word in "\n".join(all_body_lines(_risked)).lower()], [])
check("T61 the score is untouched by a collected signal",
      _risked["score"], build(_with_issn)["score"])
check("T61 ...and so is every metric",
      _risked["metrics"], build(_with_issn)["metrics"])
check("T61 no scoring component is fed from the risk join",
      [item["name"] for item in (_risked["score"].get("components") or [])
       if "risk" in item["name"].lower() or "journal" in item["source"].lower()], [])
check("T61 ...and none is registered under a name that could later be wired to one",
      [name for name in scoring.COMPONENT_NAMES if "risk" in name.lower()], [])

# CAV-33 is the short form the section prints beside the long JRN-08..12 block.
check_true("T61 Section 18 registers CAV-33", "CAV-33" in _risked["caveats"])
check_true("T61 ...and prints it verbatim from caveats.py",
           caveats.CAVEATS["CAV-33"] in caveat_text(_risked, 18))
check_true("T61 the long register is printed in the section's fixed half",
           all(f"**{key}**" in "\n".join(section(_risked, 18)["prose"])
               for key in ("JRN-08", "JRN-09", "JRN-10", "JRN-11", "JRN-12")))
check_true("T61 the dropped register records the refusal as a refusal",
           any("A predatory-journal verdict — refused" in line
               for line in section(_risked, 14)["prose"]))

# The three-way absence in one cell. A journal with no ISSN can never be looked
# up; one that simply was not in this collection can. Two cells, two next moves.
_mixed = report.build_report(
    corpus(_with_issn + [paper(1500, [author("Solo Person"), pi()], "2023 Mar", pi_index=1,
                               journal="Nanhai Reports")]),
    {}, None, FIXED_NOW, journal_table=_TABLE, journal_risk=_RISK_PAYLOAD)
_s18m = body_text(_mixed, 18)
check_true("T61 a journal the corpus recorded with no ISSN says exactly that",
           "| 无 ISSN，查不了 |" in _s18m)
check_true("T61 ...and is named in the block rather than only counted",
           "Nanhai Reports" in _s18m)
check("T61 ...and is not counted as checked", _mixed["journal_risk"]["journals_checked"], 1)

# A refused report has to carry both keys with value None so a consumer can read
# them without a guard. No other test exercises this combination.
_refused = report.build_report_from_path("pubmed_results/papers_20260722_120000.xlsx",
                                         {}, None, FIXED_NOW)
check_true("T61 a refused report still carries journal_risk", "journal_risk" in _refused)
check("T61 ...as None, never as an empty dict", _refused["journal_risk"], None)
check("T61 ...and its note as an empty string", _refused["journal_risk_note"], "")
check("T61 the refusal shape has every key the success shape has",
      sorted(set(build(_with_issn)) - set(_refused)), [])

# Both keys reach the JSON artifact, which is what a consumer actually reads.
_record = report.json_record(_risked)
check_true("T61 the risk join reaches the JSON record", "journal_risk" in _record)
check("T61 ...carrying the per-signal endpoints for anyone who wants to check them",
      _record["journal_risk"]["rows"][0]["signals"][0]["endpoint"],
      "https://doaj.org/api/search/journals/issn%3A0168-8278")
check("T61 the schema version was bumped for the new top-level key",
      _record["schema_version"], 5)
check("T61 ...and json_record still omits the rendered sections",
      "sections" in _record, False)


print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(0 if _failed == 0 else 1)
