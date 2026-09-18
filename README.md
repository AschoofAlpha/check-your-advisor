# Check Your Advisor

[![PyPI](https://img.shields.io/pypi/v/check-your-advisor)](https://pypi.org/project/check-your-advisor/)
[![Python](https://img.shields.io/pypi/pyversions/check-your-advisor)](https://pypi.org/project/check-your-advisor/)
[![License](https://img.shields.io/pypi/l/check-your-advisor)](LICENSE)
[![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)](pyproject.toml)

*[中文说明](README.zh-CN.md)*

You are choosing a PhD or master's advisor. You have a name, a lab page written
by the lab, and no way to check any of it. This reads what PubMed records about
that person and reports it back as facts with denominators.

**It does not tell you whether the advisor is good.** That judgement stays
yours. What it does is replace hearsay with counts that carry the population
they were counted over.

```bash
python scripts/run.py harvest --author "Wang Wei" --orcid 0000-0002-1825-0097 \
    --years-back 10 --output-dir ./record --no-download
python scripts/run.py cite    --output-dir ./record
python scripts/run.py profile --pi-name "Wang Wei" --output-dir ./record
```

Three commands, one HTML report. Nothing to install: the standard library only.

## What it answers

- who has appeared in this group over the window
- who the first-author slots went to, and how concentrated they are
- how long a newcomer waits before getting one
- how long people stay before they stop appearing
- where the PI sits in their own bylines — last, corresponding, or doing the work
- how much comes out per year, and whether it goes to the same few journals
- citation counts and an h-index, with the coverage they were computed over
- one composite score out of 100, with every component's raw input printed

And, once you supply a table it cannot fetch: what those journals are rated in
the edition you looked them up in, and **how many people finished a degree here
without a single indexed paper** — the one group every other number is blind to.

## What it refuses, and why the distinction matters

Four different things stand behind "it does not say whether the advisor is
good", and the report keeps them apart on the page:

| | Examples | Why |
|---|---|---|
| **Computed** | citation counts, h-index, composite score, rank, star band, letter band, fitted slope, roster ranking, citation percentile against an external cell | measurements, printed with denominators |
| **Refused** | a position among the corpora *you* loaded, a percentile on the composite score, year-over-year percentage change | a decision — see below |
| **Waiting on a file** | impact factor, JCR quartile, CAS partition, graduate roster | licensed or defended databases; the tool ships schemas, not a crawler |
| **Never visible** | lab culture, whether the PI is decent, people who left before finishing | in no database at all |

A position among the corpora on one page is not withheld here, it is
*uncomputable*: that set is the one you assembled, so adding or dropping an
unrelated corpus moves everybody in it. The composite score has no percentile for
the same reason — nothing else in the world computes that score.

A citation *count* is a different matter, and round four placed it. `cite
--percentile` asks OpenAlex where a count falls among every work sharing that
paper's topic and publication year: a population that is the same whether this
run loaded one corpus or nine, and the same for anyone else running the query.

The definition matters more than it looks. The percentile is the share of works
in the cell cited **strictly fewer** times. In one measured cell, 1399 of 3808
papers had never been cited; under "at or below", every one of those would come
back as the 65th percentile, printing "nobody cited this" as "above average".
Strictly-fewer puts them at 0.0, which is true. The other reading is printed
alongside, with the size of the tie, rather than chosen for you.

Papers that could not be placed never come back as low ones. Seven named statuses
say which thing did not happen — no citation count, no identifier, topic lookup
failed, no topic recorded, no year, distribution unavailable, reference cell too
small — and each prints its own sentence in Section 15.

## The corpus decides everything

Every number is computed over the papers `harvest` kept. If papers by a
different person with the same name get in, the roster, the time-to-first-author
and the turnover figures are all wrong — and wrong in a way that looks perfectly
normal on the page.

**Give `harvest` at least one unique identifier.** In descending order of
strength:

| Flag | Strength |
|---|---|
| `--orcid 0000-0002-...` | Strongest. One is worth all the rest |
| `--email-domain your-university.edu.cn` | The corresponding author's address. Repeatable |
| `--affiliation-keyword "..."` | Weakest — it fails on same-name colleagues inside one university system |
| `--resolve-openalex` / `--openalex-author-id A...` | OpenAlex's own author clustering. Recorded as OpenAlex's assertion, never as yours |

`--resolve-openalex` prints every OpenAlex author publishing under this name,
with its id, ORCID, institution history and works count. **One candidate is
adopted; two or more are listed and none is adopted** — picking the most
prolific one would settle an identity question on a proxy and leave no sign in
the report that a choice had been made. Re-run with `--openalex-author-id` once
you recognise the right one.

If the harvest recorded no evidence at all, the report still renders — with a
warning box at the top of Sections 0, 1 and 19 naming the condition, the numbers
observed and the fix, with Section 14 recording that this used to be a refusal,
and with the process exiting 1. It used to refuse outright; that cost sixteen
sections of computed fact, including Section 19, which is the one section that
would have shown the reader whether the corpus really does hold several people.
Such a corpus also takes no rank on a `compare` page: it keeps its row and its
score, and holds no position.

The dangerous case raises no warning at all: weak evidence produces a complete,
normal-looking report about several people. A real run for one Chinese surgeon,
keyed on a province name rather than the full institution, returned 28 records
spanning gastrointestinal surgery, analytical chemistry, structural biology,
soil microbiology and machine learning — and scored 78.2 out of 100.

**Section 19 is the check for that.** It removes the PI, who is on every record
by construction, and asks which records are still tied together by a shared
co-author. One person's output is held together by the people they work with;
two people sharing a name have no reason to share anyone else. It applies no
threshold — the measurements do not support one, and the README of a tool like
this should not pretend otherwise — it prints the clusters and their journals
and hands the reading to you. It is usually not subtle.

## A second source, if you want one

```bash
python scripts/run.py harvest --author "Wang Wei" \
    --openalex-author-id A5023888391 --openalex-works --output-dir ./record
```

Pulls the works OpenAlex files under that author id and merges them into the
PubMed corpus, deduplicating on DOI, then PMID, then normalised title plus year.
The PubMed record wins where both hold a paper — its record carries the
affiliation strings and corresponding-author emails every identity check reads,
and OpenAlex's does not. Every record says which source it came from and which
sources hold it, and Section 1 prints both denominators, because a merged corpus
and a PubMed corpus are different numbers. Neither API needs a key; `--email`
becomes OpenAlex's `mailto`, which is a rate-limit pool and not authentication.

## Three tables you fill in by hand

Impact factor, JCR quartile and CAS partition live in licensed products with no
free redistributable source. Degree-thesis libraries defend against scraping.
Student evaluations sit on forums and review pages that permit automated
collection least of all. So this ships the schema, the worklist and the join,
and no crawler. (What *is* fetched, from keyless public APIs whose terms permit
it, is citation counts and the journal risk signals in the next section — both
into their own dated files, never back into a table you filled in.)

```bash
python scripts/run.py journal-worklist --output-dir ./record
```

That writes a CSV holding **only the journals this corpus actually uses** —
typically a couple of dozen, not the twenty thousand in the world — pre-filled
with ISSN and paper count, indicator columns blank. Fill it from whichever
source you have access to, then:

```bash
python scripts/run.py profile --output-dir ./record \
    --journal-table ./record/journal_worklist_*.csv \
    --thesis-roster ./record/theses.csv \
    --evaluation-table ./record/evaluations.csv
```

Two columns are **required** in the journal table: `版本来源` (which edition —
official, rising-star, folk, or JCR) and `数据获取日期`. Without them a
partition number is unfalsifiable two years from now. Where a journal has rows
from two editions, both are shown and neither wins; disagreements are listed.

The graduate roster is the most important of the three. Export the advisor's
supervised theses from a degree library and it produces the number PubMed
structurally cannot: **how many graduates have no indexed paper at all.** Add a
romanised-name column or the Chinese roster will not match the English bylines.
Note that people who enrolled and left before finishing are in no library
either — the roster narrows the missing group, it does not close it.

The evaluation table is the one you collect entirely by hand; there is no
worklist command for it, because nothing in a corpus names the places people
talk about an advisor. Required columns: `导师姓名`, `评价来源`,
`数据获取日期`, and at least one of `评价内容` or `维度评分`. Section 20 prints
how many statements were attributed to this advisor, how many distinct sources
they came from, the span of years they cover, and then every statement in file
order with its source and retrieval date attached. **It runs no sentiment
analysis, produces no average or rating, and contributes nothing to the
composite score** — these are a handful of statements by people who chose to
write something down, so any number computed over them would measure who
bothered to post. They are reproduced, attributed and dated; the judgement is
yours.

## A fourth table: Chinese-language records

The other three hand-filled tables each feed one section. This one is merged
into the corpus, so every number in the report moves — which is the point. A
PubMed-only harvest of an advisor who publishes in Chinese journals can miss
most of their output: 12 indexed papers against 40 in Chinese core journals
makes "records per year", the first-author distribution and the turnover figures
all statements about a quarter of the work, **and the page gives no sign of it**.

```bash
python scripts/run.py profile --output-dir ./record     --chinese-records ./record/cnki.csv
```

Seven required columns; a missing one is named at load time rather than skipped.
One of them decides whether the table is usable at all: **the romanised author
column**. A Chinese byline and a romanised advisor name are in different scripts,
and without the romanisation the advisor cannot be located in their own byline —
the record still counts toward output per year, but holds no byline position.
Rows like that are reported line by line, never dropped.

Encodings are tried `utf-8-sig` then `gb18030`, in that order deliberately:
gb18030 decodes almost any byte sequence, so trying it first would silently turn
every UTF-8 file into mojibake and report success.

**Deduplication has one tier: normalised title plus year.** Chinese records
usually carry neither DOI nor PMID, so the DOI → PMID → title ladder used for the
OpenAlex merge is unavailable. It fails in both directions — two different papers
sharing a title and year collapse into one, and one paper indexed under an
English title by PubMed and a Chinese title by CNKI never matches itself. So a
cross-source hit is reported as **suspected**, listed pair by pair, and the three
denominators are printed separately rather than summed.

## Looking again six months later: `diff`

```bash
python scripts/run.py diff ./record-2026-03 ./record-2026-09
```

Older directory first. Reports what is in one corpus and not the other, who
appears for the first time, and where the first-author slots went.

**Read the window block before any count.** If the two harvests asked about
different year ranges, "7 more papers" is probably just two extra years of
searching — which is the window moving, not the record. So every one-sided count
is split three ways: inside the years both harvests asked about (that is growth),
inside years only one of them asked about (that is the caliber changing), and
undated. When either corpus did not record its window, the command says the two
**cannot be compared** rather than substituting the span the corpus happens to
cover.

**It reports membership and never a cause.** A paper in the older corpus and not
the newer one may have been retracted, or the search term may differ, or the
identity evidence, or simply what the databases answered on the two days. All
four are listed side by side, unordered. The tool cannot tell them apart and does
not guess.

## Public risk signals — statements, not a rating

Three things about a journal *are* free to look up, and one command collects
them:

```bash
python scripts/run.py journal-risk --output-dir ./record
```

DOAJ indexing status, Crossref metadata deposit coverage and OpenAlex's source
record, for every journal in the corpus carrying an ISSN. Keyless public JSON
APIs, one GET each, its own dated file, one `fetched_at` per record, nothing
written back into the corpus or into the table you filled in. `profile` picks up
the newest one and Section 18 prints each statement beside the endpoint that
returned it and the day it was read.

**Nothing here calls any journal predatory.** That is an accusation about a
publisher's conduct; none of these three APIs makes it and neither does this
tool. No count of these signals becomes a grade, a tier, a score, a letter or a
colour, and none of them touches the composite score. You get lines like
`未被 DOAJ 收录` and `Crossref 元数据缺失 3 项（共查 10 项）`, with their sources
and dates, and the reading is yours.

Two are misread almost every time, so the report says the opposite beside them:
**absence from DOAJ is not a finding** — DOAJ indexes open-access journals that
applied to it, so Journal of Hepatology is absent and always will be, as is most
of the subscription literature in medicine. **A Crossref coverage field reading
zero is not a finding either** — it measures what a publisher deposits, and
Journal of Hepatology scores 3 of 10 tracked fields at zero.

All three sources are queried every time and none wins. They disagree routinely;
where they do, both lines print with their dates. OpenAlex's Scopus field is
`null` for a great many real journals, which is a third value and is never
printed as "not indexed".

**The 中科院国际期刊预警名单 is not fetched and will not be.** It is published once
a year as a login-walled page and a PDF with no machine-readable endpoint, so it
stays in the hand-filled `是否预警` / `预警等级` columns above. A blank there
means nobody checked, and no signal in this block fills it in.

## Install

As a command-line tool:

```bash
pip install check-your-advisor
check-your-advisor harvest --author "Wang Wei" --orcid 0000-0002-1825-0097
```

As a Claude Code skill — clone it where the skill loader looks, so `SKILL.md`
lands beside the code:

```bash
git clone https://github.com/AschoofAlpha/check-your-advisor.git \
    ~/.claude/skills/check-your-advisor
```

Or run `scripts/run.py` straight out of a clone; it is the single entry point
and needs no installation at all.

The pip package declares **no dependencies**, which is not an oversight: a clean
virtualenv with this installed contains this package, pip and setuptools, and
nothing else. Two extras exist and both have a tested fallback —
`pip install "check-your-advisor[pdf]"` for PyMuPDF-backed PDF quarantine,
`[xlsx]` for openpyxl export, `[all]` for both.

## Optional extras

All three degrade with a tested fallback; none is required.

- **PyMuPDF** (AGPL-3.0, so deliberately not a hard dependency of an MIT
  project) lets PDF identity validation quarantine a wrong file. Without it
  every download is still checked for the `%PDF-` magic.
- **openpyxl** enables the `.xlsx` export. Without it the same data is written
  as a timestamped CSV.
- **An HTML-to-PDF converter already on the machine** — wkhtmltopdf, Chrome /
  Chromium / Edge, weasyprint or LibreOffice — lets `profile --pdf` write a PDF
  beside the HTML report. None of them is imported and nothing is downloaded:
  the tool runs a program that is already installed. With none present, `--pdf`
  prints how to install each and the HTML is unchanged, which is the point — the
  HTML is the deliverable and the PDF is a second copy of it. `--pdf-converter`
  picks one; `--pdf-converter-path` names a binary that is installed but not on
  PATH, which on Windows is the normal state of both Chrome and LibreOffice.

## Tests

```bash
python tests/run_all.py
python tests/run_all.py --block-third-party
```

4905 assertions across 35 files, measured 2026-09-17 by the first command
above — and that command re-measures it every run and fails if this sentence
has drifted, which is why it is a number rather than a promise. The second run
installs an import hook that
blocks `requests`, `urllib3`, `pandas`, `numpy`, `matplotlib`, `fitz` and
`openpyxl` inside each test process. It is the only thing that keeps "no install
needed" true rather than merely claimed: exactly three assertions behave
differently without them, and all three are the cases that need a real PDF file.

## Known limitations

- The report is in English; the command-line logs are in Chinese. Not a decision
  anyone would defend — it is where the tool grew up, and unifying it is open.
- ~~`cite` has no cache of its own.~~ **Resolved.** `cite` and `journal-risk`
  now cache what each API answered, keyed on which API was asked and what it was
  asked about, in a second table beside the PDF cache in the same SQLite file.
  The cache is **written on every successful fetch and read only when you pass
  `--max-age-days N`** — so a default run still asks every source and simply
  leaves the cache warmer than it found it; the saving arrives on the next run.
  A hit carries **the day the answer was really collected**, never the day it
  was served.
- Journal name matching falls back to a token heuristic for corpora harvested
  before ISSN capture existed. Re-harvest to get the exact join.

## License

MIT. See [LICENSE](LICENSE).

Citation counts come from OpenAlex, Semantic Scholar and Europe PMC, none of
which requires a key. Bibliographic records come from NCBI E-utilities. This
project ships no licensed data.
