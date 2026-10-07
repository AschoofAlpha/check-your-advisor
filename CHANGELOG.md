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
