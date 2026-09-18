"""
期刊风险信号：只取免密钥公开接口能直接 GET 到的事实
==============================================
Journal-level risk *signals* for a harvested corpus, from three free keyless
APIs. Signals, not a rating — the distinction is the whole module and it is not
negotiable.

**"Predatory" is an accusation, not a measurement.** This package's entire
credibility rests on not printing an inference as a fact, so nothing here emits
a grade, a tier, a score, a letter, a colour or a verdict. What it emits is a
list of observed statements, each carrying which endpoint said it and on what
day, in the same shape `journals.join_journals` already uses for `match_type`:
the route that produced an answer travels with the answer. Two of the signals a
naive tool would treat as damning are, measured against real journals, nothing
of the kind:

- **Not in DOAJ is not a finding.** DOAJ indexes open-access journals. Most
  subscription journals in medicine are not in it and never will be, and their
  absence says nothing at all.
- **A Crossref coverage field at 0.0 is not a finding either.** Journal of
  Hepatology — an entirely ordinary journal — deposits no abstracts for its
  backfile, so its `abstracts-backfile` is 0.0. The number says what a publisher
  deposits, not what a journal is worth.

So a reader gets `risk_signals` and decides. `JOURNAL_RISK_CAVEATS` says this
again at length and Section 18 prints it verbatim.

Why this is a separate module and a separate file on disk
---------------------------------------------------------
`journals.py` makes no network request and a test asserts its source contains
none. That is deliberate and stays: the journal metric table is a local file the
user typed, and mixing a fetch into the module that loads it would make the two
indistinguishable in the report.

DOAJ membership and Crossref deposit coverage are measurements with dates
attached; a corpus of papers is not. So they get their own
`journal_risk_<timestamp>.json`, every record carries its own `fetched_at`, and
nothing here writes back into `papers_*.json` or into the user's CSV. This is the
same argument `citations.py` makes, for the same reason, and the file shape is
deliberately the same one.

Why every source is queried, and none of them wins
--------------------------------------------------
`citations.fetch_citation_count` walks three sources and stops at the first hit,
because there is one number and any source can supply it. There is no such number
here. DOAJ, Crossref and OpenAlex answer different questions and disagree
routinely — OpenAlex carries its own `is_in_doaj`, which can lag DOAJ's own
answer — so all three are queried and every answer is kept beside the others.
That is `journals.JRN-07`'s stance applied to a second kind of table: show the
disagreement, do not resolve it.

The one list this module deliberately does not fetch
-----------------------------------------------------
**中科院国际期刊预警名单.** It is published yearly by the CAS library as a
login-walled page and a PDF, with no JSON and no CSV endpoint; `earlywarning.
fenqubiao.com` did not answer at all when this was written. It stays where it
already is — the hand-filled 是否预警 / 预警等级 columns in `journals.SCHEMA` —
because one list a year of a couple of dozen journals is cheaper to copy once
than to maintain a scraper for, and because scraping it is not permitted. Beall's
list and its mirrors are the same case with no publisher and no maintenance
promise. Nothing in this package claims either can be fetched.

All three requests say who they are
-----------------------------------
Every `get` below passes `http_client.polite_headers`, so the User-Agent is
`check-your-advisor/1.0` — plus the user's `--email` when they gave one, which
is the form Crossref's polite-pool documentation asks for. It is deliberately
**not** the rotating fake-Chrome UA the PDF download path uses. That pool exists
to get past publisher landing pages; pointing it at three keyless APIs that ask
callers to identify themselves would contradict both their documentation and
`journals.py`'s statement that what happens here is a documented API call and
not a scrape. A claim about one's own conduct has to be true in the code that
makes the request, not only in the paragraph describing it.

Standard library only. All HTTP goes through `RobustHTTPClient`.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import threading
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, quote_plus

from .http_client import RobustHTTPClient, Response, polite_headers
from .journals import normalise_issn

logger = logging.getLogger("check_your_advisor.journal_risk")

__all__ = [
    "JOURNAL_RISK_CAVEATS",
    "RISK_GLOB",
    "SIGNAL_ORDER",
    "SOURCE_CROSSREF",
    "SOURCE_DOAJ",
    "SOURCE_OPENALEX",
    "SOURCE_ORDER",
    "TRACKED_COVERAGE_FIELDS",
    "fetch_crossref_journal",
    "fetch_doaj_journal",
    "fetch_journal_risk",
    "fetch_openalex_source",
    "find_latest_risk_json",
    "index_by_issn",
    "join_risk",
    "load_risk_json",
    "reusable_records",
    "risk_record",
    "risk_targets",
    "save_risk_json",
    "worklist_cells",
]

SOURCE_DOAJ = "doaj"
SOURCE_CROSSREF = "crossref"
SOURCE_OPENALEX = "openalex"

#: Query order. Every source is queried; this decides the order signals are
#: listed in, not which one is believed.
SOURCE_ORDER: tuple[str, ...] = (SOURCE_DOAJ, SOURCE_CROSSREF, SOURCE_OPENALEX)

RISK_GLOB = "journal_risk_*.json"

# Stable machine keys, like `journals.MATCH_ISSN` and friends. Report order.
SIGNAL_DOAJ_INDEXED = "doaj_indexed"
SIGNAL_DOAJ_NOT_INDEXED = "doaj_not_indexed"
SIGNAL_DOAJ_DISCONTINUED = "doaj_discontinued"
SIGNAL_DOAJ_APC = "doaj_apc"
SIGNAL_DOAJ_REVIEW = "doaj_review_process"
SIGNAL_CROSSREF_UNKNOWN = "crossref_unknown"
SIGNAL_CROSSREF_COVERAGE = "crossref_metadata_missing"
SIGNAL_CROSSREF_DOIS = "crossref_deposited_dois"
SIGNAL_OPENALEX_UNKNOWN = "openalex_unknown"
SIGNAL_OPENALEX_DOAJ = "openalex_in_doaj"
SIGNAL_OPENALEX_SCOPUS = "openalex_indexed_in_scopus"
SIGNAL_OPENALEX_APC = "openalex_apc_usd"
SIGNAL_OPENALEX_WORKS = "openalex_works_count"

SIGNAL_ORDER: tuple[str, ...] = (
    SIGNAL_DOAJ_INDEXED,
    SIGNAL_DOAJ_NOT_INDEXED,
    SIGNAL_DOAJ_DISCONTINUED,
    SIGNAL_DOAJ_APC,
    SIGNAL_DOAJ_REVIEW,
    SIGNAL_CROSSREF_UNKNOWN,
    SIGNAL_CROSSREF_COVERAGE,
    SIGNAL_CROSSREF_DOIS,
    SIGNAL_OPENALEX_UNKNOWN,
    SIGNAL_OPENALEX_DOAJ,
    SIGNAL_OPENALEX_SCOPUS,
    SIGNAL_OPENALEX_APC,
    SIGNAL_OPENALEX_WORKS,
)

# The Crossref `coverage` keys counted for `crossref_metadata_missing`. Declared
# as a fixed list rather than "everything in the block" so the denominator is a
# constant a reader can check, and so Crossref adding a field next year does not
# silently move every number this module has ever printed.
#
# The `-current` halves only. `-backfile` measures what a publisher went back and
# deposited for content from before it joined Crossref, which is a fact about a
# migration project and not about the journal — and it is the half that reads
# alarmingly low for entirely ordinary journals.
TRACKED_COVERAGE_FIELDS: tuple[str, ...] = (
    "abstracts-current",
    "references-current",
    "licenses-current",
    "funders-current",
    "orcids-current",
    "update-policies-current",
    "similarity-checking-current",
    "resource-links-current",
    "award-numbers-current",
    "ror-ids-current",
)

_PROGRESS_EVERY = 10


# ------------------------------------------------------------------
# Decoding
# ------------------------------------------------------------------


def _json_body(resp: Response | None, source: str) -> dict[str, Any] | None:
    """Decode a JSON response body, or None with a reason in the log.

    Same shape as `citations._json_body`, and separate from it on purpose: these
    two modules fetch different things from different endpoints, and sharing a
    decoder would tie their failure handling together for the sake of nine lines.
    """
    if resp is None:
        logger.debug("  [%s] 网络层失败，无响应", source)
        return None
    if resp.status_code != 200:
        logger.debug("  [%s] HTTP %d", source, resp.status_code)
        return None
    try:
        data = json.loads(resp.content)
    except (UnicodeDecodeError, ValueError) as e:
        logger.warning("  [%s] 返回非 JSON 响应: %s", source, e)
        return None
    if not isinstance(data, dict):
        logger.warning("  [%s] JSON 顶层不是对象，实为 %s", source, type(data).__name__)
        return None
    return data


def _signal(name: str, source: str, endpoint: str, statement: str, **observed: Any) -> dict[str, Any]:
    """One observed statement about one journal.

    `observed` holds the raw values the statement was read off, so a reader who
    disbelieves the sentence can check the numbers without going back to the API.
    Nothing in this dict is a judgement; `statement` describes what the endpoint
    returned and stops there.

    The endpoint is recorded without its query string. `mailto` is the only
    parameter this module ever sends and it is the user's own address; the risk
    file is written to disk and its contents are printed into a report that gets
    mailed, so the address stays out of both. The URL left behind is still the
    one a reader can paste to check the claim.
    """
    return {
        "signal": name,
        "source": source,
        "endpoint": endpoint.split("?", 1)[0],
        "statement": statement,
        "observed": {key: value for key, value in observed.items() if value is not None},
    }


def _as_bool(value: Any) -> bool | None:
    """True / False / None, never coerced.

    None means "the field was absent or null", which is a third answer and not a
    quiet False — OpenAlex returns null for `is_indexed_in_scopus` on plenty of
    real journals, and printing that as "not in Scopus" would be inventing a
    finding out of a missing field.
    """
    return value if isinstance(value, bool) else None


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


# ------------------------------------------------------------------
# The three sources — every one queried, none of them decisive
# ------------------------------------------------------------------


def fetch_doaj_journal(client: RobustHTTPClient, issn: str) -> list[dict[str, Any]]:
    """源1: DOAJ journal search by ISSN. Keyless, public, one GET.

    `total` is 0 or 1 and that is the whole membership answer. Everything else
    returned here is DOAJ's own record of the journal's declared practice — peer
    review process, APC, whether it has been discontinued — printed as declared,
    because DOAJ collected it from the publisher and does not certify it either.

    Returns an empty list when the endpoint could not be reached, which is
    distinct from returning `doaj_not_indexed`: "nobody asked successfully" and
    "asked, and the answer was no" must never collapse into one cell.

    Identifies itself by name, like the other two — see the module docstring.
    DOAJ documents no polite pool, so no address is sent here even when the user
    supplied one; the point is that the request is not disguised, not that it
    buys anything.
    """
    if not issn:
        return []
    endpoint = f"https://doaj.org/api/search/journals/issn%3A{quote(issn, safe='')}"
    data = _json_body(client.get(endpoint, accept_type="api", timeout=30,
                                 extra_headers=polite_headers()), SOURCE_DOAJ)
    if data is None:
        return []

    total = data.get("total")
    if not isinstance(total, int):
        logger.debug("  [%s] total 字段异常: %r", SOURCE_DOAJ, total)
        return []
    if total <= 0:
        return [_signal(
            SIGNAL_DOAJ_NOT_INDEXED, SOURCE_DOAJ, endpoint,
            "未被 DOAJ 收录 — this ISSN is not in the Directory of Open Access Journals. DOAJ "
            "indexes open-access journals only, so a subscription journal is absent by "
            "construction and this is not a finding about it.",
            total=total,
        )]

    results = data.get("results")
    record = results[0] if isinstance(results, list) and results and isinstance(results[0], dict) else {}
    bibjson = record.get("bibjson") if isinstance(record.get("bibjson"), dict) else {}
    signals = [_signal(
        SIGNAL_DOAJ_INDEXED, SOURCE_DOAJ, endpoint,
        "已被 DOAJ 收录 — this ISSN is in the Directory of Open Access Journals. DOAJ applies "
        "entry criteria and does not audit a journal afterwards, so this is a membership fact "
        "and not a quality certificate.",
        total=total,
    )]

    discontinued = bibjson.get("discontinued_date")
    if discontinued:
        signals.append(_signal(
            SIGNAL_DOAJ_DISCONTINUED, SOURCE_DOAJ, endpoint,
            f"DOAJ records this journal as discontinued on {discontinued}.",
            discontinued_date=str(discontinued),
        ))

    apc = bibjson.get("apc") if isinstance(bibjson.get("apc"), dict) else {}
    has_apc = _as_bool(apc.get("has_apc"))
    if has_apc is not None:
        amounts = apc.get("max") if isinstance(apc.get("max"), list) else []
        priced = [f"{item.get('price')} {item.get('currency')}"
                  for item in amounts if isinstance(item, dict) and item.get("price") is not None]
        signals.append(_signal(
            SIGNAL_DOAJ_APC, SOURCE_DOAJ, endpoint,
            ("DOAJ records an article processing charge: " + (", ".join(priced) or "amount not stated")
             + ". An APC is how open access is normally funded and is not on its own a warning sign."
             ) if has_apc else "DOAJ records no article processing charge for this journal.",
            has_apc=has_apc, amounts=priced or None,
        ))

    review = ((bibjson.get("editorial") or {}).get("review_process")
              if isinstance(bibjson.get("editorial"), dict) else None)
    if review:
        stated = ", ".join(str(item) for item in review) if isinstance(review, list) else str(review)
        signals.append(_signal(
            SIGNAL_DOAJ_REVIEW, SOURCE_DOAJ, endpoint,
            f"DOAJ records the peer review process the publisher declared: {stated}. Declared, "
            f"not verified.",
            review_process=stated,
        ))
    return signals


def fetch_crossref_journal(client: RobustHTTPClient, issn: str, mailto: str = "") -> list[dict[str, Any]]:
    """源2: Crossref journal record by ISSN — the `coverage` block. Keyless.

    Crossref reports, per journal, the fraction of deposited records carrying
    each metadata element. That is a measurement of what a publisher deposits and
    nothing else. It is here because a publisher depositing almost no structured
    metadata is a fact worth seeing beside the rest, and it is reported as
    "N of M tracked fields are at zero" with both numbers, never as a rating.

    `mailto` is not authentication; Crossref routes requests carrying one into a
    politer pool, exactly as OpenAlex does in `citations.fetch_openalex`. It
    goes in the User-Agent as well as the query string, which is the form
    Crossref's own polite-pool documentation gives.
    """
    if not issn:
        return []
    endpoint = f"https://api.crossref.org/journals/{quote(issn, safe='')}"
    if mailto:
        endpoint += f"?mailto={quote_plus(mailto)}"
    data = _json_body(client.get(endpoint, accept_type="api", timeout=30,
                                 extra_headers=polite_headers(mailto)), SOURCE_CROSSREF)
    if data is None:
        return []

    message = data.get("message") if isinstance(data.get("message"), dict) else None
    if message is None:
        return [_signal(
            SIGNAL_CROSSREF_UNKNOWN, SOURCE_CROSSREF, endpoint,
            "Crossref 无此刊记录 — Crossref returned no journal record for this ISSN. A journal "
            "with no Crossref record deposits its DOIs elsewhere or has none; this is not a "
            "statement about the journal's standing.",
        )]

    signals: list[dict[str, Any]] = []
    coverage = message.get("coverage") if isinstance(message.get("coverage"), dict) else {}
    present = {field: _as_number(coverage.get(field))
               for field in TRACKED_COVERAGE_FIELDS if _as_number(coverage.get(field)) is not None}
    if present:
        empty = sorted(field for field, value in present.items() if value == 0.0)
        signals.append(_signal(
            SIGNAL_CROSSREF_COVERAGE, SOURCE_CROSSREF, endpoint,
            f"Crossref 元数据缺失 {len(empty)} 项（共查 {len(present)} 项）— {len(empty)} of "
            f"{len(present)} tracked Crossref metadata fields are deposited for none of this "
            f"journal's current content"
            + (f": {', '.join(empty)}. " if empty else ". ")
            + "This measures what the publisher deposits, not what the journal is worth: entirely "
              "ordinary journals sit at zero on several of these.",
            missing=len(empty), tracked=len(present), missing_fields=empty or None,
        ))

    counts = message.get("counts") if isinstance(message.get("counts"), dict) else {}
    total_dois = _as_number(counts.get("total-dois"))
    if total_dois is not None:
        signals.append(_signal(
            SIGNAL_CROSSREF_DOIS, SOURCE_CROSSREF, endpoint,
            f"Crossref holds {int(total_dois)} deposited DOI(s) for this journal, published by "
            f"{message.get('publisher') or 'a publisher Crossref did not name'}.",
            total_dois=int(total_dois), publisher=message.get("publisher") or None,
        ))
    return signals


def fetch_openalex_source(client: RobustHTTPClient, issn: str, mailto: str = "") -> list[dict[str, Any]]:
    """源3: OpenAlex `sources` by ISSN. Keyless, CC0.

    Queried alongside the other two rather than as a fallback for either, because
    OpenAlex carries its own copy of the DOAJ answer and its own Scopus indexing
    flag — and those can disagree with DOAJ's live answer. The disagreement is
    the point: both are printed and neither is preferred.
    """
    if not issn:
        return []
    endpoint = f"https://api.openalex.org/sources/issn:{quote(issn, safe='')}"
    if mailto:
        endpoint += f"?mailto={quote_plus(mailto)}"
    data = _json_body(client.get(endpoint, accept_type="api", timeout=30,
                                 extra_headers=polite_headers(mailto)), SOURCE_OPENALEX)
    if data is None:
        return []
    if not data.get("id"):
        return [_signal(
            SIGNAL_OPENALEX_UNKNOWN, SOURCE_OPENALEX, endpoint,
            "OpenAlex 无此刊记录 — OpenAlex holds no source record for this ISSN.",
        )]

    signals: list[dict[str, Any]] = []
    in_doaj = _as_bool(data.get("is_in_doaj"))
    if in_doaj is not None:
        since = data.get("is_in_doaj_since_year")
        signals.append(_signal(
            SIGNAL_OPENALEX_DOAJ, SOURCE_OPENALEX, endpoint,
            ("OpenAlex records this journal as in DOAJ" + (f" since {since}." if since else ".")
             if in_doaj else "OpenAlex records this journal as not in DOAJ.")
            + " OpenAlex's copy can lag DOAJ's own live answer, which is a separate line above; "
              "both are printed and neither is preferred.",
            is_in_doaj=in_doaj, since_year=since,
        ))

    scopus = _as_bool(data.get("is_indexed_in_scopus"))
    signals.append(_signal(
        SIGNAL_OPENALEX_SCOPUS, SOURCE_OPENALEX, endpoint,
        ("OpenAlex records this journal as indexed in Scopus." if scopus is True else
         "OpenAlex records this journal as not indexed in Scopus." if scopus is False else
         "OpenAlex has no Scopus indexing value for this journal — the field is null, which is "
         "common and is not the same as not being indexed."),
        is_indexed_in_scopus=scopus,
    ))

    apc = _as_number(data.get("apc_usd"))
    if apc is not None:
        signals.append(_signal(
            SIGNAL_OPENALEX_APC, SOURCE_OPENALEX, endpoint,
            f"OpenAlex records an article processing charge of about {int(apc)} USD.",
            apc_usd=int(apc),
        ))

    works = _as_number(data.get("works_count"))
    if works is not None:
        signals.append(_signal(
            SIGNAL_OPENALEX_WORKS, SOURCE_OPENALEX, endpoint,
            f"OpenAlex has indexed {int(works)} work(s) from this journal, published by "
            f"{data.get('host_organization_name') or 'a publisher OpenAlex did not name'}"
            + (f" in {data['country_code']}" if data.get("country_code") else "") + ".",
            works_count=int(works), publisher=data.get("host_organization_name") or None,
            country_code=data.get("country_code") or None,
        ))
    return signals


# ------------------------------------------------------------------
# Per-journal collection
# ------------------------------------------------------------------


def _now_stamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _cache_api(source: str) -> str:
    """The cache namespace for one source.

    Qualified with this module's name: `citations` asks api.openalex.org for a
    work's citation count and this module asks it for a source record, and a
    shared namespace would let one be served as the other.
    """
    return f"journal_risk.{source}"


def risk_record(
    client: RobustHTTPClient,
    journal: str,
    issn: str,
    mailto: str = "",
    paper_count: int = 0,
    cache: Any | None = None,
    max_age_days: int = 0,
) -> dict[str, Any]:
    """One journal's signals in the contract shape, whether or not anything answered.

    All three sources are queried and every answer is kept — see the module
    docstring for why there is no first-hit-wins here. A per-source exception is
    logged and the walk continues, so one malformed payload costs one source on
    one journal rather than the whole batch.

    `fetched_at` is per record, not per file: a batch takes long enough that a
    single file-level timestamp would misdate most of it.

    `cache` is an optional `cache.FetchCache`, keyed per source and per ISSN.
    Because every source is queried here rather than only the first, a cache
    saves more than it does in `citations` — and costs more care, because one
    record can be assembled from answers of different ages:

    - a source served from cache makes no request and contributes **its own**
      collection date, never today's
    - the record's `fetched_at` is then the **oldest** date that contributed, so
      a row never claims to be fresher than its stalest part
    - a source that answered nothing is not cached, for the reason
      `reusable_records` gives: DOAJ and Crossref index continuously, so an
      empty answer is a gap that may close on its own

    `max_age_days <= 0` switches the cache off, which is the default.
    """
    normalised, checksum_ok = normalise_issn(issn)
    signals: list[dict[str, Any]] = []
    sources_answered: list[str] = []
    stamps: list[str] = []
    if normalised:
        query = f"issn:{normalised}"
        attempts = (
            (SOURCE_DOAJ, lambda: fetch_doaj_journal(client, normalised)),
            (SOURCE_CROSSREF, lambda: fetch_crossref_journal(client, normalised, mailto)),
            (SOURCE_OPENALEX, lambda: fetch_openalex_source(client, normalised, mailto)),
        )
        for name, call in attempts:
            if cache is not None:
                entry = cache.get(_cache_api(name), query, max_age_days)
                if entry is not None and entry["value"]:
                    logger.debug("  ✓ [%s] 缓存命中 %s（采于 %s）",
                                 name, query, entry["fetched_at"])
                    sources_answered.append(name)
                    signals += list(entry["value"])
                    stamps.append(entry["fetched_at"])
                    continue
            try:
                found = call()
            except Exception as e:  # noqa: BLE001 - one bad payload must not end the batch
                logger.error("  [%s] 未预期的异常: %s: %s", name, type(e).__name__, e)
                continue
            if found:
                sources_answered.append(name)
                signals += found
                stamps.append(cache.put(_cache_api(name), query, found)
                              if cache is not None else _now_stamp())

    return {
        "journal": journal,
        "issn": normalised,
        "issn_raw": str(issn or ""),
        "issn_checksum_ok": checksum_ok,
        "corpus_paper_count": int(paper_count),
        # Empty for two different reasons — no ISSN to key on, or every source
        # unreachable — and `sources_answered` is what tells them apart. A reader
        # must never have to guess between "checked, nothing notable" and "never
        # checked", which is the same rule the hand-filled tables follow.
        "signals": signals,
        "sources_answered": sources_answered,
        # `min` over ISO strings of one fixed format is chronological. With no
        # cache every stamp is from this walk, so this is the moment the first
        # source answered; with one, it is the oldest answer in the row.
        "fetched_at": min(stamps) if stamps else _now_stamp(),
    }


def risk_targets(worklist: Mapping[str, Any]) -> dict[str, Any]:
    """Which journals a `journals.journal_worklist` result can actually be looked up.

    All three endpoints key on ISSN and nothing else, so a journal the corpus
    recorded without one cannot be queried at all. Those are counted and named
    rather than dropped — an unlooked-up journal and a journal with no signals
    look identical on a page unless one of them says so.
    """
    entries = list(worklist.get("entries") or [])
    with_issn: list[dict[str, Any]] = []
    without_issn: list[str] = []
    for entry in entries:
        issn, _ = normalise_issn(entry.get("issn"))
        if issn:
            with_issn.append({"journal": entry.get("journal", ""), "issn": issn,
                              "paper_count": int(entry.get("paper_count") or 0)})
        else:
            without_issn.append(str(entry.get("journal") or ""))
    return {
        "journal_denominator": len(entries),
        "targets": with_issn,
        "journals_without_issn": without_issn,
    }


def reusable_records(
    output_dir: str,
    max_age_days: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Records from the newest risk file younger than `max_age_days`, keyed by ISSN.

    Same three rules `citations.reusable_records` states, with one difference in
    the third and the reason for it:

    - `max_age_days <= 0` reuses nothing, and that is the default.
    - A carried record keeps its own `fetched_at`, never today's, so a mixed-age
      file stays self-describing per row and the report can print a span.
    - A record that **no source answered** is never carried forward. It is this
      module's equivalent of a citation miss: DOAJ and Crossref both index new
      journals continuously, so an empty answer is a gap that may close on its
      own and freezing it would make a transport failure permanent.
    """
    empty: dict[str, Any] = {"path": "", "records": {}, "max_age_days": max_age_days}
    if max_age_days <= 0:
        return empty
    path = find_latest_risk_json(output_dir)
    if not path:
        return empty
    try:
        payload = load_risk_json(path)
    except (OSError, ValueError) as exc:
        logger.warning("旧风险信号文件读不动，本次全部重抓: %s (%s)", path, exc)
        return empty

    cutoff = (now or datetime.now()) - timedelta(days=max_age_days)
    fresh: dict[str, dict[str, Any]] = {}
    for record in payload.get("records") or []:
        if not isinstance(record, dict) or not record.get("sources_answered"):
            continue
        stamp = _parse_stamp(record.get("fetched_at"))
        if stamp is None or stamp < cutoff:
            continue
        key = str(record.get("issn") or "").strip()
        if key:
            fresh[key] = dict(record)
    return {"path": path, "records": fresh, "max_age_days": max_age_days}


def _parse_stamp(value: Any) -> datetime | None:
    """An ISO timestamp, or None. Unparseable means refetch, never "recent enough"."""
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def fetch_journal_risk(
    targets: Sequence[Mapping[str, Any]],
    client: RobustHTTPClient | None = None,
    source_papers_json: str = "",
    mailto: str = "",
    max_workers: int = 4,
    reuse: Mapping[str, dict[str, Any]] | None = None,
    journals_without_issn: Sequence[str] = (),
    cache: Any | None = None,
    max_age_days: int = 0,
) -> dict[str, Any]:
    """Collect signals for every target and return the full on-disk payload.

    `records` comes back in target order — which `journal_worklist` set by paper
    count and then name — and is never re-sorted by how many signals a journal
    collected. Ordering journals by a signal count would be the rating this
    module exists not to produce.

    `cache` and `max_age_days` compose with `reuse` rather than replacing it:
    `reuse` skips a whole record the last *output file* already holds, the cache
    skips a single *source request* any earlier run already made. Both keep the
    original collection date. `max_age_days <= 0` switches the cache off, which
    is the default.
    """
    targets = list(targets)
    total = len(targets)
    reuse = dict(reuse or {})
    owns_client = client is None
    client = client or RobustHTTPClient(max_retries=3, backoff_factor=1.0, timeout=30)

    to_fetch = sum(1 for target in targets if str(target.get("issn") or "") not in reuse)
    if reuse:
        logger.info("沿用上次结果 %d 本刊，本次只查 %d 本（--max-age-days 生效；"
                    "沿用的记录保留它自己的抓取时间）", total - to_fetch, to_fetch)
    logger.info("开始采集期刊风险信号: %d 本刊，来源 %s（三个都查，不是先命中者胜）",
                to_fetch, " + ".join(SOURCE_ORDER))

    done = 0
    lock = threading.Lock()

    def _one(target: Mapping[str, Any]) -> dict[str, Any]:
        nonlocal done
        carried = reuse.get(str(target.get("issn") or ""))
        if carried is not None:
            return dict(carried)
        record = risk_record(client, str(target.get("journal") or ""),
                             str(target.get("issn") or ""), mailto=mailto,
                             paper_count=int(target.get("paper_count") or 0),
                             cache=cache, max_age_days=max_age_days)
        with lock:
            done += 1
            if done % _PROGRESS_EVERY == 0 or done == to_fetch:
                logger.info("  风险信号采集进度 %d/%d", done, to_fetch)
        return record

    if total and max_workers > 1:
        with ThreadPoolExecutor(max_workers=min(max_workers, total)) as pool:
            records = list(pool.map(_one, targets))
    else:
        records = [_one(target) for target in targets]

    if owns_client:
        logger.debug("HTTP 请求总数: %s", client.stats.get("total_requests"))

    answered = sum(1 for record in records if record["sources_answered"])
    logger.info("风险信号采集完成: %d/%d 本刊至少有一个来源应答，%d 本刊三个来源都没应答",
                answered, total, total - answered)

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_papers_json": os.path.basename(source_papers_json or ""),
        "sources": list(SOURCE_ORDER),
        "tracked_coverage_fields": list(TRACKED_COVERAGE_FIELDS),
        "denominator": {
            "journals_total": total,
            "journals_with_signals": answered,
            "journals_without_issn": len(list(journals_without_issn)),
        },
        "journals_without_issn": list(journals_without_issn),
        "records": records,
    }


# ------------------------------------------------------------------
# Disk I/O
# ------------------------------------------------------------------


def save_risk_json(payload: dict[str, Any], output_dir: str, timestamp: str | None = None) -> str:
    """Write `journal_risk_<timestamp>.json` into `output_dir`; return the path.

    Its own file, stamped with the moment the signals were taken, never merged
    into `papers_*.json` and never written into the user's hand-filled CSV. DOAJ
    membership and Crossref coverage move; a corpus does not.
    """
    stamp = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(output_dir, f"journal_risk_{stamp}.json")
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    denominator = payload.get("denominator") or {}
    logger.info("期刊风险信号已写入: %s (%s/%s 本刊有信号)", filepath,
                denominator.get("journals_with_signals", "?"),
                denominator.get("journals_total", "?"))
    return filepath


def find_latest_risk_json(output_dir: str) -> str | None:
    """The most recently modified `journal_risk_*.json` in `output_dir`, or None."""
    files = glob.glob(os.path.join(output_dir, RISK_GLOB))
    return max(files, key=os.path.getmtime) if files else None


def load_risk_json(filepath: str) -> dict[str, Any]:
    """Read a risk file back, filling missing keys without inventing values.

    A stored denominator that disagrees with the records is reported and left
    alone rather than recomputed: a recomputed denominator cannot show that a run
    died halfway, which is the one thing it would be useful for.
    """
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"journal risk JSON 顶层不是对象: {filepath}")

    records = data.get("records")
    if not isinstance(records, list):
        logger.warning("%s 缺少 records 列表，按空处理", filepath)
        records = []
    records = [r for r in records if isinstance(r, dict)]

    denominator = data.get("denominator")
    if not isinstance(denominator, dict):
        denominator = {}
    stored = denominator.get("journals_with_signals")
    actual = sum(1 for r in records if r.get("sources_answered"))
    if isinstance(stored, int) and stored != actual:
        logger.warning("%s 的 denominator 与 records 不符: 记录称 %d 本刊有信号，实际 %d 本",
                       filepath, stored, actual)

    return {
        "generated_at": data.get("generated_at") or "",
        "source_papers_json": data.get("source_papers_json") or "",
        "sources": list(data.get("sources") or SOURCE_ORDER),
        "tracked_coverage_fields": list(data.get("tracked_coverage_fields")
                                        or TRACKED_COVERAGE_FIELDS),
        "denominator": denominator,
        "journals_without_issn": list(data.get("journals_without_issn") or []),
        "records": records,
    }


def index_by_issn(payload: dict[str, Any] | Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Map ISSN -> record, for joining signals onto a journal list.

    Accepts the whole payload or a bare `records` list. Records with no ISSN are
    dropped: they cannot be joined to anything, and keeping them under an empty
    key would collapse them onto each other.
    """
    records = payload.get("records") if isinstance(payload, dict) else payload
    if not isinstance(records, (list, tuple)):
        return {}
    index: dict[str, dict[str, Any]] = {}
    duplicates = unkeyed = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        issn = str(record.get("issn") or "").strip()
        if not issn:
            unkeyed += 1
            continue
        if issn in index:
            duplicates += 1
        index[issn] = record
    if duplicates:
        logger.warning("风险信号记录中有 %d 条重复 ISSN，保留最后一条", duplicates)
    if unkeyed:
        logger.debug("风险信号记录中有 %d 条无 ISSN，无法与语料关联", unkeyed)
    return index


# ------------------------------------------------------------------
# Join
# ------------------------------------------------------------------


def worklist_cells(
    payload: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None,
) -> dict[str, dict[str, str]]:
    """Journal name -> the two worklist cells `journals.write_worklist_csv` writes.

    The signal *names* only, semicolon-joined, plus the day they were read. The
    full sentences stay in the JSON and in Section 18: a CSV cell holding five
    paragraphs is a cell that breaks every spreadsheet it is opened in, and the
    person filling the rest of the row needs to know which journals to look at
    harder rather than to read the whole argument in a column.

    The date is written unconditionally beside the names, including when there
    are none. "Checked on this day, nothing came back" and "never checked" are
    different cells, and a blank in a hand-edited file is always the second one.
    """
    records = payload.get("records") if isinstance(payload, Mapping) else payload
    if not isinstance(records, (list, tuple)):
        return {}
    cells: dict[str, dict[str, str]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        name = str(record.get("journal") or "").strip()
        if not name or not record.get("sources_answered"):
            continue
        names = [str(item.get("signal") or "") for item in (record.get("signals") or [])
                 if isinstance(item, dict)]
        cells[name] = {
            "risk_signals": "; ".join(name for name in names if name),
            "risk_checked_on": str(record.get("fetched_at") or "")[:10],
        }
    return cells


def join_risk(
    journals: Mapping[str, Any] | None,
    payload: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Attach collected signals to a `journals.join_journals` result.

    Lives here and not in `journals.py` because that module is asserted to
    contain no fetch and no reference to one; a join is not a fetch, but the
    thing being joined is dated network data and it belongs beside the code that
    dated it.

    `payload=None` is a supported call and returns the full shape with
    `risk_missing: True`, so Section 18 can print "未采集" in the column instead
    of a blank the reader has to interpret. That is the same contract
    `join_journals(table=None)` honours, for the same reason.

    Matching is by ISSN only. There is deliberately no name fallback: the whole
    point of a risk signal is that it is about a specific journal, and a fuzzy
    name match would attach a real-looking statement to the wrong one.
    """
    journal_results = list((journals or {}).get("journals") or [])
    index = index_by_issn(payload) if payload is not None else {}
    stamps = sorted(str(record.get("fetched_at") or "")
                    for record in index.values() if record.get("fetched_at"))

    rows: list[dict[str, Any]] = []
    matched = 0
    for result in journal_results:
        issn, _ = normalise_issn(result.get("corpus_issn"))
        record = index.get(issn) if issn else None
        if record is not None:
            matched += 1
        signals = list((record or {}).get("signals") or [])
        rows.append({
            "journal": result.get("journal", ""),
            "issn": issn,
            "paper_count": int(result.get("paper_count") or 0),
            "checked": record is not None,
            "sources_answered": list((record or {}).get("sources_answered") or []),
            "fetched_at": (record or {}).get("fetched_at", ""),
            "signals": signals,
            # Names only, for the table cell. The sentences live in `signals` and
            # are printed in full below the table — a cell wide enough for a
            # paragraph is a cell nobody reads.
            "signal_names": [str(item.get("signal") or "") for item in signals],
        })

    without_issn = [row["journal"] for row in rows if not row["issn"]]
    by_signal: dict[str, int] = {}
    for row in rows:
        for name in row["signal_names"]:
            by_signal[name] = by_signal.get(name, 0) + 1

    result = {
        "risk_missing": payload is None,
        "journal_denominator": len(journal_results),
        "journals_checked": matched,
        "journals_unchecked": len(journal_results) - matched,
        "journals_without_issn": without_issn,
        "rows": rows,
        # Counts against the journal denominator, never a share: the n>=20 rule
        # that decides whether a percentage may be shown at all lives in
        # `profile.metrics.percent` and is the renderer's to apply. A corpus uses
        # a couple of dozen journals, so it is almost always below that floor.
        "signal_counts": {name: by_signal[name] for name in SIGNAL_ORDER if name in by_signal},
        "provenance": {
            "sources": list((payload or {}).get("sources") or SOURCE_ORDER)
            if isinstance(payload, Mapping) else list(SOURCE_ORDER),
            "tracked_coverage_fields": list(
                (payload or {}).get("tracked_coverage_fields") or TRACKED_COVERAGE_FIELDS
            ) if isinstance(payload, Mapping) else list(TRACKED_COVERAGE_FIELDS),
            "generated_at": (payload or {}).get("generated_at", "")
            if isinstance(payload, Mapping) else "",
            "fetched_at_range": (stamps[0], stamps[-1]) if stamps else None,
            "records_in_file": len(index),
        },
    }
    if payload is None:
        logger.info("未采集期刊风险信号，%d 本刊的风险信号列全部留空", len(journal_results))
    else:
        logger.info("期刊风险信号 join: %d/%d 本刊有信号（按 ISSN 匹配，不做刊名模糊匹配）",
                    matched, len(journal_results))
    return result


# ------------------------------------------------------------------
# What these signals cannot mean
# ------------------------------------------------------------------
#
# Register style borrowed from `journals.JOURNAL_CAVEATS`, continuing its
# numbering: module constants, printed verbatim, never paraphrased at the call
# site. English, like `CAVEATS` and `JOURNAL_CAVEATS`.

JOURNAL_RISK_CAVEATS: dict[str, str] = {
    "JRN-08": (
        "Nothing in this block calls any journal predatory, and nothing in it should be read as "
        "doing so. Every line is one statement an open API returned about one ISSN on one day, "
        "printed with the endpoint that said it and the date it was read. \"Predatory\" is an "
        "accusation about a publisher's conduct; none of these three sources makes that "
        "accusation, and neither does this toolkit. No grade, tier, score, letter or colour is "
        "produced from these signals at any number of them, and they contribute nothing to the "
        "composite score in Section 16."
    ),
    "JRN-09": (
        "Absence from DOAJ is not a finding. DOAJ indexes open-access journals that applied to it, "
        "so most subscription journals in medicine are absent by construction and always will be; "
        "reading their absence as a warning would flag a large part of the ordinary literature. "
        "Presence is not a certificate either: DOAJ applies entry criteria and does not audit a "
        "journal afterwards, and it records what a publisher declared about peer review and "
        "charges rather than what a publisher does."
    ),
    "JRN-10": (
        "A Crossref coverage number measures what a publisher deposits, not what a journal is "
        "worth. Journal of Hepatology deposits no abstracts for its backfile, so that field reads "
        "0.0 for a journal nobody would question. Only the current-content fields are counted "
        "here, the list of them is fixed and printed, and the count is always reported as N of M "
        "so the denominator travels with it. A journal absent from Crossref altogether deposits "
        "its DOIs elsewhere or has none, which is again not a statement about its standing."
    ),
    "JRN-11": (
        "The 中科院国际期刊预警名单 is not fetched and cannot be. It is published once a year as a "
        "login-walled page and a PDF with no JSON or CSV endpoint, so it stays in the hand-filled "
        "是否预警 and 预警等级 columns of the journal table, where a person copies it once a year. "
        "Beall's list and its mirrors are the same case with no publisher and no maintenance "
        "promise behind them. A blank 是否预警 cell means nobody checked, not that a journal is "
        "absent from the list — the two are printed differently and must not be read as one."
    ),
    "JRN-12": (
        "The three sources disagree with each other and the disagreement is shown rather than "
        "resolved. OpenAlex carries its own copy of the DOAJ membership flag which can lag DOAJ's "
        "live answer, and its Scopus indexing field is null for a great many real journals — null "
        "is a third value here and is never printed as \"not indexed\". Where two sources say "
        "different things, both lines are printed with their endpoints and dates and neither is "
        "preferred, exactly as JRN-07 handles two editions of a partition table."
    ),
}
