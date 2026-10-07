#!/usr/bin/env python3
"""
Regression tests for the byline name matcher, `pubmed_api._name_matches`.

These exist because of a real precision bug found on a live harvest. The
matcher accepted a forename that merely contained the typed given name, or
merely began with the same letter, so "Wang Wei" also matched Wang Weibin,
Wang Weiwei, Wang Jianwei, Wang Wenjun and every other Wang W. On that run
four in five of the byline slots it accepted belonged to somebody else, and
the identity hints, the first/last/corresponding filter and the report all
read through it — the top email the hints suggested was a namesake's.

The names below are common pinyin and Western names chosen for their shape;
no record here belongs to anyone.

Run: python tests/test_name_match.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor.pubmed_api import (  # noqa: E402
    _name_matches,
    identity_hints,
    is_first_or_corresponding,
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


def author(last: str, fore: str, initials: str | None = None, **extra) -> dict:
    """One byline entry shaped like `pubmed_api._author_record`'s."""
    record = {
        "name": f"{last} {fore}".strip(), "last": last, "fore": fore,
        "initials": fore[:1].upper() if initials is None else initials,
        "affiliation": "", "is_corresponding": False, "email": "", "orcid": "",
    }
    record.update(extra)
    return record


def matches(name: str, entry: dict) -> bool:
    return _name_matches(entry, name.lower().split())


print("\n--- a full forename has to be the same name ---")

check("Wang Wei matches Wang, Wei", matches("Wang Wei", author("Wang", "Wei")), True)
for fore, initials in (("Weibin", "W"), ("Wei-Bin", "WB"), ("Weiwei", "W"),
                       ("Jianwei", "J"), ("Wenjun", "W"), ("Xiaowei", "X")):
    check(f"Wang Wei does not match Wang, {fore}",
          matches("Wang Wei", author("Wang", fore, initials)), False)
check("Stockwell Brent does not match Stockwell, Brenda",
      matches("Stockwell Brent", author("Stockwell", "Brenda")), False)
check("Zhu Guangwei does not match Zhu, Guang",
      matches("Zhu Guangwei", author("Zhu", "Guang")), False)

print("\n--- spelling differences inside one name do not matter ---")

check("hyphenated forename: Zhu Guangwei matches Zhu, Guang-Wei",
      matches("Zhu Guangwei", author("Zhu", "Guang-Wei", "GW")), True)
check("typed with a space: Zhu Guang Wei matches Zhu, Guangwei",
      matches("Zhu Guang Wei", author("Zhu", "Guangwei")), True)
check("hyphenated surname: Ouyang Wei matches Ou-Yang, Wei",
      matches("Ouyang Wei", author("Ou-Yang", "Wei")), True)
check("accents: Muller Jose matches Müller, José",
      matches("Muller Jose", author("Müller", "José")), True)
check("case: WANG WEI matches Wang, Wei", matches("WANG WEI", author("Wang", "Wei")), True)

print("\n--- middle names and initials ---")

check("a middle initial only the byline has: Stockwell, Brent R",
      matches("Stockwell Brent", author("Stockwell", "Brent R", "BR")), True)
check("a second name only the byline has: Zhang, Yi Eve",
      matches("Zhang Yi", author("Zhang", "Yi Eve", "YE")), True)
check("typed middle name, byline initial: John Andrew Smith vs Smith, John A",
      matches("Smith John Andrew", author("Smith", "John A", "JA")), True)
check("middle initials that disagree: John Andrew Smith vs Smith, John B",
      matches("Smith John Andrew", author("Smith", "John B", "JB")), False)

print("\n--- initials and nothing more: the first letter is all there is ---")

check("Wang Wei matches Wang, W", matches("Wang Wei", author("Wang", "W")), True)
check("Wang Wei matches Wang, W Q (initials only)",
      matches("Wang Wei", author("Wang", "W Q", "WQ")), True)
check("Wang Wei matches an empty forename with initials W",
      matches("Wang Wei", author("Wang", "", "W")), True)
check("Stockwell Brent matches Stockwell, BR beside Initials BR",
      matches("Stockwell Brent", author("Stockwell", "BR", "BR")), True)
check("Dupont Jean-Pierre matches Dupont, J.-P.",
      matches("Dupont Jean-Pierre", author("Dupont", "J.-P.", "JP")), True)
check("Wang Wei does not match Wang, X", matches("Wang Wei", author("Wang", "X")), False)
check("typed initial: Wang W matches Wang, Weibin",
      matches("Wang W", author("Wang", "Weibin")), True)
check("initials typed as one word: Stockwell BR matches Stockwell, Brent R",
      matches("Stockwell BR", author("Stockwell", "Brent R", "BR")), True)
check("...and Stockwell, Brent", matches("Stockwell BR", author("Stockwell", "Brent", "B")), True)
check("...but not Stockwell, Carl", matches("Stockwell BR", author("Stockwell", "Carl", "C")), False)
check("initials with a vowel, typed as the record's: Smith JA matches Smith, John A",
      matches("Smith JA", author("Smith", "John A", "JA")), True)
check("a two-letter given name is a name, not initials: Wang An does not match Wang, Anqi",
      matches("Wang An", author("Wang", "Anqi", "A")), False)
check("...and matches Wang, An", matches("Wang An", author("Wang", "An", "A")), True)
check("no forename and no initials cannot be told apart, so it passes",
      matches("Wang Wei", author("Wang", "", "")), True)

print("\n--- name order ---")

check("given-name-first typing: Brent Stockwell matches Stockwell, Brent",
      matches("Brent Stockwell", author("Stockwell", "Brent")), True)
check("Wang Wei read given-name-first matches Wei, Wang",
      matches("Wang Wei", author("Wei", "Wang")), True)
check("Wang Wei does not match Wei, Wenqiang (initial W was enough before)",
      matches("Wang Wei", author("Wei", "Wenqiang")), False)
check("Wang Wei does not match Wei, Wei", matches("Wang Wei", author("Wei", "Wei")), False)
check("a one-word name is a surname: Wang matches Wang, Anything",
      matches("Wang", author("Wang", "Anything")), True)
check("a one-word Chinese name matches a record keyed on it",
      _name_matches({"last": "王伟", "fore": "", "initials": ""}, ["王伟"]), True)
check("an empty name matches nothing", _name_matches(author("Wang", "Wei"), []), False)

print("\n--- the readers that go through it ---")

namesake_paper = {
    "pmid": "1", "title": "A paper by a namesake",
    "authors": [author("Wang", "Weibin", "W", email="weibin@example.org",
                       affiliation="Example Hospital. weibin@example.org",
                       is_corresponding=True)],
}
target_paper = {
    "pmid": "2", "title": "A paper by the person typed",
    "authors": [author("Li", "Hua"),
                author("Wang", "Wei", "W", email="wangwei@example.org",
                       affiliation="Example Hospital. wangwei@example.org",
                       is_corresponding=True)],
}
lenient = {"affiliation_keywords": [], "email_domains": [], "orcid": "",
           "require_affiliation": False}
check("the role filter skips a namesake's paper",
      is_first_or_corresponding(namesake_paper, "Wang Wei", "", lenient)[0], False)
check("the role filter keeps the typed person's paper",
      is_first_or_corresponding(target_paper, "Wang Wei", "", lenient)[0], True)
hints = identity_hints([namesake_paper, target_paper], "Wang Wei")
check("identity hints count only the typed person's record", hints["records_examined"], 1)
check("identity hints list only the typed person's email",
      [email for email, _count in hints["emails"]], ["wangwei@example.org"])


print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(0 if _failed == 0 else 1)
