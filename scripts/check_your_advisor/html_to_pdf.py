"""
报告 HTML → PDF：用本机已有的转换器，没有就说清楚怎么装
====================================================
Turn the profile report's HTML into a PDF using whatever converter the machine
already has, and say plainly what to install when it has none.

**There is no PDF writer in this module and no third-party dependency behind
it.** The standard library cannot lay out a page, and the honest options were:
hand-roll a PDF writer that would render a subset of the report and silently
lose the rest, or add reportlab / weasyprint as a hard dependency and stop a
fresh clone from running. Both are worse than shelling out to a converter that
is either present or not, and saying which.

What this means for the caller, stated because it is the whole contract:

- **The HTML output never changes.** `html_report.write_html` runs first and its
  file is the deliverable. A PDF is a second copy of it, produced from that file
  and nothing else, so a failure here costs a convenience and no information.
- **A missing converter is not an error.** `convert` returns `ok: False` with a
  reason and the install lines for this platform. Nothing raises, nothing exits
  non-zero, and `cmd_profile` prints the lines and carries on.
- **Nothing is downloaded.** This module runs a program that is already on the
  machine. It never fetches an installer and never suggests piping one to a
  shell.

Why several converters rather than one. The report is a single self-contained
file with inline SVG and no network reference, which is the easy case for every
engine here — but which engine exists depends entirely on the machine, and a
tool that only knew `wkhtmltopdf` would tell most users to install something
they do not need. They are tried in the order below; the first one found wins,
and which one produced the file is returned so a page that came out of
LibreOffice is not mistaken for one that came out of Chrome.

  1. wkhtmltopdf  — purpose-built, smallest install, honours @page and @media print
  2. Chrome / Chromium / Edge headless — already on most machines
  3. weasyprint   — a CLI on PATH; the module never imports it
  4. soffice      — LibreOffice, the weakest renderer here and the last resort

Standard library only: `shutil.which`, `subprocess.run`, `tempfile`, `pathlib`.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("check_your_advisor.pdf_export")

__all__ = [
    "CONVERTERS",
    "CONVERTER_ORDER",
    "DEFAULT_TIMEOUT",
    "Converter",
    "available_converters",
    "convert",
    "describe",
    "find_converter",
    "install_hint_lines",
]

#: Seconds before a conversion is abandoned. A headless browser that cannot
#: start hangs rather than exiting, and an unbounded wait in a report command is
#: indistinguishable from a crash.
DEFAULT_TIMEOUT = 120

CONVERTER_WKHTMLTOPDF = "wkhtmltopdf"
CONVERTER_CHROME = "chrome"
CONVERTER_WEASYPRINT = "weasyprint"
CONVERTER_SOFFICE = "soffice"

#: Tried in this order, first found wins. Coverage and fidelity, not a judgement
#: about the projects.
CONVERTER_ORDER: tuple[str, ...] = (
    CONVERTER_WKHTMLTOPDF,
    CONVERTER_CHROME,
    CONVERTER_WEASYPRINT,
    CONVERTER_SOFFICE,
)


@dataclass(frozen=True)
class Converter:
    """One way of turning an HTML file into a PDF.

    `executables` are looked for on PATH in order; `extra_paths` are absolute
    locations checked afterwards, because Chrome and LibreOffice are routinely
    installed without going on PATH on Windows and macOS and a probe that only
    consulted PATH would report "not installed" on a machine that has both.
    """

    name: str
    executables: tuple[str, ...]
    install: tuple[str, ...]
    extra_paths: tuple[str, ...] = ()
    #: Set when the converter writes its output next to the input under a name
    #: it chooses, so the caller has to move the result into place afterwards.
    writes_to_outdir: bool = False
    notes: str = ""


CONVERTERS: dict[str, Converter] = {
    CONVERTER_WKHTMLTOPDF: Converter(
        name=CONVERTER_WKHTMLTOPDF,
        executables=("wkhtmltopdf",),
        install=(
            "Windows: winget install wkhtmltopdf.wkhtmltox  (or download from wkhtmltopdf.org)",
            "macOS:   brew install --cask wkhtmltopdf",
            "Debian/Ubuntu: sudo apt install wkhtmltopdf",
        ),
        notes="honours the report's @page margins and @media print rules",
    ),
    CONVERTER_CHROME: Converter(
        name=CONVERTER_CHROME,
        executables=("chrome", "google-chrome", "google-chrome-stable", "chromium",
                     "chromium-browser", "msedge"),
        install=(
            "Any Chrome, Chromium or Edge install works; no extra package is needed.",
            "If one is installed but not on PATH, pass its full path with --pdf-converter-path.",
        ),
        extra_paths=(
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/usr/bin/chromium-browser",
            "/usr/bin/google-chrome",
        ),
        notes="already present on most machines; collapsed <details> print open "
              "because the page reopens them on beforeprint",
    ),
    CONVERTER_WEASYPRINT: Converter(
        name=CONVERTER_WEASYPRINT,
        executables=("weasyprint",),
        install=(
            "pipx install weasyprint  (or pip install weasyprint in an environment of your own)",
            "This package never imports it — it is run as a program, like the other three.",
        ),
        notes="runs no JavaScript, so the roster filter controls are absent from the PDF; "
              "every row is still there, because Python rendered them",
    ),
    CONVERTER_SOFFICE: Converter(
        name=CONVERTER_SOFFICE,
        executables=("soffice", "libreoffice"),
        install=(
            "Windows: winget install TheDocumentFoundation.LibreOffice",
            "macOS:   brew install --cask libreoffice",
            "Debian/Ubuntu: sudo apt install libreoffice-writer",
        ),
        extra_paths=(
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
            "/Applications/LibreOffice.app/Contents/MacOS/soffice",
            "/usr/bin/soffice",
        ),
        writes_to_outdir=True,
        notes="the weakest renderer here — inline SVG and CSS grid come out approximate; "
              "use it only when nothing else is installed",
    ),
}


def _resolve(converter: Converter) -> str:
    """The executable path for one converter, or "" if the machine has none."""
    for name in converter.executables:
        found = shutil.which(name)
        if found:
            return found
    for path in converter.extra_paths:
        if os.path.isfile(path):
            return path
    return ""


def find_converter(prefer: str = "", explicit_path: str = "") -> dict[str, str]:
    """The converter this machine will use, as `{"name", "path"}`; empty when none.

    `explicit_path` is honoured without probing anything: a user who names a
    binary has said which one they mean, and second-guessing them is how a tool
    ends up using the Edge on PATH instead of the Chrome they pointed at. It is
    paired with `prefer` when given, and falls back to the Chrome argv otherwise
    because that is the family a hand-supplied browser path is nearly always in.
    """
    if explicit_path:
        name = prefer if prefer in CONVERTERS else CONVERTER_CHROME
        return {"name": name, "path": explicit_path}
    order = (prefer,) + CONVERTER_ORDER if prefer in CONVERTERS else CONVERTER_ORDER
    seen: set[str] = set()
    for name in order:
        if name in seen:
            continue
        seen.add(name)
        path = _resolve(CONVERTERS[name])
        if path:
            return {"name": name, "path": path}
    return {}


def available_converters() -> list[dict[str, str]]:
    """Every converter present on this machine, in `CONVERTER_ORDER`.

    Reported rather than only the winner, because "wkhtmltopdf produced this"
    and "LibreOffice produced this" are different claims about the fidelity of
    the page and a reader is entitled to know a better one was available.
    """
    found = []
    for name in CONVERTER_ORDER:
        path = _resolve(CONVERTERS[name])
        if path:
            found.append({"name": name, "path": path})
    return found


def install_hint_lines() -> list[str]:
    """What to install, one line each, ordered like `CONVERTER_ORDER`.

    Platform-first: the lines for the running platform come first so the reader
    does not have to pick their own out of twelve. The others stay, because this
    text also lands in a report that gets mailed to somebody on another machine.
    """
    system = platform.system()
    marker = {"Windows": "Windows:", "Darwin": "macOS:"}.get(system, "Debian/Ubuntu:")
    lines = [
        "No HTML-to-PDF converter was found on this machine. The HTML report is complete and "
        "unchanged — a PDF is a second copy of it, not a different document.",
        "Install any one of these, then re-run with --pdf:",
    ]
    for name in CONVERTER_ORDER:
        converter = CONVERTERS[name]
        mine = [line for line in converter.install if line.startswith(marker)]
        rest = [line for line in converter.install if not line.startswith(marker)]
        lines.append(f"- {name} — {converter.notes}")
        lines += [f"    {line}" for line in mine + rest]
    lines.append(
        "Nothing is downloaded for you. This package runs a program that is already installed and "
        "never fetches an installer."
    )
    return lines


def _argv(name: str, executable: str, html_path: Path, pdf_path: Path,
          workdir: Path) -> list[str]:
    """The command line for one converter. Pure — builds a list, runs nothing."""
    if name == CONVERTER_WKHTMLTOPDF:
        # --enable-local-file-access is required even though the page fetches
        # nothing: wkhtmltopdf blocks `file://` reads by default and the report
        # is opened as a local file.
        return [executable, "--quiet", "--enable-local-file-access",
                str(html_path), str(pdf_path)]
    if name == CONVERTER_CHROME:
        # A throwaway user-data-dir, like the isolated profile soffice needs
        # below: headless Chrome refuses to start against a profile a visible
        # Chrome already holds, which fails silently in the middle of a report.
        return [executable, "--headless=new", "--disable-gpu", "--no-sandbox",
                "--no-first-run", "--no-pdf-header-footer",
                f"--user-data-dir={workdir}",
                f"--print-to-pdf={pdf_path}", html_path.as_uri()]
    if name == CONVERTER_WEASYPRINT:
        return [executable, str(html_path), str(pdf_path)]
    # soffice writes <input stem>.pdf into --outdir and ignores any name given,
    # so the caller moves the result. -env:UserInstallation is not optional: a
    # soffice sharing the default profile with a running instance exits 0 having
    # written nothing at all, which is the worst failure shape available here.
    return [executable, "--headless", "--norestore",
            f"-env:UserInstallation={workdir.as_uri()}",
            "--convert-to", "pdf", "--outdir", str(pdf_path.parent), str(html_path)]


def convert(
    html_path: str | Path,
    pdf_path: str | Path | None = None,
    prefer: str = "",
    explicit_path: str = "",
    timeout: int = DEFAULT_TIMEOUT,
    runner: Any = None,
) -> dict[str, Any]:
    """Convert one HTML file to PDF. Never raises; returns what happened.

    The result always carries `ok`, and on failure a `reason` naming which of
    the four things went wrong — no converter, missing input, the converter
    failed, or it exited 0 without writing a file. That last case is the reason
    the file is checked afterwards rather than the exit code trusted: a
    LibreOffice sharing a profile with a running instance exits 0 having written
    nothing, and a caller that believed the return code would report a PDF that
    does not exist.

    `runner` is `subprocess.run` unless a test supplies its own. It is the only
    seam in this module, and it is here so the argv, the timeout handling and
    the after-the-fact file check are all testable without a browser installed.
    """
    run = runner or subprocess.run
    source = Path(html_path)
    target = Path(pdf_path) if pdf_path else source.with_suffix(".pdf")
    result: dict[str, Any] = {
        "ok": False, "pdf_path": "", "html_path": str(source), "converter": "",
        "converter_path": "", "argv": [], "returncode": None, "stderr": "",
        "reason": "", "hint_lines": [],
    }

    if not source.is_file():
        result["reason"] = f"{source} does not exist, so there was nothing to convert."
        return result

    chosen = find_converter(prefer=prefer, explicit_path=explicit_path)
    if not chosen:
        result["reason"] = "no HTML-to-PDF converter is installed on this machine."
        result["hint_lines"] = install_hint_lines()
        return result

    result["converter"] = chosen["name"]
    result["converter_path"] = chosen["path"]
    converter = CONVERTERS[chosen["name"]]
    target.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="cya-pdf-") as workdir:
        argv = _argv(chosen["name"], chosen["path"], source, target, Path(workdir))
        result["argv"] = list(argv)
        logger.debug("PDF 转换命令: %s", " ".join(argv))
        try:
            completed = run(argv, capture_output=True, text=True, timeout=timeout,
                            encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            result["reason"] = (f"{chosen['name']} did not finish within {timeout}s and was "
                                f"abandoned. The HTML report is unaffected.")
            return result
        except OSError as exc:
            result["reason"] = f"{chosen['name']} could not be started: {exc}"
            return result

        result["returncode"] = getattr(completed, "returncode", None)
        result["stderr"] = (getattr(completed, "stderr", "") or "").strip()[:600]

        if converter.writes_to_outdir:
            # soffice names the output after the input stem and ignores the name
            # asked for, so the file is moved into place here rather than the
            # caller being handed a path that is right about the directory and
            # wrong about the name.
            produced = target.parent / (source.stem + ".pdf")
            if produced.exists() and produced != target:
                produced.replace(target)

    if not target.is_file():
        result["reason"] = (
            f"{chosen['name']} exited {result['returncode']} and wrote no file to {target}."
            + (f" It said: {result['stderr']}" if result["stderr"] else "")
        )
        return result
    if result["returncode"] not in (0, None):
        # A file exists but the converter complained. Reported as produced, with
        # the complaint attached: a page that rendered with one warning is more
        # use than a refusal, and the reader is told which it is.
        result["reason"] = (f"{chosen['name']} exited {result['returncode']} but did write "
                            f"{target}; check the page before circulating it.")
    result["ok"] = True
    result["pdf_path"] = str(target)
    return result


def describe(result: dict[str, Any]) -> list[str]:
    """One conversion result as log lines, ready to print verbatim.

    Kept here rather than in `cli.py` so the wording of a failure is written once
    beside the code that produced it, in the same style as the loaders that print
    the reason a hand-filled table is absent.
    """
    if not isinstance(result, dict):
        return []
    if result.get("ok"):
        lines = [f"PDF 已生成: {result['pdf_path']}（转换器 {result['converter']}，"
                 f"{result['converter_path']}）"]
        if result.get("reason"):
            lines.append(f"注意: {result['reason']}")
        return lines
    return [f"未生成 PDF: {result.get('reason', '')}"] + list(result.get("hint_lines") or [])
