# 更新记录 · Changelog

## 0.4.0 — 2026-10-07

### 中文

相对 PyPI 上的 0.3.0。0.3.1 没有发到 PyPI，它的改动也算在这里。

新功能

- 报告每次写两份，中文一份、英文一份，数字完全一样。命令行跟着系统语言走，也可以用 `--lang zh` 或 `--lang en` 指定。
- `harvest --source openalex`：从 OpenAlex 取论文。PubMed 只收生物医学，工科、计算机、物理、化学、社科的导师用这个。`--source both` 两边都取，去重后合并。
- `--author-email`：用导师本人的邮箱认人。没有 ORCID 的时候，`harvest` 会列出署名里这个名字旁边出现过的邮箱和院系，同名的几个人在这里能分开。OpenAlex 的候选作者会列出研究方向。
- 每份报告开头有一页速览。
- `journal-risk` 会顺带取 OpenAlex 的期刊两年平均被引和 h 指数，印在第 18 节。这不是 JCR 影响因子。
- `profile --thesis-source` 和 `--thesis-exported`：知网、万方直接导出的名单文件不用再手动补列。
- 可以作为 Claude Code 插件安装：`/plugin marketplace add AschoofAlpha/check-your-advisor`。
- `--version` 显示版本号。

修复

- 按 README 装成技能后找不到自己的脚本。
- SKILL.md 里的触发词写在 Claude Code 不读的字段里，一直没起作用。
- PyMuPDF 以后会去掉 `fitz` 这个模块名，到那时 PDF 身份校验会悄悄跳过。现在优先用 `pymupdf`。
- `diff` 会在当前目录建一个叫 `INFO` 的文件夹放日志，`--log-level` 也不起作用。
- 用真实的 OpenAlex 接口跑了一遍，修了它暴露的问题：
  - `--affiliation` 配 OpenAlex 时查询直接失败。OpenAlex 已经不接受按机构名搜索作者（返回 HTTP 400），现在先查出机构 ID 再按 ID 筛。
  - 候选作者只显示前 10 位时，现在会写明一共有多少位（比如"清华 Wang Wei"一共 64 位）。
  - 有的 OpenAlex 档案挂着几十上百个机构，通常是几个同名的人被并成了一个，现在会提示。
  - 每个请求只取用得到的字段，传输量小很多。
  - 期刊风险信号旁边印的来源地址里不再带你的邮箱。
- 用真实的 PubMed 数据跑了一遍（"Wang Wei" + 北京协和医学院，1601 篇），修了它暴露的问题：
  - 姓名匹配太松。"Wang Wei" 会把 Wang Weibin、Wang Weiwei、Wang Jianwei 也算进来，名字以 W 开头的王姓几乎都算。那次被算成 "Wang Wei" 的署名，每五个里有四个其实是别人。现在名字要完全一样才算（连字符、空格、大小写、声调符号不管），只有记录里只印了缩写时才比首字母。
  - 照提示加 `--affiliation-keyword "<科室>"` 重跑，结果反而更差：PubMed 按这个长科室名检索，只找回 2 条，实际至少有 9 条；核对时又把 `--affiliation` 的学校名加了回去，同校别的科室的同名者照样通过。现在检索按学校名和科室名一起搜，核对只按科室。那次重跑拿到的正好是这个科室的 9 条。
  - 只给邮箱挡不住同校的同名者，因为学校名还在核对里。提示和文档改成先给科室，知道邮箱再加上。
  - 连不上 PubMed 时，`harvest` 给一行错误说明，退出码 1，不再甩一屏 Python 报错。
  - 配置文件里写的机构也算作核对关键词了，和命令行给的一样。
- 一页速览第二行写共同作者分簇的结果。同名的几个人混在一起时，这一行是最先该看的。
- OpenAlex 开始按用量计费：不带 key 每天约 0.1 美元免费额度，够查好几位导师。设了环境变量 `OPENALEX_API_KEY` 就会带上 key，额度提高到 10 倍；额度用完时日志会说清楚。

### English

Compared with 0.3.0 on PyPI. 0.3.1 was never published there, so its changes are listed here too.

New

- Every report is written twice, in Chinese and in English, with the same numbers. The command line follows the system language; `--lang zh` or `--lang en` overrides it.
- `harvest --source openalex` takes papers from OpenAlex. PubMed covers biomedicine only; use this for advisors in engineering, computer science, physics, chemistry or the social sciences. `--source both` takes both and merges them without duplicates.
- `--author-email` identifies the advisor by their own address. Without an ORCID, `harvest` lists the emails and departments printed beside the name, which is where namesakes separate. OpenAlex candidates now show their research topics.
- Every report opens with a one-page summary.
- `journal-risk` also collects OpenAlex's 2-year mean citedness and h-index per journal, shown in Section 18. These are not JCR impact factors.
- `profile --thesis-source` and `--thesis-exported`, so a roster exported straight from CNKI or Wanfang loads without adding columns by hand.
- Installable as a Claude Code plugin: `/plugin marketplace add AschoofAlpha/check-your-advisor`.
- `--version` prints the version.

Fixes

- The skill could not find its own script when installed the way the README described.
- SKILL.md kept its trigger phrases in a field Claude Code does not read, so they never worked.
- PyMuPDF is dropping the `fitz` module name, which would have turned the PDF identity check into a silent skip. `pymupdf` is now tried first.
- `diff` created a folder named `INFO` in the current directory for its log, and ignored `--log-level`.
- A run against the live OpenAlex API turned up and fixed:
  - `--affiliation` with OpenAlex failed outright. OpenAlex no longer accepts an author search by institution name (HTTP 400); the institution is now looked up first and filtered by its id.
  - When only the first ten candidate authors are listed, the total is now given (64 for "Wang Wei" at Tsinghua).
  - Some OpenAlex profiles list dozens or hundreds of institutions, usually several people merged into one; the candidate list now says so.
  - Every request asks only for the fields it reads, which cuts the transfer a lot.
  - The source address printed beside each journal signal no longer carries your email.
- A run against real PubMed data ("Wang Wei" at Peking Union Medical College, 1,601 records) turned up and fixed:
  - Name matching was far too loose. "Wang Wei" also matched Wang Weibin, Wang Weiwei, Wang Jianwei and nearly every other Wang whose given name starts with W; four in five of the bylines it counted as "Wang Wei" on that run were someone else's. A given name now has to be the same name (hyphens, spaces, case and accents aside), and only a record that prints initials alone is compared by its first letter.
  - Following the hint and re-running with the department as `--affiliation-keyword` made things worse. The PubMed search used the long department name and found 2 records where at least 9 existed, and the check put the `--affiliation` university back in, so namesakes in other departments still passed. The search now uses the university and the department together, and the check uses the department alone; that re-run then returned exactly the department's 9 records.
  - An address alone does not keep out namesakes at the same university, because the university stays in the check. The hint and the docs now say to give the department, and the address on top if you know it.
  - When PubMed cannot be reached, `harvest` prints a one-line error and exits 1 instead of a Python traceback.
  - An institution set in the config file now counts as a check keyword too, the same as one given on the command line.
- The one-page summary's second line gives the co-author clusters, the first thing to read when several people share a name.
- OpenAlex now bills its API: about $0.10 a day free without a key, enough for several advisors. With a key in `OPENALEX_API_KEY` every OpenAlex request carries it, for ten times that; running out is explained in the log.
