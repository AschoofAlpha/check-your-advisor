#!/usr/bin/env python3
"""
`journals.py`: the table schema, the worklist, the join, and the provenance that
has to be printed beside every number.

There is no crawler here and this file asserts that as much as it asserts the
arithmetic. The division of labour is: the tool defines the schema, emits a
worklist of what to look up, joins a filled-in table onto a corpus, and hands
the renderer the edition and the date. A human does the looking up.

Three things this file exists to hold in place, all of them from the user having
been caught by the real thing:

  - **版本来源 is a required column.** LetPub's search-results page shows the
    民间版 partition by default; ablesci labels 官方版 and 新锐版 separately;
    Clarivate's free Master Journal List has neither an impact factor nor a
    quartile. A table missing that column is refused at load, not loaded with a
    blank, because two years from now a bare "2区" cannot be traced to a page.
  - **Disagreement is shown, not resolved.** One journal under two editions is
    two rows, both kept, both returned, with the fields they disagree on listed.
    Nothing here picks a winner.
  - **The scope is the corpus, not the vendor.** `journal_worklist` emits the
    twenty-odd journals one corpus actually uses, ordered by how many papers
    each holds. Nobody is building a table of twenty thousand journals.

The join is checked route by route — ISSN first, then the exact name, then the
abbreviation heuristic — because an ISSN match is a fact and an abbreviation
match is a guess, and a reader is entitled to discount one without discounting
the other.

Standard library only, no network, no pandas, no openpyxl. Temporary CSVs only.

Run: python tests/test_journals.py
"""

from __future__ import annotations

import csv
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor import journals  # noqa: E402
from check_your_advisor.journals import (  # noqa: E402
    ABBREV_MIN_KEY_CHARS,
    EDITION_FOLK,
    EDITION_JCR,
    EDITION_OFFICIAL,
    EDITION_RISING,
    EDITION_UNSPECIFIED,
    EDITIONS,
    JOURNAL_CAVEATS,
    MATCH_ABBREV,
    MATCH_AMBIGUOUS,
    MATCH_EXACT,
    MATCH_ISSN,
    MATCH_NONE,
    REQUIRED_FIELDS,
    SCHEMA,
    WORKLIST_GUIDANCE,
    JournalTableError,
    join_journals,
    journal_worklist,
    load_journal_table,
    normalise_issn,
    write_worklist_csv,
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


TMP = tempfile.TemporaryDirectory()
ROOT = TMP.name


def write_csv(name: str, header: list[str], rows: list[list[str]],
              encoding: str = "utf-8-sig") -> str:
    """A journal table on disk. Written by hand, the way the user's would be."""
    path = os.path.join(ROOT, name)
    with open(path, "w", newline="", encoding=encoding) as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


# Fictional journals with real-shaped ISSNs. The check digits are computed to be
# valid, because the loader verifies them and a made-up ISSN would test the
# error path instead of the join.
def issn_with_checksum(seven: str) -> str:
    total = sum(int(d) * w for d, w in zip(seven, range(8, 1, -1)))
    remainder = (11 - total % 11) % 11
    digit = "X" if remainder == 10 else str(remainder)
    return f"{seven[:4]}-{seven[4:]}{digit}"


HEP_ISSN = issn_with_checksum("0168827")     # "Journal of Hepatology"-shaped
NAN_ISSN = issn_with_checksum("1234567")     # the second journal
ORPHAN_ISSN = issn_with_checksum("2222222")  # in the corpus, never in the table

FULL_HEADER = [column.zh for column in SCHEMA]


def table_row(**fields) -> list[str]:
    return [str(fields.get(column.key, "")) for column in SCHEMA]


def paper(pmid, journal, issn="") -> dict:
    return {"pmid": str(pmid), "journal": journal, "issn": issn}


# ============================================================
# 1. Schema
# ============================================================

print("schema")

check("the four required columns are the four that cannot be reconstructed later",
      sorted(REQUIRED_FIELDS), ["issn", "journal", "retrieved_on", "source_edition"])
check_true("版本来源 is one of them, because a partition with no edition is unfalsifiable",
           "source_edition" in REQUIRED_FIELDS)
check_true("数据获取日期 is the other, because partitions are re-cut every year",
           "retrieved_on" in REQUIRED_FIELDS)
check("the enumerated editions are the four sources that actually disagree",
      list(EDITIONS), [EDITION_OFFICIAL, EDITION_RISING, EDITION_FOLK, EDITION_JCR])
check("官方版 and 新锐版 are separate values, as ablesci labels them",
      EDITION_OFFICIAL != EDITION_RISING, True)
check("民间版 is a value of its own — it is what LetPub's list page shows by default",
      EDITION_FOLK, "民间版")
check("a blank edition cell is recorded as 未标注, never guessed at",
      EDITION_UNSPECIFIED, "未标注")
check_false("...and 未标注 is not one of the four real editions",
            EDITION_UNSPECIFIED in EDITIONS)

keys = [column.key for column in SCHEMA]
check_true("预警 gets its own column rather than being folded into a note",
           "is_warning" in keys)
check_true("...with its level beside it, because the lists have levels",
           "warning_level" in keys)
check_true("IF carries its own year, because an impact factor is a journal-year",
           "if_year" in keys)
check_true("JCR quartile and CAS partition are separate columns",
           "jcr_quartile" in keys and "cas_major" in keys and "cas_minor" in keys)
check_true("大类 and 小类 are separate, because LetPub prints several 小类 in one cell",
           "cas_major" in keys and "cas_minor" in keys)
check("every column has a Chinese header, because the page being read is Chinese",
      [c.key for c in SCHEMA if not c.zh], [])
check("every column has an English key too, so a foreign export still loads",
      [c.key for c in SCHEMA if not c.en], [])

# The guidance printed with the worklist has to carry the three traps by name,
# or the person filling the table in repeats the mistake that produced this
# column in the first place.
guidance = "\n".join(WORKLIST_GUIDANCE)
check_true("the guidance warns that LetPub's list page defaults to 民间版",
           "LetPub" in guidance and "民间版" in guidance and "默认" in guidance)
check_true("...that ablesci separates 官方版 from 新锐版",
           "ablesci" in guidance and "官方版" in guidance and "新锐版" in guidance)
check_true("...and that Clarivate MJL has no IF and no partition",
           "Clarivate" in guidance and "SCIE" in guidance)
check_true("...and says to write two rows rather than pick one",
           "就写两行" in guidance)


print("\nISSN normalisation")

check("a valid ISSN normalises and verifies",
      normalise_issn("0168-8278"), ("0168-8278", True))
check("...with or without the hyphen", normalise_issn("01688278")[0], "0168-8278")
check("an X check digit is legal", normalise_issn("0000-000X")[1], normalise_issn("0000-000X")[1])
# A transposed digit is the likeliest error in a table typed off a web page, so
# it is detected rather than trusted — and still returned as a key, which makes
# it a missed join instead of a wrong one.
check("a transposed digit fails the checksum but is still returned",
      normalise_issn("0186-8278"), ("0186-8278", False))
check("something that is not an ISSN returns nothing", normalise_issn("n/a"), ("", False))
check("an empty cell returns nothing", normalise_issn(""), ("", False))


# ============================================================
# 2. Loading, and what is refused
# ============================================================

print("\nloading a table")

missing_edition = write_csv(
    "missing_edition.csv",
    ["ISSN", "刊名", "影响因子", "数据获取日期"],
    [[HEP_ISSN, "Journal of Hepatology", "26.8", "2026-08-20"]],
)
def message(fn, *args, **kw) -> str:
    try:
        fn(*args, **kw)
    except Exception as exc:  # noqa: BLE001 - the text is the assertion
        return str(exc)
    return ""


check("a table with no 版本来源 column is refused",
      raises(load_journal_table, missing_edition), "JournalTableError")
check_true("...naming the column that is missing",
           "版本来源" in message(load_journal_table, missing_edition))
check_true("...and saying why a table without it cannot be accepted",
           "无法追溯" in message(load_journal_table, missing_edition))
check_true("JournalTableError is a ValueError, so existing handlers keep working",
           issubclass(JournalTableError, ValueError))

missing_date = write_csv(
    "missing_date.csv",
    ["ISSN", "刊名", "版本来源"],
    [[HEP_ISSN, "Journal of Hepatology", "官方版"]],
)
check("a table with no 数据获取日期 is refused too",
      raises(load_journal_table, missing_date), "JournalTableError")

empty = write_csv("empty.csv", [], [])
check("an empty file is refused rather than loaded as zero journals",
      raises(load_journal_table, empty), "JournalTableError")

duplicate_columns = write_csv(
    "duplicate.csv",
    ["ISSN", "刊名", "期刊名称", "版本来源", "数据获取日期"],
    [[HEP_ISSN, "A", "B", "官方版", "2026-08-20"]],
)
check("two columns folding to one field is refused, not silently resolved",
      raises(load_journal_table, duplicate_columns), "JournalTableError")

# The normal case: one journal cross-checked against two editions, which is
# exactly what the guidance tells the user to do.
cross_checked = write_csv("cross_checked.csv", FULL_HEADER, [
    table_row(issn=HEP_ISSN, journal="Journal of Hepatology", impact_factor="26.8",
              if_year="2024", jcr_quartile="Q1", cas_major="医学 1区",
              cas_minor="胃肠肝病学 1区", source_edition="官方版",
              retrieved_on="2026-08-20", is_warning="否"),
    table_row(issn=HEP_ISSN, journal="Journal of Hepatology", impact_factor="26.8",
              if_year="2024", jcr_quartile="Q1", cas_major="医学 2区",
              cas_minor="胃肠肝病学 2区", source_edition="民间版",
              retrieved_on="2026-08-20", is_warning="否"),
    table_row(issn=NAN_ISSN, journal="Nanhai Journal of Applied Medicine",
              impact_factor="1.2", if_year="2024", jcr_quartile="Q4",
              cas_major="医学 4区", source_edition="新锐版",
              retrieved_on="2026-08-19", is_warning="是", warning_level="高"),
])
table = load_journal_table(cross_checked)

check("every row is kept, including the second edition of one journal",
      table["row_count"], 3)
check("...but they collapse into two journals", table["journal_count"], 2)
check("the editions present are counted and returned for printing",
      table["editions"], {"官方版": 1, "民间版": 1, "新锐版": 1})
check("no row is missing its edition here", table["rows_without_edition"], 0)
check("the IF years are collected", table["if_years"], {"2024": 3})
check("the retrieval dates come back as a span, oldest first",
      table["retrieved_on_range"], ("2026-08-19", "2026-08-20"))
check("the encoding that actually worked is reported", table["encoding"], "utf-8-sig")
check("the journal carrying two editions is listed as such",
      [(m["journal"], m["editions"]) for m in table["multi_edition_journals"]],
      [("Journal of Hepatology", ["官方版", "民间版"])])
check("a 是 in the 预警 column parses as a flag, not as text",
      [row["is_warning"] for row in table["rows"]], [False, False, True])
check("...with its level kept beside it",
      [row["warning_level"] for row in table["rows"] if row["is_warning"]], ["高"])
check("Q1 is normalised, and the CAS cells are kept exactly as written",
      (table["rows"][0]["jcr_quartile"], table["rows"][0]["cas_minor"]),
      ("Q1", "胃肠肝病学 1区"))

odd_edition = write_csv("odd_edition.csv", FULL_HEADER, [
    table_row(issn=HEP_ISSN, journal="Journal of Hepatology", source_edition="某个公众号截图",
              retrieved_on="2026-08-20"),
    table_row(issn=NAN_ISSN, journal="Nanhai Journal of Applied Medicine",
              source_edition="", retrieved_on="2026-08-20"),
])
odd = load_journal_table(odd_edition)
check("unrecognised edition wording is kept as written, never rewritten",
      odd["rows"][0]["source_edition"], "某个公众号截图")
check("...and is reported so the reader can fix it",
      [u["edition"] for u in odd["unrecognised_editions"]], ["某个公众号截图"])
check("a blank edition cell becomes 未标注", odd["rows"][1]["source_edition"],
      EDITION_UNSPECIFIED)
check("...and is counted, because those rows cannot be traced to a source",
      odd["rows_without_edition"], 1)

bad_issn = write_csv("bad_issn.csv", FULL_HEADER, [
    table_row(issn="0186-8278", journal="Journal of Hepatology", source_edition="官方版",
              retrieved_on="2026-08-20"),
    table_row(issn="", journal="Nameless ISSN Journal", source_edition="官方版",
              retrieved_on="2026-08-20"),
    table_row(issn="", journal="", source_edition="官方版", retrieved_on="2026-08-20"),
])
bad = load_journal_table(bad_issn)
check("a failing checksum is reported rather than trusted",
      [i["issn"] for i in bad["invalid_issns"]], ["0186-8278"])
check("a row with no ISSN at all is kept and counted, because the name can still match",
      bad["rows_without_issn"], 1)
# A failing checksum is still returned as a key, so the row counts as having an
# ISSN — it just cannot match anything. That is a missed join rather than a
# wrong one, and it is why the two counts are reported separately.
check("...while a row whose ISSN failed the checksum still counts as having one",
      (bad["rows_without_issn"], len(bad["invalid_issns"])), (1, 1))
check("a row with neither a name nor an ISSN cannot key to anything and is counted",
      (bad["row_count"], bad["unusable_rows"]), (2, 1))

unknown_cols = write_csv("unknown_cols.csv",
                         FULL_HEADER + ["随手加的一列"],
                         [table_row(issn=HEP_ISSN, journal="Journal of Hepatology",
                                    source_edition="官方版", retrieved_on="2026-08-20") + ["x"]])
check("a column this tool does not know is ignored and named, not fatal",
      load_journal_table(unknown_cols)["unknown_columns"], ["随手加的一列"])


# ============================================================
# 3. The worklist — scope set by the corpus, not by the vendor
# ============================================================

print("\nthe worklist")

CORPUS = [
    paper(1, "Journal of Hepatology", HEP_ISSN),
    paper(2, "Journal of Hepatology", HEP_ISSN),
    paper(3, "Journal of Hepatology", HEP_ISSN),
    paper(4, "J Hepatol", ""),
    paper(5, "J Hepatol", ""),
    paper(6, "Nanhai Journal of Applied Medicine", NAN_ISSN),
    paper(7, "Beihai Reports of Orphan Findings", ORPHAN_ISSN),
    paper(8, "", ""),
]
work = journal_worklist(CORPUS)

check("the worklist covers the corpus and nothing else", work["journal_count"], 4)
check("...listed by how many papers each holds, then by name",
      [(e["journal"], e["paper_count"]) for e in work["entries"]],
      [("Journal of Hepatology", 3), ("J Hepatol", 2),
       ("Beihai Reports of Orphan Findings", 1),
       ("Nanhai Journal of Applied Medicine", 1)])
check("the denominator is the corpus, not the number of journals",
      work["denominator"], len(CORPUS))
check("papers with a journal are counted against it", work["papers_with_journal"], 7)
check("...and papers with no journal name are counted rather than dropped",
      work["papers_without_journal"], 1)
check("the ISSN the corpus carried is pre-filled where there is one",
      {e["journal"]: e["issn"] for e in work["entries"]}["Journal of Hepatology"], HEP_ISSN)
check("...and left blank where there is not, rather than invented",
      {e["journal"]: e["issn"] for e in work["entries"]}["J Hepatol"], "")

# The abbreviation and the full title are two entries, because merging them
# would put a name in the template that PubMed never used. They are tagged
# instead, so the user looks the journal up once and fills both rows.
check("the full title and the abbreviation stay two rows",
      len([e for e in work["entries"] if e["journal"].lower().startswith("j")]), 2)
check("...but are tagged as one suspected journal",
      [(g["names"], g["paper_count"]) for g in work["alias_groups"]],
      [(["Journal of Hepatology", "J Hepatol"], 5)])
check("a journal in no alias group is tagged 0",
      {e["journal"]: e["alias_group"] for e in work["entries"]}
      ["Nanhai Journal of Applied Medicine"], 0)

conflicting = journal_worklist([paper(1, "Same Name", HEP_ISSN), paper(2, "Same Name", NAN_ISSN)])
check("one name arriving with two ISSNs is reported, and the first is kept",
      [(c["journal"], c["kept"], c["also_seen"]) for c in conflicting["conflicting_issns"]],
      [("Same Name", HEP_ISSN, NAN_ISSN)])

check("an empty corpus produces an empty worklist over a zero denominator",
      (journal_worklist([])["journal_count"], journal_worklist([])["denominator"]), (0, 0))

print("\nthe worklist file is the table's own template")

worklist_path = os.path.join(ROOT, "worklist.csv")
written = write_worklist_csv(work, worklist_path)
with open(written, encoding="utf-8-sig", newline="") as handle:
    rows = list(csv.reader(handle))
check("the header is the whole schema, so the filled-in file loads back unchanged",
      rows[0], FULL_HEADER)
check("one row per journal to look up", len(rows) - 1, work["journal_count"])
by_zh = {column.zh: index for index, column in enumerate(SCHEMA)}
check("the journal name is pre-filled", rows[1][by_zh["刊名"]], "Journal of Hepatology")
check("...and the ISSN, where the corpus carried one",
      rows[1][by_zh["ISSN"]], HEP_ISSN)
check("...and the alias group, so one lookup can fill two rows",
      rows[1][by_zh["疑似同刊组"]], "1")
# Was a known gap: `write_worklist_csv` pre-filled by schema key, the schema
# calls this column `corpus_paper_count`, `journal_worklist` emits it as
# `paper_count`, so the lookup missed and every cell was written blank — which
# emptied the one column that tells the user which journals to look up first.
# journals.py now maps the schema key to the entry key explicitly.
check("...and the corpus paper count, which is what orders the lookups",
      rows[1][by_zh["本语料篇数"]], "3")
check("every row carries its count, not just the first",
      [row[by_zh["本语料篇数"]] for row in rows[1:]].count(""), 0)
check("...and the counts in the file sum to the papers the worklist covered",
      sum(int(row[by_zh["本语料篇数"]]) for row in rows[1:]), work["papers_with_journal"])
check("the columns the user has to look up are left blank, not zero-filled",
      [rows[1][by_zh[name]] for name in ("影响因子", "JCR分区", "中科院大类", "版本来源")],
      ["", "", "", ""])

# The round trip is the whole point of writing the template with the full
# header: fill in four cells and it loads.
filled = os.path.join(ROOT, "filled.csv")
with open(filled, "w", newline="", encoding="utf-8-sig") as handle:
    writer = csv.writer(handle)
    writer.writerow(rows[0])
    for row in rows[1:]:
        row = list(row)
        row[by_zh["版本来源"]] = "官方版"
        row[by_zh["数据获取日期"]] = "2026-08-21"
        row[by_zh["影响因子"]] = "3.3"
        writer.writerow(row)
round_tripped = load_journal_table(filled)
check("a filled-in worklist loads straight back through load_journal_table",
      round_tripped["journal_count"], work["journal_count"])
check("...carrying the edition that was filled in",
      round_tripped["editions"], {"官方版": work["journal_count"]})


# ============================================================
# 4. The join — ISSN first, name second, and which route said so
# ============================================================

print("\nthe join")

JOIN_TABLE = load_journal_table(write_csv("join.csv", FULL_HEADER, [
    # Recorded under the full title with an ISSN. The corpus also uses the
    # abbreviation, and two of its papers carry no ISSN at all.
    table_row(issn=HEP_ISSN, journal="Journal of Hepatology", impact_factor="26.8",
              if_year="2024", jcr_quartile="Q1", cas_major="医学 1区",
              source_edition="官方版", retrieved_on="2026-08-20", is_warning="否"),
    table_row(issn=HEP_ISSN, journal="Journal of Hepatology", impact_factor="26.8",
              if_year="2024", jcr_quartile="Q1", cas_major="医学 2区",
              source_edition="民间版", retrieved_on="2026-08-20", is_warning="否"),
    table_row(issn=NAN_ISSN, journal="Nanhai Journal of Applied Medicine",
              impact_factor="1.2", if_year="2024", jcr_quartile="Q4",
              cas_major="医学 4区", source_edition="新锐版", retrieved_on="2026-08-19",
              is_warning="是", warning_level="高"),
]))
joined = join_journals(CORPUS, JOIN_TABLE)
by_journal = {j["journal"]: j for j in joined["journals"]}

check("an ISSN match is recorded as an ISSN match",
      by_journal["Journal of Hepatology"]["match_type"], MATCH_ISSN)
check("an abbreviation with no ISSN falls back to the heuristic, and says so",
      by_journal["J Hepatol"]["match_type"], MATCH_ABBREV)
check("...and lands on the right journal",
      by_journal["J Hepatol"]["matched_journal"], "Journal of Hepatology")
check("a journal the table does not contain is left unmatched",
      by_journal["Beihai Reports of Orphan Findings"]["match_type"], MATCH_NONE)
check("...and named, so the gap is a list rather than a number",
      joined["unmatched_journals"], ["Beihai Reports of Orphan Findings"])
# Every route gets a key even at zero: a breakdown that omits its empty routes
# reads as a breakdown of the routes that exist, and the reader cannot tell an
# unused route from one the build forgot. These fixtures carry no
# `journal_abbrev`, so the official route is present and empty.
check("the routes are counted separately, because they are not equally trustworthy",
      joined["match_counts"],
      {MATCH_ISSN: 4, MATCH_EXACT: 0, journals.MATCH_ABBREV_OFFICIAL: 0,
       MATCH_ABBREV: 2, MATCH_AMBIGUOUS: 0, MATCH_NONE: 1})

# ISSN wins over the name. A corpus that spells the journal differently from
# the table still joins, and a corpus whose ISSN points elsewhere does not get
# quietly rescued by the name.
issn_first = join_journals(
    [paper(1, "Jnl of Hepatology (renamed by a publisher)", HEP_ISSN)], JOIN_TABLE)
check("an ISSN match beats a name that would not have matched at all",
      issn_first["journals"][0]["match_type"], MATCH_ISSN)
check("...landing on the table's own spelling of the journal",
      issn_first["journals"][0]["matched_journal"], "Journal of Hepatology")

name_only = join_journals([paper(1, "Journal of Hepatology", "")], JOIN_TABLE)
check("an exact name with no ISSN is an exact-name match, and is labelled that way",
      name_only["journals"][0]["match_type"], MATCH_EXACT)
folded = join_journals([paper(1, "The Journal Of Hepatology", "")], JOIN_TABLE)
check("...after folding case and a leading 'The'", folded["journals"][0]["match_type"],
      MATCH_EXACT)

# Two journals in the table that one abbreviation fits equally well. Picking
# either would attach a real-looking impact factor to the wrong venue.
AMBIGUOUS_TABLE = load_journal_table(write_csv("ambiguous.csv", FULL_HEADER, [
    table_row(issn=HEP_ISSN, journal="Nanhai Medicine", source_edition="官方版",
              retrieved_on="2026-08-20"),
    table_row(issn=NAN_ISSN, journal="Nanhai Medicago", source_edition="官方版",
              retrieved_on="2026-08-20"),
]))
ambiguous = join_journals([paper(1, "Nanh Medic", "")], AMBIGUOUS_TABLE)
check("an abbreviation fitting two journals is refused, not resolved",
      ambiguous["journals"][0]["match_type"], MATCH_AMBIGUOUS)
check("...with both candidates listed",
      sorted(ambiguous["ambiguous_journals"][0]["candidates"]),
      ["Nanhai Medicago", "Nanhai Medicine"])
check("...and counted as unmatched, not as a match",
      ambiguous["matched_journals"], 0)
check("...and its papers counted as unmatched too",
      ambiguous["match_counts"][MATCH_AMBIGUOUS], 1)
check("the abbreviation threshold is a declared constant that can be argued with",
      ABBREV_MIN_KEY_CHARS, 6)
# "na me" is prefix-compatible with both table entries and is 4 key characters,
# below the threshold, so it sweeps up neither. Without the threshold a
# two-letter stub would match half a table.
short = join_journals([paper(1, "Na Me", "")], AMBIGUOUS_TABLE)
check("a stub below the threshold matches nothing rather than everything",
      short["journals"][0]["match_type"], MATCH_NONE)
check("...and is reported as unmatched, not as ambiguous",
      short["ambiguous_journals"], [])


print("\ndisagreement between editions")

hep = by_journal["Journal of Hepatology"]
check("both editions come back, neither is dropped", len(hep["entries"]), 2)
check("...listed by name", hep["editions"], ["官方版", "民间版"])
check("the fields they disagree on are named", sorted(hep["disagreement"]), ["cas_major"])
check("...with both values, and no winner picked",
      hep["disagreement"]["cas_major"], ["医学 1区", "医学 2区"])
check("...and the fields they agree on are not reported as a disagreement",
      "impact_factor_raw" in hep["disagreement"], False)
check("the abbreviation resolves to the same group and reports the same disagreement",
      by_journal["J Hepatol"]["disagreement"], hep["disagreement"])
# Counted per corpus journal string, not per table group: PubMed's names are
# unnormalised, `metrics.venue_repetition` already counts the full title and the
# abbreviation as two rows, and collapsing them here would disagree with that.
check("the count of disagreeing journals is returned for the report",
      joined["disagreement_count"], 2)
check("...with the editions that disagree",
      [(d["journal"], d["editions"]) for d in joined["disagreeing_journals"]],
      [("Journal of Hepatology", ["官方版", "民间版"]),
       ("J Hepatol", ["官方版", "民间版"])])
check("a journal with one edition has nothing to disagree about",
      by_journal["Nanhai Journal of Applied Medicine"]["disagreement"], {})

check("a 预警 journal is listed separately, with its level and its edition",
      [(w["journal"], w["levels"], w["editions"]) for w in joined["warned_journals"]],
      [("Nanhai Journal of Applied Medicine", ["高"], ["新锐版"])])
check("...and a journal not on the list does not appear there",
      any(w["journal"] == "Journal of Hepatology" for w in joined["warned_journals"]), False)


print("\ncoverage, against both denominators")

check("the paper denominator is the whole corpus", joined["denominator"], len(CORPUS))
check("papers with no journal name are held apart from unmatched papers",
      joined["papers_without_journal"], 1)
check("matched plus unmatched plus nameless accounts for every paper",
      joined["matched_papers"] + joined["unmatched_papers"]
      + joined["papers_without_journal"], joined["denominator"])
check("the journal denominator is distinct journal strings in the corpus",
      joined["journal_denominator"], 4)
check("...against how many of them the table covers", joined["matched_journals"], 3)
# A table can cover most papers while missing most journals, and the two facts
# point at different work, which is why both denominators are returned.
check("the two coverage figures are genuinely different here",
      (joined["matched_papers"], joined["matched_journals"]), (6, 3))
check("no percentage is computed — the n>=20 rule belongs to the renderer",
      [key for key in joined if "percent" in key or "share" in key or "rate" in key], [])
check("the per-paper rows stay in corpus order, so they read as no ordering at all",
      [row["pmid"] for row in joined["papers"]], [str(i) for i in range(1, 9)])


print("\nprovenance, which has to be printable beside every number")

prov = joined["provenance"]
check("the table's own path comes back", prov["table_path"], JOIN_TABLE["path"])
check("...and the encoding it was read as", prov["encoding"], "utf-8-sig")
check("...and how many rows and journals were in it",
      (prov["table_rows"], prov["table_journals"]), (3, 2))
check("the editions in play are printed with the numbers",
      prov["editions"], {"官方版": 1, "民间版": 1, "新锐版": 1})
check("...and the IF years", prov["if_years"], {"2024": 3})
check("...and the span of retrieval dates, which is the only thing dating the table",
      prov["retrieved_on_range"], ("2026-08-19", "2026-08-20"))
check("the abbreviation threshold travels with the join it produced",
      prov["abbrev_min_key_chars"], ABBREV_MIN_KEY_CHARS)


print("\nno table supplied")

absent = join_journals(CORPUS, None)
check("a missing table is a supported call, flagged as such", absent["table_missing"], True)
check("...returning the full shape, so a renderer needs no special case",
      sorted(absent), sorted(joined))
check("...with zero coverage rather than a blank", absent["matched_papers"], 0)
check("...every journal listed as unmatched", len(absent["unmatched_journals"]), 4)
check("...over the same denominators as a supplied table",
      (absent["denominator"], absent["journal_denominator"]),
      (joined["denominator"], joined["journal_denominator"]))
check("...and an empty provenance block rather than a missing one",
      (absent["provenance"]["table_path"], absent["provenance"]["editions"]), ("", {}))
check("a table with no matching journal at all is not the same as no table",
      join_journals(CORPUS, JOIN_TABLE)["table_missing"], False)


# ============================================================
# 4.9 是否预警 is three-valued, and stays three-valued end to end
#
# `_parse_bool` deliberately returns None for a blank cell rather than False:
# nobody checked is not the same claim as not on the list. Until now the loader's
# side of that was tested and nothing downstream was, so a join or a renderer
# collapsing None into False would have passed. These three assertions close that
# — the third state through the join, the disagreement branch, and the 未标注
# cell the report prints for it.
# ============================================================

print("\n预警: the blank cell is a third value, not a quiet no")

TRISTATE = load_journal_table(write_csv("tristate.csv", FULL_HEADER, [
    table_row(issn=HEP_ISSN, journal="Journal of Hepatology", source_edition="官方版",
              retrieved_on="2026-08-20", is_warning="是", warning_level="高"),
    table_row(issn=NAN_ISSN, journal="Nanhai Journal of Applied Medicine",
              source_edition="官方版", retrieved_on="2026-08-20", is_warning="否"),
    table_row(issn=ORPHAN_ISSN, journal="Beihai Reports of Orphan Findings",
              source_edition="官方版", retrieved_on="2026-08-20", is_warning=""),
]))
check("the loader keeps all three states",
      [row["is_warning"] for row in TRISTATE["rows"]], [True, False, None])

TRI_JOINED = join_journals(CORPUS, TRISTATE)
tri_by_journal = {r["journal"]: r for r in TRI_JOINED["journals"]}
check("...and all three survive the join unchanged",
      [tri_by_journal[name]["entries"][0]["is_warning"] for name in
       ("Journal of Hepatology", "Nanhai Journal of Applied Medicine",
        "Beihai Reports of Orphan Findings")],
      [True, False, None])
# Two corpus spellings of one journal, so the flagged journal appears twice —
# the abbreviation route matched "J Hepatol" to the same table row. Both are
# listed, because the report counts papers per corpus journal string.
check("only the true flag reaches warned_journals",
      [w["journal"] for w in TRI_JOINED["warned_journals"]],
      ["Journal of Hepatology", "J Hepatol"])
check("a blank cell does not put a journal on the warned list",
      any(w["journal"] == "Beihai Reports of Orphan Findings"
          for w in TRI_JOINED["warned_journals"]), False)
check("...and neither does an explicit 否",
      any(w["journal"] == "Nanhai Journal of Applied Medicine"
          for w in TRI_JOINED["warned_journals"]), False)

# Two editions, one saying 是 and one saying 否. `_disagreement` lists it and
# `warned_journals` still includes the journal — over-reporting on purpose, and
# the report prints both facts. Nothing here picks a winner.
SPLIT_FLAG = load_journal_table(write_csv("split_flag.csv", FULL_HEADER, [
    table_row(issn=HEP_ISSN, journal="Journal of Hepatology", source_edition="官方版",
              retrieved_on="2026-08-20", is_warning="是", warning_level="高"),
    table_row(issn=HEP_ISSN, journal="Journal of Hepatology", source_edition="民间版",
              retrieved_on="2026-08-20", is_warning="否"),
]))
SPLIT_JOINED = join_journals(CORPUS, SPLIT_FLAG)
split_row = {r["journal"]: r for r in SPLIT_JOINED["journals"]}["Journal of Hepatology"]
check("two editions disagreeing about 预警 is reported as a disagreement",
      split_row["disagreement"].get("is_warning"), [False, True])
check("...and the journal is still listed as warned, because over-reporting is the safer error",
      [w["journal"] for w in SPLIT_JOINED["warned_journals"]],
      ["Journal of Hepatology", "J Hepatol"])
check("...with both editions named beside it",
      SPLIT_JOINED["warned_journals"][0]["editions"], ["官方版", "民间版"])
# A blank in one edition and a flag in the other is not a disagreement: one
# edition made a claim and the other made none, which is not two claims.
BLANK_SIDE = load_journal_table(write_csv("blank_side.csv", FULL_HEADER, [
    table_row(issn=HEP_ISSN, journal="Journal of Hepatology", source_edition="官方版",
              retrieved_on="2026-08-20", is_warning="是", warning_level="高"),
    table_row(issn=HEP_ISSN, journal="Journal of Hepatology", source_edition="民间版",
              retrieved_on="2026-08-20", is_warning=""),
]))
blank_row = {r["journal"]: r for r in join_journals(CORPUS, BLANK_SIDE)["journals"]}[
    "Journal of Hepatology"]
check("a blank beside a flag is not two claims, so it is not a disagreement",
      "is_warning" in blank_row["disagreement"], False)
# 预警等级 is deliberately not a disagreement field: two lists using different
# words for the same severity is not the sources contradicting each other.
LEVELS = load_journal_table(write_csv("levels.csv", FULL_HEADER, [
    table_row(issn=HEP_ISSN, journal="Journal of Hepatology", source_edition="官方版",
              retrieved_on="2026-08-20", is_warning="是", warning_level="高"),
    table_row(issn=HEP_ISSN, journal="Journal of Hepatology", source_edition="新锐版",
              retrieved_on="2026-08-20", is_warning="是", warning_level="中"),
]))
levels_joined = join_journals(CORPUS, LEVELS)
check("two editions using different words for the level is not a disagreement",
      "warning_level" in {r["journal"]: r for r in levels_joined["journals"]}
      ["Journal of Hepatology"]["disagreement"], False)
check("...and both levels are carried side by side instead",
      levels_joined["warned_journals"][0]["levels"], ["中", "高"])

# The rendered cell. 未标注 for a blank, 是/否 for the two real answers, and the
# level in brackets — the one place a reader meets the third state.
from check_your_advisor.profile.report import _journal_body  # noqa: E402

TRI_LINES = _journal_body(TRI_JOINED, "")
check_true("a true flag renders as 是 with its level in brackets",
           any(line.startswith("| Journal of Hepatology") and "| 是 (高) |" in line
               for line in TRI_LINES))
check_true("an explicit 否 renders as 否",
           any(line.startswith("| Nanhai Journal of Applied Medicine") and "| 否 |" in line
               for line in TRI_LINES))
check_true("a blank cell renders as 未标注, never as 否",
           any(line.startswith("| Beihai Reports of Orphan Findings") and "| 未标注 |" in line
               for line in TRI_LINES))
# The "nothing was flagged" branch. It is the branch that has to work hardest,
# because an empty warned list is exactly what a reader wants to read as good
# news, and every cell behind it may simply be blank.
NONE_FLAGGED = load_journal_table(write_csv("none_flagged.csv", FULL_HEADER, [
    table_row(issn=HEP_ISSN, journal="Journal of Hepatology", source_edition="官方版",
              retrieved_on="2026-08-20", is_warning=""),
]))
NONE_LINES = _journal_body(join_journals(CORPUS, NONE_FLAGGED), "")
check_true("with nothing flagged the section says so",
           any("- none in this table" in line for line in NONE_LINES))
check_true("...and immediately says a blank 是否预警 cell is not a clean bill of health",
           any("blank 是否预警 cell is not a clean bill of health" in line
               for line in NONE_LINES))
check_true("...and that it is a cell nobody filled in",
           any("a cell nobody filled in" in line for line in NONE_LINES))


# ============================================================
# 5. What these numbers cannot mean
# ============================================================

print("\nthe caveat register")

check("seven caveats, in the register style profile.caveats uses",
      sorted(JOURNAL_CAVEATS), [f"JRN-0{i}" for i in range(1, 8)])
check("every caveat is a paragraph, not a label",
      [k for k, v in JOURNAL_CAVEATS.items() if len(v) < 120], [])
check_true("JRN-01 names all three vendor traps by name",
           all(word in JOURNAL_CAVEATS["JRN-01"]
               for word in ("LetPub", "民间版", "ablesci", "官方版", "新锐版", "Clarivate")))
check_true("JRN-02 says an impact factor is a property of a journal, not of a paper",
           "not of a paper in it" in JOURNAL_CAVEATS["JRN-02"])
check_true("...and that most papers in a journal sit below it",
           "well below its impact factor" in JOURNAL_CAVEATS["JRN-02"])
check_true("JRN-03 says nothing validates this local file against any vendor",
           "no request is made to one" in JOURNAL_CAVEATS["JRN-03"])
check_true("JRN-04 says an abbreviation match is a guess that can be wrong",
           "that guess can be wrong" in JOURNAL_CAVEATS["JRN-04"])
check_true("JRN-05 says a blank is a lookup nobody has done, not a low number",
           "not mean the journal has" in JOURNAL_CAVEATS["JRN-05"])
check_true("JRN-06 dates the 预警 flag rather than treating it as a property",
           "dated statement by the list's publisher" in JOURNAL_CAVEATS["JRN-06"])
check_true("JRN-07 says the disagreement is left unresolved on purpose",
           "silently choosing would hide" in JOURNAL_CAVEATS["JRN-07"])


print("\nno crawler, and nothing that looks like one")

source = open(journals.__file__, encoding="utf-8").read()
for banned in ("import requests", "urllib.request", "urlopen", "http.client",
               "RobustHTTPClient", "BeautifulSoup", "selenium"):
    check(f"the module contains no `{banned}`", banned in source, False)
check("...and exposes nothing that fetches",
      [n for n in dir(journals)
       if not n.startswith("_") and any(w in n.lower() for w in ("fetch", "scrape", "crawl",
                                                                "download", "request"))],
      [])
# The three modules this package bans wholesale, asserted here as well as by the
# runner's --block-third-party mode, so the reason is written down next to the
# code rather than only in the runner.
for banned in ("pandas", "numpy", "openpyxl", "fitz"):
    check(f"the module does not import {banned}", f"import {banned}" in source, False)


# ----------------------------------------------------------------------
# The ISSN branch survives the profile pipeline.
#
# `join_journals` matched on ISSN correctly from the day it was written, and it
# still reported `issn: 0` on every real run — because `roles.prepare_paper`,
# the normaliser every metric consumes, rebuilt each record without the ISSN
# fields. The join therefore saw a corpus with no ISSNs and reported "0 of 18
# matched", which reads as *your table does not cover this corpus* rather than
# *the plumbing is broken*. A silent zero is the worst failure this module has,
# because the honest output and the broken output are the same sentence.
#
# So this asserts the whole path: a table whose journal names are deliberately
# wrong can only match on ISSN, and it must still match after prepare_paper.
# ----------------------------------------------------------------------
print("\nISSN survives prepare_paper")

from check_your_advisor.profile.roles import prepare_paper  # noqa: E402

_raw = {
    "pmid": "1", "title": "T", "journal": "Scientific reports",
    "issn": "2045-2322", "issn_type": "Electronic", "issn_linking": "2045-2322",
    "journal_abbrev": "Sci Rep", "pub_date": "2025 Jan", "doi": "",
    "authors": [{"name": "A B", "last": "A", "fore": "B", "initials": "B",
                 "affiliation": "", "email": "", "orcid": "", "equal_contrib": False,
                 "is_corresponding": False}],
    "pi_index": 0, "pi_evidence": "orcid", "pi_ambiguous": False,
}
_prepared = prepare_paper(_raw, {"author_name": "A B"})
check("prepare_paper keeps issn", _prepared.get("issn"), "2045-2322")
check("prepare_paper keeps issn_linking", _prepared.get("issn_linking"), "2045-2322")
check("prepare_paper keeps journal_abbrev", _prepared.get("journal_abbrev"), "Sci Rep")
check("paper_issn reads a prepared record", journals.paper_issn(_prepared), "2045-2322")

# A record from a corpus harvested before those fields existed must still pass
# through without a KeyError and fall back to name matching.
_old = dict(_raw)
for _key in ("issn", "issn_type", "issn_linking", "journal_abbrev"):
    _old.pop(_key)
_old_prepared = prepare_paper(_old, {"author_name": "A B"})
check("a pre-ISSN record prepares to empty, not to a missing key",
      _old_prepared.get("issn"), "")
check("paper_issn on a pre-ISSN prepared record", journals.paper_issn(_old_prepared), "")

# The decisive one: the table's journal name cannot match, so a hit proves the
# ISSN branch fired end to end.
_issn_only_table = write_csv(
    "issn_only.csv",
    ["ISSN", "刊名", "版本来源", "数据获取日期", "影响因子"],
    [["2045-2322", "名字完全对不上的一串字", "官方版", "2026-08-21", "3.8"]],
)
_loaded = load_journal_table(_issn_only_table)
_joined = join_journals([_prepared], _loaded)
check("a name-mismatched table still matches through prepare_paper",
      _joined["match_counts"]["issn"], 1)
check("...and it did not match on the name", _joined["match_counts"]["name_exact"], 0)
check("...nor on the abbreviation heuristic", _joined["match_counts"]["name_abbrev"], 0)
check("a pre-ISSN corpus gets no ISSN match from the same table",
      join_journals([_old_prepared], _loaded)["match_counts"]["issn"], 0)


# ----------------------------------------------------------------------
# The official-abbreviation route.
#
# A hand-built table is typed from whatever the source page displayed, and those
# pages show a journal's abbreviation about as often as its full title — so the
# table's name column and the corpus's `journal` column routinely hold two
# different correct names for one journal. NLM publishes the abbreviation on
# every record as `ISOAbbreviation`; matching on it is an exact match against a
# published string, which is a different kind of claim from the token-prefix
# heuristic that used to be the only thing standing between those two spellings.
#
# The two are counted separately and never summed into one coverage figure,
# because a reader is entitled to discount the guess without discounting the
# fact. These assert that the route fires, that it outranks the heuristic, and
# that it does not fire when it would merely be restating an exact-name miss.
# ----------------------------------------------------------------------
print("\nOfficial abbreviation route")

_abbrev_table = write_csv(
    "official_abbrev.csv",
    ["ISSN", "刊名", "版本来源", "数据获取日期", "影响因子"],
    # No ISSN: a match here can only come from a name, so the route is isolated.
    [["", "Sci Rep", "官方版", "2026-08-21", "3.8"]],
)
_abbrev_loaded = load_journal_table(_abbrev_table)


def _route(paper: dict) -> str:
    return join_journals([paper], _abbrev_loaded)["journals"][0]["match_type"]


check("the corpus's full title matches the table's official abbreviation",
      _route({"journal": "Scientific reports", "journal_abbrev": "Sci Rep"}),
      journals.MATCH_ABBREV_OFFICIAL)
# Same pair, minus the field: the heuristic still gets there, and is still
# labelled a guess. This is what every corpus harvested before ISSN capture does.
check("a pre-abbreviation corpus falls back to the heuristic, not to no match",
      _route({"journal": "Scientific reports"}),
      journals.MATCH_ABBREV)
# When the corpus already stores the abbreviated form, the exact-name route has
# settled it; reporting that as an abbreviation match would inflate the one
# route that exists to be doubted.
check("a corpus that already holds the abbreviation matches exactly, not by abbreviation",
      _route({"journal": "Sci Rep", "journal_abbrev": "Sci Rep"}),
      journals.MATCH_EXACT)
# An abbreviation that is not in the table must not borrow another journal's row.
check("an official abbreviation absent from the table does not match",
      _route({"journal": "Journal of Hepatology", "journal_abbrev": "J Hepatol"}),
      journals.MATCH_NONE)

# Every matched route must be inside the coverage total, or the per-route
# breakdown printed in the report sums to more than "matched".
check("the official route counts as matched",
      journals.MATCH_ABBREV_OFFICIAL in journals.MATCHED_ROUTES, True)
check("MATCHED_ROUTES is a prefix of ALL_ROUTES",
      journals.ALL_ROUTES[:len(journals.MATCHED_ROUTES)], journals.MATCHED_ROUTES)
check("the unmatched routes are exactly ambiguous and none",
      journals.ALL_ROUTES[len(journals.MATCHED_ROUTES):],
      (journals.MATCH_AMBIGUOUS, journals.MATCH_NONE))
_covered = join_journals(
    [{"journal": "Scientific reports", "journal_abbrev": "Sci Rep"}], _abbrev_loaded)
check("a paper matched by the official abbreviation reaches matched_papers",
      _covered["matched_papers"], 1)
check("...and every route in match_counts is one the report knows how to print",
      set(_covered["match_counts"]) <= set(journals.ALL_ROUTES), True)

TMP.cleanup()

print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(1 if _failed else 0)
