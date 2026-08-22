"""
Degree theses: the one source that carries the denominator PubMed cannot.

Why this module exists
----------------------
`caveats.CAV-00` says it outright: PubMed contains only people who published, so
anyone who joined this lab and left without a paper is absent from every number
the profile report produces — from both the numerator and the denominator.
`CAV-06` repeats it for the lead-slot partition: "counted only over people who
published at least once ... this is not a rate". Those two sentences are the
structural limit of the whole toolkit, and no amount of better PubMed parsing
touches them, because the missing people were never in PubMed to begin with.

A degree-thesis library is different in kind. Every graduate deposits a thesis
and every thesis record carries a supervisor field, so a supervisor query returns
that supervisor's graduates — including the ones who published nothing at all.
That is a population PubMed is structurally incapable of enumerating, and joining
it against the PubMed roster produces the number this report has never been able
to print: **how many people finished a degree in this group without a single
indexed paper**.

What this module does not do
----------------------------
It does not scrape CNKI, Wanfang or anything else. Subscription libraries have
anti-automation measures and terms of use, and a scraper here would be both
fragile and a compliance problem. The division of labour is the same one the
journal-metric table uses: this module defines the schema, reads the file, joins
it, and prints where the data came from. Obtaining the export is the user's
manual step, and `source_db` plus `export_date` are required columns so that two
years from now a number in a report can still be traced to a library and a day.

The join is a name join, and name joins between Chinese characters and romanised
PubMed bylines do not work
--------------------------------------------------------------------------------
A CNKI export writes 学生姓名 in Chinese characters. PubMed writes the same
person as "Zhang San", "San Zhang" or "S Zhang". Nothing in the standard library
converts one into the other, and this module will not guess. When the two rosters
are written in different scripts every graduate lands in `needs_manual_review`
under `undecidable_script`, `without_pubmed_record` comes back empty, and the
result is suppressed with that reason attached — rather than reporting a roster
of graduates as "published nothing", which would be a fabrication in the exact
direction that flatters the finding.

The fix is one column: add a romanised student name (`学生姓名拼音` /
`student_latin`) to the export. It is optional in the schema and it is the single
thing that turns this join from impossible into possible for a Chinese export.

Every uncertain pair is listed by name for a human to settle. Nothing uncertain
is silently pushed into either bucket, so `without_pubmed_record` is a floor and
`without_pubmed_record + needs_manual_review` is a ceiling, and both are
returned.

Standard library only. `load_thesis_roster` touches the disk; `reconcile_roster`
is pure — dicts in, dict out — and can be checked against fixtures offline.
"""

from __future__ import annotations

import csv
import logging
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

# The sample-size floors live in `profile.metrics` and are read from there rather
# than retyped, for the reason `config.py` gives about the weight table: a second
# copy of a threshold is how two modules drift apart without anyone noticing.
#
# The import is deferred into `reconcile_roster` because at module level it is a
# cycle. `from .profile.metrics import ...` executes `profile/__init__.py`, which
# imports `html_report`, which imports `report`, which imports this module — so
# importing `check_your_advisor.theses` first, before anything has loaded the
# profile package, raised ImportError on a partially initialised module. It went
# unnoticed because every caller happened to reach `profile` first: `report.py`
# is inside the package, and `tests/test_theses.py` imports `profile.metrics` on
# the line above its `theses` import, which fully loads the package and hides the
# cycle. A deferred import breaks it without moving the constants, which are
# genuinely metrics' to own.

logger = logging.getLogger(__name__)

__all__ = [
    "COLUMNS",
    "DEGREE_VALUES",
    "DENOMINATOR_LADDER",
    "MAX_UNRESOLVED_SHARE",
    "ROSTER_LIMITS",
    "SOURCE_DB_VALUES",
    "THESIS_DENOMINATOR_CAVEAT",
    "load_thesis_roster",
    "name_script",
    "reconcile_roster",
]


# ------------------------------------------------------------------
# The paragraph that must travel with the number
# ------------------------------------------------------------------
#
# Kept as a module constant and returned by `reconcile_roster` under "caveat",
# for the same reason `caveats.py` keeps its strings verbatim: this is the part
# of the output that says what the number cannot mean, and a paraphrase at the
# call site is how that part quietly weakens. It belongs beside CAV-00 and
# CAV-06 and is written to be printed next to them.

THESIS_DENOMINATOR_CAVEAT = (
    "This denominator is people who graduated, not people who joined. A degree thesis exists only "
    "for someone who finished, so anyone who enrolled with this advisor and left without "
    "submitting one — withdrew, transferred, was dismissed, switched advisor, or is still enrolled "
    "— is absent from CNKI, from Wanfang and from PubMed alike. No library holds a record of them. "
    "The size of that group is not small-but-unknown; it is unmeasured and unmeasurable from any "
    "source this toolkit can reach, and it stays exactly as invisible as CAV-00 says it is. What "
    "this table repairs is narrower, and worth stating precisely: PubMed sees only people who "
    "published, so a graduate with no paper was missing from the numerator and the denominator of "
    "every earlier number in this report. Those people are counted here. The people who left "
    "before the degree still are not. Read 'graduates with no PubMed paper' as a floor on how many "
    "people passed through this group unpublished — never as attrition, never as a graduation "
    "rate, and never as the full membership of the lab."
)

# Three nested populations. The report should print all three rows: a reader who
# sees only the middle one will read it as the whole group, which is the specific
# misreading this module is most likely to cause.
DENOMINATOR_LADDER: tuple[tuple[str, str, str], ...] = (
    (
        "Everyone who ever joined this group",
        "no source",
        "Not observable anywhere. Enrolment lists are not public, and someone who left before "
        "submitting a thesis leaves no trace in any library this toolkit can read. Every number "
        "below sits inside this population and none of them measures it.",
    ),
    (
        "Everyone who completed a degree under this advisor",
        "the thesis export, if it is complete",
        "What `load_thesis_roster` reads. Complete only for the libraries, institutions and years "
        "the user actually searched, and only for people whose thesis was deposited and released.",
    ),
    (
        "Everyone who appeared on an indexed paper in the search window",
        "PubMed",
        "The roster every other section of this report is computed over. A strict subset of the "
        "row above, plus postdocs, technicians and outside collaborators who are not in it at all.",
    ),
)

# What the export itself misses, separately from the dropout hole above. Each of
# these makes `without_pubmed_record` or `pubmed_only` wrong in a stated
# direction, so they are returned with the result rather than left to a footnote.
ROSTER_LIMITS: tuple[tuple[str, str], ...] = (
    (
        "Only degree students are in a thesis library",
        "Postdocs, technicians, research assistants, visiting students, clinical fellows and "
        "undergraduates never deposit a thesis, so they cannot appear in this denominator while "
        "appearing freely in the PubMed corpus. That is most of what `pubmed_only` is, and it is "
        "why that list is not a list of outsiders.",
    ),
    (
        "Only the institutions and years you searched",
        "A student supervised at a previous institution, or under a joint programme filed "
        "elsewhere, is absent unless the export covers that unit too. Widening the export changes "
        "this denominator; nothing in the file records that it was narrow.",
    ),
    (
        "The supervisor field is often the first supervisor only",
        "A co-supervised student can be filed under the other name entirely, which removes them "
        "from this advisor's graduates without any indication that they were removed.",
    ),
    (
        "Withheld, embargoed and classified theses",
        "Records under 涉密 or 延迟公开 may be absent or stripped down. Absence from the export is "
        "not evidence that the person did not graduate.",
    ),
    (
        "The export is a snapshot with a deposit lag",
        "Theses reach a library months after the defence, so the most recent one or two years are "
        "undercounted in the same way CAV-17 describes for PubMed. `export_date` is the day the "
        "library was in this state and is printed for that reason.",
    ),
)


# ------------------------------------------------------------------
# Schema
# ------------------------------------------------------------------


@dataclass(frozen=True)
class _Column:
    """One column: its key, whether the file is refused without it, and its spellings."""

    key: str
    required: bool
    aliases: tuple[str, ...]
    description: str


COLUMNS: tuple[_Column, ...] = (
    _Column(
        "advisor", True,
        ("导师姓名", "导师", "指导教师", "指导老师", "第一导师", "advisor", "supervisor",
         "advisor_name", "supervisor_name", "mentor"),
        "Supervisor as the library records them. Matched against `pi_name`.",
    ),
    _Column(
        "advisor_latin", False,
        ("导师姓名拼音", "导师拼音", "导师英文名", "advisor_latin", "advisor_pinyin",
         "supervisor_latin", "advisor_name_en"),
        "Romanised supervisor name. Optional, and the only way a Chinese export can be "
        "matched against a romanised `pi_name` without the user renaming the column.",
    ),
    _Column(
        "student", True,
        ("学生姓名", "学生", "研究生", "作者", "作者姓名", "篇名作者", "student",
         "student_name", "author", "graduate"),
        "The graduate. The unit this whole module counts.",
    ),
    _Column(
        "student_latin", False,
        ("学生姓名拼音", "学生拼音", "作者拼音", "姓名拼音", "拼音", "student_latin",
         "student_pinyin", "student_name_en", "romanised_name", "romanized_name"),
        "Romanised student name. Optional in the schema and decisive in practice: without it a "
        "Chinese export cannot be joined to romanised PubMed bylines at all, and every graduate "
        "comes back as undecidable rather than as matched or unmatched.",
    ),
    _Column(
        "degree", True,
        ("学位类型", "学位", "学位级别", "论文级别", "培养层次", "degree", "degree_type",
         "level", "thesis_level"),
        "硕士 or 博士. Normalised to `master` / `doctoral`; anything else is kept raw and flagged.",
    ),
    _Column(
        "enrolment_year", False,
        ("入学年", "入学年份", "入学时间", "入学", "enrolment_year", "enrollment_year",
         "admission_year", "start_year"),
        "Optional because CNKI does not supply it — its 学位年度 is the award year. Where it is "
        "present the difference to `graduation_year` is time in the programme, which PubMed "
        "cannot see at all (CAV-09).",
    ),
    _Column(
        "graduation_year", True,
        ("毕业年", "毕业年份", "学位年度", "答辩年度", "学位授予年度", "年度", "graduation_year",
         "degree_year", "award_year", "year"),
        "Award year. Required: a graduate with no year cannot be placed against the search window.",
    ),
    _Column(
        "title", False,
        ("论文题名", "题名", "论文题目", "篇名", "标题", "title", "thesis_title"),
        "Printed verbatim, ungrouped, never classified by topic — same rule as CAV-22.",
    ),
    _Column(
        "institution", False,
        ("培养单位", "学位授予单位", "学位授予院校", "学校", "院校", "institution",
         "university", "school"),
        "Which unit awarded the degree. Its spread shows how wide the export actually was.",
    ),
    _Column(
        "source_db", True,
        ("库来源", "数据库", "来源库", "数据来源", "来源", "source", "source_db", "database"),
        "CNKI, 万方 or 其他. Required, and printed: two years from now nothing else distinguishes "
        "a row that came from one library from a row that came from another.",
    ),
    _Column(
        "export_date", True,
        ("导出日期", "导出时间", "检索日期", "检索时间", "下载日期", "export_date",
         "exported_on", "retrieved_on", "retrieved"),
        "The day the library was read. A deposit lag of months sits behind every export, so a "
        "count without its date cannot be compared with a later one.",
    ),
)

REQUIRED_KEYS: tuple[str, ...] = tuple(c.key for c in COLUMNS if c.required)
OPTIONAL_KEYS: tuple[str, ...] = tuple(c.key for c in COLUMNS if not c.required)

# `其他` is a real bucket, not a failure: a thesis from a library neither of the
# two named ones covers is still a graduate. Unrecognised text is mapped there
# and the raw string is kept beside it.
SOURCE_DB_VALUES: tuple[str, ...] = ("CNKI", "万方", "其他")

_SOURCE_DB_ALIASES: dict[str, str] = {
    "cnki": "CNKI", "知网": "CNKI", "中国知网": "CNKI", "中国期刊网": "CNKI",
    "cnkinet": "CNKI", "中国博士学位论文全文数据库": "CNKI",
    "中国优秀硕士学位论文全文数据库": "CNKI",
    "万方": "万方", "万方数据": "万方", "wanfang": "万方", "wanfangdata": "万方",
    "其他": "其他", "other": "其他",
}

DEGREE_VALUES: tuple[str, ...] = ("master", "doctoral")

# Ordered, and the order is load-bearing: 博士后 contains 博士, so a postdoctoral
# report tested after the doctorate rule would be counted as a doctorate. A
# postdoc is not a graduate of this programme, so it resolves to no degree value
# and is flagged for the reader instead.
_DEGREE_RULES: tuple[tuple[tuple[str, ...], str | None], ...] = (
    (("博士后", "postdoc", "post-doc", "postdoctoral"), None),
    (("博士", "博", "doctor", "doctoral", "phd", "ph.d", "dphil"), "doctoral"),
    (("硕士", "硕", "master", "msc", "m.s", "ma", "mphil"), "master"),
)

# The 学位条例 took effect on 1981-01-01, so a Chinese degree year below it is a
# parse artefact rather than a very old graduate. Rows outside the range are kept
# and flagged, never dropped: a wrong year is still a real person.
EARLIEST_PLAUSIBLE_DEGREE_YEAR = 1981

# Above this share of graduates left undecided, the partition is not a
# measurement of anything. `reconcile_roster` suppresses its aggregate and says
# why, instead of printing a `without_pubmed_record` count whose complement is
# mostly "we could not tell". Declared here so the report can print the anchor
# beside the number, the same way `scoring.py` prints its normalisation anchors.
MAX_UNRESOLVED_SHARE = 0.5


# ------------------------------------------------------------------
# Name handling
# ------------------------------------------------------------------

# Written as escapes, not as literal characters: this is a range, and two
# visually indistinguishable CJK glyphs at its endpoints would silently move it.
_HAN_RE = re.compile("[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_NON_LATIN_RE = re.compile(r"[^a-z]+")


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def name_script(name: str) -> str:
    """`han`, `latin`, `mixed` or `empty`. Two names only join within one script."""
    text = unicodedata.normalize("NFKC", str(name or ""))
    has_han = bool(_HAN_RE.search(text))
    has_latin = bool(_LATIN_RE.search(text))
    if has_han and has_latin:
        return "mixed"
    if has_han:
        return "han"
    if has_latin:
        return "latin"
    return "empty"


def _han_key(name: str) -> str:
    text = unicodedata.normalize("NFKC", str(name or ""))
    return "".join(_HAN_RE.findall(text))


def _latin_tokens(name: str) -> tuple[str, ...]:
    """Lowercase letter runs. Hyphens and dots split, so `Guang-Wei` is two tokens."""
    text = _strip_accents(unicodedata.normalize("NFKC", str(name or ""))).lower()
    return tuple(token for token in _NON_LATIN_RE.sub(" ", text).split() if token)


def _forms(tokens: Sequence[str]) -> frozenset[str]:
    """
    Every cyclic rotation of the tokens, concatenated.

    This is the whole romanisation tolerance, and it is deliberately this narrow.
    Rotation absorbs the two variants that actually occur — surname first versus
    surname last, and a given name written as one token or as two — because
    "zhu guang wei", "guang wei zhu" and "guangwei zhu" all produce
    "zhuguangwei" or "guangweizhu". Nothing else is absorbed: no edit distance,
    no phonetic key, no initial expansion. Those would manufacture matches that
    a reader could not check, and a wrong match here moves a person out of the
    one bucket this module exists to fill.
    """
    tokens = tuple(tokens)
    if not tokens:
        return frozenset()
    return frozenset("".join(tokens[i:] + tokens[:i]) for i in range(len(tokens)))


def _covered_by(short: Sequence[str], long_: Sequence[str]) -> bool:
    """
    Every token of `short` is claimed by a distinct token of `long_`, exactly or
    as a single-letter initial. Consumption is greedy from the longest token
    down, so a surname is spent on its own twin before an initial can take it.

    Only a one-letter token is allowed to match by prefix. A two-letter token in
    a romanised roster is a syllable, not an initial — `Li` and `Lijuan` are
    different given names — and letting those prefix-match would push genuinely
    unmatched graduates into review for no reason.
    """
    remaining = list(long_)
    for token in sorted(short, key=len, reverse=True):
        hit = next((t for t in remaining if t == token), None)
        if hit is None and len(token) == 1:
            hit = next((t for t in remaining if t.startswith(token)), None)
        if hit is None:
            return False
        remaining.remove(hit)
    return True


def _partial_name(left: Sequence[str], right: Sequence[str]) -> bool:
    """
    One side carries strictly less of the name than the other, and what it does
    carry is consistent. Two shapes reach this, and both are common:

    - an initial where a given name should be — `Wang W` against `Wang Wei`,
      which fits `Zhu Gang` and `Zhu Guilin` equally well;
    - a surname with no given name at all — `pubmed_api._author_record` builds
      its `name` as "{LastName} {ForeName}", so a record with no ForeName
      surfaces as the bare string `Zhu`, which fits every Zhu in the corpus.

    Neither is a match and neither is a mismatch, which is the whole reason this
    level exists: without it the second shape would send a graduate straight into
    `without_pubmed_record` — inflating the one count this module exists to
    report, in the direction that makes the finding look stronger.
    """
    left, right = tuple(left), tuple(right)
    if not left or not right:
        return False
    for short, long_ in ((left, right), (right, left)):
        if len(short) > len(long_):
            continue
        # Equal length is only weaker if the short side is spending an initial.
        if len(short) == len(long_) and not any(len(t) == 1 for t in short):
            continue
        if _covered_by(short, long_):
            return True
    return False


def _compare_names(left: str, right: str) -> str | None:
    """
    `exact`, `name_form`, `partial_name`, or None when the pair cannot be joined.

    Returns None for a cross-script pair rather than a weak verdict: a Chinese
    name and a romanised one are not unequal, they are incomparable, and the
    caller has to keep those two cases apart.
    """
    left_script, right_script = name_script(left), name_script(right)
    if left_script == "empty" or right_script == "empty":
        return None
    if left_script == "han" and right_script == "han":
        left_key, right_key = _han_key(left), _han_key(right)
        return "exact" if left_key and left_key == right_key else None
    if "latin" not in (left_script, right_script):
        return None
    left_tokens, right_tokens = _latin_tokens(left), _latin_tokens(right)
    if not left_tokens or not right_tokens:
        return None
    if left_tokens == right_tokens:
        # One token on both sides is a surname against a surname, not an
        # identity: "Zhu" equals "Zhu" for every Zhu alive. It reads as the
        # strongest possible evidence and carries the least, so it is demoted
        # rather than trusted.
        return "partial_name" if len(left_tokens) == 1 else "exact"
    if _forms(left_tokens) & _forms(right_tokens):
        return "name_form"
    if _partial_name(left_tokens, right_tokens):
        return "partial_name"
    return None


def _comparable(left: str, right: str) -> bool:
    """Whether a verdict on this pair is possible at all — a script question, not a name one."""
    left_script, right_script = name_script(left), name_script(right)
    if "empty" in (left_script, right_script):
        return False
    if "mixed" in (left_script, right_script):
        return True
    return left_script == right_script


# How each evidence level is treated, returned with the result so the report can
# print the rule beside the count instead of asking the reader to trust it.
MATCH_RULES: tuple[tuple[str, str, str], ...] = (
    ("exact", "counted as a match",
     "Identical Chinese characters, or identical romanised tokens."),
    ("name_form", "counted as a match",
     "Romanised tokens agree under rotation: surname-first against surname-last, or a given "
     "name written as one token against the same name written as two."),
    ("partial_name", "sent to manual review",
     "One side carries less of the name than the other and does not contradict it: an initial "
     "where a given name should be, or a PubMed record with a surname and no ForeName at all. "
     "On Chinese surnames either shape covers dozens of people (CAV-02), so it is never counted "
     "as a match — and never counted as an absence either, which is the half that matters here."),
    ("ambiguous", "sent to manual review",
     "More than one candidate reached match level, or one PubMed person was claimed by more "
     "than one graduate. Both sides are listed by name."),
    ("undecidable_script", "sent to manual review",
     "The graduate's name and the PubMed roster are written in different scripts. Nothing in "
     "the standard library romanises Chinese characters and this module will not guess, so the "
     "graduate is neither matched nor reported as unpublished."),
)


# ------------------------------------------------------------------
# Loading
# ------------------------------------------------------------------

_HEADER_NOISE_RE = re.compile(r"[\s_\-·.()（）\[\]【】:：/、]+")

# Excel on a Chinese Windows writes CSV as GBK; everything else writes UTF-8,
# usually with a BOM. Both are tried, in that order, and whichever worked is
# returned in the result — a decoding fallback that is not recorded is a silent
# degrade, and a mojibake name would land a real graduate in the unmatched pile.
_ENCODINGS: tuple[str, ...] = ("utf-8-sig", "gbk")


def _normalise_header(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or "")).strip().lstrip("﻿")
    return _HEADER_NOISE_RE.sub("", text).lower()


_ALIAS_TO_KEY: dict[str, str] = {
    _normalise_header(alias): column.key
    for column in COLUMNS
    for alias in column.aliases
}


def _year(value: Any) -> int | None:
    match = re.search(r"(\d{4})", str(value or ""))
    return int(match.group(1)) if match else None


def _iso_date(value: Any) -> str | None:
    """`YYYY-MM-DD` when the cell holds a full date, else None. The raw text is kept regardless."""
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    match = re.search(r"(\d{4})\D{0,3}(\d{1,2})\D{0,3}(\d{1,2})", text)
    if not match:
        match = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", re.sub(r"\D", "", text))
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _degree(value: Any) -> str | None:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    if not text:
        return None
    for needles, resolved in _DEGREE_RULES:
        if any(needle in text for needle in needles):
            return resolved
    return None


def _source_db(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    return _SOURCE_DB_ALIASES.get(_HEADER_NOISE_RE.sub("", text).lower(), "其他")


def _read_rows(path: Path) -> tuple[list[dict[str, str]], list[str], str]:
    last_error: UnicodeDecodeError | None = None
    for encoding in _ENCODINGS:
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                reader = csv.DictReader(handle)
                rows = [row for row in reader]
                return rows, list(reader.fieldnames or []), encoding
        except UnicodeDecodeError as error:
            last_error = error
    raise ValueError(
        f"{path} decodes as neither {' nor '.join(_ENCODINGS)}. Re-save the export as UTF-8 CSV; "
        "guessing a third encoding would corrupt the names this file exists to match on."
    ) from last_error


def load_thesis_roster(path: str | Path) -> dict[str, Any]:
    """
    Read a hand-exported thesis roster and validate it against `COLUMNS`.

    One CSV, one row per thesis. Headers may be written in Chinese or English —
    every spelling in `COLUMNS[*].aliases` is accepted — and are matched after
    case, spacing and punctuation are removed, so `导师姓名`, `导师 姓名` and
    `advisor_name` are the same column.

    Refused rather than degraded:

    - a missing required column (`advisor`, `student`, `degree`,
      `graduation_year`, `source_db`, `export_date`). `source_db` and
      `export_date` are required for the same reason the journal table needs a
      version column: a partition number with no recorded origin cannot be
      re-checked two years later, and a graduate count is no different.
    - a file that decodes as neither UTF-8 nor GBK.

    Kept but recorded, because a malformed cell is still a real person:

    - an unreadable degree word, an out-of-range year, an enrolment year after
      the graduation year and an unparseable export date all become per-row
      `flags` with the raw text preserved beside the parsed value.
    - a row with no student name or no readable graduation year cannot be
      counted or placed in time, so it goes to `rejected` with its line number
      rather than being dropped silently.
    - exact duplicates on (advisor, student, degree, graduation_year) are
      dropped once and listed. A master's and a doctorate under the same
      advisor are *not* duplicates — that is one person with two theses, and
      `reconcile_roster` counts them as one graduate.

    Returns a dict with `rows`, `rejected`, `duplicates_dropped`, the
    `denominator` those rows were counted over, `columns_used` mapping each
    schema key to the header as actually written, `columns_missing_optional`,
    `unmapped_columns`, the `encoding` that worked, and the provenance
    summaries `source_dbs`, `export_dates` and `institutions` that the report
    must print. `limits` carries `ROSTER_LIMITS` so a renderer holding only this
    dict still has the text describing what the export cannot contain.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"thesis roster not found: {path}")

    raw_rows, fieldnames, encoding = _read_rows(path)

    header_map: dict[str, str] = {}
    columns_used: dict[str, str] = {}
    unmapped: list[str] = []
    for header in fieldnames:
        key = _ALIAS_TO_KEY.get(_normalise_header(header))
        if key is None:
            unmapped.append(header)
        elif key not in columns_used:
            header_map[header] = key
            columns_used[key] = header
        else:
            unmapped.append(header)

    missing_required = [key for key in REQUIRED_KEYS if key not in columns_used]
    if missing_required:
        wanted = {
            column.key: column.aliases[0] for column in COLUMNS if column.key in missing_required
        }
        raise ValueError(
            f"{path} is missing required column(s): "
            + ", ".join(f"{key} (e.g. {header!r})" for key, header in wanted.items())
            + f". Headers found: {', '.join(fieldnames) or '(none)'}."
        )

    year_ceiling = date.today().year + 1
    rows: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, int]] = set()

    for offset, raw in enumerate(raw_rows):
        # +2: one for the header line, one because humans count from 1.
        line = offset + 2
        values = {key: str(raw.get(header) or "").strip() for header, key in header_map.items()}
        student = values.get("student", "")
        if not student:
            rejected.append({"line": line, "reason": "no student name", "raw": dict(raw)})
            continue
        graduation_year = _year(values.get("graduation_year"))
        if graduation_year is None:
            rejected.append({
                "line": line,
                "reason": f"no readable graduation year in {values.get('graduation_year', '')!r}",
                "raw": dict(raw),
            })
            continue

        flags: list[str] = []
        if not (EARLIEST_PLAUSIBLE_DEGREE_YEAR <= graduation_year <= year_ceiling):
            flags.append("graduation_year_out_of_range")
        degree = _degree(values.get("degree"))
        if degree is None:
            flags.append("degree_unrecognised")
        enrolment_year = _year(values.get("enrolment_year"))
        if enrolment_year is not None and enrolment_year > graduation_year:
            flags.append("enrolment_after_graduation")
        source_db_raw = values.get("source_db", "")
        source_db = _source_db(source_db_raw)
        if source_db == "其他" and _HEADER_NOISE_RE.sub("", source_db_raw).lower() not in {
            "其他", "other"
        }:
            flags.append("source_db_unrecognised")
        export_date = _iso_date(values.get("export_date"))
        if export_date is None:
            flags.append("export_date_unparseable")
        if not values.get("student_latin") and name_script(student) == "han":
            flags.append("no_romanised_name")

        key = (values.get("advisor", ""), student, degree or "", graduation_year)
        if key in seen:
            duplicates.append({"line": line, "student": student, "graduation_year": graduation_year})
            continue
        seen.add(key)

        rows.append({
            "line": line,
            "advisor": values.get("advisor", ""),
            "advisor_latin": values.get("advisor_latin", ""),
            "student": student,
            "student_latin": values.get("student_latin", ""),
            "degree": degree,
            "degree_raw": values.get("degree", ""),
            "enrolment_year": enrolment_year,
            "graduation_year": graduation_year,
            "title": values.get("title", ""),
            "institution": values.get("institution", ""),
            "source_db": source_db,
            "source_db_raw": source_db_raw,
            "export_date": export_date,
            "export_date_raw": values.get("export_date", ""),
            "flags": flags,
        })

    logger.info(
        "学位论文名单已读入: %s (%s 编码) — %d 行可用, %d 行被拒, %d 行重复",
        path, encoding, len(rows), len(rejected), len(duplicates),
    )
    for flag, count in sorted(Counter(f for row in rows for f in row["flags"]).items()):
        logger.warning("  %d 行带标记 %s", count, flag)

    return {
        "path": str(path),
        "encoding": encoding,
        "rows": rows,
        "rejected": rejected,
        "duplicates_dropped": duplicates,
        # R1: the population every count downstream is taken over.
        "denominator": len(rows),
        "rows_read": len(raw_rows),
        "columns_used": columns_used,
        "columns_missing_optional": [key for key in OPTIONAL_KEYS if key not in columns_used],
        "unmapped_columns": unmapped,
        "flag_counts": dict(sorted(Counter(f for row in rows for f in row["flags"]).items())),
        "source_dbs": dict(sorted(Counter(row["source_db"] for row in rows).items())),
        "export_dates": sorted({row["export_date"] or row["export_date_raw"] for row in rows if
                                row["export_date"] or row["export_date_raw"]}),
        "institutions": dict(sorted(Counter(
            row["institution"] for row in rows if row["institution"]).items())),
        "limits": ROSTER_LIMITS,
    }


# ------------------------------------------------------------------
# Reconciliation
# ------------------------------------------------------------------


def _people(pubmed_people: Any) -> list[Mapping[str, Any]]:
    """Accept `build_people()`, `person_roster()`, a whole report, or a bare list."""
    if isinstance(pubmed_people, Mapping):
        for key in ("people", "rows"):
            value = pubmed_people.get(key)
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                return [p for p in value if isinstance(p, Mapping)]
        metrics = pubmed_people.get("metrics")
        if isinstance(metrics, Mapping):
            roster = metrics.get("s2")
            if isinstance(roster, Mapping):
                return _people(roster)
        return []
    if isinstance(pubmed_people, Sequence) and not isinstance(pubmed_people, (str, bytes)):
        return [p for p in pubmed_people if isinstance(p, Mapping)]
    return []


def _roster_rows(thesis_roster: Any) -> list[Mapping[str, Any]]:
    if isinstance(thesis_roster, Mapping):
        rows = thesis_roster.get("rows")
        return [r for r in rows if isinstance(r, Mapping)] if isinstance(rows, Sequence) else []
    if isinstance(thesis_roster, Sequence) and not isinstance(thesis_roster, (str, bytes)):
        return [r for r in thesis_roster if isinstance(r, Mapping)]
    return []


def _variants(row: Mapping[str, Any], *keys: str) -> list[str]:
    return [str(row.get(key) or "").strip() for key in keys if str(row.get(key) or "").strip()]


def _select_advisor_rows(
    rows: Sequence[Mapping[str, Any]], pi_name: str
) -> tuple[list[Mapping[str, Any]], str, str | None]:
    """
    Which rows belong to this PI, and how sure that is.

    Three outcomes, kept apart on purpose. `matched` means at least one row's
    supervisor name joined to `pi_name`. `unverified_single_advisor` means the
    file carries exactly one supervisor and it did not join — the usual case for
    a Chinese export queried with a romanised `pi_name`, where the file is almost
    certainly right and the tool cannot confirm it. `refused` means several
    supervisors are present and none joined, where picking any subset would be a
    guess about whose graduates these are.
    """
    matched = [
        row for row in rows
        if any(_compare_names(name, pi_name) for name in _variants(row, "advisor", "advisor_latin"))
    ]
    if matched:
        return matched, "matched", None
    distinct = {str(row.get("advisor") or "").strip() for row in rows if str(row.get("advisor") or "").strip()}
    if not rows:
        return [], "refused", "the thesis roster is empty, so no graduate can be attributed to anyone."
    if len(distinct) == 1:
        only = next(iter(distinct))
        return list(rows), "unverified_single_advisor", (
            f"no supervisor cell joined to pi_name {pi_name!r}, but the file carries exactly one "
            f"supervisor ({only!r}), so every row was taken as theirs. If that export was queried "
            "for someone else, every count below is that other person's."
        )
    return [], "refused", (
        f"no supervisor cell joined to pi_name {pi_name!r} and the file carries "
        f"{len(distinct)} different supervisors, so there is no non-arbitrary way to say which "
        f"graduates are this PI's. Supervisors seen: {', '.join(sorted(distinct)[:12])}"
        + (" ..." if len(distinct) > 12 else "")
        + ". Fix pi_name, or add an 学生姓名拼音 / 导师姓名拼音 column."
    )


def _group_graduates(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """
    One entry per person, not per thesis.

    A master's degree followed by a doctorate under the same advisor is two rows
    and one graduate; counting rows would inflate the denominator by exactly the
    students who stayed longest. Grouping is by exact normalised name, so the
    converse failure is CAV-02's: two different people recorded under one name
    merge into one graduate, and two spellings of one person split into two.
    Rows sharing a name *and* a degree are flagged rather than assumed to be the
    same person.
    """
    grouped: dict[tuple[str, tuple[str, ...]], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        name = str(row.get("student") or "")
        grouped[(_han_key(name), _latin_tokens(name))].append(row)

    graduates: list[dict[str, Any]] = []
    for entries in grouped.values():
        ordered = sorted(entries, key=lambda r: (int(r.get("graduation_year") or 0), r.get("line", 0)))
        degrees = [entry.get("degree") for entry in ordered]
        flags: list[str] = []
        if len(ordered) > 1:
            named = [d for d in degrees if d]
            flags.append(
                "same_name_same_degree" if len(set(named)) < len(named) else "continuing_student"
            )
        graduates.append({
            "student": str(ordered[0].get("student") or ""),
            "student_latin": next((str(e.get("student_latin") or "") for e in ordered
                                   if str(e.get("student_latin") or "").strip()), ""),
            "degrees": degrees,
            "first_graduation_year": int(ordered[0].get("graduation_year") or 0),
            "last_graduation_year": int(ordered[-1].get("graduation_year") or 0),
            "institutions": sorted({str(e.get("institution") or "") for e in ordered
                                    if str(e.get("institution") or "").strip()}),
            "source_dbs": sorted({str(e.get("source_db") or "") for e in ordered
                                  if str(e.get("source_db") or "").strip()}),
            "titles": [str(e.get("title") or "") for e in ordered],
            "lines": [e.get("line") for e in ordered],
            "flags": flags + sorted({f for e in ordered for f in (e.get("flags") or [])}),
        })

    # Chronological, then by name. Never by any count: ordering people by how
    # much they published is the leaderboard `roles.build_people` refuses to
    # build, and this list is the same people.
    graduates.sort(key=lambda g: (g["first_graduation_year"], g["student"]))
    return graduates


_ACCEPTED = {"exact", "name_form"}


def reconcile_roster(
    pubmed_people: Any,
    thesis_roster: Any,
    pi_name: str,
) -> dict[str, Any]:
    """
    Join the PubMed roster against the graduation list and produce the real denominator.

    This is the one function in the toolkit whose denominator is not conditioned
    on having published. `pubmed_people` accepts `roles.build_people()`,
    `metrics.person_roster()`, a whole report dict or a bare list of person
    dicts; `thesis_roster` accepts `load_thesis_roster()` or a bare list of its
    rows; `pi_name` is the PI as `author_name` spells them.

    Four numbers come out, and the third is the one this module was built for:

    - **`graduates_total`** — distinct people who took a degree under this
      advisor, per the export. Not thesis rows: someone who took a master's and
      then a doctorate here is one graduate.
    - **`with_pubmed_record`** — graduates who also appear in the PubMed corpus.
    - **`without_pubmed_record`** — graduates who appear in no PubMed paper in
      the search window. **This is the group PubMed alone cannot see at all**:
      absent from every numerator and every denominator in Sections 2 through
      16, and the reason CAV-06 says the lead-slot partition is not a rate.
    - **`pubmed_only`** — people in the PubMed corpus who are not on the
      graduation list. Postdocs, technicians, research assistants, visiting
      students and outside collaborators all land here (see `ROSTER_LIMITS`), so
      this is not a list of outsiders and must not be printed as one.

    What is deliberately *not* one of those four:

    - Anything uncertain. A pair where one side carries only an initial or only
      a surname, a graduate with
      more than one candidate, a PubMed person claimed by more than one
      graduate, and every graduate whose name cannot be compared with the PubMed
      roster at all because the two are written in different scripts — all of
      them go to `needs_manual_review`, listed by name with their candidates and
      a reason. None of them is quietly pushed into a bucket. That is why
      `without_pubmed_record` is a floor and `without_pubmed_bounds` returns
      `(len(without_pubmed_record), len(without_pubmed_record) +
      len(needs_manual_review))`.
    - A share, whenever the result is suppressed. Below `MIN_N_AGGREGATE`
      graduates, or above `MAX_UNRESOLVED_SHARE` of them unresolved, the counts
      and the named lists still come back and `without_pubmed_share_percent` is
      None, following the convention in `metrics.py`: the parts survive, the
      aggregate does not.

    **The limit that does not go away.** Everyone here graduated. A student who
    enrolled with this advisor and left before submitting a thesis is in no
    library at all — not CNKI, not Wanfang, not PubMed — so this denominator is
    better than PubMed's and is still not "everyone who joined this group".
    `THESIS_DENOMINATOR_CAVEAT` states that in full and is returned under
    `caveat`; `DENOMINATOR_LADDER` returns the three nested populations under
    `denominator_ladder` so a report can print all three rows and a reader
    cannot mistake the middle one for the whole. Neither is optional output.
    """
    # Deferred to break the import cycle described at the top of this module.
    # By the time anyone calls this, `profile` is loaded and the import is free.
    from .profile.metrics import MIN_N_AGGREGATE, MIN_N_PERCENT, percent

    people = _people(pubmed_people)
    roster_rows = _roster_rows(thesis_roster)
    pi_name = str(pi_name or "").strip()

    # `build_people` already drops the PI, but a caller may pass a roster that
    # did not, and a PI matched to their own student list would be nonsense.
    people = [
        person for person in people
        if not _compare_names(str(person.get("name") or ""), pi_name)
    ]

    selected, advisor_filter, advisor_note = _select_advisor_rows(roster_rows, pi_name)
    graduates = _group_graduates(selected)

    with_record: list[dict[str, Any]] = []
    without_record: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    claimed_by: dict[int, list[str]] = defaultdict(list)
    matched_person_index: dict[str, int] = {}

    for graduate in graduates:
        names = _variants(graduate, "student", "student_latin") or [graduate["student"]]
        accepted: list[tuple[int, str, str]] = []
        weak: list[tuple[int, str, str]] = []
        incomparable = 0
        for index, person in enumerate(people):
            person_name = str(person.get("name") or "")
            if not any(_comparable(name, person_name) for name in names):
                incomparable += 1
                continue
            best = None
            for name in names:
                level = _compare_names(name, person_name)
                if level in _ACCEPTED:
                    best = (index, person_name, level)
                    break
                if level == "partial_name" and best is None:
                    best = (index, person_name, level)
            if best is None:
                continue
            (accepted if best[2] in _ACCEPTED else weak).append(best)

        entry = {
            "student": graduate["student"],
            "student_latin": graduate["student_latin"],
            "degrees": graduate["degrees"],
            "graduation_year": graduate["first_graduation_year"],
            "flags": graduate["flags"],
        }
        if len(accepted) == 1:
            index, person_name, level = accepted[0]
            person = people[index]
            claimed_by[index].append(graduate["student"])
            matched_person_index[graduate["student"]] = index
            with_record.append({
                **entry,
                "pubmed_name": person_name,
                "evidence": level,
                "pubmed_stratum": person.get("stratum"),
                "pubmed_first_year": person.get("first_year"),
                "pubmed_last_year": person.get("last_year"),
                "pubmed_appearances": person.get("n_appearances"),
                "pubmed_lead_slots": person.get("n_first_slots"),
                # An identity-flagged PubMed row is a merge or a split (CAV-02);
                # the match still shows the graduate published, but the counts
                # attached to it are not safely theirs.
                "pubmed_identity_flags": list(person.get("flags") or []),
            })
        elif len(accepted) > 1:
            review.append({**entry, "reason": "ambiguous",
                           "candidates": [name for _, name, _ in accepted]})
        elif weak:
            review.append({**entry, "reason": "partial_name",
                           "candidates": [name for _, name, _ in weak]})
        elif incomparable:
            review.append({
                **entry,
                "reason": "undecidable_script",
                "candidates": [],
                "incomparable_pubmed_people": incomparable,
                "note": (
                    f"this name is written in {name_script(graduate['student'])} script and "
                    f"{incomparable} PubMed "
                    f"{'person' if incomparable == 1 else 'people'} could not be compared with "
                    "it. Add a student_latin (学生姓名拼音) column to decide this row."
                ),
            })
        else:
            without_record.append(entry)

    # A PubMed person claimed by two graduates makes both matches unsafe, so
    # both are moved out of the counted bucket rather than one being preferred.
    contested = {index for index, names in claimed_by.items() if len(names) > 1}
    if contested:
        contested_names = {
            student for student, index in matched_person_index.items() if index in contested
        }
        moved = [row for row in with_record if row["student"] in contested_names]
        with_record = [row for row in with_record if row["student"] not in contested_names]
        for row in moved:
            review.append({
                "student": row["student"],
                "student_latin": row["student_latin"],
                "degrees": row["degrees"],
                "graduation_year": row["graduation_year"],
                "flags": row["flags"],
                "reason": "ambiguous",
                "candidates": [row["pubmed_name"]],
                "note": (
                    f"PubMed person {row['pubmed_name']!r} was matched by more than one graduate: "
                    + ", ".join(sorted(claimed_by[matched_person_index[row['student']]]))
                ),
            })

    claimed_indices = {
        index for student, index in matched_person_index.items()
        if index not in contested
    }
    review_candidates = {name for row in review for name in row.get("candidates") or []}
    pubmed_only = [
        {
            "name": str(person.get("name") or ""),
            "marker": person.get("marker", ""),
            "stratum": person.get("stratum"),
            "first_year": person.get("first_year"),
            "last_year": person.get("last_year"),
            "n_appearances": person.get("n_appearances"),
            "n_first_slots": person.get("n_first_slots"),
        }
        for index, person in enumerate(people)
        if index not in claimed_indices and str(person.get("name") or "") not in review_candidates
    ]

    graduates_total = len(graduates)
    unresolved = len(review)
    unresolved_share = unresolved / graduates_total if graduates_total else 0.0

    reasons: list[str] = []
    if advisor_filter == "refused":
        reasons.append(advisor_note or "no rows could be attributed to this PI")
    if graduates_total == 0:
        reasons.append("no graduate could be attributed to this PI, so there is nothing to divide")
    elif graduates_total < MIN_N_AGGREGATE:
        reasons.append(
            f"{graduates_total} graduates on record (floor {MIN_N_AGGREGATE}); the names are "
            "printed instead of a share"
        )
    if graduates_total and unresolved_share > MAX_UNRESOLVED_SHARE:
        reasons.append(
            f"{unresolved} of {graduates_total} graduates could not be decided against the PubMed "
            f"roster ({unresolved_share:.0%}, ceiling {MAX_UNRESOLVED_SHARE:.0%}). The complement "
            "of an undecided majority is not a measurement"
            + (", and this is what a Chinese export joined against romanised bylines looks like: "
               "add a student_latin (学生姓名拼音) column"
               if any(row.get("reason") == "undecidable_script" for row in review) else "")
        )
    suppressed = bool(reasons)

    provenance = thesis_roster if isinstance(thesis_roster, Mapping) else {}
    result = {
        "pi_name": pi_name,
        "advisor_filter": advisor_filter,
        "advisor_note": advisor_note,
        "advisor_names_seen": dict(sorted(Counter(
            str(row.get("advisor") or "") for row in roster_rows if str(row.get("advisor") or "")
        ).items())),
        "rows_for_pi": len(selected),
        # R1: distinct people, and the population every count below is over.
        "denominator": graduates_total,
        "counts": {
            "graduates_total": graduates_total,
            "with_pubmed_record": len(with_record),
            "without_pubmed_record": len(without_record),
            "needs_manual_review": unresolved,
            "pubmed_only": len(pubmed_only),
            "pubmed_roster_size": len(people),
        },
        "with_pubmed_record": with_record,
        "without_pubmed_record": without_record,
        "needs_manual_review": review,
        "pubmed_only": pubmed_only,
        # Floor and ceiling, because every unresolved row could fall either way.
        # Printing the floor alone would understate, and printing one number
        # would hide that the two ends are this far apart.
        "without_pubmed_bounds": (len(without_record), len(without_record) + unresolved),
        "without_pubmed_share_percent": (
            None if suppressed else percent(len(without_record), graduates_total)
        ),
        "share_basis": (
            "share of graduates on record with no paper in the PubMed corpus; a share exists only "
            f"at n >= {MIN_N_PERCENT} and never at all when the result is suppressed. It is not a "
            "graduation rate and not an attrition rate — neither has an observable denominator."
        ),
        "unresolved_share": unresolved_share,
        "max_unresolved_share": MAX_UNRESOLVED_SHARE,
        "suppressed": suppressed,
        "suppressed_reasons": reasons,
        "not_computable": graduates_total == 0,
        "match_rules": MATCH_RULES,
        # Printed with the numbers, never separated from them: which library the
        # graduate list came from, and the day it was read.
        "provenance": {
            "path": provenance.get("path"),
            "encoding": provenance.get("encoding"),
            "source_dbs": provenance.get("source_dbs") or {},
            "export_dates": provenance.get("export_dates") or [],
            "institutions": provenance.get("institutions") or {},
            "rows_read": provenance.get("rows_read"),
            "rejected": len(provenance.get("rejected") or []),
            "duplicates_dropped": len(provenance.get("duplicates_dropped") or []),
        },
        "caveat": THESIS_DENOMINATOR_CAVEAT,
        "denominator_ladder": DENOMINATOR_LADDER,
        "limits": ROSTER_LIMITS,
    }

    logger.info(
        "毕业名单对照完成: %d 名毕业生 — %d 人在 PubMed 语料中, %d 人一篇都没有, %d 人待人工核",
        graduates_total, len(with_record), len(without_record), unresolved,
    )
    for reason in reasons:
        logger.warning("对照结果被抑制: %s", reason)
    return result
