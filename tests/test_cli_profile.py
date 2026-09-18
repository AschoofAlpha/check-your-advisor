#!/usr/bin/env python3
"""
`cmd_profile` wiring: what the subcommand actually writes to disk.

The other suites test the pieces. This one tests the seam — that the HTML lands
beside the Markdown and the JSON, that the figures reach it, that a drawing
layer which will not import costs all seven figures and not the report, and that
a refused corpus draws nothing.

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

import ast
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
from check_your_advisor.profile import charts, html_report, report, roles  # noqa: E402

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


#: Which byline slot each fixture record *means* to be the target researcher's,
#: keyed by PMID. Not part of any record: it is the fixture's stated intent,
#: cross-checked against what `roles.resolve_pi` independently finds in
#: "the fixtures do not bypass resolve_pi" below.
DECLARED_PI_SLOT: dict[str, int] = {}


def paper(pmid: int, authors: list[dict], pub_date: str, pi_slot: int) -> dict:
    """One record in the shape a harvest actually writes.

    No `pi_index` / `pi_evidence` / `pi_ambiguous`. Nothing in the package puts
    those keys on a corpus record — `parse_article` does not produce them and no
    writer adds them — so a fixture carrying them exercises a shape no run
    produces. Worse, `prepare_paper` used to short-circuit `resolve_pi` on any
    record that had one, which made every assertion about locating the target by
    name pass on this fixture whatever name was supplied.

    `pi_slot` is kept as the fixture's declared intent and recorded in
    `DECLARED_PI_SLOT` instead of being written into the record.
    """
    DECLARED_PI_SLOT[str(pmid)] = pi_slot
    return {
        "pmid": str(pmid), "title": f"Study {pmid} of hepatic stellate cell activation",
        "authors": authors, "authors_str": ", ".join(a["name"] for a in authors),
        "journal": "Hepatology Reports", "pub_date": pub_date,
        "pub_year": pub_date.split()[0], "volume": "", "issue": "", "pages": "",
        "doi": "", "pmc_id": "", "abstract": "",
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

    # Counted off the placement table rather than a literal, so adding a figure
    # is one edit in html_report and none here.
    check("every placed figure reaches the page", page.count("<figure "),
          len(html_report.FIGURE_PLACEMENT))
    check("each carries a caption", page.count("<figcaption"),
          len(html_report.FIGURE_PLACEMENT))
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
    check("every figure slot survives", page.count("<figure "),
          len(html_report.FIGURE_PLACEMENT))
    check("each says why it is empty",
          page.count("chart unavailable — matplotlib not installed"),
          len(html_report.FIGURE_PLACEMENT))
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
    # G4: a corpus carrying a record with no structured author list. This is
    # what is left to refuse on — there is nothing to compute over, so there is
    # no degraded version of the report to print. G2 used to stand here and is
    # now a warning; the case below covers it.
    broken = lab_corpus()
    broken["papers"].append({"pmid": "99999", "title": "no bylines", "pub_date": "2023 Jan",
                             "authors": []})
    code, files = run_profile(tmp, broken)

    check("the run exits non-zero", code != 0, True)
    check("the HTML is still written", len(files[".html"]), 1)
    page = read(tmp, files[".html"][0])
    check("with no figure at all", page.count("<svg"), 0)
    check_true("it names the gate", "G4" in page)
    check_true("and prints the observed values", "99999" in page)
    check_true("and no section body survives the refusal",
               "What was deliberately not computed" not in page)
    check("and no raster is left behind either", files[".png"], [])

with tempfile.TemporaryDirectory() as tmp:
    # G2: nothing passed identity verification, so the corpus is "every paper by
    # anyone sharing this name". It used to refuse, which cost the reader every
    # figure and every section — including Section 19, the one that would have
    # shown them whether the corpus really does hold several people. It is now a
    # warning: the page is complete, the figures are drawn, and the exit code is
    # unchanged.
    fallen_back = lab_corpus()
    fallen_back["fallback_fired"] = True
    code, files = run_profile(tmp, fallen_back)

    check("an unverifiable identity still exits non-zero", code, 1)
    page = read(tmp, files[".html"][0])
    check_true("the whole report is rendered", "What was deliberately not computed" in page)
    check_true("...including the section that checks for this exact failure",
               "Co-author clusters" in page)
    check("...and the figures are drawn", page.count("<svg") > 0, True)
    check_true("the warning names the condition", "Warning G2 (identity fallback)" in page)
    check_true("...prints the observed values",
               "fallback_fired=True" in page
               and f"papers_harvested={len(fallen_back['papers'])}" in page)
    check_true("...and the fix", "harvest again" in page)
    check("...in a callout of its own", page.count('class="warnbox"') > 0, True)
    check_true("Section 14 records that this used to refuse",
               "Downgraded on this run" in page)

with tempfile.TemporaryDirectory() as tmp:
    # What used to be gate G1. The harvest pages now, so a shortfall between the
    # esearch count and the PMIDs retrieved is the max_records budget, repeated
    # PMIDs across pages, or PubMed's Count moving mid-harvest — none of which is
    # worth withholding the report. So it is printed in full. It is also warned
    # about and the run exits 1: for one round it did neither, and a corpus 40%
    # retrieved was indistinguishable on the page from one retrieved in full.
    capped = lab_corpus()
    capped["query"].update({"esearch_count": 900, "pmids_returned": 500, "truncated": True,
                            "max_records": 500, "pages_fetched": 1, "duplicates_dropped": 0})
    code, files = run_profile(tmp, capped)

    check("an incomplete harvest exits 1", code, 1)
    page = read(tmp, files[".html"][0])
    check_true("the report is fully rendered", "What was deliberately not computed" in page)
    check_true("and prints retrieved of matched",
               "retrieved 500 of 900 records esearch matched" in page)
    check_true("and says the counts below are floors", "is a floor rather than a value" in page)
    check_true("and carries the coverage warning in a callout",
               "Warning G1 (incomplete harvest)" in page)
    check("...in a callout of its own", page.count('class="warnbox"') > 0, True)


print("\nA mistyped --pi-name is visible on the page and in the exit code")
# `_profile_corpus` lets the config's `author_name` — which is what `--pi-name`
# writes — win over the one the harvest recorded, so a mistyped name is the name
# every downstream metric is computed against. Nothing consulted it: the evidence
# histogram comes off the role string and the OpenAlex share is a record-level id
# count, so both went on printing `N of N` while `roles.resolve_pi` rejected
# every record. The run exited 0 with no line anywhere saying the name had not
# been found, and the only visible difference was one extra person in the roster.
def _harvest_envelope(papers: list[dict]) -> dict:
    """The shape `harvest` writes: a `search` block plus the papers.

    The corpus shape `write_corpus` produces carries its own resolved
    `identity.author_name` and is read straight through, so `--pi-name` never
    reaches it. This is the path a real run takes, and it is the one where
    `_profile_corpus` lets the config's name win over the recorded one.
    """
    return {
        "search": {"esearch_term": '"Chen Xiuying"[Author]', "esearch_matched": len(papers),
                   "pmids_returned": len(papers), "retmax": 500, "max_records": 10000,
                   "pages_fetched": 1, "duplicates_dropped": 0, "years_back": 10,
                   "mindate": "2016/07/22", "maxdate": "2026/07/22",
                   "narrowed_by_affiliation": False, "truncated": False,
                   "fetched": len(papers), "verified": len(papers), "fallback_fired": False,
                   "identity": {"author_name": TARGET, "orcid": ORCID,
                                "affiliation_keywords": ["Nanhai Medical University"],
                                "email_domains": [], "openalex_author_id": "",
                                "require_affiliation": False}},
        "papers": papers,
    }


def _profile_as(directory: str, data: dict, pi_name: str) -> tuple[int, str]:
    source = write_corpus(directory, data)
    code = cli.cmd_profile(["--config", blank_config(directory), "--output-dir", directory,
                            "--papers-json", source, "--pi-name", pi_name])
    for handler in list(logging.getLogger("check_your_advisor").handlers):
        handler.close()
    logging.getLogger("check_your_advisor").handlers.clear()
    pages = [name for name in sorted(os.listdir(directory)) if name.endswith(".html")]
    return code, read(directory, pages[0]) if pages else ""


_HARVEST = _harvest_envelope(lab_corpus()["papers"])
_N_RECORDS = len(_HARVEST["papers"])

with tempfile.TemporaryDirectory() as tmp:
    _code, _page = _profile_as(tmp, _HARVEST, "Nakamura Hiroshi")

    check("a name that reached no record exits 1", _code, 1)
    check_true("the report is still rendered in full",
               "What was deliberately not computed" in _page)
    check_true("the page says the name was located on no record",
               "Warning G7 (target name not located)" in _page)
    check_true("...with the ratio beside it",
               f"target_name_on_records=0/{_N_RECORDS}" in _page)
    check_true("...and the name that was looked for",
               "target_name=Nakamura Hiroshi" in _page)
    check_true("Section 1 prints the ratio as a line of its own",
               f"target name on records: 0 of {_N_RECORDS} harvested record(s)" in _page)
    check("...in a callout of its own", _page.count('class="warnbox"') > 0, True)
    check_true("Section 14 does not claim it used to refuse",
               "Raised on this run" in _page)

with tempfile.TemporaryDirectory() as tmp:
    # The same file under the name it was harvested for: no warning, exit 0, and
    # the ratio printed anyway so the margin is visible on a clean run too.
    _code, _clean = _profile_as(tmp, _HARVEST, TARGET)
    check("the right name exits 0", _code, 0)
    check_true("...and the ratio is printed all the same",
               f"target name on records: {_N_RECORDS} of {_N_RECORDS} harvested record(s) carry"
               in _clean)
    check("...with no warning attached", "Warning G7" in _clean, False)


print("\nA malformed counts block reaches a page, not a traceback")
# `counts.by_source` is read out of the corpus file and had `int()` and `+`
# applied to it unguarded, three lines from `by_evidence` and `name_only`, which
# the same change added and which were guarded. `cli.py` passes a corpus file's
# own `counts` block straight through when the file carries no `search` key, so
# `int("six")` and `"pubmed".get` were one command line away from `main()`,
# which has no handler.
for _label, _counts in (
    ("by_source is a string", "pubmed"),
    ("by_source values are strings", {"pubmed": "six"}),
    ("by_source is a list", ["pubmed"]),
    ("the whole counts block is a string", None),
):
    with tempfile.TemporaryDirectory() as tmp:
        _bad = lab_corpus()
        if _counts is None:
            _bad["counts"] = "six"
        else:
            _bad["counts"] = dict(_bad["counts"], by_source=_counts)
        _raised = ""
        try:
            _code, _files = run_profile(tmp, _bad)
        except BaseException as exc:  # noqa: BLE001 — the point is that nothing escapes
            _code, _files, _raised = None, {}, type(exc).__name__
            # `run_profile` closes the log handler after `cmd_profile` returns and
            # never gets there when it raises. On Windows the open handle then
            # blocks the TemporaryDirectory teardown, and a real regression here
            # would surface as a PermissionError from the `with` block rather than
            # as the failure printed below.
            for _handler in list(logging.getLogger("check_your_advisor").handlers):
                _handler.close()
            logging.getLogger("check_your_advisor").handlers.clear()
        check(f"{_label}: nothing escapes main()", _raised, "")
        check(f"{_label}: ...and a page is written", len(_files.get(".html") or []), 1)
        if _files.get(".html"):
            _bad_page = read(tmp, _files[".html"][0])
            check_true(f"{_label}: ...saying the block was not recorded",
                       "not recorded" in _bad_page)

# The last unguarded key in that same block, found by sweeping the rest of it
# rather than by waiting for a third report. `check_corpus_gates` splatted
# `counts.inconsistent` straight into `str.format`, so a block missing one of the
# three operands raised KeyError and a block carrying a key named `gate_id` or
# `observed` raised TypeError — out of `main()`, from a corpus file, one command
# line away.
_G6_OK = {"fetched": 10, "verified": 30, "rejected_would_be": -20}
for _label, _inc, _expect in (
    ("inconsistent is missing an operand", {"fetched": 10, "verified": 30}, "refusal"),
    ("inconsistent states no operand at all", {"pubmed": 6}, "refusal"),
    ("inconsistent operands are strings",
     {"fetched": "ten", "verified": "thirty", "rejected_would_be": "x"}, "refusal"),
    ("inconsistent carries a key named gate_id", dict(_G6_OK, gate_id="X"), "operands"),
    ("inconsistent carries a key named observed", dict(_G6_OK, observed="X"), "operands"),
    ("inconsistent is a string", "yes", "page"),
    ("inconsistent is a list", ["yes"], "page"),
):
    with tempfile.TemporaryDirectory() as tmp:
        _bad = lab_corpus()
        _bad["counts"] = dict(_bad["counts"], inconsistent=_inc)
        _raised = ""
        try:
            _code, _files = run_profile(tmp, _bad)
        except BaseException as exc:  # noqa: BLE001 — the point is that nothing escapes
            _code, _files, _raised = None, {}, type(exc).__name__
            for _handler in list(logging.getLogger("check_your_advisor").handlers):
                _handler.close()
            logging.getLogger("check_your_advisor").handlers.clear()
        check(f"{_label}: nothing escapes main()", _raised, "")
        check(f"{_label}: ...and a page is written", len(_files.get(".html") or []), 1)
        _bad_page = read(tmp, _files[".html"][0]) if _files.get(".html") else ""
        if _expect == "refusal":
            check(f"{_label}: ...reporting it through the exit code", _code, 1)
            check_true(f"{_label}: ...on a page that names the gate", "G6" in _bad_page)
            check_true(f"{_label}: ...and quotes the block back",
                       "not all present as whole numbers" in _bad_page)
        elif _expect == "operands":
            check(f"{_label}: ...reporting it through the exit code", _code, 1)
            check_true(f"{_label}: ...with the three numbers still stated",
                       "verified=30 against fetched=10" in _bad_page)
        else:
            check(f"{_label}: ...and invents no verdict from it", _code, 0)
            check_true(f"{_label}: ...but names what the file held",
                       "recorded count inconsistency: not recorded" in _bad_page)


print("\n--pi-name reaches a corpus file that carries no search block")
# The report is read against one name, and two places supply it: `--pi-name` on
# the command line and whatever name the harvest that wrote the file was run
# under. `cli._profile_corpus` states the precedence — the config wins, the
# recorded name fills the gap — but only touches a file with a `search` key. A
# file already in the Section 4 shape goes to `build_report` untouched, and the
# precedence there was the other way round, so `--pi-name` was accepted, logged
# and dropped. Warning G7 then measured the name in the file, found it on every
# byline, and the run exited 0 with nothing on the page about the name that had
# actually been typed.
_SECTION_4 = lab_corpus()
_SECTION_4_N = len(_SECTION_4["papers"])
check_true("the fixture is in the Section 4 shape this path takes",
           "search" not in _SECTION_4 and _SECTION_4["identity"]["author_name"] == TARGET)
with tempfile.TemporaryDirectory() as tmp:
    _code, _page = _profile_as(tmp, _SECTION_4, "Nakamura Hiroshi")
    check("a --pi-name on no byline exits 1 on this path too", _code, 1)
    check_true("...and the page says so", "Warning G7 (target name not located)" in _page)
    check_true("...naming the name that was typed", "target_name=Nakamura Hiroshi" in _page)
    check_true("...over the corpus's own denominator",
               f"target_name_on_records=0/{_SECTION_4_N}" in _page)
    # The name is rendered as `<code>` on the page, so the sentence is checked in
    # two halves rather than against a backtick the HTML no longer contains.
    check_true("Section 1 measures the typed name, not the recorded one",
               f"target name on records: 0 of {_SECTION_4_N} harvested record(s) carry "
               "<code>Nakamura Hiroshi</code>" in _page)
    check("...and the recorded name is not what the line is about",
          f"carry <code>{TARGET}</code>" in _page, False)
with tempfile.TemporaryDirectory() as tmp:
    # The control, and the other half of the precedence: with no name anywhere on
    # the command line or in the config, the name in the file is still what the
    # report is about. Written with a config that carries no `author_name` at all,
    # because `blank_config` supplies one and would settle the question before it
    # reached the corpus.
    _nameless_config = os.path.join(tmp, "config.json")
    with open(_nameless_config, "w", encoding="utf-8") as _handle:
        json.dump({"download_pdfs": False}, _handle)
    _source = write_corpus(tmp, _SECTION_4)
    _code = cli.cmd_profile(["--config", _nameless_config, "--output-dir", tmp,
                             "--papers-json", _source])
    for _handler in list(logging.getLogger("check_your_advisor").handlers):
        _handler.close()
    logging.getLogger("check_your_advisor").handlers.clear()
    _page = read(tmp, [n for n in sorted(os.listdir(tmp)) if n.endswith(".html")][0])
    check("no name anywhere falls back to the name the corpus records", _code, 0)
    check("...raising nothing", "Warning G7" in _page, False)
    check_true("...and reading the file under its own name",
               f"target name on records: {_SECTION_4_N} of {_SECTION_4_N} harvested record(s) "
               f"carry <code>{TARGET}</code>" in _page)

# Resolving the name into a local and leaving `identity` as the file wrote it put
# the two halves of the report on different people: the title, G7 and Section 1
# followed `--pi-name`, while `prepare_paper` kept reading `identity["author_name"]`
# and so the roster and the byline slots stayed about the recorded name. The page
# then warned that the typed name touched no record and that "Section 7 has no
# byline slot to report" directly above a Section 7 reporting somebody else's last
# slots. The assertions above never saw it — they only read the banner and Section
# 1, which were the half that had been switched.
#
#
# This ran against a locally stripped copy of `lab_corpus()` while the fixture
# itself still cached a `pi_index` on every record, because a record carrying one
# short-circuited `resolve_pi` in `prepare_paper` and made the check pass under
# any name at all. Both halves of that are gone: `paper()` writes the shape a
# harvest writes, and `prepare_paper` resolves unconditionally. The fixture is
# used directly here, and the cached shape gets its own runs below.
_UNRESOLVED = lab_corpus()
check_true("the fixture used here carries no cached pi_index",
           all("pi_index" not in record for record in _UNRESOLVED["papers"]))
with tempfile.TemporaryDirectory() as tmp:
    _wrong_code, _wrong = _profile_as(tmp, _UNRESOLVED, "Nakamura Hiroshi")
with tempfile.TemporaryDirectory() as tmp:
    _right_code, _right = _profile_as(tmp, _UNRESOLVED, TARGET)

_slots = lambda page, slot: re.search(rf"{slot}: (\d+) of \d+", re.sub(r"<[^>]+>", " ", page))
check("the recorded name locates its own byline slots", _right_code, 0)
check_true("...as last author on most of the corpus",
           int(_slots(_right, "last").group(1)) > 0)
check("a name on no byline raises G7 on this path", _wrong_code, 1)
check_true("...and Section 7 reports no slot for it, as the banner claims",
           int(_slots(_wrong, "last").group(1)) == 0)
check_true("...counting every record as unlocated instead",
           int(_slots(_wrong, "unlocated").group(1)) == len(_UNRESOLVED["papers"]))
# The roster is the other half of the same sentence: Section 2 removes the target
# researcher from it, so a name that matched nobody removes nobody and the count
# goes up by one rather than staying put.
_people = lambda page: int(re.search(r"people found: (\d+)",
                                     re.sub(r"<[^>]+>", " ", page)).group(1))
check_true("...and the roster keeps the person it did not recognise as the target",
           _people(_wrong) == _people(_right) + 1)


print("\nThe G7 banner is true about a corpus that carries a cached pi_index too")
# The banner accepts whatever corpus it is handed, so its sentences have to hold
# for every shape one can arrive in — including the hand-edited file that carries
# resolved-PI fields. `prepare_paper` used to take the cached slot and skip
# `resolve_pi`, so on this shape the report printed "Section 7 has no byline slot
# to report" directly above a Section 7 reporting the *cached* person's 36
# last-author slots out of 38. Two names, one corpus, byte-identical Section 7.
def _with_stale_resolved_pi(data: dict) -> dict:
    """A hand-edited corpus: the slot each record was resolved to under the name
    it was harvested under, left behind when a different name is asked for."""
    staled = {key: value for key, value in data.items() if key != "papers"}
    staled["papers"] = [dict(record, pi_index=DECLARED_PI_SLOT[record["pmid"]],
                             pi_evidence="orcid", pi_ambiguous=False)
                        for record in data["papers"]]
    return staled


_STALE = _with_stale_resolved_pi(lab_corpus())
check_true("the stale-cache fixture carries a pi_index on every record",
           all("pi_index" in record for record in _STALE["papers"]))
with tempfile.TemporaryDirectory() as tmp:
    _stale_code, _stale = _profile_as(tmp, _STALE, "Nakamura Hiroshi")

check("a cached pi_index does not stop G7 firing", _stale_code, 1)
check_true("...and the banner still prints its claim about Section 7",
           "Section 7 has no byline slot to report" in _stale)
check_true("...which Section 7 now bears out: no last-author slot located",
           int(_slots(_stale, "last").group(1)) == 0)
check_true("...counting every record as unlocated, cached slot or not",
           int(_slots(_stale, "unlocated").group(1)) == len(_STALE["papers"]))
check("...so the cache changes no number the banner speaks for",
      _slots(_stale, "last").group(0), _slots(_wrong, "last").group(0))
# The other direction: the cache must not be able to *change* a correct reading
# either, or "ignored" would only mean "ignored when it disagrees".
with tempfile.TemporaryDirectory() as tmp:
    _stale_right_code, _stale_right = _profile_as(tmp, _STALE, TARGET)
check("the right name still exits 0 on the cached shape", _stale_right_code, 0)
check("...reporting the same last-author slots as the uncached corpus",
      _slots(_stale_right, "last").group(0), _slots(_right, "last").group(0))


print("\nThe fixtures cannot bypass resolve_pi")
# The failure this guards against is not a wrong number, it is a test that stops
# testing: with the short-circuit in place every assertion above that depends on
# locating the target by name was a no-op on this file's fixtures, and the G7
# repair of the previous round looked like it had not worked at all. Four
# separate ways of asserting it, because a fixture-only check would go stale the
# moment the branch came back and a code-only check would not notice a new
# fixture that carried the keys. The fifth checks the claim the short-circuit
# was justified by, which was simply untrue.
_IDENTITY = lab_corpus()["identity"]
_FIXTURE_RECORDS = lab_corpus()["papers"] + _SECTION_4["papers"] + _HARVEST["papers"]

# 1. No fixture in this module writes the resolved-PI keys.
check("no fixture record carries a resolved-PI key",
      sorted({key for record in _FIXTURE_RECORDS for key in record
              if key in ("pi_index", "pi_evidence", "pi_ambiguous")}), [])

# 2. `prepare_paper` reaches `resolve_pi` once per record, cache or no cache.
_calls: list[str] = []
_real_resolve_pi = roles.resolve_pi
try:
    roles.resolve_pi = lambda paper, name, identity: (
        _calls.append(str(paper.get("pmid", ""))) or _real_resolve_pi(paper, name, identity))
    for _record in lab_corpus()["papers"]:
        roles.prepare_paper(_record, _IDENTITY)
    _uncached_calls = list(_calls)
    _calls.clear()
    for _record in _STALE["papers"]:
        roles.prepare_paper(_record, _IDENTITY)
    _cached_calls = list(_calls)
finally:
    roles.resolve_pi = _real_resolve_pi

check("prepare_paper resolves every record of the plain fixture",
      _uncached_calls, [record["pmid"] for record in lab_corpus()["papers"]])
check("...and every record of the cached one, rather than reading the cache",
      _cached_calls, [record["pmid"] for record in _STALE["papers"]])

# 3. The fixture's declared slot is what `resolve_pi` independently finds, so the
#    intent `paper()` records is checked rather than asserted.
check("every declared PI slot is the one resolve_pi locates",
      [record["pmid"] for record in lab_corpus()["papers"]
       if roles.resolve_pi(record, TARGET, _IDENTITY)["pi_index"]
       != DECLARED_PI_SLOT[record["pmid"]]], [])

# 4. A cached slot that contradicts the byline loses to the byline. This is the
#    one assertion that fails on the short-circuit no matter what the fixtures
#    look like: it hands `prepare_paper` a record whose cached slot names the
#    wrong person and asks who it reports.
#    Every cached value disagrees with the byline, so reading the cache and
#    resolving the record give different answers on all three fields.
_contradicted = dict(lab_corpus()["papers"][0], pi_index=0, pi_evidence="name_only",
                     pi_ambiguous=True)
_honest = roles.prepare_paper(_contradicted, _IDENTITY)
_resolved = roles.resolve_pi(_contradicted, TARGET, _IDENTITY)
check("the cached slot names a byline entry that is not the target",
      _contradicted["pi_index"] == DECLARED_PI_SLOT[_contradicted["pmid"]], False)
check("a cached slot naming the wrong byline entry is overruled",
      _honest["pi_index"], DECLARED_PI_SLOT[_contradicted["pmid"]])
check("...and the cached evidence tier with it", _honest["pi_evidence"], _resolved["pi_evidence"])
check("...the tier being the one the byline supports", _honest["pi_evidence"], "orcid")
check("...and the cached ambiguity flag too", _honest["pi_ambiguous"], False)
check("...a cached slot for a name on no byline resolves to nothing at all",
      roles.prepare_paper(_contradicted, dict(_IDENTITY,
                                              author_name="Nakamura Hiroshi"))["pi_index"], None)

# 5. The deleted branch was justified by "a corpus written by the profile fetch
#    stage carries these fields". No stage writes them: `parse_article` does not
#    produce the key and no writer adds it, so the branch was reachable only from
#    a hand-edited file. That is the fact which makes ignoring a cached slot a
#    correction rather than a loss, and it is a fact about the package's source,
#    so it is read off the source rather than believed.
_PACKAGE_DIR = os.path.dirname(os.path.abspath(cli.__file__))


def _modules_writing_pi_index() -> list[str]:
    """Every module holding a dict literal or an assignment with a `pi_index` key."""
    writers = set()
    for root, _dirs, names in os.walk(_PACKAGE_DIR):
        if "__pycache__" in root:
            continue
        for name in names:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            for node in ast.walk(tree):
                literal = isinstance(node, ast.Dict) and any(
                    isinstance(key, ast.Constant) and key.value == "pi_index"
                    for key in node.keys)
                assigned = isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == "pi_index"
                    for target in node.targets)
                if literal or assigned:
                    writers.add(os.path.relpath(path, _PACKAGE_DIR).replace("\\", "/"))
    return sorted(writers)


check("the only module that writes a pi_index key is the one that resolves it",
      _modules_writing_pi_index(), ["profile/roles.py"])


print("\nAn old-format corpus is refused on a page, not with a traceback")
# A papers_*.json written by the version that overwrote `verified` with the
# merged corpus size carries verified=30 against fetched=10. The verdict is
# right — every ratio built on those two numbers is wrong — but it used to be
# delivered as an uncaught ValueError out of `cli._corpus_counts`: `main()` has
# no handler, so `profile` died with a traceback, wrote nothing, and returned
# whatever the interpreter returns. It is a refusal page now, like every other
# condition a report cannot be written around.
with tempfile.TemporaryDirectory() as tmp:
    legacy_papers = [
        paper(4000 + i, [author(f"Trainee{i:02d} Person", affiliation=INTERNAL), pi()],
              "2023 Mar", 1)
        for i in range(10)
    ]
    envelope = {
        "search": {"esearch_term": '"Chen Xiuying"[Author]', "esearch_matched": 10,
                   "pmids_returned": 10, "retmax": 500, "max_records": 10000,
                   "pages_fetched": 1, "duplicates_dropped": 0, "years_back": 10,
                   "mindate": "2016/07/22", "maxdate": "2026/07/22",
                   "narrowed_by_affiliation": False, "truncated": False,
                   "fetched": 10, "verified": 30, "fallback_fired": False,
                   "identity": {"author_name": TARGET, "orcid": ORCID,
                                "affiliation_keywords": ["Nanhai Medical University"],
                                "email_domains": [], "openalex_author_id": "",
                                "require_affiliation": False}},
        "papers": legacy_papers,
    }
    source = os.path.join(tmp, "papers_20250101_000000.json")
    with open(source, "w", encoding="utf-8") as handle:
        json.dump(envelope, handle)
    raised = ""
    try:
        code = cli.main(["profile", "--config", blank_config(tmp), "--output-dir", tmp,
                         "--papers-json", source, "--pi-name", TARGET,
                         "--no-citations", "--no-journal-risk"])
    except BaseException as exc:  # noqa: BLE001 — the point is that nothing escapes
        code, raised = None, type(exc).__name__
    for handler in list(logging.getLogger("check_your_advisor").handlers):
        handler.close()
    logging.getLogger("check_your_advisor").handlers.clear()

    check("verified above fetched raises nothing out of main()", raised, "")
    check("...and reports it through the exit code", code, 1)
    produced = sorted(os.listdir(tmp))
    refusal_html = [name for name in produced if name.endswith(".html")]
    check("...having written the refusal page", len(refusal_html), 1)
    refusal = read(tmp, refusal_html[0])
    check_true("...which names the gate", "G6" in refusal)
    check_true("...prints both observed operands",
               "verified=30" in refusal and "fetched=10" in refusal)
    check_true("...names the key the merged size belongs in", "corpus_total" in refusal)
    check_true("...and says what to re-run", "harvest" in refusal)


print("\nA fired gate reaches the process exit code")
# report["exit_code"] is checked elsewhere; what is checked here is the wiring
# from there to the shell — cmd_profile returning it, main() passing it on, and
# run.py handing it to sys.exit. That chain is easy to break without any test
# noticing, and easy to misread from a terminal: `python ... | tail` reports
# tail's status, not the interpreter's, which reads as a silent exit 0.
#
# The corpus here is the one that used to fire gate G3: no identity evidence
# recorded, so it is a name match and nothing more. It is now a warning, and
# the point of running it through a real subprocess is that the exit code did
# **not** change with it — a script branching on `!= 0` behaves as it did, and
# the difference is that the report it exits with is complete.
import json as _json  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

_root = Path(tempfile.mkdtemp(prefix="cya_exit_"))
_json.dump(
    {
        "schema_version": 1,
        "generated_at": "2026-01-01T00:00:00",
        "search": {"esearch_term": "x", "esearch_matched": 3, "pmids_returned": 3,
                   "retmax": 500, "years_back": 5, "truncated": False,
                   "identity": {"orcid": "", "affiliation_keywords": [], "email_domains": []}},
        "papers": [{"pmid": str(i), "title": f"t{i}", "pub_date": f"202{i} Mar",
                    "authors": [{"name": "Test Author", "last": "Test", "fore": "Author",
                                 "initials": "A", "affiliation": "", "email": "",
                                 "is_corresponding": False, "orcid": "", "equal_contrib": False},
                                {"name": "Other Person", "last": "Other", "fore": "Person",
                                 "initials": "P", "affiliation": "", "email": "",
                                 "is_corresponding": False, "orcid": "", "equal_contrib": False}]}
                   for i in range(3)],
    },
    open(_root / "papers_20260101_000000.json", "w", encoding="utf-8"),
    ensure_ascii=False,
)

_run = subprocess.run(
    [sys.executable, str(Path(__file__).resolve().parent.parent / "scripts" / "run.py"),
     "profile", "--pi-name", "Test Author", "--output-dir", str(_root)],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
)
check("an identity warning exits non-zero, exactly as the gate did", _run.returncode, 1)
_audit = sorted(_root.glob("advisor_profile_*.json"))
check("...and still writes the report files", len(_audit), 1)
_page = _json.loads(_audit[-1].read_text(encoding="utf-8"))
check("...which is not refused", _page["refused"], False)
check("...and carries no gate", _page["gate"], None)
check("...but does name the warning", [w["id"] for w in _page["warnings"]], ["G3"])
check("...and the report's own exit code agrees with the shell's",
      _page["exit_code"], _run.returncode)
_md = sorted(_root.glob("advisor_profile_*.md"))[-1].read_text(encoding="utf-8")
check_true("...over a report that was actually written out in full",
           any(line.startswith("## 14.") and "What was deliberately not computed" in line
               for line in _md.splitlines()))


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
    # Added in round four with the two-harvest comparison. Registered here the
    # day it was added, like the two verbs below and unlike `compare`.
    "diff": "cmd_diff",
    # Added with the journal-metric table. It is the whole reason this test is
    # written as a walk over SUBCOMMANDS rather than a fixed list: a verb added
    # to that set and nowhere else fails here, on the same assertion that caught
    # `compare` shipping without a handler.
    "journal-worklist": "cmd_journal_worklist",
    # Added with the public risk signals. It is the second verb this walk has
    # covered from the day it was added rather than the day it broke.
    "journal-risk": "cmd_journal_risk",
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
                            ("journal-worklist", "parse_journal_worklist_args"),
                            ("journal-risk", "parse_journal_risk_args")):
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

# The three hand-filled tables reach the report through `profile` flags, not
# through verbs of their own. A module that exists and a flag that does not is
# the same failure as a verb with no handler: nothing on the page changes and
# nothing says why.
_ext = _cli.parse_profile_args(["--journal-table", "j.csv", "--thesis-roster", "t.csv",
                                "--evaluation-table", "e.csv"])
check("profile accepts --journal-table", _ext.journal_table, "j.csv")
check("profile accepts --thesis-roster", _ext.thesis_roster, "t.csv")
check("profile accepts --evaluation-table", _ext.evaluation_table, "e.csv")
check("all three default to None, which is the 'no table supplied' path",
      (_cli.parse_profile_args([]).journal_table, _cli.parse_profile_args([]).thesis_roster,
       _cli.parse_profile_args([]).evaluation_table),
      (None, None, None))
# Each loader has the same three branches and none of them gates the report.
# "not supplied", "named a file that is not there" and "named a file that will
# not load" are three different sentences, and Section 20 prints whichever one
# happened rather than leaving the reader to guess.
_quiet = logging.getLogger("test.evaluations")
check("no path anywhere means no table, with the reason naming the manual step",
      _cli._load_evaluation_table(None, {}, _quiet)[0], None)
check_true("...and that reason says the collection is the user's own",
           "collect them by hand" in _cli._load_evaluation_table(None, {}, _quiet)[1].lower())
check_true("...and that nothing is fetched for them",
           "no crawler" in _cli._load_evaluation_table(None, {}, _quiet)[1])
check("a config path is used when the flag is absent",
      _cli._resolve_table_path(None, {"evaluations": {"table_path": "cfg.csv"}},
                               "evaluations", "table_path"),
      "cfg.csv")
check("the flag wins over the config path",
      _cli._resolve_table_path("flag.csv", {"evaluations": {"table_path": "cfg.csv"}},
                               "evaluations", "table_path"),
      "flag.csv")
check("the default config carries the third table's path, empty",
      _cli.DEFAULT_CONFIG["evaluations"], {"table_path": ""})

with tempfile.TemporaryDirectory() as tmp:
    check_true("a named file that does not exist is reported by name, not silently skipped",
               "does not exist" in _cli._load_evaluation_table(
                   os.path.join(tmp, "absent.csv"), {}, _quiet)[1])
    bad_eval = os.path.join(tmp, "bad_eval.csv")
    with open(bad_eval, "w", encoding="utf-8") as handle:
        handle.write("导师姓名,评价内容\n某某,还行\n")
    bad_table, bad_note = _cli._load_evaluation_table(bad_eval, {}, _quiet)
    check("an unreadable table is not a gate — the report is still built without it",
          bad_table, None)
    check_true("...and the note names the file and the loader's own complaint",
               bad_eval in bad_note and "评价来源" in bad_note)
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



# ----------------------------------------------------------------------
# `profile` picks up what `cite --percentile` left behind.
#
# Everything either side of this seam was already covered: `impact_reference`
# has its own file, and Section 15 renders whatever it is handed. What was not
# covered is the handing — and twice in this round a seam exactly like it was
# built, run green, and moved nothing, because no assertion crossed it. A
# loader that is never called produces a report that says "not computed"
# forever, which is the most expensive way for a feature to fail: it looks like
# an honest absence.
# ----------------------------------------------------------------------
print("\n--- profile reads the reference cells cite --percentile wrote ---")

_ref_dir = tempfile.mkdtemp()
_ref_payload = {
    "schema_version": 1,
    "generated_at": "2026-09-17T03:00:00",
    "method": "share of works in the cell cited strictly fewer times",
    "min_reference_population": 100,
    "status_reasons": {"no_citation_count": "No citation count was retrieved for this record. "
                                            "That is not a count of zero."},
    "denominator": {"papers_total": 4, "papers_located": 3,
                    "by_status": {"located": 3, "no_citation_count": 1}},
    "records": [], "cells": [], "caveats": {},
}
with open(os.path.join(_ref_dir, "impact_reference_20260917_030000.json"),
          "w", encoding="utf-8") as _handle:
    json.dump(_ref_payload, _handle)

_ref_code, _ = run_profile(_ref_dir, corpus([
    paper(2001, [author("Ke Lin", affiliation=INTERNAL), pi()], "2023 Mar", 1),
    paper(2002, [author("Ke Lin", affiliation=INTERNAL), pi()], "2024 Mar", 1),
]))
_ref_page = open(os.path.join(_ref_dir, sorted(
    n for n in os.listdir(_ref_dir) if n.endswith(".md"))[0]), encoding="utf-8").read()

check_true("the file on disk reaches the report",
           "3 of 4 records sit inside a cell" in _ref_page)
check_true("...with the method it was computed by", "cited strictly fewer times" in _ref_page)
check_true("...and the floor named as resolution, not representativeness",
           "not a claim of representativeness" in _ref_page)
check_true("...and the day it was collected", "2026-09-17" in _ref_page)
# The one that matters most: the unplaced paper gets its own sentence.
check_true("the record that could not be placed says why, in words",
           "That is not a count of zero." in _ref_page)
check_true("...under a heading saying none of the reasons is a low position",
           "none of them is a low position" in _ref_page)
check("profile still succeeds when the file is present", _ref_code, 0)

# And the absence case, which is the common one.
_bare_dir = tempfile.mkdtemp()
run_profile(_bare_dir, corpus([
    paper(2003, [author("Ke Lin", affiliation=INTERNAL), pi()], "2023 Mar", 1),
]))
_bare_page = open(os.path.join(_bare_dir, sorted(
    n for n in os.listdir(_bare_dir) if n.endswith(".md"))[0]), encoding="utf-8").read()
check_true("with no file, the report says it was never fetched",
           "Not computed" in _bare_page and "cite --percentile" in _bare_page)
check_true("...and refuses to let that read as a low count",
           "is not a low count" in _bare_page)



# ----------------------------------------------------------------------
# `profile --chinese-records` merges into the corpus, which is what makes it
# different from the other three hand-filled tables: those feed one section
# each, this one moves every number in the report. A PubMed-only harvest of a
# Chinese advisor can miss most of what they published, and the failure is
# silent — the report looks complete either way. So what is asserted here is
# that the merge happened *and* that the two halves stayed separable.
# ----------------------------------------------------------------------
print("\n--- profile merges a hand-exported Chinese bibliography ---")

_cn_dir = tempfile.mkdtemp()
_cn_csv = os.path.join(_cn_dir, "cn.csv")
with open(_cn_csv, "w", encoding="utf-8-sig", newline="") as _handle:
    _handle.write("篇名,作者,作者拼音,期刊,发表年份,数据来源,数据获取日期\n")
    _handle.write("肝细胞癌的临床研究,林科;陈秀英,Lin Ke;Chen Xiuying,"
                  "中华肝脏病杂志,2023,CNKI,2026-09-17\n")
    _handle.write("胆道疾病综述,林科;陈秀英,Lin Ke;Chen Xiuying,"
                  "中华消化外科杂志,2024,CNKI,2026-09-17\n")

_cn_source = write_corpus(_cn_dir, corpus([
    paper(3001, [author("Ke Lin", affiliation=INTERNAL), pi()], "2023 Mar", 1),
]))
_cn_code = cli.cmd_profile(["--config", blank_config(_cn_dir), "--output-dir", _cn_dir,
                            "--papers-json", _cn_source, "--pi-name", TARGET,
                            "--chinese-records", _cn_csv])
for _handler in list(logging.getLogger("check_your_advisor").handlers):
    _handler.close()
logging.getLogger("check_your_advisor").handlers.clear()

_cn_report = json.load(open(os.path.join(_cn_dir, sorted(
    n for n in os.listdir(_cn_dir) if n.endswith(".json") and n.startswith("advisor_"))[0]),
    encoding="utf-8"))
_cn_denominator = _cn_report["provenance"]["counts"]

check("the merged corpus holds both halves",
      _cn_report["metrics"]["s9"]["denominator"], 3)
check_true("...and the Chinese records are still labelled as their own source",
           "chinese-record" in (_cn_denominator.get("by_source") or {}))
check("...with the PubMed half still counted separately",
      (_cn_denominator.get("by_source") or {}).get("chinese-record"), 2)
# Without the flag the same corpus must stay at one paper. If this ever matches
# the merged number, the merge is happening unasked and every report is wrong.
_bare_cn = tempfile.mkdtemp()
_bare_src = write_corpus(_bare_cn, corpus([
    paper(3001, [author("Ke Lin", affiliation=INTERNAL), pi()], "2023 Mar", 1),
]))
cli.cmd_profile(["--config", blank_config(_bare_cn), "--output-dir", _bare_cn,
                 "--papers-json", _bare_src, "--pi-name", TARGET])
for _handler in list(logging.getLogger("check_your_advisor").handlers):
    _handler.close()
logging.getLogger("check_your_advisor").handlers.clear()
_bare_report = json.load(open(os.path.join(_bare_cn, sorted(
    n for n in os.listdir(_bare_cn) if n.endswith(".json") and n.startswith("advisor_"))[0]),
    encoding="utf-8"))
check("without the flag the corpus is untouched",
      _bare_report["metrics"]["s9"]["denominator"], 1)


# ----------------------------------------------------------------------
# `diff`, whose whole value is the one thing it refuses to let you conclude.
# ----------------------------------------------------------------------
print("\n--- diff splits 'new' on whether the window moved ---")


def _windowed(directory: str, papers: list, mindate: str, maxdate: str) -> None:
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "papers_20240101_000000.json"),
              "w", encoding="utf-8") as handle:
        json.dump({"papers": papers,
                   "search": {"mindate": mindate, "maxdate": maxdate,
                              "years_back": 5, "identity": {}}}, handle)


_base = tempfile.mkdtemp()
_old_d, _new_d = os.path.join(_base, "old"), os.path.join(_base, "new")
_shared = [paper(4001, [author("Ke Lin", affiliation=INTERNAL), pi()], "2019 Mar", 1),
           paper(4002, [author("Ke Lin", affiliation=INTERNAL), pi()], "2020 Mar", 1)]
_windowed(_old_d, _shared, "2019/01/01", "2020/12/31")
_windowed(_new_d, _shared + [
    paper(4003, [author("Ke Lin", affiliation=INTERNAL), pi()], "2022 Mar", 1)],
    "2019/01/01", "2022/12/31")

_diff_out = os.path.join(_base, "diff.json")
_diff_code = cli.main(["diff", _old_d, _new_d, "--out", _diff_out])
for _handler in list(logging.getLogger("check_your_advisor").handlers):
    _handler.close()
logging.getLogger("check_your_advisor").handlers.clear()

check("diff exits 0", _diff_code, 0)
_diff = json.load(open(_diff_out, encoding="utf-8"))
check("one record is in the newer corpus only", _diff["papers"]["n_only_in_new"], 1)
# The assertion this whole verb exists for. One more paper, and none of it is
# growth: the newer harvest simply asked about two more years.
check("...and none of it is growth — it sits outside the shared window",
      _diff["papers"]["by_window"]["only_in_new_inside_shared"], 0)
check("...all of it is in years only the newer harvest asked about",
      _diff["papers"]["by_window"]["only_in_new_outside_shared"], 1)
check_true("the window change is flagged as a change of caliber",
           _diff["caliber_changed"])
check_true("...and the window note is the first thing in notes",
           "window" in _diff["notes"][0].lower())
# It must not editorialise about why a record is on one side only.
_all_text = json.dumps(_diff, ensure_ascii=False).lower()
for _banned in ("retract", "withdraw", "撤稿", "disappear"):
    check(f"diff never says '{_banned}'", _banned in _all_text, False)


print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(1 if _failed else 0)
