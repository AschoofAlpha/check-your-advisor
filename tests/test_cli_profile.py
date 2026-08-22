#!/usr/bin/env python3
"""
`cmd_profile` wiring: what the subcommand actually writes to disk.

The other suites test the pieces. This one tests the seam — that the HTML lands
beside the Markdown and the JSON, that the figures reach it, that a drawing
layer which will not import costs five figures and not the report, and that a
refused corpus draws nothing.

Two rules here are the reason the visual work happened at all and are asserted
against the emitted file rather than against the source:

  - `profile` writes no raster. The removed PNG plotted 277 co-authors on one
    axis at 1934x21506 px, 190 of them a single dot each.
  - the timeline's row count equals the cohort every aggregate is computed over
    (`s5.cohort_denominator`), never the roster size.

All data is synthetic. Fully offline: no network, no PubMed, no config.json.

Run: python tests/test_cli_profile.py
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import json
import logging
import os
import re
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor import cli  # noqa: E402
from check_your_advisor.profile import charts, html_report, report  # noqa: E402

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


# ============================================================
# Fixtures
# ============================================================

TARGET = "Chen Xiuying"
ORCID = "0000-0002-1825-0097"
INTERNAL = "Department of Hepatobiliary Surgery, Nanhai Medical University, Nanhai"


def author(name: str, *, affiliation: str = "", orcid: str = "") -> dict:
    parts = name.split()
    fore = " ".join(parts[1:]) if len(parts) > 1 else ""
    return {"name": name, "last": parts[0] if parts else "", "fore": fore,
            "initials": fore[:1], "affiliation": affiliation, "email": "",
            "orcid": orcid, "equal_contrib": False, "is_corresponding": False}


def paper(pmid: int, authors: list[dict], pub_date: str, pi_index: int) -> dict:
    return {
        "pmid": str(pmid), "title": f"Study {pmid} of hepatic stellate cell activation",
        "authors": authors, "authors_str": ", ".join(a["name"] for a in authors),
        "journal": "Hepatology Reports", "pub_date": pub_date,
        "pub_year": pub_date.split()[0], "volume": "", "issue": "", "pages": "",
        "doi": "", "pmc_id": "", "abstract": "",
        "pi_index": pi_index, "pi_evidence": "orcid", "pi_ambiguous": False,
    }


def corpus(papers: list[dict], **overrides) -> dict:
    data = {
        "schema_version": 1, "generated_at": "2026-07-22T20:47:11",
        "position_filtered": False,
        "query": {"term": '"Chen Xiuying"[Author]', "mindate": "2016/07/22",
                  "maxdate": "2026/07/22", "years_back": 10, "retmax": 500,
                  "esearch_count": len(papers), "pmids_returned": len(papers),
                  "truncated": False},
        "identity": {"author_name": TARGET, "orcid": ORCID,
                     "affiliation_keywords": ["Nanhai Medical University"],
                     "email_domains": [], "require_affiliation_effective": False},
        "counts": {"fetched": len(papers), "verified": len(papers), "name_only": 0,
                   "rejected": 0, "by_evidence": {"orcid": len(papers)}},
        "fallback_fired": False, "papers": papers,
    }
    data.update(overrides)
    return data


def pi() -> dict:
    return author(TARGET, affiliation=INTERNAL, orcid=ORCID)


def lab_corpus() -> dict:
    """A lab with recurring members, one-off co-authors and a senior collaborator.

    Shaped so the three populations the timeline separates are all non-empty:
    without a stratum C and a stratum D the row filter would pass by having
    nothing to filter.
    """
    papers = []
    pmid = 1000
    # Eight recurring members, three records each, one leading its middle year.
    for index in range(8):
        member = author(f"Recur{index:02d} Person", affiliation=INTERNAL)
        for offset, year in enumerate((2019 + index // 4, 2021, 2023)):
            byline = [member, pi()] if offset == 1 else [author(f"Filler{pmid:04d} Person"), member, pi()]
            papers.append(paper(pmid, byline, f"{year} Jun", len(byline) - 1))
            pmid += 1
    # Twelve people who appear exactly once: the population the old chart drew as
    # a single dot each and the spec excludes from every aggregate.
    for index in range(12):
        papers.append(paper(pmid, [author(f"Once{index:02d} Person"), pi()], "2022 Mar", 1))
        pmid += 1
    # One senior collaborator: holds a last-author slot, so stratum D, no row.
    senior = author("Senior Collaborator", affiliation=INTERNAL)
    for year in (2020, 2024):
        papers.append(paper(pmid, [author(f"Junior{pmid:04d} Person"), pi(), senior], f"{year} Jun", 1))
        pmid += 1
    return corpus(papers)


def write_corpus(directory: str, data: dict) -> str:
    path = os.path.join(directory, "papers_20260722_204711.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    return path


def blank_config(directory: str) -> str:
    """An explicit empty config so a config.json in the cwd cannot leak in."""
    path = os.path.join(directory, "config.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"author_name": TARGET, "download_pdfs": False}, handle)
    return path


def run_profile(directory: str, data: dict) -> tuple[int, dict[str, str]]:
    """Run the subcommand exactly as `python -m check_your_advisor profile` does."""
    source = write_corpus(directory, data)
    code = cli.cmd_profile(["--config", blank_config(directory), "--output-dir", directory,
                            "--papers-json", source, "--pi-name", TARGET])
    # `setup_logging` opens a log file in the output directory and keeps the
    # handle; on Windows the directory cannot be removed until it is closed.
    for handler in list(logging.getLogger("check_your_advisor").handlers):
        handler.close()
    logging.getLogger("check_your_advisor").handlers.clear()
    produced = sorted(os.listdir(directory))
    by_suffix = {suffix: [name for name in produced if name.endswith(suffix)]
                 for suffix in (".html", ".md", ".json", ".png", ".csv")}
    return code, by_suffix


def read(directory: str, name: str) -> str:
    with open(os.path.join(directory, name), encoding="utf-8") as handle:
        return handle.read()


# `logging` writes to the real console handler `setup_logging` installs; keep the
# suite readable without suppressing anything a failure would need.
logging.getLogger("check_your_advisor").setLevel(logging.ERROR)


# ============================================================
# The three outputs
# ============================================================

print("\n--- outputs ---")

with tempfile.TemporaryDirectory() as tmp:
    code, files = run_profile(tmp, lab_corpus())
    check("the run exits 0", code, 0)
    check("one HTML report is written", len(files[".html"]), 1)
    check("the Markdown is still written", len(files[".md"]), 1)
    check_true("the JSON is still written",
               any(name.startswith("advisor_profile_") for name in files[".json"]))
    stems = {name.rsplit(".", 1)[0] for name in files[".html"] + files[".md"]
             if name.startswith("advisor_profile_")}
    check("all three share one timestamped stem", len(stems), 1)

    # Acceptance item 1. The defect was a raster, so the assertion is that no
    # raster exists — not that a particular filename is absent.
    check("the run writes no raster of any kind", files[".png"], [])
    check_true("and specifically no student_activity_gantt.png",
               not os.path.exists(os.path.join(tmp, "student_activity_gantt.png")))

    page = read(tmp, files[".html"][0])
    record = json.loads(read(tmp, [n for n in files[".json"] if n.startswith("advisor_")][0]))

    check("five figures reach the page", page.count("<figure "), 5)
    check("each carries a caption", page.count("<figcaption"), 5)
    check("every caption states a k of N", len(re.findall(r"\d+ of \d+", page)) >= 5, True)
    check_true("the person table is rendered", 'id="roster-table"' in page or "<table" in page)
    check_true("Section 0 is on the page", "What this report is and is not" in page)
    check_true("Section 14 is on the page", "What was deliberately not computed" in page)

    # The whole point of a self-contained file: it has to open from a thumb
    # drive. `xmlns` is stripped first — an XML namespace is an identifier that
    # no browser ever fetches, and leaving it in would make this check pass only
    # by being weakened later.
    external = re.findall(r"https?://[^\s\"'<>)]+", re.sub(r'xmlns(:\w+)?="[^"]*"', "", page))
    check("nothing external is referenced", external, [])
    check("and the only URI in the file is the SVG namespace",
          sorted(set(re.findall(r"https?://[^\s\"'<>)]+", page))), ["http://www.w3.org/2000/svg"])
    check_true("no <img> and no <iframe>", "<img" not in page and "<iframe" not in page)

    # Markdown must not still promise an image nobody wrote.
    markdown = read(tmp, files[".md"][0])
    check_true("the Markdown no longer embeds a timeline image",
               "![Person activity timeline]" not in markdown)
    check_true("and says where the timeline is instead",
               "advisor_profile_*.html" in markdown)

# ============================================================
# Geometry, measured on the emitted figure
# ============================================================

print("\n--- timeline geometry ---")

with tempfile.TemporaryDirectory() as tmp:
    _code, files = run_profile(tmp, lab_corpus())
    page = read(tmp, files[".html"][0])
    record = json.loads(read(tmp, [n for n in files[".json"] if n.startswith("advisor_")][0]))
    s2, s5 = record["metrics"]["s2"], record["metrics"]["s5"]

    gantt = re.search(r'<svg[^>]*id="fig-c-gantt"[^>]*>|<svg[^>]*>', page)
    heights = re.findall(r'<svg[^>]+height="([\d.]+)"', page)
    check_true("the timeline SVG is present", gantt is not None)

    rows = page.count('<g class="row">')
    cohort = s2["by_stratum"]["A"] + s2["by_stratum"]["B"]
    # Acceptance item 3: an equality between the figure and the metric dict, so
    # the two cannot drift. Never against a literal.
    check("row count equals stratum A + B", rows, cohort)
    check("row count equals s5.cohort_denominator", rows, s5["cohort_denominator"])
    check("the strata partition the roster", sum(s2["by_stratum"].values()), s2["denominator"])
    check_true("the cohort is a strict subset of the roster", rows < s2["denominator"])

    # Acceptance item 2, asserted as the formula rather than a pixel budget.
    height = float(heights[0])
    check("height is 24 * rows + chrome, chrome <= 140",
          height - charts.ROW_PITCH * rows <= charts.GANTT_CHROME_MAX, True)
    check_true("and the figure is wider than it is tall", height < charts.CANVAS_WIDTH)

    # Acceptance item 4: nobody outside the cohort gets a row.
    excluded = [row["name"] for row in s2["rows"] if row["stratum"] in ("C", "D")]
    # Acceptance item 6: the visible row labels, in order, are the roster's own
    # order restricted to A+B. `</text>` anchors the match to the drawn label
    # rather than the tooltip that repeats it.
    drawn_names = re.findall(r">([^<>]+) — n=\d+ records — [^<>]+</text>", page)
    expected_names = [row["name"] + row["marker"] for row in s2["rows"]
                      if row["stratum"] in ("A", "B")]
    check("the row labels are the roster order restricted to A+B", drawn_names, expected_names)
    check_true("no stratum C or D person has a row",
               not any(f">{name} — n=" in page for name in excluded))
    check_true("the excluded are still named on the page",
               all(name in page for name in excluded[:5]))
    check_true("the target researcher is never a row", f">{TARGET} — n=" not in page)

    # Acceptance item 33: the added per-appearance fields survive the round trip
    # through JSON, which is what lets the figure draw marks instead of bare spans.
    row0 = s2["rows"][0]
    check_true("roster rows carry years, lead_years and first_date",
               all(key in row0 for key in ("years", "lead_years", "first_date")))
    check_true("so the timeline is not the degraded spans-only variant",
               "spans only, per-appearance detail unavailable" not in page)

# ============================================================
# Determinism
# ============================================================

print("\n--- determinism ---")


class _FixedClock(datetime):
    """One clock for both runs, so `generated_at` cannot differ between them.

    That value is stamped into the page body and into the filename stem, so two
    runs landing either side of a second boundary differ by exactly that string.
    It is a real difference and not the one this check is about: the assertion
    exists to catch dict ordering, set iteration and anything else that varies
    between runs over one corpus, all of which are still under test here.
    Unfrozen, it failed at the rate two consecutive runs cross a second — on
    3.10 in CI, on a green tree, while 3.11 and 3.12 passed the same commit.

    Subclassing rather than substituting keeps `fromisoformat` and `strftime`
    working for the callers in this module that read the value back.
    """

    @classmethod
    def now(cls, tz=None):
        return cls(2026, 1, 1, 9, 30, 0)


def _without_output_dir(page: str, directory: str) -> str:
    """Blank out the run's own output directory wherever it appears in the page.

    Section 15 explains a missing `citations_*.json` by printing the directory it
    looked in and the `cite` command that would fill it, which is the right thing
    to tell a reader and the wrong thing to compare byte-for-byte between two
    runs that necessarily use two different temporary directories. The path
    arrives twice over: once as text and once JSON-escaped inside the embedded
    record, so both spellings are replaced. Everything else on the page — dict
    ordering, set iteration, float formatting, the score and its weight table —
    is still compared verbatim.
    """
    for form in (directory, directory.replace("\\", "\\\\"), directory.replace("\\", "/")):
        page = page.replace(form, "<OUTPUT_DIR>")
    return page


with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
    data = lab_corpus()
    real_clock = report.datetime
    report.datetime = _FixedClock
    try:
        run_profile(first, data)
        run_profile(second, data)
    finally:
        report.datetime = real_clock
    pages = [read(directory, [n for n in sorted(os.listdir(directory)) if n.endswith(".html")][0])
             for directory in (first, second)]
    normalised = [_without_output_dir(page, directory)
                  for page, directory in zip(pages, (first, second))]
    check("two runs over one corpus produce byte-identical HTML",
          normalised[0], normalised[1])
    check_true("...and the path substitution actually fired, so this is not vacuous",
               "<OUTPUT_DIR>" in normalised[0])


# ============================================================
# Degraded install: the drawing layer will not import
# ============================================================

print("\n--- missing drawing dependency ---")


class _BlockCharts(importlib.abc.MetaPathFinder):
    """Make `profile.charts` raise as if matplotlib were the missing package.

    A finder rather than `sys.modules[name] = None`: the point is to reproduce
    the failure a reader would actually hit — an absent third-party package —
    including the name the placeholder has to print.
    """

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "check_your_advisor.profile.charts":
            raise ModuleNotFoundError("No module named 'matplotlib'", name="matplotlib")
        return None


with tempfile.TemporaryDirectory() as tmp:
    sys.modules.pop("check_your_advisor.profile.charts", None)
    sys.meta_path.insert(0, _BlockCharts())
    try:
        code, files = run_profile(tmp, lab_corpus())
    finally:
        sys.meta_path.pop(0)
        sys.modules.pop("check_your_advisor.profile.charts", None)

    check("the run still exits 0", code, 0)
    check("the HTML is still written", len(files[".html"]), 1)
    page = read(tmp, files[".html"][0])
    check("all five figure slots survive", page.count("<figure "), 5)
    check("each says why it is empty",
          page.count("chart unavailable — matplotlib not installed"), 5)
    check("and none draws an axis with no data in it", page.count("<svg"), 0)

    # The rest of the report is a text document and must be untouched by this.
    check_true("Section 0 is unaffected", "What this report is and is not" in page)
    check_true("Section 14 is unaffected", "What was deliberately not computed" in page)
    check_true("the person table is unaffected", "Recur00 Person" in page)
    check_true("the caveats are unaffected", page.count("<blockquote") > 10)

    ids = {figure_id for figure_id, _section, _caveats in html_report.FIGURE_PLACEMENT}
    check("the placeholder covers exactly the real figure ids",
          set(cli._figure_placeholders("matplotlib")), ids)
    check("which is the chart module's own id list", ids, set(charts.CHART_IDS))


# ============================================================
# A refused corpus draws nothing
# ============================================================

print("\n--- refusal ---")

with tempfile.TemporaryDirectory() as tmp:
    # G1: esearch found more than it returned, so the corpus is a truncation of
    # unknown size and every count below would be a floor, not a value.
    truncated = lab_corpus()
    truncated["query"]["esearch_count"] = 900
    truncated["query"]["truncated"] = True
    code, files = run_profile(tmp, truncated)

    check("the run exits non-zero", code != 0, True)
    check("the HTML is still written", len(files[".html"]), 1)
    page = read(tmp, files[".html"][0])
    check("with no figure at all", page.count("<svg"), 0)
    check_true("it names the gate", "G1" in page)
    check_true("and prints the observed values", "900" in page)
    check_true("and no section body survives the refusal",
               "What was deliberately not computed" not in page)
    check("and no raster is left behind either", files[".png"], [])


print("\nA fired gate reaches the process exit code")
# report["exit_code"] is checked elsewhere; what is checked here is the wiring
# from there to the shell — cmd_profile returning it, main() passing it on, and
# run.py handing it to sys.exit. That chain is easy to break without any test
# noticing, and easy to misread from a terminal: `python ... | tail` reports
# tail's status, not the interpreter's, which reads as a silent exit 0.
import json as _json  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

_root = Path(tempfile.mkdtemp(prefix="cya_exit_"))
_json.dump(
    {
        "schema_version": 1,
        "generated_at": "2026-01-01T00:00:00",
        # No identity evidence recorded, so gate G3 must fire.
        "search": {"esearch_term": "x", "esearch_matched": 3, "pmids_returned": 3,
                   "retmax": 500, "years_back": 5, "truncated": False,
                   "identity": {"orcid": "", "affiliation_keywords": [], "email_domains": []}},
        "papers": [{"pmid": str(i), "title": f"t{i}", "pub_date": "2024",
                    "authors": [{"pos": 1, "name": "A B", "affiliation": "", "email": ""}]}
                   for i in range(3)],
    },
    open(_root / "papers_20260101_000000.json", "w", encoding="utf-8"),
    ensure_ascii=False,
)

_run = subprocess.run(
    [sys.executable, str(Path(__file__).resolve().parent.parent / "scripts" / "run.py"),
     "profile", "--pi-name", "Test", "--output-dir", str(_root)],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
)
check("a refusal exits non-zero", _run.returncode, 1)
_audit = sorted(_root.glob("advisor_profile_*.json"))
check("...and still writes the report files", len(_audit), 1)
_page = _json.loads(_audit[-1].read_text(encoding="utf-8"))
check("...whose refused flag agrees with the exit code", _page["refused"], True)
check("...and which names the gate", _page["gate"]["id"], "G3")


# ----------------------------------------------------------------------
# Every advertised subcommand resolves to something callable.
#
# `compare` shipped with its parser written, its name in SUBCOMMANDS, its branch
# in main() and its renderer in report.py — and no `cmd_compare` at all. The
# whole suite stayed green because the argument parser was tested directly and
# nothing ever walked the dispatch. `run.py compare` died on NameError at the
# first real use.
#
# Testing the parsers is not testing the seam. This walks SUBCOMMANDS itself, so
# a subcommand added to that set without a handler fails here rather than in a
# user's terminal.
# ----------------------------------------------------------------------

# ----------------------------------------------------------------------
# Every module imports on its own, from a cold start.
#
# `theses.py` did not. `from .profile.metrics import ...` at module level runs
# `profile/__init__.py`, which imports html_report, which imports report, which
# imports theses — so `import check_your_advisor.theses` as the first import of
# the process raised ImportError on a partially initialised module. Nothing
# caught it because no caller ever reached theses first: report.py is inside the
# package, and test_theses.py imports `profile.metrics` on the line above its
# `theses` import, which loads the package and hides the cycle. The bug was
# reachable by anyone writing a script that imported theses and nothing else.
#
# sys.modules is cleared between imports because within one process the first
# import primes every module after it, and a test that does not clear it passes
# whatever the import graph looks like.
# ----------------------------------------------------------------------
print("\nCold-start imports")

import importlib  # noqa: E402
import pkgutil  # noqa: E402

import check_your_advisor as _pkg  # noqa: E402

_MODULES = sorted(
    name for _, name, _ in pkgutil.walk_packages(_pkg.__path__, f"{_pkg.__name__}.")
    if "__" not in name
)
check("the package has modules to check", len(_MODULES) >= 15, True)

for _module in _MODULES:
    for _loaded in [k for k in sys.modules if k.startswith("check_your_advisor")]:
        del sys.modules[_loaded]
    try:
        importlib.import_module(_module)
        check(f"{_module.split('.', 1)[1]} imports from a cold start", True, True)
    except ImportError as _exc:
        check(f"{_module.split('.', 1)[1]} imports from a cold start", str(_exc), "no ImportError")

# Restore the modules this file's later assertions rely on.
for _loaded in [k for k in sys.modules if k.startswith("check_your_advisor")]:
    del sys.modules[_loaded]
from check_your_advisor import cli as _reimported_cli  # noqa: E402,F401

print("\nSubcommand dispatch")

from check_your_advisor import cli as _cli  # noqa: E402

_HANDLERS = {
    "harvest": "cmd_fetch",       # the default verb; harvest is its public name
    "fetch": "cmd_fetch",
    "profile": "cmd_profile",
    "cite": "cmd_cite",
    "compare": "cmd_compare",
    # Added with the journal-metric table. It is the whole reason this test is
    # written as a walk over SUBCOMMANDS rather than a fixed list: a verb added
    # to that set and nowhere else fails here, on the same assertion that caught
    # `compare` shipping without a handler.
    "journal-worklist": "cmd_journal_worklist",
    "download": "cmd_download",
    "clean-cache": "cmd_clean_cache",
}

check("every SUBCOMMANDS entry has a declared handler",
      sorted(_cli.SUBCOMMANDS), sorted(_HANDLERS))
for _verb, _handler in sorted(_HANDLERS.items()):
    check(f"{_verb} -> {_handler} exists and is callable",
          callable(getattr(_cli, _handler, None)), True)

# _split_subcommand decides which handler main() reaches. A verb that parses but
# routes to the default would silently run a harvest instead.
for _verb in sorted(_cli.SUBCOMMANDS):
    _cmd, _rest = _cli._split_subcommand([_verb, "--log-level", "ERROR"])
    check(f"{_verb} survives _split_subcommand as itself", _cmd, _verb)
    check(f"{_verb} keeps its trailing args", _rest, ["--log-level", "ERROR"])

# Each parser must accept its own --help without reaching into another verb's
# namespace. SystemExit(0) is argparse's success path for --help.
for _verb, _parser_name in (("cite", "parse_cite_args"), ("compare", "parse_compare_args"),
                            ("journal-worklist", "parse_journal_worklist_args")):
    _parser = getattr(_cli, _parser_name, None)
    if not callable(_parser):
        check(f"{_parser_name} exists", False, True)
        continue
    try:
        _parser(["--help"])
        check(f"{_verb} --help exits", "no exit", "SystemExit(0)")
    except SystemExit as _exc:
        check(f"{_verb} --help exits cleanly", _exc.code, 0)

# compare's repeatable flags: one value for all, one per corpus, or refused.
check("--config spread over 3 corpora from 1 value",
      _cli._per_corpus(["a.json"], 3, "--config"), ["a.json"] * 3)
check("--config spread over 3 corpora from 3 values",
      _cli._per_corpus(["a", "b", "c"], 3, "--config"), ["a", "b", "c"])
check("no value spreads to None, not to an empty list",
      _cli._per_corpus(None, 2, "--config"), [None, None])
try:
    # 2 values for 3 corpora would otherwise score corpus three under corpus
    # two's weights, which the reader cannot see from the page.
    _cli._per_corpus(["a", "b"], 3, "--config")
    check("a count that is neither 1 nor N is refused", "no exception", "ValueError")
except ValueError:
    check("a count that is neither 1 nor N is refused", "ValueError", "ValueError")

# The two hand-filled tables reach the report through `profile` flags, not
# through verbs of their own. A module that exists and a flag that does not is
# the same failure as a verb with no handler: nothing on the page changes and
# nothing says why.
_ext = _cli.parse_profile_args(["--journal-table", "j.csv", "--thesis-roster", "t.csv"])
check("profile accepts --journal-table", _ext.journal_table, "j.csv")
check("profile accepts --thesis-roster", _ext.thesis_roster, "t.csv")
check("both default to None, which is the 'no table supplied' path",
      (_cli.parse_profile_args([]).journal_table, _cli.parse_profile_args([]).thesis_roster),
      (None, None))
# The worklist verb writes the template the journal table is filled into. Its
# --out is optional; defaulting it inside the parser would hide the timestamp.
check("journal-worklist takes --out", _cli.parse_journal_worklist_args(["--out", "w.csv"]).out,
      "w.csv")
check("...and defaults it to None so cmd_journal_worklist can date the filename",
      _cli.parse_journal_worklist_args([]).out, None)

# --output-dir has to mean the same thing on every verb. `apply_cli_overrides`
# pulls pdf_dir and cache_db along with an overridden output dir; cmd_download
# and cmd_clean_cache wrote their own override loops and skipped it, so
# `harvest --output-dir X` cached into X while `download --output-dir X` kept
# reading pubmed_results/paper_cache.db — the resume verb consulting a cache that
# had never seen the corpus it was resuming.
print("\n--output-dir carries the cache with it, on every verb")

_defaults = dict(_cli.DEFAULT_CONFIG)
check("an overridden output dir takes the cache with it",
      _cli._sibling_of_output_dir(_defaults, "cache_db", None, "out", "paper_cache.db"),
      os.path.join("out", "paper_cache.db"))
check("...and the pdf dir too",
      _cli._sibling_of_output_dir(_defaults, "pdf_dir", None, "out", "pdfs"),
      os.path.join("out", "pdfs"))
check("an explicit --cache-db still wins",
      _cli._sibling_of_output_dir(_defaults, "cache_db", "chosen.db", "out", "paper_cache.db"),
      "chosen.db")
check("...and so does a path set in the config file",
      _cli._sibling_of_output_dir({"cache_db": "configured/c.db"}, "cache_db", None, "out",
                                  "paper_cache.db"),
      "configured/c.db")
check("with nothing overridden the default location is unchanged",
      os.path.normpath(_cli._sibling_of_output_dir(
          _defaults, "cache_db", None, _defaults["output_dir"], "paper_cache.db")),
      os.path.normpath(_defaults["cache_db"]))
# The property that actually matters: harvest and download, given the same
# --output-dir, have to name the same cache file.
_harvest_cfg = _cli.apply_cli_overrides(
    dict(_defaults), _cli.parse_fetch_args(["--output-dir", "shared"]))
check("harvest and download agree on where the cache is",
      _cli._sibling_of_output_dir(dict(_defaults), "cache_db", None, "shared", "paper_cache.db"),
      _harvest_cfg["cache_db"])


print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(1 if _failed else 0)
