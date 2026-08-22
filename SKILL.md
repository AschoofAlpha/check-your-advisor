---
name: check-your-advisor
description: 查导师：把 PubMed 里的发表记录读成带分母的事实——谁在这个组、一作名额给了谁、新人等多久、人待多久、老板自己站在署名的哪个位置。不打分给结论，只给证据。Report what a principal investigator's publication record shows about being their student, from PubMed. Answers who is in the group, who gets the first-author slots, how long a newcomer waits for one, how long people stay, where the PI sits in their own bylines, and the output and venue pattern. Every count is printed with its denominator. Citation counts, an h-index and one composite score out of 100 are computed and written to disk, the score under a flat default weight table the report prints verbatim and the user can edit. Several corpora can be laid side by side and are ranked there by that score, each with a star band and, for adjacent pairs, one sentence saying which scored higher — every rank printed with the number of corpora it was taken among. It still produces no percentile or quantile position, for want of any reference population to compute one against, no letter grade, and no fitted trend; and it never orders people, only corpora. Impact factor, JCR quartile, CAS partition and the list of this advisor's graduates are joined from CSV tables the user fills in by hand and passes in — the schemas, the worklist and the join are here, the scraping of a subscription database is not. Seven verbs — `harvest` collects one named researcher's papers and separates them from same-name authors; `cite` fetches citation counts into their own dated file; `journal-worklist` writes out the journals this corpus actually uses so they can be looked up; `profile` turns that corpus into the report; `compare` puts several corpora on one page; `download` re-runs only the PDF stage over an existing corpus; `clean-cache` drops expired failed downloads from the cache.
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

**Refused, whatever the data.** A percentile or quantile position: nothing here
holds a reference population, every normalisation anchor is a declared constant
rather than a value measured off a group of researchers, and no calculation sees
more than the few corpora on one page — so a percentile is uncomputable here,
not merely withheld. A letter grade: stars are produced and letters are not, and
that split between two coarsenings of one number is a decision, not an
inconsistency waiting to be tidied. Fitted trends, slopes and year-over-year
percentage change: a handful of right-censored integer counts do not support a
slope, and 3 papers to 5 is not "+67%". And no ordering of **people**, anywhere:
no roster in any report is sorted by a count, and what `compare` ranks is
corpora, which are files.

**Absent until you supply the table.** Journal Impact Factor, JCR quartile and
CAS partition (Section 18); the list of this advisor's graduates (Section 17).
The machinery for both is built and idle: the tool defines the CSV schema, scans
the corpus to say which entries you actually need, joins what you hand back, and
prints the source and the date beside every number. It fetches none of
it and ships no crawler — those tables live in subscription databases that
forbid scraping, and the degree libraries defend against it. Without a file
those sections say "no table was supplied" and name the command that starts the
job, rather than leaving a blank column you have to interpret. See **Two tables
you fill in by hand** below.

**Never visible, from any file.** What the group is like to be in day to day.
Whether the PI is decent to work for. And the people who enrolled and left
before finishing — who are in no library at all, not CNKI, not Wanfang, not
PubMed. The graduate roster narrows the missing group; it does not close it, and
a report with a roster joined is still not counting everyone who joined.

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
```

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/run.py" profile \
    --pi-name "Wang Wei" --output-dir ./record \
    --journal-table ./record/journals.csv \
    --thesis-roster ./record/theses.csv
```

Both table flags are optional and neither is a gate: drop them and Sections 17
and 18 print the reason they are empty and the command that fills them, while
Sections 1 to 16 are untouched.

Separate commands because the two network-bound steps — harvesting the papers
and fetching the citation counts — are slow, while the report is instant and
offline: harvest once, re-read the record many times.
`harvest` writes `papers_<timestamp>.json`; `profile` reads the most recent one
in `--output-dir` unless `--papers-json` says otherwise, and writes three files
from one run: `advisor_profile_<timestamp>.html` (the one to read), plus `.md`
and `.json` beside it for quoting and for machine consumption.

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
there: the citation sources take no key at all.

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

## Two tables you fill in by hand

Journal partitions and degree theses live behind subscriptions that forbid
scraping and defend against it. There is no crawler in this package and none is
planned. The division of labour is fixed and it is the same for both tables: the
tool **defines the schema, says which entries this corpus actually needs, joins
what you hand back, and prints where every number came from and when**. Going
and looking the entries up is yours.

Both paths can also live in the config file instead of on the command line, at
`journals.table_path` and `theses.roster_path`. The flag wins where both exist.

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

Measured difference on a real run of one Chinese surgeon, five years, same
search either way: with no evidence, 186 records came back and every one was
marked unverified. With ORCID + institution + email domain, 210 records came
back and **50 were kept, 160 rejected** — the rejected ones being a county
hospital endoscopist, a Beijing aging consortium, a Shanghai emergency
physician, a mathematics department and a nursing college, all sharing the name.

The report refuses to render if the harvest recorded no evidence (gate G3).

**The evidence is recorded into `papers_<timestamp>.json` and the gate is
decided from that**, not from the config in force when the report runs. Passing
the flags to `harvest` is enough; you do not repeat them on `profile`. This also
closes the dangerous direction: a name-only corpus cannot be certified by a
config file it was never harvested with.

### What G3 does not catch, and Section 19 does

G3 refuses a corpus harvested with *no* evidence. The dangerous case passes it:
weak evidence — an affiliation keyword that half a province matches — produces a
complete, normal-looking report about several people. A real run for one Chinese
surgeon, keyed on a province name rather than the full institution, returned 28
records spanning gastrointestinal surgery, analytical chemistry, structural
biology, soil microbiology and machine learning. It passed every gate and scored
78.2 out of 100.

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

## It refuses rather than degrading

Five gates are checked before anything is computed. A fired gate produces a
refusal page naming the gate, the observed values, and the fix — not a report
with a warning at the top, because a warning gets scrolled past.

| Gate | Fires when | Why nothing can be reported |
|---|---|---|
| G1 | esearch matched more records than were retrieved | Every count would be wrong by an unbounded amount. Raise retmax, cut `years_back`, or add affiliation keywords, then re-harvest |
| G2 | no paper passed identity verification | The corpus is "everyone sharing this name" and describes several people |
| G3 | no identity evidence configured at all | A name-only corpus blends several people for any common surname |
| G4 | corpus has no structured author records | The report cannot be built from the spreadsheet export; re-run harvest |
| G5 | 0 papers remain after exclusions | Nothing to report |

Below the gates there are suppression floors: an aggregate computed over too few
people is replaced by a plate showing the actual n rather than a number that
looks solid. Those floors are part of the specification and are not exposed as
options.

## Reporting to the user

1. If a gate fired, report the gate and its fix. Do not paraphrase the numbers
   from a refused report — there are none.
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
