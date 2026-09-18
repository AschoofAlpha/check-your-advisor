#!/usr/bin/env python3
"""
`corpus_diff.py`: what two harvests of one advisor say differently.

Every `harvest` is a full re-fetch, so the real question — "what does the corpus
say now that it did not say six months ago" — has until now been answered by
reading two HTML reports side by side. This module answers it as data.

The thing it must not do is answer the question after it, which is *why*. A
record in the older corpus and not in the newer one has several explanations
that the two files cannot tell apart, and every one of them accounts for the
whole difference on its own. The largest of them is what this file spends most
of its assertions on: **a different search window**. Widen `years_back` by two
years and every record in those two years holds only in the newer corpus, which
is a true statement about membership and a false one about output. Block [3]
builds exactly that corpus and asserts the module says so before it says
anything else.

The other two properties asserted here are about agreeing with the rest of the
package rather than about the diff itself:

  1. **The same dedup keys as `merge_corpora`.** `paper_keys` is
     `openalex._keys_of` itself, not a second implementation of DOI -> PMID ->
     normalised title + year. Two functions that decide "same paper" differently
     would let one report call a record a duplicate and the other call it new.
  2. **The same people as the report.** The roster comes from
     `roles.build_people` over `roles.prepare_paper` output, so "who appears now
     and did not before" names the people Section 2 names, under the same
     grouping and the same exclusions.

All data is synthetic. Fully offline: nothing here reaches the network, and the
only file it reads is the source of the module under test.

Run: python tests/test_corpus_diff.py
"""

from __future__ import annotations

import ast
import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor import corpus_diff  # noqa: E402
from check_your_advisor.openalex import _keys_of  # noqa: E402

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


# ======================================================================
# Synthetic corpora
# ======================================================================

PI = "Zhu Guangwei"


def author(display: str, orcid: str = "") -> dict[str, Any]:
    """One byline entry in `pubmed_api._author_record`'s shape, "Last Fore"."""
    last, _, fore = display.partition(" ")
    return {
        "name": f"{last} {fore}".strip(),
        "last": last,
        "fore": fore,
        "initials": (fore[:1] or "").upper(),
        "affiliation": "",
        "email": "",
        "orcid": orcid,
        "is_corresponding": False,
        "equal_contrib": False,
    }


def paper(pmid: str, title: str, year: int, byline: list[Any],
          doi: str = "") -> dict[str, Any]:
    """One corpus record. `byline` holds display names or ready-made entries."""
    return {
        "pmid": str(pmid),
        "title": title,
        "journal": "J Synthetic",
        "doi": doi,
        "pub_date": f"{year} Jan 01",
        "pub_year": str(year),
        "authors": [a if isinstance(a, dict) else author(a) for a in byline],
    }


def corpus(papers: list[dict[str, Any]], mindate: str = "", maxdate: str = "",
           years_back: Any = 5, orcid: str = "") -> dict[str, Any]:
    """A corpus in `cli._profile_corpus`'s shape. No dates = a legacy file."""
    query: dict[str, Any] = {"years_back": years_back}
    if mindate or maxdate:
        query.update({"mindate": mindate, "maxdate": maxdate})
    return {
        "schema_version": 1,
        "query": query,
        "identity": {
            "author_name": PI,
            "orcid": orcid,
            "affiliation_keywords": [],
            "email_domains": [],
            "openalex_author_id": "",
        },
        "papers": papers,
    }


# The older harvest: four records, 2019-2022.
OLD = corpus(
    [
        paper("1", "Alpha", 2019, ["Li Ming", PI], doi="10.1/a"),
        paper("2", "Beta", 2020, ["Li Ming", "Wang Hui", PI], doi="10.1/b"),
        paper("3", "Gamma", 2021, ["Wang Hui", PI], doi="10.1/c"),
        paper("4", "Delta", 2022, ["Chen Yu", PI]),
    ],
    mindate="2019/01/01", maxdate="2022/12/31", years_back=4,
)

# The newer harvest, run two years later, so the window moved to 2019-2024.
# Three of the four older records come back — one under the DOI resolver URL,
# one having lost its PMID, one having lost its DOI — and three records hold
# only here, two of them in years the older window never reached.
NEW = corpus(
    [
        paper("1", "Alpha", 2019, ["Li Ming", PI], doi="https://doi.org/10.1/A"),
        paper("", "Beta", 2020, ["Li Ming", "Wang Hui", PI], doi="10.1/b"),
        paper("3", "Gamma", 2021, ["Wang Hui", PI]),
        paper("7", "Eta", 2020, ["Li Ming", PI], doi="10.1/g"),
        paper("5", "Epsilon", 2023, ["Zhao Lei", PI], doi="10.1/e"),
        paper("6", "Zeta", 2024, ["Zhao Lei", PI], doi="10.1/f"),
    ],
    mindate="2019/01/01", maxdate="2024/12/31", years_back=6,
)

D = corpus_diff.diff_corpora(OLD, NEW)


# ======================================================================
# [1] the dedup keys are the ones merge_corpora uses
# ======================================================================

print("[1] the dedup keys are merge_corpora's, not a second set")

check("paper_keys is openalex._keys_of itself, not a copy",
      corpus_diff.paper_keys is _keys_of, True)
check("...so the key order is still DOI, PMID, title+year",
      [k.split(":", 1)[0] for k in corpus_diff.paper_keys(
          {"doi": "10.1/x", "pmid": "9", "title": "T", "pub_year": "2020"})],
      ["doi", "pmid", "ty"])

_only_new_titles = {row["title"] for row in D["papers"]["only_in_new"]}
check_false("a DOI written as a resolver URL is the same record",
            "Alpha" in _only_new_titles)
check_false("a record that lost its PMID still matches on DOI",
            "Beta" in _only_new_titles)
check_false("a record that lost its DOI still matches on PMID",
            "Gamma" in _only_new_titles)

# Title+year is the fallback key, and the year is part of it: the same title in
# a different year is a different record, exactly as `_title_year_key` decides.
_other_year = corpus_diff.diff_corpora(
    corpus([paper("", "Annual Report", 2020, ["Li Ming", PI])],
           mindate="2019/01/01", maxdate="2021/12/31"),
    corpus([paper("", "Annual Report", 2021, ["Li Ming", PI])],
           mindate="2019/01/01", maxdate="2021/12/31"),
)
check("same title, different year: not the same record",
      _other_year["papers"]["n_in_both_new_side"], 0)
_same_year = corpus_diff.diff_corpora(
    corpus([paper("", "Annual Report", 2020, ["Li Ming", PI])],
           mindate="2019/01/01", maxdate="2021/12/31"),
    corpus([paper("", "Annual  report!", 2020, ["Li Ming", PI])],
           mindate="2019/01/01", maxdate="2021/12/31"),
)
check("...and punctuation and case in the title are not a difference",
      _same_year["papers"]["n_in_both_new_side"], 1)


# ======================================================================
# [2] three counts, and every one of them with its denominator
# ======================================================================

print("\n[2] three counts, each against the total it came out of")

P = D["papers"]
check("the older corpus's own total", P["old_total"], 4)
check("the newer corpus's own total", P["new_total"], 6)
check("records only the newer corpus holds", P["n_only_in_new"], 3)
check("records only the older corpus holds", P["n_only_in_old"], 1)
check("records both hold, counted on the newer side", P["n_in_both_new_side"], 3)
check("...and on the older side", P["n_in_both_old_side"], 3)

# The two identities that make the three numbers readable. Without them "3 more"
# is a number with no denominator, which is the failure this repo keeps finding.
check("the newer total is accounted for exactly",
      P["n_only_in_new"] + P["n_in_both_new_side"] + P["n_unkeyable_new"],
      P["new_total"])
check("the older total is accounted for exactly",
      P["n_only_in_old"] + P["n_in_both_old_side"] + P["n_unkeyable_old"],
      P["old_total"])
check_false("no record matched more than one on the other side", P["one_to_many"])

check("the records are listed, not just counted",
      sorted(row["title"] for row in P["only_in_new"]), ["Epsilon", "Eta", "Zeta"])
check("...on both sides", [row["title"] for row in P["only_in_old"]], ["Delta"])
check_true("each listed record carries its identifiers",
           all({"pmid", "doi", "title", "year"} <= set(row) for row in P["only_in_new"]))

# A record carrying no DOI, no PMID and no title cannot be matched at all, so it
# is counted apart rather than declared to hold on one side only.
_blank = corpus_diff.diff_corpora(
    corpus([paper("", "", 2020, ["Li Ming", PI])],
           mindate="2019/01/01", maxdate="2021/12/31"),
    corpus([paper("", "", 2020, ["Li Ming", PI])],
           mindate="2019/01/01", maxdate="2021/12/31"),
)
check("a record with no identifier at all is counted as unmatchable",
      _blank["papers"]["n_unkeyable_new"], 1)
check("...and not as holding on one side only", _blank["papers"]["n_only_in_new"], 0)


# ======================================================================
# [3] the window — the number most likely to be read as a finding
# ======================================================================

print("\n[3] the search window, reported before any count is read")

W = D["window"]
check("the older window, as the file records it",
      (W["old"]["start"], W["old"]["end"]), (2019, 2022))
check("the newer window, as the file records it",
      (W["new"]["start"], W["new"]["end"]), (2019, 2024))
check_true("both files recorded a window, so the two are comparable", W["comparable"])
check_false("the two windows are not the same", W["same"])
check("the bound that moved is named", W["changed_bounds"], ["end"])
check("the years both harvests actually looked at",
      (W["shared_start"], W["shared_end"]), (2019, 2022))
check("the years only the newer harvest looked at",
      W["years_only_new_looked_at"], [2023, 2024])
check("...and none the other way", W["years_only_old_looked_at"], [])

# The whole point. Two of the three records holding only in the newer corpus sit
# in years the older harvest never asked about, so they are not a change in
# output — and the split says so with the denominator beside it.
B = P["by_window"]
check_true("the window split applies when both windows are known", B["applicable"])
check("records only in the newer corpus, inside the years both covered",
      B["only_in_new_inside_shared"], 1)
check("...and outside them, where the older harvest never looked",
      B["only_in_new_outside_shared"], 2)
check("the split adds back to the count it splits",
      B["only_in_new_inside_shared"] + B["only_in_new_outside_shared"]
      + B["only_in_new_undated"], P["n_only_in_new"])
check("the older side is split the same way",
      (B["only_in_old_inside_shared"], B["only_in_old_outside_shared"]), (1, 0))
check_true("each listed record says which side of the shared window it sits on",
           all(row["in_shared_window"] is not None for row in P["only_in_new"]))

# Prominence is the requirement, not mere presence: a reader who stops after the
# first line must have read the window before reading any count.
check_true("a changed window is the first thing the diff says",
           "window" in D["notes"][0].lower())
check_true("...and names both spans", "2022" in D["notes"][0] and "2024" in D["notes"][0])
check_true("...and the caliber flag is raised", D["caliber_changed"])

# The corpus built to produce the false conclusion: every record holding only in
# the newer corpus sits in the two extra years. "Seven more" here is entirely
# the window moving.
WIDER_OLD = corpus([paper("1", "Alpha", 2019, ["Li Ming", PI], doi="10.1/a")],
                   mindate="2019/01/01", maxdate="2020/12/31")
WIDER_NEW = corpus(
    [paper("1", "Alpha", 2019, ["Li Ming", PI], doi="10.1/a")]
    + [paper(str(n), f"T{n}", 2021 + (n % 2), ["Li Ming", PI], doi=f"10.1/{n}")
       for n in range(10, 17)],
    mindate="2019/01/01", maxdate="2022/12/31",
)
WIDER = corpus_diff.diff_corpora(WIDER_OLD, WIDER_NEW)
check("seven records hold only in the newer corpus", WIDER["papers"]["n_only_in_new"], 7)
check("...and every one of them is in a year the older harvest never asked about",
      WIDER["papers"]["by_window"]["only_in_new_outside_shared"], 7)
check("...leaving nothing at all inside the years both covered",
      WIDER["papers"]["by_window"]["only_in_new_inside_shared"], 0)
check_true("...and the window note still leads", "window" in WIDER["notes"][0].lower())

# Same window on both sides: still stated, because "we compared them and they
# match" and "nobody looked" are different facts.
SAME = corpus_diff.diff_corpora(
    OLD, corpus(OLD["papers"], mindate="2019/01/01", maxdate="2022/12/31"))
check_true("two identical windows compare equal", SAME["window"]["same"])
check("...and nothing is reported as having moved", SAME["window"]["changed_bounds"], [])
check_false("...so no caliber change is claimed", SAME["caliber_changed"])
check_true("...and the window is still stated in words",
           "2019" in SAME["window"]["note"] and "2022" in SAME["window"]["note"])

# A corpus written before the search block existed records no window at all. The
# years are then the corpus's own span, which is not the span that was asked
# for, and the two cannot be compared as windows.
LEGACY = corpus_diff.diff_corpora(corpus(OLD["papers"]), NEW)
check_false("a corpus with no recorded window cannot be compared",
            LEGACY["window"]["comparable"])
check("...so sameness is unknown rather than true or false",
      LEGACY["window"]["same"], None)
check("...and the years are marked as coming from the records themselves",
      LEGACY["window"]["old"]["source"], "papers")
check("...against the newer one, which recorded its own",
      LEGACY["window"]["new"]["source"], "query")
check_false("...so the inside/outside split is withheld rather than guessed",
            LEGACY["papers"]["by_window"]["applicable"])
check("...leaving every listed record's side of the window unstated",
      {row["in_shared_window"] for row in LEGACY["papers"]["only_in_new"]}, {None})
check_true("...and that is what the leading note says",
           "window" in LEGACY["notes"][0].lower())


# ======================================================================
# [4] people, at roles.build_people's caliber
# ======================================================================

print("\n[4] people, grouped the way the roster is grouped")

H = D["people"]
check("people the newer roster holds and the older one does not",
      [row["name"] for row in H["only_in_new"]], ["Zhao Lei"])
check("people the older roster holds and the newer one does not",
      [row["name"] for row in H["only_in_old"]], ["Chen Yu"])
check("people both rosters hold", H["n_in_both"], 2)
check("the older roster's size, as the denominator", H["old_total"], 3)
check("the newer roster's size, as the denominator", H["new_total"], 3)
check("the older roster is accounted for exactly",
      H["n_only_in_old"] + H["n_in_both"] + H["n_unkeyable_old"], H["old_total"])
check("the newer roster is accounted for exactly",
      H["n_only_in_new"] + H["n_in_both"] + H["n_unkeyable_new"], H["new_total"])
check("the records the rosters were built from are stated too",
      (H["kept_old"], H["kept_new"]), (4, 6))

check_true("a listed person carries what the roster says about them",
           {"name", "orcid", "marker", "stratum", "n_appearances", "first_year",
            "last_year", "n_first_slots"} <= set(H["only_in_new"][0]))

# The PI is on every byline by construction and is never a roster entry, so a
# diff of rosters can never report the advisor as appearing or not appearing.
_moved = {row["name"] for row in H["only_in_new"] + H["only_in_old"]}
check_false("the advisor is not reported as a person who appeared or did not",
            PI in _moved)

# An ORCID that shows up only in the later harvest must not split one person in
# two. The loose key is carried alongside the ORCID for exactly this case.
ORCID_OLD = corpus([
    paper("1", "Alpha", 2020, ["Sun Qi", PI], doi="10.1/a"),
    paper("2", "Beta", 2021, ["Sun Qi", PI], doi="10.1/b"),
], mindate="2019/01/01", maxdate="2022/12/31")
ORCID_NEW = corpus([
    paper("1", "Alpha", 2020, [author("Sun Qi", "0000-0002-1825-0097"), author(PI)],
          doi="10.1/a"),
    paper("2", "Beta", 2021, ["Sun Qi", PI], doi="10.1/b"),
], mindate="2019/01/01", maxdate="2022/12/31")
_orcid = corpus_diff.diff_corpora(ORCID_OLD, ORCID_NEW)
check("an ORCID appearing later does not make a second person",
      _orcid["people"]["n_in_both"], 1)
check("...nor an arrival", _orcid["people"]["only_in_new"], [])
check("...nor an absence", _orcid["people"]["only_in_old"], [])

# A roster entry with no ORCID and no surname cannot be keyed across two
# rosters, so it is counted apart instead of being called a new person.
_mononym = {"name": "Ming", "last": "", "fore": "Ming", "initials": "M",
            "affiliation": "", "email": "", "orcid": "",
            "is_corresponding": False, "equal_contrib": False}
NOKEY = corpus_diff.diff_corpora(
    corpus([paper("1", "Alpha", 2020, [PI], doi="10.1/a")],
           mindate="2019/01/01", maxdate="2021/12/31"),
    corpus([paper("1", "Alpha", 2020, [_mononym, author(PI)], doi="10.1/a")],
           mindate="2019/01/01", maxdate="2021/12/31"),
)
check("a roster entry with no key of its own is counted as unmatchable",
      NOKEY["people"]["n_unkeyable_new"], 1)
check("...and not as a person who newly appears", NOKEY["people"]["n_only_in_new"], 0)


# ======================================================================
# [5] first-author slots on the records that hold on one side only
# ======================================================================

print("\n[5] first-author slots, and whom they land on")

F = D["first_author_slots"]
check("first-author slots in the older corpus", F["old_total"], 4)
check("first-author slots in the newer corpus", F["new_total"], 6)
check("slots sitting on records only the newer corpus holds",
      F["n_on_records_only_in_new"], 3)
check("slots sitting on records only the older corpus holds",
      F["n_on_records_only_in_old"], 1)

_holders = {row["name"]: row for row in F["holders_only_in_new"]}
check("whom those slots land on", sorted(_holders), ["Li Ming", "Zhao Lei"])
check("Zhao Lei holds two of them", _holders["Zhao Lei"]["n_on_records_only_in_new"], 2)
check("...out of the two they hold in the newer corpus at all",
      _holders["Zhao Lei"]["n_first_slots_new"], 2)
check("...and out of none in the older one, because they are not on that roster",
      _holders["Zhao Lei"]["n_first_slots_old"], None)
check("Li Ming holds one", _holders["Li Ming"]["n_on_records_only_in_new"], 1)
check("...out of three, which is the denominator that keeps it readable",
      _holders["Li Ming"]["n_first_slots_new"], 3)
check("...against two in the older corpus", _holders["Li Ming"]["n_first_slots_old"], 2)
check("the records are named, not just counted",
      sorted(_holders["Zhao Lei"]["pmids"]), ["5", "6"])
check_true("a holder not on the older roster is marked as such",
           _holders["Zhao Lei"]["new_to_roster"])
check_false("...and one already on it is not", _holders["Li Ming"]["new_to_roster"])

# The window caveat reaches this count too: both of Zhao Lei's slots sit in
# years the older harvest never asked about.
check("slots outside the years both harvests covered",
      _holders["Zhao Lei"]["n_outside_shared_window"], 2)
check("...and Li Ming's sits inside them",
      _holders["Li Ming"]["n_outside_shared_window"], 0)
check("the per-holder counts add back to the total",
      sum(row["n_on_records_only_in_new"] for row in F["holders_only_in_new"]),
      F["n_on_records_only_in_new"])
check("the older side is reported the same way, not only the newer one",
      [row["name"] for row in F["holders_only_in_old"]], ["Chen Yu"])
check("no slot was left unattributed", F["n_slots_on_unresolved_records"], 0)


# ======================================================================
# [6] membership is reported; cause is not
# ======================================================================

print("\n[6] membership is reported; cause is not")


def strings(node: Any) -> list[str]:
    """Every string this result would show a reader, keys included."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for key, value in node.items() for s in strings(key) + strings(value)]
    if isinstance(node, (list, tuple)):
        return [s for item in node for s in strings(item)]
    return []


_text = " ".join(strings(D) + strings(LEGACY) + strings(SAME) + strings(WIDER)).lower()

# A record holding only in the older corpus has several explanations that these
# two files cannot tell apart. Naming any one of them here would be the module
# deciding, and that decision is the one this repository exists not to make.
#
# The Chinese half of the list is checked with an ASCII label: this file prints
# to a console whose encoding it does not control, and a test that crashes while
# reporting a pass is worse than the wording it was guarding.
FORBIDDEN_CAUSE = ("retract", "withdraw", "remove", "delete", "disappear",
                   "vanish", "dropped", "lost",
                   "撤稿", "撤回", "移除", "删除", "下架", "消失")
for _index, _word in enumerate(FORBIDDEN_CAUSE):
    _label = repr(_word) if _word.isascii() else f"the Chinese for it (#{_index})"
    check(f"the diff never says {_label}", _word in _text, False)

check_true("it says instead that it does not know why",
           any("does not say why" in note.lower() for note in D["notes"]))
check_true("...and names the possibilities without choosing one",
           all(word in corpus_diff.CAUSE_NOTE.lower()
               for word in ("window", "search term", "identity", "database")))

# No verdict of any kind: this is a difference between two files, not a
# trajectory, and nothing here is a standing.
def field_names(node: Any) -> list[str]:
    """Every key name in the result, at every depth."""
    if isinstance(node, dict):
        return [name for key, value in node.items()
                for name in [str(key)] + field_names(value)]
    if isinstance(node, (list, tuple)):
        return [name for item in node for name in field_names(item)]
    return []


_fields = " ".join(field_names(D)).lower()
for _forbidden in ("verdict", "score", "trend", "improving", "declining",
                   "growth", "productivity", "better", "worse", "rank",
                   "percentile", "grade"):
    check(f"the diff emits no '{_forbidden}' field", _forbidden in _fields, False)


# ======================================================================
# [7] a pure function over two loaded dicts
# ======================================================================

print("\n[7] pure: reads two dicts, returns one, touches nothing else")

_old_before = json.dumps(OLD, sort_keys=True, default=str)
_new_before = json.dumps(NEW, sort_keys=True, default=str)
corpus_diff.diff_corpora(OLD, NEW)
check("the older corpus is not written to",
      json.dumps(OLD, sort_keys=True, default=str), _old_before)
check("the newer corpus is not written to",
      json.dumps(NEW, sort_keys=True, default=str), _new_before)
check("the result is JSON-serialisable, so the caller can store it",
      isinstance(json.dumps(D, default=str), str), True)
check("two runs over the same pair give the same answer",
      json.dumps(corpus_diff.diff_corpora(OLD, NEW), sort_keys=True, default=str),
      json.dumps(D, sort_keys=True, default=str))

# Saving the result and putting it on a command line are the caller's job, so
# this module must carry nothing that would need either.
_module_path = os.path.join(os.path.dirname(__file__), "..", "scripts",
                            "check_your_advisor", "corpus_diff.py")
with open(_module_path, encoding="utf-8") as _fh:
    _tree = ast.parse(_fh.read())

_roots: set[str] = set()
for _node in ast.walk(_tree):
    if isinstance(_node, ast.Import):
        _roots.update(alias.name.split(".")[0] for alias in _node.names)
    elif isinstance(_node, ast.ImportFrom) and _node.level == 0:
        _roots.add((_node.module or "").split(".")[0])
check("every import is the standard library or this package",
      sorted(name for name in _roots
             if name and name not in sys.stdlib_module_names
             and name != "check_your_advisor"), [])

_called = {node.func.id for node in ast.walk(_tree)
           if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
for _banned in ("open", "urlopen", "input"):
    check(f"the module never calls {_banned!r}", _banned in _called, False)

# Two empty corpora are a valid question with a boring answer, not a crash.
_empty = corpus_diff.diff_corpora(corpus([]), corpus([]))
check("an empty pair reports zero against zero", _empty["papers"]["new_total"], 0)
check("...and no people", _empty["people"]["new_total"], 0)
check("...and no first-author slots", _empty["first_author_slots"]["new_total"], 0)
check_true("...and still says what it could not compare", _empty["notes"])

# The order of the arguments is the order of the story: the first is the older
# corpus. Swapping them swaps the two sides and nothing else.
_swapped = corpus_diff.diff_corpora(NEW, OLD)
check("swapping the arguments swaps the two sides",
      _swapped["papers"]["n_only_in_old"], D["papers"]["n_only_in_new"])
check("...and the other side with it",
      _swapped["papers"]["n_only_in_new"], D["papers"]["n_only_in_old"])


print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(1 if _failed else 0)
