#!/usr/bin/env python3
"""
PubMed XML 解析回归测试
=======================
保护从正则解析切换到 ElementTree 之后 parse_article() 的字段映射不退化。

每条用例都是手写的 PubMedArticle XML 片段，覆盖一个易出错的解析路径：
  - 嵌套 <i>/<sup> 标签的标题
  - 多段 AbstractText 带 Label / NlmCategory
  - DOI 在 ELocationID 而非 ArticleIdList
  - PubDate 退化到 MedlineDate
  - 作者带 ORCID Identifier + EqualContrib
  - 机构内含邮箱 → is_corresponding 自动设为 True
  - 空摘要 / 缺字段 不应崩溃

运行：python test_pubmed_parse.py
"""

import os
import sys

# The fixtures contain non-ASCII text, and the Windows console defaults to a
# codepage that cannot encode it.
sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_your_advisor.pubmed_api import parse_article  # noqa: E402  (needs the path bootstrap above)

XML_RICH = """\
<PubmedArticle>
  <MedlineCitation>
    <PMID Version="1">12345678</PMID>
    <Article>
      <Journal>
        <ISSN IssnType="Electronic">2662-1347</ISSN>
        <JournalIssue>
          <Volume>42</Volume>
          <Issue>7</Issue>
          <PubDate>
            <Year>2024</Year>
            <Month>Mar</Month>
            <Day>15</Day>
          </PubDate>
        </JournalIssue>
        <Title>Nature Cancer</Title>
        <ISOAbbreviation>Nat Cancer</ISOAbbreviation>
      </Journal>
      <ArticleTitle>The role of <i>BRCA1</i> in DNA damage response and <sup>K48</sup>-linked ubiquitination.</ArticleTitle>
      <Pagination><MedlinePgn>123-145</MedlinePgn></Pagination>
      <Abstract>
        <AbstractText Label="BACKGROUND" NlmCategory="BACKGROUND">BRCA1 deficiency causes genomic instability.</AbstractText>
        <AbstractText Label="METHODS" NlmCategory="METHODS">We used CRISPR knockouts in HCT116.</AbstractText>
        <AbstractText Label="RESULTS">BRCA1 loss increased K48 ubiquitination of RIPK1.</AbstractText>
      </Abstract>
      <AuthorList>
        <Author EqualContrib="Y">
          <LastName>Li</LastName>
          <ForeName>Wenhao</ForeName>
          <Initials>W</Initials>
          <AffiliationInfo>
            <Affiliation>Department of Hepatobiliary Surgery, Union Hospital, Nanhai Medical University, Example City, China. liwenhao@nanhai-med.example.edu</Affiliation>
          </AffiliationInfo>
          <Identifier Source="ORCID">0000-0002-1825-0097</Identifier>
        </Author>
        <Author>
          <LastName>Wang</LastName>
          <ForeName>Li</ForeName>
          <Initials>L</Initials>
          <AffiliationInfo>
            <Affiliation>Other Lab. Corresponding author.</Affiliation>
          </AffiliationInfo>
        </Author>
      </AuthorList>
    </Article>
    <MedlineJournalInfo>
      <Country>United States</Country>
      <MedlineTA>Nat Cancer</MedlineTA>
      <NlmUniqueID>101block</NlmUniqueID>
      <ISSNLinking>2662-1347</ISSNLinking>
    </MedlineJournalInfo>
  </MedlineCitation>
  <PubmedData>
    <ArticleIdList>
      <ArticleId IdType="pubmed">12345678</ArticleId>
      <ArticleId IdType="doi">10.1038/s43018-024-00777-7</ArticleId>
      <ArticleId IdType="pmc">PMC11111111</ArticleId>
    </ArticleIdList>
  </PubmedData>
</PubmedArticle>
"""


XML_DOI_IN_ELOCATION = """\
<PubmedArticle>
  <MedlineCitation>
    <PMID>22222222</PMID>
    <Article>
      <Journal>
        <JournalIssue>
          <PubDate>
            <MedlineDate>2023 Spring</MedlineDate>
          </PubDate>
        </JournalIssue>
        <Title>Some Journal</Title>
      </Journal>
      <ArticleTitle>Article without ArticleId DOI.</ArticleTitle>
      <ELocationID EIdType="doi" ValidYN="Y">10.1234/foo.2023.0042</ELocationID>
      <ELocationID EIdType="pii">S0000-0000(23)00042-X</ELocationID>
      <AuthorList>
        <Author>
          <LastName>Solo</LastName>
          <ForeName>Han</ForeName>
        </Author>
      </AuthorList>
    </Article>
  </MedlineCitation>
  <PubmedData><ArticleIdList><ArticleId IdType="pubmed">22222222</ArticleId></ArticleIdList></PubmedData>
</PubmedArticle>
"""


XML_MINIMAL = """\
<PubmedArticle>
  <MedlineCitation>
    <PMID>33333333</PMID>
    <Article>
      <Journal><Title>Minimal Journal</Title></Journal>
      <ArticleTitle>Minimal record.</ArticleTitle>
    </Article>
  </MedlineCitation>
</PubmedArticle>
"""


def _check(label: str, actual, expected) -> bool:
    ok = actual == expected
    mark = "[PASS]" if ok else "[FAIL]"
    print(f"  {mark} {label}")
    if not ok:
        print(f"        expected={expected!r}")
        print(f"        actual  ={actual!r}")
    return ok


def _check_contains(label: str, actual: str, needle: str) -> bool:
    ok = needle in (actual or "")
    mark = "[PASS]" if ok else "[FAIL]"
    print(f"  {mark} {label}")
    if not ok:
        print(f"        needle  ={needle!r}")
        print(f"        haystack={actual!r}")
    return ok


def test_rich() -> int:
    print("\n--- 用例 1: 完整字段（嵌套标签 / 多段摘要 / ORCID / EqualContrib / DOI in ArticleId）---")
    p = parse_article(XML_RICH)
    fails = 0
    fails += not _check("pmid", p["pmid"], "12345678")
    fails += not _check("journal", p["journal"], "Nature Cancer")
    fails += not _check("volume", p["volume"], "42")
    fails += not _check("issue", p["issue"], "7")
    fails += not _check("pages", p["pages"], "123-145")
    fails += not _check("pub_year", p["pub_year"], "2024")
    fails += not _check("pub_date", p["pub_date"], "2024 Mar 15")
    fails += not _check("doi", p["doi"], "10.1038/s43018-024-00777-7")
    fails += not _check("pmc_id", p["pmc_id"], "PMC11111111")
    # 嵌套标签应被 itertext 展平为纯文本
    fails += not _check_contains("title flattens nested <i>", p["title"], "BRCA1 in DNA damage")
    fails += not _check_contains("title flattens <sup>", p["title"], "K48-linked")
    # 多段摘要带 Label 前缀
    fails += not _check_contains("abstract has BACKGROUND label", p["abstract"], "BACKGROUND: BRCA1 deficiency")
    fails += not _check_contains("abstract has METHODS label", p["abstract"], "METHODS: We used CRISPR")
    fails += not _check_contains("abstract has RESULTS label", p["abstract"], "RESULTS: BRCA1 loss")
    # 作者：ORCID / EqualContrib / 邮箱 / is_corresponding
    a0, a1 = p["authors"]
    fails += not _check("author[0].name", a0["name"], "Li Wenhao")
    fails += not _check("author[0].orcid", a0["orcid"], "0000-0002-1825-0097")
    fails += not _check("author[0].equal_contrib", a0["equal_contrib"], True)
    fails += not _check("author[0].email", a0["email"], "liwenhao@nanhai-med.example.edu")
    fails += not _check("author[0].is_corresponding (via email)", a0["is_corresponding"], True)
    fails += not _check("author[1].is_corresponding (via 'Corresponding' text)", a1["is_corresponding"], True)
    fails += not _check("author[1].equal_contrib default false", a1["equal_contrib"], False)
    # authors_str 拼接顺序保持
    fails += not _check("authors_str", p["authors_str"], "Li Wenhao, Wang Li")
    return fails


def test_doi_in_elocation_and_medline_date() -> int:
    print("\n--- 用例 2: DOI 退化到 ELocationID + PubDate 退化到 MedlineDate ---")
    p = parse_article(XML_DOI_IN_ELOCATION)
    fails = 0
    fails += not _check("pmid", p["pmid"], "22222222")
    fails += not _check("doi from ELocationID", p["doi"], "10.1234/foo.2023.0042")
    # MedlineDate 没有 Year 标签，应从 "2023 Spring" 抽 4 位数字
    fails += not _check("pub_year extracted from MedlineDate", p["pub_year"], "2023")
    fails += not _check("pmc_id absent", p["pmc_id"], "")
    fails += not _check("abstract empty", p["abstract"], "")
    return fails


def test_minimal_record_no_crash() -> int:
    print("\n--- 用例 3: 最小记录（无 PubDate / 无作者 / 无摘要）---")
    p = parse_article(XML_MINIMAL)
    fails = 0
    fails += not _check("pmid", p["pmid"], "33333333")
    fails += not _check("title", p["title"], "Minimal record.")
    fails += not _check("journal", p["journal"], "Minimal Journal")
    fails += not _check("authors empty", p["authors"], [])
    fails += not _check("authors_str empty", p["authors_str"], "")
    fails += not _check("abstract empty", p["abstract"], "")
    fails += not _check("pub_year empty", p["pub_year"], "")
    fails += not _check("doi empty", p["doi"], "")
    return fails


def test_journal_identity() -> int:
    """ISSN and the ISO abbreviation, which decide whether a journal table joins.

    `journal` is free text PubMed does not normalise: one journal appears under
    its full title on some records and its abbreviation on others, so a table
    joined on the title alone matches whichever spelling its compiler used and
    silently misses the rest. These four fields are what make the join exact,
    and efetch carried all of them for years while `parse_article` dropped them
    on the floor — the worklist that shipped before this test had an empty ISSN
    column for every one of its 26 rows.
    """
    print("\n--- 用例 4: 期刊身份（ISSN / ISSNLinking / ISO 缩写）---")
    fails = 0

    p = parse_article(XML_RICH)
    fails += not _check("issn", p["issn"], "2662-1347")
    # The type travels with the number: an electronic ISSN compared against a
    # table's print column is a miss that reads as an absence.
    fails += not _check("issn_type", p["issn_type"], "Electronic")
    fails += not _check("issn_linking", p["issn_linking"], "2662-1347")
    fails += not _check("journal_abbrev", p["journal_abbrev"], "Nat Cancer")

    # A record with no ISSN at all is normal — older records and non-indexed
    # journals genuinely have none — and must parse to empty, not raise.
    m = parse_article(XML_MINIMAL)
    fails += not _check("issn empty on a minimal record", m["issn"], "")
    fails += not _check("issn_type empty on a minimal record", m["issn_type"], "")
    fails += not _check("issn_linking empty on a minimal record", m["issn_linking"], "")
    fails += not _check("journal_abbrev empty on a minimal record", m["journal_abbrev"], "")

    # The consumer side: journals.paper_issn is what the worklist and the join
    # both call, so its fallback order is asserted here rather than trusted.
    from check_your_advisor.journals import paper_issn
    fails += not _check("paper_issn reads a parsed record", paper_issn(p), "2662-1347")
    fails += not _check("paper_issn prefers the article's own ISSN",
                        paper_issn({"issn": "0028-0836", "issn_linking": "1476-4687"}),
                        "0028-0836")
    fails += not _check("paper_issn falls back to the linking ISSN",
                        paper_issn({"issn": "", "issn_linking": "1476-4687"}),
                        "1476-4687")
    # Corpora harvested before these fields existed carry none of them and must
    # fall through to name matching, exactly as they did before.
    fails += not _check("paper_issn on a pre-ISSN corpus record",
                        paper_issn({"journal": "Old Corpus Journal"}), "")
    return fails


def main() -> int:
    print("=" * 70)
    print("PubMed XML 解析回归测试 (parse_article)")
    print("=" * 70)
    fails = 0
    fails += test_rich()
    fails += test_doi_in_elocation_and_medline_date()
    fails += test_minimal_record_no_crash()
    fails += test_journal_identity()
    print("\n" + "=" * 70)
    if fails == 0:
        print("全部通过")
    else:
        print(f"共 {fails} 个断言失败")
    print("=" * 70)
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
