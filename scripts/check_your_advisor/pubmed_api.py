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
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger("check_your_advisor.pubmed")

BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


USER_AGENT = "check-your-advisor/1.0"


def _retry_after_seconds(err: HTTPError, attempt: int) -> float:
    """NCBI's own Retry-After when it sends one, otherwise a linear backoff."""
    raw = err.headers.get("Retry-After") if err.headers else None
    try:
        return min(float(raw), 30.0) if raw else 2.0 * (attempt + 1)
    except (TypeError, ValueError):
        return 2.0 * (attempt + 1)


def _eutils_get(endpoint: str, params: dict, timeout: float, retries: int = 2) -> bytes:
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
    """
    url = f"{BASE_URL}/{endpoint}?{urlencode(params)}"
    attempt = 0
    while True:
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
) -> list[str]:
    """
    搜索 PubMed，返回 PMID 列表。

    检索层的目标是**高召回**，精确性由下游身份验证负责。但通配式作者检索
    对常见姓氏会命中上万条，远超 retmax 而被静默截断。因此这里采用自适应策略：

      1. 先用最宽的检索式探测命中数(retmax=0，不取数据)
      2. 命中数在 retmax 以内 → 直接用宽检索式，召回最大化
      3. 超出且配置了机构关键词 → 加机构条件收窄，并明确告知用户
      4. 仍然超出 → 大声告警，绝不静默丢数据

    `provenance` 是可选的输出参数：传一个 dict 进来，检索的实际参数与命中数会
    写进去。这些量本来只存在于本函数内部，随日志一起消失，于是下游的画像报告
    无从判断语料是否被截断——它只能把 G1 门禁标成 `unknown`，也就是一个永远
    不会触发的门禁。用可选参数而不是改返回值，是为了不动现有调用方。
    """
    today = datetime.now()
    start_date = today - timedelta(days=years_back * 365)
    mindate = start_date.strftime("%Y/%m/%d")
    maxdate = today.strftime("%Y/%m/%d")

    identity = identity or {}
    orcid = (identity.get("orcid") or "").strip()
    affil_keywords = identity.get("affiliation_keywords") or []

    def _request(term: str, want: int) -> tuple[list[str], int]:
        params = {
            "db": "pubmed", "term": term, "retmax": want,
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
        "retmax": retmax, "narrowed_by_affiliation": False,
    })

    broad = build_search_query(author, orcid=orcid)
    if not broad:
        logger.error("作者名与 ORCID 均为空，无法检索。")
        record.update({"esearch_term": "", "esearch_matched": 0,
                       "pmids_returned": 0, "truncated": False})
        return []

    _, broad_total = _request(broad, 0)
    query = broad

    if broad_total > retmax and affil_keywords:
        narrowed = build_search_query(author, orcid=orcid, affiliation_keywords=affil_keywords)
        _, narrowed_total = _request(narrowed, 0)
        logger.warning(
            "宽检索命中 %d 条，超过 retmax=%d；已自动加入机构条件收窄至 %d 条。"
            "机构字段未被 PubMed 索引的论文可能因此漏掉——如需全量请调高 retmax。",
            broad_total, retmax, narrowed_total,
        )
        query = narrowed
        record.update({"narrowed_by_affiliation": True,
                       "broad_term": broad, "broad_matched": broad_total})

    logger.info("搜索 PubMed: %s (%s ~ %s)", query, mindate, maxdate)
    id_list, total = _request(query, retmax)
    logger.info("找到 %d 条结果，获取 %d 个 PMID", total, len(id_list))
    record.update({
        "esearch_term": query,
        "esearch_matched": total,
        "pmids_returned": len(id_list),
        "truncated": total > len(id_list),
    })

    # 静默截断会让用户以为「这就是全部」。宁可吵，也不要悄悄丢数据。
    if total > len(id_list):
        logger.warning(
            "结果被 retmax=%d 截断：命中 %d 条，仅取回 %d 条，剩余 %d 条未被检查。"
            "请缩小 years_back、补充 affiliation_keywords/orcid，或调高 retmax 后重跑。",
            retmax, total, len(id_list), total - len(id_list),
        )
    return id_list


def fetch_details(pmids: list[str], api_key: str, delay: float = 0.15) -> list[dict]:
    """批量获取论文详情"""
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

        xml_text = _eutils_get("efetch.fcgi", params, timeout=60).decode("utf-8", errors="replace")

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.error("PubMed XML 解析失败: %s", e)
            continue

        for article in root.findall(".//PubmedArticle"):
            paper = parse_article(article)
            if paper:
                papers.append(paper)

        if i + batch_size < len(pmids):
            time.sleep(delay)

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
}


# Which of the three identity checks kept a paper survives only as a marker in
# the role string, and that string is what papers_*.json persists. Writer and
# reader share these constants so rewording a marker cannot silently reclassify
# a whole corpus as unattributed; tests/test_provenance.py asserts every marker
# written below round-trips through evidence_tier_from_role.
_MARK_ORCID = "[ORCIDOK]"
_MARK_EMAIL = "[EmailOK"
_MARK_AFFILIATION = "[机构OK"

_EVIDENCE_MARKERS = (
    (_MARK_ORCID, "orcid"),
    (_MARK_EMAIL, "email"),
    (_MARK_AFFILIATION, "affiliation"),
)


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
            return True, roles_str + " [机构未验证⚠]"

    return False, ""
