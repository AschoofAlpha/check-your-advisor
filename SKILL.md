---
name: check-your-advisor
description: 查导师：把 PubMed 里的发表记录读成带分母的事实——谁在这个组、一作名额给了谁、新人等多久、人待多久、老板自己站在署名的哪个位置。不打分给结论，只给证据。Report what a principal investigator's publication record shows about being their student, from PubMed. Answers who is in the group, who gets the first-author slots, how long a newcomer waits for one, how long people stay, where the PI sits in their own bylines, and the output and venue pattern. Every count is printed with its denominator. Citation counts, an h-index and one composite score out of 100 are computed and written to disk, the score under a flat default weight table the report prints verbatim and the user can edit. Several corpora can be laid side by side and are ranked there by that score, each with a star band and, for adjacent pairs, one sentence saying which scored higher — every rank printed with the number of corpora it was taken among. A letter band sits beside the star band on the compare page, both being the same score coarsened on the same boundaries. Section 9 fits a slope over the yearly counts and prints the confidence interval and the number of points in the same sentence, refusing to fit at all below four points. Section 2 ranks the people it names by first-author slots, as a second table beside the roster rather than by reordering it, with the size of the roster printed beside the ranks. With `cite --percentile` it also asks OpenAlex where each citation count falls among every work sharing that paper's topic and publication year, and prints the position with the size of that cell beside it; papers it cannot place carry one of seven named reasons and never a low position. It still produces no position among the corpora a user happened to load, which is a different quantity and remains uncomputable. Impact factor, JCR quartile, CAS partition and the list of this advisor's graduates are joined from CSV tables the user fills in by hand and passes in — the schemas, the worklist and the join are here, the scraping of a subscription database is not. Nine verbs — `harvest` collects one named researcher's papers and separates them from same-name authors, and can also ask the free keyless OpenAlex API which author id this name belongs to (listing every candidate rather than picking one) and merge that author's OpenAlex records in as a second source, deduplicated on DOI then PMID then title and year, with every record marked with which sources hold it; `cite` fetches citation counts into their own dated file; `journal-worklist` writes out the journals this corpus actually uses so they can be looked up; `journal-risk` collects DOAJ indexing status and Crossref metadata coverage for those journals from keyless public APIs and prints them as dated statements, never as a rating — nothing here calls a journal predatory; `profile` turns that corpus into the report, with seven inline-SVG figures and an optional PDF copy produced by whatever converter the machine already has; `compare` puts several corpora on one page; `diff` compares two harvests of one advisor and splits every "in one and not the other" count on whether the search window moved between them, because a wider window looks exactly like growth and is not; `download` re-runs only the PDF stage over an existing corpus; `clean-cache` drops expired failed downloads from the cache.
triggers: check my advisor, evaluate a PI, what is this lab like, should I join this lab, advisor publication record, lab profile, PI profile, first-author slots, time to first author, compare two advisors, rank two advisors, PI citation counts, journal impact factor table, JCR quartile, CAS partition, advisor graduate list, 查导师, 选导师, 对比两个导师, 导师打分, 这个老板怎么样, 实验室发表记录, 期刊分区, 影响因子, 中科院分区, 毕业名单, 学位论文
tools: Read, Bash, Grep, Glob
model: inherit
---

# Check Your Advisor

You are choosing a PhD or master's advisor. You have a name, a lab page written
by the lab, and no way to check any of it. This reads what PubMed records about
that person and reports it back as facts with denominators.

It answers six questions from PubMed alone:

- who has appeared in this group
- who the first-author slots went to
- how long a newcomer waits before getting one
- how long people stay before they stop appearing
- where the PI sits in their own bylines
- how much comes out per year, and whether it goes to the same few journals

And two more once you supply a table it cannot fetch: what those journals are
rated in the edition you looked them up in, and how many people finished a
degree here without a single indexed paper — the one group every question above
is blind to.

## What it will and will not tell you

**It does not say whether the advisor is good.** That judgement stays yours.
Four different things stand behind that one sentence and they are not the same
kind of statement: one is a measurement, one is a decision, one is a file you
have not fetched yet, and one is a fact about the world that no file repairs.

**Computed, and written to disk.** Citation counts and an h-index: `cite`
fetches the counts from OpenAlex, Semantic Scholar or Europe PMC into their own
dated file and Section 15 computes on them. One composite score out of 100
(Section 16). And on a `compare` page, three things an earlier version of this
tool refused and that were unbanned deliberately: **a rank**, **a star band**,
and **one sentence saying which of two corpora scored higher**. Every number
prints beside the denominator it was computed over and the raw inputs that
produced it. The score's default weight table is flat — six components, each
counting the same — and the report prints it verbatim: no weighting is justified
by this data, and the honest answer to that is to show the weights and let you
set them, not to bury a set of magic numbers inside a function.

What the three new outputs carry with them is not decoration and should not be
dropped when you quote them:

- A rank is a position among **the corpora somebody chose to load**, and it moves
  when one is added or removed. Every ranked row prints the count it was ranked
  among, because first of two and first of nine are different claims. Ties share
  a rank and skip the next one, so ranks after a tie are not consecutive.
- A star band is the composite score coarsened into five equal bands whose edges
  are declared constants printed beside the result. The edges are round numbers
  on the 0-100 scale and were measured off nobody. One band is about one
  component's full swing, which is the coarsest reading the score supports.
- The directional sentence carries the difference in points and both component
  counts, and refuses itself outright when the two scores were not built from the
  same components under the same weight table. A four-component mean and a
  six-component mean share a scale, not a subject.

**Refused, whatever the data.** A position among the corpora on one page:
`compare` shows "3rd of the 5 you loaded" and stops there, because a position
inside a set the user assembled moves whenever an unrelated corpus is added or
dropped. Every normalisation anchor in `composite_score` is a declared constant
rather than a value measured off a group of researchers, so the composite score
has no percentile either and never will. Year-over-year percentage change: 3
papers to 5 is not "+67%".

**Unbanned in round four, with what each was traded for.** A *letter band*, which
is the star band relabelled on the same boundaries — the two are derived from one
table so they cannot drift apart. A *fitted slope* in Section 9: the original
objection stands, a handful of right-censored integer counts do not support one,
so the slope is printed only above four points and only with its confidence
interval and point count in the same sentence. An *ordering of people* in
Section 2, as a second table beside the roster rather than by sorting it, with
the size of the roster printed beside the ranks. And a *percentile* against an
external cell — every OpenAlex work sharing a paper's topic and year — which is
not the refused quantity above: that population is the same whether this run
loaded one corpus or nine.

**Absent until you supply the table.** Journal Impact Factor, JCR quartile and
CAS partition (Section 18); the list of this advisor's graduates (Section 17);
what other people have said about this advisor (Section 20). The machinery for
all three is built and idle: the tool defines the CSV schema, scans the corpus
to say which entries you actually need, joins what you hand back, and prints the
source and the date beside every number. It fetches none of
it and ships no crawler — those tables live in subscription databases that
forbid scraping, the degree libraries defend against it, and the pages carrying
student evaluations permit automated collection least of all. Without a file
those sections say "no table was supplied" and name the command that starts the
job, rather than leaving a blank column you have to interpret. See **Three
tables you fill in by hand** below.

**Never visible, from any file.** What the group is like to be in day to day.
Whether the PI is decent to work for. And the people who enrolled and left
before finishing — who are in no library at all, not CNKI, not Wanfang, not
PubMed. The graduate roster narrows the missing group; it does not close it, and
a report with a roster joined is still not counting everyone who joined. Section
20 does not close the first two either: an evaluation table reproduces what a
few people chose to write down, attributed and dated, and the tool computes
nothing over it — no sentiment, no average, no rating. Reading those statements
is not the same as measuring the thing they describe, and this report never
pretends otherwise.

Every report carries the middle two lists in full. Section 14 registers what is
not computed and says of each line whether it was refused, is waiting on a file,
or cannot be obtained at all — including the two lines that record what was
un-refused and when, because a register that deletes the entry for something now
being printed is a register nobody can audit. Section 16 prints two more under
their own headings: what the scoring function itself refuses, which is still
everything, since it is handed one corpus and can hold no position; and what the
ranking layer above it refuses, which is where the surviving bans now live. The
duplication is deliberate: an absence has to be tellable from an oversight, a
refusal from a missing input, and a reversal from a quiet deletion.

Read the output as evidence for your own judgement. A group where first-author
slots concentrate on two people is a fact; whether that is good depends on
whether you expect to be one of them. A score of 61.4 is one number about one
corpus under one weight table; "first of three" is a fact about three
directories on your disk. Neither becomes a statement about a career by being
printed.

## Run it

Nothing needs installing. Everything below imports only the standard library.

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/run.py" harvest \
    --author "Wang Wei" --affiliation "Peking Union Medical College" \
    --years-back 10 --output-dir ./record --no-download
```

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/run.py" cite --output-dir ./record
python "${CLAUDE_PLUGIN_ROOT}/scripts/run.py" journal-risk --output-dir ./record
```

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/run.py" profile \
    --pi-name "Wang Wei" --output-dir ./record \
    --journal-table ./record/journals.csv \
    --thesis-roster ./record/theses.csv \
    --evaluation-table ./record/evaluations.csv \
    --pdf
```

All three table flags are optional and none of them is a gate: drop them and
Sections 17, 18 and 20 print the reason they are empty and the command that
fills them, while Sections 1 to 16 are untouched. A file that will not load is
not a gate either — it is logged loudly, named, and the section says the same
thing. `cite` and `journal-risk` are optional in the same way: `profile` picks up
their newest file automatically, and says which command would produce one when
there is none.

`--pdf` writes a PDF beside the HTML using whatever converter this machine
already has — wkhtmltopdf, Chrome/Chromium/Edge headless, weasyprint or
LibreOffice, tried in that order. **It adds no dependency and installs nothing.**
With none of them present it prints one line per converter saying how to install
it and carries on; the HTML, Markdown and JSON outputs are byte-identical either
way. `--pdf-converter` picks one, `--pdf-converter-path` names a binary that is
installed but not on PATH — which on Windows is the normal state of both Chrome
and LibreOffice.

### A second source: OpenAlex

```bash
# Ask who publishes under this name. Prints every candidate; picks none unless
# there is exactly one.
python "${CLAUDE_PLUGIN_ROOT}/scripts/run.py" harvest \
    --author "Wang Wei" --affiliation "Peking Union Medical College" \
    --resolve-openalex --output-dir ./record --no-download
```

```bash
# Once you know which candidate is the right one: harvest PubMed and OpenAlex
# together and merge them.
python "${CLAUDE_PLUGIN_ROOT}/scripts/run.py" harvest \
    --author "Wang Wei" --openalex-author-id A5023888391 --openalex-works \
    --output-dir ./record --no-download
```

The merge deduplicates on DOI, then PMID, then normalised title plus year — the
first two are exact and the third is the only one that can be wrong, which is
why it is last. When both sources hold a paper the PubMed record wins, because
its record carries the affiliation strings and corresponding-author emails every
identity check downstream reads and OpenAlex's does not. Every record carries
`source` (whose metadata it holds) and `confirmed_by` (every source that holds
it), and Section 1 prints both denominators: the PubMed corpus and the merged
corpus are different numbers and are never printed as one.

`--email` is passed to OpenAlex as `mailto`. It is not a key — OpenAlex has
none — it only moves the request into a faster rate-limit pool, and it is
omitted when not configured.

Separate commands because the two network-bound steps — harvesting the papers
and fetching the citation counts — are slow, while the report is instant and
offline: harvest once, re-read the record many times.
`harvest` writes `papers_<timestamp>.json`; `profile` reads the most recent one
in `--output-dir` unless `--papers-json` says otherwise, and writes three files
from one run: `advisor_profile_<timestamp>.html` (the one to read), plus `.md`
and `.json` beside it for quoting and for machine consumption. `--pdf` adds a
fourth, on the same timestamp stem, if the machine has a converter for it.

The HTML carries seven figures, every one an inline SVG this package emits
itself: no matplotlib, no external JavaScript, no webfont, no network reference
of any kind, so the page renders from a thumb drive and prints as it appears.
The `<text>` in them is real text, so find-in-page reaches a person inside a
figure and a screen reader reads it, and every figure states its own denominator
inside the SVG because a chart gets screenshotted away from its caption. Two of
them are worth naming here:

- **Co-author clusters** (Section 19) draws the partition Section 19 already
  computed — one panel per cluster, one node per person recurring in it, a line
  where two share a byline, and the cluster's size printed as `k of N records`.
  Nothing is recomputed for the picture, so the picture and the prose beside it
  cannot disagree about who is in which group. A name that appears in two panels
  is ringed and named in the caption; that, not the number of clusters, is the
  thing worth reading, because it is what tells you whether the corpus is one
  person. No panel is ordered by any count.
- **Records per year by byline position** (Section 7) is one lane per position —
  first, last, sole, middle, not located — over a shared year axis, one square
  per record, with the partial and indexing-lag bins marked. Read down a column
  for that year's output and across the lanes for where the PI stood on it. On a
  corpus harvested with a byline-position filter it draws nothing and says why:
  the filter would be the metric.

Nothing in either figure is a rank, a rate or a fitted direction, and no percent
sign appears in any figure at any sample size.

`cite` is optional and sits between the two. It reads the same newest
`papers_<timestamp>.json` and writes `citations_<timestamp>.json` beside it,
stamped with the moment the counts were taken, without touching a byte of the
corpus: a corpus of bylines stays valid for years, a citation count is stale next
month, and merging them would give the first the shelf life of the second.
Sources are OpenAlex, then Semantic Scholar, then Europe PMC — first hit wins,
and which one answered is recorded per record, because the three disagree. None
of them takes a key; `--email` only buys a politer OpenAlex rate-limit pool, and
`--api-key` is accepted, sent nowhere, and warns that it did nothing. `profile`
then picks up the newest citation file in `--output-dir` on its own:
`--citations-json` names a different one, and `--no-citations` skips them and
makes the report say the flag did it, so a skipped lookup can never be misread as
a researcher with no citations. Without a citation file you lose Section 15 and
two of the six score components; Sections 1 to 13 are unchanged.

The score's weight table lives in the config file at `scoring.weights`, one
entry per component: `lead_slot_share`, `people_with_lead_slot`, `time_to_lead`,
`records_per_year`, `citation_h_index`, `citation_median`. All six default to
1.0, and only the ratios matter, so scaling the whole table changes nothing. Set
one to `0` to drop that component — that is the supported way to score a corpus
without its citation side, and the reason a negative weight raises rather than
quietly inverting a component's direction. An unknown component name fails before
the report is built instead of leaving the default table in place while you
believe you changed something. Below three components carrying both data and a
non-zero weight, the score is suppressed: the components still print, the
aggregate does not.

A refused report is still written as a page — it names the gate, the observed
values and the fix — and the process **exits 1**, so a script can branch on the
exit code alone. Beware of reading that status through a pipe: `python run.py
profile | tail` reports tail's status, not the interpreter's.

Drop `--no-download` on `harvest` to also race eight open-access sources for the
PDFs. `--api-key` raises NCBI's rate limit from 3/s to 10/s, and matters only
there: the citation sources take no key at all. The harvest paces itself to
whichever limit applies — one shared minimum interval for every esearch page and
every efetch batch — so a long harvest slows down rather than collecting 429s.

`harvest` pages through the whole result set: `retmax` (default 500) is the size
of one esearch page, and `max_records` (default 10000) is the total it will
collect. 10000 is NCBI's own ceiling — esearch cannot return more than the first
10000 records of a PubMed query, whatever retstart asks for — so raising it past
that buys nothing; narrow the query or split it by year instead. When a name
matches more than the budget, the harvest takes the first `max_records` and the
report prints how many of how many were retrieved.

Two maintenance verbs exist for when the PDF stage is the only thing you want to
redo. `download` re-runs that stage alone over an existing `papers_*.json`,
skipping the PubMed fetch entirely, and `clean-cache` drops failed-download
records older than `--max-age-days` so those sources get retried — successful
downloads are never touched. Neither reads or writes a report.

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/run.py" download --output-dir ./record
python "${CLAUDE_PLUGIN_ROOT}/scripts/run.py" clean-cache --output-dir ./record \
    --max-age-days 30
```

### Several corpora on one page, ranked

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/run.py" compare ./record-a ./record-b \
    --output-dir ./comparison
```

Each positional argument is a corpus directory holding its own
`papers_<timestamp>.json`. `--output-dir` is where the comparison lands, not a
corpus to read — it defaults to the current directory, which is the one flag here
worth reading twice. `--config` and `--pi-name` are repeatable: give one and
every corpus uses it, or one per directory in order. `--no-citations` applies to
every corpus at once and marks both citation components as having no data.
Output is `advisor_compare_<timestamp>.md` plus a `.json` record; there is no
HTML page for a comparison.

The page carries a rank column, a star column and, for each adjacent pair in
rank order, one sentence about which scored higher — N-1 sentences, not the
N(N-1)/2 a full matrix would give. `--order-by` chooses the **column** order,
`score` (highest first, the default) or `label` (lexicographic, which is what
round one did). The ranks are computed and printed either way, because a rank is
a property of the set and not of the column order; the page states which key
ordered the columns, in words.

Read three things off the rank column before quoting it. It is standard
competition ranking on the composite score — ties share a rank and skip the
next, so `1, 2, 2, 4` is correct output and not a bug, and ties are decided on
the score as printed to one decimal so two rows showing 61.4 can never carry
different ranks. Every row prints how many corpora it was ranked among. And a
corpus that took no position — its report was refused at a gate, or its score
was suppressed for too few components — keeps its row with the reason, and is
neither scored zero nor placed last, because last is a position too. Below two
scored corpora nothing is ranked at all: there is no "first" among one.

All corpora are scored under one shared weight table, and `compare` refuses
rather than reconciling configs that disagree about the weights — a column of
scores computed under different weights is not a comparison of anything. A
corpus whose own gate fired keeps its row and shows the gate where its numbers
would be, because dropping it silently would leave you comparing three corpora
believing you were comparing four. Differences between two component values are
printed, because a difference is arithmetic. The one-sentence verdict is
narrower than the rank column on purpose: ranks are still produced when the
corpora scored on unlike component sets, with the mismatch flagged and each
row's component count in view, but the sentence about two named people has
nowhere to put that caveat, so it is not written at all in that case.

`compare` joins no journal table and no thesis roster. Those are per-corpus and
live in each corpus's own `profile` report.

## Three tables you fill in by hand

Journal partitions and degree theses live behind subscriptions that forbid
scraping and defend against it; student evaluations live on forums and review
pages that permit it least of all. There is no crawler in this package and none
is planned. The division of labour is fixed and it is the same for all three
tables: the tool **defines the schema, says which entries this corpus actually
needs, joins what you hand back, and prints where every number came from and
when**. Going and looking the entries up is yours.

What *is* fetched, and the line between the two: citation counts (`cite`) and
journal risk signals (`journal-risk`) come from keyless public JSON APIs whose
terms permit exactly this — a documented API call, not a scrape of a vendor page.
Both write their own dated files, both record which endpoint answered and when,
and neither is ever written back into a table you filled in. A value you typed
and a value an API returned last Tuesday have to stay distinguishable in the
report, so they are never merged into one column.

All three paths can also live in the config file instead of on the command line,
at `journals.table_path`, `theses.roster_path` and `evaluations.table_path`. The
flag wins where both exist.

### Journal metrics — `journal-worklist`, then `profile --journal-table`

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/run.py" journal-worklist --output-dir ./record
```

This makes no network request. It scans the corpus and writes
`journal_worklist_<timestamp>.csv` — every journal this corpus actually used,
with its ISSN where PubMed carried one and the number of papers it holds,
ordered by that count so the highest-leverage lookups come first. That ordering
is the whole reason the design is affordable: there are tens of thousands of
indexed journals and one five-year corpus uses twenty-odd. `--out` names a
different destination, `--papers-json` a different corpus file. The CSV it writes
*is* the table's template, so filling the blank columns in and passing it to
`profile --journal-table` needs no conversion step, and journals looked up once
stay in the file and are reused by the next corpus.

Names come out exactly as PubMed recorded them, which means one journal can
appear twice — once as its full title and once as its abbreviation. They are not
merged, because merging them would put a name in your template that no vendor
page will match; instead the two rows share a `疑似同刊组` number, so you look
the journal up once and fill both rows from the same lookup.

**Three traps that have already cost real time, all of them in the source data
rather than in this tool:**

- **LetPub's search-results list page shows the 民间版 (folk) partition by
  default.** A number copied off that page and filed as "the CAS partition" is
  not the official one. Click into the journal's detail page before you write
  anything down.
- **ablesci (科研通) labels 官方版 and 新锐版 as separate claims**, which is
  precisely what makes it usable as a cross-check against LetPub.
- **Clarivate's Master Journal List is free and carries only SCIE/SSCI indexing
  status** — no impact factor, no quartile. The full JCR needs an institutional
  seat, and one of those is not something this tool can conjure.

So `版本来源` (source edition) and `数据获取日期` (retrieval date) are **required
columns**, alongside `ISSN` and `刊名`; a file missing either is refused at load
with the column named, rather than loaded into a report whose partition column is
silently unsourced. Accepted editions are `官方版` / `新锐版` / `民间版` / `JCR`,
and unrecognised wording is kept as written rather than rewritten into one of
them. Where you find two editions disagreeing about one journal, **write two
rows** and label each — both are carried through the join, the fields they
disagree on are listed as a disagreement, and no edition is chosen for you.
`是否预警` (warning-list status) and `预警等级` are their own columns, because a
warning-list entry is a dated statement by that list's publisher and not a
property of the journal; a blank there means "not looked up", never "not on the
list".

Matching is ISSN first, then the exact folded journal name, then an abbreviation
heuristic for the "J Hepatol" versus "Journal of Hepatology" case — and which
route produced each match is counted and printed, because an ISSN match is a
fact and an abbreviation match is a guess. A name that plausibly fits two
journals in your table is left unmatched with both candidates listed. Section 18
prints coverage against both denominators, papers and distinct journals, since a
table can cover most papers while missing most journals and those two facts point
at different work.

### Public risk signals — `journal-risk`, then `profile`

Three things about a journal *are* free to look up, from keyless public APIs, and
this verb collects them:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/run.py" journal-risk --output-dir ./record
```

It reads DOAJ indexing status, Crossref metadata deposit coverage and OpenAlex's
source record for every journal in the corpus that carries an ISSN, and writes
`journal_risk_<timestamp>.json` — its own dated file, one `fetched_at` per
record, nothing written back into the corpus and nothing written into the CSV you
filled in. `profile` picks up the newest one automatically and Section 18 prints
each statement beside the endpoint that returned it and the day it was read.
`--journal-risk-json` names a different file; `--no-journal-risk` skips it and
the section says the flag is why.

**Nothing here calls any journal predatory, and you should not either on this
evidence.** "Predatory" is an accusation about a publisher's conduct. None of
these three APIs makes it, this tool does not make it, and no count of these
signals is turned into a grade, a tier, a score, a letter or a colour at any
number of them. They contribute nothing to the composite score. What Section 18
prints is a list of statements — `未被 DOAJ 收录`, `Crossref 元数据缺失 3 项（共查
10 项）` — each with its source and its date, and the reading is yours.

Two of them are misread almost every time, so the section says the opposite out
loud beside them:

- **Not being in DOAJ is not a finding.** DOAJ indexes open-access journals that
  applied to it. Journal of Hepatology is not in DOAJ and never will be; nor is
  most of the subscription literature in medicine. Reading absence as a warning
  flags a large part of the ordinary field.
- **A Crossref coverage field reading zero is not a finding either.** It measures
  what a publisher deposits. Journal of Hepatology deposits no abstracts for its
  backfile; run this against it and you get "3 of 10 tracked fields at zero" for
  a journal nobody questions. Only current-content fields are counted, the list
  of them is fixed and printed, and the count always travels as N of M.

All three sources are queried every time and none of them wins. They answer
different questions and disagree routinely — OpenAlex keeps its own copy of the
DOAJ flag and it can lag DOAJ's live answer, and its Scopus field is `null` for a
great many real journals, which is a third value and is never printed as "not
indexed". Where two disagree, both lines are printed with their dates.

**The 中科院国际期刊预警名单 is not fetched and will not be.** It is published once
a year as a login-walled page and a PDF with no JSON and no CSV endpoint. It
stays in the hand-filled `是否预警` / `预警等级` columns above, where a person
copies it once a year — one list, a couple of dozen journals, cheaper to copy
than to maintain a scraper for, and scraping it is not permitted anyway. Beall's
list and its mirrors are the same case with no publisher behind them. A blank
`是否预警` cell still means nobody checked, and no signal in this block fills it
in.

`journal-worklist --risk-json` copies the signal *names* and the day they were
read into two extra template columns, so the person filling in the partition
columns can see which journals to look at harder. The full sentences stay in the
JSON and in the report; a CSV cell holding five paragraphs breaks every
spreadsheet it is opened in.

### Graduates — `profile --thesis-roster`

Export this advisor's supervised theses from CNKI or 万方 by hand and pass the
CSV. Required columns: `导师姓名`, `学生姓名`, `学位类型`, `毕业年`, `库来源`,
`导出日期`. Optional but worth having: `入学年`, `论文题名`, `培养单位`,
`导师姓名拼音`.

**Add `学生姓名拼音`.** It is optional in the schema and decisive in practice:
nothing in the standard library converts 张三 into "Zhang San", and this tool
will not guess. Without that column every graduate lands in "needs manual
review", the count of graduates with no PubMed paper is suppressed entirely, and
you get a section that cost you an export and answers nothing.

This is the only part of the report that sees a denominator PubMed structurally
cannot: everyone who finished a degree here, including the people who published
nothing at all and are therefore missing from every other number in the report.
It is reported as three counts — graduates on record, graduates present in the
PubMed corpus, graduates with no paper in it — plus a fourth for pairs the name
match could not settle, which are listed by name for a human rather than pushed
into a bucket. That is why the answer comes as a **floor and a ceiling** and not
as one number.

Two limits travel with it and must travel with any quote of it. Coverage is only
whatever export you actually ran — those libraries, those institutions, those
years, and only theses deposited and released. And **a thesis exists only for
someone who finished**: anyone who enrolled with this advisor and then withdrew,
transferred, was dismissed, changed advisor or has not yet graduated is in no
library at all. This denominator is better than PubMed's and it is still not
everyone who joined. Nothing here is a graduation rate or an attrition rate;
neither has an observable denominator.

### Student evaluations — `profile --evaluation-table`

Collect the statements yourself, from whatever public pages carry them, and pass
the CSV. Required columns: `导师姓名`, `评价来源`, `数据获取日期`, and at least
one of `评价内容` or `维度评分`. Optional: `学生身份`, `评价年份`, `原文链接`,
`导师姓名拼音`. A file missing a required column is refused at load with the
column named and the headers it did find printed beside it — the same rule the
journal table's `版本来源` lives by, for the same reason: an unattributed,
undated sentence about a named person cannot be checked by anyone later,
including the person who wrote it down.

This is the one table with no worklist command, and there will not be one.
`journal-worklist` can write the list of journals because the corpus already
names them; nothing in a corpus names the places people talk about an advisor.
The searching, reading and typing are entirely yours, and the tool fetches
nothing.

Section 20 prints what comes back: how many statements were attributed to this
advisor out of the usable rows in the file, how many distinct sources they came
from with the per-source counts, the span of years the statements themselves
cover, the span of days you read the pages on, and then every statement in file
order with its source and its retrieval date attached.

**No sentiment analysis, no aggregate, no score.** Nothing here is labelled
positive or negative, nothing is averaged, nothing is rated, and no row reaches
the composite score in Section 16 at any weight, including zero. That is not an
oversight to be filled in later. These are a handful of statements by people who
chose to write something down, so any average over them measures who bothered to
post; a number computed from them would carry the authority of a measurement and
would be quoted long after the text it came from was forgotten. The statements
are reproduced, attributed and dated, and the judgement is yours.

Three limits travel with them and must travel with any quote. Nobody is sampled:
people post after an experience strong enough to be worth typing up, in either
direction, and there is no observable denominator of students who could have
posted. Authorship is unverifiable: nothing in a post proves the writer was ever
this advisor's student, or that two posts are two people. And an advisor with
twenty years of students is not the same supervisor throughout — `评价年份` is
optional because most sources do not carry it, and rows without it cannot be
placed in time at all, so the printed span covers only the rows that can.

## The corpus decides everything — configure identity first

Every number in the report is computed over the papers `harvest` kept. If
papers by a different person with the same name get in, the roster, the
time-to-first-author and the turnover figures are all wrong, and wrong in a way
that looks perfectly normal on the page.

This is not hypothetical. A real run for one Chinese surgeon returned 114
records that included a neurologist at a different hospital with the same name;
institution keywords alone could not separate them, because both institutions
carry the same university's name.

So give `harvest` at least one unique identifier. In descending order of
strength:

| Flag | Strength |
|---|---|
| `--orcid 0000-0002-...` | Strongest. One is worth all the rest, and it also enters the search as `[auid]` |
| `--email-domain your-university.edu.cn` | The corresponding author's address. Repeatable |
| `--affiliation-keyword "..."` | Weakest — it fails on same-name colleagues inside one university system. Repeatable, and defaults to whatever `--affiliation` says |

`--require-affiliation` turns a non-matching institution from "kept and marked
unverified" into "rejected".

### Letting OpenAlex propose the identity

`--resolve-openalex` asks the free, keyless OpenAlex authors API who publishes
under this name, optionally narrowed by `--affiliation`, and prints each
candidate with its author id, its ORCID if OpenAlex holds one, its institution
history and its works count.

**One candidate is adopted. Two or more are listed and none is adopted.** Pick
the one you recognise and re-run with `--openalex-author-id A5023888391`.
Choosing the most productive candidate automatically would decide an identity
question on a proxy, which is the same error as accepting a bare name match, and
it would leave no sign in the report that a choice had been made.

An adopted id is recorded as its own kind of evidence, beside the three above
and never folded into them: those three are the researcher's own assertion,
while an author id is OpenAlex's clustering — right most of the time and wrong
in a way nothing on the page can show. Section 1 prints the id, the query, the
source and the date it was fetched. If OpenAlex holds an ORCID for the adopted
candidate the log says so and stops there; it is not written in as if you had
supplied it.

Measured difference on a real run of one Chinese surgeon, five years, same
search either way: with no evidence, 186 records came back and every one was
marked unverified. With ORCID + institution + email domain, 210 records came
back and **50 were kept, 160 rejected** — the rejected ones being a county
hospital endoscopist, a Beijing aging consortium, a Shanghai emergency
physician, a mathematics department and a nursing college, all sharing the name.

If the harvest recorded no evidence at all, the report still renders — with a
warning in a box at the top of Sections 0, 1 and 19 naming the condition, the
observed values and the fix, with Section 14 recording that this used to be a
refusal, and with the process exiting 1. See "It warns rather than refusing"
below for why that changed.

**The evidence is recorded into `papers_<timestamp>.json` and the gate is
decided from that**, not from the config in force when the report runs. Passing
the flags to `harvest` is enough; you do not repeat them on `profile`. This also
closes the dangerous direction: a name-only corpus cannot be certified by a
config file it was never harvested with.

### What G3 does not catch, and Section 19 does

G3 is raised for a corpus that no configured evidence actually *reached* —
nothing set, or something set that matched not one record, or an OpenAlex id
that reached too little of it. The dangerous case still
raises nothing at all: weak evidence that does reach the records — an affiliation keyword that half a province matches — produces a
complete, normal-looking report about several people. A real run for one Chinese
surgeon, keyed on a province name rather than the full institution, returned 28
records spanning gastrointestinal surgery, analytical chemistry, structural
biology, soil microbiology and machine learning. It raised no warning, passed every gate and
scored 78.2 out of 100.

Section 19 is the check for that. It removes the PI, who is on every record by
construction, and asks which records are still tied together by a shared
co-author: one person's output is held together by the people they work with,
and two people sharing a name have no reason to share anyone else. That corpus
split into clusters of 6 in oncology, 5 in analytical chemistry, 3 in structural
biology and 2 in soil microbiology, each naming the collaborators that held it
together — five research groups, unmistakable on sight.

**It is not a gate and applies no threshold.** Counted on three real corpora —
one known to hold five researchers, two better filtered — the cluster counts
were 15, 16 and 10, and the largest cluster held 21%, 35% and 42%. The count
separates none of them, because every real researcher accumulates one-off
collaborators and each becomes a single-record cluster. A cut-off guessed from
numbers like those would refuse real broad-ranging researchers, which is a worse
failure than the one it prevents. What actually separates one person from
several is whether the clusters' subject matter is related, and this toolkit
classifies no subjects, so the clusters and their journals are printed and the
reading is yours.

If they look like different people, nothing else in the report is worth quoting.
Re-harvest with `--orcid`.

## It warns rather than refusing

Three gates are checked before anything is computed. A fired gate produces a
refusal page naming the gate, the observed values and the fix, and nothing else.

| Gate | Fires when | Why nothing can be reported |
|---|---|---|
| G4 | corpus has no structured author records | There is nothing to compute over; the report cannot be built from the spreadsheet export, so re-run harvest |
| G5 | 0 papers remain after exclusions | Nothing to report |
| G6 | the corpus records more verified records than it fetched | The two PubMed counts contradict each other, so every denominator on the page is wrong by an unknown amount. Written by a version that overwrote `verified` with the merged corpus size; re-run harvest over the same output directory |

Three more conditions used to sit here as gates and are now **warnings**. They
are the most important things this report can say about itself, and refusing
over them destroyed sixteen sections of computed fact to prevent one claim —
including Section 19, whose entire job is to show a reader whether the corpus
holds several people, and which a reader of a refusal page never got to see.

| Warning | Raised when | What it costs |
|---|---|---|
| G1 | esearch matched more records than the harvest retrieved | The corpus is part of what the query found; every count below is a floor, and nothing on the page can say whether the missing records look like the ones that arrived |
| G2 | no paper passed identity verification | The corpus is "everyone publishing under this name" and may describe several people |
| G3 | no identity evidence reached enough of the corpus | A name-only corpus blends several people for any common surname |

A fourth warning was never a gate and is listed apart for that reason — Section
14 files it under its own heading rather than under "downgraded", because saying
it used to refuse reports would be untrue.

| Warning | Raised when | What it costs |
|---|---|---|
| G7 | the target name is on no byline in the corpus | No section is about the person named. The roster removed no target researcher and counts one person too many, Section 7 has no byline slot to report, and every phrase reading "the target researcher" names somebody the corpus does not contain |

A raised warning prints in a box at the top of the sections it affects — the
condition, the observed values, the fix. G2 and G3 land on Sections 0, 1 and 19;
G1 lands on Sections 0, 1 and 9, the per-year count a partial harvest deflates
most visibly; G7 lands on Sections 0, 1, 2 and 7, the two whose opening sentences
it falsifies. Section 14 records that G1, G2 and G3 each used to refuse, and
**the process still exits 1** for all four, so anything scripted against the old
refusal behaves as it did. Two things a warning does not do: it does not certify
the corpus, and it does not let it take a position on a `compare` page. Such a
corpus keeps its row, keeps its score inside that row, and holds no rank.

G7 covers a question none of the corpus's other readers asks. The evidence
histogram in Section 1 is derived from each record's role string and the OpenAlex
share is a record-level id count; neither consults the target name. Profile a
corpus under a mistyped `--pi-name` and both went on printing `N of N` while
`roles.resolve_pi` rejected every record — the run exited 0 with nothing anywhere
saying the name had not been found. Section 1 now prints `target name on records:
N of M` on **every** run, warning or not, and raises G7 when N is 0. No share
boundary is applied to it: the ratio is printed so a corpus the name reached a
minority of is visible, and inventing a second arbitrary constant beside
`min_openalex_record_share` would recreate the thing that constant exists to
remove. The fix is to correct the name and re-run `profile`; nothing needs
re-harvesting, because the corpus is unchanged and only the name it was read
against was wrong.

G1 took a detour worth knowing about, because you may have notes from either
side of it. It was deleted outright when the harvest learned to page: a shortfall
between the esearch count and the PMIDs retrieved stopped meaning "cut off at
retmax" and started meaning the `max_records` budget stopped a very common name,
or the pages repeated a PMID, or PubMed's own hit count moved mid-harvest. None
of those is worth withholding a report — that much still holds, which is why G1
is not a gate. But deleting it left no mark on the page at all: a corpus 40%
retrieved rendered clean, exited 0, and sat on a `compare` page ranked beside
corpora retrieved in full. It is back at warning strength. Section 1 still prints
`retrieved N of M records esearch matched` and still says every count below is a
floor; Section 14's reversal register records the round trip.

G3's boundary is a share, not a yes/no. An OpenAlex author id counts as identity
evidence for the corpus only once it reaches at least
`identity_evidence.min_openalex_record_share` of the records (default 0.5), and
Section 1 prints the ratio measured and the boundary in effect side by side. The
old test was "does any record carry it", which is a threshold of one record that
nobody declared: five bare name matches plus one merged OpenAlex record silenced
the warning outright.

G3 is also decided on what the evidence reached rather than on what was
configured, and Section 14's register records the reversal. An `orcid` in the
config that matches no byline in the corpus used to clear the warning and exit
0, while the same corpus with the field blank warned and exited 1 — the same
evidence, none, judged two ways. Such corpora now warn, so expect more runs to
exit 1 than before. The banner says which of the three happened — nothing
configured, configured and matching nothing, or an id below the share — and the
reach it is talking about is printed on the same line as `identity_evidence_on_records`.

Below all of this there are suppression floors: an aggregate computed over too
few people is replaced by a plate showing the actual n rather than a number that
looks solid. Those floors are part of the specification and are not exposed as
options.

## Reporting to the user

1. If a gate fired, report the gate and its fix. Do not paraphrase the numbers
   from a refused report — there are none. If an identity **warning** was
   raised, the numbers exist and may be quoted, but every quote has to carry the
   warning with it: the corpus may describe more than one researcher, and no
   count below is certified to be about the person named.
2. Lead with the roster and the first-author distribution; those are what the
   question "what is it like to be their student" actually turns on.
3. Quote every count with its denominator, exactly as the report prints it.
   "3 of 11" and "27%" are not interchangeable when n is 11.
4. Say which identity evidence was configured. A report built on affiliation
   keywords alone deserves an explicit caveat even when no gate fired.
5. Quote the score with the weight table that produced it and with its
   component count. "61.4 out of 100 under the default flat table, from five of
   six components" is the claim; "scores well" is not, and neither is a bare
   61.4.
6. Quote citation numbers with their coverage and their fetch date. Where
   coverage is partial, say that the total and the h-index are floors — and that
   this h-index is bounded by the search window, so it is not the career figure
   a reader will assume.
7. A rank may be quoted, and only with the count it was taken among and what
   that set is: "first of the three corpora you loaded", never "ranked first" or
   "top-ranked". Say when a rank is shared with another corpus. A star band may
   be quoted with the score it coarsens. The directional sentence may be quoted
   as the page wrote it, and not upgraded: "A scored 4.2 points higher than B on
   the same five components" is the claim, "A is the better advisor" is not.
8. If the page refused a comparison — unlike component sets, unlike weight
   tables, a gate, a suppressed score — report the refusal and its reason. Do
   not substitute the subtraction yourself; it was withheld deliberately.
9. If asked for a percentile, a letter grade or a trend, say the tool does not
   produce it and why: a percentile needs a reference population that does not
   exist here, letter grades are a deliberate omission next to the stars that do
   exist, and five right-censored integer counts do not support a slope.
10. If asked about impact factor, quartile or partition, say the tool joins them
    from a table the user fills in by hand and never fetches them, and point at
    `journal-worklist`. "The tool cannot show it" and "nobody has filled the
    table in yet" are different answers, and only the second is true here. When
    quoting a joined number, carry its edition and retrieval date with it — a
    partition with neither is unverifiable a year later.
11. When quoting the graduate section, quote the floor and the ceiling and say
    that everyone who left before finishing is in neither. Never restate it as a
    graduation rate or an attrition rate.
12. Never turn the report into a verdict. If asked "is this a good advisor",
    give the facts and say the judgement is theirs. A rank on a page of two
    directories is not that judgement arriving by another route.

## Optional extras

Both degrade with a tested fallback; neither is required.

- **PyMuPDF** (AGPL-3.0, so deliberately not a hard dependency of an MIT
  project) lets PDF identity validation *reject* a wrong file into
  `pdfs/suspect/`. Without it every download is still checked for the `%PDF-`
  magic and still recorded with a per-paper reason, but nothing is ever
  quarantined — the check reports "identity check skipped" and keeps the file.
- **openpyxl** enables the `.xlsx` export. Without it the same data is written
  as a timestamped CSV.
- **An HTML-to-PDF converter** — wkhtmltopdf, Chrome/Chromium/Edge, weasyprint or
  LibreOffice — lets `profile --pdf` write a PDF beside the HTML. None of them is
  imported: this package runs a program that is already on the machine and never
  fetches an installer. Without one, `--pdf` prints how to install each and the
  HTML output is unchanged, which is the point — the HTML *is* the deliverable
  and a PDF is a second copy of it. The standard library cannot lay out a page,
  and adding reportlab or weasyprint as a hard dependency would have broken "no
  install needed" for everyone in order to give some people a second file format.

## Tests

```bash
python "${CLAUDE_PLUGIN_ROOT}/tests/run_all.py"
python "${CLAUDE_PLUGIN_ROOT}/tests/run_all.py" --block-third-party
```

The second run installs an import hook that blocks `requests`, `urllib3`,
`pandas`, `numpy`, `matplotlib`, `fitz` and `openpyxl` inside each test process.
It is the only thing that keeps "no install needed" true rather than merely
claimed: exactly three assertions behave differently with every third-party
package unavailable, and all three are the cases that need a real PDF file,
which skip themselves. Nothing else in the suite changes between the two runs.
