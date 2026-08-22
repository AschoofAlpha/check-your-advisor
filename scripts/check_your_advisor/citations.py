"""
引用数抓取模块
==============
Citation counts for a harvested corpus, from three free keyless APIs.

Scope, stated before the code because this module deliberately reverses a rule
the rest of the package used to state absolutely:

- **Citation counts are now fetched, computed on, and written to disk.** So is
  h-index, downstream in `profile/impact.py`. That is a change of position, not
  an oversight.
- **Ordering is still not produced anywhere.** This module never sorts records
  by `citation_count`, never assigns a rank, percentile, letter grade or star,
  and never compares one researcher's numbers with another's. `records` is
  emitted in corpus order — the same order the papers arrived in — so the file
  cannot be read as a league table even by accident. A value may be printed; a
  position may not.
- **Journal impact factor and JCR / CAS quartile are not implemented here, and
  the reason is data availability, not principle.** There is no free,
  redistributable source for either; both are licensed products. Nothing in
  this module would have to be argued with if one were ever licensed — the
  fields simply do not exist yet because the data cannot be obtained legally
  for free. Read that as "cannot get it", not as "refuse to do it".

Freshness, and why this file stands alone
-----------------------------------------
A citation count is a measurement with a date attached; a corpus of papers is
not. So counts get their own `citations_<timestamp>.json`, every record carries
its own `fetched_at`, and nothing here writes back into `papers_*.json`. Merging
the two would hand a corpus that stays valid for years the shelf life of a
number that is stale next month, and would silently invalidate every existing
harvest on disk.

Fallback chain
--------------
Per paper, tried in order, first hit wins:

  1. OpenAlex          `cited_by_count`
  2. Semantic Scholar  `citationCount`
  3. Europe PMC        `citedByCount`

That order is coverage and rate-limit tolerance, not a quality judgement about
the providers. Which one answered is recorded per record in `source`, because
the three do not agree with each other and a reader needs to know which
convention produced the figure. All three failing is recorded as
`citation_count: None` and counted against the denominator rather than dropped.

Standard library only, like the rest of the package: all HTTP goes through
`RobustHTTPClient`, whose `.get()` returns None on a transport failure and
returns HTTP error codes as a `Response`.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import threading
from collections import Counter
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, quote_plus

from .http_client import RobustHTTPClient, Response

logger = logging.getLogger("check_your_advisor.citations")

__all__ = [
    "SOURCE_OPENALEX",
    "SOURCE_SEMANTIC_SCHOLAR",
    "SOURCE_EUROPE_PMC",
    "SOURCE_ORDER",
    "normalise_doi",
    "fetch_openalex",
    "fetch_semantic_scholar",
    "fetch_europe_pmc",
    "fetch_citation_count",
    "citation_record",
    "fetch_citations",
    "reusable_records",
    "save_citations_json",
    "find_latest_citations_json",
    "load_citations_json",
    "index_by_pmid",
]

SOURCE_OPENALEX = "openalex"
SOURCE_SEMANTIC_SCHOLAR = "semantic_scholar"
SOURCE_EUROPE_PMC = "europe_pmc"

#: Fallback order. Not a ranking of the providers — see the module docstring.
SOURCE_ORDER: tuple[str, ...] = (
    SOURCE_OPENALEX,
    SOURCE_SEMANTIC_SCHOLAR,
    SOURCE_EUROPE_PMC,
)

CITATIONS_GLOB = "citations_*.json"

# Every prefix a DOI arrives wearing, longest first so the `https://dx.` form is
# not half-stripped by the `https://` one.
_DOI_PREFIXES = (
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "https://doi.org/",
    "http://doi.org/",
    "dx.doi.org/",
    "doi.org/",
    "doi:",
)

_PROGRESS_EVERY = 25


# ------------------------------------------------------------------
# Normalisation and decoding
# ------------------------------------------------------------------


def normalise_doi(value: Any) -> str:
    """Strip resolver prefixes so a DOI can be pasted into an API path.

    PubMed emits bare `10.1038/xyz`, but a corpus assembled by hand or merged
    from a reference manager carries `https://doi.org/10.1038/xyz` and
    `doi:10.1038/xyz` too. Sending those verbatim produces a 404 from all three
    APIs, which is indistinguishable from "this paper is genuinely unknown" —
    the paper would be recorded as uncovered for a reason that is entirely ours.
    """
    doi = str(value or "").strip()
    lowered = doi.lower()
    for prefix in _DOI_PREFIXES:
        if lowered.startswith(prefix):
            doi = doi[len(prefix):]
            break
    return doi.strip().rstrip(".")


def _json_body(resp: Response | None, source: str) -> dict[str, Any] | None:
    """Decode a JSON response body, or None with a reason in the log.

    `RobustHTTPClient.Response` carries `status_code`, `headers` and raw
    `content` and nothing else — there is no `.json()` helper on it — so
    decoding happens here. Every failure mode is caught by name and logged;
    none is swallowed.
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


def _coerce_count(value: Any, source: str) -> int | None:
    """A citation count, or None when the field is missing or unusable.

    Zero is a real answer — a paper published last month honestly has none — so
    this returns 0 and every caller tests `is not None` rather than truthiness.
    Treating 0 as a miss would walk the entire fallback chain for the newest
    papers and then file them as uncovered, understating coverage exactly where
    the corpus is youngest.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        count = value
    elif isinstance(value, str) and value.strip().lstrip("-").isdigit():
        count = int(value.strip())
    else:
        logger.debug("  [%s] 引用数字段类型异常: %r", source, value)
        return None
    if count < 0:
        logger.warning("  [%s] 引用数为负 (%d)，按缺失处理", source, count)
        return None
    return count


# ------------------------------------------------------------------
# The three sources
# ------------------------------------------------------------------


def fetch_openalex(client: RobustHTTPClient, doi: str, mailto: str = "") -> int | None:
    """源1: OpenAlex — `cited_by_count`. Keyless, CC0 data.

    `mailto` is optional and is not authentication: OpenAlex routes requests
    carrying one into a faster pool with looser limits. Omitted when empty, so
    the default request is exactly the documented anonymous one.
    """
    if not doi:
        return None
    url = f"https://api.openalex.org/works/doi:{quote(doi, safe='/')}"
    if mailto:
        url += f"?mailto={quote_plus(mailto)}"
    data = _json_body(client.get(url, accept_type="api", timeout=30), SOURCE_OPENALEX)
    if data is None:
        return None
    return _coerce_count(data.get("cited_by_count"), SOURCE_OPENALEX)


def fetch_semantic_scholar(client: RobustHTTPClient, doi: str) -> int | None:
    """源2: Semantic Scholar Graph API — `citationCount`. Keyless.

    The unauthenticated pool is shared and rate-limits hard; the 429 handling
    (Retry-After, then exponential backoff) lives in `RobustHTTPClient` and is
    not repeated here.
    """
    if not doi:
        return None
    url = (
        f"https://api.semanticscholar.org/graph/v1/paper/DOI:{quote(doi, safe='/')}"
        f"?fields=citationCount"
    )
    data = _json_body(client.get(url, accept_type="api", timeout=30), SOURCE_SEMANTIC_SCHOLAR)
    if data is None:
        return None
    return _coerce_count(data.get("citationCount"), SOURCE_SEMANTIC_SCHOLAR)


def fetch_europe_pmc(client: RobustHTTPClient, doi: str, pmid: str = "") -> int | None:
    """源3: Europe PMC REST search — `citedByCount`. Keyless.

    The last resort, and the only one of the three that can answer from a PMID
    alone, which matters because a PubMed corpus always has PMIDs and only
    mostly has DOIs. The query is field-qualified (`EXT_ID`/`SRC`, or a quoted
    `DOI`) rather than free text: an unqualified search matches the DOI string
    appearing anywhere in a record, including in another paper's reference list.
    """
    if pmid:
        query = f"EXT_ID:{pmid} AND SRC:MED"
    elif doi:
        query = f'DOI:"{doi}"'
    else:
        return None
    url = (
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        f"?query={quote_plus(query)}&format=json&resultType=core&pageSize=1"
    )
    data = _json_body(client.get(url, accept_type="api", timeout=30), SOURCE_EUROPE_PMC)
    if data is None:
        return None

    result_list = data.get("resultList")
    results = result_list.get("result") if isinstance(result_list, dict) else None
    if not isinstance(results, list):
        results = []
    for record in results:
        if not isinstance(record, dict):
            continue
        count = _coerce_count(record.get("citedByCount"), SOURCE_EUROPE_PMC)
        if count is not None:
            return count
    logger.debug("  [%s] 无匹配记录: %s", SOURCE_EUROPE_PMC, query)
    return None


# ------------------------------------------------------------------
# Per-paper fallback chain
# ------------------------------------------------------------------


def fetch_citation_count(
    client: RobustHTTPClient,
    doi: str = "",
    pmid: str = "",
    mailto: str = "",
) -> tuple[int | None, str | None]:
    """Walk the three sources in order; return `(count, source)` at the first hit.

    Returns `(None, None)` when all three miss. A per-source exception is
    logged and the chain continues, so one malformed payload costs one source
    on one paper rather than the whole batch.
    """
    doi = normalise_doi(doi)
    pmid = str(pmid or "").strip()

    attempts = (
        (SOURCE_OPENALEX, lambda: fetch_openalex(client, doi, mailto)),
        (SOURCE_SEMANTIC_SCHOLAR, lambda: fetch_semantic_scholar(client, doi)),
        (SOURCE_EUROPE_PMC, lambda: fetch_europe_pmc(client, doi, pmid)),
    )
    for name, call in attempts:
        try:
            count = call()
        except Exception as e:  # noqa: BLE001 - one bad payload must not end the batch
            logger.error("  [%s] 未预期的异常: %s: %s", name, type(e).__name__, e)
            continue
        if count is not None:
            logger.debug("  ✓ [%s] %s → %d", name, doi or pmid or "?", count)
            return count, name
    return None, None


def citation_record(
    client: RobustHTTPClient,
    paper: dict[str, Any],
    mailto: str = "",
) -> dict[str, Any]:
    """One record in the contract shape, whether or not the lookup succeeded.

    `fetched_at` is per record, not per file: a batch of 500 papers takes long
    enough that a single file-level timestamp would misdate most of it.
    """
    doi = normalise_doi(paper.get("doi"))
    pmid = str(paper.get("pmid") or "").strip()
    count, source = fetch_citation_count(client, doi=doi, pmid=pmid, mailto=mailto)
    if count is None:
        logger.debug(
            "  ✗ PMID %s / DOI %s — 三级源均未返回引用数",
            pmid or "?", doi or "?",
        )
    return {
        "pmid": pmid,
        "doi": doi,
        "citation_count": count,
        "source": source,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


def reusable_records(
    output_dir: str,
    max_age_days: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Counts from the newest citations file that are younger than `max_age_days`.

    Re-running `cite` on a 500-paper corpus refetches all 500, at roughly a
    second of deliberate rate-limit sleep each. This is the opt-out: counts
    already taken recently enough are carried forward and only the rest are
    fetched.

    Three rules, each of which is the honest form of a shortcut:

    - `max_age_days <= 0` reuses nothing. That is the default, because a
      citation count moves every week and silently serving a stale one would
      undercut the reason this file is dated at all.
    - A carried-forward record keeps its **own** `fetched_at` and `source`,
      never today's. A mixed-age file is then self-describing at the record
      level, which is what lets the report print a range rather than one date
      that would be true of only some rows.
    - A previous **miss** is never carried forward. `citation_count: None` means
      three sources had nothing, and coverage is the one thing that improves on
      its own as sources index more; reusing a miss would freeze a gap that a
      refetch might close.
    """
    empty: dict[str, Any] = {"path": "", "records": {}, "max_age_days": max_age_days}
    if max_age_days <= 0:
        return empty
    path = find_latest_citations_json(output_dir)
    if not path:
        return empty
    try:
        payload = load_citations_json(path)
    except (OSError, ValueError) as exc:
        logger.warning("旧引用数文件读不动，本次全部重抓: %s (%s)", path, exc)
        return empty

    cutoff = (now or datetime.now()) - timedelta(days=max_age_days)
    fresh: dict[str, dict[str, Any]] = {}
    for record in payload.get("records") or []:
        if not isinstance(record, dict) or record.get("citation_count") is None:
            continue
        stamp = _parse_stamp(record.get("fetched_at"))
        if stamp is None or stamp < cutoff:
            continue
        key = str(record.get("pmid") or "").strip()
        if key:
            fresh[key] = dict(record)
    return {"path": path, "records": fresh, "max_age_days": max_age_days}


def _parse_stamp(value: Any) -> datetime | None:
    """An ISO timestamp, or None for anything that is not one.

    Unparseable means "refetch": a record whose age cannot be established is not
    a record whose age is acceptable.
    """
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def fetch_citations(
    papers: Sequence[dict[str, Any]],
    client: RobustHTTPClient | None = None,
    source_papers_json: str = "",
    mailto: str = "",
    max_workers: int = 4,
    reuse: Mapping[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Fetch a count for every paper and return the full on-disk payload.

    `records` comes back in corpus order, never sorted by count — see the module
    docstring. `denominator` is emitted alongside, following the convention the
    metric layer already uses: no figure travels without the population it was
    computed over, so a reader cannot mistake "12 papers cited" for "12 of 12".

    `mailto` is passed to OpenAlex only; `config["email"]` is the natural
    source for it. `source_papers_json` is stored as a bare filename so the
    payload stays portable across machines.

    `reuse` maps PMID to a record carried forward from an earlier run — see
    `reusable_records`. Carried records are emitted verbatim, keeping their own
    `fetched_at`, so the payload's shape is unchanged and the age of every count
    stays readable per row. Papers absent from it are fetched normally.
    """
    papers = list(papers)
    total = len(papers)
    reuse = dict(reuse or {})
    owns_client = client is None
    client = client or RobustHTTPClient(max_retries=3, backoff_factor=1.0, timeout=30)

    to_fetch = sum(1 for paper in papers if str(paper.get("pmid") or "").strip() not in reuse)
    if reuse:
        logger.info("沿用上次结果 %d 篇，本次只抓 %d 篇（--max-age-days 生效；"
                    "沿用的记录保留它自己的抓取时间，不会被记成今天）",
                    total - to_fetch, to_fetch)
    logger.info("开始抓取引用数: %d 篇，来源顺序 %s", to_fetch, " → ".join(SOURCE_ORDER))

    done = 0
    lock = threading.Lock()

    def _one(paper: dict[str, Any]) -> dict[str, Any]:
        nonlocal done
        carried = reuse.get(str(paper.get("pmid") or "").strip())
        if carried is not None:
            return dict(carried)
        record = citation_record(client, paper, mailto=mailto)
        with lock:
            done += 1
            if done % _PROGRESS_EVERY == 0 or done == to_fetch:
                logger.info("  引用数抓取进度 %d/%d", done, to_fetch)
        return record

    # `pool.map` preserves input order, which is what keeps the output in corpus
    # order without a sort anywhere in this module.
    if total and max_workers > 1:
        with ThreadPoolExecutor(max_workers=min(max_workers, total)) as pool:
            records = list(pool.map(_one, papers))
    else:
        records = [_one(paper) for paper in papers]

    if owns_client:
        logger.debug("HTTP 请求总数: %s", client.stats.get("total_requests"))

    covered = sum(1 for record in records if record["citation_count"] is not None)
    logger.info("引用数抓取完成: %d/%d 有数据，%d 篇三级源均未命中", covered, total, total - covered)
    by_source = Counter(record["source"] for record in records if record["source"])
    for name in SOURCE_ORDER:
        if by_source.get(name):
            logger.info("  命中来源 %s: %d 篇", name, by_source[name])

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_papers_json": os.path.basename(source_papers_json or ""),
        "denominator": {"papers_total": total, "papers_with_citations": covered},
        "records": records,
    }


# ------------------------------------------------------------------
# Disk I/O
# ------------------------------------------------------------------


def save_citations_json(
    payload: dict[str, Any],
    output_dir: str,
    timestamp: str | None = None,
) -> str:
    """Write `citations_<timestamp>.json` into `output_dir`; return the path.

    The timestamp format matches `papers_<timestamp>.json` (`%Y%m%d_%H%M%S`) so
    a harvest and its counts sit next to each other in a directory listing.
    Pass the harvest's own timestamp to pair the two files exactly.
    """
    stamp = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(output_dir, f"citations_{stamp}.json")
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    denominator = payload.get("denominator") or {}
    logger.info(
        "引用数已写入: %s (%s/%s 篇有数据)",
        filepath,
        denominator.get("papers_with_citations", "?"),
        denominator.get("papers_total", "?"),
    )
    return filepath


def find_latest_citations_json(output_dir: str) -> str | None:
    """The most recently modified `citations_*.json` in `output_dir`, or None.

    Mirrors `corpus.find_latest_json` deliberately, mtime and all: rerunning a
    fetch against an old corpus is normal here, so the newest *file* is the
    newest set of counts even when its name carries an older stamp.
    """
    files = glob.glob(os.path.join(output_dir, CITATIONS_GLOB))
    return max(files, key=os.path.getmtime) if files else None


def load_citations_json(filepath: str) -> dict[str, Any]:
    """Read a citations file back, filling missing keys without inventing values.

    Absent metadata becomes empty rather than plausible: no generated_at is
    fabricated and no denominator is recomputed from the records, because a
    recomputed denominator cannot show that a run died halfway. A stored
    denominator that disagrees with the records is reported, not corrected.
    """
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"citations JSON 顶层不是对象: {filepath}")

    records = data.get("records")
    if not isinstance(records, list):
        logger.warning("%s 缺少 records 列表，按空处理", filepath)
        records = []
    records = [r for r in records if isinstance(r, dict)]

    denominator = data.get("denominator")
    if not isinstance(denominator, dict):
        denominator = {}

    stored = denominator.get("papers_with_citations")
    actual = sum(1 for r in records if r.get("citation_count") is not None)
    if isinstance(stored, int) and stored != actual:
        logger.warning(
            "%s 的 denominator 与 records 不符: 记录称 %d 篇有数据，实际 %d 篇",
            filepath, stored, actual,
        )

    return {
        "generated_at": data.get("generated_at") or "",
        "source_papers_json": data.get("source_papers_json") or "",
        "denominator": denominator,
        "records": records,
    }


def index_by_pmid(citations: dict[str, Any] | Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Map PMID -> record, for joining counts onto a paper list.

    Accepts either the whole payload or a bare `records` list, since the two
    are equally natural things for a caller to be holding. Records without a
    PMID are dropped: they cannot be joined to a PubMed corpus, and keeping
    them under an empty key would collapse them onto each other.
    """
    records = citations.get("records") if isinstance(citations, dict) else citations
    if not isinstance(records, (list, tuple)):
        return {}

    index: dict[str, dict[str, Any]] = {}
    duplicates = 0
    unkeyed = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        pmid = str(record.get("pmid") or "").strip()
        if not pmid:
            unkeyed += 1
            continue
        if pmid in index:
            duplicates += 1
        index[pmid] = record
    if duplicates:
        logger.warning("引用数记录中有 %d 条重复 PMID，保留最后一条", duplicates)
    if unkeyed:
        logger.debug("引用数记录中有 %d 条无 PMID，无法与语料关联", unkeyed)
    return index
