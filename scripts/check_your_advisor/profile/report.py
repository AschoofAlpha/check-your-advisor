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
  who published nothing at all. Section 20 does it for student evaluations,
  which the reader collects by hand from public pages; those rows are printed
  verbatim beside their source and their retrieval date, and nothing is computed
  over them — no sentiment, no average, no rating, and no contribution to the
  composite score.

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
from ..evaluations import (
    EVALUATION_CAVEATS,
    EVALUATION_LIMITS,
    EVALUATION_STANCE,
    join_evaluations,
)
from ..journal_risk import JOURNAL_RISK_CAVEATS, SOURCE_ORDER, join_risk
from ..journals import (
    EDITIONS,
    JOURNAL_CAVEATS,
    MATCH_ABBREV,
    MATCH_ABBREV_OFFICIAL,
    MATCH_EXACT,
    MATCH_ISSN,
    join_journals,
)
from ..pubmed_api import (
    _affiliation_matches,
    _email_domain_matches,
    _name_matches,
    coverage_remedy,
    normalise_openalex_id,
    record_openalex_author_ids,
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
from .roles import apply_record_exclusions, build_people, prepare_paper, rank_people
from .scoring import SCORING_EXCLUSIONS, composite_score, resolve_weights
from .trends import fit_trend

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
#
# 4: `evaluations` sits beside `graduates` and `journals`, with Section 20
# rendering it. Additive like the two before it, and the reason for the bump is
# sharper this time: every earlier version of this file held nothing but
# publication metadata, and a consumer was entitled to read it that way. This
# one can carry third-party statements about a named person, collected by hand
# from public pages. They enter no score and no aggregate — `EVALUATION_STANCE`
# and Section 20 say so at length — but a consumer that assumed "this file
# contains no free text about anybody" should learn that from the version
# rather than from the contents.
#
# 5: `journal_risk` sits beside `journals`, rendered as a block inside Section
# 18. Additive like the three before it, and this bump matters for a reason none
# of them had: every earlier version of this file was assembled from PubMed, a
# citation lookup and files the user typed. This one can carry statements about a
# journal read off DOAJ, Crossref and OpenAlex on a stated day — fetched data
# about a third party rather than about the corpus. They produce no grade, enter
# no score and rank nothing (`JOURNAL_RISK_CAVEATS` says so at length), but a
# consumer that assumed "every value in this file describes this researcher's own
# records" should learn otherwise from the version rather than from a surprise.
SCHEMA_VERSION = 5

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
#
# G1, G2 and G3 all left this mapping and are warnings now. Refusing over any of
# them destroyed sixteen sections of computed fact to protect one claim —
# including Section 19, whose entire job is to show a reader whether the corpus
# holds several people, and which a reader of a refusal page never gets to see.
# As warnings: `check_coverage_warnings` / `check_identity_warnings` return them,
# `_warning_line` prints them at the top of the sections they affect in bold,
# Section 14 records the downgrade, `exit_code` still comes back 1, and
# `_scored_value` still withholds a rank on the comparison page — so a script
# branching on either behaves exactly as it did when these were refusals.
#
# G1 took a detour worth naming, because the register in Section 14 is the only
# place a reader can see it. It was deleted outright when the harvest learned to
# page, on the argument that a shortfall between the esearch count and the PMIDs
# retrieved now means the `max_records` budget stopped a very common name, or the
# pages repeated a PMID, or PubMed's own Count moved mid-harvest — none of them
# worth withholding a report. That much still holds and is why G1 is not a gate.
# What the deletion also removed was any mark on the page at all: a corpus 40%
# retrieved rendered clean, exited 0, and sat on a `compare` page ranked against
# corpora that were retrieved in full, with nothing distinguishing it. It is back
# at the same strength as G2 and G3 — printed, warned, exit 1, unranked.
#
# No id is ever reused. G1, G2 and G3 keep their numbers as warnings and G4/G5
# keep theirs as gates — a "gate G3" written in somebody's notes still names the
# same condition.
GATES: dict[str, tuple[str, str]] = {
    "G4": ("no structured authors",
           "Corpus lacks structured author records. Run the profile fetch stage; the report cannot be "
           "built from the Excel export."),
    "G5": ("empty corpus", "0 papers remain after exclusions. Nothing can be reported."),
    "G6": ("inconsistent provenance counts",
           "This corpus records verified={verified} against fetched={fetched}, so the rejected count "
           "would be {rejected_would_be}. Both count PubMed records only — `fetched` is what efetch "
           "parsed and `verified` is what the first/corresponding-author identity filter kept out of "
           "it, which Section 1 prints as `kept` — so verified can never exceed "
           "fetched, and a merged corpus size belongs under `corpus_total`. The file was written by a "
           "version that overwrote `verified` with the merged total; every ratio built on those two "
           "numbers is wrong by an amount nothing on the page can show. Re-run `harvest` against the "
           "same output directory to rewrite it."),
}

#: The three operands G6's sentence is built from, named once.
#:
#: `cli._corpus_counts` writes exactly these three under `counts["inconsistent"]` and
#: `GATES["G6"]` formats exactly these three out of it. Listing them here rather than
#: relying on `**observed` matching the template is what keeps a hand-edited file from
#: reaching `str.format` with a key set it does not have — which raised `KeyError` out
#: of `main()`, and `TypeError` when the file happened to carry a key named `gate_id`
#: or `observed`.
G6_OPERANDS: tuple[str, str, str] = ("fetched", "verified", "rejected_would_be")

#: What G6 says when the block is there but its operands are not readable.
#:
#: The verdict does not depend on being able to print the three numbers: a mapping
#: under `counts["inconsistent"]` is this tool's own record that the corpus's PubMed
#: counts contradict each other, and every ratio built on them is wrong whether or not
#: the operands survived whatever edited the file. So the gate still fires, and the
#: sentence drops the three numbers it does not have instead of formatting `None` into
#: them. The block itself is quoted back through `observed`.
G6_OPERANDS_UNREADABLE = (
    "This corpus records under `counts.inconsistent` that its own PubMed counts contradict "
    "each other, but not in the shape `harvest` writes: the three operands `fetched`, "
    "`verified` and `rejected_would_be` are not all present as whole numbers, so this page "
    "cannot state them and does not guess. The verdict is unchanged — a corpus whose own "
    "`verified` exceeds its own `fetched` has a denominator that is wrong by an unknown "
    "amount under every number that would be on the page. The block as the file carries it "
    "is quoted below. Re-run `harvest` against the same output directory to rewrite it."
)

#: Which sections carry the identity warning. Section 0 states what the report
#: is, Section 19 is the check for exactly this failure, and Section 1 holds the
#: observed numbers — and in print order the reader meets them 0, 19, 1. Three
#: placements rather than twenty: a line repeated in every section is a line
#: readers learn to skip, which is the failure the old refusal was guarding
#: against in the first place.
WARNING_SECTIONS: tuple[int, ...] = (0, 1, 19)

#: Where G1 goes instead. Same argument, different sections: Section 1 holds the
#: retrieved-of-matched numbers, Section 9 is the per-year count a partial
#: harvest deflates most visibly, and Section 0 states what the report is. Not
#: Section 19 — an incomplete corpus is not a corpus that may hold two people,
#: and putting one warning everywhere is how a reader stops reading any of them.
COVERAGE_WARNING_SECTIONS: tuple[int, ...] = (0, 1, 9)

#: Where G7 goes. Section 0 states what the report is, Section 1 holds the
#: located-of-harvested ratio, and Sections 2 and 7 are the two whose own opening
#: sentences are falsified by a name that reached nothing: Section 2's roster is
#: "after removing the target researcher" and removed nobody, and Section 7 is
#: about where the target sits in a byline they are not on. Not Section 19 —
#: whether the corpus blends several people is a different question from whether
#: it is about the person who was asked for, and G3 already lands there.
NAME_WARNING_SECTIONS: tuple[int, ...] = (0, 1, 2, 7)

#: How much of a corpus an OpenAlex author id has to actually reach before it
#: counts as identity evidence for the corpus as a whole, and so clears warning
#: G3 on its own.
#:
#: Declared here, printed verbatim in Section 1 beside the ratio it was compared
#: against, and overridable per run under `identity_evidence.min_openalex_record_share`
#: — the same rule `scoring.DEFAULT_WEIGHTS` and `ranking.STAR_BANDS` follow, and
#: for the same reason: a boundary a reader cannot see is a boundary a reader
#: cannot argue with.
#:
#: 0.5 is a stated convention, not a measurement. There is no data here that
#: could calibrate it, and saying so is the point: what it rules out is a corpus
#: whose identity rests on a minority of its own records, and the exact minority
#: it draws the line at is arbitrary in a way the printed value makes checkable.
#: The previous behaviour was the same kind of choice made silently — one record
#: out of any number was enough.
MIN_OPENALEX_RECORD_SHARE = 0.5

# Name, what is wrong, and what to do about it. Split into three fields rather
# than one paragraph because the fix has to survive being read on its own.
#
# Unlike `GATES`, these strings are never `.format`ted: the numbers live in the
# warning's `observed` dict and are rendered once by `_flat_observed`, so one
# condition cannot print two differently-worded versions of its own numbers.
WARNINGS: dict[str, tuple[str, str, str]] = {
    "G1": ("incomplete harvest",
           "esearch matched more records than this harvest retrieved, so the corpus is part of what "
           "the query found. Every count below is computed over the part that arrived and is a floor "
           "rather than a value — and nothing on the page can say whether the records that never "
           "arrived look like the ones that did.",
           "Read the retrieved-of-matched line in Section 1 first: it says whether the shortfall was "
           "this tool's max_records budget (raise it and re-harvest) or NCBI's own 10,000-record "
           "esearch ceiling (narrow the query instead — cut years_back, add affiliation_keywords, or "
           "supply an ORCID)."),
    "G2": ("identity fallback",
           "No paper in this corpus passed identity verification, so the harvest fell back to keeping "
           "everything it found. This corpus is 'every paper by anyone publishing under this name' and "
           "may describe several different researchers.",
           "Configure orcid, affiliation_keywords or email_domains — or resolve an OpenAlex author id "
           "with `harvest --resolve-openalex` — and harvest again."),
    "G3": ("weak identity config",
           # Placeholder only. Every G3 raised by `check_identity_warnings` carries one of
           # `G3_SITUATIONS` instead, chosen from what the evidence actually reached; this
           # entry is what a bare `_warning("G3", …)` falls back to and is the mildest of
           # the three, so a caller that forgets to choose cannot over-claim.
           "No identity evidence reached enough of this corpus to tell one researcher from another, "
           "so the records it did not reach are name matches and nothing more. For any common "
           "surname that blends several people, and nothing below can tell them apart.",
           "Set orcid, email_domains or affiliation_keywords to a value the records themselves carry "
           "— a key that matches nothing is worth exactly what no key is worth, and this warning now "
           "counts matches rather than settings — and harvest again. An openalex_author_id "
           "alone clears this only once it reaches min_openalex_record_share of the corpus, and "
           "PubMed's own metadata carries no author id: the id lands on a record only where OpenAlex "
           "holds that paper too — either a record OpenAlex contributed, or a PubMed record that "
           "`harvest --openalex-works` merged an OpenAlex work into, which carries the confirming "
           "byline's ids at record level. Run that merge, or supply one of the other three."),
    "G7": ("target name not located",
           # Placeholder only, exactly as G3's is: every G7 raised by
           # `check_name_warnings` carries one of `G7_SITUATIONS` instead. This entry is
           # what a bare `_warning("G7", …)` falls back to.
           "The target name was not located on the byline of any record in this corpus, so no "
           "section below is about the person it names.",
           "Check the spelling and the order of `--pi-name` / `author_name` against the byline as "
           "PubMed spells it — surname first and given name second is what the matcher expects, "
           "and it also accepts the reverse. Section 2's roster lists every name the corpus "
           "actually holds; pick the one that is meant and re-run `profile`. Nothing needs "
           "re-harvesting: the corpus is unchanged and only the name it is read against is wrong."),
}

#: The two situations G7 fires on, and the sentence each one gets.
#:
#: Split for the same reason `G3_SITUATIONS` is split: "you spelt the name differently from
#: PubMed" and "you supplied no name at all" have different fixes, and one sentence covering
#: both would be false about one of them. Neither sentence carries a number — the ratio lives
#: in `observed` and `_flat_observed` renders it once.
G7_SITUATIONS: dict[str, str] = {
    "not_located": (
        "The target name was located on the byline of none of this corpus's records. Every "
        "section below was computed by looking for that name and finding it nowhere: the roster "
        "in Section 2 removed no target researcher and therefore counts one person too many, "
        "Section 7 has no byline slot to report, and every count that says \"the target "
        "researcher\" is about a name this corpus does not contain. The corpus itself may be "
        "perfectly good — this says the name it was read against is not the one on its papers."
    ),
    "no_name_configured": (
        "No target name was configured for this report, so there was nothing to look for on any "
        "byline. Every section below still computed: the roster removed no target researcher, "
        "Section 7 located no byline slot, and each phrase reading \"the target researcher\" "
        "names nobody. This is a report about a corpus rather than about a person."
    ),
}

#: The three situations G3 fires on, and the sentence each one gets.
#:
#: One sentence used to cover all three. It read "No identity evidence reached a single record
#: in this corpus" and was printed over corpora whose own Observed values, on the same line,
#: read `openalex_id_on_records=2/6` — the banner denying the number beside it, and Section 1
#: two lines above it counting `openalex 2`. Which of the three happened is a different fact
#: each time and is now said each time.
#:
#: Each entry declares whether its sentence *claims zero reach*, so that claim can be checked
#: against the corpus's numbers instead of proof-read. The invariant, asserted in
#: `tests/test_profile.py` over every corpus that fires: a message flagged True may be chosen
#: only where `observed["identity_evidence_on_records"]` is `0/N`, and a message flagged False
#: only where it is not. The flag is kept honest against the prose by
#: `G3_ZERO_REACH_PHRASES` — a flagged message must contain one of those fragments and an
#: unflagged one must contain none — so a copy-edit that moves a zero-reach claim into the
#: partial-reach sentence fails the suite rather than shipping.
#:
#: The strings hold no numbers, for the same reason `WARNINGS` holds none: the numbers live in
#: `observed` and are rendered once by `_flat_observed`, so one condition cannot print two
#: differently-worded versions of its own arithmetic.
G3_SITUATIONS: dict[str, tuple[bool, str]] = {
    "none_configured": (
        True,
        "No identity evidence was configured for this harvest — no orcid, no email_domains, no "
        "affiliation_keywords, no openalex_author_id — so nothing but the name reached any record "
        "here and the corpus is a name match and nothing more. For any common surname that blends "
        "several people, and nothing below can tell them apart.",
    ),
    "configured_no_reach": (
        True,
        "Identity evidence was configured and reached no record in this corpus: the Observed values "
        "below name what was set and count how far each got, which was nowhere. A key that matches "
        "nothing constrains nothing, so this corpus is a name match and nothing more — exactly as if "
        "the key had been left blank. For any common surname that blends several people, and nothing "
        "below can tell them apart.",
    ),
    "below_min_share": (
        False,
        "The only identity evidence that reached this corpus is an OpenAlex author id, and it reached "
        "fewer of the records than min_openalex_record_share requires — the ratio and the boundary "
        "are both in the Observed values below. A minority of a corpus does not vouch for the rest of "
        "it, so the records the id never reached are name matches and nothing more. For any common "
        "surname that blends several people, and nothing below can tell them apart.",
    ),
}

#: Fragments that assert *zero* reach. Declared beside `G3_SITUATIONS` rather than retyped in
#: the test, so the phrase a message is judged against and the phrase it contains are one string.
G3_ZERO_REACH_PHRASES: tuple[str, ...] = (
    "No identity evidence was configured",
    "reached no record",
)


def _gate(gate_id: str, observed: dict[str, Any], message: str | None = None,
          **fields: Any) -> dict[str, Any]:
    """One gate record. `message` overrides the mapping's default sentence.

    The override exists for the same reason `_warning`'s does: G6's condition has
    two shapes — the three operands recorded as `harvest` writes them, and a block
    that says the counts contradict without stating them readably — and the default
    sentence names three numbers, so it is false about the second. `fields` are the
    format slots of whichever sentence is used, and callers pass the named slots
    rather than splatting a dict read out of a file: `**observed` on a mapping that
    happened to carry a `gate_id` or `observed` key raised `TypeError` here, and one
    missing a slot raised `KeyError`, both of them out through `build_report` and out
    of `main()`, which has no handler.
    """
    name, default_message = GATES[gate_id]
    message = default_message if message is None else message
    return {
        "id": gate_id,
        "name": name,
        "message": message.format(**fields),
        "observed": observed,
    }


def _flat_observed(observed: Mapping[str, Any]) -> str:
    """`k=v; k=v` for a warning's observed values, lists rendered as counts.

    A keyword list is printed as `affiliation_keywords=0` rather than as `[]`,
    because the number is what the reader has to act on and an empty bracket
    reads as a rendering artifact.
    """
    parts = []
    for key, value in observed.items():
        if isinstance(value, (list, tuple)):
            parts.append(f"{key}={len(value)}")
        elif value == "" or value is None:
            parts.append(f"{key}=(none)")
        else:
            parts.append(f"{key}={value}")
    return "; ".join(parts)


def _warning(
    warning_id: str,
    observed: dict[str, Any],
    sections: Sequence[int] | None = None,
    message: str | None = None,
    downgraded: bool = True,
) -> dict[str, Any]:
    """One warning record. `message` overrides the mapping's default sentence.

    G3 and G7 use the override, and they use it for the same reason: their
    condition has more than one shape — for G3, nothing configured, configured and
    reaching nothing, an OpenAlex id reaching too little; for G7, a name that
    matched nothing and no name at all — and one sentence cannot describe them
    without being false about the rest. The override is a whole sentence from
    `G3_SITUATIONS` / `G7_SITUATIONS` rather than a `.format` slot: numbers stay in
    `observed`, where `_flat_observed` renders them once.

    `downgraded` is whether this condition used to refuse the whole report.
    G1, G2 and G3 did and say so; G7 never existed as a gate, and printing "this
    used to refuse the whole report" over it would invent a history the register
    in Section 14 exists to keep straight. Both the section banner and that
    register read this flag rather than assuming.
    """
    name, default_message, fix = WARNINGS[warning_id]
    message = default_message if message is None else message
    return {
        "id": warning_id,
        "name": name,
        "message": message,
        "fix": fix,
        "observed": observed,
        # Rendered once, here, so the section callout, the Section 14 register
        # line and the command-line log all print the same string. Three call
        # sites formatting one dict three ways is how `orcid=` in one place and
        # `orcid=(none)` in another end up looking like different observations.
        "observed_text": _flat_observed(observed),
        "sections": list(WARNING_SECTIONS if sections is None else sections),
        "downgraded": downgraded,
    }


def _as_int(value: Any) -> int | None:
    """`value` as an int, or None when it is not a number.

    A corpus that predates provenance carries "?" in these slots and a
    hand-written one can carry anything. `int("?")` raises, and the old G1 gate
    did exactly that — `int(query.get("esearch_count") or 0)` died on the first
    legacy corpus it met. Comparing two unknowns is not a finding, so an
    unparseable operand yields no warning rather than an exception.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def check_coverage_warnings(corpus: dict[str, Any]) -> list[dict[str, Any]]:
    """
    G1: esearch matched more records than the harvest retrieved.

    Separate from `check_identity_warnings` because it is a different question
    with a different fix — "is this all of it" rather than "is this one person" —
    and because the two sets of affected sections do not overlap beyond 0 and 1.
    Both lists feed the same `warnings` slot, so a corpus can carry either, both,
    or neither.

    Silent when either side of the comparison is unrecorded. Section 1 already
    prints "retrieved ? of ?" for that corpus, which is the honest answer; a
    warning built on two unknowns would be an assertion about a number nobody has.
    """
    query = _readable_block(corpus.get("query"))
    count = _as_int(query.get("esearch_count"))
    returned = _as_int(query.get("pmids_returned"))
    if count is None or returned is None or count <= returned:
        return []
    return [_warning("G1", {
        "esearch_count": count,
        "pmids_returned": returned,
        "never_retrieved": count - returned,
        # The two settings a reader has to look at to tell "my budget stopped it"
        # from "NCBI's ceiling stopped it". Section 1's coverage line makes that
        # call in words; the raw values travel with the warning so the call can
        # be checked rather than taken.
        "max_records": query.get("max_records", "?"),
        "pages_fetched": query.get("pages_fetched", "?"),
    }, sections=COVERAGE_WARNING_SECTIONS)]


def check_identity_warnings(
    corpus: dict[str, Any], min_openalex_record_share: float = MIN_OPENALEX_RECORD_SHARE
) -> list[dict[str, Any]]:
    """
    The two identity conditions that used to refuse the report, as warnings.

    Returns zero, one or both. Both is now possible and was not before: as gates
    the first one to fire hid the second, so a corpus that had no evidence
    configured *and* kept nothing through verification was reported as one
    problem. They are different problems with different fixes and both are said.

    Whether a corpus can be ranked beside others is decided from this list too —
    see `_scored_value`. A corpus that may hold several people keeps its row on
    the comparison page and holds no position, which is exactly what it did when
    this was a refusal.

    `min_openalex_record_share` is how much of the corpus the OpenAlex author id
    has to actually reach before it clears G3 on its own. It is a share and not a
    boolean because the boolean was a hidden threshold of one record: a corpus of
    six where five were bare name matches and one came from the OpenAlex merge
    cleared the warning outright and exited 0. The default is
    `MIN_OPENALEX_RECORD_SHARE`, the config key is
    `identity_evidence.min_openalex_record_share`, and whatever value is in
    effect is printed in Section 1 beside the ratio it was compared against.

    G3 is decided on what the evidence *reached*, not on what was configured,
    and that is a reversal — Section 14 carries the register line. An ORCID set
    in the config and matched against no byline in the corpus constrains exactly
    as much as no ORCID at all; under the old rule the second warned and exited 1
    while the first was silent and exited 0. Now both warn. The OpenAlex share
    boundary is untouched; what changed is that the other three are counted the
    same way it always was.
    """
    warnings: list[dict[str, Any]] = []
    papers = corpus.get("papers")
    if bool(corpus.get("fallback_fired")):
        warnings.append(_warning("G2", {
            "fallback_fired": True,
            "papers_stamped_unverified": sum(
                1 for paper in (papers if isinstance(papers, list) else [])
                if isinstance(paper, dict) and paper.get("role") == "待确认"
            ),
            # Named `papers_harvested`, not `corpus_size`: this is the file as
            # harvested, while `provenance.corpus_size` is what survives the
            # record exclusions. Two different numbers under one name in one
            # report is how a reader concludes the exclusions did nothing.
            "papers_harvested": len(papers) if isinstance(papers, list) else 0,
        }))

    identity, identity_unreadable = _readable_identity(corpus.get("identity"))
    openalex_id = (identity.get("openalex_author_id") or "").strip()
    # An OpenAlex author id is evidence only for records that actually carry it,
    # and PubMed's `parse_article` never writes the field — so on a PubMed-only
    # corpus `evidence_tier` returns `name_only` for every single byline entry
    # while the configured id silently cleared this warning. Counted here as a
    # share of the corpus rather than as "at least one", because at-least-one is
    # a threshold of 1/N that nobody declared and nobody could see.
    carried, total = openalex_id_record_share(papers, openalex_id)
    share = (carried / total) if total else 0.0
    openalex_backed = bool(openalex_id) and total > 0 and share >= min_openalex_record_share
    # The other three, counted the same way. `strong_reached` is how many records
    # an ORCID, an email domain or an affiliation keyword actually matched; a
    # configured key that matched none of them clears nothing, which is the whole
    # reversal. `reached` is the union with the records the id reached, and it is
    # what the banner's sentence is about — so the sentence and the number it
    # asserts sit on the same printed line and can be read against each other.
    reached, strong_reached, _ = identity_evidence_record_reach(papers, identity)
    if not strong_reached and not openalex_backed:
        # `reached` can only be non-zero here through the OpenAlex id: any record
        # an ORCID, a domain or a keyword touched would have set `strong_reached`
        # and cleared the warning outright. So the three situations partition
        # cleanly, and each gets the only sentence that is true of it.
        if reached:
            situation = "below_min_share"
        elif identity_unreadable or openalex_id \
                or (identity.get("orcid") or "").strip() \
                or (identity.get("affiliation_keywords") or []) \
                or (identity.get("email_domains") or []):
            # `identity_unreadable` is first because it is the case the other
            # three cannot see: `_readable_identity` empties a field it cannot
            # read, so a file carrying `orcid: 3.5` arrives here looking exactly
            # like a file carrying no ORCID at all. Without this term the banner
            # would print "No identity evidence was configured for this harvest",
            # which is a statement about the user's config and false about that
            # file. "Configured and reached no record" is true of it, and the
            # raw value is quoted in `identity_fields_unreadable` below.
            situation = "configured_no_reach"
        else:
            situation = "none_configured"
        warnings.append(_warning("G3", {
            "orcid": identity.get("orcid", ""),
            "affiliation_keywords": identity.get("affiliation_keywords", []),
            "email_domains": identity.get("email_domains", []),
            "openalex_author_id": openalex_id,
            # Only when there is something to say. Quoted with `repr` rather than
            # handed to `_flat_observed`, which renders a list as its length and
            # would print `affiliation_keywords=2` over a file holding `[1, 2]`.
            **({"identity_fields_unreadable": ", ".join(
                f"{key}={raw!r}" for key, raw in sorted(identity_unreadable.items())
            )} if identity_unreadable else {}),
            # Spelt out because `openalex_author_id=A5000000001` beside a fired
            # G3 otherwise reads as a bug in the warning rather than as the
            # finding it is: the id was configured and reached too little of the
            # corpus to stand in for the other three. Printed as a ratio and a
            # threshold together so the reader can check the call.
            "openalex_id_on_records": f"{carried}/{total}",
            "openalex_id_record_share": round(share, 3),
            "min_openalex_record_share": min_openalex_record_share,
            # The number the banner's own sentence claims something about. It is
            # printed whichever situation fired, so "reached no record" can be
            # checked against `0/N` on the same line rather than believed.
            "identity_evidence_on_records": f"{reached}/{total}",
        }, message=G3_SITUATIONS[situation][1]))
    return warnings


def check_name_warnings(corpus: dict[str, Any], target_name: str) -> list[dict[str, Any]]:
    """
    G7: the target name is on no byline in this corpus.

    Separate from `check_identity_warnings` because it is a different question
    with a different fix. G2 and G3 ask whether the corpus is *one* person; this
    asks whether it is *the* person, and its fix is to correct the name and re-run
    `profile` rather than to configure evidence and harvest again.

    It is also the one question none of the other readers of this corpus asks.
    `_corpus_counts` derives the evidence histogram from the role string, and
    `openalex_id_record_share` counts an author id at record level; neither
    consults the name, so on a corpus profiled under a mistyped name both go on
    reporting `8 of 8` while `roles.resolve_pi` — the only reader that does
    consult it — rejects every record. Three lines of Section 1 then agree about
    a corpus that no section below is about. Measured here so the disagreement is
    printed instead of hidden, at warning strength and not as a gate: the counts
    on the page are still counts of real records, and refusing would destroy
    nineteen sections of fact over a fixable typo.

    Fires only at zero. No share boundary is invented for it and none is printed,
    because there is no second reading available: `MIN_OPENALEX_RECORD_SHARE`
    exists to replace a hidden threshold of one record with a stated one, and
    adding a second arbitrary constant here would create the very thing that
    constant was introduced to remove. Section 1 prints the ratio on every run,
    warning or not, so a corpus the name reached 2 of 40 records of is visible
    without a boundary having been asserted about it.

    Silent on an empty corpus: `0 of 0` is a statement about a file with no
    records, which gate G5 answers a few lines later in `build_report` and
    answers better.
    """
    located, records = target_name_record_reach(corpus.get("papers"), target_name)
    if records == 0 or located:
        return []
    situation = "not_located" if (target_name or "").strip() else "no_name_configured"
    return [_warning("G7", {
        "target_name": (target_name or "").strip(),
        # The ratio the banner's sentence is a claim about, printed beside it so
        # the claim can be checked rather than taken — the same rule G3's
        # `identity_evidence_on_records` follows.
        "target_name_on_records": f"{located}/{records}",
    }, sections=NAME_WARNING_SECTIONS, message=G7_SITUATIONS[situation], downgraded=False)]


def target_name_record_reach(papers: Any, target_name: str) -> tuple[int, int]:
    """How many records carry the target name on a byline, out of how many.

    Counted through `pubmed_api._name_matches`, which is the matcher
    `roles.resolve_pi` builds its candidate list with, so the number Section 1
    prints and the number of records that got a located PI cannot disagree. It
    accepts either name order and matches a given name against an initial, so
    this counts what `resolve_pi` would count and not a stricter private rule.

    Record-level and byline-wide, matching `openalex_id_record_share` and
    `identity_evidence_record_reach`: a record counts once, whichever entry
    carried the name. The three ratios are printed in the same block of Section 1
    over the same denominator, and scoping one of them differently is how a
    reader ends up comparing numbers that are not comparable.

    An empty `target_name` reaches nothing, and the denominator is still the
    corpus: `(0, len(records))` rather than `(0, 0)`, for the reason
    `openalex_id_record_share` gives about an absent id.
    """
    records = [paper for paper in (papers if isinstance(papers, list) else [])
               if isinstance(paper, dict)]
    parts = (target_name or "").lower().split()
    if not parts:
        return 0, len(records)
    located = sum(
        1 for paper in records
        if any(_name_matches(author, parts)
               for author in (paper.get("authors") or []) if isinstance(author, dict))
    )
    return located, len(records)


def identity_evidence_record_reach(
    papers: Any, identity: Mapping[str, Any]
) -> tuple[int, int, int]:
    """How far the configured identity evidence got into this corpus.

    Returns `(reached, strong_reached, records)`. `strong_reached` counts the
    records where an ORCID, an email domain or an affiliation keyword matched a
    byline entry; `reached` is that set unioned with the records carrying the
    configured OpenAlex author id, which is the wider question the G3 banner
    talks about. Both are counts of records, over the same denominator Section 1
    prints.

    Each of the four is read at the level it is true at. The id is a record-level
    fact — a paper both databases hold keeps PubMed's byline and carries the
    confirming work's ids under `OPENALEX_IDS_FIELD` — so it goes through
    `record_openalex_author_ids`, the reader `openalex_id_record_share` and
    `roles.resolve_pi` already share. The other three are properties of a byline
    entry and are matched against every entry on the record.

    Deliberately not `roles.evidence_tier` in a loop, though the matchers are the
    same ones. That function answers "what is the *strongest* evidence on this
    entry", so an entry carrying both the OpenAlex id and a matching affiliation
    reports `openalex` and the affiliation match disappears — and this function
    would then report that no affiliation keyword reached the corpus over a
    corpus where one reached every record of it. Strongest-wins is right for
    picking a PI and wrong for asking whether a key matched anything.

    No name filter, matching `openalex_id_record_share`. The two numbers are
    printed side by side on one warning line, and scoping one of them to byline
    entries that match the target name while the other stays record-level is how
    a banner ends up denying the ratio next to it.
    """
    records = [paper for paper in (papers if isinstance(papers, list) else [])
               if isinstance(paper, dict)]
    # Sanitised here as well as in `check_identity_warnings`, which is the only
    # caller inside this module: this function is public, `tests/test_profile.py`
    # calls it with a corpus's own `identity` block, and the guard belongs where
    # the `.strip()` and the `list()` are rather than at one of the call sites.
    # `_readable_identity` is idempotent, so sanitising twice costs a dict copy.
    identity, _ = _readable_identity(identity)
    orcid = (identity.get("orcid") or "").strip().lower()
    domains = list(identity.get("email_domains") or [])
    keywords = list(identity.get("affiliation_keywords") or [])
    wanted = normalise_openalex_id(identity.get("openalex_author_id"))
    reached = strong_reached = 0
    for paper in records:
        by_strong = any(
            _entry_carries_strong_evidence(author, orcid, domains, keywords)
            for author in (paper.get("authors") or []) if isinstance(author, dict)
        )
        by_openalex = bool(wanted) and wanted in record_openalex_author_ids(paper)
        strong_reached += int(by_strong)
        reached += int(by_strong or by_openalex)
    return reached, strong_reached, len(records)


def _entry_carries_strong_evidence(
    author: Mapping[str, Any], orcid: str, domains: list[str], keywords: list[str]
) -> bool:
    """Whether one byline entry matches a configured ORCID, mail domain or keyword.

    The three matchers are `roles.evidence_tier`'s own, imported rather than
    retyped: a keyword rule that is substring-and-case-folded in one file and
    exact in another is how one corpus gets described two ways.
    """
    author_orcid = (author.get("orcid") or "").strip().lower()
    if orcid and author_orcid and author_orcid == orcid:
        return True
    if _email_domain_matches(author.get("email") or "", domains):
        return True
    return bool(_affiliation_matches(author.get("affiliation") or "", keywords)[0])


def openalex_id_record_share(papers: Any, openalex_id: str) -> tuple[int, int]:
    """How many records in this corpus carry `openalex_id`, out of how many.

    Counted through `record_openalex_author_ids`, the same reader
    `roles.resolve_pi` uses, so the warning and the per-paper evidence tier
    cannot disagree about how far this id reached into the corpus.

    That reader covers both halves of the answer, and reading only the first
    half is what this used to get wrong: a byline is evidence for the records
    OpenAlex contributed, but a record both databases hold keeps PubMed's byline
    and carries the id at record level instead. Scanning bylines alone therefore
    counted "records only OpenAlex holds" and called it identity coverage — so a
    corpus where every single paper was confirmed by both databases scored 0,
    and G3 declared the identity unverifiable on the strength of the agreement.

    Returns `(0, len(papers))` for an empty or unrecognised id rather than
    `(0, 0)`: the denominator is a property of the corpus, and Section 1 prints
    it whether or not an id was configured.
    """
    records = [paper for paper in (papers if isinstance(papers, list) else [])
               if isinstance(paper, dict)]
    wanted = normalise_openalex_id(openalex_id)
    if not wanted:
        return 0, len(records)
    carried = sum(1 for paper in records if wanted in record_openalex_author_ids(paper))
    return carried, len(records)


def check_corpus_gates(corpus: dict[str, Any]) -> dict[str, Any] | None:
    """
    Gates G4 and G6, evaluated before anything is computed. G5 needs the record
    exclusions and fires later.

    Three things used to be refused here and are now printed instead. An
    incomplete harvest (G1) is a numerator over a denominator in Section 1 and a
    warning banner; an unverifiable identity (G2, G3) is a bold line at the top
    of Sections 0, 1 and 19. What is left are the two classes a report cannot be
    written around at all: a corpus with no structured author records has nothing
    to compute over, and a corpus whose own PubMed counts contradict each other
    has a denominator that is wrong by an unknown amount under every number on
    the page. Neither has a degraded version worth printing.

    G6 arrives here as `counts["inconsistent"]`, recorded by `cli._corpus_counts`
    rather than raised from it. Raising was the same verdict delivered as an
    uncaught ValueError: `main()` has no handler, so `profile` on an old-format
    corpus printed a traceback instead of the reason and the fix.

    That block is read out of a JSON file and is checked before it is formatted,
    the same rule `by_source`, `by_evidence` and `name_only` follow in
    `_source_lines` / `_evidence_lines`. It was the last key under `counts` that
    was not: `_gate("G6", observed, **observed)` splatted whatever the file held
    into `str.format`, so a block missing one of the three operands raised
    `KeyError` and a block carrying a key named `gate_id` or `observed` raised
    `TypeError` — both out of `main()`, and `cli.py` hands a corpus file's own
    `counts` block straight here whenever the file carries no `search` key, so
    both were one command line away. An unreadable mapping still fires the gate,
    because a mapping under this key *is* the record that the counts contradict;
    what changes is that the sentence stops claiming three numbers it does not
    have and quotes the block instead.

    A non-mapping under the same key fires nothing. `_corpus_counts` writes a
    mapping and only a mapping, so `"inconsistent": "no"` is a file this tool did
    not write and refusing a whole report over a value whose meaning is unknown
    would invent a verdict. It is not dropped in silence either — `_provenance_body`
    prints one line naming what the file actually held.
    """
    papers = corpus.get("papers")
    if not isinstance(papers, list):
        return _gate("G4", {"papers": type(papers).__name__})
    for paper in papers:
        authors = paper.get("authors") if isinstance(paper, dict) else None
        if not isinstance(authors, list) or not authors or not all(isinstance(a, dict) for a in authors):
            return _gate("G4", {"pmid": (paper or {}).get("pmid", "?") if isinstance(paper, dict) else "?"})
    inconsistent = _readable_counts(corpus.get("counts")).get("inconsistent")
    if isinstance(inconsistent, Mapping) and inconsistent:
        if _is_inconsistency_record(inconsistent):
            # Only the three named slots reach `str.format`; anything else the
            # file carries beside them stays visible through `observed`.
            return _gate("G6", dict(inconsistent),
                         **{key: inconsistent[key] for key in G6_OPERANDS})
        return _gate("G6", {"inconsistent": repr(inconsistent)},
                     message=G6_OPERANDS_UNREADABLE)
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
    query = _readable_block(corpus.get("query"))
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
    warnings: Sequence[str] = (),
) -> dict[str, Any]:
    """
    One rendered section.

    `body` holds computed values, `prose` and `caveats` hold fixed text. The
    split exists so a test can scan the computed half for prohibited quantities
    without tripping over the caveats that name those same quantities in order
    to rule them out.

    `warnings` is its own slot rather than the first lines of `prose` so that
    each renderer can give it the prominence it needs without either of them
    having to recognise a warning by its wording: Markdown prints it before
    everything else in the section, HTML puts it in a callout at the top. It
    replaces a page that used to refuse to render, so being mistaken for another
    caveat is the one failure mode that would undo the change.
    """
    return {
        "id": section_id,
        "title": title,
        "body": list(body),
        "caveats": list(caveats),
        "prose": list(prose),
        "warnings": list(warnings),
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


def resolve_openalex_record_share(config: Mapping[str, Any] | None) -> float:
    """
    The G3 evidence-share boundary for one run, fully resolved.

    Read from `config["identity_evidence"]["min_openalex_record_share"]`, which
    is where `config.py` puts it and therefore where a user edits it, and
    defaulting to `MIN_OPENALEX_RECORD_SHARE`. Validated here rather than
    clamped: a share outside 0..1 is a typo, and silently reading 50 as 1.0
    would let a reader believe they had loosened a boundary they had in fact
    pinned shut. Section 1 prints whatever comes back.
    """
    block = (config or {}).get("identity_evidence")
    if not isinstance(block, Mapping) or "min_openalex_record_share" not in block:
        return MIN_OPENALEX_RECORD_SHARE
    value = block["min_openalex_record_share"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"identity_evidence.min_openalex_record_share must be a number between 0 and 1, "
            f"got {value!r}"
        )
    if not 0.0 <= float(value) <= 1.0:
        raise ValueError(
            f"identity_evidence.min_openalex_record_share must be between 0 and 1, got {value!r}"
        )
    return float(value)


def build_report(
    corpus: dict[str, Any],
    config: dict[str, Any] | None = None,
    gantt_path: str | Path | None = None,
    now: datetime | None = None,
    citations: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    citations_note: str = "",
    journal_table: Mapping[str, Any] | None = None,
    journal_note: str = "",
    journal_risk: Mapping[str, Any] | None = None,
    journal_risk_note: str = "",
    impact_reference: Mapping[str, Any] | None = None,
    impact_reference_note: str = "",
    thesis_roster: Mapping[str, Any] | None = None,
    thesis_note: str = "",
    evaluation_table: Mapping[str, Any] | None = None,
    evaluation_note: str = "",
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

    `journal_table` is a `journals.load_journal_table` result, `thesis_roster` a
    `theses.load_thesis_roster` result, and `evaluation_table` an
    `evaluations.load_evaluation_table` result. All three are hand-filled local
    files, all three are optional, and none of them is ever fetched: there is no
    crawler in this package and there will not be one. Their `_note` companions
    play the same role `citations_note` does — Sections 17, 18 and 20 print the
    reason a table is absent rather than an empty cell, because a blank
    partition column, a blank graduate count and a blank evaluation list are
    each indistinguishable from a finding if nobody says which file was looked
    for.

    `journal_table=None` still produces Section 18: `join_journals` accepts a
    missing table by design and returns the corpus's own journal counts with
    `table_missing`, which is what the section needs to say how much work the
    lookup would be. `thesis_roster=None` produces the section with no counts at
    all, because there is nothing to count and inventing a zero denominator
    would be the exact fabrication that section exists to prevent.
    `evaluation_table=None` behaves like `thesis_roster=None` and for the same
    reason: an empty list of statements about a person and a list nobody
    collected look identical on the page unless one of them says so.

    `journal_risk` is the odd one in that list and is deliberately kept apart
    from it: it is the payload of a `journal_risk_<timestamp>.json` that the
    `journal-risk` verb fetched from three open keyless APIs, so it *is* fetched,
    unlike the three hand-filled tables. What it carries is statements with an
    endpoint and a date attached — never a grade, a tier or a verdict, and
    nothing derived from it reaches `score`. It renders as a block inside Section
    18 rather than a section of its own, because it qualifies the same journals
    the table above it does. `journal_risk=None` produces that block with
    `risk_missing` and the reason, exactly as a missing table does.
    """
    now = now or datetime.now()
    config = config or {}
    advisor = {**DEFAULT_ADVISOR_CONFIG, **(config.get("advisor") or {})}
    # Every field at the type it is read at, and a record of the ones the file
    # wrote at some other type. `{**identity, ...}` two lines below raised
    # `TypeError` on a block that was not a mapping, and `.strip()` / `len()` /
    # `list()` raised further down on a field that was not a string or a list —
    # all of them through `build_report` and out of `main()`, which has no
    # handler. `_readable_identity` says why none of this is a refusal.
    identity, identity_unreadable = _readable_identity(corpus.get("identity"))
    # The config value wins, because it carries `--pi-name` — the user saying
    # outright which person this report is about. The corpus's recorded name fills
    # the gap when there is none, which is what lets `compare` read several corpora
    # about different people without a name per directory.
    #
    # This is the precedence `cli._profile_corpus` already states and applies, and
    # having it backwards here made the two disagree on exactly one path. A corpus
    # file already in the Section 4 shape is passed to `build_report` untouched —
    # `cli.py` does that for any file with no `search` key — so `--pi-name` was
    # read, logged, and then silently discarded in favour of the name baked into
    # the file. The report then measured, warned and exited on a name the user had
    # not asked for: `profile --pi-name 'Chen Wei'` over a corpus recorded as
    # `Zhang Wei` printed "target name on records: 8 of 8 carry `Zhang Wei`",
    # raised no G7, and exited 0 — the same silence G7 exists to break, surviving
    # on the one path that skips `_profile_corpus`.
    author_name = config.get("author_name", "") or (identity.get("author_name") or "")
    # Every section below reads the name out of `identity`, not out of this local,
    # so resolving it here and leaving `identity` as the file wrote it splits the
    # report in two: the title, G7 and Section 1 are about the name that was asked
    # for, while the roster, the byline slots and every count under them stay about
    # the name the file records. That produces a page headed "Chen Wei", warning
    # that the name touched no record, over a Section 7 quietly reporting somebody
    # else's eight last-author slots — and the G7 banner's own sentences about
    # Sections 2 and 7 become false on the one path that reaches this branch.
    identity = {**identity, "author_name": author_name}

    gate = check_corpus_gates(corpus)
    if gate:
        return _refusal(gate, author_name, now)

    # Not a gate: the report is built, and every section that depends on the
    # corpus being complete, being one person, or being the person who was asked
    # for carries the warning at the top. The exit code is still 1, so nothing
    # that branched on a refusal changes behaviour.
    #
    # Name first, then coverage, then identity, which is the order a reader has
    # to settle them in: "is this the right person at all" (G7) precedes "is this
    # all of it" (G1), which precedes "is this one person" (G2, G3). A reader who
    # settles the last two first has spent the effort on a corpus that may be
    # about somebody else entirely.
    min_openalex_share = resolve_openalex_record_share(config)
    warnings = (
        check_name_warnings(corpus, author_name)
        + check_coverage_warnings(corpus)
        + check_identity_warnings(corpus, min_openalex_share)
    )

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
    # Always computed, like the join above and for the same reason: `join_risk`
    # treats a missing payload as a supported call and comes back with
    # `risk_missing` plus the journal list it would have annotated, which is what
    # the "go and collect these" line needs. Note what it is not wired into —
    # `computed`, `score` and `stars` are fixed above this point and no branch
    # below feeds a risk signal into any of them.
    risk = join_risk(journals, journal_risk)
    # Only computed when a roster exists. `reconcile_roster` would accept None
    # and report "the thesis roster is empty", which is true of the argument and
    # misleading about the world: no file was supplied, and that is a different
    # sentence from a file that supplied nobody.
    graduates = reconcile_roster(people_data, thesis_roster, author_name) \
        if thesis_roster is not None else None
    # Same rule as the roster above, for the same reason. `join_evaluations`
    # would accept None and report "no statement could be attributed", which is
    # true of the argument and misleading about the world: nobody went looking.
    # Note what this join does *not* touch — `computed`, `score` and `stars` are
    # already fixed by this point, and no branch below feeds an evaluation into
    # any of them.
    evaluations = join_evaluations(evaluation_table, author_name) \
        if evaluation_table is not None else None

    counts = corpus.get("counts") or {}
    # Counted over the harvested file, before the record exclusions, so it shares
    # a denominator with `counts["by_evidence"]` and `counts["by_source"]` rather
    # than with `corpus_size`. Recomputed here rather than read back out of the
    # G3 warning, because Section 1 prints the ratio and the boundary on every
    # run — including the runs where the warning does not fire, which are exactly
    # the runs where a reader wants to see how close it came.
    openalex_carried, openalex_of = openalex_id_record_share(
        corpus.get("papers"), identity.get("openalex_author_id", "")
    )
    # The same rule and the same denominator, for the fact none of the other
    # counts on this page carries: whether the name every section is written
    # about is on any of these records at all. Printed on every run, warning or
    # not, because a corpus the name reached 2 of 40 records of raises no warning
    # and is exactly the corpus a reader needs to see the ratio for.
    name_located, name_of = target_name_record_reach(corpus.get("papers"), author_name)
    # A `query` that is not a mapping is emptied, not repaired: every field
    # Section 1 takes from it already prints `?` when absent, and the block is
    # named on the "not recorded" line below beside the identity fields.
    query_raw = corpus.get("query")
    provenance = {
        "author_name": author_name,
        "query": _readable_block(query_raw),
        # Every block and field the file wrote at a type this report cannot read,
        # collected once and printed on one Section 1 line. Named `identity.x`
        # rather than `x` so a reader can find the key in the file; the whole
        # blocks keep their own names.
        "unreadable": {
            **{f"identity.{key}": raw for key, raw in identity_unreadable.items()
               if key != "identity"},
            **({"identity": identity_unreadable["identity"]}
               if "identity" in identity_unreadable else {}),
            **({"query": query_raw} if query_raw and not isinstance(query_raw, Mapping) else {}),
        },
        "identity": {
            "orcid": identity.get("orcid", ""),
            "affiliation_keywords": identity.get("affiliation_keywords", []),
            "email_domains": identity.get("email_domains", []),
            "require_affiliation_effective": identity.get("require_affiliation_effective", "unknown"),
            # Beside the three the user asserted, never folded into them: this
            # one is OpenAlex's clustering, and Section 1 prints where it came
            # from and when it was fetched.
            "openalex_author_id": identity.get("openalex_author_id", ""),
        },
        "counts": counts,
        "openalex_id_records": [openalex_carried, openalex_of],
        "target_name_records": [name_located, name_of],
        "min_openalex_record_share": min_openalex_share,
        "fallback_fired": bool(corpus.get("fallback_fired")),
        # Whether the line above is a record or a reconstruction. Absent from a
        # corpus dict assembled by hand, where "not stated" is the same class of
        # answer as "recorded" — neither is a guess this module made.
        "fallback_fired_inferred": bool(corpus.get("fallback_fired_inferred")),
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
        risk=risk, risk_note=journal_risk_note,
        impact_reference=impact_reference, impact_reference_note=impact_reference_note,
        graduates=graduates, thesis_note=thesis_note,
        evaluations=evaluations, evaluation_note=evaluation_note,
        warnings=warnings,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat(timespec="seconds"),
        "author_name": author_name,
        "refused": False,
        # Unchanged semantics for anything that branched on the old refusal: a
        # corpus that may hold several people still exits 1. What changed is
        # that the page it exits with is the whole report.
        "exit_code": 1 if warnings else 0,
        "warnings": warnings,
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
        # Beside `journals`, never inside it: one is a file the reader typed and
        # the other is what three open APIs said on a particular day, and a
        # consumer must not have to unpick which is which. Nothing reads either
        # of these back into a number.
        "journal_risk": risk,
        "journal_risk_note": (
            "" if journal_risk is not None else (journal_risk_note or _NO_JOURNAL_RISK_NOTE)
        ),
        "graduates": graduates,
        "thesis_note": "" if graduates is not None else (thesis_note or _NO_THESIS_ROSTER_NOTE),
        # Beside the metrics and outside them, like `graduates` and `journals`,
        # and one step further out than either: this key holds statements, not
        # measurements. Nothing reads it back into a number.
        "evaluations": evaluations,
        "evaluation_note": (
            "" if evaluations is not None else (evaluation_note or _NO_EVALUATION_TABLE_NOTE)
        ),
        "caveats": used_caveats,
        "sections": sections,
    }


def _refusal(gate: dict[str, Any], author_name: str, now: datetime) -> dict[str, Any]:
    """A fired gate produces the gate, the observed values and the fix. Nothing else.

    `score`, `stars`, `impact`, `journals`, `journal_risk`, `graduates` and
    `evaluations` are
    None here for the same reason every metric is absent: once a gate fires,
    every number the corpus could produce is wrong by an unbounded amount, and a
    composite of wrong numbers is the most confident wrong number of all. A star
    band is the worst of them — five characters that survive being copied out of
    a refused report with nothing attached. `evaluations` is close behind, for a
    different reason: a quotable sentence about a named person that outlived the
    page saying the report was refused. The keys exist so a consumer can read
    them without a guard; their value is None, never 0 and never an empty dict.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat(timespec="seconds"),
        "author_name": author_name,
        "refused": True,
        "exit_code": 1,
        # Present and empty, matching the success shape key for key: a consumer
        # reading `report["warnings"]` must not have to guess whether the report
        # was refused first.
        "warnings": [],
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
        "journal_risk": None,
        "journal_risk_note": "",
        "graduates": None,
        "thesis_note": "",
        "evaluations": None,
        "evaluation_note": "",
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
    journal_risk: Mapping[str, Any] | None = None,
    journal_risk_note: str = "",
    thesis_roster: Mapping[str, Any] | None = None,
    thesis_note: str = "",
    evaluation_table: Mapping[str, Any] | None = None,
    evaluation_note: str = "",
) -> dict[str, Any]:
    """Load a corpus file and build the report, refusing spreadsheets unopened."""
    gate = check_source_path(path)
    if gate:
        return _refusal(gate, (config or {}).get("author_name", ""), now or datetime.now())
    return build_report(
        load_corpus(path), config, gantt_path, now,
        citations=citations, citations_note=citations_note,
        journal_table=journal_table, journal_note=journal_note,
        journal_risk=journal_risk, journal_risk_note=journal_risk_note,
        thesis_roster=thesis_roster, thesis_note=thesis_note,
        evaluation_table=evaluation_table, evaluation_note=evaluation_note,
    )


# --- Sections ---


#: Chinese titles, printed alongside the English ones rather than instead of them.
#:
#: The report body is English and the command-line log is Chinese, which the
#: README has called an indefensible accident since round one. Translating the
#: body is a separate piece of work and a risky one: this report's value is in
#: the precision of sentences like "a paper with no percentile is not a low
#: percentile", and a translation that drifts from one of those cannot be caught
#: by any test here. So the headings are bilingual and the bodies are not — a
#: reader can navigate the report in Chinese, and every argued sentence is still
#: the sentence that was argued.
#:
#: Keyed by section id, so a renumbering fails loudly instead of silently
#: relabelling a section. `test_profile.py` holds this table to covering every id
#: `_build_sections` emits.
SECTION_TITLES_ZH: dict[int, str] = {
    0: "这份报告是什么，不是什么",
    1: "语料来源与出处",
    2: "人员与活动时间线",
    3: "一作名额",
    4: "拿到第一个一作要等多久",
    5: "观测到的活跃跨度",
    6: "组规模与人员流动",
    7: "PI 本人在署名里的位置",
    8: "共同一作标记",
    9: "年度记录数",
    10: "团队规模",
    11: "发表去向",
    12: "机构署名字符串",
    13: "逐年题名",
    14: "哪些东西是刻意不算的",
    15: "引用影响力",
    16: "综合分与星级",
    17: "在册毕业生 —— PubMed 看不见的那个分母",
    18: "期刊层面的指标",
    19: "共同作者簇 —— 这是同一个人吗",
    20: "学生评价 —— 第三方说法，原样印出",
}


def _titled(section_id: int, english: str) -> str:
    """One heading in both languages, Chinese first, English unchanged after it.

    English is kept verbatim rather than dropped: every cross-reference in this
    package, in SKILL.md and in four other modules names sections by their
    English title, and a heading that no longer contains it would break each of
    them silently.
    """
    chinese = SECTION_TITLES_ZH.get(section_id)
    return f"{chinese} / {english}" if chinese else english


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
    risk: dict[str, Any] | None = None,
    risk_note: str = "",
    impact_reference: Mapping[str, Any] | None = None,
    impact_reference_note: str = "",
    graduates: dict[str, Any] | None = None,
    thesis_note: str = "",
    evaluations: dict[str, Any] | None = None,
    evaluation_note: str = "",
    warnings: Sequence[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    def cav(caveat_id: str, **fields: Any) -> str:
        text = caveat(caveat_id, **fields)
        used[caveat_id] = text
        return text

    warnings = list(warnings or [])

    def warn(section_id: int) -> list[str]:
        """The warnings this section carries, in its own slot.

        Not caveats: those render at the *bottom* of a section behind a marker
        readers learn to skim. Not prose either, because prose is where a section
        explains itself and this is a statement that the section may be about the
        wrong person, or about part of the corpus. Both renderers put it first
        and above everything else.
        """
        return [_warning_line(item) for item in warnings if section_id in item["sections"]]

    query = prov["query"]
    years_back = query.get("years_back", "?")
    # CAV-01 is about what this corpus is *missing* — "matched the name, carried
    # no verifiable evidence, was excluded", stated in the caveat's own words as
    # an upper bound. That is `rejected`, the records the identity filter dropped;
    # it is an upper bound because the filter also drops name matches that hold no
    # first / last / corresponding slot. It used to be fed `counts["name_only"]`,
    # a key nothing produced, so the caveat read "0 further papers" on every run
    # ever printed. `name_only` counts records that are *in* the corpus, which is
    # the opposite of what this sentence says, and it is printed in Section 1's
    # evidence line instead.
    rejected = _readable_counts(prov["counts"]).get("rejected")
    rejected_text = "An unrecorded number of" if rejected is None else str(rejected)

    sections = [
        # Section 0 precedes provenance because the limits reframe every number
        # that follows.
        _section(0, _titled(0, "What this report is and is not"), prose=[cav("CAV-00")],
                 warnings=warn(0)),
        # Section 17, printed second. It is the only section whose denominator is
        # not conditioned on having published, so it is the only one that can
        # answer the question CAV-00 raises and then leaves open. Its number
        # sorts last because it was added last and the sections below are
        # cross-referenced by number from four other modules; its position on the
        # page is where it belongs. Both facts are stated in the section itself
        # rather than left to look like a mistake.
        _section(17, _titled(17, "Graduates on record — the denominator PubMed cannot see"),
                 body=_graduates_body(graduates, thesis_note),
                 prose=_graduates_prose()),
        # Printed third and numbered 19, for the same reason Section 17 is
        # printed second: it decides whether anything below it is about one
        # person. A reader who reaches the roster before reaching this has
        # already formed the belief this section exists to test.
        _section(19, _titled(19, "Co-author clusters — is this one person?"),
                 body=_cohesion_body(computed["s19"]),
                 prose=_cohesion_prose(), warnings=warn(19)),
        _section(1, _titled(1, "Corpus provenance"), body=_provenance_body(prov),
                 warnings=warn(1),
                 caveats=[cav("CAV-01", n=rejected_text),
                          cav("CAV-02", n_strict=prov["n_strict"], n_loose=prov["n_loose"])]),
        # Carries the name warning (G7). Its opening sentence is "after removing
        # the target researcher", and on a corpus the name never reached nobody
        # was removed — so this roster is one person too long and every count
        # over it is one person too high, which is exactly the section a reader
        # cannot detect that from.
        _section(2, _titled(2, "People and activity timeline"),
                 body=_roster_body(computed["s2"], gantt_path),
                 warnings=warn(2),
                 caveats=[used["CAV-02"], cav("CAV-03")]),
        _section(3, _titled(3, "First-author slots"),
                 body=_first_author_body(computed["s3a"], computed["s3b"]),
                 caveats=[cav("CAV-04"), cav("CAV-05"), cav("CAV-06")]),
        _section(4, _titled(4, "Time to a first-author slot"), body=_time_to_lead_body(computed["s4"]),
                 caveats=[cav("CAV-07"), cav("CAV-08")]),
        _section(5, _titled(5, "Observed activity span"),
                 body=_span_body(computed["s5"], prov["flips"]),
                 caveats=[cav("CAV-09"), cav("CAV-10", years_back=years_back), cav("CAV-11")]),
        _section(6, _titled(6, "Group size and turnover"), body=_turnover_body(computed["s6"]),
                 caveats=[cav("CAV-12")]),
        # Carries the name warning (G7) too, and more directly than any other
        # section: this one is about where the target sits in a byline, and a
        # name that reached no record has no slot on any of them. Unmeasured on a
        # role-filtered corpus and every record "unlocated" on an unfiltered one,
        # and neither of those two outcomes says which of "the filter was the
        # metric" and "the name is wrong" produced it.
        _section(7, _titled(7, "The PI's own byline position"), body=_pi_position_body(computed["s7"]),
                 warnings=warn(7),
                 caveats=(
                     [cav("CAV-13")] if not computed["s7"]["measured"] else []
                 ) + [
                     cav("CAV-14"),
                     cav("CAV-15",
                         covered=computed["s7"]["email_coverage"]["covered"],
                         total=computed["s7"]["email_coverage"]["denominator"]),
                 ]),
        _section(8, _titled(8, "Shared-authorship flags"), body=_equal_contrib_body(computed["s8"]),
                 caveats=[cav("CAV-16")]),
        # Carries the coverage warning (G1). A harvest that retrieved part of
        # what esearch matched deflates this section's numbers most visibly —
        # every bar is a floor — and a reader looking at a year-by-year count is
        # the reader most likely to read a dip as a fact about the lab.
        _section(9, _titled(9, "Records per year"), body=_records_body(computed["s9"]),
                 warnings=warn(9),
                 caveats=[cav("CAV-17"), cav("CAV-18")]),
        _section(10, _titled(10, "Team size"), body=_team_size_body(computed["s10"]), caveats=[cav("CAV-19")]),
        _section(11, _titled(11, "Venues"), body=_venue_body(computed["s11"]), caveats=[cav("CAV-20")]),
        _section(12, _titled(12, "Affiliation strings"), body=_affiliation_body(computed["s12"]),
                 caveats=[cav("CAV-21")]),
        _section(13, _titled(13, "Titles by year"), body=_titles_body(computed["s13"]), caveats=[cav("CAV-22")]),
        _section(14, _titled(14, "What was deliberately not computed"),
                 prose=_dropped_register_prose(warnings)),
        _section(15, _titled(15, "Citation impact"),
                 body=_impact_body(impact, citations_note, impact_reference, impact_reference_note),
                 prose=_IMPACT_PROSE),
        _section(16, _titled(16, "Composite score and star band"),
                 body=_score_body(score, stars),
                 prose=_score_prose()),
        _section(18, _titled(18, "Journal-level metrics"),
                 body=_journal_body(journals, journal_note, risk) + _risk_body(risk, risk_note),
                 prose=_journal_prose() + _risk_prose(),
                 caveats=[cav("CAV-33")]),
        # Last on the page and numbered 20, both for the same reason Section 17
        # explains about itself. Last because it is the only section that is not
        # a measurement at all: everything above is computed from records, and
        # this is a list of things other people said. A reader who meets it
        # first would carry it over the sections that can actually be checked.
        # Numbered 20 because the sections below are cross-referenced by number
        # from four other modules and renumbering would falsify all of them.
        _section(20, _titled(20, "Student evaluations — third-party statements, printed as given"),
                 body=_evaluations_body(evaluations, evaluation_note),
                 prose=_evaluations_prose()),
    ]
    return sections


def _warning_line(item: Mapping[str, Any]) -> str:
    """One warning as the bold line that opens an affected section.

    Everything the old refusal page carried is here — which condition, the
    observed values, the fix — in the order a reader needs it, and in bold so it
    is not read as another caveat. The last clause states the reversal in place,
    because a reader who has seen this tool refuse before is entitled to know
    that a rendered report is now the expected outcome rather than a bug.

    That clause is printed only for the conditions it is true of. G1, G2 and G3
    were gates and carry `downgraded`; G7 never was, and telling a reader it
    "used to refuse the whole report" would invent a history the register in
    Section 14 exists to keep straight. Both sentences end the same way, because
    the standing this warning has is the same either way.

    The closing clause names no specific defect. G1 says the corpus is partial,
    G2/G3 say it may be several people, G7 says it may be about nobody, and a
    sentence that asserted one of those over the others would be wrong on most of
    the warnings this function renders.
    """
    history = (
        "This condition used to refuse the whole report; it now renders in full and the "
        "process still exits 1."
        if item.get("downgraded", True) else
        "This condition has never refused a report: it is printed, the report renders in full, "
        "and the process exits 1."
    )
    return (
        f"**Warning {item['id']} ({item['name']}) — {item['message']}** "
        f"Observed: {item['observed_text'] or '(none)'}. "
        f"Fix: {item['fix']} "
        f"{history} Nothing below is certified: every number in this section was "
        f"computed over the corpus this warning describes."
    )


#: Decisions that were reversed, in the order they happened, kept because the
#: register's rule is that a reversal is recorded rather than deleted. Section 14
#: prints this on every run, warnings or not — a reader who only ever sees clean
#: corpora still has to be able to see what moved.
#:
#: `DROPPED_REGISTER` in `caveats.py` is the other half and answers a different
#: question: what was never computed. A line belongs here when a behaviour was
#: removed and then wholly or partly restored, which is exactly the case a
#: register that only records removals cannot represent.
REVERSALS: tuple[tuple[str, str], ...] = (
    (
        "Gate G1 (incomplete harvest) — removed, then restored as a warning",
        "It refused any corpus where esearch reported more records than came back. When the harvest "
        "learned to page, that condition stopped meaning \"cut off at retmax\" and started meaning "
        "one of three smaller things, so the gate was deleted outright and the shortfall became a "
        "printed line in Section 1. Deleting it went too far: a corpus 40% retrieved then rendered "
        "clean, exited 0, and sat on a `compare` page ranked beside corpora retrieved in full, with "
        "nothing on either page marking it as partial. G1 is back at warning strength — the report "
        "is still printed in full, the affected sections carry a banner, the exit code is 1 again, "
        "and the corpus takes no rank. It is not a gate and will not become one again.",
    ),
)


def _dropped_register_prose(warnings: Sequence[Mapping[str, Any]]) -> list[str]:
    """Section 14: the standing register, the reversals, then this run's warnings.

    The per-run half exists because the register's rule is that a reversal is
    recorded rather than deleted, and a reversal that only appears in the source
    is not recorded anywhere a reader can see. A run with no warnings prints the
    register alone and nothing about warnings, which is the truth about that run.

    A warning that was never a gate is listed under its own heading. Filing G7
    under "downgraded" would say it used to refuse reports, which it never did —
    this register is the one place a reader can check that claim, so it is the
    last place that may guess at it.
    """
    lines = [f"- **{name}** — {reason}" for name, reason in DROPPED_REGISTER]
    lines += [
        "",
        "**Reversed decisions — behaviour that was removed and then restored:**",
        "",
    ]
    lines += [f"- **{name}** — {reason}" for name, reason in REVERSALS]
    if not warnings:
        return lines
    downgraded = [item for item in warnings if item.get("downgraded", True)]
    never_gates = [item for item in warnings if not item.get("downgraded", True)]
    if downgraded:
        lines += [
            "",
            "**Downgraded on this run — what was refused before and is printed instead:**",
            "",
        ]
        lines += [
            f"- **{item['id']} ({item['name']})** — this used to refuse the whole report. It is "
            f"now a warning at the top of Sections "
            f"{', '.join(str(section) for section in item['sections'])}, the report below was "
            f"built anyway, and the process exit code is still 1. Observed: "
            f"{item['observed_text'] or '(none)'}. Nothing in this report was suppressed to "
            f"accommodate it, so every count below is computed over a corpus whose identity is "
            f"unconfirmed and should be read that way."
            for item in downgraded
        ]
    if never_gates:
        lines += [
            "",
            "**Raised on this run — conditions that were never gates:**",
            "",
        ]
        lines += [
            f"- **{item['id']} ({item['name']})** — this has never refused a report. It is a "
            f"warning at the top of Sections "
            f"{', '.join(str(section) for section in item['sections'])}, the report below was "
            f"built anyway, and the process exit code is 1. Observed: "
            f"{item['observed_text'] or '(none)'}. Nothing in this report was suppressed to "
            f"accommodate it, so every count below is computed over the corpus this warning "
            f"describes and should be read that way."
            for item in never_gates
        ]
    return lines


def _coverage_lines(query: Mapping[str, Any]) -> list[str]:
    """取回 N / 共 M —— 这一行取代了旧的 G1 门禁。

    N 是 harvest 实际拿到手的 PMID 数，M 是 esearch 报告的命中总数。两个数都
    印出来，是因为下面每一个计数的分母都是 N，而读者关心的问题是 N 离 M 有多
    远。翻页之后 N < M 有三种成因——`max_records` 预算截停、页与页之间的重复
    PMID、PubMed 自己的 Count 在翻页途中漂移——所以这里只报数，不替读者断言
    是哪一种。

    缺口存在时多印一句，把「下面的计数是下界而不是值」说明白：一份取回 500 /
    共 900 的语料，它的每一个计数都不是错的，只是不完整，而这两件事需要读者
    自己分得清。

    补救办法分两种，因为成因分两种。命中数没有超过 esearch 的 10000 条硬顶
    时，卡住取数的是本工具的 max_records 预算，调高它就有用。命中数超过硬顶
    时，调高 max_records 一条都多取不到——NCBI 只把前 10000 条交出来，翻页翻
    到 10001 条只会拿到空页。以前这一句在两种情况下都把「Raise max_records」
    排在第一位，于是缺口最大的那份报告，给的第一个动作恰好是唯一无效的那个。

    判定和文案都不在这里，在 `pubmed_api.coverage_remedy`：采集日志要在这份
    报告写出来之前的二十分钟就说同一件事，两处各写一份的下场已经见过一次了。
    """
    count = query.get("esearch_count", "?")
    returned = query.get("pmids_returned", "?")
    budget = query.get("max_records", "?")
    lines = [
        f"- PubMed corpus coverage: retrieved {returned} of {count} records esearch matched "
        f"(esearch pages fetched {query.get('pages_fetched', '?')}; page size retmax="
        f"{query.get('retmax', '?')}; budget max_records={budget}; "
        f"repeated PMIDs dropped across pages {query.get('duplicates_dropped', '?')})",
    ]
    if isinstance(count, int) and isinstance(returned, int) and returned < count:
        lines.append(
            f"- **{count - returned} of those {count} records were never retrieved.** Every count "
            f"below is computed over the {returned} that were, and is a floor rather than a value. "
            + coverage_remedy(count, budget, "en")
        )
    return lines


def _source_lines(counts: Mapping[str, Any], corpus_size: int) -> list[str]:
    """Which bibliographic source each record came from, and which confirmed it.

    Absent from a PubMed-only harvest, and that absence is printed as a sentence
    rather than as `openalex 0`: a corpus that was never merged and a corpus
    where the merge found nothing are different, and only the second says
    anything about the author. When there was a merge, both denominators are
    stated — the merged corpus and the PubMed-only corpus a reader may be
    comparing against from an earlier run.

    The three source counts are taken over the harvested file, before the record
    exclusions in this same section are applied, so their total is stated as the
    harvested total and `corpus_size` is named separately. Printing the
    post-exclusion size as the sum of three pre-exclusion counts would be an
    equation that does not add up on any corpus with a single excluded record.

    `by_source` is read out of a JSON file some earlier run wrote and is checked
    with `_is_count_histogram`, the same rule `by_evidence` follows one line
    below in `_evidence_lines`. The two were added in the same change and only
    one of them was guarded: `int()` on a string raised `ValueError` and `.get`
    on a string raised `AttributeError`, both of them out through `build_report`
    and out of `main()`, which has no handler. `cli.py`'s profile path hands a
    corpus file's own `counts` block straight to this function whenever the file
    carries no `search` key, so both were one command line away. An unreadable
    histogram prints "not recorded" with the value quoted back, and nothing is
    summed.

    The total is `sum()` over every bucket rather than `pubmed + openalex + both`.
    Those three are the names the merge writes, but a file naming a fourth source
    used to have it silently dropped from an equation that then still printed as
    if it balanced — `0 harvested record(s) = 0 + 0 + 0` over a file that said
    six. Unknown buckets are added to the total and named in the line.
    """
    by_source = counts.get("by_source")
    if by_source is not None and not _is_count_histogram(by_source):
        return [
            f"- corpus sources: not recorded — this corpus file carries by_source={by_source!r}, "
            f"which is not a mapping of source name to whole-number count, so neither the split "
            f"nor the harvested total can be stated and nothing was assumed in their place. The "
            f"{corpus_size} record(s) every count below is over are unaffected: they are counted "
            f"from the records themselves, not from this block. Re-harvest to rewrite the file."
        ]
    by_source = by_source or {}
    if not by_source:
        return [
            "- corpus sources: PubMed only. No second source was merged into this corpus, so every "
            "denominator below is the PubMed corpus. (`harvest --openalex-works` adds OpenAlex as a "
            "second source and prints both denominators here.)"
        ]
    pubmed_only = by_source.get("pubmed", 0)
    openalex_only = by_source.get("openalex", 0)
    both = by_source.get("both", 0)
    harvested = sum(by_source.values())
    other = {key: value for key, value in sorted(by_source.items())
             if key not in ("pubmed", "openalex", "both")}
    unknown = (
        f" + {sum(other.values())} under source name(s) the merge does not write "
        f"({', '.join(f'{key} {value}' for key, value in other.items())})"
    ) if other else ""
    return [
        f"- corpus sources: {harvested} harvested record(s) = {pubmed_only} PubMed only + "
        f"{openalex_only} OpenAlex only + {both} held by both{unknown}",
        f"- the two denominators are different numbers and both are stated: the PubMed corpus is "
        f"{pubmed_only + both} record(s), the merged corpus is {harvested}. The record exclusions "
        f"listed below apply to both, and every count elsewhere in this report is over the "
        f"{corpus_size} record(s) that survived them.",
    ]


def _openalex_lines(query: Mapping[str, Any], identity: Mapping[str, Any]) -> list[str]:
    """What OpenAlex was asked, what it answered, and when.

    Printed separately from the identity line above it because the two are
    different kinds of claim. `orcid`, `affiliation_keywords` and `email_domains`
    are the user's own assertions about who this is. An OpenAlex author id is one
    database's clustering decision, and it is only usable as evidence if a reader
    can see that it came from there, on what query, on what date — so all three
    travel with it and none is elided when the block is absent.

    Every one of the five nested blocks here is read out of the same JSON file
    the `counts` guards were written for, and none of them was checked: `or {}`
    covers `None` and lets `"unique"` and `[1, 2]` through to `.get`, `or []`
    lets a string through to a loop that then calls `.get` on single characters,
    and `len()` on either raised before that. Each unreadable block now prints
    one line naming what the file held, in place of the line whose numbers it
    would have supplied — the rule `_source_lines` follows for `by_source`. The
    author id above them is unaffected: it comes from `identity`, and a corpus
    whose resolution block is a string still knows which id it was harvested
    under.
    """
    resolution_raw = query.get("openalex")
    works_raw = query.get("openalex_works")
    resolution = _readable_block(resolution_raw)
    works = _readable_block(works_raw)
    unreadable = [
        f"- openalex {name}: not recorded — this corpus file carries query.{key}={raw!r} where the "
        f"block `harvest {flag}` writes belongs, so nothing was read out of it and nothing was "
        f"assumed in its place. Re-harvest to rewrite the file."
        for name, key, flag, raw in (
            ("author resolution", "openalex", "--resolve-openalex", resolution_raw),
            ("works lookup", "openalex_works", "--openalex-works", works_raw),
        )
        if raw and not isinstance(raw, Mapping)
    ]
    author_id = (identity.get("openalex_author_id") or "").strip()
    if not resolution and not works and not author_id:
        return unreadable

    lines = unreadable + [
        f"- openalex author id in use: {author_id or '(none)'} — this is OpenAlex's own author "
        f"clustering, not the researcher's assertion, and it is recorded beside the identity line "
        f"above rather than folded into it"
    ]
    if resolution:
        # The count in the sentence below is `len(candidates)`, so an unreadable
        # list is not silently filtered down to the entries that happened to be
        # mappings: that would print a count of what survived under a sentence
        # about what OpenAlex returned. It is emptied, named, and the per-candidate
        # lines are not written at all.
        candidates_raw = resolution.get("candidates")
        readable = _is_record_list(candidates_raw)
        candidates = candidates_raw if readable else []
        if candidates_raw and not readable:
            lines.append(
                f"- openalex candidates: not recorded — this corpus file carries "
                f"candidates={candidates_raw!r}, which is not a list of candidate records, so "
                f"neither how many there were nor which they were can be stated and nothing was "
                f"assumed in their place. Re-harvest to rewrite the file."
            )
        # `?` rather than 0 when the list is unreadable: this line's own count
        # would otherwise contradict the line directly above it, and a printed 0
        # is a claim that OpenAlex returned nothing.
        counted = len(candidates) if readable or not candidates_raw else "?"
        lines.append(
            f"- openalex author resolution: {resolution.get('resolution', '?')} "
            f"({counted} candidate(s)); query `{resolution.get('query', '')}`; "
            f"source {resolution.get('source', '?')}; retrieved "
            f"{resolution.get('retrieved_at') or 'not recorded'}"
        )
        if resolution.get("resolution") == "unique":
            lines.append(
                "- **this id is the sole candidate of a fuzzy `display_name.search` and was adopted "
                "without confirmation.** One candidate means the name search returned one profile, "
                "not that OpenAlex verified who this is: a same-named stranger with a single "
                "profile looks exactly like this, and so does the right person split across several "
                "profiles where only one matched. Check the institution and works count printed "
                "above against the researcher you meant, and re-harvest with "
                "`--openalex-author-id <id>` if they do not agree."
            )
        if resolution.get("resolution") == "ambiguous":
            lines.append(
                "- **the name resolved to more than one OpenAlex author and none was adopted.** "
                "The candidates are listed below; re-harvest with "
                "`--openalex-author-id <id>` once you recognise the right one. Nothing was picked "
                "automatically, because picking the most productive candidate decides an identity "
                "question on a proxy."
            )
        for index, candidate in enumerate(candidates, 1):
            # Same all-or-nothing rule one level down. `(inst or {})` covered
            # `None` and handed every other non-mapping entry to `.get`, and the
            # comprehension itself raised on a value that was not iterable at
            # all. An unusable list prints "not recorded" in the slot rather than
            # an empty string, which would read as an author with no affiliation.
            institutions_raw = candidate.get("institutions")
            institutions = (", ".join(
                str(inst.get("display_name", "")) for inst in institutions_raw
            ) if _is_record_list(institutions_raw) else "") or "not recorded"
            lines.append(
                f"  - candidate {index}/{counted}: {candidate.get('display_name', '')} "
                f"(id {candidate.get('openalex_author_id', '?')}; orcid "
                f"{candidate.get('orcid') or '(none)'}; works {candidate.get('works_count', '?')}; "
                f"institutions {institutions})"
            )
    if works:
        if not works.get("merged"):
            lines.append(
                f"- openalex works: requested but not merged — {works.get('reason', 'unstated')}"
            )
        elif works.get("request_failed"):
            # Distinguished from a completed lookup that found nothing, because
            # "retrieved 0 of 0" reads as a fact about the author and this is a
            # fact about the request.
            lines.append(
                f"- **the OpenAlex works lookup failed after {works.get('pages_fetched', '?')} "
                f"page(s), so only {works.get('works_returned', '?')} of this author's works "
                f"reached the merge.** That is a failed request, not an author with no works: the "
                f"OpenAlex side of every count below is a floor of unknown depth. Re-run the "
                f"harvest to close it."
            )
        else:
            lines.append(
                f"- openalex works coverage: retrieved {works.get('works_returned', '?')} of "
                f"{works.get('works_matched', '?')} works OpenAlex files under this author id "
                f"(pages {works.get('pages_fetched', '?')}; budget max_works="
                f"{works.get('max_works', '?')}); {works.get('works_usable', '?')} usable after "
                f"dropping {works.get('works_without_lead_slot', '?')} where this author holds no "
                f"first / last / corresponding slot"
            )
            # `or {}` again, and again it only covered `None`. Every field under
            # it already prints `?` when absent, so an unreadable block prints
            # three `?` and the sentence stays true — there is no number here to
            # fabricate.
            matched = _readable_block(works.get("matched_on"))
            lines.append(
                f"- cross-source confirmations — an OpenAlex work landing on a record PubMed also "
                f"holds, on a key PubMed itself published: DOI {matched.get('doi', '?')}, PMID "
                f"{matched.get('pmid', '?')}, title+year {matched.get('title_year', '?')} "
                f"(strongest key first; title+year is the only one that can be wrong). These are "
                f"the only hits where two independent databases agree the paper exists."
            )
            internal = works.get("openalex_internal_duplicates")
            if isinstance(internal, Mapping):
                lines.append(
                    f"- OpenAlex records deduplicated against each other — one database listing one "
                    f"paper twice, confirming nothing: DOI {internal.get('doi', '?')}, PMID "
                    f"{internal.get('pmid', '?')}, title+year {internal.get('title_year', '?')}. "
                    f"This bucket also holds the near-miss: an OpenAlex work that reaches a PubMed "
                    f"record only through a DOI another OpenAlex work donated. The paper is still "
                    f"counted once, but PubMed never published that key, so nothing about the hit "
                    f"is cross-source. Counted separately from the line above because adding the "
                    f"two together prints same-source dedup as cross-source agreement."
                )
    return lines


def _is_count(value: Any) -> bool:
    """A whole number this module is allowed to add. `True` is not one.

    `bool` subclasses `int`, so without the second half `name_only: true` would
    print as `name_only 1` and go into a denominator — a typo rendered as a
    measurement, which is worse than the "not recorded" it would displace.
    """
    return isinstance(value, int) and not isinstance(value, bool)


def _is_count_histogram(value: Any) -> bool:
    """The shape `counts["by_evidence"]` and `counts["by_source"]` promise: name -> count.

    Keys are checked as well as values because these histograms are `sorted()`
    before they are printed, and a mapping with mixed key types raises there
    rather than in the sum. One rule covers both.
    """
    return isinstance(value, Mapping) and all(
        isinstance(key, str) and _is_count(count) for key, count in value.items()
    )


def _is_inconsistency_record(value: Any) -> bool:
    """The shape `counts["inconsistent"]` promises: all three of `G6_OPERANDS` as
    whole numbers.

    Presence *and* type, because both halves have their own failure. A missing
    operand raised `KeyError` inside `str.format`; a present but unreadable one
    (`None`, `"thirty"`, `[30]`) formatted straight into the gate's sentence, so
    the refusal page said "records verified=None against fetched=None" — a
    non-number printed where the whole point of the sentence is the arithmetic
    between two numbers.

    `rejected_would_be` is negative by construction — it is `fetched - verified`
    on a corpus where `verified` is the larger — so `_is_count` is the right rule
    here: it rejects `bool` and non-integers and says nothing about sign. Extra
    keys beside the three are allowed and left visible; they are not read.
    """
    return isinstance(value, Mapping) and all(_is_count(value.get(key)) for key in G6_OPERANDS)


def _is_text(value: Any) -> bool:
    """A string this module is allowed to `.strip()`, `.lower()` or match against.

    The same rule `_is_count` states for numbers, for the fields that are not
    numbers. `identity["orcid"]`, `identity["openalex_author_id"]` and
    `identity["author_name"]` all arrive out of a JSON file and were all read as
    `(value or "").strip()`, which turns `None` into a string and hands `3.5`,
    `True` and `["0000-…"]` straight to `.strip` — `AttributeError` through
    `build_report` and out of `main()`, which has no handler.

    `bool` is excluded for the reason `_is_count` excludes it, mirrored: `True`
    is not an ORCID, and `orcid: true` printed as `orcid=True` would be a typo
    rendered as an assertion about a person's identity.
    """
    return isinstance(value, str)


def _is_text_list(value: Any) -> bool:
    """The shape `affiliation_keywords` and `email_domains` promise: a list of strings.

    All-or-nothing, the rule `_is_count_histogram` follows: one unusable entry
    makes the list unusable rather than being dropped out of it. Dropping would
    change how many keywords the identity filter was told to look for while the
    page went on printing the original count — a denominator quietly edited to
    match a defect.

    Elements are checked because they reach `pubmed_api._affiliation_matches`
    and `_email_domain_matches`, which call `.lower()` on each one; a list
    holding a single integer raised `AttributeError` from inside the matcher,
    two modules away from the file that carried it.
    """
    return isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value)


def _is_record_list(value: Any) -> bool:
    """The shape `query["openalex"]["candidates"]` and a candidate's `institutions`
    promise: a list of mappings.

    Same all-or-nothing rule as `_is_text_list`, and here it also protects a
    printed number: the candidate list's length is rendered as
    "N candidate(s)", so silently dropping the entries that are not mappings
    would print a count of what survived under a sentence about what OpenAlex
    returned. A string is not a list of mappings even though it is a sequence —
    `"unique"` under this key used to iterate into single characters and reach
    `.get` on each of them.
    """
    return isinstance(value, (list, tuple)) and all(isinstance(item, Mapping) for item in value)


def _readable_block(value: Any) -> Mapping[str, Any]:
    """A nested block read out of the corpus file, or an empty one.

    `_readable_counts` for every other mapping the report layer reads: `query`,
    `query["openalex"]`, `query["openalex_works"]`, a works block's `matched_on`,
    one candidate, one institution. All of them were read as `(...) or {}`, which
    covers `None` and lets every truthy non-mapping through to `.get`.

    Emptied rather than repaired, and the caller names what the file held on the
    line that would have carried the numbers. There is no fabrication in an empty
    block: every field under it already prints `?` or "not recorded" when absent.
    """
    return value if isinstance(value, Mapping) else {}


#: The `identity` fields read as strings, and the fields read as lists of strings.
#:
#: Listed here rather than at each read site for the reason `G6_OPERANDS` is listed
#: once: `orcid` is read in four places — the G3 warning, the evidence reach count,
#: Section 1's identity line and the roles matcher — and a guard added at three of
#: them is the "adjacent field, only one plugged" failure this block exists to end.
IDENTITY_TEXT_FIELDS: tuple[str, ...] = ("orcid", "openalex_author_id", "author_name")
IDENTITY_TEXT_LIST_FIELDS: tuple[str, ...] = ("affiliation_keywords", "email_domains")


def _readable_identity(value: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """`(the identity block at the types it is read at, the fields that were not)`.

    Sanitised once and passed down, rather than guarded at each of the dozen
    reads: `orcid` alone reaches `.strip().lower()` in `identity_evidence_record_reach`,
    `.strip()` in `roles.evidence_tier`, `or '(none)'` in Section 1's identity
    line and `_flat_observed` in the G3 banner, and `email_domains` reaches
    `list()` in one place and `len()` in another. A float under either key
    raised from whichever ran first.

    A field that is *absent* is not a defect and is not reported as one: `None`
    is what every one of those readers already meant by "not configured", and it
    is coerced to `""` / `[]` silently. A field that is *present and unusable* is
    a different fact and is returned in the second mapping, so the caller can
    print it back. Which matters for exactly one decision: G3's three situations
    are told apart by whether anything was configured, and an unreadable ORCID
    emptied in silence would pick "No identity evidence was configured for this
    harvest" — a sentence about the user's config that is false about a file
    carrying `orcid: 3.5`.

    A whole block that is not a mapping is emptied the same way `_readable_counts`
    empties `counts`, and reported under its own name. It is not a refusal: an
    identity block that says nothing usable leaves every count on the page
    exactly as measured and costs only the identity claim, which is what G3
    already exists to raise. Refusing would discard twenty sections of true
    counts over a field none of them is computed from — the trade the G1/G2/G3
    downgrade was made to stop making.
    """
    if not isinstance(value, Mapping):
        return {}, ({"identity": value} if value else {})
    clean = dict(value)
    unreadable: dict[str, Any] = {}
    for field in IDENTITY_TEXT_FIELDS + IDENTITY_TEXT_LIST_FIELDS:
        if field not in clean:
            continue
        is_list = field in IDENTITY_TEXT_LIST_FIELDS
        if (_is_text_list if is_list else _is_text)(clean[field]):
            continue
        if clean[field] is not None:
            unreadable[field] = clean[field]
        clean[field] = [] if is_list else ""
    return clean, unreadable


def _unreadable_lines(unreadable: Mapping[str, Any]) -> list[str]:
    """The one Section 1 line naming the blocks and fields that were not usable.

    One line for all of them, the rule `_provenance_body` already applies to
    `counts`: a file whose `identity` is a string has one defect, and printing it
    once per reader would read as several. The values are quoted with `repr` and
    not summarised — `_flat_observed` renders a list as its length, which would
    print `affiliation_keywords=2` over a file holding `[1, 2]` and turn the
    defect into a plausible measurement.
    """
    if not unreadable:
        return []
    held = ", ".join(f"{key}={value!r}" for key, value in sorted(unreadable.items()))
    return [
        f"- corpus fields not recorded — this corpus file carries {held}, and none of those is the "
        f"shape this report reads that key at, so nothing was taken from them and nothing was "
        f"assumed in their place. The records themselves are unaffected and every count below is "
        f"over them. An identity field named here reached no record by definition, which is what "
        f"the identity warning above reports. Re-harvest to rewrite the file."
    ]


def _readable_counts(value: Any) -> Mapping[str, Any]:
    """The corpus's `counts` block, or an empty one when the file wrote something else.

    `counts` arrives out of a JSON file and is not trusted to be a mapping, for
    the same reason its individual fields are not trusted to be numbers. Every
    reader of it here did `(...) or {}`, which turns `None` into a mapping and
    leaves `"six"` and `[1, 2]` to reach `.get` and raise `AttributeError` —
    through `build_report`, out of `main()`, which has no handler. `cli.py`'s
    profile path passes a corpus file's own `counts` block straight through when
    the file carries no `search` key, so that is a command line away.

    An unreadable block is emptied rather than repaired, and `_provenance_body`
    prints one line naming what the file actually held. Emptying it silently
    would be the failure this whole provenance block exists to prevent: every
    count derived from it would read as "absent", which on this page is a
    statement about the harvest rather than about the file.
    """
    return value if isinstance(value, Mapping) else {}


def _target_name_lines(prov: Mapping[str, Any]) -> list[str]:
    """The one line that says whether the target name is on these records at all.

    Kept beside the two ratios it has to be read against — `identity evidence on
    records` and `openalex author id on records` — because those two are computed
    without consulting the name, and a reader comparing them needs all three over
    one denominator.

    No boundary is printed because none is applied: G7 fires at zero and nothing
    else, and a threshold stated on the page that nothing tests against would be
    worse than no threshold at all. The ratio is what a reader acts on when the
    name reached some but not most of the corpus.
    """
    located, of_records = prov.get("target_name_records") or (0, 0)
    name = (prov.get("author_name") or "").strip()
    subject = f"`{name}`" if name else "(no target name was configured)"
    tail = (
        " Warning G7 is raised at zero. This is the only count in this section that looks at the "
        "name at all: the two lines below it are derived from role strings and author ids, so "
        "they read the same whether the name on the command line was the right one or a typo."
    ) if not located else (
        " Counted through the same matcher `roles.resolve_pi` builds its candidate list with, so "
        "this is the number of records that could have a located PI, over the harvested file."
    )
    return [
        f"- target name on records: {located} of {of_records} harvested record(s) carry "
        f"{subject} on a byline." + tail
    ]


def _evidence_lines(prov: Mapping[str, Any]) -> list[str]:
    """Whether the target name is here at all, then how strong the evidence is.

    The name line is printed first and on every run, because it is the question
    the two lines below it presuppose. Both of them count records without ever
    looking at the name — `by_evidence` is derived from the role string and the
    OpenAlex share is a record-level id count — so on a corpus profiled under a
    mistyped name they went on reporting `8 of 8` while no section below was
    about anybody. Warning G7 fires at zero; the ratio is printed either way, so
    a corpus the name reached a minority of is visible without this line having
    to assert a boundary it has no basis for.

    One denominator, stated: `by_evidence` and `name_only` are both counted over
    the harvested file, so they add up to it and a reader can check them against
    each other. The name line shares that denominator too, and deliberately —
    three ratios in one block are only comparable if they are over the same set.
    `verified` above is a PubMed-side count and does not belong in
    this sum — which is precisely why `name_only` used to be printed on that
    line, always as a hard-coded 0, while the tier histogram beside it described
    a different set of records.

    `name_only` absent means the corpus holds records whose role predates the
    evidence markers. That prints "not recorded", never 0: "no record was a bare
    name match" and "nobody wrote down which records were" are different facts,
    and a 0 in the second case is the more reassuring of the two.

    The second line exists only when an OpenAlex author id was configured, and
    carries the boundary that decides warning G3 next to the ratio it is compared
    against. It is printed whether or not G3 fired, because a corpus that cleared
    the boundary by one record is a corpus a reader should see the margin on.

    Both halves are read out of a JSON file some earlier run wrote, so neither is
    trusted to be the type it should be. A histogram that is not a mapping of
    counts, or a zero bucket that is not an integer, is *unreadable* rather than
    empty: it takes the "not recorded" branch, with its own reason, instead of
    being summed. `sum()` and `+` on a string used to raise from here — through
    `build_report`, out of `main()`, which has no handler — so one bad field in
    one file replaced the whole report with a traceback. That is the same rule
    `_coverage_lines` follows on its own subtraction, and the same one
    `_corpus_counts` follows when `fetched` and `verified` contradict each other.
    """
    counts = _readable_counts(prov["counts"])
    by_evidence = counts.get("by_evidence")
    name_only = counts.get("name_only")
    unreadable = []
    if by_evidence is not None and not _is_count_histogram(by_evidence):
        unreadable.append(f"by_evidence={by_evidence!r}")
        by_evidence = None
    if name_only is not None and not _is_count(name_only):
        unreadable.append(f"name_only={name_only!r}")
        name_only = None
    by_evidence = by_evidence or {}
    tiers = ", ".join(f"{tier} {value}" for tier, value in sorted(by_evidence.items()))
    with_evidence = sum(by_evidence.values())
    if unreadable:
        lines = [
            "- identity evidence on records: not recorded — this corpus file carries "
            + " and ".join(unreadable)
            + ", which is not a number this line can add up, so neither the numerator nor the "
            "denominator can be stated. Nothing was assumed in their place. Re-harvest to rewrite "
            "the file."
            + (f" Records that do carry a marker: {tiers}." if tiers else "")
        ]
    elif name_only is None:
        lines = [
            "- identity evidence on records: not recorded — this corpus holds records whose role "
            "string predates the evidence markers, so a record that carried no evidence cannot be "
            "told apart from one whose evidence was never written down. Re-harvest to settle it."
            + (f" Records that do carry a marker: {tiers}." if tiers else "")
        ]
    else:
        lines = [
            f"- identity evidence on records: {with_evidence} of {with_evidence + name_only} "
            f"harvested record(s) carry identity evidence, strongest first — "
            f"{tiers or 'none'}; name_only {name_only} (the name matched and nothing else did)"
        ]
    lines = _target_name_lines(prov) + lines
    openalex_id = prov["identity"].get("openalex_author_id") or ""
    if openalex_id:
        carried, of_records = prov.get("openalex_id_records") or (0, 0)
        minimum = prov.get("min_openalex_record_share", MIN_OPENALEX_RECORD_SHARE)
        share = (carried / of_records) if of_records else 0.0
        lines.append(
            f"- openalex author id on records: {carried} of {of_records} "
            f"(share {share:.2f}). An id on its own stands in for orcid / email_domains / "
            f"affiliation_keywords only at or above min_openalex_record_share = {minimum:.2f}; "
            f"below it warning G3 is raised. The boundary is a stated convention, not a "
            f"measurement — change it under `identity_evidence.min_openalex_record_share` and "
            f"this line prints whatever you set."
        )
    return lines


def _provenance_body(prov: dict[str, Any]) -> list[str]:
    # `build_report` already empties a `query` that is not a mapping, but this
    # function is also called with a provenance dict assembled by hand and the
    # guard costs a `isinstance`. Same rule `_readable_counts` follows one line
    # below, and for the same reason.
    query = _readable_block(prov["query"])
    # Sanitised here too, for the same reason: `build_report` stores a block
    # whose fields are already at the right types, but this function also renders
    # a provenance dict assembled by hand, and `len()` on a `None` and `.strip()`
    # on a float are the two crashes this line used to be.
    identity, _ = _readable_identity(prov.get("identity"))
    counts = _readable_counts(prov["counts"])
    # Named once, here, rather than in each of the three helpers that read the
    # block: a file whose `counts` is a string has one defect, and printing it
    # three times would read as three.
    block_unreadable = [
        f"- provenance counts: not recorded — this corpus file carries "
        f"counts={prov['counts']!r} where a mapping of count names to numbers belongs, so every "
        f"number this section takes from that block is stated as unrecorded below rather than "
        f"assumed. The records themselves are unaffected. Re-harvest to rewrite the file."
    ] if prov["counts"] and not isinstance(prov["counts"], Mapping) else []
    # The one key under `counts` whose absence from the page would be a claim.
    # A mapping here is gate G6 and this report was never built; a non-mapping
    # fires nothing, because `_corpus_counts` writes a mapping and only a mapping
    # and the meaning of anything else is unknown. Unknown is not the same as
    # absent, so it is named rather than dropped: a reader who put something
    # under this key would otherwise get a clean page and no sign it was ignored.
    inconsistent = counts.get("inconsistent")
    inconsistency_unreadable = [
        f"- recorded count inconsistency: not recorded — this corpus file carries "
        f"counts.inconsistent={inconsistent!r}, which is not the mapping of `fetched`, `verified` "
        f"and `rejected_would_be` that `harvest` writes there, so nothing was read out of it and "
        f"gate G6 was neither raised nor cleared on its strength. The counts printed on this line "
        f"and below come from the other keys in the same block. Re-harvest to rewrite the file."
    ] if inconsistent and not isinstance(inconsistent, Mapping) else []
    lines = [
        f"- esearch term: `{query.get('term', '')}`",
        f"- date range: {query.get('mindate', '?')} to {query.get('maxdate', '?')} "
        f"(years_back={query.get('years_back', '?')})",
        *block_unreadable,
        *inconsistency_unreadable,
        *_unreadable_lines(_readable_block(prov.get("unreadable"))),
        *_coverage_lines(query),
        *_source_lines(counts, prov["corpus_size"]),
        *_openalex_lines(query, identity),
        # All three are PubMed-side counts and say so, because the merged corpus
        # is a different and larger number and the two were once printed under
        # one name — which is how `verified 30` appeared over `fetched 10`.
        # `name_only` used to sit in this line too and never belonged: it counts
        # harvested records, not PubMed-stage outcomes, and it is printed on the
        # evidence line below with the denominator it actually shares.
        #
        # Printed as `kept`, not as `verified`, because the middle number is
        # `len(matched_papers)` — every record `is_first_or_corresponding`
        # returned True for. Under `require_affiliation=false` that includes a
        # bare name match, stamped `[机构未验证⚠]` and counted as `name_only` on
        # the evidence line directly below. So a loose-mode corpus printed
        # `verified 6` one line above `name_only 4`, and the two lines denied
        # each other over the same records under a word that claimed more than
        # the filter checked. The key in the corpus file is untouched — renaming
        # it would strand every file already written — so this line names both.
        f"- PubMed records: fetched {counts.get('fetched', '?')} / kept "
        f"{counts.get('verified', '?')} / rejected {counts.get('rejected', '?')} "
        f"(fetched = parsed out of efetch; kept = the target name held a first / corresponding / "
        f"last-author slot and the record cleared the identity filter — with "
        f"require_affiliation=false that filter also passes a name match with nothing behind it, "
        f"so `kept` is a filter outcome and not a count of confirmed identities; the identity "
        f"evidence line below splits the harvested records by what actually carried each one. "
        f"rejected = fetched - kept. Both are counted before any fallback, so a fired fallback "
        f"shows kept 0; the merged corpus is counted separately above; the corpus file records "
        f"this number under its original key `verified`)",
        *_evidence_lines(prov),
        f"- identity: orcid={identity.get('orcid') or '(none)'}; "
        f"affiliation_keywords={len(identity.get('affiliation_keywords') or [])}; "
        f"email_domains={len(identity.get('email_domains') or [])}",
        f"- effective require_affiliation: {identity.get('require_affiliation_effective', 'unknown')}",
        f"- position_filtered: {prov['position_filtered']}",
        f"- identity fallback fired: {prov['fallback_fired']}"
        + (" (inferred from the role stamps, not recorded — this corpus predates the explicit key, "
           "and after a source merge the inference reads False even when the fallback did fire; "
           "re-harvest to settle it)" if prov["fallback_fired_inferred"] else ""),
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
        "Rows above are ordered by first appearance, then by name — never by a count. That order "
        "is the one the timeline figure reads, and it does not move when a re-harvest changes "
        "somebody's totals.",
        "",
        "By position label: " + ", ".join(
            f"{STRATUM_LABEL[key]} {value}" for key, value in roster["by_stratum"].items()
        ),
    ]

    # Round four. Rounds one through three refused to order people at all; the
    # counts were printed, one line each, and arranging them was left to the
    # reader. The ordering is printed now, as a second view rather than by
    # resorting the table above — the roster keeps an order nobody's output can
    # change, and the leaderboard is clearly a leaderboard instead of being the
    # default reading of the same rows.
    if roster["rows"]:
        ranked = rank_people(roster["rows"], by="first_slots")
        lines += [
            "",
            "**By first-author slots.** Ties share a rank.",
            "",
            "| rank | person | lead slots | appearances |",
            "|---|---|---|---|",
        ]
        for row in ranked["ranked"]:
            tie = " (tied)" if row["tied"] else ""
            lines.append(
                f"| {row['rank']}{tie} | {row['name']}{row['marker']} | "
                f"{row['n_first_slots']} | {row['n_appearances']} |"
            )
        lines += ["", ranked["basis"]]
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

    # Round four. Rounds one through three refused a fitted slope here, and the
    # reason they gave — a handful of right-censored integer points do not
    # support one — is still true; `trends` does not dispute it, it prints the
    # interval that makes it visible and refuses outright below four points.
    # The slope is deliberately the *shortest* part of what follows.
    fit = fit_trend(result["years"])
    lines.extend(["", "**Direction over these years.**", fit["basis"]])
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


def _impact_body(impact: dict[str, Any] | None, note: str,
                 reference: Mapping[str, Any] | None = None,
                 reference_note: str = "") -> list[str]:
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
        ] + _reference_position_lines(reference, reference_note)

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
    lines += _reference_position_lines(reference, reference_note)
    return lines


def _reference_position_lines(reference: Mapping[str, Any] | None, note: str) -> list[str]:
    """Section 15's round-four half: where those counts sit in an external cell.

    A count on its own is unreadable — 4 citations is ordinary in one field and
    remarkable in another, and rounds one through three printed the count and
    stopped there, because the only reference population available was the
    handful of corpora a user had loaded. `impact_reference` supplies a different
    population: every OpenAlex work in one topic in one year. `ranking`'s refusal
    of a position among the loaded corpora is untouched and still correct.

    The block is written so that the papers that could *not* be placed are as
    visible as the ones that could. That is the failure mode worth designing
    against: a reader who sees percentiles for eight of twelve papers will supply
    a low number for the other four unless told, in words, what actually happened.
    """
    if reference is None:
        return [
            "",
            "**Position in an external reference population.** Not computed.",
            f"Reason: {note or 'run `cite --percentile` to fetch one.'}",
            "A citation count with no reference cell beside it is not a low count; "
            "it is a count whose context was never fetched.",
        ]

    counts = reference.get("denominator") or {}
    total = counts.get("papers_total", 0)
    located = counts.get("papers_located", 0)
    lines = [
        "",
        "**Position in an external reference population.**",
        f"- placed: {located} of {total} records sit inside a cell of every OpenAlex work "
        f"sharing their topic and publication year",
        f"- method: {reference.get('method', '')}",
        f"- smallest cell used: {reference.get('min_reference_population', '')} works "
        f"(a floor on arithmetic resolution, not a claim of representativeness)",
        f"- collected: {reference.get('generated_at', 'date not recorded')}",
    ]

    by_status = counts.get("by_status") or {}
    reasons = reference.get("status_reasons") or {}
    unplaced = [(status, n) for status, n in by_status.items() if n and status != "located"]
    if unplaced:
        lines += [
            "",
            f"{total - located} records carry no percentile. Each reason below is a different "
            "thing that did not happen, and none of them is a low position:",
        ]
        lines += [f"- {n} — {reasons.get(status, status)}" for status, n in unplaced]
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

# The one absent-input note in this file whose fix is a verb rather than a file
# the reader has to fill in by hand: these signals come from three open keyless
# APIs, so the work is a command rather than an afternoon on a vendor page.
_NO_JOURNAL_RISK_NOTE = (
    "no journal risk signals were passed to build_report, and none were looked for. Collect them "
    "with `check-your-advisor journal-risk --output-dir <corpus dir>`."
)

# "evaluation table", never "student-evaluation table", and the same in
# `_evaluations_body` below. Both strings are printed into a section `body`, and
# the computed half of every section is scanned for the word "student" — the
# report labels nobody a student, because no PubMed field separates a PhD
# student from a postdoc, a technician or a visiting scholar (see STRATUM_LABEL).
# The section title and its prose carry the full name of the table; the computed
# half does not need to and must not.
_NO_EVALUATION_TABLE_NOTE = (
    "no evaluation table was passed to build_report, and none was looked for. Pass one with "
    "`check-your-advisor profile --evaluation-table <path.csv>`."
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
        "report again. Warning G3 is raised for a corpus no configured identity evidence actually "
        "reached — nothing set, or something set that matched nothing, or an OpenAlex id that "
        "reached too little of it — in bold at the top of this section, and it no longer refuses "
        "the report; this section exists because evidence that *did* reach the records can still "
        "be weak, an affiliation keyword or an institutional mail domain shared by a whole "
        "department raises no warning at all, and such a corpus can still describe several people.",
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


def _risk_cells(risk: dict[str, Any] | None) -> dict[str, str]:
    """Journal name -> the one short 风险信号 cell for the table above the block.

    Four distinct absences, spelled four different ways, because they call for
    four different next moves: nobody collected any signals, nobody could collect
    them for this journal, the collection ran and this journal was not in it, and
    the collection ran and every source stayed silent. Collapsing any two of them
    would put "we did not look" and "we looked and found nothing" in one cell,
    which is the failure this whole section is built to avoid.

    The count is what goes in the cell and the sentences go below it. A cell wide
    enough for a paragraph is a cell nobody reads, and every statement is printed
    in full in the block underneath.
    """
    if not risk or risk.get("risk_missing"):
        return {}
    cells: dict[str, str] = {}
    for row in risk.get("rows") or []:
        name = str(row.get("journal") or "")
        if not row.get("issn"):
            cells[name] = "无 ISSN，查不了"
        elif not row.get("checked"):
            cells[name] = "本次未采集"
        elif not row.get("sources_answered"):
            cells[name] = "三源均未应答"
        else:
            count = len(row.get("signal_names") or [])
            cells[name] = f"{count} 项（见下）" if count else "0 项"
    return cells


def _journal_body(journals: dict[str, Any] | None, note: str,
                  risk: dict[str, Any] | None = None) -> list[str]:
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
        "(年份) | JCR | 中科院大类 | 中科院小类 | 预警 | 风险信号 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    # 预警 and 风险信号 are two columns and never one. The first is the CAS
    # warning list, copied by hand from a page with no API, and it is a dated
    # statement by that list's publisher. The second is what three open APIs
    # returned about the same journal on a stated day, and it is not a list
    # anybody publishes. Merging them would put a hand-copied Chinese list and a
    # DOAJ membership flag in one cell as though they were the same kind of claim.
    cells = _risk_cells(risk)
    default_cell = "未采集" if (not risk or risk.get("risk_missing")) else "-"
    for result in journals["journals"]:
        risk_cell = cells.get(result["journal"], default_cell)
        if not result["entries"]:
            # "本表未收录", not "未提供对照表": a table was supplied and this
            # journal is not in it, which is a lookup nobody has done yet. The
            # two cells look alike and mean opposite things about whose move it
            # is next, so they are never spelled the same way.
            lines.append(
                f"| {result['journal']} | {result['paper_count']} | {result['match_type']} | "
                f"本表未收录 | - | - | - | - | - | - | {risk_cell} |"
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
                f"{row['cas_minor'] or '-'} | {warning} | {risk_cell} |"
            )
    if not journals["journals"]:
        # A header with no rows under it reads as a rendering failure. This
        # happens when no record in the corpus carries a journal string at all,
        # which is a fact about the corpus and is worth one row saying so.
        lines.append(
            f"| (no record in this corpus carries a journal string) | "
            f"{journals['papers_without_journal']} | - | - | - | - | - | - | - | - | - |"
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


def _risk_prose() -> list[str]:
    """Section 18's second block of fixed text: what a risk signal is, and is not."""
    lines = [
        "",
        "**Public risk signals, and the word this block will not use.** The columns above come "
        "from a file you filled in. This block comes from three open keyless APIs — DOAJ, Crossref "
        "and OpenAlex — read on a stated day and printed with the endpoint that said each thing. "
        "Every line is a statement of what an endpoint returned. None of them says a journal is "
        "predatory, and neither does this toolkit: that is an accusation about a publisher's "
        "conduct, none of these three sources makes it, and no count of these signals is turned "
        "into a grade, a tier, a score, a letter or a colour here at any number of them. Nothing "
        "in this block reaches the composite score in Section 16.",
        "",
        "All three sources are queried and none of them wins. That is deliberate and it is the "
        "same rule JRN-07 applies to two editions of a partition table: they answer different "
        "questions, they disagree routinely — OpenAlex keeps its own copy of the DOAJ membership "
        "flag and it can lag DOAJ's live answer — and where two of them say different things both "
        "lines are printed with their dates and neither is preferred.",
        "",
        "The 中科院国际期刊预警名单 is not in this block and cannot be. It is published once a "
        "year as a login-walled page and a PDF with no JSON and no CSV endpoint, so it stays where "
        "it already is: the hand-filled 是否预警 and 预警等级 columns in the table above, which a "
        "person copies once a year. This package has no crawler for it and will not grow one.",
        "",
    ]
    lines += [f"- **{key}** — {text}" for key, text in JOURNAL_RISK_CAVEATS.items()]
    return lines


def _risk_body(risk: dict[str, Any] | None, note: str) -> list[str]:
    """Section 18's risk block: the collected signals, or a stated absence."""
    if risk is None:
        return ["", "Not computed: no journal risk join was supplied to the renderer."]

    if risk["risk_missing"]:
        return [
            "",
            "**Public risk signals: 未采集** — no risk signals were collected for this corpus, so "
            "the 风险信号 column above is empty for a stated reason rather than because the "
            "journals produced no signal.",
            "",
            f"Reason: {note or _NO_JOURNAL_RISK_NOTE}",
            "",
            f"- distinct journals in this corpus that could be checked: "
            f"{risk['journal_denominator'] - len(risk['journals_without_issn'])} of "
            f"{risk['journal_denominator']}",
            f"- journals the corpus recorded with no usable ISSN, which the three endpoints "
            f"cannot be asked about at all: {len(risk['journals_without_issn'])} of "
            f"{risk['journal_denominator']} — "
            f"{', '.join(risk['journals_without_issn']) or 'none'}",
            "",
            "Run `check-your-advisor journal-risk --output-dir <corpus dir>` to collect them. It "
            "makes one GET per journal against " + " + ".join(SOURCE_ORDER) + ", needs no key and "
            "no account, writes its own dated file and touches neither the corpus nor the journal "
            "table you filled in.",
            "- An empty cell there is a lookup nobody has run yet. It is not a clean bill of "
            "health and it is not a statement about any journal.",
        ]

    provenance = risk["provenance"]
    span = provenance.get("fetched_at_range")
    lines = [
        "",
        "**Public risk signals, one line per statement.** Collected from three open APIs; each "
        "line names the endpoint it came from and the day it was read.",
        "",
        f"- journals matched to a collected record, by ISSN only: {risk['journals_checked']} of "
        f"{risk['journal_denominator']} — there is deliberately no name fallback here, because a "
        f"fuzzy name match would attach a real-looking statement about one journal to another",
        f"- journals with no collected record: {risk['journals_unchecked']} of "
        f"{risk['journal_denominator']}",
        f"- journals the corpus recorded with no usable ISSN, which none of the three endpoints "
        f"can be asked about: {len(risk['journals_without_issn'])} of "
        f"{risk['journal_denominator']} — "
        f"{', '.join(risk['journals_without_issn']) or 'none'}",
        f"- sources queried, all of them, every time: {', '.join(provenance.get('sources') or [])}",
        f"- Crossref metadata fields counted, a fixed list so the denominator does not move: "
        f"{len(provenance.get('tracked_coverage_fields') or [])} — "
        f"{', '.join(provenance.get('tracked_coverage_fields') or []) or 'none'}",
        f"- signals were read: {' to '.join(span) if span else 'not recorded'}",
        f"- collected file written: {provenance.get('generated_at') or 'not recorded'}",
        "",
    ]

    if risk["signal_counts"]:
        lines += [
            "How many journals carried each signal. Counts against "
            f"{risk['journal_denominator']} distinct journals, never a share — a corpus uses a "
            "couple of dozen journals, far below the sample size at which this report will print "
            "a percentage at all:",
            "",
        ]
        lines += [f"- {name}: {count} of {risk['journal_denominator']}"
                  for name, count in risk["signal_counts"].items()]
        lines.append("")

    lines += [
        "| journal | papers | ISSN | signal | 判据来源 | 采集日期 | statement |",
        "|---|---|---|---|---|---|---|",
    ]
    printed = 0
    for row in risk["rows"]:
        if not row["signals"]:
            continue
        printed += 1
        for signal in row["signals"]:
            lines.append(
                f"| {_table_cell(row['journal'])} | {row['paper_count']} | "
                f"{row['issn'] or '-'} | {_table_cell(signal.get('signal'))} | "
                f"{_table_cell(signal.get('source'))} | {row['fetched_at'] or '未记录'} | "
                f"{_table_cell(signal.get('statement'))} |"
            )
    if not printed:
        # A header with no rows reads as a rendering failure. Which of the two
        # empty states this is has already been said above; the row says it again
        # where the reader is looking.
        lines.append(
            f"| (no journal in this corpus carried a signal) | - | - | - | - | - | "
            f"{risk['journals_checked']} of {risk['journal_denominator']} journals were checked "
            f"and none produced a statement. That is a fact about the lookup, not a clean bill of "
            f"health for the journals. |"
        )
    lines += [
        "",
        "- 未被 DOAJ 收录 is not a finding. DOAJ indexes open-access journals that applied to it, "
        "so a subscription journal is absent by construction.",
        "- A Crossref field at zero is not a finding either. It says what a publisher deposits: "
        "Journal of Hepatology deposits no abstracts for its backfile.",
        "- A blank 是否预警 cell in the table above still means nobody checked the CAS list. "
        "Nothing in this block fills it in, and no signal here is a substitute for it.",
    ]
    return lines


def _table_cell(text: Any) -> str:
    """One user-supplied string, safe to place between two pipes.

    Every other table in this file is built from values the toolkit produced. A
    评价来源 is typed in by hand and may contain a `|`, which would split one
    cell into two and shift every column after it — `html_report._cells` splits
    on the character and honours no escape. The pipe is replaced with its
    full-width form so nothing is lost and the row still parses.
    """
    return str(text if text is not None else "").replace("|", "｜").strip() or "-"


def _evaluations_prose() -> list[str]:
    """Section 20's fixed text: what this is, what it refuses to do, and who collected it."""
    lines = [
        "This section is printed last and numbered 20. Last because it is the only section on the "
        "page that is not a measurement: everything above is computed from publication records "
        "that anyone can re-fetch and re-check, and this is a list of statements other people "
        "made. Numbered 20 because four other modules cross-reference the sections above by "
        "number, and renumbering to put it here would falsify all of them at once.",
        "",
        EVALUATION_STANCE,
        "",
        "**Nothing here was fetched.** This package contains no crawler, makes no request to any "
        "forum, review site or Q&A page, and could not have collected this material even if it "
        "wanted to — those sites do not permit automated collection and several of them defend "
        "against it. The reader searched, read and typed in every row. What the toolkit does is "
        "define the columns, refuse a file that cannot say where a statement came from or when it "
        "was read, attribute the rows to one advisor, and print them with their source attached.",
        "",
        "What a hand-collected set of statements cannot tell you, separately from whether any one "
        "of them is accurate. Each of these bends the counts below in a stated direction:",
        "",
    ]
    lines += [f"- **{name}** — {note}" for name, note in EVALUATION_LIMITS]
    lines += ["", "What these rows cannot mean:", ""]
    lines += [f"- **{key}** — {text}" for key, text in EVALUATION_CAVEATS.items()]
    return lines


def _evaluations_body(evaluations: dict[str, Any] | None, note: str) -> list[str]:
    """Section 20. The collected statements with their sources, or a stated absence."""
    if evaluations is None:
        return [
            "Not computed: no evaluation table was joined to this report.",
            "",
            f"Reason: {note or _NO_EVALUATION_TABLE_NOTE}",
            "",
            "What that costs is bounded, and worth stating so the absence is not read as either "
            "an endorsement or an accusation. No statement about this advisor was collected, so "
            "none is printed. That is not evidence that none exists, and it is not evidence that "
            "any that exist are favourable or unfavourable. Nothing in Sections 1 to 19 is "
            "evidence about it either, in any direction: those sections are computed from "
            "publication records, which carry no account of what supervision was like.",
            "",
            "To supply one: search wherever such statements are kept for this advisor, read the "
            "pages yourself, and record what you find as UTF-8 CSV carrying at least the columns "
            "导师姓名, 评价来源, 数据获取日期 and one of 评价内容 or 维度评分. Then re-run with "
            "`--evaluation-table <path.csv>`.",
            "- 评价来源 and 数据获取日期 are required columns and the loader refuses a file "
            "without them, for the same reason the journal table requires an edition: an "
            "unattributed, undated sentence about a named person cannot be checked by anyone "
            "later, including the person who wrote it down.",
            "- 学生身份, 评价年份 and 原文链接 are optional. Where 评价年份 is missing the "
            "statement cannot be placed in time at all, and a twelve-year-old account of a lab is "
            "not a current one.",
            "- There is no collection command to run first, and there will not be one. This is "
            "the one table with no worklist generator: `journal-worklist` can write the list of "
            "journals because the corpus already names them, and nothing in a corpus names the "
            "places people talk about an advisor.",
        ]

    counts = evaluations["counts"]
    provenance = evaluations["provenance"]
    lines = [
        f"{counts['evaluations_total']} statement(s) attributed to this advisor, out of "
        f"{counts['rows_in_file']} usable row(s) in the file. Each one is printed below with the "
        f"source it came from and the day that source was read. They are not scored, not averaged "
        f"and not summarised.",
        "",
        "| what | count | of |",
        "|---|---|---|",
        f"| statements attributed to this advisor | {counts['evaluations_total']} | "
        f"{counts['rows_in_file']} usable rows in the file |",
        f"| ... carrying a 评价年份 | {counts['rows_with_year']} | "
        f"{counts['evaluations_total']} |",
        f"| ... carrying no 评价年份, so not placeable in time | {counts['rows_without_year']} | "
        f"{counts['evaluations_total']} |",
        f"| ... carrying a 原文链接 | {counts['rows_with_url']} | {counts['evaluations_total']} |",
        f"| ... carrying a 维度评分 | {counts['rows_with_rating']} | "
        f"{counts['evaluations_total']} |",
        f"| distinct 评价来源 they came from | {counts['source_count']} | "
        f"{counts['evaluations_total']} statements |",
    ]

    year_range = evaluations["year_range"]
    date_range = evaluations["retrieved_on_range"]
    span = (
        f"{year_range[0]} to {year_range[1]}" if year_range and year_range[0] != year_range[1]
        else f"{year_range[0]} only" if year_range
        else "not recorded — no row carries a year"
    )
    lines += [
        "",
        f"- time span the statements themselves cover: {span}, over the "
        f"{counts['rows_with_year']} of {counts['evaluations_total']} row(s) that carry a year"
        # Only when there are any. A dangling "the other 0" reads as a rounding
        # artefact and invites the reader to check a number that is not there.
        + (f". The other {counts['rows_without_year']} row(s) sit outside this span rather than "
           f"inside it, and widening the span to cover them would be inventing a date"
           if counts["rows_without_year"] else ""),
        "- days the pages were read: "
        + (f"{date_range[0]} to {date_range[1]}" if date_range and date_range[0] != date_range[1]
           else f"{date_range[0]}" if date_range else "not recorded"),
        f"- attribution to this advisor: {evaluations['advisor_filter']}",
    ]
    if evaluations["advisor_note"]:
        lines.append(f"- attribution note: {evaluations['advisor_note']}")
    if evaluations["suppressed"]:
        lines.append("- nothing is printed below, and the reasons are not rounded off:")
        lines += [f"  - {reason}" for reason in evaluations["suppressed_reasons"]]

    roles = evaluations["student_roles"]
    lines += [
        "",
        f"- 学生身份 as recorded: {_fmt_input(roles) or 'not recorded on any row'}. This is what "
        f"each writer said about themselves and nothing verified it",
        "",
        "Where the statements came from. Ten statements from one thread and ten from ten sites "
        "are different evidence, so the per-source counts are printed rather than one total:",
        "",
        "| 评价来源 | statements | of |",
        "|---|---|---|",
    ]
    if evaluations["sources"]:
        lines += [
            f"| {_table_cell(source)} | {count} | {counts['evaluations_total']} |"
            for source, count in evaluations["sources"].items()
        ]
    else:
        lines.append(f"| (no row carries a source) | 0 | {counts['evaluations_total']} |")

    lines += [
        "",
        "Where the file came from. Without this a quotation in this section is a sentence with no "
        "page and no day attached:",
        "",
        f"- file: `{provenance.get('path') or 'not recorded'}` (read as "
        f"{provenance.get('encoding') or 'unknown encoding'})",
        f"- rows read {provenance.get('rows_read', '?')}; rejected "
        f"{provenance.get('rejected', 0)}; exact duplicates dropped "
        f"{provenance.get('duplicates_dropped', 0)}",
        f"- sources present anywhere in the file, including rows belonging to other advisors: "
        f"{_fmt_input(provenance.get('sources_in_file')) or 'none'}",
        f"- optional columns the file does not carry: "
        f"{_fmt_input(provenance.get('columns_missing_optional')) or 'none — all present'}",
        "",
        "**The statements, in the order the file supplied them.** That order is the order they "
        "were collected in and is not a ranking: sorting statements about a person by anything "
        "would be building the judgement this section refuses to make.",
        "",
    ]
    if evaluations["entries"]:
        for number, entry in enumerate(evaluations["entries"], start=1):
            parts = [
                f"来源 {entry['source'] or 'not recorded'}",
                f"获取日期 {entry['retrieved_on'] or entry['retrieved_on_raw'] or 'not recorded'}",
                f"评价年份 {entry['year'] if entry['year'] is not None else 'not recorded'}",
                f"学生身份 {entry['student_role'] or 'not recorded'}",
            ]
            if entry["content"]:
                parts.append(f"评价内容 {entry['content']}")
            if entry["rating"]:
                parts.append(f"维度评分 {entry['rating']}")
            parts.append(f"原文链接 {entry['url'] or 'not recorded'}")
            lines.append(f"- **[{number}]** " + " — ".join(parts))
    else:
        lines.append("- none")
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
        # Before the prose, and `.get` so a section dict built by an older caller
        # still renders. This is the line that replaced a refusal page.
        for text in section.get("warnings") or []:
            parts += [text, ""]
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

    An identity warning is treated the same way as a refusal, which is what it
    was until this round. Rendering the report was a decision about the report;
    it was not a decision to let a corpus that may hold several people take a
    position beside corpora that hold one. The row stays, the score stays inside
    the row, and the position does not exist.
    """
    report = entry.get("report") or {}
    if report.get("refused") or report.get("warnings"):
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
            # Carried per row for the same reason `gate` is: a corpus that may
            # describe several people must not be given a position beside clean
            # ones, and `ranking._resolve` needs to be able to say why.
            "warnings": list(report.get("warnings") or []),
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
            if item.get("warnings"):
                # Printed before the citation state, because "this may not be one
                # person, or may not be all of it" outranks "these numbers have
                # no citation counts". Labelled `warning`, not `identity warning`:
                # G1 is a coverage condition and calling it an identity one told
                # the reader the wrong thing to go and check.
                state = "warning " + ", ".join(
                    str((w or {}).get("id", "?")) for w in item["warnings"]
                ) + "; " + state
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
