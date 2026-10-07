#!/usr/bin/env python3
"""
Where `harvest` takes papers from, and how it helps tell namesakes apart.

Two problems, one file, because both live in `cmd_fetch` and both were found by
asking who the tool fails:

- **An advisor outside biomedicine had no corpus at all.** PubMed indexes the
  life sciences, so an engineering, computer-science or physics professor came
  back with zero PMIDs, and `harvest` returned on the spot — before the OpenAlex
  merge it had been asked for — with "check the spelling". `--source openalex`
  now skips PubMed entirely, `--source both` survives an empty PubMed side, and
  the empty-PubMed message names the flag that would have worked.
- **A common name without an ORCID could not be pinned down.** The evidence a
  student can actually find is the advisor's email on the lab page, and it only
  counted as a domain — shared by everyone at the university. `--author-email`
  matches the whole address, domains match at a label boundary, and the harvest
  lists the addresses and departments the bylines printed beside the name so the
  reader can pick theirs.

Fully offline: PubMed's two calls are scripted stubs and the OpenAlex client is a
canned-reply object.

Run: python tests/test_harvest_sources.py
"""

from __future__ import annotations

import glob
import json
import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor import cli, http_client, openalex, pubmed_api  # noqa: E402
from check_your_advisor.http_client import Response  # noqa: E402
from check_your_advisor.profile import report  # noqa: E402

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


AUTHOR = "Wang Wei"
OPENALEX_ID = "A5023888391"


def work(index: int) -> dict:
    """One OpenAlex work where the resolved author holds the first slot."""
    return {
        "id": f"https://openalex.org/W{7100 + index}",
        "title": f"A computer vision paper {index}",
        "publication_year": 2024,
        "publication_date": "2024-04-01",
        "doi": f"https://doi.org/10.5555/cv{index}",
        "ids": {},
        "authorships": [
            {"author_position": "first", "is_corresponding": True,
             "author": {"id": f"https://openalex.org/{OPENALEX_ID}", "display_name": "Wei Wang"},
             "institutions": []},
            {"author_position": "last",
             "author": {"id": "https://openalex.org/A9", "display_name": "Someone Last"},
             "institutions": []},
        ],
    }


def candidate(author_id: str, institution: str, topic: str, field: str) -> dict:
    return {
        "id": f"https://openalex.org/{author_id}",
        "display_name": "Wei Wang",
        "works_count": 120, "cited_by_count": 900,
        "last_known_institutions": [{"display_name": institution}],
        "topics": [{"display_name": topic, "count": 40, "field": {"display_name": field}}],
    }


class Client:
    """Answers OpenAlex's authors and works endpoints from canned data, and records each URL."""

    def __init__(self, works: list[dict], authors: list[dict] | None = None) -> None:
        self.works = works
        self.authors = authors or []
        self.calls: list[str] = []

    def get(self, url: str, **kwargs):
        self.calls.append(url)
        if "/institutions?" in url:
            results = [{"id": "https://openalex.org/I20231570", "display_name": "Peking University"}]
        else:
            results = self.authors if "/authors" in url else self.works
        payload = {"meta": {"count": len(results), "next_cursor": None}, "results": results}
        return Response(200, {"Content-Type": "application/json"}, json.dumps(payload).encode("utf-8"))

    def close(self) -> None:
        pass


def byline(name_last: str, name_fore: str, affiliation: str, email: str = "",
           corresponding: bool = False) -> dict:
    return {"name": f"{name_last} {name_fore}", "last": name_last, "fore": name_fore,
            "initials": name_fore[:1], "affiliation": affiliation, "email": email,
            "orcid": "", "is_corresponding": corresponding, "equal_contrib": False}


def pubmed_record(index: int, authors: list[dict]) -> dict:
    return {
        "pmid": str(8000 + index), "title": f"A PubMed paper {index}",
        "authors_str": ", ".join(a["name"] for a in authors), "authors": authors,
        "journal": "Journal of Things", "issn": "1234-5678", "issn_type": "",
        "pub_date": "2023 Mar", "pub_year": "2023", "volume": "", "issue": "",
        "pages": "", "doi": "", "pmc_id": "", "abstract": "",
    }


def close_log_handlers() -> None:
    for handler in list(logging.getLogger("check_your_advisor").handlers):
        handler.close()
    logging.getLogger("check_your_advisor").handlers.clear()


def harvest(argv: list[str], pmids: list[str], records: list[dict], client: Client):
    """Run `cmd_fetch` with PubMed and OpenAlex replaced. Returns (code, search calls, corpus, log)."""
    calls: list[str] = []

    def stub_search(name, years_back, api_key, retmax=500, identity=None,
                    provenance=None, max_records=None, **kwargs):
        calls.append(name)
        if provenance is not None:
            provenance.update({"esearch_term": f'"{name}"[Author]', "esearch_matched": len(pmids),
                               "pmids_returned": len(pmids), "retmax": retmax,
                               "max_records": max_records, "pages_fetched": 1,
                               "duplicates_dropped": 0, "mindate": "2021/01/01",
                               "maxdate": "2026/12/31", "years_back": years_back})
        return list(pmids)

    real = (pubmed_api.search_pubmed, pubmed_api.fetch_details, http_client.RobustHTTPClient)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            pubmed_api.search_pubmed = stub_search
            pubmed_api.fetch_details = lambda ids, api_key, delay: [dict(r) for r in records]
            http_client.RobustHTTPClient = lambda *a, **k: client
            code = cli.cmd_fetch(["--author", AUTHOR, "--output-dir", tmp, "--no-download",
                                  "--years-back", "5", *argv])
        finally:
            (pubmed_api.search_pubmed, pubmed_api.fetch_details,
             http_client.RobustHTTPClient) = real
            close_log_handlers()
        written = sorted(glob.glob(os.path.join(tmp, "papers_*.json")))
        corpus = json.load(open(written[0], encoding="utf-8")) if len(written) == 1 else None
        log = "".join(open(path, encoding="utf-8").read()
                      for path in glob.glob(os.path.join(tmp, "download_*.log")))
    return code, calls, corpus, log


def section_one(corpus_file: dict) -> str:
    corpus = cli._profile_corpus(corpus_file["papers"], {"author_name": AUTHOR}, corpus_file["search"])
    markdown = report.render_markdown(report.build_report(corpus, {}, None, None))
    start = markdown.find("## 1.")
    return markdown[start:markdown.find("\n## ", start + 4)]


# The console handler `setup_logging` installs is real; keep the run readable.
logging.getLogger("check_your_advisor").setLevel(logging.ERROR)


# ============================================================
# 1. --source openalex: PubMed is never asked
# ============================================================

print("\n--- --source openalex ---")

client = Client([work(i) for i in range(3)])
code, calls, corpus, log = harvest(["--source", "openalex", "--openalex-author-id", OPENALEX_ID],
                                   pmids=["1", "2"], records=[], client=client)
check("PubMed is not searched", calls, [])
check_true("the log's banner names the source it will use", "来源: OpenAlex" in log)
check("...and says nothing about a PubMed key, a PubMed filter or a PubMed merge it never runs",
      [phrase for phrase in ("PubMed API key", "身份验证结果", "语料合并：PubMed") if phrase in log], [])
check_true("a corpus is written", corpus)
check("...holding the three OpenAlex works", len((corpus or {}).get("papers", [])), 3)
check("...and saying PubMed was not searched", (corpus or {}).get("search", {}).get("pubmed_searched"), False)
check("...and which source it came from", (corpus or {}).get("search", {}).get("sources"), ["openalex"])
text = section_one(corpus) if corpus else ""
check_true("Section 1 says PubMed was not searched", "PubMed: not searched" in text)
check("...and prints no esearch line over a search that did not happen", "esearch term" in text, False)
check("...nor PubMed coverage or efetch counts",
      ("PubMed corpus coverage" in text, "PubMed records: fetched" in text), (False, False))
check("...nor a 'PubMed corpus of 0 records' beside the OpenAlex one, which reads as a search that found nothing",
      ("the two denominators" in text, "PubMed was not searched, so there is one denominator" in text),
      (False, True))
check_true("...and an id given on the command line is said to have needed no lookup",
           "explicit — the id was given with `--openalex-author-id`, so no lookup was made" in text)

ambiguous = Client([], authors=[candidate("A1", "Peking University", "Computer Vision", "Computer Science"),
                                candidate("A2", "Peking University", "Cardiology", "Medicine")])
code, calls, corpus, log = harvest(["--source", "openalex", "--affiliation", "Peking University"],
                                   pmids=[], records=[], client=ambiguous)
check("with two candidates and no id it stops with exit code 1", code, 1)
check("...without promising a PubMed search that will not happen", "PubMed 单源" in log, False)
check("...writes no corpus", corpus, None)
check("...and never falls back to PubMed", calls, [])
check_true("...after asking OpenAlex who publishes under the name",
           any("/authors" in url for url in ambiguous.calls))
check_true("...and listing each candidate with what they work on",
           "Computer Vision (Computer Science)" in log and "Cardiology (Medicine)" in log)



class DownClient(Client):
    """OpenAlex unreachable: every request comes back as no response at all."""

    def get(self, url: str, **kwargs):
        self.calls.append(url)
        return None


code, calls, corpus, log = harvest(["--source", "openalex", "--affiliation", "Peking University"],
                                   pmids=[], records=[], client=DownClient([]))
check("an unreachable OpenAlex stops the run with exit code 1", (code, corpus, calls), (1, None, []))
check("...and the log does not say a PubMed search goes ahead", "PubMed 检索照常进行" in log, False)

overrides = cli.apply_cli_overrides({}, cli.parse_fetch_args(["--author", AUTHOR, "--source", "openalex"]))
check("--source openalex switches on the author lookup and the works merge",
      (overrides["openalex"].get("resolve_author"), overrides["openalex"].get("merge_works")), (True, True))
check("...and pubmed stays the default", "source" in cli.apply_cli_overrides(
    {}, cli.parse_fetch_args(["--author", AUTHOR])), False)


# ============================================================
# 2. An empty PubMed side
# ============================================================

print("\n--- PubMed returns nothing ---")

code, calls, corpus, log = harvest([], pmids=[], records=[], client=Client([]))
check("the default source still stops when PubMed has nothing", corpus, None)
check_true("...under a banner naming PubMed, with the anonymous-rate warning a PubMed run needs",
           "来源: PubMed" in log and "PubMed API key" in log)
check_true("...but now names the flag that covers every field",
           "--source openalex" in log)

code, calls, corpus, log = harvest(["--source", "both", "--openalex-author-id", OPENALEX_ID],
                                   pmids=[], records=[], client=Client([work(i) for i in range(2)]))
check("--source both survives an empty PubMed side", len((corpus or {}).get("papers", [])), 2)
check("...searched PubMed first", calls, [AUTHOR])
check_true("...under a banner naming both sources", "来源: PubMed + OpenAlex" in log)
check("...and records both sources as searched, PubMed with no hits",
      ((corpus or {}).get("search", {}).get("sources"), (corpus or {}).get("search", {}).get("esearch_matched")),
      (["pubmed", "openalex"], 0))


# ============================================================
# 3. The advisor's own address, and domains at a boundary
# ============================================================

print("\n--- --author-email and email matching ---")

matches = pubmed_api._email_domain_matches
for email, allowed, expected, why in (
    ("a@pku.edu.cn", ["pku.edu.cn"], True, "a domain admits its own addresses"),
    ("a@stu.pku.edu.cn", ["pku.edu.cn"], True, "...and its subdomains"),
    ("a@xpku.edu.cn", ["pku.edu.cn"], False, "...but not a longer domain that ends the same way"),
    ("a@pku.edu.cn", ["@pku.edu.cn"], True, "a leading @ on a domain is tolerated"),
    ("wangwei@pku.edu.cn", ["wangwei@pku.edu.cn"], True, "a whole address matches itself"),
    ("WangWei@PKU.edu.cn", ["wangwei@pku.edu.cn"], True, "...without regard to case"),
    ("xwangwei@pku.edu.cn", ["wangwei@pku.edu.cn"], False, "...and nothing that merely ends with it"),
    ("", ["pku.edu.cn"], False, "a byline with no email matches nothing"),
):
    check(f"{why}: {email or '(none)'} against {allowed}", matches(email, allowed), expected)

cfg = cli.apply_cli_overrides({}, cli.parse_fetch_args(
    ["--author", AUTHOR, "--author-email", "WangWei@pku.edu.cn", "--author-email", "wangwei@PKU.edu.cn",
     "--email-domain", "pku.edu.cn"]))
check("--author-email lands in the identity block once, beside the domains",
      sorted(cfg["author_identity"]["email_domains"], key=str.lower), ["pku.edu.cn", "WangWei@pku.edu.cn"])
check_true("...and counts as evidence a namesake cannot share", cli._has_unique_evidence(cfg["author_identity"]))
check("a domain alone does not", cli._has_unique_evidence({"email_domains": ["pku.edu.cn"]}), False)

kept, role = pubmed_api.is_first_or_corresponding(
    pubmed_record(1, [byline("Wang", "Wei", "Peking University", "wangwei@pku.edu.cn", True)]),
    AUTHOR, identity={"email_domains": ["wangwei@pku.edu.cn"], "require_affiliation": True})
check("the identity filter keeps the record carrying the advisor's address", (kept, "EmailOK" in role), (True, True))
kept, _role = pubmed_api.is_first_or_corresponding(
    pubmed_record(2, [byline("Wang", "Wei", "Peking University", "wwang@pku.edu.cn", True)]),
    AUTHOR, identity={"email_domains": ["wangwei@pku.edu.cn"], "require_affiliation": True})
check("...and refuses a namesake at the same university", kept, False)


# ============================================================
# 4. What the bylines print beside the name
# ============================================================

print("\n--- identity hints ---")

records = [
    pubmed_record(i, [byline("Wang", "Wei", "Department of Cardiology, Peking University First Hospital, "
                             "Beijing, China", "wangwei@pkufh.cn", True), byline("Li", "Na", "X")])
    for i in range(3)
] + [
    pubmed_record(10 + i, [byline("Zhao", "Lei", "Y"),
                           byline("Wang", "Wei", "School of Computer Science, Peking University, "
                                  "Beijing, China", "wwei@pku.edu.cn")])
    for i in range(2)
] + [
    # The name in a middle slot: not a slot the identity filter reads, so not counted.
    pubmed_record(20, [byline("Zhao", "Lei", "Y"), byline("Wang", "Wei", "Elsewhere", "mid@x.cn"),
                       byline("Sun", "Yu", "Z")]),
]
hints = pubmed_api.identity_hints(records, AUTHOR)
check("hints count every record where the name holds a lead slot", hints["records_examined"], 5)
check("...listing the addresses most frequent first",
      hints["emails"], [["wangwei@pkufh.cn", 3], ["wwei@pku.edu.cn", 2]])
check("...and the departments with their institutions",
      hints["affiliations"], [["Department of Cardiology, Peking University First Hospital", 3],
                              ["School of Computer Science, Peking University", 2]])
check("...and never an address from a middle slot", any("mid@x.cn" in e for e, _n in hints["emails"]), False)

code, calls, corpus, log = harvest([], pmids=[str(i) for i in range(len(records))], records=records,
                                   client=Client([]))
check_true("the harvest stores the hints in the corpus file", (corpus or {}).get("search", {}).get("identity_hints"))
check_true("...and logs them when no ORCID or address was given",
           "wangwei@pkufh.cn" in log and "--author-email" in log)
text = section_one(corpus) if corpus else ""
check_true("Section 1 prints them for a reader with no ORCID",
           "wangwei@pkufh.cn (3)" in text and "--author-email" in text)

code, calls, corpus, log = harvest(["--author-email", "wangwei@pkufh.cn"], pmids=[str(i) for i in range(5)],
                                   records=records[:5], client=Client([]))
check("with the advisor's address given the log does not list them", "wwei@pku.edu.cn" in log, False)
text = section_one(corpus) if corpus else ""
check("...and neither does Section 1", "printed beside" in text, False)
roles_kept = [paper.get("role", "") for paper in (corpus or {}).get("papers", [])]
check("...the advisor's three records carry the address as evidence",
      sum("[EmailOK wangwei@pkufh.cn]" in role for role in roles_kept), 3)
check("...while the namesake's two stay in, marked as name matches only (the default loose mode)",
      sum(pubmed_api.is_name_only_role(role) for role in roles_kept), 2)
code, calls, corpus, log = harvest(["--author-email", "wangwei@pkufh.cn", "--require-affiliation"],
                                   pmids=[str(i) for i in range(5)], records=records[:5], client=Client([]))
check("...and --require-affiliation keeps the namesake out", len((corpus or {}).get("papers", [])), 3)

# The run the hints lead to keeps the institution it searched with. Every record
# below names Peking University, the advisor's three in cardiology and the
# namesake's two in computer science, so only a department can tell them apart:
# the filter used to add --affiliation back in beside --affiliation-keyword, and
# the namesake passed on the university name.
INSTITUTION = ["--affiliation", "Peking University"]
DEPARTMENT = ["--affiliation-keyword", "Department of Cardiology, Peking University First Hospital"]
code, calls, corpus, log = harvest([*INSTITUTION, *DEPARTMENT, "--require-affiliation"],
                                   pmids=[str(i) for i in range(5)], records=records[:5], client=Client([]))
check("with the department as the keyword, a namesake in another department stays out",
      len((corpus or {}).get("papers", [])), 3)
code, calls, corpus, log = harvest([*INSTITUTION, "--author-email", "wangwei@pkufh.cn", "--require-affiliation"],
                                   pmids=[str(i) for i in range(5)], records=records[:5], client=Client([]))
check("...while the address alone lets them in on the university name, as the docs now say",
      len((corpus or {}).get("papers", [])), 5)


# ============================================================
# 5. PubMed unreachable
# ============================================================

print("\n--- PubMed unreachable ---")


def harvest_failing(search_error=None, fetch_error=None):
    """Run `cmd_fetch` with PubMed's search or detail fetch raising. Returns (code, files, log)."""
    def search(*args, **kwargs):
        if search_error is not None:
            raise search_error
        return ["1"]

    def fetch(*args, **kwargs):
        raise fetch_error

    real = (pubmed_api.search_pubmed, pubmed_api.fetch_details)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            pubmed_api.search_pubmed = search
            pubmed_api.fetch_details = fetch
            code = cli.cmd_fetch(["--author", AUTHOR, "--output-dir", tmp, "--no-download",
                                  "--years-back", "5"])
        finally:
            pubmed_api.search_pubmed, pubmed_api.fetch_details = real
            close_log_handlers()
        files = sorted(os.path.basename(path) for path in glob.glob(os.path.join(tmp, "*"))
                       if not os.path.basename(path).startswith("download_"))
        log = "".join(open(path, encoding="utf-8").read()
                      for path in glob.glob(os.path.join(tmp, "download_*.log")))
    return code, files, log


from urllib.error import HTTPError, URLError  # noqa: E402

for label, kwargs, reason in (
    ("a refused connection during the search", {"search_error": URLError("connection refused")},
     "connection refused"),
    ("an HTTP 503 during the search", {"search_error": HTTPError("u", 503, "Service Unavailable", None, None)},
     "HTTP 503"),
    ("a body that is not JSON", {"search_error": ValueError("Expecting value")}, "Expecting value"),
    ("a timeout while fetching details", {"fetch_error": TimeoutError("timed out")}, "timed out"),
):
    code, files, log = harvest_failing(**kwargs)
    check(f"{label} ends the run with exit code 1, not a traceback", code, 1)
    check(f"...writes nothing", files, [])
    check_true(f"...and says why, in a line naming the cause", "PubMed" in log and reason in log)


# ============================================================
# 6. OpenAlex candidates say what they work on
# ============================================================

print("\n--- candidate topics ---")

record = openalex.author_candidate(candidate("A1", "Peking University", "Computer Vision", "Computer Science"))
check("a candidate carries its top topics with their field", record["topics"], ["Computer Vision (Computer Science)"])
check("a candidate without topics carries none", openalex.author_candidate({"id": "https://openalex.org/A3"})["topics"], [])


print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(0 if _failed == 0 else 1)
