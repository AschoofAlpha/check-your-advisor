#!/usr/bin/env python3
"""
`openalex.py`: author disambiguation, works as a second corpus source, and the merge.

Three things are pinned here, and the failure paths matter more than the happy
ones because each of them is a way to turn "we could not find out" into a
confident answer:

- **Resolution never picks.** One candidate is adopted; two or more are listed
  and nothing is adopted. The assertion that matters is that the *most
  productive* candidate is not chosen — picking on `works_count` decides an
  identity question on a proxy, which is the same error as accepting a bare name
  match, and it would be invisible afterwards.
- **`none` and `error` are different.** "OpenAlex has never heard of this
  person" says something about the author; "we could not ask" says something
  about the network. Collapsing them would let a dead socket read as a finding.
- **The merge says where every record came from.** DOI, then PMID, then title +
  year, in that order, and the PubMed copy wins when both hold a paper — its
  record carries the affiliation strings and corresponding-author emails that
  every identity check downstream reads, and OpenAlex's does not.

No socket is opened. `StubClient` serves scripted replies keyed by a URL
fragment and records every URL it was asked for, so "the affiliation reached the
filter" is checkable rather than assumed — the same technique
tests/test_citations.py uses.

Fully offline. Run: python tests/test_openalex.py
"""

from __future__ import annotations

import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor import openalex  # noqa: E402
from check_your_advisor.http_client import Response  # noqa: E402
from check_your_advisor.profile import roles  # noqa: E402
from check_your_advisor.pubmed_api import evidence_tier_from_role  # noqa: E402

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


# Resolution logs the candidate list at WARNING in Chinese. That output is the
# product, not the test; with no handler installed it would land on a
# locale-encoded stderr.
logging.getLogger("check_your_advisor").setLevel(logging.CRITICAL)


class StubClient:
    """`RobustHTTPClient.get` with a script instead of a socket.

    `replies` maps a URL fragment to a `Response`, to None (a transport failure,
    which the real client returns rather than raising), or to an exception
    instance. Several entries may be listed under one fragment, in which case
    they are served in order — that is what makes cursor paging testable.
    """

    def __init__(self, replies=None, default=None):
        self.replies = {key: list(value) if isinstance(value, list) else [value]
                        for key, value in (replies or {}).items()}
        self.default = default if default is not None else Response(404, {}, b"")
        self.calls: list[str] = []
        self.stats = {"total_requests": 0}

    def get(self, url, accept_type="pdf", timeout=None, allow_redirects=True,
            extra_headers=None):
        self.calls.append(url)
        self.stats["total_requests"] += 1
        for token, queue in self.replies.items():
            if token in url:
                reply = queue.pop(0) if len(queue) > 1 else queue[0]
                if isinstance(reply, BaseException):
                    raise reply
                return reply
        return self.default


def ok(payload) -> Response:
    return Response(200, {"Content-Type": "application/json"},
                    json.dumps(payload).encode("utf-8"))


def api_author(suffix: str, name: str, orcid: str = "", works: int = 10,
               institution: str = "Example University", years=(2018, 2024)) -> dict:
    """One authors-API result, in the shape OpenAlex actually serves."""
    return {
        "id": f"https://openalex.org/A{suffix}",
        "display_name": name,
        "display_name_alternatives": [name.upper()],
        "orcid": f"https://orcid.org/{orcid}" if orcid else None,
        "works_count": works,
        "cited_by_count": works * 7,
        "affiliations": [{
            "institution": {"id": "https://openalex.org/I1", "display_name": institution,
                            "ror": "https://ror.org/abc", "country_code": "CN"},
            "years": list(years),
        }],
        "last_known_institutions": [
            {"id": "https://openalex.org/I1", "display_name": institution},
        ],
    }


# ============================================================
# 1. Identifier normalisation
# ============================================================

print("\n--- an OpenAlex author id has three spellings and one meaning ---")

for raw in ("A5023888391", "https://openalex.org/A5023888391",
            "openalex.org/a5023888391", "  A5023888391  "):
    check(f"{raw!r} normalises", openalex.normalise_openalex_id(raw), "A5023888391")
check("nothing normalises to nothing", openalex.normalise_openalex_id(""), "")
check("None normalises to nothing", openalex.normalise_openalex_id(None), "")
check("a work id is not an author id shape but still parses",
      openalex.normalise_openalex_id("https://openalex.org/W2741809807"), "")
check("an ORCID is not mistaken for an author id",
      openalex.normalise_openalex_id("0000-0002-1825-0097"), "")


# ============================================================
# 2. Author resolution — the normal path
# ============================================================

print("\n--- one candidate is adopted ---")

client = StubClient({"api.openalex.org/authors": ok({
    "meta": {"count": 1},
    "results": [api_author("5023888391", "Guangwei Zhu", orcid="0000-0002-1825-0097", works=43)],
})})
resolved = openalex.resolve_author(client, "Zhu Guangwei", affiliation="Example University",
                                   mailto="me@example.edu")

check("the resolution is unique", resolved["resolution"], "unique")
check("the id is adopted", resolved["openalex_author_id"], "A5023888391")
check("one candidate came back", len(resolved["candidates"]), 1)
check("the ORCID is stripped of its resolver prefix",
      resolved["candidates"][0]["orcid"], "0000-0002-1825-0097")
check("the works count survives for a human to check",
      resolved["candidates"][0]["works_count"], 43)
check("the institution history survives with its years",
      resolved["candidates"][0]["institutions"][0]["years"], [2018, 2024])
check("the institution is not duplicated by the two OpenAlex spellings of it",
      len(resolved["candidates"][0]["institutions"]), 1)
check("the source is recorded, because this is somebody else's assertion",
      resolved["source"], "openalex-authors-api")
check_true("...with the date it was fetched", resolved["retrieved_at"])
check_true("the name reached the filter", "display_name.search" in client.calls[0])
check_true("the affiliation reached the filter as OpenAlex's own institution filter",
           "last_known_institutions.display_name.search" in client.calls[0])
check_true("the mailto is sent", "mailto=me%40example.edu" in client.calls[0])
check("exactly one request was made", len(client.calls), 1)

# `mailto` is optional and is not a key. Omitted means the documented anonymous
# request, not a request with an empty parameter that OpenAlex has to parse.
bare = StubClient({"authors": ok({"results": [api_author("1", "Solo Person")]})})
openalex.resolve_author(bare, "Solo Person")
check_false("no mailto is sent when none was configured", "mailto=" in bare.calls[0])
check_false("and no institution filter when no affiliation was given",
            "last_known_institutions" in bare.calls[0])


# ============================================================
# 3. Author resolution — the failure paths
# ============================================================

print("\n--- more than one candidate: nothing is picked ---")

many = StubClient({"authors": ok({"results": [
    api_author("111", "Wei Zhang", orcid="0000-0001-0000-0001", works=8,
               institution="Provincial Hospital"),
    api_author("222", "Wei Zhang", works=250, institution="Institute of Chemistry"),
    api_author("333", "Wei Zhang", works=31, institution="School of Nursing"),
]})})
ambiguous = openalex.resolve_author(many, "Zhang Wei")

check("the resolution is ambiguous", ambiguous["resolution"], "ambiguous")
check("nothing is adopted", ambiguous["openalex_author_id"], "")
check("every candidate comes back for the user to pick from",
      [c["openalex_author_id"] for c in ambiguous["candidates"]], ["A111", "A222", "A333"])
# The one assertion this whole design exists for.
check_false("the most productive candidate was not silently chosen",
            ambiguous["openalex_author_id"] == "A222")
check_false("neither was the one that happens to carry an ORCID",
            ambiguous["openalex_author_id"] == "A111")

print("\n--- nobody, versus could not ask ---")

empty = StubClient({"authors": ok({"meta": {"count": 0}, "results": []})})
check("an empty result set is 'none'",
      openalex.resolve_author(empty, "Nobody Here")["resolution"], "none")

dead = StubClient({"authors": None})
check("a transport failure is 'error', not 'none'",
      openalex.resolve_author(dead, "Someone")["resolution"], "error")
check("an HTTP error is 'error' too",
      openalex.resolve_author(StubClient({"authors": Response(500, {}, b"")}),
                              "Someone")["resolution"], "error")
check("a body that is not JSON is 'error'",
      openalex.resolve_author(StubClient({"authors": Response(200, {}, b"<html>")}),
                              "Someone")["resolution"], "error")
check("a JSON array where an object belongs is 'error'",
      openalex.resolve_author(StubClient({"authors": ok([1, 2, 3])}),
                              "Someone")["resolution"], "error")
check("an error adopts nothing either",
      openalex.resolve_author(dead, "Someone")["openalex_author_id"], "")

nameless = StubClient({"authors": ok({"results": [api_author("1", "X Y")]})})
check("an empty name resolves to none", openalex.resolve_author(nameless, "")["resolution"], "none")
check("...without touching the network", nameless.calls, [])

# A result with no usable id is not a candidate; keeping it would produce a
# shortlist entry the user cannot act on.
junk = StubClient({"authors": ok({"results": [{"display_name": "No Id Person"}]})})
check("a result with no author id is not a candidate",
      openalex.resolve_author(junk, "No Id Person")["resolution"], "none")


# ============================================================
# 4. One work becomes one paper
# ============================================================

print("\n--- a work in the shape the rest of the package consumes ---")

WORK = {
    "id": "https://openalex.org/W2741809807",
    "doi": "https://doi.org/10.1038/S41586-021-03819-2",
    "title": "A structure prediction method",
    "publication_year": 2021,
    "publication_date": "2021-07-15",
    "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/34265844",
            "pmcid": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8371605"},
    "primary_location": {"source": {"display_name": "Nature", "issn_l": "0028-0836",
                                    "issn": ["1476-4687", "0028-0836"],
                                    "abbreviated_title": "Nature"}},
    "biblio": {"volume": "596", "issue": "7873", "first_page": "583", "last_page": "589"},
    "abstract_inverted_index": {"Protein": [0], "structure": [1], "prediction": [2]},
    "authorships": [
        {"author_position": "first",
         "author": {"id": "https://openalex.org/A5023888391", "display_name": "Guangwei Zhu",
                    "orcid": "https://orcid.org/0000-0002-1825-0097"},
         "raw_affiliation_strings": ["Dept of Surgery, Example University"],
         "is_corresponding": False},
        {"author_position": "middle",
         "author": {"id": "https://openalex.org/A999", "display_name": "Middle Person"},
         "institutions": [{"display_name": "Other Institute"}],
         "is_corresponding": False},
        {"author_position": "last",
         "author": {"id": "https://openalex.org/A777", "display_name": "Senior, Person"},
         "raw_affiliation_strings": ["Example University"],
         "is_corresponding": True},
    ],
}

paper = openalex.work_to_paper(WORK, "A5023888391")

check("the DOI is normalised to the bare, lowercased form",
      paper["doi"], "10.1038/s41586-021-03819-2")
check("the PMID is pulled out of the PubMed URL", paper["pmid"], "34265844")
check("the PMC accession is pulled out of its URL", paper["pmc_id"], "PMC8371605")
check("the journal name comes from the primary location", paper["journal"], "Nature")
check("the linking ISSN is the one journals.join_journals wants",
      paper["issn_linking"], "0028-0836")
check("an unlabelled ISSN list claims no type, so it cannot join the wrong column",
      paper["issn_type"], "")
check("the page range is rebuilt from biblio", paper["pages"], "583-589")
check("the volume and issue survive", (paper["volume"], paper["issue"]), ("596", "7873"))
check("the year is a string, matching parse_article", paper["pub_year"], "2021")
check("the abstract is rebuilt from the inverted index",
      paper["abstract"], "Protein structure prediction")
check("the work id is kept", paper["openalex_work_id"], "W2741809807")
check("the record says which source it came from", paper["source"], "openalex")
check("...and which sources hold it", paper["confirmed_by"], ["openalex"])

print("\n--- author records match _author_record's shape ---")

first = paper["authors"][0]
check("a given-name-first display name splits into last and fore",
      (first["last"], first["fore"]), ("Zhu", "Guangwei"))
check("...and `name` is rebuilt in PubMed's order, so one person is one roster row",
      first["name"], "Zhu Guangwei")
check("initials are derived", first["initials"], "G")
check("the ORCID loses its resolver prefix", first["orcid"], "0000-0002-1825-0097")
check("the raw affiliation string is preferred over the institution name",
      first["affiliation"], "Dept of Surgery, Example University")
check("the author id travels on the byline entry, for the profile-side tier check",
      first["openalex_author_id"], "A5023888391")
check("OpenAlex publishes no addresses, so email is empty rather than guessed",
      first["email"], "")
check("...and equal_contrib is False rather than invented", first["equal_contrib"], False)
check("the comma form splits the other way round",
      (paper["authors"][2]["last"], paper["authors"][2]["fore"]), ("Senior", "Person"))
check("a byline entry with only institutions falls back to them",
      paper["authors"][1]["affiliation"], "Other Institute")

print("\n--- the role carries the evidence, and only where a slot is held ---")

check("a first-author slot is stamped with the OpenAlex marker",
      paper["role"], "第一作者 [OpenAlexOK A5023888391]")
check("the marker round-trips to a tier",
      evidence_tier_from_role(paper["role"]), "openalex")
check("a last + corresponding author gets both roles",
      openalex.work_to_paper(WORK, "A777")["role"],
      "末位作者 / 通讯作者 [OpenAlexOK A777]")
check("a middle author holds no slot, so no role is stamped",
      openalex.work_to_paper(WORK, "A999")["role"], "")
check("an author not on the byline gets no role either",
      openalex.work_to_paper(WORK, "A00000")["role"], "")
check("with no author id at all nothing is claimed",
      openalex.work_to_paper(WORK, "")["role"], "")

print("\n--- what cannot be reported on at all ---")

check("a work with no title and no identifier is dropped",
      openalex.work_to_paper({"authorships": []}, "A1"), None)
check("a work with a title but nothing else survives",
      openalex.work_to_paper({"title": "Untitled elsewhere"}, "A1")["title"],
      "Untitled elsewhere")
check("a broken inverted index yields no abstract rather than half of one",
      openalex.work_to_paper({"title": "t", "abstract_inverted_index": {"a": "nope"}},
                             "A1")["abstract"], "")
check("an absent inverted index yields no abstract",
      openalex.work_to_paper({"title": "t"}, "A1")["abstract"], "")


# ============================================================
# 5. Works paging
# ============================================================

print("\n--- works are paged with a cursor, and the budget is printed not enforced silently ---")


def work(index: int, doi: str = "", pmid: str = "", title: str = "", year: int = 2023) -> dict:
    return {
        "id": f"https://openalex.org/W{index}",
        "doi": f"https://doi.org/{doi}" if doi else None,
        "title": title or f"Work {index}",
        "publication_year": year,
        "ids": {"pmid": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}"} if pmid else {},
        "authorships": [{
            "author_position": "first",
            "author": {"id": "https://openalex.org/A5023888391", "display_name": "Guangwei Zhu"},
            "raw_affiliation_strings": ["Example University"],
            "is_corresponding": False,
        }],
    }


paged = StubClient({"api.openalex.org/works": [
    ok({"meta": {"count": 5, "next_cursor": "c2"}, "results": [work(1), work(2)]}),
    ok({"meta": {"count": 5, "next_cursor": "c3"}, "results": [work(3), work(4)]}),
    ok({"meta": {"count": 5, "next_cursor": None}, "results": [work(5)]}),
]})
prov: dict = {}
works = openalex.fetch_works(paged, "A5023888391", from_year=2019, to_year=2024,
                             per_page=2, provenance=prov)

check("every page was collected", len(works), 5)
check("three requests were made", len(paged.calls), 3)
check_true("the first page opens the cursor", "cursor=%2A" in paged.calls[0])
check_true("the second page carries the cursor the first returned", "cursor=c2" in paged.calls[1])
check_true("the author id is the filter", "author.id%3AA5023888391" in paged.calls[0])
check_true("the window is a filter, not a post-hoc trim",
           "from_publication_date%3A2019-01-01" in paged.calls[0])
check("the reported total is recorded", prov["works_matched"], 5)
check("...beside how many actually came back", prov["works_returned"], 5)
check("...and how many pages that took", prov["pages_fetched"], 3)
check_false("a complete harvest is not truncated", prov["truncated"])

budgeted = StubClient({"works": [
    ok({"meta": {"count": 400, "next_cursor": "c2"}, "results": [work(i) for i in range(2)]}),
    ok({"meta": {"count": 400, "next_cursor": "c3"}, "results": [work(i) for i in range(2, 4)]}),
]})
prov2: dict = {}
capped = openalex.fetch_works(budgeted, "A5023888391", per_page=2, max_works=4,
                              provenance=prov2)
check("the budget stops the paging", len(capped), 4)
check("...and is recorded", prov2["max_works"], 4)
check("...and the shortfall is stated as a numerator over a denominator",
      (prov2["works_returned"], prov2["works_matched"]), (4, 400))
check_true("...and flagged as truncated rather than refused", prov2["truncated"])

check("no author id means no request at all",
      openalex.fetch_works(StubClient(), "", provenance={}), [])
nothing = StubClient({"works": ok({"meta": {"count": 0}, "results": []})})
prov3: dict = {}
check("an author with no works comes back empty",
      openalex.fetch_works(nothing, "A1", provenance=prov3), [])
check("...over a denominator of zero, not an unknown", prov3["works_matched"], 0)

broken = StubClient({"works": Response(503, {}, b"")})
prov4: dict = {}
check("a failed page ends the paging rather than looping",
      openalex.fetch_works(broken, "A1", provenance=prov4), [])
check("...and is not counted as a page fetched", prov4["pages_fetched"], 0)
# "the request failed" and "this author has no works" are the same empty list
# and completely different facts. Only the first is about the network.
check("...and is recorded as a failed request", prov4["request_failed"], True)
check("an author who genuinely has none is not marked failed",
      prov3["request_failed"], False)
check("a complete harvest is not marked failed either", prov["request_failed"], False)

# A failure part-way through is the dangerous one: a non-empty result that looks
# complete.
half = StubClient({"works": [
    ok({"meta": {"count": 50, "next_cursor": "c2"}, "results": [work(1), work(2)]}),
    Response(500, {}, b""),
]})
prov5: dict = {}
openalex.fetch_works(half, "A1", per_page=2, provenance=prov5)
check("a mid-harvest failure keeps what it got", prov5["works_returned"], 2)
check("...and still says the request failed", prov5["request_failed"], True)

# Two pages that repeat a work id: OpenAlex's index can shift mid-harvest for
# the same reason PubMed's can.
repeated = StubClient({"works": [
    ok({"meta": {"count": 3, "next_cursor": "c2"}, "results": [work(1), work(2)]}),
    ok({"meta": {"count": 3, "next_cursor": None}, "results": [work(2), work(3)]}),
]})
check("a work repeated across pages is kept once",
      len(openalex.fetch_works(repeated, "A1", per_page=2, provenance={})), 3)


# ============================================================
# 6. The merge
# ============================================================

print("\n--- DOI, then PMID, then title + year ---")


def pubmed_paper(pmid: str, doi: str = "", title: str = "", year: str = "2023") -> dict:
    return {"pmid": pmid, "doi": doi, "title": title or f"PubMed {pmid}",
            "pub_year": year, "authors": [], "role": "第一作者 [ORCIDOK]"}


merged = openalex.merge_corpora(
    [
        pubmed_paper("1", doi="10.1/aaa", title="Shared by DOI"),
        pubmed_paper("2", title="Shared by PMID"),
        pubmed_paper("3", title="Shared by title and year", year="2020"),
        pubmed_paper("4", doi="10.1/ddd", title="PubMed only"),
    ],
    [
        openalex.work_to_paper(work(11, doi="10.1/AAA", title="Different typesetting"),
                               "A5023888391"),
        openalex.work_to_paper(work(12, pmid="2", title="Also different"), "A5023888391"),
        openalex.work_to_paper(work(13, title="Shared by title and year!", year=2020),
                               "A5023888391"),
        openalex.work_to_paper(work(14, doi="10.1/eee", title="OpenAlex only"),
                               "A5023888391"),
    ],
)
papers = merged["papers"]
by_title = {p["title"]: p for p in papers}

check("nothing is lost and one record is added", len(papers), 5)
check("the DOI match folded in", merged["matched_on"]["doi"], 1)
check("...case and resolver prefix and all", by_title["Shared by DOI"]["confirmed_by"],
      ["openalex", "pubmed"])
check("the PMID match folded in", merged["matched_on"]["pmid"], 1)
check("the title + year match folded in", merged["matched_on"]["title_year"], 1)
check("...through the punctuation difference",
      by_title["Shared by title and year"]["confirmed_by"], ["openalex", "pubmed"])
check("the unmatched OpenAlex record was appended, not dropped",
      by_title["OpenAlex only"]["source"], "openalex")
check("a PubMed record nobody confirmed says only PubMed",
      by_title["PubMed only"]["confirmed_by"], ["pubmed"])

print("\n--- the PubMed copy wins, because it is the one that carries identity fields ---")

check("the merged record keeps PubMed's title", by_title["Shared by DOI"]["source"], "pubmed")
check("...and PubMed's role marker, which records what verified it",
      by_title["Shared by DOI"]["role"], "第一作者 [ORCIDOK]")
check("a PubMed record with no DOI takes the OpenAlex one, because every join keys on it",
      by_title["Shared by PMID"]["doi"], "")
check("...and picks up the work id for provenance",
      by_title["Shared by PMID"]["openalex_work_id"], "W12")

with_doi = openalex.merge_corpora(
    [pubmed_paper("7", title="No DOI here")],
    [openalex.work_to_paper(work(20, pmid="7", doi="10.9/zzz", title="No DOI here"), "A1")],
)
check("a DOI-less PubMed record is backfilled from its OpenAlex twin",
      with_doi["papers"][0]["doi"], "10.9/zzz")

print("\n--- the counts, and the order ---")

check("the two denominators are both reported",
      (merged["counts"]["pubmed_total"], merged["counts"]["merged_total"]), (4, 5))
check("...split into the three populations",
      (merged["counts"]["pubmed_only"], merged["counts"]["openalex_only"],
       merged["counts"]["both"]), (1, 1, 3))
check("the three populations sum to the merged corpus",
      merged["counts"]["pubmed_only"] + merged["counts"]["openalex_only"]
      + merged["counts"]["both"], merged["counts"]["merged_total"])
check("the PubMed corpus keeps its order, so a diff shows additions and no movement",
      [p["title"] for p in papers[:4]],
      ["Shared by DOI", "Shared by PMID", "Shared by title and year", "PubMed only"])

check("merging nothing into a corpus leaves it alone",
      len(openalex.merge_corpora([pubmed_paper("1")], [])["papers"]), 1)
check("...and still labels its source",
      openalex.merge_corpora([pubmed_paper("1")], [])["papers"][0]["confirmed_by"], ["pubmed"])
check("merging into an empty corpus is just the OpenAlex records",
      openalex.merge_corpora([], [openalex.work_to_paper(work(1), "A1")])["counts"],
      {"pubmed_total": 0, "openalex_total": 1, "merged_total": 1,
       "pubmed_only": 0, "openalex_only": 1, "both": 0})

# Two papers can genuinely share a title across years — an annual report, a
# conference series — so the year is part of the key rather than a separate check.
same_title = openalex.merge_corpora(
    [pubmed_paper("1", title="Annual report", year="2021")],
    [openalex.work_to_paper(work(30, title="Annual report", year=2022), "A1")],
)
check("the same title in a different year is a different paper",
      len(same_title["papers"]), 2)

# A record with neither identifier nor title cannot key on anything, and must
# not collapse onto every other such record.
keyless = openalex.merge_corpora(
    [{"pmid": "", "doi": "", "title": "", "pub_year": ""},
     {"pmid": "", "doi": "", "title": "", "pub_year": ""}],
    [],
)
check("two unkeyable records stay two records", len(keyless["papers"]), 2)


# ============================================================
# 7. The profile layer's own derivation must agree
# ============================================================

print("\n--- the second, independent evidence derivation ---")

check("openalex outranks an email domain but not an ORCID",
      [tier for tier, _ in sorted(roles.EVIDENCE_RANK.items(), key=lambda kv: -kv[1])],
      ["orcid", "openalex", "email", "affiliation", "name_only"])

IDENTITY = {"author_name": "Zhu Guangwei", "orcid": "", "email_domains": [],
            "affiliation_keywords": [], "openalex_author_id": "A5023888391"}

check("a byline entry carrying the resolved id is openalex-tier",
      roles.evidence_tier(paper["authors"][0], IDENTITY), "openalex")
check("...and one carrying a different id is not",
      roles.evidence_tier(paper["authors"][1], IDENTITY), "name_only")
check("the id spellings are normalised on both sides",
      roles.evidence_tier({"openalex_author_id": "https://openalex.org/a5023888391"},
                          {"openalex_author_id": "A5023888391"}), "openalex")
check("no configured id means no tier, however many the bylines carry",
      roles.evidence_tier(paper["authors"][0], {"openalex_author_id": ""}), "name_only")
check("an ORCID match still beats it",
      roles.evidence_tier({"orcid": "0000-0002-1825-0097",
                           "openalex_author_id": "A5023888391"},
                          {**IDENTITY, "orcid": "0000-0002-1825-0097"}), "orcid")

# resolve_pi consumes the same ranking, so a corpus keyed on an OpenAlex id is
# `verified` rather than `name_only` — which is what keeps the evidence
# histogram and the per-paper pi_evidence from describing one corpus two ways.
resolved_pi = roles.resolve_pi(paper, "Zhu Guangwei", IDENTITY)
check("resolve_pi calls such a paper verified", resolved_pi["disposition"], "verified")
check("...names the tier that carried it", resolved_pi["pi_evidence"], "openalex")
check("...and locates the PI", resolved_pi["pi_index"], 0)


# ============================================================
# 8. The harvest wiring, end to end
# ============================================================

print("\n--- what cmd_fetch actually does with all of it ---")
# `_resolve_openalex_identity` and `_openalex_corpus` build their own client, so
# the seam here is the class itself. Everything below the constructor is the
# real code path cmd_fetch runs — the flag reading, the write-back into the
# identity block, the conversion, the merge and the provenance record.

from check_your_advisor import cli, http_client  # noqa: E402

_real_client = http_client.RobustHTTPClient
_scripted: list[StubClient] = []


def _install(stub: StubClient) -> None:
    _scripted.append(stub)
    http_client.RobustHTTPClient = lambda *args, **kwargs: stub  # noqa: E731


try:
    _install(StubClient({"authors": ok({"results": [
        api_author("5023888391", "Guangwei Zhu", orcid="0000-0002-1825-0097", works=43)]})}))
    _identity: dict = {}
    _cfg = {"author_name": "Zhu Guangwei", "affiliation": "Example University",
            "email": "me@example.edu", "openalex": {"resolve_author": True}}
    _record = cli._resolve_openalex_identity(_cfg, _identity, logging.getLogger("t"))

    check("a unique resolution is written back into the identity block",
          _identity["openalex_author_id"], "A5023888391")
    check("...and recorded for the corpus", _record["resolution"], "unique")
    # The one thing that must NOT happen: OpenAlex's ORCID becoming the user's
    # own assertion. `identity["orcid"]` is where a user says "this is me".
    check("OpenAlex's ORCID is not copied in as the user's assertion",
          "orcid" in _identity, False)

    _install(StubClient({"authors": ok({"results": [
        api_author("111", "Wei Zhang"), api_author("222", "Wei Zhang")]})}))
    _identity2: dict = {}
    _amb = cli._resolve_openalex_identity(
        {"author_name": "Zhang Wei", "openalex": {"resolve_author": True}},
        _identity2, logging.getLogger("t"))
    check("an ambiguous resolution writes no id back", _identity2, {})
    check("...and says so in the record", _amb["resolution"], "ambiguous")
    check("...carrying both candidates into the corpus", len(_amb["candidates"]), 2)

    _install(StubClient({"works": ok({
        "meta": {"count": 2, "next_cursor": None},
        "results": [work(1, doi="10.1/aaa", title="Shared"),
                    work(2, doi="10.1/bbb", title="OpenAlex only"),
                    # A work where the author sits in the middle: dropped, for
                    # the same reason the PubMed filter drops one.
                    {"id": "https://openalex.org/W3", "title": "Middle only",
                     "publication_year": 2023, "authorships": [
                         {"author_position": "first",
                          "author": {"id": "https://openalex.org/A9", "display_name": "Someone Else"}},
                         {"author_position": "middle",
                          "author": {"id": "https://openalex.org/A5023888391",
                                     "display_name": "Guangwei Zhu"}},
                         {"author_position": "last",
                          "author": {"id": "https://openalex.org/A8", "display_name": "Last Person"}},
                     ]}],
    })}))
    _corpus = [pubmed_paper("1", doi="10.1/AAA", title="Shared")]
    _out, _prov = cli._openalex_corpus(
        {"years_back": 5, "email": "", "openalex": {"merge_works": True, "max_works": 500}},
        {"openalex_author_id": "A5023888391"}, _corpus, logging.getLogger("t"),
    )

    check("the merge ran", _prov["merged"], True)
    check("...adding the record PubMed did not have", len(_out), 2)
    check("...confirming the one it did", _out[0]["confirmed_by"], ["openalex", "pubmed"])
    check("...dropping the work where the author holds no slot",
          _prov["works_without_lead_slot"], 1)
    check("...and recording both denominators",
          (_prov["counts"]["pubmed_total"], _prov["counts"]["merged_total"]), (1, 2))
    check("the resolved id is recorded beside the counts",
          _prov["openalex_author_id"], "A5023888391")
    check("the date window reached the works filter",
          "from_publication_date" in _scripted[-1].calls[0], True)
finally:
    http_client.RobustHTTPClient = _real_client


# ============================================================
# 9. The spreadsheet a human opens says it too
# ============================================================

print("\n--- the source column in the CSV export ---")
# The papers JSON is the authoritative record, but a spreadsheet cannot show
# whether it is a single-source or a merged corpus unless the column is there,
# and the two have different denominators.

import csv as _csv  # noqa: E402
import tempfile  # noqa: E402

from check_your_advisor import export  # noqa: E402

check("the header is present once", export.HEADERS.count("来源"), 1)
check("every header has a column and a width",
      len(export.HEADERS), len(export._paper_row(1, {})))

with tempfile.TemporaryDirectory() as _tmp:
    _path = os.path.join(_tmp, "papers.csv")
    export.save_to_csv([
        {"pmid": "1", "title": "both", "confirmed_by": ["openalex", "pubmed"]},
        {"pmid": "2", "title": "openalex only", "source": "openalex",
         "confirmed_by": ["openalex"]},
        {"pmid": "3", "title": "legacy corpus, no field at all"},
    ], _path)
    with open(_path, encoding="utf-8-sig", newline="") as _f:
        _rows = list(_csv.DictReader(_f))
    check("a record both sources hold names both", _rows[0]["来源"], "openalex+pubmed")
    check("a record only OpenAlex holds names it", _rows[1]["来源"], "openalex")
    check("a corpus harvested before the merge existed leaves it blank, not 'pubmed'",
          _rows[2]["来源"], "")
    check("the abstract is still the last column and did not shift into it",
          list(_rows[0])[-1], "摘要")


print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(1 if _failed else 0)
