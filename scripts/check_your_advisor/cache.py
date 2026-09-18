"""
缓存模块（SQLite）
==================
替代原代码中仅通过 os.path.exists(pdf_path) 检查的简陋缓存。

改进：
- 记录每篇论文的元数据（DOI、PMID、下载状态、来源、时间）
- 支持过期清理
- 支持查询命中/未命中统计

Two caches, one file, no shared rows
------------------------------------
`PaperCache` caches PDF *files*: keyed on PMID, and a hit is only a hit if the
file is still on disk. `FetchCache` caches what a keyless API *said*: keyed on
which API was asked and what about, and a hit is only a hit if it is young
enough. They live in one SQLite file because a run already carries one cache
path around, and in two tables because they answer different questions.
"""

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("check_your_advisor.cache")

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS paper_cache (
    doi TEXT,
    pmid TEXT,
    title TEXT,
    pdf_path TEXT,
    source TEXT,
    status TEXT DEFAULT 'pending',
    file_size_bytes INTEGER DEFAULT 0,
    created_at REAL,
    updated_at REAL,
    PRIMARY KEY (pmid)
);
CREATE INDEX IF NOT EXISTS idx_doi ON paper_cache(doi);
CREATE INDEX IF NOT EXISTS idx_status ON paper_cache(status);
"""


class PaperCache:
    def __init__(self, db_path: str = "paper_cache.db"):
        self.db_path = db_path
        db_parent = Path(db_path).parent
        if str(db_parent) not in {"", "."}:
            db_parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False 允许跨线程使用；加锁保证串行访问
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._init_db()
        self.hits = 0
        self.misses = 0

    def _init_db(self):
        with self._lock:
            cursor = self._conn.cursor()
            cursor.executescript(CREATE_TABLE_SQL)
            self._conn.commit()

    def lookup(self, pmid: str) -> dict | None:
        """查询缓存。返回 dict 或 None。"""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT * FROM paper_cache WHERE pmid = ?", (pmid,))
            row = cursor.fetchone()

        if row and row["status"] == "downloaded" and row["pdf_path"]:
            if Path(row["pdf_path"]).exists():
                self.hits += 1
                logger.debug("  缓存命中: PMID %s", pmid)
                return dict(row)
            else:
                logger.warning("  缓存记录存在但文件丢失: %s", row["pdf_path"])
                self.update(pmid, status="pending", pdf_path="")
        self.misses += 1
        return None

    def update(self, pmid: str, **kwargs):
        """更新或插入缓存记录"""
        now = time.time()
        with self._lock:
            existing = self._conn.execute(
                "SELECT pmid FROM paper_cache WHERE pmid = ?", (pmid,)
            ).fetchone()

            if existing:
                sets = ", ".join(f"{k} = ?" for k in kwargs)
                vals = list(kwargs.values()) + [now, pmid]
                self._conn.execute(
                    f"UPDATE paper_cache SET {sets}, updated_at = ? WHERE pmid = ?",
                    vals,
                )
            else:
                kwargs["pmid"] = pmid
                kwargs["created_at"] = now
                kwargs["updated_at"] = now
                cols = ", ".join(kwargs.keys())
                placeholders = ", ".join("?" for _ in kwargs)
                self._conn.execute(
                    f"INSERT INTO paper_cache ({cols}) VALUES ({placeholders})",
                    list(kwargs.values()),
                )
            self._conn.commit()

    def mark_downloaded(
        self,
        pmid: str,
        pdf_path: str,
        source: str,
        file_size: int = 0,
        doi: str = "",
        title: str = "",
    ):
        self.update(
            pmid,
            doi=doi,
            title=title,
            status="downloaded",
            pdf_path=pdf_path,
            source=source,
            file_size_bytes=file_size,
        )

    def mark_failed(self, pmid: str, doi: str = "", title: str = ""):
        self.update(pmid, doi=doi, title=title, status="all_sources_failed")

    def cleanup_expired(self, max_age_days: int = 90) -> int:
        """清理超过指定天数的失败记录（下载成功的不清理）。返回删除条数。"""
        cutoff = time.time() - max_age_days * 86400
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                "DELETE FROM paper_cache WHERE status = 'all_sources_failed' AND updated_at < ?",
                (cutoff,),
            )
            deleted = cursor.rowcount
            self._conn.commit()
        if deleted:
            logger.info("  清理了 %d 条过期失败记录", deleted)
        return deleted

    def stats(self) -> dict:
        with self._lock:
            cursor = self._conn.cursor()
            total = cursor.execute("SELECT COUNT(*) FROM paper_cache").fetchone()[0]
            downloaded = cursor.execute(
                "SELECT COUNT(*) FROM paper_cache WHERE status = 'downloaded'"
            ).fetchone()[0]
            failed = cursor.execute(
                "SELECT COUNT(*) FROM paper_cache WHERE status = 'all_sources_failed'"
            ).fetchone()[0]
        return {
            "total": total,
            "downloaded": downloaded,
            "failed": failed,
            "session_hits": self.hits,
            "session_misses": self.misses,
        }

    def close(self):
        with self._lock:
            self._conn.close()


# ======================================================================
# FetchCache — what an API said, and the day it said it
# ======================================================================

CREATE_FETCH_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS fetch_cache (
    api        TEXT NOT NULL,
    query      TEXT NOT NULL,
    payload    TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (api, query)
);
"""


class FetchCache:
    """A dated store of one API's answer to one question.

    `cite` and `journal-risk` re-ask the same keyless endpoints about the same
    DOIs and ISSNs on every run. This is where the answer is kept between runs.

    Three properties, each of which is a rule this package states elsewhere:

    - **The key is (api, query).** `api` names the endpoint that answered —
      `citations.openalex`, `journal_risk.openalex` — and `query` names what it
      was asked about. Both halves are required, so Semantic Scholar's answer
      about a DOI is never served as OpenAlex's, and the two modules' separate
      uses of api.openalex.org never collide.

    - **`fetched_at` is the day the answer really arrived**, stored as the same
      ISO string the records on disk carry, and handed back verbatim on a hit.
      Every record in this package travels with its own collection date; a cache
      that restamped a hit with today would turn that date into a lie exactly
      when it started working. Callers date their record from what `get` returns,
      not from the clock.

    - **Expiry is `--max-age-days`**, with the three rules
      `citations.reusable_records` already states: `<= 0` serves nothing (which
      is the default, so an unmodified run still re-collects everything), a stamp
      older than `now - max_age_days` serves nothing, and a stamp that will not
      parse serves nothing either. There is no second notion of freshness here.

    Nothing is evicted. A re-fetch overwrites its row in place, so the table is
    bounded by how many distinct things were ever asked about, not by how often.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        if db_path != ":memory:":
            db_parent = Path(db_path).parent
            if str(db_parent) not in {"", "."}:
                db_parent.mkdir(parents=True, exist_ok=True)
        # Same reasoning as PaperCache: the fetch paths are thread pools.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(CREATE_FETCH_TABLE_SQL)
            self._conn.commit()
        self.hits = 0
        self.misses = 0

    def get(
        self,
        api: str,
        query: str,
        max_age_days: int,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """`{"value": ..., "fetched_at": "<iso>"}` for a young enough row, else None.

        `max_age_days <= 0` returns None without a lookup and counts as neither a
        hit nor a miss: the cache is switched off, which is not the same event as
        asking it and finding nothing.

        A hit's `value` may itself be None or 0 — a null field and a zero count
        are both real answers — so callers must test the returned dict against
        None rather than testing `value` for truth.
        """
        if max_age_days <= 0 or not api or not query:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT payload, fetched_at FROM fetch_cache WHERE api = ? AND query = ?",
                (api, query),
            ).fetchone()
        if row is None:
            self.misses += 1
            return None

        stamp = _parse_stamp(row["fetched_at"])
        if stamp is None:
            # Unparseable means refetch. A row whose age cannot be established is
            # not a row whose age is acceptable.
            logger.debug("  [%s] 缓存时间戳无法解析，按未命中处理: %r", api, row["fetched_at"])
            self.misses += 1
            return None
        if stamp < (now or datetime.now()) - timedelta(days=max_age_days):
            self.misses += 1
            return None

        try:
            value = json.loads(row["payload"])
        except (TypeError, ValueError) as exc:
            logger.warning("  [%s] 缓存内容损坏，按未命中处理: %s", api, exc)
            self.misses += 1
            return None

        self.hits += 1
        return {"value": value, "fetched_at": row["fetched_at"]}

    def put(
        self,
        api: str,
        query: str,
        value: Any,
        fetched_at: str | None = None,
    ) -> str:
        """Store one answer; return the stamp written, for the caller's record.

        The return value is the point of the signature: the caller writes that
        exact string into the record it emits, so a record dated by a live fetch
        and a record dated by a cache hit are produced the same way.
        """
        stamp = fetched_at if fetched_at is not None else datetime.now().isoformat(
            timespec="seconds")
        with self._lock:
            self._conn.execute(
                "INSERT INTO fetch_cache (api, query, payload, fetched_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(api, query) DO UPDATE SET payload = excluded.payload, "
                "fetched_at = excluded.fetched_at",
                (api, query, json.dumps(value, ensure_ascii=False), stamp),
            )
            self._conn.commit()
        return stamp

    def stats(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute("SELECT COUNT(*) FROM fetch_cache").fetchone()[0]
        return {"rows": rows, "hits": self.hits, "misses": self.misses}

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _parse_stamp(value: Any) -> datetime | None:
    """An ISO timestamp, or None. Same rule as `citations._parse_stamp`."""
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
