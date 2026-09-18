#!/usr/bin/env python3
"""
The corpus merge, and the five things it used to get wrong about its own numbers.

Every assertion here is a regression: each one fails on the code as it stood
before this file existed. They share one root cause — the merge changes the
corpus after the facts about the corpus have been decided, and four separate
readers were re-deriving those facts from the merged list afterwards.

The combination none of the other 2893 assertions covered is the first block:
**the PubMed side verifies nothing and the OpenAlex merge is on.** Alone,
either is exercised. Together they are the state in which:

  - identity warning G2 disappeared, because it was inferred from "every record
    is stamped 待确认" and merged OpenAlex records carry a real role. The report
    then exited 0 and the corpus joined the comparison ranking — a corpus that
    is "every paper by anyone with this name" certified as one person's;
  - Section 1 printed `fetched 10 / verified 30 / rejected -20`, because
    `verified` had been overwritten with the size of the merged corpus while
    `rejected` was still subtracting it from the PubMed efetch count.

Blocks 3 and 4 are the merge's own arithmetic: a donated DOI that was not
indexed let one paper enter twice and inflate the denominator of every count in
the report, and OpenAlex deduplicating against itself was printed as two
databases agreeing.

Fully offline. No socket is opened: `search_pubmed` and `fetch_details` are
replaced with scripted stubs, and the OpenAlex client is a canned-reply object.

Run: python tests/test_merge_provenance.py
"""

from __future__ import annotations

import glob
import itertools
import json
import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor import cli, http_client, openalex, pubmed_api  # noqa: E402
from check_your_advisor.http_client import Response  # noqa: E402
from check_your_advisor.profile import report, roles  # noqa: E402

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


AUTHOR = "Zhu Guangwei"
OPENALEX_ID = "A5023888391"

# Ten PubMed records under the right name and the wrong institution. With
# require_affiliation on and a keyword that matches none of them, the identity
# filter keeps nothing and the harvest falls back to keeping everything.
PUBMED_N = 10
# Twenty OpenAlex works, none of which PubMed holds. Every one of them carries a
# real role, which is what falsified the old "every record is 待确认" inference.
OPENALEX_N = 20


def pubmed_record(index: int) -> dict:
    return {
        "pmid": str(9000 + index),
        "title": f"A PubMed paper {index}",
        "authors_str": AUTHOR,
        "authors": [{"name": AUTHOR, "last": "Zhu", "fore": "Guangwei", "initials": "G",
                     "affiliation": "Somewhere Else University", "email": "",
                     "orcid": "", "is_corresponding": True, "equal_contrib": False}],
        "journal": "Journal of Things", "issn": "1234-5678", "issn_type": "",
        "pub_date": "2023 Mar", "pub_year": "2023", "volume": "", "issue": "",
        "pages": "", "doi": "", "pmc_id": "", "abstract": "",
    }


def openalex_work(index: int) -> dict:
    """One work where the resolved author holds the first slot."""
    return {
        "id": f"https://openalex.org/W{7000 + index}",
        "title": f"An OpenAlex work {index}",
        "publication_year": 2023,
        "publication_date": "2023-05-01",
        "doi": f"https://doi.org/10.5555/oa{index}",
        "ids": {},
        "authorships": [
            {"author_position": "first", "is_corresponding": True,
             "author": {"id": f"https://openalex.org/{OPENALEX_ID}",
                        "display_name": "Guangwei Zhu"},
             "institutions": []},
            {"author_position": "last",
             "author": {"id": "https://openalex.org/A8", "display_name": "Someone Last"},
             "institutions": []},
        ],
    }


class WorksClient:
    """Serves one page of works and nothing else. Records every URL it saw."""

    def __init__(self, works: list[dict]) -> None:
        self.works = works
        self.calls: list[str] = []

    def get(self, url: str, **kwargs):
        self.calls.append(url)
        payload = {"meta": {"count": len(self.works), "next_cursor": None},
                   "results": self.works}
        return Response(200, {"Content-Type": "application/json"},
                        json.dumps(payload).encode("utf-8"))

    def close(self) -> None:
        pass


def close_log_handlers() -> None:
    """`setup_logging` keeps the log file open; Windows will not remove the dir."""
    for handler in list(logging.getLogger("check_your_advisor").handlers):
        handler.close()
    logging.getLogger("check_your_advisor").handlers.clear()


# The console handler `setup_logging` installs is real. Keep the suite readable
# without suppressing anything a failing assertion would need.
logging.getLogger("check_your_advisor").setLevel(logging.ERROR)


# ============================================================
# 1. PubMed verified nothing and the merge ran anyway
# ============================================================

print("\n--- the combination: zero verified on the PubMed side, merge enabled ---")


def stub_search(name, years_back, api_key, retmax=500, identity=None,
                provenance=None, max_records=None, **kwargs):
    if provenance is not None:
        provenance.update({
            "esearch_term": f'"{name}"[Author]', "esearch_matched": PUBMED_N,
            "pmids_returned": PUBMED_N, "retmax": retmax, "max_records": max_records,
            "pages_fetched": 1, "duplicates_dropped": 0,
            "mindate": "2018/01/01", "maxdate": "2023/12/31",
            "narrowed_by_affiliation": False, "truncated": False,
            "years_back": years_back,
        })
    return [str(9000 + i) for i in range(PUBMED_N)]


def stub_details(pmids, api_key, delay):
    return [pubmed_record(i) for i in range(len(pmids))]


_real = (pubmed_api.search_pubmed, pubmed_api.fetch_details,
         http_client.RobustHTTPClient)
_client = WorksClient([openalex_work(i) for i in range(OPENALEX_N)])

with tempfile.TemporaryDirectory() as tmp:
    try:
        pubmed_api.search_pubmed = stub_search
        pubmed_api.fetch_details = stub_details
        http_client.RobustHTTPClient = lambda *a, **k: _client
        cli.cmd_fetch([
            "--author", AUTHOR, "--output-dir", tmp, "--no-download",
            "--years-back", "5",
            # The keyword no record matches, made binding. This is the documented
            # way to end up with zero verified records.
            "--affiliation-keyword", "Nanhai Medical University",
            "--require-affiliation",
            "--openalex-author-id", OPENALEX_ID, "--openalex-works",
        ])
    finally:
        (pubmed_api.search_pubmed, pubmed_api.fetch_details,
         http_client.RobustHTTPClient) = _real
        close_log_handlers()

    written = sorted(glob.glob(os.path.join(tmp, "papers_*.json")))
    check("the harvest wrote exactly one corpus", len(written), 1)
    with open(written[0], encoding="utf-8") as handle:
        data = json.load(handle)
    search = data["search"]
    papers = data["papers"]

    check("the merged corpus is PubMed's ten plus OpenAlex's twenty",
          len(papers), PUBMED_N + OPENALEX_N)
    check("...and the merged records are not all stamped 待确认, which is what "
          "falsified the old inference",
          sum(1 for p in papers if p.get("role") != "待确认"), OPENALEX_N)

    # ---- F1: the fallback is recorded, not reconstructed ----
    check("the fallback is recorded as an explicit boolean before the merge",
          search.get("fallback_fired"), True)
    corpus = cli._profile_corpus(papers, {"author_name": AUTHOR}, search)
    check("...and the reader takes it from the record", corpus["fallback_fired"], True)
    check("...marked as recorded rather than inferred",
          corpus.get("fallback_fired_inferred"), False)
    check("identity warning G2 survives the merge",
          [w["id"] for w in report.check_identity_warnings(corpus)], ["G2"])
    built = report.build_report(corpus, {}, None, None)
    check("...so the report still exits 1 on an unverifiable corpus",
          built["exit_code"], 1)
    check("...and still refuses to hold a rank beside other corpora",
          bool(built["warnings"]), True)

    # ---- F2: verified keeps its meaning and rejected cannot go negative ----
    #
    # The fallback fired on this harvest: nothing passed the identity filter, so
    # everything was kept. `verified` is therefore 0 and `rejected` is all ten,
    # which is what the harvest log printed at the time. It used to be taken
    # *after* the fallback replaced `matched_papers` with `all_papers`, so the
    # same run recorded verified=10 / rejected=0 — the exact opposite of its own
    # log, under a word that says "passed identity verification".
    check("fetched is what efetch parsed", search.get("fetched"), PUBMED_N)
    check("verified counts what passed the filter, before the fallback kept everything",
          search.get("verified"), 0)
    check("the merged size has its own key",
          search.get("corpus_total"), PUBMED_N + OPENALEX_N)
    counts = corpus["counts"]
    check("rejected is a real subtraction of two PubMed counts",
          counts.get("rejected"), PUBMED_N)
    check_true("...and is never negative", (counts.get("rejected") or 0) >= 0)
    check("...with the merged size carried beside it, not folded into it",
          counts.get("corpus_total"), PUBMED_N + OPENALEX_N)

    # Section 1 has to print the two denominators apart, or the reader is back to
    # subtracting the wrong pair by hand.
    body = "\n".join(report._provenance_body(built["provenance"]))
    check_true("Section 1 labels the three counts as PubMed-side",
               "PubMed records: fetched 10" in body)
    check_true("...and prints kept 0 beside the fired fallback, matching the harvest log",
               "kept 0 / rejected 10" in body)
    check_true("...and does not print a negative rejection count", "rejected -" not in body)
    check_true("...and states the fallback", "identity fallback fired: True" in body)


# ============================================================
# 2. A corpus with no explicit key still gets an answer, marked as a guess
# ============================================================

print("\n--- the compatibility path for files harvested before the key existed ---")

legacy_papers = [dict(pubmed_record(i), role="待确认") for i in range(3)]
legacy_search = {"esearch_term": "x", "fetched": 3, "verified": 3,
                 "identity": {"orcid": "", "affiliation_keywords": [], "email_domains": [],
                              "openalex_author_id": "", "author_name": AUTHOR}}
legacy = cli._profile_corpus(legacy_papers, {"author_name": AUTHOR}, legacy_search)
check("a file with no recorded key still answers", legacy["fallback_fired"], True)
check("...and says the answer was inferred", legacy.get("fallback_fired_inferred"), True)
legacy_body = "\n".join(report._provenance_body(
    report.build_report(legacy, {}, None, None)["provenance"]))
check_true("...which Section 1 prints rather than passing a guess off as a record",
           "inferred from the role stamps, not recorded" in legacy_body)

recorded_false = cli._profile_corpus(
    legacy_papers, {"author_name": AUTHOR}, dict(legacy_search, fallback_fired=False))
check("an explicit False beats the inference, both ways",
      (recorded_false["fallback_fired"], recorded_false.get("fallback_fired_inferred")),
      (False, False))


# ============================================================
# 3. rejected can never be printed negative again
# ============================================================

print("\n--- the guard that stops a negative rejection count reaching the page ---")

# The verdict is the same as it always was — this corpus is not reportable — but
# it is delivered as a refusal page rather than as an uncaught ValueError.
# `main()` has no try, so raising from here killed `profile` on an old-format
# corpus with a traceback: the right diagnosis, printed where nobody reads it and
# with no exit code a script could branch on.
try:
    _bad = cli._corpus_counts({"fetched": 10, "verified": 30}, [])
except ValueError as exc:  # what it used to do, and what nothing above it caught
    _bad = {"raised": type(exc).__name__}
check("verified above fetched is recorded, not raised", _bad.get("inconsistent"),
      {"fetched": 10, "verified": 30, "rejected_would_be": -20})
check("...and no rejected count is emitted at all", "rejected" in _bad, False)

_bad_corpus = {"papers": [{"pmid": "1", "title": "t", "pub_year": "2024",
                           "authors": [{"name": "Doe Jane", "last": "Doe", "fore": "Jane"}]}],
               "identity": {"author_name": AUTHOR}, "counts": _bad}
_bad_report = report.build_report(_bad_corpus, {}, None, None)
_bad_gate = _bad_report.get("gate") or {}
check("...which the report turns into a refusal page", _bad_gate.get("id"), "G6")
check("...refused rather than rendered", _bad_report["refused"], True)
check("...with a non-zero exit code", _bad_report["exit_code"], 1)
check_true("...naming both operands so the fix is obvious",
           "verified=30" in _bad_gate.get("message", "")
           and "fetched=10" in _bad_gate.get("message", ""))
check_true("...and pointing at the key the merged size belongs in",
           "corpus_total" in _bad_gate.get("message", ""))
check_true("...and saying what to run", "harvest" in _bad_gate.get("message", ""))

check("the ordinary case is untouched",
      cli._corpus_counts({"fetched": 10, "verified": 4}, [])["rejected"], 6)
check("equal counts are fine", cli._corpus_counts({"fetched": 4, "verified": 4}, [])["rejected"], 0)
check("a consistent corpus records no inconsistency",
      "inconsistent" in cli._corpus_counts({"fetched": 10, "verified": 4}, []), False)


# ============================================================
# 4. A donated DOI is indexed in the same breath it is donated
# ============================================================

print("\n--- W1 matches on PMID and donates a DOI; W2 carries that DOI ---")
# The exact scenario the index gap let through: the PubMed record has no DOI,
# W1 reaches it on PMID and hands one over, and W2 — same paper, same DOI, a
# different title — misses because the donated key was never indexed. It then
# enters as a new paper and inflates `merged_total`, which is the denominator of
# every count in the report. The title-duplicate check downstream cannot catch
# it: the two records do not share a title.

pm_no_doi = [{"pmid": "111", "title": "The registered title", "pub_year": "2024",
              "doi": "", "authors": []}]
w1 = {"pmid": "111", "title": "OpenAlex's spelling of the same paper",
      "pub_year": "2024", "doi": "10.1/donated", "authors": []}
w2 = {"pmid": "", "title": "A third spelling entirely", "pub_year": "2024",
      "doi": "10.1/donated", "authors": []}
merged = openalex.merge_corpora(pm_no_doi, [w1, w2])

check("one paper in, one paper out", merged["counts"]["merged_total"], 1)
check("...and nothing was added as OpenAlex-only", merged["counts"]["openalex_only"], 0)
check("the donated DOI landed on the PubMed record", merged["papers"][0]["doi"], "10.1/donated")
check("W1 matched on the PMID PubMed itself published",
      (merged["matched_on"]["pmid"], merged["matched_on"]["doi"]), (1, 0))
check("...and W2, which only reached that record through the DOI W1 donated, is "
      "counted as OpenAlex repeating itself — PubMed never published that key",
      merged.get("openalex_internal_duplicates"), {"doi": 1, "pmid": 0, "title_year": 0})
check("the record is confirmed by both sources exactly once",
      merged["papers"][0]["confirmed_by"], ["openalex", "pubmed"])

# The PubMed record already having a DOI is the control for the *donation*:
# nothing is handed over, and the record keeps the DOI it was registered with.
# It still collapses to one paper, because W1 shares a PMID with it and a DOI
# with W2, and a record that bridges two entries proves they are one paper
# whether or not it donated anything.
pm_has_doi = [{"pmid": "111", "title": "The registered title", "pub_year": "2024",
               "doi": "10.1/original", "authors": []}]
control = openalex.merge_corpora(pm_has_doi, [w1, w2])
check("a record that already had a DOI keeps it",
      control["papers"][0]["doi"], "10.1/original")
check("...and W2 still lands on it through W1's DOI rather than entering twice",
      control["counts"]["merged_total"], 1)
check("...counted as an OpenAlex self-duplicate, not as PubMed agreeing",
      (control["matched_on"]["doi"],
       control["openalex_internal_duplicates"]["doi"]), (0, 1))

# A truly unrelated OpenAlex DOI, sharing nothing with anything, is the control
# that the collapse above is evidence-driven and not a merge that eats everything.
unrelated = openalex.merge_corpora(
    pm_has_doi,
    [w1, {"pmid": "", "title": "A different paper", "pub_year": "2024",
          "doi": "10.1/unrelated", "authors": []}])
check("a work sharing no key with anything enters as its own paper",
      unrelated["counts"]["merged_total"], 2)


# ============================================================
# 4b. The same records in a different order are the same corpus
# ============================================================

print("\n--- arrival order decides nothing: every permutation of the same works ---")

# One paper filed four ways. PubMed has the PMID and no DOI; W1 carries both that
# PMID and a DOI; W2 carries the same DOI under a different title; W3 carries
# only W2's title. Nothing links the PubMed record to W2 or W3 except through
# W1, so a merge that stops at the first key that hits gets a different answer
# depending on which work OpenAlex happened to serve first: W2 arriving before
# W1 takes the DOI slot, W1 then stops there and never tries its PMID, and the
# paper is counted twice with the two sources declared to have no overlap at all.
# `merged_total` is the denominator of every count in the report, so that made
# the whole page a fact about OpenAlex's sort order.
bridge_pubmed = [{"pmid": "111", "title": "The registered title", "pub_year": "2024",
                  "doi": "", "authors": []}]
bridge_works = [w1, w2,
                {"pmid": "", "title": "A third spelling entirely", "pub_year": "2024",
                 "doi": "", "authors": []}]

orders = list(itertools.permutations(bridge_works))
results = [openalex.merge_corpora(bridge_pubmed, [dict(work) for work in order])
           for order in orders]
check("every arrival order of the three works was tried", len(orders), 6)
check("...and each one collapses to the single paper",
      sorted({result["counts"]["merged_total"] for result in results}), [1])
check("...with one identical set of counts, not one per order",
      {json.dumps(result["counts"], sort_keys=True) for result in results},
      {json.dumps({"pubmed_total": 1, "openalex_total": 3, "merged_total": 1,
                   "pubmed_only": 0, "openalex_only": 0, "both": 1}, sort_keys=True)})
check("...one identical cross-source histogram: the PMID PubMed published, once",
      {json.dumps(result["matched_on"], sort_keys=True) for result in results},
      {json.dumps({"doi": 0, "pmid": 1, "title_year": 0}, sort_keys=True)})
check("...and one identical self-duplicate histogram for the other two hits",
      {json.dumps(result["openalex_internal_duplicates"], sort_keys=True)
       for result in results},
      {json.dumps({"doi": 1, "pmid": 0, "title_year": 1}, sort_keys=True)})
check("the record left standing is PubMed's, holding the donated DOI, in every order",
      {(result["papers"][0]["source"], result["papers"][0]["doi"],
        tuple(result["papers"][0]["confirmed_by"])) for result in results},
      {("pubmed", "10.1/donated", ("openalex", "pubmed"))})

# A longer chain, because folding one record into another can itself be folded
# again: A is folded into B, and B into the PubMed record, so the key that once
# pointed at A now has to be followed two hops to find the record still standing.
# Five works, every one of the 120 orders, one answer.
chain_pubmed = [{"pmid": "500", "doi": "", "title": "Registered", "pub_year": "2024",
                 "authors": []}]
chain_works = [
    {"pmid": "", "doi": "", "title": "Spelling A", "pub_year": "2024", "authors": []},
    {"pmid": "", "doi": "", "title": "Spelling B", "pub_year": "2024", "authors": []},
    {"pmid": "", "doi": "10.9/x", "title": "Spelling A", "pub_year": "2024", "authors": []},
    {"pmid": "", "doi": "10.9/x", "title": "Spelling B", "pub_year": "2024", "authors": []},
    {"pmid": "500", "doi": "10.9/x", "title": "Spelling E", "pub_year": "2024",
     "authors": []},
]
chain = {json.dumps({"counts": result["counts"], "matched_on": result["matched_on"],
                     "internal": result["openalex_internal_duplicates"]}, sort_keys=True)
         for result in (openalex.merge_corpora(chain_pubmed, [dict(work) for work in order])
                        for order in itertools.permutations(chain_works))}
check("a five-record chain has one answer across all 120 arrival orders",
      chain,
      {json.dumps({"counts": {"pubmed_total": 1, "openalex_total": 5, "merged_total": 1,
                              "pubmed_only": 0, "openalex_only": 0, "both": 1},
                   "matched_on": {"doi": 0, "pmid": 1, "title_year": 0},
                   "internal": {"doi": 2, "pmid": 0, "title_year": 2}}, sort_keys=True)})

# The half of the fix that is not about ordering: a record that merges registers
# *every* key it holds, not only the DOI it donated. W1's title is a key too, and
# the next work carrying that title has to land on the same paper.
by_title = openalex.merge_corpora(
    bridge_pubmed,
    [w1, {"pmid": "", "title": "OpenAlex's spelling of the same paper",
          "pub_year": "2024", "doi": "", "authors": []}])
check("a later work matching the merged record's OpenAlex title lands on it",
      by_title["counts"]["merged_total"], 1)
check("...as a same-source duplicate, because PubMed filed a different title",
      by_title["openalex_internal_duplicates"]["title_year"], 1)
check("...and claims no cross-source confirmation for it",
      by_title["matched_on"]["title_year"], 0)

# Two PubMed records are left alone even when an OpenAlex work hits both: PubMed
# deduplicating its own corpus is not this function's job, and collapsing a pair
# of them would move `pubmed_total` out from under every count that divides by it.
two_pubmed = openalex.merge_corpora(
    [{"pmid": "201", "title": "Same title", "pub_year": "2024", "doi": "", "authors": []},
     {"pmid": "202", "title": "Same title", "pub_year": "2024", "doi": "", "authors": []}],
    [{"pmid": "202", "title": "Same title", "pub_year": "2024", "doi": "", "authors": []}])
check("an OpenAlex work hitting two PubMed records collapses neither",
      two_pubmed["counts"]["merged_total"], 2)
check("...and confirms exactly the one it shares a PMID with",
      [paper["confirmed_by"] for paper in two_pubmed["papers"]],
      [["pubmed"], ["openalex", "pubmed"]])


# ============================================================
# 5. OpenAlex deduplicating against itself is not two databases agreeing
# ============================================================

print("\n--- same-source dedup counted apart from cross-source confirmation ---")

pm_unrelated = [{"pmid": "999", "title": "Unrelated", "pub_year": "2024",
                 "doi": "10.9/unrelated", "authors": []}]
dupes = openalex.merge_corpora(pm_unrelated, [
    {"pmid": "", "title": "Listed twice, spelling one", "pub_year": "2024",
     "doi": "10.5/dup", "authors": []},
    {"pmid": "", "title": "Listed twice, spelling two", "pub_year": "2024",
     "doi": "10.5/dup", "authors": []},
])
check("no cross-source confirmation is claimed",
      dupes["matched_on"], {"doi": 0, "pmid": 0, "title_year": 0})
check("...and the self-duplicate is counted as one",
      dupes.get("openalex_internal_duplicates"), {"doi": 1, "pmid": 0, "title_year": 0})
check("the two OpenAlex spellings still collapse to one record",
      dupes["counts"]["merged_total"], 2)
check("...and no record claims two sources hold it", dupes["counts"]["both"], 0)

# Both kinds in one merge, so the split is checkable rather than a relabelling.
mixed = openalex.merge_corpora(
    [{"pmid": "1", "title": "Shared", "pub_year": "2024", "doi": "10.1/shared", "authors": []}],
    [{"pmid": "", "title": "Shared", "pub_year": "2024", "doi": "10.1/shared", "authors": []},
     {"pmid": "", "title": "OA one", "pub_year": "2024", "doi": "10.2/oa", "authors": []},
     {"pmid": "", "title": "OA one again", "pub_year": "2024", "doi": "10.2/oa", "authors": []}],
)
check("one cross-source confirmation", mixed["matched_on"]["doi"], 1)
check("...and one same-source duplicate, in a different bucket",
      (mixed.get("openalex_internal_duplicates") or {}).get("doi"), 1)

# And the page prints them as two sentences, because one number is the strongest
# claim on it and the other says nothing about PubMed at all.
lines = "\n".join(report._openalex_lines(
    {"openalex_works": {"merged": True, "works_returned": 3, "works_matched": 3,
                        "pages_fetched": 1, "max_works": 500, "works_usable": 3,
                        "works_without_lead_slot": 0,
                        "matched_on": mixed["matched_on"],
                        "openalex_internal_duplicates":
                            mixed.get("openalex_internal_duplicates") or {}}},
    {"openalex_author_id": OPENALEX_ID}))
check_true("the cross-source line says what it counts",
           "cross-source confirmations" in lines and "DOI 1" in lines)
check_true("...and states the test it applies, which is the key's publisher and "
           "not the record's",
           "on a key PubMed itself published" in lines)
check_true("the same-source line is separate and says it confirms nothing",
           "deduplicated against each other" in lines and "confirming nothing" in lines)
check_true("...and names the near-miss it also holds, so a reader is not left "
           "reading a donated DOI as PubMed agreeing",
           "another OpenAlex work donated" in lines
           and "PubMed never published that key" in lines)
check_true("...and is not folded into the cross-source total",
           lines.count("DOI 1") == 2)

# A corpus harvested before the split existed has no such block, and prints one
# line rather than inventing a zero for the other.
old_lines = "\n".join(report._openalex_lines(
    {"openalex_works": {"merged": True, "works_returned": 3, "works_matched": 3,
                        "pages_fetched": 1, "max_works": 500, "works_usable": 3,
                        "works_without_lead_slot": 0,
                        "matched_on": {"doi": 1, "pmid": 0, "title_year": 0}}},
    {"openalex_author_id": OPENALEX_ID}))
check_true("a pre-split corpus prints no same-source line at all",
           "deduplicated against each other" not in old_lines)


# ============================================================
# 6. A sole fuzzy-search candidate is adopted, and says it was not confirmed
# ============================================================

print("\n--- the unique candidate is labelled as unconfirmed on the page ---")

unique_lines = "\n".join(report._openalex_lines(
    {"openalex": {"resolution": "unique", "query": "display_name.search:Zhu Guangwei",
                  "source": "openalex", "retrieved_at": "2026-08-22",
                  "candidates": [{"display_name": "Guangwei Zhu",
                                  "openalex_author_id": OPENALEX_ID,
                                  "orcid": "", "works_count": 43, "institutions": []}]}},
    {"openalex_author_id": OPENALEX_ID}))
check_true("the sole candidate is named as a fuzzy search result",
           "sole candidate of a fuzzy `display_name.search`" in unique_lines)
check_true("...and as adopted without confirmation",
           "adopted without confirmation" in unique_lines)
check_true("...telling the reader what to check it against",
           "--openalex-author-id <id>" in unique_lines)


# ============================================================
# 7. An OpenAlex id is evidence only where a record carries it
# ============================================================

print("\n--- the id clears G3 only for the records that hold it ---")


def id_corpus(author_extra: dict) -> dict:
    return {
        "identity": {"author_name": AUTHOR, "orcid": "", "affiliation_keywords": [],
                     "email_domains": [], "openalex_author_id": OPENALEX_ID},
        "fallback_fired": False,
        "papers": [{"pmid": "1", "title": "t", "pub_year": "2024",
                    "authors": [{"name": AUTHOR, "affiliation": "", "email": "",
                                 "orcid": "", **author_extra}]}],
    }


check("a PubMed-only corpus is not certified by an id no record carries",
      [w["id"] for w in report.check_identity_warnings(id_corpus({}))], ["G3"])
check("...and the same id on a record does clear it",
      report.check_identity_warnings(
          id_corpus({"openalex_author_id": OPENALEX_ID})), [])
check("...matching on the normalised id, not the spelling",
      report.check_identity_warnings(
          id_corpus({"openalex_author_id": f"https://openalex.org/{OPENALEX_ID}"})), [])
check("...and a different author's id is not this author's evidence",
      [w["id"] for w in report.check_identity_warnings(
          id_corpus({"openalex_author_id": "A9999999999"}))], ["G3"])

# The harvest-time half of the same rule: without --openalex-works, no record
# can ever carry the id, so the pre-flight check must not call the run evidenced.
check("an id with no works merge is not pre-flight evidence",
      cli._has_identity_evidence({"openalex_author_id": OPENALEX_ID}), False)
check("...and is, once the merge that supplies those records is on",
      cli._has_identity_evidence({"openalex_author_id": OPENALEX_ID}, merge_works=True), True)


# ============================================================
# 8. Two databases agreeing is not the absence of identity evidence
# ============================================================

print("\n--- the id survives the fold that throws its byline away ---")
# The sixth thing the merge used to get wrong about its own numbers, and the
# only one that got *worse* the better the two sources agreed. `_fold` keeps the
# PubMed record's byline, and the byline is where `openalex_author_id` lives, so
# every paper both databases held contributed 0 to
# `openalex_id_record_share` while every paper only OpenAlex held contributed 1.
# The number therefore measured how little the two sources overlapped. A corpus
# in which PubMed confirmed all ten OpenAlex works scored 0/10 and G3 declared
# the identity unverifiable; the same ten works against a PubMed set they shared
# nothing with scored 10/20 and cleared.

ID_IDENTITY = {"author_name": AUTHOR, "orcid": "", "email_domains": [],
               "affiliation_keywords": [], "openalex_author_id": OPENALEX_ID}


def pubmed_side(doi: str, extra_author: dict | None = None) -> dict:
    """A PubMed record: real byline, no OpenAlex id anywhere on it."""
    authors = [{"name": AUTHOR, "last": "Zhu", "fore": "Guangwei", "initials": "G",
                "affiliation": "", "email": "", "orcid": ""}]
    if extra_author is not None:
        authors.append(extra_author)
    return {"pmid": doi.replace("/", ""), "title": f"Paper {doi}", "pub_year": "2024",
            "doi": doi, "authors": authors}


def openalex_side(doi: str, author_id: str = OPENALEX_ID) -> dict:
    """The same paper as OpenAlex files it, byline carrying the author id."""
    return {"pmid": "", "title": f"OpenAlex spelling of {doi}", "pub_year": "2024",
            "doi": doi, "openalex_work_id": "W1",
            "authors": [{"name": AUTHOR, "last": "Zhu", "fore": "Guangwei",
                         "initials": "G", "affiliation": "", "email": "", "orcid": "",
                         "openalex_author_id": author_id},
                        {"name": "Wang Lei", "last": "Wang", "fore": "Lei",
                         "initials": "L", "affiliation": "", "email": "", "orcid": "",
                         "openalex_author_id": "A9999999999"}]}


agreed = openalex.merge_corpora([pubmed_side("10.1/a")], [openalex_side("10.1/a")])
kept = agreed["papers"][0]

check("the two sources agree on the one paper", kept["confirmed_by"], ["openalex", "pubmed"])
check("...and the surviving byline is still PubMed's, unedited",
      [a.get("openalex_author_id", "") for a in kept["authors"]], [""])
check("...so the id is kept as a fact about the record, not guessed onto a name",
      kept.get("openalex_author_ids"), sorted([OPENALEX_ID, "A9999999999"]))
check_true("...which is the whole confirming byline, because saying which of "
           "PubMed's two names the id belongs to would be a guess",
           "A9999999999" in (kept.get("openalex_author_ids") or [])
           and len(kept["authors"]) == 1)

# The two derivations the fix has to keep in step. They are not the same shape —
# one is per record and one is per paper — so the assertion is that they cannot
# describe this corpus differently: the share counts the record, and the tier
# names the id that carried it.
check("the corpus-wide share counts the confirmed record",
      report.openalex_id_record_share(agreed["papers"], OPENALEX_ID), (1, 1))
check("...and the per-paper derivation names the same evidence",
      roles.resolve_pi(kept, AUTHOR, ID_IDENTITY)["pi_evidence"], "openalex")
check("...which makes the paper verified rather than a bare name match",
      roles.resolve_pi(kept, AUTHOR, ID_IDENTITY)["disposition"], "verified")

# The bug direction, stated as the comparison that used to invert: ten works
# PubMed confirms cannot score below the same ten works PubMed has never heard of.
confirmed = openalex.merge_corpora([pubmed_side(f"10.1/c{i}") for i in range(10)],
                                   [openalex_side(f"10.1/c{i}") for i in range(10)])
disjoint = openalex.merge_corpora([pubmed_side(f"10.1/d{i}") for i in range(10)],
                                  [openalex_side(f"10.2/e{i}") for i in range(10)])
confirmed_share = report.openalex_id_record_share(confirmed["papers"], OPENALEX_ID)
disjoint_share = report.openalex_id_record_share(disjoint["papers"], OPENALEX_ID)
check("every record confirmed by both is every record the id reached",
      confirmed_share, (10, 10))
check("...while a corpus sharing nothing keeps the half it actually evidenced",
      disjoint_share, (10, 20))
check_true("...and the better-confirmed corpus no longer scores worse",
           confirmed_share[0] / confirmed_share[1] >= disjoint_share[0] / disjoint_share[1])
check("a corpus of records both databases hold is no longer called unverifiable",
      [w["id"] for w in report.check_identity_warnings(
          {"identity": ID_IDENTITY, "fallback_fired": False, "papers": confirmed["papers"]})],
      [])

# Controls. The fold has to carry a fact, not clear the gate for anything that
# was merged at all.
other = openalex.merge_corpora([pubmed_side("10.1/f")],
                               [openalex_side("10.1/f", author_id="A7777777777")])
check("another author's id folded in is not this author's evidence",
      report.openalex_id_record_share(other["papers"], OPENALEX_ID), (0, 1))
check("...and the per-paper tier says the same",
      roles.resolve_pi(other["papers"][0], AUTHOR, ID_IDENTITY)["pi_evidence"], "name_only")
check("a PubMed record nothing merged into carries no record-level id",
      openalex.merge_corpora([pubmed_side("10.1/g")], [])["papers"][0]
      .get("openalex_author_ids"), None)
check("...and an OpenAlex-only record still answers off its own byline",
      report.openalex_id_record_share(
          openalex.merge_corpora([], [openalex_side("10.1/h")])["papers"], OPENALEX_ID),
      (1, 1))
check("an OpenAlex work folded into another does not copy that record's own ids "
      "back onto it",
      openalex.merge_corpora([], [openalex_side("10.1/i"),
                                  openalex_side("10.1/i")])["papers"][0]
      .get("openalex_author_ids"), None)

# The record-level fact says which paper, never which byline slot, and it is
# kept out of the slot decision for exactly that reason: applied to every
# name-matching candidate it would flatten the affiliation match that does tell
# them apart, and move the PI onto the other person of the same name.
two_of_a_name = pubmed_side("10.1/j", extra_author={
    "name": AUTHOR, "last": "Zhu", "fore": "Guangwei", "initials": "G",
    "affiliation": "Fujian Medical University", "email": "", "orcid": ""})
namesakes = openalex.merge_corpora([two_of_a_name], [openalex_side("10.1/j")])["papers"][0]
resolved_namesakes = roles.resolve_pi(
    namesakes, AUTHOR, {**ID_IDENTITY, "affiliation_keywords": ["Fujian Medical University"]})
check("the affiliation match still decides which namesake is the PI",
      resolved_namesakes["pi_index"], 1)
check("...and is not reported as ambiguous by a tier both of them share",
      resolved_namesakes["pi_ambiguous"], False)


# ============================================================
# 9. Three readers of one fact, two of which had been updated
# ============================================================

print("\n--- the persisted role string was the reader left behind ---")
# `_fold` keeps the confirming OpenAlex work's author ids as a record-level fact
# and deliberately never writes them onto a byline. Two of the three places that
# answer "did this id reach this record" were changed to read them —
# `report.openalex_id_record_share` and `roles.resolve_pi`. The third,
# `cli._corpus_counts`, still parsed only the persisted role string, and the role
# of a merged record still says name-only. Section 1 printed both answers, two
# lines apart, over one corpus:
#
#   - identity evidence on records: 0 of 10 ... none; name_only 10
#   - openalex author id on records: 10 of 10 (share 1.00)
#
# Every assertion below fails on the code as it stood before this block existed.

NAME_ONLY_ROLE = "第一作者 [机构未验证⚠]"
TEST_ORCID = "0000-0002-1825-0097"
TEST_AFFILIATION = "Fujian Medical University"

# Three of the four evidence kinds are configured on purpose: the raise has to be
# provably a raise, so the corpus needs a tier above openalex and one below it.
THREE_WAY_IDENTITY = {"author_name": AUTHOR, "orcid": TEST_ORCID, "email_domains": [],
                      "affiliation_keywords": [TEST_AFFILIATION],
                      "openalex_author_id": OPENALEX_ID}


def person(**extra) -> dict:
    entry = {"name": AUTHOR, "last": "Zhu", "fore": "Guangwei", "initials": "G",
             "affiliation": "", "email": "", "orcid": "", "is_corresponding": False,
             "equal_contrib": False}
    entry.update(extra)
    return entry


def harvested(tag: str, role: str, author: dict, record_ids=(), confirmed=("pubmed",)) -> dict:
    """One record as `papers_*.json` persists it: a role string and a byline."""
    record = {"pmid": tag, "title": f"Paper {tag}", "journal": "Journal of Things",
              "issn": "1234-5678", "pub_date": "2024 Mar", "pub_year": "2024",
              "doi": f"10.9/{tag}", "authors": [author], "role": role,
              "confirmed_by": list(confirmed)}
    if record_ids:
        # Where `_fold` puts them, because the byline that held them was the one
        # thrown away.
        record["openalex_author_ids"] = sorted(record_ids)
    return record


def role_tier(paper: dict) -> str:
    """What `cli._corpus_counts` credits this single record with."""
    counts = cli._corpus_counts({"fetched": 1, "verified": 1}, [paper], THREE_WAY_IDENTITY)
    tiers = counts.get("by_evidence") or {}
    if tiers:
        return next(iter(tiers))
    return "name_only" if "name_only" in counts else "unrecorded"


# One corpus holding every combination the three readers could split on.
three_way = [
    # Both databases hold it: PubMed's byline survived, so the id is on the
    # record and the role still says the name matched and nothing else did.
    harvested("merged", NAME_ONLY_ROLE, person(), [OPENALEX_ID, "A9999999999"],
              ["openalex", "pubmed"]),
    # OpenAlex contributed it: the id is on the byline and in the role.
    harvested("openalex_only", f"第一作者 [OpenAlexOK {OPENALEX_ID}]",
              person(openalex_author_id=OPENALEX_ID), (), ["openalex"]),
    # PubMed alone, nothing verified it.
    harvested("pubmed_only", NAME_ONLY_ROLE, person()),
    # A stronger identifier already carried it. A database's clustering must not
    # relabel an ORCID match.
    harvested("orcid_and_id", "第一作者 [ORCIDOK]", person(orcid=TEST_ORCID), [OPENALEX_ID],
              ["openalex", "pubmed"]),
    # A weaker one did, so the record-level id raises it — which is the case a
    # fix that only refilled the name_only bucket would still get wrong.
    harvested("affiliation_and_id", f"末位作者 [机构OK {TEST_AFFILIATION}]",
              person(affiliation=TEST_AFFILIATION), [OPENALEX_ID], ["openalex", "pubmed"]),
]
EXPECTED_TIER = {"merged": "openalex", "openalex_only": "openalex",
                 "pubmed_only": "name_only", "orcid_and_id": "orcid",
                 "affiliation_and_id": "openalex"}

for record in three_way:
    tag = record["pmid"]
    from_role = role_tier(record)
    from_byline = roles.resolve_pi(record, AUTHOR, THREE_WAY_IDENTITY)["pi_evidence"]
    check(f"{tag}: the harvest histogram and the per-paper tier agree",
          (from_role, from_byline), (EXPECTED_TIER[tag], EXPECTED_TIER[tag]))
    if OPENALEX_ID in pubmed_api.record_openalex_author_ids(record):
        # The one-directional invariant that holds for any corpus: a record the
        # share counts is a record that carries identity evidence, so it cannot
        # be sitting in the histogram's zero bucket.
        check_true(f"{tag}: ...and a record the share counts is never called name-only",
                   roles.EVIDENCE_RANK[from_role] >= roles.EVIDENCE_RANK["openalex"])

three_way_counts = cli._corpus_counts({"fetched": 4, "verified": 2}, three_way,
                                      THREE_WAY_IDENTITY)
check("the histogram over the whole corpus",
      three_way_counts.get("by_evidence"), {"openalex": 3, "orcid": 1})
check("...with only the record nothing reached left in the zero bucket",
      three_way_counts.get("name_only"), 1)
three_way_share = report.openalex_id_record_share(three_way, OPENALEX_ID)
check("...and the share counting the four records the id reached", three_way_share, (4, 5))
check_true("...so no record the share counts is missing from the histogram",
           three_way_share[0] <= sum(three_way_counts["by_evidence"].values()))

# The control on the raise: it has to carry this author's id, not any id, and it
# is inert when no id was configured to reach anything.
check("another author's id folded in does not raise the tier",
      cli._corpus_counts({"fetched": 1, "verified": 0},
                         [harvested("stranger", NAME_ONLY_ROLE, person(), ["A7777777777"],
                                    ["openalex", "pubmed"])],
                         THREE_WAY_IDENTITY).get("name_only"), 1)
check("...and with no id configured the role string is again the only reader",
      cli._corpus_counts({"fetched": 1, "verified": 0},
                         [harvested("m", NAME_ONLY_ROLE, person(), [OPENALEX_ID],
                                    ["openalex", "pubmed"])],
                         {"openalex_author_id": ""}).get("name_only"), 1)
check("a role written before the markers still withholds the count when nothing reached it",
      "name_only" in cli._corpus_counts({"fetched": 1, "verified": 1},
                                        [harvested("legacy", "第一作者", person())],
                                        THREE_WAY_IDENTITY), False)

# The reported symptom itself: ten records, every one held by both databases.
all_confirmed = [harvested(f"both{i}", NAME_ONLY_ROLE, person(), [OPENALEX_ID],
                           ["openalex", "pubmed"]) for i in range(10)]
both_counts = cli._corpus_counts({"fetched": 10, "verified": 10}, all_confirmed,
                                 THREE_WAY_IDENTITY)
check("ten records both databases confirm are not ten bare name matches",
      (both_counts.get("by_evidence"), both_counts.get("name_only")), ({"openalex": 10}, 0))
check("...which is what the line below them says as well",
      report.openalex_id_record_share(all_confirmed, OPENALEX_ID), (10, 10))

# End to end, because the contradiction was two printed lines and not two dicts.
three_way_search = {"esearch_term": "x", "fetched": 4, "verified": 2, "corpus_total": 5,
                    "esearch_matched": 4, "pmids_returned": 4, "fallback_fired": False,
                    "identity": THREE_WAY_IDENTITY}
three_way_body = "\n".join(report._provenance_body(report.build_report(
    cli._profile_corpus(three_way, {"author_name": AUTHOR}, three_way_search),
    {}, None, None)["provenance"]))
check_true("Section 1 credits four of the five records with identity evidence",
           "identity evidence on records: 4 of 5" in three_way_body)
check_true("...and the line directly below counts the same four",
           "openalex author id on records: 4 of 5" in three_way_body)
check_true("...naming the tier that carried them rather than reporting none",
           "openalex 3, orcid 1; name_only 1" in three_way_body)

# The G3 banner is the fourth reader of the same fact, and it was the one
# printing the opposite. Four bare name matches plus two records the id reached
# is a share of 0.33, so G3 fires — and its bold line said "No identity evidence
# reached a single record in this corpus" directly above Section 1's
# "identity evidence on records: 2 of 6", with `openalex_id_on_records=2/6` in
# its own Observed values on the same line. Three numbers agreeing and one
# sentence denying all three.
thin_records = [harvested(f"bare{i}", NAME_ONLY_ROLE, person()) for i in range(4)]
thin_records += [harvested(f"reached{i}", NAME_ONLY_ROLE, person(), [OPENALEX_ID],
                           ["openalex", "pubmed"]) for i in range(2)]
thin_report = report.build_report(
    cli._profile_corpus(thin_records, {"author_name": AUTHOR},
                        {"esearch_term": "x", "fetched": 6, "verified": 0, "esearch_matched": 6,
                         "pmids_returned": 6, "fallback_fired": False, "identity": ID_IDENTITY}),
    {}, None, None)
thin_banner = next((line for sec in thin_report["sections"] for line in sec["warnings"]
                    if line.startswith("**Warning G3")), "")
thin_body = "\n".join(report._provenance_body(thin_report["provenance"]))
check("the thin corpus still warns", [w["id"] for w in thin_report["warnings"]], ["G3"])
check_true("Section 1 credits the two records the id reached",
           "identity evidence on records: 2 of 6" in thin_body)
check_true("...the line below it counts the same two",
           "openalex author id on records: 2 of 6" in thin_body)
check_true("...and so does the warning banner printed above both",
           "identity_evidence_on_records=2/6" in thin_banner
           and "openalex_id_on_records=2/6" in thin_banner)
check("...whose sentence no longer claims none of them were reached",
      any(phrase in thin_banner for phrase in report.G3_ZERO_REACH_PHRASES), False)
check_true("...saying instead that the id reached too few of them",
           "min_openalex_record_share requires" in thin_banner)

# The same root cause in prose: G3's fix text told the reader that a PubMed
# record can never carry the id, which the fold above is the counter-example to,
# and it was printed verbatim every time G3 fired.
_g3_fix = report.WARNINGS["G3"][2]
check("the G3 fix no longer denies the field the merge writes",
      "never carry that field" in _g3_fix, False)
check_true("...and says instead where the id does land on a PubMed record",
           "--openalex-works" in _g3_fix and "at record level" in _g3_fix)
check_true("...beside the threshold that actually decides the warning",
           "min_openalex_record_share" in _g3_fix)


print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(1 if _failed else 0)
