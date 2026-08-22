#!/usr/bin/env python3
"""
PDF binary validation, saving, identity matching and link extraction.

Text extraction is stubbed throughout, so these tests need neither real PDFs nor
PyMuPDF.

Run: python tests/test_pdf_validation.py
"""

from __future__ import annotations

import builtins
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor import pdf_utils  # noqa: E402
from check_your_advisor.pdf_utils import (  # noqa: E402
    _doi_key,
    _title_tokens,
    extract_pdf_text,
    extract_pdf_urls_from_html,
    is_valid_pdf,
    save_pdf,
    validate_pdf_matches_paper,
)

_passed = 0
_failed = 0


def check(label, actual, expected):
    global _passed, _failed
    ok = actual == expected
    if ok:
        _passed += 1
    else:
        _failed += 1
    detail = "" if ok else f"  (expected {expected!r}, got {actual!r})"
    print(f"  {'[PASS]' if ok else '[FAIL]'} {label}{detail}")


def _pdf_bytes(body: bytes = b"x", *, magic: bytes = b"%PDF-", eof: bytes = b"%%EOF") -> bytes:
    """A byte string shaped like a PDF, padded past the default 1000-byte gate."""
    return magic + body * 2000 + eof


def _with_temp_file(fn, *, content: bytes | None = None):
    fd, path = tempfile.mkstemp(suffix=".pdf")
    if content is not None:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
    else:
        os.close(fd)
    try:
        return fn(path)
    finally:
        if os.path.exists(path):
            os.remove(path)


def _stub_text(text: str, note: str = "stubbed"):
    """Replace extract_pdf_text for one call; returns a restore callable."""
    original = pdf_utils.extract_pdf_text
    pdf_utils.extract_pdf_text = lambda _path, *a, **k: (text, note)
    return lambda: setattr(pdf_utils, "extract_pdf_text", original)


# ======================================================================
print("\n--- is_valid_pdf: size gate, magic number, %%EOF ---")
# ======================================================================
check("accepts a well-formed PDF", is_valid_pdf(_pdf_bytes()), True)
check("rejects content below min_size", is_valid_pdf(b"%PDF-tiny"), False)
check("rejects content without the %PDF- magic number",
      is_valid_pdf(_pdf_bytes(magic=b"<html")), False)
check("min_size is configurable", is_valid_pdf(b"%PDF-" + b"x" * 20 + b"%%EOF", min_size=10), True)

# The %%EOF branch logs and deliberately does not reject: the source comment says
# "不强制拒绝，因为某些合法 PDF 可能尾部有多余字节". Pinning that here so the
# non-enforcement is a decision on record rather than something a reader of the
# docstring ("额外检查") would assume works.
check("a truncated PDF with no %%EOF is still accepted",
      is_valid_pdf(_pdf_bytes(eof=b"")), True)
check("%%EOF is matched case-insensitively via the lowercase branch",
      is_valid_pdf(_pdf_bytes(eof=b"%%eof")), True)


# ======================================================================
print("\n--- save_pdf: response gating and what content-type actually does ---")
# ======================================================================
class _Resp:
    def __init__(self, content: bytes, status: int = 200, content_type: str = "application/pdf"):
        self.content = content
        self.status_code = status
        self.headers = {"content-type": content_type}


def _save(resp, name="out.pdf"):
    d = tempfile.mkdtemp()
    path = os.path.join(d, "nested", name)
    ok = save_pdf(resp, path)
    exists = os.path.exists(path)
    if exists:
        os.remove(path)
    return ok, exists


check("a None response is refused", save_pdf(None, "unused.pdf"), False)
check("a non-200 response is refused", _save(_Resp(_pdf_bytes(), status=404))[0], False)
check("a valid PDF is written", _save(_Resp(_pdf_bytes()))[0], True)
check("missing parent directories are created", _save(_Resp(_pdf_bytes()))[1], True)
check("an HTML error page served as application/pdf is refused",
      _save(_Resp(b"<html>paywall</html>", content_type="application/pdf"))[0], False)

# Both branches of `if "pdf" in content_type or is_valid_pdf(...)` re-test the
# content, so the header never changes the outcome — save_pdf is exactly
# is_valid_pdf(). These two pin that equivalence: a mislabelled but valid PDF is
# kept, and no content-type can rescue invalid bytes.
check("a valid PDF mislabelled as octet-stream is still kept",
      _save(_Resp(_pdf_bytes(), content_type="application/octet-stream"))[0], True)
check("content-type alone never rescues invalid content",
      _save(_Resp(b"not a pdf at all", content_type="application/pdf"))[0], False)


# ======================================================================
print("\n--- extract_pdf_text: missing PyMuPDF and the 200-character floor ---")
# ======================================================================
_real_import = builtins.__import__


def _no_fitz(name, *args, **kwargs):
    if name == "fitz":
        raise ImportError("No module named 'fitz'")
    return _real_import(name, *args, **kwargs)


builtins.__import__ = _no_fitz
try:
    check("a missing PyMuPDF is reported, not raised",
          extract_pdf_text("whatever.pdf"), ("", "PyMuPDF unavailable"))
    check("pdf_text_extraction_available() reports the same condition",
          pdf_utils.pdf_text_extraction_available(), False)
finally:
    builtins.__import__ = _real_import

# Reported, not asserted. PyMuPDF is an optional extra — it is AGPL-3.0 and this
# project is MIT — so a clone with nothing installed is a supported state, and in
# that state identity validation is skipped by design. Asserting the package is
# present would fail the very configuration the tool promises to run in. The two
# checks above already pin the behaviour that matters: absence is reported rather
# than raised, and the availability probe agrees with it.
if pdf_utils.pdf_text_extraction_available():
    print("  [INFO] PyMuPDF present: identity validation can reject a wrong paper")
else:
    print("  [INFO] PyMuPDF absent: identity validation is skipped, nothing is quarantined")

check("a nonexistent path fails softly rather than raising",
      extract_pdf_text(os.path.join(tempfile.mkdtemp(), "absent.pdf"))[0], "")

# The 200-character floor lives inside extract_pdf_text, so exercising it needs a
# real PDF: every other test here stubs extraction out and would stay green if
# the floor were deleted. Skipped rather than failed when the optional PyMuPDF
# extra is not installed.
if pdf_utils.pdf_text_extraction_available():
    import fitz  # noqa: E402

    def _real_pdf(lines: list[str]) -> str:
        # One line per list entry: a single long string would run off the page
        # width and only part of it would land in the text layer.
        path = os.path.join(tempfile.mkdtemp(), "real.pdf")
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), "\n".join(lines))
        doc.save(path)
        doc.close()
        return path

    check("a PDF with too little text is flagged as insufficient",
          extract_pdf_text(_real_pdf(["Short."]))[1], "insufficient extracted text")
    check("a PDF with enough text reports success",
          extract_pdf_text(_real_pdf(["gastric cancer cohort analysis"] * 20))[1],
          "text extracted")
    check("the short PDF's text is still returned for the caller to inspect",
          "Short." in extract_pdf_text(_real_pdf(["Short."]))[0], True)
else:  # pragma: no cover - depends on which extras are installed
    print("  [SKIP] PyMuPDF not installed; real-PDF extraction cases not run")


# ======================================================================
print("\n--- validate_pdf_matches_paper: accept, reject and skip paths ---")
# ======================================================================
_LONG = "gastric cancer molecular characterization cohort analysis " * 10


def _validate(paper, text, **kw):
    restore = _stub_text(text)
    try:
        return _with_temp_file(lambda p: validate_pdf_matches_paper(p, paper, **kw))
    finally:
        restore()


check("a missing candidate file is refused",
      validate_pdf_matches_paper(os.path.join(tempfile.mkdtemp(), "gone.pdf"), {"title": "x"}),
      (False, "candidate file missing"))

# Skipping is the designed answer when the tool cannot read the paper: the binary
# already passed is_valid_pdf, so refusing here would discard good downloads.
check("empty extracted text skips the check rather than failing it",
      _validate({"title": "anything"}, "")[0], True)
check("text under 200 characters skips the check",
      _validate({"title": "anything"}, "short text")[0], True)
check("and says why it skipped",
      _validate({"title": "anything"}, "short text")[1].startswith("identity check skipped"), True)

check("a DOI printed in the text is accepted",
      _validate({"doi": "10.1038/nature13480", "title": "unrelated words here"},
                "Published DOI: 10.1038/nature13480 " + _LONG),
      (True, "DOI matched"))
check("a doi.org URL prefix on the supplied DOI still matches",
      _validate({"doi": "https://doi.org/10.1038/nature13480", "title": "unrelated"},
                "DOI 10.1038/nature13480 " + _LONG)[1], "DOI matched")
check("punctuation differences do not break the DOI match",
      _validate({"doi": "10.1016/S0140-6736(20)31288-5", "title": "unrelated"},
                "doi 10 1016 S0140 6736 20 31288 5 " + _LONG)[1], "DOI matched")

check("the full normalized title appearing verbatim is accepted",
      _validate({"doi": "", "title": "Comprehensive molecular characterization of gastric adenocarcinoma"},
                "Comprehensive molecular characterization of gastric adenocarcinoma. " + _LONG)[1],
      "title matched")
check("a wrong paper is refused on token overlap",
      _validate({"doi": "", "title": "YY1 mediated transcriptional regulation of LINC01615 in gastric cancer"},
                "MUC5AC CD44 axis promotes lung tumor invasion macrophage signaling " * 30)[0],
      False)
check("the overlap threshold is honoured",
      _validate({"doi": "", "title": "alpha bravo charlie delta echo foxtrot"},
                "alpha bravo charlie " + _LONG, min_title_overlap=0.9)[0],
      False)
check("the same evidence passes at a lower threshold",
      _validate({"doi": "", "title": "alpha bravo charlie delta echo foxtrot"},
                "alpha bravo charlie " + _LONG, min_title_overlap=0.4)[0],
      True)
check("nothing to check on is accepted rather than refused",
      _validate({"doi": "", "title": ""}, _LONG),
      (True, "no title/DOI available for identity check"))


# ======================================================================
print("\n--- token and DOI normalisation helpers ---")
# ======================================================================
check("https doi.org prefix is stripped", _doi_key("https://doi.org/10.1/A"), "10.1/a")
check("http doi.org prefix is stripped", _doi_key("http://doi.org/10.1/A"), "10.1/a")
check("a doi: prefix is stripped", _doi_key(" DOI:10.1/A "), "10.1/a")
check("an empty DOI normalises to empty", _doi_key(None), "")

check("tokens shorter than four characters are dropped",
      _title_tokens("A new PD1 assay for tumour cells"), {"assay", "tumour", "cells"})
check("stop words are dropped even when long enough",
      _title_tokens("relationship between analysis and effects"), set())
check("html entities are decoded before tokenising",
      "naive" in _title_tokens("A na&iuml;ve approach"), False)


# ======================================================================
print("\n--- extract_pdf_urls_from_html: priority order and dedup ---")
# ======================================================================
_HTML = """
<meta name="citation_pdf_url" content="https://host/best.pdf">
<link type="application/pdf" href="https://host/typed.pdf">
<a href="https://host/generic.pdf">pdf</a>
<a href="https://host/generic.pdf">duplicate</a>
"""
_urls = extract_pdf_urls_from_html(_HTML)
check("the citation_pdf_url meta tag ranks first", _urls[0], "https://host/best.pdf")
check("a typed application/pdf link ranks second", _urls[1], "https://host/typed.pdf")
check("a generic .pdf URL is still picked up", "https://host/generic.pdf" in _urls, True)
check("duplicates are collapsed", len(_urls), len(set(_urls)))
check("a page with no PDF links yields nothing",
      extract_pdf_urls_from_html("<html><body>no links</body></html>"), [])
check("query strings are preserved",
      extract_pdf_urls_from_html('<a href="https://host/a.pdf?token=1">x</a>'),
      ["https://host/a.pdf?token=1"])


print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(0 if _failed == 0 else 1)
