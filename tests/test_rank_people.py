#!/usr/bin/env python3
"""
`profile/roles.py::rank_people`: ordering people by a count, which rounds one
through three refused outright.

The refusal was never about arithmetic. `n_first_slots` and `n_appearances` have
been computed and printed per person since round one; what was refused was
arranging people by them, because a list in count order reads as a standing, and
the reader is someone deciding where to spend five years. The user asked for the
ordering in round four. This file holds what the ordering must still not do.

Three properties matter more than the ordering itself, and each is asserted
against a case built to break it:

  1. **The roster is not reordered.** `assign_people` still returns people in
     first-appearance order, and `rank_people` neither sorts that list in place
     nor writes a rank back onto its records. The timeline figure and
     `person_id` both read that order, and a count changes on every re-harvest
     while a first appearance does not.
  2. **Ties are shared, never broken.** Two people with the same count get the
     same rank. A tiebreak on any other field would invent a difference the
     data does not hold; the stable roster order decides *listing* order only.
  3. **The denominator travels with the rank.** `basis` names how many people
     the rank is among, so "1st" cannot read as first of many when it is first
     of two, and says in words that absence from the roster is not a low rank.

All data is synthetic. Fully offline.

Run: python tests/test_rank_people.py
"""

from __future__ import annotations

import os
import sys
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor.profile.roles import (  # noqa: E402
    RANK_BASIS,
    RANKABLE,
    rank_people,
)

_passed = 0
_failed = 0


def check(label: str, actual, expected) -> None:
    global _passed, _failed
    ok = actual == expected
    if ok:
        _passed += 1
    else:
        _failed += 1
    detail = "" if ok else f"  (expected {expected!r}, got {actual!r})"
    print(f"  {'[PASS]' if ok else '[FAIL]'} {label}{detail}")


def check_true(label: str, actual) -> None:
    check(label, bool(actual), True)


def check_false(label: str, actual) -> None:
    check(label, bool(actual), False)


def person(name: str, first_date: str, first_slots: int,
           appearances: int, span: int = 0) -> dict[str, Any]:
    """One roster record reduced to the fields `rank_people` reads."""
    return {
        "name": name,
        "first_date": first_date,
        "n_first_slots": first_slots,
        "n_appearances": appearances,
        "span_years": span,
    }


# Chen leads most; Ding and Bai tie on leads; An never led but appears most.
ROSTER = [
    person("An", "2018-01-01", 0, 9, 5),
    person("Bai", "2019-01-01", 2, 4, 3),
    person("Chen", "2020-01-01", 5, 6, 2),
    person("Ding", "2021-01-01", 2, 2, 1),
]


print("[1] the ordering itself")

_by_leads = rank_people(ROSTER, by="first_slots")
_names = [row["name"] for row in _by_leads["ranked"]]
check("most first-author slots comes first", _names[0], "Chen")
check("the whole order, ties in roster order", _names, ["Chen", "Bai", "Ding", "An"])
check("ranks are 1, 2, 2, 4 — tied rank shared, next skipped",
      [row["rank"] for row in _by_leads["ranked"]], [1, 2, 2, 4])
check("the value ranked on is carried, not recomputed by the reader",
      [row["value"] for row in _by_leads["ranked"]], [5, 2, 2, 0])

_by_appearances = rank_people(ROSTER, by="appearances")
check("a different key gives a different order",
      [row["name"] for row in _by_appearances["ranked"]], ["An", "Chen", "Bai", "Ding"])
check("...and An, last by leads, is first by appearances",
      _by_appearances["ranked"][0]["name"], "An")

_by_span = rank_people(ROSTER, by="span")
check("span is rankable too", [row["name"] for row in _by_span["ranked"]][0], "An")


print("\n[2] ties are shared, never broken")

check_true("Bai and Ding are marked tied",
           all(row["tied"] for row in _by_leads["ranked"] if row["name"] in ("Bai", "Ding")))
check_false("Chen, alone at the top, is not marked tied",
            _by_leads["ranked"][0]["tied"])
check("a tie does not consume the next rank number",
      sorted({row["rank"] for row in _by_leads["ranked"]}), [1, 2, 4])

_all_tied = rank_people([person("X", "2020-01-01", 3, 3), person("Y", "2021-01-01", 3, 3)],
                        by="first_slots")
check("everyone tied is everyone at rank 1",
      [row["rank"] for row in _all_tied["ranked"]], [1, 1])
check_true("...and all of them say so", all(row["tied"] for row in _all_tied["ranked"]))


print("\n[3] the roster is not reordered and not written to")

_input = [dict(record) for record in ROSTER]
_before = [record["name"] for record in _input]
_result = rank_people(_input, by="first_slots")
check("the caller's list is left in its own order",
      [record["name"] for record in _input], _before)
check_false("no rank is written back onto the caller's records",
            any("rank" in record for record in _input))
check_false("...nor a value", any("value" in record for record in _input))
check_true("the ranked rows are new dicts",
           all(row is not record for row, record in zip(_result["ranked"], _input)))
check_true("...carrying the person's own keys through",
           all("n_appearances" in row for row in _result["ranked"]))


print("\n[4] the denominator travels with the rank")

check("n is the size of the roster the rank was taken among", _by_leads["n"], 4)
check_true("basis names that n", "4 people" in _by_leads["basis"])
check_true("basis names the key ranked on", "first slots" in _by_leads["basis"])
check_true("basis says absence is not a low rank", "are absent" in _by_leads["basis"])
check_true("basis says ties share a rank", "Ties share a rank" in _by_leads["basis"])
check("basis is RANK_BASIS filled in, not a second wording",
      _by_leads["basis"], RANK_BASIS.format(n=4, label="first slots"))

_one = rank_people([person("Solo", "2020-01-01", 1, 1)], by="first_slots")
check("first of one is still reported with its denominator", _one["n"], 1)
check_true("...and says so in words", "1 people" in _one["basis"])

_none = rank_people([], by="first_slots")
check("an empty roster ranks nobody", _none["ranked"], [])
check("...and reports a denominator of zero rather than failing", _none["n"], 0)


print("\n[5] what it refuses")

try:
    rank_people(ROSTER, by="score")
    check("an unknown key is refused", "no exception", "ValueError")
except ValueError as exc:
    check_true("an unknown key is refused by name", "score" in str(exc))
    check_true("...and the message lists what is known", "appearances" in str(exc))

check("the rankable keys are a closed, declared set",
      sorted(RANKABLE), ["appearances", "first_slots", "span"])
check_true("every rankable key names a field, not a formula",
           all(isinstance(field, str) for field in RANKABLE.values()))

# A composite would need weights, and weights over people are the thing this
# function is least able to defend. The guard is the closed set above: adding a
# blended key would have to pass through it and would fail this assertion.
check_false("no rankable key blends two counts",
            any("+" in field or "/" in field for field in RANKABLE.values()))

_text = " ".join(_by_leads["basis"] for _ in (0,)).lower()
for _token in ("percentile", "quantile", "grade", "tier"):
    check(f"a rank is not dressed as a {_token}", _token in _text, False)


print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(1 if _failed else 0)
