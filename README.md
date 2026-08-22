# Check Your Advisor

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
| **Computed** | citation counts, h-index, composite score, rank, star band | measurements, printed with denominators |
| **Refused** | percentile, quantile, letter grade, fitted trend, any ordering of *people* | a decision — see below |
| **Waiting on a file** | impact factor, JCR quartile, CAS partition, graduate roster | licensed or defended databases; the tool ships schemas, not a crawler |
| **Never visible** | lab culture, whether the PI is decent, people who left before finishing | in no database at all |

A percentile is not withheld here, it is *uncomputable*: nothing in the
calculation ever holds more than the few corpora on one page, so there is no
reference population to take a position in. Letter grades are refused while
star bands are produced — that split between two coarsenings of one number is a
decision, and the report says so rather than leaving it looking like an
oversight.

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

The report **refuses to render** if the harvest recorded no evidence at all
(gate G3). But the dangerous case passes that gate: weak evidence produces a
complete, normal-looking report about several people. A real run for one Chinese
surgeon, keyed on a province name rather than the full institution, returned 28
records spanning gastrointestinal surgery, analytical chemistry, structural
biology, soil microbiology and machine learning — and scored 78.2 out of 100.

**Section 19 is the check for that.** It removes the PI, who is on every record
by construction, and asks which records are still tied together by a shared
co-author. One person's output is held together by the people they work with;
two people sharing a name have no reason to share anyone else. It applies no
threshold — the measurements do not support one, and the README of a tool like
this should not pretend otherwise — it prints the clusters and their journals
and hands the reading to you. It is usually not subtle.

## Two tables you fill in by hand

Impact factor, JCR quartile and CAS partition live in licensed products with no
free redistributable source. Degree-thesis libraries defend against scraping.
So this ships the schema, the worklist and the join, and no crawler:

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
    --thesis-roster ./record/theses.csv
```

Two columns are **required** in the journal table: `版本来源` (which edition —
official, rising-star, folk, or JCR) and `数据获取日期`. Without them a
partition number is unfalsifiable two years from now. Where a journal has rows
from two editions, both are shown and neither wins; disagreements are listed.

The graduate roster is the more important of the two. Export the advisor's
supervised theses from a degree library and it produces the number PubMed
structurally cannot: **how many graduates have no indexed paper at all.** Add a
romanised-name column or the Chinese roster will not match the English bylines.
Note that people who enrolled and left before finishing are in no library
either — the roster narrows the missing group, it does not close it.

## Install

As a Claude Code skill:

```bash
git clone https://github.com/<owner>/check-your-advisor.git \
    ~/.claude/skills/check-your-advisor
```

Or run it directly as a CLI from anywhere — `scripts/run.py` is the single
entry point and needs no installation.

## Optional extras

Both degrade with a tested fallback; neither is required.

- **PyMuPDF** (AGPL-3.0, so deliberately not a hard dependency of an MIT
  project) lets PDF identity validation quarantine a wrong file. Without it
  every download is still checked for the `%PDF-` magic.
- **openpyxl** enables the `.xlsx` export. Without it the same data is written
  as a timestamped CSV.

## Tests

```bash
python tests/run_all.py
python tests/run_all.py --block-third-party
```

1925 assertions across 19 files. The second run installs an import hook that
blocks `requests`, `urllib3`, `pandas`, `numpy`, `matplotlib`, `fitz` and
`openpyxl` inside each test process. It is the only thing that keeps "no install
needed" true rather than merely claimed: exactly three assertions behave
differently without them, and all three are the cases that need a real PDF file.

## Known limitations

- The report is in English; the command-line logs are in Chinese. Not a decision
  anyone would defend — it is where the tool grew up, and unifying it is open.
- `cite` has no cache of its own. `--max-age-days N` reuses counts from the
  previous run's file, which is the workaround.
- Journal name matching falls back to a token heuristic for corpora harvested
  before ISSN capture existed. Re-harvest to get the exact join.

## License

MIT. See [LICENSE](LICENSE).

Citation counts come from OpenAlex, Semantic Scholar and Europe PMC, none of
which requires a key. Bibliographic records come from NCBI E-utilities. This
project ships no licensed data.
