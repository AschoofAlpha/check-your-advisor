"""
两份语料的差异 — comparing two harvests of one advisor.
======================================================

`harvest` is always a full re-fetch, so the file it writes is a snapshot and
never a delta. The way this tool is actually used is the other way round:
somebody tracks two or three candidate supervisors, re-runs the harvest half a
year later, and wants to know what the corpus says now that it did not say
before. Until this module that question was answered by opening two HTML reports
side by side and reading them against each other.

`diff_corpora` answers it as data. It takes two corpora already loaded into
memory — the dicts `cli._profile_corpus` builds — and returns one dict. It
reaches no network, writes no file and reorders neither input. Storing the
result and wiring it to a subcommand belong to the caller.

**It reports membership and refuses to report cause.** A record that is in the
older corpus and not in the newer one is reported as exactly that and in those
words. The reasons it could be so are several, the two files cannot tell them
apart, and each of them accounts for the whole difference on its own — so
choosing one would be this module inventing the most interesting of them.
`CAUSE_NOTE` says so in the returned value and not only here, because the reader
of a table is not the reader of a docstring.

**The window comes before the counts.** The single easiest way to read a false
conclusion out of a diff is to compare a four-year harvest against a six-year
one: every record in the two extra years then holds only in the newer corpus,
and "seven more papers" is entirely the question having changed. So the window
is compared first, the note about it is `notes[0]`, `caliber_changed` is raised,
and every count of records holding on one side only is additionally split into
the years both harvests covered and the years only one of them did. When either
file does not record its window — a corpus written before `save_to_json` grew an
envelope — the comparison is reported as impossible rather than guessed, and the
split is withheld.

**Two things are reused rather than re-decided**, because a second opinion on
either would let this module and the report disagree about one corpus:

- *Which records are the same record.* `paper_keys` **is**
  `openalex._keys_of` — the DOI -> PMID -> normalised title + year ladder
  `merge_corpora` dedups on, imported and re-exported, not reimplemented. A
  record matches when any one of its keys is held by the other corpus, which is
  the same "any key hits" rule the merge applies.
- *Who the people are.* The roster on each side is `roles.build_people` over
  `roles.prepare_paper`, after `roles.apply_record_exclusions` — the pipeline
  `profile.report.build_report` runs, with that corpus's own recorded identity.
  So "who appears now and did not before" names the people Section 2 names,
  under the same grouping, the same exclusions and the same `[?]` markers. One
  deliberate difference: no `exclude_names` are applied, because those come from
  an advisor config this function is not given, and silently applying the
  config loaded today to a corpus harvested under another one is the mismatch
  `_profile_corpus` already refuses elsewhere.

Standard library plus this package, and it has to stay that way: nothing here
may put a diff behind an install.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from .openalex import _keys_of as paper_keys
from .profile.report import _year_in, window_years
from .profile.roles import (
    apply_record_exclusions,
    build_people,
    key_loose,
    paper_year,
    prepare_paper,
)

logger = logging.getLogger("check_your_advisor.corpus_diff")

__all__ = [
    "CAUSE_NOTE",
    "IDENTITY_FIELDS",
    "UNDATED_YEAR",
    "diff_corpora",
    "paper_keys",
]

#: `corpus._date_iso` returns this year for a publication date it cannot read,
#: and `roles.paper_year` passes it through. Such a record is placed on neither
#: side of the shared window: it has no year to be placed by, and defaulting it
#: to either side would be an answer rather than the absence of one.
UNDATED_YEAR = 1900

#: The identity evidence a harvest filtered on, compared field by field. A
#: corpus harvested with an ORCID and one harvested without it were built under
#: different membership rules, so a record holding on one side only can be the
#: rule having moved rather than anything about the advisor — the same failure
#: the window block exists for, which is why both feed `caliber_changed`.
IDENTITY_FIELDS: tuple[str, ...] = (
    "author_name", "orcid", "openalex_author_id",
    "affiliation_keywords", "email_domains",
)

CAUSE_NOTE = (
    "Every count here is about membership and nothing else: a record is in one "
    "corpus and not in the other. This module does not say why, and the two "
    "files do not let it. The two harvests can differ in the date window they "
    "asked about, in the search term they asked with, in the identity evidence "
    "they filtered on, and in what the bibliographic databases themselves "
    "answered on the two days they were asked. Any one of those accounts for "
    "the whole difference on its own, and nothing in either file tells them "
    "apart. Settle the window block above before reading any count below as a "
    "change in what this person published."
)


# ------------------------------------------------------------------
# The window — compared before anything else is
# ------------------------------------------------------------------


def _window_side(corpus: Mapping[str, Any],
                 prepared: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """One corpus's year window, and where the two numbers came from.

    `start` and `end` are `report.window_years`' own answer, so a span printed
    here is the span Section 1 prints. `source` is the part that function does
    not return and this comparison cannot do without: it falls back to the
    corpus's earliest and latest record when the file records no dates, and the
    span a corpus happens to hold is not the span that was asked for. Two such
    spans differing says nothing at all about the two questions.
    """
    query = corpus.get("query")
    query = query if isinstance(query, Mapping) else {}
    mindate = str(query.get("mindate") or "")
    maxdate = str(query.get("maxdate") or "")
    recorded = _year_in(mindate) is not None and _year_in(maxdate) is not None
    start, end = window_years(dict(corpus), prepared)
    return {
        "start": start,
        "end": end,
        "recorded": recorded,
        "source": "query" if recorded else "papers",
        "mindate": mindate,
        "maxdate": maxdate,
        "years_back": query.get("years_back", "?"),
    }


def _window(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Compare the two windows, and say plainly when they cannot be compared."""
    comparable = old["recorded"] and new["recorded"]
    changed: list[str] = []
    shared_start: int | None = None
    shared_end: int | None = None
    only_new: list[int] = []
    only_old: list[int] = []

    if comparable:
        if old["start"] != new["start"]:
            changed.append("start")
        if old["end"] != new["end"]:
            changed.append("end")
        shared_start = max(old["start"], new["start"])
        shared_end = min(old["end"], new["end"])
        old_years = set(range(old["start"], old["end"] + 1))
        new_years = set(range(new["start"], new["end"] + 1))
        only_new = sorted(new_years - old_years)
        only_old = sorted(old_years - new_years)
        if shared_start > shared_end:
            # Disjoint windows: there is no year both harvests asked about, so
            # every record on either side sits outside the shared window and the
            # two corpora have no common ground to be compared over at all.
            shared_start = shared_end = None

    return {
        "old": old,
        "new": new,
        "comparable": comparable,
        "same": (not changed) if comparable else None,
        "changed_bounds": changed,
        "shared_start": shared_start,
        "shared_end": shared_end,
        "years_only_new_looked_at": only_new,
        "years_only_old_looked_at": only_old,
        "note": _window_note(old, new, comparable, changed,
                             shared_start, shared_end, only_new, only_old),
    }


def _span(side: Mapping[str, Any]) -> str:
    return f"{side['start']}-{side['end']}"


def _years(years: Sequence[int]) -> str:
    return str(years[0]) if len(years) == 1 else f"{years[0]}-{years[-1]}"


def _window_note(old: Mapping[str, Any], new: Mapping[str, Any], comparable: bool,
                 changed: Sequence[str], shared_start: int | None,
                 shared_end: int | None, only_new: Sequence[int],
                 only_old: Sequence[int]) -> str:
    """The sentence that has to be read before any count below it."""
    if not comparable:
        missing = [f"the {name} corpus" for name, side
                   in (("older", old), ("newer", new)) if not side["recorded"]]
        verb = "records" if len(missing) == 1 else "record"
        return (
            f"The search window cannot be compared: {' and '.join(missing)} "
            f"{verb} none. The years shown ({_span(old)} against {_span(new)}) "
            f"are the earliest and latest record each file holds, which is the "
            f"span the corpus turned out to cover and not the span that was "
            f"asked for. Until both files record a window, a record holding in "
            f"one corpus only cannot be told apart from a record in a year the "
            f"other harvest never asked about. Re-run the harvest to record one."
        )
    if not changed:
        return (
            f"Both corpora record the same search window, {_span(old)}. The "
            f"counts below are over the same years on both sides, so a record "
            f"holding in one corpus only is not a year the other harvest "
            f"skipped."
        )
    shared = (f"{shared_start}-{shared_end}" if shared_start is not None
              else "no year at all")
    extra = ", ".join(part for part in (
        f"{_years(only_new)} only the newer harvest asked about" if only_new else "",
        f"{_years(only_old)} only the older one asked about" if only_old else "",
    ) if part)
    return (
        f"The two corpora were harvested over different search windows: the "
        f"older one records {_span(old)}, the newer one {_span(new)}. Both "
        f"harvests asked about {shared}, with {extra}. A record counted below "
        f"as holding in one corpus only, but sitting outside {shared}, is in a "
        f"year the other harvest never asked about — that is the window having "
        f"moved, not the record. Every count below is split on that line."
    )


def _side_of_window(year: int, window: Mapping[str, Any]) -> bool | None:
    """True inside the years both harvests covered, False outside, None unknown.

    None for an undated record and for a pair whose windows cannot be compared.
    Both are the absence of an answer, and the two reasons are already stated in
    the window block above rather than encoded in a third value here.
    """
    if not window["comparable"] or year == UNDATED_YEAR:
        return None
    if window["shared_start"] is None or window["shared_end"] is None:
        return False
    return window["shared_start"] <= year <= window["shared_end"]


# ------------------------------------------------------------------
# Records
# ------------------------------------------------------------------


def _key_index(papers: Sequence[Mapping[str, Any]]) -> set[str]:
    """Every dedup key this corpus publishes, flattened.

    Deliberately a set of keys and not a mapping back to records: this module
    answers "does the other corpus hold this paper", not "which record is it",
    and the weaker structure is the one that cannot be misread as a pairing.
    """
    keys: set[str] = set()
    for record in papers:
        keys.update(paper_keys(record))
    return keys


def _record_state(record: Mapping[str, Any], other_keys: set[str],
                  window: Mapping[str, Any]) -> tuple[bool, bool | None] | None:
    """(holds on this side only, which side of the shared window), or None.

    None means unmatchable: a record with no DOI, no PMID and no readable title
    carries no key and can be compared against nothing. Reporting it as holding
    on one side only would be a claim made out of missing metadata.
    """
    keys = paper_keys(record)
    if not keys:
        return None
    if any(key in other_keys for key in keys):
        return (False, None)
    return (True, _side_of_window(paper_year(dict(record)), window))


def _record_row(record: Mapping[str, Any], in_shared: bool | None) -> dict[str, Any]:
    """One listed record, reduced to what identifies it to a reader."""
    year = paper_year(dict(record))
    return {
        "pmid": str(record.get("pmid") or ""),
        "doi": str(record.get("doi") or ""),
        "title": str(record.get("title") or ""),
        "journal": str(record.get("journal") or ""),
        "year": None if year == UNDATED_YEAR else year,
        "in_shared_window": in_shared,
    }


def _split_records(papers: Sequence[Mapping[str, Any]], other_keys: set[str],
                   window: Mapping[str, Any]) -> tuple[list[dict[str, Any]], int, int]:
    """(records the other corpus does not hold, how many it does, unmatchable)."""
    only: list[dict[str, Any]] = []
    in_both = 0
    unkeyable = 0
    for record in papers:
        state = _record_state(record, other_keys, window)
        if state is None:
            unkeyable += 1
        elif state[0]:
            only.append(_record_row(record, state[1]))
        else:
            in_both += 1
    return only, in_both, unkeyable


def _window_tally(rows: Sequence[Mapping[str, Any]]) -> tuple[int, int, int]:
    """(inside the shared window, outside it, undated or not comparable)."""
    inside = sum(1 for row in rows if row["in_shared_window"] is True)
    outside = sum(1 for row in rows if row["in_shared_window"] is False)
    return inside, outside, len(rows) - inside - outside


# ------------------------------------------------------------------
# One side, prepared the way the report prepares it
# ------------------------------------------------------------------


def _roster(corpus: Mapping[str, Any]) -> dict[str, Any]:
    """One corpus's prepared records and the roster built from them.

    The same three calls, in the same order and with the same corpus-recorded
    identity, that `profile.report.build_report` makes. Running a private
    grouping here instead would let this module name a person the report does
    not, or miss one it does, with nothing on either page saying why.

    `raw_of` maps each prepared record back to the corpus record it came from,
    by object identity. It is needed because the dedup keys live on the raw
    record — `_keys_of` reads `pub_year`, which `prepare_paper` converts to an
    `int` under a different name — while the roster speaks in prepared records.
    Re-deriving the keys from the prepared copy would be a second key function,
    which is the thing this module exists not to have.
    """
    identity = corpus.get("identity")
    identity = dict(identity) if isinstance(identity, Mapping) else {}
    raw = [record for record in (corpus.get("papers") or [])
           if isinstance(record, Mapping)]

    prepared = [prepare_paper(dict(record), identity) for record in raw]
    raw_of = {id(copy): record for copy, record in zip(prepared, raw, strict=True)}

    exclusions = apply_record_exclusions(prepared)
    kept = exclusions["kept"]
    records_only = exclusions["records_only"]
    start, end = window_years(dict(corpus), kept + records_only)
    people = build_people(kept, identity, (), start, end)
    return {
        "identity": identity,
        "papers": raw,
        "prepared": prepared,
        "raw_of": raw_of,
        "kept": kept,
        "records_only": records_only,
        "people": people["people"],
        "appearances": people["appearances"],
    }


# ------------------------------------------------------------------
# People, at the roster's own caliber
# ------------------------------------------------------------------


def _loose_by_name(appearances: Sequence[Mapping[str, Any]]) -> dict[str, tuple[str, str]]:
    """Display name -> `roles.key_loose`, read off the appearances themselves.

    A person record carries a display name and an ORCID but not the name parts
    the roster grouped on, and splitting the display name here would be a second
    surname rule beside `key_loose` — wrong for exactly the names this tool is
    most used on, where the family name comes first. The appearances still hold
    the parts, so the key is looked up rather than re-derived.
    """
    table: dict[str, tuple[str, str]] = {}
    for appearance in appearances:
        name = str(appearance.get("name") or "")
        if name and name not in table:
            table[name] = key_loose(dict(appearance))
    return table


def _person_keys(person: Mapping[str, Any],
                 loose_by_name: Mapping[str, tuple[str, str]]) -> list[str]:
    """The keys one roster entry can be recognised by in the other roster.

    Both are kept, never only the strongest. An ORCID that a publisher deposited
    on one paper appears in whichever harvest holds that paper and is absent
    from the other; keying on the ORCID alone would then report one person as
    two, once as an arrival and once as an absence — the most misleading row
    this module could produce. Two entries are the same person when their key
    sets intersect, which is the rule `_keys_of` and `merge_corpora` already
    apply to records.
    """
    keys: list[str] = []
    orcid = str(person.get("orcid") or "").strip()
    if orcid:
        keys.append(f"orcid:{orcid.lower()}")
    last, initial = loose_by_name.get(str(person.get("name") or ""), ("", ""))
    if last:
        keys.append(f"loose:{last}|{initial}")
    return keys


def _keyed_people(side: Mapping[str, Any]) -> list[tuple[list[str], dict[str, Any]]]:
    """Roster entries that carry at least one cross-corpus key, with their keys.

    An entry with neither an ORCID nor a surname — `roles` flags it
    `incomplete_name` — cannot be looked for in the other roster at all. It is
    left out here and counted as unmatchable by the caller, because listing it
    as a person who newly appears would report a gap in the byline as a fact
    about the lab.
    """
    loose_by_name = _loose_by_name(side["appearances"])
    keyed: list[tuple[list[str], dict[str, Any]]] = []
    for person in side["people"]:
        keys = _person_keys(person, loose_by_name)
        if keys:
            keyed.append((keys, person))
    return keyed


def _person_row(person: Mapping[str, Any]) -> dict[str, Any]:
    """One listed person, carrying the roster's own statement about them."""
    return {
        "name": person.get("name", ""),
        "orcid": person.get("orcid", ""),
        "marker": person.get("marker", ""),
        "flags": list(person.get("flags") or []),
        "stratum": person.get("stratum", ""),
        "n_appearances": person.get("n_appearances", 0),
        "n_first_slots": person.get("n_first_slots", 0),
        "first_year": person.get("first_year"),
        "last_year": person.get("last_year"),
    }


def _people_block(old: Mapping[str, Any], new: Mapping[str, Any]) -> dict[str, Any]:
    old_keyed = _keyed_people(old)
    new_keyed = _keyed_people(new)
    old_key_set = {key for keys, _ in old_keyed for key in keys}
    new_key_set = {key for keys, _ in new_keyed for key in keys}

    only_new = [person for keys, person in new_keyed
                if not any(key in old_key_set for key in keys)]
    only_old = [person for keys, person in old_keyed
                if not any(key in new_key_set for key in keys)]

    return {
        "old_total": len(old["people"]),
        "new_total": len(new["people"]),
        # The records each roster was built from. A roster is only as large as
        # the corpus it came out of, so this is the denominator behind the
        # denominator and the two are never printed apart.
        "kept_old": len(old["kept"]),
        "kept_new": len(new["kept"]),
        "n_in_both": len(new_keyed) - len(only_new),
        "n_only_in_new": len(only_new),
        "n_only_in_old": len(only_old),
        "n_unkeyable_old": len(old["people"]) - len(old_keyed),
        "n_unkeyable_new": len(new["people"]) - len(new_keyed),
        "only_in_new": [_person_row(person) for person in only_new],
        "only_in_old": [_person_row(person) for person in only_old],
    }


# ------------------------------------------------------------------
# First-author slots
# ------------------------------------------------------------------


def _membership_by_pmid(side: Mapping[str, Any], other_keys: set[str],
                        window: Mapping[str, Any]) -> dict[str, Any]:
    """PMID -> what `_record_state` says about the record under it, or None.

    `build_people` records an appearance by PMID and nothing else, and a merged
    OpenAlex-only record carries none, so several kept records can sit under the
    empty string. Where they do and they do not agree, the answer is None and
    the slot is counted as unresolved rather than attributed to whichever of
    them was reached first.
    """
    states: dict[str, set[Any]] = {}
    for prepared in side["kept"]:
        raw = side["raw_of"].get(id(prepared))
        state = None if raw is None else _record_state(raw, other_keys, window)
        states.setdefault(prepared["pmid"], set()).add(state)
    return {pmid: (next(iter(group)) if len(group) == 1 else None)
            for pmid, group in states.items()}


def _holders(side: Mapping[str, Any], other: Mapping[str, Any],
             membership: Mapping[str, Any], here: str, there: str,
             flag: str) -> tuple[list[dict[str, Any]], int, int]:
    """Who holds the first-author slots sitting on records only `side` holds.

    Returns the rows, how many slots were attributed, and how many sat on a
    record that could not be identified.

    Each row carries the holder's first-author count in **both** corpora, so the
    number reads as "one of this person's three first-author slots in the newer
    corpus, against two in the older one" rather than as a bare "one". A
    counterpart of None is not zero: it means the other roster does not name
    this person at all, which `flag` says in a word.
    """
    loose_here = _loose_by_name(side["appearances"])
    loose_there = _loose_by_name(other["appearances"])
    by_key: dict[str, Mapping[str, Any]] = {}
    for person in other["people"]:
        for key in _person_keys(person, loose_there):
            by_key.setdefault(key, person)

    rows: list[dict[str, Any]] = []
    attributed = 0
    unresolved = 0
    for person in side["people"]:
        pmids: list[str] = []
        outside = 0
        for pmid in person["first_slot_pmids"]:
            state = membership.get(pmid)
            if state is None:
                unresolved += 1
                continue
            only_here, in_shared = state
            if not only_here:
                continue
            pmids.append(pmid)
            if in_shared is False:
                outside += 1
        if not pmids:
            continue
        attributed += len(pmids)
        counterpart = next((by_key[key] for key in _person_keys(person, loose_here)
                            if key in by_key), None)
        rows.append({
            "name": person["name"],
            "orcid": person["orcid"],
            "marker": person["marker"],
            "stratum": person["stratum"],
            f"n_first_slots_{here}": person["n_first_slots"],
            f"n_first_slots_{there}": (None if counterpart is None
                                       else counterpart["n_first_slots"]),
            f"n_on_records_only_in_{here}": len(pmids),
            "pmids": pmids,
            "n_outside_shared_window": outside,
            flag: counterpart is None,
        })
    return rows, attributed, unresolved


def _first_author_block(old: Mapping[str, Any], new: Mapping[str, Any],
                        old_keys: set[str], new_keys: set[str],
                        window: Mapping[str, Any]) -> dict[str, Any]:
    new_rows, new_attributed, new_unresolved = _holders(
        new, old, _membership_by_pmid(new, old_keys, window),
        "new", "old", "new_to_roster")
    old_rows, old_attributed, old_unresolved = _holders(
        old, new, _membership_by_pmid(old, new_keys, window),
        "old", "new", "absent_from_newer_roster")
    return {
        "old_total": sum(person["n_first_slots"] for person in old["people"]),
        "new_total": sum(person["n_first_slots"] for person in new["people"]),
        "n_on_records_only_in_new": new_attributed,
        "n_on_records_only_in_old": old_attributed,
        "n_slots_on_unresolved_records": new_unresolved + old_unresolved,
        "holders_only_in_new": new_rows,
        "holders_only_in_old": old_rows,
    }


# ------------------------------------------------------------------
# Identity
# ------------------------------------------------------------------


def _identity_value(identity: Mapping[str, Any], field: str) -> Any:
    value = identity.get(field)
    if isinstance(value, (list, tuple)):
        return sorted(str(item) for item in value)
    return str(value or "")


def _identity_block(old: Mapping[str, Any], new: Mapping[str, Any]) -> dict[str, Any]:
    """Which pieces of recorded identity evidence the two harvests disagree on.

    Not a judgement about either: a harvest that gained an ORCID is a better
    harvest. It is here because it moved the membership rule, so the counts
    under it describe two differently filtered corpora — which is the same thing
    a moved window does, and is reported beside it for that reason.
    """
    changed = [field for field in IDENTITY_FIELDS
               if _identity_value(old, field) != _identity_value(new, field)]
    return {
        "same": not changed,
        "changed_fields": changed,
        "old": {field: _identity_value(old, field) for field in IDENTITY_FIELDS},
        "new": {field: _identity_value(new, field) for field in IDENTITY_FIELDS},
    }


# ------------------------------------------------------------------
# The whole comparison
# ------------------------------------------------------------------


def diff_corpora(old: Mapping[str, Any], new: Mapping[str, Any]) -> dict[str, Any]:
    """What the newer corpus says that the older one did not, and the reverse.

    `old` and `new` are two corpora about the same advisor, in the shape
    `cli._profile_corpus` returns and `export.load_papers_json` feeds. The order
    of the arguments is the order of the story: swapping them swaps the two
    sides of every count and changes nothing else. Neither input is modified.

    Returns::

        {
          "window": {"old", "new", "comparable", "same", "changed_bounds",
                     "shared_start", "shared_end", "years_only_new_looked_at",
                     "years_only_old_looked_at", "note"},
          "identity": {"same", "changed_fields", "old", "new"},
          "papers": {"old_total", "new_total",
                     "n_in_both_old_side", "n_in_both_new_side",
                     "n_only_in_old", "n_only_in_new",
                     "n_unkeyable_old", "n_unkeyable_new", "one_to_many",
                     "only_in_old": [...], "only_in_new": [...],
                     "by_window": {...}},
          "people": {"old_total", "new_total", "kept_old", "kept_new",
                     "n_in_both", "n_only_in_old", "n_only_in_new",
                     "n_unkeyable_old", "n_unkeyable_new",
                     "only_in_old": [...], "only_in_new": [...]},
          "first_author_slots": {"old_total", "new_total",
                                 "n_on_records_only_in_new",
                                 "n_on_records_only_in_old",
                                 "n_slots_on_unresolved_records",
                                 "holders_only_in_new": [...],
                                 "holders_only_in_old": [...]},
          "notes": [...],
          "caliber_changed": bool,
        }

    Three properties hold of every count in it:

    - **Each one comes with the total it was taken out of.** `n_only_in_new` is
      never readable without `new_total`, so the two travel together, and on
      each side `only + both + unkeyable == total` exactly. Where the same fact
      can be counted from either side — `n_in_both_old_side` and
      `n_in_both_new_side` — both are returned rather than one being picked.
      They differ only when a record on one side matches several on the other,
      and `one_to_many` says so instead of leaving the reader to subtract and
      find the arithmetic broken.
    - **Each one is split by the window** when the two windows are comparable. A
      record holding only in the newer corpus but sitting in a year the older
      harvest never asked about is not a change in output, and the split is what
      keeps those two apart.
    - **None of them says why.** See `CAUSE_NOTE`, which is returned in `notes`.
    """
    old_side = _roster(old)
    new_side = _roster(new)

    window = _window(
        _window_side(old, old_side["kept"] + old_side["records_only"]),
        _window_side(new, new_side["kept"] + new_side["records_only"]),
    )

    old_keys = _key_index(old_side["papers"])
    new_keys = _key_index(new_side["papers"])
    only_in_new, both_new_side, unkeyable_new = _split_records(
        new_side["papers"], old_keys, window)
    only_in_old, both_old_side, unkeyable_old = _split_records(
        old_side["papers"], new_keys, window)

    new_inside, new_outside, new_undated = _window_tally(only_in_new)
    old_inside, old_outside, old_undated = _window_tally(only_in_old)

    papers = {
        "old_total": len(old_side["papers"]),
        "new_total": len(new_side["papers"]),
        "n_in_both_old_side": both_old_side,
        "n_in_both_new_side": both_new_side,
        "n_only_in_old": len(only_in_old),
        "n_only_in_new": len(only_in_new),
        "n_unkeyable_old": unkeyable_old,
        "n_unkeyable_new": unkeyable_new,
        "one_to_many": both_old_side != both_new_side,
        "only_in_old": only_in_old,
        "only_in_new": only_in_new,
        "by_window": {
            "applicable": bool(window["comparable"]),
            "shared_start": window["shared_start"],
            "shared_end": window["shared_end"],
            "only_in_new_inside_shared": new_inside,
            "only_in_new_outside_shared": new_outside,
            "only_in_new_undated": new_undated,
            "only_in_old_inside_shared": old_inside,
            "only_in_old_outside_shared": old_outside,
            "only_in_old_undated": old_undated,
        },
    }

    identity = _identity_block(old_side["identity"], new_side["identity"])
    people = _people_block(old_side, new_side)
    first_author = _first_author_block(old_side, new_side, old_keys, new_keys, window)

    # The window leads, always. A reader who stops after one line has to have
    # read the thing that most often turns a change of question into a finding.
    notes = [window["note"]]
    if not identity["same"]:
        notes.append(
            "The two harvests recorded different identity evidence "
            f"({', '.join(identity['changed_fields'])}), so they did not keep "
            "records by the same rule. A record holding in one corpus only can "
            "be that rule having moved."
        )
    notes.append(CAUSE_NOTE)

    logger.info(
        "语料对比：论文 %d 篇 → %d 篇（两边都有 %d 篇；只在旧的里 %d 篇，只在新的里 %d 篇，"
        "其中 %d 篇落在两次检索共同覆盖的年份之外）；人员 %d 人 → %d 人"
        "（只在新的里 %d 人，只在旧的里 %d 人）；一作名额 %d → %d。"
        "本模块只报「在哪一份语料里」，不判断为什么——检索窗口%s。",
        papers["old_total"], papers["new_total"], both_new_side,
        papers["n_only_in_old"], papers["n_only_in_new"], new_outside,
        people["old_total"], people["new_total"],
        people["n_only_in_new"], people["n_only_in_old"],
        first_author["old_total"], first_author["new_total"],
        "两份相同" if window["same"] else
        ("两份不同，见 window.note" if window["comparable"] else "无法比较，见 window.note"),
    )

    return {
        "window": window,
        "identity": identity,
        "papers": papers,
        "people": people,
        "first_author_slots": first_author,
        "notes": notes,
        "caliber_changed": bool(window["same"] is not True or not identity["same"]),
    }
