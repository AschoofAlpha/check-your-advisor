"""
缓存模块（SQLite）
==================
替代原代码中仅通过 os.path.exists(pdf_path) 检查的简陋缓存。

改进：
- 记录每篇论文的元数据（DOI、PMID、下载状态、来源、时间）
- 支持过期清理
- 支持查询命中/未命中统计
"""

import logging
import sqlite3
import threading
import time
from pathlib import Path

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
