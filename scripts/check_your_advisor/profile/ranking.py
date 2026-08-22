"""
Ordering, coarsening and direction — the three things round one refused.

`scoring` says, at length, that it will not order anyone: "a value may be
printed, a position may not". That boundary was drawn deliberately and it has
now been moved, also deliberately. This module is where the move happens, so
that a reader can find the whole of it in one file rather than discovering it
spread through a renderer.

What moved, and the argument that lets it move:

- **Rank.** `composite_score` sees one corpus and cannot order anything; that is
  a fact about its signature, not a principle, and this module is handed the N
  corpora `report.build_comparison` already assembled. A rank here is a position
  inside *that hand-picked set of N* and nothing wider. `RANK_METHOD` says so in
  words, and every ranked row carries the number of corpora it was ranked among,
  because "first" means very different things at N=2 and N=9.
- **Stars.** A star count is the score coarsened, not a new measurement. The band
  edges are declared module constants, they are equal width, and they are printed
  with the result — see `STAR_BANDS` and the argument in `star_rating`.
- **Direction.** `comparative_statement` will say which of two corpora scored
  higher. It says it about the score and only about the score, it always carries
  the difference and both component counts, and it refuses outright when the two
  numbers were not built the same way.

What did **not** move is in `RANKING_EXCLUSIONS`, in full and machine-readable,
kept in two lists because `scoring`'s distinction between a verdict and a missing
input holds here too: a position inside a reference population is not computed
because no such population exists here, whereas letter tiers and fitted slopes
are refused outright and a better data source would not reopen them. The rank
above is emphatically not the first of those — it is a position among the few
corpora somebody loaded, and it says so wherever it is printed.

The one asymmetry in this file worth naming before it is mistaken for an
oversight. `rank_corpora` ranks corpora whose scores rest on different component
sets, flagging the mismatch; `comparative_statement` refuses that same pair
outright. That is on purpose. Citation coverage varies between corpora as a
matter of routine, so refusing every unequal set would delete the rank column in
the common case, while a rank column that carries each row's component count
still lets a reader see what happened. A one-sentence verdict about two named
people has no such margin: there is nowhere in "A scored higher than B" to put
the caveat, so the sentence is not produced at all.

Pure, like `scoring` and `metrics`: dicts in, dict out, no file access, no
network, standard library only. Nothing here recomputes a score; it reads the
dicts `composite_score` already returned.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .scoring import COMPONENT_NAMES

__all__ = [
    "COMPARISON_CAVEAT",
    "MIN_RANKED_CORPORA",
    "RANKING_EXCLUSIONS",
    "RANK_METHOD",
    "RANK_TIE_NOTE",
    "SCORE_SCALE_MAX",
    "STAR_BANDS",
    "STAR_BAND_WIDTH",
    "STAR_BASIS",
    "STAR_MAX",
    "STAR_SCALE_NOTE",
    "TIE_DECIMALS",
    "comparative_statement",
    "rank_corpora",
    "star_rating",
]


# ------------------------------------------------------------------
# Anchors
# ------------------------------------------------------------------
#
# Same rule as `scoring`: every threshold this module compares against is a
# declared constant with a name, exported so a report can print it verbatim.
# There is no second set of numbers hidden inside a function body.

# The scale `composite_score` emits on. Not a configurable: it is what the
# rescaling in `composite_score` already fixed, and it is repeated here only so
# the band edges below can be derived from it instead of typed out.
SCORE_SCALE_MAX = 100.0

# `composite_score` rounds to one decimal, so one decimal is what a reader ever
# sees. Ties are therefore decided on the printed value, not the underlying
# float. This is not a tolerance chosen for convenience — it closes a specific
# indefensible output: two rows both showing 61.4, one of them ranked above the
# other because the sixteenth decimal differed.
TIE_DECIMALS = 1

# A rank is a position among the corpora on the page. At N=1 there is no
# position to hold, and "#1" printed beside a single corpus reads as an
# achievement rather than as arithmetic over a set of one. Below this floor the
# ranking is suppressed and every corpus is returned unranked with the reason,
# which is the same convention `metrics` uses for an aggregate below n=5.
MIN_RANKED_CORPORA = 2

# Five bands, equal width, over the full 0-100 scale.
STAR_MAX = 5
STAR_BAND_WIDTH = SCORE_SCALE_MAX / STAR_MAX

# Derived rather than written out, so "equal width" is structurally true instead
# of being a claim in a comment that a later edit could falsify. Highest band
# first; each band is [lower, lower + STAR_BAND_WIDTH) except the top, which is
# closed at SCORE_SCALE_MAX.
STAR_BANDS: tuple[tuple[float, int], ...] = tuple(
    (STAR_BAND_WIDTH * (stars - 1), stars) for stars in range(STAR_MAX, 0, -1)
)

# How far one component can move the total under the flat default weight table.
# Used only in the printable note below, and computed from the live component
# registry so that adding a seventh component updates the sentence rather than
# leaving a stale number in prose.
_FLAT_COMPONENT_SWING = SCORE_SCALE_MAX / len(COMPONENT_NAMES)

STAR_SCALE_NOTE = "; ".join(
    f"{stars}★ = {lower:g}-"
    f"{SCORE_SCALE_MAX if stars == STAR_MAX else lower + STAR_BAND_WIDTH:g}"
    for lower, stars in reversed(STAR_BANDS)
)

STAR_BASIS = (
    f"{STAR_MAX} equal bands of {STAR_BAND_WIDTH:g} points over the 0-{SCORE_SCALE_MAX:g} "
    f"composite score ({STAR_SCALE_NOTE}). The edges are round numbers on the score's own "
    "scale, not cut points measured off any group of researchers, so a star count is the "
    "score coarsened and nothing else."
)

RANK_METHOD = (
    "standard competition ranking (1, 2, 2, 4) on the composite score, highest first, with "
    f"ties decided on the score as printed to {TIE_DECIMALS} decimal. The position is held "
    "among the corpora handed to this call and no wider set. Corpora whose report was refused "
    "at a gate, and corpora whose score was suppressed, keep their row and are returned "
    "unranked with the reason."
)

RANK_TIE_NOTE = (
    "Tied corpora share one rank and the next rank is skipped, so ranks after a tie are not "
    "consecutive. Within a tie the rows are ordered by label, case-folded — alphabetical order "
    "is a tiebreak for printing and carries no meaning whatsoever."
)

COMPARISON_CAVEAT = (
    "A higher composite score is a higher weighted mean of the components listed beside it, "
    "under the one weight table printed with it. It is not a measurement of supervision, and "
    "it is not comparable across fields: no field normalisation is applied anywhere in this "
    "toolkit, and citation rates differ between fields by an order of magnitude. Two corpora "
    "from different fields can be ranked here and the ranking will still be meaningless — the "
    "arithmetic is sound and the subject is not."
)


# ------------------------------------------------------------------
# What is still not produced, and the two different reasons
# ------------------------------------------------------------------

RANKING_EXCLUSIONS: dict[str, tuple[tuple[str, str], ...]] = {
    # Absent by decision. A better data source would not change these.
    "refused_by_design": (
        (
            "Letter tiers",
            "Not produced. Stars are produced and letters are not, which is a deliberate split "
            "between two coarsenings of the same number rather than an inconsistency to be "
            "tidied away. Recorded here so that a later reader does not unify them and reverse "
            "a decision they were not party to.",
        ),
        (
            "Trends, fitted slopes, year-over-year change",
            "Refused, unchanged from round one: a handful of right-censored integer points do "
            "not support a slope. Nothing in this module reads a time series, and a rank is a "
            "position at one moment, never a movement between two.",
        ),
    ),
    # Absent because the input does not exist here. A different sentence.
    "not_computable_here": (
        (
            "A position inside a reference population",
            "Not computed, because there is no reference population. The corpora on a page are "
            "the ones a user chose to load; a position inside that set would move whenever an "
            "unrelated corpus was added or dropped, and would be read as though it referred to "
            "researchers in general. `rank_corpora` returns a position among the loaded few and "
            "names the count it was taken over, which is the honest version of the same idea.",
        ),
    ),
}


# ------------------------------------------------------------------
# Reading what `composite_score` returned
# ------------------------------------------------------------------


@dataclass(frozen=True)
class _Resolved:
    """One corpus, reduced to what an ordering needs and why it might not have it."""

    label: str
    source: str
    value: float | None
    components: tuple[str, ...]
    weight_signature: tuple[tuple[str, float], ...]
    score: Mapping[str, Any] | None
    gate: Mapping[str, Any] | None
    unavailable: str | None


def _printed(value: float) -> float:
    """The value as a reader sees it. Every comparison in this module uses this."""
    return round(float(value), TIE_DECIMALS)


def _is_score_dict(candidate: Mapping[str, Any]) -> bool:
    """A `composite_score` result, as opposed to a comparison entry wrapping one."""
    return "components" in candidate and "weights_used" in candidate


def _scored_components(score: Mapping[str, Any]) -> tuple[str, ...]:
    """
    Names of the components that actually carried the score, sorted.

    Weight-zero components appear in `composite_score`'s `components` list with a
    contribution of 0.0 and are excluded from its `denominator`; they are
    excluded here too, so this tuple's length equals that denominator. Sorted
    rather than left in registration order because it is used as a set identity,
    and two corpora that scored on the same components must produce the same
    tuple regardless of anything else.
    """
    items = score.get("components") or []
    return tuple(sorted(
        str(item.get("name"))
        for item in items
        if isinstance(item, Mapping) and float(item.get("weight") or 0.0) > 0.0
    ))


def _weight_signature(score: Mapping[str, Any]) -> tuple[tuple[str, float], ...]:
    """
    The weight table as a comparable value.

    Two scores computed under different tables are two different quantities that
    happen to share a scale, so this is checked before any direction is stated.
    Rounded to six places because the tables are compared, never re-applied, and
    float noise from a config round-trip is not a difference of opinion.
    """
    weights = score.get("weights_used") or {}
    return tuple(sorted(
        (str(name), round(float(weight), 6)) for name, weight in weights.items()
    ))


def _resolve(entry: Any, fallback_label: str) -> _Resolved:
    """
    Accept a `build_comparison` corpus entry or a bare `composite_score` result.

    A refused report, an absent score and a suppressed score are three different
    situations and each keeps its own sentence. None of them becomes a zero and
    none of them is dropped: a corpus that cannot be ranked is still a corpus the
    reader asked about, and its absence from a list of four would be read as
    "there were three".
    """
    if not isinstance(entry, Mapping):
        raise TypeError(
            f"expected a corpus entry or a composite_score result mapping, got "
            f"{type(entry).__name__}"
        )

    if _is_score_dict(entry):
        label, source, refused, gate = fallback_label, "", False, None
        score: Mapping[str, Any] | None = entry
    else:
        label = str(entry.get("label") or "").strip() or fallback_label
        source = str(entry.get("source") or "")
        refused = bool(entry.get("refused"))
        raw_gate = entry.get("gate")
        gate = raw_gate if isinstance(raw_gate, Mapping) else None
        raw_score = entry.get("score")
        score = raw_score if isinstance(raw_score, Mapping) else None

    def unranked(reason: str, components: tuple[str, ...] = ()) -> _Resolved:
        # `components` survives even when the total does not: a score suppressed
        # at two components is a different row from one with no data at all, and
        # printing 0 for both would erase that.
        return _Resolved(label, source, None, components, (), score, gate, reason)

    if refused:
        gate_id = (gate or {}).get("id", "?")
        gate_name = (gate or {}).get("name", "")
        return unranked(
            f"the report for this corpus was refused at gate {gate_id} ({gate_name}), so there "
            "is no score to place"
        )
    if score is None:
        return unranked("this corpus carries no composite score")

    components = _scored_components(score)
    value = score.get("score")
    if score.get("suppressed") or value is None:
        return unranked(
            f"the composite score was suppressed at {int(score.get('denominator') or 0)} scored "
            f"component(s), floor {int(score.get('min_components') or 0)}; the parts are in the "
            "report, the total is not",
            components,
        )
    return _Resolved(
        label=label,
        source=source,
        value=_printed(value),
        components=components,
        weight_signature=_weight_signature(score),
        score=score,
        gate=gate,
        unavailable=None,
    )


# ------------------------------------------------------------------
# 1. Rank
# ------------------------------------------------------------------


def rank_corpora(corpora: Any) -> dict[str, Any]:
    """
    Positions for the corpora on one comparison page, highest composite score
    first.

    `corpora` is the `corpora` list from `report.build_comparison`, the whole
    comparison dict (unwrapped to that list), or any sequence of mappings
    carrying `label`, `source` and a `score` that `composite_score` produced. A
    bare `composite_score` result is accepted in place of an entry and is
    labelled by position.

    Ties: standard competition ranking. Equal scores share a rank and the
    following rank is skipped, so the sequence runs 1, 2, 2, 4 and never 1, 2, 2,
    3. The alternative — breaking ties on some second quantity — would smuggle in
    a second, undeclared criterion to decide exactly the cases the first one
    called equal. Equality is decided on the score as printed (`TIE_DECIMALS`),
    because two rows showing the same number must not carry different ranks.
    Within a tie, rows are ordered by label for printing only; `RANK_TIE_NOTE`
    says so and every tied row lists the labels it is tied with.

    Returns:
      ranked        entries in rank order, each with `rank`, `score`, `stars`,
                    `n_components`, `components`, `tied_with` and `ranked=True`.
      unranked      entries that took no position — a refused report, an absent
                    score, a suppressed score — each with `rank=None`,
                    `ranked=False` and its own `reason`. Never dropped, never
                    scored as zero, never placed last: last is a position.
      entries       ranked followed by unranked, so a renderer can print every
                    corpus in one pass and still show which took a position.
      denominator   how many corpora were handed in (R1), against `n_ranked`.
      n_scored      how many corpora carried a score. Equal to `n_ranked` except
                    when the ranking is suppressed, where `n_ranked` is 0 and
                    this is not: narrate "carried a score" from this field, never
                    from `n_ranked`, or the page reports 0 scored corpora while
                    listing the score it just printed.
      suppressed    True when fewer than MIN_RANKED_CORPORA corpora had a score.
                    Then nothing is ranked and everything is in `unranked`.
      comparable    True only when every ranked corpus scored on the same
                    component set under the same weight table. When False the
                    ranks are still produced — see the asymmetry argued in the
                    module docstring — and `comparability` carries what differs.
                    A renderer that prints the ranks without this line is
                    printing the half of the output that flatters.
      method        `RANK_METHOD`, in words, for printing beside the column.

    Nothing here recomputes a score, so a rank cannot disagree with the number
    beside it.
    """
    if isinstance(corpora, Mapping):
        nested = corpora.get("corpora")
        if not isinstance(nested, Sequence) or isinstance(nested, (str, bytes)):
            raise TypeError(
                "rank_corpora takes the corpora list from build_comparison, or a comparison "
                "dict carrying one under 'corpora'"
            )
        corpora = nested
    if isinstance(corpora, (str, bytes)) or not isinstance(corpora, Sequence):
        raise TypeError(
            f"rank_corpora ranks a sequence of corpora, got {type(corpora).__name__}. To score "
            "one corpus, call scoring.composite_score; a rank needs a set to be a position in."
        )

    resolved = [
        _resolve(entry, f"corpus {index + 1}") for index, entry in enumerate(corpora)
    ]
    scored = [item for item in resolved if item.unavailable is None]
    suppressed = len(scored) < MIN_RANKED_CORPORA

    component_sets = sorted({item.components for item in scored})
    weight_tables = {item.weight_signature for item in scored}
    sets_match = len(component_sets) <= 1
    weights_match = len(weight_tables) <= 1

    ranked: list[dict[str, Any]] = []
    unrankable = list(resolved) if suppressed else [
        item for item in resolved if item.unavailable is not None
    ]

    if not suppressed:
        ordered = sorted(scored, key=lambda item: (-item.value, item.label.casefold(), item.source))
        for index, item in enumerate(ordered):
            rank = index + 1
            if index and ordered[index - 1].value == item.value:
                rank = ranked[index - 1]["rank"]
            ranked.append({
                "label": item.label,
                "source": item.source,
                "ranked": True,
                "rank": rank,
                "of": len(ordered),
                "score": item.value,
                "stars": star_rating(item.score)["stars"],
                "n_components": len(item.components),
                "components": list(item.components),
                "tied_with": [],
            })
        for row in ranked:
            row["tied_with"] = [
                other["label"] for other in ranked
                if other["rank"] == row["rank"] and other["label"] != row["label"]
            ]

    unranked = [
        {
            "label": item.label,
            "source": item.source,
            "ranked": False,
            "rank": None,
            # None whenever the score itself was unavailable; the real value when
            # the only thing missing was somebody to be ranked against.
            "score": item.value,
            "n_components": len(item.components),
            "components": list(item.components),
            "reason": item.unavailable or (
                f"only {len(scored)} corpus/corpora on this page carried a score, floor "
                f"{MIN_RANKED_CORPORA}; with fewer than that there is nothing to be first among"
            ),
            "gate": item.gate,
        }
        for item in sorted(unrankable, key=lambda item: (item.label.casefold(), item.source))
    ]

    if sets_match and weights_match:
        note = (
            "Every ranked corpus scored on the same components under the same weight table, so "
            "the ranked numbers are means over like inputs."
        )
    else:
        differing = []
        if not sets_match:
            differing.append(
                "the ranked corpora did not all score on the same components ("
                + "; ".join(
                    f"{len(names)}: {', '.join(names) or 'none'}" for names in component_sets
                )
                + ")"
            )
        if not weights_match:
            differing.append("more than one weight table is present among the ranked corpora")
        note = (
            "These ranks order numbers that were not all built the same way: "
            + "; and ".join(differing)
            + ". A mean over four components and a mean over six share a scale and not a "
            "meaning, so read the ranks with each row's component count in view, and take no "
            "single-sentence conclusion from them — comparative_statement refuses this pair "
            "outright for exactly this reason."
        )

    return {
        "schema_version": 1,
        "method": RANK_METHOD,
        "tie_note": RANK_TIE_NOTE,
        "tie_decimals": TIE_DECIMALS,
        "caveat": COMPARISON_CAVEAT,
        "ranked": ranked,
        "unranked": unranked,
        "entries": ranked + unranked,
        "n_ranked": len(ranked),
        "n_unranked": len(unranked),
        # How many corpora carried a score, which is *not* `n_ranked` once the
        # ranking is suppressed: below the floor nothing is ranked, so `n_ranked`
        # drops to 0 while the scores themselves still exist. A renderer that
        # narrated "N corpora carried a score" from `n_ranked` printed "0 corpora
        # carried a score" on the same page as "only 1 corpus carried a score,
        # floor 2" — one report contradicting itself about its own input.
        "n_scored": len(scored),
        # Denominator convention (R1): the population a rank was taken over is
        # the set of corpora that carried a score, against everything handed in.
        "denominator": len(resolved),
        "suppressed": suppressed,
        "min_ranked_corpora": MIN_RANKED_CORPORA,
        "comparable": sets_match and weights_match,
        "comparability": {
            "component_sets_match": sets_match,
            "weight_tables_match": weights_match,
            "component_sets": [list(names) for names in component_sets],
            "note": note,
        },
    }


# ------------------------------------------------------------------
# 2. Stars
# ------------------------------------------------------------------


def star_rating(score: Any) -> dict[str, Any]:
    """
    The composite score coarsened into `STAR_MAX` stars. One corpus at a time.

    `score` is a `composite_score` result — preferred, because the result carries
    the component count and this function can then return the score's own
    denominator with it — or a bare number on the 0-`SCORE_SCALE_MAX` scale, in
    which case `denominator` comes back None because a loose float does not say
    what it was computed over. A sequence raises TypeError: a star count is a
    restatement of one number, and anything that turned several numbers into
    stars at once would be `rank_corpora` wearing a different hat.

    Why five equal bands, since a band structure is exactly the kind of hidden
    judgement `scoring` spends its docstring refusing:

    - **Equal width, because unequal width would be a claim.** `DEFAULT_WEIGHTS`
      is flat because a flat table asserts nothing; equal bands are the same
      argument applied to the scale. Any other spacing — tighter at the top,
      generous at the bottom — encodes a belief about where the interesting
      differences lie, and nothing in this data supports one.
    - **Edges on the score's own scale, not on a group of researchers.** Every
      band edge here is a round number of points, so `STAR_BANDS` can be printed
      and disagreed with. No edge was measured off anybody, which is what keeps
      a star count from being a position in disguise.
    - **Five, because one band is about one component's full swing.** Under the
      flat default table a single component moving from 0 to its declared anchor
      moves the total by {swing:.1f} points, against a band width of
      {width:g}. So one star is roughly one component's entire range: the
      coarsest reading the score supports. Ten half-stars would resolve to
      {half:g} points, well inside a single component's swing, and would show
      motion where the inputs cannot distinguish any.
    - **No zero-star band.** The lowest band is 0-{width:g} and it is worth one
      star, so the scale never reads as a null verdict on a corpus that scored.
      Absence of a score is a different output entirely: `stars` comes back None
      with a reason, never as zero stars.

    Returns `stars` (1..{max_stars}, or None), `score`, the `band` the score fell
    in, the full `bands` table and `band_width` for printing, `denominator` (the
    components the score rested on), `suppressed`, and `unavailable` carrying the
    reason whenever `stars` is None. `basis` is `STAR_BASIS` verbatim, so the
    sentence that justifies the bands travels with every rating rather than
    living only in this docstring.

    Raises ValueError on a non-finite value or one outside 0-{scale:g}. That
    check is narrower than it looks and the gap is worth knowing: a normalised
    component handed in by mistake sits in [0, 1], which is a legal if dismal
    score, so it is indistinguishable from the real thing and collects the lowest
    band in silence. Nothing can be done about that from inside this function —
    it is the reason a `composite_score` result is the preferred input and a bare
    float is second class.
    """
    denominator: int | None = None
    registered: int | None = None
    suppressed = False

    if isinstance(score, Mapping):
        if not _is_score_dict(score):
            raise TypeError(
                "star_rating takes a composite_score result or a number, not an arbitrary "
                "mapping; a comparison entry must be unwrapped to its 'score' first"
            )
        denominator = int(score.get("denominator") or 0)
        registered = score.get("components_registered")
        suppressed = bool(score.get("suppressed"))
        value = score.get("score")
        if suppressed or value is None:
            return _no_stars(
                f"the composite score was suppressed at {denominator} scored component(s), floor "
                f"{int(score.get('min_components') or 0)}; there is no total to coarsen",
                denominator=denominator,
                registered=registered,
                suppressed=True,
            )
    elif score is None:
        return _no_stars("no composite score was supplied")
    elif isinstance(score, (str, bytes)) or isinstance(score, Sequence):
        raise TypeError(
            f"star_rating rates one score at a time, not a {type(score).__name__} of them. To "
            "place several corpora against each other, call rank_corpora, which says in its "
            "output what the position is a position among."
        )
    else:
        value = score

    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"score must be a number on the 0-{SCORE_SCALE_MAX:g} scale, got {score!r}") from None
    if not math.isfinite(value):
        raise ValueError(f"score must be finite, got {value!r}")
    if not 0.0 <= value <= SCORE_SCALE_MAX:
        raise ValueError(
            f"score must lie on the 0-{SCORE_SCALE_MAX:g} scale that composite_score emits, got "
            f"{value}. Pass the composite_score result rather than a loose number: this check "
            "catches a wrong scale only when it overshoots, and a normalised component in "
            "[0, 1] would pass it and take the lowest band without complaint."
        )

    printed = _printed(value)
    stars = next(count for lower, count in STAR_BANDS if printed >= lower)
    lower = STAR_BAND_WIDTH * (stars - 1)
    upper = SCORE_SCALE_MAX if stars == STAR_MAX else lower + STAR_BAND_WIDTH
    return _rating(stars, printed, [lower, upper], denominator, registered, suppressed, None)


star_rating.__doc__ = (star_rating.__doc__ or "").format(
    swing=_FLAT_COMPONENT_SWING,
    width=STAR_BAND_WIDTH,
    half=STAR_BAND_WIDTH / 2,
    max_stars=STAR_MAX,
    scale=SCORE_SCALE_MAX,
)


def _rating(
    stars: int | None,
    score: float | None,
    band: list[float] | None,
    denominator: int | None,
    registered: int | None,
    suppressed: bool,
    unavailable: str | None,
) -> dict[str, Any]:
    """
    The one shape a rating comes back in, rated or not.

    Built in one place so that a missing rating cannot quietly carry fewer keys
    than a present one and make a renderer's `.get` return None for a band table
    that was never in question. `denominator` is R1: the components the score
    rested on, or None when a bare number was passed and it genuinely is not
    known.
    """
    return {
        "stars": stars,
        "max_stars": STAR_MAX,
        "score": score,
        "band": band,
        "band_closed_at_top": stars == STAR_MAX,
        "bands": [list(edge) for edge in STAR_BANDS],
        "band_width": STAR_BAND_WIDTH,
        "scale_note": STAR_SCALE_NOTE,
        "basis": STAR_BASIS,
        "caveat": COMPARISON_CAVEAT,
        "denominator": denominator,
        "components_registered": registered,
        "suppressed": suppressed,
        "unavailable": unavailable,
    }


def _no_stars(
    reason: str,
    denominator: int | None = None,
    registered: int | None = None,
    suppressed: bool = False,
) -> dict[str, Any]:
    """No rating, and why, in the shape a rating comes back in."""
    return _rating(None, None, None, denominator, registered, suppressed, reason)


# ------------------------------------------------------------------
# 3. Direction
# ------------------------------------------------------------------


def comparative_statement(a: Any, b: Any) -> dict[str, Any]:
    """
    One sentence about which of two corpora scored higher, or a refusal to write
    that sentence.

    `a` and `b` are `build_comparison` corpus entries (`label`, `source`,
    `score`) or bare `composite_score` results, which are then labelled "corpus
    A" and "corpus B".

    The sentence always carries three things together, and they are not
    separable: the direction, the difference in points, and **both component
    counts**. The counts are load-bearing rather than decorative. A composite
    score is a weighted *mean* of whichever components had data, so a corpus
    scoring on four components and one scoring on six produce numbers on the same
    scale out of different material — the four-component score is not "the same
    measurement with less of it", it is a different measurement. Round one
    established this and it has not stopped being true now that direction is
    allowed.

    So when the two component sets are not identical, this function does not
    compare. It returns `comparable=False`, a statement that says the two are not
    directly comparable and why, and — deliberately — **no difference and no
    direction**. The subtraction is arithmetically available and is withheld,
    because a number printed under a "not comparable" heading is read as the
    answer and the heading as throat-clearing. Set identity is required, not just
    equal counts: two corpora scoring on five components each, one on citations
    and one on lead-slot timing, are no more comparable than four against six,
    and are arguably less.

    The same refusal covers the other two ways two scores can fail to be the same
    quantity: either corpus having no usable score (refused report, absent score,
    suppressed score), and the two having been computed under different weight
    tables.

    Returns:
      comparable   whether a direction was stated at all.
      statement    the sentence, in either case. Always safe to print.
      direction    "a_higher", "b_higher", "tied", or None when not comparable.
      higher/lower the labels, or None.
      difference   absolute difference in points, or None. Never signed: the sign
                   is `direction`, in words, and a signed number invites a reader
                   to build their own ordering out of several of them.
      a, b         each corpus's label, score, component count and component
                   list, so the sentence can be checked against its inputs.
      denominator  the shared component count when comparable, else None (R1).
      reason       why not, when not.
      caveat       `COMPARISON_CAVEAT` — what a higher score does not mean. It is
                   returned even on the comparable path, because that is the path
                   where a reader is most likely to stop reading.

    A tie is a tie at the printed precision, and the statement says so rather
    than implying the two are equal in any deeper sense.
    """
    left = _resolve(a, "corpus A")
    right = _resolve(b, "corpus B")

    def result(**fields: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "comparable": False,
            "statement": "",
            "direction": None,
            "higher": None,
            "lower": None,
            "difference": None,
            "a": {
                "label": left.label,
                "score": left.value,
                "n_components": len(left.components),
                "components": list(left.components),
            },
            "b": {
                "label": right.label,
                "score": right.value,
                "n_components": len(right.components),
                "components": list(right.components),
            },
            "denominator": None,
            "reason": None,
            "caveat": COMPARISON_CAVEAT,
            "tie_decimals": TIE_DECIMALS,
        }
        base.update(fields)
        return base

    blocked = [item for item in (left, right) if item.unavailable is not None]
    if blocked:
        reason = "; ".join(f"{item.label}: {item.unavailable}" for item in blocked)
        return result(
            statement=(
                f"{left.label} and {right.label} cannot be compared: {reason}. Nothing is being "
                "said about either one — this is a statement about the data, not about the "
                "corpora."
            ),
            reason=reason,
        )

    if left.components != right.components:
        reason = (
            f"{left.label} scored on {len(left.components)} component(s) "
            f"({', '.join(left.components) or 'none'}) and {right.label} on "
            f"{len(right.components)} ({', '.join(right.components) or 'none'})"
        )
        return result(
            statement=(
                f"{left.label} and {right.label} are not directly comparable: {reason}. Both "
                "totals are weighted means, so they run on the same 0-100 scale over different "
                "material and their difference has no subject. The difference is not printed "
                "here on purpose. To compare them, score both on the components they share by "
                "setting the others to weight 0.0, then ask again."
            ),
            reason=reason,
        )

    if left.weight_signature != right.weight_signature:
        reason = (
            f"{left.label} and {right.label} were scored under different weight tables, so the "
            "two totals are different quantities that happen to share a scale"
        )
        return result(
            statement=(
                f"{left.label} and {right.label} are not directly comparable: {reason}. Rescore "
                "both under one table — build_comparison applies a single table to every corpus "
                "for this reason — and ask again."
            ),
            reason=reason,
        )

    shared = len(left.components)
    names = ", ".join(left.components)
    if left.value == right.value:
        return result(
            comparable=True,
            direction="tied",
            difference=0.0,
            denominator=shared,
            statement=(
                f"{left.label} and {right.label} both score {left.value:.1f} out of "
                f"{SCORE_SCALE_MAX:g} on the same {shared} component(s) ({names}) under one "
                f"weight table. That is a tie at the printed precision of {TIE_DECIMALS} "
                "decimal, not a finding that the two are alike."
            ),
        )

    higher, lower = (left, right) if left.value > right.value else (right, left)
    difference = _printed(abs(left.value - right.value))
    return result(
        comparable=True,
        direction="a_higher" if higher is left else "b_higher",
        higher=higher.label,
        lower=lower.label,
        difference=difference,
        denominator=shared,
        statement=(
            f"{higher.label} scores higher than {lower.label}: {higher.value:.1f} against "
            f"{lower.value:.1f} out of {SCORE_SCALE_MAX:g}, a difference of {difference:.1f} "
            f"point(s), both computed over the same {shared} component(s) ({names}) under one "
            "weight table. What is higher is that weighted mean; the sentence says nothing "
            "beyond it."
        ),
    )
