"""
Configuration loading for the paper downloader.

Default settings are intentionally safe to commit. Secrets and local choices can
come from config.json, a custom JSON file, or environment variables.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

# The score weight table is defined once, in `profile.scoring`, and imported
# here rather than retyped. A second copy of those six numbers is how a config
# default and the module that consumes it drift apart without anyone noticing;
# and a weight table that disagrees with the one the report prints would defeat
# the entire point of printing it. `profile.scoring` is pure and standard-library
# only, so this import costs nothing but the module load.
from check_your_advisor.profile.scoring import DEFAULT_WEIGHTS as DEFAULT_SCORE_WEIGHTS

DEFAULT_CONFIG: dict[str, Any] = {
    "author_name": "",
    "affiliation": "",
    "api_key": "",
    "email": "",
    "years_back": 5,
    # esearch 单次最多取回多少条 PMID。命中数超过它会触发告警,
    # 并在配置了 affiliation_keywords 时自动加机构条件收窄。
    "retmax": 500,
    "output_dir": "pubmed_results",
    "pdf_dir": "pubmed_results/pdfs",
    "download_pdfs": True,
    "delay_seconds": 0.15,
    "max_workers": 4,
    "cache_db": "pubmed_results/paper_cache.db",
    "log_level": "INFO",
    "proxy_list": [],
    # Author disambiguation. Leave empty to accept every name match (not
    # recommended for common names). See config.example.json for a worked
    # example showing how to express institution name variants.
    "author_identity": {
        "affiliation_keywords": [],
        "email_domains": [],
        "orcid": "",
        "require_affiliation": False,
    },
    # Composite score. `weights` is the full component table, defaulting to
    # `scoring.DEFAULT_WEIGHTS` — flat, every component 1.0, which is the only
    # default that asserts nothing about relative importance.
    #
    # It lives in the config file rather than inside the scoring function
    # because no weighting of these components is justified by the data. The
    # answer to that is not a better set of magic numbers, it is putting the
    # numbers where the reader can see and edit them: whatever table ends up in
    # effect is printed verbatim in the report, so a score always travels with
    # the assumptions that produced it.
    #
    # Override the whole table or any single component; a partial override is
    # merged onto the defaults. Set a component to 0.0 to drop it from the
    # score. Unknown component names and negative weights are refused at load
    # rather than ignored.
    "scoring": {"weights": dict(DEFAULT_SCORE_WEIGHTS)},
    # Two hand-filled local tables, both empty by default and neither ever
    # fetched. There is no crawler in this package: impact factors and CAS
    # partitions live in licensed databases, degree theses live in subscription
    # libraries, and both defend against automation. So the tool defines the
    # schema, says which rows this corpus needs looked up, joins whatever comes
    # back and prints where it came from — the lookup itself is the user's.
    #
    # `journals.table_path` is a CSV in `journals.SCHEMA`'s shape. Generate the
    # blank with `check-your-advisor journal-worklist`, which pre-fills only the
    # journals this corpus actually uses (a couple of dozen, not the twenty
    # thousand indexed ones) and leaves the metric columns empty. 版本来源 and
    # 数据获取日期 are required columns, and the loader refuses a table without
    # them: LetPub's search-results page shows the 民间版 partition by default,
    # ablesci labels 官方版 and 新锐版 separately, and a partition number with
    # no edition beside it cannot be checked by anyone a year later.
    #
    # `theses.roster_path` is a CSV exported by hand from CNKI or 万方 and is
    # the only file that carries graduates who never published: PubMed contains
    # people who published, so it cannot enumerate them even in principle. Add
    # a 学生姓名拼音 column — optional in the schema, decisive in practice, and
    # without it a Chinese roster cannot be joined to romanised bylines at all.
    #
    # Empty string means "not supplied", and the report says so in the section
    # rather than leaving the column blank. --journal-table and --thesis-roster
    # override these per run.
    "journals": {"table_path": ""},
    "theses": {"roster_path": ""},
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _list(value: str) -> list[str]:
    return [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Config file must contain a JSON object: {path}")
    return data


def load_config(config_path: str | None = None) -> dict[str, Any]:
    """
    Load configuration in this order:
    1. built-in safe defaults
    2. config.json or --config path
    3. environment variables
    """
    config = copy.deepcopy(DEFAULT_CONFIG)

    path: Path | None = None
    if config_path:
        path = Path(config_path)
    else:
        candidate = Path("config.json")
        if candidate.exists():
            path = candidate

    if path:
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        _deep_merge(config, _load_json(path))

    env_map = {
        "PUBMED_AUTHOR_NAME": ("author_name", str),
        "PUBMED_AFFILIATION": ("affiliation", str),
        "PUBMED_API_KEY": ("api_key", str),
        "UNPAYWALL_EMAIL": ("email", str),
        "PUBMED_YEARS_BACK": ("years_back", int),
        "PAPER_DOWNLOADER_OUTPUT_DIR": ("output_dir", str),
        "PAPER_DOWNLOADER_PDF_DIR": ("pdf_dir", str),
        "PAPER_DOWNLOADER_CACHE_DB": ("cache_db", str),
        "PAPER_DOWNLOADER_MAX_WORKERS": ("max_workers", int),
        "PAPER_DOWNLOADER_LOG_LEVEL": ("log_level", str),
        "PAPER_DOWNLOADER_DOWNLOAD_PDFS": ("download_pdfs", _bool),
    }
    for env_name, (config_key, caster) in env_map.items():
        if env_name in os.environ and os.environ[env_name].strip():
            config[config_key] = caster(os.environ[env_name])

    identity = config.setdefault("author_identity", {})
    nested_env = {
        "AUTHOR_ORCID": ("orcid", str),
        "AUTHOR_REQUIRE_AFFILIATION": ("require_affiliation", _bool),
        "AUTHOR_AFFILIATION_KEYWORDS": ("affiliation_keywords", _list),
        "AUTHOR_EMAIL_DOMAINS": ("email_domains", _list),
    }
    for env_name, (identity_key, caster) in nested_env.items():
        if env_name in os.environ and os.environ[env_name].strip():
            identity[identity_key] = caster(os.environ[env_name])

    if config.get("output_dir") != DEFAULT_CONFIG["output_dir"]:
        if config.get("pdf_dir") == DEFAULT_CONFIG["pdf_dir"]:
            config["pdf_dir"] = os.path.join(config["output_dir"], "pdfs")
        if config.get("cache_db") == DEFAULT_CONFIG["cache_db"]:
            config["cache_db"] = os.path.join(config["output_dir"], "paper_cache.db")

    return config
