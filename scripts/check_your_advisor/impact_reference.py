"""
一篇论文的引用数在「同领域同年」里的位置：外部参照人群，不是本页的几份语料
==========================================================================
Where one paper's citation count falls inside an **external** reference
population: every work OpenAlex files under the same `primary_topic` in the same
`publication_year`.

What this module is not, and what it did not overturn
-----------------------------------------------------
`profile/ranking.py`'s `RANKING_EXCLUSIONS["not_computable_here"]` refuses "a
position inside a reference population" in these words:

    Not computed, because there is no reference population. The corpora on a
    page are the ones a user chose to load; a position inside that set would
    move whenever an unrelated corpus was added or dropped.

That entry is correct and is not touched by this file. It is about a position
among **the corpora somebody loaded** — a set that changes when a user adds or
drops one, so the same paper's position changes with it. The population here is
a different object: every OpenAlex work in one topic in one year. It is the same
population tomorrow whether this run loaded one corpus or nine, nobody on the
page is in it by choice, and a second person running the same query gets the
same cell. So the two coexist, and neither is the other's replacement:
`rank_corpora` still says "3rd of the 5 you loaded" and this module says "the
count sits above N of M papers in T10091 / 2023".

Nothing here orders people, and nothing here produces a grade, a tier, a letter
or a star. A percentile is a position inside one external cell, and it arrives
with the size of that cell attached.

The measurement, and the one request it rests on
-------------------------------------------------
OpenAlex's `/works` endpoint answers `group_by=cited_by_count` with the complete
citation-count histogram of whatever the filter selected, in one keyless GET::

    GET https://api.openalex.org/works
        ?filter=primary_topic.id:T10091,publication_year:2023
        &group_by=cited_by_count

    meta.count = 3808          the papers OpenAlex counts in that topic-year
    group_by   = [{"key": "0", "count": 1399}, {"key": "1", "count": 379}, ...]

1399 of those 3808 papers have never been cited. That single fact is the reason
for the percentile definition below, and it is why a report that prints "0
citations" without the cell beside it is printing the wrong half of the finding.

The topic is not in the corpus, so it is fetched
-------------------------------------------------
`openalex.work_to_paper` keeps no `primary_topic`, `concept` or `field` — it was
written for the bibliographic record, and the topic is not part of it. What it
does keep is `openalex_work_id`, and the PubMed side keeps `doi`. Either one
resolves to the work record that carries `primary_topic`, so the topic costs one
extra GET per paper and is looked up rather than guessed from the journal.

Five ways this does not produce a number
-----------------------------------------
The failure this module is built to avoid is printing "we could not place this
paper" as "this paper placed low". So there is no default percentile, no zero
stand-in, and no `0.0` anywhere except where a paper genuinely sits at the
bottom of a cell that answered. Every refusal is a named status carrying
`percentile: None` and a sentence: `no_citation_count`, `no_identifier`,
`topic_lookup_failed`, `no_topic_recorded`, `no_year`,
`distribution_unavailable`, `distribution_too_small`. `STATUS_REASONS` states
each of them in words, machine-readably, so a renderer prints the reason rather
than a blank cell a reader will fill in with the worst assumption available.

Every record carries its denominator and the day it was collected, per this
package's two standing rules. A cell measured today is a cell as OpenAlex
indexed it today; citation counts move, and an undated position is a position
about no particular moment.

Standard library only. All HTTP goes through `RobustHTTPClient`, with
`polite_headers` on every request — the same honest User-Agent `openalex.py` and
`journal_risk.py` send to the same keyless public API.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, quote_plus

from .citations import normalise_doi
from .http_client import RobustHTTPClient, Response, polite_headers

logger = logging.getLogger("check_your_advisor.impact_reference")

__all__ = [
    "CITATION_COUNT_FIELD",
    "CITATION_COUNT_SOURCE_FIELD",
    "DISTRIBUTION_METHOD",
    "IMPACT_REFERENCE_CAVEATS",
    "MIN_REFERENCE_POPULATION",
    "PERCENTILE_METHOD",
    "REFERENCE_POPULATION_SOURCE",
    "STATUS_DISTRIBUTION_READY",
    "STATUS_DISTRIBUTION_TOO_SMALL",
    "STATUS_DISTRIBUTION_UNAVAILABLE",
    "STATUS_LOCATED",
    "STATUS_NO_CITATION_COUNT",
    "STATUS_NO_IDENTIFIER",
    "STATUS_NO_TOPIC",
    "STATUS_NO_YEAR",
    "STATUS_ORDER",
    "STATUS_REASONS",
    "STATUS_TOPIC_FOUND",
    "STATUS_TOPIC_LOOKUP_FAILED",
    "UNLOCATABLE_STATUSES",
    "fetch_reference_distribution",
    "fetch_topics",
    "locate_corpus",
    "percentile_in_distribution",
    "IMPACT_REFERENCE_GLOB",
    "save_impact_reference_json",
    "find_latest_impact_reference_json",
    "load_impact_reference_json",
]


WORKS_URL = "https://api.openalex.org/works"

#: Where a paper's own citation count is read from. Named rather than inlined
#: because the join between `citations_*.json` and `papers_*.json` happens in the
#: caller, and this is the field name that join has to land on.
CITATION_COUNT_FIELD = "citation_count"

#: Which of the three citation APIs produced that count, when the caller knows.
#: Deliberately **not** `source`: in `papers_*.json` that key already means which
#: corpus the record came from (`pubmed` / `openalex`), and reading it here would
#: silently answer a different question. Unknown stays unknown.
CITATION_COUNT_SOURCE_FIELD = "citation_count_source"


# ------------------------------------------------------------------
# Declared thresholds
# ------------------------------------------------------------------
#
# Same rule as `scoring`, `ranking` and `trends`: every number this module
# compares against is a named module constant, exported, so a report can print it
# and a reader can disagree with it. There is no second threshold hidden in a
# function body — `test_impact_reference.py` moves this one and watches the
# behaviour move with it.

#: The smallest reference cell this module will take a position inside.
#:
#: The argument is about resolution, which is the part of a percentile a reader
#: cannot see. A cell of N papers can only express positions in steps of 100/N
#: percentage points: at N = 20 one paper is five points, so "the 45th
#: percentile" and "the 50th" differ by a single paper that happened to be
#: indexed, and the printed number carries two digits of precision the data
#: cannot support. At N = 100 one paper is one point, which is the coarsest cell
#: in which a percentile printed to the nearest point rests on at least one paper
#: per unit. That is the whole basis: it is a statement about arithmetic
#: resolution, not about representativeness.
#:
#: It is deliberately **not** a claim that a 100-paper cell is a good reference
#: population. It is one topic, in one year, in one database, and passing this
#: floor does not make it a population of researchers in general — see
#: `IMPACT_REFERENCE_CAVEATS`. Cells below the floor are refused under their own
#: status rather than placed with a wide disclaimer, because a percentile is a
#: single number and there is nowhere inside it to put the disclaimer.
MIN_REFERENCE_POPULATION = 100


# ------------------------------------------------------------------
# The statuses, and the sentence each one owes a reader
# ------------------------------------------------------------------

STATUS_LOCATED = "located"
STATUS_NO_CITATION_COUNT = "no_citation_count"
STATUS_NO_IDENTIFIER = "no_identifier"
STATUS_TOPIC_LOOKUP_FAILED = "topic_lookup_failed"
STATUS_NO_TOPIC = "no_topic_recorded"
STATUS_NO_YEAR = "no_year"
STATUS_DISTRIBUTION_UNAVAILABLE = "distribution_unavailable"
STATUS_DISTRIBUTION_TOO_SMALL = "distribution_too_small"

#: Intermediate statuses: the two sub-steps have their own success value, because
#: "the topic was found" is not yet "the paper was placed" and a renderer that
#: read one as the other would report a position that was never computed.
STATUS_TOPIC_FOUND = "topic_found"
STATUS_DISTRIBUTION_READY = "distribution_ready"

#: Every final per-paper status, located first. Not an ordering of anything: it
#: fixes the key order of the `by_status` denominator block so a status with no
#: papers in it still prints as 0 rather than disappearing.
STATUS_ORDER: tuple[str, ...] = (
    STATUS_LOCATED,
    STATUS_NO_CITATION_COUNT,
    STATUS_NO_IDENTIFIER,
    STATUS_TOPIC_LOOKUP_FAILED,
    STATUS_NO_TOPIC,
    STATUS_NO_YEAR,
    STATUS_DISTRIBUTION_UNAVAILABLE,
    STATUS_DISTRIBUTION_TOO_SMALL,
)

#: Derived, so that adding a status without deciding whether it is a placement is
#: impossible rather than merely discouraged.
UNLOCATABLE_STATUSES: tuple[str, ...] = tuple(
    status for status in STATUS_ORDER if status != STATUS_LOCATED
)

#: One sentence per status, printable verbatim. Seven of the eight exist for a
#: single reason: each names a *different* thing that did not happen, and every
#: one of them would otherwise be read as the same thing — a low percentile.
STATUS_REASONS: dict[str, str] = {
    STATUS_LOCATED: (
        "Placed: the count was compared against every OpenAlex work in the same primary topic and "
        "the same publication year, and the size of that cell is printed beside the position."
    ),
    STATUS_NO_CITATION_COUNT: (
        "No position, because this paper has no citation count to place. Three citation APIs were "
        "either not asked or had nothing, which is a fact about coverage and not about the paper. "
        "This is not zero citations: a paper known to have zero citations is placed, at the bottom "
        "of its cell, and says so."
    ),
    STATUS_NO_IDENTIFIER: (
        "No position, because the record carries neither an OpenAlex work id nor a DOI, so there "
        "is nothing to ask OpenAlex with. The topic could not be looked up, so no reference cell "
        "exists for this paper and none was fetched."
    ),
    STATUS_TOPIC_LOOKUP_FAILED: (
        "No position, because the request for this work's record did not answer — a network "
        "failure, an HTTP error, or a body that would not decode. This says nothing about the "
        "paper; re-running when the API answers may place it."
    ),
    STATUS_NO_TOPIC: (
        "No position, because OpenAlex answered and files no primary topic for this work. Its "
        "topic model does not cover every record, so there is no topic-year cell to place the "
        "count inside. Distinct from the request having failed, which is the status above."
    ),
    STATUS_NO_YEAR: (
        "No position, because no publication year could be read from the corpus record or from "
        "OpenAlex. A topic alone is not a reference population: citation counts grow with age, so "
        "a cell that mixed years would place old papers above new ones by construction."
    ),
    STATUS_DISTRIBUTION_UNAVAILABLE: (
        "No position, because the citation-count distribution for this topic and year did not "
        "answer. The paper has a topic and a year; what is missing is the population, so nothing "
        "about this status refers to the paper itself."
    ),
    STATUS_DISTRIBUTION_TOO_SMALL: (
        f"No position, because the reference cell that answered holds fewer than "
        f"{MIN_REFERENCE_POPULATION} papers. In a cell that small one paper moves the percentile "
        "by more than a point, so the position would carry precision the population cannot "
        "support. The cell size is reported so a reader can see how far under the floor it fell."
    ),
}


# ------------------------------------------------------------------
# The method strings a report prints verbatim
# ------------------------------------------------------------------

PERCENTILE_METHOD = (
    "Percentile = the share of papers in the reference cell with strictly fewer citations than "
    "this one, times 100. Strictly fewer, not fewer-or-equal, because the cells are discrete and "
    "heavily tied at the bottom: 1399 of the 3808 papers in the measured T10091/2023 cell have "
    "zero citations, and a fewer-or-equal rule would hand every one of them the 36.7th percentile "
    "for being at the top of that tie block. Under this rule an uncited paper scores 0.0, which is "
    "the true statement that no paper in the cell has fewer citations. The tie block is reported "
    "beside the position as `tied_count` and `percentile_at_or_below`, so the other reading is "
    "arithmetic a reader can redo rather than a number they have to trust."
)

DISTRIBUTION_METHOD = (
    "One keyless GET to api.openalex.org/works with "
    "filter=primary_topic.id:<topic>,publication_year:<year> and group_by=cited_by_count, which "
    "returns the complete citation-count histogram of that cell. The population is the sum of the "
    "returned group counts, and it is reported beside OpenAlex's own meta.count so a cell whose "
    "groups do not add up to the reported total is visible as such rather than silently short."
)

REFERENCE_POPULATION_SOURCE = (
    "OpenAlex /works, grouped by cited_by_count, filtered to one primary_topic and one "
    "publication_year"
)


# ------------------------------------------------------------------
# What a percentile from this module does not mean
# ------------------------------------------------------------------

IMPACT_REFERENCE_CAVEATS: dict[str, str] = {
    "IMP-01": (
        "A percentile here is a position inside one OpenAlex topic in one calendar year, measured "
        "on one day. It is not a position among researchers, not a position among the corpora "
        "loaded on this page, and not a quality judgement. `RANKING_EXCLUSIONS` still refuses the "
        "second of those and this block does not reopen it: the corpora on a page are the ones a "
        "user chose to load, while this cell is the same cell for anybody who runs the query."
    ),
    "IMP-02": (
        "The paper is normally inside the reference population it is being placed in: it is "
        "compared against a cell that contains itself. OpenAlex files it in that topic-year cell "
        "too, so it is one of the N papers the position is taken over and one member of its own "
        "tie block. At the floor of 100 papers that is worth at most one "
        "percentage point, and it is not corrected for, because correcting it would require "
        "knowing whether OpenAlex indexed this paper in this cell — which is exactly what a "
        "grouped count cannot tell you."
    ),
    "IMP-03": (
        "A paper with no percentile is not a low percentile. Seven separate statuses "
        "say so — no citation count, no identifier, the lookup failed, no topic on file, no year, "
        "the distribution did not answer, the cell was too small — and every one of them carries "
        "`percentile: None` and a sentence. A renderer that prints a blank, a dash or a zero for "
        "any of them is printing a measurement that was never made."
    ),
    "IMP-04": (
        "The citation count being placed and the cell it is placed in may come from different "
        "sources and different days. This package's counts fall back through OpenAlex, Semantic "
        "Scholar and Europe PMC, which disagree routinely, while the cell is always OpenAlex's. "
        "Where the count's source is recorded, the record says whether it matched; where it is "
        "not, the record says unknown rather than assuming."
    ),
    "IMP-05": (
        "OpenAlex's primary topic is a model's output, not a label an author chose. It is assigned "
        "with a score, the score travels with the record, and a paper that sits between two topics "
        "is placed in a cell that may not be the one its author would have picked. Changing the "
        "topic changes the cell, and with it the position."
    ),
    "IMP-06": (
        "Citation counts grow, so the cell is dated and the position is dated with it. A cell "
        "measured today and a count fetched last month are a position about neither day; the "
        "record carries both dates for exactly that reason."
    ),
}


# ------------------------------------------------------------------
# Small readers
# ------------------------------------------------------------------

_TOPIC_ID_RE = re.compile(r"(?i)\bT\d+\b")
_WORK_ID_RE = re.compile(r"(?i)\bW\d+\b")
_YEAR_RE = re.compile(r"\b(1[5-9]\d{2}|2[01]\d{2})\b")


def _now_stamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _topic_id(value: Any) -> str:
    """`T10091` out of any of the three spellings OpenAlex uses for a topic."""
    match = _TOPIC_ID_RE.search(str(value or "").strip())
    return match.group(0).upper() if match else ""


def _work_id(value: Any) -> str:
    """`W123` out of the URI or the bare form.

    Narrow on purpose, exactly like `pubmed_api.normalise_openalex_id` is narrow
    to authors: a function that accepted any entity id would let a source or an
    institution be requested as a work.
    """
    match = _WORK_ID_RE.search(str(value or "").strip())
    return match.group(0).upper() if match else ""


def _year(value: Any) -> int | None:
    """A four-digit calendar year, or None. `2023-05-01` reads as 2023."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        value = str(value)
    match = _YEAR_RE.search(str(value or "").strip())
    return int(match.group(1)) if match else None


def _count(value: Any) -> int | None:
    """A citation count as a non-negative int, or None for anything else.

    None and 0 are the two values this whole module exists to keep apart, so a
    missing count is never coerced to a number and a negative one — which no API
    should send — is refused rather than placed at the bottom of a cell.
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _with_mailto(url: str, mailto: str) -> str:
    """Append `mailto` if there is one. Not a key — a politer rate-limit pool."""
    if not mailto:
        return url
    return f"{url}{'&' if '?' in url else '?'}mailto={quote_plus(mailto)}"


def _json_body(resp: Response | None, what: str) -> dict[str, Any] | None:
    """Decode one OpenAlex response, or None with the reason in the log.

    Same shape as `openalex._json_body`, and for the same reason: every failure
    mode is named in the log rather than swallowed into an empty result that
    would read as "this cell is empty".
    """
    if resp is None:
        logger.warning("  [impact-ref] %s：网络层失败，无响应", what)
        return None
    if getattr(resp, "status_code", 0) != 200:
        logger.warning("  [impact-ref] %s：HTTP %s", what, getattr(resp, "status_code", "?"))
        return None
    try:
        data = json.loads(resp.content)
    except (UnicodeDecodeError, ValueError) as exc:
        logger.warning("  [impact-ref] %s：返回非 JSON 响应: %s", what, exc)
        return None
    if not isinstance(data, dict):
        logger.warning("  [impact-ref] %s：JSON 顶层不是对象，实为 %s", what, type(data).__name__)
        return None
    return data


# ------------------------------------------------------------------
# The three shapes, each built in one place
# ------------------------------------------------------------------
#
# The convention `trends._result` and `ranking._rating` established: a refusal
# is built by the same function as a success, so it cannot quietly carry fewer
# keys. A renderer must never have to tell "this run produced no percentile"
# apart from "this key was never in the payload".


def _topic_record(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": 1,
        "status": STATUS_NO_IDENTIFIER,
        "reason": "",
        "topic_id": "",
        "topic_display_name": "",
        "topic_score": None,
        "year": None,
        "year_source": "",
        "queried_by": "",
        "query": "",
        "pmid": "",
        "doi": "",
        "openalex_work_id": "",
        "title": "",
        "citation_count": None,
        "citation_count_source": "",
        "fetched_at": _now_stamp(),
    }
    base.update(fields)
    return base


def _distribution(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": 1,
        "available": False,
        "status": STATUS_DISTRIBUTION_UNAVAILABLE,
        "reason": "",
        "topic_id": "",
        "year": None,
        "counts": {},
        "population": 0,
        # R1, as everywhere in this package: the population a position would be
        # taken in. Equal to `population`, under the same name the rest of the
        # package prints denominators by.
        "denominator": 0,
        "reported_total": None,
        "groups_returned": 0,
        "groups_skipped": 0,
        "max_citation_count": None,
        "truncated": False,
        "missing": 0,
        "sufficient": False,
        "min_population": MIN_REFERENCE_POPULATION,
        "query": "",
        "fetched_at": "",
        "from_cache": False,
    }
    base.update(fields)
    return base


def _record(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": 1,
        # The verdict, and the two keys that must never disagree with it.
        "status": STATUS_NO_CITATION_COUNT,
        "located": False,
        "unlocatable": STATUS_REASONS[STATUS_NO_CITATION_COUNT],
        # None, never 0.0. This is the module's whole point.
        "percentile": None,
        "percentile_at_or_below": None,
        "percentile_bounds": None,
        "papers_below": None,
        "tied_count": None,
        "tied_fraction": None,
        "resolution_points_per_paper": None,
        # The count being placed, and where it came from.
        "citation_count": None,
        "citation_count_source": "",
        "count_source_is_reference": None,
        # The cell, and how much of it was actually seen.
        "denominator": 0,
        "population": 0,
        "reported_total": None,
        "missing": 0,
        "distribution_truncated": False,
        "groups_returned": 0,
        "max_citation_count": None,
        "above_distribution_max": False,
        "min_population": MIN_REFERENCE_POPULATION,
        # Which cell, and which paper.
        "topic_id": "",
        "topic_display_name": "",
        "topic_score": None,
        "year": None,
        "year_source": "",
        "queried_by": "",
        "pmid": "",
        "doi": "",
        "openalex_work_id": "",
        "title": "",
        "method": PERCENTILE_METHOD,
        "fetched_at": _now_stamp(),
        "basis": "",
    }
    base.update(fields)
    return base


# ------------------------------------------------------------------
# 1. The topic, which the corpus does not carry
# ------------------------------------------------------------------


def fetch_topics(
    papers: Sequence[Mapping[str, Any]],
    client: RobustHTTPClient | None = None,
    mailto: str = "",
    provenance: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """One topic record per paper, in corpus order, whether or not it resolved.

    Each paper is looked up by its `openalex_work_id` when it has one and by its
    `doi` otherwise — `openalex.work_to_paper` keeps both and neither carries the
    topic, which is why this request exists at all. A paper with neither is
    `no_identifier` and costs no request.

    Four outcomes, and they are four because collapsing any two of them would
    print one fact as another:

    - `topic_found` — a `primary_topic` came back, with its id, name and the
      score OpenAlex's model assigned it.
    - `no_identifier` — nothing to ask with. Nobody looked.
    - `topic_lookup_failed` — the request or the decode failed. We asked and got
      nothing back.
    - `no_topic_recorded` — OpenAlex answered and files no topic for this work.

    The year is resolved here too, since the work record carries one: the
    corpus's own `pub_year` wins when it has one and `year_source` says which was
    used, so a cell built on OpenAlex's year is visibly built on OpenAlex's year.

    Two papers sharing a work id or a DOI cost one request: the lookup is
    memoised per call. `provenance` is an out-parameter on the pattern
    `openalex.fetch_works` uses — pass a dict and the request and reuse counts
    are written into it.
    """
    record = provenance if provenance is not None else {}
    client = client or RobustHTTPClient(max_retries=3, backoff_factor=1.0, timeout=30)

    seen: dict[str, dict[str, Any]] = {}
    requests = 0
    reused = 0
    records: list[dict[str, Any]] = []

    for paper in papers:
        paper = paper if isinstance(paper, Mapping) else {}
        identity = {
            "pmid": str(paper.get("pmid") or "").strip(),
            "doi": normalise_doi(paper.get("doi")),
            "openalex_work_id": _work_id(paper.get("openalex_work_id")),
            "title": str(paper.get("title") or "").strip(),
            "citation_count": _count(paper.get(CITATION_COUNT_FIELD)),
            "citation_count_source": str(paper.get(CITATION_COUNT_SOURCE_FIELD) or "").strip(),
        }
        corpus_year = _year(paper.get("pub_year") or paper.get("pub_date"))

        work_id = identity["openalex_work_id"]
        doi = identity["doi"]
        if work_id:
            queried_by, query = "openalex_work_id", work_id
            url = f"{WORKS_URL}/{quote(work_id, safe='')}"
        elif doi:
            queried_by, query = "doi", f"doi:{doi}"
            url = f"{WORKS_URL}/doi:{quote(doi, safe='/')}"
        else:
            records.append(_topic_record(
                **identity,
                status=STATUS_NO_IDENTIFIER,
                reason=STATUS_REASONS[STATUS_NO_IDENTIFIER],
                year=corpus_year,
                year_source="corpus" if corpus_year is not None else "",
            ))
            continue

        if query in seen:
            reused += 1
            found = seen[query]
        else:
            data = _json_body(
                client.get(_with_mailto(url, mailto), accept_type="api", timeout=30,
                           extra_headers=polite_headers(mailto)),
                f"work {query}",
            )
            requests += 1
            found = {"data": data}
            seen[query] = found

        data = found["data"]
        if data is None:
            records.append(_topic_record(
                **identity,
                status=STATUS_TOPIC_LOOKUP_FAILED,
                reason=STATUS_REASONS[STATUS_TOPIC_LOOKUP_FAILED],
                queried_by=queried_by, query=query,
                year=corpus_year,
                year_source="corpus" if corpus_year is not None else "",
            ))
            continue

        topic = data.get("primary_topic")
        topic = topic if isinstance(topic, Mapping) else {}
        topic_id = _topic_id(topic.get("id"))
        api_year = _year(data.get("publication_year"))
        year = corpus_year if corpus_year is not None else api_year
        year_source = ("corpus" if corpus_year is not None
                       else ("openalex" if api_year is not None else ""))

        if not topic_id:
            records.append(_topic_record(
                **identity,
                status=STATUS_NO_TOPIC,
                reason=STATUS_REASONS[STATUS_NO_TOPIC],
                queried_by=queried_by, query=query,
                year=year, year_source=year_source,
            ))
            continue

        score = topic.get("score")
        records.append(_topic_record(
            **identity,
            status=STATUS_TOPIC_FOUND,
            topic_id=topic_id,
            topic_display_name=str(topic.get("display_name") or "").strip(),
            topic_score=float(score) if isinstance(score, (int, float)) else None,
            queried_by=queried_by, query=query,
            year=year, year_source=year_source,
        ))

    found_count = sum(1 for r in records if r["status"] == STATUS_TOPIC_FOUND)
    record.update({
        "papers": len(records),
        "topic_requests": requests,
        "topic_cache_hits": reused,
        "topics_found": found_count,
    })
    logger.info("OpenAlex 主题反查：%d/%d 篇拿到 primary_topic（%d 次请求，%d 次本轮复用）",
                found_count, len(records), requests, reused)
    return records


# ------------------------------------------------------------------
# 2. The reference cell
# ------------------------------------------------------------------


def fetch_reference_distribution(
    client: RobustHTTPClient,
    topic_id: str,
    year: Any,
    mailto: str = "",
    cache: dict | None = None,
) -> dict[str, Any]:
    """The citation-count histogram of one (topic, year), in one GET.

    `DISTRIBUTION_METHOD` states the request. The population this module uses is
    the **sum of the returned group counts**, never OpenAlex's `meta.count`: the
    two can differ, and when they do the difference is papers whose bucket was
    not returned, which is a hole in the histogram rather than a number that can
    be placed. Both travel in the result, with the gap named in `missing` and
    flagged in `truncated`, so the position computed from it can be reported as a
    band rather than a point.

    `cache` is an ordinary dict owned by the caller and keyed by
    `(topic_id, year)`. It exists so that a corpus of forty papers in one
    topic-year costs one request rather than forty. A cached cell comes back with
    `from_cache: True` and keeps **its own** `fetched_at`, on the same rule
    `citations.reusable_records` follows. Failures are cached too: forty papers
    in a cell OpenAlex cannot serve should cost one failed request, not forty.

    Returns the same keys whatever happened. `available` is whether the request
    answered; `sufficient` is whether what it answered with reaches
    `MIN_REFERENCE_POPULATION`; `status` is one of `distribution_ready`,
    `distribution_unavailable`, `distribution_too_small`. A cell that answered
    with nothing is `available` and not `sufficient`, which is a different fact
    from a cell that did not answer.
    """
    topic = _topic_id(topic_id)
    parsed_year = _year(year)
    if not topic or parsed_year is None:
        return _distribution(
            topic_id=topic, year=parsed_year,
            status=STATUS_DISTRIBUTION_UNAVAILABLE,
            reason=("no (topic, year) to ask about: "
                    f"topic={topic_id!r}, year={year!r}. Nothing was requested."),
            fetched_at=_now_stamp(),
        )

    key = (topic, parsed_year)
    if cache is not None and key in cache:
        return dict(cache[key], from_cache=True)

    works_filter = f"primary_topic.id:{topic},publication_year:{parsed_year}"
    query = f"{WORKS_URL}?filter={quote_plus(works_filter)}&group_by=cited_by_count"
    data = _json_body(
        client.get(_with_mailto(query, mailto), accept_type="api", timeout=60,
                   extra_headers=polite_headers(mailto)),
        f"group_by cited_by_count for {topic}/{parsed_year}",
    )

    if data is None:
        result = _distribution(
            topic_id=topic, year=parsed_year, query=query, fetched_at=_now_stamp(),
            status=STATUS_DISTRIBUTION_UNAVAILABLE,
            reason=(f"the group_by request for {topic} / {parsed_year} did not answer, so there "
                    "is no reference population for this cell in this run. Nothing about this "
                    "refusal refers to the papers in it."),
        )
        if cache is not None:
            cache[key] = result
        return result

    groups = data.get("group_by")
    groups = groups if isinstance(groups, list) else []
    counts: dict[int, int] = {}
    skipped = 0
    for group in groups:
        if not isinstance(group, Mapping):
            skipped += 1
            continue
        citations = _count(group.get("key"))
        papers = _count(group.get("count"))
        if citations is None or papers is None:
            # OpenAlex emits an `unknown` bucket on some fields. Its papers are
            # real but their citation count is not known here, so they are left
            # out of the population and reappear below as `missing` rather than
            # being counted as zero-citation papers.
            skipped += 1
            continue
        counts[citations] = counts.get(citations, 0) + papers

    meta = data.get("meta") if isinstance(data.get("meta"), Mapping) else {}
    reported_total = _count(meta.get("count"))
    population = sum(counts.values())
    missing = max(0, reported_total - population) if reported_total is not None else 0
    sufficient = population >= MIN_REFERENCE_POPULATION

    result = _distribution(
        topic_id=topic,
        year=parsed_year,
        counts=counts,
        population=population,
        denominator=population,
        reported_total=reported_total,
        groups_returned=len(counts),
        groups_skipped=skipped,
        max_citation_count=max(counts) if counts else None,
        truncated=missing > 0,
        missing=missing,
        available=True,
        sufficient=sufficient,
        status=STATUS_DISTRIBUTION_READY if sufficient else STATUS_DISTRIBUTION_TOO_SMALL,
        reason=("" if sufficient else
                f"the cell answered with {population} paper(s), below the floor of "
                f"{MIN_REFERENCE_POPULATION}"),
        query=query,
        fetched_at=_now_stamp(),
    )
    if cache is not None:
        cache[key] = result
    return result


# ------------------------------------------------------------------
# 3. The position
# ------------------------------------------------------------------


def _cell_fields(distribution: Mapping[str, Any] | None) -> dict[str, Any]:
    """Whatever the cell can contribute to a record, refusal or not."""
    if not isinstance(distribution, Mapping):
        return {}
    return {
        "topic_id": distribution.get("topic_id", ""),
        "year": distribution.get("year"),
        "denominator": distribution.get("population", 0),
        "population": distribution.get("population", 0),
        "reported_total": distribution.get("reported_total"),
        "missing": distribution.get("missing", 0),
        "distribution_truncated": bool(distribution.get("truncated")),
        "groups_returned": distribution.get("groups_returned", 0),
        "max_citation_count": distribution.get("max_citation_count"),
        "min_population": distribution.get("min_population", MIN_REFERENCE_POPULATION),
    }


def _located_basis(
    count: int,
    below: int,
    ties: int,
    population: int,
    percentile: float,
    bounds: tuple[float, float],
    missing: int,
    reported_total: int | None,
    topic_id: str,
    year: int | None,
    above_max: bool,
    count_source: str,
) -> str:
    """The self-describing paragraph, written to survive being quoted alone.

    Everything needed to discount the number is inside it: the cell, its size,
    what the position is a position among, the tie block, the part of the cell
    that did not come back, and the fact that the paper is normally one of the
    papers it is being compared against.
    """
    parts = [
        f"{count} citation(s) placed in OpenAlex topic {topic_id} / {year}: above {below} of "
        f"{population} papers in that cell, tied with {ties}, which is the "
        f"{percentile:.1f}th percentile under the strictly-fewer rule."
    ]
    if ties:
        parts.append(
            f"The tie block is {100.0 * ties / population:.1f}% of the cell, so a paper one "
            "citation either side of this count sits a whole block away."
        )
    if missing:
        parts.append(
            f"OpenAlex reports {reported_total} papers in this cell and returned buckets for "
            f"{population}; the {missing} unaccounted papers put the position between "
            f"{bounds[0]:.1f} and {bounds[1]:.1f} depending on where they fall."
        )
    if above_max:
        parts.append(
            "This count is higher than every bucket the cell returned, which usually means the "
            "count came from a source or a day the cell did not."
        )
    parts.append(
        f"One paper is worth {100.0 / population:.2f} percentile points here, and this paper is "
        "normally one of the papers it is being compared against."
    )
    if count_source and count_source != "openalex":
        parts.append(
            f"The count came from {count_source} and the cell from OpenAlex; the two sources "
            "disagree routinely, so the position is as good as that agreement."
        )
    return " ".join(parts)


def percentile_in_distribution(
    citation_count: Any,
    distribution: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Where `citation_count` falls inside `distribution`, or why it does not.

    Pure: no network, no file access, no clock beyond the stamp on the record.
    `distribution` is what `fetch_reference_distribution` returned; None is
    accepted and reads as "the cell did not answer".

    The definition is fixed and stated in `PERCENTILE_METHOD`: **the share of
    papers in the cell with strictly fewer citations than this one**. The
    alternative — fewer-or-equal — is rejected because these cells are discrete
    and enormously tied at the bottom, and it would award every uncited paper in
    the measured T10091/2023 cell the 36.7th percentile for being at the top of
    the zero block. Under the rule used here an uncited paper scores 0.0, which
    is the true statement that no paper in the cell has fewer citations, and
    `percentile_at_or_below` carries the other reading beside it.

    Returns the same keys whatever happened:

      located       the verdict. False for every status in
                    `UNLOCATABLE_STATUSES`, and then `percentile`,
                    `percentile_at_or_below`, `percentile_bounds` and
                    `papers_below` are all None — never 0, never 0.0.
      percentile    0.0 to 100.0. A located 0.0 is a finding: the count is at the
                    bottom of a cell that answered. That is the one value a
                    reader must be able to tell apart from a refusal, and the
                    refusals carry None precisely so they can.
      percentile_bounds  (low, high). Equal to the point estimate when the cell
                    came back whole. When it did not, the low end assumes every
                    unreturned paper outranks this one and the high end assumes
                    the opposite, so the width is the cost of the missing
                    buckets rather than a hidden assumption about them.
      denominator, population, tied_count, resolution_points_per_paper
                    the size of the cell and what one paper in it is worth.
      unlocatable   the reason, from `STATUS_REASONS`, or None when located.
      basis         one paragraph, printable verbatim.
    """
    count = _count(citation_count)
    cell = _cell_fields(distribution)

    if count is None:
        return _record(
            **cell,
            status=STATUS_NO_CITATION_COUNT,
            unlocatable=STATUS_REASONS[STATUS_NO_CITATION_COUNT],
            basis=STATUS_REASONS[STATUS_NO_CITATION_COUNT],
        )

    if not isinstance(distribution, Mapping) or not distribution.get("available"):
        reason = STATUS_REASONS[STATUS_DISTRIBUTION_UNAVAILABLE]
        detail = str((distribution or {}).get("reason") or "") if distribution else ""
        return _record(
            **cell,
            citation_count=count,
            status=STATUS_DISTRIBUTION_UNAVAILABLE,
            unlocatable=reason,
            basis=f"{reason} ({detail})" if detail else reason,
        )

    population = int(distribution.get("population") or 0)
    if not distribution.get("sufficient") or population < 1:
        reason = STATUS_REASONS[STATUS_DISTRIBUTION_TOO_SMALL]
        return _record(
            **cell,
            citation_count=count,
            status=STATUS_DISTRIBUTION_TOO_SMALL,
            unlocatable=reason,
            basis=(f"{reason} This cell holds {population} paper(s) against a floor of "
                   f"{cell.get('min_population', MIN_REFERENCE_POPULATION)}."),
        )

    counts = distribution.get("counts") or {}
    below = sum(papers for value, papers in counts.items() if value < count)
    ties = int(counts.get(count, 0))
    missing = int(distribution.get("missing") or 0)
    reported_total = distribution.get("reported_total")
    widened = population + missing

    percentile = 100.0 * below / population
    bounds = (100.0 * below / widened, 100.0 * (below + missing) / widened)
    maximum = distribution.get("max_citation_count")
    above_max = isinstance(maximum, int) and count > maximum

    return _record(
        **cell,
        status=STATUS_LOCATED,
        located=True,
        unlocatable=None,
        citation_count=count,
        percentile=percentile,
        percentile_at_or_below=100.0 * (below + ties) / population,
        percentile_bounds=bounds,
        papers_below=below,
        tied_count=ties,
        tied_fraction=ties / population,
        resolution_points_per_paper=100.0 / population,
        above_distribution_max=above_max,
        basis=_located_basis(
            count, below, ties, population, percentile, bounds, missing, reported_total,
            str(cell.get("topic_id") or ""), cell.get("year"), above_max, "",
        ),
    )


# ------------------------------------------------------------------
# 4. The whole corpus, and the denominators it is read with
# ------------------------------------------------------------------


def locate_corpus(
    papers: Sequence[Mapping[str, Any]],
    client: RobustHTTPClient | None = None,
    source_papers_json: str = "",
    mailto: str = "",
) -> dict[str, Any]:
    """Place every paper that can be placed, and name what happened to the rest.

    `records` comes back in corpus order and is never reordered by percentile —
    ordering a corpus by position is the thing `ranking` refuses and this module
    is not a way around it.

    Two economies, both visible in the denominator block:

    - A paper with no citation count costs no request. There is nothing to place,
      so its topic is not looked up and it is named `no_citation_count`. That is
      why `papers_with_topic` is read against `papers_with_citation_count` rather
      than against the corpus.
    - Each (topic, year) cell is fetched once per run and shared by every paper
      in it, failures included.

    The payload is the on-disk shape, on the pattern `citations.fetch_citations`
    and `journal_risk.fetch_journal_risk` use: `generated_at`, the method
    strings, a `denominator` block, one row per cell, one record per paper, and
    the caveats that have to travel with the numbers. Nothing is written back
    into the corpus — a position is a dated measurement against a population that
    moves, and `papers_*.json` is not.
    """
    papers = list(papers)
    owns_client = client is None
    client = client or RobustHTTPClient(max_retries=3, backoff_factor=1.0, timeout=30)

    placeable = [
        index for index, paper in enumerate(papers)
        if isinstance(paper, Mapping) and _count(paper.get(CITATION_COUNT_FIELD)) is not None
    ]
    topic_provenance: dict[str, Any] = {}
    topic_records = fetch_topics([papers[i] for i in placeable], client, mailto=mailto,
                                 provenance=topic_provenance)
    topics = dict(zip(placeable, topic_records, strict=True))

    cache: dict[tuple[str, int], dict[str, Any]] = {}
    cells: dict[tuple[str, int], dict[str, Any]] = {}
    distribution_requests = 0
    cache_hits = 0
    records: list[dict[str, Any]] = []

    for index, paper in enumerate(papers):
        paper = paper if isinstance(paper, Mapping) else {}
        topic = topics.get(index)

        if topic is None:
            records.append(_record(
                status=STATUS_NO_CITATION_COUNT,
                unlocatable=STATUS_REASONS[STATUS_NO_CITATION_COUNT],
                basis=STATUS_REASONS[STATUS_NO_CITATION_COUNT],
                pmid=str(paper.get("pmid") or "").strip(),
                doi=normalise_doi(paper.get("doi")),
                openalex_work_id=_work_id(paper.get("openalex_work_id")),
                title=str(paper.get("title") or "").strip(),
                citation_count_source=str(paper.get(CITATION_COUNT_SOURCE_FIELD) or "").strip(),
                year=_year(paper.get("pub_year") or paper.get("pub_date")),
            ))
            continue

        identity = {
            "pmid": topic["pmid"],
            "doi": topic["doi"],
            "openalex_work_id": topic["openalex_work_id"],
            "title": topic["title"],
            "citation_count": topic["citation_count"],
            "citation_count_source": topic["citation_count_source"],
            "count_source_is_reference": (
                None if not topic["citation_count_source"]
                else topic["citation_count_source"] == "openalex"
            ),
            "topic_id": topic["topic_id"],
            "topic_display_name": topic["topic_display_name"],
            "topic_score": topic["topic_score"],
            "year": topic["year"],
            "year_source": topic["year_source"],
            "queried_by": topic["queried_by"],
        }

        if topic["status"] != STATUS_TOPIC_FOUND:
            records.append(_record(
                **identity,
                status=topic["status"],
                unlocatable=STATUS_REASONS[topic["status"]],
                basis=STATUS_REASONS[topic["status"]],
            ))
            continue

        if topic["year"] is None:
            records.append(_record(
                **identity,
                status=STATUS_NO_YEAR,
                unlocatable=STATUS_REASONS[STATUS_NO_YEAR],
                basis=STATUS_REASONS[STATUS_NO_YEAR],
            ))
            continue

        key = (topic["topic_id"], topic["year"])
        distribution = fetch_reference_distribution(
            client, topic["topic_id"], topic["year"], mailto=mailto, cache=cache)
        if distribution["from_cache"]:
            cache_hits += 1
        else:
            distribution_requests += 1
            cells[key] = {
                "topic_id": distribution["topic_id"],
                "topic_display_name": topic["topic_display_name"],
                "year": distribution["year"],
                "status": distribution["status"],
                "available": distribution["available"],
                "sufficient": distribution["sufficient"],
                "population": distribution["population"],
                "denominator": distribution["denominator"],
                "reported_total": distribution["reported_total"],
                "groups_returned": distribution["groups_returned"],
                "groups_skipped": distribution["groups_skipped"],
                "truncated": distribution["truncated"],
                "missing": distribution["missing"],
                "min_population": distribution["min_population"],
                "papers_in_corpus": 0,
                "query": distribution["query"],
                "fetched_at": distribution["fetched_at"],
            }
        if key in cells:
            cells[key]["papers_in_corpus"] += 1

        position = percentile_in_distribution(topic["citation_count"], distribution)
        position.update(identity)
        if position["located"]:
            position["basis"] = _located_basis(
                position["citation_count"], position["papers_below"], position["tied_count"],
                position["population"], position["percentile"], position["percentile_bounds"],
                position["missing"], position["reported_total"], position["topic_id"],
                position["year"], position["above_distribution_max"],
                position["citation_count_source"],
            )
        records.append(position)

    by_status = {status: 0 for status in STATUS_ORDER}
    for item in records:
        by_status[item["status"]] = by_status.get(item["status"], 0) + 1

    located = by_status[STATUS_LOCATED]
    with_topic = sum(1 for r in topic_records if r["status"] == STATUS_TOPIC_FOUND)
    with_cell = sum(1 for r in records if r["status"] in
                    (STATUS_LOCATED, STATUS_DISTRIBUTION_TOO_SMALL))

    if owns_client:
        logger.debug("HTTP 请求总数: %s", client.stats.get("total_requests"))
    logger.info("引用数定位完成：%d/%d 篇给出百分位；其余按 %d 种状态分列，均不记作低百分位",
                located, len(papers), len(UNLOCATABLE_STATUSES))

    return {
        "schema_version": 1,
        "generated_at": _now_stamp(),
        "source_papers_json": source_papers_json,
        "method": PERCENTILE_METHOD,
        "distribution_method": DISTRIBUTION_METHOD,
        "reference_population_source": REFERENCE_POPULATION_SOURCE,
        "min_reference_population": MIN_REFERENCE_POPULATION,
        "statuses": list(STATUS_ORDER),
        "status_reasons": dict(STATUS_REASONS),
        "denominator": {
            "papers_total": len(papers),
            "papers_with_citation_count": len(placeable),
            # Read against the line above, not against the corpus: a paper with
            # no count is never looked up, so it cannot have a topic.
            "papers_with_topic": with_topic,
            # The cell answered at all — whether or not it was big enough.
            "papers_with_reference_cell": with_cell,
            "papers_located": located,
            "papers_unlocatable": len(papers) - located,
            "by_status": by_status,
            "reference_cells_requested": len(cells),
            "reference_cells_usable": sum(1 for c in cells.values() if c["sufficient"]),
            "topic_requests": int(topic_provenance.get("topic_requests") or 0),
            "topic_cache_hits": int(topic_provenance.get("topic_cache_hits") or 0),
            "distribution_requests": distribution_requests,
            "distribution_cache_hits": cache_hits,
        },
        "cells": list(cells.values()),
        "records": records,
        "caveats": dict(IMPACT_REFERENCE_CAVEATS),
    }


# ----------------------------------------------------------------------
# Persistence. Same shape as `journal_risk`'s, and for the same reason: a
# reference distribution is a reading of the world on one day, while a corpus is
# a list of papers. Merging the two would put today's date on last spring's
# harvest and would overwrite the previous reading of a quantity whose whole
# point is that it moves.
# ----------------------------------------------------------------------

#: Filenames this module writes and looks for.
IMPACT_REFERENCE_GLOB = "impact_reference_*.json"


def save_impact_reference_json(
    payload: dict[str, Any],
    output_dir: str,
    timestamp: str | None = None,
) -> str:
    """Write `impact_reference_<timestamp>.json` into `output_dir`; return the path."""
    stamp = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(output_dir, f"impact_reference_{stamp}.json")
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    denominator = payload.get("denominator") or {}
    logger.info("引用百分位已写入: %s (%s/%s 篇定位到参照分布)", filepath,
                denominator.get("papers_located", "?"),
                denominator.get("papers_total", "?"))
    return filepath


def find_latest_impact_reference_json(output_dir: str) -> str | None:
    """The most recently modified `impact_reference_*.json` in `output_dir`, or None."""
    files = glob.glob(os.path.join(output_dir, IMPACT_REFERENCE_GLOB))
    return max(files, key=os.path.getmtime) if files else None


def load_impact_reference_json(filepath: str) -> dict[str, Any]:
    """Read one back, filling missing keys without inventing values.

    A stored denominator that disagrees with the records is reported and left
    alone rather than recomputed, exactly as `journal_risk.load_risk_json` does:
    a recomputed denominator cannot show that a run died halfway, which is the
    one thing it would be useful for.
    """
    with open(filepath, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"impact reference JSON 顶层不是对象: {filepath}")

    records = data.get("records")
    if not isinstance(records, list):
        logger.warning("%s 缺少 records 列表，按空处理", filepath)
        records = []
    data["records"] = [record for record in records if isinstance(record, dict)]

    denominator = data.get("denominator")
    data["denominator"] = denominator if isinstance(denominator, dict) else {}
    return data
