"""
Rendering for the advisor profile report.

Owns the corpus gates and the section ordering that turns metric dicts into a
Markdown document plus a JSON record of the same numbers. The caveat strings it
renders come from `caveats.py` unchanged.

This module draws nothing. The activity timeline is an inline SVG in the HTML
report (`charts.person_timeline_chart`); `build_report` keeps `gantt_path` so a
caller holding a raster of its own (`analyze`) can still point at one.

What this renderer will and will not turn a number into. The three cases are
different in kind and are never merged, because a reader who cannot tell them
apart cannot tell which one would change tomorrow:

- **Printed.** Absolute values, each beside the denominator it was computed
  over. Citation counts, h-index, i10-index, median citations, one composite
  score out of 100 with every input, weight and contribution beside it, and —
  new in this round — the star band that score falls in (`ranking.star_rating`).
  On the side-by-side page, a rank among the corpora actually on that page, and
  a one-sentence statement of which of two corpora scored higher
  (`ranking.rank_corpora`, `ranking.comparative_statement`).
- **Still refused.** Percentile and quantile position, because there is no
  reference population anywhere in this toolkit and one cannot be assembled from
  the few corpora a user happened to load. Letter tiers A/B/C, refused by
  decision — stars are produced and letters are not, and that split is
  deliberate rather than an inconsistency to be tidied away. Fitted trends,
  slopes and year-over-year percentage change, unchanged: a handful of
  right-censored integer points do not support a slope. The current register is
  `ranking.RANKING_EXCLUSIONS` and Section 16 prints it verbatim.
- **Supplied by hand or absent.** Journal Impact Factor, JCR quartile and CAS
  partition are not shipped with this toolkit and are not fetched by it — those
  tables are licensed products and there is no crawler here. Section 18 joins
  them from a CSV the reader fills in themselves, prints the edition (官方版 /
  新锐版 / 民间版 / JCR) and the retrieval date beside every number, and prints
  "未提供对照表" in the column when no table was given. Section 17 does the same
  for a degree-thesis export, which is the only source that carries graduates
  who published nothing at all.

Section 17 is printed immediately after Section 0 and keeps a number that sorts
last. It is placed there because it repairs the limit Section 0 states — every
count in Sections 1 to 16 is conditioned on having published — and it is
numbered there because renumbering 1 to 16 would falsify every cross-reference
in `caveats.py`, `scoring.py`, `theses.py` and `journals.py`, none of which this
module owns.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from .cohesion import CLUSTER_DETAIL_MIN, coauthor_clusters
from ..journals import (
    EDITIONS,
    JOURNAL_CAVEATS,
    MATCH_ABBREV,
    MATCH_ABBREV_OFFICIAL,
    MATCH_EXACT,
    MATCH_ISSN,
    join_journals,
)
from ..theses import (
    DENOMINATOR_LADDER,
    MATCH_RULES,
    ROSTER_LIMITS,
    THESIS_DENOMINATOR_CAVEAT,
    reconcile_roster,
)
from . import metrics as M
from .caveats import DROPPED_REGISTER, caveat
from .impact import citation_metrics
from .ranking import (
    RANKING_EXCLUSIONS,
    RANK_METHOD,
    RANK_TIE_NOTE,
    STAR_BASIS,
    STAR_MAX,
    comparative_statement,
    rank_corpora,
    star_rating,
)
from .roles import apply_record_exclusions, build_people, prepare_paper
from .scoring import SCORING_EXCLUSIONS, composite_score, resolve_weights

# 2: the report record gained `score` and `impact` at the top level, and
# sections 15 and 16. Both additions are additive — every key a version-1
# consumer reads is still present and unchanged — but a consumer that asserted
# "this file contains no citation number" was relying on the old version being
# the whole story, and deserves to see the number change.
#
# 3: `stars` sits beside `score`, and `graduates` and `journals` sit beside
# `impact`, with Sections 17 and 18 rendering them. Additive again, and again
# worth a version bump for the same reason: a consumer that asserted "this file
# contains no star count and no journal partition" was reading a promise that
# has been withdrawn on purpose, and should find out from the version rather
# than from a surprising value.
SCHEMA_VERSION = 3

DEFAULT_ADVISOR_CONFIG: dict[str, Any] = {"exclude_names": [], "lag_years": 3}

# Section 6.4. The only permitted labels. No field in PubMed separates a PhD
# student from a postdoc, technician, staff scientist, clinical fellow, rotation
# student or visiting scholar, so the report never claims to.
STRATUM_LABEL = {
    "A": "lead-trainee candidate",
    "B": "support candidate",
    "C": "single appearance",
    "D": "senior collaborator",
    "unclassified": "unclassified",
}

# --- Gates ---

# Name and refusal message per gate, in one mapping so the two cannot drift apart.
GATES: dict[str, tuple[str, str]] = {
    "G1": ("truncation",
           "esearch matched {count} records but only {returned} were retrieved. Every count in this "
           "report would be wrong by an unbounded amount. Raise retmax, reduce years_back, or add "
           "affiliation_keywords, then re-fetch."),
    "G2": ("identity fallback",
           "No paper passed identity verification. The corpus is 'every paper by anyone sharing this "
           "name' and describes several different researchers. Configure orcid, affiliation_keywords, "
           "or email_domains and re-fetch."),
    "G3": ("weak identity config",
           "No identity evidence is configured, so the corpus is a name match only. For any common "
           "surname this blends several people. Set at least one of orcid / affiliation_keywords / "
           "email_domains."),
    "G4": ("no structured authors",
           "Corpus lacks structured author records. Run the profile fetch stage; the report cannot be "
           "built from the Excel export."),
    "G5": ("empty corpus", "0 papers remain after exclusions. Nothing can be reported."),
}


def _gate(gate_id: str, observed: dict[str, Any], **fields: Any) -> dict[str, Any]:
    name, message = GATES[gate_id]
    return {
        "id": gate_id,
        "name": name,
        "message": message.format(**fields),
        "observed": observed,
    }


def check_corpus_gates(corpus: dict[str, Any]) -> dict[str, Any] | None:
    """
    Gates G1-G4, evaluated before anything is computed. G5 needs the record
    exclusions and fires later.

    There is no degrade-with-a-warning path. A warning gets scrolled past; a
    missing report does not.
    """
    query = corpus.get("query") or {}
    count = int(query.get("esearch_count") or 0)
    returned = int(query.get("pmids_returned") or 0)
    if count > returned:
        return _gate("G1", {"esearch_count": count, "pmids_returned": returned},
                     count=count, returned=returned)

    if bool(corpus.get("fallback_fired")):
        return _gate("G2", {"fallback_fired": True})

    identity = corpus.get("identity") or {}
    if not (identity.get("orcid") or "").strip() \
            and not (identity.get("affiliation_keywords") or []) \
            and not (identity.get("email_domains") or []):
        return _gate("G3", {
            "orcid": identity.get("orcid", ""),
            "affiliation_keywords": identity.get("affiliation_keywords", []),
            "email_domains": identity.get("email_domains", []),
        })

    papers = corpus.get("papers")
    if not isinstance(papers, list):
        return _gate("G4", {"papers": type(papers).__name__})
    for paper in papers:
        authors = paper.get("authors") if isinstance(paper, dict) else None
        if not isinstance(authors, list) or not authors or not all(isinstance(a, dict) for a in authors):
            return _gate("G4", {"pmid": (paper or {}).get("pmid", "?") if isinstance(paper, dict) else "?"})
    return None


_SPREADSHEET_SUFFIXES = {".xlsx", ".xlsm", ".xls", ".csv"}


def check_source_path(path: str | Path) -> dict[str, Any] | None:
    """
    G4 for the Excel export, decided from the extension so the file is never
    opened.

    `build_author_records` silently falls back to splitting `authors_str` when a
    record has no PMID match, which forces equal_contrib=False,
    is_corresponding=False and affiliation="" for every author. That converts
    "unknown" into a confident zero, which is the most dangerous silent failure
    in the pipeline — so the report refuses to start from a spreadsheet at all.
    """
    if Path(path).suffix.lower() in _SPREADSHEET_SUFFIXES:
        return _gate("G4", {"path": str(path)})
    return None


# --- Corpus loading and window derivation ---


def load_corpus(path: str | Path) -> dict[str, Any]:
    """Read one advisor corpus JSON. The only file this module ever opens."""
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Advisor corpus must be a JSON object: {path}")
    return data


def _year_in(text: Any) -> int | None:
    match = re.search(r"(\d{4})", str(text or ""))
    return int(match.group(1)) if match else None


def window_years(corpus: dict[str, Any], papers: Sequence[dict[str, Any]]) -> tuple[int, int]:
    """
    Window bounds from the recorded query, falling back to the corpus itself.

    Taking them from the query rather than from the data matters for censoring:
    a person whose first paper is the earliest in the corpus is only
    left-censored if the window actually starts there.
    """
    query = corpus.get("query") or {}
    years = [paper["year"] for paper in papers]
    start = _year_in(query.get("mindate"))
    end = _year_in(query.get("maxdate"))
    if start is None:
        start = min(years) if years else 0
    if end is None:
        end = max(years) if years else start
    return start, max(start, end)


# --- Report construction ---


def _section(
    section_id: int,
    title: str,
    body: Sequence[str] = (),
    caveats: Sequence[str] = (),
    prose: Sequence[str] = (),
) -> dict[str, Any]:
    """
    One rendered section.

    `body` holds computed values, `prose` and `caveats` hold fixed text. The
    split exists so a test can scan the computed half for prohibited quantities
    without tripping over the caveats that name those same quantities in order
    to rule them out.
    """
    return {
        "id": section_id,
        "title": title,
        "body": list(body),
        "caveats": list(caveats),
        "prose": list(prose),
    }


def _fmt_number(value: float | None) -> str:
    if value is None:
        return "n/a"
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def _fmt_person(entry: dict[str, Any]) -> str:
    return f"{entry['name']}{entry.get('marker', '')}".strip()


def _pmid_list(pmids: Sequence[str]) -> str:
    return ", ".join(pmids) if pmids else "none"


def resolve_score_weights(config: dict[str, Any] | None) -> dict[str, float]:
    """
    The composite-score weight table for one run, fully resolved.

    Read from `config["scoring"]["weights"]`, which is where `config.py` puts it
    and therefore where a user edits it. `scoring.resolve_weights` keys it
    differently — `advisor.score_weights`, then `score_weights` — so both
    spellings are honoured and the config-file one wins; a hand-built config
    dict from an older caller keeps working unchanged.

    Whatever comes back is merged onto `scoring.DEFAULT_WEIGHTS` and validated
    there: an unknown component name or a negative weight raises rather than
    being ignored, because silently dropping a typo lets a reader believe they
    changed the score when they did not. The merged table is the one Section 16
    prints verbatim.
    """
    config = config or {}
    scoring_config = config.get("scoring")
    override = scoring_config.get("weights") if isinstance(scoring_config, Mapping) else None
    if override is None:
        return resolve_weights(config)
    return resolve_weights({"score_weights": override})


def build_report(
    corpus: dict[str, Any],
    config: dict[str, Any] | None = None,
    gantt_path: str | Path | None = None,
    now: datetime | None = None,
    citations: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    citations_note: str = "",
    journal_table: Mapping[str, Any] | None = None,
    journal_note: str = "",
    thesis_roster: Mapping[str, Any] | None = None,
    thesis_note: str = "",
) -> dict[str, Any]:
    """
    Build the whole report from one corpus dict.

    Returns a dict carrying the metric values, the rendered sections, and
    `refused`/`exit_code` so a caller can honour a fired gate without inspecting
    the text.

    `citations` is the payload of a `citations_<timestamp>.json` (or the bare
    record list) as written by `citations.save_citations_json`. It is optional
    and its absence is not a gate: a missing citation file costs Section 15 and
    the two citation score components, and leaves the six questions this report
    was built to answer exactly as they were. `citations_note` is the caller's
    one-line explanation of that absence — which directory was searched, or
    which flag suppressed the lookup — printed in place of the section so the
    reader is never left to guess between "not fetched" and "fetched, nothing
    found".

    `journal_table` is a `journals.load_journal_table` result and
    `thesis_roster` a `theses.load_thesis_roster` result. Both are hand-filled
    local files, both are optional, and neither is ever fetched: there is no
    crawler in this package and there will not be one. Their `_note` companions
    play the same role `citations_note` does — Section 17 and Section 18 print
    the reason a table is absent rather than an empty cell, because a blank
    partition column and a blank graduate count are each indistinguishable from
    a finding if nobody says which file was looked for.

    `journal_table=None` still produces Section 18: `join_journals` accepts a
    missing table by design and returns the corpus's own journal counts with
    `table_missing`, which is what the section needs to say how much work the
    lookup would be. `thesis_roster=None` produces the section with no counts at
    all, because there is nothing to count and inventing a zero denominator
    would be the exact fabrication that section exists to prevent.
    """
    now = now or datetime.now()
    config = config or {}
    advisor = {**DEFAULT_ADVISOR_CONFIG, **(config.get("advisor") or {})}
    identity = corpus.get("identity") or {}
    author_name = identity.get("author_name", "") or config.get("author_name", "")

    gate = check_corpus_gates(corpus)
    if gate:
        return _refusal(gate, author_name, now)

    prepared = [prepare_paper(paper, identity) for paper in corpus["papers"]]
    exclusions = apply_record_exclusions(prepared)
    kept = exclusions["kept"]
    records_only = exclusions["records_only"]
    if not kept:
        return _refusal(_gate("G5", {"papers_after_exclusions": 0}), author_name, now)

    start_year, end_year = window_years(corpus, kept + records_only)
    people_data = build_people(
        kept, identity, advisor["exclude_names"], start_year, end_year
    )
    people = people_data["people"]
    lead_stratum = people_data["lead_stratum_by_pmid"]
    position_filtered = bool(corpus.get("position_filtered", True))

    partition = M.lead_slot_partition(people, end_year, advisor["lag_years"])
    computed = {
        "s2": M.person_roster(people, people_data["n_strict"], people_data["n_loose"]),
        "s3a": M.first_author_slots(kept, lead_stratum),
        "s3b": partition,
        "s4": M.time_to_lead(people, partition),
        "s5": M.activity_span(people),
        "s6": M.roster_turnover(people, start_year, end_year),
        "s7": M.pi_byline_positions(kept, position_filtered),
        "s8": M.equal_contrib_occurrences(kept),
        "s9": M.records_per_year(kept, records_only, start_year, end_year),
        "s10": M.team_size(kept, lead_stratum),
        "s11": M.venue_repetition(kept),
        "s12": M.affiliation_strings(kept, start_year, end_year),
        "s13": M.titles_by_year(kept),
        # Computed over `kept` like everything else, so the partition describes
        # the same records the rest of the report describes. Feeding it the raw
        # corpus would let excluded records form clusters that no other number
        # in the report can see.
        "s19": coauthor_clusters(kept, author_name),
    }

    # The key is `impact`, never `s14`: `scoring.COMPONENTS` accepts both names
    # for the citation source, and Section 14 already exists as the dropped
    # register. Filing anything else under `s14` would be silently read as a
    # citation bundle.
    #
    # Absent rather than empty when there is no citation file. `composite_score`
    # then reports both citation components as unavailable with the reason, which
    # is the truth; an empty dict would be read as "looked, found nothing", and a
    # corpus with no citation lookup is not a corpus with no citations.
    impact = citation_metrics(kept, citations) if citations is not None else None
    if impact is not None:
        computed["impact"] = impact

    weights = resolve_score_weights(config)
    score = composite_score(computed, weights)
    # One corpus, so this is the score coarsened and nothing else — no position
    # among anybody. `star_rating` returns the same shape whether or not it
    # produced a count, so Section 16 never has to branch on a missing key.
    stars = star_rating(score)

    # Always computed: `join_journals` treats a missing table as a supported
    # call and comes back with `table_missing` plus the corpus's own journal
    # counts, which is exactly what the "go and look these up" line needs.
    journals = join_journals(kept, journal_table)
    # Only computed when a roster exists. `reconcile_roster` would accept None
    # and report "the thesis roster is empty", which is true of the argument and
    # misleading about the world: no file was supplied, and that is a different
    # sentence from a file that supplied nobody.
    graduates = reconcile_roster(people_data, thesis_roster, author_name) \
        if thesis_roster is not None else None

    counts = corpus.get("counts") or {}
    provenance = {
        "author_name": author_name,
        "query": corpus.get("query") or {},
        "identity": {
            "orcid": identity.get("orcid", ""),
            "affiliation_keywords": identity.get("affiliation_keywords", []),
            "email_domains": identity.get("email_domains", []),
            "require_affiliation_effective": identity.get("require_affiliation_effective", "unknown"),
        },
        "counts": counts,
        "fallback_fired": bool(corpus.get("fallback_fired")),
        "position_filtered": position_filtered,
        "window_start_year": start_year,
        "window_end_year": end_year,
        "lag_years": advisor["lag_years"],
        "exclude_names": list(advisor["exclude_names"]),
        "corpus_size": len(kept),
        "records_only_size": len(records_only),
        "exclusions": exclusions["excluded"],
        "title_duplicates": exclusions["title_duplicates"],
        "slot0_collective_pmids": exclusions["slot0_collective_pmids"],
        "sole_author_papers": people_data["sole_author_papers"],
        "ambiguous_pi_papers": [p["pmid"] for p in kept if p["pi_ambiguous"]],
        "n_strict": people_data["n_strict"],
        "n_loose": people_data["n_loose"],
        "n_people": len(people),
        "flips": people_data["flips"],
    }

    used_caveats: dict[str, str] = {}
    sections = _build_sections(
        provenance, computed, used_caveats, gantt_path, impact, citations_note, score,
        stars=stars, journals=journals, journal_note=journal_note,
        graduates=graduates, thesis_note=thesis_note,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat(timespec="seconds"),
        "author_name": author_name,
        "refused": False,
        "exit_code": 0,
        "gate": None,
        "gantt_path": str(gantt_path) if gantt_path else None,
        "provenance": provenance,
        "metrics": computed,
        # Hoisted out of `metrics` so a consumer reads one score per report and
        # cannot accidentally feed it back into `composite_score` as an input.
        "score": score,
        # Beside the score, never inside it: a star count is that score
        # coarsened, and a consumer that fed it back into anything would be
        # weighting the same number twice.
        "stars": stars,
        "impact": impact,
        "citations_note": "" if impact is not None else (citations_note or _NO_CITATIONS_NOTE),
        "journals": journals,
        "journal_note": "" if journal_table is not None else (journal_note or _NO_JOURNAL_TABLE_NOTE),
        "graduates": graduates,
        "thesis_note": "" if graduates is not None else (thesis_note or _NO_THESIS_ROSTER_NOTE),
        "caveats": used_caveats,
        "sections": sections,
    }


def _refusal(gate: dict[str, Any], author_name: str, now: datetime) -> dict[str, Any]:
    """A fired gate produces the gate, the observed values and the fix. Nothing else.

    `score`, `stars`, `impact`, `journals` and `graduates` are None here for the
    same reason every metric is absent: once a gate fires, every number the
    corpus could produce is wrong by an unbounded amount, and a composite of
    wrong numbers is the most confident wrong number of all. A star band is the
    worst of them — five characters that survive being copied out of a refused
    report with nothing attached. The keys exist so a consumer can read them
    without a guard; their value is None, never 0 and never an empty dict.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat(timespec="seconds"),
        "author_name": author_name,
        "refused": True,
        "exit_code": 1,
        "gate": gate,
        "gantt_path": None,
        "provenance": {},
        "metrics": {},
        "score": None,
        "stars": None,
        "impact": None,
        "citations_note": "",
        "journals": None,
        "journal_note": "",
        "graduates": None,
        "thesis_note": "",
        "caveats": {},
        "sections": [],
    }


def build_report_from_path(
    path: str | Path,
    config: dict[str, Any] | None = None,
    gantt_path: str | Path | None = None,
    now: datetime | None = None,
    citations: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    citations_note: str = "",
    journal_table: Mapping[str, Any] | None = None,
    journal_note: str = "",
    thesis_roster: Mapping[str, Any] | None = None,
    thesis_note: str = "",
) -> dict[str, Any]:
    """Load a corpus file and build the report, refusing spreadsheets unopened."""
    gate = check_source_path(path)
    if gate:
        return _refusal(gate, (config or {}).get("author_name", ""), now or datetime.now())
    return build_report(
        load_corpus(path), config, gantt_path, now,
        citations=citations, citations_note=citations_note,
        journal_table=journal_table, journal_note=journal_note,
        thesis_roster=thesis_roster, thesis_note=thesis_note,
    )


# --- Sections ---


def _build_sections(
    prov: dict[str, Any],
    computed: dict[str, Any],
    used: dict[str, str],
    gantt_path: str | Path | None,
    impact: dict[str, Any] | None = None,
    citations_note: str = "",
    score: dict[str, Any] | None = None,
    stars: dict[str, Any] | None = None,
    journals: dict[str, Any] | None = None,
    journal_note: str = "",
    graduates: dict[str, Any] | None = None,
    thesis_note: str = "",
) -> list[dict[str, Any]]:
    def cav(caveat_id: str, **fields: Any) -> str:
        text = caveat(caveat_id, **fields)
        used[caveat_id] = text
        return text

    query = prov["query"]
    years_back = query.get("years_back", "?")
    name_only = (prov["counts"] or {}).get("name_only", 0)

    sections = [
        # Section 0 precedes provenance because the limits reframe every number
        # that follows.
        _section(0, "What this report is and is not", prose=[cav("CAV-00")]),
        # Section 17, printed second. It is the only section whose denominator is
        # not conditioned on having published, so it is the only one that can
        # answer the question CAV-00 raises and then leaves open. Its number
        # sorts last because it was added last and the sections below are
        # cross-referenced by number from four other modules; its position on the
        # page is where it belongs. Both facts are stated in the section itself
        # rather than left to look like a mistake.
        _section(17, "Graduates on record — the denominator PubMed cannot see",
                 body=_graduates_body(graduates, thesis_note),
                 prose=_graduates_prose()),
        # Printed third and numbered 19, for the same reason Section 17 is
        # printed second: it decides whether anything below it is about one
        # person. A reader who reaches the roster before reaching this has
        # already formed the belief this section exists to test.
        _section(19, "Co-author clusters — is this one person?",
                 body=_cohesion_body(computed["s19"]),
                 prose=_cohesion_prose()),
        _section(1, "Corpus provenance", body=_provenance_body(prov),
                 caveats=[cav("CAV-01", n=name_only),
                          cav("CAV-02", n_strict=prov["n_strict"], n_loose=prov["n_loose"])]),
        _section(2, "People and activity timeline",
                 body=_roster_body(computed["s2"], gantt_path),
                 caveats=[used["CAV-02"], cav("CAV-03")]),
        _section(3, "First-author slots",
                 body=_first_author_body(computed["s3a"], computed["s3b"]),
                 caveats=[cav("CAV-04"), cav("CAV-05"), cav("CAV-06")]),
        _section(4, "Time to a first-author slot", body=_time_to_lead_body(computed["s4"]),
                 caveats=[cav("CAV-07"), cav("CAV-08")]),
        _section(5, "Observed activity span",
                 body=_span_body(computed["s5"], prov["flips"]),
                 caveats=[cav("CAV-09"), cav("CAV-10", years_back=years_back), cav("CAV-11")]),
        _section(6, "Group size and turnover", body=_turnover_body(computed["s6"]),
                 caveats=[cav("CAV-12")]),
        _section(7, "The PI's own byline position", body=_pi_position_body(computed["s7"]),
                 caveats=(
                     [cav("CAV-13")] if not computed["s7"]["measured"] else []
                 ) + [
                     cav("CAV-14"),
                     cav("CAV-15",
                         covered=computed["s7"]["email_coverage"]["covered"],
                         total=computed["s7"]["email_coverage"]["denominator"]),
                 ]),
        _section(8, "Shared-authorship flags", body=_equal_contrib_body(computed["s8"]),
                 caveats=[cav("CAV-16")]),
        _section(9, "Records per year", body=_records_body(computed["s9"]),
                 caveats=[cav("CAV-17"), cav("CAV-18")]),
        _section(10, "Team size", body=_team_size_body(computed["s10"]), caveats=[cav("CAV-19")]),
        _section(11, "Venues", body=_venue_body(computed["s11"]), caveats=[cav("CAV-20")]),
        _section(12, "Affiliation strings", body=_affiliation_body(computed["s12"]),
                 caveats=[cav("CAV-21")]),
        _section(13, "Titles by year", body=_titles_body(computed["s13"]), caveats=[cav("CAV-22")]),
        _section(14, "What was deliberately not computed",
                 prose=[f"- **{name}** — {reason}" for name, reason in DROPPED_REGISTER]),
        _section(15, "Citation impact",
                 body=_impact_body(impact, citations_note),
                 prose=_IMPACT_PROSE),
        _section(16, "Composite score and star band",
                 body=_score_body(score, stars),
                 prose=_score_prose()),
        _section(18, "Journal-level metrics",
                 body=_journal_body(journals, journal_note),
                 prose=_journal_prose()),
    ]
    return sections


def _provenance_body(prov: dict[str, Any]) -> list[str]:
    query = prov["query"]
    counts = prov["counts"] or {}
    by_evidence = counts.get("by_evidence") or {}
    lines = [
        f"- esearch term: `{query.get('term', '')}`",
        f"- date range: {query.get('mindate', '?')} to {query.get('maxdate', '?')} "
        f"(years_back={query.get('years_back', '?')})",
        f"- retmax {query.get('retmax', '?')}; esearch matched {query.get('esearch_count', '?')}; "
        f"PMIDs returned {query.get('pmids_returned', '?')}; truncated: {query.get('truncated', False)}",
        f"- fetched {counts.get('fetched', '?')} / verified {counts.get('verified', '?')} / "
        f"name_only {counts.get('name_only', 0)} / rejected {counts.get('rejected', '?')}",
        "- verified by evidence tier: "
        + (", ".join(f"{tier} {value}" for tier, value in sorted(by_evidence.items())) or "not recorded"),
        f"- identity: orcid={prov['identity']['orcid'] or '(none)'}; "
        f"affiliation_keywords={len(prov['identity']['affiliation_keywords'])}; "
        f"email_domains={len(prov['identity']['email_domains'])}",
        f"- effective require_affiliation: {prov['identity']['require_affiliation_effective']}",
        f"- position_filtered: {prov['position_filtered']}",
        f"- identity fallback fired: {prov['fallback_fired']}",
        f"- window used: {prov['window_start_year']} to {prov['window_end_year']}",
        f"- records usable after exclusions: {prov['corpus_size']} "
        f"(plus {prov['records_only_size']} counted only in records-per-year)",
        "",
        "Record exclusions (Section 6.5):",
    ]
    for reason, pmids in prov["exclusions"].items():
        lines.append(f"- {reason}: {len(pmids)} — {_pmid_list(pmids)}")
    lines += [
        "",
        f"- title-identical records flagged but not merged: {len(prov['title_duplicates'])} "
        f"group(s) — {'; '.join(', '.join(g) for g in prov['title_duplicates']) or 'none'}",
        f"- consortium in the lead slot: {len(prov['slot0_collective_pmids'])} — "
        f"{_pmid_list(prov['slot0_collective_pmids'])}",
        f"- sole-author records (the author is forced into the senior slot by the last-author rule): "
        f"{len(prov['sole_author_papers'])} — {_pmid_list(prov['sole_author_papers'])}",
        f"- records where two byline entries matched the target name at the same evidence tier: "
        f"{len(prov['ambiguous_pi_papers'])} — {_pmid_list(prov['ambiguous_pi_papers'])}",
        f"- configured name exclusions: {_pmid_list(prov['exclude_names'])}",
        "",
        f"- people found: {prov['n_people']}; strict keying finds {prov['n_strict']}, "
        f"loose keying finds {prov['n_loose']}",
    ]
    return lines


def _roster_body(roster: dict[str, Any], gantt_path: str | Path | None) -> list[str]:
    lines = [
        f"{roster['denominator']} people, after removing the target researcher, consortium entries "
        f"and configured exclusions.",
        "",
        "| person | position label | affiliation signal | appearances | lead slots | "
        "equal-contribution flags | first | last | censoring | notes |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in roster["rows"]:
        censoring = ", ".join(
            label for label, flag in (("left", row["left_censored"]), ("right", row["right_censored"])) if flag
        ) or "none"
        lines.append(
            f"| {row['name']}{row['marker']} | {STRATUM_LABEL[row['stratum']]} | "
            f"{row['affiliation_signal']} | {row['n_appearances']} | {row['n_first_slots']} | "
            f"{row['n_equal_contrib']} | {row['first_year']} | {row['last_year']} | {censoring} | "
            f"{', '.join(row['flags']) or '-'} |"
        )
    lines += [
        "",
        "Rows are ordered by first appearance, then by name. They are never ordered by any count.",
        "",
        "By position label: " + ", ".join(
            f"{STRATUM_LABEL[key]} {value}" for key, value in roster["by_stratum"].items()
        ),
    ]
    if gantt_path:
        lines += ["", f"![Person activity timeline]({Path(gantt_path).name})",
                  f"Timeline rendered by analysis.render_gantt: `{gantt_path}`"]
    else:
        # Markdown cannot hold an inline SVG; an unexplained absence would be worse.
        lines += ["", "The activity timeline is drawn in the HTML report beside this file "
                  "(`advisor_profile_*.html`, Section 2), one row per person in the cohort "
                  "every aggregate below is computed over."]
    return lines


def _first_author_body(slots: dict[str, Any], partition: dict[str, Any]) -> list[str]:
    lines = ["**Paper side — who occupies the lead slot.**", ""]
    if slots["not_computable"]:
        lines.append("not computable: the PI is first author on every corpus paper")
    elif slots["suppressed"]:
        lines += [
            f"Only {slots['denominator']} eligible records, which is below the minimum for an "
            f"aggregate. The records are listed instead:",
            "",
            "| PMID | year | lead author | position label |",
            "|---|---|---|---|",
        ]
        for row in slots["rows"]:
            lines.append(
                f"| {row['pmid']} | {row['year']} | {row['lead_name']} | {STRATUM_LABEL[row['stratum']]} |"
            )
    else:
        for key, count in slots["counts"].items():
            pct = (slots["percentages"] or {}).get(key)
            suffix = f" ({pct}%)" if pct is not None else ""
            lines.append(f"- {STRATUM_LABEL[key]}: {count} of {slots['denominator']} records{suffix}")
    if not slots["not_computable"]:
        # Withheld when the eligible set is empty: the spec requires that case to
        # print its one sentence and no counts at all, including zeros.
        lines += [
            "",
            f"Eligible records exclude {len(slots['dropped_pi_is_lead'])} where the target researcher "
            f"holds the lead slot and {len(slots['dropped_slot0_collective'])} where a consortium does.",
        ]
    lines += [
        "",
        "**Person side — who has ever led a paper.**",
        "",
        f"- holds at least one lead slot: {partition['counts']['holds_lead']} of "
        f"{partition['denominator']}",
        f"- no lead slot, first seen at least {partition['lag_years']} years before the window end: "
        f"{partition['counts']['observed_without_lead']} of {partition['denominator']}",
        f"- no lead slot, first seen inside the trailing {partition['lag_years']} years, so too recent "
        f"to tell: {partition['counts']['too_recent']} of {partition['denominator']}",
        "",
        "This is a count partition, not a rate, and no proportion is computed from it at any sample size.",
    ]
    return lines


def _time_to_lead_body(result: dict[str, Any]) -> list[str]:
    if result["not_computable"]:
        lines = ["no person in this corpus holds a first-author slot"]
    else:
        lines = [f"Years from first appearance to first lead slot, over {result['denominator']} people.", ""]
        for lag, count in result["distribution"].items():
            lines.append(f"- {lag} year(s): {count} of {result['denominator']} people")
        lines.append(f"- at 0 years (debuted in the lead slot): {result['count_at_zero']} of {result['denominator']}")
        if result["suppressed"]:
            lines += ["", f"Below the minimum for a median at n={result['denominator']}; the values are:"]
            lines += [f"- {_fmt_person(item)}: {item['lag_years']} year(s)" for item in result["values"]]
        else:
            lines += ["", f"Median: {_fmt_number(result['median'])} year(s), over {result['denominator']} people."]
    lines += ["", "People with no lead slot yet, printed beside the figure above:"]
    if result["still_without_lead"]:
        lines += [
            f"- {_fmt_person(item)}: observed {item['years_observed']} year(s), no lead slot"
            for item in result["still_without_lead"]
        ]
    else:
        lines.append("- none")
    return lines


def _span_body(result: dict[str, Any], flips: Sequence[dict[str, Any]]) -> list[str]:
    lines = [
        f"Cohort: {result['cohort_denominator']} people. "
        f"Single-appearance people are counted separately ({result['single_appearance_count']}) and are "
        f"never given a span.",
        "",
        "Censoring: " + ", ".join(f"{key} {value}" for key, value in result["buckets"].items()),
        "",
    ]
    if result["suppressed"]:
        lines += [
            f"Only {result['denominator']} uncensored spans, below the minimum for a median. "
            f"The values are:",
        ]
    else:
        low, high = result["iqr"]
        lines += [
            f"Median span: {_fmt_number(result['median'])} year(s) over {result['denominator']} "
            f"uncensored people; IQR {_fmt_number(low)} to {_fmt_number(high)}.",
            "",
            "Per person:",
        ]
    for item in result["values"]:
        span = f"{item['span_years']}"
        if item["same_year"]:
            span = "0 (same year)"
        lines.append(
            f"- {_fmt_person(item)}: span {span} year(s) "
            f"[{item['first_year']}-{item['last_year']}, {item['bucket']}]"
        )
    lines += ["", "People who held a lead slot and later took the senior slot:"]
    if flips:
        lines += [
            f"- {_fmt_person(flip)}: lead slot from {flip['first_lead_year']}, "
            f"senior slot from {flip['first_last_year']}"
            for flip in flips
        ]
    else:
        lines.append("- none observed in this window")
    return lines


def _turnover_body(result: dict[str, Any]) -> list[str]:
    lines = [
        "| year | active | arrivals | latest appearance |",
        "|---|---|---|---|",
    ]
    for row in result["years"]:
        note = " (right-censored — not departures)" if row["departures_right_censored"] else ""
        lines.append(f"| {row['year']} | {row['active']} | {row['arrivals']} | {row['departures']}{note} |")
    lines += ["", f"Counts are over the {result['denominator']} people in the roster."]
    return lines


def _pi_position_body(result: dict[str, Any]) -> list[str]:
    if not result["measured"]:
        return ["Not measured on this corpus; see the caveat below."]
    coverage = result["email_coverage"]
    lines = []
    if result["suppressed"]:
        lines += [
            f"Only {result['denominator']} records, below the minimum for an aggregate. "
            f"Per record:",
            "",
            "| PMID | year | byline position |",
            "|---|---|---|",
        ]
        lines += [f"| {row['pmid']} | {row['year']} | {row['position']} |" for row in result["rows"]]
    else:
        for key, count in result["counts"].items():
            pct = (result["percentages"] or {}).get(key)
            suffix = f" ({pct}%)" if pct is not None else ""
            lines.append(f"- {key}: {count} of {result['denominator']} records{suffix}")
    lines += ["", "Heuristic, reported beside its own coverage:"]
    if result["corresponding"] is None:
        lines.append(
            f"- corresponding-author flag: suppressed. Email coverage is "
            f"{coverage['covered']} of {coverage['denominator']} records, so the flag is False for "
            f"everyone for reasons unrelated to this researcher."
        )
    else:
        lines.append(
            f"- the target researcher's own entry carries the corresponding-author flag on "
            f"{result['corresponding']['count']} of {result['corresponding']['denominator']} records; "
            f"email coverage is {coverage['covered']} of {coverage['denominator']} records"
        )
    return lines


def _equal_contrib_body(result: dict[str, Any]) -> list[str]:
    if result["not_measurable"]:
        return ["not measurable in this corpus"]
    lines = [
        f"{result['count']} of {result['denominator']} records carry the equal-contribution attribute.",
        "",
    ]
    for category, count in result["categories"].items():
        lines.append(f"- {category.replace('_', ' ')}: {count} of {result['count']} flagged records")
    lines += ["", "| PMID | year | flagged group size | includes lead slot | includes senior slot |", "|---|---|---|---|---|"]
    for row in result["papers"]:
        lines.append(
            f"| {row['pmid']} | {row['year']} | {row['group_size']} | "
            f"{row['includes_first']} | {row['includes_last']} |"
        )
    return lines


def _records_body(result: dict[str, Any]) -> list[str]:
    lines = [f"{result['denominator']} records in total.", ""]
    for row in result["years"]:
        notes = []
        if row["partial"]:
            notes.append("PARTIAL")
        if row["indexing_lag"]:
            notes.append("subject to PubMed indexing lag")
        suffix = f"  ({'; '.join(notes)})" if notes else ""
        lines.append(f"- {row['year']}: {row['count']}{suffix}")
    return lines


def _team_size_body(result: dict[str, Any]) -> list[str]:
    lines = []
    if result["suppressed"]:
        lines += [
            f"Only {result['denominator']} records, below the minimum for a median. Author counts: "
            f"{', '.join(str(v) for v in result['values'])}.",
        ]
    else:
        low, high = result["iqr"]
        lines += [
            f"Median {_fmt_number(result['median'])} authors per record over {result['denominator']} "
            f"records; IQR {_fmt_number(low)} to {_fmt_number(high)}; range {result['min']} to "
            f"{result['max']}.",
        ]
    lines.append(
        f"- records with 20 or more authors: {result['large_team_count']} of {result['denominator']} "
        f"— {_pmid_list(result['large_team_pmids'])}"
    )
    subset = result["subset"]
    if subset["suppressed"]:
        lines.append(
            f"- records led by a lead-trainee or support candidate: {subset['denominator']}, below the "
            f"minimum for a separate median"
        )
    else:
        lines.append(
            f"- records led by a lead-trainee or support candidate: median "
            f"{_fmt_number(subset['median'])} authors over {subset['denominator']} records"
        )
    return lines


def _venue_body(result: dict[str, Any]) -> list[str]:
    lines = [f"Journal strings over {result['denominator']} records, exactly as recorded.", ""]
    for name, count in result["repeated"]:
        lines.append(f"- {name}: {count} of {result['denominator']} records")
    if not result["repeated"]:
        lines.append("- no journal string appears more than once")
    lines.append(f"- {result['singleton_count']} journal string(s) appear once")
    if result["missing_journal_count"]:
        lines.append(f"- {result['missing_journal_count']} record(s) carry no journal string")
    return lines


def _affiliation_body(result: dict[str, Any]) -> list[str]:
    lines = [
        f"Affiliation strings appearing on at least {result['min_papers']} of "
        f"{result['denominator']} records, printed verbatim and ungrouped:",
        "",
    ]
    if result["strings"]:
        lines += [f"- `{text}` — {count} records" for text, count in result["strings"]]
    else:
        lines.append(f"- no affiliation string reaches {result['min_papers']} records")
    lines += ["", "Coverage per year (author entries carrying any affiliation string):", ""]
    for row in result["coverage_by_year"]:
        if row["total"] == 0 or row["covered"] == 0:
            lines.append(f"- {row['year']}: no affiliation data")
        else:
            lines.append(f"- {row['year']}: {row['covered']} of {row['total']} author entries")
    return lines


def _titles_body(result: dict[str, Any]) -> list[str]:
    lines = [f"All {result['denominator']} record titles, verbatim, by year.", ""]
    for group in result["years"]:
        lines.append(f"**{group['year']}**")
        lines += [f"- {record['title']} (PMID {record['pmid']})" for record in group["records"]]
        lines.append("")
    return lines


# --- Sections 15 and 16: citation impact and the composite score ---

# What `citations_note` says when a caller passed neither citations nor an
# explanation. Deliberately about the call, not about the researcher.
_NO_CITATIONS_NOTE = (
    "no citation payload was passed to build_report, and none was looked for. "
    "Run `check-your-advisor cite --output-dir <dir>` to fetch counts for this corpus."
)


def _fmt_score(value: Any, digits: int = 2) -> str:
    """A score-side number at a fixed precision, or `n/a`.

    Separate from `_fmt_number`, which drops everything after the first decimal:
    a normalised component printed as `0.4` cannot be multiplied back into its
    contribution by hand, and checkable arithmetic is the only thing that makes
    an unjustifiable weight table honest.
    """
    if value is None:
        return "n/a"
    return f"{float(value):.{digits}f}"


def _fmt_input(value: Any) -> str:
    """One raw input, rendered so a dict or a list stays readable inside a bullet."""
    if value is None:
        return "not recorded"
    if isinstance(value, Mapping):
        return "; ".join(f"{key} {item}" for key, item in value.items()) or "none"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value) or "none"
    if isinstance(value, float):
        return _fmt_score(value, 2)
    return str(value)


_IMPACT_PROSE: tuple[str, ...] = (
    "Citation counts and the h-index are printed here as absolute values, each beside the number "
    "of records it was computed over. No ordering is produced from them: no rank, no percentile, "
    "no quantile position, no grade, no tier, no comparison with any other researcher. A value is "
    "printed; a position is not.",
    "",
    "Three properties of these numbers that a reader has to carry with them:",
    "",
    "- **They are dated.** A citation count moves every week, so the fetch date is printed above "
    "and the counts live in their own file rather than inside the corpus. A figure quoted without "
    "its date is not a fact about anything.",
    "- **They are incomplete.** Coverage is whatever the citation sources matched. Where coverage "
    "is partial, the total, the h-index and the i10-index are floors — the missing records can "
    "only push them up. The median is not a floor: a missing record moves it in an unknown "
    "direction, which is why it is printed with its own denominator instead of as a summary of "
    "the corpus.",
    "- **This h-index is not a career h-index.** It is bounded by the papers inside this search "
    "window, and it grows with lab size. Two of them are not comparable unless the windows, the "
    "fields and the citation sources all match, and this report does not claim they do.",
    "",
    "Journal Impact Factor, JCR quartile and CAS partition are absent for a different reason and "
    "the difference matters: there is no free, redistributable source for any of those tables and "
    "this toolkit ships no licensed data, so the input cannot be obtained rather than being "
    "refused. Section 16 states both cases under their own headings.",
)


def _impact_body(impact: dict[str, Any] | None, note: str) -> list[str]:
    """Section 15. The citation numbers, or the reason there are none."""
    if impact is None:
        return [
            "Not computed: no citation data was joined to this corpus.",
            "",
            f"Reason: {note or _NO_CITATIONS_NOTE}",
            "",
            "This section is the only thing that is missing. Citation counts are fetched "
            "separately and are not part of the corpus, so their absence costs this section and "
            "the two citation components of Section 16, and changes nothing in Sections 1 to 13.",
        ]

    generated = impact.get("generated_at")
    when = f"fetched {generated}" if generated else "fetch date not recorded in the citation file"
    lines = [
        f"Citation counts joined onto the {impact['denominator']} usable records of this corpus "
        f"({when}).",
        "",
    ]

    if impact["not_computable"]:
        lines += [
            f"No citation count was retrieved for any of the {impact['denominator']} records. That "
            "is a statement about the lookup, not about the papers: nothing here says these "
            "records are uncited.",
            "",
        ]
    else:
        ledger = impact["record_counts"]
        lines += [
            f"- coverage: {impact['covered']} of {impact['denominator']} records carry a citation "
            f"count",
            f"- retrieved from: {_fmt_input(impact['sources'])}",
            f"- total citations over covered records: {impact['total_citations']}",
        ]
        if impact["suppressed"]:
            lines += [
                f"- below the minimum for an aggregate at {impact['covered']} covered records, so "
                f"the h-index, the i10-index and the median are withheld. The counts themselves, "
                f"ascending and deliberately not tied back to any record: "
                f"{', '.join(str(value) for value in impact['values']) or 'none'}",
            ]
        else:
            low, high = impact["iqr"]
            lines += [
                f"- h-index over covered records: {impact['h_index']}",
                f"- i10-index (covered records with at least 10 citations): {impact['i10_index']}",
                f"- median citations per covered record: {_fmt_number(impact['median_citations'])}; "
                f"IQR {_fmt_number(low)} to {_fmt_number(high)}",
            ]
        lines += [
            f"- fetched-record ledger: {ledger['total']} records in the citation file, "
            f"{ledger['matched']} matched onto this corpus, {ledger['unmatched']} matched nothing, "
            f"{ledger['duplicate']} duplicate, {ledger['invalid']} unreadable",
        ]
        if impact["lower_bound"]:
            lines += [
                "- partial coverage: the total, the h-index and the i10-index above are floors. "
                "The median is not a floor and must not be read as one.",
            ]
        if impact["mixed_sources"]:
            lines += [
                "- these counts come from more than one citation source. The three sources sit on "
                "three different citation graphs and do not agree with each other, so an aggregate "
                "mixing them is not comparable with one taken from any single source.",
            ]
        lines += [
            f"- records with no citation count retrieved: "
            f"{len(impact['uncovered_pmids'])} — {_pmid_list(impact['uncovered_pmids'])}",
        ]
    return lines


def _stars_line(stars: Mapping[str, Any] | None) -> list[str]:
    """
    Section 16's star band, or the reason there is none. Never a zero-star row.

    The glyphs and the number are printed together because the glyphs alone are
    an image and the number alone is not what anyone remembers, and both are
    printed with the band edges that produced them, because a band with no edges
    beside it is a verdict wearing a scale's clothes. `STAR_BASIS` — the argument
    for five equal bands — travels in the section's prose, where the rest of the
    fixed text lives.
    """
    if not stars:
        return []
    count = stars.get("stars")
    if count is None:
        return [
            f"- star band: none. {stars.get('unavailable') or 'no reason recorded'}",
            f"- a score that was not computed is never coarsened into 0 of {STAR_MAX} stars; "
            "there is no zero band on this scale and an absent score is not a low one",
        ]
    band = stars.get("band") or [0, 0]
    closed = " (closed at the upper edge)" if stars.get("band_closed_at_top") else ""
    denominator = stars.get("denominator")
    over = f" over {denominator} scored component(s)" if denominator else ""
    return [
        f"- star band: {'★' * int(count)}{'☆' * (STAR_MAX - int(count))} — {int(count)} of "
        f"{STAR_MAX}, from a score of {_fmt_score(stars.get('score'), 1)} out of 100{over}",
        f"- the band this score fell in: {band[0]:g} to {band[1]:g} points{closed}, one of "
        f"{STAR_MAX} equal bands {stars.get('band_width', 0):g} points wide",
        f"- every band edge, printed so it can be disagreed with: {stars.get('scale_note', '')}",
    ]


def _score_body(score: dict[str, Any] | None, stars: dict[str, Any] | None = None) -> list[str]:
    """Section 16. The score, its star band, its inputs, and the weight table."""
    if score is None:
        return ["Not computed: no score was supplied to the renderer."]

    if score["suppressed"]:
        lines = [
            f"No score. Only {score['denominator']} of {score['components_registered']} registered "
            f"components carried both data and a non-zero weight, below the minimum of "
            f"{score['min_components']}; total weight in effect is "
            f"{_fmt_score(score['weight_total'])}. Below that floor a \"composite\" is one or two "
            f"metrics with a change of scale, and printing it out of 100 would imply more evidence "
            f"than exists. The parts survive below; the aggregate does not, and is not defaulted "
            f"to zero.",
        ]
    else:
        lines = [
            f"Score: {_fmt_score(score['score'], 1)} out of 100, computed over "
            f"{score['denominator']} of {score['components_registered']} registered components, "
            f"total weight {_fmt_score(score['weight_total'])}.",
        ]

    star_lines = _stars_line(stars)
    if star_lines:
        lines += ["", *star_lines]

    lines += [
        "",
        "| component | source | raw | normalised | weight | contribution |",
        "|---|---|---|---|---|---|",
    ]
    for item in score["components"]:
        lines.append(
            f"| {item['name']} | {item['source']} | {_fmt_score(item['raw'], 3)} | "
            f"{_fmt_score(item['normalised'], 3)} | {_fmt_score(item['weight'])} | "
            f"{_fmt_score(item['contribution'])} |"
        )
    if not score["components"]:
        lines.append("| (none carried data) | - | n/a | n/a | n/a | n/a |")
    lines += [
        "",
        "Components are listed in registration order and are never reordered by contribution, "
        "weight or value. Contributions are `100 x weight x normalised / total weight` and sum to "
        "the score before rounding, so the arithmetic can be redone by hand without rerunning "
        "anything.",
        "",
        "Inputs behind each component, as consumed:",
        "",
    ]
    for item in score["components"]:
        inputs = ", ".join(
            f"{key}={_fmt_input(value)}" for key, value in (item["raw_inputs"] or {}).items()
        )
        lines.append(f"- **{item['name']}** — {inputs or 'no raw inputs recorded'}")
        lines.append(f"- normalisation basis: {item['basis']}")
    if not score["components"]:
        lines.append("- none")

    lines += [
        "",
        "Weight table in effect, printed verbatim including the components that carried no data. "
        "This is the whole of what the score assumes:",
        "",
        "| component | weight |",
        "|---|---|",
    ]
    for name, weight in score["weights_used"].items():
        lines.append(f"| {name} | {_fmt_score(weight)} |")
    lines += [
        "",
        "Edit it under `scoring.weights` in the config file. Only the ratios matter, so scaling "
        "the whole table changes nothing; setting a component to 0.0 drops it from the score.",
        "",
        "Components with no usable data. These are excluded from the weighted denominator "
        "entirely and are never scored as zero — a lookup that returned nothing is not a result of "
        "nothing:",
        "",
    ]
    if score["unavailable"]:
        lines += [
            f"- **{name}** — {score['unavailable_reasons'].get(name, 'no reason recorded')}"
            for name in score["unavailable"]
        ]
    else:
        lines.append("- none; every registered component carried data")
    return lines


def _score_prose() -> list[str]:
    """
    Section 16's fixed text, including every exclusion register that bears on it.

    The registers are printed under separate headings and are never merged. "We
    cannot get the data", "we will not do this" and "this function cannot see
    enough to do it" are three different sentences, and a reader who cannot tell
    them apart cannot tell which one would change if a licence were bought
    tomorrow.

    Two registers are printed because two modules are involved and they say
    different things on purpose. `scoring.SCORING_EXCLUSIONS` is
    `composite_score`'s own refusal and is still exactly true of that function:
    it is handed one corpus, so it can produce no position of any kind. The star
    band at the head of this section is not produced there — it is produced by
    `profile.ranking` from the number `composite_score` returned, and
    `ranking.RANKING_EXCLUSIONS` is the register that governs it. Printing only
    the first would tell the reader stars are refused while a star band sits
    four lines above; printing only the second would delete a live constraint on
    the scoring function. So both are printed, in that order, with the sentence
    that says how they fit together.
    """
    lines = [
        "A score is one number about one corpus under one weight table. It is not a position. No "
        "weighting of these components is justified by this data, and this report does not claim "
        "to have found one — it answers that objection by refusing to hide the weights instead. "
        "The default table is flat, every component counting the same, because a flat table is "
        "the only default that asserts nothing; a reader who leaves it alone has chosen \"count "
        "everything equally\", which is a position they can defend.",
        "",
        "Every normalisation anchor is a declared constant printed in the basis line of the "
        "component that uses it. Nothing is normalised against a population of researchers, so a "
        "percentile is not merely withheld here, it is uncomputable: the calculation never holds "
        "more than one corpus. Two of these numbers side by side are still not an ordering until "
        "a reader supplies the judgement, which is where that judgement belongs.",
        "",
        "The score is also not comparable across fields. Citation rates differ between fields by "
        "an order of magnitude and no field normalisation is applied, for the reason given in the "
        "first register below.",
        "",
        f"**The star band.** {STAR_BASIS}",
        "",
        "A star count adds no information to the score above it and subtracts a good deal: it is "
        "the same number with most of its resolution thrown away, printed because it is asked "
        "for and printed beside its own band edges so it can be checked against the score. It is "
        "not a position among researchers, it was not calibrated against any group of them, and "
        "two corpora carrying the same star count are two scores that landed in one 20-point "
        "band — which is a fact about the band, not a finding about the two.",
        "",
        "**Not implemented, because the input cannot be obtained.** These would be reopened on "
        "their merits if the data became available. Do not read them as verdicts.",
        "",
    ]
    lines += [f"- **{name}** — {reason}" for name, reason in SCORING_EXCLUSIONS["not_implemented"]]
    lines += [
        "",
        "One of those has moved since it was written, and only part way. Journal Impact Factor, "
        "JCR quartile and CAS partition are still not shipped with this toolkit and are still "
        "never fetched by it — there is no crawler in this package. Section 18 joins them from a "
        "table you fill in by hand, prints the edition and the retrieval date beside every "
        "number, and prints \"未提供对照表\" when there is no table. Nothing about that changes "
        "what the register says: the toolkit supplies no such data, and none of it enters the "
        "score above.",
        "",
        "**Refused by `composite_score` itself.** A better data source would not change these "
        "answers. This register belongs to the scoring function, which is handed one corpus and "
        "can therefore produce no position of any kind — that is a fact about its signature. The "
        "star band above is produced elsewhere, by `profile.ranking`, out of the number this "
        "function returned; the register immediately after this one is the one that governs it.",
        "",
    ]
    lines += [f"- **{name}** — {reason}" for name, reason in SCORING_EXCLUSIONS["refused_by_design"]]
    lines += [
        "",
        "**Refused by `profile.ranking`, which is where ordering is now done.** This is the "
        "current register for anything that turns a score into a position. Stars are produced "
        "and letter tiers are not; a rank among the corpora on a side-by-side page is produced "
        "and a percentile is not. Both splits are deliberate and both are argued here rather "
        "than left to be discovered.",
        "",
    ]
    lines += [f"- **{name}** — {reason}" for name, reason in RANKING_EXCLUSIONS["refused_by_design"]]
    lines += [
        "",
        "**Not computable here, for want of a reference population.** A different sentence from "
        "the one above: no decision is being defended, an input simply does not exist.",
        "",
    ]
    lines += [f"- **{name}** — {reason}" for name, reason in RANKING_EXCLUSIONS["not_computable_here"]]
    return lines


# --- Sections 17 and 18: the two hand-supplied tables ---
#
# Same division of labour in both, and it is the whole design: this package
# defines the schema, says which rows a corpus actually needs looked up, joins a
# filled-in file, and prints where every number came from. It fetches nothing.
# The tables behind both sections are licensed products or subscription
# libraries; scraping them is neither permitted nor reliable, so the lookup is
# the reader's manual step and the file is theirs.

_NO_THESIS_ROSTER_NOTE = (
    "no degree-thesis roster was passed to build_report, and none was looked for. Pass one with "
    "`check-your-advisor profile --thesis-roster <path.csv>`."
)

_NO_JOURNAL_TABLE_NOTE = (
    "no journal metric table was passed to build_report, and none was looked for. Pass one with "
    "`check-your-advisor profile --journal-table <path.csv>`."
)


def _cohesion_body(data: dict[str, Any]) -> list[str]:
    """Section 19's computed half: the partition, and nothing derived from it."""
    if data.get("suppressed"):
        return [
            f"- not partitioned: {data['denominator']} records, below the floor of {data['min_n']}",
            "- below that floor the partition says nothing. Four records sharing no co-author are "
            "four clusters whether or not they are four people.",
        ]

    clusters = data["clusters"]
    lines = [
        f"- records partitioned: {data['denominator']}",
        f"- clusters joined by a shared co-author: {data['n_clusters']}",
        f"- largest cluster: {data['largest_size']} of {data['denominator']} records",
        f"- clusters holding a single record: {data['singleton_clusters']}",
        "",
        "Clusters are listed largest first so the page is readable. That order is not a ranking "
        "and carries no claim that the largest one is the real person.",
        "",
    ]
    for number, cluster in enumerate(clusters, start=1):
        if not cluster["detailed"]:
            continue
        span = ""
        if cluster["year_range"]:
            low, high = cluster["year_range"]
            span = f", {low}" if low == high else f", {low}–{high}"
        lines.append(f"- cluster {number}: {cluster['size']} records{span}")
        for venue in cluster["journals"][:6]:
            suffix = f" x{venue['count']}" if venue["count"] > 1 else ""
            lines.append(f"    - {venue['journal']}{suffix}")
        extra = len(cluster["journals"]) - 6
        if extra > 0:
            lines.append(f"    - ...and {extra} further journal(s)")
        recurring = cluster["recurring_people"][:4]
        if recurring:
            joined = ", ".join(f"{p['name']} ({p['n_records']})" for p in recurring)
            lines.append(f"    - held together by: {joined}")
    singletons = [c for c in clusters if not c["detailed"]]
    if singletons:
        venues = [c["journals"][0]["journal"] for c in singletons if c["journals"]]
        lines.append(
            f"- {len(singletons)} single-record cluster(s), in: "
            + (", ".join(venues[:8]) or "no journal recorded")
            + ("..." if len(venues) > 8 else "")
        )
    return lines


def _cohesion_prose() -> list[str]:
    """Section 19's fixed text: what the partition is, and what it is not."""
    return [
        "Remove the PI, who is on every record by construction, and ask which records are still "
        "tied together by a shared co-author. One person's output is tied together by the people "
        "they work with. Two people who share a name have no reason to share anyone else.",
        "",
        "**This section does not decide anything, and it is not a gate.** No threshold is applied, "
        "because the measurements do not support one. Counted with this exact function, a corpus "
        "known to hold at least five different researchers split into 15 clusters with the largest "
        "holding 21% of records; two better-filtered corpora of the same kind split into 16 and 10 "
        "clusters holding 35% and 42%. The cluster count separates none of them. Any real "
        "researcher accumulates one-off collaborators and each becomes a single-record cluster, so "
        "the count tracks how many one-off papers there are, not how many people are in the "
        "corpus. A cut-off guessed from numbers like those would refuse real broad-ranging "
        "researchers, which is a worse failure than the one it would prevent.",
        "",
        "What separates \"one person working across fields\" from \"several people sharing a name\" "
        "is whether the clusters' subject matter is related, and this toolkit classifies no "
        "subjects — see Section 14 for why MeSH does not rescue that. So the clusters and their "
        "journals are printed, and the reading is yours. It is usually not subtle: clusters in "
        "oncology, in analytical chemistry, in soil microbiology and in machine learning are not "
        "one surgeon's decade.",
        "",
        "If the clusters look like different people, nothing above this line is worth quoting. "
        "Re-harvest with `--orcid`, which is the only evidence that settles it, and read the "
        "report again. Gate G3 refuses a corpus harvested with no identity evidence at all; this "
        "section exists because a corpus harvested with weak evidence passes that gate and can "
        "still describe several people.",
        "",
        f"Clusters holding fewer than {CLUSTER_DETAIL_MIN} records are summarised rather than "
        "listed in full: a page with thirty one-record clusters printed out buries the ones worth "
        "reading. Their journals are still named.",
    ]


def _graduates_prose() -> list[str]:
    """Section 17's fixed text: the caveat, the three populations, the limits, the rules."""
    lines = [
        "This section is printed second and numbered 17. Both are deliberate. It is printed here "
        "because it repairs the limit Section 0 states — every count in Sections 1 to 16 is taken "
        "over people who appear on an indexed paper, so a graduate who published nothing is "
        "missing from every numerator and every denominator above. It is numbered 17 because it "
        "was added after those sections and four other modules cross-reference them by number; "
        "renumbering to put it second would falsify all of those at once.",
        "",
        THESIS_DENOMINATOR_CAVEAT,
        "",
        "Three nested populations. Read all three rows: a reader who sees only the middle one "
        "will take it for the whole group, which is the specific misreading this section is most "
        "likely to cause.",
        "",
        "| population | source | what it is |",
        "|---|---|---|",
    ]
    lines += [f"| {name} | {source} | {note} |" for name, source, note in DENOMINATOR_LADDER]
    lines += [
        "",
        "What a thesis export misses, separately from the people who left before finishing. Each "
        "of these bends a count below in a stated direction:",
        "",
    ]
    lines += [f"- **{name}** — {note}" for name, note in ROSTER_LIMITS]
    lines += [
        "",
        "How each name pairing was treated. Nothing uncertain is pushed into either bucket, which "
        "is why the count of graduates with no paper is reported as a floor and a ceiling rather "
        "than as one number:",
        "",
    ]
    lines += [f"- **{level}** — {treatment}. {why}" for level, treatment, why in MATCH_RULES]
    return lines


def _graduates_body(graduates: dict[str, Any] | None, note: str) -> list[str]:
    """Section 17. The real denominator, or the reason there is none."""
    if graduates is None:
        return [
            "Not computed: no degree-thesis roster was joined to this corpus.",
            "",
            f"Reason: {note or _NO_THESIS_ROSTER_NOTE}",
            "",
            "What that costs is the one thing this report cannot work around on its own. PubMed "
            "holds people who published. Someone who took a degree in this group and never "
            "appeared on an indexed paper is in none of the counts above — not in a numerator, "
            "not in a denominator, not in the roster, not in any figure. Nothing in Sections 1 "
            "to 16 is evidence about how many such people there are, in either direction.",
            "",
            "To supply one: search a degree-thesis library (CNKI, 万方) for this advisor as "
            "supervisor, export the hit list, and save it as UTF-8 CSV carrying at least the "
            "columns 导师姓名, 学生姓名, 学位类型, 毕业年, 库来源, 导出日期. Then re-run with "
            "`--thesis-roster <path.csv>`.",
            "- 学生姓名拼音 (a romanised name for each graduate) is optional in the schema and "
            "decisive in practice: without it a roster written in Chinese characters cannot be "
            "joined to romanised PubMed bylines at all, and every graduate comes back undecided "
            "rather than matched or unmatched.",
            "- 库来源 and 导出日期 are required for the same reason the journal table needs an "
            "edition column: theses reach a library months after the defence, so a count with no "
            "date on it cannot be compared with a later one.",
        ]

    counts = graduates["counts"]
    floor, ceiling = graduates["without_pubmed_bounds"]
    provenance = graduates["provenance"]
    lines = [
        f"{counts['graduates_total']} distinct people took a degree under this advisor according "
        f"to the export. Not thesis rows: someone who took a master's and then a doctorate here "
        f"is one graduate.",
        "",
        "| population | count | of |",
        "|---|---|---|",
        f"| graduates on record | {counts['graduates_total']} | {counts['graduates_total']} |",
        f"| ... who also appear in the PubMed corpus | {counts['with_pubmed_record']} | "
        f"{counts['graduates_total']} |",
        f"| ... who appear in no PubMed paper in the window | {counts['without_pubmed_record']} | "
        f"{counts['graduates_total']} |",
        f"| ... undecided against the PubMed roster | {counts['needs_manual_review']} | "
        f"{counts['graduates_total']} |",
        f"| in the PubMed corpus but not on the graduation list | {counts['pubmed_only']} | "
        f"{counts['pubmed_roster_size']} |",
        "",
        f"- graduates with no PubMed paper, as a floor and a ceiling: {floor} to {ceiling} of "
        f"{counts['graduates_total']}. Every undecided row could fall either way, so one number "
        f"here would hide how far apart the two ends are.",
    ]

    # Three cases, and they are not two. `suppressed` covers a refused
    # attribution or too many undecided rows; a share can still be absent when
    # nothing was suppressed at all, because `metrics.percent` refuses any
    # percentage below n=20 and a supervisor's graduate list is almost always
    # smaller than that. Collapsing the second case into the third prints
    # "None%", which is what the first run of this section actually did.
    share = graduates["without_pubmed_share_percent"]
    if graduates["suppressed"]:
        lines.append("- no share is computed, and the reasons are not rounded off:")
        lines += [f"  - {reason}" for reason in graduates["suppressed_reasons"]]
    elif share is None:
        lines.append(
            f"- no share is computed: a percentage needs at least {M.MIN_N_PERCENT} in the "
            f"denominator and this one is {counts['graduates_total']}. The counts and the names "
            f"stand on their own, and at this size a name is more checkable than a percentage. "
            f"{graduates['share_basis']}"
        )
    else:
        lines.append(
            f"- share of graduates on record with no PubMed paper: {share}% of "
            f"{counts['graduates_total']}. {graduates['share_basis']}"
        )
    lines += [
        f"- undecided share: {graduates['unresolved_share']:.0%} of graduates, against a declared "
        f"ceiling of {graduates['max_unresolved_share']:.0%} above which the aggregate is "
        f"withheld entirely",
        f"- rows attributed to this PI: {graduates['rows_for_pi']} "
        f"(attribution: {graduates['advisor_filter']})",
    ]
    if graduates["advisor_note"]:
        lines.append(f"- attribution note: {graduates['advisor_note']}")

    lines += [
        "",
        "Where the graduate list came from. Without these a count in this section is a number "
        "with no library and no day attached:",
        "",
        f"- file: `{provenance.get('path') or 'not recorded'}` (read as "
        f"{provenance.get('encoding') or 'unknown encoding'})",
        f"- libraries: {_fmt_input(provenance.get('source_dbs')) or 'not recorded'}",
        f"- export dates: {_fmt_input(provenance.get('export_dates')) or 'not recorded'}",
        f"- awarding institutions: {_fmt_input(provenance.get('institutions')) or 'not recorded'}",
        f"- rows read {provenance.get('rows_read', '?')}; rejected {provenance.get('rejected', 0)}; "
        f"exact duplicates dropped {provenance.get('duplicates_dropped', 0)}",
    ]

    lines += ["", "**Graduates on record with no PubMed paper in this window.** Named, because at "
              "these counts a name is checkable and a number is not:", ""]
    if graduates["without_pubmed_record"]:
        lines += [
            f"- {row['student']}"
            + (f" ({row['student_latin']})" if row["student_latin"] else "")
            + f" — {', '.join(d for d in row['degrees'] if d) or 'degree not recognised'}, "
            f"{row['graduation_year']}"
            for row in graduates["without_pubmed_record"]
        ]
    else:
        lines.append("- none")

    lines += ["", "**Undecided, for a human to settle.** These are neither counted as published "
              "nor counted as unpublished:", ""]
    if graduates["needs_manual_review"]:
        lines += [
            f"- {row['student']} ({row['graduation_year']}) — {row['reason']}; candidates: "
            f"{', '.join(row.get('candidates') or []) or 'none'}"
            for row in graduates["needs_manual_review"]
        ]
    else:
        lines.append("- none")

    lines += [
        "",
        f"**In the PubMed corpus and not on the graduation list: {counts['pubmed_only']} of "
        f"{counts['pubmed_roster_size']}.** This is not a list of outsiders and must not be read "
        "as one — postdocs, technicians, research assistants, visiting trainees, clinical fellows "
        "and undergraduates never deposit a thesis and land here as a matter of course.",
    ]
    return lines


def _journal_prose() -> list[str]:
    """Section 18's fixed text: where the numbers come from and what they cannot mean."""
    lines = [
        "Nothing in this section was fetched. Impact factor, JCR quartile and CAS partition live "
        "in subscription databases that forbid scraping and defend against it, so this package "
        "ships no crawler and makes no request for any of them. It defines the table's columns, "
        "says which journals this corpus actually uses, joins the file you filled in, and prints "
        "the edition and retrieval date beside every number it prints.",
        "",
        "The scope is set by the corpus, not by the vendor. There are tens of thousands of "
        "indexed journals and one five-year corpus uses a couple of dozen; "
        "`check-your-advisor journal-worklist` writes exactly those, with their ISSNs and the "
        "number of papers each holds, in the table's own format. Fill the metric columns in and "
        "it loads straight back. Journals looked up once stay in the file and are reused by the "
        "next corpus.",
        "",
        "The edition column is not bureaucracy. The sources disagree, and which one a number "
        "came from decides what it means: " + " / ".join(EDITIONS) + ". A partition with no "
        "edition beside it cannot be checked by anyone later, including the person who wrote it "
        "down.",
        "",
    ]
    lines += [f"- **{key}** — {text}" for key, text in JOURNAL_CAVEATS.items()]
    return lines


def _journal_body(journals: dict[str, Any] | None, note: str) -> list[str]:
    """Section 18. The joined journal metrics, or a stated absence — never a blank cell."""
    if journals is None:
        return ["Not computed: no journal join was supplied to the renderer."]

    if journals["table_missing"]:
        return [
            "未提供对照表 — no journal metric table was joined to this corpus, so every "
            "journal-level column in this report is empty for a stated reason rather than "
            "because the journals have no metrics.",
            "",
            f"Reason: {note or _NO_JOURNAL_TABLE_NOTE}",
            "",
            f"- distinct journal strings in this corpus: {journals['journal_denominator']}",
            f"- records carrying a journal string: {journals['papers_with_journal']} of "
            f"{journals['denominator']}",
            f"- records carrying none: {journals['papers_without_journal']} of "
            f"{journals['denominator']}",
            "",
            "That first number is the whole size of the job: nobody is looking up twenty thousand "
            "journals, and nobody has to. Run `check-your-advisor journal-worklist --output-dir "
            "<corpus dir>` to write those journals, their ISSNs and their paper counts into a CSV "
            "in this table's own format, look them up on LetPub or ablesci, fill in the metric "
            "columns, and re-run with `--journal-table <path.csv>`.",
            "- An empty cell here is a lookup nobody has done yet. It is not a low impact factor, "
            "not an absent partition and not a statement about any journal.",
            "- LetPub's search-results list shows the 民间版 partition by default. If you copy "
            "from that page, write 民间版 in 版本来源 — do not file it as the official number.",
        ]

    provenance = journals["provenance"]
    match_counts = journals["match_counts"]
    lines = [
        f"Journal metrics joined from a table you supplied. Coverage is reported against both "
        f"denominators, because a table can cover most papers while missing most journals and the "
        f"two facts point at different work.",
        "",
        f"- records matched to the table: {journals['matched_papers']} of "
        f"{journals['papers_with_journal']} carrying a journal string "
        f"({journals['denominator']} records in the corpus)",
        f"- distinct journals matched: {journals['matched_journals']} of "
        f"{journals['journal_denominator']}",
        f"- by route: ISSN {match_counts.get(MATCH_ISSN, 0)}, exact name "
        f"{match_counts.get(MATCH_EXACT, 0)}, official abbreviation "
        f"{match_counts.get(MATCH_ABBREV_OFFICIAL, 0)}, abbreviation guess "
        f"{match_counts.get(MATCH_ABBREV, 0)} "
        f"— the first three are facts about what the journal is called, the last is an inference "
        f"from how a name is spelled, so they are listed apart and never added together into one "
        f"coverage figure. The official abbreviation is NLM's own, carried on the record; it "
        f"matters because a table typed from a source page holds the abbreviation about as often "
        f"as the full title, and the corpus holds the other one",
        "",
        "Where the table came from. This is the part that is unfalsifiable two years from now if "
        "it is not written down:",
        "",
        f"- file: `{provenance.get('table_path') or 'not recorded'}` (read as "
        f"{provenance.get('encoding') or 'unknown encoding'})",
        f"- rows {provenance.get('table_rows', 0)} covering {provenance.get('table_journals', 0)} "
        f"journals",
        f"- 版本来源 present in the file: {_fmt_input(provenance.get('editions')) or 'none'}",
        f"- rows with no edition recorded: {provenance.get('rows_without_edition', 0)}",
        f"- IF 年份 present: {_fmt_input(provenance.get('if_years')) or 'none'}",
        f"- 数据获取日期 span: "
        f"{' to '.join(provenance.get('retrieved_on_range') or []) or 'not recorded'}",
        "",
        "One row per journal per edition. Where a journal was checked against two editions both "
        "rows are here and neither wins:",
        "",
        "| journal (as PubMed records it) | papers | match | 版本来源 | 数据获取日期 | 影响因子 "
        "(年份) | JCR | 中科院大类 | 中科院小类 | 预警 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for result in journals["journals"]:
        if not result["entries"]:
            # "本表未收录", not "未提供对照表": a table was supplied and this
            # journal is not in it, which is a lookup nobody has done yet. The
            # two cells look alike and mean opposite things about whose move it
            # is next, so they are never spelled the same way.
            lines.append(
                f"| {result['journal']} | {result['paper_count']} | {result['match_type']} | "
                f"本表未收录 | - | - | - | - | - | - |"
            )
            continue
        for row in result["entries"]:
            warning = "是" if row["is_warning"] else ("否" if row["is_warning"] is False else "未标注")
            if row["warning_level"]:
                warning += f" ({row['warning_level']})"
            impact = row["impact_factor_raw"] or "-"
            if row["if_year"]:
                impact += f" ({row['if_year']})"
            lines.append(
                f"| {result['journal']} | {result['paper_count']} | {result['match_type']} | "
                f"{row['source_edition']} | {row['retrieved_on'] or '未记录'} | {impact} | "
                f"{row['jcr_quartile'] or '-'} | {row['cas_major'] or '-'} | "
                f"{row['cas_minor'] or '-'} | {warning} |"
            )
    if not journals["journals"]:
        # A header with no rows under it reads as a rendering failure. This
        # happens when no record in the corpus carries a journal string at all,
        # which is a fact about the corpus and is worth one row saying so.
        lines.append(
            f"| (no record in this corpus carries a journal string) | "
            f"{journals['papers_without_journal']} | - | - | - | - | - | - | - | - |"
        )

    lines += [
        "",
        f"- journals in this corpus that the table does not contain: "
        f"{len(journals['unmatched_journals'])} — "
        f"{', '.join(journals['unmatched_journals']) or 'none'}",
        f"- journal names whose abbreviation fits more than one table entry, left unmatched "
        f"rather than resolved: {len(journals['ambiguous_journals'])} — "
        + (", ".join(
            f"{item['journal']} (could be {', '.join(item['candidates'])})"
            for item in journals["ambiguous_journals"]
        ) or "none"),
    ]

    lines += ["", f"**Editions that disagree: {journals['disagreement_count']}.** Shown, not "
              "resolved — this package has no basis for preferring one edition over another:", ""]
    if journals["disagreeing_journals"]:
        lines += [
            f"- {item['journal']} ({', '.join(item['editions'])}): "
            + "; ".join(f"{field} = {' vs '.join(str(v) for v in values)}"
                        for field, values in item["fields"].items())
            for item in journals["disagreeing_journals"]
        ]
    else:
        lines.append("- none")

    lines += ["", "**On a 预警 (warning) list, per the table as filled in:**", ""]
    if journals["warned_journals"]:
        lines += [
            f"- {item['journal']} — {item['paper_count']} record(s); level "
            f"{', '.join(item['levels']) or 'not recorded'}; edition "
            f"{', '.join(item['editions'])}"
            for item in journals["warned_journals"]
        ]
    else:
        lines.append("- none in this table; note that a blank 是否预警 cell is not a clean bill of "
                     "health, it is a cell nobody filled in")
    return lines


# --- Output ---


def render_markdown(report: dict[str, Any]) -> str:
    """
    Markdown rendering.

    Sections are emitted in list order, not in numeric order: Section 17 is
    printed second, immediately after Section 0, because it repairs the limit
    Section 0 states. `_build_sections` explains the number it kept.

    Section 16 emits one composite score out of 100 beside every input, weight
    and contribution that produced it, and the star band that score falls in
    beside the band edges that decided it. It emits no percentile, no quantile
    position and no letter grade, here or anywhere else, and it places no person
    above any other person: the roster is still never ordered by a count.
    """
    title = f"# Observed publication pattern — {report['author_name'] or '(unnamed researcher)'}"
    if report["refused"]:
        gate = report["gate"]
        observed = "\n".join(f"- {key}: {value}" for key, value in gate["observed"].items())
        return "\n".join([
            title,
            "",
            f"## Report refused — gate {gate['id']} ({gate['name']})",
            "",
            gate["message"],
            "",
            "Observed:",
            observed or "- (none)",
            "",
        ])

    parts = [title, "", f"_Generated {report['generated_at']}._", ""]
    for section in report["sections"]:
        parts += [f"## {section['id']}. {section['title']}", ""]
        parts += section["prose"] + ([""] if section["prose"] else [])
        parts += section["body"] + ([""] if section["body"] else [])
        for text in section["caveats"]:
            parts += [f"> {text}", ""]
    return "\n".join(parts).rstrip() + "\n"


def json_record(report: dict[str, Any]) -> dict[str, Any]:
    """
    The machine-readable half: the same numbers, without the rendered prose.

    `sections` is dropped because it is a view of `metrics`; keeping both would
    let the two drift apart with no way to tell which one is authoritative.
    """
    return {key: value for key, value in report.items() if key != "sections"}


def write_report(report: dict[str, Any], output_dir: str | Path) -> dict[str, str]:
    """Write the Markdown and the JSON side by side. Returns both paths."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(report["generated_at"]).strftime("%Y%m%d_%H%M%S")
    markdown_path = directory / f"advisor_profile_{stamp}.md"
    json_path = directory / f"advisor_profile_{stamp}.json"
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    json_path.write_text(
        json.dumps(json_record(report), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"markdown": str(markdown_path), "json": str(json_path)}


# --- Side by side ---
#
# Several corpora on one page. Round one fixed the page order by label and
# refused to sort by anything on the page, so that the first column could not be
# read as the winner. That refusal has been lifted deliberately: this page now
# orders by score, numbers the positions, prints a star band per corpus, and
# will say in a sentence which of two corpora scored higher.
#
# What did not move, and the reason it did not: there is still no percentile and
# no quantile position, because a position inside a reference population needs a
# reference population and the corpora here are the few somebody chose to load.
# There is still no letter tier — stars are produced and letters are not, and
# that is a decision rather than an oversight. There is still no fitted trend
# and no slope. `ranking.RANKING_EXCLUSIONS` is the register and this page
# prints it.
#
# The one thing every rank on this page carries is the count it was taken over.
# "First" means something very different at N=2 and at N=9, and a rank column
# without its denominator beside it is the exact number this section spent a
# round refusing to print.

ORDER_BY_SCORE = "score"
ORDER_BY_LABEL = "label"

COMPARISON_ORDER = (
    "corpus label, lexicographic (case-folded), then the source directory. "
    "Never any value on this page."
)

COMPARISON_ORDER_BY_SCORE = (
    "composite score, highest first, with ties decided on the score as printed and broken for "
    "display only by label. Corpora with no score — a refused report, a suppressed score — keep "
    "their row and follow the ranked ones in label order; they are not placed last on merit, "
    "because last is a position and they hold none."
)


def _comparison_sort_key(entry: Mapping[str, Any]) -> tuple[str, str, str]:
    label = str(entry.get("label") or "")
    return (label.casefold(), label, str(entry.get("source") or ""))


def _scored_value(entry: Mapping[str, Any]) -> float | None:
    """The corpus's score as a reader sees it, or None when it has none.

    A suppressed score is None here and not a low number: `composite_score`
    withheld it because too few components carried data, and sorting it as 0.0
    would put a corpus with thin citation coverage below a corpus that genuinely
    scored badly.
    """
    report = entry.get("report") or {}
    if report.get("refused"):
        return None
    score = report.get("score") or {}
    value = score.get("score")
    if score.get("suppressed") or value is None:
        return None
    return float(value)


def _comparison_score_key(entry: Mapping[str, Any]) -> tuple[int, float, str, str]:
    value = _scored_value(entry)
    label = str(entry.get("label") or "")
    # Unscored corpora sort after scored ones and among themselves by label. The
    # negated score puts the highest first; the label is a display tiebreak and
    # carries no meaning, which is what RANK_TIE_NOTE says out loud.
    return (0 if value is not None else 1, -(value or 0.0), label.casefold(), label)


def build_comparison(
    entries: Sequence[Mapping[str, Any]],
    weights: Mapping[str, float] | None = None,
    now: datetime | None = None,
    order_by: str = ORDER_BY_SCORE,
) -> dict[str, Any]:
    """
    Lay N built reports side by side under one weight table, ranked.

    `entries` is a sequence of `{"label", "source", "report"}` mappings; `source`
    is the directory each corpus came from. Each report is one that
    `build_report` already produced, so every gate that would have refused a
    report on its own refuses it here too — a refused corpus keeps its row and
    shows its gate instead of numbers, because dropping it silently would leave
    the reader comparing three corpora believing they were comparing four.

    `order_by` is `"score"` (default) or `"label"`. Under `"score"` the columns
    run highest first and each carries its position; under `"label"` they run in
    the fixed lexicographic order round one used, and the ranks are still
    computed and still printed, because a rank is a property of the set and not
    of the column order. Either way `order` names the key in words, which is the
    one thing a reader has to be able to check to know what the first column is.

    The ranking itself comes from `ranking.rank_corpora` and is not recomputed
    here, so a rank cannot disagree with the score printed beside it. Directional
    sentences come from `ranking.comparative_statement`, one per adjacent pair in
    rank order: N-1 sentences rather than the N(N-1)/2 a full matrix would
    produce, and each one refuses itself outright when the two corpora did not
    score on the same components under the same table.
    """
    now = now or datetime.now()
    weights_used = dict(weights) if weights else dict(resolve_weights(None))
    order_by = order_by if order_by in (ORDER_BY_SCORE, ORDER_BY_LABEL) else ORDER_BY_SCORE
    key = _comparison_score_key if order_by == ORDER_BY_SCORE else _comparison_sort_key

    corpora: list[dict[str, Any]] = []
    for entry in sorted(entries, key=key):
        report = entry.get("report") or {}
        score = report.get("score")
        corpora.append({
            "label": str(entry.get("label") or ""),
            "source": str(entry.get("source") or ""),
            "refused": bool(report.get("refused")),
            "gate": report.get("gate"),
            "corpus_size": (report.get("provenance") or {}).get("corpus_size"),
            "score": score,
            "impact": report.get("impact"),
            "citations_note": report.get("citations_note", ""),
            "generated_at": report.get("generated_at", ""),
        })

    ranking = rank_corpora(corpora)
    # Keyed on label *and* source: two directories harvested for the same PI
    # carry the same label, and keying on the label alone would give both rows
    # whichever rank happened to be written last.
    def ident(row: Mapping[str, Any]) -> tuple[str, str]:
        return (str(row.get("label") or ""), str(row.get("source") or ""))

    ranked_rows = {ident(row): row for row in ranking["entries"]}
    for item in corpora:
        row = ranked_rows.get(ident(item)) or {}
        item["rank"] = row.get("rank")
        item["ranked"] = bool(row.get("ranked"))
        item["stars"] = row.get("stars")
        item["rank_of"] = row.get("of", ranking["n_ranked"])
        item["tied_with"] = list(row.get("tied_with") or [])
        item["unranked_reason"] = row.get("reason")

    position = {ident(item): index for index, item in enumerate(corpora)}
    ranked_order = [
        corpora[position[ident(row)]] for row in ranking["ranked"] if ident(row) in position
    ]
    statements = [
        comparative_statement(ranked_order[index], ranked_order[index + 1])
        for index in range(len(ranked_order) - 1)
    ]

    return {
        # 2: the page gained a rank column, a star column and directional
        # sentences. A consumer that read version 1 as "this file contains no
        # ordering" was reading a promise that has been withdrawn on purpose.
        "schema_version": 2,
        "generated_at": now.isoformat(timespec="seconds"),
        "order_by": order_by,
        "order": COMPARISON_ORDER_BY_SCORE if order_by == ORDER_BY_SCORE else COMPARISON_ORDER,
        "weights_used": weights_used,
        "n_corpora": len(corpora),
        "corpora": corpora,
        "ranking": ranking,
        "statements": statements,
    }


def _component_cell(score: Mapping[str, Any] | None, name: str, field: str) -> str:
    """One component's value for one corpus, or why there is none."""
    if not score:
        return "no report"
    for item in score.get("components") or []:
        if item.get("name") == name:
            return _fmt_score(item.get(field), 3 if field == "normalised" else 2)
    if name in (score.get("unavailable") or []):
        return "no data"
    return "n/a"


def _component_value(score: Mapping[str, Any] | None, name: str, field: str) -> float | None:
    if not score:
        return None
    for item in score.get("components") or []:
        if item.get("name") == name:
            value = item.get(field)
            return None if value is None else float(value)
    return None


def _stars_cell(item: Mapping[str, Any]) -> str:
    """A corpus's star band for the ranked table, or why it has none."""
    count = item.get("stars")
    if count is None:
        return "none"
    return f"{'★' * int(count)}{'☆' * (STAR_MAX - int(count))} ({int(count)} of {STAR_MAX})"


def render_comparison_markdown(comparison: Mapping[str, Any]) -> str:
    """
    The side-by-side page, ranked.

    Every table on it is keyed by component down the rows and by corpus across
    the columns, in the order `build_comparison` produced — by score unless the
    caller asked for label order, and `order` says which in words at the head of
    the page.

    Round one's version of this docstring said the page "does not order them by
    any value, does not rank them, and does not say which is better". All three
    of those have been reversed on purpose. What replaces them is narrower and
    is stated on the page itself: a rank here is a position among the corpora
    somebody chose to load and nothing wider, it is printed with the count it
    was taken over, it says nothing about supervision, and it is not comparable
    across fields no matter how sound the arithmetic is. Percentile, quantile
    position and letter tiers are still not produced, and the register saying so
    is printed at the foot of the page rather than summarised.
    """
    corpora = list(comparison.get("corpora") or [])
    labels = [item["label"] or "(unnamed)" for item in corpora]
    weights_used = comparison.get("weights_used") or {}
    ranking = comparison.get("ranking") or {}
    statements = list(comparison.get("statements") or [])

    parts = [
        f"# Corpora side by side — {len(corpora)} corpora",
        "",
        f"_Generated {comparison.get('generated_at', '')}._",
        "",
        f"Column order: {comparison.get('order', COMPARISON_ORDER)}",
        "",
        "This page ranks the corpora on it by composite score and says which of two scored "
        "higher. Read that for exactly what it is: a position among the corpora somebody loaded "
        "into this run, decided by one weighted mean under one weight table printed below. Every "
        "score here is the same number Section 16 of that corpus's own report prints and carries "
        "every limit stated there — it is not comparable across fields, the citation components "
        "are bounded by each corpus's own search window, and a corpus with thin citation coverage "
        "scores on fewer components than one without.",
        "",
        ranking.get("caveat", ""),
        "",
        "## Ranked",
        "",
        f"Method: {ranking.get('method', RANK_METHOD)}",
        "",
        f"Ties: {ranking.get('tie_note', RANK_TIE_NOTE)}",
        "",
        "| # | corpus | score out of 100 | stars | source | records | components scored | state |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for item in corpora:
        label = item["label"] or "(unnamed)"
        score = item.get("score") or {}
        if item["refused"]:
            gate = item["gate"] or {}
            state = f"report refused — gate {gate.get('id', '?')} ({gate.get('name', '')})"
            size = "n/a"
            value = "not computed"
            scored = "n/a"
        else:
            state = "citations joined" if item.get("impact") else "no citation data"
            size = str(item.get("corpus_size", "?"))
            value = "withheld (too few components)" if score.get("suppressed") \
                else _fmt_score(score.get("score"), 1)
            scored = f"{score.get('denominator', '?')} of {score.get('components_registered', '?')}"
        place = f"{item['rank']} of {item.get('rank_of', '?')}" if item.get("ranked") else "unplaced"
        parts.append(
            f"| {place} | {label} | {value} | {_stars_cell(item)} | `{item['source']}` | {size} | "
            f"{scored} | {state} |"
        )

    parts += [
        "",
        f"Positions were taken over the {ranking.get('n_scored', ranking.get('n_ranked', 0))} "
        f"corpora that carried a score, "
        f"out of {ranking.get('denominator', len(corpora))} on this page. That denominator is not "
        "decoration: first of two and first of nine are different facts and the column cannot tell "
        "them apart on its own.",
    ]
    if ranking.get("suppressed"):
        parts += [
            "",
            f"No position was assigned to anything. Fewer than "
            f"{ranking.get('min_ranked_corpora', 2)} corpora on this page carried a score, and "
            "with fewer than that there is nothing to be first among.",
        ]
    unranked = [row for row in ranking.get("unranked") or []]
    if unranked:
        parts += ["", "Corpora holding no position, and why. None of them was scored as zero and "
                  "none was placed last, because last is a position:", ""]
        parts += [
            f"- **{row['label'] or '(unnamed)'}** — {row.get('reason', 'no reason recorded')}"
            for row in unranked
        ]
    comparability = ranking.get("comparability") or {}
    if comparability.get("note"):
        parts += ["", comparability["note"]]

    if statements:
        parts += [
            "",
            "## Which of two scored higher",
            "",
            "One sentence per adjacent pair in rank order — which is the column order above only "
            "when the page is ordered by score, so read the ranks rather than the columns. Each "
            "sentence carries the difference and both component counts, and refuses itself "
            "outright when the two scores were not built from the same components under the same "
            "table: a difference between two weighted means over different material has no "
            "subject, and is withheld rather than printed under a hedge.",
            "",
        ]
        parts += [f"- {statement['statement']}" for statement in statements]

    parts += [
        "",
        "## Weight table in effect",
        "",
        "One table for every corpus on this page. A score computed under a different table is a "
        "different number and does not belong in these columns.",
        "",
        "| component | weight |",
        "|---|---|",
    ]
    parts += [f"| {name} | {_fmt_score(weight)} |" for name, weight in weights_used.items()]

    parts += [
        "",
        "## Composite score",
        "",
        "| corpus | score out of 100 | components scored | components with no data |",
        "|---|---|---|---|",
    ]
    for item in corpora:
        score = item.get("score")
        if item["refused"] or not score:
            parts.append(f"| {item['label'] or '(unnamed)'} | not computed | n/a | n/a |")
            continue
        value = "withheld (too few components)" if score.get("suppressed") \
            else _fmt_score(score.get("score"), 1)
        parts.append(
            f"| {item['label'] or '(unnamed)'} | {value} | {score.get('denominator', '?')} of "
            f"{score.get('components_registered', '?')} | "
            f"{', '.join(score.get('unavailable') or []) or 'none'} |"
        )

    component_names = list(weights_used.keys())
    parts += [
        "",
        "## Component values, normalised to [0, 1]",
        "",
        "Rows are in the components' registration order — never reordered by what is in them. "
        "Columns are in the page order named at the head of this file.",
        "",
        "| component | " + " | ".join(labels) + " |",
        "|---" * (len(labels) + 1) + "|",
    ]
    for name in component_names:
        cells = " | ".join(_component_cell(item.get("score"), name, "normalised") for item in corpora)
        parts.append(f"| {name} | {cells} |")

    if len(corpora) >= 2:
        reference = corpora[0]
        ref_label = reference["label"] or "(unnamed)"
        others = corpora[1:]
        why = (
            "the highest-scoring column under the weight table below"
            if comparison.get("order_by", ORDER_BY_SCORE) == ORDER_BY_SCORE
            else "the column whose label sorts first, which is not a statement about the corpus"
        )
        parts += [
            "",
            f"## Differences from {ref_label}",
            "",
            f"{ref_label} is the reference column because it is {why}. A difference here is an "
            "arithmetic fact about two normalised component values, and it is a per-component "
            "figure: it does not aggregate, and a corpus ahead on the total can sit behind on any "
            "row of this table. Blank where either side has no value to subtract.",
            "",
            "| component | " + " | ".join(
                f"{item['label'] or '(unnamed)'} minus {ref_label}" for item in others
            ) + " |",
            "|---" * (len(others) + 1) + "|",
        ]
        for name in component_names:
            cells = []
            base = _component_value(reference.get("score"), name, "normalised")
            for item in others:
                value = _component_value(item.get("score"), name, "normalised")
                cells.append("n/a" if base is None or value is None else _fmt_score(value - base, 3))
            parts.append(f"| {name} | {' | '.join(cells)} |")

    parts += [
        "",
        "## What this page still does not contain",
        "",
        "- No percentile and no quantile position. Those need a reference population; the corpora "
        "here are the few somebody chose to load, and a position inside that set would move "
        "whenever an unrelated corpus was added or dropped.",
        "- No letter tier. Stars are produced and A/B/C is not — a decision, recorded here so a "
        "later reader does not unify them and reverse a call they were not party to.",
        "- No fitted trend, slope or year-over-year change. A rank is a position at one moment and "
        "never a movement between two.",
        "- No ordering of people. No roster on any corpus's own report is ever sorted by a count.",
        "- No Journal Impact Factor, JCR quartile or CAS partition on this page. Those are joined "
        "per corpus from a table the reader fills in by hand, in Section 18 of each corpus's own "
        "report, with the edition and the retrieval date printed beside every number. They are "
        "not fetched, not shipped, and never enter the score ranked above.",
        "",
        "The register behind the first three, verbatim:",
        "",
    ]
    for group in ("refused_by_design", "not_computable_here"):
        parts += [f"- **{name}** — {reason}" for name, reason in RANKING_EXCLUSIONS[group]]
    parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def write_comparison(comparison: Mapping[str, Any], output_dir: str | Path) -> dict[str, str]:
    """Write the side-by-side Markdown and its JSON record. Returns both paths."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(str(comparison["generated_at"])).strftime("%Y%m%d_%H%M%S")
    markdown_path = directory / f"advisor_compare_{stamp}.md"
    json_path = directory / f"advisor_compare_{stamp}.json"
    markdown_path.write_text(render_comparison_markdown(comparison), encoding="utf-8")
    json_path.write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return {"markdown": str(markdown_path), "json": str(json_path)}
