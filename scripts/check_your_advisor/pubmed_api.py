"""
PubMed API 模块
===============
从原 pubmed_fetcher.py 中提取的 PubMed 搜索与解析逻辑。
解析层使用 XML parser，避免正则在嵌套标签、多段摘要、多机构字段上漏数据。
"""

import html
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger("check_your_advisor.pubmed")

BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


USER_AGENT = "check-your-advisor/1.0"

# NCBI 的公开限速：无 api_key 每秒 3 次，有 key 每秒 10 次
# (E-utilities 文档 https://www.ncbi.nlm.nih.gov/books/NBK25499/，2026-08-22 查)。
# 按「最小请求间隔」实现并留一点余量：3/s → 0.34s，10/s → 0.11s。
_MIN_INTERVAL_NO_KEY = 0.34
_MIN_INTERVAL_WITH_KEY = 0.11

# 同一份文档：「For PubMed and PMC, ESearch can only retrieve the first 10,000
# records matching the query」(2026-08-22 查)。翻页突破不了这条线，所以取数
# 上限的默认值就设在这里 —— 再往上调只会翻出空页。
#
# 两个名字指同一个 10000，但说的是两件事，报告要靠这个区分才能给出对的建议：
# ESEARCH_MAX_RETRIEVABLE 是 NCBI 接口的硬顶，本工具改不动；MAX_RECORDS 是本
# 工具的预算默认值，用户可以调。缺口出现在硬顶上时「调高 max_records」是句废
# 话 —— 见下面的 coverage_remedy。
ESEARCH_MAX_RETRIEVABLE = 10000
MAX_RECORDS = ESEARCH_MAX_RETRIEVABLE


# ============================================================
# 取不全的成因与补救文案 —— 采集日志和报告共用这一处
# ============================================================
# 两个界面在两个时刻对同一件事说话：本模块的日志当场就打，profile/report.py 的
# `_coverage_lines` 要等这一轮跑完才写出来，用户先看到日志、二十分钟后才看到
# 报告。判定和建议因此必须来自同一个地方；上一版是报告改对了、日志没跟上，于是
# 撞硬顶的那次采集，日志给的第一个动作恰好是唯一无效的那个。
#
# 两种成因，两套建议：
# - ceiling：命中数已经越过 NCBI 的硬顶，而本工具的预算不是更紧的那一个。
#   调高 max_records 一条也多取不到，翻到第 10001 条只会拿到空页。
# - budget：卡住取数的是本工具的 max_records，调高它确实有用。
#
# 语言分两种是因为两个界面本来就用两种语言（日志中文、报告英文），不是两份文案：
# 成因判定只有 `shortfall_is_ceiling_bound` 这一个实现，四条句子并排放在这里，
# 改其中一条时另外三条就在眼前。
_REMEDY_CEILING_ZH = (
    f"调高 max_records 一条也补不回来：NCBI 的 E-utilities 对任何 PubMed esearch 最多只交出"
    f"前 {ESEARCH_MAX_RETRIEVABLE:,} 条，这次已经顶在那条线上了。这是 NCBI 的接口上限，"
    f"不是本工具的配置。把检索式收窄到命中数低于 {ESEARCH_MAX_RETRIEVABLE:,} —— 缩小 "
    f"years_back、补 affiliation_keywords 或 orcid —— 或者按年份分段重跑再合并。"
)
_REMEDY_BUDGET_ZH = (
    f"调高 max_records（最高到 NCBI 的 {ESEARCH_MAX_RETRIEVABLE:,} 条 esearch 硬顶）、"
    f"缩小 years_back，或补 affiliation_keywords/orcid 后重跑。"
)
_REMEDY_CEILING_EN = (
    f"Raising max_records will not recover any of them: NCBI's E-utilities returns at "
    f"most the first {ESEARCH_MAX_RETRIEVABLE:,} records for any PubMed esearch, and "
    f"this run was already at that interface limit. It is NCBI's ceiling, not a "
    f"setting in this tool. Narrow the search until it matches fewer than "
    f"{ESEARCH_MAX_RETRIEVABLE:,} — cut years_back, add affiliation_keywords, or "
    f"supply an ORCID — or re-harvest one year at a time and merge the results."
)
_REMEDY_BUDGET_EN = (
    f"Raise max_records (up to NCBI's {ESEARCH_MAX_RETRIEVABLE:,}-record esearch "
    f"ceiling), cut years_back, or add affiliation_keywords, then re-harvest."
)

_COVERAGE_REMEDY = {
    ("ceiling", "zh"): _REMEDY_CEILING_ZH,
    ("ceiling", "en"): _REMEDY_CEILING_EN,
    ("budget", "zh"): _REMEDY_BUDGET_ZH,
    ("budget", "en"): _REMEDY_BUDGET_EN,
}


def shortfall_is_ceiling_bound(matched: object, budget: object) -> bool:
    """取不全，是撞了 NCBI 的硬顶，还是撞了本工具的预算？

    撞硬顶的条件有两条，缺一不可：命中数确实越过了 esearch 交得出的上限，
    **并且**本工具的预算不是更紧的那一个。命中 25000 条而 max_records=500 时
    调高预算能多取 9500 条，那仍然是预算受限。

    `budget` 没有记录（旧语料的 provenance 里可能缺这个键）时按硬顶算，因为
    默认值就钉在硬顶上。两个参数都收 object：报告那边的 provenance 是从 JSON
    读出来的，缺字段时是 "?" 而不是 int。
    """
    if not isinstance(matched, int) or matched <= ESEARCH_MAX_RETRIEVABLE:
        return False
    return not isinstance(budget, int) or budget >= ESEARCH_MAX_RETRIEVABLE


def coverage_remedy(matched: object, budget: object, lang: str) -> str:
    """按成因给出补救建议，`lang` 只决定用哪种语言说，不决定说什么。"""
    cause = "ceiling" if shortfall_is_ceiling_bound(matched, budget) else "budget"
    return _COVERAGE_REMEDY[(cause, lang)]


# 模块级，因为限速是按 IP 算的：esearch 的翻页和 efetch 的批次共用同一份配额，
# 两个端点各记各的账等于把速率翻倍。
_last_request_at = 0.0


def _throttle(has_api_key: bool, floor: float = 0.0) -> None:
    """两个 E-utilities 端点共用的最小请求间隔。

    `floor` 是调用方自己要求的更慢节奏(fetch_details 的 delay)，取两者的大者：
    调用方可以比 NCBI 的限速更客气，但不能更急。
    """
    global _last_request_at
    gap = max(_MIN_INTERVAL_WITH_KEY if has_api_key else _MIN_INTERVAL_NO_KEY, floor)
    wait = _last_request_at + gap - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def _retry_after_seconds(err: HTTPError, attempt: int) -> float:
    """NCBI's own Retry-After when it sends one, otherwise a linear backoff."""
    raw = err.headers.get("Retry-After") if err.headers else None
    try:
        return min(float(raw), 30.0) if raw else 2.0 * (attempt + 1)
    except (TypeError, ValueError):
        return 2.0 * (attempt + 1)


def _eutils_get(endpoint: str, params: dict, timeout: float, retries: int = 2,
                min_interval: float = 0.0) -> bytes:
    """GET one E-utilities endpoint and return the raw body.

    Standard library only. `requests` was this module's single third-party
    import, and because `profile/roles.py` imports the name-matching helpers
    below, it made the entire `profile` package — which never touches the
    network — depend on it. Two call sites were the whole cost.

    Failures raise, exactly as `resp.raise_for_status()` did, so callers that
    never handled an error still don't have to. What is new is the retry: NCBI
    returns 429 precisely when a query is large, which is when losing the
    response costs the most, and the bare `requests.get` this replaced treated
    that as a hard failure.

    此处是 esearch 与 efetch 唯一的出口，所以限速也只在这里做一次：一次万条
    作者的检索是 20 次 esearch 加 200 次 efetch，各自为政必然连撞 429。
    有没有 api_key 直接从 params 里读，调用方不用再传一遍。
    """
    url = f"{BASE_URL}/{endpoint}?{urlencode(params)}"
    attempt = 0
    while True:
        _throttle(bool(params.get("api_key")), min_interval)
        req = Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except HTTPError as e:
            if e.code in (429, 503) and attempt < retries:
                wait = _retry_after_seconds(e, attempt)
                logger.warning("PubMed 返回 %d，%.1f 秒后重试（第 %d 次）", e.code, wait, attempt + 1)
                time.sleep(wait)
                attempt += 1
                continue
            raise
        except (TimeoutError, URLError) as e:
            # TimeoutError must be caught alongside URLError but is not a
            # subclass of it: socket timeouts arrive as the former.
            if attempt < retries:
                wait = 1.0 * (attempt + 1)
                logger.warning("PubMed 连接失败(%s)，%.1f 秒后重试（第 %d 次）",
                               getattr(e, "reason", e), wait, attempt + 1)
                time.sleep(wait)
                attempt += 1
                continue
            raise


def _with_api_key(params: dict, api_key: str) -> dict:
    if api_key:
        params["api_key"] = api_key
    return params


def author_query_variants(author: str) -> list[str]:
    """
    把作者名展开为 PubMed [Author] 检索式的多种写法。

    PubMed 对每位作者**总是**建立 "Surname Initials"(如 `Stockwell BR`)索引，
    而 "Surname Forename"(如 `Stockwell Brent`)索引只在投稿时提供了完整名字
    时才有。因此只用全名检索会严重丢召回：

        "Stockwell Brent"[Author]  ->    6 篇
        "Stockwell BR"[Author]     ->  232 篇   (同一个人)

    这里同时给出全名式与「姓 + 首字母」式并做 OR。首字母式会带进同名的其他人，
    但这正是本工具下游身份验证(ORCID / 机构 / 邮箱)要解决的问题 —— 检索层负责
    召回，过滤层负责精确。检索层过窄会让身份验证无从发挥。

    输入约定为「姓 名」顺序(与 PubMed 展示顺序一致，见 config.example.json)。
    客户端的 `_name_matches` 本身容忍正反序，因此顺序写反只影响召回、不产生错配。
    """
    author = (author or "").strip()
    if not author:
        return []

    parts = author.split()
    if len(parts) < 2:
        # 只给了姓：不加引号，让 PubMed 自行展开(加引号会精确匹配到 0 条)
        return [f"{author}[Author]"]

    surname, given = parts[0], parts[1:]
    initial = given[0][0].upper() if given[0] else ""

    variants = [f'"{author}"[Author]']
    if initial:
        # 通配式覆盖所有中间名首字母组合(B* 同时匹配 B / BR / BJ)。
        # 从「Stockwell Brent」推不出中间名首字母 R，所以必须用通配。
        variants.append(f'"{surname} {initial}*"[Author]')
    return variants


def build_search_query(
    author: str,
    orcid: str = "",
    affiliation_keywords: list[str] | None = None,
) -> str:
    """
    组装 esearch 检索式。

    ORCID 项独立 OR 在最外层：它已是精确标识符，不应被机构条件收窄，
    否则机构字段未被索引的论文会丢失。
    """
    terms = []
    if orcid:
        terms.append(f"{orcid}[auid]")

    name_part = " OR ".join(author_query_variants(author))
    if name_part:
        if affiliation_keywords:
            affil = " OR ".join(f'"{kw}"[Affiliation]' for kw in affiliation_keywords)
            terms.append(f"(({name_part}) AND ({affil}))")
        else:
            terms.append(f"({name_part})")

    return " OR ".join(terms)


def search_pubmed(
    author: str,
    years_back: int,
    api_key: str,
    retmax: int = 500,
    identity: dict | None = None,
    provenance: dict | None = None,
    max_records: int = MAX_RECORDS,
) -> list[str]:
    """
    搜索 PubMed，用 retstart 翻页取完全部命中，返回 PMID 列表。

    检索层的目标是**高召回**，精确性由下游身份验证负责。`retmax` 在这里是
    **每页页大小**，不是总预算：命中 900 条、retmax=500 就发两次 esearch，
    第二次带 retstart=500。总预算是 `max_records`。

      1. 先用最宽的检索式探测命中数(retmax=0，不取数据)
      2. 命中数在 max_records 以内 → 翻页取完，一条不少
      3. 超出且配置了机构关键词 → 加机构条件收窄，并明确告知用户
      4. 仍然超出 → 取到 max_records 为止，把「取回 N / 共 M」写进 provenance

    第 4 条不是拒绝。上限存在只是为了不让一个超常见姓名把进程挂死，语料取不
    全是报告里如实印出的一行分子分母，不是门禁 —— 旧的 G1 门禁把这件事变成了
    「整份报告作废」，而翻页之后 N < M 的常见成因已经是去重和 PubMed 自己的
    Count 漂移，用它拒绝一份取全了的语料是误伤。

    `provenance` 是可选的输出参数：传一个 dict 进来，检索的实际参数、命中数、
    翻了几页、丢弃了几个重复 PMID 都会写进去。这些量本来只存在于本函数内部，
    随日志一起消失，于是下游的画像报告无从知道语料覆盖到什么程度。用可选参数
    而不是改返回值，是为了不动现有调用方。
    """
    today = datetime.now()
    start_date = today - timedelta(days=years_back * 365)
    mindate = start_date.strftime("%Y/%m/%d")
    maxdate = today.strftime("%Y/%m/%d")

    identity = identity or {}
    orcid = (identity.get("orcid") or "").strip()
    affil_keywords = identity.get("affiliation_keywords") or []

    def _request(term: str, want: int, start: int = 0) -> tuple[list[str], int]:
        params = {
            "db": "pubmed", "term": term, "retmax": want, "retstart": start,
            "datetype": "pdat", "mindate": mindate, "maxdate": maxdate,
            "retmode": "json",
        }
        _with_api_key(params, api_key)
        raw = _eutils_get("esearch.fcgi", params, timeout=30)
        result = json.loads(raw.decode("utf-8", errors="replace")).get("esearchresult", {})
        ids = result.get("idlist", []) or []
        try:
            total = int(result.get("count", "0"))
        except (TypeError, ValueError):
            total = len(ids)
        return ids, total

    record = provenance if provenance is not None else {}
    record.update({
        "mindate": mindate, "maxdate": maxdate, "years_back": years_back,
        "retmax": retmax, "max_records": max_records,
        "narrowed_by_affiliation": False,
    })

    broad = build_search_query(author, orcid=orcid)
    if not broad:
        logger.error("作者名与 ORCID 均为空，无法检索。")
        record.update({"esearch_term": "", "esearch_matched": 0,
                       "pmids_returned": 0, "truncated": False,
                       "pages_fetched": 0, "duplicates_dropped": 0})
        return []

    _, broad_total = _request(broad, 0)
    query, total = broad, broad_total

    # 收窄的触发点是总预算而不是页大小：翻页之后 retmax 只决定发几次请求，
    # 命中 900 条、retmax=500 已经能一条不少地取回，没有理由为此改检索式。
    if broad_total > max_records and affil_keywords:
        narrowed = build_search_query(author, orcid=orcid, affiliation_keywords=affil_keywords)
        _, narrowed_total = _request(narrowed, 0)
        logger.warning(
            "宽检索命中 %d 条，超过 max_records=%d；已自动加入机构条件收窄至 %d 条。"
            "机构字段未被 PubMed 索引的论文可能因此漏掉——如需全量请调高 max_records。",
            broad_total, max_records, narrowed_total,
        )
        query, total = narrowed, narrowed_total
        record.update({"narrowed_by_affiliation": True,
                       "broad_term": broad, "broad_matched": broad_total})

    logger.info("搜索 PubMed: %s (%s ~ %s)，命中 %d 条", query, mindate, maxdate, total)

    # 翻页。retstart 按「这一页实际返回了几条」推进，不按去重后的条数——
    # 每一页都是重跑一次检索式，页与页之间新入库的记录会让结果集平移，
    # 用去重后的计数当偏移量会漏取。重复的 PMID 在这里就丢掉，否则同一篇
    # 会被 efetch 两次、解析成两条、把 fetched/verified 撑大，一直要到
    # profile 阶段的 roles.py 才被吞掉，看起来像是 PubMed 的锅。
    budget = min(total, max_records)
    collected: list[str] = []
    seen: set[str] = set()
    pages = 0
    duplicates = 0
    start = 0
    while len(collected) < budget and start < total:
        page, _ = _request(query, min(retmax, budget - len(collected)), start)
        pages += 1
        if not page:
            logger.warning(
                "第 %d 页(retstart=%d)没有返回任何 PMID，翻页提前结束。esearch 对 PubMed "
                "只能取到前 10000 条，要越过这条线得换更窄的检索式或按年份分段跑。",
                pages, start,
            )
            break
        start += len(page)
        for pmid in page:
            if pmid in seen:
                duplicates += 1
                continue
            seen.add(pmid)
            collected.append(pmid)

    logger.info("翻了 %d 页，取回 %d 个 PMID（丢弃 %d 个重复）", pages, len(collected), duplicates)
    record.update({
        "esearch_term": query,
        "esearch_matched": total,
        "pmids_returned": len(collected),
        "truncated": len(collected) < total,
        "pages_fetched": pages,
        "duplicates_dropped": duplicates,
    })

    # 取不全不再是拒绝，但也绝不静默：报告要印「取回 N / 共 M」，日志先说一遍。
    # 建议走 coverage_remedy，与报告同一处成因判定、同一套说法——用户先看到这一行，
    # 二十分钟后才看到报告，两边给的第一个动作不该是相反的。
    if len(collected) < total:
        logger.warning(
            "只取回 %d / 共 %d 条(上限 max_records=%d，翻页中丢弃重复 PMID %d 个)。"
            "缺的 %d 条没有被检查，报告里的每个计数都是下界——%s",
            len(collected), total, max_records, duplicates, total - len(collected),
            coverage_remedy(total, max_records, "zh"),
        )
    return collected


def fetch_details(pmids: list[str], api_key: str, delay: float = 0.15) -> list[dict]:
    """批量获取论文详情。

    `delay` 现在是批次间隔的**下限**，真正的节奏由 _eutils_get 里那一个节流器
    决定。原先这里自己 time.sleep(delay)，与节流器两处各睡各的，实际速率就算
    不出来了；而 delay 的默认值 0.15s 折合 6.7 req/s，本身就已经超出无 key 时
    NCBI 允许的 3/s。
    """
    if not pmids:
        return []

    papers = []
    batch_size = 50

    for i in range(0, len(pmids), batch_size):
        batch = pmids[i:i + batch_size]
        ids_str = ",".join(batch)

        params = {
            "db": "pubmed",
            "id": ids_str,
            "retmode": "xml",
        }
        _with_api_key(params, api_key)

        xml_text = _eutils_get(
            "efetch.fcgi", params, timeout=60, min_interval=delay
        ).decode("utf-8", errors="replace")

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.error("PubMed XML 解析失败: %s", e)
            continue

        for article in root.findall(".//PubmedArticle"):
            paper = parse_article(article)
            if paper:
                papers.append(paper)

    return papers


def _text(elem: ET.Element | None) -> str:
    if elem is None:
        return ""
    return html.unescape("".join(elem.itertext()).strip())


def _find_text(elem: ET.Element | None, path: str) -> str:
    return _text(elem.find(path) if elem is not None else None)


def _find_first_text(elem: ET.Element, paths: list[str]) -> str:
    for path in paths:
        value = _find_text(elem, path)
        if value:
            return value
    return ""


def _journal_issn(article_node: ET.Element | None) -> tuple[str, str]:
    """The article's own ISSN and which kind it is.

    PubMed carries a single `<ISSN IssnType="Print|Electronic">` on the journal,
    not both, so the type has to travel with the number: an electronic ISSN
    compared against a table's print column is a miss that looks like an absence.
    Returns two empty strings when the record has no ISSN at all, which older
    records and some non-indexed journals genuinely do not.
    """
    if article_node is None:
        return "", ""
    node = article_node.find("Journal/ISSN")
    if node is None:
        return "", ""
    return _text(node), (node.attrib.get("IssnType") or "").strip()


def _article_id(article: ET.Element, id_type: str) -> str:
    id_type_lower = id_type.lower()
    for node in article.findall(".//PubmedData/ArticleIdList/ArticleId"):
        if (node.attrib.get("IdType") or "").lower() == id_type_lower:
            return _text(node)
    return ""


def _article_doi(article: ET.Element) -> str:
    doi = _article_id(article, "doi")
    if doi:
        return doi
    for node in article.findall(".//Article/ELocationID"):
        if (node.attrib.get("EIdType") or "").lower() == "doi":
            return _text(node)
    return ""


def _pub_date(article: ET.Element) -> tuple[str, str]:
    date_node = article.find(".//Article/Journal/JournalIssue/PubDate")
    if date_node is None:
        date_node = article.find(".//PubMedPubDate")
    year = _find_text(date_node, "Year")
    if not year:
        medline_date = _find_text(date_node, "MedlineDate")
        match = re.search(r"\d{4}", medline_date)
        year = match.group(0) if match else ""
    month = _find_text(date_node, "Month")
    day = _find_text(date_node, "Day")
    return year, " ".join(part for part in [year, month, day] if part)


def _abstract(article: ET.Element) -> str:
    parts = []
    for node in article.findall(".//Article/Abstract/AbstractText"):
        text = _text(node)
        if not text:
            continue
        label = node.attrib.get("Label") or node.attrib.get("NlmCategory")
        parts.append(f"{label}: {text}" if label else text)
    return " ".join(parts)


def _author_record(author: ET.Element) -> dict:
    last = _find_text(author, "LastName")
    fore = _find_text(author, "ForeName")
    initials = _find_text(author, "Initials")
    collective = _find_text(author, "CollectiveName")
    affils = [_text(node) for node in author.findall(".//AffiliationInfo/Affiliation")]
    affil_text = " ".join(part for part in affils if part)

    email = ""
    email_match = re.search(r"[\w.+-]+@[\w.-]+\.\w+", affil_text)
    if email_match:
        email = email_match.group(0)
    is_corresponding = bool(email) or "corresponding" in affil_text.lower()

    orcid = ""
    for node in author.findall("Identifier"):
        if (node.attrib.get("Source") or "").lower() == "orcid":
            orcid = _text(node)
            break

    name = f"{last} {fore}".strip() if last else (fore or collective)
    return {
        "name": name,
        "last": last,
        "fore": fore,
        "initials": initials,
        "affiliation": affil_text,
        "is_corresponding": is_corresponding,
        "email": email,
        "orcid": orcid,
        "equal_contrib": author.attrib.get("EqualContrib", "").upper() == "Y",
    }


def parse_article(article_xml: str | ET.Element) -> dict:
    """Parse one PubMedArticle into the downloader's paper dict."""
    if isinstance(article_xml, ET.Element):
        article = article_xml
    else:
        try:
            article = ET.fromstring(article_xml)
        except ET.ParseError:
            article = ET.fromstring(f"<PubmedArticle>{article_xml}</PubmedArticle>")

    article_node = article.find(".//Article")
    pub_year, pub_date = _pub_date(article)
    authors = [_author_record(a) for a in article.findall(".//Article/AuthorList/Author")]
    authors_str = ", ".join(a["name"] for a in authors if a["name"])
    issn, issn_type = _journal_issn(article_node)

    return {
        "pmid": _find_first_text(article, [".//MedlineCitation/PMID", ".//PMID"]),
        "title": _find_text(article_node, "ArticleTitle"),
        "authors_str": authors_str,
        "authors": authors,
        "journal": _find_text(article_node, "Journal/Title"),
        # Journal identity beyond the title string. `journal` is free text that
        # PubMed does not normalise, so one journal appears under its full title
        # on some records and its abbreviation on others; joining a journal table
        # on that alone is guesswork. These four fields are what make the join
        # exact, and efetch has carried all of them all along.
        "issn": issn,
        "issn_type": issn_type,
        # The linking ISSN unifies a journal's print and electronic ISSNs under
        # one number. A journal table usually lists whichever of the two its
        # compiler had, so matching on the article's own ISSN alone misses the
        # other half; this is the value that bridges them.
        "issn_linking": _find_text(article, ".//MedlineJournalInfo/ISSNLinking"),
        # NLM's own abbreviation for the journal, which is what the abbreviated
        # spellings in `journal` actually are. With it, abbreviation matching
        # stops being a heuristic over token prefixes.
        "journal_abbrev": _find_text(article_node, "Journal/ISOAbbreviation"),
        "pub_date": pub_date,
        "pub_year": pub_year,
        "volume": _find_text(article_node, "Journal/JournalIssue/Volume"),
        "issue": _find_text(article_node, "Journal/JournalIssue/Issue"),
        "pages": _find_text(article_node, "Pagination/MedlinePgn"),
        "doi": _article_doi(article),
        "pmc_id": _article_id(article, "pmc"),
        "abstract": _abstract(article),
    }


def _name_matches(author: dict, target_parts: list[str]) -> bool:
    """
    姓名模糊匹配（支持中文拼音的正反序）。
    target_parts: ["zhu", "guangwei"] 或 ["guangwei", "zhu"]
    """
    if not target_parts:
        return False

    last_lower = (author.get("last") or "").lower()
    fore_lower = (author.get("fore") or "").lower()
    initials_lower = (author.get("initials") or "").lower()

    def _given_matches(given: str) -> bool:
        """名字模糊匹配"""
        return (given in fore_lower
                or fore_lower in given
                or (given and initials_lower and given[0] == initials_lower[0]))

    # 正序：target_parts[0] 是姓
    target_last = target_parts[0]
    if target_last == last_lower:
        if len(target_parts) > 1:
            if _given_matches(target_parts[1]):
                return True
        else:
            return True

    # 反序：target_parts[-1] 是姓（如 "Guangwei Zhu"）
    if len(target_parts) >= 2:
        reversed_last = target_parts[-1]
        if reversed_last == last_lower:
            if _given_matches(target_parts[0]):
                return True

    return False


def _affiliation_matches(affil_text: str, affil_keywords: list[str]) -> tuple[bool, str]:
    """
    机构深度模糊匹配。

    返回: (is_match, matched_keyword)

    关键词由调用方通过 config 提供，需覆盖机构在 PubMed affiliation
    字段中出现的各种写法，例如全称、缩写、附属医院、英译名。
    PubMed 不对这些做归一化。
    """
    affil_lower = affil_text.lower()
    if not affil_lower:
        return False, ""

    for kw in affil_keywords:
        if kw.lower() in affil_lower:
            return True, kw

    return False, ""


def _email_domain_matches(email: str, allowed_domains: list[str]) -> bool:
    """检查邮箱后缀是否匹配"""
    if not email or not allowed_domains:
        return False
    email_lower = email.lower()
    for domain in allowed_domains:
        if email_lower.endswith(domain.lower()):
            return True
    return False


# ============================================================
# 作者身份验证配置的兜底默认值
#
# 真正的配置来自 config.json / 环境变量，经 config.py 加载后以
# `identity` 参数传入 is_first_or_corresponding()。此处仅作为调用方
# 未提供配置时的空默认：不做任何机构约束，退化为「姓名匹配即通过」。
# 对常见姓名请务必在 config.json 中填写 affiliation_keywords / orcid。
# ============================================================
AUTHOR_IDENTITY = {
    "affiliation_keywords": [],
    "email_domains": [],
    "orcid": "",
    "require_affiliation": False,
    # OpenAlex 的作者 ID（形如 A5023888391）。空串表示没做过 OpenAlex 消歧。
    # 它与上面四项的区别在于**来源**：那四项是用户自己断言的，这一项是
    # OpenAlex 的作者聚类算法给出的，所以报告里两者分开印，不混成一句
    # 「身份已验证」。见 openalex.resolve_author。
    "openalex_author_id": "",
}


# `A` followed by digits, with no length floor: OpenAlex assigns ids of whatever
# length it needs and a floor would silently reject the short ones as "not an
# id at all", which reads downstream as "no OpenAlex evidence".
_OPENALEX_ID_RE = re.compile(r"(?i)\bA\d+\b")


def normalise_openalex_id(value) -> str:
    """把 OpenAlex 作者 ID 归一成裸的 `A…` 形式，认不出来就返回空串。

    同一个 ID 会以三种写法出现：完整 URI `https://openalex.org/A5023888391`、
    去掉协议的 `openalex.org/a5023888391`、以及裸 ID。三者相等，字符串比较却
    不相等，于是「本人的 ID」和「这条署名上的 ID」会莫名其妙对不上。

    这个纯函数住在 pubmed_api 而不是 openalex，理由和 `_name_matches` /
    `_affiliation_matches` 住在这里一样：`profile/roles.py` 要用它做第二次独立的
    证据判级，而 profile 包一行网络代码都不能引入。openalex.py 反过来从这里导入。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    match = _OPENALEX_ID_RE.search(text)
    return match.group(0).upper() if match else ""


# The record-level counterpart of the byline field `openalex_author_id`, written
# by `openalex.merge_corpora` and read back here. One home for the spelling, for
# the same reason the evidence markers below have one: a writer and a reader
# holding two copies of a key is how a whole corpus quietly loses a field.
OPENALEX_IDS_FIELD = "openalex_author_ids"


def record_openalex_author_ids(paper) -> set[str]:
    """Every OpenAlex author id known to sit on this record, normalised.

    Two places carry them and both count. A record OpenAlex contributed carries
    them on its own byline entries. A record that survived the merge with its
    byline intact carries them under `OPENALEX_IDS_FIELD` instead — the byline
    that held the ids belonged to the copy that was folded away, and which
    surviving byline entry each id belongs to cannot be known without matching
    names across the two, which is a guess this package does not make. So the
    fact is kept at the level it is true at: *this record's OpenAlex work has a
    byline carrying these ids*.

    One reader for both halves, because `profile.roles.resolve_pi` and
    `profile.report.openalex_id_record_share` both answer "did this id reach
    this record" and a corpus described two ways by those two is exactly the
    failure `evidence_tier`'s docstring warns about.
    """
    if not isinstance(paper, Mapping):
        return set()
    found = set()
    for author in (paper.get("authors") or []):
        if isinstance(author, Mapping):
            normalised = normalise_openalex_id(author.get("openalex_author_id"))
            if normalised:
                found.add(normalised)
    stored = paper.get(OPENALEX_IDS_FIELD)
    if isinstance(stored, (list, tuple, set)):
        for value in stored:
            normalised = normalise_openalex_id(value)
            if normalised:
                found.add(normalised)
    return found


# Which identity check kept a paper survives only as a marker in the role
# string, and that string is what papers_*.json persists. Writer and reader
# share these constants so rewording a marker cannot silently reclassify a whole
# corpus as unattributed; tests/test_provenance.py asserts every marker written
# below round-trips through evidence_tier_from_role.
#
# `_MARK_OPENALEX` is written by `openalex.work_to_paper`, not by anything in
# this module: PubMed's efetch carries no OpenAlex id, so a PubMed-sourced record
# can never earn this tier. It is defined here because `evidence_tier_from_role`
# has to read it back, and one constant with two homes is how a marker drifts.
_MARK_ORCID = "[ORCIDOK]"
_MARK_OPENALEX = "[OpenAlexOK"
_MARK_EMAIL = "[EmailOK"
_MARK_AFFILIATION = "[机构OK"

_EVIDENCE_MARKERS = (
    (_MARK_ORCID, "orcid"),
    (_MARK_OPENALEX, "openalex"),
    (_MARK_EMAIL, "email"),
    (_MARK_AFFILIATION, "affiliation"),
)

# The other half of the same contract: the two role strings that say outright
# "this record carried no identity evidence". They exist because "" from
# `evidence_tier_from_role` answers two different questions at once — "no
# evidence" and "written before markers existed" — and a provenance block that
# prints a count has to be able to tell those apart. A record stamped with
# either of these is a *recorded* name-only match; a record with a plain role
# and no marker at all is unrecorded, and stays that way.
_ROLE_NAME_ONLY = "[机构未验证⚠]"   # loose mode kept it: name matched, nothing verified
_ROLE_FALLBACK = "待确认"           # nothing passed verification, so everything was kept


def evidence_tier_from_role(role: str) -> str:
    """
    Which identity check carried this paper, or "" when the role says nothing.

    The empty string is a real answer here: a corpus that predates these markers,
    or one kept by the fallback that stamps every paper 待确认, has no tier to
    report. Returning a tier anyway would invent provenance.
    """
    for marker, tier in _EVIDENCE_MARKERS:
        if marker in (role or ""):
            return tier
    return ""


def is_name_only_role(role: str) -> bool:
    """Whether this role string records a name match that carried no evidence.

    False for a role that predates the markers, which is the whole point: a
    caller counting name-only records must not read an unlabelled old corpus as
    "nobody was name-only". `evidence_tier_from_role` returning "" and this
    returning False together mean "this record's evidence was never recorded".
    """
    text = (role or "").strip()
    return _ROLE_NAME_ONLY in text or text == _ROLE_FALLBACK


def is_first_or_corresponding(
    paper: dict,
    target_name: str,
    target_affiliation: str = "",
    identity: dict | None = None,
) -> tuple[bool, str]:
    """
    判断目标作者是否为该论文的第一/通讯作者，且机构身份可验证。

    核心变更（对比原逻辑）：
    - 原代码：姓名匹配即通过，机构仅作标签 → 大量同名误匹配
    - 新逻辑：姓名匹配后，必须通过「机构/邮箱/ORCID」三重验证之一

    验证优先级：
    1. ORCID 命中 → 直接通过（最强标识符）
    2. 邮箱后缀命中 → 直接通过（强标识符）
    3. 机构关键词命中 → 通过（主要过滤手段）
    4. 以上均不命中 → 拒绝（require_affiliation=True 时）

    返回: (is_match, role_description)
    """
    cfg = identity or AUTHOR_IDENTITY
    target_parts = target_name.lower().split()

    affil_keywords = cfg.get("affiliation_keywords", [])
    email_domains = cfg.get("email_domains", [])
    orcid = cfg.get("orcid", "")
    require_affil = cfg.get("require_affiliation", True)

    # 如果 target_affiliation 不在关键词列表中，自动加入
    if target_affiliation and target_affiliation not in affil_keywords:
        affil_keywords = [target_affiliation] + affil_keywords

    pmid = paper.get("pmid", "?")
    title_short = paper.get("title", "")[:50]

    best_match = None  # 记录最佳匹配结果

    for i, author in enumerate(paper["authors"]):
        if not _name_matches(author, target_parts):
            continue

        # 姓名匹配了，开始身份验证
        roles = []
        identity_evidence = []

        # 角色判定
        if i == 0:
            roles.append("第一作者")
        if i == len(paper["authors"]) - 1:
            roles.append("末位作者")
        if author["is_corresponding"]:
            roles.append("通讯作者")

        if not roles:
            # 既不是第一作者也不是通讯/末位作者，跳过
            logger.debug(
                "[Skip] PMID %s: 姓名匹配但非第一/通讯/末位作者 (位置 %d/%d) — %s",
                pmid, i + 1, len(paper["authors"]), title_short,
            )
            continue

        # === 身份验证（三重） ===

        # 验证1: ORCID（最强）
        author_orcid = author.get("orcid", "")
        if orcid and author_orcid and orcid.lower() == author_orcid.lower():
            identity_evidence.append(f"ORCID={orcid}")
            logger.info(
                "[Keep] PMID %s: ORCID 命中 (%s) — %s | %s",
                pmid, orcid, " / ".join(roles), title_short,
            )
            return True, " / ".join(roles) + f" {_MARK_ORCID}"

        # 验证2: 邮箱后缀（强）
        author_email = author.get("email", "")
        if _email_domain_matches(author_email, email_domains):
            identity_evidence.append(f"Email={author_email}")
            logger.info(
                "[Keep] PMID %s: 邮箱命中 (%s) — %s | %s",
                pmid, author_email, " / ".join(roles), title_short,
            )
            return True, " / ".join(roles) + f" {_MARK_EMAIL} {author_email}]"

        # 验证3: 机构关键词匹配
        affil_text = author.get("affiliation", "")
        affil_ok, matched_kw = _affiliation_matches(affil_text, affil_keywords)

        if affil_ok:
            identity_evidence.append(f"Affiliation='{matched_kw}'")
            logger.info(
                "[Keep] PMID %s: 机构命中 '%s' in '%s' — %s | %s",
                pmid, matched_kw, affil_text[:60], " / ".join(roles), title_short,
            )
            return True, " / ".join(roles) + f" {_MARK_AFFILIATION} {matched_kw}]"

        # 三重验证均未通过 — 记录为候选但不立即拒绝
        # （可能论文中该作者的 affiliation 字段为空或不完整）
        if best_match is None:
            best_match = {
                "roles": roles,
                "affiliation": affil_text,
                "email": author_email,
                "author_index": i,
            }

    # 所有姓名匹配的作者都未通过身份验证
    if best_match:
        roles_str = " / ".join(best_match["roles"])
        affil_str = best_match["affiliation"][:80] if best_match["affiliation"] else "(无机构信息)"

        if require_affil:
            logger.warning(
                "[Skip] PMID %s: 姓名匹配但机构不符 — 角色: %s | 机构: '%s' | %s",
                pmid, roles_str, affil_str, title_short,
            )
            return False, ""
        else:
            # 宽松模式：通过但标记警告
            logger.warning(
                "[Keep?] PMID %s: 姓名匹配但机构未验证（宽松模式放行）— 角色: %s | 机构: '%s' | %s",
                pmid, roles_str, affil_str, title_short,
            )
            return True, roles_str + f" {_ROLE_NAME_ONLY}"

    return False, ""
