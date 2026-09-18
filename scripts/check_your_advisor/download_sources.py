"""
下载源模块
==========
每个下载源封装为独立函数，统一签名，方便并发调度。

核心改进（对比原 L315-767）：
1. 消除所有 `except Exception: pass` — 改为精确捕获 + 日志
2. 每个源函数只负责自身逻辑，不再包含重试（重试由 http_client 层处理）
3. 统一返回值：(success: bool, source_name: str)

两种请求头，按打的是什么分
--------------------------
每个源函数里有两类请求，它们面对的不是同一种对手，所以不共用一套头：

- **查目录的那一跳**（`accept_type="api"`，本文件共 6 处）打的是有文档的
  keyless 公共 JSON API：Unpaywall、Europe PMC REST、Semantic Scholar Graph、
  CORE v3、bioRxiv/medRxiv details、Open Access Button。它们和
  `citations.py` / `openalex.py` / `journal_risk.py` 打的是同一类端点——其中
  `api.semanticscholar.org` 和 `www.ebi.ac.uk` 干脆就是同两台主机——所以走
  `polite_headers()`，自报 `check-your-advisor/1.0`。在此之前同一次运行里同一
  台主机会听到两个身份：`citations.fetch_semantic_scholar` 说自己是
  check-your-advisor，`try_semantic_scholar` 说自己是 Safari 17.4。
- **取正文的那一跳**（`accept_type="pdf"` / `"html"`）打的是上一跳返回的落地
  页与 PDF 二进制，多数落在出版商域名上。那条路照旧走 `http_client` 的 UA 池，
  一个字没改，理由见 `http_client` 模块头。

唯一的例外记在这里：`try_pmc` 里的 `oa.fcgi` 也是 NCBI 有文档的 OA Web
Service，但它以 `accept_type="html"` 发出（返回的是 XML），不在上面那 6 处里，
本轮未改，维持现状。
"""

import logging
import re
from urllib.parse import quote_plus

from .http_client import RobustHTTPClient, polite_headers
from .pdf_utils import extract_pdf_urls_from_html, save_pdf

logger = logging.getLogger("check_your_advisor.sources")


def try_pmc(client: RobustHTTPClient, pmc_id: str, save_path: str) -> bool:
    """
    源1: PubMed Central

    原代码 L315-368 问题：
    - L331 except Exception: pass — 连接失败无日志
    - L340 重新 import re（冗余）
    - L352 tarfile 打开后异常时未关闭（资源泄漏）
    """
    if not pmc_id:
        return False

    urls = [
        f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc_id}/pdf/",
        f"https://europepmc.org/backend/ptpmcrender.fcgi?accid={pmc_id}&blobtype=pdf",
    ]
    for url in urls:
        resp = client.get(url, accept_type="pdf", timeout=60)
        if save_pdf(resp, save_path):
            return True

    # OA API — tar.gz 解包
    oa_url = f"https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id={pmc_id}"
    resp = client.get(oa_url, accept_type="html", timeout=30)
    if resp and resp.status_code == 200 and "error" not in resp.text.lower().split("href")[0]:
        links = re.findall(r'href="([^"]+)"', resp.text)
        for link in links:
            if link.startswith("ftp://ftp.ncbi.nlm.nih.gov/"):
                link = link.replace("ftp://ftp.ncbi.nlm.nih.gov/",
                                    "https://ftp.ncbi.nlm.nih.gov/")
            if ".tar.gz" in link:
                resp2 = client.get(link, accept_type="pdf", timeout=120)
                if resp2 and resp2.status_code == 200 and len(resp2.content) > 1000:
                    try:
                        import io
                        import tarfile
                        with tarfile.open(fileobj=io.BytesIO(resp2.content), mode="r:gz") as tar:
                            for member in tar.getmembers():
                                if member.name.lower().endswith(".pdf"):
                                    pdf_data = tar.extractfile(member).read()
                                    if pdf_data[:5] == b"%PDF-":
                                        from pathlib import Path
                                        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
                                        with open(save_path, "wb") as f:
                                            f.write(pdf_data)
                                        logger.info("  ✓ PMC tar.gz 解包成功")
                                        return True
                    except (OSError, tarfile.TarError) as e:
                        logger.warning("  PMC tar.gz 解包失败: %s", e)
    return False


def try_unpaywall(client: RobustHTTPClient, doi: str, save_path: str, email: str) -> bool:
    """
    源2: Unpaywall API

    原代码 L371-436 问题：
    - L431 except Exception: continue — HTML→PDF 提取失败无日志
    - L403 硬跳过所有 PMC 链接（可能误杀有效链接）
    """
    if not doi or not email or "example.com" in email:
        return False

    # `email` goes into the User-Agent as well as the query string, and only
    # here. Unpaywall's own documentation makes the address the required way to
    # identify a caller on this endpoint, and it is already in the URL for that
    # reason — so naming the same address in the UA discloses nothing new to
    # anyone. The other five API sources get the bare project name: none of them
    # documents an address-keyed polite pool, and handing a user's address to a
    # third party that never asked for it is the failure `citations.py` guards
    # against in its own fallback chain.
    api_url = f"https://api.unpaywall.org/v2/{doi}?email={email}"
    resp = client.get(api_url, accept_type="api", timeout=30,
                      extra_headers=polite_headers(email))
    if not resp or resp.status_code != 200:
        return False

    try:
        data = resp.json()
    except ValueError:
        logger.warning("  Unpaywall 返回非 JSON 响应")
        return False

    if not data.get("is_oa"):
        logger.debug("  Unpaywall: 非 OA 论文")
        return False

    # 收集候选 URL
    candidate_urls = []
    locations = []
    best = data.get("best_oa_location")
    if best:
        locations.append(best)
    for loc in data.get("oa_locations", []):
        if loc not in locations:
            locations.append(loc)

    for loc in locations:
        for key in ["url_for_pdf", "url"]:
            u = loc.get(key)
            if u and u not in candidate_urls:
                candidate_urls.append(u)

    for pdf_url in candidate_urls:
        resp2 = client.get(pdf_url, accept_type="pdf", timeout=60)
        if save_pdf(resp2, save_path):
            return True
        # HTML 页面→提取内嵌 PDF 链接
        if resp2 and resp2.status_code == 200 and "html" in resp2.headers.get("content-type", ""):
            for inner_url in extract_pdf_urls_from_html(resp2.text)[:3]:
                resp3 = client.get(inner_url, accept_type="pdf", timeout=60)
                if save_pdf(resp3, save_path):
                    return True

    return False


def try_europe_pmc(client: RobustHTTPClient, doi: str, pmc_id: str, save_path: str) -> bool:
    """源3: Europe PMC REST API（原 L439-464）"""
    if not doi and not pmc_id:
        return False
    query = pmc_id if pmc_id else doi
    api_url = (f"https://www.ebi.ac.uk/europepmc/webservices/rest/search"
               f"?query={quote_plus(query)}&format=json&resultType=core")
    resp = client.get(api_url, accept_type="api", timeout=30,
                      extra_headers=polite_headers())
    if not resp or resp.status_code != 200:
        return False
    try:
        data = resp.json()
    except ValueError:
        return False

    for r in data.get("resultList", {}).get("result", []):
        for link in r.get("fullTextUrlList", {}).get("fullTextUrl", []):
            if link.get("documentStyle") == "pdf" and link.get("availability") == "Open access":
                pdf_url = link.get("url")
                if pdf_url:
                    resp2 = client.get(pdf_url, accept_type="pdf", timeout=60)
                    if save_pdf(resp2, save_path):
                        return True
    return False


def try_semantic_scholar(client: RobustHTTPClient, doi: str, save_path: str) -> bool:
    """源4: Semantic Scholar API（原 L467-486）"""
    if not doi:
        return False
    api_url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}?fields=isOpenAccess,openAccessPdf"
    resp = client.get(api_url, accept_type="api", timeout=30,
                      extra_headers=polite_headers())
    if not resp or resp.status_code != 200:
        return False
    try:
        data = resp.json()
    except ValueError:
        return False
    oa_pdf = data.get("openAccessPdf")
    if oa_pdf and oa_pdf.get("url"):
        resp2 = client.get(oa_pdf["url"], accept_type="pdf", timeout=60)
        if save_pdf(resp2, save_path):
            return True
    return False


def try_doi_redirect(client: RobustHTTPClient, doi: str, save_path: str) -> bool:
    """
    源5: DOI 跳转→出版商页面→提取 PDF

    原代码 L489-518 问题：
    - L506 不检查 pdf_url 是否合法就直接请求
    """
    if not doi:
        return False
    doi_url = f"https://doi.org/{doi}"
    resp = client.get(doi_url, accept_type="html", timeout=30)
    if not resp or resp.status_code != 200:
        return False

    for pdf_url in extract_pdf_urls_from_html(resp.text)[:3]:
        if not pdf_url.startswith("http"):
            continue
        resp2 = client.get(pdf_url, accept_type="pdf", timeout=60)
        if save_pdf(resp2, save_path):
            return True
    return False


def try_core(client: RobustHTTPClient, doi: str, save_path: str) -> bool:
    """源6: CORE API（原 L557-577）"""
    if not doi:
        return False
    api_url = f"https://api.core.ac.uk/v3/search/works/?q=doi%3A%22{quote_plus(doi)}%22&limit=1"
    resp = client.get(api_url, accept_type="api", timeout=30,
                      extra_headers=polite_headers())
    if not resp or resp.status_code != 200:
        return False
    try:
        data = resp.json()
    except ValueError:
        return False
    for r in data.get("results", []):
        pdf_url = r.get("downloadUrl")
        if not pdf_url:
            source_urls = r.get("sourceFulltextUrls", [])
            pdf_url = source_urls[0] if source_urls else None
        if pdf_url:
            resp2 = client.get(pdf_url, accept_type="pdf", timeout=60)
            if save_pdf(resp2, save_path):
                return True
    return False


def try_biorxiv_medrxiv(client: RobustHTTPClient, doi: str, title: str, save_path: str) -> bool:
    """源7: bioRxiv/medRxiv 预印本（原 L580-616）"""
    if doi:
        for server in ["biorxiv", "medrxiv"]:
            api_url = f"https://api.biorxiv.org/details/{server}/10.1101/{doi.split('/')[-1]}/na/na/json"
            resp = client.get(api_url, accept_type="api", timeout=20,
                              extra_headers=polite_headers())
            if resp and resp.status_code == 200:
                try:
                    data = resp.json()
                except ValueError:
                    continue
                for item in data.get("collection", []):
                    item_doi = item.get("doi", "")
                    version = item.get("version", "1")
                    pdf_url = f"https://www.{server}.org/content/{item_doi}v{version}.full.pdf"
                    resp2 = client.get(pdf_url, accept_type="pdf", timeout=60)
                    if save_pdf(resp2, save_path):
                        return True
    return False


def try_oa_button(client: RobustHTTPClient, doi: str, save_path: str) -> bool:
    """源9: Open Access Button（原 L670-698）"""
    if not doi:
        return False
    api_url = f"https://api.openaccessbutton.org/find?id={quote_plus(doi)}"
    resp = client.get(api_url, accept_type="api", timeout=30,
                      extra_headers=polite_headers())
    if not resp or resp.status_code != 200:
        return False
    try:
        data = resp.json()
    except ValueError:
        return False
    pdf_url = data.get("url")
    if pdf_url:
        resp2 = client.get(pdf_url, accept_type="pdf", timeout=60)
        if save_pdf(resp2, save_path):
            return True
    for a in data.get("availability", []):
        u = a.get("url")
        if u:
            resp2 = client.get(u, accept_type="pdf", timeout=60)
            if save_pdf(resp2, save_path):
                return True
    return False
