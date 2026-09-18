#!/usr/bin/env python3
"""
The keyless-API calls identify themselves; they do not impersonate a browser.

Why this file exists
--------------------
`http_client` carries a UA pool and a per-request fingerprint randomiser, and
its own docstring calls that 反爬 — 模拟真实浏览器行为. That machinery exists for
the PDF download race, where the other end is a publisher landing page.

Eight requests were going through it that should never have: OpenAlex authors,
OpenAlex works, DOAJ journals, Crossref journals, OpenAlex sources, and — found
a round later, in `citations.py` — OpenAlex citation counts, Semantic Scholar
and Europe PMC. Every one of them is a keyless public JSON API; OpenAlex and
Crossref both document a polite pool that asks the caller to say who it is, and
rotating a fake Chrome UA at them is the exact opposite of that. It also
contradicted this package's own prose — `journals.py` states that
`journal_risk.py`'s traffic is "a documented API call, not a scrape of a vendor
page" — while `Sec-Ch-Ua-Platform: "Windows"` was going out on the wire beside a
randomly chosen `Mozilla/5.0`.

The last three mattered for a second reason: `cite` reaches `api.openalex.org`,
the same host `harvest --openalex-works` reaches. One process was addressing one
API under two identities, whichever of the two is the honest one.

So the eight sites now pass `http_client.polite_headers()` through the
`extra_headers` hook, and this file pins the result at four levels:

  1. `polite_headers` returns a fixed project identifier, never a browser string
  2. `RobustHTTPClient.get` actually puts it on the wire, and drops the
     browser-only Sec-* / DNT headers when a caller names itself
  3. the PDF path is untouched — no `extra_headers`, so the pool still rotates
  4. all eight call sites pass it, asserted against the source so that a ninth
     endpoint added later cannot quietly go back to the pool

The `--email` address goes into the User-Agent only where the endpoint documents
a polite pool keyed on it (OpenAlex, Crossref). Semantic Scholar and Europe PMC
get the bare project name, and section 5 pins that the fallback chain does not
leak the address sideways into them.

No socket is opened. The client's jitter sleep is stubbed, a fake opener stands
in for urllib, and the three API modules are driven with recording stubs.

Run: python tests/test_honest_user_agent.py
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor import citations, http_client, journal_risk, openalex  # noqa: E402
from check_your_advisor.http_client import (  # noqa: E402
    PROJECT_USER_AGENT,
    Response,
    RobustHTTPClient,
    polite_headers,
)

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


def check_true(label: str, cond) -> None:
    check(label, bool(cond), True)


def check_false(label: str, cond) -> None:
    check(label, bool(cond), False)


# The two API modules log in Chinese at WARNING. That output is the product, not
# the test.
logging.getLogger("check_your_advisor").setLevel(logging.CRITICAL)

# The client jitters 0.3-1.5s before every request. Left alone this file would
# take minutes to make its "the UA is the same every time" point.
_slept: list[float] = []
http_client.time.sleep = lambda s: _slept.append(s)

REPEATS = 40  # enough that a 6-entry random pool would show more than one value


# ======================================================================
print("1. polite_headers — a name, not a costume")
# ======================================================================

bare = polite_headers()
check("the User-Agent is the project identifier", bare["User-Agent"], PROJECT_USER_AGENT)
check("which is the string pubmed_api already sends to NCBI",
      PROJECT_USER_AGENT, "check-your-advisor/1.0")
check_false("it is not one of the pool's browser strings",
            bare["User-Agent"] in http_client.USER_AGENTS)
check_false("it claims to be no Mozilla", "Mozilla" in bare["User-Agent"])
check_false("...no Chrome", "Chrome" in bare["User-Agent"])
check_false("...no Safari", "Safari" in bare["User-Agent"])
check("Accept-Language is pinned rather than rotated", bare["Accept-Language"], "en")
check("nothing browser-only is offered",
      sorted(bare), ["Accept-Language", "User-Agent"])

check("it is byte-identical on every call",
      len({polite_headers()["User-Agent"] for _ in range(REPEATS)}), 1)

with_mail = polite_headers("advisor@example.edu")
check("an address supplied by --email goes in, in Crossref's documented form",
      with_mail["User-Agent"], "check-your-advisor/1.0 (mailto:advisor@example.edu)")
check_true("the project identifier is still the head of it",
           with_mail["User-Agent"].startswith(PROJECT_USER_AGENT))
check("an empty address adds nothing", polite_headers("")["User-Agent"], PROJECT_USER_AGENT)


# ======================================================================
print("\n2. RobustHTTPClient.get puts it on the wire, and strips the costume")
# ======================================================================


class FakeHeaders(dict):
    def get(self, key, default=None):
        for k, v in self.items():
            if k.lower() == str(key).lower():
                return v
        return default


class FakeRaw:
    def __init__(self, body=b"{}", content_type="application/json"):
        self.status = 200
        self.headers = FakeHeaders({"Content-Type": content_type})
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class RecordingOpener:
    """Captures the Request object so the actual sent headers can be read."""

    def __init__(self):
        self.requests: list = []

    def open(self, req, timeout=None):
        self.requests.append(req)
        return FakeRaw()

    def sent(self) -> list[dict]:
        # urllib normalises header names to Capitalised-Form in `.headers`.
        return [{k.lower(): v for k, v in req.headers.items()} for req in self.requests]


def wired() -> tuple[RobustHTTPClient, RecordingOpener]:
    client = RobustHTTPClient(backoff_factor=0.0)
    opener = RecordingOpener()
    client._opener = lambda proxy, allow_redirects: opener  # noqa: SLF001
    return client, opener


client, opener = wired()
for _ in range(REPEATS):
    client.get("https://api.openalex.org/works?x=1", accept_type="api",
               extra_headers=polite_headers())
sent = opener.sent()

check("every request was recorded", len(sent), REPEATS)
check("exactly one User-Agent was ever sent",
      sorted({h["user-agent"] for h in sent}), [PROJECT_USER_AGENT])
check("no request carried a Sec-* header",
      [k for h in sent for k in h if k.startswith("sec-")], [])
check("no request carried DNT", [k for h in sent for k in h if k == "dnt"], [])
check("Accept-Language was not rotated",
      sorted({h["accept-language"] for h in sent}), ["en"])
check("content negotiation still works — the api Accept profile survives",
      sorted({h["accept"] for h in sent}), [http_client.ACCEPT_PROFILES["api"]])
check("and so does the encoding the client can actually decode",
      sorted({h["accept-encoding"] for h in sent}), ["gzip, deflate"])

# The address form has to reach the wire too, not just the helper's return value.
client, opener = wired()
client.get("https://api.crossref.org/journals/1234-5678", accept_type="api",
           extra_headers=polite_headers("advisor@example.edu"))
check("the mailto form reaches the wire intact",
      opener.sent()[0]["user-agent"],
      "check-your-advisor/1.0 (mailto:advisor@example.edu)")

# The PDF download path is deliberately NOT in scope for this change, and this
# is the assertion that says so: with no extra_headers the pool still rotates
# and the browser headers still go out. If a later edit "cleans up" the pool,
# this fails and the reviewer is told which behaviour they changed.
client, opener = wired()
for _ in range(REPEATS):
    client.get("https://publisher.example.com/article.pdf", accept_type="pdf")
sent = opener.sent()
uas = {h["user-agent"] for h in sent}
check_true("the download path still draws from the pool", uas <= set(http_client.USER_AGENTS))
check_true("...and still rotates", len(uas) > 1)
check_true("...and still sends the browser headers for at least one Chrome draw",
           any(k.startswith("sec-") for h in sent for k in h))

# A caller that supplies extra_headers WITHOUT a User-Agent has not claimed an
# identity, so nothing is stripped. This keeps the rule narrow and testable.
client, opener = wired()
for _ in range(REPEATS):
    client.get("https://example.com/x", accept_type="pdf", extra_headers={"Range": "bytes=0-99"})
sent = opener.sent()
check_true("an extra header that is not a UA leaves the pool alone",
           {h["user-agent"] for h in sent} <= set(http_client.USER_AGENTS))
check("...and the extra header is still sent",
      sorted({h["range"] for h in sent}), ["bytes=0-99"])


# ======================================================================
print("\n3. openalex.py — both endpoints identify themselves")
# ======================================================================


class RecordingClient:
    """A stub `RobustHTTPClient` that keeps the headers each call asked for."""

    def __init__(self, payloads=None):
        self.payloads = payloads or {}
        self.headers_seen: list[dict] = []
        self.urls: list[str] = []
        self.stats = {"total_requests": 0}

    def get(self, url, accept_type="pdf", timeout=None, allow_redirects=True,
            extra_headers=None):
        self.urls.append(url)
        self.headers_seen.append(dict(extra_headers or {}))
        self.stats["total_requests"] += 1
        for token, payload in self.payloads.items():
            if token in url:
                return Response(200, {"Content-Type": "application/json"},
                                json.dumps(payload).encode("utf-8"))
        return Response(200, {"Content-Type": "application/json"}, b"{}")

    def user_agents(self) -> list[str]:
        return [h.get("User-Agent", "<none>") for h in self.headers_seen]


AUTHOR_PAYLOAD = {"results": [{
    "id": "https://openalex.org/A5000001",
    "display_name": "Doe Jane",
    "orcid": None,
    "works_count": 12,
    "cited_by_count": 90,
    "affiliations": [],
}]}

stub = RecordingClient({"/authors": AUTHOR_PAYLOAD})
for _ in range(REPEATS):
    openalex.resolve_author(stub, "Doe Jane")
check("resolve_author asked once per call", len(stub.headers_seen), REPEATS)
check("...and sent the project identifier every single time",
      sorted(set(stub.user_agents())), [PROJECT_USER_AGENT])
check_false("...never a browser string",
            any(ua in http_client.USER_AGENTS for ua in stub.user_agents()))

stub = RecordingClient({"/authors": AUTHOR_PAYLOAD})
openalex.resolve_author(stub, "Doe Jane", mailto="advisor@example.edu")
check("resolve_author passes --email into the UA as well as the query string",
      stub.user_agents(), ["check-your-advisor/1.0 (mailto:advisor@example.edu)"])
check_true("...and the query string still carries it, which is OpenAlex's own form",
           "mailto=advisor%40example.edu" in stub.urls[0])

WORKS_PAGE = {"meta": {"count": 1, "next_cursor": None},
              "results": [{"id": "https://openalex.org/W1", "display_name": "A work",
                           "publication_year": 2024, "authorships": []}]}

stub = RecordingClient({"/works": WORKS_PAGE})
for _ in range(REPEATS):
    openalex.fetch_works(stub, "A5000001")
check_true("fetch_works asked at least once per call", len(stub.headers_seen) >= REPEATS)
check("...and sent the project identifier every single time",
      sorted(set(stub.user_agents())), [PROJECT_USER_AGENT])

stub = RecordingClient({"/works": WORKS_PAGE})
openalex.fetch_works(stub, "A5000001", mailto="advisor@example.edu")
check("fetch_works carries --email too",
      sorted(set(stub.user_agents())),
      ["check-your-advisor/1.0 (mailto:advisor@example.edu)"])


# ======================================================================
print("\n4. journal_risk.py — all three sources identify themselves")
# ======================================================================

DOAJ_PAYLOAD = {"total": 0, "results": []}
CROSSREF_PAYLOAD = {"message": {"publisher": "Example Press",
                                "counts": {"total-dois": 10},
                                "coverage": {"abstracts-current": 0.0}}}
SOURCE_PAYLOAD = {"id": "https://openalex.org/S1", "is_in_doaj": True}

stub = RecordingClient({"doaj.org": DOAJ_PAYLOAD})
for _ in range(REPEATS):
    journal_risk.fetch_doaj_journal(stub, "1234-5678")
check("DOAJ sent the project identifier every time",
      sorted(set(stub.user_agents())), [PROJECT_USER_AGENT])
check_false("...never a browser string",
            any(ua in http_client.USER_AGENTS for ua in stub.user_agents()))

stub = RecordingClient({"crossref.org": CROSSREF_PAYLOAD})
for _ in range(REPEATS):
    journal_risk.fetch_crossref_journal(stub, "1234-5678")
check("Crossref sent the project identifier every time",
      sorted(set(stub.user_agents())), [PROJECT_USER_AGENT])

stub = RecordingClient({"crossref.org": CROSSREF_PAYLOAD})
journal_risk.fetch_crossref_journal(stub, "1234-5678", mailto="advisor@example.edu")
check("Crossref's polite pool gets the address in the UA, as its docs ask",
      stub.user_agents(), ["check-your-advisor/1.0 (mailto:advisor@example.edu)"])

stub = RecordingClient({"openalex.org/sources": SOURCE_PAYLOAD})
for _ in range(REPEATS):
    journal_risk.fetch_openalex_source(stub, "1234-5678")
check("OpenAlex sources sent the project identifier every time",
      sorted(set(stub.user_agents())), [PROJECT_USER_AGENT])

stub = RecordingClient({"openalex.org/sources": SOURCE_PAYLOAD})
journal_risk.fetch_openalex_source(stub, "1234-5678", mailto="advisor@example.edu")
check("OpenAlex sources carry --email too",
      stub.user_agents(), ["check-your-advisor/1.0 (mailto:advisor@example.edu)"])

# The address is a polite-pool credential, not something to file on disk. The
# signal records the endpoint without its query string for that reason, and
# putting the address in a header must not have re-introduced it.
stub = RecordingClient({"crossref.org": CROSSREF_PAYLOAD})
signals = journal_risk.fetch_crossref_journal(stub, "1234-5678",
                                              mailto="advisor@example.edu")
check("the recorded endpoint still holds no address",
      [s for s in signals if "example.edu" in json.dumps(s)], [])


# ======================================================================
print("\n5. citations.py — all three sources identify themselves")
# ======================================================================
# These three were missed the first time round, which is how `cite` ended up
# reaching api.openalex.org as a rotating fake Chrome while `harvest
# --openalex-works` reached the same host as check-your-advisor/1.0. One
# process, one API, two identities.

OPENALEX_WORK = {"cited_by_count": 42}
S2_PAYLOAD = {"citationCount": 7}
EPMC_PAYLOAD = {"resultList": {"result": [{"citedByCount": 3}]}}

stub = RecordingClient({"api.openalex.org": OPENALEX_WORK})
for _ in range(REPEATS):
    citations.fetch_openalex(stub, "10.1000/example")
check("OpenAlex citation counts sent the project identifier every time",
      sorted(set(stub.user_agents())), [PROJECT_USER_AGENT])
check_false("...never a browser string",
            any(ua in http_client.USER_AGENTS for ua in stub.user_agents()))
check("...and the same host `harvest` uses now hears one identity, not two",
      sorted({polite_headers()["User-Agent"], PROJECT_USER_AGENT}), [PROJECT_USER_AGENT])

stub = RecordingClient({"api.openalex.org": OPENALEX_WORK})
check("the count still comes back after the header change",
      citations.fetch_openalex(stub, "10.1000/example"), 42)

stub = RecordingClient({"api.openalex.org": OPENALEX_WORK})
citations.fetch_openalex(stub, "10.1000/example", mailto="advisor@example.edu")
check("OpenAlex's polite pool gets --email in the UA, as its docs ask",
      stub.user_agents(), ["check-your-advisor/1.0 (mailto:advisor@example.edu)"])
check_true("...and the query string still carries it too",
           "mailto=advisor%40example.edu" in stub.urls[0])

stub = RecordingClient({"semanticscholar.org": S2_PAYLOAD})
for _ in range(REPEATS):
    citations.fetch_semantic_scholar(stub, "10.1000/example")
check("Semantic Scholar sent the project identifier every time",
      sorted(set(stub.user_agents())), [PROJECT_USER_AGENT])
check_false("...never a browser string",
            any(ua in http_client.USER_AGENTS for ua in stub.user_agents()))

stub = RecordingClient({"semanticscholar.org": S2_PAYLOAD})
check("the count still comes back", citations.fetch_semantic_scholar(stub, "10.1000/x"), 7)

stub = RecordingClient({"ebi.ac.uk": EPMC_PAYLOAD})
for _ in range(REPEATS):
    citations.fetch_europe_pmc(stub, doi="10.1000/example")
check("Europe PMC sent the project identifier every time",
      sorted(set(stub.user_agents())), [PROJECT_USER_AGENT])
check_false("...never a browser string",
            any(ua in http_client.USER_AGENTS for ua in stub.user_agents()))

stub = RecordingClient({"ebi.ac.uk": EPMC_PAYLOAD})
check("the count still comes back",
      citations.fetch_europe_pmc(stub, doi="", pmid="12345678"), 3)

# Only OpenAlex documents a polite pool keyed on the address. Handing it to the
# other two would be user data sent to a third party that never asked for it,
# so the fallback chain must not leak it sideways.
stub = RecordingClient()
citations.fetch_citation_count(stub, doi="10.1000/example", pmid="12345678",
                               mailto="advisor@example.edu")
check("the whole fallback chain was walked", len(stub.user_agents()), 3)
check("only OpenAlex — the first link — carries the address",
      [ua for ua in stub.user_agents() if "example.edu" in ua],
      ["check-your-advisor/1.0 (mailto:advisor@example.edu)"])
check("the other two send the bare project name",
      stub.user_agents()[1:], [PROJECT_USER_AGENT, PROJECT_USER_AGENT])
check_false("...and no address reaches their URLs either",
            any("example.edu" in url for url in stub.urls[1:]))


# ======================================================================
print("\n6. the invariant, asserted against the source")
# ======================================================================
# Points 3, 4 and 5 test the call sites that exist today. This tests the rule,
# so that an endpoint added next month cannot silently fall back to the UA pool.


def get_call_arguments(path: str) -> list[str]:
    """The argument text of every `client.get(...)` in a source file."""
    src = open(path, encoding="utf-8").read()
    out = []
    for match in re.finditer(r"client\.get\(", src):
        depth = 0
        start = match.end() - 1
        for index in range(start, len(src)):
            if src[index] == "(":
                depth += 1
            elif src[index] == ")":
                depth -= 1
                if depth == 0:
                    out.append(src[start + 1:index])
                    break
    return out


for module in (openalex, journal_risk, citations):
    calls = get_call_arguments(module.__file__)
    name = os.path.basename(module.__file__)
    check_true(f"{name} has HTTP call sites to check", len(calls) > 0)
    check(f"every client.get in {name} names itself",
          [c.split(",")[0].strip() for c in calls if "extra_headers=" not in c], [])
    check(f"...and none of them re-enables the pool by hand in {name}",
          [c for c in calls if "USER_AGENTS" in c or "_random_headers" in c], [])

for module in (openalex, journal_risk, citations):
    src = open(module.__file__, encoding="utf-8").read()
    check(f"{os.path.basename(module.__file__)} imports the honest header helper",
          "polite_headers" in src, True)


# ======================================================================
print("\n7. download_sources.py — the catalogue hop names itself, the PDF hop does not")
# ======================================================================
# The download race makes two kinds of request per source, and they were both
# going out as a rotating fake browser. Only the second kind should be:
#
#   - the catalogue hop (accept_type="api") asks a keyless public JSON API which
#     open copy exists — Unpaywall, Europe PMC REST, Semantic Scholar Graph,
#     CORE v3, bioRxiv/medRxiv details, Open Access Button. Two of those hosts
#     are the same two `citations.py` already talks to as check-your-advisor/1.0,
#     so one run addressed api.semanticscholar.org and www.ebi.ac.uk under two
#     identities at once.
#   - the fetch hop (accept_type="pdf" / "html") follows the URL that answer
#     gave, which is a publisher landing page or a PDF binary. That is what the
#     UA pool exists for and it is deliberately untouched.
#
# Bucketed below by the Accept header, because that is the only thing on the
# wire that says which of the two a request was.

from check_your_advisor import download_sources  # noqa: E402

_ADDRESS = "advisor@example.edu"


class SourceOpener:
    """urllib stand-in that answers each source's API shape by URL.

    Every PDF body is deliberately not a PDF, so `save_pdf` declines it and the
    source function walks on to its next candidate instead of stopping at the
    first hit. That is what makes one sweep exercise every call site, and it
    writes no files.
    """

    def __init__(self):
        self.requests: list = []

    def open(self, req, timeout=None):
        self.requests.append(req)
        url = req.full_url

        def js(obj):
            return FakeRaw(json.dumps(obj).encode("utf-8"))

        if "oa.fcgi" in url:
            # An href with no `.tar.gz` in it, so try_pmc stops here.
            return FakeRaw(b'<a href="https://europepmc.org/x.pdf">p</a>', "text/html")
        if "api.unpaywall.org" in url:
            return js({"is_oa": True,
                       "best_oa_location": {"url_for_pdf": "https://publisher.example/a.pdf"}})
        if "ebi.ac.uk" in url:
            return js({"resultList": {"result": [{"fullTextUrlList": {"fullTextUrl": [
                {"documentStyle": "pdf", "availability": "Open access",
                 "url": "https://publisher.example/b.pdf"}]}}]}})
        if "api.semanticscholar.org" in url:
            return js({"isOpenAccess": True,
                       "openAccessPdf": {"url": "https://publisher.example/c.pdf"}})
        if "api.core.ac.uk" in url:
            return js({"results": [{"downloadUrl": "https://publisher.example/d.pdf"}]})
        if "api.biorxiv.org" in url:
            return js({"collection": [{"doi": "10.1101/x", "version": "1"}]})
        if "api.openaccessbutton.org" in url:
            return js({"url": "https://publisher.example/e.pdf"})
        if "doi.org" in url:
            return FakeRaw(b'<a href="https://publisher.example/f.pdf">pdf</a>', "text/html")
        return FakeRaw(b"not a pdf", "application/pdf")


def sweep(email: str = "") -> list[dict]:
    """Every request all eight sources make, as `{host, accept, headers}`."""
    client = RobustHTTPClient(backoff_factor=0.0, max_retries=0)
    opener = SourceOpener()
    client._opener = lambda proxy, allow_redirects: opener  # noqa: SLF001
    out = "/nonexistent-directory-for-this-test/x.pdf"
    download_sources.try_pmc(client, "PMC123", out)
    download_sources.try_unpaywall(client, "10.1/x", out, email or "a@b.edu")
    download_sources.try_europe_pmc(client, "10.1/x", "", out)
    download_sources.try_semantic_scholar(client, "10.1/x", out)
    download_sources.try_core(client, "10.1/x", out)
    download_sources.try_doi_redirect(client, "10.1/x", out)
    download_sources.try_biorxiv_medrxiv(client, "10.1101/x", "t", out)
    download_sources.try_oa_button(client, "10.1/x", out)
    records = []
    for req in opener.requests:
        headers = {k.lower(): v for k, v in req.headers.items()}
        records.append({"url": req.full_url,
                        "host": req.full_url.split("/")[2],
                        "accept": headers.get("accept", ""),
                        "headers": headers})
    return records


# Three sweeps, for the same reason section 2 repeats: one sweep could draw the
# same pool entry every time by luck and the rotation assertions would not mean
# anything.
seen = [rec for _ in range(3) for rec in sweep(_ADDRESS)]
api_hop = [r for r in seen if r["accept"] == http_client.ACCEPT_PROFILES["api"]]
fetch_hop = [r for r in seen if r["accept"] != http_client.ACCEPT_PROFILES["api"]]

check_true("the sweep reached the catalogue hop", len(api_hop) > 0)
check_true("...and the fetch hop", len(fetch_hop) > 0)
check("the six catalogue endpoints are the ones being asserted about",
      sorted({r["host"] for r in api_hop}),
      ["api.biorxiv.org", "api.core.ac.uk", "api.openaccessbutton.org",
       "api.semanticscholar.org", "api.unpaywall.org", "www.ebi.ac.uk"])
check("every catalogue request names this project",
      sorted({r["headers"]["user-agent"].split(" (")[0] for r in api_hop}),
      [PROJECT_USER_AGENT])
check_false("...never a browser string",
            any(r["headers"]["user-agent"] in http_client.USER_AGENTS for r in api_hop))
check("...and none of them carries a Sec-* header",
      [k for r in api_hop for k in r["headers"] if k.startswith("sec-")], [])
check("...or DNT", [k for r in api_hop for k in r["headers"] if k == "dnt"], [])
check("...with Accept-Language pinned rather than rotated",
      sorted({r["headers"]["accept-language"] for r in api_hop}), ["en"])

# The same two hosts `citations.py` reaches now hear one identity per run, which
# is the concrete defect this section closes.
for host, fetch in (("api.semanticscholar.org", citations.fetch_semantic_scholar),
                    ("www.ebi.ac.uk", citations.fetch_europe_pmc)):
    stub = RecordingClient()
    fetch(stub, "10.1000/example")
    download_ua = {r["headers"]["user-agent"] for r in api_hop if r["host"] == host}
    check(f"{host} hears one identity from both call sites in a run",
          sorted(download_ua | set(stub.user_agents())), [PROJECT_USER_AGENT])

# The address rides along only where the endpoint's own docs already put it in
# the query string. Everywhere else it would be user data handed to a third
# party that never asked for it.
carrying = {r["host"] for r in seen if _ADDRESS in r["headers"].get("user-agent", "")}
check("only Unpaywall carries --email in the User-Agent", sorted(carrying), ["api.unpaywall.org"])
check_false("...and no fetch-hop request carries it at all",
            any(_ADDRESS in r["headers"].get("user-agent", "") for r in fetch_hop))
check("the other five catalogue endpoints send the bare project name",
      sorted({r["headers"]["user-agent"] for r in api_hop
              if r["host"] != "api.unpaywall.org"}),
      [PROJECT_USER_AGENT])
# There is no "Unpaywall without an address" case to pin a UA for: the endpoint
# documents the address as required, and the source declines rather than issuing
# an anonymous query. Which is also why putting it in the UA discloses nothing —
# no request reaches that host at all unless the address is already in the URL.
_no_mail = RobustHTTPClient(backoff_factor=0.0, max_retries=0)
_no_mail_opener = SourceOpener()
_no_mail._opener = lambda proxy, allow_redirects: _no_mail_opener  # noqa: SLF001
check("without --email, Unpaywall is not asked at all",
      (download_sources.try_unpaywall(_no_mail, "10.1/x", "/nonexistent/x.pdf", ""),
       len(_no_mail_opener.requests)), (False, 0))

# The other half of the boundary: the fetch hop is untouched. If a later edit
# "finishes the job" by converting the PDF path too, this is what says so.
fetch_uas = {r["headers"]["user-agent"] for r in fetch_hop}
check_true("the fetch hop still draws from the pool", fetch_uas <= set(http_client.USER_AGENTS))
check_true("...and still rotates", len(fetch_uas) > 1)
check_true("...and still sends the browser headers for a Chrome draw",
           any(k.startswith("sec-") for r in fetch_hop for k in r["headers"]))

# One genuinely arguable case, pinned as status quo rather than changed. NCBI's
# `oa.fcgi` is a documented OA Web Service, but it is issued as accept_type="html"
# because it answers XML, so it is not one of the six and this round left it
# alone. Pinned so that a future change to it is a decision somebody makes on
# purpose, in either direction.
oa_fcgi = [r for r in seen if "oa.fcgi" in r["url"]]
check_true("the NCBI OA service was reached", len(oa_fcgi) > 0)
check_true("...and is still on the pool — unchanged this round, not endorsed",
           {r["headers"]["user-agent"] for r in oa_fcgi} <= set(http_client.USER_AGENTS))

# And the rule, asserted against the source, so a seventh catalogue endpoint
# added later cannot quietly go back to the pool — and so that a "consistency"
# edit cannot sweep the PDF fetches along with it.
ds_calls = get_call_arguments(download_sources.__file__)
api_calls = [c for c in ds_calls if 'accept_type="api"' in c]
pdf_calls = [c for c in ds_calls if 'accept_type="pdf"' in c]
check("download_sources.py has six catalogue call sites", len(api_calls), 6)
check("every one of them names itself",
      [c.split(",")[0].strip() for c in api_calls if "extra_headers=" not in c], [])
check("...and none re-enables the pool by hand",
      [c for c in api_calls if "USER_AGENTS" in c or "_random_headers" in c], [])
check("no PDF fetch was converted along with them",
      [c for c in pdf_calls if "extra_headers=" in c], [])
check("download_sources.py imports the honest header helper",
      "polite_headers" in open(download_sources.__file__, encoding="utf-8").read(), True)


print(f"\nSummary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
sys.exit(1 if _failed else 0)
