"""
并发下载引擎
============
核心改进（对比原 L735-766 的串行 download_pdf_multi）：

原代码问题：
  for name, fn in strategies:   # L759 — 11 个源完全串行
      try:
          if fn():
              return True, name
      except Exception:          # L763 — 又一个静默异常
          pass

改进方案：
1. 多源竞速：8 个开放获取源并行请求，第一个通过身份校验的胜出
2. 使用 concurrent.futures.ThreadPoolExecutor 实现多论文并发下载
3. 下载后校验 PDF 正文与目标论文是否一致，疑似误匹配隔离
"""

import logging
import os
import re
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from . import download_sources as src
from .cache import PaperCache
from .http_client import RobustHTTPClient
from .pdf_utils import pdf_text_extraction_available, validate_pdf_matches_paper

logger = logging.getLogger("check_your_advisor.engine")


class DownloadEngine:
    """
    并发下载引擎。

    用法：
        engine = DownloadEngine(email="you@example.com", max_workers=4)
        results = engine.download_batch(papers, pdf_dir="./pdfs")
    """

    def __init__(
        self,
        email: str = "",
        max_workers: int = 4,
        proxy_list: list[str] | None = None,
        cache_db: str = "paper_cache.db",
        validate_identity: bool = True,
    ):
        self.email = email
        self.max_workers = max_workers
        self.validate_identity = validate_identity

        # 身份校验是本工具的核心卖点之一。PyMuPDF 缺失时它会退化成 no-op，
        # 而下载依然报成功 —— 那正是本工具试图消除的「成功但错误」。
        # 因此在这里一次性大声告警，而不是把它埋进每篇论文的字段里。
        if validate_identity and not pdf_text_extraction_available():
            logger.warning(
                "PyMuPDF 不可用，PDF 身份校验将被跳过：下载到的文件不会与目标 DOI/标题"
                "比对，误匹配也不会被隔离。请执行 `pip install PyMuPDF` 后重跑。"
            )
        self.client = RobustHTTPClient(
            max_retries=3,
            backoff_factor=1.0,
            timeout=30,
            proxy_list=proxy_list,
        )
        self.cache = PaperCache(cache_db)

    def _candidate_path(self, candidate_dir: str, pmid: str, source_name: str) -> str:
        safe_source = re.sub(r'[\\/:*?"<>|]', "_", source_name)
        return os.path.join(candidate_dir, f"{pmid}_{safe_source}.pdf")

    def _get_strategies(self, paper: dict, candidate_dir: str):
        """
        返回可并行竞速的开放获取下载源。

        本工具只使用合法的开放获取来源：
          PMC, Unpaywall, Europe PMC, Semantic Scholar, CORE, DOI 直连, bioRxiv/medRxiv, OA Button
        """
        doi = paper.get("doi", "")
        pmc_id = paper.get("pmc_id", "")
        title = paper.get("title", "")
        pmid = paper.get("pmid", "unknown")
        c = self.client

        def attempt(name: str, callback):
            candidate_path = self._candidate_path(candidate_dir, pmid, name)
            return name, candidate_path, lambda path=candidate_path: callback(path)

        return [
            attempt("PMC",             lambda path: src.try_pmc(c, pmc_id, path)),
            attempt("Unpaywall",       lambda path: src.try_unpaywall(c, doi, path, self.email)),
            attempt("Europe PMC",      lambda path: src.try_europe_pmc(c, doi, pmc_id, path)),
            attempt("Semantic Scholar", lambda path: src.try_semantic_scholar(c, doi, path)),
            attempt("CORE",            lambda path: src.try_core(c, doi, path)),
            attempt("DOI直连",         lambda path: src.try_doi_redirect(c, doi, path)),
            attempt("bioRxiv_medRxiv", lambda path: src.try_biorxiv_medrxiv(c, doi, title, path)),
            attempt("OA Button",       lambda path: src.try_oa_button(c, doi, path)),
        ]

    def _quarantine_pdf(self, pdf_path: str, reason: str) -> str:
        suspect_dir = os.path.join(os.path.dirname(pdf_path), "suspect")
        os.makedirs(suspect_dir, exist_ok=True)
        stem, ext = os.path.splitext(os.path.basename(pdf_path))
        suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = os.path.join(suspect_dir, f"{stem}.mismatch_{suffix}{ext or '.pdf'}")
        os.replace(pdf_path, target)
        logger.warning("  疑似误匹配 PDF 已隔离: %s (%s)", target, reason)
        return target

    def _existing_pdf_ok(self, paper: dict, pdf_path: str) -> bool:
        if not os.path.exists(pdf_path):
            return False
        if not self.validate_identity:
            return True

        ok, reason = validate_pdf_matches_paper(pdf_path, paper)
        paper["pdf_validation"] = reason
        if ok:
            return True

        logger.warning("  现有 PDF 未通过身份校验: PMID %s — %s", paper.get("pmid", "?"), reason)
        try:
            self._quarantine_pdf(pdf_path, reason)
        except OSError as e:
            logger.error("  隔离疑似误匹配 PDF 失败: %s", e)
        return False

    def _promote_candidate(self, paper: dict, candidate_path: str, save_path: str, source_name: str) -> bool:
        if not os.path.exists(candidate_path):
            logger.debug("  [%s] 声称成功但候选文件不存在", source_name)
            return False

        if self.validate_identity:
            ok, reason = validate_pdf_matches_paper(candidate_path, paper)
            paper["pdf_validation"] = reason
            if not ok:
                logger.warning(
                    "  [%s] PDF 身份校验失败，丢弃候选: PMID %s — %s",
                    source_name, paper.get("pmid", "?"), reason,
                )
                return False

        target_dir = os.path.dirname(save_path)
        if target_dir:
            os.makedirs(target_dir, exist_ok=True)
        try:
            os.replace(candidate_path, save_path)
        except OSError as e:
            logger.error("  [%s] 候选 PDF 晋升失败: %s", source_name, e)
            return False
        logger.debug("  [%s] PDF 身份校验通过: %s", source_name, paper.get("pdf_validation", "skipped"))
        return True

    def download_single(self, paper: dict, save_path: str) -> tuple[bool, str]:
        """
        下载单篇论文。

        策略：所有开放获取源并行竞速，第一个成功且通过身份校验的胜出，
        其余请求取消。全部失败则记为失败（不回退到任何非授权来源）。
        """
        pmid = paper.get("pmid", "")

        # 检查缓存
        cached = self.cache.lookup(pmid)
        if cached:
            cached_path = cached.get("pdf_path", "")
            if self._existing_pdf_ok(paper, cached_path):
                paper["pdf_status"] = f"缓存命中({cached.get('source', '?')})"
                return True, "缓存"
            self.cache.update(pmid, status="pending", pdf_path="")

        candidate_dir = tempfile.mkdtemp(prefix=f"paper_{pmid}_")
        try:
            strategies = self._get_strategies(paper, candidate_dir)

            # OA 源并行竞速。每个源写自己的临时文件，避免互相覆盖。
            accepted_source = ""
            with ThreadPoolExecutor(max_workers=min(4, len(strategies))) as pool:
                future_to_attempt = {
                    pool.submit(self._safe_call, name, fn): (name, candidate_path)
                    for name, candidate_path, fn in strategies
                }

                for future in as_completed(future_to_attempt):
                    name, candidate_path = future_to_attempt[future]
                    success = future.result()
                    if success and self._promote_candidate(paper, candidate_path, save_path, name):
                        accepted_source = name
                        for f in future_to_attempt:
                            f.cancel()
                        break

            if accepted_source:
                self.cache.mark_downloaded(
                    pmid, save_path, accepted_source,
                    file_size=os.path.getsize(save_path) if os.path.exists(save_path) else 0,
                    doi=paper.get("doi", ""),
                    title=paper.get("title", ""),
                )
                return True, accepted_source

            logger.debug("  所有开放获取源均未命中 PMID %s", pmid)
            self.cache.mark_failed(pmid, doi=paper.get("doi", ""), title=paper.get("title", ""))
            return False, ""
        finally:
            shutil.rmtree(candidate_dir, ignore_errors=True)

    def _safe_call(self, source_name: str, fn) -> bool:
        """安全调用下载源，捕获所有异常并记录日志（不再静默吞掉）"""
        try:
            return fn()
        except Exception as e:
            logger.error("  [%s] 未预期的异常: %s: %s", source_name, type(e).__name__, e)
            return False

    def download_batch(
        self,
        papers: list[dict],
        pdf_dir: str = "./pdfs",
    ) -> dict:
        """
        批量并发下载。

        原代码 L900-923 问题：完全串行，逐篇下载。
        改进：使用线程池并发下载多篇论文。

        返回: {"downloaded": n, "failed": n, "cached": n, "total": n}
        """
        os.makedirs(pdf_dir, exist_ok=True)
        results = {"downloaded": 0, "failed": 0, "cached": 0, "total": len(papers)}

        def _process_paper(idx_paper):
            idx, paper = idx_paper
            safe_title = re.sub(r'[\\/:*?"<>|]', '_', paper["title"][:60])
            pdf_name = f"{paper['pmid']}_{safe_title}.pdf"
            pdf_path = os.path.join(pdf_dir, pdf_name)

            # 文件已存在（简单缓存）
            if os.path.exists(pdf_path):
                if self._existing_pdf_ok(paper, pdf_path):
                    paper["pdf_status"] = "已存在"
                    return "cached", paper["pmid"], ""
                logger.warning("  PMID %s 现有 PDF 疑似误匹配，将重新下载", paper["pmid"])

            logger.info(
                "[%d/%d] PMID %s — %s",
                idx, len(papers), paper["pmid"], paper["title"][:50],
            )
            success, source = self.download_single(paper, pdf_path)
            if success:
                paper["pdf_status"] = f"已下载({source})"
                return "downloaded", paper["pmid"], source
            else:
                paper["pdf_status"] = "全部源失败"
                return "failed", paper["pmid"], ""

        # 多篇论文并发（max_workers 控制并发度）
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {
                pool.submit(_process_paper, (idx, p)): p
                for idx, p in enumerate(papers, 1)
            }
            for future in as_completed(futures):
                try:
                    status, pmid, source = future.result()
                    results[status] = results.get(status, 0) + 1
                    if status == "downloaded":
                        logger.info("  ✓ PMID %s via %s", pmid, source)
                    elif status == "failed":
                        logger.warning("  ✗ PMID %s 全部源失败", pmid)
                except Exception as e:
                    logger.error("  批量下载异常: %s", e)
                    results["failed"] += 1

        logger.info(
            "下载完成: %d/%d 成功, %d 缓存命中, %d 失败",
            results["downloaded"], results["total"],
            results["cached"], results["failed"],
        )
        return results

    def close(self):
        self.cache.close()
