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
  - **Direction.** "A scores higher than B", refused outright when the two
    numbers were not built the same way. The failure mode is the polite version:
    printing the difference anyway, under a heading that says the two are not
    comparable, where the number is read as the answer and the heading as
    throat-clearing.

And the line that did not move, asserted rather than assumed:

  - no percentile and no quantile, because there is no reference population;
  - no trend and no fitted slope, unchanged from round one;
  - **no letter grade.** Stars are produced and letters are not. That is one
    number coarsened two ways and split deliberately, on the user's instruction,
    and `RANKING_EXCLUSIONS` records the split so that a later reader does not
    tidy it away by unifying them. It is the only prohibition round two adds, so
    it is checked here by walking the whole returned structure, not by grepping
    prose.

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


def refused_entry(label: str, gate_id: str = "G1", name: str = "truncation") -> dict:
    return {"label": label, "source": f"dir/{label}", "refused": True,
            "gate": {"id": gate_id, "name": name}, "score": None}


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
    refused_entry("Refused corpus", "G1", "truncation"),
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
           "gate G1" in by_label["Refused corpus"]["reason"]
           and "truncation" in by_label["Refused corpus"]["reason"])
check("...and carries the gate itself for a renderer",
      by_label["Refused corpus"]["gate"], {"id": "G1", "name": "truncation"})
check_true("a suppressed score says how many components it had and the floor",
           "suppressed at 1 scored component(s), floor 3"
           in by_label["Suppressed corpus"]["reason"])
# A score suppressed at two components and a corpus with no data at all are
# different rows, and printing 0 for both would erase the difference.
check("...and its component count survives the suppression",
      by_label["Suppressed corpus"]["n_components"], 1)
check("a refused corpus has no components to report",
      by_label["Refused corpus"]["n_components"], 0)


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
BANNED_KEYS = ("percentile", "quantile", "trend", "slope", "grade", "letter")
for name, payload in PAYLOADS.items():
    offending = sorted({key for _, key, _ in walk(payload)
                        if any(word in key.lower() for word in BANNED_KEYS)})
    check(f"{name} exposes no percentile, quantile, trend, slope or grade field",
          offending, [])

check("the module exports no helper for any of them",
      [n for n in dir(ranking)
       if not n.startswith("_")
       and any(w in n.lower() for w in ("percentile", "quantile", "trend", "slope", "grade"))],
      [])

# 字母等级 — the one prohibition round two adds, checked as a value rather than
# as a word, for the same reason the key scan above is a key scan.
LETTER_GRADE = re.compile(
    r"(?i:grade|tier|band|等级|评级)\s*\d*\s*[:：=]\s*[\"'“]?[A-DF][+\-]?(?![A-Za-z])"
    r"|(?<![A-Za-z])[A-DF][+\-]?\s*(?:级|档)(?![A-Za-z])"
    r"|(?<![A-Za-z])A\s*[/、,]\s*B\s*[/、,]\s*C(?![A-Za-z])"
)
for offending in ("grade: B", "评级：A+", "band 3 = C-", "the scale is A/B/C"):
    check(f"the letter-grade guard catches {offending!r}",
          bool(LETTER_GRADE.search(offending)), True)
for name, payload in PAYLOADS.items():
    strings = [value for _, _, value in walk(payload) if isinstance(value, str)]
    hits = sorted({hit for text in strings for hit in LETTER_GRADE.findall(text)})
    check(f"{name} emits no value as a letter grade", hits, [])
# The three star fields are the reason the letter ban has to be asserted rather
# than assumed: the coarsening exists, it just is not spelled with letters.
check("stars are produced, which is what makes the letter ban a real distinction",
      (PAYLOADS["star_rating"]["stars"], PAYLOADS["star_rating"]["max_stars"]),
      (star_rating(expected(20))["stars"], STAR_MAX))

print("\nthe exclusions register")

check("the register keeps two lists, for two different reasons",
      sorted(RANKING_EXCLUSIONS), ["not_computable_here", "refused_by_design"])
refused_by_design = dict(RANKING_EXCLUSIONS["refused_by_design"])
not_computable = dict(RANKING_EXCLUSIONS["not_computable_here"])
check("letter tiers are refused by decision, not for want of data",
      "Letter tiers" in refused_by_design, True)
check_true("...and the register says the split from stars was deliberate",
           "deliberate split" in refused_by_design["Letter tiers"])
check_true("...and warns the next reader not to unify them",
           "reverse a decision they were not party to" in refused_by_design["Letter tiers"])
check("trends and fitted slopes are refused by decision too",
      "Trends, fitted slopes, year-over-year change" in refused_by_design, True)
check_true("...unchanged from round one",
           "unchanged from round one"
           in refused_by_design["Trends, fitted slopes, year-over-year change"])
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
