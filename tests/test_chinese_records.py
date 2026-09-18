#!/usr/bin/env python3
"""
`chinese_records.py`: the fourth hand-filled table, and the first one that moves
a denominator every other section has already printed.

Why this table is not like the other three
------------------------------------------
`journals.py` adds a column to papers the corpus already holds. `theses.py` and
`evaluations.py` add rows that live in their own section and touch nothing else.
This one adds **papers**. A Chinese medical PI with 12 PubMed records and 40
中文核心 records has had every earlier number in the report — 年产出, 一作名额
分布, 人员流动 — computed over a quarter of their output, with nothing on the
page saying so. So the assertions run in four directions:

  1. **Schema and provenance, the rules all four tables live by.** 数据来源 and
     数据获取日期 are required columns; a file missing any required column is
     refused at load with that column named and the headers actually found
     printed beside it. Every record this module emits carries both, and no
     function here writes a file of any kind — a value someone typed into a
     spreadsheet must never end up inside a harvested `papers_*.json` looking
     like something PubMed returned.

  2. **The name join, which is what this feature is most likely to die of.** A
     CNKI export writes 作者 in Chinese characters; PubMed writes the same
     person as "Zhu Guangwei". Nothing in the standard library converts one into
     the other, so 作者拼音 is a **required** column here rather than the
     optional one it is in `theses.py`. Every row whose byline the PI could not
     be placed in still becomes a record — with an empty `role` and a named
     reason — because dropping it would delete a real paper to tidy up a match
     failure.

  3. **The merge, and its separate denominators.** `merge_chinese_records`
     reports how many records PubMed held, how many the table held, how many
     survived, and how many hits were only suspected duplicates. Four numbers,
     never one blended total, the way `openalex.merge_corpora` does it.

  4. **The dedup key, and its stated weakness.** 中文题录 carry neither DOI nor
     PMID, so the only key available is normalised title + year — one tier where
     `openalex.py` has three, failing in both directions. The docstring has to
     say so and these tests hold it to that.

Encoding gets its own block because it is the first thing that will go wrong in
the field: Excel on a Chinese Windows writes GBK, everything else writes UTF-8
with a BOM, and a file that is neither must produce a sentence the user can act
on rather than a bare `UnicodeDecodeError` from the middle of the loader.

All data is synthetic and written to a temporary directory. Standard library
only, no network.

Run: python tests/test_chinese_records.py
"""

from __future__ import annotations

import csv
import io
import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

# `profile` first, then the module under test: `chinese_records` imports
# `theses`, which defers its own `profile.metrics` import into a function to
# break the `profile/__init__ -> html_report -> report -> theses` cycle.
# Importing the package first is what the CLI happens to do, and doing it here
# on purpose keeps this file honest rather than accidentally lucky.
from check_your_advisor.profile import report  # noqa: E402,F401
from check_your_advisor import openalex  # noqa: E402
from check_your_advisor import pubmed_api  # noqa: E402
from check_your_advisor import chinese_records  # noqa: E402
from check_your_advisor.chinese_records import (  # noqa: E402
    AUTHOR_SEPARATORS,
    CHINESE_RECORD_CAVEATS,
    CHINESE_RECORD_LIMITS,
    CORRESPONDING_MARKS,
    DEDUP_KEY_BASIS,
    EARLIEST_PLAUSIBLE_YEAR,
    ENCODINGS,
    MATCH_RULES,
    OPTIONAL_KEYS,
    REQUIRED_FIELDS,
    ROLE_MARKER,
    SCHEMA,
    SOURCE_CHINESE,
    SOURCE_DB_VALUES,
    SOURCE_PUBMED,
    ChineseRecordError,
    load_chinese_records,
    merge_chinese_records,
    to_paper_records,
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
    """A 题录 export on disk, written the way the user's spreadsheet would be."""
    path = os.path.join(ROOT, name)
    with open(path, "w", newline="", encoding=encoding) as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def write_bytes(name: str, header: list[str], rows: list[list[str]],
                encoding: str) -> str:
    """The same file, encoded by hand — for the codecs a Chinese Excel emits."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    path = os.path.join(ROOT, name)
    with open(path, "wb") as handle:
        handle.write(buffer.getvalue().encode(encoding))
    return path


def raises(fn, *args, **kwargs):
    """The exception `fn` raised, or None."""
    try:
        fn(*args, **kwargs)
    except Exception as error:  # noqa: BLE001 — the type is what is under test
        return error
    return None


PI_LATIN = "Zhu Guangwei"
PI_HAN = "朱广伟"
FULL_HEADER = [column.zh for column in SCHEMA]
REQUIRED_HEADER = [c.zh for c in SCHEMA if c.required]
DROP_OPTIONAL = tuple(c.key for c in SCHEMA if not c.required)


def full_row(title: str = "腹腔镜胃癌根治术后并发症的危险因素分析",
             authors: str = "朱广伟;张三;李四",
             pinyin: str = "Zhu Guangwei;Zhang San;Li Si",
             journal: str = "中华消化外科杂志",
             year: str = "2023",
             source_db: str = "知网",
             retrieved_on: str = "2026-09-01",
             **overrides: str) -> list[str]:
    """One row in `FULL_HEADER` order, so a fixture cannot drift from the schema."""
    values = {
        "title": title,
        "authors": authors,
        "authors_pinyin": pinyin,
        "journal": journal,
        "pub_year": year,
        "source_db": source_db,
        "retrieved_on": retrieved_on,
        "issn": "1673-9752",
        "doi": "",
        "volume": "22",
        "issue": "6",
        "pages": "601-608",
        "abstract": "",
        "index_status": "北大核心",
        "institution": "海源学院附属医院胃肠外科",
        "notes": "",
    }
    values.update(overrides)
    return [values[column.key] for column in SCHEMA]


def header_without(*keys: str) -> list[str]:
    return [c.zh for c in SCHEMA if c.key not in keys]


def row_without(*keys: str, **overrides: str) -> list[str]:
    values = dict(zip([c.key for c in SCHEMA], full_row(**overrides), strict=True))
    return [values[c.key] for c in SCHEMA if c.key not in keys]


# ============================================================
# 1. Schema — what the file must carry before it is read at all
# ============================================================

print("\n--- schema ---")

check("the required columns are the seven a record cannot be used without",
      sorted(REQUIRED_FIELDS),
      ["authors", "authors_pinyin", "journal", "pub_year", "retrieved_on",
       "source_db", "title"])
check("作者拼音 is required here, unlike the optional column of the same job in theses.py",
      "authors_pinyin" in REQUIRED_FIELDS, True)
check("no required column is also listed as optional",
      sorted(set(REQUIRED_FIELDS) & set(OPTIONAL_KEYS)), [])
check("every schema key is either required or optional",
      sorted(c.key for c in SCHEMA), sorted(set(REQUIRED_FIELDS) | set(OPTIONAL_KEYS)))
check("every column declares a Chinese and an English spelling",
      [c.key for c in SCHEMA if not (c.zh and c.en)], [])
check("no two columns share a header spelling",
      len({name for c in SCHEMA for name in (c.key, c.zh, c.en, *c.aliases)}),
      sum(len({c.key, c.zh, c.en, *c.aliases}) for c in SCHEMA))

# The thresholds and the required-column list have to be module constants, not
# literals inside the loader: a reader must be able to see what the file must
# carry without reading the code that reads it.
for _name in ("SCHEMA", "REQUIRED_FIELDS", "OPTIONAL_KEYS", "ENCODINGS",
              "SOURCE_DB_VALUES", "EARLIEST_PLAUSIBLE_YEAR", "AUTHOR_SEPARATORS",
              "CORRESPONDING_MARKS", "DEDUP_KEY_BASIS", "MATCH_RULES",
              "SOURCE_CHINESE", "SOURCE_PUBMED", "ROLE_MARKER"):
    check_true(f"{_name} is declared at module scope and exported",
               _name in chinese_records.__all__ and hasattr(chinese_records, _name))

check("__all__ is sorted, so a new export cannot hide at the bottom",
      list(chinese_records.__all__), sorted(chinese_records.__all__))
check("the encodings tried are declared, UTF-8 first",
      list(ENCODINGS), ["utf-8-sig", "gb18030"])
check("the libraries a row can come from are enumerated",
      list(SOURCE_DB_VALUES), ["知网", "万方", "维普", "其他"])
check_true("the plausible-year floor is a declared year, not a number in a branch",
           1900 < EARLIEST_PLAUSIBLE_YEAR < 2000)
check_true("the author separators cover half-width and full-width punctuation alike",
           {";", "；", "、", ","} <= set(AUTHOR_SEPARATORS))
check_true("the corresponding-author marks include the full-width star",
           {"*", "＊"} <= set(CORRESPONDING_MARKS))

# `openalex.SOURCE_PUBMED` cannot be imported by this module: openalex imports
# http_client, and `chinese_records` is reachable from profile/, which must stay
# offline. So the string is re-declared there and this assertion is the thing
# that keeps the two equal.
check("SOURCE_PUBMED equals the string openalex.py uses — checked, not imported",
      SOURCE_PUBMED, openalex.SOURCE_PUBMED)
check("SOURCE_CHINESE is a third source name, distinct from both existing ones",
      len({SOURCE_CHINESE, openalex.SOURCE_PUBMED, openalex.SOURCE_OPENALEX}), 3)


# ============================================================
# 2. Loading — refused, flagged, or rejected, never silently dropped
# ============================================================

print("\n--- loading ---")

good = write_csv("good.csv", FULL_HEADER, [
    full_row(),
    full_row(title="加速康复外科在结直肠癌手术中的应用",
             authors="张三;朱广伟*", pinyin="Zhang San;Zhu Guangwei*",
             journal="中国实用外科杂志", year="2024", source_db="万方"),
    full_row(title="幽门螺杆菌根除治疗的时机选择",
             authors="李四;朱广伟;王五", pinyin="Li Si;Zhu Guangwei;Wang Wu",
             journal="中华内科杂志", year="2022", source_db="维普"),
])
loaded = load_chinese_records(good)

check("three usable rows load", loaded["denominator"], 3)
check("...and the denominator is the population every count below is over",
      loaded["denominator"], len(loaded["rows"]))
check("nothing was rejected", loaded["rejected"], [])
check("nothing was dropped as a duplicate", loaded["duplicates_dropped"], [])
check("the file the numbers came from is recorded", loaded["path"], good)
check("the encoding that worked is recorded rather than assumed",
      loaded["encoding"], "utf-8-sig")
check("the libraries the rows came from are counted separately",
      loaded["source_dbs"], {"万方": 1, "知网": 1, "维普": 1})
check("the day each library was read is carried up",
      loaded["retrieved_dates"], ["2026-09-01"])
check("the years the rows cover", loaded["year_range"], (2022, 2024))
check("every schema key maps to the header as it was actually written",
      loaded["columns_used"]["authors_pinyin"], "作者拼音")
check("no optional column is missing from a full file",
      loaded["columns_missing_optional"], [])
check("no header went unrecognised", loaded["unknown_columns"], [])
check("the limits travel with the table", loaded["limits"], CHINESE_RECORD_LIMITS)
check("the result is JSON-serialisable",
      isinstance(json.dumps(loaded, default=str), str), True)

english = write_csv("english.csv",
                    ["title", "authors", "authors_pinyin", "journal",
                     "pub_year", "source_db", "retrieved_on"],
                    [row_without(*DROP_OPTIONAL)])
check("an English-headed file loads", load_chinese_records(english)["denominator"], 1)
check("...and reports the optional columns it did not find",
      sorted(load_chinese_records(english)["columns_missing_optional"]),
      sorted(DROP_OPTIONAL))

noisy = write_csv("noisy.csv",
                  ["篇 名", "作者", "作者_拼音", "期　刊", " 发表年份 ",
                   "数据来源：", "数据获取日期"],
                  [row_without(*DROP_OPTIONAL)])
check("headers survive spacing, underscores, ideographic spaces and full-width colons",
      load_chinese_records(noisy)["denominator"], 1)

extra = write_csv("extra.csv", FULL_HEADER + ["被引频次"], [full_row() + ["17"]])
check("a column this tool does not know is named back to the user, not swallowed",
      load_chinese_records(extra)["unknown_columns"], ["被引频次"])


# ------------------------------------------------------------------
# Missing required columns — named one at a time
# ------------------------------------------------------------------

print("\n--- a missing required column is refused, by name ---")

for _key in REQUIRED_FIELDS:
    _column = next(c for c in SCHEMA if c.key == _key)
    _path = write_csv(f"missing_{_key}.csv", header_without(_key), [row_without(_key)])
    _error = raises(load_chinese_records, _path)
    check_true(f"a file with no {_column.zh} column is refused",
               isinstance(_error, ChineseRecordError))
    check_true(f"...and the error names {_column.zh} specifically",
               _column.zh in str(_error))
    check_true(f"...and prints the headers it did find alongside ({_column.zh})",
               "表头" in str(_error))

_two_missing = write_csv("missing_two.csv", header_without("source_db", "retrieved_on"),
                         [row_without("source_db", "retrieved_on")])
_error = raises(load_chinese_records, _two_missing)
check_true("two missing columns are both named, not just the first one found",
           "数据来源" in str(_error) and "数据获取日期" in str(_error))
check_true("...with the reason the provenance columns are required at all",
           "追溯" in str(_error))

_no_pinyin = write_csv("no_pinyin.csv", header_without("authors_pinyin"),
                       [row_without("authors_pinyin")])
_error = raises(load_chinese_records, _no_pinyin)
check_true("a file with no 作者拼音 column is refused rather than loaded unjoinable",
           isinstance(_error, ChineseRecordError))
check_true("...and the error says why that one column decides the whole feature",
           "拼音" in str(_error))

check("the error subclasses ValueError, so `except ValueError` callers still work",
      issubclass(ChineseRecordError, ValueError), True)

_dupe_header = write_csv("dupe_header.csv", FULL_HEADER + ["题名"], [full_row() + ["x"]])
_error = raises(load_chinese_records, _dupe_header)
check_true("two headers folding to one field are refused, not silently resolved",
           isinstance(_error, ChineseRecordError))
check_true("...and both column positions are named",
           "第 1 列" in str(_error) and f"第 {len(FULL_HEADER) + 1} 列" in str(_error))

check_true("an empty file is refused with a sentence, not an IndexError",
           isinstance(raises(load_chinese_records, write_csv("empty.csv", [], [])),
                      ChineseRecordError))
check_true("a missing file raises FileNotFoundError",
           isinstance(raises(load_chinese_records, os.path.join(ROOT, "nope.csv")),
                      FileNotFoundError))


# ------------------------------------------------------------------
# Rows: rejected with a line number, or kept with a flag
# ------------------------------------------------------------------

print("\n--- rows are rejected by line, or kept and flagged ---")

messy = write_csv("messy.csv", FULL_HEADER, [
    full_row(),                                                       # line 2
    full_row(title="", authors="朱广伟", pinyin="Zhu Guangwei"),        # line 3
    full_row(title="无年份的一条", year="见刊中"),                       # line 4
    full_row(title="无作者的一条", authors="", pinyin=""),               # line 5
    [],                                                               # line 6
    full_row(title="年份离谱的一条", year="1066"),                       # line 7
    full_row(title="日期读不出来的一条", retrieved_on="上个月"),          # line 8
    full_row(title="库名不认识的一条", source_db="某站"),                 # line 9
    full_row(title="拼音列空着的一条", pinyin=""),                       # line 10
    full_row(title="拼音数目对不上的一条", authors="朱广伟;张三;李四",      # line 11
             pinyin="Zhu Guangwei;Zhang San"),
])
messy_loaded = load_chinese_records(messy)
_by_line = {row["line"]: row for row in messy_loaded["rows"]}
_reasons = {row["line"]: row["reason"] for row in messy_loaded["rejected"]}

check("rows that cannot be counted at all are rejected, with their line number",
      sorted(_reasons), [3, 4, 5])
check("...the one with no title says so", "篇名" in _reasons[3], True)
check("...the one with no readable year says so, and quotes the cell",
      "见刊中" in _reasons[4], True)
check("...the one with no byline says so", "作者" in _reasons[5], True)
check("a blank line is skipped rather than rejected", 6 in _reasons, False)
check("every other row survives", messy_loaded["denominator"], 6)
check("rows_read counts every non-blank line the file held",
      messy_loaded["rows_read"], 9)

_flags = messy_loaded["flag_counts"]
check("an implausible year is flagged, not dropped — a wrong year is a real paper",
      _flags.get("pub_year_out_of_range"), 1)
check("an unreadable 数据获取日期 is flagged", _flags.get("retrieved_on_unparseable"), 1)
check("a library name outside the enumeration is flagged",
      _flags.get("source_db_unrecognised"), 1)
check("a blank 作者拼音 cell is flagged — the column is required, a cell can be empty",
      _flags.get("pinyin_missing"), 1)
check("a pinyin list of a different length to the byline is flagged separately",
      _flags.get("author_count_mismatch"), 1)
check("a blank pinyin cell is not also counted as a length mismatch",
      _by_line[10]["flags"], ["pinyin_missing"])
check("an unrecognised library resolves to the 其他 bucket rather than vanishing",
      _by_line[9]["source_db"], "其他")
check("...with the raw string kept beside it", _by_line[9]["source_db_raw"], "某站")
check("the raw year text survives beside the parsed one",
      (_by_line[7]["pub_year"], _by_line[7]["pub_year_raw"]), (1066, "1066"))
check("the raw retrieval date survives when it cannot be parsed",
      (_by_line[8]["retrieved_on"], _by_line[8]["retrieved_on_raw"]), ("", "上个月"))
check("a mismatched pinyin list is padded, never truncated to fit",
      (_by_line[11]["authors"], _by_line[11]["authors_pinyin"]),
      (["朱广伟", "张三", "李四"], ["Zhu Guangwei", "Zhang San", ""]))

# The same paper exported twice from one library is an export artefact. The same
# paper held by two libraries is not, and stays two rows until the merge.
dupes = write_csv("dupes.csv", FULL_HEADER, [
    full_row(),
    full_row(title="腹腔镜胃癌根治术后并发症的 危险因素分析！"),
    full_row(source_db="万方"),
])
dupes_loaded = load_chinese_records(dupes)
check("the same title and year from the same library is dropped once",
      len(dupes_loaded["duplicates_dropped"]), 1)
check("...and the dropped row is listed with its line",
      dupes_loaded["duplicates_dropped"][0]["line"], 3)
check("...and its title, so the drop can be checked",
      dupes_loaded["duplicates_dropped"][0]["title"],
      "腹腔镜胃癌根治术后并发症的 危险因素分析！")
check("the same paper held by a second library is kept — that is two libraries agreeing",
      dupes_loaded["denominator"], 2)


# ============================================================
# 3. Encoding — the first thing that goes wrong in the field
# ============================================================

print("\n--- encoding ---")

gbk = write_bytes("gbk.csv", FULL_HEADER, [full_row()], "gbk")
gbk_loaded = load_chinese_records(gbk)
check("a GBK file out of a Chinese Excel loads", gbk_loaded["denominator"], 1)
check("...and says it fell back to gb18030 rather than reporting UTF-8",
      gbk_loaded["encoding"], "gb18030")
check("...with the Chinese text intact rather than mojibake",
      gbk_loaded["rows"][0]["journal"], "中华消化外科杂志")
check("...and the byline readable, which is what the whole name join rests on",
      gbk_loaded["rows"][0]["authors"], ["朱广伟", "张三", "李四"])

check_true("the GB18030 fixture below really needs GB18030, not GBK",
           isinstance(raises(lambda: "\U00020000".encode("gbk")), UnicodeEncodeError))
wide = write_bytes("gb18030.csv", FULL_HEADER,
                   [full_row(title="\U00020000 四字节字的一条")], "gb18030")
check("a GB18030 file holding characters GBK cannot encode still loads",
      load_chinese_records(wide)["denominator"], 1)

bom = write_csv("bom.csv", FULL_HEADER, [full_row()], encoding="utf-8-sig")
bom_loaded = load_chinese_records(bom)
check("a UTF-8 file with a BOM loads", bom_loaded["denominator"], 1)
check("...and reports utf-8-sig, the codec that actually read it",
      bom_loaded["encoding"], "utf-8-sig")
check("the BOM does not end up glued to the first header",
      "title" in bom_loaded["columns_used"], True)

plain = write_csv("plain.csv", FULL_HEADER, [full_row()], encoding="utf-8")
check("a UTF-8 file with no BOM loads too", load_chinese_records(plain)["denominator"], 1)

# gb18030 decodes nearly any byte sequence without complaining, so trying it
# first would silently mojibake every UTF-8 file. The order in ENCODINGS is the
# whole safeguard, so it is asserted rather than trusted.
check("UTF-8 is tried before gb18030, or every UTF-8 file would load as mojibake",
      ENCODINGS.index("utf-8-sig") < ENCODINGS.index("gb18030"), True)

utf16 = write_bytes("utf16.csv", FULL_HEADER, [full_row()], "utf-16")
_error = raises(load_chinese_records, utf16)
check_true("a file that is neither UTF-8 nor GB18030 raises this module's own error",
           isinstance(_error, ChineseRecordError))
check_false("...and not a bare UnicodeDecodeError from the middle of the loader",
            isinstance(_error, UnicodeDecodeError))
for _enc in ENCODINGS:
    check_true(f"...the error names {_enc} as an encoding it tried", _enc in str(_error))
check_true("...names the file it failed on", "utf16.csv" in str(_error))
check_true("...and tells the user what to do about it",
           "UTF-8" in str(_error) and "另存为" in str(_error))


# ============================================================
# 4. Records — the same shape a harvested paper has
# ============================================================

print("\n--- records isomorphic to papers_*.json ---")

built = to_paper_records(loaded, PI_LATIN)
papers = built["papers"]

_xml = ET.fromstring(
    "<PubmedArticle><MedlineCitation><PMID>1</PMID><Article>"
    "<Journal><Title>J</Title></Journal><ArticleTitle>T</ArticleTitle>"
    "<AuthorList><Author><LastName>Zhu</LastName><ForeName>Guangwei</ForeName>"
    "</Author></AuthorList></Article></MedlineCitation></PubmedArticle>")
_reference = pubmed_api.parse_article(_xml)

check("every key a PubMed record carries is present on a Chinese one",
      sorted(set(_reference) - set(papers[0])), [])
check("...including every key on a byline entry",
      sorted(set(_reference["authors"][0]) - set(papers[0]["authors"][0])), [])
check("the identifiers PubMed would carry are present and empty, not absent",
      (papers[0]["pmid"], papers[0]["pmc_id"], papers[0]["doi"]), ("", "", ""))
check("pub_year is a string, the way every other source writes it",
      isinstance(papers[0]["pub_year"], str), True)
check("...and pub_date degrades to the year, which is all the table carries",
      (papers[0]["pub_date"], papers[0]["pub_year"]), ("2023", "2023"))
check("the journal comes through as written", papers[0]["journal"], "中华消化外科杂志")
check("the ISSN comes through when the export carried one",
      papers[0]["issn"], "1673-9752")
check("authors_str is the romanised byline, which is what the roster joins on",
      papers[0]["authors_str"], "Zhu Guangwei, Zhang San, Li Si")
check("the Chinese byline is kept beside it rather than replaced",
      [a["name_zh"] for a in papers[0]["authors"]], ["朱广伟", "张三", "李四"])
check("the surname is taken as the first pinyin token, the CNKI convention",
      (papers[0]["authors"][0]["last"], papers[0]["authors"][0]["fore"]),
      ("Zhu", "Guangwei"))
check("the 第一作者单位 column lands on the first byline entry, not on all of them",
      [a["affiliation"] for a in papers[0]["authors"]],
      ["海源学院附属医院胃肠外科", "", ""])
check("the result is JSON-serialisable", isinstance(json.dumps(built, default=str), str), True)

# A source marker and a retrieval date on every single record.
check("every record says it came from a Chinese bibliographic table",
      {p["source"] for p in papers}, {SOURCE_CHINESE})
check("...and confirmed_by starts as that one source",
      {tuple(p["confirmed_by"]) for p in papers}, {(SOURCE_CHINESE,)})
check("every record names the library it was exported from",
      [p["source_db"] for p in papers], ["知网", "万方", "维普"])
check("every record carries the day that library was read",
      {p["retrieved_on"] for p in papers}, {"2026-09-01"})
check("no record is missing either provenance field",
      [p["title"] for p in papers if not (p["source_db"] and p["retrieved_on"])], [])
check("the language is stated rather than inferred downstream",
      {p["language"] for p in papers}, {"zh"})
check("the 核心 status is carried, since it is why this corpus matters at all",
      {p["index_status"] for p in papers}, {"北大核心"})
check("the line of the file each record came from is kept",
      [p["source_line"] for p in papers], [2, 3, 4])

check("a bare list of rows is accepted as well as a loaded table",
      len(to_paper_records(loaded["rows"], PI_LATIN)["papers"]), 3)
check("an empty table produces no records and no error",
      to_paper_records([], PI_LATIN)["counts"]["papers_out"], 0)


# ============================================================
# 5. The name join — reported, never resolved by guessing
# ============================================================

print("\n--- the name join ---")

check("nothing is dropped: one record out per row in",
      (built["counts"]["rows_in"], built["counts"]["papers_out"]), (3, 3))
check("the PI was placed in all three bylines", built["counts"]["pi_matched"], 3)
check("...so nothing needed reporting", built["unmatched"], [])
check("the PI name the join ran against comes back with the result",
      built["pi_name"], PI_LATIN)
check("the first-author row is stamped first author",
      papers[0]["role"].split(" [")[0], "第一作者")
check("a PI last in the byline and starred is last and corresponding author",
      papers[1]["role"].split(" [")[0], "末位作者 / 通讯作者")
check("...and the star is stripped off the name rather than kept in it",
      papers[1]["authors"][1]["name"], "Zhu Guangwei")
check("...and recorded as the corresponding flag the report actually reads",
      [a["is_corresponding"] for a in papers[1]["authors"]], [False, True])
check("a PI in the middle of a byline holds no lead slot, so the role stays empty",
      papers[2]["role"], "")
check("...and that is a match, not a failure — the record is not reported",
      [r["line"] for r in built["unmatched"]], [])
check("every role carries the marker saying the match was a hand-typed pinyin cell",
      [ROLE_MARKER in p["role"] for p in papers if p["role"]], [True, True])
check("the marker claims none of the four verified-identity tiers PubMed stamps",
      pubmed_api.evidence_tier_from_role(papers[0]["role"]), "")

# The Han name works too: the rotation tolerance lives in `theses._compare_names`
# and is reused rather than re-derived, so a graduate matched in Section 18 and
# an author matched here cannot disagree about one name.
check("a PI named in Chinese characters joins the 作者 column",
      to_paper_records(loaded, PI_HAN)["counts"]["pi_matched"], 3)
check("...and a rotated romanisation joins the 作者拼音 column",
      to_paper_records(loaded, "Guangwei Zhu")["counts"]["pi_matched"], 3)
check("...as does the given name written as two tokens",
      to_paper_records(loaded, "Guang Wei Zhu")["counts"]["pi_matched"], 3)
check("a PI nobody in the file is joins nothing, rather than joining the first row",
      to_paper_records(loaded, "Li Wenhao")["counts"]["pi_matched"], 0)

unmatchable = write_csv("unmatchable.csv", FULL_HEADER, [
    full_row(title="别人的一篇", authors="王小明;李大力",
             pinyin="Wang Xiaoming;Li Dali"),
    full_row(title="拼音空着的一篇", authors="朱广伟;张三", pinyin=""),
    full_row(title="同名两个人的一篇", authors="朱广伟;朱广伟",
             pinyin="Zhu Guangwei;Zhu Guangwei"),
    full_row(title="只有姓的一篇", authors="朱;张三", pinyin="Zhu;Zhang San"),
])
odd = to_paper_records(load_chinese_records(unmatchable), PI_LATIN)
_unmatched = {row["line"]: row for row in odd["unmatched"]}

check("a row the PI cannot be placed in still becomes a record",
      odd["counts"]["papers_out"], 4)
check("...and is reported rather than deleted", len(odd["unmatched"]), 4)
check("the two counts are one population split, not two measurements",
      odd["counts"]["pi_matched"] + odd["counts"]["pi_unmatched"],
      odd["counts"]["rows_in"])
check("each reported row names why the join failed",
      sorted(r["reason"] for r in odd["unmatched"]),
      ["ambiguous", "no_author_matched", "partial_name", "undecidable_script"])
check("...and carries the line of the file it came from",
      sorted(_unmatched), [2, 3, 4, 5])
check("...and the title, so a human can go and look at the row",
      _unmatched[2]["title"], "别人的一篇")
check("...and the byline it failed against, in both scripts",
      (_unmatched[2]["authors"], _unmatched[2]["authors_pinyin"]),
      (["王小明", "李大力"], ["Wang Xiaoming", "Li Dali"]))
check("a blank pinyin cell against a romanised PI is undecidable, not unmatched",
      _unmatched[3]["reason"], "undecidable_script")
check("...and the note names the one column that would decide it",
      "拼音" in _unmatched[3]["note"], True)
check("one surname against a full name is never counted as the same person",
      _unmatched[5]["reason"], "partial_name")
check("...and the candidate it declined to accept is listed",
      _unmatched[5]["candidates"], ["Zhu"])
check("two identical names in one byline is ambiguous, not the first one winning",
      _unmatched[4]["reason"], "ambiguous")
check("...with both positions listed", len(_unmatched[4]["candidates"]), 2)
check("every unmatched record has an empty role rather than a guessed one",
      {p["role"] for p in odd["papers"]}, {""})
check("the reasons are counted, so a report can print how bad the join was",
      odd["match_reasons"],
      {"ambiguous": 1, "no_author_matched": 1, "partial_name": 1,
       "undecidable_script": 1})
check("the rule for each outcome comes back with the result",
      [name for name, _, _ in MATCH_RULES],
      ["exact", "name_form", "partial_name", "ambiguous", "no_author_matched",
       "undecidable_script"])
check("...and only two of the six are counted as a match",
      sorted({treatment for _, treatment, _ in MATCH_RULES}),
      ["counted as a match", "reported, record kept, role left empty"])
check("the rules travel with every join result", built["match_rules"], MATCH_RULES)

# A file whose pinyin column is entirely blank is the likeliest real failure, and
# it has to read as a broken join rather than as a PI with no Chinese papers.
blank_pinyin = write_csv("blank_pinyin.csv", FULL_HEADER,
                         [full_row(title=f"第{i}篇", pinyin="") for i in range(1, 6)])
blank = to_paper_records(load_chinese_records(blank_pinyin), PI_LATIN)
check("a file with no romanisation at all joins nothing", blank["counts"]["pi_matched"], 0)
check("...and keeps all five records anyway", len(blank["papers"]), 5)
check("...and reports every one of them", len(blank["unmatched"]), 5)
check("...under the reason that names the fix",
      {r["reason"] for r in blank["unmatched"]}, {"undecidable_script"})
check("...never as five papers the PI is simply not on",
      blank["match_reasons"].get("no_author_matched", 0), 0)


# ============================================================
# 6. The merge — the denominators stay separate
# ============================================================

print("\n--- merge ---")


def pubmed_paper(pmid: str, title: str, year: str) -> dict:
    """A harvested record, in the shape `pubmed_api.parse_article` returns."""
    return {
        "pmid": pmid, "title": title, "pub_year": year, "pub_date": year,
        "journal": "Gut", "doi": "", "authors_str": "Zhu Guangwei",
        "authors": [{"name": "Zhu Guangwei", "last": "Zhu", "fore": "Guangwei"}],
        "abstract": "", "issn": "", "issn_type": "", "issn_linking": "",
        "journal_abbrev": "", "volume": "", "issue": "", "pages": "", "pmc_id": "",
    }


PUBMED = [
    pubmed_paper("101", "Laparoscopic gastrectomy outcomes", "2023"),
    pubmed_paper("102", "Helicobacter pylori eradication timing", "2022"),
]
merged = merge_chinese_records(PUBMED, papers)

check("the PubMed denominator is reported on its own",
      merged["counts"]["pubmed_total"], 2)
check("the Chinese denominator is reported on its own",
      merged["counts"]["chinese_total"], 3)
check("the merged denominator is reported on its own",
      merged["counts"]["merged_total"], 5)
check("suspected duplicates get a number of their own as well",
      merged["counts"]["suspected_duplicates"], 0)
check("the three denominators are three keys, never collapsed into one total",
      sorted(k for k in merged["counts"] if k.endswith("_total")),
      ["chinese_total", "merged_total", "pubmed_total"])
check("neither source denominator is altered by the merge",
      (merged["counts"]["pubmed_total"], merged["counts"]["chinese_total"]),
      (len(PUBMED), len(papers)))
check("an English title and a Chinese one are not the same paper",
      merged["counts"]["both"], 0)
check("...so the split is all of both sources",
      (merged["counts"]["pubmed_only"], merged["counts"]["chinese_only"]), (2, 3))
check("the basis for every one of those numbers comes back with them",
      merged["dedup_basis"], DEDUP_KEY_BASIS)

check("the PubMed corpus keeps its order",
      [p.get("pmid") for p in merged["papers"][:2]], ["101", "102"])
check("...and the Chinese records follow it, so a diff shows additions and no movement",
      [p["source"] for p in merged["papers"]],
      [SOURCE_PUBMED] * 2 + [SOURCE_CHINESE] * 3)
check("a PubMed record arriving with no source field is stamped with one",
      merged["papers"][0]["source"], SOURCE_PUBMED)
check("...and its confirmed_by says only PubMed holds it",
      merged["papers"][0]["confirmed_by"], [SOURCE_PUBMED])
check("the input lists are not mutated", "source" in PUBMED[0], False)
check("the merge result is JSON-serialisable",
      isinstance(json.dumps(merged, default=str), str), True)

# A cross-source hit. Rare, because the two sources index the same paper under
# titles in two different languages — which is exactly why it is *suspected*, and
# why it is listed row by row rather than only counted.
SHARED_TITLE = "腹腔镜胃癌根治术后并发症的危险因素分析"
overlap = merge_chinese_records(
    PUBMED + [pubmed_paper("103", SHARED_TITLE, "2023")], papers)
check("a title+year hit across the two sources is counted as suspected, not certain",
      overlap["counts"]["suspected_duplicates"], 1)
check("...and folded, so the merged denominator does not count it twice",
      overlap["counts"]["merged_total"], 5)
check("...leaving both source denominators untouched",
      (overlap["counts"]["pubmed_total"], overlap["counts"]["chinese_total"]), (3, 3))
check("...and recorded as one record both sources hold", overlap["counts"]["both"], 1)
check("every suspected duplicate is listed, never only counted",
      len(overlap["suspected_duplicate_pairs"]), 1)
check("...with the title a human has to eyeball",
      overlap["suspected_duplicate_pairs"][0]["title"], SHARED_TITLE)
check("...the year that made up the other half of the key",
      overlap["suspected_duplicate_pairs"][0]["pub_year"], "2023")
check("...the PubMed record it was folded into",
      overlap["suspected_duplicate_pairs"][0]["pubmed_pmid"], "103")
check("...and the line of the Chinese file it came from",
      overlap["suspected_duplicate_pairs"][0]["chinese_line"], 2)
_survivor = [p for p in overlap["papers"] if p.get("pmid") == "103"][0]
check("the surviving record is the PubMed one, which carries affiliations",
      _survivor["source"], SOURCE_PUBMED)
check("...and it now names both sources that hold it",
      _survivor["confirmed_by"], sorted([SOURCE_CHINESE, SOURCE_PUBMED]))
check("...and the library the Chinese copy came from, so provenance is not lost",
      _survivor["source_dbs"], ["知网"])

# Two libraries holding one paper is the common case, and it is the *table's own*
# duplicate — never cross-source confirmation. Separate bucket, the same
# distinction `openalex.merge_corpora` draws between the two.
two_libraries = write_csv("two_libraries.csv", FULL_HEADER, [
    full_row(source_db="知网"),
    full_row(source_db="万方"),
    full_row(title="只有知网有的一篇", source_db="知网"),
])
two_lib_papers = to_paper_records(load_chinese_records(two_libraries), PI_LATIN)["papers"]
internal = merge_chinese_records(PUBMED, two_lib_papers)
check("知网 and 万方 holding one paper collapses to one record",
      internal["counts"]["merged_total"], 4)
check("...counted as the table's own duplicate, not as a source agreeing with PubMed",
      internal["counts"]["chinese_internal_duplicates"], 1)
check("...and never as a suspected cross-source duplicate",
      internal["counts"]["suspected_duplicates"], 0)
check("...and the pair is listed, so the collapse can be checked",
      internal["chinese_internal_duplicate_pairs"][0]["title"], SHARED_TITLE)
check("...with both libraries named",
      sorted(internal["chinese_internal_duplicate_pairs"][0]["source_dbs"]),
      ["万方", "知网"])
check("the surviving record records that two libraries hold it",
      sorted(tuple(sorted(p["source_dbs"])) for p in internal["papers"]
             if p["source"] == SOURCE_CHINESE),
      [("万方", "知网"), ("知网",)])
check("...while its own 数据来源 cell still says which row it was",
      sorted(p["source_db"] for p in internal["papers"] if p["source"] == SOURCE_CHINESE),
      ["知网", "知网"])
check("the two kinds of duplicate are two numbers and are never added together",
      sorted(k for k in internal["counts"] if "duplicat" in k),
      ["chinese_internal_duplicates", "suspected_duplicates"])

check("merging into an empty PubMed corpus still reports all three denominators",
      merge_chinese_records([], papers)["counts"]["pubmed_total"], 0)
check("...and an empty Chinese table is a valid merge, not an error",
      merge_chinese_records(PUBMED, [])["counts"],
      {"pubmed_total": 2, "chinese_total": 0, "merged_total": 2,
       "pubmed_only": 2, "chinese_only": 0, "both": 0,
       "suspected_duplicates": 0, "chinese_internal_duplicates": 0})
check("a record with no title at all is added rather than folded onto every other",
      merge_chinese_records(
          [pubmed_paper("201", "", "2023"), pubmed_paper("202", "", "2023")],
          [])["counts"]["merged_total"], 2)


# ============================================================
# 7. The key, and the tiers it does not have
# ============================================================

print("\n--- the key, and the two tiers it does not have ---")

check_true("the module states the key it deduplicates on",
           "title" in DEDUP_KEY_BASIS and "year" in DEDUP_KEY_BASIS)
_doc = chinese_records.merge_chinese_records.__doc__ or ""
check_true("merge_chinese_records' docstring names the key",
           "title" in _doc and "year" in _doc)
check_true("...says the records carry neither a DOI nor a PMID",
           "DOI" in _doc and "PMID" in _doc)
check_true("...names openalex.py as the thing it is weaker than", "openalex" in _doc)
check_true("...and says outright that this is one tier where that one has three",
           "three" in _doc and "one tier" in _doc)
check_true("...states the false negative: one paper indexed in two languages",
           "language" in _doc.lower())
for _direction in ("false positive", "false negative"):
    check_true(f"...and names the {_direction} direction by name",
               _direction in _doc.lower())

# The trap this module exists around: `openalex._title_year_key` strips every
# character that is not [a-z0-9], which reduces any Chinese title to the empty
# string. Reusing it would have made every Chinese record keyless, and a keyless
# record deduplicates against nothing.
check("openalex's title key is empty for a Chinese title",
      openalex._title_year_key(SHARED_TITLE, 2023), "")
check("...and this module's is not, which is the reason it has its own",
      bool(chinese_records._title_year_key(SHARED_TITLE, "2023")), True)
check("spacing and full-width punctuation do not make two keys out of one paper",
      chinese_records._title_year_key(SHARED_TITLE, "2023"),
      chinese_records._title_year_key("腹腔镜胃癌根治术后并发症的 危险因素分析！", "2023"))
check("full-width Latin and digits fold to their ASCII forms",
      chinese_records._title_year_key("ＰＤ-1 抑制剂的应用", "2023"),
      chinese_records._title_year_key("PD1抑制剂的应用", "2023"))
check("a different year is a different paper, even under one title",
      chinese_records._title_year_key(SHARED_TITLE, "2023")
      == chinese_records._title_year_key(SHARED_TITLE, "2024"), False)
check("an English title still keys, so a PubMed record can be looked up by it",
      bool(chinese_records._title_year_key("Laparoscopic gastrectomy outcomes", "2023")),
      True)
check("a record with no title has no key rather than an empty one they all share",
      chinese_records._title_year_key("", "2023"), "")
check("a title of pure punctuation has no key either",
      chinese_records._title_year_key("——《》！", "2023"), "")

check_true("the limits register says the missing identifiers are the whole story",
           any("DOI" in text for _t, text in CHINESE_RECORD_LIMITS))
check_true("...and that 中文核心 is invisible to PubMed, which is why this exists",
           any("PubMed" in text for _t, text in CHINESE_RECORD_LIMITS))
check_true("...and that a traditional-character title will not match a simplified one",
           any("繁" in text for _t, text in CHINESE_RECORD_LIMITS))
check_true("...and that the export covers only the databases the user searched",
           any("知网" in text or "万方" in text for _t, text in CHINESE_RECORD_LIMITS))
check("every limit has both a heading and a body",
      [t for t, text in CHINESE_RECORD_LIMITS if not (t.strip() and text.strip())], [])
check("the caveat register is coded the way the other tables' are",
      sorted(CHINESE_RECORD_CAVEATS)[0], "CNR-01")
check("every caveat has text behind its code",
      [code for code, text in CHINESE_RECORD_CAVEATS.items() if not text.strip()], [])
check_true("one caveat says outright that nothing here is written back to the corpus",
           any("papers_" in text for text in CHINESE_RECORD_CAVEATS.values()))


# ============================================================
# 8. The lines this module does not cross
# ============================================================

print("\n--- no crawler, no writes, no third party ---")

_source = open(chinese_records.__file__, encoding="utf-8").read()

for _banned in ("import requests", "urlopen", "urllib.request", "RobustHTTPClient",
                "BeautifulSoup", "selenium"):
    check(f"the module contains no `{_banned}`", _banned in _source, False)
# Importing either of these would drag the HTTP layer into a module the offline
# profile report reaches, which is the rule `openalex.py` states about itself.
for _banned in ("from .http_client", "from .openalex", "from .pubmed_api"):
    check(f"the module does not import via `{_banned}`", _banned in _source, False)
for _banned in ("pandas", "numpy", "openpyxl", "fitz", "matplotlib"):
    check(f"the module does not import {_banned}", f"import {_banned}" in _source, False)
check("CSV is read with the standard library's csv module", "import csv" in _source, True)

# A hand-typed value must never end up inside a harvested corpus file looking
# like something PubMed returned. The module writes nothing at all, which is the
# only version of that rule with no edge cases left in it.
for _banned in ("json.dump", ".write(", "open(", "write_text", "write_bytes",
                "mkdir", "shutil"):
    check(f"the module never calls `{_banned}`", _banned in _source, False)
check("...and exposes no writer, fetcher or exporter in its public surface",
      [n for n in chinese_records.__all__
       if any(w in n.lower() for w in ("write", "save", "dump", "export", "fetch",
                                       "download", "scrape"))], [])
check("nothing here ranks or scores anything",
      [n for n in dir(chinese_records)
       if not n.startswith("_")
       and any(w in n.lower() for w in ("rank", "score", "percentile", "grade",
                                        "top_", "best_", "productivity"))], [])
check("the records come back in file order — never sorted by anything about them",
      [p["source_line"] for p in papers], sorted(p["source_line"] for p in papers))

TMP.cleanup()

print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(1 if _failed else 0)
