#!/usr/bin/env python3
"""
Regression tests for the standard-library HTTP client.

These exist because this module had no tests at all while it was 186 lines of
`requests` + `urllib3.Retry`, and it was then rewritten on `urllib` to remove
the package's last hard dependency. Every behaviour the download path relies on
was, until now, guaranteed by a third-party library rather than by anything in
this repository:

  - a network failure returns None, an HTTP error status returns a Response.
    Every caller reads `resp.status_code`; turning a 404 into an exception
    would change the meaning of all thirteen call sites in download_sources.py
  - 429 and 5xx retry, and Retry-After is honoured when the server sends one
  - Content-Encoding is undone. `requests` did this invisibly; `urllib` does
    not, so a gzipped PDF would otherwise be saved as garbage that still starts
    with the wrong magic bytes
  - `deflate` arrives in both the zlib-wrapped and the raw form, and the recipe
    usually quoted for it handles only the raw one

No socket is opened: a fake opener is installed on the client, and the jitter
sleep is stubbed out, so the whole file runs offline in well under a second.

Run: python tests/test_http_client.py
"""

from __future__ import annotations

import gzip
import io
import os
import sys
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor import http_client  # noqa: E402
from check_your_advisor.http_client import (  # noqa: E402
    Response,
    RobustHTTPClient,
    _decompress,
    _random_headers,
)

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


# Never sleep: the client jitters 0.3-1.5s before every request and backs off
# between retries. Left alone, this file would take minutes.
_slept: list[float] = []
http_client.time.sleep = lambda s: _slept.append(s)


class FakeHeaders(dict):
    """email.message.Message is case-insensitive; dict is not. Mimic the part used."""

    def get(self, key, default=None):
        for k, v in self.items():
            if k.lower() == str(key).lower():
                return v
        return default


class FakeRaw:
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = FakeHeaders(headers or {})
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Serves a scripted list of outcomes, one per call.

    An entry is either a FakeRaw or an exception instance to raise.
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.timeouts: list[float] = []

    def open(self, req, timeout=None):
        self.calls += 1
        self.timeouts.append(timeout)
        item = self.script.pop(0) if self.script else self.script_default()
        if isinstance(item, BaseException):
            raise item
        return item

    def script_default(self):
        return FakeRaw(200, {"Content-Type": "text/plain"}, b"default")


def client_with(script, **kw) -> tuple[RobustHTTPClient, FakeOpener]:
    c = RobustHTTPClient(backoff_factor=0.0, **kw)
    opener = FakeOpener(script)
    c._opener = lambda proxy, allow_redirects: opener  # noqa: SLF001
    return c, opener


print("Headers")
h = _random_headers("pdf")
check_true("no brotli is advertised, because nothing here can decode it",
           "br" not in h["Accept-Encoding"])
check("gzip and deflate are still advertised", h["Accept-Encoding"], "gzip, deflate")
check_true("Connection is not claimed — urllib decides it", "Connection" not in h)
check("the accept profile is honoured", h["Accept"], http_client.ACCEPT_PROFILES["pdf"])
check("an unknown profile falls back to pdf",
      _random_headers("nonsense")["Accept"], http_client.ACCEPT_PROFILES["pdf"])
check_true("the User-Agent comes from the pool", h["User-Agent"] in http_client.USER_AGENTS)


print("\nContent-Encoding")
body = b"%PDF-1.7 the actual bytes " * 20
check("identity passes through", _decompress(body, ""), body)
check("gzip is undone", _decompress(gzip.compress(body), "gzip"), body)
check("zlib-wrapped deflate is undone — the standards-correct form",
      _decompress(zlib.compress(body), "deflate"), body)
raw_deflate = zlib.compressobj(wbits=-zlib.MAX_WBITS)
raw_bytes = raw_deflate.compress(body) + raw_deflate.flush()
check("raw deflate is undone too — what many servers actually send",
      _decompress(raw_bytes, "deflate"), body)
check("an undecodable body is returned as-is rather than lost",
      _decompress(b"not really gzip", "gzip"), b"not really gzip")
check("an unknown encoding is left alone", _decompress(body, "br"), body)


print("\nSuccess path")
c, op = client_with([FakeRaw(200, {"Content-Type": "application/pdf"}, body)])
r = c.get("https://example.org/a.pdf", accept_type="pdf", timeout=60)
check("status is carried", r.status_code, 200)
check("body is carried", r.content, body)
check("headers are readable case-insensitively", r.headers.get("content-type"), "application/pdf")
check("the per-request timeout reaches the opener", op.timeouts, [60])
check("stats count the request", c.stats, {"total_requests": 1})

c, op = client_with([FakeRaw(200, {"Content-Encoding": "gzip"}, gzip.compress(body))])
check("a gzipped response is decompressed before the caller sees it",
      c.get("https://example.org/a.pdf").content, body)


print("\nHTTP errors are responses, not exceptions")
from urllib.error import HTTPError, URLError  # noqa: E402

# A syntactically valid URL: urllib.request.Request rejects a bare token
# before any opener is consulted, which would test the wrong thing.
URL = "https://example.org/x"

err = HTTPError("https://example.org/missing", 404, "Not Found",
                FakeHeaders({"Content-Type": "text/html"}), io.BytesIO(b"gone"))
c, op = client_with([err])
r = c.get("https://example.org/missing")
check_true("a 404 returns a Response rather than raising", r is not None)
check("...carrying the status", r.status_code, 404)
check("...and the error body", r.content, b"gone")
check("a 404 is not retried", op.calls, 1)

c, op = client_with([HTTPError(URL, 403, "Forbidden", FakeHeaders({}), io.BytesIO(b""))])
check("403 is returned, not retried", (c.get(URL).status_code, op.calls), (403, 1))


print("\nRetries")
c, op = client_with([
    HTTPError(URL, 503, "busy", FakeHeaders({}), io.BytesIO(b"")),
    FakeRaw(200, {}, b"ok"),
])
r = c.get(URL)
check("503 retries and then succeeds", (r.status_code, r.content), (200, b"ok"))
check("...in exactly two calls", op.calls, 2)

c, op = client_with([HTTPError(URL, 500, "boom", FakeHeaders({}), io.BytesIO(b""))] * 9,
                    max_retries=3)
r = c.get(URL)
check("a persistently failing status is returned once retries run out", r.status_code, 500)
check("...after 1 + max_retries attempts", op.calls, 4)

_slept.clear()
c, op = client_with([
    HTTPError(URL, 429, "slow down", FakeHeaders({"Retry-After": "7"}), io.BytesIO(b"")),
    FakeRaw(200, {}, b"ok"),
])
c.get(URL)
check_true("Retry-After is honoured rather than the backoff curve", 7.0 in _slept)

c, op = client_with([TimeoutError(), TimeoutError(), TimeoutError(), TimeoutError()],
                    max_retries=3)
check("an exhausted timeout returns None, it does not raise", c.get(URL), None)
check("...after 1 + max_retries attempts", op.calls, 4)

c, op = client_with([TimeoutError(), FakeRaw(200, {}, b"ok")])
check("a transient timeout is retried and recovers", c.get(URL).content, b"ok")

c, op = client_with([URLError("connection reset")] * 5, max_retries=2)
check("an exhausted connection error returns None", c.get(URL), None)
check("...after 1 + max_retries attempts", op.calls, 3)

c, op = client_with([ValueError("something unforeseen")])
check("an unexpected error kills the request, not the run", c.get(URL), None)


print("\nOpeners")
c = RobustHTTPClient()
check("no proxy is chosen when the pool is empty", c._get_proxy(), None)
check("an opener is cached per (proxy, redirect policy)",
      c._opener(None, True) is c._opener(None, True), True)
check("a different redirect policy gets a different opener",
      c._opener(None, True) is c._opener(None, False), False)
c = RobustHTTPClient(proxy_list=["http://p:1"])
check("a proxy is chosen when the pool is not empty", c._get_proxy(), "http://p:1")
check("a different proxy gets a different opener",
      c._opener("http://p:1", True) is c._opener(None, True), False)



# ----------------------------------------------------------------------
# Response.text and Response.json()
#
# These two members were missing while ten call sites in download_sources.py
# used them, so seven of the eight open-access sources raised AttributeError on
# their first parse and never reached their download. The `.json()` sites guard
# with `except ValueError`, which does not catch AttributeError, so the failure
# escaped the source function and ended the race instead of falling through to
# the next source. Only try_pmc's two direct PDF URLs, which read `.content`
# through save_pdf, ever worked.
#
# The last check in this block is the one that matters: it walks every source
# function with a stub client and asserts none of them raises. A future edit
# that trims Response back to three fields "because nothing reads the rest"
# fails here rather than in a user's download run.
# ----------------------------------------------------------------------
print("\nResponse.text / Response.json()")

check("json() parses a JSON body",
      Response(200, {"content-type": "application/json"}, b'{"cited_by_count": 42}').json(),
      {"cited_by_count": 42})
check("json() tolerates absent headers", Response(200, {}, b"{}").json(), {})
check("text decodes UTF-8 when the charset says so",
      Response(200, {"content-type": "text/html; charset=utf-8"}, "中文".encode("utf-8")).text,
      "中文")
check("text honours a non-UTF-8 charset",
      Response(200, {"content-type": "text/html; charset=gbk"}, "中文".encode("gbk")).text,
      "中文")
check("text defaults to UTF-8 when Content-Type states no charset",
      Response(200, {"content-type": "text/html"}, "中文".encode("utf-8")).text,
      "中文")
check("text reads a capitalised Content-Type header",
      Response(200, {"Content-Type": "text/html; charset=gbk"}, "中文".encode("gbk")).text,
      "中文")
check("text strips quotes around the charset",
      Response(200, {"content-type": 'text/html; charset="gbk"'}, "中文".encode("gbk")).text,
      "中文")
# A misspelled charset must not cost the body: servers send `utf8`, `UTF-8"` and
# outright typos, and raising here would abort a download race over a header.
check("an unknown charset falls back to UTF-8 instead of raising",
      Response(200, {"content-type": "text/html; charset=not-a-real-codec"}, b"hi").text,
      "hi")
check("undecodable bytes are replaced, not raised",
      Response(200, {"content-type": "text/html"}, b"\xff\xfeok").text.endswith("ok"),
      True)

# download_sources guards every json() call with `except ValueError`. JSONDecodeError
# subclasses ValueError, so a non-JSON body arrives as the error those sites expect.
try:
    Response(200, {}, b"<html>not json</html>").json()
    check("a non-JSON body raises ValueError", "no exception", "ValueError")
except ValueError:
    check("a non-JSON body raises ValueError", "ValueError", "ValueError")
except Exception as exc:  # noqa: BLE001 - the point is that it is not this
    check("a non-JSON body raises ValueError", type(exc).__name__, "ValueError")

_FAKE_PDF = b"%PDF-1.4\n" + b"0" * 40000 + b"\n%%EOF"


class _SourceStub:
    """Answers each source's API shape, so every parse path is actually walked."""

    def get(self, url, accept_type="pdf", timeout=None, **kwargs):
        import json as _json

        def body(obj):
            return Response(200, {"content-type": "application/json"},
                            _json.dumps(obj).encode("utf-8"))

        if url.endswith(".pdf") or "ptpmcrender" in url or "/pdf/" in url:
            return Response(200, {"content-type": "application/pdf"}, _FAKE_PDF)
        if "oa.fcgi" in url:
            return Response(200, {"content-type": "text/html"}, b'<a href="https://x/y.pdf">p</a>')
        if "unpaywall" in url:
            return body({"is_oa": True, "best_oa_location": {"url_for_pdf": "https://h/a.pdf"}})
        if "europepmc" in url:
            return body({"resultList": {"result": [{"fullTextUrlList": {"fullTextUrl": [
                {"documentStyle": "pdf", "availability": "Open access", "url": "https://h/b.pdf"}]}}]}})
        if "semanticscholar" in url:
            return body({"isOpenAccess": True, "openAccessPdf": {"url": "https://h/c.pdf"}})
        if "core.ac.uk" in url:
            return body({"results": [{"downloadUrl": "https://h/d.pdf"}]})
        if "biorxiv.org/details" in url:
            return body({"collection": [{"doi": "10.1101/x", "version": "1"}]})
        if "openaccessbutton" in url:
            return body({"url": "https://h/e.pdf"})
        if "doi.org" in url:
            return Response(200, {"content-type": "text/html"}, b'<a href="https://h/f.pdf">pdf</a>')
        return Response(404, {}, b"")


import tempfile  # noqa: E402

from check_your_advisor import download_sources as _ds  # noqa: E402

_tmp = tempfile.mkdtemp()
_out = lambda name: os.path.join(_tmp, name + ".pdf")  # noqa: E731
_SOURCES = (
    ("try_pmc", lambda c: _ds.try_pmc(c, "PMC123", _out("1"))),
    ("try_unpaywall", lambda c: _ds.try_unpaywall(c, "10.1/x", _out("2"), "a@b.edu")),
    ("try_europe_pmc", lambda c: _ds.try_europe_pmc(c, "10.1/x", "", _out("3"))),
    ("try_semantic_scholar", lambda c: _ds.try_semantic_scholar(c, "10.1/x", _out("4"))),
    ("try_doi_redirect", lambda c: _ds.try_doi_redirect(c, "10.1/x", _out("5"))),
    ("try_core", lambda c: _ds.try_core(c, "10.1/x", _out("6"))),
    ("try_biorxiv_medrxiv", lambda c: _ds.try_biorxiv_medrxiv(c, "10.1101/x", "t", _out("7"))),
    ("try_oa_button", lambda c: _ds.try_oa_button(c, "10.1/x", _out("8"))),
)
for _name, _fn in _SOURCES:
    try:
        check(f"{_name} completes against a real Response and saves the PDF", _fn(_SourceStub()), True)
    except AttributeError as exc:
        check(f"{_name} completes against a real Response and saves the PDF",
              f"AttributeError: {exc}", True)


print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(1 if _failed else 0)
