"""
期刊指标表：schema、加载、join、待查清单
=====================================
Journal-level metrics for a harvested corpus — the table's schema, a loader, a
join, and the worklist that says which journals to go and look up.

**This module makes no network request, and there is no crawler here or
anywhere else in the package.** Impact factor, JCR quartile and CAS partition
live in subscription databases that forbid scraping and defend against it. So
the tool does four things and no more:

  1. define the table's schema             (`SCHEMA`, `REQUIRED_FIELDS`)
  2. emit a worklist of what to look up    (`journal_worklist`, `write_worklist_csv`)
  3. join a filled-in table onto a corpus  (`join_journals`)
  4. hand the renderer the provenance it has to print

The data is fetched by hand, by the user, from LetPub or ablesci or a JCR seat.
That is slower than a scraper and it is the only version of this that is both
legal and accurate.

Scope is set by the corpus, not by the vendor
---------------------------------------------
There are tens of thousands of indexed journals; one advisor's five-year corpus
uses twenty-odd. `journal_worklist` scans the corpus and emits exactly those,
with the ISSN when the corpus carries one and the number of papers in each,
ordered by that count so the highest-leverage lookups come first. The CSV it
writes is the table's own template: fill the blank metric columns in and it
loads back through `load_journal_table` unchanged. Journals looked up once stay
in the table and are reused by the next corpus.

Why every row must name its edition
-----------------------------------
The partition number depends on who published the partition, and the sources do
not agree with each other:

- **LetPub's search-results page shows the 民间版 partition by default.** A
  number copied off that page and filed as "the CAS partition" is not the
  official one. This has already cost this user once.
- **ablesci (科研通) labels 官方版 and 新锐版 separately**, which is what makes
  it usable as a cross-check.
- **Clarivate's Master Journal List is free but carries only SCIE/SSCI indexing
  status** — no impact factor, no quartile. The full JCR needs an institutional
  seat.

So `source_edition` is a required column, `retrieved_on` is a required column,
and `join_journals` returns both in `provenance` for the report to print beside
every number. Two years from now a bare "2区" in a spreadsheet is unfalsifiable;
"2区 / 官方版 / 2026-08-20" is a claim someone can check. 预警 (warning-list)
status gets its own two columns for the same reason — it is a dated statement by
a list's publisher, not a property of the journal.

Disagreement is shown, not resolved
-----------------------------------
One journal may legitimately appear on several rows, one per edition. All of
them are kept, all of them are returned together by the join, and where they
disagree the disagreement is reported under `disagreement`. This module does not
pick a winner: which edition to believe is the reader's call, and hiding the
split would take that call away from them silently.

What these numbers cannot mean is in `JOURNAL_CAVEATS`, in the register style of
`profile.caveats`, and a renderer is expected to print them uncollapsed.

Conventions carried over from the rest of the package: every count travels with
the denominator it was computed over; nothing is coerced into a plausible value
to avoid a blank; unreadable input is counted and named rather than dropped. No
percentage is computed here — the n>=20 suppression rule lives in
`profile.metrics.percent` and belongs to whoever renders these counts, not to
the module that produces them.

Standard library only.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("check_your_advisor.journals")

__all__ = [
    "ABBREV_MIN_KEY_CHARS",
    "EDITIONS",
    "EDITION_FOLK",
    "EDITION_JCR",
    "EDITION_OFFICIAL",
    "EDITION_RISING",
    "EDITION_UNSPECIFIED",
    "JOURNAL_CAVEATS",
    "ALL_ROUTES",
    "MATCH_ABBREV",
    "MATCH_ABBREV_OFFICIAL",
    "MATCH_AMBIGUOUS",
    "MATCH_EXACT",
    "MATCH_ISSN",
    "MATCH_NONE",
    "MATCHED_ROUTES",
    "REQUIRED_FIELDS",
    "SCHEMA",
    "WORKLIST_GUIDANCE",
    "JournalTableError",
    "journal_name_key",
    "journal_name_tokens",
    "journal_worklist",
    "join_journals",
    "load_journal_table",
    "normalise_issn",
    "write_worklist_csv",
]


# ------------------------------------------------------------------
# 版本来源 — the enumerated editions
# ------------------------------------------------------------------
#
# Declared constants, exported, and echoed back in every join result. A
# partition number whose edition is unknown is not comparable with one whose
# edition is known, and after a year nobody remembers which page it came from.

EDITION_OFFICIAL = "官方版"      # 中科院分区表 official edition
EDITION_RISING = "新锐版"        # 中科院分区表 rising/新锐 edition
EDITION_FOLK = "民间版"          # the unofficial edition LetPub list pages default to
EDITION_JCR = "JCR"              # Clarivate JCR quartile / impact factor
EDITION_UNSPECIFIED = "未标注"    # the cell was blank — recorded, never guessed

EDITIONS: tuple[str, ...] = (
    EDITION_OFFICIAL,
    EDITION_RISING,
    EDITION_FOLK,
    EDITION_JCR,
)

_EDITION_ALIASES: dict[str, str] = {
    "官方版": EDITION_OFFICIAL, "官方": EDITION_OFFICIAL,
    "中科院官方版": EDITION_OFFICIAL, "中科院分区官方版": EDITION_OFFICIAL,
    "official": EDITION_OFFICIAL, "cas": EDITION_OFFICIAL, "cas官方版": EDITION_OFFICIAL,
    "新锐版": EDITION_RISING, "新锐": EDITION_RISING, "升级版": EDITION_RISING,
    "rising": EDITION_RISING,
    "民间版": EDITION_FOLK, "民间": EDITION_FOLK, "旧版": EDITION_FOLK,
    "letpub": EDITION_FOLK, "letpub列表页": EDITION_FOLK, "folk": EDITION_FOLK,
    "jcr": EDITION_JCR, "clarivate": EDITION_JCR, "wos": EDITION_JCR,
    "webofscience": EDITION_JCR, "jcr官方": EDITION_JCR,
}


# ------------------------------------------------------------------
# Schema
# ------------------------------------------------------------------


@dataclass(frozen=True)
class Column:
    """One column of the journal table.

    `zh` is what gets written into the template, because the person filling it
    in is reading a Chinese vendor page. `en` and `aliases` are accepted on the
    way back in, so a table exported from somewhere else still loads.
    """

    key: str
    zh: str
    en: str
    required: bool = False
    aliases: tuple[str, ...] = ()


SCHEMA: tuple[Column, ...] = (
    Column("issn", "ISSN", "issn", required=True,
           aliases=("印刷版issn", "纸质issn", "issn号", "printissn", "issn(print)")),
    Column("eissn", "eISSN", "eissn",
           aliases=("e-issn", "电子issn", "电子版issn", "onlineissn", "在线issn")),
    Column("journal", "刊名", "journal", required=True,
           aliases=("期刊", "期刊名", "期刊名称", "刊物名称", "journalname", "journaltitle")),
    Column("impact_factor", "影响因子", "impact_factor",
           aliases=("if", "impactfactor", "jcr影响因子", "最新影响因子", "五年影响因子")),
    Column("if_year", "IF年份", "if_year",
           aliases=("影响因子年份", "ifyear", "数据年份", "jcr年份", "年份")),
    Column("jcr_quartile", "JCR分区", "jcr_quartile",
           aliases=("jcr", "quartile", "jcrquartile", "jcr分区(q)", "wos分区")),
    # 大类 and 小类 are stored as written — "医学 2区", "胃肠肝病学 2区". They are
    # not split into a subject and an integer: LetPub prints several 小类 in one
    # cell, and a parser that silently kept the first would be inventing a
    # simplification the reader never agreed to.
    Column("cas_major", "中科院大类", "cas_major",
           aliases=("中科院大类分区", "大类学科", "大类", "casmajor", "中科院分区", "中科院分区大类")),
    Column("cas_minor", "中科院小类", "cas_minor",
           aliases=("中科院小类分区", "小类学科", "小类", "casminor", "中科院分区小类")),
    Column("source_edition", "版本来源", "source_edition", required=True,
           aliases=("来源版本", "分区版本", "版本", "sourceedition", "edition", "数据来源")),
    Column("retrieved_on", "数据获取日期", "retrieved_on", required=True,
           aliases=("获取日期", "查询日期", "采集日期", "retrievedon", "retrieved", "dateretrieved")),
    Column("is_warning", "是否预警", "is_warning",
           aliases=("预警", "是否在预警名单", "iswarning", "warning", "预警名单")),
    Column("warning_level", "预警等级", "warning_level",
           aliases=("预警级别", "warninglevel", "预警等级(高中低)")),
    Column("notes", "备注", "notes", aliases=("说明", "note", "remark", "remarks")),
    # Written by the worklist template so the filled-in file round-trips without
    # a special case. Carried through the loader, never used by the join.
    Column("corpus_paper_count", "本语料篇数", "corpus_paper_count",
           aliases=("语料篇数", "出现篇数", "papercount", "corpuspapercount")),
    Column("alias_group", "疑似同刊组", "alias_group",
           aliases=("同刊组", "aliasgroup", "别名组")),
)

REQUIRED_FIELDS: tuple[str, ...] = tuple(c.key for c in SCHEMA if c.required)

_BY_KEY: dict[str, Column] = {c.key: c for c in SCHEMA}


class JournalTableError(ValueError):
    """A journal table that cannot be loaded. Subclasses ValueError on purpose:
    callers already written as `except ValueError` keep working, and callers that
    want to tell a bad table from a bad number can catch this."""


# ------------------------------------------------------------------
# Normalisation
# ------------------------------------------------------------------

# Dropped before comparing journal names. Short function words survive
# abbreviation ("J Am Coll Cardiol" keeps "Am"); these do not, so keeping them
# would make token counts differ between a full title and its abbreviation.
_NAME_STOPWORDS = frozenset({"the", "of", "and", "for", "in", "on", "a", "an", "de", "der", "die", "das"})

# The shortest a token sequence may be before an abbreviation match is allowed.
# A declared threshold, not a measured one, and a legitimate thing to disagree
# with: "j hepatol" is 8 characters and matches; "j med" is 4 and does not, so a
# two-letter stub cannot sweep up an unrelated journal. Printed in JRN-04.
ABBREV_MIN_KEY_CHARS = 6

MATCH_ISSN = "issn"
MATCH_EXACT = "name_exact"
# NLM's own abbreviation for the journal, carried on the record as
# `Journal/ISOAbbreviation`. This is an exact match against a published
# abbreviation, not the token-prefix guess below, and the two are counted
# separately for that reason: one is a fact about what the journal is called,
# the other is an inference from how its name is spelled.
MATCH_ABBREV_OFFICIAL = "name_abbrev_official"
MATCH_ABBREV = "name_abbrev"
MATCH_AMBIGUOUS = "ambiguous"
MATCH_NONE = "none"

# The routes that count as a match, strongest first. Named once so that adding a
# route cannot land in the counter but be forgotten in the coverage total, which
# would print a per-route breakdown that sums to more than "matched".
MATCHED_ROUTES: tuple[str, ...] = (
    MATCH_ISSN, MATCH_EXACT, MATCH_ABBREV_OFFICIAL, MATCH_ABBREV,
)
# Every route a paper can end on, matched or not. Report order.
ALL_ROUTES: tuple[str, ...] = MATCHED_ROUTES + (MATCH_AMBIGUOUS, MATCH_NONE)

_TRUE_WORDS = frozenset({"是", "有", "y", "yes", "true", "1", "预警", "是的", "在"})
_FALSE_WORDS = frozenset({"否", "无", "n", "no", "false", "0", "非预警", "不是", "不在", "未预警"})
_BLANK_WORDS = frozenset({"", "-", "--", "na", "n/a", "nan", "none", "null", "暂无", "无数据", "未查"})

_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d", "%Y-%m", "%Y")


def _norm_header(text: Any) -> str:
    """Fold a header cell to its comparison form: NFKC, lowercase, alnum only.

    NFKC first because a table edited in a Chinese Excel arrives with full-width
    parentheses and full-width Latin letters, which are the same header to a
    human and different bytes to `==`.
    """
    folded = unicodedata.normalize("NFKC", str(text or "")).strip().lower()
    return "".join(ch for ch in folded if ch.isalnum())


_ALIAS_TO_KEY: dict[str, str] = {}
for _column in SCHEMA:
    for _name in (_column.key, _column.zh, _column.en, *_column.aliases):
        _folded = _norm_header(_name)
        if _folded and _folded not in _ALIAS_TO_KEY:
            _ALIAS_TO_KEY[_folded] = _column.key


def normalise_issn(value: Any) -> tuple[str, bool]:
    """Return `(NNNN-NNNC, checksum_ok)`, or `("", False)` if it is not an ISSN.

    The check digit is verified because this table is typed in by hand from a
    web page and a transposed digit is the likeliest error in the whole file. A
    failing checksum is still returned as a key — it just cannot match anything,
    which is a missed join rather than a wrong one — and is counted in the
    loader's `invalid_issns` so it can be fixed rather than silently lost.
    """
    raw = unicodedata.normalize("NFKC", str(value or "")).strip().upper()
    compact = re.sub(r"[^0-9X]", "", raw)
    if len(compact) != 8 or "X" in compact[:7]:
        return "", False
    total = sum(int(digit) * weight for digit, weight in zip(compact[:7], range(8, 1, -1)))
    remainder = (11 - total % 11) % 11
    expected = "X" if remainder == 10 else str(remainder)
    return f"{compact[:4]}-{compact[4:]}", compact[7] == expected


def journal_name_key(name: Any) -> str:
    """A journal name folded for exact comparison.

    Accents are stripped, `&` becomes `and`, punctuation goes, a leading "The"
    goes. This is comparison only — every name this module prints is the string
    as it was recorded, because `metrics.venue_repetition` prints them verbatim
    too and two spellings of one number would be worse than none.
    """
    text = unicodedata.normalize("NFKD", str(name or "")).lower()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9一-鿿]+", " ", text).strip()
    if text.startswith("the "):
        text = text[4:]
    return re.sub(r"\s+", " ", text).strip()


def journal_name_tokens(name: Any) -> tuple[str, ...]:
    """The comparison key split into content tokens, stopwords dropped."""
    return tuple(t for t in journal_name_key(name).split(" ") if t and t not in _NAME_STOPWORDS)


def _abbrev_match(left: Sequence[str], right: Sequence[str]) -> bool:
    """True when two token sequences are the same title, one of them abbreviated.

    PubMed's `Journal/Title` is not normalised — `roles.prepare_paper` copies it
    through and `metrics.venue_repetition` documents the consequence: one journal
    shows up as both "J Hepatol" and "Journal of Hepatology", counted as two.
    The table will carry whichever form the vendor page used, which need not be
    the form PubMed used, so an exact-name join alone silently loses journals.

    The rule: equal token counts, and each pair prefix-compatible in either
    direction — ["j", "hepatol"] against ["journal", "hepatology"]. It is a
    heuristic and it can be wrong, which is why matches made this way are tagged
    `name_abbrev` rather than folded in with the ISSN matches, and why a token
    sequence hitting more than one journal is refused as ambiguous instead of
    resolved by picking one.
    """
    if len(left) != len(right) or not left:
        return False
    shortest = 0
    for a, b in zip(left, right):
        if not (a.startswith(b) or b.startswith(a)):
            return False
        shortest += min(len(a), len(b))
    return shortest >= ABBREV_MIN_KEY_CHARS


def _clean(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def _is_blank(text: str) -> bool:
    return text.strip().lower() in _BLANK_WORDS


def _parse_float(text: str) -> float | None:
    """An impact factor, or None. The raw string is preserved by the caller."""
    if _is_blank(text):
        return None
    try:
        value = float(text.replace(",", "").rstrip("*"))
    except ValueError:
        return None
    return value if value >= 0 else None


def _parse_bool(text: str) -> bool | None:
    """预警 status. A blank cell is None, never False.

    Absence of a mark is not a statement that the journal is not on a warning
    list — the same distinction `CAV-16` makes about equal-contribution flags.
    Collapsing it to False would print a clean bill of health the table never
    gave.
    """
    folded = text.strip().lower()
    if folded in _TRUE_WORDS:
        return True
    if folded in _FALSE_WORDS:
        return False
    return None


def _parse_quartile(text: str) -> str:
    """`Q1`..`Q4` when the cell says so, otherwise the cell as written."""
    if _is_blank(text):
        return ""
    match = re.fullmatch(r"q\s*([1-4])", text.strip().lower())
    return f"Q{match.group(1)}" if match else text.strip()


def _parse_date(text: str) -> str:
    """`YYYY-MM-DD` when the cell parses as a date, otherwise the cell as written."""
    stripped = text.strip()
    if _is_blank(stripped):
        return ""
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(stripped, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return stripped


def _canonical_edition(text: str) -> tuple[str, bool]:
    """`(edition, recognised)`. Unknown wording is kept, never rewritten."""
    stripped = text.strip()
    if _is_blank(stripped):
        return EDITION_UNSPECIFIED, False
    folded = _norm_header(stripped)
    if folded in _EDITION_ALIASES:
        return _EDITION_ALIASES[folded], True
    for alias, canonical in _EDITION_ALIASES.items():
        if alias in folded:
            return canonical, True
    return stripped, False


# ------------------------------------------------------------------
# Loading
# ------------------------------------------------------------------

_ENCODINGS = ("utf-8-sig", "gb18030")


def _decode(path: str) -> tuple[str, str]:
    """Read the file as text; return `(text, encoding)`.

    UTF-8 first, then GB18030, because "save as CSV" in a Chinese Windows Excel
    writes GB18030 and that is the machine this table gets filled in on. The
    encoding that worked is returned so the caller can print it rather than
    leaving a mojibake journal name looking like a data error.
    """
    data = Path(path).read_bytes()
    for encoding in _ENCODINGS:
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise JournalTableError(
        f"期刊表无法解码: {path}（已尝试 {', '.join(_ENCODINGS)}）。"
        "请在 Excel 里另存为 UTF-8 CSV 后重试。"
    )


def _header_map(header: Sequence[str], path: str) -> tuple[dict[int, str], list[str]]:
    """Map column positions to schema keys; refuse an ambiguous header.

    Two columns folding to the same field is refused rather than resolved,
    because either one could be the one the user meant and picking silently
    would put the wrong number in the report.
    """
    mapping: dict[int, str] = {}
    seen: dict[str, int] = {}
    unknown: list[str] = []
    for position, cell in enumerate(header):
        key = _ALIAS_TO_KEY.get(_norm_header(cell))
        if key is None:
            if _clean(cell):
                unknown.append(_clean(cell))
            continue
        if key in seen:
            raise JournalTableError(
                f"期刊表表头有两列都对应字段 {key}: 第 {seen[key] + 1} 列与第 {position + 1} 列"
                f"（{path}）。请删掉其中一列后重试。"
            )
        seen[key] = position
        mapping[position] = key

    missing = [k for k in REQUIRED_FIELDS if k not in seen]
    if missing:
        wanted = "、".join(f"{_BY_KEY[k].zh}（或 {_BY_KEY[k].en}）" for k in missing)
        why = ""
        if {"source_edition", "retrieved_on"} & set(missing):
            why = (
                "缺版本来源或数据获取日期时，表里的分区数字过后无法追溯是哪一版、"
                "哪一天取的，因此不接受这张表。"
            )
        raise JournalTableError(f"期刊表缺少必需列: {wanted}（{path}）。{why}")
    return mapping, unknown


def _parse_row(cells: Sequence[str], mapping: Mapping[int, str], row_number: int) -> dict[str, Any]:
    """One CSV line to one table row, with the raw text kept where parsing can fail."""
    values = {key: "" for key in _BY_KEY}
    for position, key in mapping.items():
        if position < len(cells):
            values[key] = _clean(cells[position])

    issn, issn_ok = normalise_issn(values["issn"])
    eissn, eissn_ok = normalise_issn(values["eissn"])
    edition, edition_recognised = _canonical_edition(values["source_edition"])
    impact_factor = _parse_float(values["impact_factor"])

    return {
        "row_number": row_number,
        "journal": values["journal"],
        "journal_key": journal_name_key(values["journal"]),
        "journal_tokens": journal_name_tokens(values["journal"]),
        "issn": issn,
        "issn_raw": values["issn"],
        "issn_checksum_ok": issn_ok,
        "eissn": eissn,
        "eissn_raw": values["eissn"],
        "eissn_checksum_ok": eissn_ok,
        "impact_factor": impact_factor,
        # Kept whether or not it parsed: an unreadable "6.7 (2023)" prints as
        # written instead of vanishing into a None the reader cannot see.
        "impact_factor_raw": values["impact_factor"],
        "if_year": values["if_year"],
        "jcr_quartile": _parse_quartile(values["jcr_quartile"]),
        "cas_major": values["cas_major"],
        "cas_minor": values["cas_minor"],
        "source_edition": edition,
        "source_edition_raw": values["source_edition"],
        "edition_recognised": edition_recognised,
        "retrieved_on": _parse_date(values["retrieved_on"]),
        "retrieved_on_raw": values["retrieved_on"],
        "is_warning": _parse_bool(values["is_warning"]),
        "warning_level": values["warning_level"],
        "notes": values["notes"],
    }


def load_journal_table(path: str) -> dict[str, Any]:
    """Read a journal table from CSV. Raises `JournalTableError` on a bad header.

    Missing required columns stop the load with the column names spelled out in
    both languages — the alternative, skipping them, produces a report whose
    partition column is silently unsourced.

    Every row is kept, including several rows for one journal under different
    editions: that is the normal case for a cross-checked table and the join
    presents them together. Rows with no journal name and no ISSN are the one
    exception; they cannot be keyed to anything and are counted in
    `unusable_rows`.

    The returned dict carries the whole provenance block a report has to print:
    which editions are represented, which IF years, the span of retrieval dates,
    and every row whose edition wording was not recognised.
    """
    text, encoding = _decode(path)
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration:
        raise JournalTableError(f"期刊表是空文件: {path}") from None

    mapping, unknown_columns = _header_map(header, path)

    rows: list[dict[str, Any]] = []
    unusable = 0
    for line_number, cells in enumerate(reader, start=2):
        if not any(_clean(cell) for cell in cells):
            continue
        row = _parse_row(cells, mapping, line_number)
        if not row["journal_key"] and not row["issn"]:
            unusable += 1
            logger.warning("%s 第 %d 行既无刊名也无可用 ISSN，无法与任何语料对应", path, line_number)
            continue
        rows.append(row)

    editions = Counter(row["source_edition"] for row in rows)
    if_years = Counter(row["if_year"] for row in rows if row["if_year"])
    dates = sorted({row["retrieved_on"] for row in rows if row["retrieved_on"]})
    invalid_issns = [
        {"row_number": row["row_number"], "journal": row["journal"], "issn": row["issn_raw"]}
        for row in rows
        if (row["issn_raw"] and not row["issn"]) or (row["issn"] and not row["issn_checksum_ok"])
    ]
    unrecognised = [
        {"row_number": row["row_number"], "journal": row["journal"], "edition": row["source_edition_raw"]}
        for row in rows
        if not row["edition_recognised"] and row["source_edition"] != EDITION_UNSPECIFIED
    ]
    without_edition = sum(1 for row in rows if row["source_edition"] == EDITION_UNSPECIFIED)

    groups = _group_rows(rows)
    multi_edition = [
        {
            "journal": group["journal"],
            "editions": sorted({row["source_edition"] for row in group["rows"]}),
            "row_count": len(group["rows"]),
        }
        for group in groups
        if len(group["rows"]) > 1
    ]

    logger.info(
        "期刊表已载入: %s，%d 行 / %d 本刊，编码 %s，版本来源 %s",
        path, len(rows), len(groups), encoding,
        "、".join(f"{name} {count}" for name, count in editions.most_common()) or "无",
    )
    if without_edition:
        logger.warning("期刊表有 %d 行未标注版本来源，其分区数字无法追溯来源", without_edition)
    if invalid_issns:
        logger.warning("期刊表有 %d 行 ISSN 校验失败，这些行只能靠刊名匹配", len(invalid_issns))
    if unknown_columns:
        logger.info("期刊表有本工具不认识的列，已忽略: %s", "、".join(unknown_columns))

    return {
        "path": str(path),
        "encoding": encoding,
        "rows": rows,
        "groups": groups,
        "row_count": len(rows),
        "journal_count": len(groups),
        "unusable_rows": unusable,
        "unknown_columns": unknown_columns,
        "editions": dict(editions),
        "rows_without_edition": without_edition,
        "unrecognised_editions": unrecognised,
        "if_years": dict(if_years),
        "retrieved_on_range": (dates[0], dates[-1]) if dates else None,
        "rows_without_issn": sum(1 for row in rows if not row["issn"]),
        "invalid_issns": invalid_issns,
        "multi_edition_journals": multi_edition,
    }


# ------------------------------------------------------------------
# Grouping — one journal, possibly several rows
# ------------------------------------------------------------------


def _row_keys(row: Mapping[str, Any]) -> list[str]:
    keys = []
    if row.get("issn"):
        keys.append(f"issn:{row['issn']}")
    if row.get("eissn"):
        keys.append(f"issn:{row['eissn']}")
    if row.get("journal_key"):
        keys.append(f"name:{row['journal_key']}")
    return keys


def _group_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Collapse rows that are the same journal into groups, keeping every row.

    Union-find over the row's identifiers, so a row carrying only an ISSN and a
    row carrying only that journal's name end up in one group as soon as some
    third row carries both. Two rows for one journal under two editions are one
    group with two rows — that is the disagreement the join has to show.
    """
    parent = list(range(len(rows)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(a: int, b: int) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[max(root_a, root_b)] = min(root_a, root_b)

    first_seen: dict[str, int] = {}
    for index, row in enumerate(rows):
        for key in _row_keys(row):
            if key in first_seen:
                union(first_seen[key], index)
            else:
                first_seen[key] = index

    members: dict[int, list[int]] = defaultdict(list)
    for index in range(len(rows)):
        members[find(index)].append(index)

    groups: list[dict[str, Any]] = []
    root_to_group: dict[int, int] = {}
    for root in sorted(members):
        indexes = members[root]
        group_rows = [dict(rows[i]) for i in indexes]
        names = list(dict.fromkeys(r["journal"] for r in group_rows if r["journal"]))
        root_to_group[root] = len(groups)
        groups.append({
            "group_id": len(groups),
            "journal": names[0] if names else "",
            "names": names,
            "issns": sorted({r["issn"] for r in group_rows if r["issn"]}
                            | {r["eissn"] for r in group_rows if r["eissn"]}),
            "token_variants": sorted({r["journal_tokens"] for r in group_rows if r["journal_tokens"]}),
            "rows": group_rows,
        })

    for key, index in first_seen.items():
        groups[root_to_group[find(index)]].setdefault("keys", []).append(key)
    for group in groups:
        group.setdefault("keys", [])
    return groups


def _lookup(groups: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    return {key: group["group_id"] for group in groups for key in group["keys"]}


# ------------------------------------------------------------------
# Worklist
# ------------------------------------------------------------------

WORKLIST_GUIDANCE: tuple[str, ...] = (
    "逐本查刊，只查清单里这些，不必建全库。",
    f"每行必须填 {_BY_KEY['source_edition'].zh}，取值：" + " / ".join(EDITIONS) + "。",
    "LetPub 检索结果列表页默认显示民间版分区；要官方版请点进期刊详情页确认后再填。",
    "科研通 ablesci.com 把官方版与新锐版分开标注，可用来交叉验证。",
    "Clarivate MJL 免费但只有 SCIE/SSCI 收录状态，没有影响因子也没有分区。",
    "同一本刊查到两个版本的分区，就写两行，各标各的版本来源，不要挑一个填。",
    f"{_BY_KEY['retrieved_on'].zh} 按当天日期填，格式 YYYY-MM-DD；"
    "这是过两年判断数据是否过期的唯一依据。",
    "填完直接存回 CSV（UTF-8），本工具可以直接读。",
)


def journal_worklist(papers: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The journals this corpus actually uses, ordered by how many papers each holds.

    This is the piece that makes the whole design affordable: nobody is looking
    up twenty thousand journals, and nobody has to — one five-year corpus uses
    twenty-odd, and the ones holding the most papers are worth looking up first.

    Names are emitted exactly as PubMed recorded them, so a journal appearing
    under both its full title and its abbreviation appears as two entries. They
    are not merged, because merging on the abbreviation heuristic would put one
    made-up name in the template. `alias_group` marks entries the heuristic
    believes are one journal, so the user can look them up once and fill both
    rows; `alias_groups` collects those sets for a report to print.

    Pure — no file access. `write_worklist_csv` does the writing.
    """
    counts: Counter[str] = Counter()
    issns: dict[str, str] = {}
    conflicting_issns: list[dict[str, str]] = []
    without_journal = 0

    for paper in papers:
        name = _clean(paper.get("journal"))
        if not name:
            without_journal += 1
            continue
        counts[name] += 1
        issn = paper_issn(paper)
        if not issn:
            continue
        if name not in issns:
            issns[name] = issn
        elif issns[name] != issn:
            conflicting_issns.append({"journal": name, "kept": issns[name], "also_seen": issn})

    # Same order as `metrics.venue_repetition`: count descending, name ascending
    # so the file is stable between runs.
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))

    group_of = _alias_groups([name for name, _ in ordered])
    entries = [
        {
            "journal": name,
            "issn": issns.get(name, ""),
            "paper_count": count,
            "alias_group": group_of.get(name, 0),
        }
        for name, count in ordered
    ]

    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        if entry["alias_group"]:
            grouped[entry["alias_group"]].append(entry)
    alias_groups = [
        {
            "alias_group": group_id,
            "names": [e["journal"] for e in members],
            "paper_count": sum(e["paper_count"] for e in members),
        }
        for group_id, members in sorted(grouped.items())
    ]

    if conflicting_issns:
        logger.warning("语料里有 %d 处同名期刊带了不同 ISSN，清单只保留先出现的那个", len(conflicting_issns))
    logger.info(
        "待查清单: %d 本刊，覆盖 %d/%d 篇；其中 %d 组疑似同刊不同写法",
        len(entries), sum(counts.values()), len(papers), len(alias_groups),
    )

    return {
        "denominator": len(papers),
        "papers_with_journal": sum(counts.values()),
        "papers_without_journal": without_journal,
        "journal_count": len(entries),
        "entries": entries,
        "alias_groups": alias_groups,
        "conflicting_issns": conflicting_issns,
    }


def paper_issn(paper: Mapping[str, Any]) -> str:
    """The best ISSN a harvested record carries, in descending order of use.

    `issn_linking` is tried last but is the one that most often decides a join:
    it unifies a journal's print and electronic ISSNs, and a hand-built table
    usually lists whichever of the two its compiler happened to have. The
    article's own ISSN is preferred over it only because it is the number that
    record actually carries.

    `journal_issn` is accepted for corpora harvested before `pubmed_api` stored
    any of this; those records have none of the three and fall through to name
    matching, which is what they did before the field existed.
    """
    for key in ("issn", "journal_issn", "issn_linking"):
        issn, _ = normalise_issn(paper.get(key))
        if issn:
            return issn
    return ""


def _alias_groups(names: Sequence[str]) -> dict[str, int]:
    """Assign a group number to names the abbreviation heuristic ties together.

    Names in no group get 0. This is advisory only — nothing downstream joins on
    it — so a false positive here costs the user a glance, not a wrong number.
    """
    token_list = [(name, journal_name_tokens(name)) for name in names]
    assigned: dict[str, int] = {}
    next_id = 1
    for index, (name, tokens) in enumerate(token_list):
        if name in assigned or not tokens:
            continue
        partners = [
            other for other, other_tokens in token_list[index + 1:]
            if other not in assigned and other_tokens and _abbrev_match(tokens, other_tokens)
        ]
        if not partners:
            continue
        assigned[name] = next_id
        for other in partners:
            assigned[other] = next_id
        next_id += 1
    return {name: assigned.get(name, 0) for name in names}


def write_worklist_csv(worklist: Mapping[str, Any], path: str) -> str:
    """Write the worklist as the table's own template; return the path.

    The header is the full schema, so what comes back after the metric columns
    are filled in loads through `load_journal_table` with no conversion step.
    Known cells — journal name, ISSN when the corpus had one, the corpus paper
    count, the alias group — are pre-filled; everything the user has to look up
    is blank.

    UTF-8 with BOM, matching `export.save_to_csv`, so Excel opens it without
    mangling the Chinese headers.
    """
    # Schema key -> entry key. `journal_worklist` calls the count `paper_count`
    # while the column is `corpus_paper_count`; reading the entry by the schema
    # key alone silently wrote an empty cell for every row, which emptied the one
    # column that tells the user which journals to look up first.
    prefilled = {
        "journal": "journal",
        "issn": "issn",
        "corpus_paper_count": "paper_count",
        "alias_group": "alias_group",
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow([column.zh for column in SCHEMA])
        for entry in worklist.get("entries", []):
            writer.writerow([
                entry.get(prefilled[column.key], "") if column.key in prefilled else ""
                for column in SCHEMA
            ])
    logger.info("待查清单已写入: %s（%d 本刊待查）", target, len(worklist.get("entries", [])))
    return str(target)


# ------------------------------------------------------------------
# Join
# ------------------------------------------------------------------

_DISAGREEMENT_FIELDS = ("impact_factor_raw", "if_year", "jcr_quartile", "cas_major", "cas_minor")


def _disagreement(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[Any]]:
    """Fields on which the rows for one journal do not agree.

    Two rows disagreeing is the expected outcome of cross-checking an official
    edition against a folk one, and it is the reason the edition column exists.
    Nothing here resolves it — the values are listed with their editions and the
    reader decides.
    """
    conflicts: dict[str, list[Any]] = {}
    for field in _DISAGREEMENT_FIELDS:
        values = sorted({str(row.get(field) or "") for row in rows if str(row.get(field) or "")})
        if len(values) > 1:
            conflicts[field] = values
    flags = sorted({row["is_warning"] for row in rows if row.get("is_warning") is not None})
    if len(flags) > 1:
        conflicts["is_warning"] = flags
    return conflicts


def _match_group(
    name: str,
    issn: str,
    lookup: Mapping[str, int],
    groups: Sequence[Mapping[str, Any]],
    abbrev: str = "",
) -> tuple[int | None, str, list[str]]:
    """`(group_id, match_type, ambiguous_candidates)` for one journal name.

    Four routes, tried strongest first, and which one answered is returned
    rather than discarded: an ISSN match is a fact, an official-abbreviation
    match is a fact about a different string, and the token heuristic at the end
    is a guess that a reader is entitled to discount separately.

    `abbrev` is NLM's `ISOAbbreviation` for the record. It matters because a
    hand-built table is typed from whatever the source page displayed, and
    LetPub-style pages show the abbreviation about as often as the full title —
    so the table's name column and the corpus's `journal` column routinely hold
    two different correct names for one journal. Corpora harvested before
    `pubmed_api` stored it pass "" and fall through to the heuristic unchanged.
    """
    if issn and f"issn:{issn}" in lookup:
        return lookup[f"issn:{issn}"], MATCH_ISSN, []

    key = journal_name_key(name)
    if key and f"name:{key}" in lookup:
        return lookup[f"name:{key}"], MATCH_EXACT, []

    abbrev_key = journal_name_key(abbrev)
    # Guarded against `abbrev_key == key`: when the corpus already stores the
    # abbreviated form as the journal name the two are the same string, and the
    # exact-name miss above has already settled it. Reporting that as an
    # abbreviation match would inflate the route that exists to be doubted.
    if abbrev_key and abbrev_key != key and f"name:{abbrev_key}" in lookup:
        return lookup[f"name:{abbrev_key}"], MATCH_ABBREV_OFFICIAL, []

    tokens = journal_name_tokens(name)
    if not tokens:
        return None, MATCH_NONE, []
    hits = [
        group for group in groups
        if any(_abbrev_match(tokens, variant) for variant in group["token_variants"])
    ]
    if len(hits) == 1:
        return hits[0]["group_id"], MATCH_ABBREV, []
    if len(hits) > 1:
        # Refused rather than resolved: picking one of two plausible journals
        # would attach a real-looking impact factor to the wrong venue.
        return None, MATCH_AMBIGUOUS, [group["journal"] for group in hits]
    return None, MATCH_NONE, []


def join_journals(
    papers: Sequence[Mapping[str, Any]],
    table: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Join a loaded journal table onto a corpus. Returns counts with denominators.

    Matching is ISSN first, then the exact folded journal name, then the
    abbreviation heuristic — and which route produced each match is recorded, in
    `match_counts` and per journal, because an ISSN match is a fact and an
    abbreviation match is a guess. A name that plausibly matches two different
    journals in the table is left unmatched and listed under
    `ambiguous_journals`.

    Every row for a matched journal comes back in `entries`, one per edition,
    and where the editions disagree the fields they disagree on are listed in
    `disagreement`. No edition wins.

    `table=None` is a supported call and returns the full shape with
    `table_missing: True` and zero coverage, so a report can print "未提供对照表"
    in the column instead of leaving a blank the reader has to interpret.

    Coverage is returned as counts against two denominators, papers and distinct
    journals, and never as a percentage: the n>=20 rule that decides whether a
    percentage may be shown at all lives in `profile.metrics.percent` and is the
    renderer's to apply.
    """
    denominator = len(papers)
    groups: list[Mapping[str, Any]] = list(table.get("groups") or []) if table else []
    lookup = _lookup(groups) if groups else {}

    corpus_counts: Counter[str] = Counter()
    corpus_issns: dict[str, str] = {}
    # One official abbreviation per journal name. First wins, like the ISSN map
    # above: NLM assigns one per journal, so a second different value means the
    # corpus holds two journals under one name string, which name matching
    # cannot resolve either way.
    corpus_abbrevs: dict[str, str] = {}
    without_journal = 0
    for paper in papers:
        name = _clean(paper.get("journal"))
        if not name:
            without_journal += 1
            continue
        abbrev = _clean(paper.get("journal_abbrev"))
        if abbrev:
            corpus_abbrevs.setdefault(name, abbrev)
        corpus_counts[name] += 1
        issn = paper_issn(paper)
        if issn:
            corpus_issns.setdefault(name, issn)

    resolved: dict[str, dict[str, Any]] = {}
    journal_results: list[dict[str, Any]] = []
    for name, count in sorted(corpus_counts.items(), key=lambda item: (-item[1], item[0])):
        group_id, match_type, candidates = _match_group(
            name, corpus_issns.get(name, ""), lookup, groups,
            corpus_abbrevs.get(name, ""),
        )
        group = groups[group_id] if group_id is not None else None
        entries = list(group["rows"]) if group else []
        result = {
            "journal": name,
            "journal_key": journal_name_key(name),
            "corpus_issn": corpus_issns.get(name, ""),
            "corpus_abbrev": corpus_abbrevs.get(name, ""),
            "paper_count": count,
            "match_type": match_type,
            "matched_journal": group["journal"] if group else "",
            "ambiguous_candidates": candidates,
            "entries": entries,
            "editions": sorted({row["source_edition"] for row in entries}),
            "disagreement": _disagreement(entries) if len(entries) > 1 else {},
        }
        resolved[name] = result
        journal_results.append(result)

    # Corpus order, like `citations.fetch_citations`: the per-paper list is not
    # sorted by anything, so it cannot be read as an ordering of the papers.
    paper_rows: list[dict[str, str]] = []
    for paper in papers:
        name = _clean(paper.get("journal"))
        paper_rows.append({
            "pmid": str(paper.get("pmid") or ""),
            "journal": name,
            "match_type": resolved[name]["match_type"] if name else MATCH_NONE,
        })

    match_counts = Counter(row["match_type"] for row in paper_rows if row["journal"])
    matched_papers = sum(match_counts[t] for t in MATCHED_ROUTES)
    matched_journals = [r for r in journal_results if r["match_type"] in MATCHED_ROUTES]
    disagreeing = [r for r in matched_journals if r["disagreement"]]

    result = {
        "table_missing": table is None,
        # R1: both denominators, because a table can cover most papers while
        # missing most journals, and the two facts point at different work.
        "denominator": denominator,
        "papers_with_journal": denominator - without_journal,
        "papers_without_journal": without_journal,
        "matched_papers": matched_papers,
        "unmatched_papers": (denominator - without_journal) - matched_papers,
        "journal_denominator": len(journal_results),
        "matched_journals": len(matched_journals),
        "unmatched_journals": [r["journal"] for r in journal_results if r["match_type"] == MATCH_NONE],
        "ambiguous_journals": [
            {"journal": r["journal"], "candidates": r["ambiguous_candidates"]}
            for r in journal_results if r["match_type"] == MATCH_AMBIGUOUS
        ],
        # Split by route: `name_abbrev` matches are the heuristic's, and a reader
        # is entitled to discount them without discounting the ISSN matches.
        "match_counts": {key: match_counts.get(key, 0)
                         for key in ALL_ROUTES},
        "journals": journal_results,
        "papers": paper_rows,
        "disagreement_count": len(disagreeing),
        "disagreeing_journals": [
            {"journal": r["journal"], "editions": r["editions"], "fields": r["disagreement"]}
            for r in disagreeing
        ],
        "warned_journals": [
            {
                "journal": r["journal"],
                "paper_count": r["paper_count"],
                "levels": sorted({row["warning_level"] for row in r["entries"] if row["warning_level"]}),
                "editions": r["editions"],
            }
            for r in matched_journals
            if any(row["is_warning"] for row in r["entries"])
        ],
        # Everything the report must print beside the numbers. Without it a
        # partition in the output is a number with no source and no date.
        "provenance": {
            "table_path": table.get("path", "") if table else "",
            "encoding": table.get("encoding", "") if table else "",
            "table_rows": table.get("row_count", 0) if table else 0,
            "table_journals": table.get("journal_count", 0) if table else 0,
            "editions": dict(table.get("editions") or {}) if table else {},
            "rows_without_edition": table.get("rows_without_edition", 0) if table else 0,
            "unrecognised_editions": list(table.get("unrecognised_editions") or []) if table else [],
            "if_years": dict(table.get("if_years") or {}) if table else {},
            "retrieved_on_range": table.get("retrieved_on_range") if table else None,
            "abbrev_min_key_chars": ABBREV_MIN_KEY_CHARS,
        },
    }

    if table is None:
        logger.info("未提供期刊指标表，%d 篇论文的期刊指标列全部留空", denominator)
    else:
        logger.info(
            "期刊指标 join: %d/%d 篇匹配（ISSN %d / 刊名 %d / 官方缩写 %d / 缩写推测 %d），"
            "%d/%d 本刊匹配，%d 本刊版本间有分歧",
            matched_papers, denominator - without_journal,
            match_counts.get(MATCH_ISSN, 0), match_counts.get(MATCH_EXACT, 0),
            match_counts.get(MATCH_ABBREV_OFFICIAL, 0),
            match_counts.get(MATCH_ABBREV, 0),
            len(matched_journals), len(journal_results), len(disagreeing),
        )
        if result["ambiguous_journals"]:
            logger.warning("有 %d 本刊靠缩写能匹配到多本，已按未匹配处理", len(result["ambiguous_journals"]))
    return result


# ------------------------------------------------------------------
# What these numbers cannot mean
# ------------------------------------------------------------------
#
# Register style borrowed from `profile.caveats`: module constants, printed
# verbatim, never paraphrased at the call site. English, like `CAVEATS`.

JOURNAL_CAVEATS: dict[str, str] = {
    "JRN-01": (
        "Every partition and impact factor here was typed in by hand from a vendor page, and the "
        "edition it came from is printed beside it because the sources disagree. LetPub's "
        "search-results list shows the folk (民间版) partition by default, not the official one; "
        "ablesci labels the official (官方版) and rising (新锐版) editions separately; Clarivate's "
        "free Master Journal List carries indexing status only, with no impact factor and no "
        "quartile. A partition with no edition beside it cannot be checked by anyone later, "
        "including the person who wrote it down."
    ),
    "JRN-02": (
        "An impact factor is a property of a journal in one year, not of a paper in it. It is the "
        "mean of a distribution skewed hard enough that most papers in a journal are cited well "
        "below its impact factor. Nothing here says how often any of these papers was cited — that "
        "is a different number, fetched separately, with its own coverage."
    ),
    "JRN-03": (
        "This table is a local file. Nothing in this toolkit validates it against any vendor, and no "
        "request is made to one: these tables are licensed products and scraping them is neither "
        "permitted nor reliable. The retrieval date in each row is the only thing distinguishing a "
        "current table from one filled in three years ago, and partitions are re-cut every year."
    ),
    "JRN-04": (
        "Journal names in PubMed are not normalised, so one journal arrives as both its full title "
        "and its abbreviation. Matches made by ISSN are exact. Matches marked name_abbrev were "
        f"guessed by comparing token prefixes over at least {ABBREV_MIN_KEY_CHARS} characters, and "
        "that guess can be wrong. Where the guess fits more than one journal in the table the paper "
        "is left unmatched and both candidates are listed, rather than one of them being chosen."
    ),
    "JRN-05": (
        "An unmatched journal means the table does not contain it. It does not mean the journal has "
        "no impact factor, no partition, or a low one. Read a blank cell as a lookup nobody has done "
        "yet, and do not fill it in from memory: coverage counts are printed against both "
        "denominators — papers and distinct journals — so the size of the gap is visible."
    ),
    "JRN-06": (
        "A warning-list (预警) flag is a dated statement by the list's publisher about a journal, "
        "and the lists are re-issued yearly with journals added and removed. It is not a statement "
        "about any paper in this corpus, and a journal that was not on the list when this table was "
        "filled in may be on the current one."
    ),
    "JRN-07": (
        "Where a journal has several rows, they are several editions of the same claim and all of "
        "them are shown. Fields they disagree on are listed as a disagreement and left unresolved: "
        "this toolkit has no basis for preferring one edition over another, and silently choosing "
        "would hide the one fact the reader most needs, which is that the sources do not agree."
    ),
}
