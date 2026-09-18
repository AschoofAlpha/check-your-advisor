"""
Author classification for the advisor profile report.

Implements Sections 6.1-6.6 of docs/profile-metrics-spec.md: deciding which
byline entry is the target PI, dropping records that cannot be reasoned about,
grouping the remaining byline entries into people, and giving each person
exactly one stratum.

Nothing here touches the network and nothing here opens a file. Every function
takes the plain dicts that `pubmed_api.parse_article` produces and returns plain
dicts, so the whole classification layer runs offline against synthetic
fixtures.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from itertools import combinations
from typing import Any

# `_date_iso` is the same fallback-to-1900 parser the rest of the pipeline uses.
# Reusing it keeps the exclusion rule in Section 6.5 honest: the report must
# exclude exactly the records the shared helper cannot date, not a private
# approximation of them.
from ..corpus import _date_iso
from ..pubmed_api import (
    _affiliation_matches,
    _email_domain_matches,
    _name_matches,
    normalise_openalex_id,
    record_openalex_author_ids,
)

# Section 6.1. Ranked so a stronger identifier always beats a weaker one no
# matter where the two candidates sit in the byline.
#
# `openalex` sits below `orcid` and above `email` because of where it comes
# from, not because of how often it is right. An ORCID match is the person's own
# identifier compared against the byline's; an OpenAlex author id is one
# database's clustering decision, correct most of the time and wrong in a way
# nothing on the page can show. It outranks an email domain because a domain is
# shared by everybody at an institution while an author id names one cluster.
# The numbers moved up by one to make room; only the order is load-bearing.
EVIDENCE_RANK = {"orcid": 4, "openalex": 3, "email": 2, "affiliation": 1, "name_only": 0}

# Section 6.5. `parse_article` extracts no PublicationType, so the title is the
# only separator available. Anchored at the start and word-bounded so
# "Corrective surgery ..." is not mistaken for "Correction to: ...".
CORRECTION_TITLE_RE = re.compile(
    r"(?i)^\s*(correction|corrigendum|erratum|author correction|publisher correction|"
    r"retraction|retracted|comment on|reply to|response to|editorial)\b"
)

# Consortium and multicentre papers are a structurally different kind of
# authorship, so they leave person-level analysis entirely (Section 6.5).
HYPERAUTHORSHIP_MIN_AUTHORS = 50

# Reported as its own line rather than used as a cut-off (Section 7.10).
LARGE_TEAM_MIN_AUTHORS = 20

EXCLUSION_REASONS = (
    "unparseable_date",
    "correction_or_comment",
    "empty_author_list",
    "hyperauthorship",
    "duplicate_pmid",
    "duplicate_doi",
)


# ------------------------------------------------------------------
# Section 6.1 — which byline entry is the PI
# ------------------------------------------------------------------


def evidence_tier(author: dict[str, Any], identity: dict[str, Any]) -> str:
    """Strongest identity evidence this byline entry carries for `identity`.

    This is the profile layer's own derivation, computed from the raw byline
    entry and the recorded identity block, ignoring the role string entirely. It
    has to agree with the marker `pubmed_api` / `openalex` wrote at harvest time,
    or the corpus's evidence histogram and its per-paper `pi_evidence` will
    describe the same corpus differently. Adding a tier to one and not the other
    is the way that happens, so both were changed in the same pass.

    Strictly per byline entry, and it stays that way: an OpenAlex author id that
    a merged record carries at record level says the *paper* is this person's,
    not which byline slot they hold, so it is applied in `resolve_pi` — after
    the slot is chosen — rather than handed to every entry here.

    Which puts that agreement at two levels, and they are answered separately.
    At byline level this function and the harvest-time marker say the same thing
    about a merged record: no surviving entry carries an id, and the marker says
    name-only. At record level `resolve_pi`, `report.openalex_id_record_share`
    and `cli._corpus_counts` all read `record_openalex_author_ids` and all say
    openalex. So a merged record whose `pi_evidence` is openalex while its role
    marker is not is the two levels speaking, not a disagreement; the real
    disagreement was `_corpus_counts` reading the byline-level statement off the
    role string and printing it as the record-level one — `0 of 10 … name_only
    10` one line above `openalex author id on records: 10 of 10`.

    One thing that agreement does not cover, and this function is where it goes
    missing: the target *name*. This function is reached only for byline entries
    `resolve_pi` already matched the name against, while
    `report.openalex_id_record_share` and `cli._corpus_counts` never look at the
    name at all. Profile the same corpus under a name nobody on it holds and the
    two of them go on reporting `8 of 8` while `resolve_pi` rejects every record
    — three readers agreeing about a corpus no section is about. Nothing here
    fixes that, because nothing here can: the number is measured in
    `report.target_name_record_reach`, printed in Section 1 on every run, and
    raised as warning G7 when it is zero.
    """
    orcid = (identity.get("orcid") or "").strip()
    author_orcid = (author.get("orcid") or "").strip()
    if orcid and author_orcid and author_orcid.lower() == orcid.lower():
        return "orcid"
    wanted_openalex = normalise_openalex_id(identity.get("openalex_author_id"))
    author_openalex = normalise_openalex_id(author.get("openalex_author_id"))
    if wanted_openalex and author_openalex and wanted_openalex == author_openalex:
        return "openalex"
    if _email_domain_matches(author.get("email") or "", identity.get("email_domains") or []):
        return "email"
    if _affiliation_matches(author.get("affiliation") or "", identity.get("affiliation_keywords") or [])[0]:
        return "affiliation"
    return "name_only"


def resolve_pi(paper: dict[str, Any], target_name: str, identity: dict[str, Any]) -> dict[str, Any]:
    """
    Section 6.1. Decide this paper's disposition and where the PI sits.

    Byline position is deliberately not consulted. `is_first_or_corresponding`
    skips every name match that holds no first/last/corresponding role, so a
    corpus built through it cannot then be used to measure the PI's own byline
    position without the filter becoming the measurement.

    Two derivations meet here and they are ranked separately on purpose. Which
    byline entry is the PI is decided by `evidence_tier` alone, from what each
    entry itself carries. What the paper is *backed by* can additionally come
    from the record: a record both databases hold keeps PubMed's byline, so the
    OpenAlex author id that confirmed it survives as a record-level fact and no
    entry carries it. That fact raises the reported tier and is kept out of the
    tie-break, because a tier every candidate shares discriminates between none
    of them — feeding it into the tie-break would let it overturn the
    affiliation and email matches that do, and move `pi_index` onto a different
    person.
    """
    target_parts = (target_name or "").lower().split()
    authors = paper.get("authors") or []
    candidates = [
        (index, evidence_tier(author, identity))
        for index, author in enumerate(authors)
        if isinstance(author, dict) and _name_matches(author, target_parts)
    ]
    if not candidates:
        # `rejected` here means "the target name is not on this byline", and it is
        # the one disposition no other reader of the corpus can see: nothing else
        # in the pipeline consults `target_name`. A corpus profiled under a
        # mistyped name takes this branch on every record and used to leave no
        # trace of it anywhere on the page — the report still printed a roster, a
        # timeline and a span, all of them about a name the corpus does not hold.
        # `report.target_name_record_reach` counts these records with this same
        # matcher and warning G7 says so.
        return {"disposition": "rejected", "pi_index": None, "pi_evidence": "", "pi_ambiguous": False}

    best_rank = max(EVIDENCE_RANK[tier] for _, tier in candidates)
    tied = [(index, tier) for index, tier in candidates if EVIDENCE_RANK[tier] == best_rank]
    # Ties break to the lowest index, and the paper carries the flag so the
    # reader can see the guess rather than inherit it silently.
    pi_index, tier = tied[0]
    ambiguous = len(tied) > 1
    # The record-level half, read through the same function
    # `report.openalex_id_record_share` counts with and `cli._corpus_counts`
    # raises the harvested record's tier with, so the per-paper tier, the
    # corpus-wide share and Section 1's evidence histogram — the three readers
    # of this one fact — cannot describe *this* fact three ways.
    #
    # The scope of that claim is the OpenAlex id and nothing wider. The three
    # readers agree about which records carry the id; they do not agree about
    # which records are this person's, because only this function has been told
    # who that is. `target_name` is checked above and nowhere else in the corpus,
    # so on a mistyped name the other two report full coverage over records this
    # one rejects outright. `report.target_name_record_reach` measures exactly
    # that gap and Section 1 prints it beside the other two ratios.
    wanted_openalex = normalise_openalex_id(identity.get("openalex_author_id"))
    if EVIDENCE_RANK[tier] < EVIDENCE_RANK["openalex"] and wanted_openalex \
            and wanted_openalex in record_openalex_author_ids(paper):
        tier = "openalex"
    disposition = "name_only" if EVIDENCE_RANK[tier] == 0 else "verified"
    return {
        "disposition": disposition,
        "pi_index": pi_index,
        "pi_evidence": tier,
        "pi_ambiguous": ambiguous,
    }


# ------------------------------------------------------------------
# Section 6.2 — removals applied before anyone is classified
# ------------------------------------------------------------------


def is_collective_name(author: dict[str, Any]) -> bool:
    """
    Section 6.2.1. `_author_record` leaves last/fore/initials empty and fills
    only `name` for a <CollectiveName> byline entry, so a named entry with no
    name parts is a consortium rather than a person.
    """
    return (
        not (author.get("last") or "").strip()
        and not (author.get("fore") or "").strip()
        and not (author.get("initials") or "").strip()
        and bool((author.get("name") or "").strip())
    )


def default_gantt_exclude_names(pi_name: str, advisor_config: dict[str, Any] | None = None) -> set[str]:
    """
    P3. `analysis.render_gantt` hardcodes six personal names left over from one
    specific lab; reused unchanged it silently deletes those six people from
    every other lab's timeline. This is the replacement: the PI, the empty name,
    and whatever the operator actually configured.
    """
    names = {pi_name or "", ""}
    for name in (advisor_config or {}).get("exclude_names") or []:
        if name and str(name).strip():
            names.add(str(name).strip())
    return names


# ------------------------------------------------------------------
# Paper preparation
# ------------------------------------------------------------------


def paper_year(paper: dict[str, Any]) -> int:
    """Publication year via the shared date parser; 1900 means unparseable."""
    return int(_date_iso(paper.get("pub_date", ""))[:4])


def prepare_paper(paper: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    """
    Normalise one corpus record into the shape every metric consumes.

    `persons` is the author list with consortium entries removed. It is built
    before the first/last-slot tests because a trailing consortium entry would
    otherwise occupy the senior slot and hide the person who actually holds it.

    The PI slot is resolved here on every record and is never read back off the
    record, however the record spells it. See the comment at the `resolve_pi`
    call below for why a cached `pi_index` is not trusted.
    """
    authors = [a for a in (paper.get("authors") or []) if isinstance(a, dict)]
    collective = [is_collective_name(a) for a in authors]
    persons = [a for a, is_group in zip(authors, collective, strict=True) if not is_group]

    person_index_of: dict[int, int] = {}
    cursor = 0
    for index, is_group in enumerate(collective):
        if not is_group:
            person_index_of[index] = cursor
            cursor += 1

    # Resolved on every record, and a `pi_index` the record already carries is
    # ignored rather than reused. There was a short-circuit here that skipped
    # `resolve_pi` entirely for such a record, justified by a comment saying the
    # profile fetch stage writes those fields. It does not: `parse_article`
    # produces no `pi_index`, `cli._profile_corpus` copies the paper list through
    # untouched, `export.save_to_json` adds nothing, and the only writer of the
    # key anywhere in the package is this function and `resolve_pi` above. So the
    # branch was unreachable on a harvested corpus and reachable only from a
    # hand-edited file — the one input whose cached value has no reason to be
    # current.
    #
    # It also silently outranked `--pi-name`. The name the report is about is
    # settled per run, and a cached slot is a slot for whatever name the file was
    # written under; taking it meant Section 7 kept reporting the cached person's
    # last-author slots directly beneath warning G7's sentence saying there was
    # no byline slot to report. Resolving unconditionally is what makes that
    # sentence true on both corpus shapes.
    resolved = resolve_pi(paper, identity.get("author_name", ""), identity)
    pi_index = resolved["pi_index"]
    pi_evidence = resolved["pi_evidence"]
    pi_ambiguous = resolved["pi_ambiguous"]

    slot0_collective = bool(collective) and collective[0]
    return {
        "pmid": str(paper.get("pmid", "")),
        "title": paper.get("title", "") or "",
        "journal": paper.get("journal", "") or "",
        # Journal identity travels with the title, because `journal` alone does
        # not identify a journal: PubMed leaves it unnormalised, so one journal
        # arrives under its full title on some records and its abbreviation on
        # others. Dropping these three here silently disabled the ISSN branch of
        # the journal-table join — every paper reached it with an empty ISSN, so
        # a table keyed on ISSN matched nothing and reported it as a table that
        # simply did not cover this corpus. Empty strings for corpora harvested
        # before `pubmed_api` stored them, which fall through to name matching.
        "issn": paper.get("issn", "") or "",
        "issn_linking": paper.get("issn_linking", "") or "",
        "journal_abbrev": paper.get("journal_abbrev", "") or "",
        "doi": (paper.get("doi", "") or "").strip().lower(),
        "pub_date": paper.get("pub_date", "") or "",
        "date_iso": _date_iso(paper.get("pub_date", "")),
        "year": paper_year(paper),
        "authors": authors,
        "persons": persons,
        "n_authors": len(persons),
        "pi_index": pi_index,
        "pi_person_index": person_index_of.get(pi_index) if pi_index is not None else None,
        "pi_evidence": pi_evidence,
        "pi_ambiguous": pi_ambiguous,
        # T16: a consortium in slot 0 means nobody on this paper holds the lead
        # slot, and the paper leaves the first-author eligible set entirely.
        "slot0_collective": slot0_collective,
        "n_collective": sum(1 for is_group in collective if is_group),
        "any_email": any((a.get("email") or "").strip() for a in persons),
        "equal_contrib_indices": [i for i, a in enumerate(persons) if a.get("equal_contrib")],
    }


def _pmid_sort_key(pmid: str) -> tuple[int, Any]:
    """PMIDs are numeric in practice; fall back to string order if they are not."""
    return (0, int(pmid)) if pmid.isdigit() else (1, pmid)


def _normalise_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (title or "").lower())


def apply_record_exclusions(prepared: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """
    Section 6.5. Split prepared records into what each metric may use.

    `kept` feeds every metric. `records_only` holds hyperauthorship papers,
    which stay in the records-per-year count but leave all person-level
    analysis. Every exclusion keeps its PMIDs so the provenance block can list
    them instead of quietly shrinking the denominators.
    """
    excluded: dict[str, list[str]] = {reason: [] for reason in EXCLUSION_REASONS}
    survivors: list[dict[str, Any]] = []

    for paper in prepared:
        pmid = paper["pmid"]
        if paper["date_iso"].startswith("1900-"):
            # Not an early paper: 1900 is `_date_iso`'s fallback. One such record
            # gives a person a ~125-year span and destroys every median.
            excluded["unparseable_date"].append(pmid)
            continue
        if CORRECTION_TITLE_RE.match(paper["title"]):
            excluded["correction_or_comment"].append(pmid)
            continue
        if paper["n_authors"] == 0:
            # Reachable when every byline entry was a consortium; gate G4 catches
            # the different case of a corpus with no structured authors at all.
            excluded["empty_author_list"].append(pmid)
            continue
        survivors.append(paper)

    seen: dict[str, dict[str, Any]] = {}
    deduped: list[dict[str, Any]] = []
    for paper in survivors:
        # An empty PMID is not an identity. Merged OpenAlex-only records carry
        # none, and keying them all on "" collapsed the entire second source to
        # a single paper. The DOI pass below already guards this way; this one
        # did not. Records with no PMID fall through to that pass instead.
        if paper["pmid"] and paper["pmid"] in seen:
            excluded["duplicate_pmid"].append(paper["pmid"])
            continue
        if paper["pmid"]:
            seen[paper["pmid"]] = paper
        deduped.append(paper)

    by_doi: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for paper in deduped:
        if paper["doi"]:
            by_doi[paper["doi"]].append(paper)
    dropped_pmids: set[str] = set()
    for group in by_doi.values():
        if len(group) < 2:
            continue
        # Same DOI is the same work by definition. Keeping the lowest PMID is
        # arbitrary but deterministic, which is what a reproducible count needs.
        group.sort(key=lambda p: _pmid_sort_key(p["pmid"]))
        for paper in group[1:]:
            dropped_pmids.add(paper["pmid"])
            excluded["duplicate_doi"].append(paper["pmid"])
    deduped = [p for p in deduped if p["pmid"] not in dropped_pmids]

    kept: list[dict[str, Any]] = []
    records_only: list[dict[str, Any]] = []
    for paper in deduped:
        if paper["n_authors"] >= HYPERAUTHORSHIP_MIN_AUTHORS:
            excluded["hyperauthorship"].append(paper["pmid"])
            records_only.append(paper)
        else:
            kept.append(paper)

    # Flagged and counted, never merged: a preprint and its journal version carry
    # different DOIs, but so do genuinely distinct papers with similar titles.
    title_groups: dict[str, list[str]] = defaultdict(list)
    for paper in kept + records_only:
        key = _normalise_title(paper["title"])
        if key:
            title_groups[key].append(paper["pmid"])
    title_duplicates = [sorted(pmids, key=_pmid_sort_key) for pmids in title_groups.values() if len(pmids) > 1]

    return {
        "kept": kept,
        "records_only": records_only,
        "excluded": excluded,
        "title_duplicates": sorted(title_duplicates),
        "large_team_pmids": [p["pmid"] for p in kept if p["n_authors"] >= LARGE_TEAM_MIN_AUTHORS],
        "slot0_collective_pmids": [p["pmid"] for p in kept if p["slot0_collective"]],
    }


# ------------------------------------------------------------------
# Section 6.3 — person keys
# ------------------------------------------------------------------


def key_strict(author: dict[str, Any]) -> tuple[str, str]:
    return ((author.get("last") or "").strip().lower(), (author.get("fore") or "").strip().lower())


def key_loose(author: dict[str, Any]) -> tuple[str, str]:
    given = (author.get("fore") or "").strip() or (author.get("initials") or "").strip()
    return ((author.get("last") or "").strip().lower(), given[:1].lower())


def prefix_compatible(left: str, right: str) -> bool:
    """One forename is empty, or one is a case-insensitive prefix of the other."""
    if not left or not right:
        return True
    return left.startswith(right) or right.startswith(left)


def _display_name(entries: Sequence[dict[str, Any]]) -> str:
    """Most-used spelling; ties go to the longest form, which carries more information."""
    counts = Counter(entry["name"] for entry in entries if entry["name"])
    if not counts:
        return ""
    return max(counts.items(), key=lambda item: (item[1], len(item[0]), item[0]))[0]


def _group_appearances(appearances: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Section 6.3, steps 1-5. Returns groups of appearances with their flags."""
    orcid_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    loose_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for appearance in appearances:
        orcid = (appearance.get("orcid") or "").strip()
        if orcid:
            orcid_groups[orcid].append(appearance)
        else:
            loose_groups[key_loose(appearance)].append(appearance)

    owners_by_loose: dict[tuple[str, str], set[str]] = defaultdict(set)
    for orcid, entries in orcid_groups.items():
        for entry in entries:
            owners_by_loose[key_loose(entry)].add(orcid)

    # Two different ORCIDs under one name string is the only positive collision
    # test PubMed affords, so it is recorded even when no ORCID-less entry exists.
    confirmed_orcids = {orcid for owners in owners_by_loose.values() if len(owners) > 1 for orcid in owners}

    merge_target: dict[tuple[str, str], str] = {}
    confirmed_loose: set[tuple[str, str]] = set()
    for loose_key in loose_groups:
        if not loose_key[1]:
            # Rule 5: an entry with no forename and no initials matches every
            # given name, so any merge it could make is unconstrained.
            continue
        owners = owners_by_loose.get(loose_key, set())
        if len(owners) == 1:
            merge_target[loose_key] = next(iter(owners))
        elif len(owners) > 1:
            confirmed_loose.add(loose_key)

    groups: list[dict[str, Any]] = []
    for orcid, entries in orcid_groups.items():
        merged = list(entries)
        for loose_key, target in merge_target.items():
            if target == orcid:
                merged.extend(loose_groups[loose_key])
        groups.append({
            "entries": merged,
            "orcid": orcid,
            "flags": ["collision_confirmed"] if orcid in confirmed_orcids else [],
        })

    for loose_key, entries in loose_groups.items():
        if loose_key in merge_target:
            continue
        flags: list[str] = []
        if not loose_key[1]:
            flags.append("incomplete_name")
        elif loose_key in confirmed_loose:
            flags.append("collision_confirmed")
        else:
            forenames = sorted({key_strict(entry)[1] for entry in entries})
            if not all(prefix_compatible(a, b) for a, b in combinations(forenames, 2)):
                flags.append("collision_suspected")
            elif len(forenames) > 1:
                # Only flag drift that was actually observed. A group recorded
                # one way throughout carries no drift information, and flagging
                # it would put a marker on almost every row.
                flags.append("drift_benign")
        groups.append({"entries": entries, "orcid": "", "flags": flags})

    return groups


# ------------------------------------------------------------------
# Sections 6.4 and 6.6 — strata and affiliation signal
# ------------------------------------------------------------------


def _stratum(n_last_slots: int, n_first_slots: int, n_appearances: int) -> str:
    """
    Section 6.4, first match wins. Last-author position is the only structural
    seniority signal PubMed carries, and first-author position the only "did the
    work" signal; span and appearance count are consequences this report
    measures, never criteria, because a criterion built from a consequence bakes
    the conclusion into the definition.
    """
    if n_last_slots >= 1:
        return "D"
    if n_first_slots >= 1:
        return "A"
    if n_appearances >= 2:
        return "B"
    return "C"


def affiliation_signal(entries: Sequence[dict[str, Any]], affiliation_keywords: Sequence[str]) -> str:
    """
    Section 6.6. A descriptive attribute, never a cohort gate: dual appointments
    are space-joined into one string and coverage is era-dependent, so this
    cannot decide whether somebody is external.
    """
    seen_any = False
    for entry in entries:
        affiliation = (entry.get("affiliation") or "").strip()
        if not affiliation:
            continue
        seen_any = True
        if _affiliation_matches(affiliation, list(affiliation_keywords))[0]:
            return "internal"
    return "external" if seen_any else "unknown"


def build_people(
    papers: Sequence[dict[str, Any]],
    identity: dict[str, Any],
    exclude_names: Iterable[str] = (),
    window_start_year: int = 0,
    window_end_year: int = 0,
) -> dict[str, Any]:
    """
    Sections 6.2-6.4 and 6.6. Turn prepared papers into people.

    Returns the roster, the strict/loose ambiguity budget, the PMIDs of
    sole-author papers (whose author is forced into stratum D by the last-author
    rule and must be visible rather than silently reclassified), and the people
    who took a senior slot after having led a paper.
    """
    excluded_lower = {str(name).strip().lower() for name in exclude_names if str(name).strip()}
    appearances: list[dict[str, Any]] = []
    sole_author_papers: list[str] = []

    for paper in papers:
        persons = paper["persons"]
        if len(persons) == 1 and paper["pi_person_index"] != 0:
            sole_author_papers.append(paper["pmid"])
        last_index = len(persons) - 1
        for index, author in enumerate(persons):
            if index == paper["pi_person_index"]:
                continue
            name = (author.get("name") or "").strip()
            if not name or name.lower() in excluded_lower:
                continue
            appearances.append({
                "pmid": paper["pmid"],
                "year": paper["year"],
                "date_iso": paper["date_iso"],
                "index": index,
                "name": name,
                "last": author.get("last", "") or "",
                "fore": author.get("fore", "") or "",
                "initials": author.get("initials", "") or "",
                "orcid": author.get("orcid", "") or "",
                "affiliation": author.get("affiliation", "") or "",
                "equal_contrib": bool(author.get("equal_contrib")),
                "is_corresponding": bool(author.get("is_corresponding")),
                "is_first": index == 0 and not paper["slot0_collective"],
                "is_last": index == last_index,
            })

    groups = _group_appearances(appearances)
    keywords = list(identity.get("affiliation_keywords") or [])

    people: list[dict[str, Any]] = []
    for group in groups:
        entries = sorted(group["entries"], key=lambda e: (e["date_iso"], e["pmid"]))
        if not entries:
            continue
        years = [entry["year"] for entry in entries]
        first_slots = [entry for entry in entries if entry["is_first"]]
        last_slots = [entry for entry in entries if entry["is_last"]]
        flags = list(group["flags"])
        first_year, last_year = min(years), max(years)
        people.append({
            "name": _display_name(entries),
            "orcid": group["orcid"],
            "flags": flags,
            # Section 7.1: an uncertain identity taints every value derived from it.
            "marker": "[?]" if ({"collision_suspected", "collision_confirmed"} & set(flags)) else "",
            "stratum": _stratum(len(last_slots), len(first_slots), len(entries)),
            "affiliation_signal": affiliation_signal(entries, keywords),
            "n_appearances": len(entries),
            "n_first_slots": len(first_slots),
            "n_last_slots": len(last_slots),
            "n_equal_contrib": sum(1 for entry in entries if entry["equal_contrib"]),
            "first_year": first_year,
            "last_year": last_year,
            "years": sorted(set(years)),
            # Which of this person's years hold a first-author record. The timeline
            # figure needs the years, not the PMIDs: one mark covers a whole year, so
            # a per-mark PMID would not be well defined.
            "lead_years": sorted({entry["year"] for entry in first_slots}),
            "first_date": entries[0]["date_iso"],
            "span_years": last_year - first_year,
            "left_censored": first_year <= window_start_year,
            "right_censored": last_year >= window_end_year - 1,
            "first_lead_year": min((e["year"] for e in first_slots), default=None),
            "first_last_year": min((e["year"] for e in last_slots), default=None),
            "pmids": [entry["pmid"] for entry in entries],
            "first_slot_pmids": [entry["pmid"] for entry in first_slots],
        })

    # R5: first appearance, then name. This stays the order `people` is returned
    # in, and it is not a ranking: it is the order the timeline figure reads and
    # the order `person_id` is handed out in, both of which need to be stable
    # against a count that moves every time the corpus is re-harvested.
    #
    # Round four added `rank_people` below, which *does* order people by a count.
    # The two are deliberately separate calls rather than a flag on this one: a
    # caller that wants a leaderboard has to ask for one by name, and the roster
    # a reader sees first is still the one nobody's output can reorder.
    people.sort(key=lambda person: (person["first_date"], person["name"]))
    for person_id, person in enumerate(people):
        person["person_id"] = person_id

    # Which stratum holds each paper's lead slot. Papers whose slot-0 entry was
    # excluded by name or by config end up absent here and are reported as
    # `unclassified` rather than dropped from the denominator.
    lead_stratum_by_pmid = {
        pmid: person["stratum"] for person in people for pmid in person["first_slot_pmids"]
    }

    return {
        "people": people,
        "appearances": appearances,
        "n_strict": len({key_strict(a) for a in appearances}),
        "n_loose": len({key_loose(a) for a in appearances}),
        "sole_author_papers": sole_author_papers,
        "lead_stratum_by_pmid": lead_stratum_by_pmid,
        "flips": _lead_to_senior_flips(people),
    }


#: What `rank_people` will order a roster by. Each maps to one integer already
#: on every person record, so a rank is a re-reading of a number the report
#: already prints, never a new measurement and never a composite of several.
#: A key absent from here is refused by name rather than silently ignored.
RANKABLE: dict[str, str] = {
    "first_slots": "n_first_slots",
    "appearances": "n_appearances",
    "span": "span_years",
}

#: Printed beside any ranked roster. A rank here is a position among *the people
#: this one corpus happens to name*, which is not a statement about anybody's
#: standing anywhere else — the denominator is the whole caveat, so it travels
#: with the rank instead of being left to the reader to remember.
RANK_BASIS = (
    "Rank among the {n} people this corpus names, by {label}. The denominator is "
    "the roster, not a field, a department or a cohort: someone who published "
    "elsewhere, left before the window opened or never appeared in a PubMed byline "
    "is not below anyone here, they are absent. Ties share a rank and the next "
    "rank is skipped, so positions are comparable but not consecutive."
)


def rank_people(
    people: Sequence[dict[str, Any]],
    by: str = "first_slots",
) -> dict[str, Any]:
    """
    Order a roster by one count. Round four; refused in rounds one through three.

    The refusal it replaces was not about arithmetic — the counts were always
    printed, one line each, in Section 2. It was that arranging people by them
    turns a description into a standing, and this tool is read by someone
    deciding where to spend five years. That objection has not stopped being
    true; the user asked for the ordering anyway, and this function is the
    ordering, kept in one named place rather than spread through the report.

    What it does *not* do is the part still worth keeping:

    - **No composite.** `by` picks exactly one of `RANKABLE`, each a count the
      report already shows. There is no weighted blend of them, because a blend
      would need weights nobody can defend for people.
    - **No percentile, no grade.** A position among a handful of colleagues is
      not a quantile, and `n` is printed beside every rank so a "1st" that is
      first of two cannot read as first of many.
    - **Ties are shared, not broken.** Two people with the same count get the
      same rank. Breaking a tie on an unrelated field would invent a difference
      the data does not hold.
    - **The roster itself is untouched.** The input list is not reordered and
      nothing is written back onto the person records; the ranked view is a new
      list of new dicts.

    Returns `{"by", "field", "label", "n", "basis", "ranked"}`, where `ranked`
    holds `{"rank", "tied", "value", ...}` plus the person's own keys.
    """
    if by not in RANKABLE:
        raise ValueError(
            f"rank_people cannot order by {by!r}. "
            f"Known keys: {', '.join(sorted(RANKABLE))}."
        )
    field = RANKABLE[by]
    label = by.replace("_", " ")

    # Descending by the count, then the roster's own stable order as the
    # tiebreak, so two runs over one corpus list tied people identically.
    ordered = sorted(
        people,
        key=lambda person: (-int(person[field]), person["first_date"], person["name"]),
    )

    ranked: list[dict[str, Any]] = []
    previous_value: Any = object()
    previous_rank = 0
    for position, person in enumerate(ordered, start=1):
        value = int(person[field])
        if value == previous_value:
            rank = previous_rank
        else:
            rank = position
            previous_value, previous_rank = value, position
        ranked.append({**person, "rank": rank, "value": value})

    counts = [row["rank"] for row in ranked]
    for row in ranked:
        row["tied"] = counts.count(row["rank"]) > 1

    return {
        "by": by,
        "field": field,
        "label": label,
        "n": len(ranked),
        "basis": RANK_BASIS.format(n=len(ranked), label=label),
        "ranked": ranked,
    }


def _lead_to_senior_flips(people: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Section 6.4. People trained here who later took the senior slot. This is the
    strongest positive datum the corpus can produce, so it is reported by name
    and year rather than buried inside a stratum reassignment.
    """
    flips = []
    for person in people:
        if person["stratum"] == "D" and person["n_first_slots"] >= 1:
            flips.append({
                "name": person["name"],
                "marker": person["marker"],
                "first_lead_year": person["first_lead_year"],
                "first_last_year": person["first_last_year"],
            })
    return flips
