#!/usr/bin/env python3
"""
Command-line entry point.

Subcommands
-----------
  profile           What the publication record says about being this PI's student
  harvest           Search PubMed, filter by author identity, download OA PDFs
                    (also accepted as `fetch`, the name it shipped under). Can
                    also resolve the author against OpenAlex — listing every
                    candidate rather than choosing one — and merge that author's
                    OpenAlex records in as a second, deduplicated source
  cite              Fetch citation counts for a harvested corpus into their own file
  compare           Lay several corpora side by side, ranked, under one weight table
  diff              Compare two harvests of one advisor: what is in one and not the
                    other, split on whether the search window moved between them
  journal-worklist  Write the journals this corpus uses into a CSV to go and look up
  journal-risk      Collect DOAJ, Crossref and OpenAlex statements about those
                    journals from keyless public APIs, into their own dated file.
                    Statements with sources and dates, never a rating
  download          Retry PDF downloads from a previously exported papers_*.json
  clean-cache       Drop stale failure records from the SQLite cache

Run without a subcommand to get `fetch` (kept for backwards compatibility).

What the report will now turn a number into, and what it still will not:

- `compare` ranks the corpora on its page by composite score, prints each
  position with the count it was taken over, gives each corpus a star band, and
  will say in a sentence which of two scored higher. All three were refused in
  the previous round and have been reversed deliberately.
- There is still no percentile and no quantile position — those need a reference
  population and this toolkit holds none. There is still no letter tier: stars
  are produced and A/B/C is not, which is a decision rather than an oversight.
  There is still no fitted trend and no slope.
- No ordering of *people*, anywhere. No roster is ever sorted by a count.

`journal-worklist` is the front half of the journal-metric join and it makes no
network request: impact factors and CAS partitions live in licensed databases
that forbid scraping, so this writes the couple of dozen journals your corpus
actually uses into a CSV in the table's own shape, and you fill it in from
LetPub or ablesci and hand it back with `profile --journal-table`. The same
division of labour covers the degree-thesis roster behind `--thesis-roster`,
which is the only source that can count graduates who never published, and the
student evaluations behind `--evaluation-table`, which are printed as collected
beside their source and their retrieval date and are never scored, averaged or
folded into the composite. That third table has no worklist verb and will not
get one: a corpus names its own journals, and nothing in a corpus names the
places people talk about an advisor.
"""

import argparse
import logging
import os
import sys
from datetime import datetime
from collections.abc import Mapping
from typing import Any

from check_your_advisor.config import DEFAULT_CONFIG, load_config


def setup_logging(output_dir: str, level: str = "INFO"):
    """配置日志：同时输出到控制台和文件"""
    os.makedirs(output_dir, exist_ok=True)
    log_file = os.path.join(output_dir, f"download_{datetime.now():%Y%m%d_%H%M%S}.log")

    root = logging.getLogger("check_your_advisor")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()

    # 控制台
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    root.addHandler(console)

    # 文件（DEBUG 级别，记录所有细节）
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    ))
    root.addHandler(file_handler)

    return log_file


def parse_fetch_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="check-your-advisor [harvest]",
        description="检索一位研究者的论文、把同名的人挡在外面、可选下载 PDF（默认子命令；可省略 harvest）",
    )
    parser.add_argument("--config", help="JSON 配置文件路径（默认自动读取 config.json）")
    parser.add_argument("--author", dest="author_name", help="目标作者名")
    parser.add_argument("--affiliation", help="目标机构名")
    parser.add_argument("--years-back", type=int, help="向前检索年数")
    parser.add_argument("--email", help="Unpaywall 邮箱")
    parser.add_argument("--api-key", help="PubMed API key")
    parser.add_argument("--output-dir", help="输出目录")
    parser.add_argument("--pdf-dir", help="PDF 输出目录")
    parser.add_argument("--cache-db", help="SQLite 缓存路径")
    parser.add_argument("--max-workers", type=int, help="论文级并发下载线程数")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="日志级别")
    parser.add_argument("--no-download", action="store_true", help="只检索与导出清单，不下载 PDF")

    identity = parser.add_argument_group(
        "作者身份验证",
        "把同名的另一个人挡在语料之外。一条都不给，得到的就是「所有叫这个名字的人」，"
        "报告照常生成，但第 0/1/19 节顶部会带一行醒目提示说明这份语料没有身份证据、"
        "进程退出码是 1。以前这些只能写在 config 文件里。",
    )
    identity.add_argument("--orcid", help="本人 ORCID（最强证据，一条顶其余全部）")
    identity.add_argument("--email-domain", action="append", dest="email_domains",
                          metavar="DOMAIN", help="通讯邮箱域名，可重复给（如 pumc.edu.cn）")
    identity.add_argument("--affiliation-keyword", action="append", dest="affiliation_keywords",
                          metavar="KEYWORD",
                          help="机构关键词，可重复给。不给则自动取 --affiliation 的值")
    identity.add_argument("--require-affiliation", action="store_true", default=None,
                          help="机构不匹配就直接剔除，而不是标记为未验证后保留")

    openalex = parser.add_argument_group(
        "OpenAlex（免费、无密钥）",
        "第二个来源，两件事：按姓名+机构查作者候选做消歧，以及把该作者名下的 works "
        "拉回来与 PubMed 语料合并去重。候选多于一个时不替你选——列出来让你认，"
        "认出后加 --openalex-author-id 重跑。--email 会作为 mailto 一并发出，"
        "那不是密钥，只换一条更宽松的限速通道。",
    )
    openalex.add_argument("--resolve-openalex", action="store_true", default=None,
                          help="查 OpenAlex authors 接口，列出叫这个名字的候选作者及其 "
                               "ORCID/机构史/作品数")
    openalex.add_argument("--openalex-author-id", metavar="ID",
                          help="直接指定 OpenAlex 作者 ID（如 A5023888391），跳过候选查询")
    openalex.add_argument("--openalex-works", action="store_true", default=None,
                          help="额外从 OpenAlex 拉该作者 ID 名下的作品，与 PubMed 语料按 "
                               "DOI → PMID → 标题+年份 去重合并（需要一个确定的作者 ID）")
    openalex.add_argument("--max-works", type=int, metavar="N",
                          help="OpenAlex 一次最多取回多少条作品（默认 2000）")
    return parser.parse_args(argv)


def parse_clean_cache_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="check-your-advisor clean-cache",
        description="清理 SQLite 缓存里的过期失败记录（下载成功的记录不动）",
    )
    parser.add_argument("--config", help="JSON 配置文件路径（用于读取 cache_db）")
    parser.add_argument("--cache-db", help="SQLite 缓存路径（覆盖 config）")
    parser.add_argument("--output-dir", help="输出目录（用于推断默认 cache 路径）")
    parser.add_argument("--max-age-days", type=int, default=90,
                        help="清理 N 天之前的失败记录（默认 90）")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO")
    return parser.parse_args(argv)


def parse_download_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="check-your-advisor download",
        description="只跑 PDF 下载阶段：从已有 papers_*.json 读论文清单，跳过 PubMed efetch",
    )
    parser.add_argument("--config", help="JSON 配置文件路径（默认自动读取 config.json）")
    parser.add_argument("--input", help="papers_*.json 路径（默认取 output-dir 下最新一份）")
    parser.add_argument("--output-dir", help="输出目录（默认 pubmed_results）")
    parser.add_argument("--pdf-dir", help="PDF 输出目录（默认 output-dir/pdfs）")
    parser.add_argument("--cache-db", help="SQLite 缓存路径")
    parser.add_argument("--max-workers", type=int, help="论文级并发下载线程数")
    parser.add_argument("--email", help="Unpaywall 邮箱")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO")
    return parser.parse_args(argv)


def parse_profile_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="check-your-advisor profile",
        description="从已有 papers_*.json 生成导师画像：发表记录反映出的「当这位 PI 的学生是什么样」",
        epilog="报告给一个 0-100 的综合分，并把权重表和星级分档边界原样印出来。"
               "不给百分位（没有参照人群，算不出），不给 A/B/C 字母等级，不做趋势拟合，"
               "也不给任何人排名次——名单永远不按数量排序。样本量下限是规范的一部分，不作为参数暴露。",
    )
    parser.add_argument("--config", help="JSON 配置文件路径（提供 author_identity、advisor 与 scoring.weights 配置）")
    parser.add_argument("--output-dir", help="报告输出目录（默认与 fetch 相同：pubmed_results）")
    parser.add_argument("--papers-json", help="papers_*.json 路径（默认取 output-dir 下最新一份）")
    parser.add_argument("--citations-json",
                        help="citations_*.json 路径（默认取 output-dir 下最新一份；没有就跳过影响力段落并说明原因）")
    parser.add_argument("--no-citations", action="store_true",
                        help="不读引用数文件。第 15 节会写明是这个开关关掉的，而不是没抓到数据")
    parser.add_argument("--pi-name", help="目标 PI 名（默认取 config 的 author_name）")
    external = parser.add_argument_group(
        "手工补的外部对照表",
        "三张表都不联网抓，本工具也不带爬虫：分区表是有版权的商业产品，学位论文库有反爬，"
        "学生评价散在论坛和评价站上、同样不允许自动采集。"
        "工具只负责定 schema、吐待查清单、join、并把来源和日期印在报告里；查表是人干的。"
        "不给表也不会报错，报告会写明「未提供对照表」和补表的命令，而不是留一列空白。",
    )
    external.add_argument("--chinese-records", metavar="CSV",
                          help="中文期刊题录 CSV（知网/万方等自行导出），并入语料。"
                               "这一张和其余三张不同：它改变语料本身，报告里每个数字都会跟着变——"
                               "这正是它存在的理由（只查 PubMed 时中文核心期刊完全看不见）。"
                               "去重只能靠「归一化标题 + 年份」一级，跨源命中一律标为「疑似重复」，"
                               "三个分母分别印出、不合成一个")
    external.add_argument("--journal-table", metavar="CSV",
                          help="期刊指标表 CSV（ISSN/刊名/影响因子/JCR分区/中科院大类小类/"
                               "版本来源/数据获取日期/是否预警）。空白模板用 journal-worklist 生成。"
                               "版本来源与数据获取日期是必需列——缺了以后无法追溯某个分区是哪一版")
    # Not in the `external` group: it is not hand-filled. It is a dated file the
    # `journal-risk` verb wrote from three open APIs, so it belongs with
    # --citations-json, which is the other flag of exactly that kind.
    parser.add_argument("--journal-risk-json",
                        help="journal_risk_*.json 路径（默认取 output-dir 下最新一份）。"
                             "第 18 节会按 ISSN 把 DOAJ 收录状态与 Crossref 元数据完整度"
                             "并到期刊表旁边，只列事实与来源日期，不给等级也不下结论")
    parser.add_argument("--no-journal-risk", action="store_true",
                        help="不读风险信号文件。第 18 节会写明是这个开关关掉的，而不是没采到")
    external.add_argument("--thesis-roster", metavar="CSV",
                          help="学位论文名单 CSV（导师姓名/学生姓名/学位类型/毕业年/库来源/导出日期）。"
                               "这是唯一能数出「毕业了但一篇 PubMed 都没有」的那批人的来源。"
                               "建议加一列 学生姓名拼音，否则中文名单与英文署名根本对不上")
    external.add_argument("--evaluation-table", metavar="CSV",
                          help="学生评价 CSV（导师姓名/评价来源/数据获取日期/评价内容或维度评分，"
                               "可选 学生身份、评价年份、原文链接）。第 20 节按原样列出每一条并注明出处，"
                               "不做情感分析、不汇总、不打分，也不进综合分；评价来源与数据获取日期是必需列")
    delivery = parser.add_argument_group(
        "导出",
        "HTML 报告始终生成，这一组只加副本。PDF 走本机已装的转换器"
        "（wkhtmltopdf / Chrome 无头 / weasyprint / soffice），本包不带 PDF 引擎、"
        "也不为此新增任何依赖；一个都没装就打印怎么装，HTML 输出一个字节都不变。",
    )
    delivery.add_argument("--pdf", action="store_true",
                          help="把 HTML 报告转一份 PDF 放在旁边。没有可用转换器时不报错，"
                               "只打印装哪一个的说明")
    delivery.add_argument("--pdf-converter",
                          choices=["wkhtmltopdf", "chrome", "weasyprint", "soffice"],
                          help="指定优先使用哪个转换器（默认按上面的顺序取第一个找得到的）")
    delivery.add_argument("--pdf-converter-path", metavar="EXE",
                          help="转换器的可执行文件全路径。装了但不在 PATH 上时用这个"
                               "（Windows 上的 Chrome 与 LibreOffice 通常都不在 PATH）")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO")
    return parser.parse_args(argv)


def parse_journal_worklist_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="check-your-advisor journal-worklist",
        description="扫语料，吐出「本语料实际用到的刊」待查清单 CSV。不联网，不抓任何一页。",
        epilog="全球两万多本刊逐本查是几百小时；一份五年语料只用到二十来本。清单按篇数从多到少排，"
               "先查最值钱的。填的时候注意：LetPub 检索结果列表页默认显示的是民间版分区，"
               "要官方版得点进详情页；科研通 ablesci 把官方版与新锐版分开标注，可交叉验证；"
               "Clarivate MJL 免费但只有 SCIE/SSCI 收录状态，没有影响因子也没有分区。"
               "同一本刊查到两版就写两行，各标各的版本来源，不要挑一个填。"
               "填完用 profile --journal-table 传回来即可，查过的刊留在表里，下一份语料自动复用。",
    )
    parser.add_argument("--config", help="JSON 配置文件路径（默认自动读取 config.json）")
    parser.add_argument("--output-dir", help="语料所在目录，也是清单的写入目录（默认 pubmed_results）")
    parser.add_argument("--papers-json", help="papers_*.json 路径（默认取 output-dir 下最新一份）")
    parser.add_argument("--out", metavar="CSV",
                        help="清单写到哪（默认 output-dir/journal_worklist_<时间戳>.csv）")
    parser.add_argument("--risk-json", nargs="?", const="", metavar="PATH",
                        help="把 journal-risk 采到的公开风险信号预填进清单的「风险信号」列"
                             "（不带值则取 output-dir 下最新一份 journal_risk_*.json）。"
                             "只填信号名与采集日期，完整陈述在报告第 18 节；"
                             "本命令自己仍然不联网")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO")
    return parser.parse_args(argv)


def parse_journal_risk_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="check-your-advisor journal-risk",
        description="给本语料用到的刊采集公开风险信号，写 journal_risk_<时间戳>.json。"
                    "只报事实，不下结论——不产出任何等级、评分、颜色或「掠夺性」判定。",
        epilog="三个免密钥公开接口：DOAJ 收录状态、Crossref 元数据完整度、OpenAlex 刊记录。"
               "三个都查，不是先命中者胜——它们回答的是不同问题且经常互相矛盾，矛盾照原样并列印出。"
               "每条信号都带来源接口与获取日期。注意两件事：未被 DOAJ 收录不是问题（DOAJ 只收 OA 刊，"
               "订阅刊天生不在里面）；Crossref 某项元数据为 0 也不是问题（Journal of Hepatology "
               "的 abstracts-backfile 就是 0）。中科院预警名单没有开放接口，不抓，"
               "继续走期刊表里手填的 是否预警 列。",
    )
    parser.add_argument("--config", help="JSON 配置文件路径（默认自动读取 config.json）")
    parser.add_argument("--output-dir", help="语料所在目录，也是风险信号文件的写入目录（默认 pubmed_results）")
    parser.add_argument("--papers-json", help="papers_*.json 路径（默认取 output-dir 下最新一份）")
    parser.add_argument("--email", help="Crossref/OpenAlex 的 mailto（不是密钥，只换一条更宽松的限速通道；"
                                        "不会写进风险信号文件，也不会印进报告）")
    parser.add_argument("--max-workers", type=int, help="并发采集线程数（默认取 config 的 max_workers）")
    parser.add_argument("--max-age-days", type=int, default=0, metavar="N",
                        help="沿用上一份 journal_risk_*.json 里 N 天内采到的信号，只补采其余的。"
                             "默认 0 = 全部重采。沿用的记录保留它自己的采集时间；"
                             "上次三源都没应答的不沿用")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO")
    return parser.parse_args(argv)


def parse_cite_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="check-your-advisor cite",
        description="给已有语料抓引用数：读 output-dir 下最新 papers_*.json，"
                    "写 citations_<时间戳>.json。papers_*.json 一个字节都不动。",
        epilog="三级来源 OpenAlex → Semantic Scholar → Europe PMC，都不需要密钥，"
               "先命中者胜，命中来源逐条记录。引用数是随时间变化的量，所以单独成文件、"
               "每条自带抓取时间，绝不并回语料——否则一份能放几年的语料会被一个下个月就过期的"
               "数字拖着一起失效。输出按语料顺序排列，不按引用数排序。",
    )
    parser.add_argument("--config", help="JSON 配置文件路径（默认自动读取 config.json）")
    parser.add_argument("--output-dir", help="语料所在目录，也是引用数文件的写入目录（默认 pubmed_results）")
    parser.add_argument("--papers-json", help="papers_*.json 路径（默认取 output-dir 下最新一份）")
    parser.add_argument("--email", help="OpenAlex 的 mailto（不是密钥，只是换一条更宽松的限速通道）")
    parser.add_argument("--max-workers", type=int, help="并发抓取线程数（默认取 config 的 max_workers）")
    # Accepted so a wrapper that passes the same flags to every subcommand does
    # not fail here, and refused a silent role: none of the three citation
    # sources takes a key, so this value is not sent anywhere and cmd_cite says
    # so out loud when it is supplied. A flag that looks like it did something
    # is worse than a flag that is not offered.
    parser.add_argument("--api-key", help="接受但不使用：三个引用数来源都不需要密钥，给了也不会被发出去")
    # Default 0 rather than a comfortable 30: a citation count moves every week,
    # and quietly serving a month-old one would undercut the reason this file is
    # dated at all. Reuse is a thing you ask for. When there is a recent file to
    # reuse and this was not passed, cmd_cite says the flag exists.
    parser.add_argument("--max-age-days", type=int, default=0, metavar="N",
                        help="沿用上一份 citations_*.json 里 N 天内抓到的计数，只补抓其余的。"
                             "默认 0 = 全部重抓。沿用的记录保留它自己的抓取时间，不会被记成今天；"
                             "上次三源皆未命中的不沿用，因为覆盖率会随来源收录而自行变好")
    # Off by default because it costs one extra request per paper plus one per
    # topic-year, against an API that asks for nothing in return. The counts are
    # the answer on their own; the cell is what makes a count readable.
    parser.add_argument("--percentile", action="store_true",
                        help="再问一次 OpenAlex：每篇的引用数在「同领域同年」的全部论文里排在什么位置。"
                             "写成单独的 impact_reference_*.json，不并进语料。"
                             "定位不到的论文明确标出原因，绝不当成低百分位")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO")
    return parser.parse_args(argv)


def parse_diff_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="check-your-advisor diff",
        description="比较同一位导师的两份语料：新增了什么、谁第一次出现、一作名额落在谁头上",
        epilog="第一个目录是旧的，第二个是新的。**先读窗口那一段再读任何计数**："
               "如果两次检索的年份范围不一样，「新增 7 篇」很可能只是这次多查了两年，"
               "那不是新增，是口径变了，本命令会显著地说出来。"
               "一条记录只在其中一份里，本命令只报这个事实，不说它为什么不在——"
               "撤稿、检索式不同、身份证据不同、书目库当天的应答都可能，工具分不出来，也不猜。",
    )
    parser.add_argument("old_dir", metavar="OLD_DIR", help="较早那份语料所在目录")
    parser.add_argument("new_dir", metavar="NEW_DIR", help="较新那份语料所在目录")
    parser.add_argument("--out", metavar="JSON", help="把完整差异写成 JSON（默认只打印摘要）")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO")
    return parser.parse_args(argv)


def cmd_diff(argv: list[str]):
    """Compare two harvests of one advisor.

    The summary is printed in an order that is itself part of the output: the
    window block first, then the counts. A reader who sees "7 more papers"
    before learning that the newer harvest looked at two extra years has already
    drawn the wrong conclusion, and no amount of caveat further down undoes it.
    """
    args = parse_diff_args(argv)
    setup_logging(args.log_level)
    logger = logging.getLogger("check_your_advisor.diff")

    import json as _json

    from check_your_advisor.corpus import find_latest_json
    from check_your_advisor.corpus_diff import diff_corpora
    from check_your_advisor.export import load_papers_json

    # `_profile_corpus` rather than a dict built here: it is what assembles the
    # `query` block, and `corpus_diff` reads the recorded window out of that
    # block. Building the wrapper by hand would leave the window unrecorded, and
    # an unrecorded window is exactly the case where this command has to say
    # "cannot be compared" instead of counting.
    loaded = {}
    for label, directory in (("old", args.old_dir), ("new", args.new_dir)):
        path = find_latest_json(directory)
        if not path:
            logger.error("%s 目录下没有 papers_*.json: %s", label, directory)
            return 1
        papers, search = load_papers_json(path)
        loaded[label] = _profile_corpus(papers, {}, search)
        logger.info("%s 语料: %s (%d 篇)", label, path, len(papers))

    result = diff_corpora(loaded["old"], loaded["new"])

    logger.info("=" * 60)
    # Deliberately first, and deliberately before any count. `notes[0]` is the
    # window note by contract in `corpus_diff`.
    for note in result["notes"]:
        logger.info("%s", note)
    logger.info("-" * 60)

    papers = result["papers"]
    logger.info("论文: 旧 %d 篇 → 新 %d 篇；两边都有 %d，只在旧的里 %d，只在新的里 %d",
                papers["old_total"], papers["new_total"], papers["n_in_both_new_side"],
                papers["n_only_in_old"], papers["n_only_in_new"])
    by_window = papers.get("by_window") or {}
    if by_window.get("applicable"):
        logger.info("  只在新的里那 %d 篇中：%d 篇落在两次都查过的年份内（这才是新增），"
                    "%d 篇落在只有新语料查过的年份里（这是口径变了），%d 篇没有年份",
                    papers["n_only_in_new"],
                    by_window.get("only_in_new_inside_shared", 0),
                    by_window.get("only_in_new_outside_shared", 0),
                    by_window.get("only_in_new_undated", 0))

    people = result["people"]
    logger.info("人员: 旧 %d 人 → 新 %d 人；两边都有 %d，只在旧的里 %d，只在新的里 %d",
                people["old_total"], people["new_total"], people["n_in_both"],
                people["n_only_in_old"], people["n_only_in_new"])

    slots = result["first_author_slots"]
    logger.info("一作名额: 旧 %d 个 → 新 %d 个；其中 %d 个落在只有新语料才有的记录上",
                slots["old_total"], slots["new_total"], slots["n_on_records_only_in_new"])

    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            _json.dump(result, handle, ensure_ascii=False, indent=2)
        logger.info("完整差异已写入: %s", args.out)
    return 0


def parse_compare_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="check-your-advisor compare",
        description="把 N 份语料并排放在一页上：名次、综合分、星级、分项值，以及相对第一列的分项差值",
        epilog="默认按综合分从高到低排名次，每个名次都带「几份里的第几」——两份里的第一和九份里的第一"
               "不是一回事。也可以 --order-by label 回到按语料名排。星级旁边还有一个字母档，"
               "两者是同一个分数在同一组边界上的两种拼法。仍然不给「在你加载的这几份语料里的百分位」"
               "——那几份是你自己选的，加一份删一份位置就变，这个量算不出来。"
               "引用数的百分位是另一回事，见 cite --percentile。"
               "所有语料必须使用同一张权重表，否则拒绝出图。",
    )
    parser.add_argument("output_dirs", nargs="+", metavar="OUTPUT_DIR",
                        help="要并排的语料目录，每个目录下要有 papers_*.json")
    parser.add_argument("--config", action="append", dest="configs", metavar="PATH",
                        help="JSON 配置文件路径。给一个则全部共用；给多个则按语料目录顺序一一对应")
    parser.add_argument("--pi-name", action="append", dest="pi_names", metavar="NAME",
                        help="目标 PI 名，规则同 --config：给一个全用，给多个按顺序对应")
    parser.add_argument("--output-dir", dest="report_dir", default=".",
                        help="对比结果的写入目录（默认当前目录；这是输出，不是被比较的语料）")
    parser.add_argument("--order-by", choices=["score", "label"], default="score",
                        help="列的排序依据：score=按综合分从高到低（默认），label=按语料名字典序。"
                             "无论哪种，名次都照算照印——名次是这组语料的属性，不是列顺序的属性")
    parser.add_argument("--no-citations", action="store_true",
                        help="所有语料都不读引用数文件，两个引用数分项会一并标为无数据")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO")
    return parser.parse_args(argv)


def _has_identity_evidence(identity: dict, merge_works: bool = False) -> bool:
    """Whether anything unique to this person will reach the harvested records.

    A name is not evidence. `require_affiliation` is not evidence either — it
    only says how strictly to apply keywords that may not exist.

    A resolved OpenAlex author id counts only when `merge_works` is on, and
    counts as the weakest thing here that counts at all. The condition is not a
    policy choice: `pubmed_api.parse_article` never writes `openalex_author_id`
    onto a byline entry, so on a PubMed-only harvest `roles.evidence_tier`
    returns `name_only` for every record no matter what id was configured. The
    id is evidence for the records OpenAlex itself contributes and for nothing
    else, and without `--openalex-works` it contributes none. It is a third
    party's clustering rather than the user's own assertion either way, so it is
    printed with its source beside it rather than folded into the other three.
    """
    return bool(
        (identity.get("orcid") or "").strip()
        or identity.get("email_domains")
        or identity.get("affiliation_keywords")
        or (merge_works and (identity.get("openalex_author_id") or "").strip())
    )


def apply_cli_overrides(cfg: dict, args: argparse.Namespace) -> dict:
    output_overridden = args.output_dir is not None
    for key in ["author_name", "affiliation", "email", "output_dir", "pdf_dir", "cache_db", "max_workers", "log_level"]:
        value = getattr(args, key, None)
        if value is not None:
            cfg[key] = value
    if output_overridden and args.pdf_dir is None and cfg.get("pdf_dir") == DEFAULT_CONFIG["pdf_dir"]:
        cfg["pdf_dir"] = os.path.join(cfg["output_dir"], "pdfs")
    if output_overridden and args.cache_db is None and cfg.get("cache_db") == DEFAULT_CONFIG["cache_db"]:
        cfg["cache_db"] = os.path.join(cfg["output_dir"], "paper_cache.db")
    if args.years_back is not None:
        cfg["years_back"] = args.years_back
    if args.api_key is not None:
        cfg["api_key"] = args.api_key
    if args.no_download:
        cfg["download_pdfs"] = False

    identity = cfg.setdefault("author_identity", {})
    for flag, key in (("orcid", "orcid"),
                      ("email_domains", "email_domains"),
                      ("affiliation_keywords", "affiliation_keywords"),
                      ("require_affiliation", "require_affiliation"),
                      ("openalex_author_id", "openalex_author_id")):
        value = getattr(args, flag, None)
        if value is not None:
            identity[key] = value

    # The OpenAlex switches live in their own config block rather than inside
    # `author_identity`, because they say what to *fetch* while that block says
    # who the person is. `--openalex-author-id` is the one that belongs in both
    # senses and is filed under identity, above.
    openalex = cfg.setdefault("openalex", {})
    for flag, key in (("resolve_openalex", "resolve_author"),
                      ("openalex_works", "merge_works"),
                      ("max_works", "max_works")):
        value = getattr(args, flag, None)
        if value is not None:
            openalex[key] = value

    # `--affiliation` used to reach only the search query, so a run that named
    # the institution on the command line still verified nobody against it and
    # marked every paper "机构未验证". Seeding the keyword from it is what the
    # flag plainly means; an explicit --affiliation-keyword still wins, and a
    # list configured in the config file is never overwritten.
    if getattr(args, "affiliation", None) and not identity.get("affiliation_keywords"):
        identity["affiliation_keywords"] = [args.affiliation]

    return cfg


# Every name here must resolve to a handler in main(). `compare` once shipped in
# this set with a parser, a dispatch branch and no cmd_compare at all: the suite
# stayed green because the parser was tested directly and nothing walked the
# dispatch, and the first real `run.py compare` died on NameError. Adding a verb
# means adding it in three places — here, in main(), and as a cmd_* function —
# and then running it.
SUBCOMMANDS = {"harvest", "fetch", "profile", "cite", "compare", "diff", "journal-worklist",
               "journal-risk", "download", "clean-cache"}


def _split_subcommand(argv: list[str] | None) -> tuple[str, list[str]]:
    """Pop a leading subcommand if present; default to 'fetch' for backward compat."""
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] in SUBCOMMANDS:
        return argv[0], argv[1:]
    return "fetch", list(argv)


def _force_utf8_stdio() -> None:
    """Make Chinese output survive a stdout that cannot encode it.

    Every message this tool prints is Chinese. On Windows a real console renders
    that through the console API whatever the code page is — but a *redirect*
    (`> log.txt`, a pipe, or capture by a parent process) falls back to the
    locale encoding instead, and cp1252 cannot encode a single Chinese
    character. Before this, `run.py --help` piped anywhere died outright with
    UnicodeEncodeError, and during a real run every Chinese log line was dropped
    and replaced by a logging-error traceback while the run still reported
    success.

    Called from main() rather than at import time, because importing a library
    must not reach out and mutate the process's streams. setup_logging() binds
    sys.stdout into a StreamHandler afterwards, so the console handler inherits
    this; the log *file* was never affected, it already opens as UTF-8.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # already replaced, e.g. by a test's StringIO
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def main(argv: list[str] | None = None):
    _force_utf8_stdio()
    cmd, rest = _split_subcommand(argv)
    if cmd == "profile":
        return cmd_profile(rest)
    if cmd == "cite":
        return cmd_cite(rest)
    if cmd == "compare":
        return cmd_compare(rest)
    if cmd == "diff":
        return cmd_diff(rest)
    if cmd == "journal-worklist":
        return cmd_journal_worklist(rest)
    if cmd == "journal-risk":
        return cmd_journal_risk(rest)
    if cmd == "download":
        return cmd_download(rest)
    if cmd == "clean-cache":
        return cmd_clean_cache(rest)
    return cmd_fetch(rest)


def _sibling_of_output_dir(cfg: dict, key: str, explicit: Any, output_dir: str, leaf: str) -> str:
    """Where `key` lives once `--output-dir` has been given on the command line.

    `apply_cli_overrides` — the harvest path — already does this: an overridden
    `--output-dir` pulls `pdf_dir` and `cache_db` along with it *unless* they were
    set on purpose. `cmd_download` and `cmd_clean_cache` wrote their own override
    loops and skipped the step, so `harvest --output-dir X` put the cache in X
    while `download --output-dir X` kept reading `pubmed_results/paper_cache.db`.
    One flag, one tool, two destinations — and `download` is the verb whose whole
    job is to resume the corpus harvested into X, so it consulted a cache that
    never saw those papers and re-fetched what was already on disk.

    An explicit flag still wins, and so does a path configured in the config file;
    only an untouched default follows the output directory.
    """
    if explicit is not None:
        return str(explicit)
    configured = cfg.get(key)
    if configured and configured != DEFAULT_CONFIG[key]:
        return str(configured)
    return os.path.join(output_dir, leaf)


def cmd_clean_cache(argv: list[str]):
    args = parse_clean_cache_args(argv)
    cfg = load_config(args.config)
    output_dir = args.output_dir or cfg.get("output_dir", "pubmed_results")
    cache_db = _sibling_of_output_dir(cfg, "cache_db", args.cache_db, output_dir, "paper_cache.db")

    setup_logging(output_dir, args.log_level)
    logger = logging.getLogger("check_your_advisor.clean_cache")

    if not os.path.exists(cache_db):
        logger.error("缓存文件不存在: %s", cache_db)
        return 1

    from check_your_advisor.cache import PaperCache
    cache = PaperCache(cache_db)
    try:
        before = cache.stats()
        logger.info("清理前: total=%d | downloaded=%d | failed=%d",
                    before["total"], before["downloaded"], before["failed"])
        deleted = cache.cleanup_expired(args.max_age_days)
        after = cache.stats()
        logger.info("清理后: total=%d | downloaded=%d | failed=%d",
                    after["total"], after["downloaded"], after["failed"])
        logger.info("已删除 %d 条 (>%d 天的失败记录) — %s",
                    deleted, args.max_age_days, cache_db)
    finally:
        cache.close()
    return 0


def cmd_download(argv: list[str]):
    args = parse_download_args(argv)
    cfg = load_config(args.config)

    # 命令行覆盖
    for key in ("output_dir", "pdf_dir", "cache_db", "email"):
        value = getattr(args, key, None)
        if value is not None:
            cfg[key] = value
    if args.max_workers is not None:
        cfg["max_workers"] = args.max_workers

    output_dir = cfg["output_dir"]
    # Both follow an overridden --output-dir, matching the harvest path; see
    # _sibling_of_output_dir for what went wrong when they did not.
    pdf_dir = _sibling_of_output_dir(cfg, "pdf_dir", args.pdf_dir, output_dir, "pdfs")
    cache_db = _sibling_of_output_dir(cfg, "cache_db", args.cache_db, output_dir, "paper_cache.db")

    log_file = setup_logging(output_dir, args.log_level)
    logger = logging.getLogger("check_your_advisor.download")

    # 定位输入 JSON
    json_path = args.input
    if not json_path:
        from check_your_advisor.corpus import find_latest_json
        json_path = find_latest_json(output_dir)
    if not json_path or not os.path.exists(json_path):
        logger.error("没找到 papers_*.json — 先跑 harvest，或用 --input 指定路径")
        return 1

    from check_your_advisor.export import load_papers_json
    papers, _ = load_papers_json(json_path)
    if not papers:
        logger.error("输入 JSON 不是论文列表或为空: %s", json_path)
        return 1

    logger.info("=" * 60)
    logger.info("仅下载阶段：跳过 PubMed efetch")
    logger.info("输入: %s (%d 篇)", json_path, len(papers))
    logger.info("PDF 目录: %s | 缓存: %s | 并发: %d 线程",
                pdf_dir, cache_db, cfg.get("max_workers", 4))
    logger.info("日志: %s", log_file)
    logger.info("=" * 60)

    from check_your_advisor.engine import DownloadEngine
    engine = DownloadEngine(
        email=cfg.get("email", ""),
        max_workers=cfg.get("max_workers", 4),
        proxy_list=cfg.get("proxy_list"),
        cache_db=cache_db,
    )
    try:
        engine.download_batch(papers, pdf_dir)
        logger.info("缓存统计: %s", engine.cache.stats())
    finally:
        engine.close()

    # 写新版 Excel / JSON / 校验报告（不覆盖输入）
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    from check_your_advisor.export import save_to_csv, save_to_excel, save_to_json
    from check_your_advisor.reports import write_pdf_validation_report

    try:
        import openpyxl  # noqa: F401
        excel_path = os.path.join(output_dir, f"papers_{timestamp}.xlsx")
        save_to_excel(papers, excel_path)
        logger.info("Excel 清单: %s", excel_path)
    except ImportError:
        csv_path = os.path.join(output_dir, f"papers_{timestamp}.csv")
        save_to_csv(papers, csv_path)
        logger.warning(
            "openpyxl 不可用，已降级导出 CSV 而非 Excel: %s（执行 `pip install openpyxl` 可恢复）",
            csv_path,
        )

    out_json = os.path.join(output_dir, f"papers_{timestamp}.json")
    save_to_json(papers, out_json)
    logger.debug("Papers JSON: %s", out_json)

    report_path = os.path.join(output_dir, f"pdf_validation_report_{timestamp}.csv")
    write_pdf_validation_report(papers, report_path, pdf_dir=pdf_dir)
    logger.info("PDF 校验报告: %s", report_path)
    return 0


def cmd_cite(argv: list[str]):
    """Fetch a citation count per corpus paper into `citations_<timestamp>.json`.

    Structure follows cmd_download: locate the newest `papers_*.json`, refuse if
    there is none, do the network work, write a new file beside the input. It
    writes nothing back into the corpus — see `citations.py` for why a dated
    measurement must not be merged into an undated one.
    """
    args = parse_cite_args(argv)
    cfg = load_config(args.config)

    for key in ("output_dir", "email"):
        value = getattr(args, key, None)
        if value is not None:
            cfg[key] = value
    if args.max_workers is not None:
        cfg["max_workers"] = args.max_workers

    output_dir = cfg["output_dir"]
    log_file = setup_logging(output_dir, args.log_level)
    logger = logging.getLogger("check_your_advisor.cite")

    if args.api_key:
        logger.warning(
            "--api-key 已收下但不会被使用：OpenAlex / Semantic Scholar / Europe PMC "
            "三个来源都不需要密钥，这个值不会出现在任何一次请求里。"
        )

    from check_your_advisor.corpus import find_latest_json

    json_path = args.papers_json or find_latest_json(output_dir)
    if not json_path or not os.path.exists(json_path):
        logger.error("没找到 papers_*.json — 先跑 harvest，或用 --papers-json 指定路径")
        return 1

    from check_your_advisor.export import load_papers_json
    papers, _ = load_papers_json(json_path)
    if not papers:
        logger.error("输入 JSON 不是论文列表或为空: %s", json_path)
        return 1

    logger.info("=" * 60)
    logger.info("引用数抓取：三级免费来源，不写回语料")
    logger.info("输入: %s (%d 篇) | 输出目录: %s", json_path, len(papers), output_dir)
    logger.info("并发: %d 线程 | 日志: %s", cfg.get("max_workers", 4), log_file)
    logger.info("=" * 60)
    if not cfg.get("email"):
        logger.info("未配置 email，OpenAlex 将按匿名池请求（可用，只是限速更紧）。")

    from check_your_advisor.cache import FetchCache
    from check_your_advisor.citations import (
        fetch_citations,
        find_latest_citations_json,
        reusable_records,
        save_citations_json,
    )

    reuse = reusable_records(output_dir, args.max_age_days)
    if reuse["records"]:
        logger.info("沿用来源: %s（%d 篇在 %d 天内抓过）",
                    reuse["path"], len(reuse["records"]), args.max_age_days)
    elif args.max_age_days <= 0 and find_latest_citations_json(output_dir):
        # Only said when there is actually something to reuse. Advertising a
        # flag that would save nothing is noise.
        logger.info("这个目录已经有一份 citations_*.json。加 --max-age-days N 可只补抓 N 天前的，"
                    "本次按默认全部重抓。")

    # The cache is written on every successful fetch and read only when
    # `--max-age-days N` asks for it, so a default run still asks every source
    # and still leaves the cache warmer than it found it. That asymmetry is the
    # point: the saving arrives on the *next* run, without this one quietly
    # serving a number it did not take today.
    cache = FetchCache(
        _sibling_of_output_dir(cfg, "cache_db", None, output_dir, "paper_cache.db"))
    try:
        payload = fetch_citations(
            papers,
            source_papers_json=json_path,
            mailto=cfg.get("email", ""),
            max_workers=cfg.get("max_workers", 4),
            reuse=reuse["records"],
            cache=cache,
            max_age_days=args.max_age_days,
        )
    finally:
        logger.info("抓取缓存: %s", cache.stats())
        cache.close()
    # The file is stamped with the moment the counts were taken, not with the
    # harvest's stamp. Reusing the corpus timestamp would put a date from months
    # ago on a measurement taken today, and would overwrite the previous counts
    # for that corpus — destroying the earlier reading of a quantity whose whole
    # point is that it moves. The pairing is not lost: `source_papers_json`
    # inside the payload names the corpus this was fetched for.
    citations_path = save_citations_json(payload, output_dir)

    denominator = payload["denominator"]
    logger.info("=" * 60)
    logger.info("引用数文件: %s", citations_path)
    logger.info("覆盖: %d/%d 篇拿到引用数（其余三级源均未命中，这是查找的结果，不是论文的属性）",
                denominator["papers_with_citations"], denominator["papers_total"])

    if args.percentile:
        _locate_in_reference(papers, payload, json_path, output_dir, cfg)

    logger.info("下一步: check-your-advisor profile --output-dir %s —— 画像会自动接上这份引用数", output_dir)
    return 0


def _locate_in_reference(papers, payload, json_path: str, output_dir: str, cfg: dict) -> None:
    """`cite --percentile`: place each count inside its own topic-year cell.

    The join happens here rather than inside `impact_reference`, which reads two
    field names off a paper and does not know where counts come from. Counts are
    copied onto throwaway dicts, never onto `papers` itself: the corpus file is
    what `harvest` wrote, and a count taken today does not belong in it.
    """
    logger = logging.getLogger("check_your_advisor.cite")

    from check_your_advisor.citations import index_by_pmid
    from check_your_advisor.impact_reference import (
        CITATION_COUNT_FIELD,
        CITATION_COUNT_SOURCE_FIELD,
        locate_corpus,
        save_impact_reference_json,
    )

    by_pmid = index_by_pmid(payload)
    joined = []
    for paper in papers:
        record = by_pmid.get(str(paper.get("pmid", "")))
        joined.append({
            **paper,
            CITATION_COUNT_FIELD: (record or {}).get("citation_count"),
            CITATION_COUNT_SOURCE_FIELD: (record or {}).get("source", ""),
        })

    located = locate_corpus(joined, source_papers_json=json_path,
                            mailto=cfg.get("email", ""))
    save_impact_reference_json(located, output_dir)

    counts = located["denominator"]
    logger.info("-" * 60)
    logger.info("引用百分位: %d/%d 篇定位到同领域同年的参照分布",
                counts["papers_located"], counts["papers_total"])
    # Printed per status rather than as one "the rest failed" number, because the
    # seven reasons are not interchangeable and the whole point of the module is
    # that none of them means "this paper scored low".
    for status, n in (counts.get("by_status") or {}).items():
        if n and status != "located":
            logger.info("  %d 篇未定位 — %s", n,
                        (located.get("status_reasons") or {}).get(status, status))


def cmd_journal_worklist(argv: list[str]):
    """Write the journals this corpus uses into a fill-in-by-hand CSV.

    Structure follows cmd_cite: locate the newest `papers_*.json`, refuse if
    there is none, write a new file beside it. Unlike cmd_cite it makes no
    request at all — see `journals.py` for why there is no crawler here and why
    the edition column is required on the way back in.
    """
    args = parse_journal_worklist_args(argv)
    cfg = load_config(args.config)
    if args.output_dir is not None:
        cfg["output_dir"] = args.output_dir

    output_dir = cfg["output_dir"]
    setup_logging(output_dir, args.log_level)
    logger = logging.getLogger("check_your_advisor.journal_worklist")

    from check_your_advisor.corpus import find_latest_json

    json_path = args.papers_json or find_latest_json(output_dir)
    if not json_path or not os.path.exists(json_path):
        logger.error("没找到 papers_*.json — 先跑 harvest，或用 --papers-json 指定路径")
        return 1

    from check_your_advisor.export import load_papers_json
    papers, _ = load_papers_json(json_path)
    if not papers:
        logger.error("输入 JSON 不是论文列表或为空: %s", json_path)
        return 1

    from check_your_advisor.journals import (
        WORKLIST_GUIDANCE,
        journal_worklist,
        write_worklist_csv,
    )

    worklist = journal_worklist(papers)
    # Read from a file a different verb already wrote. This command still makes
    # no request of its own — see journals.py — and the flag being absent leaves
    # the template byte-identical to what it was before the column existed.
    risk_cells: dict = {}
    if args.risk_json is not None:
        from check_your_advisor.journal_risk import (
            find_latest_risk_json,
            load_risk_json,
            worklist_cells,
        )

        risk_path = args.risk_json or find_latest_risk_json(output_dir)
        if not risk_path:
            logger.warning("--risk-json 没给路径，%s 下也没有 journal_risk_*.json，"
                           "「风险信号」列留空。先跑 check-your-advisor journal-risk --output-dir %s",
                           output_dir, output_dir)
        else:
            try:
                risk_cells = worklist_cells(load_risk_json(risk_path))
            except (OSError, ValueError) as exc:
                logger.error("风险信号文件无法读取，「风险信号」列留空: %s (%s)", risk_path, exc)
            else:
                logger.info("风险信号: %s（%d 本刊有信号，将预填进清单）", risk_path, len(risk_cells))

    out_path = args.out or os.path.join(
        output_dir, f"journal_worklist_{datetime.now():%Y%m%d_%H%M%S}.csv"
    )
    written = write_worklist_csv(worklist, out_path, risk_cells)

    logger.info("=" * 60)
    logger.info("期刊待查清单：只列本语料实际用到的刊，不建全库，也不联网")
    logger.info("输入: %s (%d 篇)", json_path, worklist["denominator"])
    logger.info("待查 %d 本刊，覆盖 %d/%d 篇；%d 篇没有刊名，无法列入",
                worklist["journal_count"], worklist["papers_with_journal"],
                worklist["denominator"], worklist["papers_without_journal"])
    if worklist["alias_groups"]:
        logger.info("其中 %d 组疑似同一本刊的不同写法（全称 vs 缩写），已标在「疑似同刊组」列，"
                    "查一次填两行即可：", len(worklist["alias_groups"]))
        for group in worklist["alias_groups"]:
            logger.info("  组 %d: %s（共 %d 篇）",
                        group["alias_group"], " / ".join(group["names"]), group["paper_count"])
    if worklist["conflicting_issns"]:
        logger.warning("有 %d 处同名期刊在语料里带了不同 ISSN，清单只保留先出现的那个，填表时请核对",
                       len(worklist["conflicting_issns"]))
    logger.info("清单: %s", written)
    logger.info("-" * 60)
    for line in WORKLIST_GUIDANCE:
        logger.info("· %s", line)
    logger.info("-" * 60)
    logger.info("填完后: check-your-advisor profile --output-dir %s --journal-table %s",
                output_dir, written)
    logger.info("想同时看公开风险信号（DOAJ 收录 / Crossref 元数据完整度）: "
                "check-your-advisor journal-risk --output-dir %s", output_dir)
    return 0


def cmd_journal_risk(argv: list[str]):
    """Collect public risk signals for this corpus's journals into their own JSON.

    Structure follows cmd_cite exactly, and for the same reasons: locate the
    newest `papers_*.json`, refuse if there is none, do the network work, write a
    new dated file beside the input and never into it. What it emits is a list of
    statements with their endpoints and dates — no grade and no verdict; see
    `journal_risk.py` for why that boundary is the whole module.
    """
    args = parse_journal_risk_args(argv)
    cfg = load_config(args.config)

    for key in ("output_dir", "email"):
        value = getattr(args, key, None)
        if value is not None:
            cfg[key] = value
    if args.max_workers is not None:
        cfg["max_workers"] = args.max_workers

    output_dir = cfg["output_dir"]
    log_file = setup_logging(output_dir, args.log_level)
    logger = logging.getLogger("check_your_advisor.journal_risk")

    from check_your_advisor.corpus import find_latest_json

    json_path = args.papers_json or find_latest_json(output_dir)
    if not json_path or not os.path.exists(json_path):
        logger.error("没找到 papers_*.json — 先跑 harvest，或用 --papers-json 指定路径")
        return 1

    from check_your_advisor.export import load_papers_json
    papers, _ = load_papers_json(json_path)
    if not papers:
        logger.error("输入 JSON 不是论文列表或为空: %s", json_path)
        return 1

    from check_your_advisor.cache import FetchCache
    from check_your_advisor.journal_risk import (
        JOURNAL_RISK_CAVEATS,
        SOURCE_ORDER,
        fetch_journal_risk,
        find_latest_risk_json,
        reusable_records,
        risk_targets,
        save_risk_json,
    )
    from check_your_advisor.journals import journal_worklist

    scope = risk_targets(journal_worklist(papers))

    logger.info("=" * 60)
    logger.info("期刊风险信号：只报事实，不下结论；不产出等级、评分或「掠夺性」判定")
    logger.info("输入: %s (%d 篇) | 输出目录: %s", json_path, len(papers), output_dir)
    logger.info("本语料 %d 本刊，其中 %d 本带可用 ISSN 可查；来源 %s（三个都查）",
                scope["journal_denominator"], len(scope["targets"]), " + ".join(SOURCE_ORDER))
    logger.info("并发: %d 线程 | 日志: %s", cfg.get("max_workers", 4), log_file)
    logger.info("=" * 60)
    if scope["journals_without_issn"]:
        # Named, not just counted. All three endpoints key on ISSN alone, so a
        # journal the corpus recorded without one cannot be queried at all — and
        # "not looked up" must never be mistaken for "looked up, nothing found".
        logger.warning("%d 本刊语料里没有 ISSN，三个接口都只认 ISSN，因此查不了（不是查了没结果）: %s",
                       len(scope["journals_without_issn"]),
                       "、".join(scope["journals_without_issn"][:8])
                       + ("…" if len(scope["journals_without_issn"]) > 8 else ""))
    if not cfg.get("email"):
        logger.info("未配置 email，Crossref/OpenAlex 将按匿名池请求（可用，只是限速更紧）。")

    reuse = reusable_records(output_dir, args.max_age_days)
    if reuse["records"]:
        logger.info("沿用来源: %s（%d 本刊在 %d 天内采过）",
                    reuse["path"], len(reuse["records"]), args.max_age_days)
    elif args.max_age_days <= 0 and find_latest_risk_json(output_dir):
        logger.info("这个目录已经有一份 journal_risk_*.json。加 --max-age-days N 可只补采 N 天前的，"
                    "本次按默认全部重采。")

    # Same asymmetry as `cmd_cite`: always written, read only on request.
    cache = FetchCache(
        _sibling_of_output_dir(cfg, "cache_db", None, output_dir, "paper_cache.db"))
    try:
        payload = fetch_journal_risk(
            scope["targets"],
            source_papers_json=json_path,
            mailto=cfg.get("email", ""),
            max_workers=cfg.get("max_workers", 4),
            reuse=reuse["records"],
            journals_without_issn=scope["journals_without_issn"],
            cache=cache,
            max_age_days=args.max_age_days,
        )
    finally:
        logger.info("采集缓存: %s", cache.stats())
        cache.close()
    risk_path = save_risk_json(payload, output_dir)

    denominator = payload["denominator"]
    logger.info("=" * 60)
    logger.info("风险信号文件: %s", risk_path)
    logger.info("覆盖: %d/%d 本刊至少有一个来源应答（其余是没查到，不是期刊的属性）",
                denominator["journals_with_signals"], denominator["journals_total"])
    logger.info("-" * 60)
    for key, text in JOURNAL_RISK_CAVEATS.items():
        logger.info("· %s — %s", key, text)
    logger.info("-" * 60)
    logger.info("下一步: check-your-advisor profile --output-dir %s —— 第 18 节会自动接上这份信号",
                output_dir)
    return 0


def _corpus_counts(
    search: dict, papers: list[dict] | None = None, identity: dict | None = None
) -> dict:
    """
    Counts for the provenance block, derived only where both operands exist.

    `rejected` is fetched minus verified. Defaulting either side to zero would
    turn "not recorded" into a confident number, which is the failure this whole
    provenance change exists to remove.

    Both operands are statements about the PubMed half of the harvest — records
    efetch parsed, and records the first/corresponding-author identity filter
    kept out of them — so `verified` can never exceed `fetched` and `rejected`
    can never be negative. The key keeps the name it was written under; Section 1
    prints it as `kept`, because with `require_affiliation=false` the filter also
    keeps a bare name match and "verified" claimed more than the filter checked.
    It was negative, once:
    the merge overwrote `verified` with the size of the merged corpus, and
    Section 1 printed `fetched 10 / verified 30 / rejected -20`. When the two
    operands contradict each other this records the contradiction under
    `inconsistent` and emits no `rejected` at all; gate G6 in `profile.report`
    turns that into a refusal page. It used to raise ValueError from here, which
    was the right verdict delivered as an uncaught traceback: `main()` has no
    handler, so an old-format corpus killed the process instead of printing the
    reason and the fix.

    `by_evidence` and `name_only` are the two halves of one histogram over the
    harvested records, and they follow the same rule. `by_evidence` says which of
    ORCID, OpenAlex id, email domain or affiliation keyword actually carried each
    record; `name_only` is that histogram's zero bucket — records whose role
    string says outright that the name matched and nothing else did. The two add
    up to the harvested total, which is what lets a reader check them against
    each other.

    A record whose role predates the markers is in neither: `evidence_tier_from_role`
    returns "" for it and `is_name_only_role` is False, and "no evidence" and
    "evidence never written down" are different facts. One such record and
    `name_only` is withheld entirely rather than printed as a floor — Section 1
    prints "not recorded" instead, because a floor read as a value is exactly the
    confusion this block exists to prevent.

    `identity` is the resolved identity block `_profile_corpus` hands the rest of
    the report, and it is here for one fact the role string cannot hold. When
    both databases hold a paper the merge keeps PubMed's byline and stores the
    confirming OpenAlex work's author ids on the record, under
    `OPENALEX_IDS_FIELD` — so a record the configured id demonstrably reached
    still carries a role string that says name-only. Three readers answer "did
    this id reach this record", and this was the one left reading the role alone:
    Section 1 printed `identity evidence on records: 0 of 10 … name_only 10`
    directly above `openalex author id on records: 10 of 10`, two adjacent lines
    denying each other over one corpus. Read here through
    `record_openalex_author_ids`, the same function `report.openalex_id_record_share`
    counts with and `roles.resolve_pi` promotes on.

    Applied as a raise and only over a weaker tier, which is `resolve_pi`'s rule
    and `EVIDENCE_RANK` is imported rather than retyped so the two orders cannot
    drift: an ORCID match already outranks a database's clustering and is not
    relabelled by it, while an affiliation or email match is. The id is never
    written back onto the role string. A role names one byline slot, and which
    surviving slot each donated id belongs to is exactly the guess
    `openalex._fold` refuses to make; the same string also carries the literal
    `待确认` stamp that warning G2 and `_fallback_flags` count by equality.
    """
    counts = {k: search[k] for k in ("fetched", "verified") if k in search}
    if "fetched" in counts and "verified" in counts:
        rejected = counts["fetched"] - counts["verified"]
        if rejected < 0:
            counts["inconsistent"] = {
                "fetched": counts["fetched"],
                "verified": counts["verified"],
                "rejected_would_be": rejected,
            }
        else:
            counts["rejected"] = rejected
    # The merged corpus size, when a second source was folded in. Its own key,
    # never `verified`: it answers "how big is the corpus" and `verified`
    # answers "how many PubMed records survived the identity filter".
    if "corpus_total" in search:
        counts["corpus_total"] = search["corpus_total"]

    # Kept as a local import only to avoid a cycle back through cli. The reason
    # it was written this way — pubmed_api pulling in requests, which importing
    # this module must not require — no longer holds: pubmed_api is stdlib-only.
    # `profile.roles` is stdlib-only too and imports nothing from here.
    from check_your_advisor.profile.roles import EVIDENCE_RANK
    from check_your_advisor.pubmed_api import (
        evidence_tier_from_role,
        is_name_only_role,
        normalise_openalex_id,
        record_openalex_author_ids,
    )

    wanted_openalex = normalise_openalex_id((identity or {}).get("openalex_author_id"))
    tiers: dict[str, int] = {}
    sources: dict[str, int] = {}
    name_only = 0
    unrecorded = 0
    for paper in papers or []:
        role = paper.get("role", "")
        tier = evidence_tier_from_role(role)
        # "" means the role was written before the markers existed, and it loses
        # to every tier for the same reason `name_only` does — hence the default.
        if wanted_openalex \
                and EVIDENCE_RANK.get(tier or "name_only", 0) < EVIDENCE_RANK["openalex"] \
                and wanted_openalex in record_openalex_author_ids(paper):
            tier = "openalex"
        if tier:
            tiers[tier] = tiers.get(tier, 0) + 1
        elif is_name_only_role(role):
            name_only += 1
        else:
            unrecorded += 1
        # Which bibliographic source(s) hold this record. Absent from every
        # corpus harvested from PubMed alone, and left absent rather than
        # defaulted to `{"pubmed": n}`: "one source, because that is all there
        # was" and "one source, because the merge was never run" are different
        # facts, and Section 1 prints a different sentence for each.
        confirmed = paper.get("confirmed_by")
        if isinstance(confirmed, list) and confirmed:
            key = "both" if len(confirmed) > 1 else str(confirmed[0])
            sources[key] = sources.get(key, 0) + 1
    if tiers:
        counts["by_evidence"] = tiers
    if papers and not unrecorded:
        counts["name_only"] = name_only
    if sources:
        counts["by_source"] = sources
    return counts


def _fallback_flags(search: dict, papers: list[dict]) -> dict[str, bool]:
    """Whether the harvest kept everything because nothing passed verification.

    `cmd_fetch` writes this as an explicit boolean before it merges a second
    source in, so the reader takes the fact from the record rather than
    reconstructing it. The reconstruction — "every paper is stamped 待确认" —
    survives only for files harvested before the key existed, and it is a
    *lower* bound rather than an equivalent: it is falsified by any record that
    carries a real role, which after a merge means one OpenAlex work is enough
    to hide the condition entirely.

    `fallback_fired_inferred` travels with the answer so Section 1 can say which
    of the two it printed. A guess presented as a record is the failure this
    whole provenance block exists to prevent.
    """
    recorded = search.get("fallback_fired")
    if isinstance(recorded, bool):
        return {"fallback_fired": recorded, "fallback_fired_inferred": False}
    return {
        "fallback_fired": bool(papers) and all(p.get("role") == "待确认" for p in papers),
        "fallback_fired_inferred": True,
    }


def _openalex_id(value: Any) -> str:
    """Normalise an OpenAlex author id without importing the network module.

    `pubmed_api` is stdlib-only and is already imported by the profile layer for
    exactly this class of pure helper; `openalex.py` imports the same function
    from there. Reaching for `openalex.normalise_openalex_id` here instead would
    pull `http_client` into every profile run for one regex.
    """
    from check_your_advisor.pubmed_api import normalise_openalex_id

    return normalise_openalex_id(value)


def _profile_corpus(papers: list[dict], cfg: dict, search: dict | None = None) -> dict:
    """
    Wrap a papers_*.json list in the Section 4 corpus contract.

    `search` is what `fetch` recorded about the query it actually ran. When it is
    absent the file predates provenance, and nothing is invented to fill the gap:
    unknown fields stay unset and render as "?" rather than as a confident value,
    and the truncation gate reports `unknown` instead of a fabricated `False`.
    """
    search = search or {}
    # What the harvest actually used, when the file records it. Falling back to
    # the config loaded right now answers a different question — "what would a
    # harvest do today" rather than "what produced this corpus" — and it is
    # wrong in both directions: a corpus disambiguated by ORCID gets refused
    # because the flag was not repeated on the profile command, and a name-only
    # corpus gets certified because a config file happens to carry one.
    recorded = search.get("identity")
    if recorded is not None:
        identity = recorded
    else:
        identity = cfg.get("author_identity") or {}
        if identity:
            logging.getLogger("check_your_advisor.profile").warning(
                "这份 papers_*.json 没有记录检索时用的身份证据（旧格式）。身份提示将按"
                "**当前配置**判定，而不是按产出这份语料的那次检索。若两者不一致，"
                "结论会失真——重跑一次 harvest 即可消除这个不确定性。"
            )
    query: dict[str, Any] = {"years_back": search.get("years_back", cfg.get("years_back", "?"))}
    if search:
        query.update({
            # Key names follow the Section 4 contract that report.py renders from
            # (`term`, `esearch_count`), not the names search_pubmed happens to use.
            "term": search.get("esearch_term", ""),
            "esearch_count": search.get("esearch_matched", "?"),
            "pmids_returned": search.get("pmids_returned", "?"),
            "retmax": search.get("retmax", "?"),
            # Paging: how many esearch pages were needed, what the total budget
            # was, and how many repeated PMIDs the pages had between them. All
            # three are what makes "retrieved N of M" readable rather than an
            # unexplained shortfall; absent from a corpus harvested before
            # paging existed, and printed as "?" there rather than as a zero.
            "max_records": search.get("max_records", "?"),
            "pages_fetched": search.get("pages_fetched", "?"),
            "duplicates_dropped": search.get("duplicates_dropped", "?"),
            "mindate": search.get("mindate", "?"),
            "maxdate": search.get("maxdate", "?"),
            "narrowed_by_affiliation": search.get("narrowed_by_affiliation", False),
            "truncated": search.get("truncated", "unknown"),
        })
        # The second source, when there was one. Copied verbatim rather than
        # flattened, because the whole point of the OpenAlex block is that it
        # says who asserted what and when — a resolution reduced to an id has
        # lost the part that makes it checkable.
        for key in ("openalex", "openalex_works"):
            block = search.get(key)
            if isinstance(block, dict) and block:
                query[key] = block
    else:
        # Coverage is unknowable from a legacy file: it records neither the
        # esearch hit count nor how many PMIDs came back, so Section 1 prints
        # "?" on both sides of the "retrieved N of M" line.
        query["truncated"] = "unknown"

    return {
        "schema_version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        # cmd_fetch keeps only first/last/corresponding-author papers, so on this
        # corpus the PI's byline position is decided by that filter rather than by
        # the data. Section 7.7 suppresses the metric on the strength of this flag.
        "position_filtered": True,
        "query": query,
        "identity": {
            # The config value wins because it carries `--pi-name`, which is the
            # user saying it outright. The recorded name fills the gap when there
            # is none — which is what makes `compare` able to read several
            # corpora, each about a different person, without being handed a name
            # per directory.
            "author_name": cfg.get("author_name", "") or (identity.get("author_name") or ""),
            "orcid": (identity.get("orcid") or "").strip(),
            "affiliation_keywords": list(identity.get("affiliation_keywords") or []),
            "email_domains": list(identity.get("email_domains") or []),
            # Kept in the identity block because `roles.evidence_tier` reads it
            # from there, and normalised on the way in so the `A…` / URL / lower
            # case spellings of one id cannot fail to match the byline's copy.
            "openalex_author_id": _openalex_id(identity.get("openalex_author_id")),
            # Spec P5: is_first_or_corresponding defaults this to True when the key
            # is absent from the identity dict it is handed, while DEFAULT_CONFIG
            # sets False. The default here must match the function, not the config.
            "require_affiliation_effective": bool(identity.get("require_affiliation", True)),
        },
        # The same resolved identity the block above prints and `build_report`
        # hands to `openalex_id_record_share` and `resolve_pi`, not a second
        # lookup: reading `search["identity"]` again here would leave the counts
        # describing a legacy corpus by the recorded identity while every other
        # number on the page described it by the configured one.
        "counts": _corpus_counts(search, papers, identity),
        **_fallback_flags(search, papers),
        "papers": papers,
    }


def _figure_placeholders(missing: str) -> dict[str, dict]:
    """One stated placeholder per figure slot, naming the package that is absent.

    The ids come from the HTML builder's own placement table rather than from a
    list retyped here, so a figure can never lose its slot to a spelling drift.
    Shape matches `charts._prose`: the message travels in `caption` with an empty
    `svg` and `drawn` false, which is the same degenerate path the chart module
    uses for a figure it declines to draw. An empty axis is never emitted.
    """
    from check_your_advisor.profile.html_report import FIGURE_PLACEMENT

    note = f"chart unavailable — {missing} not installed"
    return {
        figure_id: {"id": figure_id, "svg": "", "caption": note, "desc": note,
                    "rows": [], "drawn": False}
        for figure_id, _section, _caveats in FIGURE_PLACEMENT
    }


def _profile_figures(report: dict, logger: logging.Logger) -> dict[str, dict]:
    """The report's figures, or placeholders if the drawing module will not import.

    `profile.charts` emits SVG from the standard library alone, so on any working
    install this returns real figures and the guard costs nothing. It exists
    because a drawing dependency is not worth the report: without it an
    ImportError here would take the HTML, and the reader would lose every
    section of prose over seven pictures. Seven is `html_report.FIGURE_PLACEMENT`
    and `charts.CHART_IDS`, both of which `tests/test_declared_counts.py` pins
    to the number written here.
    """
    try:
        from check_your_advisor.profile.charts import figures_for_report
    except ImportError as exc:
        missing = exc.name or "a drawing dependency"
        logger.warning(
            "%s 不可用，HTML 报告的图表位改为占位说明，其余章节照常生成"
            "（该图层已无第三方依赖，出现此警告说明是代码缺陷而非缺包）。", missing
        )
        return _figure_placeholders(missing)
    return figures_for_report(report)


def _load_citations(
    output_dir: str,
    papers_json: str,
    logger: logging.Logger,
    explicit: str | None = None,
    disabled: bool = False,
) -> tuple[dict | None, str]:
    """The citation payload for one corpus directory, or None and the reason why.

    Returns `(payload, note)`. The note is written into the report in place of
    Section 15, so it has to say which of the several possible absences happened:
    the flag was given, the directory holds no citation file, the named file is
    missing, or the file would not parse. "No citation data" with no explanation
    is indistinguishable from "this researcher is uncited", which is the one
    reading that must never be available.

    A missing or unreadable file is never a gate. The report's six questions are
    answered from the corpus alone; citation counts add a section and two score
    components and take nothing away when absent. That is why this returns a
    reason instead of raising, while still logging an unreadable file as an
    error — silence and refusal are both wrong here, so it does neither.
    """
    if disabled:
        return None, "--no-citations was given on the command line, so no citation file was read."

    from check_your_advisor.citations import (
        find_latest_citations_json,
        load_citations_json,
    )

    path = explicit
    if path:
        if not os.path.exists(path):
            logger.error("--citations-json 指向的文件不存在: %s", path)
            return None, f"--citations-json named {path}, which does not exist."
    else:
        path = find_latest_citations_json(output_dir)
        if not path:
            logger.info(
                "%s 下没有 citations_*.json，报告将跳过影响力段落（第 15 节会写明原因）。"
                "跑一次 `check-your-advisor cite --output-dir %s` 即可补上。",
                output_dir, output_dir,
            )
            return None, (
                f"no citations_*.json was found in {output_dir}. Run "
                f"`check-your-advisor cite --output-dir {output_dir}` to fetch counts for this "
                "corpus; nothing else in this report depends on it."
            )

    try:
        payload = load_citations_json(path)
    except (OSError, ValueError) as exc:
        logger.error("引用数文件无法读取，报告将跳过影响力段落: %s (%s)", path, exc)
        return None, f"{path} could not be read as a citations file: {exc}"

    source = payload.get("source_papers_json") or ""
    if source and source != os.path.basename(papers_json):
        # Not fatal: the join is by PMID and DOI, and `impact.citation_metrics`
        # reports exactly how much of the corpus it matched. But a reader
        # deserves to know the two files came from different harvests before
        # reading a coverage figure.
        logger.warning(
            "引用数文件是给 %s 抓的，当前语料是 %s。两者仍按 PMID/DOI 关联，"
            "覆盖率会如实反映对不上的部分。",
            source, os.path.basename(papers_json),
        )

    denominator = payload.get("denominator") or {}
    logger.info("引用数: %s (%s/%s 篇有数据，抓取时间 %s)",
                path,
                denominator.get("papers_with_citations", "?"),
                denominator.get("papers_total", "?"),
                payload.get("generated_at") or "未记录")
    return payload, ""


def _resolve_table_path(explicit: str | None, cfg: dict, section: str, key: str) -> str:
    """A hand-filled table's path: the flag first, then the config file, then none."""
    if explicit:
        return explicit
    block = cfg.get(section)
    return str((block or {}).get(key) or "") if isinstance(block, dict) else ""


def _load_journal_table(
    path: str | None, cfg: dict, logger: logging.Logger
) -> tuple[dict | None, str]:
    """The journal metric table, or None and the reason why.

    Returns `(table, note)` on the pattern `_load_citations` set: the note is
    printed in Section 18 in place of the numbers, so it has to say which of the
    several absences happened rather than leaving a reader to read a blank
    partition column as a finding about the journals.

    A table that will not load is an error and not a gate. Sections 1 to 16
    answer the report's own questions without it, and refusing the whole report
    over a malformed CSV would cost fifteen sections to protect one. A bad file
    is logged loudly, named, and the section says the same thing.
    """
    resolved = _resolve_table_path(path, cfg, "journals", "table_path")
    if not resolved:
        return None, (
            "no journal metric table was supplied. Run `check-your-advisor journal-worklist` to "
            "write the journals this corpus actually uses into a CSV, fill in the metric columns "
            "from LetPub or ablesci, and pass it back with --journal-table."
        )
    if not os.path.exists(resolved):
        logger.error("--journal-table 指向的文件不存在: %s", resolved)
        return None, f"--journal-table named {resolved}, which does not exist."

    from check_your_advisor.journals import JournalTableError, load_journal_table

    try:
        table = load_journal_table(resolved)
    except (OSError, JournalTableError, ValueError) as exc:
        logger.error("期刊指标表无法读取，第 18 节将写明原因: %s (%s)", resolved, exc)
        return None, f"{resolved} could not be read as a journal table: {exc}"

    logger.info("期刊指标表: %s（%d 行 / %d 本刊，编码 %s）",
                resolved, table["row_count"], table["journal_count"], table["encoding"])
    editions = table["editions"] or {}
    logger.info("版本来源分布: %s；数据获取日期: %s",
                "、".join(f"{name} {count}" for name, count in editions.items()) or "无",
                " 到 ".join(table["retrieved_on_range"] or []) or "未记录")
    if table["rows_without_edition"]:
        logger.warning(
            "有 %d 行没标版本来源。这些分区数字过后无法判断是官方版、新锐版还是民间版——"
            "LetPub 检索列表页默认显示的就是民间版，这一栏空着等于把它当官方版用。",
            table["rows_without_edition"],
        )
    return table, ""


def _load_impact_reference(
    output_dir: str,
    logger: logging.Logger,
) -> tuple[dict | None, str]:
    """The reference cells `cite --percentile` collected, or None and the reason.

    Same `(payload, note)` contract as `_load_journal_risk`, and never a gate:
    an absent file costs one block inside Section 15 and nothing else. The note
    matters more here than elsewhere, because Section 15 prints it verbatim — a
    reader who sees "position in an external reference population" with no
    numbers has to be told that none were fetched, not left to conclude that
    none were found.
    """
    from check_your_advisor.impact_reference import (
        find_latest_impact_reference_json,
        load_impact_reference_json,
    )

    path = find_latest_impact_reference_json(output_dir)
    if not path:
        return None, (
            f"no impact_reference_*.json was found in {output_dir}. Run "
            f"`check-your-advisor cite --percentile --output-dir {output_dir}` to ask OpenAlex "
            "where each citation count falls among the works sharing its topic and year; "
            "nothing else in this report depends on it."
        )

    try:
        payload = load_impact_reference_json(path)
    except (OSError, ValueError) as exc:
        logger.error("引用百分位文件无法读取，第 15 节将写明原因: %s (%s)", path, exc)
        return None, f"{path} could not be read as an impact reference file: {exc}"

    denominator = payload.get("denominator") or {}
    logger.info("引用百分位: %s（%s/%s 篇定位到参照分布，采集时间 %s）", path,
                denominator.get("papers_located", "?"),
                denominator.get("papers_total", "?"),
                payload.get("generated_at", "未记录"))
    return payload, ""


def _load_journal_risk(
    output_dir: str,
    logger: logging.Logger,
    explicit: str | None = None,
    disabled: bool = False,
) -> tuple[dict | None, str]:
    """The collected journal risk signals, or None and the reason why.

    Same `(payload, note)` contract as `_load_citations`, and it is the citation
    loader rather than the table loaders this one copies: both read a dated file
    that a separate verb wrote, so both have the extra branch the CSV loaders do
    not need — the file may simply not have been collected yet.

    Never a gate. A missing risk file costs one block inside Section 18 and
    nothing else, and Section 18 itself still renders from the hand-filled table.
    """
    if disabled:
        return None, ("--no-journal-risk was given on the command line, so no risk signal file "
                      "was read.")

    from check_your_advisor.journal_risk import find_latest_risk_json, load_risk_json

    path = explicit
    if path:
        if not os.path.exists(path):
            logger.error("--journal-risk-json 指向的文件不存在: %s", path)
            return None, f"--journal-risk-json named {path}, which does not exist."
    else:
        path = find_latest_risk_json(output_dir)
        if not path:
            return None, (
                f"no journal_risk_*.json was found in {output_dir}. Run "
                f"`check-your-advisor journal-risk --output-dir {output_dir}` to collect DOAJ "
                "indexing status and Crossref metadata coverage for the journals this corpus "
                "uses; nothing else in this report depends on it."
            )

    try:
        payload = load_risk_json(path)
    except (OSError, ValueError) as exc:
        logger.error("期刊风险信号文件无法读取，第 18 节的信号块将写明原因: %s (%s)", path, exc)
        return None, f"{path} could not be read as a journal risk file: {exc}"

    denominator = payload.get("denominator") or {}
    logger.info("期刊风险信号: %s（%s/%s 本刊有信号，采集时间 %s）", path,
                denominator.get("journals_with_signals", "?"),
                denominator.get("journals_total", "?"),
                payload.get("generated_at") or "未记录")
    return payload, ""


def _load_thesis_roster(
    path: str | None, cfg: dict, logger: logging.Logger
) -> tuple[dict | None, str]:
    """The degree-thesis roster, or None and the reason why.

    Same contract as `_load_journal_table`, and the same reason it never gates:
    a missing roster costs Section 17 and nothing else. What it costs is large —
    Section 17 is the only place a graduate who never published is counted at
    all — which is exactly why the reason is printed rather than the section
    quietly disappearing.
    """
    resolved = _resolve_table_path(path, cfg, "theses", "roster_path")
    if not resolved:
        return None, (
            "no degree-thesis roster was supplied, so no graduate who never published is counted "
            "anywhere in this report. Export this advisor's supervised theses from CNKI or 万方 "
            "and pass the CSV with --thesis-roster."
        )
    if not os.path.exists(resolved):
        logger.error("--thesis-roster 指向的文件不存在: %s", resolved)
        return None, f"--thesis-roster named {resolved}, which does not exist."

    from check_your_advisor.theses import load_thesis_roster

    try:
        roster = load_thesis_roster(resolved)
    except (OSError, ValueError) as exc:
        logger.error("学位论文名单无法读取，第 17 节将写明原因: %s (%s)", resolved, exc)
        return None, f"{resolved} could not be read as a thesis roster: {exc}"

    logger.info("学位论文名单: %s（%d 行可用 / 共读入 %d 行，编码 %s）",
                resolved, roster["denominator"], roster["rows_read"], roster["encoding"])
    logger.info("库来源: %s；导出日期: %s",
                "、".join(f"{name} {count}" for name, count in (roster["source_dbs"] or {}).items())
                or "未记录",
                "、".join(roster["export_dates"]) or "未记录")
    if "student_latin" in (roster["columns_missing_optional"] or []):
        logger.warning(
            "名单里没有「学生姓名拼音」列。中文姓名和 PubMed 的英文署名之间没有任何标准库能换算，"
            "本工具也不猜——没有这一列，每个毕业生都会落在「待人工核」里，"
            "「一篇都没发」的人数会被整段抑制。补这一列是让这个 join 从不可能变成可能的唯一办法。"
        )
    for flag, count in (roster["flag_counts"] or {}).items():
        logger.info("  %d 行带标记 %s（已保留，未丢弃）", count, flag)
    return roster, ""


def _load_evaluation_table(
    path: str | None, cfg: dict, logger: logging.Logger
) -> tuple[dict | None, str]:
    """The student-evaluation table, or None and the reason why.

    Same contract as the two loaders above, and the same reason it never gates:
    a missing table costs Section 20 and nothing else. It is the one table with
    no worklist command to generate it — a corpus names its own journals, and
    nothing in a corpus names the places people talk about an advisor — so the
    note has to carry the whole instruction rather than a verb to run.
    """
    # Every note returned here is printed into Section 20's `body`, and the
    # computed half of a section may not contain the word "student": the report
    # labels no person a student, because nothing in PubMed separates one from a
    # postdoc or a technician (`report.STRATUM_LABEL`). The section title and
    # its prose say what the table is; these three sentences do not need to.
    resolved = _resolve_table_path(path, cfg, "evaluations", "table_path")
    if not resolved:
        return None, (
            "no evaluation table was supplied, so no third-party statement about this advisor is "
            "printed. Collect them by hand from whatever public pages carry them and pass the CSV "
            "with --evaluation-table. Nothing is fetched for you: this package has no crawler and "
            "the sites do not permit one."
        )
    if not os.path.exists(resolved):
        logger.error("--evaluation-table 指向的文件不存在: %s", resolved)
        return None, f"--evaluation-table named {resolved}, which does not exist."

    from check_your_advisor.evaluations import EvaluationTableError, load_evaluation_table

    try:
        table = load_evaluation_table(resolved)
    except (OSError, EvaluationTableError, ValueError) as exc:
        logger.error("学生评价表无法读取，第 20 节将写明原因: %s (%s)", resolved, exc)
        return None, f"{resolved} could not be read as an evaluation table: {exc}"

    logger.info("学生评价表: %s（%d 行可用 / 共读入 %d 行，编码 %s）",
                resolved, table["denominator"], table["rows_read"], table["encoding"])
    logger.info("评价来源: %s；数据获取日期: %s",
                "、".join(f"{name} {count}" for name, count in (table["sources"] or {}).items())
                or "未记录",
                " 到 ".join(table["retrieved_on_range"] or []) or "未记录")
    if table["rows_without_year"]:
        logger.warning(
            "有 %d 行没填评价年份。这些话放不进时间轴——一位导师二十年里不是同一个人，"
            "十年前的说法和今年的说法在报告里会并排印出，但工具无法替你分辨哪句是哪年的。",
            table["rows_without_year"],
        )
    for flag, count in (table["flag_counts"] or {}).items():
        logger.info("  %d 行带标记 %s（已保留，未丢弃）", count, flag)
    return table, ""


def _is_count_map(value: Any) -> bool:
    """A mapping of name to whole-number count, which is what Section 1 can print.

    Mirrors `report._is_count_histogram`. A corpus file whose `by_source` is
    something else is reported as unstatable rather than corrected, so adding a
    bucket to it here would turn an honest "not recorded" into a fabricated
    split that happens to contain one real number.
    """
    return isinstance(value, Mapping) and all(
        isinstance(count, int) and not isinstance(count, bool) and count >= 0
        for count in value.values()
    )


def _load_chinese_records(path: str | None, cfg: dict, logger: logging.Logger):
    """The hand-exported Chinese bibliography, turned into corpus records.

    Unlike the other three tables this one does not feed a single section: it is
    merged into the corpus, so every count in the report moves. That is the whole
    point — a PubMed-only harvest of a Chinese advisor can miss most of what they
    published — but it also means a bad table corrupts everything rather than one
    block, so a malformed file refuses here instead of degrading quietly.
    """
    path = path or cfg.get("chinese_records")
    if not path:
        return None, ""

    from check_your_advisor.chinese_records import (
        ChineseRecordError,
        load_chinese_records,
        to_paper_records,
    )

    try:
        table = load_chinese_records(path)
        converted = to_paper_records(table, cfg.get("author_name", ""))
    except (OSError, ChineseRecordError) as exc:
        logger.error("中文题录读不了，本次不并入: %s", exc)
        return None, f"{path} could not be read as a Chinese bibliography: {exc}"

    counts = converted["counts"]
    logger.info("中文题录: %s（编码 %s，读入 %d 行，产出 %d 条记录）",
                path, table["encoding"], counts["rows_in"], counts["papers_out"])
    # Never silently dropped: a row whose byline could not be matched to the PI
    # still becomes a record and still counts toward output per year. What it
    # cannot do is hold a byline position, so it is named here rather than left
    # to look like a row that matched.
    if counts["pi_unmatched"]:
        logger.warning("其中 %d 行没能在署名里定位到目标导师，记录照样产出、"
                       "但不占任何署名位次。逐行原因如下：", counts["pi_unmatched"])
        for row in converted["unmatched"][:10]:
            logger.warning("  第 %s 行 [%s] %s", row.get("line", "?"),
                           row.get("reason", "?"), (row.get("title") or "")[:40])
    return converted, ""


def _build_profile_report(
    json_path: str,
    cfg: dict,
    output_dir: str,
    logger: logging.Logger,
    citations: dict | None = None,
    citations_note: str = "",
    journal_table: dict | None = None,
    journal_note: str = "",
    journal_risk: dict | None = None,
    chinese_records: dict | None = None,
    impact_reference: dict | None = None,
    impact_reference_note: str = "",
    journal_risk_note: str = "",
    thesis_roster: dict | None = None,
    thesis_note: str = "",
    evaluation_table: dict | None = None,
    evaluation_note: str = "",
) -> dict | None:
    """Turn the input file into a report dict. None means the input is unusable."""
    from check_your_advisor.profile import build_report, build_report_from_path, check_source_path

    # The spreadsheet refusal is decided on the extension alone so the file is
    # never opened; build_report_from_path is the entry point that does that.
    if check_source_path(json_path):
        return build_report_from_path(
            json_path, cfg,
            journal_table=journal_table, journal_note=journal_note,
            journal_risk=journal_risk, journal_risk_note=journal_risk_note,
            impact_reference=impact_reference, impact_reference_note=impact_reference_note,
            thesis_roster=thesis_roster, thesis_note=thesis_note,
            evaluation_table=evaluation_table, evaluation_note=evaluation_note,
        )

    import json as _json
    with open(json_path, encoding="utf-8") as f:
        data = _json.load(f)

    if isinstance(data, dict) and "search" in data:
        # fetch envelope: the search recorded what it did, so the report can
        # print how many of the matched records were actually retrieved.
        corpus = _profile_corpus(data.get("papers") or [], cfg, data["search"])
    elif isinstance(data, dict):
        corpus = data  # already written in the Section 4 corpus shape
    elif isinstance(data, list) and data:
        logger.warning(
            "这份 papers_*.json 是旧格式，不含检索式与命中数，provenance 的 query 块"
            "按当前 config 重建，报告里的「取回 N / 共 M」两个数都印不出来。"
            "重跑 harvest 可消除此限制。"
        )
        corpus = _profile_corpus(data, cfg)
    else:
        logger.error("输入 JSON 既不是论文列表也不是语料对象，或者是空的: %s", json_path)
        return None

    # No `gantt_path`: the timeline is now the HTML report's inline SVG, drawn over
    # the cohort the report's own aggregates are computed over. The PNG plotted all
    # 277 co-authors of a real corpus on one axis at 1934x21506 px, contradicting the
    # spec that excludes single-appearance people from every aggregate.
    # `analysis.render_gantt` is unchanged and still serves the `analyze` command.
    if chinese_records:
        from check_your_advisor.chinese_records import merge_chinese_records
        merged = merge_chinese_records(corpus.get("papers") or [], chinese_records["papers"])
        counts = merged["counts"]
        # Three denominators, never summed into one. A merged corpus and a PubMed
        # corpus are different numbers, and Section 1 prints the split because a
        # reader has to be able to tell which half a count came from.
        logger.info("并入中文题录: PubMed %d 篇 + 中文 %d 篇 → 合并 %d 篇"
                    "（疑似重复 %d 对，中文表内部重复 %d 对）",
                    counts["pubmed_total"], counts["chinese_total"], counts["merged_total"],
                    counts["suspected_duplicates"], counts["chinese_internal_duplicates"])
        if counts["suspected_duplicates"]:
            logger.warning("疑似重复只按「归一化标题 + 年份」判定，一级去重。"
                           "同一篇论文 PubMed 收英文题名、知网收中文题名时对不上，会算成两篇；"
                           "同年同题的两篇不同论文会被并成一篇。逐对明细见报告第 1 节。")
        # `counts["by_source"]` is what Section 1 prints, and it describes the
        # harvested file. Replacing `papers` without it would leave the report
        # stating a source split over a corpus that no longer matches it — the
        # exact failure this repository has hit five times, here with the merge
        # itself as the thing the prose contradicts. The existing buckets are
        # preserved rather than recomputed: `pubmed` / `openalex` / `both` record
        # what the *harvest* resolved, and a later merge is not entitled to
        # restate it.
        existing = dict(corpus.get("counts") or {})
        by_source = dict(existing.get("by_source") or {})
        if _is_count_map(by_source):
            by_source["chinese-record"] = counts["chinese_only"]
            existing["by_source"] = by_source
        corpus = {**corpus, "papers": merged["papers"], "counts": existing}

    return build_report(
        corpus, cfg, citations=citations, citations_note=citations_note,
        journal_table=journal_table, journal_note=journal_note,
        journal_risk=journal_risk, journal_risk_note=journal_risk_note,
        impact_reference=impact_reference, impact_reference_note=impact_reference_note,
        thesis_roster=thesis_roster, thesis_note=thesis_note,
        evaluation_table=evaluation_table, evaluation_note=evaluation_note,
    )


def cmd_profile(argv: list[str]):
    args = parse_profile_args(argv)
    cfg = load_config(args.config)
    output_dir = args.output_dir or cfg.get("output_dir", "pubmed_results")
    # Written back into cfg because the report reads the target name from there
    # when the corpus does not carry one.
    cfg["author_name"] = args.pi_name or cfg.get("author_name", "")

    setup_logging(output_dir, args.log_level)
    logger = logging.getLogger("check_your_advisor.profile")

    from check_your_advisor.corpus import find_latest_json
    from check_your_advisor.profile import resolve_score_weights, write_html, write_report

    json_path = args.papers_json or find_latest_json(output_dir)
    if not json_path or not os.path.exists(json_path):
        logger.error("没找到 papers_*.json — 先跑 harvest，或用 --papers-json 指定路径")
        return 1

    # Resolved here, before any work, so a typo in the weight table fails on the
    # first line instead of halfway through a built report. The same call runs
    # again inside build_report; it is pure and deterministic, so the answer is
    # the same one and this only decides where the failure lands.
    try:
        weights = resolve_score_weights(cfg)
    except (TypeError, ValueError) as exc:
        logger.error("scoring.weights 配置无效，未生成报告: %s", exc)
        return 1

    logger.info("=" * 60)
    logger.info("导师画像：发表记录反映出的「当这位 PI 的学生是什么样」")
    logger.info("output_dir=%s | 输入=%s | PI=%s",
                output_dir, json_path, cfg["author_name"] or "(未指定)")
    logger.info("综合分权重: %s（报告里会原样印出这张表）",
                ", ".join(f"{name}={value:g}" for name, value in weights.items()))
    logger.info("=" * 60)

    citations, citations_note = _load_citations(
        output_dir, json_path, logger,
        explicit=args.citations_json, disabled=args.no_citations,
    )
    journal_table, journal_note = _load_journal_table(args.journal_table, cfg, logger)
    journal_risk, journal_risk_note = _load_journal_risk(
        output_dir, logger,
        explicit=args.journal_risk_json, disabled=args.no_journal_risk,
    )
    chinese_records, _chinese_note = _load_chinese_records(args.chinese_records, cfg, logger)
    impact_reference, impact_reference_note = _load_impact_reference(output_dir, logger)
    thesis_roster, thesis_note = _load_thesis_roster(args.thesis_roster, cfg, logger)
    evaluation_table, evaluation_note = _load_evaluation_table(
        args.evaluation_table, cfg, logger)

    report = _build_profile_report(
        json_path, cfg, output_dir, logger,
        citations=citations, citations_note=citations_note,
        journal_table=journal_table, journal_note=journal_note,
        journal_risk=journal_risk, journal_risk_note=journal_risk_note,
        impact_reference=impact_reference, impact_reference_note=impact_reference_note,
        chinese_records=chinese_records,
        thesis_roster=thesis_roster, thesis_note=thesis_note,
        evaluation_table=evaluation_table, evaluation_note=evaluation_note,
    )
    if report is None:
        return 1

    paths = write_report(report, output_dir)
    # A refused report draws nothing: every number a figure would carry is wrong by
    # an unbounded amount once a gate fires, so the page states the gate instead.
    figures = {} if report["refused"] else _profile_figures(report, logger)
    html_path = write_html(report, output_dir, figures)

    if report["refused"]:
        gate = report["gate"]
        logger.error("门禁 %s (%s) 拒绝出报告：%s", gate["id"], gate["name"], gate["message"])
    else:
        for item in report.get("warnings") or []:
            # Logged as an error, not a warning: this is what used to stop the
            # run, the exit code is still 1, and a run that scrolls past this in
            # a terminal has learned nothing from the report being produced.
            logger.error(
                "身份提示 %s (%s)：%s 观测到：%s 修法：%s "
                "报告已完整生成（第 %s 节顶部各挂一行），退出码仍为 1。",
                item["id"], item["name"], item["message"],
                item["observed_text"] or "无", item["fix"],
                "、".join(str(section) for section in item["sections"]),
            )
        prov = report["provenance"]
        logger.info("语料 %d 篇 / 人员 %d 位（严格键 %d、宽松键 %d，两者之差即人员计数的误差范围）",
                    prov["corpus_size"], prov["n_people"], prov["n_strict"], prov["n_loose"])
        drawn = sum(1 for figure in figures.values() if figure.get("drawn"))
        logger.info("图表 %d/%d 已绘制，其余以说明文字代替（样本量不足或无可绘制的行）",
                    drawn, len(figures))
        impact = report.get("impact")
        if impact:
            logger.info("引用数覆盖 %d/%d 篇；h-index %s（受限于本检索窗口，不是终身 h-index）",
                        impact["covered"], impact["denominator"],
                        impact["h_index"] if impact["h_index"] is not None else "样本量不足，已抑制")
        else:
            logger.info("第 15 节（影响力）已跳过：%s", report.get("citations_note", ""))
        score = report.get("score") or {}
        stars = report.get("stars") or {}
        if score.get("suppressed"):
            logger.info("综合分：未给出——只有 %d 个分项同时有数据且权重非零，低于下限 %d；星级同样不给",
                        score.get("denominator", 0), score.get("min_components", 0))
        else:
            count = stars.get("stars")
            logger.info("综合分：%.1f / 100，来自 %d 个分项；星级 %s（%s 分档，边界印在报告里）。"
                        "这是本语料自己的一个绝对值，不是名次——报告里没有百分位，没有 A/B/C 等级，"
                        "也没有把任何一个人排在另一个人前面。",
                        score.get("score", 0.0), score.get("denominator", 0),
                        ("★" * count + "☆" * (stars.get("max_stars", 5) - count))
                        if count is not None else "未给出",
                        f"{stars.get('band', ['?', '?'])[0]:g}-{stars.get('band', ['?', '?'])[1]:g}"
                        if count is not None else "无")
        graduates = report.get("graduates")
        if graduates:
            counts = graduates["counts"]
            floor, ceiling = graduates["without_pubmed_bounds"]
            logger.info("毕业名单对照：%d 名毕业生，其中 %d 人在 PubMed 语料里，%d 人一篇都没有，"
                        "%d 人待人工核；「一篇都没发」的下界 %d、上界 %d",
                        counts["graduates_total"], counts["with_pubmed_record"],
                        counts["without_pubmed_record"], counts["needs_manual_review"],
                        floor, ceiling)
            for reason in graduates["suppressed_reasons"]:
                logger.warning("第 17 节的比例被抑制：%s", reason)
        else:
            logger.info("第 17 节（毕业名单）没有分母可对照：%s", report.get("thesis_note", ""))
        journals = report.get("journals") or {}
        if journals.get("table_missing"):
            logger.info("第 18 节（期刊指标）未提供对照表：本语料用到 %d 本刊，"
                        "跑一次 `check-your-advisor journal-worklist --output-dir %s` 生成待查清单",
                        journals.get("journal_denominator", 0), output_dir)
        elif journals:
            logger.info("期刊指标：%d/%d 篇匹配上，%d/%d 本刊匹配上，%d 本刊在版本之间有分歧（已并列，不替你选）",
                        journals["matched_papers"], journals["papers_with_journal"],
                        journals["matched_journals"], journals["journal_denominator"],
                        journals["disagreement_count"])
        risk = report.get("journal_risk") or {}
        if risk.get("risk_missing"):
            logger.info("第 18 节（期刊风险信号）未采集：%s", report.get("journal_risk_note", ""))
        elif risk:
            logger.info("期刊风险信号：%d/%d 本刊按 ISSN 对上；命中的信号 %s。"
                        "全部是事实陈述，不是等级——未被 DOAJ 收录不等于有问题，"
                        "Crossref 某项为 0 也不等于有问题",
                        risk["journals_checked"], risk["journal_denominator"],
                        "、".join(f"{name} {count}"
                                  for name, count in (risk["signal_counts"] or {}).items())
                        or "无")
        evaluations = report.get("evaluations")
        if evaluations:
            counts = evaluations["counts"]
            years = evaluations["year_range"]
            logger.info("学生评价：%d/%d 行归到本人名下，来自 %d 个来源，覆盖 %s；"
                        "%d 行没有年份。按原样列出，不做情感分析、不汇总、不打分，也不进综合分",
                        counts["evaluations_total"], counts["rows_in_file"],
                        counts["source_count"],
                        f"{years[0]}-{years[1]}" if years else "未记录年份",
                        counts["rows_without_year"])
            for reason in evaluations["suppressed_reasons"]:
                logger.warning("第 20 节无可印内容：%s", reason)
        else:
            logger.info("第 20 节（学生评价）未提供对照表：%s", report.get("evaluation_note", ""))
    logger.info("画像报告（主）: %s", html_path)
    logger.info("画像报告（备）: %s | %s", paths["markdown"], paths["json"])
    if args.pdf:
        # Runs last and on purpose. The HTML above is the deliverable; a PDF is a
        # second copy of that same file, so a machine with no converter loses a
        # convenience and nothing else — which is why this neither gates nor
        # changes the exit code.
        from check_your_advisor.html_to_pdf import convert, describe

        for line in describe(convert(
            html_path,
            prefer=args.pdf_converter or "",
            explicit_path=args.pdf_converter_path or "",
        )):
            logger.info("%s", line)
    return report["exit_code"]


def _per_corpus(values: list[str] | None, count: int, flag: str) -> list[str | None]:
    """Spread a repeatable flag over N corpora: one for all, or one each.

    Any other count is refused rather than padded. Silently reusing the last
    value would score corpus four under corpus three's config, and the reader
    has no way to see that from the page.
    """
    if not values:
        return [None] * count
    if len(values) == 1:
        return [values[0]] * count
    if len(values) == count:
        return list(values)
    raise ValueError(
        f"{flag} 给了 {len(values)} 个，但语料有 {count} 份——"
        f"要么给 1 个（全部共用），要么给 {count} 个（按顺序一一对应）"
    )


def cmd_compare(argv: list[str]):
    args = parse_compare_args(argv)
    setup_logging(args.report_dir, args.log_level)
    logger = logging.getLogger("check_your_advisor.compare")

    from check_your_advisor.corpus import find_latest_json
    from check_your_advisor.profile import (
        build_comparison,
        resolve_score_weights,
        write_comparison,
    )

    dirs = list(args.output_dirs)
    try:
        configs = _per_corpus(args.configs, len(dirs), "--config")
        pi_names = _per_corpus(args.pi_names, len(dirs), "--pi-name")
    except ValueError as exc:
        logger.error("%s", exc)
        return 1

    logger.info("=" * 60)
    logger.info("并排对比 %d 份语料", len(dirs))
    logger.info("=" * 60)

    # One weight table for the whole page, agreed before any corpus is read. A
    # column of scores computed under different weights is not a comparison of
    # anything, so a disagreement is refused here rather than reconciled — and
    # refused before the expensive part, so nothing is built and thrown away.
    shared: dict | None = None
    for directory, config_path in zip(dirs, configs):
        try:
            weights = resolve_score_weights(load_config(config_path))
        except (TypeError, ValueError, FileNotFoundError) as exc:
            logger.error("%s 的 scoring.weights 配置无效，未出对比: %s", directory, exc)
            return 1
        if shared is None:
            shared = weights
        elif weights != shared:
            logger.error(
                "权重表不一致，拒绝出对比。%s 用的是 %s，第一份语料用的是 %s。"
                "不同权重下算出的分并排放在一起不构成比较，所以这里拒绝而不是替你调和。",
                directory,
                ", ".join(f"{k}={v:g}" for k, v in weights.items()),
                ", ".join(f"{k}={v:g}" for k, v in shared.items()),
            )
            return 1
    logger.info("共用权重表: %s", ", ".join(f"{k}={v:g}" for k, v in (shared or {}).items()))

    entries = []
    for directory, config_path, pi_name in zip(dirs, configs, pi_names):
        cfg = load_config(config_path)
        cfg["author_name"] = pi_name or cfg.get("author_name", "")
        json_path = find_latest_json(directory)
        if not json_path or not os.path.exists(json_path):
            logger.error("%s 下没有 papers_*.json —— 先对它跑一次 harvest", directory)
            return 1

        citations, citations_note = _load_citations(
            directory, json_path, logger, disabled=args.no_citations,
        )
        report = _build_profile_report(
            json_path, cfg, directory, logger,
            citations=citations, citations_note=citations_note,
        )
        if report is None:
            logger.error("%s 的语料无法解析，未出对比", directory)
            return 1

        label = cfg["author_name"] or os.path.basename(os.path.normpath(directory))
        entries.append({"label": label, "source": directory, "report": report})
        if report["refused"]:
            gate = report["gate"]
            logger.warning("%s：门禁 %s (%s) —— 这一列保留，数字位置显示门禁",
                           label, gate["id"], gate["name"])
        elif report.get("warnings"):
            logger.warning(
                "%s：身份提示 %s —— 这一列的报告和分值都在，但不给名次：可能不止一个人的语料，"
                "名次是关于一个人的断言",
                label,
                "、".join(f"{item['id']} ({item['name']})" for item in report["warnings"]),
            )
        else:
            score = report.get("score") or {}
            logger.info("%s：语料 %d 篇%s", label,
                        report["provenance"]["corpus_size"],
                        "，综合分未给出（分项不足）" if score.get("suppressed")
                        else f"，综合分 {score.get('score', 0.0):.1f}")

    comparison = build_comparison(entries, shared, order_by=args.order_by)
    paths = write_comparison(comparison, args.report_dir)

    ranking = comparison["ranking"]
    logger.info("列序：%s", comparison["order"])
    if ranking["suppressed"]:
        # n_scored, not n_ranked: when the ranking is suppressed n_ranked is 0 by
        # construction, and printing that here claimed no corpus carried a score
        # two lines under the score itself.
        logger.info("没有排出名次：只有 %d 份语料带着分值，低于下限 %d——不足这个数就没有「第一」可言",
                    ranking.get("n_scored", ranking["n_ranked"]),
                    ranking["min_ranked_corpora"])
    else:
        for row in ranking["ranked"]:
            logger.info("第 %d / 共 %d 名：%s —— %.1f 分，%s，%d 个分项%s",
                        row["rank"], row["of"], row["label"] or "(未命名)", row["score"],
                        "★" * (row["stars"] or 0) + "☆" * (5 - (row["stars"] or 0)),
                        row["n_components"],
                        "，与 " + "、".join(row["tied_with"]) + " 并列" if row["tied_with"] else "")
    for row in ranking["unranked"]:
        logger.warning("%s 没有名次：%s（既不算 0 分，也不排在最后——最后也是一个位置）",
                       row["label"] or "(未命名)", row["reason"])
    if not ranking["comparable"]:
        logger.warning("%s", ranking["comparability"]["note"])
    for statement in comparison["statements"]:
        logger.info("%s", statement["statement"])
    # The last clause used to read「也没有给任何一个人排名」— printed directly under
    # 第 1 名 / 第 2 名 on rows labelled with people's names, which contradicted the
    # ranks above it and the page's own footer. What is refused is narrower and is
    # what the markdown says: no roster inside any report is ordered, and what is
    # ranked here is corpora.
    logger.info("名次是这几份语料之间的位置，不是任何更大人群里的位置；换一份语料进来，名次就会变。"
                "报告里没有百分位，没有 A/B/C 等级；排的是语料，任何一份报告里的人员名单都不按数量排序。")
    logger.info("对比结果: %s | %s", paths["markdown"], paths["json"])
    # A refused corpus keeps its row, so the page is still worth reading; the
    # exit code reports that at least one row carries a gate instead of numbers.
    # An identity warning counts the same way, which keeps the exit code exactly
    # what it was before G2/G3 stopped being gates.
    return 1 if any(
        entry["report"]["refused"] or entry["report"].get("warnings") for entry in entries
    ) else 0


def _resolve_openalex_identity(
    cfg: dict, identity_cfg: dict, logger: logging.Logger
) -> dict:
    """Settle the OpenAlex author id for this harvest, or record why it is not settled.

    Returns the block stored under `search["openalex"]`. It always says whether a
    resolution was asked for, what came back, and — when nothing was adopted —
    the candidates, so the corpus carries the ambiguity rather than the harvest
    swallowing it.

    Called before `search_pubmed` because the id belongs to the corpus this run
    produces, and because a run that ends ambiguous should say so before spending
    twenty minutes on efetch rather than after.

    An id given with `--openalex-author-id` is taken as given and no lookup is
    made: the user has already answered the question this lookup exists to ask.
    """
    openalex_cfg = cfg.get("openalex") or {}
    explicit = _openalex_id(identity_cfg.get("openalex_author_id"))
    if explicit:
        logger.info("OpenAlex 作者 ID 由命令行/配置直接指定: %s（不再查候选）", explicit)
        return {"requested": False, "resolution": "explicit",
                "openalex_author_id": explicit, "candidates": [], "query": "",
                "source": "user", "retrieved_at": ""}

    if not openalex_cfg.get("resolve_author"):
        return {}

    from check_your_advisor.http_client import RobustHTTPClient
    from check_your_advisor.openalex import resolve_author

    client = RobustHTTPClient(max_retries=3, backoff_factor=1.0, timeout=30)
    record = resolve_author(
        client,
        cfg.get("author_name", ""),
        affiliation=cfg.get("affiliation", ""),
        mailto=cfg.get("email", ""),
    )
    if record["openalex_author_id"]:
        # Written back so the PubMed filter, the provenance block and
        # `roles.evidence_tier` all read one value. The ORCID OpenAlex holds is
        # deliberately *not* copied into `identity["orcid"]`: that field means
        # "the user told us this", and quietly filling it from a third party's
        # record would make an inherited guess indistinguishable from a fact.
        identity_cfg["openalex_author_id"] = record["openalex_author_id"]
        orcid = (record["candidates"][0] or {}).get("orcid") if record["candidates"] else ""
        if orcid and not (identity_cfg.get("orcid") or "").strip():
            logger.info(
                "OpenAlex 记录这位作者的 ORCID 是 %s。工具**不会**替你把它当作你自己的断言写进"
                "身份块——核对无误后加 --orcid %s 重跑，那才是最强的一条证据。",
                orcid, orcid,
            )
    return record


def _openalex_corpus(
    cfg: dict, identity_cfg: dict, papers: list[dict], logger: logging.Logger
) -> tuple[list[dict], dict]:
    """The merged corpus and the merge's own provenance, or the input unchanged.

    Runs after the PubMed identity filter so the merge is against the records
    that survived it. An OpenAlex work duplicating a *rejected* PubMed record
    therefore enters as an OpenAlex-only record, which is the honest outcome:
    OpenAlex files it under this author id and PubMed's affiliation check did
    not confirm it, and the report shows both facts instead of silently picking
    a winner.
    """
    openalex_cfg = cfg.get("openalex") or {}
    if not openalex_cfg.get("merge_works"):
        return papers, {}

    author_id = _openalex_id(identity_cfg.get("openalex_author_id"))
    if not author_id:
        logger.error(
            "--openalex-works 需要一个确定的 OpenAlex 作者 ID，本次没有解析出来（候选为多个、"
            "为零，或查询失败）。本次按 PubMed 单源出语料；认出候选后加 --openalex-author-id 重跑。"
        )
        return papers, {"merge_requested": True, "merged": False,
                        "reason": "no resolved openalex_author_id"}

    from check_your_advisor.http_client import RobustHTTPClient
    from check_your_advisor.openalex import fetch_works, merge_corpora, work_to_paper

    years_back = int(cfg.get("years_back", 5) or 5)
    this_year = datetime.now().year
    client = RobustHTTPClient(max_retries=3, backoff_factor=1.0, timeout=30)
    works_provenance: dict = {}
    works = fetch_works(
        client, author_id,
        from_year=this_year - years_back, to_year=this_year,
        mailto=cfg.get("email", ""),
        max_works=int(openalex_cfg.get("max_works", 2000) or 2000),
        provenance=works_provenance,
    )

    converted = []
    no_lead_slot = 0
    for work in works:
        paper = work_to_paper(work, author_id)
        if paper is None:
            continue
        if not paper["role"]:
            # Same rule the PubMed filter applies: a corpus used to measure the
            # PI's own byline position cannot be built from records where the PI
            # holds no first / last / corresponding slot, or the filter becomes
            # the measurement.
            no_lead_slot += 1
            continue
        converted.append(paper)
    logger.info("OpenAlex 作品转换: %d 条可用，%d 条因本人不在第一/末位/通讯位被跳过",
                len(converted), no_lead_slot)

    result = merge_corpora(papers, converted)
    works_provenance.update({
        "merge_requested": True,
        "merged": True,
        "openalex_author_id": author_id,
        "works_usable": len(converted),
        "works_without_lead_slot": no_lead_slot,
        "counts": result["counts"],
        "matched_on": result["matched_on"],
        # Kept apart from `matched_on` all the way to the page: one is two
        # databases agreeing, the other is one database repeating itself.
        "openalex_internal_duplicates": result["openalex_internal_duplicates"],
    })
    return result["papers"], works_provenance


def cmd_fetch(argv: list[str]):
    args = parse_fetch_args(argv)
    cfg = apply_cli_overrides(load_config(args.config), args)

    # 设置日志
    log_file = setup_logging(cfg["output_dir"], cfg["log_level"])
    logger = logging.getLogger("check_your_advisor.main")

    logger.info("=" * 60)
    logger.info("PubMed 论文检索与下载工具 (v2.1)")
    logger.info("作者: %s | 机构: %s | 近 %d 年", cfg["author_name"], cfg["affiliation"], cfg["years_back"])
    logger.info("并发: %d 线程 | 日志: %s", cfg["max_workers"], log_file)
    logger.info("PDF下载: %s", "启用" if cfg.get("download_pdfs") else "关闭")
    logger.info("=" * 60)
    if not cfg.get("api_key"):
        logger.warning("未配置 PubMed API key，将按 NCBI 匿名限速请求。")
    if cfg.get("download_pdfs") and not cfg.get("email"):
        logger.warning("未配置 Unpaywall 邮箱，Unpaywall 源会自动跳过。")

    # 导入模块
    from check_your_advisor.engine import DownloadEngine
    from check_your_advisor.pubmed_api import (
        AUTHOR_IDENTITY,
        MAX_RECORDS,
        fetch_details,
        is_first_or_corresponding,
        search_pubmed,
    )

    # 用 CONFIG 中的 author_identity 覆盖默认配置。这里要的是一个**可写**的 dict：
    # OpenAlex 消歧解析出唯一候选时会把作者 ID 写回去，下游的过滤和 provenance
    # 都从这一个对象读。config.json 里写成 null 也不能让后面炸掉。
    identity_cfg = cfg.get("author_identity")
    if not isinstance(identity_cfg, dict):
        identity_cfg = {}
        cfg["author_identity"] = identity_cfg
    if identity_cfg:
        AUTHOR_IDENTITY.update(identity_cfg)
        logger.info("身份验证: 严格模式=%s | ORCID=%s | 机构关键词=%d个 | 邮箱域=%d个",
                     identity_cfg.get("require_affiliation", True),
                     "有" if identity_cfg.get("orcid") else "无",
                     len(identity_cfg.get("affiliation_keywords", [])),
                     len(identity_cfg.get("email_domains", [])))

    # Step 0: OpenAlex 作者消歧。放在检索之前，一是唯一候选的作者 ID 要写进本次
    # 语料的身份块，二是候选不唯一时要在花掉二十分钟 efetch 之前就说出来。
    openalex_record = _resolve_openalex_identity(cfg, identity_cfg, logger)
    if openalex_record.get("openalex_author_id"):
        AUTHOR_IDENTITY.update(identity_cfg)

    # Warned here, before the search, because harvesting is the slow half. The
    # profile report no longer refuses on this condition — it renders in full
    # with a warning at the top of Sections 0, 1 and 19 and exits 1 — but
    # discovering it there still means having already spent the run: the corpus
    # is "everyone sharing this name" and has to be collected again from the
    # start.
    if not _has_identity_evidence(identity_cfg or {},
                                  merge_works=bool((cfg.get("openalex") or {}).get("merge_works"))):
        logger.warning(
            "没有配置任何能落到记录上的身份证据（ORCID / 邮箱域名 / 机构关键词），"
            "检索回来的将是「所有叫这个名字的人」。常见姓名下这会混进好几个不同的研究者，"
            "画像报告仍会照常生成，但第 0/1/19 节顶部会挂一行提示说明这一点，退出码为 1。"
        )
        logger.warning(
            "补一条即可：--orcid 0000-0002-XXXX-XXXX，或 --email-domain your-university.edu.cn，"
            "或 --affiliation-keyword \"Your University Full Name\"。"
        )
        if (identity_cfg or {}).get("openalex_author_id"):
            logger.warning(
                "已有 OpenAlex 作者 ID，但**没有**加 --openalex-works：PubMed 的记录里根本不带这个"
                "字段，所以本次语料的每一条都只会是姓名匹配，这个 ID 一条也验证不了。"
                "要让它算数，就再加 --openalex-works 把 OpenAlex 的记录合并进来。"
            )

    # Step 1: 搜索。把身份配置一并传入 —— ORCID 可直接作为检索项，
    # 机构关键词在结果超出 max_records 时用于服务端收窄。
    # Collected here and written into papers_*.json so the profile report can
    # print how much of the matched set was actually retrieved, rather than
    # rebuilding a guess from the config it happens to be run with later.
    search_provenance: dict = {}
    pmids = search_pubmed(
        cfg["author_name"], cfg["years_back"], cfg["api_key"],
        retmax=cfg.get("retmax", 500), identity=identity_cfg,
        provenance=search_provenance,
        max_records=cfg.get("max_records", MAX_RECORDS),
    )
    if not pmids:
        logger.warning("未找到任何论文，请检查作者名拼写。")
        return

    # Step 2: 获取详情
    logger.info("正在获取 %d 篇论文的详细信息...", len(pmids))
    all_papers = fetch_details(pmids, cfg["api_key"], cfg["delay_seconds"])
    logger.info("成功解析 %d 篇", len(all_papers))

    # Step 3: 过滤（带机构深度验证 + [Keep]/[Skip] 日志）
    logger.info("筛选第一/通讯作者 + 机构身份验证...")
    matched_papers = []
    skipped_papers = []
    for p in all_papers:
        is_match, role = is_first_or_corresponding(
            p, cfg["author_name"], cfg["affiliation"], identity=identity_cfg
        )
        if is_match:
            p["role"] = role
            matched_papers.append(p)
        else:
            skipped_papers.append(p)

    logger.info("身份验证结果: %d 篇通过 / %d 篇拒绝 / %d 篇总计",
                len(matched_papers), len(skipped_papers), len(all_papers))

    # Written here, on the line above the fallback, and read nowhere else. These
    # are the two numbers the log line just printed, and `verified` means what
    # the word says: how many records passed the identity filter. Taking it
    # *after* the fallback — which is where it used to be taken — measured a
    # different thing under the same name: the fallback replaces `matched_papers`
    # with everything, so a run that logged「0 篇通过 / 3 篇拒绝」recorded
    # verified=3 / rejected=0 and Section 1 printed the opposite of the log. The
    # count of records that survived the PubMed stage is `corpus_total` minus
    # whatever OpenAlex added, and `fallback_fired` says which of the two
    # readings applies.
    search_provenance["fetched"] = len(all_papers)
    search_provenance["verified"] = len(matched_papers)

    # Decided here rather than inferred later. After Step 3.5 the fact is no
    # longer recoverable from the corpus: the old reader inferred it from "every
    # record is stamped 待确认", and a single merged OpenAlex record carrying a
    # real role makes that inference false — warning G2 disappears, the exit
    # code drops from 1 to 0, and a corpus that may hold several people rejoins
    # the comparison ranking.
    fallback_fired = bool(all_papers) and not matched_papers
    if fallback_fired:
        logger.warning("未找到以第一/通讯作者发表的论文，将保存全部结果供参考。")
        for p in all_papers:
            p["role"] = "待确认"
        matched_papers = all_papers

    search_provenance["fallback_fired"] = fallback_fired

    # Step 3.5: 第二语料源。合并在身份过滤之后，所以合并的是过滤活下来的那批。
    matched_papers, openalex_works = _openalex_corpus(
        cfg, identity_cfg, matched_papers, logger
    )

    matched_papers.sort(key=lambda x: x.get("pub_year", "0"), reverse=True)

    # Step 4: 并发下载 PDF
    if cfg["download_pdfs"]:
        logger.info("开始并发下载 PDF（%d 线程）...", cfg["max_workers"])
        engine = DownloadEngine(
            email=cfg["email"],
            max_workers=cfg["max_workers"],
            proxy_list=cfg.get("proxy_list"),
            cache_db=cfg["cache_db"],
        )
        try:
            stats = engine.download_batch(matched_papers, cfg["pdf_dir"])
            logger.info(
                "下载结果: 成功 %d / 缓存命中 %d / 失败 %d / 共 %d 篇",
                stats["downloaded"], stats["cached"], stats["failed"], stats["total"],
            )
            logger.info("缓存统计: %s", engine.cache.stats())
        finally:
            engine.close()
    else:
        for p in matched_papers:
            p["pdf_status"] = "未下载"

    # Step 5: 保存清单
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    from check_your_advisor.export import save_to_csv, save_to_excel, save_to_json
    try:
        import openpyxl  # noqa: F401  probe the optional `xlsx` extra
        excel_path = os.path.join(cfg["output_dir"], f"papers_{timestamp}.xlsx")
        save_to_excel(matched_papers, excel_path)
        logger.info("Excel 清单: %s", excel_path)
    except ImportError:
        csv_path = os.path.join(cfg["output_dir"], f"papers_{timestamp}.csv")
        save_to_csv(matched_papers, csv_path)
        logger.warning(
            "openpyxl 不可用，已降级导出 CSV 而非 Excel: %s（执行 `pip install openpyxl` 可恢复）",
            csv_path,
        )

    # 同步导出 JSON（供 download / profile 子命令复用作者完整信息）
    json_path = os.path.join(cfg["output_dir"], f"papers_{timestamp}.json")
    # `fetched` and `verified` were written before Step 3.5 and are deliberately
    # not rewritten here: both are statements about the PubMed half, and
    # `matched_papers` is the merged corpus by this point. The merged size gets
    # its own key so nothing has to be reconstructed from a subtraction.
    search_provenance["corpus_total"] = len(matched_papers)
    # Record which identity evidence this corpus was actually built with, for
    # the same reason the search parameters are recorded: the profile report
    # decides its identity gate from it, and deciding that from whatever config
    # happens to be loaded at report time answers the wrong question. It gets
    # both failures backwards — a properly disambiguated corpus is refused when
    # the flags are not repeated, and a name-only corpus is certified when they
    # are. The ORCID is a public identifier and the domains are what was
    # supplied; nothing secret goes in here.
    search_provenance["identity"] = {
        # The name this corpus was harvested under, so a later `profile` or
        # `compare` on the directory does not have to be told again. Same reason
        # as the rest of this block: what produced the corpus is a property of
        # the corpus, not of whatever config file happens to be loaded later.
        "author_name": cfg.get("author_name", ""),
        "orcid": (identity_cfg or {}).get("orcid", ""),
        "affiliation_keywords": list((identity_cfg or {}).get("affiliation_keywords", [])),
        "email_domains": list((identity_cfg or {}).get("email_domains", [])),
        "require_affiliation": bool((identity_cfg or {}).get("require_affiliation", False)),
        # Beside the four the user asserted, never merged into them. The block
        # under `search["openalex"]` says who said so and when.
        "openalex_author_id": _openalex_id((identity_cfg or {}).get("openalex_author_id")),
    }
    # Two separate records because they answer two questions: `openalex` is
    # "who did OpenAlex say this person is, and was that settled", `openalex_works`
    # is "what did the second source contribute to this corpus". A run can have
    # the first without the second.
    if openalex_record:
        search_provenance["openalex"] = openalex_record
    if openalex_works:
        search_provenance["openalex_works"] = openalex_works
    save_to_json(matched_papers, json_path, provenance=search_provenance)
    logger.debug("Papers JSON: %s", json_path)

    # PDF 下载结果与身份校验汇总报告
    from check_your_advisor.reports import write_pdf_validation_report
    report_path = os.path.join(cfg["output_dir"], f"pdf_validation_report_{timestamp}.csv")
    write_pdf_validation_report(matched_papers, report_path, pdf_dir=cfg["pdf_dir"])
    logger.info("PDF 校验报告: %s", report_path)

    # 打印论文列表
    logger.info("=" * 60)
    logger.info("检索完成！符合条件: %d 篇", len(matched_papers))
    for i, p in enumerate(matched_papers, 1):
        logger.info(
            "%d. [%s] %s | %s (%s) | PDF: %s",
            i, p.get("role", ""), p["title"][:60],
            p["journal"], p["pub_date"], p.get("pdf_status", ""),
        )


