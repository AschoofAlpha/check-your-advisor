#!/usr/bin/env python3
"""
`evaluations.py` + Section 20: statements about a person, printed as given.

This is the third hand-filled table, and the only one whose rows are sentences
about a human being rather than numbers about a paper. That changes what the
tests have to pin down. For the journal table the risk is a wrong partition; for
the thesis roster it is an inflated count of people who never published. Here
the risk is that the toolkit turns a handful of self-selected forum posts into
something that looks like a measurement — a polarity, an average, a rating, a
contribution to the composite score — and that the number then outlives the text
it came from.

So the assertions run in two directions:

  1. **Schema and provenance, the same rules the other two tables live by.**
     `评价来源` and `数据获取日期` are required columns; a file missing either is
     refused at load with the missing column named and the headers found printed
     beside it. A file carrying neither `评价内容` nor `维度评分` is refused too,
     because it records that statements exist without recording one of them.
     Every row that survives carries its source and its retrieval date into the
     report.

  2. **The refusals, asserted as behaviour rather than trusted as intent.** No
     key anywhere in the join holds a score, a sentiment, a polarity or an
     average. The composite score is byte-identical whether or not an evaluation
     table was supplied. The section body prints no percentage. And the rows
     come back in file order, because any sort would be a ranking of statements
     about a person.

The degraded path gets the same treatment as Sections 17 and 18: no table means
a printed reason and the command that fixes it, never a blank column and never
silence.

Standard library only, no network. `join_evaluations` is pure; only
`load_evaluation_table` touches the disk, and only in a temporary directory.

Run: python tests/test_evaluations.py
"""

from __future__ import annotations

import csv
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

# `profile` first, then the module under test. `evaluations` imports `theses`,
# which imports `profile.metrics` from inside a function to break the cycle
# `profile/__init__ -> html_report -> report -> theses` sets up. Importing the
# package first is what the CLI happens to do and what keeps this file honest
# about the order rather than accidentally lucky.
from check_your_advisor.profile import report  # noqa: E402
from check_your_advisor.profile import html_report  # noqa: E402
from check_your_advisor import evaluations  # noqa: E402
from check_your_advisor.evaluations import (  # noqa: E402
    EVALUATION_CAVEATS,
    EVALUATION_LIMITS,
    EVALUATION_STANCE,
    REQUIRED_EITHER,
    REQUIRED_FIELDS,
    SCHEMA,
    EvaluationTableError,
    join_evaluations,
    load_evaluation_table,
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


TMP = tempfile.TemporaryDirectory()
ROOT = TMP.name


def write_csv(name: str, header: list[str], rows: list[list[str]],
               encoding: str = "utf-8-sig") -> str:
    """An evaluation table on disk. Written by hand, the way the user's would be."""
    path = os.path.join(ROOT, name)
    with open(path, "w", newline="", encoding=encoding) as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


PI = "Xiuying Chen"
FULL_HEADER = [column.zh for column in SCHEMA]


def full_row(source: str, date: str, content: str = "组会每周一次，论文改得很细",
             rating: str = "", role: str = "已毕业硕士", year: str = "2023",
             url: str = "https://example.invalid/post", advisor: str = PI) -> list[str]:
    """One row in `FULL_HEADER` order, so a fixture cannot drift from the schema."""
    values = {
        "advisor": advisor, "advisor_latin": "", "source": source, "retrieved_on": date,
        "content": content, "rating": rating, "student_role": role, "year": year, "url": url,
    }
    return [values[column.key] for column in SCHEMA]


# ============================================================
# 1. Schema — what the file must carry before it is read at all
# ============================================================

print("\n--- schema ---")

check("导师姓名, 评价来源 and 数据获取日期 are the required columns",
      sorted(REQUIRED_FIELDS), ["advisor", "retrieved_on", "source"])
check("评价内容 and 维度评分 are the one-of-these-two pair",
      sorted(REQUIRED_EITHER), ["content", "rating"])
check("the optional columns are the three the spec names, plus the advisor romanisation",
      sorted(c.key for c in SCHEMA if not c.required and c.key not in REQUIRED_EITHER),
      ["advisor_latin", "student_role", "url", "year"])
check("every column declares a Chinese and an English spelling",
      [c.key for c in SCHEMA if not (c.zh and c.en)], [])
check("no two columns share a header spelling",
      len({name for c in SCHEMA for name in (c.key, c.zh, c.en, *c.aliases)}),
      sum(len({c.key, c.zh, c.en, *c.aliases}) for c in SCHEMA))

good = write_csv("good.csv", FULL_HEADER, [
    full_row("知乎", "2026-08-20", rating="指导频率 4/5；push 3/5"),
    full_row("某导师评价站", "2026/08/21", content="放养，半年见一次",
             role="在读博士", year="2021", url=""),
    full_row("知乎", "2026-08-20", content="", rating="沟通 2/5", role="匿名", year="", url=""),
])
loaded = load_evaluation_table(good)
check("three usable rows load", loaded["denominator"], 3)
check("...and the denominator is the population every count below is over",
      loaded["denominator"], len(loaded["rows"]))
check("the encoding that worked is recorded, not guessed at later",
      loaded["encoding"], "utf-8-sig")
check("每 row keeps the source it came from",
      [row["source"] for row in loaded["rows"]], ["知乎", "某导师评价站", "知乎"])
check("a slash-form date is normalised without losing the raw text",
      (loaded["rows"][1]["retrieved_on"], loaded["rows"][1]["retrieved_on_raw"]),
      ("2026-08-21", "2026/08/21"))
check("a row with a 维度评分 and no 评价内容 is kept — a score is a statement",
      (loaded["rows"][2]["content"], loaded["rows"][2]["rating"]), ("", "沟通 2/5"))
check("the sources are summarised with their counts", loaded["sources"],
      {"某导师评价站": 1, "知乎": 2})
check("the span of retrieval dates is recorded", loaded["retrieved_on_range"],
      ("2026-08-20", "2026-08-21"))
check("...and so is the span of the statements themselves", loaded["year_range"], (2021, 2023))
check("a row with no year is counted, not dropped", loaded["rows_without_year"], 1)
check("EVALUATION_LIMITS travels with the loaded table", loaded["limits"], EVALUATION_LIMITS)


# ============================================================
# 2. A missing required column is refused, and the error names it
# ============================================================

print("\n--- missing columns ---")


def load_error(path: str) -> str:
    try:
        load_evaluation_table(path)
    except EvaluationTableError as exc:
        return str(exc)
    return ""


no_source = write_csv("no_source.csv",
                      ["导师姓名", "数据获取日期", "评价内容"],
                      [[PI, "2026-08-20", "还行"]])
message = load_error(no_source)
check_true("a table with no 评价来源 is refused", bool(message))
check_true("...and the error names the missing column in both languages",
           "评价来源" in message and "source" in message)
check_true("...and says why source and date are non-negotiable",
           "无法追溯" in message)
check_true("...and prints the headers it did find, so the fix is visible",
           "导师姓名" in message and "数据获取日期" in message)

no_date = write_csv("no_date.csv",
                    ["导师姓名", "评价来源", "评价内容"],
                    [[PI, "知乎", "还行"]])
check_true("a table with no 数据获取日期 is refused", "数据获取日期" in load_error(no_date))

no_advisor = write_csv("no_advisor.csv",
                       ["评价来源", "数据获取日期", "评价内容"],
                       [["知乎", "2026-08-20", "还行"]])
check_true("a table with no 导师姓名 is refused", "导师姓名" in load_error(no_advisor))

no_substance = write_csv("no_substance.csv",
                         ["导师姓名", "评价来源", "数据获取日期", "学生身份"],
                         [[PI, "知乎", "2026-08-20", "已毕业硕士"]])
message = load_error(no_substance)
check_true("a table with neither 评价内容 nor 维度评分 is refused", bool(message))
check_true("...and the error says the two columns are an either/or, not both",
           "至少要有一列" in message)
check_true("...and says what such a file is: citations with nothing cited",
           "只有出处没有内容" in message)

only_rating = write_csv("only_rating.csv",
                        ["导师姓名", "评价来源", "数据获取日期", "维度评分"],
                        [[PI, "知乎", "2026-08-20", "指导频率 4/5"]])
check("a table carrying 维度评分 alone is accepted",
      load_evaluation_table(only_rating)["denominator"], 1)
check("...and records that 评价内容 was the one not supplied",
      load_evaluation_table(only_rating)["columns_missing_either"], ["content"])

duplicated = write_csv("dup_header.csv",
                       ["导师姓名", "评价来源", "来源", "数据获取日期", "评价内容"],
                       [[PI, "知乎", "贴吧", "2026-08-20", "还行"]])
check_true("two headers folding to one field are refused rather than resolved",
           "两列都对应字段" in load_error(duplicated))

empty = os.path.join(ROOT, "empty.csv")
open(empty, "w", encoding="utf-8").close()
check_true("an empty file is refused with its own message", "空文件" in load_error(empty))

try:
    load_evaluation_table(os.path.join(ROOT, "nope.csv"))
    check("a path that does not exist raises", "no exception", "FileNotFoundError")
except FileNotFoundError:
    check("a path that does not exist raises FileNotFoundError, not a table error",
          "FileNotFoundError", "FileNotFoundError")

check_true("EvaluationTableError is catchable as ValueError, like the journal table's",
           issubclass(EvaluationTableError, ValueError))


# ============================================================
# 3. Headers in either language, and a GB18030 export
# ============================================================

print("\n--- headers and encodings ---")

english = write_csv("english.csv",
                    ["advisor", "source", "retrieved_on", "content", "year"],
                    [[PI, "Zhihu", "2026-08-20", "weekly group meeting", "2023"]])
check("an English header loads", load_evaluation_table(english)["denominator"], 1)

spaced = write_csv("spaced.csv",
                   ["导师 姓名", "评价（来源）", "数据_获取_日期", "评价内容"],
                   [[PI, "知乎", "2026-08-20", "还行"]])
check("case, spacing and punctuation are stripped from headers before matching",
      load_evaluation_table(spaced)["denominator"], 1)

gbk = write_csv("gbk.csv", FULL_HEADER, [full_row("知乎", "2026-08-20")],
                encoding="gb18030")
gbk_loaded = load_evaluation_table(gbk)
check("a GB18030 export loads", gbk_loaded["denominator"], 1)
check("...and the fallback encoding is recorded rather than hidden",
      gbk_loaded["encoding"], "gb18030")
check("...and the Chinese survives the fallback intact",
      gbk_loaded["rows"][0]["source"], "知乎")

unknown = write_csv("unknown_col.csv",
                    ["导师姓名", "评价来源", "数据获取日期", "评价内容", "点赞数"],
                    [[PI, "知乎", "2026-08-20", "还行", "31"]])
check("a column this tool does not know is listed, not fatal",
      load_evaluation_table(unknown)["unknown_columns"], ["点赞数"])


# ============================================================
# 4. Malformed cells are kept and flagged; unusable rows are listed
# ============================================================

print("\n--- kept, flagged, rejected ---")

messy = write_csv("messy.csv", FULL_HEADER, [
    full_row("知乎", "2026-08-20", year="不详"),
    full_row("知乎", "去年夏天", content="记不清了", year="2019"),
    full_row("贴吧", "2026-08-20", content="上个世纪的事", year="1899"),
    full_row("知乎", "2026-08-20", content="没有链接", url=""),
    full_row("", "2026-08-20", content="没写来源"),
    full_row("微博", "2026-08-20", content="", rating=""),
])
messy_loaded = load_evaluation_table(messy)
check("a row with no source cannot be attributed and is rejected",
      [r["reason"] for r in messy_loaded["rejected"] if r["reason"] == "no source recorded"],
      ["no source recorded"])
check("a row with neither content nor rating is rejected",
      len([r for r in messy_loaded["rejected"] if "neither" in r["reason"]]), 1)
check("every rejected row carries its line number for a human to go and fix",
      [isinstance(r["line"], int) for r in messy_loaded["rejected"]], [True, True])
check("four rows with unreadable cells survive as real statements",
      messy_loaded["denominator"], 4)
check("an unreadable year is flagged, not dropped",
      messy_loaded["rows"][0]["flags"], ["year_unreadable"])
check("...and the raw text is kept beside the parsed value",
      (messy_loaded["rows"][0]["year"], messy_loaded["rows"][0]["year_raw"]), (None, "不详"))
check_true("an unparseable retrieval date is flagged",
           "retrieved_on_unparseable" in messy_loaded["rows"][1]["flags"])
check("...and the cell is printed as written rather than blanked",
      messy_loaded["rows"][1]["retrieved_on"], "去年夏天")
check_true("a year before the sites existed is flagged as out of range",
           "year_out_of_range" in messy_loaded["rows"][2]["flags"])
check_true("a row with no 原文链接 is flagged so the gap is countable",
           "no_source_url" in messy_loaded["rows"][3]["flags"])
check_true("the flag counts are summarised for the log", messy_loaded["flag_counts"])

dupes = write_csv("dupes.csv", FULL_HEADER, [
    full_row("知乎", "2026-08-20", content="一模一样的一句话"),
    full_row("知乎", "2026-08-20", content="一模一样的一句话"),
    full_row("贴吧", "2026-08-20", content="一模一样的一句话"),
])
dupes_loaded = load_evaluation_table(dupes)
check("an exact duplicate is dropped once", dupes_loaded["denominator"], 2)
check("...and listed with its line number", len(dupes_loaded["duplicates_dropped"]), 1)
check("the same sentence on a second site is not a duplicate",
      sorted(row["source"] for row in dupes_loaded["rows"]), ["知乎", "贴吧"])
check("rows_read covers everything the file held, usable or not",
      dupes_loaded["rows_read"], 3)

multiline = write_csv("multiline.csv", FULL_HEADER,
                      [full_row("知乎", "2026-08-20", content="第一行\n第二行\n第三行")])
check("a pasted multi-line statement is rejoined into one body line",
      load_evaluation_table(multiline)["rows"][0]["content"], "第一行 第二行 第三行")

# The whole section is built on "printed as given", and NFKC — which both other
# table loaders apply to every cell — rewrites ，to , and （to (. On a journal
# name that is a harmless comparison fold; on somebody's sentence it is a silent
# edit to a quotation. So the free text is stripped and nothing else.
VERBATIM = "组会每周一次，论文改得很细（但要求多）；push 强度还好"
verbatim = write_csv("verbatim.csv", FULL_HEADER,
                     [full_row("知乎（提问帖）", "2026-08-20", content=VERBATIM,
                               rating="指导频率 4／5")])
verbatim_row = load_evaluation_table(verbatim)["rows"][0]
check("full-width punctuation in a statement survives the loader untouched",
      verbatim_row["content"], VERBATIM)
check("...and in a 维度评分 too", verbatim_row["rating"], "指导频率 4／5")
check("...and in a source name, which is printed beside every row",
      verbatim_row["source"], "知乎（提问帖）")
check("a full-width date still parses, because folding happens where it is needed",
      load_evaluation_table(write_csv(
          "wide_date.csv", FULL_HEADER,
          [full_row("知乎", "２０２６－０８－２０")]))["rows"][0]["retrieved_on"],
      "2026-08-20")


# ============================================================
# 5. The join — counts with denominators, and attribution
# ============================================================

print("\n--- join ---")

joined = join_evaluations(loaded, PI)
check("every statement is attributed to the PI who is named in the file",
      joined["advisor_filter"], "matched")
check("the count of statements is printed against the rows in the file",
      (joined["counts"]["evaluations_total"], joined["counts"]["rows_in_file"]), (3, 3))
check("the number of distinct sources is its own count",
      joined["counts"]["source_count"], 2)
check("...with the per-source counts beside it, never one total",
      joined["sources"], {"知乎": 2, "某导师评价站": 1})
check("the time span the statements cover is returned", joined["year_range"], (2021, 2023))
check("rows that cannot be placed in time are counted separately",
      (joined["counts"]["rows_with_year"], joined["counts"]["rows_without_year"]), (2, 1))
check("...and the two add up to the whole",
      joined["counts"]["rows_with_year"] + joined["counts"]["rows_without_year"],
      joined["counts"]["evaluations_total"])
check("the span of days the pages were read is returned",
      joined["retrieved_on_range"], ("2026-08-20", "2026-08-21"))
check("every entry carries its source", [bool(e["source"]) for e in joined["entries"]],
      [True, True, True])
check("every entry carries the day that source was read",
      [bool(e["retrieved_on"]) for e in joined["entries"]], [True, True, True])
check("entries come back in file order, because any sort would rank the statements",
      [e["line"] for e in joined["entries"]], [2, 3, 4])
check("the file's path and encoding travel with the rows",
      (joined["provenance"]["path"], joined["provenance"]["encoding"]), (good, "utf-8-sig"))
check("...and so does how much of the file was unusable",
      (joined["provenance"]["rejected"], joined["provenance"]["duplicates_dropped"]), (0, 0))
check("the stance travels with the result rather than living only in the renderer",
      joined["stance"], EVALUATION_STANCE)
check("a bare list of rows joins the same way as a loaded table",
      join_evaluations(loaded["rows"], PI)["counts"]["evaluations_total"], 3)

other = write_csv("other_advisor.csv", FULL_HEADER, [
    full_row("知乎", "2026-08-20", advisor="Somebody Else"),
    full_row("贴吧", "2026-08-20", advisor="Somebody Else"),
    full_row("知乎", "2026-08-20", advisor=PI, content="这条是本人的"),
])
mixed = join_evaluations(load_evaluation_table(other), PI)
check("only the rows naming this PI are attributed to them",
      mixed["counts"]["evaluations_total"], 1)
check("...out of every row the file held", mixed["counts"]["rows_in_file"], 3)
check("...and the advisors the file names are listed so a typo is visible",
      sorted(mixed["advisor_names_seen"]), ["Somebody Else", PI])

rotated = join_evaluations(loaded, "Chen Xiuying")
check("a romanised name in the other order still joins", rotated["advisor_filter"], "matched")

single = write_csv("single_advisor.csv", FULL_HEADER,
                   [full_row("知乎", "2026-08-20", advisor="陈秀英")])
unverified = join_evaluations(load_evaluation_table(single), PI)
check("one advisor in the file who did not join is taken as this PI, and marked unverified",
      unverified["advisor_filter"], "unverified_single_advisor")
check("...with the row still counted", unverified["counts"]["evaluations_total"], 1)
check_true("...and the note says whose statements they are if the file was for someone else",
           "that other person" in (unverified["advisor_note"] or ""))

several = write_csv("several_advisors.csv", FULL_HEADER, [
    full_row("知乎", "2026-08-20", advisor="张三"),
    full_row("贴吧", "2026-08-20", advisor="李四"),
])
refused = join_evaluations(load_evaluation_table(several), PI)
check("several advisors and none joining is refused, not guessed",
      refused["advisor_filter"], "refused")
check("...so nothing is attributed", refused["counts"]["evaluations_total"], 0)
check_true("...and the reason is carried, not rounded off", refused["suppressed_reasons"])
check_true("...and the advisors seen are named so pi_name can be fixed",
           "张三" in refused["suppressed_reasons"][0])

check("an empty table joins to an empty result rather than raising",
      join_evaluations({"rows": []}, PI)["counts"]["evaluations_total"], 0)
check_true("...and says why there is nothing to print",
           join_evaluations({"rows": []}, PI)["suppressed"])


# ============================================================
# 6. What this section refuses to compute
# ============================================================

print("\n--- no sentiment, no aggregate, no score ---")

FORBIDDEN = ("sentiment", "polarity", "positive", "negative", "average", "mean_",
             "overall", "grade", "star", "percent", "share")
flat = json.dumps(joined, ensure_ascii=False, default=str)


def keys_of(value, found=None):
    found = [] if found is None else found
    if isinstance(value, dict):
        for key, item in value.items():
            found.append(key)
            keys_of(item, found)
    elif isinstance(value, list):
        for item in value:
            keys_of(item, found)
    return found


check("no key anywhere in the join names a sentiment, a score or a share",
      sorted({k for k in keys_of(joined) if any(word in k.lower() for word in FORBIDDEN)}), [])
check("no percentage is produced", "%" in flat.replace("4/5", ""), False)
check_true("the stance says outright that no sentiment analysis is run",
           "no sentiment analysis" in EVALUATION_STANCE)
check_true("...and that nothing here reaches the composite score",
           "contributes nothing to the composite score" in EVALUATION_STANCE)
check_true("...and that the judgement is the reader's",
           "the reader's call" in EVALUATION_STANCE)
check("the caveat register covers collection, sentiment, scoring, order and double counting",
      sorted(EVALUATION_CAVEATS), ["EVL-01", "EVL-02", "EVL-03", "EVL-04", "EVL-05"])
check_true("EVL-01 states that nothing was fetched and there is no crawler",
           "no crawler" in EVALUATION_CAVEATS["EVL-01"])
check_true("EVL-03 states that no row enters the score at any weight, including zero",
           "including zero" in EVALUATION_CAVEATS["EVL-03"])
check("every limit is a (name, explanation) pair the renderer can print",
      [len(item) for item in EVALUATION_LIMITS], [2] * len(EVALUATION_LIMITS))
check_true("the self-selection limit is stated first, because it bends every count",
           "chose to write" in EVALUATION_LIMITS[0][0])


# ============================================================
# 7. Section 20 in the report — supplied, and absent
# ============================================================

print("\n--- section 20 ---")

ORCID = "0000-0002-1825-0097"


def author(name, affiliation="", email="", orcid=""):
    parts = name.split()
    return {
        "name": name, "last": parts[-1], "fore": " ".join(parts[:-1]),
        "initials": "".join(p[0] for p in parts[:-1]), "affiliation": affiliation,
        "email": email, "orcid": orcid, "equal_contrib": False,
        "is_corresponding": bool(email),
    }


def paper(pmid, authors, pub_date="2023 Jun"):
    return {
        "pmid": str(pmid), "title": f"Study {pmid}", "authors": authors,
        "authors_str": ", ".join(a["name"] for a in authors), "journal": "J Hepatol",
        "pub_date": pub_date, "pub_year": pub_date.split()[0], "volume": "", "issue": "",
        "pages": "", "doi": "", "pmc_id": "", "abstract": "",
        "pi_index": len(authors) - 1, "pi_evidence": "orcid",
    }


def corpus():
    pi_author = author(PI, "Teaching Hospital", "", ORCID)
    papers = [paper(900 + i, [author(f"Lead{i:02d} Person"), pi_author]) for i in range(8)]
    return {
        "schema_version": 1, "generated_at": "2026-07-22T20:47:11", "position_filtered": False,
        "query": {"term": "x", "mindate": "2021/07/22", "maxdate": "2026/07/22",
                  "years_back": 5, "retmax": 500, "esearch_count": 8,
                  "pmids_returned": 8, "truncated": False},
        "identity": {"author_name": PI, "orcid": ORCID,
                     "affiliation_keywords": ["Teaching Hospital"], "email_domains": []},
        "counts": {"fetched": 8, "verified": 8, "name_only": 0, "rejected": 0,
                   "by_evidence": {"orcid": 8}},
        "fallback_fired": False, "papers": papers,
    }


def section(rep, section_id):
    return next(s for s in rep["sections"] if s["id"] == section_id)


with_table = report.build_report(corpus(), {}, evaluation_table=loaded)
without = report.build_report(corpus(), {})

check("section 20 exists", section(with_table, 20)["id"], 20)
check("it is printed last, after section 18",
      [s["id"] for s in with_table["sections"]][-2:], [18, 20])
check("it renders whether or not a table was supplied",
      [len(rep["sections"]) for rep in (with_table, without)],
      [len(with_table["sections"])] * 2)
check("the id is 20 and nothing below it was renumbered to make room",
      sorted(s["id"] for s in with_table["sections"]), list(range(21)))

body = "\n".join(section(with_table, 20)["body"])
prose = "\n".join(section(with_table, 20)["prose"])
check("the count of statements is printed against the rows in the file",
      "3 statement(s) attributed to this advisor, out of 3 usable row(s)" in body, True)
check_true("the number of sources is printed", "distinct 评价来源" in body)
check_true("the time span the statements cover is printed", "2021 to 2023" in body)
check_true("the span of retrieval dates is printed", "2026-08-20 to 2026-08-21" in body)
check("every statement is printed with its source and its retrieval date",
      body.count("来源 ") - body.count("评价来源"), 3)
check("...each one carrying a 获取日期", body.count("获取日期 "), 3)
check("no percentage appears in the computed half of the section", "%" in body, False)
check_true("the stance is in the prose, where no renderer collapses it",
           EVALUATION_STANCE in prose)
check_true("the prose says the collection was the reader's own work",
           "Nothing here was fetched" in prose)
check_true("...and that this package has no crawler", "no crawler" in prose)
check_true("the prose says why the section is last and why it is numbered 20",
           "printed last and numbered 20" in prose)

absent = "\n".join(section(without, 20)["body"])
check_true("with no table the section states that it was not computed",
           absent.startswith("Not computed:"))
check_true("...and prints the reason rather than a blank column", "Reason: " in absent)
check_true("...and the reason names the flag that fixes it", "--evaluation-table" in absent)
check_true("...and the required columns are spelled out",
           "评价来源" in absent and "数据获取日期" in absent)
check_true("...and says the absence is neither an endorsement nor an accusation",
           "an endorsement or an accusation" in absent)
check_true("...and that no worklist command exists to generate this one",
           "no worklist generator" in absent)
check("...and no count is invented to fill the space", "%" in absent, False)

caller_note = report.build_report(corpus(), {}, evaluation_note="--evaluation-table was not given")
check_true("a caller's own note is printed in place of the default",
           "--evaluation-table was not given"
           in "\n".join(section(caller_note, 20)["body"]))


# ============================================================
# 8. The keys the report contract promises
# ============================================================

print("\n--- report keys ---")

check("the join is hoisted to a top-level key, not buried in metrics",
      with_table["evaluations"]["counts"]["evaluations_total"], 3)
check("...and never appears inside metrics",
      [k for k in with_table["metrics"] if "eval" in k.lower()], [])
check("the note is blank when a table was supplied", with_table["evaluation_note"], "")
check_true("...and carries the default reason when one was not",
           "--evaluation-table" in without["evaluation_note"])
# Read off the module rather than hard-coded to 4. The number this file cared
# about was "it went up when `evaluations` was added", and pinning the literal
# made the next additive key — `journal_risk`, version 5 — fail here in a file
# that has nothing to do with journals. The version's own history is asserted in
# report.py's comment block and in test_profile.py.
check("the schema version is the one report.py declares",
      with_table["schema_version"], report.SCHEMA_VERSION)
check("...and it is past the version that first carried evaluations",
      report.SCHEMA_VERSION >= 4, True)

refusal = report.build_report({"papers": "not a list"}, {})
check_true("a refused report still fires its gate", refusal["refused"])
check("...and both new keys exist so a consumer can read them unguarded",
      (refusal["evaluations"], refusal["evaluation_note"]), (None, ""))
check("...as None and empty string, never 0 and never {}",
      [type(refusal[k]).__name__ for k in ("evaluations", "evaluation_note")], ["NoneType", "str"])

check("the json record carries the evaluations",
      report.json_record(with_table)["evaluations"]["counts"]["evaluations_total"], 3)
check("...and drops the rendered sections, as it does for every other section",
      "sections" in report.json_record(with_table), False)


# ============================================================
# 9. The score does not move
# ============================================================

print("\n--- the composite score is untouched ---")

check("supplying an evaluation table changes no score component",
      with_table["score"], without["score"])
check("...and no star band", with_table["stars"], without["stars"])
check("...and no metric anywhere", with_table["metrics"], without["metrics"])
score_body = "\n".join(section(with_table, 16)["body"])
check("section 16 names no evaluation input",
      [word for word in ("evaluation", "评价") if word in score_body], [])

# A refusal that lives only in the module that refuses is a refusal the report
# never states. Section 14 is the register of everything deliberately not
# computed, and this one belongs in it beside the letter grade and the fitted
# slope rather than inline in Section 20's own prose.
register = "\n".join(section(with_table, 14)["prose"])
check_true("the refusal to score evaluations is registered in section 14",
           "A sentiment score or overall rating" in register)
check_true("...and the register says it is a decision, not a difficulty",
           "Refused by decision, not by difficulty" in register)
check_true("...and that no row enters the score at any weight",
           "including zero" in register)


# ============================================================
# 10. Both renderers
# ============================================================

print("\n--- markdown and html ---")

markdown = report.render_markdown(with_table)
check_true("the section heading survives into the Markdown",
           any(line.startswith("## 20.") and "Student evaluations" in line
               for line in markdown.splitlines()))
check_true("...in both languages, since round four made headings bilingual",
           any(line.startswith("## 20.") and "学生评价" in line
               for line in markdown.splitlines()))
check_true("...and each statement with its source",
           "来源 知乎" in markdown and "获取日期 2026-08-20" in markdown)

page = html_report.render_html(with_table, {})
check("one h2 per section, section 20 included",
      page.count("<h2"), len(with_table["sections"]) + 1)
check_true("section 20 has its own anchor", 'id="s20"' in page)
check_true("...and is linked from the table of contents", 'href="#s20"' in page)
segment = page.split('id="s20"', 1)[1].split("</section>", 1)[0]
check("nothing in section 20 is behind a disclosure", "<details" in segment, False)
check_true("the per-source table renders as a table", "<table>" in segment)
check_true("the stance renders above the numbers, in a paragraph nobody has to expand",
           segment.index("no sentiment analysis") < segment.index("<table>"))
check_true("the page header says the statements are not scored",
           "no contribution to any score on this page" in page)

# A source containing a pipe would split one table cell into two and shift every
# column after it: `html_report._cells` splits on the character and honours no
# escape. The character is replaced, not the text dropped.
piped = write_csv("piped.csv", FULL_HEADER,
                  [full_row("知乎 | 提问帖", "2026-08-20")])
piped_report = report.build_report(corpus(), {},
                                   evaluation_table=load_evaluation_table(piped))
piped_body = section(piped_report, 20)["body"]
source_rows = [line for line in piped_body if line.startswith("| 知乎")]
check("a pipe in a source name does not split the table row",
      [line.count("|") for line in source_rows], [4])
piped_page = html_report.render_html(piped_report, {})
piped_segment = piped_page.split('id="s20"', 1)[1].split("</section>", 1)[0]
check_true("...and the full source name survives into the rendered cell",
           "提问帖" in piped_segment)

# Sections 3 and 7 are shares of a stated denominator. Section 9 joined them in
# round four for a different reason and is held to a narrower rule: the only "%"
# allowed there is the confidence level on the fitted slope, which is not a share
# of anything in the corpus — it says how often an interval built this way covers
# the true value, and prints on the same line as the interval it belongs to. Any
# other percentage in section 9 is the failure this guard was written to catch,
# so the exception is conditioned on that word rather than granted to the section.
check("no section body outside 3, 7 and 9 prints a percentage, section 20 included",
      {s["id"] for s in with_table["sections"]
       if any("%" in line for line in s["body"])} - {3, 7, 9}, set())
check("section 9's only percentage is the confidence level beside its interval",
      [line for line in section(with_table, 9)["body"]
       if "%" in line and "interval" not in line], [])

# The report labels no person a student: nothing in PubMed separates a PhD
# student from a postdoc, a technician or a visiting scholar, so the computed
# half of every section is scanned for the word (test_profile.py T60). This
# section is *called* "Student evaluations" and still has to honour that in its
# body, which is why the absent-table note reads "evaluation table".
for _rep, _label in ((with_table, "with a table"), (without, "without one")):
    check(f"section 20's computed half keeps the word 'student' out, {_label}",
          [line for line in section(_rep, 20)["body"] if "student" in line.lower()], [])
check_true("...while the title still says what the table is",
           "Student evaluations" in section(with_table, 20)["title"])

TMP.cleanup()

print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(1 if _failed else 0)
