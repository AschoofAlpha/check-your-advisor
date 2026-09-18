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
    # esearch 每页取多少条 PMID。命中数超过它时自动用 retstart 翻页，
    # 所以这个值只决定发几次请求，不再是取数的天花板。
    "retmax": 500,
    # 一次检索最多取回多少条 PMID，翻页的总预算。默认 10000 是 NCBI 自己的
    # 硬上限(esearch 对 PubMed 只能取到前 10000 条，2026-08-22 查 E-utilities
    # 文档)，再往上调只会翻出空页。命中数超过它时会自动加机构条件收窄；仍然
    # 超出就取到上限为止，报告里如实印「取回 N / 共 M」，不拒绝出报告。
    "max_records": 10000,
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
        # OpenAlex 的作者 ID（形如 A5023888391）。空串 = 没做过 OpenAlex 消歧。
        # 与上面四项并列但来源不同：那四项是用户自己的断言，这一项是 OpenAlex
        # 的作者聚类结果，报告里分开印。用 `harvest --resolve-openalex` 查候选，
        # 认出是哪一位后用 `--openalex-author-id` 指定；候选多于一个时工具不替
        # 你选，因为按作品数挑等于用产量代替身份。
        "openalex_author_id": "",
    },
    # 第二语料源。两个开关都默认关闭：多打一个 API 是用户的选择，不是默认行为。
    # OpenAlex 免费、无密钥，`email` 只换一条更宽松的限速通道（沿用 citations
    # 的做法，不是认证）。
    "openalex": {
        # 按 author_name + affiliation 查 OpenAlex authors 接口拿候选作者。
        "resolve_author": False,
        # 把该作者 ID 名下的 works 拉回来，与 PubMed 语料按 DOI → PMID →
        # 标题+年份 去重合并。需要先有一个确定的 openalex_author_id。
        "merge_works": False,
        # 一次最多取回多少条 works，翻页的总预算。上限存在只是为了不让一个
        # 认错的作者 ID 把一个联盟的全部产出拉进来；取不满会如实印出
        # 「取回 N / 共 M」，不拒绝出报告。
        "max_works": 2000,
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
    # 身份证据的比例边界，画像报告用它判定「OpenAlex 作者 ID 算不算这份语料的
    # 身份证据」。默认 0.5 来自 `profile.report.MIN_OPENALEX_RECORD_SHARE`，
    # 报告第 1 节会把当前生效值和实测比例并排印出来。
    #
    # 它和 scoring.weights 放在配置里的理由完全一样：这个数没有任何数据能标定，
    # 那就不该藏在函数体里。旧代码是「一条记录带上这个 ID 就算数」——同样是一个
    # 阈值，只是它等于 1/N、没有名字、也印不出来，于是 6 条里 5 条纯姓名匹配的
    # 语料把身份警告整个清零了。
    "identity_evidence": {"min_openalex_record_share": 0.5},
    # Three hand-filled local tables, all empty by default and none of them ever
    # fetched. There is no crawler in this package: impact factors and CAS
    # partitions live in licensed databases, degree theses live in subscription
    # libraries, student evaluations live on forums and review pages, and all of
    # them defend against automation. So the tool defines the schema, says which
    # rows this corpus needs looked up, joins whatever comes back and prints
    # where it came from — the lookup itself is the user's.
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
    # `evaluations.table_path` is a CSV the user types up by hand from whatever
    # public pages carry statements about this advisor. 评价来源 and
    # 数据获取日期 are required columns for the same reason 版本来源 is required
    # above: an unattributed, undated sentence about a named person cannot be
    # checked by anyone later. Section 20 prints those rows verbatim beside
    # their source and computes nothing over them — no sentiment, no average, no
    # rating, and no contribution to the composite score.
    #
    # Empty string means "not supplied", and the report says so in the section
    # rather than leaving the column blank. --journal-table, --thesis-roster and
    # --evaluation-table override these per run.
    "journals": {"table_path": ""},
    "theses": {"roster_path": ""},
    "evaluations": {"table_path": ""},
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
        "AUTHOR_OPENALEX_ID": ("openalex_author_id", str),
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
