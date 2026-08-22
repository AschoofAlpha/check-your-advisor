#!/usr/bin/env python3
"""
`theses.py`: the denominator PubMed structurally cannot see.

Every other number in this toolkit is computed over people who published,
because PubMed contains nobody else. CAV-00 says so and CAV-06 repeats it. A
degree-thesis export is different in kind: every graduate deposits a thesis, so
a supervisor query returns the graduates who published nothing at all.

`reconcile_roster` turns that into three numbers, and this file is mostly about
those three and the fourth thing that must never be folded into them:

    graduates_total        distinct people who took a degree, per the export
    with_pubmed_record     of those, the ones who appear in the corpus
    without_pubmed_record  of those, the ones who appear in no paper at all
    needs_manual_review    everyone whose name could not be decided either way

The fourth is the point. `without_pubmed_record` is the finding, and it is the
finding that a sloppy name join inflates: every graduate the matcher fails to
recognise looks exactly like a graduate who never published. So the tests here
push on that direction specifically —

  - a Chinese roster against romanised PubMed bylines must produce an *empty*
    `without_pubmed_record` and a suppressed result, never a roster of graduates
    reported as unpublished;
  - a PubMed row carrying a surname and no forename, and a row carrying an
    initial, are neither matches nor absences and go to review;
  - one PubMed person claimed by two graduates makes *both* matches unsafe, so
    both move out of the counted bucket rather than one being preferred;
  - the count comes back as a floor and a ceiling, because every undecided row
    could fall either way.

And the limit that does not go away, asserted rather than assumed: everyone in
this denominator graduated. Someone who enrolled and left is in no library at
all, so this is a better denominator than PubMed's and still not "everyone who
joined the group".

Standard library only, no network. `reconcile_roster` is pure; only
`load_thesis_roster` touches the disk, and only in a temporary directory.

Run: python tests/test_theses.py
"""

from __future__ import annotations

import csv
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

# Import order is load-bearing here, and not by choice. `theses` imports
# `.profile.metrics` for the two sample-size floors; `profile/__init__` imports
# `html_report`, which imports `report`, which imports `..theses`. A process
# that reaches `theses` first therefore dies on a partially initialised module.
# Importing `profile` first resolves the cycle, which is what the CLI happens to
# do (`cmd_profile` loads `report` long before `--thesis-roster` is read) and
# why nothing has tripped over it yet. `python -c "from check_your_advisor
# import theses"` is broken today; the fix belongs in report.py or
# profile/__init__.py, not in this file, which owns tests only.
from check_your_advisor.profile.metrics import MIN_N_AGGREGATE, MIN_N_PERCENT  # noqa: E402
from check_your_advisor import theses  # noqa: E402
from check_your_advisor.theses import (  # noqa: E402
    COLUMNS,
    DEGREE_VALUES,
    DENOMINATOR_LADDER,
    MAX_UNRESOLVED_SHARE,
    ROSTER_LIMITS,
    SOURCE_DB_VALUES,
    THESIS_DENOMINATOR_CAVEAT,
    load_thesis_roster,
    name_script,
    reconcile_roster,
)

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


def message(fn, *args, **kw) -> str:
    try:
        fn(*args, **kw)
    except Exception as exc:  # noqa: BLE001 - the text is the assertion
        return str(exc)
    return ""


TMP = tempfile.TemporaryDirectory()
ROOT = TMP.name

PI = "Chen Xiuying"
PI_HAN = "陈秀英"

REQUIRED_HEADER = ["导师姓名", "学生姓名", "学位类型", "毕业年", "库来源", "导出日期"]


def roster_csv(name: str, header: list[str], rows: list[list[str]],
               encoding: str = "utf-8-sig") -> str:
    path = os.path.join(ROOT, name)
    with open(path, "w", newline="", encoding=encoding) as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def graduate(student, year, degree="硕士", advisor=PI, **extra) -> dict:
    """A loaded roster row, as `load_thesis_roster` would hand it over."""
    row = {"line": extra.pop("line", 2), "advisor": advisor, "advisor_latin": "",
           "student": student, "student_latin": extra.pop("student_latin", ""),
           "degree": {"硕士": "master", "博士": "doctoral"}.get(degree, degree),
           "degree_raw": degree, "enrolment_year": None, "graduation_year": year,
           "title": "", "institution": "", "source_db": "CNKI", "source_db_raw": "CNKI",
           "export_date": "2026-08-21", "export_date_raw": "2026-08-21", "flags": []}
    row.update(extra)
    return row


def person(name, **extra) -> dict:
    """A PubMed roster entry, as `roles.build_people` would hand it over."""
    row = {"name": name, "marker": "", "stratum": "A", "first_year": 2022,
           "last_year": 2024, "n_appearances": 3, "n_first_slots": 1, "flags": []}
    row.update(extra)
    return row


# ============================================================
# 1. Schema
# ============================================================

print("schema")

required = [c.key for c in COLUMNS if c.required]
check("six required columns", sorted(required),
      ["advisor", "degree", "export_date", "graduation_year", "source_db", "student"])
check_true("库来源 is required, for the reason the journal table needs an edition",
           "source_db" in required)
check_true("导出日期 is required, because a thesis reaches a library months late",
           "export_date" in required)
optional = [c.key for c in COLUMNS if not c.required]
check_true("the romanised student name is optional in the schema",
           "student_latin" in optional)
check_true("...and so is the romanised supervisor name", "advisor_latin" in optional)
check("the two degree values are the two a thesis library records",
      list(DEGREE_VALUES), ["master", "doctoral"])
check("the source databases are the two named ones plus a real 其他 bucket",
      list(SOURCE_DB_VALUES), ["CNKI", "万方", "其他"])
check("every column carries at least one Chinese and one English spelling",
      [c.key for c in COLUMNS
       if not any("一" <= ch <= "鿿" for alias in c.aliases for ch in alias)], [])

print("\nloading a roster")

missing = roster_csv("missing.csv", ["导师姓名", "学生姓名", "学位类型", "毕业年"],
                     [[PI, "Li Wei", "硕士", "2022"]])
check("a roster with no 库来源 or 导出日期 is refused", raises(load_thesis_roster, missing),
      "ValueError")
check_true("...naming both missing columns",
           "source_db" in message(load_thesis_roster, missing)
           and "export_date" in message(load_thesis_roster, missing))
check_true("...and listing the headers that were found, so the fix is obvious",
           "Headers found" in message(load_thesis_roster, missing))
check("a roster that does not exist raises rather than loading empty",
      raises(load_thesis_roster, os.path.join(ROOT, "nope.csv")), "FileNotFoundError")

good = roster_csv("good.csv", REQUIRED_HEADER + ["学生姓名拼音", "培养单位"], [
    [PI, "李伟", "硕士", "2020", "CNKI", "2026-08-21", "Li Wei", "南海医科大学"],
    [PI, "李伟", "博士", "2023", "CNKI", "2026-08-21", "Li Wei", "南海医科大学"],
    [PI, "王芳", "硕士", "2021", "万方", "2026-08-21", "Wang Fang", "南海医科大学"],
    [PI, "王芳", "硕士", "2021", "万方", "2026-08-21", "Wang Fang", "南海医科大学"],
    [PI, "", "硕士", "2022", "CNKI", "2026-08-21", "", "南海医科大学"],
    [PI, "赵敏", "硕士", "不详", "CNKI", "2026-08-21", "Zhao Min", "南海医科大学"],
    [PI, "孙立", "博士后", "2024", "谁家的库", "昨天", "Sun Li", "南海医科大学"],
])
loaded = load_thesis_roster(good)

check("usable rows are kept", loaded["denominator"], 4)
check("...against the number of lines actually read", loaded["rows_read"], 7)
check("an exact duplicate row is dropped once and listed",
      [(d["student"], d["graduation_year"]) for d in loaded["duplicates_dropped"]],
      [("王芳", 2021)])
check("a row with no student name is rejected with its line number, not dropped",
      [(r["line"], r["reason"]) for r in loaded["rejected"] if r["reason"] == "no student name"],
      [(6, "no student name")])
check("a row with no readable graduation year is rejected too, and says which cell",
      [r["line"] for r in loaded["rejected"] if "graduation year" in r["reason"]], [7])
check("the two rejections are the only ones", len(loaded["rejected"]), 2)

# A malformed cell is still a real person: the row survives, flagged.
flagged = {row["student"]: row["flags"] for row in loaded["rows"]}
check_true("an unrecognised degree word is flagged, not dropped",
           "degree_unrecognised" in flagged["孙立"])
check_true("...because 博士后 is not a graduate of this programme",
           [r["degree"] for r in loaded["rows"] if r["student"] == "孙立"] == [None])
check_true("an unrecognised source database is flagged",
           "source_db_unrecognised" in flagged["孙立"])
check_true("...and mapped to 其他 rather than guessed at",
           [r["source_db"] for r in loaded["rows"] if r["student"] == "孙立"] == ["其他"])
check_true("an unparseable export date is flagged", "export_date_unparseable" in flagged["孙立"])
check("the provenance a report has to print comes back with the rows",
      (loaded["source_dbs"], loaded["export_dates"]),
      ({"CNKI": 2, "万方": 1, "其他": 1}, ["2026-08-21", "昨天"]))
check("...including which units awarded the degrees", loaded["institutions"],
      {"南海医科大学": 4})
check("...and which encoding the file turned out to be", loaded["encoding"], "utf-8-sig")
check("the limits text travels with the file, not only with the module",
      loaded["limits"], ROSTER_LIMITS)
check("the columns actually used are reported under the headers as written",
      loaded["columns_used"]["student_latin"], "学生姓名拼音")

# The one column that decides whether a Chinese roster can be joined at all.
no_latin = load_thesis_roster(roster_csv("no_latin.csv", REQUIRED_HEADER, [
    [PI, "李伟", "硕士", "2020", "CNKI", "2026-08-21"],
]))
check_true("a Chinese name with no romanisation is flagged at load time",
           "no_romanised_name" in no_latin["rows"][0]["flags"])
check("...and the missing optional column is named",
      "student_latin" in no_latin["columns_missing_optional"], True)

check("names are classified by script, because two scripts never join",
      [name_script(n) for n in ("李伟", "Li Wei", "李伟 Li Wei", "")],
      ["han", "latin", "mixed", "empty"])


# ============================================================
# 2. The three numbers
# ============================================================

print("\nthe three numbers")

ROSTER = [
    graduate("Li Wei", 2020),
    graduate("Li Wei", 2023, "博士"),        # one person, two degrees
    graduate("Wang Fang", 2021),
    graduate("Zhao Min", 2022),
    graduate("Sun Lijuan", 2023),
    graduate("Zhou Qiang", 2024),
]
PEOPLE = [
    person("Li Wei"),
    person("Fang Wang"),                     # surname-last, same person as Wang Fang
    person("Han Meimei"),                    # a postdoc: in PubMed, in no thesis library
    person(PI),                              # the PI's own byline
]
base = reconcile_roster(PEOPLE, ROSTER, PI)

check("a master's followed by a doctorate is one graduate, not two",
      base["counts"]["graduates_total"], 5)
check("...over six thesis rows", base["rows_for_pi"], 6)
check("the denominator is people, and is the population every count sits inside",
      base["denominator"], base["counts"]["graduates_total"])
check("graduates who appear in the corpus are counted",
      base["counts"]["with_pubmed_record"], 2)
check("graduates who appear in no paper at all are counted — this is the finding",
      base["counts"]["without_pubmed_record"], 3)
check("...and named, so the number is a list rather than a claim",
      sorted(row["student"] for row in base["without_pubmed_record"]),
      ["Sun Lijuan", "Zhao Min", "Zhou Qiang"])
check("the three buckets plus review account for every graduate",
      base["counts"]["with_pubmed_record"] + base["counts"]["without_pubmed_record"]
      + base["counts"]["needs_manual_review"], base["counts"]["graduates_total"])
check("the PI is not matched against their own student list",
      [row["name"] for row in base["pubmed_only"]], ["Han Meimei"])
check("...and the PubMed roster size is reported after that removal",
      base["counts"]["pubmed_roster_size"], 3)

# `pubmed_only` is not a list of outsiders and the module says so; the test
# pins the reason to the text rather than to a comment.
limits = dict(ROSTER_LIMITS)
check_true("the limits say why pubmed_only is mostly postdocs and technicians",
           "That is most of what `pubmed_only` is" in
           limits["Only degree students are in a thesis library"])

matched = {row["student"]: row for row in base["with_pubmed_record"]}
check("an identical name is matched as exact", matched["Li Wei"]["evidence"], "exact")
check("surname-first against surname-last is matched under rotation",
      (matched["Wang Fang"]["pubmed_name"], matched["Wang Fang"]["evidence"]),
      ("Fang Wang", "name_form"))
check("a matched graduate carries the PubMed row's own numbers, for checking",
      (matched["Li Wei"]["pubmed_appearances"], matched["Li Wei"]["pubmed_lead_slots"]),
      (3, 1))
check("...and the degrees they took, in order",
      matched["Li Wei"]["degrees"], ["master", "doctoral"])
check("a continuing student is flagged as one rather than as a duplicate",
      "continuing_student" in matched["Li Wei"]["flags"], True)

print("\nfloor and ceiling")

check("the count comes back as a range, because undecided rows could fall either way",
      base["without_pubmed_bounds"], (3, 3))
check("with nothing undecided the two ends meet",
      base["without_pubmed_bounds"][0], base["without_pubmed_bounds"][1])
check("the unresolved share is reported beside its ceiling",
      (base["unresolved_share"], base["max_unresolved_share"]), (0.0, MAX_UNRESOLVED_SHARE))
check("the ceiling is a declared constant, printed with the number", MAX_UNRESOLVED_SHARE, 0.5)


# ============================================================
# 3. Nothing uncertain is pushed into a bucket
# ============================================================

print("\nan initial where a given name should be")

initial = reconcile_roster([person("Zhu G")], [graduate("Zhu Guangwei", 2022)], PI)
check("an initial is neither a match nor an absence", initial["counts"]["with_pubmed_record"], 0)
check("...and specifically not an absence, which is the half that matters",
      initial["counts"]["without_pubmed_record"], 0)
check("...it goes to review", initial["counts"]["needs_manual_review"], 1)
check("...with the reason named", initial["needs_manual_review"][0]["reason"], "partial_name")
check("...and the candidate listed by name",
      initial["needs_manual_review"][0]["candidates"], ["Zhu G"])

# `pubmed_api._author_record` builds `name` as "{LastName} {ForeName}", so a
# record with no ForeName arrives as a bare surname, which fits every Zhu in the
# corpus. Without this level that graduate would be reported as unpublished.
bare = reconcile_roster([person("Zhu")], [graduate("Zhu Guangwei", 2022)], PI)
check("a PubMed row with a surname and no forename is not an absence either",
      bare["counts"]["without_pubmed_record"], 0)
check("...it goes to review too", bare["needs_manual_review"][0]["reason"], "partial_name")

surname_only = reconcile_roster([person("Zhu")], [graduate("Zhu", 2022)], PI)
check("a surname against a surname is demoted, not treated as an identity",
      surname_only["counts"]["with_pubmed_record"], 0)
check("...because 'Zhu' equals 'Zhu' for every Zhu alive",
      surname_only["needs_manual_review"][0]["reason"], "partial_name")

print("\nmore than one candidate, on either side")

two_candidates = reconcile_roster(
    [person("Li Wei"), person("Wei Li")], [graduate("Li Wei", 2022)], PI)
check("a graduate matching two PubMed people is not matched to one of them",
      two_candidates["counts"]["with_pubmed_record"], 0)
check("...it goes to review as ambiguous",
      two_candidates["needs_manual_review"][0]["reason"], "ambiguous")
check("...with both candidates listed",
      sorted(two_candidates["needs_manual_review"][0]["candidates"]), ["Li Wei", "Wei Li"])

# The converse, and the one that would otherwise pass silently: two graduates
# recorded under one spelling both match one PubMed person. Preferring either
# would put a real paper against the wrong graduate, so both move out.
contested = reconcile_roster(
    [person("Li Wei")], [graduate("Li Wei", 2020), graduate("Li Wei", 2022, "博士")], PI)
check("two rows under one name group into one graduate", contested["denominator"], 1)
# Two *different* graduates whose names both reach match level against one
# PubMed person. Preferring either would put a real paper against the wrong
# graduate, so both leave the counted bucket.
contested_two = reconcile_roster(
    [person("Li Wei")], [graduate("Li Wei", 2020), graduate("Wei Li", 2022)], PI)
check("they are two graduates, not one", contested_two["denominator"], 2)
check("a PubMed person claimed by more than one graduate makes both matches unsafe",
      contested_two["counts"]["with_pubmed_record"], 0)
check("...and neither becomes an absence", contested_two["counts"]["without_pubmed_record"], 0)
check("...both go to review", contested_two["counts"]["needs_manual_review"], 2)
check("...as ambiguous, naming the person they both claimed",
      sorted({row["reason"] for row in contested_two["needs_manual_review"]}), ["ambiguous"])
check_true("...and the note names the graduates who contested it",
           all("was matched by more than one graduate" in (row.get("note") or "")
               for row in contested_two["needs_manual_review"]))
check("...and the contested PubMed person is not silently counted as an outsider either",
      contested_two["counts"]["pubmed_only"], 0)

print("\nthe join that cannot be made at all")

# A CNKI export writes 学生姓名 in Chinese. PubMed writes the same people in
# romanised form. Nothing in the standard library converts one into the other,
# and reporting a roster of graduates as "published nothing" would be a
# fabrication in the exact direction that flatters the finding.
CHINESE_ROSTER = [graduate(n, 2020 + i) for i, n in enumerate(["李伟", "王芳", "赵敏", "孙立", "周强"])]
cross = reconcile_roster(PEOPLE, CHINESE_ROSTER, PI_HAN)
check("every graduate lands in review", cross["counts"]["needs_manual_review"], 5)
check("...and not one is reported as unpublished",
      cross["counts"]["without_pubmed_record"], 0)
check("...the named list is empty too, not merely the count",
      cross["without_pubmed_record"], [])
check("the reason is the script, not the name", cross["needs_manual_review"][0]["reason"],
      "undecidable_script")
# Four, not three: with `pi_name` written in Chinese and the PubMed bylines
# romanised, the PI's own row cannot be compared either, so it is not removed
# from the roster before the join. The same script gap, one level up.
check("...and how many PubMed people could not be compared is reported",
      cross["needs_manual_review"][0]["incomparable_pubmed_people"], 4)
check_true("...and the note names the one column that fixes it",
           "学生姓名拼音" in cross["needs_manual_review"][0]["note"])
check_true("the whole result is suppressed", cross["suppressed"])
check_true("...because the undecided share is over the ceiling",
           any(f"ceiling {MAX_UNRESOLVED_SHARE:.0%}" in r for r in cross["suppressed_reasons"]))
check_true("...and the reason says what a Chinese export against bylines looks like",
           any("学生姓名拼音" in r for r in cross["suppressed_reasons"]))
check("no share is printed when the result is suppressed",
      cross["without_pubmed_share_percent"], None)
check("the floor and the ceiling are as far apart as the undecided rows make them",
      cross["without_pubmed_bounds"], (0, 5))

# One added column turns the same roster from undecidable into decided.
WITH_LATIN = [graduate(n, 2020 + i, student_latin=latin)
              for i, (n, latin) in enumerate([("李伟", "Li Wei"), ("王芳", "Wang Fang"),
                                              ("赵敏", "Zhao Min"), ("孙立", "Sun Li"),
                                              ("周强", "Zhou Qiang")])]
rescued = reconcile_roster(PEOPLE, WITH_LATIN, PI)
check("adding 学生姓名拼音 decides the same roster",
      rescued["counts"]["needs_manual_review"], 0)
check("...matching the two who published", rescued["counts"]["with_pubmed_record"], 2)
check("...and reporting the three who did not", rescued["counts"]["without_pubmed_record"], 3)
check_false("...with nothing suppressed", rescued["suppressed"])


# ============================================================
# 4. Suppression, and the shares that are not printed
# ============================================================

print("\nsuppression")

check("the aggregate floor is metrics' floor, not a second copy of one",
      MIN_N_AGGREGATE, 5)
small = reconcile_roster([person("Li Wei")], [graduate("Li Wei", 2020),
                                              graduate("Zhao Min", 2021)], PI)
check_true("two graduates is below the floor and is suppressed", small["suppressed"])
check_true("...naming the count and the floor",
           any(f"floor {MIN_N_AGGREGATE}" in r for r in small["suppressed_reasons"]))
check("...but the counts and the names survive, as metrics.py does everywhere",
      (small["counts"]["with_pubmed_record"], small["counts"]["without_pubmed_record"]), (1, 1))
check("...while the share does not", small["without_pubmed_share_percent"], None)

empty = reconcile_roster(PEOPLE, [], PI)
check_true("an empty roster is not computable", empty["not_computable"])
check("...over a denominator of zero", empty["denominator"], 0)
check_true("...and says there is nothing to divide",
           any("nothing to divide" in r for r in empty["suppressed_reasons"]))

# A share exists only at n >= MIN_N_PERCENT, and never at all when suppressed.
#
# The names are distinct *words*, not a stem plus a digit: `_latin_tokens`
# strips everything outside [a-z], so "Pub0" and "Pub7" both fold to ("pub",)
# and twenty people would group into two graduates. That is CAV-02's failure
# mode reproduced in a fixture, and it is worth knowing it bites this easily.
WORDS = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel",
         "India", "Juliet", "Kilo", "Lima", "Mike", "November", "Oscar", "Papa",
         "Quebec", "Romeo", "Sierra", "Tango"]
PUBLISHED = [f"Pub {word}" for word in WORDS[:10]]
QUIET = [f"Quiet {word}" for word in WORDS[10:]]
check("the fixture really is twenty distinct people",
      len({tuple(sorted(n.lower().split())) for n in PUBLISHED + QUIET}), 20)

big = reconcile_roster(
    [person(name) for name in PUBLISHED],
    [graduate(name, 2020) for name in PUBLISHED] + [graduate(name, 2021) for name in QUIET],
    PI,
)
check("twenty graduates clears the percentage floor", big["denominator"], MIN_N_PERCENT)
check_false("...and is not suppressed", big["suppressed"])
check("...so a share is printed", big["without_pubmed_share_percent"], 50)
check_true("the share says what it is not, in the payload rather than in a footnote",
           "not a graduation rate and not an attrition rate" in big["share_basis"])
nineteen = reconcile_roster(
    [person(name) for name in PUBLISHED],
    [graduate(name, 2020) for name in PUBLISHED] + [graduate(name, 2021) for name in QUIET[:9]],
    PI,
)
check("nineteen does not, and the counts are printed instead",
      nineteen["without_pubmed_share_percent"], None)
check("...with the counts intact", nineteen["counts"]["without_pubmed_record"], 9)


print("\nwhose graduates these are")

check("a supervisor name that joins selects that supervisor's rows",
      base["advisor_filter"], "matched")
mixed_advisors = [graduate("Li Wei", 2020, advisor=PI),
                  graduate("Wang Fang", 2021, advisor="Somebody Else")]
check("...and leaves the other supervisor's rows out",
      reconcile_roster(PEOPLE, mixed_advisors, PI)["rows_for_pi"], 1)

# A Chinese export queried with a romanised pi_name: the file is almost
# certainly right and the tool cannot confirm it, which is a third outcome and
# not either of the other two.
single = reconcile_roster(PEOPLE, [graduate("李伟", 2020, advisor=PI_HAN)], PI)
check("one supervisor in the file, not joinable, is taken as theirs — with a warning",
      single["advisor_filter"], "unverified_single_advisor")
check_true("...saying whose counts these would be if the export was someone else's",
           "every count below is that other person's" in single["advisor_note"])

many = reconcile_roster(PEOPLE, [graduate("李伟", 2020, advisor="导师甲"),
                                 graduate("王芳", 2021, advisor="导师乙")], PI)
check("several supervisors and none joining is refused, not guessed at",
      many["advisor_filter"], "refused")
check("...so no graduate is attributed to anyone", many["denominator"], 0)
check_true("...and the supervisors seen are listed so the fix is obvious",
           "导师甲" in many["advisor_note"] and "导师乙" in many["advisor_note"])
check("the supervisor names in the file are reported either way",
      many["advisor_names_seen"], {"导师甲": 1, "导师乙": 1})


print("\ninput shapes")

check("a bare list of person dicts is accepted",
      reconcile_roster(PEOPLE, ROSTER, PI)["counts"]["pubmed_roster_size"], 3)
check("a build_people() result is unwrapped to its people",
      reconcile_roster({"people": PEOPLE}, ROSTER, PI)["counts"]["pubmed_roster_size"], 3)
check("a person_roster() result is unwrapped to its rows",
      reconcile_roster({"rows": PEOPLE}, ROSTER, PI)["counts"]["pubmed_roster_size"], 3)
check("a whole report is unwrapped through metrics.s2",
      reconcile_roster({"metrics": {"s2": {"rows": PEOPLE}}}, ROSTER, PI)
      ["counts"]["pubmed_roster_size"], 3)
check("a load_thesis_roster() result is accepted in place of its rows",
      reconcile_roster(PEOPLE, {"rows": ROSTER}, PI)["denominator"], 5)


# ============================================================
# 5. The limit that does not go away
# ============================================================

print("\nwhat this denominator still is not")

check("the caveat travels with the numbers", base["caveat"], THESIS_DENOMINATOR_CAVEAT)
check_true("...and says this counts people who graduated, not people who joined",
           "people who graduated, not people who joined" in THESIS_DENOMINATOR_CAVEAT)
check_true("...that someone who left before finishing is in no library at all",
           "absent from CNKI, from Wanfang and from PubMed alike"
           in THESIS_DENOMINATOR_CAVEAT)
check_true("...that the size of that group is unmeasurable, not merely unknown",
           "unmeasured and unmeasurable" in THESIS_DENOMINATOR_CAVEAT)
check_true("...and that the number is a floor, never a graduation or attrition rate",
           "never as attrition, never as a graduation rate" in THESIS_DENOMINATOR_CAVEAT)

check("three nested populations are returned, so the middle one is not read as the whole",
      len(DENOMINATOR_LADDER), 3)
check("...and the ladder comes back with the result", base["denominator_ladder"],
      DENOMINATOR_LADDER)
check("the widest population has no source at all", DENOMINATOR_LADDER[0][1], "no source")
check("...the middle one is the thesis export", DENOMINATOR_LADDER[1][1],
      "the thesis export, if it is complete")
check("...and the narrowest is PubMed, which is what every other section uses",
      DENOMINATOR_LADDER[2][1], "PubMed")

check("five roster limits are returned with the result", len(base["limits"]), 5)
check("...and they are the module's, verbatim", base["limits"], ROSTER_LIMITS)
check_true("...covering the deposit lag that undercounts the newest years",
           "months after the defence"
           in dict(ROSTER_LIMITS)["The export is a snapshot with a deposit lag"])
check_true("...and co-supervision filing a student under the other name",
           "filed under the other name entirely"
           in dict(ROSTER_LIMITS)["The supervisor field is often the first supervisor only"])

check("the match rules come back so the rule is printed beside the count",
      [name for name, _, _ in base["match_rules"]],
      ["exact", "name_form", "partial_name", "ambiguous", "undecidable_script"])
check("...and two of the five are counted as matches, three are sent to review",
      sorted({treatment for _, treatment, _ in base["match_rules"]}),
      ["counted as a match", "sent to manual review"])

check("the provenance names the library and the day it was read",
      sorted(base["provenance"]),
      ["duplicates_dropped", "encoding", "export_dates", "institutions", "path",
       "rejected", "rows_read", "source_dbs"])
from_file = reconcile_roster(PEOPLE, loaded, PI_HAN)
check("...and is filled in when a loaded roster was passed rather than a bare list",
      (from_file["provenance"]["source_dbs"], from_file["provenance"]["rejected"]),
      ({"CNKI": 2, "万方": 1, "其他": 1}, 2))


print("\nno scraper, and no ordering of people")

source = open(theses.__file__, encoding="utf-8").read()
for banned in ("import requests", "urlopen", "urllib.request", "RobustHTTPClient",
               "BeautifulSoup", "selenium"):
    check(f"the module contains no `{banned}`", banned in source, False)
for banned in ("pandas", "numpy", "openpyxl", "fitz"):
    check(f"the module does not import {banned}", f"import {banned}" in source, False)
# The graduates are the same people Section 2 refuses to order by a count, so
# this list is not allowed to order them by one either.
check("graduates come back in graduation order, then by name — never by any count",
      [row["student"] for row in base["without_pubmed_record"]],
      ["Zhao Min", "Sun Lijuan", "Zhou Qiang"])
check("the module exposes no ranking helper",
      [n for n in dir(theses)
       if not n.startswith("_")
       and any(w in n.lower() for w in ("rank", "percentile", "quantile", "grade", "top_",
                                        "best_", "productivity"))],
      [])

TMP.cleanup()

print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(1 if _failed else 0)
