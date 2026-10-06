"""
Translation catalogs, one module per part of the package.

A module named `zh_<part>.py` holds `MESSAGES`, a dict from an English source
string to its Chinese translation; `en_<part>.py` holds the reverse, Chinese
source to English. `check_your_advisor.i18n` merges every module of each kind on
first use, so adding a part needs no list edited anywhere.

The key is the source string exactly as the code writes it, `{field}` names and
%-conversions included, so changing a source sentence orphans its translation
instead of silently pairing the new sentence with the old meaning.
tests/test_i18n.py finds both: a source string with no translation, and a
translation whose source no longer exists.

Data only, standard library only, nothing executed at import beyond the dicts.
"""

from __future__ import annotations

import pkgutil
from importlib import import_module

__all__ = ["catalog_modules", "load_catalogs"]


def catalog_modules() -> dict[str, list[str]]:
    """{target language: [module names]} for every catalog module in this package."""
    found: dict[str, list[str]] = {"zh": [], "en": []}
    for info in pkgutil.iter_modules(__path__):
        prefix = info.name.split("_", 1)[0]
        if prefix in found and "_" in info.name:
            found[prefix].append(info.name)
    return {lang: sorted(names) for lang, names in found.items()}


def load_catalogs() -> dict[str, dict[str, str]]:
    """{target language: {source string: translation}}, merged across modules.

    Two modules translating one source string differently is a ValueError, not a
    last-one-wins: the same sentence printed two ways in one report is the kind of
    drift this whole arrangement exists to make impossible.
    """
    merged: dict[str, dict[str, str]] = {"zh": {}, "en": {}}
    origin: dict[str, dict[str, str]] = {"zh": {}, "en": {}}
    for lang, names in catalog_modules().items():
        for name in names:
            messages = getattr(import_module(f"{__name__}.{name}"), "MESSAGES", {})
            for source, translation in messages.items():
                seen = merged[lang].get(source)
                if seen is not None and seen != translation:
                    raise ValueError(
                        f"{name} and {origin[lang][source]} translate one {lang} source "
                        f"string differently: {source!r}"
                    )
                merged[lang][source] = translation
                origin[lang][source] = name
    return merged
