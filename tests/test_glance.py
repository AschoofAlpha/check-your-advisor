#!/usr/bin/env python3
"""
The at-a-glance summary at the top of every report.

A report for a 38-record corpus ran to about fifteen thousand words over
twenty-one sections. Every section has a reason to exist, and none of that changes;
what was missing was one place a student could read in a minute before deciding
whether to read on. This file holds the summary to four rules:

1. It sits above Section 0, on the Markdown page and as a card on the HTML page.
2. It fits on a page: a dozen-odd lines, each naming the section it came from.
3. It cannot disagree with the page — every number in it is a number its section
   prints — and it gives no verdict.
4. Warnings come first, a suppressed metric is said to be suppressed, and a
   refused report has no summary at all.

Run: python tests/test_glance.py
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor.profile import html_report, report  # noqa: E402

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


TARGET = "Chen Xiuying"
INTERNAL = "Department of Hepatobiliary Surgery, Nanhai Medical University, Nanhai"
ORCID = "0000-0002-1825-0097"
NOW = datetime(2026, 7, 22, 20, 47, 11)


def author(name, affiliation=""):
    parts = name.split()
    return {"name": name, "last": parts[0], "fore": " ".join(parts[1:]), "initials": parts[-1][:1],
            "affiliation": affiliation, "email": "", "orcid": "", "equal_contrib": False,
            "is_corresponding": False}


def pi():
    entry = author(TARGET, INTERNAL)
    entry["orcid"] = ORCID
    return entry


def paper(pmid, byline, date, pi_index):
    return {"pmid": str(pmid), "title": f"Study {pmid}", "authors": byline,
            "authors_str": ", ".join(a["name"] for a in byline), "journal": "Hepatology Reports",
            "pub_date": date, "pub_year": date.split()[0], "volume": "", "issue": "", "pages": "",
            "doi": "", "pmc_id": "", "abstract": "", "pi_index": pi_index, "pi_evidence": "orcid",
            "pi_ambiguous": False}


def lab(identity=True, members=8, fillers=12):
    papers, pmid = [], 1000
    for index in range(members):
        member = author(f"Recur{index:02d} Person", INTERNAL)
        for offset, year in enumerate((2019 + index // 4, 2021, 2023)):
            byline = [member, pi()] if offset == 1 else [author(f"Filler{pmid:04d} Person"), member, pi()]
            papers.append(paper(pmid, byline, f"{year} Jun", len(byline) - 1))
            pmid += 1
    for index in range(fillers):
        papers.append(paper(pmid, [author(f"Once{index:02d} Person"), pi()], "2022 Mar", 1))
        pmid += 1
    ident = {"author_name": TARGET, "orcid": ORCID if identity else "",
             "affiliation_keywords": ["Nanhai Medical University"] if identity else [],
             "email_domains": [], "require_affiliation_effective": False}
    return {
        "schema_version": 1, "generated_at": "2026-07-22T20:47:11", "position_filtered": False,
        "query": {"term": f'"{TARGET}"[Author]', "mindate": "2018/07/22", "maxdate": "2026/07/22",
                  "years_back": 8, "retmax": 500, "esearch_count": len(papers),
                  "pmids_returned": len(papers), "max_records": len(papers), "truncated": False},
        "identity": ident,
        "counts": {"fetched": len(papers), "verified": len(papers), "name_only": 0, "rejected": 0,
                   "by_evidence": {"orcid": len(papers)} if identity else {}},
        "fallback_fired": False, "papers": papers,
    }


def glance_block(markdown: str) -> list[str]:
    start = markdown.index("**At a glance.**")
    block = markdown[start:markdown.index("\n## ", start)]
    return [line for line in block.splitlines() if line.startswith("- ")]


# ============================================================
# 1. Where it sits and how long it is
# ============================================================

print("\n--- placement and length ---")

clean = report.build_report(lab(), {"author_name": TARGET}, None, NOW)
markdown = report.render_markdown(clean)
lines = glance_block(markdown)
check_true("the summary opens the page, above Section 0",
           markdown.index("**At a glance.**") < markdown.index("## 0."))
check_true("it is a dozen-odd lines, not another section", 8 <= len(lines) <= 15)
check_true("...short enough to read in a minute", len(" ".join(lines).split()) <= 450)
check("every line names the section it came from",
      [line for line in lines if not re.search(r"Sections? \d", line)], [])
check("the summary is the glance_lines the renderer was given",
      [line[2:] for line in lines], report.glance_lines(clean))


# ============================================================
# 2. It agrees with the page it sits on
# ============================================================

print("\n--- agreement with the sections ---")

metrics = clean["metrics"]
position = metrics["s7"]["counts"]
check_true("the PI's byline counts are Section 7's",
           any(f"last author on {position['last']} of {metrics['s7']['denominator']} records" in line
               for line in lines))
complete = [row["count"] for row in metrics["s9"]["years"] if not row["partial"] and not row["indexing_lag"]]
check_true("records per year are Section 9's complete years",
           any(f"{min(complete)} to {max(complete)}" in line for line in lines))
check_true("the corpus size is Section 1's",
           any(line.startswith(f"- {clean['provenance']['corpus_size']} records") for line in lines))
wait = metrics["s4"]
check_true("time to a first slot carries the people still without one, as Section 4 insists",
           any(f"median {report._fmt_number(wait['median'])} years over the {wait['denominator']} people"
               in line and f"leaves out {len(wait['still_without_lead'])} more with none so far" in line
           for line in lines))
clusters = metrics["s19"]
check_true("the co-author clusters are summarised second, with Section 19's own counts",
           lines[1].startswith(f"- Co-author clusters with the PI taken out: {clusters['n_clusters']} over "
                               f"{clusters['denominator']} records, the largest holding "
                               f"{clusters['largest_size']}")
           if clusters["n_clusters"] > 1 else
           lines[1].startswith(f"- Co-author clusters with the PI taken out: all {clusters['denominator']} "
                               "records are tied together"))
filtered = lab()
filtered["position_filtered"] = True
filtered_lines = glance_block(report.render_markdown(report.build_report(filtered, {"author_name": TARGET}, None, NOW)))
check_true("on a position-filtered corpus, every harvest's, the byline line says it was not measured and why",
           any(line.startswith("- The PI's own byline position: not measured — harvest keeps only papers")
               for line in filtered_lines))
check("no line passes judgement",
      [line for line in lines if re.search(r"\b(good|bad|recommend|avoid|safe|risky)\b", line, re.I)], [])


# ============================================================
# 3. Warnings first; suppression said; refusals summarised by nothing
# ============================================================

print("\n--- warnings, suppression, refusal ---")

warned = report.build_report(lab(identity=False), {"author_name": TARGET}, None, NOW)
warned_lines = glance_block(report.render_markdown(warned))
check_true("a corpus with warnings opens its summary with them", "warning" in warned_lines[0])
check("...naming every warning the report carries",
      [w["id"] for w in warned["warnings"] if w["id"] not in warned_lines[0]], [])
check("a clean corpus has no warning line", any("warning(s)" in line for line in lines), False)

small = report.build_report(lab(members=2, fillers=1), {"author_name": TARGET}, None, NOW)
small_lines = glance_block(report.render_markdown(small))
check_true("a metric its section suppresses is said to be suppressed, not given a number",
           not small["metrics"]["s5"].get("suppressed")
           or any("too few people to aggregate (Section 5)" in line for line in small_lines))

refused_corpus = lab()
refused_corpus["papers"] = [paper(1, [], "2023 Jun", None), paper(2, [], "2024 Jun", None)]
refused = report.build_report(refused_corpus, {"author_name": TARGET}, None, NOW)
check("a refused report has no summary", report.glance_lines(refused), [])
check("...and its page none", "At a glance" in report.render_markdown(refused), False)

supplied = report.build_report(lab(), {"author_name": TARGET}, None, NOW,
                               journal_table={"rows": [], "source": "test"})
check_true("a supplied journal table is reported as supplied",
           any("journal metrics supplied (Section 18)" in line for line in glance_block(report.render_markdown(supplied))))
check_true("...and an absent one as absent", any("journal metrics not supplied" in line for line in lines))


# ============================================================
# 4. The HTML card
# ============================================================

print("\n--- the HTML card ---")

page = html_report.render_html(clean, {})
check_true("the HTML page carries the summary card", 'id="glance"' in page)
check_true("...between the page header and the section list",
           page.index("</header>") < page.index('id="glance"') < page.index('<nav class="toc"'))
check_true("...and above the paragraph saying what the page is, which used to sit in the header over it",
           page.index('id="glance"') < page.index("This report describes publication metadata"))
header = page[page.index('<header class="page">'):page.index("</header>")]
check("...leaving the header a title and a date", re.sub(r"<[^>]+>", "", header).count("."), 1)
card = page[page.index('id="glance"'):page.index("</section>", page.index('id="glance"'))]
check("...one list item per summary line", card.count("<li>"), len(lines))
zh_page = html_report.render_html(report.localize(clean, "zh"), {})
check_true("the Chinese page has it too, in Chinese", "一页速览" in zh_page)


print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(0 if _failed == 0 else 1)
