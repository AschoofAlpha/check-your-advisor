"""
OpenAlex 作者消歧与第二语料源
================================
Two things this module does, and one it deliberately does not.

**1. Author disambiguation.** `resolve_author` asks the OpenAlex authors API for
the people who publish under a name (optionally narrowed by institution) and
brings back their OpenAlex author id, their ORCID if OpenAlex holds one, their
institution history and their works count. That is a *third party's* opinion
about who this person is, and it is recorded as such: the corpus keeps it under
`identity.openalex` with `source: openalex-authors-api` and the date it was
fetched, beside — never merged into — the ORCID / email domain / affiliation
keyword the user asserted themselves. The two answer different questions. A user
saying "my ORCID is X" is a fact about the user; OpenAlex saying "these 213
works are one person" is the output of a clustering algorithm that is right most
of the time and wrong in a way nobody can see from the report.

**2. A second corpus source.** `fetch_works` pulls the works OpenAlex files
under one author id, `work_to_paper` converts each into the same paper dict
`pubmed_api.parse_article` produces, and `merge_corpora` folds them into a
PubMed corpus, deduplicating on DOI, then PMID, then normalised title + year.
Every surviving record says which source it came from and which sources hold it,
so the report can print two denominators instead of one blended number.

**What it does not do: pick for you.** When the name resolves to more than one
candidate, nothing is selected. The candidates are returned, logged and written
into the corpus, and the harvest carries on under PubMed alone. Choosing the
most productive candidate would be exactly the same error as accepting a bare
name match — it decides an identity question on a proxy — and it would be
invisible afterwards, because the report would show one author id with no sign
that there had been a choice.

No key. OpenAlex is free and keyless; `mailto` is not authentication, it only
moves the request into a faster rate-limit pool, and it is omitted when empty.
Same convention `citations.fetch_openalex` already uses.

Both requests here identify themselves. `http_client.polite_headers` puts
`check-your-advisor/1.0` — plus the user's `--email` when there is one — in the
User-Agent, in place of the rotating fake-browser UA the download path uses.
OpenAlex's polite pool asks callers to say who they are; rotating a Chrome
string at it would be the opposite of that, and this package states elsewhere
that its API traffic is documented API calls rather than scraping.

All HTTP goes through `RobustHTTPClient`, so this module is import-safe for
anything outside `profile/` but must never be imported *by* `profile/` — the
offline report depends on that. The pure identity helper the profile layer needs
(`normalise_openalex_id`) lives in `pubmed_api` for exactly that reason.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from urllib.parse import quote_plus

from .http_client import RobustHTTPClient, Response, polite_headers
from .pubmed_api import (
    _MARK_OPENALEX,
    OPENALEX_IDS_FIELD,
    normalise_openalex_id,
    record_openalex_author_ids,
)

logger = logging.getLogger("check_your_advisor.openalex")

__all__ = [
    "SOURCE_PUBMED",
    "SOURCE_OPENALEX",
    "AUTHORS_URL",
    "WORKS_URL",
    "MAX_WORKS",
    "WORKS_PAGE_SIZE",
    "normalise_openalex_id",
    "author_candidate",
    "resolve_author",
    "work_to_paper",
    "fetch_works",
    "merge_corpora",
]

SOURCE_PUBMED = "pubmed"
SOURCE_OPENALEX = "openalex"

AUTHORS_URL = "https://api.openalex.org/authors"
WORKS_URL = "https://api.openalex.org/works"

#: Works requested per page. 200 is the largest page OpenAlex's API reference
#: documents for a list endpoint, but that is a number about their service and
#: not one this repository has measured, so nothing here depends on it being
#: right: a page size the server rejects produces a logged HTTP error and a
#: `request_failed` provenance flag that the report prints, rather than an empty
#: result that would read as "this author has no works".
WORKS_PAGE_SIZE = 200

#: Total budget for one author's works, mirroring `pubmed_api.MAX_RECORDS`: a
#: ceiling exists so a mis-resolved author id cannot pull a consortium's entire
#: output, and the shortfall is printed rather than refused.
MAX_WORKS = 2000

#: How many candidates the authors lookup asks for. More than this is not a
#: shortlist a human can read, and a name that returns twenty candidates is not
#: one this lookup can settle anyway.
CANDIDATE_LIMIT = 10

_DOI_PREFIX_RE = re.compile(r"(?i)^(?:https?://)?(?:dx\.)?doi\.org/|^doi:")
_PMID_RE = re.compile(r"(\d+)\s*$")
_PMCID_RE = re.compile(r"(?i)\bPMC\d+\b")
_ENTITY_ID_RE = re.compile(r"(?i)\b([AWISCFPT]\d+)\s*$")
_TITLE_NOISE_RE = re.compile(r"[^a-z0-9]")


# ------------------------------------------------------------------
# Small normalisers
# ------------------------------------------------------------------


def _doi_key(value: Any) -> str:
    """A DOI reduced to the form two sources can be compared on.

    OpenAlex stores `https://doi.org/10.1/x`, PubMed stores `10.1/x`, and a
    hand-edited corpus stores `DOI:10.1/X`. Compared verbatim those are three
    different papers, which is the whole reason a merge needs a key function.
    """
    doi = _DOI_PREFIX_RE.sub("", str(value or "").strip())
    return doi.strip().rstrip(".").lower()


def _pmid_key(value: Any) -> str:
    """The bare digits of a PMID, from either `12345678` or the PubMed URL form."""
    match = _PMID_RE.search(str(value or "").strip())
    return match.group(1) if match else ""


def _pmcid_key(value: Any) -> str:
    """The `PMC…` accession, from either the bare form or OpenAlex's URL form."""
    match = _PMCID_RE.search(str(value or "").strip())
    return match.group(0).upper() if match else ""


def _entity_id(value: Any) -> str:
    """The bare id of any OpenAlex entity URI — `W…`, `S…`, `I…` as well as `A…`.

    `normalise_openalex_id` is deliberately narrower: it answers "is this an
    *author* id", and returning a work id from it would let a work be compared
    against an author. This one is for provenance fields where the entity type
    is already known from the field name.
    """
    match = _ENTITY_ID_RE.search(str(value or "").strip())
    return match.group(1).upper() if match else ""


def _title_year_key(title: Any, year: Any) -> str:
    """Normalised title plus year, the fallback key when neither id exists.

    Punctuation, case and whitespace are dropped because the two sources
    typeset titles differently — OpenAlex keeps the publisher's HTML entities,
    PubMed strips them — and a difference in a comma is not a different paper.
    The year is part of the key rather than a separate check: two papers can
    genuinely share a title across years (an annual report, a conference series),
    and merging those would delete a real record.
    """
    normalised = _TITLE_NOISE_RE.sub("", str(title or "").lower())
    if not normalised:
        return ""
    return f"{normalised}|{str(year or '').strip()}"


def _split_display_name(display_name: str) -> tuple[str, str]:
    """Split an OpenAlex display name into (last, fore).

    OpenAlex normalises to given-name-first (`Guangwei Zhu`) but keeps the
    comma form (`Zhu, Guangwei`) for some records. Returning the pair in
    `_author_record`'s order matters: `roles.key_strict` and
    `pubmed_api._name_matches` both read `last` and `fore` by name, so getting
    the halves the wrong way round would put one person in the roster twice —
    once per source — with nothing on the page saying why.
    """
    text = " ".join(str(display_name or "").split())
    if not text:
        return "", ""
    if "," in text:
        last, _, fore = text.partition(",")
        return last.strip(), fore.strip()
    parts = text.split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[-1], " ".join(parts[:-1])


def _json_body(resp: Response | None, what: str) -> dict[str, Any] | None:
    """Decode one OpenAlex response, or None with the reason in the log.

    Same shape as `citations._json_body` and for the same reason: `Response`
    carries raw bytes and no `.json()`, and every failure mode is named in the
    log rather than swallowed into an empty result that reads as "this author
    has no works".
    """
    if resp is None:
        logger.warning("  [openalex] %s：网络层失败，无响应", what)
        return None
    if resp.status_code != 200:
        logger.warning("  [openalex] %s：HTTP %d", what, resp.status_code)
        return None
    try:
        data = json.loads(resp.content)
    except (UnicodeDecodeError, ValueError) as exc:
        logger.warning("  [openalex] %s：返回非 JSON 响应: %s", what, exc)
        return None
    if not isinstance(data, dict):
        logger.warning("  [openalex] %s：JSON 顶层不是对象，实为 %s", what, type(data).__name__)
        return None
    return data


def _with_mailto(url: str, mailto: str) -> str:
    """Append `mailto` if there is one. Not a key — a politer rate-limit pool."""
    if not mailto:
        return url
    return f"{url}{'&' if '?' in url else '?'}mailto={quote_plus(mailto)}"


# ------------------------------------------------------------------
# 1. Author disambiguation
# ------------------------------------------------------------------


def _institutions(author: Mapping[str, Any]) -> list[dict[str, Any]]:
    """This author's institution history, newest field first, oldest fallback.

    OpenAlex renamed `last_known_institution` (one object) to
    `last_known_institutions` (a list) in 2024 and kept serving the old key for
    a while. Both are read, and `affiliations` supplies the years, because "which
    institution, and when" is the only part of this record a human can check
    against what they already know about the person.
    """
    seen: dict[str, dict[str, Any]] = {}

    def add(node: Any, years: Sequence[Any] = ()) -> None:
        if not isinstance(node, Mapping):
            return
        name = str(node.get("display_name") or "").strip()
        if not name:
            return
        entry = seen.setdefault(name, {
            "display_name": name,
            "ror": str(node.get("ror") or ""),
            "country_code": str(node.get("country_code") or ""),
            "years": [],
        })
        for year in years:
            if isinstance(year, int) and year not in entry["years"]:
                entry["years"].append(year)

    for item in author.get("affiliations") or []:
        if isinstance(item, Mapping):
            add(item.get("institution"), item.get("years") or ())
    for item in author.get("last_known_institutions") or []:
        add(item)
    add(author.get("last_known_institution"))

    for entry in seen.values():
        entry["years"].sort()
    return list(seen.values())


def author_candidate(author: Mapping[str, Any]) -> dict[str, Any]:
    """One authors-API result reduced to what a human needs to pick with.

    `works_count` is carried because it is the field a reader will use, and it
    is exactly the field this module refuses to pick on: the most prolific
    candidate is not the one you are looking for, it is the one with the most
    papers.
    """
    return {
        "openalex_author_id": normalise_openalex_id(author.get("id")),
        "display_name": str(author.get("display_name") or "").strip(),
        "display_name_alternatives": [
            str(name).strip()
            for name in (author.get("display_name_alternatives") or [])
            if str(name).strip()
        ],
        # OpenAlex stores the ORCID as a resolver URL; the bare form is what
        # `is_first_or_corresponding` compares against, so strip it here rather
        # than leaving every consumer to.
        "orcid": str(author.get("orcid") or "").strip().rsplit("/", 1)[-1],
        "works_count": author.get("works_count"),
        "cited_by_count": author.get("cited_by_count"),
        "institutions": _institutions(author),
    }


def resolve_author(
    client: RobustHTTPClient,
    name: str,
    affiliation: str = "",
    mailto: str = "",
    limit: int = CANDIDATE_LIMIT,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Ask OpenAlex who publishes under `name`, and never decide between them.

    Returns a record in the shape the corpus stores:

        {"requested": True, "resolution": ..., "openalex_author_id": ...,
         "candidates": [...], "query": ..., "source": ..., "retrieved_at": ...}

    `resolution` is one of:

    - `unique` — exactly one candidate, adopted. Its id is in
      `openalex_author_id`.
    - `ambiguous` — more than one. **Nothing is adopted**, `openalex_author_id`
      is empty, and every candidate is returned so the caller can print them and
      the user can re-run with the one they recognise. This is the case the whole
      module is arranged around; see the module docstring.
    - `none` — the lookup succeeded and matched nobody.
    - `error` — the request or the decode failed. Distinct from `none` on
      purpose: "OpenAlex has never heard of this person" and "we could not ask"
      are different facts about the world and only the first says anything about
      the author.

    `affiliation` narrows the search through OpenAlex's own institution filter
    rather than by post-filtering the results, so a name that returns forty
    people can be cut to the handful at one university without this code
    inventing a matching rule of its own.
    """
    name = " ".join(str(name or "").split())
    if not name:
        logger.error("OpenAlex 作者消歧需要作者名，当前为空——跳过。")
        return _resolution("none", query="", candidates=[], now=now)

    filters = [f"display_name.search:{name}"]
    if affiliation:
        filters.append(f"last_known_institutions.display_name.search:{affiliation}")
    query = ",".join(filters)
    url = _with_mailto(
        f"{AUTHORS_URL}?filter={quote_plus(query)}&per-page={int(limit)}", mailto
    )

    data = _json_body(
        client.get(url, accept_type="api", timeout=30,
                   extra_headers=polite_headers(mailto)),
        f"authors?{query}",
    )
    if data is None:
        logger.warning(
            "OpenAlex 作者查询失败，本次不做 OpenAlex 消歧；PubMed 检索照常进行。"
            "这与「查到了但没有此人」不是一回事，语料里记为 resolution=error。"
        )
        return _resolution("error", query=query, candidates=[], now=now)

    results = data.get("results")
    candidates = [
        author_candidate(item) for item in (results if isinstance(results, list) else [])
        if isinstance(item, Mapping) and normalise_openalex_id(item.get("id"))
    ]

    if not candidates:
        logger.warning("OpenAlex 里没有匹配「%s」%s的作者。", name,
                       f"（机构含「{affiliation}」）" if affiliation else "")
        return _resolution("none", query=query, candidates=[], now=now)

    if len(candidates) == 1:
        chosen = candidates[0]
        logger.warning(
            "OpenAlex 作者消歧只返回一个候选并已自动采纳：%s (%s)，ORCID=%s，作品数=%s，机构=%s。"
            "**这不是确认**——检索用的是 display_name.search 模糊匹配，唯一候选完全可能是同名的"
            "另一个人，也可能是本人被拆成多个 profile 后只命中了其中一个。请照上面的机构和作品数"
            "核对一眼；不对就加 --openalex-author-id <正确的 ID> 重跑。",
            chosen["display_name"], chosen["openalex_author_id"],
            chosen["orcid"] or "无",
            chosen["works_count"] if chosen["works_count"] is not None else "?",
            "、".join(inst["display_name"] for inst in chosen["institutions"]) or "未记录",
        )
        return _resolution("unique", query=query, candidates=candidates,
                           author_id=chosen["openalex_author_id"], now=now)

    logger.warning(
        "OpenAlex 里「%s」对应 %d 位候选作者，**不替你选**。下面逐条列出，"
        "认出是哪一位后加 --openalex-author-id <ID> 重跑即可；本次检索按 PubMed 单源进行。",
        name, len(candidates),
    )
    for index, candidate in enumerate(candidates, 1):
        logger.warning(
            "  候选 %d/%d: %s | ID=%s | ORCID=%s | 作品数=%s | 被引=%s | 机构: %s",
            index, len(candidates), candidate["display_name"],
            candidate["openalex_author_id"], candidate["orcid"] or "无",
            candidate["works_count"] if candidate["works_count"] is not None else "?",
            candidate["cited_by_count"] if candidate["cited_by_count"] is not None else "?",
            "、".join(
                f"{inst['display_name']}"
                + (f"（{inst['years'][0]}-{inst['years'][-1]}）" if inst["years"] else "")
                for inst in candidate["institutions"]
            ) or "未记录",
        )
    logger.warning(
        "作品数最多的那位不是答案——按作品数挑等于用产量代替身份，和只按姓名匹配是同一个错误。"
    )
    return _resolution("ambiguous", query=query, candidates=candidates, now=now)


def _resolution(
    resolution: str,
    query: str,
    candidates: Sequence[Mapping[str, Any]],
    author_id: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """The corpus-side record of one resolution attempt.

    `source` and `retrieved_at` travel with it because this is somebody else's
    assertion about identity, and a report that prints it without saying where it
    came from and when has turned a third party's clustering into a fact.
    """
    return {
        "requested": True,
        "resolution": resolution,
        "openalex_author_id": author_id,
        "candidates": [dict(candidate) for candidate in candidates],
        "query": query,
        "source": "openalex-authors-api",
        "retrieved_at": (now or datetime.now()).isoformat(timespec="seconds"),
    }


# ------------------------------------------------------------------
# 2. Works as a second corpus source
# ------------------------------------------------------------------


def _authorship_record(authorship: Mapping[str, Any]) -> dict[str, Any]:
    """One OpenAlex authorship in `pubmed_api._author_record`'s shape.

    Every field the profile layer reads is produced, and the ones OpenAlex
    genuinely does not carry are left empty rather than defaulted to something
    plausible. `email` is the important one: OpenAlex publishes no addresses at
    all, so an OpenAlex-sourced record can never satisfy the email-domain check
    and `is_corresponding` comes from the flag rather than from an address.
    `equal_contrib` is likewise absent from the schema, so Section 8's count over
    a merged corpus is a floor — which is what the section already says about
    publisher-deposited flags in general.
    """
    author = authorship.get("author")
    author = author if isinstance(author, Mapping) else {}
    display = str(author.get("display_name") or authorship.get("raw_author_name") or "").strip()
    last, fore = _split_display_name(display)

    affils = [
        str(text).strip()
        for text in (authorship.get("raw_affiliation_strings") or [])
        if str(text).strip()
    ]
    if not affils:
        affils = [
            str(inst.get("display_name") or "").strip()
            for inst in (authorship.get("institutions") or [])
            if isinstance(inst, Mapping) and str(inst.get("display_name") or "").strip()
        ]

    return {
        "name": f"{last} {fore}".strip() or display,
        "last": last,
        "fore": fore,
        "initials": (fore[:1] or "").upper(),
        "affiliation": " ".join(affils),
        "is_corresponding": bool(authorship.get("is_corresponding")),
        "email": "",
        "orcid": str(author.get("orcid") or "").strip().rsplit("/", 1)[-1],
        "equal_contrib": False,
        # The field that makes this record's identity checkable at profile time.
        # `roles.evidence_tier` compares it against the corpus's recorded
        # `openalex_author_id`; without it the second, independent derivation of
        # the evidence tier would disagree with the marker in the role string.
        "openalex_author_id": normalise_openalex_id(author.get("id")),
    }


def _abstract_from_inverted_index(index: Any) -> str:
    """Rebuild an abstract from OpenAlex's inverted index.

    OpenAlex ships abstracts as `{word: [positions]}` for licensing reasons.
    Returns "" for anything that is not that structure, because a partially
    reconstructed abstract is worse than none: it reads as the author's words.
    """
    if not isinstance(index, Mapping) or not index:
        return ""
    slots: dict[int, str] = {}
    for word, positions in index.items():
        if not isinstance(positions, (list, tuple)):
            return ""
        for position in positions:
            if isinstance(position, int) and position >= 0:
                slots[position] = str(word)
    if not slots:
        return ""
    return " ".join(slots[key] for key in sorted(slots))


def work_to_paper(work: Mapping[str, Any], author_id: str = "") -> dict[str, Any] | None:
    """One OpenAlex work as the paper dict the rest of this package consumes.

    Returns None for a record with neither a title nor any identifier, which is
    the only thing that cannot be reported on at all.

    `role` is stamped here rather than by `is_first_or_corresponding`, because
    the evidence is different in kind: PubMed's filter asks "does this byline
    entry match the person we described", while this record arrived *because*
    OpenAlex files it under `author_id`. The marker says so, so the corpus's
    evidence histogram can keep the two apart. Papers where the resolved author
    holds no first / last / corresponding slot get an empty role, and the caller
    drops them — the same rule the PubMed side applies, for the same reason:
    this corpus is used to measure byline position, so the filter cannot be the
    measurement.
    """
    authorships = [a for a in (work.get("authorships") or []) if isinstance(a, Mapping)]
    authors = [_authorship_record(a) for a in authorships]

    title = str(work.get("title") or work.get("display_name") or "").strip()
    doi = _doi_key(work.get("doi"))
    ids = work.get("ids") if isinstance(work.get("ids"), Mapping) else {}
    pmid = _pmid_key(ids.get("pmid"))
    if not title and not doi and not pmid:
        return None

    location = work.get("primary_location")
    source = location.get("source") if isinstance(location, Mapping) else None
    source = source if isinstance(source, Mapping) else {}
    issns = [str(value).strip() for value in (source.get("issn") or []) if str(value).strip()]

    biblio = work.get("biblio") if isinstance(work.get("biblio"), Mapping) else {}
    first_page = str(biblio.get("first_page") or "").strip()
    last_page = str(biblio.get("last_page") or "").strip()
    pages = "-".join(part for part in (first_page, last_page) if part)

    year = work.get("publication_year")
    pub_year = str(year) if isinstance(year, int) else ""
    pub_date = str(work.get("publication_date") or "").strip() or pub_year

    wanted = normalise_openalex_id(author_id)
    role = ""
    if wanted:
        for index, (authorship, record) in enumerate(zip(authorships, authors, strict=True)):
            if record["openalex_author_id"] != wanted:
                continue
            roles = []
            position = str(authorship.get("author_position") or "").lower()
            if index == 0 or position == "first":
                roles.append("第一作者")
            if index == len(authors) - 1 or position == "last":
                roles.append("末位作者")
            if record["is_corresponding"]:
                roles.append("通讯作者")
            if roles:
                role = " / ".join(roles) + f" {_MARK_OPENALEX} {wanted}]"
            break

    return {
        "pmid": pmid,
        "title": title,
        "authors_str": ", ".join(a["name"] for a in authors if a["name"]),
        "authors": authors,
        "journal": str(source.get("display_name") or "").strip(),
        "issn": issns[0] if issns else "",
        # OpenAlex reports the type of `issn_l` only implicitly: it is the
        # linking ISSN, which is what `journals.join_journals` wants under
        # `issn_linking`. `issn_type` stays empty because the list under `issn`
        # is unlabelled, and guessing print vs electronic would produce a join
        # that silently matches the wrong column.
        "issn_type": "",
        "issn_linking": str(source.get("issn_l") or "").strip(),
        "journal_abbrev": str(source.get("abbreviated_title") or "").strip(),
        "pub_date": pub_date,
        "pub_year": pub_year,
        "volume": str(biblio.get("volume") or "").strip(),
        "issue": str(biblio.get("issue") or "").strip(),
        "pages": pages,
        "doi": doi,
        "pmc_id": _pmcid_key(ids.get("pmcid")),
        "abstract": _abstract_from_inverted_index(work.get("abstract_inverted_index")),
        "openalex_work_id": _entity_id(work.get("id")),
        "role": role,
        "source": SOURCE_OPENALEX,
        "confirmed_by": [SOURCE_OPENALEX],
    }


def fetch_works(
    client: RobustHTTPClient,
    author_id: str,
    from_year: int | None = None,
    to_year: int | None = None,
    mailto: str = "",
    max_works: int = MAX_WORKS,
    per_page: int = WORKS_PAGE_SIZE,
    provenance: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Every work OpenAlex files under `author_id`, inside the same date window.

    Paged with OpenAlex's cursor rather than an offset, which is what its own
    documentation requires past the first 10,000 results and which cannot skip a
    record when the index shifts mid-harvest — the failure `pubmed_api` works
    around by advancing `retstart` by the raw page length.

    `provenance` is an optional out-parameter on the same pattern
    `search_pubmed` uses: pass a dict and the filter, the reported total, the
    number retrieved and the page count are written into it, so the report can
    print "retrieved N of M" for this source too instead of a bare list length.
    """
    record = provenance if provenance is not None else {}
    author_id = normalise_openalex_id(author_id)
    if not author_id:
        record.update({"works_filter": "", "works_matched": 0, "works_returned": 0,
                       "pages_fetched": 0, "truncated": False})
        return []

    filters = [f"author.id:{author_id}"]
    if from_year:
        filters.append(f"from_publication_date:{int(from_year)}-01-01")
    if to_year:
        filters.append(f"to_publication_date:{int(to_year)}-12-31")
    works_filter = ",".join(filters)

    collected: list[dict[str, Any]] = []
    seen: set[str] = set()
    cursor = "*"
    pages = 0
    total = 0
    failed = False
    budget = max(0, int(max_works))

    while cursor and len(collected) < budget:
        want = min(int(per_page), budget - len(collected))
        url = _with_mailto(
            f"{WORKS_URL}?filter={quote_plus(works_filter)}"
            f"&per-page={want}&cursor={quote_plus(cursor)}",
            mailto,
        )
        data = _json_body(client.get(url, accept_type="api", timeout=60,
                                     extra_headers=polite_headers(mailto)),
                          f"works page {pages + 1}")
        if data is None:
            # The request failed. That is not the same fact as "this author has
            # no works", and the difference has to survive into the report: an
            # empty list with `works_matched: 0` beside it reads as a finding
            # about the author when it is a finding about the network.
            failed = True
            break
        pages += 1
        meta = data.get("meta") if isinstance(data.get("meta"), Mapping) else {}
        reported = meta.get("count")
        if isinstance(reported, int):
            total = reported
        results = data.get("results")
        page = [item for item in (results if isinstance(results, list) else [])
                if isinstance(item, Mapping)]
        if not page:
            break
        for work in page:
            key = _entity_id(work.get("id"))
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            collected.append(work)
        cursor = meta.get("next_cursor") or ""

    record.update({
        "works_filter": works_filter,
        "works_matched": total or len(collected),
        "works_returned": len(collected),
        "pages_fetched": pages,
        "max_works": budget,
        "truncated": len(collected) < (total or len(collected)),
        "request_failed": failed,
    })
    if failed:
        logger.error(
            "OpenAlex works 查询在第 %d 页失败，本次只拿到 %d 条。这与「这位作者没有作品」"
            "不是一回事，语料里记为 request_failed=True，报告会照实说明。",
            pages + 1, len(collected),
        )
    elif record["truncated"]:
        logger.warning(
            "OpenAlex 只取回 %d / 共 %d 条作品（上限 max_works=%d）。缺的部分没有被检查，"
            "合并语料里所有计数都是下界——调高 openalex.max_works 后重跑。",
            len(collected), record["works_matched"], budget,
        )
    else:
        logger.info("OpenAlex 取回 %d 条作品（共 %d 条，翻了 %d 页）",
                    len(collected), record["works_matched"], pages)
    return collected


# ------------------------------------------------------------------
# 3. Merge
# ------------------------------------------------------------------


def _keys_of(paper: Mapping[str, Any]) -> list[str]:
    """The identity keys one record can be matched on, strongest first.

    DOI first because it is the identifier both sources publish and the one that
    is stable across versions of a record. PMID second: it is equally exact but
    only half the OpenAlex corpus carries one, so it catches what a missing DOI
    would otherwise lose. Title + year last, and only last — it is the only key
    here that can be wrong, and it is prefixed so a title can never collide with
    a DOI in the same index.
    """
    keys = []
    doi = _doi_key(paper.get("doi"))
    if doi:
        keys.append(f"doi:{doi}")
    pmid = _pmid_key(paper.get("pmid"))
    if pmid:
        keys.append(f"pmid:{pmid}")
    title_year = _title_year_key(paper.get("title"), paper.get("pub_year"))
    if title_year:
        keys.append(f"ty:{title_year}")
    return keys


_KEY_KINDS = {"doi": "doi", "pmid": "pmid", "ty": "title_year"}


def _key_kind(key: str) -> str:
    """Which histogram bucket a key built by `_keys_of` is counted in."""
    return _KEY_KINDS[key.split(":", 1)[0]]


def _fold(donor: Mapping[str, Any], into: dict[str, Any], sources: Sequence[str]) -> None:
    """Fold a duplicate copy into the record that keeps the slot.

    `sources` is what the donor proves about the paper existing, not what the
    donor's metadata is worth. The surviving record keeps its own title, journal
    and byline and takes only the three fields a weaker copy can strictly
    improve: a missing DOI, because every downstream join keys on it, the
    OpenAlex work id, because provenance needs it, and the OpenAlex author ids
    that work's byline carries.

    The third is in that class for the same reason the second is — it is
    provenance about the donor's *work*, not metadata about this paper's byline,
    so nothing the surviving record says about itself is touched or contradicted.
    It has to be folded because the byline holding those ids is precisely the one
    thrown away here: `fetch_works(X)` returns only works OpenAlex files under X,
    so every donor carries X, and dropping it made
    `report.openalex_id_record_share` measure the share of the corpus OpenAlex
    holds *alone* rather than the share the id reached — the better two databases
    agreed, the lower it scored, and G3 fired hardest on the best-confirmed
    corpora.

    Kept as a record-level set and never written onto a byline entry: which
    surviving entry each id belongs to would have to be guessed by matching names
    across two bylines, and "the OpenAlex work confirming this record carries id
    X" is the strongest thing that is true without guessing.
    """
    new_sources = [source for source in sources if source not in into["confirmed_by"]]
    if new_sources:
        into["confirmed_by"] = sorted(into["confirmed_by"] + new_sources)
    if not _doi_key(into.get("doi")) and _doi_key(donor.get("doi")):
        into["doi"] = _doi_key(donor.get("doi"))
    if not str(into.get("openalex_work_id") or "").strip():
        into["openalex_work_id"] = donor.get("openalex_work_id", "")
    # Only what the survivor does not already say. An OpenAlex work folded into
    # another OpenAlex record adds nothing, and writing it anyway would put a
    # copy of the record's own byline ids into every merged file.
    donated = record_openalex_author_ids(donor) - record_openalex_author_ids(into)
    if donated:
        stored = into.get(OPENALEX_IDS_FIELD)
        kept = set(stored) if isinstance(stored, (list, tuple, set)) else set()
        into[OPENALEX_IDS_FIELD] = sorted(kept | donated)


def merge_corpora(
    pubmed_papers: Sequence[Mapping[str, Any]],
    openalex_papers: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Fold an OpenAlex corpus into a PubMed one, and say what came from where.

    Returns `{"papers": [...], "counts": {...}, "matched_on": {...},
    "openalex_internal_duplicates": {...}}`.

    The last two are both key-hit histograms and they are deliberately not one
    number. `matched_on` counts an OpenAlex work landing on a record **PubMed
    itself publishes that key on** — two independent bibliographic databases
    agreeing a paper exists. `openalex_internal_duplicates` counts everything
    else: an OpenAlex work landing on another OpenAlex work, and an OpenAlex
    work reaching a PubMed record only through a key some *other* OpenAlex work
    donated. The second case looks like cross-source agreement and is not one —
    the key came from OpenAlex, so PubMed never confirmed it — which is why the
    test is "is this key in `pubmed_keys`" and not "is this record a PubMed
    record". Adding the two histograms together prints same-source dedup as
    cross-source confirmation, which is the strongest claim on this page.

    The PubMed record wins whenever both sources hold a paper. Not because
    PubMed is better, but because its record carries affiliation strings and
    corresponding-author emails and OpenAlex's does not, and those two fields are
    what every identity check downstream reads. Taking the OpenAlex copy would
    turn a verified paper into an unverifiable one while appearing to add data.

    Every surviving record gets two fields:

    - `source` — which source's metadata this record actually carries.
    - `confirmed_by` — every source that independently holds it, sorted. Two
      entries means two bibliographic databases agree the paper exists and is
      this author's, which is a stronger statement than either alone; one entry
      is not evidence of anything being wrong, only of coverage.

    Nothing is reordered: the PubMed corpus keeps its order and the
    OpenAlex-only records follow it, so a reader diffing this against the
    PubMed-only run sees additions and no movement. The only records that
    disappear are OpenAlex copies of a paper already in the list, folded into
    the copy that keeps the slot.

    The result does not depend on the order OpenAlex served its works in. Every
    key of an incoming record is looked up, not just the first one that hits,
    because a record can be the bridge between two entries that never shared a
    key with each other — a PubMed record with only a PMID and an OpenAlex work
    with only a DOI are one paper the moment a third record arrives carrying
    both. Stopping at the first hit made that paper's count depend on which of
    the three arrived first.
    """
    merged: list[dict[str, Any]] = []
    index: dict[str, int] = {}
    pubmed_keys: set[str] = set()

    for paper in pubmed_papers:
        record = dict(paper)
        record["source"] = record.get("source") or SOURCE_PUBMED
        record["confirmed_by"] = [SOURCE_PUBMED]
        position = len(merged)
        merged.append(record)
        for key in _keys_of(record):
            index.setdefault(key, position)
            # Kept apart from the index because the index also ends up holding
            # keys OpenAlex donated. Only these were published by PubMed, and
            # only a hit on one of these is two databases agreeing.
            pubmed_keys.add(key)

    # Everything below this index came out of `pubmed_papers`. Used to pick
    # which record a duplicate is folded *into*, never to decide whether a hit
    # was cross-source — that is `pubmed_keys`, above. Nothing re-derives either
    # from the `source` field, which a re-merged corpus can already have set.
    pubmed_count = len(merged)

    # A record folded into another keeps its slot in `merged` until the end, so
    # positions already stored in the index stay valid; `live()` follows the
    # fold to whichever record is still standing.
    redirect: dict[int, int] = {}
    folded: set[int] = set()

    def live(position: int) -> int:
        while position in redirect:
            position = redirect[position]
        return position

    matched_on = {"doi": 0, "pmid": 0, "title_year": 0}
    internal_duplicates = {"doi": 0, "pmid": 0, "title_year": 0}
    added = 0
    for paper in openalex_papers:
        keys = _keys_of(paper)
        hits = [(key, live(index[key])) for key in keys if key in index]
        # Strongest evidence wins, not the first key that happens to be indexed:
        # a key PubMed published first, then a hit on a PubMed record through a
        # donated key, then a hit on another OpenAlex record. Ties keep the
        # DOI > PMID > title+year order `_keys_of` returns, because the sort is
        # stable.
        hits.sort(key=lambda hit: 0 if hit[0] in pubmed_keys
                  else (1 if hit[1] < pubmed_count else 2))
        if not hits:
            record = dict(paper)
            record["source"] = SOURCE_OPENALEX
            record["confirmed_by"] = [SOURCE_OPENALEX]
            position = len(merged)
            merged.append(record)
            added += 1
            for key in _keys_of(record):
                index.setdefault(key, position)
            continue

        hit_key, target = hits[0]
        bucket = matched_on if hit_key in pubmed_keys else internal_duplicates
        bucket[_key_kind(hit_key)] += 1
        existing = merged[target]
        _fold(paper, existing, [SOURCE_OPENALEX])

        # This record can hit more than one standing record, which proves those
        # records are the same paper even though no key of theirs ever matched.
        # Two PubMed records are left alone: PubMed's own duplicates are not
        # this function's to resolve, and collapsing them would move the
        # `pubmed_total` denominator.
        for key, position in hits[1:]:
            position = live(position)
            if position == target or position < pubmed_count:
                continue
            _fold(merged[position], existing, merged[position]["confirmed_by"])
            redirect[position] = target
            folded.add(position)
            internal_duplicates[_key_kind(key)] += 1

        # Every key of this record, not just the DOI it donated. A key left out
        # here is a key the next record misses on, which lets one paper enter
        # twice and inflate `merged_total` — the denominator of every count in
        # the report. The title check downstream cannot catch it: two records
        # that match on DOI need not share a title.
        for key in keys:
            index.setdefault(key, target)

    merged = [record for position, record in enumerate(merged) if position not in folded]
    both = sum(1 for paper in merged if len(paper["confirmed_by"]) > 1)
    counts = {
        "pubmed_total": len(pubmed_papers),
        "openalex_total": len(openalex_papers),
        "merged_total": len(merged),
        "pubmed_only": len(pubmed_papers) - both,
        # Only OpenAlex-created records are ever folded away, so this stays a
        # count of the records that entered and are still standing.
        "openalex_only": added - len(folded),
        "both": both,
    }
    logger.info(
        "语料合并：PubMed %d 篇 + OpenAlex %d 篇 → 合并后 %d 篇"
        "（两源共有 %d 篇，仅 PubMed %d 篇，仅 OpenAlex %d 篇；"
        "跨源命中（命中 PubMed 自己登记的键）DOI %d / PMID %d / 标题+年份 %d；"
        "OpenAlex 内部自重复（含仅靠另一条 OpenAlex 记录捐来的键才够到 PubMed 记录的情况）"
        "DOI %d / PMID %d / 标题+年份 %d——后者是同一个库把一篇列了两次，"
        "不构成任何跨源确认）",
        counts["pubmed_total"], counts["openalex_total"], counts["merged_total"],
        counts["both"], counts["pubmed_only"], counts["openalex_only"],
        matched_on["doi"], matched_on["pmid"], matched_on["title_year"],
        internal_duplicates["doi"], internal_duplicates["pmid"],
        internal_duplicates["title_year"],
    )
    return {"papers": merged, "counts": counts, "matched_on": matched_on,
            "openalex_internal_duplicates": internal_duplicates}
