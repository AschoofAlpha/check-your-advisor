#!/usr/bin/env python3
"""
`check_your_advisor.i18n` and its catalogs: two languages, one source of truth.

Every string the package prints is written once, in its source language — the
report in English, the command line in Chinese — and translated through a catalog
(`check_your_advisor.locales`). `profile` and `compare` write one report in each
language from the same built report; the command line follows `--lang`, then
$CHECK_YOUR_ADVISOR_LANG, then the system locale. What has to hold:

  1. **Which language** is decided the way a user expects: `--lang` beats the
     environment variable beats the locale, a typo falls through instead of being
     obeyed, and `C`/`POSIX` is English.
  2. **Lookup** returns the source text when no language is set, which is the
     state of every library caller and of this suite, so nothing here depends on
     the machine it runs on. A `Text` rebuilds its sentence in the other language
     from its template; a `Joined` translates each part.
  3. **The catalogs are complete and honest**: every source string the report can
     print has a Chinese translation, every Chinese command-line message has an
     English one, no translation drops or adds a `{field}` or reorders a
     `%`-conversion, and no catalog key is an orphan whose source string no
     longer exists — a changed sentence must not stay paired with the old meaning.
  4. **The Chinese report renders in strict mode**, where a missing translation
     raises instead of quietly printing English, and its numbers are the English
     report's numbers.
  5. **The command line** prints in the language it was asked for, help included.

All data is synthetic. Fully offline.

Run: python tests/test_i18n.py
"""

from __future__ import annotations

import ast
import io
import json
import logging
import os
import pickle
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from check_your_advisor import i18n  # noqa: E402
from check_your_advisor.locales import catalog_modules, load_catalogs  # noqa: E402

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


HAN = re.compile(r"[一-鿿]")


# ============================================================
# 1. Which language
# ============================================================

print("\n--- which language ---")

check("zh tags normalise to zh",
      [i18n.normalize(v) for v in ("zh", "zh-CN", "zh_CN.UTF-8", "ZH_tw", "Chinese (Simplified)_China")],
      ["zh"] * 5)
check("en tags normalise to en",
      [i18n.normalize(v) for v in ("en", "en-US", "en_GB.utf8", "English_United States")], ["en"] * 4)
check("C, POSIX, empty and a third language normalise to None",
      [i18n.normalize(v) for v in ("C", "POSIX", "", None, "fr_FR.UTF-8")], [None] * 5)

check("LANG=zh_CN is Chinese", i18n.system_language({"LANG": "zh_CN.UTF-8"}), "zh")
check("LANG=en_US is English", i18n.system_language({"LANG": "en_US.UTF-8"}), "en")
check("LC_ALL outranks LANG", i18n.system_language({"LC_ALL": "en_US.UTF-8", "LANG": "zh_CN.UTF-8"}), "en")
check("LC_MESSAGES outranks LANG",
      i18n.system_language({"LC_MESSAGES": "zh_TW.UTF-8", "LANG": "en_US.UTF-8"}), "zh")
check("LANGUAGE's first entry outranks a real locale",
      i18n.system_language({"LANGUAGE": "zh_CN:en", "LANG": "en_US.UTF-8"}), "zh")
check("...but not a C locale, which is English",
      i18n.system_language({"LANGUAGE": "zh_CN", "LC_ALL": "C"}), "en")
check("a third language falls back to English", i18n.system_language({"LANG": "de_DE.UTF-8"}), "en")

env_zh = {i18n.ENV_VAR: "zh", "LANG": "en_US.UTF-8"}
check("--lang beats the environment variable",
      i18n.resolve_language(["profile", "--lang", "en"], env_zh), ("en", "--lang"))
check("--lang=X works too", i18n.resolve_language(["--lang=zh"], {"LANG": "en_US"}), ("zh", "--lang"))
check("the last --lang wins", i18n.resolve_language(["--lang", "zh", "--lang", "en"], {}), ("en", "--lang"))
check("the environment variable beats the locale", i18n.resolve_language([], env_zh), ("zh", i18n.ENV_VAR))
check("a typo in --lang falls through rather than being obeyed",
      i18n.resolve_language(["--lang", "klingon"], env_zh), ("zh", i18n.ENV_VAR))
check("...and so does one in the environment variable",
      i18n.resolve_language([], {i18n.ENV_VAR: "xx", "LANG": "zh_CN.UTF-8"}), ("zh", "system locale"))
check("report file names: English keeps the old name, Chinese adds .zh-CN",
      (i18n.report_suffix("en"), i18n.report_suffix("zh")), ("", ".zh-CN"))
check("English is written first", i18n.REPORT_LANGUAGES[0], "en")


# ============================================================
# 2. Lookup
# ============================================================

print("\n--- lookup ---")

check("no language set: source text comes back", i18n.language(), None)
check("...English source untouched", i18n.en("records"), "records")
check("...Chinese source untouched", i18n.zh("未记录"), "未记录")
check("...fields are filled", i18n.en("{n} of {m}", n=1, m=2), "1 of 2")
check("...braces in a string with no fields are left alone", i18n.en("a {b} c"), "a {b} c")

with i18n.using("zh"):
    check("using('zh') sets the language", i18n.language(), "zh")
    check("an English source is looked up in the Chinese catalog", bool(HAN.search(i18n.en("records"))), True)
    check("a Chinese source is returned as it is", i18n.zh("未记录"), "未记录")
    with i18n.using("en"):
        check("using() nests", i18n.language(), "en")
        check("...and translates the other way", i18n.zh("未记录"), "not recorded")
    check("...and restores on the way out", i18n.language(), "zh")
check("the language is gone after the block", i18n.language(), None)

try:
    with i18n.using("klingon"):
        pass
    unknown_raised = False
except ValueError:
    unknown_raised = True
check_true("using() refuses a language it does not have", unknown_raised)

text = i18n.lazy_en("{denominator} records in total.", denominator=7)
check("a Text is its English rendering", text, "7 records in total.")
check("...hashes like it", hash(text), hash("7 records in total."))
check("...serialises like it", json.dumps({"note": text}), json.dumps({"note": "7 records in total."}))
check("...and pickles with its template", pickle.loads(pickle.dumps(text)).template,
      "{denominator} records in total.")
with i18n.using("zh"):
    rebuilt = i18n.en(text)
check_true("...and rebuilds itself in Chinese from the template", "7" in rebuilt and HAN.search(rebuilt))
check("wrapping a Text again keeps its template", i18n.lazy_en(text).template,
      "{denominator} records in total.")

nested = i18n.lazy_en("{n} annual point(s) of {unit}, below the floor of {floor}: {why}",
                      n=1, unit=i18n.lazy_en("records"), floor=4,
                      why=i18n.lazy_en("a single point has no direction"))
with i18n.using("zh"):
    nested_zh = i18n.en(nested)
check_false("a Text field inside a Text is translated with it", "single point" in nested_zh)

joined = i18n.lazy_join([i18n.lazy_en("The interval spans zero, so these points are as consistent "
                                      "with no trend at all as with the slope printed above."),
                         i18n.lazy_en("{floor} points is the floor this module will fit at, and "
                                      "n={n} is not a sample size at which a slope means much; the "
                                      "interval is the part of this line worth reading.",
                                      floor=4, n=6)])
check_true("a Joined reads as the plain join", str(joined).count(". ") >= 1)
with i18n.using("zh"):
    joined_zh = i18n.en(joined)
check_true("...renders each part in Chinese", HAN.search(joined_zh) and "n=6" in joined_zh)
check_false("...with no space left between two Chinese sentences", "。 " in joined_zh)

zh_text = i18n.lazy_zh("学生评价表是空文件: {path}", path="x.csv")
check("a Chinese-source Text is its Chinese rendering", zh_text, "学生评价表是空文件: x.csv")
with i18n.using("en"):
    check_false("...and becomes English on demand", HAN.search(i18n.zh(zh_text)))
check("i18n.reason() hands back the Text an exception carries",
      i18n.reason(ValueError(zh_text)) is zh_text, True)
check("...and the message of any other exception", i18n.reason(ValueError("plain")), "plain")

with i18n.using("zh"):
    once = i18n.en("records")
    check("translating an already-translated string is a no-op", i18n.en(once), once)

i18n.clear_missing()
with i18n.using("zh"):
    i18n.en("a sentence no catalog will ever hold, 42")
check_true("a miss is recorded", "a sentence no catalog will ever hold, 42" in i18n.missing("zh"))
check("...and printed in its source language rather than raising",
      i18n.en("another sentence nobody translated"), "another sentence nobody translated")
try:
    with i18n.using("zh"), i18n.strict():
        i18n.en("a sentence no catalog will ever hold, 43")
    strict_raised = False
except KeyError:
    strict_raised = True
check_true("strict() turns a miss into a KeyError", strict_raised)
i18n.clear_missing()


@i18n.in_language("en")
def _pinned() -> str:
    return i18n.language()


with i18n.using("zh"):
    check("in_language pins a payload builder to its source language", _pinned(), "en")


check("placeholders: fields sorted, %-conversions in order",
      i18n.placeholders("%s of {b} and {a:.1f} then %d"), (("a", "b"), ("%s", "%d")))


# ============================================================
# 3. The catalogs
# ============================================================

print("\n--- catalogs ---")

modules = catalog_modules()
check_true("there is at least one Chinese and one English catalog module",
           modules["zh"] and modules["en"])
catalogs = load_catalogs()
check_true("the catalogs merge without two modules disagreeing", catalogs)

PACKAGE = ROOT / "scripts" / "check_your_advisor"


class _Any:
    def __format__(self, spec):
        return "0"

    def __repr__(self):
        return "'x'"


def _literals():
    """Every string constant in the package, and every first argument of a wrapper."""
    constants: set[str] = set()
    wrapped: dict[str, set[str]] = {"en": set(), "zh": set()}
    log_messages: set[str] = set()
    for path in PACKAGE.rglob("*.py"):
        if "locales" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                constants.add(node.value)
            if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                name = node.func.id if isinstance(node.func, ast.Name) else (
                    node.func.attr if isinstance(node.func, ast.Attribute) else "")
                if name in ("en", "lazy_en"):
                    wrapped["en"].add(node.args[0].value)
                elif name in ("zh", "lazy_zh"):
                    wrapped["zh"].add(node.args[0].value)
                elif name in ("debug", "info", "warning", "error", "exception", "critical") \
                        and HAN.search(node.args[0].value):
                    log_messages.add(node.args[0].value)
    return constants, wrapped, log_messages


constants, wrapped, log_messages = _literals()
known = constants | {value.replace("_", " ") for value in constants}

bad_fields, bad_format, empty = [], [], []
for lang, catalog in catalogs.items():
    for source, translation in catalog.items():
        if not isinstance(translation, str) or (source.strip() and not translation.strip()):
            empty.append((lang, source[:60]))
            continue
        if i18n.placeholders(source) != i18n.placeholders(translation):
            bad_fields.append((lang, source[:60]))
        names = i18n.placeholders(source)[0]
        if names:
            try:
                translation.format(**{name: _Any() for name in names})
            except (KeyError, IndexError, ValueError):
                bad_format.append((lang, source[:60]))
check("no translation is empty", empty, [])
check("no translation adds, drops or renames a {field} or reorders a %-conversion", bad_fields, [])
check("every translation with fields formats", bad_format, [])


untranslated_en = sorted(s[:80] for s in wrapped["en"] if s not in catalogs["zh"])
check("every English string the code wraps has a Chinese translation", untranslated_en, [])
untranslated_zh = sorted(s[:80] for s in wrapped["zh"] if s not in catalogs["en"])
check("every Chinese string the code wraps has an English translation", untranslated_zh, [])
untranslated_logs = sorted(s[:80] for s in log_messages if s not in catalogs["en"])
check("every Chinese log message has an English translation", untranslated_logs, [])

# Constants rendered through en() at their use site, rather than written inside one.
from check_your_advisor import evaluations, impact_reference, journal_risk, journals, theses  # noqa: E402
from check_your_advisor.profile import caveats, figures, ranking, report, roles, scoring  # noqa: E402


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


rendered_constants = [
    caveats.CAVEATS, caveats.DROPPED_REGISTER, report.STRATUM_LABEL,
    [name for name, _ in report.GATES.values()], [m for _, m in report.GATES.values()],
    list(report.WARNINGS.values()), report.G7_SITUATIONS,
    [message for _, message in report.G3_SITUATIONS.values()], report.G6_OPERANDS_UNREADABLE,
    report.REVERSALS, report._IMPACT_PROSE, report.COMPARISON_ORDER, report.COMPARISON_ORDER_BY_SCORE,
    scoring.SCORING_EXCLUSIONS, ranking.RANKING_EXCLUSIONS, ranking.RANK_METHOD, ranking.RANK_TIE_NOTE,
    ranking.STAR_BASIS, ranking.LETTER_BASIS, ranking.COMPARISON_CAVEAT, roles.RANK_BASIS,
    figures.NOT_MEASURED_SENTENCE, [label for _, label in figures.BYLINE_LANES],
    [label for _, label in figures._SPAN_LANES],
    theses.DENOMINATOR_LADDER, theses.MATCH_RULES, theses.ROSTER_LIMITS, theses.THESIS_DENOMINATOR_CAVEAT,
    evaluations.EVALUATION_CAVEATS, evaluations.EVALUATION_LIMITS, evaluations.EVALUATION_STANCE,
    journals.JOURNAL_CAVEATS, journal_risk.JOURNAL_RISK_CAVEATS,
    impact_reference.STATUS_REASONS, impact_reference.PERCENTILE_METHOD,
]
# A catalog key whose source sentence was edited is an orphan: it would pair the
# new sentence with nothing and keep the old meaning around. Known strings are the
# literals in the code, the same with `_` read as a space (codes the report prints
# as words), and the constants above, some of which are assembled at import time.
known |= {s for group in rendered_constants for s in _strings(group)}
known |= {getattr(component, attr) for component in scoring.COMPONENTS
          for attr in ("basis", "description") if isinstance(getattr(component, attr, None), str)}
orphans = sorted(source[:80] for catalog in catalogs.values() for source in catalog
                 if source not in known)
check("no catalog key is an orphan: each is a string the code still contains", orphans, [])

untranslated_constants = sorted(
    {s[:80] for group in rendered_constants for s in _strings(group)
     if re.search(r"[A-Za-z]{2}", s) and s not in catalogs["zh"]})
check("every prose constant the report renders has a Chinese translation", untranslated_constants, [])


# ============================================================
# 4. The Chinese report, rendered strictly
# ============================================================

print("\n--- the Chinese report ---")

from check_your_advisor.profile import charts, html_report  # noqa: E402

TARGET = "Chen Xiuying"
INTERNAL = "Department of Hepatobiliary Surgery, Nanhai Medical University, Nanhai"
ORCID = "0000-0002-1825-0097"
NOW = datetime(2026, 7, 22, 20, 47, 11)


def author(name, affiliation=""):
    parts = name.split()
    return {"name": name, "last": parts[0], "fore": " ".join(parts[1:]), "initials": parts[-1][:1],
            "affiliation": affiliation, "email": "", "orcid": "", "equal_contrib": False,
            "is_corresponding": False}


def pi():
    entry = author(TARGET, INTERNAL)
    entry["orcid"] = ORCID
    return entry


def paper(pmid, byline, date, pi_index):
    return {"pmid": str(pmid), "title": f"Study {pmid} of hepatic stellate cell activation",
            "authors": byline, "authors_str": ", ".join(a["name"] for a in byline),
            "journal": "Hepatology Reports", "pub_date": date, "pub_year": date.split()[0],
            "volume": "", "issue": "", "pages": "", "doi": "", "pmc_id": "", "abstract": "",
            "pi_index": pi_index, "pi_evidence": "orcid", "pi_ambiguous": False}


def lab(identity=True):
    papers, pmid = [], 1000
    for index in range(8):
        member = author(f"Recur{index:02d} Person", INTERNAL)
        for offset, year in enumerate((2019 + index // 4, 2021, 2023)):
            byline = [member, pi()] if offset == 1 else [author(f"Filler{pmid:04d} Person"), member, pi()]
            papers.append(paper(pmid, byline, f"{year} Jun", len(byline) - 1))
            pmid += 1
    for index in range(12):
        papers.append(paper(pmid, [author(f"Once{index:02d} Person"), pi()], "2022 Mar", 1))
        pmid += 1
    senior = author("Senior Collaborator", INTERNAL)
    for year in (2020, 2024):
        papers.append(paper(pmid, [author(f"Junior{pmid:04d} Person"), pi(), senior], f"{year} Jun", 1))
        pmid += 1
    ident = {"author_name": TARGET, "orcid": ORCID if identity else "",
             "affiliation_keywords": ["Nanhai Medical University"] if identity else [],
             "email_domains": [], "require_affiliation_effective": False}
    return {
        "schema_version": 1, "generated_at": "2026-07-22T20:47:11", "position_filtered": False,
        "query": {"term": f'"{TARGET}"[Author]', "mindate": "2018/07/22", "maxdate": "2026/07/22",
                  "years_back": 8, "retmax": 500, "esearch_count": len(papers) + 5,
                  "pmids_returned": len(papers), "max_records": len(papers), "truncated": False},
        "identity": ident,
        "counts": {"fetched": len(papers), "verified": len(papers), "name_only": 0, "rejected": 3,
                   "by_evidence": {"orcid": len(papers)} if identity else {}},
        "fallback_fired": False, "papers": papers,
    }


unauthored = lab()
unauthored["papers"] = [paper(1, [], "2023 Jun", None), paper(2, [], "2024 Jun", None)]
reports = {
    "a clean corpus": report.build_report(lab(), {"author_name": TARGET}, None, NOW),
    "a corpus that raises warnings": report.build_report(lab(identity=False), {"author_name": TARGET}, None, NOW),
    "a refused corpus": report.build_report(unauthored, {"author_name": TARGET}, None, NOW),
}
check_true("the warning fixture raises a warning", reports["a corpus that raises warnings"]["warnings"])
check_true("the refusal fixture is refused", reports["a refused corpus"]["refused"])

for label, built in reports.items():
    english_md = report.render_markdown(built)
    view = report.localize(built, "zh")
    try:
        with i18n.strict():
            chinese_md = report.render_markdown(view)
            chinese_html = html_report.render_html(view, charts.figures_for_report(view))
        failure = ""
    except KeyError as exc:
        chinese_md = chinese_html = ""
        failure = str(exc)[:160]
    check(f"{label}: the Chinese report renders in strict mode", failure, "")
    check_true(f"{label}: ...and is in Chinese", HAN.search(chinese_md))
    check_true(f"{label}: ...as an HTML page that says so", '<html lang="zh-CN">' in chinese_html)
    check(f"{label}: the English report is unchanged by having been translated",
          report.render_markdown(built), english_md)
    check(f"{label}: both carry one JSON record", report.json_record(view), report.json_record(built))
    english_numbers = re.findall(r"\b\d+\b", re.sub(r"`[^`]*`", "", english_md))
    chinese_numbers = re.findall(r"\b\d+\b", re.sub(r"`[^`]*`", "", chinese_md))
    check_true(f"{label}: ...and the same numbers appear in both",
               set(english_numbers) <= set(chinese_numbers) | {"0", "1"})

clean = reports["a clean corpus"]
other = report.build_report(lab(), {"author_name": "Wang Wei"}, None, NOW)
comparison = report.build_comparison(
    [{"label": "lab A", "source": "a", "report": clean}, {"label": "lab B", "source": "b", "report": other}],
    now=NOW)
try:
    with i18n.strict():
        comparison_zh = report.render_comparison_markdown(report.localize_comparison(comparison, "zh"))
    failure = ""
except KeyError as exc:
    comparison_zh, failure = "", str(exc)[:160]
check("the Chinese comparison page renders in strict mode", failure, "")
check_true("...in Chinese", HAN.search(comparison_zh))

with tempfile.TemporaryDirectory() as tmp:
    paths_en = report.write_report(clean, tmp)
    paths_zh = report.write_report(report.localize(clean, "zh"), tmp)
    check("write_report: English Markdown and JSON", sorted(paths_en), ["json", "markdown"])
    check("...Chinese Markdown and no second JSON", sorted(paths_zh), ["markdown"])
    check_true("...under the .zh-CN name", paths_zh["markdown"].endswith(".zh-CN.md"))
    check("...beside the English one", Path(paths_zh["markdown"]).name.replace(".zh-CN", ""),
          Path(paths_en["markdown"]).name)


# ============================================================
# 5. The command line
# ============================================================

print("\n--- the command line ---")


def run(args, env_lang=None, locale="en_US.UTF-8"):
    env = {key: value for key, value in os.environ.items()
           if key not in ("LANG", "LC_ALL", "LC_MESSAGES", "LANGUAGE", i18n.ENV_VAR)}
    env["LANG"] = locale
    env["PYTHONIOENCODING"] = "utf-8"
    if env_lang:
        env[i18n.ENV_VAR] = env_lang
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "run.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    return done.returncode, done.stdout + done.stderr


with tempfile.TemporaryDirectory() as tmp:
    code_en, out_en = run(["clean-cache", "--output-dir", tmp])
    check("an English system: the command still runs", code_en, 1)
    check_false("...and logs in English", HAN.search(out_en))
    code_zh, out_zh = run(["clean-cache", "--output-dir", tmp, "--lang", "zh"])
    check_true("--lang zh logs in Chinese on the same system", HAN.search(out_zh))
    code_env, out_env = run(["clean-cache", "--output-dir", tmp], env_lang="zh")
    check_true("$CHECK_YOUR_ADVISOR_LANG=zh does the same", HAN.search(out_env))
    code_sys, out_sys = run(["clean-cache", "--output-dir", tmp], locale="zh_CN.UTF-8")
    check_true("a Chinese system locale does the same with no flag", HAN.search(out_sys))

code, help_en = run(["profile", "--help"])
check("profile --help exits 0", code, 0)
check_true("...lists --lang", "--lang" in help_en)
check_true("...in English on an English system", "composite score" in help_en.lower())
code, help_zh = run(["profile", "--help", "--lang", "zh"])
check_true("...and in Chinese when asked", "综合分" in help_zh)

# argparse's own words follow too: they reach the page through argparse's
# gettext hook, not through `zh()`, so the help text being Chinese proves nothing
# about them.
check_true("...argparse's usage prefix included", help_zh.startswith("用法："))
check_true("...and -h's own help", "显示本帮助并退出" in help_zh)
check_false("...with no English scaffolding left", re.search(r"\busage:|show this help", help_zh))
check_true("the English page keeps argparse's English", help_en.startswith("usage: "))


def _columns(text: str) -> int:
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


first, second = help_zh.splitlines()[:2]
check("a wrapped Chinese usage line lines up under the first, Chinese counted as two columns",
      len(second) - len(second.lstrip(" ")), _columns(first[:first.index("[")]))
code, out = run(["profile", "--bogus"], locale="zh_CN.UTF-8")
check("a rejected command line still exits 2", code, 2)
check_true("...and says why in Chinese", "错误：无法识别的参数：--bogus" in out)
code, out = run(["profile", "--bogus"])
check_true("...and in English on an English system", "error: unrecognized arguments: --bogus" in out)

import argparse  # noqa: E402

from check_your_advisor import cli  # noqa: E402
from check_your_advisor.locales.argparse_zh import ARGPARSE_ZH  # noqa: E402

import gettext  # noqa: E402

check_true("importing the CLI leaves argparse's gettext alone", argparse._ is gettext.gettext)
cli._translate_argparse()
for lang, prefix in (("zh", "用法："), ("en", "usage: "), (None, "usage: "), ("zh", "用法：")):
    i18n.set_language(lang)
    check(f"once installed, argparse's words follow the message language: {lang}",
          argparse.ArgumentParser(prog="p").format_usage()[:len(prefix)], prefix)
i18n.set_language(None)


def _argparse_messages() -> set[str]:
    """Every string the running argparse passes to its gettext hook."""
    tree = ast.parse(Path(argparse.__file__).read_text(encoding="utf-8"))
    return {node.args[0].value for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_"
            and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)}


# Present in some supported Pythons and not others: 3.10 formats headings and
# argument errors without the hook, and only 3.13 has deprecation warnings.
VERSION_SPECIFIC = {"%(heading)s:", "argument %(argument_name)s: %(message)s",
                    "%(prog)s: warning: %(message)s\n"}
messages = _argparse_messages()
check("every argparse key is a string this Python's argparse asks for, or a known version difference",
      sorted(key for key in ARGPARSE_ZH if key not in messages and key not in VERSION_SPECIFIC), [])
check_true("...the help page's scaffolding and the common errors among them",
           {"usage: ", "options", "show this help message and exit", "%(prog)s: error: %(message)s\n",
            "unrecognized arguments: %s", "invalid choice: %(value)r (choose from %(choices)s)"} <= messages)
check("each argparse translation keeps its %-conversions, in order",
      [key for key, value in ARGPARSE_ZH.items() if i18n.placeholders(key) != i18n.placeholders(value)], [])
check("no argparse key is also a catalog key, which would make it a second translation",
      sorted(set(ARGPARSE_ZH) & set(catalogs["zh"])), [])

# A log record carrying a Text, an exception carrying one, and plain data.
stream = io.StringIO()
handler = logging.StreamHandler(stream)
handler.addFilter(i18n.LogTranslator())
log = logging.getLogger("check_your_advisor.test_i18n")
log.addHandler(handler)
log.setLevel(logging.INFO)
log.propagate = False
i18n.set_language("en")
try:
    log.info("缓存文件不存在: %s", "/tmp/x.db")
    log.info("%s", ValueError(i18n.lazy_zh("学生评价表是空文件: {path}", path="e.csv")))
    log.info("%s", "王伟")
finally:
    i18n.set_language(None)
    log.removeHandler(handler)
lines = stream.getvalue().splitlines()
check_false("the log filter translates a Chinese message", HAN.search(lines[0]))
check_true("...keeps its argument", "/tmp/x.db" in lines[0])
check_false("...translates an exception that carries a template", HAN.search(lines[1]))
check("...and leaves data alone, even Chinese data", lines[2], "王伟")
check("no language set again: the suite's default", i18n.language(), None)

# A line logged from inside `build_report`, which pins its own language to English
# while it builds the payload, is still a message to the person at the terminal.
inner = io.StringIO()
inner_handler = logging.StreamHandler(inner)
inner_handler.addFilter(i18n.LogTranslator())
package_log = logging.getLogger("check_your_advisor")
package_log.addHandler(inner_handler)
previous_level = package_log.level
package_log.setLevel(logging.INFO)
try:
    for lang in ("zh", "en"):
        i18n.set_language(lang)
        report.build_report(lab(), {"author_name": TARGET}, None, NOW)
finally:
    i18n.set_language(None)
    package_log.removeHandler(inner_handler)
    package_log.setLevel(previous_level)
built_lines = [line for line in inner.getvalue().splitlines() if "journal" in line.lower() or "期刊" in line]
check_true("a log line from inside build_report follows the message language: Chinese",
           any(HAN.search(line) for line in built_lines))
check_true("...and English", any(line and not HAN.search(line) for line in built_lines))

print(f"\nSummary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
sys.exit(1 if _failed else 0)
