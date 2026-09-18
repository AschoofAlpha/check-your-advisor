#!/usr/bin/env python3
"""
Tests for `html_to_pdf`: the optional HTML-to-PDF path.

The module shells out to a converter that is either installed or not, so the
tests have to work on a machine with all four and on a machine with none. They do
that by driving the two seams the module deliberately exposes — `runner` for the
subprocess and `shutil.which` for the probe — so every branch is reachable
without installing anything.

What is actually being protected here, in order of how badly it would hurt:

  1. A missing converter is not an error. It returns `ok: False` with install
     lines and the caller carries on, because the HTML report is the deliverable
     and a PDF is a second copy of it.
  2. An exit code of 0 is never trusted on its own. LibreOffice sharing a profile
     with a running instance exits 0 having written nothing, and a caller that
     believed the return code would report a PDF that does not exist.
  3. Nothing is imported from a third-party package and nothing is downloaded.

Fully offline: no network, no browser, no pytest.

Run: python tests/test_html_to_pdf.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor import html_to_pdf  # noqa: E402

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


ROOT = tempfile.mkdtemp(prefix="cya-pdf-test-")
PAGE = Path(ROOT) / "advisor_profile_20260822_120000.html"
PAGE.write_text("<!DOCTYPE html><html><body><h1>report</h1></body></html>", encoding="utf-8")


class FakeCompleted:
    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


def runner_writing(pdf_bytes: bytes = b"%PDF-1.4\n", returncode: int = 0, stderr: str = ""):
    """A fake `subprocess.run` that records the argv and writes the file the real
    converter would have written."""
    seen: list[list[str]] = []

    def run(argv, **kwargs):
        seen.append(list(argv))
        for token in argv:
            if str(token).endswith(".pdf"):
                Path(str(token).split("=", 1)[-1]).write_bytes(pdf_bytes)
        return FakeCompleted(returncode, stderr)

    run.seen = seen  # type: ignore[attr-defined]
    return run


def runner_silent(returncode: int = 0, stderr: str = ""):
    """A converter that exits without writing anything — the soffice failure."""
    def run(argv, **kwargs):
        return FakeCompleted(returncode, stderr)
    return run


# ============================================================
# 1. Probing
# ============================================================

print("finding a converter")

check("the four converters are declared in one place",
      sorted(html_to_pdf.CONVERTERS), sorted(html_to_pdf.CONVERTER_ORDER))
check("wkhtmltopdf is tried first — smallest install, best print-CSS support",
      html_to_pdf.CONVERTER_ORDER[0], "wkhtmltopdf")
check("soffice is tried last — it is the weakest renderer of the four",
      html_to_pdf.CONVERTER_ORDER[-1], "soffice")
check("every converter carries install lines, because that is the whole fallback",
      [name for name, c in html_to_pdf.CONVERTERS.items() if not c.install], [])
check("every converter carries a note saying what it costs the page",
      [name for name, c in html_to_pdf.CONVERTERS.items() if not c.notes], [])
check("...and every one names at least one executable to look for",
      [name for name, c in html_to_pdf.CONVERTERS.items() if not c.executables], [])

# An explicitly named binary is honoured without probing anything: a user who
# points at a binary has said which one they mean.
check("an explicit path is used as given",
      html_to_pdf.find_converter(explicit_path="/opt/weird/chrome"),
      {"name": "chrome", "path": "/opt/weird/chrome"})
check("...and is paired with the converter family that was asked for",
      html_to_pdf.find_converter(prefer="wkhtmltopdf", explicit_path="/opt/wk"),
      {"name": "wkhtmltopdf", "path": "/opt/wk"})

_real_which = html_to_pdf.shutil.which
_real_isfile = html_to_pdf.os.path.isfile
try:
    html_to_pdf.shutil.which = lambda name: None
    html_to_pdf.os.path.isfile = lambda path: False
    check("a machine with none installed reports none", html_to_pdf.find_converter(), {})
    check("...and lists none as available", html_to_pdf.available_converters(), [])

    hints = html_to_pdf.install_hint_lines()
    check_true("the hint says the HTML report is complete and unchanged",
               any("complete and unchanged" in line for line in hints))
    check_true("...and names every converter", all(any(name in line for line in hints)
                                                   for name in html_to_pdf.CONVERTER_ORDER))
    check_true("...and says nothing is downloaded for the user",
               any("never fetches an installer" in line for line in hints))
    check_false("...and does not tell anyone to pipe anything to a shell",
                any("curl" in line and "sh" in line for line in hints))

    result = html_to_pdf.convert(PAGE)
    check_false("a machine with no converter does not produce a pdf", result["ok"])
    check("...and does not raise", result["reason"],
          "no HTML-to-PDF converter is installed on this machine.")
    check_true("...and hands back the install lines", len(result["hint_lines"]) > 4)
    check_true("...and describe() prints the reason followed by them",
               html_to_pdf.describe(result)[0].startswith("未生成 PDF"))

    html_to_pdf.shutil.which = lambda name: f"/usr/bin/{name}" if name == "soffice" else None
    check("only what is installed is offered",
          html_to_pdf.find_converter(), {"name": "soffice", "path": "/usr/bin/soffice"})
    check("...and --pdf-converter cannot conjure one that is absent",
          html_to_pdf.find_converter(prefer="wkhtmltopdf"),
          {"name": "soffice", "path": "/usr/bin/soffice"})

    html_to_pdf.shutil.which = lambda name: f"/usr/bin/{name}" if name in (
        "soffice", "wkhtmltopdf") else None
    check("a preference wins when the preferred one is installed",
          html_to_pdf.find_converter(prefer="soffice"),
          {"name": "soffice", "path": "/usr/bin/soffice"})
    check("...and the declared order decides when there is no preference",
          html_to_pdf.find_converter(),
          {"name": "wkhtmltopdf", "path": "/usr/bin/wkhtmltopdf"})
finally:
    html_to_pdf.shutil.which = _real_which
    html_to_pdf.os.path.isfile = _real_isfile


# ============================================================
# 2. The command line each converter gets
# ============================================================

print("\nthe argv, per converter")

WORK = Path(ROOT) / "work"
WORK.mkdir(exist_ok=True)
OUT = Path(ROOT) / "out.pdf"

wk = html_to_pdf._argv("wkhtmltopdf", "wkhtmltopdf", PAGE, OUT, WORK)
check_true("wkhtmltopdf is allowed to read the local file it was handed",
           "--enable-local-file-access" in wk)
check("...and is given the input then the output, in that order",
      wk[-2:], [str(PAGE), str(OUT)])

chrome = html_to_pdf._argv("chrome", "chrome", PAGE, OUT, WORK)
check_true("chrome runs headless", "--headless=new" in chrome)
check_true("...against a throwaway profile, so a running Chrome cannot block it",
           any(arg.startswith("--user-data-dir=") for arg in chrome))
check_true("...printing to the path asked for",
           f"--print-to-pdf={OUT}" in chrome)
check_true("...and is handed a file:// URI, not a bare path",
           chrome[-1].startswith("file:///"))
check_true("...with no header and no footer stamped over the report",
           "--no-pdf-header-footer" in chrome)

soffice = html_to_pdf._argv("soffice", "soffice", PAGE, OUT, WORK)
check_true("soffice gets an isolated user profile — sharing one exits 0 writing nothing",
           any(arg.startswith("-env:UserInstallation=") for arg in soffice))
check_true("...and converts into a directory, because it names the output itself",
           "--outdir" in soffice)

weasy = html_to_pdf._argv("weasyprint", "weasyprint", PAGE, OUT, WORK)
check("weasyprint takes input then output and nothing else",
      weasy, ["weasyprint", str(PAGE), str(OUT)])


# ============================================================
# 3. Converting
# ============================================================

print("\nconverting")

missing = html_to_pdf.convert(Path(ROOT) / "not-here.html", explicit_path="/usr/bin/wkhtmltopdf")
check_false("a missing input is refused before anything is run", missing["ok"])
check_true("...and says so plainly", "does not exist" in missing["reason"])

target = Path(ROOT) / "ok.pdf"
run = runner_writing()
ok = html_to_pdf.convert(PAGE, target, prefer="wkhtmltopdf",
                         explicit_path="/usr/bin/wkhtmltopdf", runner=run)
check_true("a converter that writes a file reports success", ok["ok"])
check("...and hands back the path it wrote", ok["pdf_path"], str(target))
check("...and names which converter produced it", ok["converter"], "wkhtmltopdf")
check_true("...and records the argv so a failure can be reproduced by hand", ok["argv"])
check_true("the file is really there", target.is_file())
check_true("describe() names the converter beside the path",
           "wkhtmltopdf" in html_to_pdf.describe(ok)[0])

# The load-bearing case. soffice sharing a profile with a running instance exits
# 0 having written nothing at all; trusting the return code reports a PDF that
# does not exist.
silent = html_to_pdf.convert(PAGE, Path(ROOT) / "silent.pdf", prefer="wkhtmltopdf",
                             explicit_path="/usr/bin/wkhtmltopdf",
                             runner=runner_silent(returncode=0))
check_false("exit code 0 with no file written is a failure, not a success", silent["ok"])
check_true("...and the reason names the path that stayed empty",
           "wrote no file to" in silent["reason"])
check("...and no path is handed back to be logged", silent["pdf_path"], "")

loud = html_to_pdf.convert(PAGE, Path(ROOT) / "loud.pdf", prefer="wkhtmltopdf",
                           explicit_path="/usr/bin/wkhtmltopdf",
                           runner=runner_silent(returncode=3, stderr="could not load font"))
check_true("a converter's own complaint is carried into the reason",
           "could not load font" in loud["reason"])

# A non-zero exit that still produced a page is reported as produced with the
# complaint attached: a page that rendered with one warning is more use than a
# refusal, and the reader is told which it is.
warned = html_to_pdf.convert(PAGE, Path(ROOT) / "warned.pdf", prefer="wkhtmltopdf",
                             explicit_path="/usr/bin/wkhtmltopdf",
                             runner=runner_writing(returncode=1, stderr="one bad glyph"))
check_true("a warning with a file is still a success", warned["ok"])
check_true("...but the exit code is reported rather than swallowed",
           "exited 1 but did write" in warned["reason"])
check("...and describe() prints both lines", len(html_to_pdf.describe(warned)), 2)


def runner_timeout(argv, **kwargs):
    raise subprocess.TimeoutExpired(argv, 5)


timed = html_to_pdf.convert(PAGE, Path(ROOT) / "slow.pdf", prefer="wkhtmltopdf",
                            explicit_path="/usr/bin/wkhtmltopdf", timeout=5,
                            runner=runner_timeout)
check_false("a hung converter is abandoned rather than waited on forever", timed["ok"])
check_true("...and the reason says the HTML is unaffected",
           "The HTML report is unaffected" in timed["reason"])


def runner_oserror(argv, **kwargs):
    raise OSError(8, "Exec format error")


broken = html_to_pdf.convert(PAGE, Path(ROOT) / "broken.pdf", prefer="wkhtmltopdf",
                             explicit_path="/usr/bin/wkhtmltopdf", runner=runner_oserror)
check_false("a binary that will not start is a stated failure, not a traceback", broken["ok"])
check_true("...and the OS error is quoted", "Exec format error" in broken["reason"])

# soffice names its own output after the input stem and ignores the name asked
# for. The module moves it, so the caller is not handed a path that is right
# about the directory and wrong about the name.
so_target = Path(ROOT) / "renamed.pdf"


def runner_soffice(argv, **kwargs):
    outdir = Path(argv[argv.index("--outdir") + 1])
    (outdir / (PAGE.stem + ".pdf")).write_bytes(b"%PDF-1.4\n")
    return FakeCompleted(0)


moved = html_to_pdf.convert(PAGE, so_target, prefer="soffice",
                            explicit_path="/usr/bin/soffice", runner=runner_soffice)
check_true("soffice's output is moved to the name that was asked for", moved["ok"])
check("...and the path handed back is that name", moved["pdf_path"], str(so_target))
check_true("the renamed file exists", so_target.is_file())
check_false("...and the name soffice chose is gone",
            (Path(ROOT) / (PAGE.stem + ".pdf")).exists())

# Default output name: beside the HTML, same stem. The three report files already
# share a timestamp stem and the PDF joins them rather than starting a new one.
beside = html_to_pdf.convert(PAGE, prefer="wkhtmltopdf",
                             explicit_path="/usr/bin/wkhtmltopdf", runner=runner_writing())
check("with no path given the pdf lands beside the html, same stem",
      Path(beside["pdf_path"]).name, "advisor_profile_20260822_120000.pdf")


# ============================================================
# 4. No dependency, no download
# ============================================================

print("\nno third-party import, and nothing fetched")

source = open(html_to_pdf.__file__, encoding="utf-8").read()
# `reportlab` and `weasyprint` are named in the module docstring, which is the
# point — it records that both were considered and rejected. What is banned is
# importing them, so the pattern is the import and not the word.
for banned in ("import requests", "urllib.request", "urlopen", "RobustHTTPClient",
               "import reportlab", "import weasyprint", "import fitz", "import pypdf"):
    check(f"the module contains no `{banned}`", banned in source, False)
for banned in ("pandas", "numpy", "openpyxl", "fitz", "matplotlib"):
    check(f"the module does not import {banned}", f"import {banned}" in source, False)
check("...and exposes nothing that fetches",
      [n for n in dir(html_to_pdf)
       if not n.startswith("_") and any(w in n.lower()
                                        for w in ("fetch", "scrape", "crawl", "download"))],
      [])


print(f"\nSummary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
sys.exit(1 if _failed else 0)
