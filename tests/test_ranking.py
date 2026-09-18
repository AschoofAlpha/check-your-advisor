#!/usr/bin/env python3
"""
`profile/ranking.py`: the three things round one refused, and the line that did
not move with them.

Round one drew a boundary and argued for it at length: a value may be printed, a
position may not. Round two moved that boundary on the user's instruction, and
this file is the moved boundary written as assertions. Three features are now
expected to work, and each one is tested for the specific way it could go wrong:

  - **Rank.** Standard competition ranking (1, 2, 2, 4), ties decided on the
    score *as printed*, every ranked row carrying the count it was ranked among.
    The failure mode is a tie broken by float noise: two rows both showing 45.0,
    one placed above the other because the sixteenth decimal differed.
  - **Stars.** Five equal bands over 0-100, edges declared as module constants
    and printed with the result. The failure mode is a band table that drifts
    away from the constant it claims to be derived from, or a zero-star band
    that reads as a verdict on somebody who did score.
  - **Letters.** The same five bands spelled with a letter instead of a star,
    derived from `STAR_BANDS` rather than cut again, on the user's instruction.
    The failure mode is a second cut: a letter table with edges of its own, which
    would put one score in the fourth band by one coarsening and the third by the
    other and leave a reader to guess which of the two the page meant.
  - **Direction.** "A scores higher than B", refused outright when the two
    numbers were not built the same way. The failure mode is the polite version:
    printing the difference anyway, under a heading that says the two are not
    comparable, where the number is read as the answer and the heading as
    throat-clearing.

And the line that did not move, asserted rather than assumed:

  - no percentile and no quantile, because there is no reference population;
  - no trend and no fitted slope, unchanged from round one;
  - no ordering of people anywhere. A rank is a position among the corpora on one
    page and says so; a letter is a position on a fixed scale and is not a
    position among anybody at all. Both are checked here by walking the whole
    returned structure, not by grepping prose.

Pure computation: no file access, no network, standard library only.

Run: python tests/test_ranking.py
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor.profile import ranking  # noqa: E402
from check_your_advisor.profile.ranking import (  # noqa: E402
    COMPARISON_CAVEAT,
    LETTER_BANDS,
    LETTER_BASIS,
    LETTER_SCALE_NOTE,
    LETTER_SYMBOLS,
    MIN_RANKED_CORPORA,
    RANKING_EXCLUSIONS,
    RANK_METHOD,
    RANK_TIE_NOTE,
    SCORE_SCALE_MAX,
    STAR_BAND_WIDTH,
    STAR_BANDS,
    STAR_BASIS,
    STAR_MAX,
    STAR_SCALE_NOTE,
    TIE_DECIMALS,
    comparative_statement,
    letter_grade,
    rank_corpora,
    star_rating,
)
from check_your_advisor.profile.scoring import composite_score  # noqa: E402

_passed = 0
_failed = 0


def check(label: str, actual, expected) -> None:
    global _passed, _failed
    if actual == expected:
        _passed += 1
        print(f"  [PASS] {label}")
    else:
        _failed += 1
        print(f"  [FAIL] {label}  (expected {expected!r}, got {actual!r})")


def check_true(label: str, cond) -> None:
    check(label, bool(cond), True)


def check_false(label: str, cond) -> None:
    check(label, bool(cond), False)


def raises(fn, *args, **kw) -> str | None:
    try:
        fn(*args, **kw)
    except Exception as exc:  # noqa: BLE001 - the type is the assertion
        return type(exc).__name__
    return None


# ============================================================
# Fixtures: real composite_score results, not hand-written dicts
# ============================================================
#
# The scores below come out of `composite_score` rather than being typed in, so
# that a change to the scoring arithmetic surfaces here as a moved rank instead
# of passing against a stale constant. Only the lead-slot numerator varies; the
# other five components are held fixed, which makes the resulting scores
# predictable without pinning them:
#
#     score(k) = round(100 * (k/20 + 2.20) / 6, 1)
#
# 2.20 is the sum of the five fixed normalised components (0.75 + 0.75 + 0.25 +
# 0.20 + 0.25) and 6 is the flat-weighted denominator.

S3B = {"denominator": 15, "lag_years": 2,
       "counts": {"holds_lead": 9, "observed_without_lead": 3, "too_recent": 3}}  # 0.75
S4 = {"denominator": 8, "median": 1.0, "count_at_zero": 2, "still_without_lead": ["x"],
      "suppressed": False, "not_computable": False}                              # 0.75
S9 = {"denominator": 30,
      "years": [{"year": 2021, "count": 2, "partial": False, "indexing_lag": False},
                {"year": 2022, "count": 2, "partial": False, "indexing_lag": False},
                {"year": 2023, "count": 2, "partial": False, "indexing_lag": False}]}  # 0.25
IMPACT = {"denominator": 10, "covered": 10, "suppressed": False, "not_computable": False,
          "h_index": 3, "i10_index": 2, "total_citations": 60, "median_citations": 5.0,
          "iqr": (2.0, 8.0), "lower_bound": False, "mixed_sources": False,
          "sources": {"openalex": 10}, "generated_at": "2026-01-01T09:30:00"}
#                                              h 3/15 = 0.20, median 5/20 = 0.25


def s3a(lead: int) -> dict:
    """Lead-slot metrics whose normalised value is `lead` / 20."""
    return {"denominator": 20,
            "counts": {"A": lead, "B": 20 - lead, "D": 0, "unclassified": 0},
            "suppressed": False, "not_computable": False}


def score_of(lead: int, **weights) -> dict:
    """A full six-component score whose only moving part is the lead-slot share."""
    return composite_score(
        {"s3a": s3a(lead), "s3b": S3B, "s4": S4, "s9": S9, "impact": IMPACT},
        weights or None,
    )


def expected(lead: int) -> float:
    return round(100.0 * (lead / 20 + 2.20) / 6, 1)


def entry(label: str, lead: int, source: str = "", **weights) -> dict:
    """A `build_comparison` corpus entry, which is what rank_corpora is handed."""
    return {"label": label, "source": source or f"dir/{label}", "refused": False,
            "gate": None, "score": score_of(lead, **weights)}


def refused_entry(label: str, gate_id: str = "G2", name: str = "identity fallback") -> dict:
    return {"label": label, "source": f"dir/{label}", "refused": True,
            "gate": {"id": gate_id, "name": name}, "score": None}


def warned_entry(label: str, lead: int = 10, warning_id: str = "G2",
                 name: str = "identity fallback") -> dict:
    """A corpus whose report was built but whose identity is unconfirmed.

    G2 and G3 stopped refusing the report. What they did not stop doing is
    withholding a position: a rank is a claim about one person, and this corpus
    may hold several. The score is in the row and the row is on the page.
    """
    return {"label": label, "source": f"dir/{label}", "refused": False, "gate": None,
            "warnings": [{"id": warning_id, "name": name}], "score": score_of(lead)}


def suppressed_entry(label: str) -> dict:
    """One component only, which is below MIN_SCORED_COMPONENTS, so no total."""
    return {"label": label, "source": f"dir/{label}", "refused": False, "gate": None,
            "score": composite_score({"s3a": s3a(10)})}


# The four fixed scores this file leans on, checked once so that every rank
# assertion below is anchored to a number rather than to another assertion.
print("the fixture scores")

for _lead in (0, 4, 10, 16, 20):
    check(f"a corpus with lead={_lead} scores {expected(_lead)}",
          score_of(_lead)["score"], expected(_lead))
check("the five fixtures are five distinct scores",
      len({score_of(k)["score"] for k in (0, 4, 10, 16, 20)}), 5)


# ============================================================
# 1. Rank — position among the corpora on the page, and nothing wider
# ============================================================

print("\nrank")

result = rank_corpora([entry("Bravo", 20), entry("Alpha", 10), entry("Charlie", 0)])

check("every corpus takes a position", [row["rank"] for row in result["ranked"]], [1, 2, 3])
check("highest score first",
      [row["label"] for row in result["ranked"]], ["Bravo", "Alpha", "Charlie"])
check("the score beside each rank is the score that was handed in",
      [row["score"] for row in result["ranked"]],
      [expected(20), expected(10), expected(0)])
check("nothing was recomputed on the way through",
      [row["score"] for row in result["ranked"]],
      [e["score"]["score"] for e in (entry("b", 20), entry("a", 10), entry("c", 0))])
check("no corpus is left unranked", result["unranked"], [])
check("n_ranked and the denominator agree when every corpus scored",
      (result["n_ranked"], result["denominator"]), (3, 3))
check("every ranked row says what its position is a position among",
      {row["of"] for row in result["ranked"]}, {3})
check("`entries` is ranked followed by unranked, so one pass prints everything",
      [row["label"] for row in result["entries"]], ["Bravo", "Alpha", "Charlie"])
check_false("three scored corpora are not suppressed", result["suppressed"])
check("the method is returned in words for printing beside the column",
      result["method"], RANK_METHOD)
check_true("...and it names the population the position was taken over",
           "among the corpora handed to this call and no wider set" in RANK_METHOD)
check("the caveat travels with the ranks", result["caveat"], COMPARISON_CAVEAT)
check_true("...and it says a rank across fields is meaningless even when correct",
           "the arithmetic is sound and the subject is not" in COMPARISON_CAVEAT)

# Every ranked row carries its star band, so a renderer cannot print a rank
# without the number and the coarsening that produced it.
check("each ranked row carries the star band of its own score",
      [row["stars"] for row in result["ranked"]],
      [star_rating(score_of(k))["stars"] for k in (20, 10, 0)])
check("...and the same band spelled as a letter",
      [row["letter"] for row in result["ranked"]],
      [letter_grade(score_of(k))["letter"] for k in (20, 10, 0)])
# One cut, two spellings. A row whose letter disagreed with its star count would
# be two coarsenings of one number rather than one coarsening printed twice.
check("the letter on a row is the star band of that same row, never a second cut",
      [row["letter"] for row in result["ranked"]],
      [LETTER_SYMBOLS[row["stars"] - 1] for row in result["ranked"]])


# --- ties ---------------------------------------------------------------

print("\nties")

tied = rank_corpora([
    entry("Delta", 20), entry("Bravo", 10), entry("alpha", 10), entry("Charlie", 0),
])
check("equal scores share one rank", [row["rank"] for row in tied["ranked"]], [1, 2, 2, 4])
check("...and the rank after a tie is skipped, never made consecutive",
      3 in [row["rank"] for row in tied["ranked"]], False)
# Case-folded, so "alpha" sorts before "Bravo" rather than after it: a raw
# byte sort would put every capitalised label above every lowercase one and
# read as a second criterion nobody declared.
check("within a tie the order is by label, case-folded",
      [row["label"] for row in tied["ranked"]], ["Delta", "alpha", "Bravo", "Charlie"])
check("each tied row names who it is tied with",
      {row["label"]: row["tied_with"] for row in tied["ranked"] if row["rank"] == 2},
      {"alpha": ["Bravo"], "Bravo": ["alpha"]})
check("rows that are not tied say so with an empty list",
      [row["tied_with"] for row in tied["ranked"] if row["rank"] in (1, 4)], [[], []])
check("the tie note is returned, and says the sequence is not consecutive",
      tied["tie_note"], RANK_TIE_NOTE)
check_true("...and that alphabetical order inside a tie means nothing",
           "carries no meaning whatsoever" in RANK_TIE_NOTE)

# The specific indefensible output TIE_DECIMALS exists to close: two rows
# printing the same number, ranked differently because the float underneath them
# differed far below the printed precision.
base = score_of(10)
noisy = {**base, "score": base["score"] + 1e-9}
check("tie_decimals is reported so a reader knows what 'equal' meant",
      tied["tie_decimals"], TIE_DECIMALS)
invisible = rank_corpora([
    {"label": "Same A", "source": "a", "score": base},
    {"label": "Same B", "source": "b", "score": noisy},
])
check("two rows printing the same score take the same rank",
      [row["rank"] for row in invisible["ranked"]], [1, 1])
check("...and both print that same score",
      len({row["score"] for row in invisible["ranked"]}), 1)


# --- what does not take a position -------------------------------------

print("\nwhat is not ranked, and why not")

mixed = rank_corpora([
    entry("Scored one", 20), entry("Scored two", 0),
    refused_entry("Refused corpus", "G2", "identity fallback"),
    suppressed_entry("Suppressed corpus"),
])
check("only the corpora with a score take positions", mixed["n_ranked"], 2)
check("the others keep their row", mixed["n_unranked"], 2)
check("the denominator counts everything handed in, not everything ranked",
      (mixed["denominator"], mixed["n_ranked"]), (4, 2))
check("nothing was dropped", len(mixed["entries"]), 4)
check("an unranked corpus holds no position at all",
      [row["rank"] for row in mixed["unranked"]], [None, None])
check("...and is not placed last, because last is a position",
      [row["ranked"] for row in mixed["unranked"]], [False, False])
check("...and is never scored as zero",
      [row["score"] for row in mixed["unranked"]], [None, None])

by_label = {row["label"]: row for row in mixed["unranked"]}
check_true("a refused corpus names its gate in the reason",
           "gate G2" in by_label["Refused corpus"]["reason"]
           and "identity fallback" in by_label["Refused corpus"]["reason"])
check("...and carries the gate itself for a renderer",
      by_label["Refused corpus"]["gate"], {"id": "G2", "name": "identity fallback"})
check_true("a suppressed score says how many components it had and the floor",
           "suppressed at 1 scored component(s), floor 3"
           in by_label["Suppressed corpus"]["reason"])
# A score suppressed at two components and a corpus with no data at all are
# different rows, and printing 0 for both would erase the difference.
check("...and its component count survives the suppression",
      by_label["Suppressed corpus"]["n_components"], 1)
check("a refused corpus has no components to report",
      by_label["Refused corpus"]["n_components"], 0)

# The identity warnings that used to be gates. Rendering the report was a
# decision about the report; it was not a decision to let a corpus that may hold
# several people take a position beside corpora that hold one.
warned = rank_corpora([
    entry("Clean one", 20), entry("Clean two", 4), warned_entry("Unverified corpus"),
])
check("a warned corpus takes no position", warned["n_ranked"], 2)
check("...but keeps its row", warned["n_unranked"], 1)
check("...over a denominator that still counts it", warned["denominator"], 3)
_warned_row = warned["unranked"][0]
check("...holding no rank at all", _warned_row["rank"], None)
check("...and never scored as zero", _warned_row["score"], None)
check_true("...with a reason naming the warning",
           "G2 (identity fallback)" in _warned_row["reason"])
check_true("...and saying why a rank in particular is withheld",
           "a rank compares corpora that are each complete and each about one person"
           in _warned_row["reason"])
# The reason names the warning and stops there. It used to assert "so it may
# describe more than one researcher", which is what G2 and G3 mean and is not
# what G1 means — a harvest that retrieved 500 of 900 records for one person is
# still one person, and that row said otherwise.
check("...without asserting the corpus holds several people",
      "may describe more than one researcher" in _warned_row["reason"], False)
check("...while its component count survives, because the score was computed",
      _warned_row["n_components"] > 0, True)
check("both warnings are named when both fired",
      "G3 (weak identity config)" in rank_corpora([
          entry("A", 20), entry("B", 4),
          {"label": "Both", "source": "dir/Both", "refused": False, "gate": None,
           "score": score_of(10),
           "warnings": [{"id": "G2", "name": "identity fallback"},
                        {"id": "G3", "name": "weak identity config"}]},
      ])["unranked"][0]["reason"], True)
check("an empty warnings list is not a warning",
      rank_corpora([entry("A", 20),
                    {"label": "B", "source": "dir/B", "refused": False, "gate": None,
                     "warnings": [], "score": score_of(4)}])["n_ranked"], 2)


# --- the floor ----------------------------------------------------------

print("\nthe floor: a rank needs a set to be a position in")

check("the floor is a declared constant", MIN_RANKED_CORPORA, 2)
alone = rank_corpora([entry("Only corpus", 10)])
check_true("one corpus is suppressed rather than crowned", alone["suppressed"])
check("...so nothing is ranked", alone["ranked"], [])
check("...and the single corpus keeps its row", len(alone["unranked"]), 1)
check("...with its score intact, because the score was never in doubt",
      alone["unranked"][0]["score"], expected(10))
check_true("...and a reason naming the floor",
           f"floor {MIN_RANKED_CORPORA}" in alone["unranked"][0]["reason"])
check_true("...phrased as arithmetic over a set of one, not as an achievement",
           "nothing to be first among" in alone["unranked"][0]["reason"])
check("the floor is returned so a report can print it",
      alone["min_ranked_corpora"], MIN_RANKED_CORPORA)

check_true("an empty page is suppressed too", rank_corpora([])["suppressed"])
check("...over a denominator of zero", rank_corpora([])["denominator"], 0)
# One scored corpus beside one refused corpus is still one scored corpus.
one_of_two = rank_corpora([entry("Scored", 10), refused_entry("Refused")])
check_true("a page whose second corpus was refused is suppressed", one_of_two["suppressed"])
check("...and both rows come back unranked", one_of_two["n_unranked"], 2)

# How many corpora carried a score is a different question from how many took a
# position, and the two answers diverge exactly when the ranking is suppressed.
# Both renderers narrated "N corpora carried a score" from `n_ranked`, which is 0
# here by construction: the compare page printed "0 corpora that carried a score"
# directly above a row reading "only 1 corpus/corpora on this page carried a
# score, floor 2". One page, two numbers, one input.
check("a suppressed page still reports how many corpora carried a score",
      one_of_two["n_scored"], 1)
check("...while nothing took a position", one_of_two["n_ranked"], 0)
check_true("...so the two fields disagree, which is why the narration needs n_scored",
           one_of_two["n_scored"] != one_of_two["n_ranked"])
# The refused row explains itself with its gate; the scored-but-unplaceable row
# is the one carrying the floor message, so pick it by its reason rather than by
# position — `unranked` is sorted by label and "Refused" sorts first.
_floor_reasons = [row["reason"] for row in one_of_two["unranked"]
                  if "carried a score" in row["reason"]]
check("exactly one unranked row explains itself by the floor", len(_floor_reasons), 1)
check("the count in that row's reason agrees with n_scored",
      int(re.search(r"only (\d+) corpus", _floor_reasons[0]).group(1)),
      one_of_two["n_scored"])
check("one corpus alone reports itself as scored", alone["n_scored"], 1)
check("an empty page has nothing scored", rank_corpora([])["n_scored"], 0)
# Unsuppressed, the two agree — so a renderer reading either one is right, and
# only the suppressed case ever exposed the bug.
_both = rank_corpora([entry("A", 20), entry("B", 10)])
check("with a real ranking the two counts agree", (_both["n_scored"], _both["n_ranked"]), (2, 2))


# --- comparability, which is reported and not enforced ------------------

print("\ncomparability of the ranked numbers")

# `impact` absent removes the citation pair, so this corpus scores on four
# components while a full one scores on six.
four = {"label": "Four components", "source": "d4",
        "score": composite_score({"s3a": s3a(10), "s3b": S3B, "s4": S4, "s9": S9})}
check("the four-component fixture really has four", four["score"]["denominator"], 4)

same = rank_corpora([entry("Full A", 20), entry("Full B", 0)])
check_true("like against like reports comparable", same["comparable"])
check_true("...on both axes", same["comparability"]["component_sets_match"]
           and same["comparability"]["weight_tables_match"])
check_true("...and says so in a sentence the report can print",
           "means over like inputs" in same["comparability"]["note"])

unlike = rank_corpora([entry("Full", 20), four])
check_true("four components against six still produces ranks", unlike["n_ranked"] == 2)
check_false("...but the result is flagged as not comparable", unlike["comparable"])
check_false("...on the component axis", unlike["comparability"]["component_sets_match"])
check_true("...with both component sets listed, so the reader sees what differs",
           sorted(len(names) for names in unlike["comparability"]["component_sets"]) == [4, 6])
check_true("...and a note that a mean over four and a mean over six share a scale only",
           "share a scale and not a meaning" in unlike["comparability"]["note"])
check("every ranked row carries its own component count for the same reason",
      sorted(row["n_components"] for row in unlike["ranked"]), [4, 6])

reweighted = rank_corpora([entry("Flat", 10), entry("Heavy", 10, time_to_lead=2.0)])
check_false("two weight tables on one page is not comparable either",
            reweighted["comparable"])
check_false("...flagged on the weight axis",
            reweighted["comparability"]["weight_tables_match"])
check_true("...and named as such",
           "more than one weight table" in reweighted["comparability"]["note"])


# --- what rank_corpora will not accept ----------------------------------

print("\ninput shapes")

check("the corpora list from build_comparison is accepted",
      rank_corpora([entry("A", 20), entry("B", 0)])["n_ranked"], 2)
check("a whole comparison dict is unwrapped to its corpora",
      rank_corpora({"corpora": [entry("A", 20), entry("B", 0)]})["n_ranked"], 2)
check("bare composite_score results are accepted and labelled by position",
      [row["label"] for row in rank_corpora([score_of(20), score_of(0)])["ranked"]],
      ["corpus 1", "corpus 2"])
check("a mapping that is neither a comparison nor a score raises",
      raises(rank_corpora, {"score": 50.0}), "TypeError")
check("a single score result is not a page of one", raises(rank_corpora, score_of(10)), "TypeError")
check("a string is not a sequence of corpora", raises(rank_corpora, "Alpha"), "TypeError")
check("a number is not a sequence of corpora", raises(rank_corpora, 42), "TypeError")


# ============================================================
# 2. Stars — the score coarsened, on declared anchors
# ============================================================

print("\nstar anchors")

check("five bands", STAR_MAX, 5)
check("...over the score's own scale", SCORE_SCALE_MAX, 100.0)
check("...each one band-width wide", STAR_BAND_WIDTH, 20.0)
check("the band table is derived from the two constants, not typed out",
      STAR_BANDS, tuple((20.0 * (n - 1), n) for n in range(5, 0, -1)))
check("highest band first", [stars for _, stars in STAR_BANDS], [5, 4, 3, 2, 1])
check("every band is the same width",
      {round(STAR_BANDS[i - 1][0] - STAR_BANDS[i][0], 6) for i in range(1, len(STAR_BANDS))},
      {STAR_BAND_WIDTH})
# Unequal bands would encode a belief about where the interesting differences
# lie, and nothing in this data supports one. Equal width is the same argument
# `scoring.DEFAULT_WEIGHTS` makes for a flat table.
check("the lowest band is worth one star, so the scale has no null verdict",
      min(stars for _, stars in STAR_BANDS), 1)
check("...and it starts at zero, so a corpus that scored always lands somewhere",
      min(lower for lower, _ in STAR_BANDS), 0.0)

# An anchor that is not printed is an anchor nobody can disagree with. Same rule
# `scoring` applies to its normalisation anchors.
check_true("every band edge appears in the printable note",
           all(f"{stars}★ = " in STAR_SCALE_NOTE for _, stars in STAR_BANDS))
check("the note spells the edges out in full",
      STAR_SCALE_NOTE, "1★ = 0-20; 2★ = 20-40; 3★ = 40-60; 4★ = 60-80; 5★ = 80-100")
check_true("the basis carries the note, so the argument travels with the rating",
           STAR_SCALE_NOTE in STAR_BASIS)
check_true("...and states that no edge was measured off a group of researchers",
           "not cut points measured off any group of researchers" in STAR_BASIS)

print("\nstar boundaries")

for value, stars in ((0.0, 1), (19.9, 1), (20.0, 2), (39.9, 2), (40.0, 3),
                     (60.0, 4), (79.9, 4), (80.0, 5), (100.0, 5)):
    check(f"{value} falls in band {stars}", star_rating(value)["stars"], stars)
check("a band is closed at the bottom and open at the top",
      (star_rating(20.0)["band"], star_rating(19.9)["band"]), ([20.0, 40.0], [0.0, 20.0]))
check("the top band is closed at the scale maximum", star_rating(100.0)["band"], [80.0, 100.0])
check_true("...and says so, so 100 is not read as falling off the end",
           star_rating(100.0)["band_closed_at_top"])
check_false("a lower band is not", star_rating(50.0)["band_closed_at_top"])
# Ties are decided on the printed value everywhere in this module, bands
# included, so a score a reader sees as 20.0 cannot take the band below it.
check("the band is decided on the score as printed, like every other comparison",
      star_rating(19.96)["stars"], 2)

print("\nwhat a rating carries with it")

rated = star_rating(score_of(20))
check("a composite_score result rates to the same number it printed",
      rated["score"], expected(20))
check("...and reports the components that score rested on",
      rated["denominator"], score_of(20)["denominator"])
check("...and how many were registered, so the gap is visible",
      rated["components_registered"], score_of(20)["components_registered"])
check("the full band table comes back with every rating",
      rated["bands"], [list(edge) for edge in STAR_BANDS])
check("...and the band width", rated["band_width"], STAR_BAND_WIDTH)
check("...and the basis verbatim", rated["basis"], STAR_BASIS)
check("...and the caveat", rated["caveat"], COMPARISON_CAVEAT)
check("a bare float cannot say what it was computed over",
      star_rating(50.0)["denominator"], None)

print("\nno rating, and the shape it comes back in")

blank = star_rating(None)
check("no score means no stars", blank["stars"], None)
check("...not zero stars, which would read as a verdict", blank["stars"] is None, True)
check_true("...with the reason attached", "no composite score was supplied" in blank["unavailable"])
# One shape for both paths, so a renderer's `.get("bands")` cannot come back
# None for a band table that was never in question.
check("a rating that exists and one that does not carry the same keys",
      sorted(blank), sorted(rated))
check("...including the band table", blank["bands"], rated["bands"])

held = star_rating(composite_score({"s3a": s3a(10)}))
check("a suppressed score is not coarsened into stars", held["stars"], None)
check_true("...and the suppression is reported as suppression", held["suppressed"])
check_true("...naming the count and the floor",
           "suppressed at 1 scored component(s), floor 3" in held["unavailable"])
check("...while the component count survives", held["denominator"], 1)
check_false("a rated score is not flagged suppressed", rated["suppressed"])

print("\nwhat star_rating refuses")

check("a score above the scale raises", raises(star_rating, 100.1), "ValueError")
check("a negative score raises", raises(star_rating, -0.1), "ValueError")
check("a NaN raises", raises(star_rating, float("nan")), "ValueError")
check("an infinity raises", raises(star_rating, float("inf")), "ValueError")
check("a value that is not a number at all raises", raises(star_rating, object()), "ValueError")
# A string is a Sequence, so it is turned away by the sequence guard below
# rather than by the numeric one. Recorded as the behaviour it is: the input is
# still refused, just under the other of the two messages.
check("a string is refused as a sequence, not parsed as a number",
      raises(star_rating, "excellent"), "TypeError")
# A star count is a restatement of one number. Anything that turned several into
# stars at once would be rank_corpora wearing a different hat, and would produce
# an ordering without the sentence saying what it was an ordering among.
check("a list of scores raises", raises(star_rating, [50.0, 60.0]), "TypeError")
check("a tuple of scores raises", raises(star_rating, (50.0, 60.0)), "TypeError")
check("an arbitrary mapping raises", raises(star_rating, {"score": 50.0}), "TypeError")


def message(fn, *args) -> str:
    try:
        fn(*args)
    except Exception as exc:  # noqa: BLE001 - the text is the assertion
        return str(exc)
    return ""


check_true("...and the message points at the function that does take a set",
           "rank_corpora" in message(star_rating, [50.0, 60.0]))
check_true("...and at composite_score for the mapping case",
           "composite_score" in message(star_rating, {"score": 50.0}))


# ============================================================
# 2b. Letters — the same bands, spelled differently
# ============================================================
#
# Round one refused letters while producing stars, and recorded the split as
# deliberate. The user has reversed that, so the thing to assert is no longer
# absence but *identity*: a letter must be the star band relabelled, because a
# letter table with edges of its own would be a second, undeclared cut of the
# same number, and the page would carry two coarsenings that can disagree.

print("\nletter anchors")

check("one letter per star band, no more and no fewer", len(LETTER_BANDS), len(STAR_BANDS))
check("the letter bands sit on the star band edges, so the two cannot drift apart",
      [lower for lower, _ in LETTER_BANDS], [lower for lower, _ in STAR_BANDS])
check("the symbols are a declared constant, indexed by the star count",
      LETTER_SYMBOLS, ("E", "D", "C", "B", "A"))
check("highest band first, like the star table",
      [letter for _, letter in LETTER_BANDS], ["A", "B", "C", "D", "E"])
check("the band table is derived from the star table, not typed out",
      LETTER_BANDS,
      tuple((lower, LETTER_SYMBOLS[stars - 1]) for lower, stars in STAR_BANDS))
check("every band is the same width",
      {round(LETTER_BANDS[i - 1][0] - LETTER_BANDS[i][0], 6)
       for i in range(1, len(LETTER_BANDS))},
      {STAR_BAND_WIDTH})
check("...and the lowest starts at zero, so a corpus that scored always lands somewhere",
      min(lower for lower, _ in LETTER_BANDS), 0.0)
# The star scale has no zero-star band for the same reason. "F" is the one letter
# that is read as a verdict rather than as a position on a scale, and the lowest
# band is where a corpus that did score lands.
check("there is no F, so the lowest band is not a verdict on a corpus that scored",
      "F" in {letter for _, letter in LETTER_BANDS}, False)

# An anchor that is not printed is an anchor nobody can disagree with.
check("the note spells the edges out in full",
      LETTER_SCALE_NOTE, "A = 80-100; B = 60-80; C = 40-60; D = 20-40; E = 0-20")
check_true("the basis carries the note, so the argument travels with the grade",
           LETTER_SCALE_NOTE in LETTER_BASIS)
check_true("...and states that no edge was measured off a group of researchers",
           "not cut points measured off any group of researchers" in LETTER_BASIS)
check_true("...and that a letter is therefore not a percentile",
           "not a percentile" in LETTER_BASIS)
check_true("...and that it orders no person",
           "orders no person" in LETTER_BASIS)

print("\nletter boundaries")

for value, letter in ((0.0, "E"), (19.9, "E"), (20.0, "D"), (39.9, "D"), (40.0, "C"),
                      (60.0, "B"), (79.9, "B"), (80.0, "A"), (100.0, "A")):
    check(f"{value} falls in band {letter}", letter_grade(value)["letter"], letter)
check("a band is closed at the bottom and open at the top",
      (letter_grade(20.0)["band"], letter_grade(19.9)["band"]), ([20.0, 40.0], [0.0, 20.0]))
check("the top band is closed at the scale maximum", letter_grade(100.0)["band"], [80.0, 100.0])
check_true("...and says so, so 100 is not read as falling off the end",
           letter_grade(100.0)["band_closed_at_top"])
check("the band is decided on the score as printed, like every other comparison",
      letter_grade(19.96)["letter"], "D")
# The identity the whole design rests on, checked across the scale rather than at
# one point: same input, same band, two spellings.
check("a letter is the star band relabelled, at every band",
      [letter_grade(v)["letter"] for v in (0.0, 25.0, 50.0, 75.0, 100.0)],
      [LETTER_SYMBOLS[star_rating(v)["stars"] - 1]
       for v in (0.0, 25.0, 50.0, 75.0, 100.0)])
check("...and the star count it came from travels with it, so the two can be checked",
      [letter_grade(v)["stars"] for v in (0.0, 50.0, 100.0)],
      [star_rating(v)["stars"] for v in (0.0, 50.0, 100.0)])

print("\nwhat a grade carries with it")

graded = letter_grade(score_of(20))
check("a composite_score result grades to the same number it printed",
      graded["score"], expected(20))
check("...and reports the components that score rested on",
      graded["denominator"], score_of(20)["denominator"])
check("the full band table comes back with every grade",
      graded["bands"], [list(edge) for edge in LETTER_BANDS])
check("...and the band width", graded["band_width"], STAR_BAND_WIDTH)
check("...and the basis verbatim", graded["basis"], LETTER_BASIS)
check("...and the caveat", graded["caveat"], COMPARISON_CAVEAT)
check("a bare float cannot say what it was computed over",
      letter_grade(50.0)["denominator"], None)

print("\nno grade, and the shape it comes back in")

ungraded = letter_grade(None)
check("no score means no letter", ungraded["letter"], None)
# Same convention as `stars=None`: a missing score is not the bottom of the
# scale, and E is where a corpus that scored badly lands, not where a corpus
# nobody could score lands.
check("...not the lowest band, which would read as a verdict on missing data",
      ungraded["letter"] is None, True)
check_true("...with the reason attached",
           "no composite score was supplied" in ungraded["unavailable"])
check("a grade that exists and one that does not carry the same keys",
      sorted(ungraded), sorted(graded))
check("...including the band table", ungraded["bands"], graded["bands"])

held_letter = letter_grade(composite_score({"s3a": s3a(10)}))
check("a suppressed score is not coarsened into a letter", held_letter["letter"], None)
check_true("...and the suppression is reported as suppression", held_letter["suppressed"])
check_true("...naming the count and the floor",
           "suppressed at 1 scored component(s), floor 3" in held_letter["unavailable"])
check("...while the component count survives", held_letter["denominator"], 1)
check_false("a graded score is not flagged suppressed", graded["suppressed"])

print("\nwhat letter_grade refuses")

check("a score above the scale raises", raises(letter_grade, 100.1), "ValueError")
check("a negative score raises", raises(letter_grade, -0.1), "ValueError")
check("a NaN raises", raises(letter_grade, float("nan")), "ValueError")
check("an infinity raises", raises(letter_grade, float("inf")), "ValueError")
check("a value that is not a number at all raises", raises(letter_grade, object()), "ValueError")
# Same split as star_rating: a string is a Sequence, so it is turned away by the
# sequence guard rather than by the numeric one.
check("a string is refused as a sequence, not parsed as a number",
      raises(letter_grade, "excellent"), "TypeError")
check("a list of scores raises", raises(letter_grade, [50.0, 60.0]), "TypeError")
check("a tuple of scores raises", raises(letter_grade, (50.0, 60.0)), "TypeError")
check("an arbitrary mapping raises", raises(letter_grade, {"score": 50.0}), "TypeError")
check_true("...and the message names the function that was called",
           "letter_grade" in message(letter_grade, [50.0, 60.0]))
check_true("...and points at the function that does take a set",
           "rank_corpora" in message(letter_grade, [50.0, 60.0]))


# ============================================================
# 3. Direction — and the refusal to state one
# ============================================================

print("\ndirection")

higher = comparative_statement(entry("Alpha", 20), entry("Bravo", 0))
check_true("a direction is stated when both were built the same way", higher["comparable"])
check("...naming which is higher", (higher["higher"], higher["lower"]), ("Alpha", "Bravo"))
check("...as a direction, in words", higher["direction"], "a_higher")
check("...with the difference in points", higher["difference"],
      round(expected(20) - expected(0), 1))
check_true("...unsigned, so several of them cannot be assembled into an ordering",
           higher["difference"] > 0)
check("the reverse call reverses the direction and not the difference",
      (comparative_statement(entry("Bravo", 0), entry("Alpha", 20))["direction"],
       comparative_statement(entry("Bravo", 0), entry("Alpha", 20))["difference"]),
      ("b_higher", higher["difference"]))
check("the shared component count is the denominator", higher["denominator"], 6)
check("both sides report their own component count",
      (higher["a"]["n_components"], higher["b"]["n_components"]), (6, 6))
check("both sides report their score, so the sentence can be checked",
      (higher["a"]["score"], higher["b"]["score"]), (expected(20), expected(0)))
check_true("the sentence says what is higher", "scores higher than" in higher["statement"])
check_true("...and what that does not mean",
           "the sentence says nothing beyond it" in higher["statement"])
check("the caveat is returned on the comparable path too, where it is most needed",
      higher["caveat"], COMPARISON_CAVEAT)

tie = comparative_statement(entry("Alpha", 10), entry("Bravo", 10))
check("equal scores are a tie, not a direction", tie["direction"], "tied")
check_true("...still comparable", tie["comparable"])
check("...with a zero difference rather than a missing one", tie["difference"], 0.0)
check("...and nobody named higher or lower", (tie["higher"], tie["lower"]), (None, None))
check_true("...and the tie is stated as a tie at the printed precision",
           f"tie at the printed precision of {TIE_DECIMALS} decimal" in tie["statement"])
check_true("...not as a finding that the two are alike",
           "not a finding that the two are alike" in tie["statement"])


print("\nrefusing to compare")

# The heart of it: a score over four components and a score over six are two
# different measurements on one scale. The subtraction is available and is
# withheld, because a number under a "not comparable" heading is read as the
# answer.
mismatch = comparative_statement(entry("Six", 10), four)
check_false("four components against six is not comparable", mismatch["comparable"])
check("...so no direction is stated", mismatch["direction"], None)
check("...and no difference is printed, although it could be computed",
      mismatch["difference"], None)
check("...and nobody is named higher", (mismatch["higher"], mismatch["lower"]), (None, None))
check("...and there is no shared denominator to report", mismatch["denominator"], None)
check_true("the reason names both component counts",
           "scored on 6 component(s)" in mismatch["reason"]
           and "on 4 (" in mismatch["reason"])
check_true("the statement is still safe to print",
           "are not directly comparable" in mismatch["statement"])
check_true("...and says why the difference was withheld rather than missing",
           "The difference is not printed here on purpose" in mismatch["statement"])
check_true("...and says how to get an answer instead of leaving a dead end",
           "setting the others to weight 0.0" in mismatch["statement"])
check("both sides still report their own counts, so the refusal can be checked",
      (mismatch["a"]["n_components"], mismatch["b"]["n_components"]), (6, 4))

# Equal counts are not enough. Set identity is required: five components of
# citations and five of lead-slot timing are no more comparable than four
# against six, and arguably less.
left_four = {"label": "Lead-shaped", "source": "l",
             "score": composite_score({"s3a": s3a(10), "s3b": S3B, "s4": S4, "s9": S9})}
right_four = {"label": "Citation-shaped", "source": "r",
              "score": composite_score({"s3a": s3a(10), "s3b": S3B, "impact": IMPACT})}
check("the two four-component fixtures really do have four each",
      (left_four["score"]["denominator"], right_four["score"]["denominator"]), (4, 4))
check("...over different components",
      sorted(c["name"] for c in left_four["score"]["components"])
      == sorted(c["name"] for c in right_four["score"]["components"]), False)
equal_counts = comparative_statement(left_four, right_four)
check_false("equal counts over different components is still refused",
            equal_counts["comparable"])
check("...with no difference printed", equal_counts["difference"], None)
check_true("...and the reason lists the component names, not just the counts",
           "records_per_year" in equal_counts["reason"]
           and "citation_h_index" in equal_counts["reason"])

reweighted_pair = comparative_statement(entry("Flat", 10), entry("Heavy", 10, time_to_lead=2.0))
check_false("two weight tables is refused", reweighted_pair["comparable"])
check("...with no difference", reweighted_pair["difference"], None)
check_true("...naming the tables as the reason",
           "different weight tables" in reweighted_pair["reason"])
check_true("...and pointing at build_comparison, which applies one table to all",
           "build_comparison applies a single table" in reweighted_pair["statement"])

for label, blocked in (("a refused report", refused_entry("Refused")),
                       ("a suppressed score", suppressed_entry("Suppressed")),
                       ("no score at all", {"label": "Empty", "source": "e", "score": None})):
    verdict = comparative_statement(entry("Fine", 10), blocked)
    check(f"{label} on one side refuses the comparison", verdict["comparable"], False)
    check(f"...{label}: with no difference", verdict["difference"], None)
    check_true(f"...{label}: and says it is a statement about the data",
               "this is a statement about the data, not about the corpora"
               in verdict["statement"])

check("bare composite_score results are labelled corpus A and corpus B",
      [comparative_statement(score_of(20), score_of(0))[side]["label"] for side in ("a", "b")],
      ["corpus A", "corpus B"])
check("a non-mapping raises rather than comparing nothing",
      raises(comparative_statement, [score_of(20)], score_of(0)), "TypeError")


# ============================================================
# 4. The line that did not move
# ============================================================

print("\nthe line that did not move")

# Every payload this module can produce, walked as one structure. A new field
# cannot quietly introduce a position or a letter without failing here.
PAYLOADS = {
    "rank_corpora": rank_corpora([entry("Alpha", 20), entry("Bravo", 10),
                                  entry("Charlie", 10), refused_entry("Delta")]),
    "star_rating": star_rating(score_of(20)),
    "star_rating (none)": star_rating(None),
    "letter_grade": letter_grade(score_of(20)),
    "letter_grade (none)": letter_grade(None),
    "comparative_statement": comparative_statement(entry("Alpha", 20), entry("Bravo", 0)),
    "comparative_statement (refused)": comparative_statement(entry("Six", 10), four),
}


def walk(node, path=""):
    """Every (path, key, value) in a nested structure."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield path, str(key), value
            yield from walk(value, f"{path}.{key}")
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            yield from walk(value, f"{path}[{index}]")


# Keys, not prose. `RANKING_EXCLUSIONS` and the caveats have to name these
# quantities in order to refuse them, so a bare-word scan would read the refusal
# as the offence. A field named `percentile` is the offence.
#
# "grade" and "letter" came off this list when the letter band was added. They
# were here to assert an absence; the absence is gone, and a ban that outlives
# the feature it banned passes for the wrong reason.
BANNED_KEYS = ("percentile", "quantile", "trend", "slope")
for name, payload in PAYLOADS.items():
    offending = sorted({key for _, key, _ in walk(payload)
                        if any(word in key.lower() for word in BANNED_KEYS)})
    check(f"{name} exposes no percentile, quantile, trend or slope field",
          offending, [])

check("the module exports no helper for any of them",
      [n for n in dir(ranking)
       if not n.startswith("_")
       and any(w in n.lower() for w in ("percentile", "quantile", "trend", "slope"))],
      [])

# 字母等第 — produced now, and the assertion is that it is the star band relabelled
# rather than a second cut. Both coarsenings are walked out of the same payloads
# above, so a letter that disagreed with its own star count would fail here.
for name in ("rank_corpora", "letter_grade"):
    rows = (PAYLOADS[name]["ranked"] if name == "rank_corpora" else [PAYLOADS[name]])
    check(f"{name}: every letter is the letter of its own star band",
          [row["letter"] for row in rows],
          [LETTER_SYMBOLS[row["stars"] - 1] for row in rows])
check("tied corpora take one letter, because they took one score",
      len({row["letter"] for row in PAYLOADS["rank_corpora"]["ranked"]
           if row["rank"] == 2}), 1)
# Stars did not go away when letters arrived. One number, one cut, two spellings,
# and both are printed so neither can be quietly re-cut.
check("stars are still produced beside the letters",
      (PAYLOADS["star_rating"]["stars"], PAYLOADS["star_rating"]["max_stars"]),
      (star_rating(expected(20))["stars"], STAR_MAX))
check("...and a payload with no score has neither, rather than the bottom of each",
      (PAYLOADS["star_rating (none)"]["stars"], PAYLOADS["letter_grade (none)"]["letter"]),
      (None, None))

print("\nthe exclusions register")

check("the register keeps two lists, for two different reasons",
      sorted(RANKING_EXCLUSIONS), ["not_computable_here", "refused_by_design"])
refused_by_design = dict(RANKING_EXCLUSIONS["refused_by_design"])
not_computable = dict(RANKING_EXCLUSIONS["not_computable_here"])
# The register said letter tiers were refused by decision. That decision was
# reversed by the user, letters are produced, and the entry is gone — a register
# that still refused a feature the module emits would be the exact drift it
# exists to prevent, and deleting the entry is how it stays true.
check("nothing in the register still refuses letters",
      [name for name in refused_by_design if "etter" in name], [])
check("...and nothing refuses tiers under another word either",
      [name for name in refused_by_design if "ier" in name], [])
# Round four narrowed this entry rather than deleting it. `profile/trends.py`
# now fits a slope for Section 9, so an entry reading "trends are refused" flat
# would be false; what remains true, and is what this register is for, is that
# nothing *here* reads one. The heading carries that scope now, and these
# assertions hold it to the scope rather than to the old flat wording.
_trend_entry = [name for name in refused_by_design if name.startswith("Trends, fitted slopes")]
check("the register still speaks to trends", len(_trend_entry), 1)
check_true("...scoped to what a rank reads, not to their existence",
           "as anything a rank reads" in _trend_entry[0])
check_true("...keeping round one's objection verbatim",
           "unchanged from round one" in refused_by_design[_trend_entry[0]])
check_true("...and saying plainly that no rank moves on a direction",
           "No corpus outranks another for having risen" in refused_by_design[_trend_entry[0]])
# The distinction the register exists to keep: a percentile is not refused here,
# it is uncomputable here, and a better data source would change that answer
# where it would not change the two above.
check("a position inside a reference population is the other kind of absence",
      "A position inside a reference population" in not_computable, True)
check_true("...because no reference population exists",
           "there is no reference population"
           in not_computable["A position inside a reference population"])
check_true("...and the rank that *is* produced says what it is a position among",
           "position among the loaded few"
           in not_computable["A position inside a reference population"])


print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(1 if _failed else 0)
