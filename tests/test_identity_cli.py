#!/usr/bin/env python3
"""
Regression tests for supplying author-identity evidence from the command line.

These exist because of a silent failure found on a real run. `--affiliation`
reached only the PubMed search string, never `author_identity`, so a harvest
that named the institution on the command line still verified nobody against
it: 186 papers came back, every one of them marked "机构未验证", spanning blood
pressure monitoring, cholera, nanowire arrays and neural networks — several
different people who happen to share a name. Nothing said so until the profile
report refused hours later, by which time the corpus had to be collected again.

There was also no way at all to supply the two stronger identifiers, ORCID and
the corresponding-author email domain, without writing a config file first.

Run: python tests/test_identity_cli.py
"""

from __future__ import annotations

import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor.cli import (  # noqa: E402
    _has_identity_evidence,
    apply_cli_overrides,
    parse_fetch_args,
)
from check_your_advisor.config import DEFAULT_CONFIG  # noqa: E402

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


def identity(argv: list[str], base: dict | None = None) -> dict:
    cfg = base if base is not None else copy.deepcopy(DEFAULT_CONFIG)
    return apply_cli_overrides(cfg, parse_fetch_args(argv))["author_identity"]


print("--affiliation now reaches identity verification")
i = identity(["--author", "X", "--affiliation", "Fujian Medical University"])
check("the search institution seeds the verification keyword",
      i["affiliation_keywords"], ["Fujian Medical University"])
check("...so the run is no longer evidence-free", _has_identity_evidence(i), True)

check("no affiliation, no keyword",
      identity(["--author", "X"])["affiliation_keywords"], [])


print("\nExplicit keywords win over the search institution")
i = identity(["--author", "X", "--affiliation", "A University",
              "--affiliation-keyword", "B Hospital",
              "--affiliation-keyword", "C Center"])
check("both repeats are kept, in order", i["affiliation_keywords"], ["B Hospital", "C Center"])

base = copy.deepcopy(DEFAULT_CONFIG)
base["author_identity"]["affiliation_keywords"] = ["From Config"]
check("a config-file list is never silently replaced",
      identity(["--author", "X", "--affiliation", "CLI Value"], base)["affiliation_keywords"],
      ["From Config"])


print("\nThe stronger identifiers are reachable without a config file")
i = identity(["--author", "X", "--orcid", "0000-0002-6118-8583",
              "--email-domain", "fjmu.edu.cn", "--email-domain", "126.com"])
check("ORCID is carried", i["orcid"], "0000-0002-6118-8583")
check("email domains repeat", i["email_domains"], ["fjmu.edu.cn", "126.com"])
check("either one alone counts as evidence",
      _has_identity_evidence(identity(["--author", "X", "--orcid", "0000-1"])), True)
check("...as does an email domain alone",
      _has_identity_evidence(identity(["--author", "X", "--email-domain", "a.edu"])), True)


print("\nWhat does not count as evidence")
check("a name alone does not", _has_identity_evidence({"orcid": "", "email_domains": [],
                                                       "affiliation_keywords": []}), False)
check("an empty ORCID string does not",
      _has_identity_evidence({"orcid": "   ", "email_domains": [], "affiliation_keywords": []}), False)
check("require_affiliation alone does not — it only says how strictly to apply "
      "keywords that may not exist",
      _has_identity_evidence({"require_affiliation": True, "orcid": "",
                              "email_domains": [], "affiliation_keywords": []}), False)


print("\nrequire_affiliation")
check("the flag sets it",
      identity(["--author", "X", "--affiliation", "A", "--require-affiliation"])["require_affiliation"],
      True)
check("omitting the flag leaves the configured value alone, rather than forcing False",
      identity(["--author", "X"])["require_affiliation"],
      DEFAULT_CONFIG["author_identity"]["require_affiliation"])

base = copy.deepcopy(DEFAULT_CONFIG)
base["author_identity"]["require_affiliation"] = True
check("...including one turned on in the config file",
      identity(["--author", "X"], base)["require_affiliation"], True)


print("\nThe OpenAlex switches, and where each of them lands")
# `--openalex-author-id` is identity — it says who this is. The other two are
# fetch instructions and live in their own config block, so a config file that
# turns the lookup on does not thereby claim an identity.
from check_your_advisor.cli import apply_cli_overrides as _apply  # noqa: E402


def cfg_of(argv: list[str], base: dict | None = None) -> dict:
    return _apply(base if base is not None else copy.deepcopy(DEFAULT_CONFIG),
                  parse_fetch_args(argv))


check("--openalex-author-id lands in the identity block",
      identity(["--author", "X", "--openalex-author-id", "A5023888391"])["openalex_author_id"],
      "A5023888391")
# The id alone is not evidence about a PubMed harvest: `parse_article` never
# writes `openalex_author_id` onto a byline entry, so every record would come
# back `name_only` no matter what id was configured. It counts once
# `--openalex-works` is on, because then OpenAlex contributes records that do
# carry it.
check("...but on its own it is not evidence — no PubMed record can carry it",
      _has_identity_evidence(
          identity(["--author", "X", "--openalex-author-id", "A5023888391"])), False)
check("...and counts once --openalex-works will merge records that do carry it",
      _has_identity_evidence(
          identity(["--author", "X", "--openalex-author-id", "A5023888391"]),
          merge_works=True), True)
check("...while --openalex-works with no id stays evidence-free",
      _has_identity_evidence(identity(["--author", "X"]), merge_works=True), False)
check("no flag leaves it empty",
      identity(["--author", "X"])["openalex_author_id"], "")
check("...and an empty one is not evidence",
      _has_identity_evidence(identity(["--author", "X"])), False)
check("--resolve-openalex lands in the openalex block, not in identity",
      cfg_of(["--author", "X", "--resolve-openalex"])["openalex"]["resolve_author"], True)
check("...and asserts no identity by itself",
      _has_identity_evidence(
          cfg_of(["--author", "X", "--resolve-openalex"])["author_identity"]), False)
check("--openalex-works lands beside it",
      cfg_of(["--author", "X", "--openalex-works"])["openalex"]["merge_works"], True)
check("--max-works overrides the budget",
      cfg_of(["--author", "X", "--max-works", "50"])["openalex"]["max_works"], 50)
check("both switches default off, because an extra API call is a choice",
      (cfg_of(["--author", "X"])["openalex"]["resolve_author"],
       cfg_of(["--author", "X"])["openalex"]["merge_works"]), (False, False))

# A value set in the config file survives a run that does not repeat the flag,
# on the same rule every other identity field follows.
_base = copy.deepcopy(DEFAULT_CONFIG)
_base["openalex"]["merge_works"] = True
_base["author_identity"]["openalex_author_id"] = "A777"
check("a configured author id is not wiped by omitting the flag",
      identity(["--author", "X"], _base)["openalex_author_id"], "A777")
_base2 = copy.deepcopy(DEFAULT_CONFIG)
_base2["openalex"]["merge_works"] = True
check("...nor is a configured merge switch",
      cfg_of(["--author", "X"], _base2)["openalex"]["merge_works"], True)


print("\nAn explicit id short-circuits the lookup; an unresolved one refuses to guess")
import logging  # noqa: E402

logging.getLogger("check_your_advisor").setLevel(logging.CRITICAL)
from check_your_advisor.cli import (  # noqa: E402
    _openalex_corpus,
    _resolve_openalex_identity,
)

_explicit_identity = {"openalex_author_id": "https://openalex.org/A5023888391"}
_record = _resolve_openalex_identity(
    {"openalex": {"resolve_author": True}, "author_name": "X"}, _explicit_identity, logging.getLogger("t")
)
check("an explicit id is taken as given", _record["resolution"], "explicit")
check("...normalised out of its URL form", _record["openalex_author_id"], "A5023888391")
check("...and attributed to the user, not to OpenAlex", _record["source"], "user")
check("...with no candidate list, because no question was asked",
      _record["candidates"], [])

check("no id and no --resolve-openalex means no OpenAlex block at all",
      _resolve_openalex_identity({"openalex": {}, "author_name": "X"}, {},
                                 logging.getLogger("t")), {})

# The merge cannot run without a settled id, and it says so rather than
# guessing at one — this is the ambiguous-candidates path arriving downstream.
_papers = [{"pmid": "1", "title": "t"}]
_merged, _prov = _openalex_corpus(
    {"openalex": {"merge_works": True}}, {"openalex_author_id": ""}, _papers,
    logging.getLogger("t"),
)
check("an unresolved id leaves the corpus untouched", _merged, _papers)
check("...and records that the merge was asked for and did not happen",
      (_prov["merge_requested"], _prov["merged"]), (True, False))
check("...with the reason", _prov["reason"], "no resolved openalex_author_id")
check("not asking for the merge records nothing",
      _openalex_corpus({"openalex": {}}, {"openalex_author_id": "A1"}, _papers,
                       logging.getLogger("t"))[1], {})


print("\nThe corpus carries the evidence it was built with")
# The identity gate has to be decided from what produced the corpus, not from
# whatever config is loaded when the report runs. Deciding it from the latter is
# wrong in both directions, and the dangerous direction is the second one.
from check_your_advisor.cli import _profile_corpus  # noqa: E402

RECORDED = {
    "orcid": "0000-0002-6118-8583",
    "affiliation_keywords": ["Fujian Medical University"],
    "email_domains": ["fjmu.edu.cn"],
    "require_affiliation": False,
}
PAPERS = [{"pmid": "1", "title": "t", "authors": [], "pub_date": "2024"}]

corpus = _profile_corpus(PAPERS, {"author_identity": {}}, {"identity": RECORDED})
check("a recorded ORCID survives a profile run that repeats no flags",
      corpus["identity"]["orcid"], "0000-0002-6118-8583")

# The false positive: a corpus harvested with nothing, profiled on a machine
# whose config happens to carry an ORCID, used to pass the gate and be reported
# on as if it described one person.
empty_run = {"orcid": "", "affiliation_keywords": [], "email_domains": [],
             "require_affiliation": False}
corpus = _profile_corpus(PAPERS, {"author_identity": RECORDED}, {"identity": empty_run})
check("a name-only corpus is not certified by a config it was never harvested with",
      _has_identity_evidence(corpus["identity"]), False)

# Legacy files record nothing at all. They fall back, because the config was the
# only way to configure this before flags existed — but the run warns.
corpus = _profile_corpus(PAPERS, {"author_identity": RECORDED}, {"esearch_term": "x"})
check("a file predating the record still falls back to the config",
      corpus["identity"]["orcid"], "0000-0002-6118-8583")
# An empty recorded block is a record — "this harvest used nothing" — not an
# absence. It must not be treated as a missing key and refilled from the config.
# The returned block is normalised rather than verbatim, so check the evidence.
empty_recorded = _profile_corpus(PAPERS, {"author_identity": RECORDED},
                                 {"identity": {}})["identity"]
check("an empty recorded block is a record, not an absence",
      _has_identity_evidence(empty_recorded), False)

# The OpenAlex id follows the same rule as the other three, and is normalised on
# the way in so the URL spelling recorded at harvest time still matches the bare
# spelling `roles.evidence_tier` compares against.
oa_corpus = _profile_corpus(
    PAPERS, {"author_identity": {}},
    {"identity": {**empty_run, "openalex_author_id": "https://openalex.org/A5023888391"}},
)
check("a recorded OpenAlex id is normalised into the corpus",
      oa_corpus["identity"]["openalex_author_id"], "A5023888391")
check("...and is still not evidence on its own, only under --openalex-works",
      _has_identity_evidence(oa_corpus["identity"]), False)
check("...which is what makes it count",
      _has_identity_evidence(oa_corpus["identity"], merge_works=True), True)

# The two OpenAlex provenance blocks travel with the corpus so the report can
# print who asserted the identity and when, rather than just the id.
prov_corpus = _profile_corpus(
    PAPERS, {"author_identity": {}},
    {"identity": empty_run, "esearch_term": "x",
     "openalex": {"resolution": "ambiguous", "candidates": [{"openalex_author_id": "A1"}]},
     "openalex_works": {"merged": True, "works_returned": 12}},
)
check("the resolution block reaches the report's query block",
      prov_corpus["query"]["openalex"]["resolution"], "ambiguous")
check("...and so does the works block",
      prov_corpus["query"]["openalex_works"]["works_returned"], 12)
check("a PubMed-only harvest carries neither",
      [k for k in ("openalex", "openalex_works")
       if k in _profile_corpus(PAPERS, {}, {"esearch_term": "x"})["query"]], [])


print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(1 if _failed else 0)
