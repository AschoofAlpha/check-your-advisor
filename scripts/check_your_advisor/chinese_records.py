"""
中文期刊题录：schema、加载、规整、并入语料
=========================================
The fourth hand-filled table, and the first one that moves a denominator every
other section has already printed.

Why this exists
---------------
This toolkit reads PubMed and OpenAlex. Neither indexes 中文核心期刊 to any
useful depth, so a Chinese medical PI with 12 PubMed records and 40 中文核心
records has had 年产出, 一作名额分布 and 人员流动 computed over roughly a
quarter of their output — and nothing on the page said so. The missing three
quarters are not a rounding error; they are the part of the group's work that
happens to be written in Chinese, and leaving them out biases every one of those
numbers in a direction nobody chose.

**This module makes no network request, and there is no crawler here or anywhere
else in the package.** 知网 and 万方 defend against automated collection and
their terms forbid it, so the division of labour is the one `journals.py`,
`theses.py` and `evaluations.py` already use: the user exports the 题录 by hand,
and this module

  1. defines the table's schema             (`SCHEMA`, `REQUIRED_FIELDS`)
  2. reads and validates a filled-in file   (`load_chinese_records`)
  3. regularises it into paper records      (`to_paper_records`)
  4. folds those into a PubMed corpus       (`merge_chinese_records`)

**Nothing here writes a file.** Not a corpus, not a cache, not a log file — the
module has no write path at all, and a test asserts its source contains none.
That is deliberate and it is the whole of caveat `CNR-03`: a value somebody
typed into a spreadsheet must never end up inside a harvested `papers_*.json`
where it would be indistinguishable from something PubMed returned. Records
produced here carry `source`, `source_db` and `retrieved_on` on every single
one, so wherever they end up a reader can see which came from a hand-filled
table and which day the library was read.

The join is a name join, and 作者拼音 is required here
-----------------------------------------------------
A 知网 export writes 作者 in Chinese characters. PubMed writes the same person
as "Zhu Guangwei". Nothing in the standard library converts one into the other,
and this module will not guess. `theses.py` makes its romanised-name column
optional and then reports every graduate as undecidable when it is absent; the
README has had to tell people to add it by hand ever since. Here it is a
**required column**, because the byline position is the whole point — a Chinese
record whose PI cannot be located in its own byline contributes to 年产出 and to
nothing else.

A required column is not a filled cell. A blank 作者拼音 cell, a pinyin list of
the wrong length, and a byline the PI simply is not on are three different
things, and all three produce a record: the paper is real either way, and
dropping it to tidy up a failed match would remove a publication from the corpus
to make the match statistics look better. Every one of them comes back in
`unmatched` with a reason, a line number and the byline it failed against.

What this cannot do, and says so
--------------------------------
中文题录 carry neither a DOI nor a PMID, so the only merge key available is a
normalised title plus a year. `merge_chinese_records` documents exactly how much
weaker that is than the three-tier key `openalex.py` uses, `DEDUP_KEY_BASIS`
states it as a constant, and `CHINESE_RECORD_LIMITS` carries the rest of what
the export cannot contain.

Standard library only, and it has to stay that way: this module is reachable
from the offline profile report, so it imports nothing that reaches the network
— which rules out `openalex.py` and `pubmed_api.py` and is why `SOURCE_PUBMED`
is re-declared below with a test holding it equal to theirs.
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

# One definition of what a name match is. `theses` owns it because that is where
# the rotation tolerance was argued out, and re-deriving it here is how two
# modules come to disagree about one person without anyone noticing: a graduate
# joined in Section 18 and an author joined here have to reach the same verdict
# on the same pair of names. `_compare_names` and `_comparable` are private to
# `theses`, and imported anyway for that reason — the same call `openalex.py`
# makes when it imports `_MARK_OPENALEX` rather than retyping the string.
from .theses import _comparable, _compare_names, name_script

logger = logging.getLogger(__name__)

__all__ = [
    "AUTHOR_SEPARATORS",
    "CHINESE_RECORD_CAVEATS",
    "CHINESE_RECORD_LIMITS",
    "CORRESPONDING_MARKS",
    "ChineseRecordError",
    "DEDUP_KEY_BASIS",
    "EARLIEST_PLAUSIBLE_YEAR",
    "ENCODINGS",
    "MATCH_RULES",
    "OPTIONAL_KEYS",
    "REQUIRED_FIELDS",
    "ROLE_MARKER",
    "SCHEMA",
    "SOURCE_CHINESE",
    "SOURCE_DB_VALUES",
    "SOURCE_PUBMED",
    "load_chinese_records",
    "merge_chinese_records",
    "to_paper_records",
]


# ------------------------------------------------------------------
# Source names
# ------------------------------------------------------------------

#: What a record produced by this module says about itself. A third value beside
#: `openalex.SOURCE_PUBMED` and `openalex.SOURCE_OPENALEX`, never folded into
#: either: a hand-typed row and an API response are not the same kind of claim.
SOURCE_CHINESE = "chinese-record"

#: Must equal `openalex.SOURCE_PUBMED`. It is re-declared rather than imported
#: because `openalex` reaches the network and this module is reachable from the
#: offline profile report, which may not grow a transport dependency. A test
#: asserts the two strings are equal, which is the part a comment cannot do.
SOURCE_PUBMED = "pubmed"

#: Stamped into the `role` of every record whose PI was located in the byline.
#: It is not one of the four identity markers `pubmed_api` recognises, and that
#: is the point: the evidence behind this match is a pinyin cell somebody typed,
#: which no check in this package can verify. `evidence_tier_from_role` returns
#: "" for it, which is the correct answer.
ROLE_MARKER = "[中文题录·姓名对照未经验证⚠]"


# ------------------------------------------------------------------
# What these records cannot tell you
# ------------------------------------------------------------------
#
# Module constants, printed verbatim, never paraphrased at the call site — the
# register `journals.JOURNAL_CAVEATS` and `theses.ROSTER_LIMITS` established.

CHINESE_RECORD_LIMITS: tuple[tuple[str, str], ...] = (
    (
        "没有 DOI，也没有 PMID",
        "中文期刊题录绝大多数不带 DOI，全部不带 PMID，所以合并时唯一能用的主键是"
        "「归一化标题 + 年份」这一层。openalex.py 有 DOI、PMID、标题+年份三层，这里只有"
        "最弱的那一层，而且它是三层里唯一会判错的一层。",
    ),
    (
        "PubMed 看不见这些刊，所以此前的每个数字都少算了",
        "中文核心期刊绝大部分不被 PubMed 收录。并入之前，本报告的年产出、一作名额分布、"
        "人员流动全部只算在英文那一部分语料上，而页面上看不出来。并入之后这些数字会变，"
        "变的幅度就是这张表的行数——这不是修正了误差，是换了一个分母。",
    ),
    (
        "只有你检索过的那几个库、那几个年份",
        "知网、万方、维普三家收录范围不一样，任何一家都不全。没被你检索到的刊、没被导出的"
        "年份，在这张表里和不存在没有区别，而文件本身不会记录这次检索有多窄。",
    ),
    (
        "繁体题名和简体题名不是同一个键",
        "归一化只做 NFKC 和大小写，不做繁简转换——标准库里没有这个映射，猜一个会把不相干的"
        "两篇并成一篇。所以港澳台期刊的同一篇论文若以繁体题名录入，不会和简体那条去重。",
    ),
    (
        "一篇论文被两种语言各索引一次，这里查不出来",
        "有些中文期刊的论文同时有英文题名进入 PubMed。两条记录的标题是两种语言，"
        "「归一化标题 + 年份」永远对不上，于是同一篇论文在合并后被算作两篇。"
        "这个方向的漏判没有上界，本模块也不去估计它。",
    ),
    (
        "作者拼音是人手填的",
        "拼音列决定了这位导师能不能在自己的署名里被定位。它由填表的人一条条敲进去，"
        "没有任何一步能验证它是对的：姓名顺序、多音字、同名同姓，都只会安静地出错。"
        "所以每条记录的 role 都带着 ROLE_MARKER，从不冒充已验证的身份证据。",
    ),
)

CHINESE_RECORD_CAVEATS: dict[str, str] = {
    "CNR-01": (
        "这张表里的每一行都是读者自己从知网、万方或维普导出来的。本包一条都没有抓取，"
        "也不含任何爬虫：这些库有反爬措施，其使用条款也不允许自动采集。本工具做的是"
        "定义列、读文件、把题录规整成记录、并说明每条来自哪个库、哪一天导出。"
    ),
    "CNR-02": (
        "合并去重的主键只有「归一化标题 + 年份」一层。它会把同名不同年的论文分开，"
        "但分不开同一年里标题恰好相同的两篇，也认不出同一篇论文的中英文两个题名。"
        "疑似重复因此是逐条列出来给人看的，不是一个可以直接引用的结论。"
    ),
    "CNR-03": (
        "本模块不写任何文件。这里产出的记录不会、也不允许被写回 papers_*.json —— "
        "那个文件里的每一条都应当是从 PubMed 或 OpenAlex 取回来的，掺进手填的行之后，"
        "两者在下一次读取时就再也分不开了。要落盘由调用方自己决定，并且必须另存。"
    ),
    "CNR-04": (
        "并入之后必须分别印出三个分母：PubMed 语料几篇、中文题录几篇、合并后几篇，"
        "外加疑似重复几篇。把它们加成一个总数，等于把一个手填的计数和一个检索回来的"
        "计数说成同一种东西。"
    ),
    "CNR-05": (
        "导师本人在署名里定位不到的行，记录照样产出，role 留空，并逐条报出原因。"
        "把这些行丢掉会让对照率好看，代价是从语料里删掉真实存在的论文。"
    ),
}

#: The one sentence that has to travel with every count `merge_chinese_records`
#: produces. Declared here so a renderer holding only the result dict still has
#: it, and so no call site can paraphrase it into something weaker.
DEDUP_KEY_BASIS = (
    "Deduplicated on normalised title + publication year, and on nothing else. Chinese "
    "bibliographic records carry no PMID and usually no DOI, so the DOI and PMID tiers "
    "openalex.py deduplicates on do not exist here. This key both over-merges (two papers "
    "sharing a title in one year become one) and under-merges (one paper indexed in English by "
    "PubMed and in Chinese by 知网 stays two). Every hit is reported as suspected rather than "
    "confirmed, and listed row by row so it can be checked by hand."
)


# ------------------------------------------------------------------
# Schema
# ------------------------------------------------------------------


@dataclass(frozen=True)
class Column:
    """One column of the 题录 table.

    `zh` is what the person filling the file in is most likely to type, because
    the export came out of a Chinese database. `en` and `aliases` are accepted on
    the way back in, so a file kept in English still loads.
    """

    key: str
    zh: str
    en: str
    required: bool = False
    aliases: tuple[str, ...] = ()


SCHEMA: tuple[Column, ...] = (
    # Required — the four that make a record a record, plus the romanisation
    # that makes it joinable and the two that make it traceable.
    Column("title", "篇名", "title", required=True,
           aliases=("题名", "标题", "论文题名", "文献题名", "论文题目", "article_title",
                    "paper_title")),
    Column("authors", "作者", "authors", required=True,
           aliases=("作者姓名", "著者", "全部作者", "署名", "author", "author_list")),
    Column("authors_pinyin", "作者拼音", "authors_pinyin", required=True,
           aliases=("作者姓名拼音", "姓名拼音", "拼音", "作者英文名", "作者罗马字",
                    "authors_latin", "author_latin", "authors_pinyin_list",
                    "romanised_authors", "romanized_authors")),
    Column("journal", "期刊", "journal", required=True,
           aliases=("刊名", "期刊名", "期刊名称", "来源期刊", "文献来源", "journal_name",
                    "source_journal", "publication")),
    Column("pub_year", "发表年份", "pub_year", required=True,
           aliases=("年份", "出版年", "发表年", "见刊年份", "year", "publication_year",
                    "pubyear")),
    Column("source_db", "数据来源", "source_db", required=True,
           aliases=("库来源", "数据库", "来源库", "检索库", "database", "source_database")),
    Column("retrieved_on", "数据获取日期", "retrieved_on", required=True,
           aliases=("获取日期", "导出日期", "导出时间", "检索日期", "采集日期", "下载日期",
                    "exported_on", "export_date", "retrieved", "dateretrieved")),
    # Optional — carried through, printed, and joined on by nothing.
    Column("issn", "ISSN", "issn",
           aliases=("国际标准刊号", "刊号", "印刷版issn", "issn号")),
    Column("doi", "DOI", "doi", aliases=("数字对象标识符", "doi号")),
    Column("volume", "卷", "volume", aliases=("卷号", "vol")),
    Column("issue", "期", "issue", aliases=("期号", "no")),
    Column("pages", "页码", "pages", aliases=("起止页码", "页", "page_range")),
    Column("abstract", "摘要", "abstract", aliases=("中文摘要", "文摘", "summary")),
    Column("index_status", "收录类别", "index_status",
           aliases=("核心类别", "收录情况", "期刊类别", "核心期刊", "indexing",
                    "index_category")),
    Column("institution", "第一作者单位", "institution",
           aliases=("作者单位", "单位", "机构", "第一单位", "affiliation", "first_affiliation")),
    Column("notes", "备注", "notes", aliases=("说明", "note", "remark", "remarks")),
)

REQUIRED_FIELDS: tuple[str, ...] = tuple(c.key for c in SCHEMA if c.required)
OPTIONAL_KEYS: tuple[str, ...] = tuple(c.key for c in SCHEMA if not c.required)

_BY_KEY: dict[str, Column] = {c.key: c for c in SCHEMA}

#: `其他` is a real bucket, not a failure: a record from a database neither of
#: the three named ones covers is still a paper. Unrecognised text maps there and
#: the raw string is kept beside it.
SOURCE_DB_VALUES: tuple[str, ...] = ("知网", "万方", "维普", "其他")

_SOURCE_DB_ALIASES: dict[str, str] = {
    "知网": "知网", "中国知网": "知网", "cnki": "知网", "cnkinet": "知网",
    "中国期刊全文数据库": "知网", "中国学术期刊网络出版总库": "知网",
    "万方": "万方", "万方数据": "万方", "wanfang": "万方", "wanfangdata": "万方",
    "维普": "维普", "维普资讯": "维普", "cqvip": "维普", "vip": "维普",
    "中文科技期刊数据库": "维普",
    "其他": "其他", "other": "其他",
}

#: A 中文核心期刊 record older than this is a parse artefact rather than a very
#: old paper: the databases this table is exported from do not hold an advisor's
#: own byline from before it. Rows outside the range are kept and flagged, never
#: dropped — a wrong year is still a real paper.
EARLIEST_PLAUSIBLE_YEAR = 1949

#: What the 作者 and 作者拼音 cells may be split on. Chinese exports use all of
#: these and are not consistent even within one file.
AUTHOR_SEPARATORS: tuple[str, ...] = (";", "；", ",", "，", "、", "|", "｜")

#: How a Chinese export marks the corresponding author. There is no dedicated
#: column for it in any of the three databases; the star is appended to the name.
CORRESPONDING_MARKS: tuple[str, ...] = ("*", "＊", "#", "＃", "†", "‡")

#: How each name-join outcome is treated, returned with every join result so a
#: report can print the rule beside the count instead of asking for trust.
MATCH_RULES: tuple[tuple[str, str, str], ...] = (
    ("exact", "counted as a match",
     "Identical Chinese characters in 作者, or identical romanised tokens in 作者拼音."),
    ("name_form", "counted as a match",
     "Romanised tokens agree under rotation: surname-first against surname-last, or a given "
     "name written as one token against the same name written as two."),
    ("partial_name", "reported, record kept, role left empty",
     "A byline entry carries less of the name than `pi_name` does and does not contradict it — "
     "a bare surname, or an initial where a given name should be. On Chinese surnames that "
     "covers dozens of people, so it is never counted as a match; the record is kept and the "
     "row is listed for a human to settle."),
    ("ambiguous", "reported, record kept, role left empty",
     "More than one byline entry reached match level. Both positions are listed rather than "
     "the first one being taken."),
    ("no_author_matched", "reported, record kept, role left empty",
     "The byline was comparable with `pi_name` and nobody on it is this person. Usually a row "
     "that belongs to somebody else; occasionally a romanisation spelled differently."),
    ("undecidable_script", "reported, record kept, role left empty",
     "The 作者拼音 cell is empty and `pi_name` is romanised, so the two are written in "
     "different scripts and cannot be compared at all. Filling that cell is the entire fix, "
     "and it is why the column is required."),
)

_ACCEPTED_LEVELS = frozenset({"exact", "name_form"})


class ChineseRecordError(ValueError):
    """A 中文题录 table that cannot be loaded. Subclasses ValueError on purpose,
    for the reason `journals.JournalTableError` does: callers already written as
    `except ValueError` keep working, and a caller that wants to tell a bad table
    from a bad number can catch this one."""


# ------------------------------------------------------------------
# Normalisation
# ------------------------------------------------------------------

_HEADER_NOISE_RE = re.compile(r"[\s_\-·.()（）\[\]【】:：/、]+")

_BLANK_WORDS = frozenset({"", "-", "--", "na", "n/a", "nan", "none", "null", "暂无", "无", "未填"})

_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d", "%Y年%m月%d日")

#: Everything a title key drops. The CJK ranges are the load-bearing half:
#: `openalex._title_year_key` strips every character outside `[a-z0-9]`, which
#: reduces any Chinese title to the empty string — and a record with an empty key
#: deduplicates against nothing at all. That is why this module has its own key
#: function instead of reusing the one next door. Written as escapes rather than
#: as literal characters because it is a range, and two visually identical CJK
#: glyphs at its endpoints would move it silently.
_TITLE_NOISE_RE = re.compile(r"[^0-9a-z㐀-䶿一-鿿豈-﫿]")

#: Excel on a Chinese Windows writes CSV as GBK; everything else writes UTF-8,
#: usually with a BOM. Both are tried, **in this order**, and the order is the
#: whole safeguard: gb18030 decodes very nearly any byte sequence without
#: complaining, so trying it first would turn every UTF-8 file into mojibake and
#: report success. Whichever codec worked comes back in the result, because a
#: decoding fallback nobody recorded is a silent degrade — and a mojibake name is
#: a real author who will never join the PubMed roster.
ENCODINGS: tuple[str, ...] = ("utf-8-sig", "gb18030")


def _clean(value: Any) -> str:
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


def _parse_year(value: Any) -> int | None:
    match = re.search(r"(\d{4})", _fold(value))
    return int(match.group(1)) if match else None


def _parse_date(value: Any) -> str:
    """`YYYY-MM-DD` when the cell holds a full date, otherwise "".

    A bare year or month is not a day, and returning one would let a row claim a
    precision the cell does not carry. The raw text is kept beside the parsed
    value by the caller, and the row is flagged rather than rejected: an
    unreadable export date is a real record with a bad cell.
    """
    folded = _fold(value)
    if _is_blank(folded):
        return ""
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(folded, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def _source_db(value: Any) -> str:
    folded = _HEADER_NOISE_RE.sub("", _fold(value)).lower()
    return _SOURCE_DB_ALIASES.get(folded, "其他")


_SEPARATOR_RE = re.compile("[" + re.escape("".join(AUTHOR_SEPARATORS)) + "]")
_MARK_RE = re.compile("[" + re.escape("".join(CORRESPONDING_MARKS)) + "]+$")


def _split_names(value: Any) -> tuple[list[str], list[bool]]:
    """Split a byline cell into names, and say which ones carried a star.

    Returns `(names, corresponding)` of equal length. The mark is stripped off
    the name rather than kept in it: a name ending in `*` joins nothing, and
    leaving it on would turn a matchable author into an unmatched one for the
    sake of a punctuation character.
    """
    names: list[str] = []
    corresponding: list[bool] = []
    for part in _SEPARATOR_RE.split(str(value or "")):
        cleaned = part.strip()
        if not cleaned:
            continue
        stripped = _MARK_RE.sub("", cleaned).strip()
        if not stripped:
            continue
        names.append(stripped)
        corresponding.append(stripped != cleaned)
    return names, corresponding


def _split_latin(name: str) -> tuple[str, str]:
    """(last, fore) for a romanised byline entry.

    The first token is taken as the surname, which is what 知网 and 万方 write
    and what `pubmed_api._author_record` produces on the other side ("Zhu
    Guangwei"). A cell written given-name-first puts the halves the wrong way
    round in `last` / `fore` — and *only* there: the PI join runs through
    `theses._compare_names`, which is rotation-tolerant, so whether this module
    finds the advisor never depends on the order the cell was typed in.
    """
    tokens = [token for token in re.split(r"\s+", _fold(name)) if token]
    if not tokens:
        return "", ""
    return tokens[0], " ".join(tokens[1:])


def _title_year_key(title: Any, year: Any) -> str:
    """Normalised title plus year — the only key this module has.

    NFKC first, so a title typed with full-width Latin letters or digits folds to
    the same key as one typed with ASCII. Punctuation, spacing and case are
    dropped because the three databases typeset a title differently and a comma
    is not a different paper. What is deliberately *not* done is 繁简转换: the
    standard library has no such mapping, and guessing one would merge two
    unrelated papers.

    Returns "" for a title with no alphanumeric or CJK content at all. An empty
    key is never indexed — otherwise every untitled record would collide with
    every other untitled record.
    """
    normalised = _TITLE_NOISE_RE.sub("", unicodedata.normalize("NFKC", str(title or "")).lower())
    if not normalised:
        return ""
    return f"zhty:{normalised}|{_clean(year)}"


# ------------------------------------------------------------------
# Loading
# ------------------------------------------------------------------


def _decode(path: Path) -> tuple[str, str]:
    """Read the file as text; return `(text, encoding)`.

    Raises `ChineseRecordError` — never a bare `UnicodeDecodeError` from the
    middle of a loop — because the user's next action is a Save As in Excel and
    a codec traceback does not say that.
    """
    data = path.read_bytes()
    for encoding in ENCODINGS:
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ChineseRecordError(
        f"中文题录表无法解码: {path}（已尝试 {'、'.join(ENCODINGS)}）。"
        "请在 Excel 里另存为 UTF-8 CSV 后重试。"
        "再猜第三种编码只会把这张表赖以对照的人名弄坏，所以这里不猜。"
    )


def _header_map(header: Sequence[str], path: Path) -> tuple[dict[int, str], list[str]]:
    """Map column positions to schema keys; refuse a header missing a required column.

    Two columns folding to the same field is refused rather than resolved:
    either could be the one the user meant, and picking silently would file one
    paper's metadata under another's column.
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
            raise ChineseRecordError(
                f"中文题录表表头有两列都对应字段 {key}: 第 {seen[key] + 1} 列与"
                f"第 {position + 1} 列（{path}）。请删掉其中一列后重试。"
            )
        seen[key] = position
        mapping[position] = key

    found = "、".join(_clean(cell) for cell in header if _clean(cell)) or "(无)"
    missing = [key for key in REQUIRED_FIELDS if key not in seen]
    if missing:
        wanted = "、".join(f"{_BY_KEY[k].zh}（或 {_BY_KEY[k].en}）" for k in missing)
        why = ""
        if {"source_db", "retrieved_on"} & set(missing):
            why += (
                "缺数据来源或数据获取日期时，表里每一条题录过后都无法追溯是从哪个库、"
                "哪一天导出来的，因此不接受这张表——这与期刊表缺版本来源是同一条规矩。"
            )
        if "authors_pinyin" in missing:
            why += (
                "缺作者拼音时，中文署名和 PubMed 的罗马化署名根本无法比对，"
                "导师本人是不是这篇的第一作者就只能靠猜——这一列是这张表能不能用的分水岭，"
                "所以它在这里是必填，而不像毕业名单里那样可选。"
            )
        raise ChineseRecordError(
            f"中文题录表缺少必需列: {wanted}（{path}）。{why}表头读到的列: {found}。"
        )
    return mapping, unknown


def _parse_row(cells: Sequence[str], mapping: Mapping[int, str], line: int) -> dict[str, Any]:
    """One CSV line to one row, with the raw text kept wherever parsing can fail."""
    values = {key: "" for key in _BY_KEY}
    for position, key in mapping.items():
        if position < len(cells):
            values[key] = _clean(cells[position])

    names, name_marks = _split_names(values["authors"])
    pinyin, pinyin_marks = _split_names(values["authors_pinyin"])

    flags: list[str] = []
    if not pinyin:
        flags.append("pinyin_missing")
    elif len(pinyin) != len(names):
        flags.append("author_count_mismatch")

    # Padded at the end, never truncated: a byline of three with two
    # romanisations is two names that can be checked and one that cannot, and
    # cutting the list to fit would delete an author to make the arrays line up.
    width = max(len(names), len(pinyin))
    names += [""] * (width - len(names))
    pinyin += [""] * (width - len(pinyin))
    name_marks += [False] * (width - len(name_marks))
    pinyin_marks += [False] * (width - len(pinyin_marks))
    corresponding = [bool(a or b) for a, b in zip(name_marks, pinyin_marks, strict=True)]

    year = _parse_year(values["pub_year"])
    if year is not None and not (EARLIEST_PLAUSIBLE_YEAR <= year <= datetime.now().year + 1):
        flags.append("pub_year_out_of_range")
    retrieved_on = _parse_date(values["retrieved_on"])
    if not retrieved_on:
        flags.append("retrieved_on_unparseable")
    source_db_raw = values["source_db"]
    source_db = _source_db(source_db_raw)
    if source_db == "其他" and _norm_header(source_db_raw) not in {"其他", "other"}:
        flags.append("source_db_unrecognised")

    return {
        "line": line,
        "title": values["title"],
        "authors": names,
        "authors_raw": values["authors"],
        "authors_pinyin": pinyin,
        "authors_pinyin_raw": values["authors_pinyin"],
        "corresponding": corresponding,
        "journal": values["journal"],
        "pub_year": year,
        "pub_year_raw": values["pub_year"],
        "issn": values["issn"],
        "doi": values["doi"],
        "volume": values["volume"],
        "issue": values["issue"],
        "pages": values["pages"],
        "abstract": values["abstract"],
        "index_status": values["index_status"],
        "institution": values["institution"],
        "notes": values["notes"],
        "source_db": source_db,
        "source_db_raw": source_db_raw,
        "retrieved_on": retrieved_on,
        "retrieved_on_raw": values["retrieved_on"],
        "flags": flags,
    }


def load_chinese_records(path: str | Path) -> dict[str, Any]:
    """
    Read a hand-exported 中文期刊题录 table and validate it against `SCHEMA`.

    One CSV, one row per paper. Headers may be written in Chinese or English —
    every spelling in `SCHEMA[*].aliases` is accepted — and are matched after
    case, spacing and punctuation are removed, so `作者拼音`, `作者 拼音` and
    `authors_latin` are the same column.

    Refused rather than degraded:

    - a missing required column (`篇名`, `作者`, `作者拼音`, `期刊`, `发表年份`,
      `数据来源`, `数据获取日期`), named individually in the error together with
      the headers that were found. `数据来源` and `数据获取日期` are required for
      the reason the journal table needs an edition column: a count with no
      recorded origin cannot be re-checked two years later. `作者拼音` is
      required because without it a Chinese byline cannot be compared to a
      romanised `pi_name` at all, and the whole join collapses into "undecidable"
      — see `CHINESE_RECORD_LIMITS`.
    - two columns folding to the same field, and a file that decodes as neither
      UTF-8 nor GB18030. The second raises `ChineseRecordError` naming both
      codecs, never a bare `UnicodeDecodeError`.

    Kept but recorded, because a malformed cell is still a real paper:

    - an out-of-range `发表年份`, an unparseable `数据获取日期`, a `数据来源`
      outside `SOURCE_DB_VALUES`, an empty `作者拼音` cell and a pinyin list of a
      different length to the byline all become per-row `flags`, with the raw
      text preserved beside the parsed value.
    - a row with no 篇名, no readable 发表年份 or no 作者 cannot be keyed,
      placed in time or attributed, so it goes to `rejected` with its line number
      rather than being dropped silently.
    - duplicates on (normalised title, year, 数据来源) are dropped once and
      listed: that is one record exported twice from one library. The *same*
      paper held by 知网 **and** 万方 is deliberately **not** dropped here — two
      libraries independently holding it is information, and collapsing it is
      `merge_chinese_records`' job, where it is counted under
      `chinese_internal_duplicates`.

    Returns a dict with `rows`, `rejected`, `duplicates_dropped`, the
    `denominator` those rows were counted over, `columns_used` mapping each
    schema key to the header as actually written, `columns_missing_optional`,
    `unknown_columns`, the `encoding` that worked, and the provenance summaries
    `source_dbs`, `retrieved_dates` and `year_range` that the report must print.
    `limits` carries `CHINESE_RECORD_LIMITS` so a renderer holding only this dict
    still has the text describing what the export cannot contain.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"中文题录表不存在: {path}")

    text, encoding = _decode(path)
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration:
        raise ChineseRecordError(f"中文题录表是空文件: {path}") from None

    mapping, unknown_columns = _header_map(header, path)
    columns_used = {key: _clean(header[position]) for position, key in mapping.items()}

    rows: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    rows_read = 0

    for line, cells in enumerate(reader, start=2):
        if not any(_clean(cell) for cell in cells):
            continue
        rows_read += 1
        row = _parse_row(cells, mapping, line)
        if not row["title"]:
            rejected.append({"line": line, "reason": "没有篇名，这一条没有任何可用的主键"})
            continue
        if row["pub_year"] is None:
            rejected.append({
                "line": line,
                "reason": f"发表年份读不出来: {row['pub_year_raw']!r}，无法放进任何年度计数",
            })
            continue
        if not any(row["authors"]) and not any(row["authors_pinyin"]):
            rejected.append({"line": line, "reason": "作者一栏是空的，这一条归不到任何人名下"})
            continue
        key = (_title_year_key(row["title"], row["pub_year"]), row["source_db"])
        if key in seen:
            duplicates.append({
                "line": line, "title": row["title"],
                "pub_year": row["pub_year"], "source_db": row["source_db"],
            })
            continue
        seen.add(key)
        rows.append(row)

    years = sorted({row["pub_year"] for row in rows if row["pub_year"] is not None})
    dates = sorted({row["retrieved_on"] for row in rows if row["retrieved_on"]})
    flag_counts = dict(sorted(Counter(f for row in rows for f in row["flags"]).items()))

    logger.info(
        "中文题录表已读入: %s (%s 编码) — %d 行可用, %d 行被拒, %d 行重复, 来自 %d 个库",
        path, encoding, len(rows), len(rejected), len(duplicates),
        len({row["source_db"] for row in rows}),
    )
    for flag, count in flag_counts.items():
        logger.warning("  %d 行带标记 %s（已保留，未丢弃）", count, flag)
    if unknown_columns:
        logger.info("中文题录表有本工具不认识的列，已忽略: %s", "、".join(unknown_columns))

    return {
        "path": str(path),
        "encoding": encoding,
        "rows": rows,
        "rejected": rejected,
        "duplicates_dropped": duplicates,
        # R1: the population every count downstream is taken over.
        "denominator": len(rows),
        "rows_read": rows_read,
        "columns_used": columns_used,
        "columns_missing_optional": [k for k in OPTIONAL_KEYS if k not in columns_used],
        "unknown_columns": unknown_columns,
        "flag_counts": flag_counts,
        "source_dbs": dict(sorted(Counter(row["source_db"] for row in rows).items())),
        "retrieved_dates": dates,
        "retrieved_on_range": (dates[0], dates[-1]) if dates else None,
        "year_range": (years[0], years[-1]) if years else None,
        "limits": CHINESE_RECORD_LIMITS,
    }


# ------------------------------------------------------------------
# Regularisation — one row, one paper record
# ------------------------------------------------------------------


def _table_rows(table: Any) -> list[Mapping[str, Any]]:
    """Accept `load_chinese_records()` or a bare list of its rows."""
    if isinstance(table, Mapping):
        rows = table.get("rows")
        return [r for r in rows if isinstance(r, Mapping)] if isinstance(rows, Sequence) else []
    if isinstance(table, Sequence) and not isinstance(table, (str, bytes)):
        return [r for r in table if isinstance(r, Mapping)]
    return []


def _locate_pi(
    names: Sequence[str], pinyin: Sequence[str], pi_name: str
) -> tuple[int | None, str, list[str]]:
    """
    Which byline position is the PI, and how sure that is.

    Returns `(index, outcome, candidates)`. `outcome` is one of the six names in
    `MATCH_RULES`; `index` is set only when exactly one position reached `exact`
    or `name_form`. Every other outcome leaves the index unset and the caller
    reports the row — nothing uncertain is quietly pushed into a byline slot,
    because a wrong slot here becomes a wrong 一作名额 in Section 7.

    Both cells are tried for every position: a Han `pi_name` joins 作者 and a
    romanised one joins 作者拼音, and neither is preferred over the other.
    """
    accepted: list[tuple[int, str]] = []
    weak: list[tuple[int, str]] = []
    incomparable = 0
    for index in range(max(len(names), len(pinyin))):
        variants = [
            value for value in (
                names[index] if index < len(names) else "",
                pinyin[index] if index < len(pinyin) else "",
            ) if value
        ]
        if not variants:
            continue
        if not any(_comparable(value, pi_name) for value in variants):
            incomparable += 1
            continue
        best: tuple[int, str] | None = None
        weakest: tuple[int, str] | None = None
        for value in variants:
            level = _compare_names(value, pi_name)
            if level in _ACCEPTED_LEVELS:
                best = (index, value)
                break
            if level == "partial_name" and weakest is None:
                weakest = (index, value)
        if best is not None:
            accepted.append(best)
        elif weakest is not None:
            weak.append(weakest)

    if len(accepted) == 1:
        return accepted[0][0], "exact", [accepted[0][1]]
    if len(accepted) > 1:
        return None, "ambiguous", [value for _index, value in accepted]
    if weak:
        return None, "partial_name", [value for _index, value in weak]
    if incomparable:
        return None, "undecidable_script", []
    return None, "no_author_matched", []


def _author_record(
    name_zh: str, name_latin: str, affiliation: str, is_corresponding: bool
) -> dict[str, Any]:
    """One byline entry in `pubmed_api._author_record`'s shape.

    `name` is the romanisation when the cell carries one, because that is what
    the PubMed-side roster is written in and what any later join keys on. It
    falls back to the Chinese name rather than to "": an empty byline entry is
    useless to every consumer, and `name_zh` plus `theses.name_script` let a
    reader see which script they are looking at.

    The fields OpenAlex and PubMed genuinely carry and this table does not —
    `email`, `orcid`, `equal_contrib`, `openalex_author_id` — are present and
    empty rather than absent, so a merged corpus has one shape. They are never
    filled with something plausible: an empty `email` is why a Chinese record can
    never satisfy the email-domain identity check, which is true and has to stay
    visible.
    """
    name = name_latin or name_zh
    last, fore = _split_latin(name_latin) if name_latin else (name_zh, "")
    return {
        "name": name,
        "name_zh": name_zh,
        "last": last,
        "fore": fore,
        "initials": (fore[:1] or "").upper(),
        "affiliation": affiliation,
        "is_corresponding": is_corresponding,
        "email": "",
        "orcid": "",
        "equal_contrib": False,
        "openalex_author_id": "",
    }


def to_paper_records(table: Any, pi_name: str) -> dict[str, Any]:
    """
    Regularise 中文题录 rows into records shaped like the ones in `papers_*.json`.

    `table` accepts `load_chinese_records()` or a bare list of its rows;
    `pi_name` is the PI as `author_name` spells them, in either script.

    Every key a PubMed record carries is produced, including the identifiers this
    table does not have: `pmid` and `pmc_id` come back as empty strings rather
    than being left out, so a merged corpus has one shape and a consumer never
    has to ask which source a record came from before reading a field. Four
    fields are added that a PubMed record has no use for — `source_db`,
    `retrieved_on`, `language` and `source_line` — and the first two are on
    **every** record, because that is the only way a hand-typed row stays
    distinguishable from a fetched one after it has been merged.

    **Nothing is dropped.** `counts["papers_out"]` always equals
    `counts["rows_in"]`. A row whose byline the PI could not be located in still
    becomes a record, with `role` left empty, and is listed in `unmatched` with
    its line number, its title, the byline in both scripts, the outcome from
    `MATCH_RULES` and — for the undecidable case — the note naming the column
    that would settle it. Deleting those rows would shrink the corpus to improve
    a match rate, which is the wrong trade in the wrong direction.

    `role` is stamped from the byline position the way `pubmed_api` does it:
    first slot, last slot and a starred name become 第一作者 / 末位作者 /
    通讯作者. A PI in the middle of a byline holds no lead slot and gets an empty
    role — that is a successful match with nothing to record, not a failure, and
    the row is not reported. Every non-empty role carries `ROLE_MARKER`, which is
    none of the four identity markers `pubmed_api.evidence_tier_from_role`
    recognises: the evidence here is a pinyin cell somebody typed, and claiming a
    verified tier for it would launder a hand entry into a checked one.

    Returns `{"papers", "unmatched", "counts", "match_reasons", "match_rules",
    "pi_name", "limits"}`. Pure — dicts in, dict out, no file access.
    """
    rows = _table_rows(table)
    pi_name = _clean(pi_name)

    papers: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()

    for row in rows:
        names = [str(n or "") for n in (row.get("authors") or [])]
        pinyin = [str(n or "") for n in (row.get("authors_pinyin") or [])]
        marks = list(row.get("corresponding") or [])
        marks += [False] * (max(len(names), len(pinyin)) - len(marks))
        institution = str(row.get("institution") or "")

        authors = [
            _author_record(
                names[index] if index < len(names) else "",
                pinyin[index] if index < len(pinyin) else "",
                institution if index == 0 else "",
                bool(marks[index]) if index < len(marks) else False,
            )
            for index in range(max(len(names), len(pinyin)))
        ]

        index, outcome, candidates = _locate_pi(names, pinyin, pi_name)
        role = ""
        if index is None:
            reasons[outcome] += 1
            entry = {
                "line": row.get("line"),
                "title": str(row.get("title") or ""),
                "authors": names,
                "authors_pinyin": pinyin,
                "reason": outcome,
                "candidates": candidates,
                "note": "",
            }
            if outcome == "undecidable_script":
                entry["note"] = (
                    f"这一行的署名是 {name_script(next((n for n in names if n), ''))} 文字，"
                    f"pi_name {pi_name!r} 是另一种文字，两者无法比较。"
                    "把这一行的作者拼音填上就能判定——这也是这一列为什么是必填。"
                )
            unmatched.append(entry)
        else:
            reasons["matched"] += 1
            slots: list[str] = []
            if index == 0:
                slots.append("第一作者")
            if index == len(authors) - 1:
                slots.append("末位作者")
            if authors[index]["is_corresponding"]:
                slots.append("通讯作者")
            if slots:
                role = " / ".join(slots) + f" {ROLE_MARKER}"

        year = row.get("pub_year")
        year_text = "" if year is None else str(year)
        papers.append({
            # The PubMed shape, with this table's blanks left visibly blank.
            "pmid": "",
            "title": str(row.get("title") or ""),
            "authors_str": ", ".join(a["name"] for a in authors if a["name"]),
            "authors": authors,
            "journal": str(row.get("journal") or ""),
            "issn": str(row.get("issn") or ""),
            "issn_type": "",
            "issn_linking": "",
            "journal_abbrev": "",
            "pub_date": year_text,
            "pub_year": year_text,
            "volume": str(row.get("volume") or ""),
            "issue": str(row.get("issue") or ""),
            "pages": str(row.get("pages") or ""),
            "doi": str(row.get("doi") or ""),
            "pmc_id": "",
            "abstract": str(row.get("abstract") or ""),
            "role": role,
            # Provenance, on every record without exception. CNR-03.
            "source": SOURCE_CHINESE,
            "confirmed_by": [SOURCE_CHINESE],
            "source_db": str(row.get("source_db") or ""),
            "source_dbs": [str(row.get("source_db") or "")] if row.get("source_db") else [],
            "retrieved_on": str(row.get("retrieved_on") or ""),
            "language": "zh",
            "index_status": str(row.get("index_status") or ""),
            "source_line": row.get("line"),
        })

    matched = reasons.get("matched", 0)
    logger.info(
        "中文题录规整完成: %d 行 → %d 条记录，其中 %d 条定位到 %s 的署名位置，"
        "%d 条定位不到（已逐条报出，未丢弃）",
        len(rows), len(papers), matched, pi_name or "(未指定 PI)", len(unmatched),
    )
    for reason, count in sorted(reasons.items()):
        if reason != "matched":
            logger.warning("  %d 行因 %s 未能定位署名位置", count, reason)

    return {
        "pi_name": pi_name,
        "papers": papers,
        "unmatched": unmatched,
        "counts": {
            "rows_in": len(rows),
            "papers_out": len(papers),
            "pi_matched": matched,
            "pi_unmatched": len(unmatched),
        },
        "match_reasons": {k: v for k, v in sorted(reasons.items()) if k != "matched"},
        "match_rules": MATCH_RULES,
        "limits": CHINESE_RECORD_LIMITS,
    }


# ------------------------------------------------------------------
# Merge
# ------------------------------------------------------------------


def _fold_chinese(donor: Mapping[str, Any], into: dict[str, Any]) -> None:
    """Fold a duplicate Chinese copy into the record that keeps the slot.

    The surviving record keeps its own title, journal and byline. It takes three
    things the donor can strictly add: the sources that independently hold the
    paper, the libraries it was found in, and a DOI where it had none. A PubMed
    record's affiliation strings and corresponding-author emails are what every
    identity check downstream reads, so replacing it with the Chinese copy would
    turn a verifiable record into an unverifiable one while appearing to add
    data — the same argument `openalex.merge_corpora` makes for the same reason.
    """
    sources = set(into.get("confirmed_by") or []) | set(donor.get("confirmed_by") or [])
    into["confirmed_by"] = sorted(sources)
    libraries = set(into.get("source_dbs") or []) | set(donor.get("source_dbs") or [])
    if donor.get("source_db"):
        libraries.add(str(donor["source_db"]))
    if libraries:
        into["source_dbs"] = sorted(libraries)
    if not _clean(into.get("doi")) and _clean(donor.get("doi")):
        into["doi"] = _clean(donor.get("doi"))


def merge_chinese_records(
    pubmed_papers: Sequence[Mapping[str, Any]],
    chinese_papers: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Fold a 中文题录 corpus into a PubMed one, and say what came from where.

    Returns `{"papers", "counts", "suspected_duplicate_pairs",
    "chinese_internal_duplicate_pairs", "dedup_basis", "limits"}`.

    **The key, and how much weaker it is than the one next door.**
    `openalex.merge_corpora` matches on DOI, then PMID, then normalised title +
    year — three tiers, the first two exact identifiers that both databases
    publish. Chinese bibliographic records carry no PMID at all and usually no
    DOI, so those two tiers do not exist here and this merge runs on **one tier**
    only: normalised title + year, and nothing else. Even where a 题录 row
    happens to carry a hand-typed DOI it is carried onto the record and not used
    as a key, because a key present on some rows and absent on others makes the
    result depend on which cells somebody got round to filling in, and the report
    could then not state one dedup rule.

    That one tier fails in both directions and neither failure is estimable here:

    - **false positive** — two different papers sharing a title within one year
      (a conference series, an annual review, a common clinical topic) merge into
      one, and a real paper disappears from the count.
    - **false negative** — one paper indexed by PubMed under an English title and
      by 知网 under its Chinese title has two titles in two languages, so the key
      never matches and the paper is counted twice. Chinese journals with English
      abstracts in PubMed are exactly this case.

    Because of that, a cross-source hit is reported as **suspected**, never as
    confirmed, and every one is listed individually in
    `suspected_duplicate_pairs` with its title, year, the PubMed record it was
    folded into and the line of the 题录 file it came from. A count alone would
    be unusable: the reader has to look at the titles.

    **Four numbers, never one.** `counts` reports `pubmed_total`,
    `chinese_total` and `merged_total` as three separate denominators, plus
    `suspected_duplicates` — and they are never added together or collapsed into
    a single "total". A harvested count and a hand-typed count are different
    kinds of claim, and a reader who sees one number cannot tell which of the two
    moved. `chinese_internal_duplicates` is kept apart from
    `suspected_duplicates` for the same reason `openalex.py` separates
    `openalex_internal_duplicates` from `matched_on`: 知网 and 万方 both holding
    one paper is the table deduplicating against itself, and printing it beside a
    cross-source hit would sell same-source dedup as two databases agreeing.

    The PubMed corpus keeps its order and the Chinese-only records follow it, so
    a reader diffing this against the PubMed-only run sees additions and no
    movement. Neither input list is mutated.
    """
    merged: list[dict[str, Any]] = []
    index: dict[str, int] = {}

    for paper in pubmed_papers:
        record = dict(paper)
        record["source"] = record.get("source") or SOURCE_PUBMED
        record["confirmed_by"] = list(record.get("confirmed_by") or [SOURCE_PUBMED])
        position = len(merged)
        merged.append(record)
        key = _title_year_key(record.get("title"), record.get("pub_year"))
        if key:
            index.setdefault(key, position)

    # Everything below this index came out of `pubmed_papers`. It is what decides
    # whether a hit is a cross-source suspicion or the table's own duplicate, and
    # it is a position rather than a reading of the `source` field, which a
    # re-merged corpus can already have set.
    pubmed_count = len(merged)

    suspected: list[dict[str, Any]] = []
    internal: list[dict[str, Any]] = []
    added = 0

    for paper in chinese_papers:
        key = _title_year_key(paper.get("title"), paper.get("pub_year"))
        target = index.get(key) if key else None
        if target is None:
            record = dict(paper)
            record["source"] = record.get("source") or SOURCE_CHINESE
            record["confirmed_by"] = list(record.get("confirmed_by") or [SOURCE_CHINESE])
            record["source_dbs"] = list(
                record.get("source_dbs")
                or ([record["source_db"]] if record.get("source_db") else [])
            )
            position = len(merged)
            merged.append(record)
            added += 1
            if key:
                index.setdefault(key, position)
            continue

        existing = merged[target]
        detail = {
            "key": key,
            "title": str(paper.get("title") or ""),
            "pub_year": _clean(paper.get("pub_year")),
            "chinese_line": paper.get("source_line"),
            "source_db": str(paper.get("source_db") or ""),
        }
        if target < pubmed_count:
            detail["pubmed_pmid"] = str(existing.get("pmid") or "")
            detail["pubmed_title"] = str(existing.get("title") or "")
            suspected.append(detail)
        else:
            detail["lines"] = [existing.get("source_line"), paper.get("source_line")]
            detail["source_dbs"] = sorted({
                str(existing.get("source_db") or ""), str(paper.get("source_db") or ""),
            } - {""})
            internal.append(detail)
        _fold_chinese(paper, existing)

    both = sum(1 for record in merged if len(record["confirmed_by"]) > 1)
    counts = {
        # Three denominators, three keys. CNR-04: never summed into one.
        "pubmed_total": len(pubmed_papers),
        "chinese_total": len(chinese_papers),
        "merged_total": len(merged),
        "pubmed_only": len(pubmed_papers) - both,
        # A Chinese-created record can never gain PubMed confirmation — only the
        # other direction folds — so every record this loop added is Chinese-only.
        "chinese_only": added,
        "both": both,
        "suspected_duplicates": len(suspected),
        "chinese_internal_duplicates": len(internal),
    }

    logger.info(
        "语料合并：PubMed %d 篇 + 中文题录 %d 篇 → 合并后 %d 篇"
        "（两源共有 %d 篇，仅 PubMed %d 篇，仅中文题录 %d 篇；"
        "跨源疑似重复 %d 篇——只凭标题+年份判定，必须逐条人工核；"
        "中文题录内部自重复 %d 篇——同一篇论文被两个库各收一次，不构成任何跨源确认）",
        counts["pubmed_total"], counts["chinese_total"], counts["merged_total"],
        counts["both"], counts["pubmed_only"], counts["chinese_only"],
        counts["suspected_duplicates"], counts["chinese_internal_duplicates"],
    )

    return {
        "papers": merged,
        "counts": counts,
        "suspected_duplicate_pairs": suspected,
        "chinese_internal_duplicate_pairs": internal,
        "dedup_basis": DEDUP_KEY_BASIS,
        "limits": CHINESE_RECORD_LIMITS,
    }
