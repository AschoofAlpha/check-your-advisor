# 查导师 Check Your Advisor

[![PyPI](https://img.shields.io/pypi/v/check-your-advisor)](https://pypi.org/project/check-your-advisor/)
[![Python](https://img.shields.io/pypi/pyversions/check-your-advisor)](https://pypi.org/project/check-your-advisor/)
[![License](https://img.shields.io/pypi/l/check-your-advisor)](LICENSE)
[![Dependencies](https://img.shields.io/badge/%E4%BE%9D%E8%B5%96-%E6%97%A0-brightgreen)](pyproject.toml)

*[English](README.md)*

你要选硕导或博导。手里只有一个名字、一个实验室自己写的主页，没有任何办法核实。
这个工具去 PubMed 把这个人的发表记录读出来，还给你一堆**带分母的事实**。

**它不告诉你这个导师好不好。**那是你的判断。它做的是把传闻换成数字，
并且每个数字后面都跟着"从多少人/多少篇里算出来的"。

```bash
python scripts/run.py harvest --author "Wang Wei" --orcid 0000-0002-1825-0097 \
    --years-back 10 --output-dir ./record --no-download
python scripts/run.py cite    --output-dir ./record
python scripts/run.py profile --pi-name "Wang Wei" --output-dir ./record
```

三条命令，一份 HTML 报告。不需要装任何东西，纯标准库。

## 它回答什么

- 这个组这些年出现过谁
- 一作名额落在谁头上，集中还是分散
- 新人等多久才拿到第一个一作
- 人一般待几年
- 这个老板自己在署名里站哪儿 —— 末位、通讯，还是也做具体活
- 年产出多少，是不是反复投同几本刊
- 引用数和 h-index，连同它们的覆盖率
- 一个 0–100 的综合分，每个分项的原始输入都印出来

以及两件需要你提供表格才能算的：这些期刊在**你查的那一版**里是什么档次，
还有 **有多少人在这里毕业却一篇被收录的论文都没有** —— 那正是上面所有数字都看不见的一群人。

## 它拒绝什么，以及为什么这个区分重要

"不告诉你导师好不好"背后其实是四件性质完全不同的事，报告里是分开写的：

| | 例子 | 为什么 |
|---|---|---|
| **算，且落盘** | 引用数、h-index、综合分、名次、星级 | 是测量，带分母印出来 |
| **拒绝** | 百分位、分位数、字母等级、趋势拟合、任何对**人**的排序 | 是决定，见下 |
| **等你给表** | 影响因子、JCR 分区、中科院分区、毕业名单 | 授权数据库或有反爬，工具只给 schema 不给爬虫 |
| **永远看不见** | 组里氛围、老板人品、没读完就走的人 | 任何数据库里都没有 |

百分位不是"不给"，是**算不出来** —— 整个计算过程一次最多只握着一页上那几份语料，
根本没有参照人群可以定位。字母等级拒绝、星级给，这两个都是同一个数的粗化，
这个区别是个决定，报告里明说了，免得看起来像疏漏。

## 语料决定一切

每个数字都是在 `harvest` 留下的那批论文上算的。如果混进了同名的另一个人，
花名册、等待时长、人员流动全都是错的 —— **而且错得在页面上完全看不出来**。

**至少给 `harvest` 一个唯一标识。**按强度排：

| 参数 | 强度 |
|---|---|
| `--orcid 0000-0002-...` | 最强，一个顶其余全部 |
| `--email-domain your-university.edu.cn` | 通讯作者邮箱域名，可重复给 |
| `--affiliation-keyword "..."` | 最弱 —— 同一个大学系统里的同名同事挡不住 |

完全没配身份证据时，报告会**拒绝生成**（门禁 G3）。但真正危险的情况能过这道门：
证据太弱会产出一份完整、看起来很正常、实际描述好几个人的报告。
实测一次：某位中国外科医生，只用省名而非完整机构名做关键词，抓回 28 篇，
横跨胃肠外科、分析化学、结构生物学、土壤微生物和机器学习 —— 综合分 78.2。

**第 19 节就是查这个的。**它把 PI 本人从作者里去掉（他按定义在每篇上），
问剩下的论文还能不能靠共同作者连成一片。一个人的产出是被他合作的人维系的；
两个同名的人没有理由共享任何第三者。它**不设阈值** —— 实测数据不支持任何阈值，
所以它只把簇和各簇的期刊印出来，判断交给你。通常一眼就能看出来。

## 两张要你手工填的表

影响因子、JCR 分区、中科院分区都是有授权的商业产品，没有免费可再分发的来源；
学位论文库有反爬。所以这里给的是 schema、待查清单和 join，不给爬虫：

```bash
python scripts/run.py journal-worklist --output-dir ./record
```

它生成的 CSV **只包含这份语料实际用到的期刊** —— 通常二十来本，不是全球两万本 ——
ISSN 和篇数已经填好，指标列留空。你从任何有权限的来源填完，然后：

```bash
python scripts/run.py profile --output-dir ./record \
    --journal-table ./record/journal_worklist_*.csv \
    --thesis-roster ./record/theses.csv
```

期刊表有两列是**必填**：`版本来源`（官方版/新锐版/民间版/JCR）和 `数据获取日期`。
少了这两列，过两年那个分区数字就无法追溯。同一本刊查到两个版本就写两行，
两行都会印出来、不替你选，不一致的字段会单独列出。

> 实测提醒：LetPub 检索结果列表页默认显示民间版分区，要官方版得点进详情页；
> 科研通 ablesci.com 把官方版和新锐版分开标注，可以交叉验证；
> Clarivate MJL 免费但只有 SCIE/SSCI 收录状态，没有影响因子也没有分区。

毕业名单是两张表里更重要的那张。从学位论文库导出该导师名下的题录，
它能算出 PubMed 结构上不可能给的数字：**有多少毕业生一篇被收录的论文都没有。**
记得加一列姓名拼音，否则中文名单和英文署名根本对不上。
另外，入学后没读完就走的人在任何库里都没有 —— 这张表只是把缺口缩小，没有补上。

## 安装

当命令行工具用：

```bash
pip install check-your-advisor
check-your-advisor harvest --author "Wang Wei" --orcid 0000-0002-1825-0097
```

当 Claude Code 技能用 —— clone 到技能加载器会看的位置，让 `SKILL.md` 和代码待在一起：

```bash
git clone https://github.com/AschoofAlpha/check-your-advisor.git \
    ~/.claude/skills/check-your-advisor
```

也可以 clone 下来直接跑 `scripts/run.py`，它是唯一入口，完全不用装。

pip 包**不声明任何依赖**，这不是漏写：装完之后的干净虚拟环境里只有这个包、pip 和
setuptools，再无其他。两个可选 extras 都有经过测试的降级路径 ——
`pip install "check-your-advisor[pdf]"` 启用 PyMuPDF 的 PDF 隔离，
`[xlsx]` 启用 openpyxl 导出，`[all]` 两个都要。

## 可选依赖

两个都有经过测试的降级路径，都不是必需的。

- **PyMuPDF**（AGPL-3.0，所以刻意不做 MIT 项目的硬依赖）让 PDF 身份校验能把
  下错的文件隔离出去。没有它时每个下载仍然会检查 `%PDF-` 魔数。
- **openpyxl** 启用 `.xlsx` 导出。没有它就写成带时间戳的 CSV。

## 测试

```bash
python tests/run_all.py
python tests/run_all.py --block-third-party
```

19 个文件 1925 条断言。第二条命令会装一个 import hook，在每个测试进程里屏蔽
`requests`、`urllib3`、`pandas`、`numpy`、`matplotlib`、`fitz`、`openpyxl`。
这是"不需要装东西"这句话唯一的保证，而不是只是嘴上说说：
恰好三条断言在没有它们时行为不同，而那三条都是需要真实 PDF 文件的用例。

## 已知问题

- 报告是英文，命令行日志是中文。这不是谁能辩护的设计，是它长成这样的，统一是待办。
- `cite` 没有自己的缓存。`--max-age-days N` 可以沿用上次那份文件里的计数，是变通办法。
- 期刊名匹配对 ISSN 抓取功能之前建的语料只能退回 token 启发式。重抓一次可以拿到精确 join。

## 许可证

MIT，见 [LICENSE](LICENSE)。

引用数来自 OpenAlex、Semantic Scholar、Europe PMC，三家都不需要密钥。
文献记录来自 NCBI E-utilities。本项目不附带任何授权数据。
