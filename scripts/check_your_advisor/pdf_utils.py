"""
PDF 工具模块
============
PDF 验证、保存、从 HTML 页面提取 PDF 链接。

修复原代码问题：
- L306-312: _save_pdf 只检查 content-type 和魔数，不够健壮
- L416-430: HTML→PDF 链接提取逻辑重复出现在多个函数中
"""

import html
import logging
import re
from pathlib import Path
from typing import Mapping, Protocol

logger = logging.getLogger("check_your_advisor.pdf")


class HttpResponse(Protocol):
    """Everything `save_pdf` needs from an HTTP response — and nothing else.

    `requests` used to be imported into this module for one type annotation,
    which put a third-party package in the import path of every consumer for
    no runtime benefit at all. Stating the contract structurally instead means
    the transport can be replaced without this module knowing, and it says
    exactly what a replacement has to provide: three members, no methods.
    """

    status_code: int
    headers: Mapping[str, str]
    content: bytes


STOP_WORDS = {
    "with", "from", "that", "this", "into", "using", "based", "study", "analysis",
    "effect", "effects", "role", "roles", "and", "the", "for", "its", "via",
    "are", "was", "were", "between", "relationship",
}


def is_valid_pdf(content: bytes, min_size: int = 1000) -> bool:
    """检查二进制内容是否为有效 PDF"""
    if len(content) < min_size:
        return False
    # PDF 魔数检查
    if not content[:5] == b"%PDF-":
        return False
    # 额外检查：PDF 应该包含 %%EOF 标记（部分截断文件不包含）
    # 只检查最后 1KB，避免大文件全量扫描
    tail = content[-1024:]
    if b"%%EOF" not in tail and b"%%eof" not in tail:
        logger.debug("  PDF 缺少 %%EOF 标记，文件可能不完整")
        # 不强制拒绝，因为某些合法 PDF 可能尾部有多余字节
    return True


def save_pdf(resp: HttpResponse | None, save_path: str) -> bool:
    """
    验证响应并保存为 PDF。

    改进点（对比原 L303-312 _save_pdf）：
    - 增加 %%EOF 完整性提示
    - 明确日志记录保存成功/失败原因
    """
    if resp is None or resp.status_code != 200:
        return False

    content_type = resp.headers.get("content-type", "").lower()

    # 优先检查 content-type
    if "pdf" in content_type or is_valid_pdf(resp.content):
        if is_valid_pdf(resp.content):
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            with open(save_path, "wb") as f:
                f.write(resp.content)
            size_mb = len(resp.content) / (1024 * 1024)
            logger.info("  ✓ PDF 已保存 (%.1f MB) → %s", size_mb, Path(save_path).name)
            return True
        else:
            logger.debug("  content-type 声称是 PDF 但内容校验失败 (%d bytes)", len(resp.content))

    return False


def _normalize_text(text: str) -> str:
    text = html.unescape(text or "").lower()
    return re.sub(r"[^a-z0-9]+", " ", text)


def _doi_key(doi: str) -> str:
    doi = (doi or "").strip().lower()
    doi = doi.removeprefix("https://doi.org/")
    doi = doi.removeprefix("http://doi.org/")
    doi = doi.removeprefix("doi:")
    return doi.strip()


def _title_tokens(title: str) -> set[str]:
    words = _normalize_text(title).split()
    return {w for w in words if len(w) >= 4 and w not in STOP_WORDS}


def pdf_text_extraction_available() -> bool:
    """
    Whether PyMuPDF can be imported, i.e. whether identity validation can run.

    Callers should check this once at startup and warn, rather than discovering
    per-download that validation is silently degrading to a no-op. PyMuPDF is a
    declared dependency, so this returning False means a broken install.
    """
    try:
        import fitz  # noqa: F401
    except ImportError:
        return False
    return True


def extract_pdf_text(pdf_path: str, max_pages: int = 3, max_chars: int = 12000) -> tuple[str, str]:
    """
    Extract first-page text for identity validation.

    PyMuPDF is optional at runtime; if it is missing, callers should treat
    validation as unavailable rather than as a hard failure.
    """
    try:
        import fitz  # type: ignore
    except ImportError:
        return "", "PyMuPDF unavailable"

    try:
        doc = fitz.open(pdf_path)
        try:
            chunks = []
            for page_no in range(min(max_pages, doc.page_count)):
                page = doc.load_page(page_no)
                chunks.append(page.get_text("text"))
                if sum(len(c) for c in chunks) >= max_chars:
                    break
            text = "\n".join(chunks)[:max_chars]
        finally:
            doc.close()
    except Exception as e:
        return "", f"text extraction failed: {type(e).__name__}: {e}"

    if len(text.strip()) < 200:
        return text, "insufficient extracted text"
    return text, "text extracted"


def validate_pdf_matches_paper(pdf_path: str, paper: dict, min_title_overlap: float = 0.45) -> tuple[bool, str]:
    """
    Best-effort guard against wrong-paper downloads.

    Accepts when DOI matches, or when enough significant title tokens appear in
    the first pages. If text extraction is unavailable, the caller can still keep
    the PDF because the basic PDF binary validation has already passed.
    """
    if not Path(pdf_path).exists():
        return False, "candidate file missing"

    text, note = extract_pdf_text(pdf_path)
    if not text.strip():
        return True, f"identity check skipped ({note})"
    if len(text.strip()) < 200:
        return True, f"identity check skipped ({note})"

    raw_text_lower = html.unescape(text).lower()
    normalized_text = _normalize_text(text)
    normalized_words = set(normalized_text.split())

    doi = _doi_key(paper.get("doi", ""))
    if doi:
        compact_doi = re.sub(r"[^a-z0-9]+", "", doi)
        compact_text = re.sub(r"[^a-z0-9]+", "", raw_text_lower)
        if doi in raw_text_lower or compact_doi in compact_text:
            return True, "DOI matched"

    title = paper.get("title", "")
    tokens = _title_tokens(title)
    if tokens:
        hits = {token for token in tokens if token in normalized_words}
        overlap = len(hits) / len(tokens)
        normalized_title = _normalize_text(title).strip()
        if normalized_title and normalized_title in normalized_text:
            return True, "title matched"
        if overlap >= min_title_overlap:
            return True, f"title token overlap {len(hits)}/{len(tokens)}"
        return False, f"title mismatch: token overlap {len(hits)}/{len(tokens)}"

    return True, "no title/DOI available for identity check"


def extract_pdf_urls_from_html(html: str) -> list[str]:
    """
    从 HTML 页面中提取可能的 PDF 链接。

    整合原代码中重复的提取逻辑（L416-422, L498-503, L717-720）。
    按优先级排序：meta 标签 > 明确的 PDF 链接 > 模糊匹配。
    """
    urls = []

    # 1. <meta name="citation_pdf_url"> — 最可靠
    meta_matches = re.findall(
        r'citation_pdf_url["\s]+content="([^"]+)"', html
    )
    urls.extend(meta_matches)

    # 2. <a href="...pdf"> 或 <link type="application/pdf">
    typed_matches = re.findall(
        r'application/pdf["\s]+href="([^"]+)"', html
    )
    urls.extend(typed_matches)

    # 3. 通用 .pdf URL 模式
    generic_matches = re.findall(
        r'(https?://[^\s"\'<>]+\.pdf(?:\?[^\s"\'<>]*)?)', html
    )
    urls.extend(generic_matches)

    # 去重并保持顺序
    seen = set()
    unique = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)

    return unique
