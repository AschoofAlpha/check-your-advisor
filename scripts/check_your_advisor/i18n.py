"""
Two languages, and one rule about which one a string is in.

Every string this package prints to a person is written once, in the language it
was first written in — the report in English, the command line in Chinese — and
wrapped in `en()` or `zh()` to say which. The wrapper returns the string in the
current language, looking the other one up in `check_your_advisor.locales`.

With no language set, every string comes back in its source language, which is
exactly what the package printed before it had two. That is the state of every
library caller and of the test suite, so neither depends on the machine it runs
on. Two places set a language, and they set it for different things:

- The command line sets the *message* language once, at startup, from `--lang`,
  then $CHECK_YOUR_ADVISOR_LANG, then the system locale (`resolve_language`). It
  is process-wide, so log lines from worker threads follow it too.
- The report renderers set the *report* language around their own work with
  `using()`, because `profile` and `compare` write one report in each language
  from the same computed numbers. That override is context-local and ends with
  the block, so the two reports cannot leak into each other or into the log.

A string missing from a catalog is printed in its source language rather than
raising — a half-translated page is a worse reading experience than a missing
word, but a page that is not written at all loses every number on it. Every miss
is recorded (`missing()`), and `strict()` turns a miss into a KeyError, which is
how the test suite proves no rendered string is untranslated.
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
import logging
import os
import re
import sys
from collections.abc import Iterator, Mapping, Sequence

__all__ = [
    "ENV_VAR",
    "LANGUAGES",
    "REPORT_LANGUAGES",
    "Joined",
    "LogTranslator",
    "Text",
    "clear_missing",
    "en",
    "in_language",
    "language",
    "lazy_en",
    "lazy_join",
    "lazy_zh",
    "missing",
    "normalize",
    "placeholders",
    "reason",
    "report_suffix",
    "resolve_language",
    "set_language",
    "strict",
    "system_language",
    "using",
    "zh",
]

LANGUAGES: tuple[str, ...] = ("zh", "en")

#: The order `profile` and `compare` write their reports in. English first: its
#: file names are the ones every earlier release wrote, so a script that picks up
#: `advisor_profile_<stamp>.html` keeps finding the same file.
REPORT_LANGUAGES: tuple[str, ...] = ("en", "zh")

ENV_VAR = "CHECK_YOUR_ADVISOR_LANG"

# Appended to a report's file stem. English carries none, for the reason above.
_REPORT_SUFFIX = {"en": "", "zh": ".zh-CN"}

_message_language: str | None = None
_report_language: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "check_your_advisor_report_language", default=None
)
_strict: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "check_your_advisor_strict", default=False
)
_missing: dict[str, set[str]] = {lang: set() for lang in LANGUAGES}
_catalogs: dict[str, dict[str, str]] | None = None


# ------------------------------------------------------------------
# Which language
# ------------------------------------------------------------------


def normalize(value: object) -> str | None:
    """'zh' or 'en' for a language tag or locale name, None for anything else.

    Accepts what a person types (`zh`, `en`, `zh-CN`, `en_US`) and what a locale
    variable holds (`zh_CN.UTF-8`, `en_GB.utf8`), and Windows' long names
    (`Chinese (Simplified)_China`). `C` and `POSIX` are not a language and come
    back None, so a caller can tell "English" from "unset".
    """
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text.startswith(("zh", "chinese")):
        return "zh"
    if text.startswith(("en", "english")):
        return "en"
    return None


def system_language(environ: Mapping[str, str] | None = None,
                    platform: str | None = None) -> str:
    """'zh' when the system's message locale is Chinese, 'en' otherwise.

    POSIX rules, as gettext reads them: the first of LC_ALL, LC_MESSAGES and LANG
    that is set decides, and LANGUAGE — a priority list — outranks it whenever
    that locale is a real one rather than C. A C or POSIX locale is English. With
    none of them set, Windows is asked for its UI language and anything else for
    the locale module's guess; a language that is neither Chinese nor English
    falls back to English, the package's other language.
    """
    env = os.environ if environ is None else environ
    effective = next((env[name] for name in ("LC_ALL", "LC_MESSAGES", "LANG")
                      if env.get(name)), "")
    if effective:
        if effective.split(".")[0].upper() in ("C", "POSIX"):
            return "en"
        preferred = (env.get("LANGUAGE") or "").split(":")[0]
        return normalize(preferred) or normalize(effective) or "en"
    if (platform or sys.platform) == "win32":
        ui = _windows_ui_language()
        if ui:
            return ui
    try:
        import locale

        guess = locale.getlocale()[0] or ""
    except (ValueError, TypeError, AttributeError):
        guess = ""
    return normalize(guess) or "en"


def _windows_ui_language() -> str | None:
    """The Windows display language, which is what a Windows user reads menus in."""
    try:
        import ctypes

        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - any failure here means "ask something else"
        return None
    # The low ten bits are the primary language; 0x04 is Chinese, every variant.
    return "zh" if (int(lang_id) & 0x3FF) == 0x04 else "en"


def _flag_value(argv: Sequence[str]) -> str | None:
    """The value of the last `--lang X` or `--lang=X` in argv, or None."""
    value = None
    for index, arg in enumerate(argv):
        if arg == "--lang" and index + 1 < len(argv):
            value = argv[index + 1]
        elif arg.startswith("--lang="):
            value = arg.split("=", 1)[1]
    return value


def resolve_language(argv: Sequence[str] | None = None,
                     environ: Mapping[str, str] | None = None,
                     platform: str | None = None) -> tuple[str, str]:
    """(language, what decided it) for the command line's messages.

    `--lang` first, then $CHECK_YOUR_ADVISOR_LANG, then the system locale. A value
    that names neither language is skipped rather than obeyed, so a typo falls
    through to the next source instead of silently printing English.
    """
    env = os.environ if environ is None else environ
    flagged = normalize(_flag_value(argv or ()))
    if flagged:
        return flagged, "--lang"
    configured = normalize(env.get(ENV_VAR))
    if configured:
        return configured, ENV_VAR
    return system_language(env, platform), "system locale"


def set_language(lang: str | None) -> None:
    """Set the message language for the whole process. None restores source text."""
    global _message_language
    _message_language = normalize(lang) if lang is not None else None


def language() -> str | None:
    """The language strings come back in now, or None for their source language."""
    return _report_language.get() or _message_language


@contextlib.contextmanager
def using(lang: str) -> Iterator[None]:
    """Render in `lang` inside this block, whatever the message language is."""
    resolved = normalize(lang)
    if resolved is None:
        raise ValueError(f"unknown language {lang!r}; expected one of {LANGUAGES}")
    token = _report_language.set(resolved)
    try:
        yield
    finally:
        _report_language.reset(token)


def in_language(lang: str):
    """Decorator: run the function as if inside `using(lang)`.

    For the functions that build a payload rather than render one. A payload is
    data — it is written to JSON once and rendered into every language later — so
    whatever sentence it holds has to be in its source language whatever the
    caller's message language is, and pinning the language at the boundary is
    what makes that true of every helper underneath without each one having to
    remember it.
    """
    def decorate(func):
        @functools.wraps(func)
        def pinned(*args, **kwargs):
            with using(lang):
                return func(*args, **kwargs)
        return pinned
    return decorate


def report_suffix(lang: str) -> str:
    """'' for English, '.zh-CN' for Chinese: what goes between a report's stem and its extension."""
    return _REPORT_SUFFIX[normalize(lang) or "en"]


# ------------------------------------------------------------------
# Lookup
# ------------------------------------------------------------------


class Text(str):
    """A finished string that remembers how it was built.

    It *is* its source-language rendering — equal to it, hashed like it, written
    to JSON as it — so a payload holding one reads exactly as it did when it held
    a plain string. What it adds is the template and the fields, which lets a
    renderer working in the other language rebuild the sentence instead of
    looking up a string that, with the numbers already in it, no catalog holds.
    Pass it to `en()` or `zh()` as it is; anything that rebuilds it with `+` or
    `.strip()` gets a plain string back and loses the template, on purpose.
    """

    template: str
    fields: dict[str, object]
    source: str

    def __new__(cls, template: str, fields: Mapping[str, object], source: str) -> Text:
        value = template.format(**fields) if fields else template
        made = super().__new__(cls, value)
        made.template, made.fields, made.source = template, dict(fields), source
        return made

    def __reduce__(self):  # pickles as the class it is, template and all
        return (Text, (self.template, self.fields, self.source))


class Joined(Text):
    """Sentences or clauses joined when rendered, each translated on its own.

    Its value is `sep.join(parts)`, so it reads exactly as the plain join it
    replaces. A sentence built from optional parts cannot be one template —
    there would be one per combination — and a plain join loses every part's
    template. Rendered in Chinese, the space between two sentences is dropped:
    Chinese does not separate sentences with one.
    """

    parts: tuple[object, ...]
    sep: str

    def __new__(cls, parts: Sequence[object], sep: str = " ") -> Joined:
        made = str.__new__(cls, sep.join(str(part) for part in parts))
        made.template, made.fields, made.source = str(made), {}, "join"
        made.parts, made.sep = tuple(parts), sep
        return made

    def __reduce__(self):
        return (Joined, (self.parts, self.sep))

    def render(self, target: str | None = None) -> str:
        """The parts in `target`, or in the current language when none is given."""
        target = language() if target is None else target
        sep = (_translate(self.sep, self.sep.source, {}, target) if isinstance(self.sep, Text)
               else self.sep)
        if sep == " " and target == "zh":
            sep = ""
        return sep.join(_translate(part, part.source, {}, target) if isinstance(part, Text)
                        else str(part) for part in self.parts)


def lazy_join(parts: Sequence[object], sep: str = " ") -> Joined:
    """`sep.join(parts)` that keeps every part translatable; see `Joined`."""
    return Joined(parts, sep)


def lazy_en(template: str, /, **fields: object) -> Text:
    """An English sentence a renderer may later need in Chinese; see `Text`.

    A `Text` handed back in with no fields is returned as it is: wrapping it again
    would make its finished English the template and lose the real one.
    """
    if isinstance(template, Text) and not fields:
        return template
    return Text(template, fields, "en")


def lazy_zh(template: str, /, **fields: object) -> Text:
    """A Chinese sentence a renderer may later need in English; see `Text`."""
    if isinstance(template, Text) and not fields:
        return template
    return Text(template, fields, "zh")


def _catalog(lang: str) -> dict[str, str]:
    global _catalogs
    if _catalogs is None:
        from check_your_advisor.locales import load_catalogs

        _catalogs = load_catalogs()
    return _catalogs[lang]


_translated_values: dict[str, frozenset[str]] = {}


def _translations(lang: str) -> frozenset[str]:
    """Every translation the catalog for `lang` holds, as finished text."""
    if lang not in _translated_values:
        _translated_values[lang] = frozenset(_catalog(lang).values())
    return _translated_values[lang]


_CURRENT = object()


def _translate(text: str, source: str, fields: Mapping[str, object],
               target: object = _CURRENT) -> str:
    """`text` in `target` (the current language unless given), fields filled in."""
    if not isinstance(text, str) or not text:
        # A number or an empty cell handed over by a renderer that wraps every
        # value it prints: there is nothing to translate and nothing to record.
        return text
    if target is _CURRENT:
        target = language()
    if isinstance(text, Joined):
        return text.render(target)
    if isinstance(text, Text):
        # The sentence's own source wins over the wrapper it was handed to: a
        # renderer wraps a payload string in `en()` without knowing whether the
        # producer wrote it in English or in Chinese.
        text, source, fields = text.template, text.source, {**text.fields, **fields}
    if fields and target is not None:
        # A field that is itself a sentence ("unknown", a reason, a nested clause)
        # is rendered in the same language as the sentence around it.
        fields = {name: _translate(value, value.source, {}, target) if isinstance(value, Text)
                  else value for name, value in fields.items()}
    if target is None or target == source:
        out = text
    else:
        found = _catalog(target).get(text)
        if found is None and text in _translations(target):
            # Already in the target language: a renderer wrapped a value that a
            # wrapped literal had produced. Twice is the same as once.
            found = text
        if found is None:
            _missing[target].add(text)
            if _strict.get():
                raise KeyError(f"no {target} translation for: {text!r}")
            out = text
        else:
            out = found
    return out.format(**fields) if fields else out


def en(text: str, /, **fields: object) -> str:
    """`text` is English. Returns it in the current language, `fields` filled in.

    Without fields the string is returned untouched, braces and all, so a literal
    `{` needs doubling only in a string that is also given fields.
    """
    return _translate(text, "en", fields)


def zh(text: str, /, **fields: object) -> str:
    """`text` is Chinese. Returns it in the current language, `fields` filled in."""
    return _translate(text, "zh", fields)


def reason(exc: BaseException) -> object:
    """What an exception says, as a sentence that can still be translated.

    The loaders raise with a `Text` (`lazy_zh`) so that a report note quoting the
    failure, written later in either language, can render it in that language;
    `str(exc)` would freeze it in the language it was written in.
    """
    if exc.args and isinstance(exc.args[0], Text):
        return exc.args[0]
    return str(exc)


def _quiet(value: object, target: str | None) -> object:
    """A log argument in the message language, when it is a sentence this package wrote.

    Only a `Text`, an exception carrying one, or a string that is itself a catalog
    key is replaced. Anything else — a path, a title, a count, somebody's name —
    is data and is left exactly as it is, and a miss is not recorded: an argument
    is not a message, so an absent translation is not a gap.
    """
    if isinstance(value, BaseException) and value.args and isinstance(value.args[0], Text):
        value = value.args[0]
    if isinstance(value, Text):
        return _translate(value, value.source, {}, target)
    if isinstance(value, str) and target is not None:
        source = "en" if target == "zh" else "zh"
        if value in _catalog(target) and (source == "zh") == bool(_HAN.search(value)):
            return _translate(value, source, {}, target)
    return value


_HAN = re.compile(r"[\u4e00-\u9fff]")


class LogTranslator(logging.Filter):
    """Puts each log record into the message language just before it is written.

    The command line's log messages are written in Chinese at the call site, as
    they always were, and translated here rather than there: one filter on the
    handlers instead of a wrapper around three hundred `logger` calls, and a
    message that has not been given a translation still prints — in Chinese — and
    is recorded by `missing()`. Messages in English (a library's own, or this
    package's few) are left alone; so is every argument that is data.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # The message language, never a report's: `build_report` pins its own
        # language to English while it runs, and a log line written from inside
        # it is still a message to the person at the terminal.
        target = _message_language
        if target is None:
            return True
        msg = record.msg
        if isinstance(msg, Text):
            record.msg = _translate(msg, msg.source, {}, target)
        elif isinstance(msg, str) and target == "en" and _HAN.search(msg):
            record.msg = _translate(msg, "zh", {}, target)
        if isinstance(record.args, tuple):
            record.args = tuple(_quiet(arg, target) for arg in record.args)
        elif isinstance(record.args, Mapping):
            record.args = {key: _quiet(arg, target) for key, arg in record.args.items()}
        return True


def missing(lang: str | None = None) -> set[str]:
    """Source strings that had no translation when last asked for, by target language."""
    if lang is None:
        return set().union(*_missing.values())
    return set(_missing[lang])


def clear_missing() -> None:
    for seen in _missing.values():
        seen.clear()


@contextlib.contextmanager
def strict() -> Iterator[None]:
    """Raise KeyError on any untranslated string inside this block."""
    token = _strict.set(True)
    try:
        yield
    finally:
        _strict.reset(token)


# ------------------------------------------------------------------
# What a translation has to keep
# ------------------------------------------------------------------

_FIELD = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)(?:![rsa])?(?::[^{}]*)?\}(?!\})")
_PERCENT = re.compile(r"%(?:\([A-Za-z_]+\))?[-+#0]*(?:\d+|\*)?(?:\.\d+)?[sdifrxXeEgGc%]")


def placeholders(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(sorted `{field}` names, %-conversions in order) that a translation must keep.

    The `{field}` names may be reordered by a translation, because they are filled
    by name. The %-conversions may not: logging fills them by position, so a
    translation that swaps two of them prints each value in the other's place.
    The space flag is not recognised, so prose such as "40% retrieved" is not read
    as a `% r` conversion; nothing in this package formats with it.
    """
    fields = tuple(sorted(set(_FIELD.findall(text))))
    percents = tuple(spec for spec in _PERCENT.findall(text) if spec != "%%")
    return fields, percents
