"""
学生评价表：schema、加载、join
==============================
Third-party statements about one advisor — the table's schema, a loader, and a
join that selects the rows belonging to one PI and counts them.

**This module makes no network request, and there is no crawler here or
anywhere else in the package.** Student evaluations live on forums, Q&A sites,
review pages and in private conversations; none of them permits automated
collection and several of them defend against it. So the tool does three things
and no more:

  1. define the table's schema             (`SCHEMA`, `REQUIRED_FIELDS`)
  2. load a filled-in table                (`load_evaluation_table`)
  3. select one PI's rows and count them   (`join_evaluations`)

The collection is done by hand, by the user. That is the same division of
labour `journals.py` and `theses.py` use, and the reason `source` and
`retrieved_on` are required columns: an anonymous sentence with no site and no
date behind it is not evidence of anything two years from now.

What this module deliberately does not compute
----------------------------------------------
No sentiment analysis. No polarity label, no positive/negative count, no
keyword score, no aggregate of any kind, and no contribution to the composite
score in Section 16. Those are all technically easy and all wrong here, for one
reason: this is a small, self-selected set of statements by people who chose to
write something down, and any average over it measures who bothered to post,
not what the advisor is like. A number computed over it would carry an
authority the underlying text cannot support, and it would travel further than
the text it came from.

So the rows are printed as given, each beside the source it came from and the
day that source was read, and the reading is the reader's. `EVALUATION_STANCE`
says this in the section itself rather than leaving it to a commit message.

The counts this module does produce are counts of rows, not of opinions: how
many statements were collected, how many distinct sources they came from, and
what span of time they cover. Every one of them carries its denominator.

Standard library only. `load_evaluation_table` touches the disk;
`join_evaluations` is pure — dicts in, dict out — and can be checked against
fixtures offline.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

# `name_script` only, and only to decide whether two advisor names are
# comparable at all. Importing it rather than re-deriving the CJK ranges keeps
# one definition of what "written in Chinese characters" means; a second copy of
# that range is how two modules disagree about one name without anyone noticing.
# `theses` imports nothing from `profile` at module level, so this adds no cycle.
from .theses import name_script

logger = logging.getLogger(__name__)

__all__ = [
    "EVALUATION_CAVEATS",
    "EVALUATION_LIMITS",
    "EVALUATION_STANCE",
    "EvaluationTableError",
    "REQUIRED_EITHER",
    "REQUIRED_FIELDS",
    "SCHEMA",
    "join_evaluations",
    "load_evaluation_table",
]


# ------------------------------------------------------------------
# The paragraph that must travel with the section
# ------------------------------------------------------------------
#
# Kept as a module constant and returned by `join_evaluations` under "stance",
# for the same reason `theses.THESIS_DENOMINATOR_CAVEAT` is kept as one: this is
# the part that says what the section refuses to do, and a paraphrase at the
# call site is how that refusal quietly weakens into a hedge.

EVALUATION_STANCE = (
    "These are third-party statements, reproduced as they were collected and attributed to the "
    "source they came from. This section runs no sentiment analysis, assigns no polarity, counts "
    "no positive or negative words, produces no average, no rating and no summary judgement, and "
    "contributes nothing to the composite score in Section 16 — the score is computed from "
    "publication metadata alone and adding an opinion to it would launder the opinion into a "
    "number. What is counted here is rows, sources and dates, never approval. Whether any of "
    "these statements is true, representative or fair is the reader's call, and it has to be made "
    "on the text itself rather than on a figure this toolkit computed over it."
)

# What the table itself misses, separately from whether any single statement is
# accurate. Each of these bends the row count in a stated direction, so they are
# returned with the result rather than left to a footnote.
EVALUATION_LIMITS: tuple[tuple[str, str], ...] = (
    (
        "Everyone here chose to write something",
        "Nobody is sampled. People post after an experience strong enough to be worth typing up, "
        "in either direction, and the many students who felt neither way are absent from every "
        "row. The count of statements is a count of people who posted, and there is no observable "
        "denominator of people who could have.",
    ),
    (
        "Authorship is unverifiable",
        "Nothing in a forum post proves the writer was ever this advisor's student, or that two "
        "posts are two people. A group with a grievance and a group with a stake both have "
        "reasons to write, and neither leaves a signature. Rows are printed with their source so "
        "that judgement is possible; the toolkit makes none.",
    ),
    (
        "Only the sources you searched",
        "A statement on a site nobody looked at is missing, and the file does not record that the "
        "search was narrow. Widening the collection changes this count, and nothing here says by "
        "how much.",
    ),
    (
        "Pages are edited and deleted",
        "A thread can be revised, locked or removed after it is read. `数据获取日期` is the day "
        "the page was in the state recorded here, which is why it is a required column and why a "
        "row with no date behind it is not evidence of a current state.",
    ),
    (
        "One person's supervision is not a constant",
        "An advisor with twenty years of students is not the same supervisor throughout, and a "
        "statement about 2015 says nothing certain about now. `评价年份` is optional in the schema "
        "because most sources do not carry it, and where it is missing the statement cannot be "
        "placed in time at all.",
    ),
)

# Register style borrowed from `journals.JOURNAL_CAVEATS`: module constants,
# printed verbatim, never paraphrased at the call site.
EVALUATION_CAVEATS: dict[str, str] = {
    "EVL-01": (
        "Every row here was collected by hand, by the reader, from a page the reader chose. This "
        "package fetched none of it and contains no crawler: the sites carrying this material do "
        "not permit automated collection, and a scraper would be both a compliance problem and "
        "unreliable. What the toolkit does is define the columns, read the file, attribute the "
        "rows to one advisor and print where each one came from."
    ),
    "EVL-02": (
        "No sentiment analysis is performed and none will be. A polarity label over a handful of "
        "self-selected posts is a number with the authority of a measurement and the content of a "
        "guess, and it would be quoted long after the text it came from was forgotten."
    ),
    "EVL-03": (
        "Nothing in this section enters the composite score. Section 16 is computed from "
        "publication metadata only, and its weight table is printed in full; no row of this table "
        "appears in it, at any weight, including zero."
    ),
    "EVL-04": (
        "The rows are printed in the order the file supplied them, and that order is not a "
        "ranking, a chronology or a measure of importance. Where a statement carries no 评价年份 "
        "it cannot be placed in time, and the span printed below covers only the rows that do."
    ),
    "EVL-05": (
        "A count of statements is not a count of students, and two rows are not two people unless "
        "the sources say so. One writer can post on three sites and one thread can be quoted "
        "twice; exact duplicates are dropped and listed, but a paraphrase of the same experience "
        "is indistinguishable from a second experience and is counted twice."
    ),
}


# ------------------------------------------------------------------
# Schema
# ------------------------------------------------------------------


@dataclass(frozen=True)
class Column:
    """One column of the evaluation table.

    `zh` is what the person filling the file in is most likely to type, because
    the pages this material comes from are Chinese. `en` and `aliases` are
    accepted on the way back in, so a file kept in English still loads.
    """

    key: str
    zh: str
    en: str
    required: bool = False
    aliases: tuple[str, ...] = ()


SCHEMA: tuple[Column, ...] = (
    Column("advisor", "导师姓名", "advisor", required=True,
           aliases=("导师", "指导教师", "指导老师", "老师", "supervisor", "advisor_name",
                    "supervisor_name", "mentor")),
    Column("advisor_latin", "导师姓名拼音", "advisor_latin",
           aliases=("导师拼音", "导师英文名", "advisor_pinyin", "supervisor_latin",
                    "advisor_name_en")),
    Column("source", "评价来源", "source", required=True,
           aliases=("来源", "出处", "评价出处", "平台", "网站", "站点", "source_name",
                    "site", "platform", "origin")),
    Column("retrieved_on", "数据获取日期", "retrieved_on", required=True,
           aliases=("获取日期", "采集日期", "查询日期", "检索日期", "抓取日期", "记录日期",
                    "retrieved", "retrievedon", "dateretrieved", "collected_on")),
    # One of these two must be present. Neither alone is required, because a
    # source that publishes per-dimension scores and no prose is as usable as a
    # source that publishes prose and no scores — and a file carrying neither
    # holds no statement at all, only a citation to one.
    Column("content", "评价内容", "content",
           aliases=("评价", "评语", "内容", "原文", "正文", "评价正文", "留言", "comment",
                    "text", "review", "statement", "body")),
    Column("rating", "维度评分", "rating",
           aliases=("评分", "打分", "分项评分", "各维度评分", "维度打分", "score",
                    "ratings", "dimension_score", "dimension_scores")),
    Column("student_role", "学生身份", "student_role",
           aliases=("身份", "作者身份", "评价人身份", "学生类型", "role", "author_role",
                    "reviewer_role", "identity")),
    Column("year", "评价年份", "year",
           aliases=("年份", "发表年份", "评价时间", "发布年份", "发布时间", "评价年",
                    "evaluation_year", "posted_year", "year_posted")),
    Column("url", "原文链接", "url",
           aliases=("链接", "网址", "原文地址", "地址", "link", "permalink", "source_url")),
)

REQUIRED_FIELDS: tuple[str, ...] = tuple(c.key for c in SCHEMA if c.required)

# The one-of-these-two rule, declared beside the flat requirement rather than
# hidden inside the loader: a reader of the schema has to be able to see that a
# file with neither column is refused.
REQUIRED_EITHER: tuple[str, ...] = ("content", "rating")

OPTIONAL_KEYS: tuple[str, ...] = tuple(
    c.key for c in SCHEMA if not c.required and c.key not in REQUIRED_EITHER
)

_BY_KEY: dict[str, Column] = {c.key: c for c in SCHEMA}


class EvaluationTableError(ValueError):
    """An evaluation table that cannot be loaded. Subclasses ValueError on
    purpose, for the reason `journals.JournalTableError` does: callers already
    written as `except ValueError` keep working, and a caller that wants to tell
    a bad table from a bad number can catch this one."""


# ------------------------------------------------------------------
# Normalisation
# ------------------------------------------------------------------

_HEADER_NOISE_RE = re.compile(r"[\s_\-·.()（）\[\]【】:：/、]+")

# A year below this in an evaluation cell is a parse artefact, not a very old
# post: the sites this material comes from did not exist earlier. Rows outside
# the range are kept and flagged, never dropped — a wrong year is still a real
# statement.
EARLIEST_PLAUSIBLE_YEAR = 1995

_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d", "%Y-%m", "%Y")

_BLANK_WORDS = frozenset({"", "-", "--", "na", "n/a", "nan", "none", "null", "暂无", "无", "未填"})

# Excel on a Chinese Windows writes CSV as GB18030; everything else writes UTF-8,
# usually with a BOM. Both are tried, in that order, and whichever worked is
# returned in the result — a decoding fallback that is not recorded is a silent
# degrade, and a mojibake statement would be printed as if that were what
# somebody wrote.
_ENCODINGS: tuple[str, ...] = ("utf-8-sig", "gb18030")


def _clean(value: Any) -> str:
    """Strip a cell. Deliberately does NOT normalise it.

    The other two table loaders NFKC every cell, and they are right to: what
    they hold is a journal name or a supervisor name, and every one of those is
    a key something is matched on. What this table holds is somebody's sentence,
    printed back verbatim, and NFKC rewrites `，` to `,`, `；` to `;` and `（`
    to `(`. That is a silent edit to a quotation — small, invisible in the diff,
    and exactly the class of change a section built on "printed as given" cannot
    make. So folding happens where it is needed, inside `_fold`, and the cell
    itself is left alone.
    """
    return str(value if value is not None else "").strip()


def _fold(value: Any) -> str:
    """The comparison/parsing form of a cell: NFKC, stripped. Never printed."""
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def _is_blank(text: str) -> bool:
    return _fold(text).lower() in _BLANK_WORDS


def _norm_header(text: Any) -> str:
    folded = unicodedata.normalize("NFKC", str(text or "")).strip().lstrip("﻿").lower()
    return _HEADER_NOISE_RE.sub("", folded)


_ALIAS_TO_KEY: dict[str, str] = {}
for _column in SCHEMA:
    for _name in (_column.key, _column.zh, _column.en, *_column.aliases):
        _folded = _norm_header(_name)
        if _folded and _folded not in _ALIAS_TO_KEY:
            _ALIAS_TO_KEY[_folded] = _column.key


def _one_line(text: str) -> str:
    """Collapse a cell to one line. Both renderers read one body line at a time.

    A statement pasted out of a forum arrives with newlines in it, and a body
    line that contains one splits into two blocks in the Markdown reader — the
    second of which no longer carries the source it belongs to. The text is not
    trimmed, only rejoined.
    """
    return re.sub(r"\s*[\r\n]+\s*", " ", text).strip()


def _parse_year(value: Any) -> int | None:
    match = re.search(r"(\d{4})", _fold(value))
    return int(match.group(1)) if match else None


def _parse_date(text: str) -> str:
    """`YYYY-MM-DD` when the cell parses as a date, otherwise the cell as written.

    Folded before parsing so a date typed with full-width digits still reads,
    and returned unfolded when it does not parse — an unparseable cell is
    printed the way the file spelled it, like every other cell here.
    """
    folded = _fold(text)
    if _is_blank(folded):
        return ""
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(folded, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return text.strip()


# ------------------------------------------------------------------
# Loading
# ------------------------------------------------------------------


def _decode(path: str | Path) -> tuple[str, str]:
    """Read the file as text; return `(text, encoding)`."""
    data = Path(path).read_bytes()
    for encoding in _ENCODINGS:
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise EvaluationTableError(
        f"学生评价表无法解码: {path}（已尝试 {', '.join(_ENCODINGS)}）。"
        "请在 Excel 里另存为 UTF-8 CSV 后重试。"
    )


def _header_map(header: Sequence[str], path: str | Path) -> tuple[dict[int, str], list[str]]:
    """Map column positions to schema keys; refuse a header that is missing a required column.

    Two columns folding to the same field is refused rather than resolved,
    because either one could be the one the user meant and picking silently
    would put the wrong text under the wrong attribution.
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
            raise EvaluationTableError(
                f"学生评价表表头有两列都对应字段 {key}: 第 {seen[key] + 1} 列与第 {position + 1} 列"
                f"（{path}）。请删掉其中一列后重试。"
            )
        seen[key] = position
        mapping[position] = key

    found = ", ".join(_clean(cell) for cell in header if _clean(cell)) or "(none)"
    missing = [k for k in REQUIRED_FIELDS if k not in seen]
    if missing:
        wanted = "、".join(f"{_BY_KEY[k].zh}（或 {_BY_KEY[k].en}）" for k in missing)
        why = ""
        if {"source", "retrieved_on"} & set(missing):
            why = (
                "缺评价来源或数据获取日期时，表里的每句话过后都无法追溯是谁在哪一天说的、"
                "从哪一页抄来的，因此不接受这张表——这与期刊表缺版本来源是同一条规矩。"
            )
        raise EvaluationTableError(
            f"学生评价表缺少必需列: {wanted}（{path}）。{why}表头读到的列: {found}。"
        )
    if not any(k in seen for k in REQUIRED_EITHER):
        raise EvaluationTableError(
            "学生评价表缺少必需列: "
            + "、".join(f"{_BY_KEY[k].zh}（或 {_BY_KEY[k].en}）" for k in REQUIRED_EITHER)
            + f"——两列至少要有一列（{path}）。两列都没有时，这张表只有出处没有内容，"
            f"没有任何一句评价可以印出来。表头读到的列: {found}。"
        )
    return mapping, unknown


def _parse_row(cells: Sequence[str], mapping: Mapping[int, str], line: int) -> dict[str, Any]:
    """One CSV line to one row, with the raw text kept where parsing can fail."""
    values = {key: "" for key in _BY_KEY}
    for position, key in mapping.items():
        if position < len(cells):
            values[key] = _clean(cells[position])

    flags: list[str] = []
    year = _parse_year(values["year"])
    if values["year"] and year is None:
        flags.append("year_unreadable")
    elif year is not None and not (EARLIEST_PLAUSIBLE_YEAR <= year <= datetime.now().year + 1):
        flags.append("year_out_of_range")
    retrieved_on = _parse_date(values["retrieved_on"])
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", retrieved_on or ""):
        flags.append("retrieved_on_unparseable")
    if not values["url"]:
        flags.append("no_source_url")

    return {
        "line": line,
        "advisor": values["advisor"],
        "advisor_latin": values["advisor_latin"],
        "source": values["source"],
        "retrieved_on": retrieved_on,
        "retrieved_on_raw": values["retrieved_on"],
        # One line each, and printed verbatim otherwise: no truncation, no
        # summary, no re-wording. The row is somebody else's sentence.
        "content": _one_line(values["content"]),
        "rating": _one_line(values["rating"]),
        "student_role": values["student_role"],
        "year": year,
        "year_raw": values["year"],
        "url": values["url"],
        "flags": flags,
    }


def load_evaluation_table(path: str | Path) -> dict[str, Any]:
    """
    Read a hand-collected student-evaluation table and validate it against `SCHEMA`.

    One CSV, one row per statement. Headers may be written in Chinese or English
    — every spelling in `SCHEMA[*].aliases` is accepted — and are matched after
    case, spacing and punctuation are removed, so `评价来源`, `评价 来源` and
    `source` are the same column.

    Refused rather than degraded:

    - a missing required column (`导师姓名`, `评价来源`, `数据获取日期`), named
      individually in the error together with the headers that were found.
      `评价来源` and `数据获取日期` are required for the same reason the journal
      table needs an edition column: a sentence with no origin and no date
      cannot be re-checked later, and an unattributed sentence about a person is
      worth less than nothing.
    - a header carrying neither `评价内容` nor `维度评分`. Such a file records
      that statements exist without recording any of them.
    - two columns folding to the same field, and a file that decodes as neither
      UTF-8 nor GB18030.

    Kept but recorded, because a malformed cell is still a real statement:

    - an unreadable or out-of-range `评价年份`, an unparseable `数据获取日期`
      and a missing `原文链接` all become per-row `flags` with the raw text
      preserved beside the parsed value.
    - a row with no `评价来源`, or with neither content nor rating, cannot be
      printed as an attributed statement, so it goes to `rejected` with its line
      number rather than being dropped silently.
    - exact duplicates on (advisor, source, content, rating, year) are dropped
      once and listed. A paraphrase of the same experience is not a duplicate
      and is not detected as one — see `EVL-05`.

    Returns a dict with `rows`, `rejected`, `duplicates_dropped`, the
    `denominator` those rows were counted over, `columns_used` mapping each
    schema key to the header as actually written, `columns_missing_optional`,
    `unknown_columns`, the `encoding` that worked, and the provenance summaries
    `sources`, `retrieved_dates` and `year_range` that the report must print.
    `limits` carries `EVALUATION_LIMITS` so a renderer holding only this dict
    still has the text describing what the collection cannot contain.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"student evaluation table not found: {path}")

    text, encoding = _decode(path)
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration:
        raise EvaluationTableError(f"学生评价表是空文件: {path}") from None

    mapping, unknown_columns = _header_map(header, path)
    columns_used = {key: _clean(header[position]) for position, key in mapping.items()}

    rows: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, int]] = set()

    for line, cells in enumerate(reader, start=2):
        if not any(_clean(cell) for cell in cells):
            continue
        row = _parse_row(cells, mapping, line)
        if not row["source"]:
            rejected.append({"line": line, "reason": "no source recorded"})
            continue
        if not row["content"] and not row["rating"]:
            rejected.append({
                "line": line,
                "reason": "neither 评价内容 nor 维度评分 carries anything",
            })
            continue
        key = (row["advisor"], row["source"], row["content"], row["rating"], row["year"] or 0)
        if key in seen:
            duplicates.append({"line": line, "source": row["source"], "year": row["year"]})
            continue
        seen.add(key)
        rows.append(row)

    years = sorted({row["year"] for row in rows if row["year"] is not None})
    dates = sorted({row["retrieved_on"] for row in rows if row["retrieved_on"]})

    logger.info(
        "学生评价表已读入: %s (%s 编码) — %d 行可用, %d 行被拒, %d 行重复, 来自 %d 个来源",
        path, encoding, len(rows), len(rejected), len(duplicates),
        len({row["source"] for row in rows}),
    )
    for flag, count in sorted(Counter(f for row in rows for f in row["flags"]).items()):
        logger.warning("  %d 行带标记 %s（已保留，未丢弃）", count, flag)
    if unknown_columns:
        logger.info("学生评价表有本工具不认识的列，已忽略: %s", "、".join(unknown_columns))

    return {
        "path": str(path),
        "encoding": encoding,
        "rows": rows,
        "rejected": rejected,
        "duplicates_dropped": duplicates,
        # R1: the population every count downstream is taken over.
        "denominator": len(rows),
        "rows_read": len(rows) + len(rejected) + len(duplicates),
        "columns_used": columns_used,
        "columns_missing_optional": [k for k in OPTIONAL_KEYS if k not in columns_used],
        "columns_missing_either": [k for k in REQUIRED_EITHER if k not in columns_used],
        "unknown_columns": unknown_columns,
        "flag_counts": dict(sorted(Counter(f for row in rows for f in row["flags"]).items())),
        "sources": dict(sorted(Counter(row["source"] for row in rows).items())),
        "retrieved_dates": dates,
        "retrieved_on_range": (dates[0], dates[-1]) if dates else None,
        "year_range": (years[0], years[-1]) if years else None,
        "rows_without_year": sum(1 for row in rows if row["year"] is None),
        "limits": EVALUATION_LIMITS,
    }


# ------------------------------------------------------------------
# Attribution — whose evaluations are these?
# ------------------------------------------------------------------


def _advisor_key(name: str) -> str:
    """An advisor name folded for comparison. Comparison only — never printed."""
    folded = unicodedata.normalize("NFKC", str(name or "")).strip().lower()
    return _HEADER_NOISE_RE.sub("", folded).replace(",", "")


def _same_advisor(left: str, right: str) -> bool:
    """Whether two advisor names are the same person, as far as this table can tell.

    Deliberately narrower than `theses._compare_names`, and the difference is
    the stake. There, a name join decides whether a graduate published, so a
    near-miss has to go to manual review rather than be counted either way.
    Here the name only selects which rows belong to the PI the report is about,
    and the three-way outcome below covers the case where nothing joined. So:
    exact after folding, or the same romanised tokens in either order — no
    initials, no prefixes, no partial credit.
    """
    left_key, right_key = _advisor_key(left), _advisor_key(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    # Rotation only, and only inside the Latin script: "Zhang San" against "San
    # Zhang". Two Han names that are not identical are two different names.
    if name_script(left) != "latin" or name_script(right) != "latin":
        return False
    left_tokens = sorted(t for t in re.split(r"[^a-z]+", left.lower()) if t)
    right_tokens = sorted(t for t in re.split(r"[^a-z]+", right.lower()) if t)
    return bool(left_tokens) and left_tokens == right_tokens


def _table_rows(table: Any) -> list[Mapping[str, Any]]:
    """Accept `load_evaluation_table()` or a bare list of its rows."""
    if isinstance(table, Mapping):
        rows = table.get("rows")
        return [r for r in rows if isinstance(r, Mapping)] if isinstance(rows, Sequence) else []
    if isinstance(table, Sequence) and not isinstance(table, (str, bytes)):
        return [r for r in table if isinstance(r, Mapping)]
    return []


def _select_advisor_rows(
    rows: Sequence[Mapping[str, Any]], pi_name: str
) -> tuple[list[Mapping[str, Any]], str, str | None]:
    """
    Which rows belong to this PI, and how sure that is.

    Three outcomes, kept apart on purpose, and the same three
    `theses._select_advisor_rows` produces. `matched` means at least one row's
    advisor cell joined to `pi_name`. `unverified_single_advisor` means the file
    carries exactly one advisor and it did not join — the usual case for a
    Chinese collection read against a romanised `pi_name`, where the file is
    almost certainly right and the tool cannot confirm it. `refused` means
    several advisors are present and none joined, where printing any subset
    would be attributing somebody else's reviews to this person.
    """
    matched = [
        row for row in rows
        if any(_same_advisor(str(row.get(key) or ""), pi_name)
               for key in ("advisor", "advisor_latin"))
    ]
    if matched:
        return matched, "matched", None
    if not rows:
        return [], "refused", (
            "the evaluation table is empty, so there is no statement to attribute to anyone."
        )
    distinct = {str(row.get("advisor") or "").strip() for row in rows
                if str(row.get("advisor") or "").strip()}
    if len(distinct) == 1:
        only = next(iter(distinct))
        return list(rows), "unverified_single_advisor", (
            f"no 导师姓名 cell joined to pi_name {pi_name!r}, but the file carries exactly one "
            f"advisor ({only!r}), so every row was taken as theirs. If that file was collected "
            "for someone else, every statement below is about that other person."
        )
    return [], "refused", (
        f"no 导师姓名 cell joined to pi_name {pi_name!r} and the file carries {len(distinct)} "
        f"different advisors, so there is no non-arbitrary way to say which statements are this "
        f"PI's. Advisors seen: {', '.join(sorted(distinct)[:12])}"
        + (" ..." if len(distinct) > 12 else "")
        + ". Fix pi_name, or add a 导师姓名拼音 column."
    )


# ------------------------------------------------------------------
# Join
# ------------------------------------------------------------------


def join_evaluations(table: Any, pi_name: str) -> dict[str, Any]:
    """
    Select one PI's evaluation rows and count them. Pure — no file access.

    `table` accepts `load_evaluation_table()` or a bare list of its rows;
    `pi_name` is the PI as `author_name` spells them.

    What comes back is counts of rows beside their denominators, plus the rows
    themselves in file order, each carrying the source it came from and the day
    that source was read:

    - **`evaluations_total`** — statements attributed to this PI, out of
      `rows_in_file` in the file.
    - **`source_count`** — distinct 评价来源 those statements came from, with
      the per-source counts beside it. Ten statements from one forum thread and
      ten from ten sites are different evidence and are never printed as one
      number.
    - **`year_range`** / **`rows_without_year`** — the span the statements cover
      and how many could not be placed in time at all.
    - **`retrieved_on_range`** — the span of days the pages were read on.

    What deliberately does not come back, per `EVALUATION_STANCE`: any polarity,
    any average, any rating of the advisor, any percentage, and any value that
    Section 16's composite score could consume. `suppressed` here means only
    that no statement could be attributed to this PI — it is never a withheld
    number, because there is no number to withhold.
    """
    rows = _table_rows(table)
    selected, advisor_filter, advisor_note = _select_advisor_rows(rows, pi_name)

    entries = [
        {
            "line": row.get("line"),
            "source": str(row.get("source") or ""),
            "retrieved_on": str(row.get("retrieved_on") or ""),
            "retrieved_on_raw": str(row.get("retrieved_on_raw") or ""),
            "content": str(row.get("content") or ""),
            "rating": str(row.get("rating") or ""),
            "student_role": str(row.get("student_role") or ""),
            "year": row.get("year"),
            "url": str(row.get("url") or ""),
            "flags": list(row.get("flags") or []),
        }
        for row in selected
    ]

    sources = Counter(entry["source"] for entry in entries if entry["source"])
    years = sorted({entry["year"] for entry in entries if entry["year"] is not None})
    dates = sorted({entry["retrieved_on"] for entry in entries if entry["retrieved_on"]})
    roles = Counter(entry["student_role"] for entry in entries if entry["student_role"])

    reasons: list[str] = []
    if advisor_filter == "refused":
        reasons.append(advisor_note or "no row could be attributed to this PI")
    elif not entries:
        reasons.append(
            "the file loaded and carries no usable row for this PI, so there is nothing to print"
        )
    if entries and not dates:
        reasons.append(
            "no row carries a parseable 数据获取日期, so none of these statements can be tied to a "
            "day the page was read"
        )

    provenance = table if isinstance(table, Mapping) else {}
    result = {
        "pi_name": pi_name,
        "advisor_filter": advisor_filter,
        "advisor_note": advisor_note,
        "advisor_names_seen": dict(sorted(Counter(
            str(row.get("advisor") or "") for row in rows if str(row.get("advisor") or "")
        ).items())),
        "rows_in_file": len(rows),
        # R1: the population every count below is over.
        "denominator": len(entries),
        "counts": {
            "evaluations_total": len(entries),
            "rows_in_file": len(rows),
            "source_count": len(sources),
            "rows_with_year": len(entries) - sum(1 for e in entries if e["year"] is None),
            "rows_without_year": sum(1 for e in entries if e["year"] is None),
            "rows_with_url": sum(1 for e in entries if e["url"]),
            "rows_with_rating": sum(1 for e in entries if e["rating"]),
        },
        # File order, which is the order the reader collected them in. Not
        # sorted by anything: any sort here would be a ranking of statements
        # about a person, which is the one thing this section refuses to build.
        "entries": entries,
        "sources": dict(sources.most_common()),
        "student_roles": dict(roles.most_common()),
        "year_range": (years[0], years[-1]) if years else None,
        "retrieved_on_range": (dates[0], dates[-1]) if dates else None,
        "suppressed": bool(reasons),
        "suppressed_reasons": reasons,
        "not_computable": not entries,
        # Printed with the rows, never separated from them: which file they came
        # from, and how much of it was usable.
        "provenance": {
            "path": provenance.get("path"),
            "encoding": provenance.get("encoding"),
            "rows_read": provenance.get("rows_read"),
            "rejected": len(provenance.get("rejected") or []),
            "duplicates_dropped": len(provenance.get("duplicates_dropped") or []),
            "sources_in_file": dict(provenance.get("sources") or {}),
            "retrieved_dates_in_file": list(provenance.get("retrieved_dates") or []),
            "columns_missing_optional": list(provenance.get("columns_missing_optional") or []),
        },
        "stance": EVALUATION_STANCE,
        "limits": EVALUATION_LIMITS,
    }

    logger.info(
        "学生评价对照完成: %d/%d 行归到 %s 名下，来自 %d 个来源（归属判定: %s）",
        len(entries), len(rows), pi_name or "(未指定 PI)", len(sources), advisor_filter,
    )
    for reason in reasons:
        logger.warning("第 20 节无可印内容: %s", reason)
    return result
