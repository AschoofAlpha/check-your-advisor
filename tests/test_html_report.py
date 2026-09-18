#!/usr/bin/env python3
"""
Structural tests for the self-contained HTML profile report.

These check the properties that make the page a disclosure document rather than
a dashboard, in roughly the order a reader loses by their absence:

- one file, no network reference of any kind, so it opens from file:// forever;
- Section 0 and Section 14 reachable without a click, because they are the
  qualifications on every number between them;
- every caveat in the report dict present as visible page text, outside the
  embedded JSON and outside every <details>;
- a person whose PubMed name is `<script>alert(1)</script>` rendered as text;
- the embedded JSON parses.

Fully offline: synthetic fixtures, no network, no matplotlib, no pytest.

Run: python tests/test_html_report.py
"""

from __future__ import annotations

import html as html_module
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor.profile import caveats, html_report, report  # noqa: E402
from check_your_advisor.profile.html_report import render_html  # noqa: E402

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


# ============================================================
# Fixtures
# ============================================================

TARGET = "Chen Xiuying"
# ORCID's own published example identifier for the fictional Josiah Carberry,
# matching tests/test_profile.py. No real person is named in this file.
ORCID = "0000-0002-1825-0097"
INTERNAL = "Department of Hepatobiliary Surgery, Nanhai Medical University, Nanhai"
FIXED_NOW = datetime(2026, 7, 22, 20, 47, 11)

# The name a naive renderer breaks on. It is a legal PubMed author string: the
# field is free text, and nothing upstream rejects markup.
HOSTILE_NAME = "<script>alert(1)</script>"
HOSTILE_AFFILIATION = 'Institute of "Quotes" & <Angles>, Beihai'


def author(name, *, affiliation="", email="", orcid=""):
    parts = name.split()
    fore = " ".join(parts[1:]) if len(parts) > 1 else ""
    return {
        "name": name,
        "last": parts[0] if parts else "",
        "fore": fore,
        "initials": fore[:1] if fore else "",
        "affiliation": affiliation,
        "email": email,
        "orcid": orcid,
        "equal_contrib": False,
        "is_corresponding": bool(email),
    }


def pi(affiliation=INTERNAL):
    return author(TARGET, affiliation=affiliation, orcid=ORCID)


def paper(pmid, authors, pub_date="2023 Jun", *, title=None, journal="Hepatology Reports"):
    return {
        "pmid": str(pmid),
        "title": title if title is not None else f"Study {pmid} of hepatic stellate cells",
        "authors": authors,
        "authors_str": ", ".join(a["name"] for a in authors),
        "journal": journal,
        "pub_date": pub_date,
        "pub_year": pub_date.split()[0],
        "volume": "", "issue": "", "pages": "", "doi": "", "pmc_id": "", "abstract": "",
        "pi_index": len(authors) - 1,
        "pi_evidence": "orcid",
        "pi_ambiguous": False,
    }


def corpus(papers, **overrides):
    data = {
        "schema_version": 1,
        "generated_at": "2026-07-22T20:47:11",
        "position_filtered": False,
        "query": {
            "term": '"Chen Xiuying"[Author]',
            "mindate": "2018/07/22", "maxdate": "2026/07/22",
            "years_back": 8, "retmax": 500,
            "esearch_count": len(papers), "pmids_returned": len(papers), "truncated": False,
        },
        "identity": {
            "author_name": TARGET,
            "orcid": ORCID,
            "affiliation_keywords": ["Nanhai Medical University"],
            "email_domains": ["@nanhai-med.example.edu"],
            "require_affiliation_effective": False,
        },
        "counts": {
            "fetched": len(papers), "verified": len(papers), "name_only": 2, "rejected": 0,
            "by_evidence": {"orcid": len(papers)},
        },
        "fallback_fired": False,
        "papers": papers,
    }
    data.update(overrides)
    return data


def build(papers, **overrides):
    return report.build_report(corpus(papers, **overrides), {}, None, FIXED_NOW)


# A corpus with recurring people (so the roster has A and B strata), several
# single-appearance people (stratum C), and one person whose name is markup.
def make_report():
    papers = []
    for index in range(6):
        # Two people who recur, one of them leading, so strata A and B both fill.
        papers.append(paper(
            2000 + index,
            [author(f"Recur{index % 2:02d} Person", affiliation=INTERNAL),
             author("Support00 Person", affiliation=INTERNAL),
             pi()],
            pub_date=f"{2019 + index} Mar",
        ))
    for index in range(4):
        papers.append(paper(
            3000 + index,
            [author(f"Once{index:02d} Person", affiliation=INTERNAL), pi()],
            pub_date=f"{2021 + index} Sep",
        ))
    papers.append(paper(
        3100,
        [author(HOSTILE_NAME, affiliation=HOSTILE_AFFILIATION), pi()],
        pub_date="2022 Feb",
        title='A title with <b>markup</b> & an ampersand, "quoted"',
    ))
    # Three records, so the hostile affiliation string clears
    # AFFILIATION_MIN_PAPERS and is printed verbatim in Section 12. That is the
    # one place a raw PubMed free-text field reaches the page unsummarised.
    for pmid, date in ((3101, "2023 Feb"), (3102, "2024 Feb")):
        papers.append(paper(
            pmid,
            [author(HOSTILE_NAME, affiliation=HOSTILE_AFFILIATION), pi()],
            pub_date=date,
        ))
    return build(papers)


# Chart artifacts in the shape docs/profile-visual-spec.md Section 3 defines.
# The svg deliberately carries the SVG namespace declaration, because that is
# what a real hand-emitted figure looks like and the external-reference scan has
# to survive it.
def fake_chart(figure_id, rows):
    return {
        "svg": (
            f'<svg xmlns="http://www.w3.org/2000/svg" role="img" width="1100" height="200" '
            f'aria-labelledby="{figure_id}-t {figure_id}-d">'
            f"<title id=\"{figure_id}-t\">{figure_id}</title>"
            f"<desc id=\"{figure_id}-d\">77 of 277 people plotted.</desc>"
            f"<text x=\"10\" y=\"20\">Recur00 Person</text></svg>"
        ),
        "caption": f"{figure_id}: 77 of 277 people.",
        "desc": "77 of 277 people plotted.",
        "rows": rows,
        "drawn": True,
    }


CHARTS = {
    "C-GANTT": fake_chart("C-GANTT", [{"name": "Recur00 Person", "years": [2019, 2020]}]),
    "C-LAG": fake_chart("C-LAG", [{"name": "Recur00 Person", "lag_years": 0}]),
    "C-SPAN": fake_chart("C-SPAN", [{"name": "Recur00 Person", "span_years": 4}]),
    "C-POS": fake_chart("C-POS", [{"year": 2019, "records": 1, "first author": 1}]),
    "C-YEAR": fake_chart("C-YEAR", [{"year": 2019, "count": 1}]),
    "C-TEAM": fake_chart("C-TEAM", [{"authors": 3, "records": 2}]),
    "C-NET": fake_chart("C-NET", [{"cluster": 1, "person": "Recur00 Person"}]),
}


# ============================================================
# Helpers over the emitted page
# ============================================================

_JSON_BLOCK = re.compile(
    r'<script type="application/json" id="report-data">(.*?)</script>', re.S
)
_ANY_SCRIPT = re.compile(r"<script\b.*?</script>", re.S)
_DETAILS = re.compile(r"<details\b.*?</details>", re.S)
_SECTION = re.compile(r'<section class="rep" id="s\d+".*?</section>', re.S)

# Namespace URIs are declarations, not fetches: no engine ever requests them,
# and a hand-emitted SVG root legitimately carries one. Everything else that
# looks like a URL is a genuine external reference and must not appear.
_INERT_URIS = (
    "http://www.w3.org/2000/svg",
    "http://www.w3.org/1999/xlink",
    "http://www.w3.org/1999/xhtml",
)


def embedded_json(page: str) -> dict:
    match = _JSON_BLOCK.search(page)
    if not match:
        raise AssertionError("no embedded JSON block")
    return json.loads(match.group(1))


def without_scripts(page: str) -> str:
    """The page minus both script elements.

    Every "does this text appear" assertion runs against this. Without it the
    JSON copy of a string satisfies the test while the reader sees nothing, and
    the `&&` operator in the table script counts as an unescaped ampersand.
    """
    return _ANY_SCRIPT.sub("", page)


def strip_inert_uris(page: str) -> str:
    for uri in _INERT_URIS:
        page = page.replace(uri, "")
    return page


PAGE = render_html(make_report(), CHARTS)
BARE = render_html(make_report(), None)
REPORT = make_report()


# ============================================================
# 1. One file, no network
# ============================================================

print("\n--- self-contained, no external reference ---")

scan = strip_inert_uris(PAGE)
check("no http:// reference", "http://" in scan, False)
check("no https:// reference", "https://" in scan, False)
check("no protocol-relative reference", "//cdn" in scan or 'src="//' in scan, False)
check("no src= attribute at all", "src=" in PAGE, False)
check("no <img> element", "<img" in PAGE.lower(), False)
check("no <link> element", "<link" in PAGE.lower(), False)
check("no @import in the stylesheet", "@import" in PAGE, False)
check("no @font-face, so no webfont fetch", "@font-face" in PAGE, False)
check("no url() asset reference in CSS", "url(" in PAGE, False)
check("no iframe", "<iframe" in PAGE.lower(), False)
check("the stylesheet is inline", PAGE.count("<style>"), 1)
check("exactly two script elements: the data block and the table controls",
      PAGE.count("<script"), 2)
check("the first script element is the JSON data block",
      PAGE.index('<script type="application/json"') < PAGE.index("<script>"), True)
check("a page built without charts still has exactly two script elements",
      BARE.count("<script"), 2)
check("the page is one document", (PAGE.count("<html"), PAGE.count("</html>")), (1, 1))


# ============================================================
# 2. The embedded JSON parses
# ============================================================

print("\n--- embedded JSON ---")

data = embedded_json(PAGE)
check("the embedded JSON parses", isinstance(data, dict), True)
check("it is the json_record, so the rendered sections are not duplicated",
      "sections" in data, False)
check("it carries the metrics", sorted(data["metrics"])[:3], ["s10", "s11", "s12"])
check("it carries the caveats map", sorted(data["caveats"])[0], "CAV-00")
check("it round-trips the roster denominator",
      data["metrics"]["s2"]["denominator"], REPORT["metrics"]["s2"]["denominator"])
check("no raw </script> can terminate the block early",
      "</script>" in _JSON_BLOCK.search(PAGE).group(1), False)
check("markup inside the JSON is unicode-escaped, and still decodes",
      any(row["name"] == HOSTILE_NAME for row in data["metrics"]["s2"]["rows"]), True)


# ============================================================
# 3. Section 0 and Section 14 are visible without interaction
# ============================================================

print("\n--- honesty sections are not behind a click ---")

collapsed_regions = "".join(_DETAILS.findall(PAGE))
section_0 = re.search(r'<section class="rep" id="s0".*?</section>', PAGE, re.S)
section_14 = re.search(r'<section class="rep" id="s14".*?</section>', PAGE, re.S)
check_true("Section 0 is rendered", section_0)
check_true("Section 14 is rendered", section_14)
check("Section 0 contains no <details>", "<details" in section_0.group(0), False)
check("Section 14 contains no <details>", "<details" in section_14.group(0), False)
check("Section 0 is not inside any collapsed element",
      section_0.group(0) in collapsed_regions, False)
check("Section 14 is not inside any collapsed element",
      section_14.group(0) in collapsed_regions, False)
check_true("Section 0 carries CAV-00 verbatim",
           caveats.CAVEATS["CAV-00"] in section_0.group(0))
# Re-anchored in round four, and the reason the previous anchor failed is worth
# keeping. It was the fitted slope, chosen on the argument that a slope over five
# right-censored points is refused "whatever the data source" and would therefore
# stay put. It did not: round four kept the objection and changed the
# presentation, and the entry was reworded. The lesson is that "refused by
# design" is not a durable anchor — a decision is exactly the kind of thing that
# gets revisited.
#
# What cannot be revisited is an entry whose absence is structural. PubMed has no
# field distinguishing a PhD student from a postdoc or a technician, so no
# decision and no data source available to this tool can promote that entry out
# of the register. That is the anchor now.
check_true("Section 14 names a dropped metric",
           "Any distinction between PhD student" in section_14.group(0))
check("every entry of the dropped register survives",
      sum(1 for name, _ in caveats.DROPPED_REGISTER if name.split(",")[0][:30] in section_14.group(0)),
      len(caveats.DROPPED_REGISTER))
# The table of contents sits above Section 0 by design (it is navigation, not
# content), so the ordering claim is about sections and figures, not about the
# first occurrence of a section title string.
check("CAV-00 precedes provenance and every figure",
      PAGE.index(caveats.CAVEATS["CAV-00"]) < min(PAGE.index('id="s1"'), PAGE.index("<figure")),
      True)


# ============================================================
# 4. Escaping
# ============================================================

print("\n--- escaping ---")

visible = without_scripts(PAGE)
check("the hostile name never appears unescaped", HOSTILE_NAME in PAGE, False)
check_true("the hostile name appears escaped, as text",
           "&lt;script&gt;alert(1)&lt;/script&gt;" in visible)
check_true("unescaping the page recovers the name as text",
           HOSTILE_NAME in html_module.unescape(visible))
check("the injected script did not become an element",
      re.search(r"<script[^>]*>\s*alert\(1\)", PAGE), None)
check_true("an affiliation with quotes and angle brackets is escaped",
           "&lt;Angles&gt;" in visible and "&amp;" in visible)
check("a raw ampersand from PubMed never reaches the page unescaped",
      re.search(r"&(?!amp;|lt;|gt;|quot;|#39;|nbsp;)", visible.replace("&amp;", "")), None)
check_true("a title containing markup is escaped in the titles section",
           "&lt;b&gt;markup&lt;/b&gt;" in visible)


# ============================================================
# 5. Caveats travel with their numbers
# ============================================================

print("\n--- caveats ---")

used = REPORT["caveats"]
check_true("the fixture exercises many caveats", len(used) >= 18)
missing = [key for key, text in used.items() if text not in visible]
check("every caveat in the report dict is visible page text, not only in the JSON",
      missing, [])
verbatim = [key for key, text in used.items() if text not in PAGE]
check("every caveat is byte-identical to caveats.py after formatting", verbatim, [])
inside_details = [key for key, text in used.items() if text in collapsed_regions]
check("no caveat is inside a <details>", inside_details, [])
# CAV-02, CAV-06 and CAV-09 are each attached to two sections on purpose: the
# visual spec repeats CAV-09 under the timeline because that figure is where a
# reader forms the tenure belief it exists to deny. What is forbidden is
# printing one caveat twice inside one section, which trains readers to skip
# caveat blocks.
repeated_in_section = sorted({
    key
    for block in _SECTION.findall(visible)
    for key, text in used.items()
    if block.count(text) > 1
})
check("no caveat is printed twice inside one section", repeated_in_section, [])
check("a caveat attached to two sections appears in both",
      visible.count(caveats.CAVEATS["CAV-09"]), 2)
check_true("the caveat id is printed beside its text", 'data-caveat="CAV-09"' in PAGE)
check_true("a caveat containing quotation marks keeps them",
           caveats.CAVEATS["CAV-11"] in visible)

for figure_id, expected in (
    ("c-gantt", ("CAV-02", "CAV-03", "CAV-09")),
    ("c-lag", ("CAV-06", "CAV-07", "CAV-08")),
    ("c-span", ("CAV-09", "CAV-10", "CAV-11")),
    ("c-year", ("CAV-17", "CAV-18")),
    ("c-team", ("CAV-19",)),
):
    block = re.search(rf'<figure id="fig-{figure_id}".*?</figure>', PAGE, re.S)
    found = tuple(re.findall(r'data-caveat="(CAV-\d+)"', block.group(0))) if block else ()
    check(f"{figure_id} carries exactly its assigned caveats", found, expected)


# ============================================================
# 6. The roster table
# ============================================================

print("\n--- the 277-row person table ---")

roster = re.search(r'<details id="roster".*?</details>', PAGE, re.S).group(0)
n_people = REPORT["metrics"]["s2"]["denominator"]
check("the table is collapsed by default", "<details id=\"roster\">" in PAGE, True)
check("one row per person, all rendered server-side",
      len(re.findall(r'<tr data-i="\d+">', roster)), n_people)
check("the summary states the true row count without being opened",
      f"Full roster table — {n_people} rows" in roster, True)
check("row indices match the embedded JSON row order",
      [int(i) for i in re.findall(r'<tr data-i="(\d+)">', roster)], list(range(n_people)))
check_true("a filter control exists", 'id="roster-q"' in roster)
check_true("an order control exists", 'id="roster-order"' in roster)
check_true("the controls are inert until the script enables them", "hidden>" in roster)

orders = re.findall(r'<option value="([a-z-]+)">', roster)
banned = [o for o in orders if any(w in o for w in ("appear", "lead", "slot", "count", "equal"))]
check("no order option ranks people by a count", banned, [])
check("the offered orders are report order, name and year only",
      sorted(set(orders)),
      ["first", "first-desc", "last", "last-desc", "name", "name-desc", "report"])
check_true("the table says why counts are not sortable",
           "productivity ranking" in roster)
check("no sort control is attached to a column header",
      re.search(r"<th[^>]*>\s*<(button|a)\b", PAGE), None)
check("no data-sort attribute anywhere", "data-sort" in PAGE, False)
check("no button element anywhere", "<button" in PAGE, False)
check("no inline event handler anywhere",
      re.search(r"\son(click|change|input|load|error)\s*=", PAGE), None)


# ============================================================
# 7. A score, and still no ranking
# ============================================================

print("\n--- prohibited quantities ---")

# The caveats and the dropped register name these terms in order to rule them
# out, so the scan runs over the computed half of each section only.
computed = "\n".join(
    line for section in REPORT["sections"] for line in section["body"]
)
# "h-index", "impact factor", "citation count" and "overall score" came off this
# list in round one: the page prints them, so banning the words would ban the
# feature. In round two "rank", "star rating", "top ", "best ", "better than"
# and "outperform" came off for the same reason one round later — the compare
# page ranks corpora, Section 16 prints a star band, and
# `ranking.comparative_statement` writes "X scores higher than Y".
#
# This *profile* page still ranks nobody, because a profile page holds one
# corpus and a rank is a position in a set. That claim is now made positively,
# below, against the roster and the order control, rather than by banning a
# word the page is entitled to use.
#
# Nothing is banned outright here any more. "percentile" and "quantile" came off
# in round four together with the objection behind them: there is a reference
# population now, and it is not the set of corpora a user loaded —
# `impact_reference` places a count inside every OpenAlex work sharing its topic
# and year. `ranking`'s refusal of the *other* position is untouched.
#
# "trend" and "slope" came off in round four. The objection to a fitted
# direction over a few right-censored points was never withdrawn — Section 9 now
# prints the interval and the point count beside the slope instead of printing
# the slope alone, and refuses to fit at all below four points. The rendered
# page is held to the same condition as the report body (test_profile T60c):
# the word may appear, bare confidence in it may not.
check_true("a slope reaches the rendered page", "slope" in computed.lower())
check_true("...with its interval rendered alongside it", "interval" in computed.lower())
# This page is rendered without a reference payload, so the words must arrive
# attached to the reason none were fetched rather than to an empty cell.
check_true("the rendered page names the reference population",
           "reference population" in computed.lower())
check_true("...and says plainly that an absent one is not a low count",
           "is not a low count" in computed.lower())
check("no per-person row carries a percentage",
      any("%" in line for line in computed.splitlines() if line.startswith("| ")), False)
check_true("the header states there is no ranking of people",
           "no ranking of people" in PAGE)
check_true("...and enumerates what that rules out",
           "no percentile, no quantile position and no letter grade" in PAGE)
# The other half of the same claim: the header has to be describing a page that
# does print a score and a star band, or "no ranking of people" is just the old
# prohibition reworded.
check_true("...on a page that does print a composite score",
           "composite score" in PAGE.lower() and 'id="s16"' in PAGE)
check_true("...and a star band derived from it",
           "star band" in PAGE.lower() and "★" in PAGE)
# Ranking people is still refused, and the refusal is structural rather than
# lexical: no control offers to order the roster by a count.
check_true("no roster order option is a count",
           not [o for o in re.findall(r'<option value="([a-z-]+)">', PAGE)
                if any(w in o for w in ("appear", "lead", "slot", "count", "equal", "score", "star"))])

# 字母等级 — the one prohibition round two adds. Stars yes, letters no: two
# coarsenings of one number, split on the user's instruction, and the split is
# recorded in `ranking.RANKING_EXCLUSIONS` so nobody unifies them later.
#
# Matched as a value, not as a word. The whole page is scanned, including the
# header and the dropped register, both of which have to *name* letter grades in
# order to refuse them — so the pattern requires an actual letter after a
# grading label. "verified by evidence tier: orcid" is an identity tier, not a
# band, and does not match.
LETTER_GRADE = re.compile(
    r"(?i:grade|tier|band|等级|评级)\s*\d*\s*[:：=]\s*[\"'“]?[A-DF][+\-]?(?![A-Za-z])"
    r"|(?<![A-Za-z])[A-DF][+\-]?\s*(?:级|档)(?![A-Za-z])"
    r"|(?<![A-Za-z])A\s*[/、,]\s*B\s*[/、,]\s*C(?![A-Za-z])"
)
for offending in ("grade: B", "评级：A+", "band 3 = C-", "the scale is A/B/C"):
    check(f"the letter-grade guard catches {offending!r}",
          bool(LETTER_GRADE.search(offending)), True)
check("no letter grade anywhere on the page", LETTER_GRADE.findall(PAGE), [])
check("no letter grade in the embedded JSON either",
      LETTER_GRADE.findall(json.dumps(embedded_json(PAGE), ensure_ascii=False)), [])


# ============================================================
# 8. Figures, structure and keyboard
# ============================================================

print("\n--- figures and structure ---")

check("every placed figure is on the page", len(re.findall(r"<figure id=", PAGE)),
      len(html_report.FIGURE_PLACEMENT))
check("the gantt lands in Section 2",
      'id="s2"' in PAGE and PAGE.index('id="s2"') < PAGE.index('id="fig-c-gantt"'), True)
check("the byline-position figure lands in Section 7",
      'id="s7"' in PAGE and PAGE.index('id="s7"') < PAGE.index('id="fig-c-pos"'), True)
check("the co-author network lands in Section 19",
      'id="s19"' in PAGE and PAGE.index('id="s19"') < PAGE.index('id="fig-c-net"'), True)
# Figures follow the *print* order of the sections they are pinned to, which is
# not numeric order and not the placement table's own order: Section 19 is
# printed third, right under Section 0 and Section 17, so its figure is the first
# on the page even though its id sorts last. `_render_section` iterates
# `report["sections"]` as given and must never start sorting by id.
check("figure order follows report.py's section print order, not id order",
      [m for m in re.findall(r'<figure id="fig-([a-z-]+)"', PAGE)],
      ["c-net", "c-gantt", "c-lag", "c-span", "c-pos", "c-year", "c-team"])
check("every figure has a figcaption", len(re.findall(r"<figcaption", PAGE)),
      len(html_report.FIGURE_PLACEMENT))
check("every figcaption states a k of N",
      len([c for c in re.findall(r"<figcaption[^>]*>(.*?)</figcaption>", PAGE, re.S)
           if re.search(r"\d+ of \d+", c)]), len(html_report.FIGURE_PLACEMENT))
check("a page built without charts renders no figure", "<figure" in BARE, False)
check("a page built without charts still shows every caveat",
      [key for key, text in used.items() if text not in without_scripts(BARE)], [])

# Section 18 is always open, so its journal table and its risk block both render
# without a click however long they get. That is the whole reason 18 is in
# ALWAYS_OPEN_SECTIONS: the edition, the retrieval date and the endpoint that
# produced a statement are what qualify the number beside them, and a reader who
# has to expand a disclosure to find them ends up with the number and none of it.
_S18 = re.search(r'<section class="rep" id="s18".*?</section>', PAGE, re.S).group(0)
check("Section 18 contains no <details>", "<details" in _S18, False)
check_true("...and carries the 风险信号 column header", "风险信号" in _S18)
check_true("...and says the signals were not collected, rather than leaving a blank",
           "未采集" in _S18)
check_true("...and names the verb that collects them",
           "check-your-advisor journal-risk" in _S18)
check_true("...and refuses the word the whole block exists not to print",
           "does not call any journal predatory" in _S18
           or "None of them says a journal is predatory" in _S18)
check_true("the header stamp tells a reader up front that no journal is rated",
           "never a rating" in PAGE)
# The long register prints in full, uncollapsed, like every other caveat block.
check("every JRN-08..12 caveat is on the page",
      [key for key in ("JRN-08", "JRN-09", "JRN-10", "JRN-11", "JRN-12")
       if key not in _S18], [])
check("one h1", PAGE.count("<h1"), 1)
# 21 report sections (0-14, plus 15 citation impact, 16 composite score and star
# band, 17 graduates on record, 18 journal-level metrics, 19 co-author clusters
# and 20 student evaluations) and the embedded-JSON block. 17, 18 and 20 are
# rendered even with no table supplied, because a missing section reads as a
# question nobody asked; 19 needs no table and renders always, printing why it
# did not partition when the corpus is below its floor.
check("one h2 per section plus the data block", PAGE.count("<h2"), 22)
check_true("a skip link is the first focusable element",
           PAGE.index('class="skip"') < PAGE.index("<header"))
check_true("wide figures are keyboard scrollable", 'tabindex="0"' in PAGE)
check("no positive tabindex", re.search(r'tabindex="[1-9]', PAGE), None)
check_true("the table of contents links every section",
           all(f'href="#s{i}"' in PAGE for i in range(19)))
check_true("print expands disclosures", "details:not([open])" in PAGE)
check_true("print restores filtered rows", "tr.is-filtered-out{display:table-row}" in PAGE)
check_true("focus stays visible", ":focus-visible" in PAGE)


# ============================================================
# 9. A refused report
# ============================================================

print("\n--- refusal ---")

# G4 rather than G2: the identity conditions became warnings, so the first
# thing still refused is a corpus with no structured author records. What is
# under test here is the refusal page, not which gate produced it.
refused = corpus([paper(1, [author("Liu Hua"), pi()])])
refused["papers"].append({"pmid": "9", "title": "t", "pub_date": "2024", "authors": []})
refusal = render_html(report.build_report(refused, {}, None, FIXED_NOW), CHARTS)
check("a refusal renders the gate id", "gate G4" in refusal, True)
check("a refusal names the gate", "no structured authors" in refusal, True)
check("a refusal prints the observed values", "pmid" in refusal, True)
check("a refusal renders no figure", "<svg" in refusal, False)
check("a refusal renders no section body", "Corpus provenance" in refusal, False)
check("a refusal carries exactly one script, the data block", refusal.count("<script"), 1)
check("the refusal JSON parses", isinstance(embedded_json(refusal), dict), True)

# An incomplete harvest is the case that used to land here as gate G1. It now
# renders as a whole page carrying one extra line, so the reader gets the
# numbers and the warning instead of an empty document.
capped = corpus([paper(1, [author("Liu Hua"), pi()])])
capped["query"].update({"esearch_count": 900, "pmids_returned": 500,
                        "max_records": 500, "pages_fetched": 1, "duplicates_dropped": 0})
capped_page = render_html(report.build_report(capped, {}, None, FIXED_NOW), CHARTS)
check("an incomplete corpus still renders its sections",
      "Corpus provenance" in capped_page, True)
check("...and prints retrieved of matched",
      "retrieved 500 of 900 records esearch matched" in capped_page, True)
check("...and prints the shortfall in bold",
      "<strong>400 of those 900 records were never retrieved.</strong>" in capped_page, True)

# The other two conditions that used to land on the refusal page. G2 and G3 are
# warnings now: the whole report renders, and each affected section opens with a
# callout of its own rather than another caveat blockquote a reader would skim.
warned = corpus([paper(1, [author("Liu Hua"), pi()])], fallback_fired=True)
# The name stays TARGET, which is the name on this corpus's only byline. It read
# "Zhu Guangwei" — a name nobody here holds — and that made the fixture raise G7
# as well, which is correct behaviour and not what these four checks are about.
# Only orcid / affiliation_keywords / email_domains are blanked, which is what
# G2 and G3 are conditioned on.
warned["identity"] = {"author_name": TARGET, "orcid": "",
                      "affiliation_keywords": [], "email_domains": []}
warned_report = report.build_report(warned, {}, None, FIXED_NOW)
warned_page = render_html(warned_report, CHARTS)
check("a warned corpus renders every section", "Corpus provenance" in warned_page, True)
check("...including the one that checks for exactly this failure",
      "Co-author clusters" in warned_page, True)
check("...and both warnings are raised, not just the first",
      [w["id"] for w in warned_report["warnings"]], ["G2", "G3"])
check("...each rendered in its own callout, three sections times two warnings",
      warned_page.count('class="warnbox"'), 6)
check("...marked up as a note for a screen reader", 'role="note"' in warned_page, True)
check("...not as another caveat blockquote",
      'class="warnbox" role="note"><blockquote' in warned_page, False)
check("...with the warning text in bold",
      "<strong>Warning G2 (identity fallback)" in warned_page, True)
check("...naming the observed values", "papers_stamped_unverified=0" in warned_page, True)
check("...and the fix", "Fix: Set orcid, email_domains or affiliation_keywords" in warned_page, True)
# The fix text must not send the reader after an id that would clear nothing on
# a PubMed-only harvest, which is what "set at least one of ... /
# openalex_author_id" did. It must not deny the field either: once the merge
# folds a confirming OpenAlex work in, a PubMed record does carry the id at
# record level, and "PubMed records never carry that field" went out verbatim on
# every fired G3.
check("...which says outright that an id alone clears this only above the threshold",
      "alone clears this only once it reaches min_openalex_record_share" in warned_page, True)
check("...and no longer denies the field the merge writes onto a PubMed record",
      "never carry that field" in warned_page, False)
check("the callout is styled without depending on colour",
      "border:3px solid var(--ink)" in warned_page, True)
check("...and does not break across printed pages",
      ".warnbox,tr{break-inside:avoid}" in warned_page, True)
# The callout must sit above the section's prose, not below it, or it is a
# footnote to the thing it is warning about.
_s0 = warned_page.index('id="s0"')
check("the callout precedes the section prose",
      warned_page.index('class="warnbox"', _s0) < warned_page.index("<p>", _s0), True)
check("Section 14 records the downgrade on the page",
      "Downgraded on this run" in warned_page, True)


# ============================================================
# 10. Determinism
# ============================================================

print("\n--- determinism ---")

check("two renders of one report are byte-identical",
      render_html(REPORT, CHARTS), render_html(REPORT, CHARTS))
check("chart keys are accepted under either spelling",
      render_html(REPORT, {k.removeprefix("C-").lower(): v for k, v in CHARTS.items()}),
      render_html(REPORT, CHARTS))


print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(0 if _failed == 0 else 1)
