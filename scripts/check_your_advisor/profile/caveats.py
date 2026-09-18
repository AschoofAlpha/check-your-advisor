"""
The verbatim caveat strings, and the register of what this report does not compute.

Sections 8 and 10 of docs/profile-metrics-spec.md. These live as module
constants and are never paraphrased at the call site: they are the part of the
report that says what the numbers cannot mean, and a paraphrase is how that
part quietly weakens.

Four kinds of absence, and they are not interchangeable
------------------------------------------------------
The register below used to hold one list under one heading. It now has to keep
four cases apart, because a reader who cannot tell them apart cannot tell which
of them would look different tomorrow:

- **Computed.** Citation counts, the h-index and one composite score out of 100
  under an exposed weight table are produced and written to disk, and on a
  `compare` page so are a rank among the corpora loaded, a star band and a
  sentence saying which of two corpora scored higher. They are not in this
  register except where a line says outright that it moved.
- **Refused.** A percentile or quantile position, a letter grade, and any fitted
  trend or year-over-year percentage change. A better data source would not
  change these answers, which is what makes them refusals — and in the
  percentile's case there is no data source to improve, because nothing here
  holds a reference population to sit inside.
- **Supplied by hand, or not at all.** Journal Impact Factor, JCR quartile, CAS
  partition and the degree-thesis roster are joined from files the user fills in
  and passes on the command line. This toolkit defines their columns, says which
  entries a corpus actually needs, joins what comes back and prints where it came
  from. It fetches none of them and ships no crawler. Until a file is supplied
  the numbers are absent, and that absence is a missing input, never a verdict.
- **Unobtainable.** Field-normalised citation impact, and the population
  "everyone who ever joined this group". No file the user could go and find
  supplies either one, which is what separates them from the case above.

English only. Translation is out of scope.
"""

from __future__ import annotations

from typing import Any

CAVEATS: dict[str, str] = {
    "CAV-00": (
        "This report describes publication metadata, and nothing else. PubMed contains only people "
        "who published: anyone who joined this lab and left without a paper is absent from every "
        "number below, from both the numerator and the denominator. Section 17 is the single "
        "exception and only when a degree-thesis roster was supplied by hand, in which case people "
        "who finished a degree here without an indexed paper are counted there and nowhere else; "
        "everyone who left before finishing stays invisible in that section too. The size of that "
        "missing group cannot be recovered from this data. The report cannot see advising style, "
        "funding, working hours, lab culture, or what happened to people who left — which are the "
        "things you actually want to know. Do not let the numbers that are here stand in for the "
        "ones that are not."
    ),
    "CAV-01": (
        "{n} further papers matched the name but carried no verifiable identity evidence and were "
        "excluded. That is the upper bound on how much this corpus is missing for identity reasons. "
        "A PI who changed institution inside the window loses their earlier papers this way unless "
        "every institution variant is configured."
    ),
    "CAV-02": (
        "People are identified by name string. Romanised names collide heavily, and the tool has "
        "ORCID and affiliation keywords for the PI only — co-authors have nothing equivalent. Two "
        "people with one name merge into one row with an inflated count and an over-long span; one "
        "person recorded two ways splits into two short rows. Strict keying finds {n_strict} people, "
        "loose keying finds {n_loose}; the gap is the error bar on every count in this section."
    ),
    "CAV-03": (
        "Position labels are inferences from the byline, not facts about a person. A PhD student, "
        "postdoc, technician, staff scientist, clinical fellow, rotation student and visiting scholar "
        "all produce the same positional shape, and no field in PubMed separates them."
    ),
    "CAV-04": (
        "This counts slots on papers, not people. A high trainee-led count can come from three "
        "prolific people in a twenty-person lab. It describes the shape of the lab's output, not your "
        "personal odds."
    ),
    "CAV-05": (
        "Papers where a trainee elsewhere led and this PI was a middle author are in this corpus, but "
        "papers where this PI is absent entirely are not — so this says nothing about the PI's "
        "behaviour outside their own lab."
    ),
    "CAV-06": (
        "Counted only over people who published at least once. Anyone who joined and left without a "
        "paper is in neither bucket. This is not a rate and must not be read as your chance of "
        "leading a paper here."
    ),
    "CAV-07": (
        "Measured on publication dates, which trail the work by roughly one to two years including "
        "review. A median of three years does not mean you publish in year three."
    ),
    "CAV-08": (
        "Computed only over people who reached a first-author slot. The people still without one are "
        "printed beside it for exactly this reason — read both numbers or neither."
    ),
    "CAV-09": (
        "This is the interval between two publication dates, not time in the lab. The period before "
        "someone's first paper — typically the first two to three years of a PhD — is invisible by "
        "construction, and a paper can appear a year or more after the person has left."
    ),
    "CAV-10": (
        "The median covers only people whose whole span fits inside the search window, which biases "
        "it toward short stays: at years_back={years_back} anyone long-tenured is censored at one end "
        "or both. A growing lab looks like it churns people; a lab whose students all left years ago "
        "shows clean, complete, long spans."
    ),
    "CAV-11": (
        "A short span supports \"fast and efficient\" and \"left after a year\" equally well. Nothing "
        "in this data distinguishes them."
    ),
    "CAV-12": (
        "Author count is not headcount. This counts people whose papers happened to come out that "
        "year, so a lab that just doubled shows no change for two years and a lab that emptied last "
        "year still looks full. Co-authors are not lab members and PubMed offers no way to separate "
        "them."
    ),
    "CAV-13": (
        "Not measured. This corpus was built with a first/last/corresponding-author filter, so the "
        "PI's byline position is decided by the filter rather than by the data. Re-fetch with the "
        "profile path, which verifies identity without filtering on position."
    ),
    "CAV-14": (
        "Last-author-equals-senior-author is a biomedical convention, not a rule. In many clinical "
        "departments the division head is last on everything regardless of involvement — which is "
        "precisely the case this count exists to detect and precisely the case it cannot detect."
    ),
    "CAV-15": (
        "Corresponding-author status is inferred from an email address appearing in the affiliation "
        "string; PubMed has no corresponding-author field. It tracks journal formatting policy and "
        "changes over time. Email coverage in this corpus: {covered} of {total} papers."
    ),
    "CAV-16": (
        "PubMed carries the equal-contribution attribute only when the publisher supplies it. Absence "
        "is not evidence of absence: journals that mark co-first authorship with a footnote and "
        "deposit nothing produce a zero here. No rate is computed from this field for that reason."
    ),
    "CAV-17": (
        "The first and last bins are partial: the window starts mid-year, and the most recent 18 "
        "months are undercounted by PubMed indexing lag and by ahead-of-print records with no issue "
        "date yet. Every lab looks like it is winding down at the right-hand edge."
    ),
    "CAV-18": (
        "These are PubMed records, not research papers. Publication type is not parsed, so reviews, "
        "letters, comments and case reports are counted alongside primary research except where a "
        "title made the record identifiable as a correction."
    ),
    "CAV-19": (
        "A small team supports \"you get attention\" and \"there is nobody here to learn from\" "
        "equally. A large one supports \"generous inclusion\" and \"your contribution disappears into "
        "position 14\". The count cannot choose between them."
    ),
    "CAV-20": (
        "Journal names are printed as recorded and are not normalised, so one journal can appear "
        "twice under its full title and its abbreviation. No impact factor, quartile or partition "
        "stands beside any of these names here, and none was fetched anywhere in this toolkit: "
        "each of those tables is a licensed product with no free, redistributable source and this "
        "package ships no crawler. Section 18 carries them only when you filled in the table "
        "yourself and passed it in, and then only for the journals your file covers. Read a blank "
        "either way as a lookup nobody has done, not as a verdict on these venues — and do not "
        "fill it in from memory, because a journal name you recognise and one you do not are "
        "equally uninformative about the papers listed here. Concentration in one venue supports "
        "\"deep specialisation\" and \"a reliable low-bar outlet\" equally."
    ),
    "CAV-21": (
        "These are affiliation strings, not institutions. They are unnormalised free text and are not "
        "counted or grouped. Coverage is strongly time-biased: older PubMed records often carry only "
        "the first author's affiliation, so years are not comparable to each other. An author with "
        "joint appointments has all of them joined into one string, so a shared home institution "
        "hides the external one."
    ),
    "CAV-22": (
        "Titles are printed verbatim and ungrouped. No topic classification is offered: research "
        "direction cannot be measured honestly from the fields this toolkit parses, and stability "
        "would be as easy to read as \"a mined-out vein\" as \"deep expertise\"."
    ),
    # ------------------------------------------------------------------
    # CAV-30 and up: the two tables the user supplies by hand
    # ------------------------------------------------------------------
    #
    # A new number block rather than CAV-23, because these three describe a
    # different kind of input from everything above them. CAV-00 to CAV-22 are
    # about what PubMed does and does not record; these are about data that
    # entered the report through a CSV a person typed, from a source this
    # toolkit never contacted and cannot verify. The gap in the numbering is
    # the boundary, and it is meant to survive later additions on either side.
    #
    # `journals.JOURNAL_CAVEATS` (JRN-01..07) and `theses.THESIS_DENOMINATOR_CAVEAT`
    # say more, in the same voice, inside Sections 18 and 17. These three are the
    # short forms, for the sections that print a journal name or a headcount
    # without printing the joined table beside it.
    "CAV-30": (
        "Every impact factor and partition in this report was typed in by hand from a vendor page "
        "and joined from a local file. Nothing here fetched one, and nothing here can check one. "
        "The edition is printed beside every number because the sources disagree with each other "
        "and the disagreement is not marginal: LetPub's search-results list shows the 民间版 "
        "(folk) partition by default rather than the official one, ablesci labels 官方版 "
        "(official) and 新锐版 (rising) as separate claims, and Clarivate's free Master Journal "
        "List carries indexing status alone, with no impact factor and no quartile at all. Where "
        "two editions disagree about one journal, both are shown and neither is chosen — that "
        "choice is not this toolkit's to make. A partition with no edition and no retrieval date "
        "beside it is unfalsifiable a year later, including by the person who wrote it down, and "
        "it must not be read as though it were the official one."
    ),
    "CAV-31": (
        "An impact factor or a partition is a property of a journal in one year, not a property "
        "of any paper in it, and not a property of the person who wrote the paper. Both are "
        "re-cut annually, so the data year and the retrieval date are part of the number rather "
        "than filing detail: a partition read in one year printed beside a paper published in "
        "another is two claims presented as one, and this report prints both dates so you can see "
        "when that is happening. The journal-level figure also says nothing about the papers "
        "listed here — an impact factor is the mean of a distribution skewed hard enough that "
        "most papers in a journal are cited well below it, so a paper in a 1区 journal may be "
        "uncited and a paper in an unranked one heavily cited. How often these particular papers "
        "were actually cited is a different number, fetched separately, with its own coverage "
        "fraction, in Section 15."
    ),
    "CAV-32": (
        "A degree-thesis roster is a better denominator than PubMed's and it is still not a "
        "complete one. A thesis record exists only for someone who finished: anyone who enrolled "
        "with this advisor and then withdrew, transferred, was dismissed, changed advisor or has "
        "not yet graduated is in no library at all — not CNKI, not Wanfang, not PubMed — and is "
        "in none of the counts here. That group is not small-but-unknown; it is unmeasured and "
        "unmeasurable from every source this toolkit can reach, so nothing in this report is a "
        "graduation rate, an attrition rate, or the membership of the lab, and \"graduates with "
        "no PubMed paper\" is a floor on how many people passed through unpublished rather than "
        "the number of them. What the roster does repair is narrower and worth stating exactly: "
        "PubMed sees only people who published, so a graduate with no paper was missing from both "
        "the numerator and the denominator of every other number in this report, and those people "
        "are counted here. Two further limits travel with the file. Its coverage is whatever "
        "export was actually run — those libraries, those institutions, those years, and only "
        "theses that were deposited and released. And a Chinese roster does not join to romanised "
        "PubMed bylines by any rule in the standard library, so pairs that cannot be settled are "
        "listed for a human and left out of both buckets, which is why the result is reported as "
        "a floor and a ceiling instead of one number."
    ),
    # The one entry in this block that is not about a hand-filled file. It sits
    # here anyway, and not with CAV-00..22, because those are about what PubMed
    # records and this is about a third party's statement concerning a journal —
    # the same distinction the block boundary above was drawn for. The long
    # version is `journal_risk.JOURNAL_RISK_CAVEATS`, printed inside Section 18.
    "CAV-33": (
        "Some journal-level lines in this report were fetched rather than typed: DOAJ indexing "
        "status, Crossref metadata deposit coverage and OpenAlex's source record, each read on a "
        "stated day and printed with the endpoint that returned it. They are statements, not a "
        "rating. Nothing in this report calls any journal predatory, no count of these signals is "
        "turned into a grade, a tier, a score, a letter or a colour, and none of them contributes "
        "to the composite score. Two of them are routinely misread and are worth stating in "
        "reverse: absence from DOAJ is not a finding, because DOAJ indexes open-access journals "
        "that applied to it and every subscription journal is absent by construction; a Crossref "
        "coverage field reading zero is not a finding either, because it measures what a publisher "
        "deposits and ordinary journals sit at zero on several of them. The 中科院国际期刊预警名单 "
        "is not among these and cannot be — it is published once a year behind a login with no "
        "machine-readable endpoint, so it stays in the hand-filled 是否预警 column, and a blank "
        "cell there means nobody checked rather than that a journal is absent from the list."
    ),
}


def caveat(caveat_id: str, **fields: Any) -> str:
    """Formatted caveat text. Missing fields raise rather than render a hole."""
    return CAVEATS[caveat_id].format(**fields)


# Section 10 of the spec, rendered verbatim as report Section 14. It is part of
# the deliverable: a reader who cannot see what was left out has no way to tell a
# considered omission from an oversight.
#
# Most lines here are refusals — things that would still not be computed if
# better data arrived. Four are not, and each says so in its own text rather than
# leaving the distinction to the heading: two record reversals (the citation line
# and the ranking line, both of which now name what the tool does produce), the
# journal-metric line records an input that is obtainable only by hand, and the
# field-normalisation line records one that cannot be obtained at all. Section 16
# prints the same split under two headings (`scoring.SCORING_EXCLUSIONS`); the
# duplication is deliberate, because the one failure this register exists to
# prevent is a reader flattening "we cannot get it" and "we will not do it" into
# a single shrug.
#
# A reversed line is never deleted. A register that quietly loses the entry for
# something now being printed is a register that cannot be audited, and the whole
# argument for keeping this list is that a reader can see what moved and when.
DROPPED_REGISTER: tuple[tuple[str, str], ...] = (
    (
        "Research direction stability / topic drift",
        "parse_article extracts no MeSH and no keywords. Adding MeSH would not fix it: MeSH indexing "
        "lags publication by months, so the newest year is systematically under-indexed and the "
        "pipeline would manufacture a drift signal that is pure artifact. Title-word overlap measures "
        "house style. Replaced by Section 13, titles by year.",
    ),
    (
        "render_topic_charts",
        "Reads an externally produced _topic_extraction.json that this toolkit never writes, and its "
        "BUCKET_NAMES are one specific lab's subject areas. Out of scope for the profile report.",
    ),
    (
        "Count of collaborating institutions",
        "Affiliation strings are unnormalised; counting them measures string variance, not "
        "collaboration. It is also a prestige proxy, which is the ranking use-case this product "
        "excludes. Replaced by Section 12.",
    ),
    (
        "External-affiliation dependency ratio",
        "Same denominator problem plus era-dependent coverage, and joint appointments are space-joined "
        "into one string so external ties disappear. Any trend in it is a metadata trend that reads as "
        "a scientific one.",
    ),
    (
        "Lead-author conversion rate",
        "The denominator is conditioned on having published, so the rate is optimistic by an "
        "unmeasurable amount and is read as personal odds. Replaced by the three-bucket partition in "
        "Section 3.",
    ),
    (
        "Co-first authorship rate",
        "EqualContrib is publisher-deposited with unknown, journal-dependent missingness. Replaced by "
        "a count and a position breakdown in Section 8.",
    ),
    (
        "Mean first-author papers per trainee",
        "A ratio of two undercounted quantities, dominated by the single most productive person, that "
        "reads as a productivity score for a named individual.",
    ),
    (
        "Corresponding-author rate as a headline",
        "An email-presence proxy. Reported only beside its own coverage rate, never alone.",
    ),
    (
        "Citation counts, h-index, i10-index, median citations — no longer dropped",
        "This line is kept so the reversal is visible instead of silent. These are now fetched by "
        "`check-your-advisor cite` from OpenAlex, Semantic Scholar or Europe PMC into their own "
        "dated file, computed in Section 15, and consumed by two components of Section 16. efetch "
        "still returns none of it, so the limits travel with the numbers: coverage is only what "
        "those three sources matched and is printed as a fraction of the corpus, a partially "
        "covered total or h-index is a floor rather than a value, the h-index is bounded by this "
        "search window and is not a career figure, the three sources sit on three citation graphs "
        "that disagree, and every count is a measurement that moves after the day it was taken. "
        "What was dropped alongside them — ordering the papers or the people by those counts — "
        "was not reinstated.",
    ),
    (
        "Field-normalised citation impact",
        "Not implemented, for want of an input rather than by decision. It needs a subject "
        "classification plus per-field citation distributions at record level, and no free source "
        "supplies both. Its absence is why the citation numbers above carry a field-confound note "
        "instead of a correction, and why nothing computed from them is comparable across fields.",
    ),
    (
        "Journal Impact Factor, quartile, CAS partition — still not fetched, now joined from your "
        "own table",
        "Half of this line has moved and half has not. Still not fetched, and never will be from "
        "inside this package: each of those tables is a licensed product with no free, "
        "redistributable source, the vendors forbid scraping and defend against it, and this "
        "toolkit ships no crawler and no licensed data. What changed is that the blank is now "
        "fillable by the reader. `check-your-advisor journal-worklist` writes out the couple of "
        "dozen journals this corpus actually uses — not a global table, which is hundreds of "
        "hours — with their ISSNs and paper counts, in the shape the loader reads back; you look "
        "them up by hand and pass the file with `profile --journal-table`, and Section 18 joins "
        "it and prints the edition and retrieval date beside every number. Whether a journal-level "
        "number should ever stand in for an individual paper is a separate argument and this "
        "register still does not settle it: CAV-31 travels with those numbers rather than "
        "endorsing them. With no table supplied the section says so in words, and that absence "
        "remains a missing input rather than a judgement about the venues.",
    ),
    (
        "A predatory-journal verdict — refused, and the signals behind one printed instead",
        "This is a refusal and not a missing input, so it does not move if better data arrives. "
        "\"Predatory\" is an accusation about a publisher's conduct; it is not a measurement, no "
        "open source makes it, and a tool whose whole credibility rests on not printing an "
        "inference as a fact has no business manufacturing one. So there is no risk grade here, no "
        "tier, no score, no letter and no colour, at any number of signals, and nothing collected "
        "reaches the composite score. What is printed instead is the evidence a reader would want "
        "before forming their own view: `check-your-advisor journal-risk` reads DOAJ indexing "
        "status, Crossref metadata deposit coverage and OpenAlex's source record — three open "
        "keyless APIs, all three queried every time, no first-hit-wins and no winner where they "
        "disagree — and Section 18 prints each statement beside the endpoint that returned it and "
        "the day it was read. The two signals most likely to be misread are stated in reverse "
        "there: absence from DOAJ is not a finding, and a Crossref coverage field at zero is not "
        "one either. The 中科院国际期刊预警名单 stays a hand-filled column for a different reason "
        "again — it has no machine-readable endpoint at all, and this package will not scrape one.",
    ),
    (
        "Rank, star band, \"scored higher than\" — no longer dropped",
        "This line is kept so the second reversal is visible instead of silent. Round one printed "
        "a score and refused every ordering built on it, on the rule \"a value may be printed, a "
        "position may not\". That rule has been withdrawn on purpose, and only for corpora: "
        "`compare` now ranks the corpora on its page by composite score under one shared weight "
        "table, prints a star band beside each score, and states in a sentence which of two "
        "corpora scored higher. What the old rule was protecting is carried by the output instead "
        "of by the silence. A rank is a position among the corpora somebody chose to load and "
        "moves the moment one is added or dropped, so every ranked row prints the count it was "
        "ranked among — first of two and first of nine are different claims. A star band is that "
        "same score coarsened into five equal bands whose edges are declared constants printed "
        "beside the result, measured off the 0-100 scale and off no group of people. The "
        "directional sentence carries the difference in points and both component counts, and "
        "refuses itself outright when the two scores were not built from the same components "
        "under the same weights. What did not come back with any of it is the ordering of "
        "*people*: no roster in this report is sorted by a count, and the rank column ranks "
        "corpora, which are files.",
    ),
    (
        "Refusing the whole report over an unverifiable identity — no longer done",
        "This line is kept so the third reversal is visible instead of silent. Gates G2 (no paper "
        "passed identity verification) and G3 (no identity evidence reached the corpus) used to "
        "produce a page carrying the gate, the observed values and the fix, and nothing else. They "
        "are now warnings printed in bold at the top of Sections 0, 1 and 19, the report is built "
        "in full, and the process exit code is still 1 so anything scripted against the refusal "
        "behaves as it did. The reason for the change is Section 19: it is the check for exactly "
        "the failure those gates fire on — it removes the PI and asks whether the remaining "
        "records are still tied together by shared co-authors — and refusing meant a reader who "
        "needed it most never saw it. Sixteen sections of computed fact were being destroyed to "
        "prevent one unwarranted claim, and the claim is prevented by the warning instead. What "
        "did not change: the corpus is not certified by rendering, it takes no position on the "
        "comparison page (`ranking` leaves it unranked and names the warning as the reason), and "
        "the numbers are not softened or suppressed anywhere to accommodate it. G4 (no structured "
        "author records) and G5 (nothing left after exclusions) are still gates, because there is "
        "no degraded report to print when there is nothing to compute over.",
    ),
    (
        "Warning G3 read off the config — now read off what the evidence reached",
        "This line is kept so a reversed judgement is visible instead of silent. G3 used to ask "
        "whether an orcid, an email_domains entry or an affiliation_keywords entry was *set*. A "
        "corpus that set one and matched it against not a single record therefore cleared the "
        "warning and exited 0, while an identical corpus with the field left blank warned and "
        "exited 1 — the same evidence, none, judged two ways on the strength of a string in a "
        "config file. It now counts how many records each key actually reached, through the same "
        "matchers `resolve_pi` uses for the byline keys and the same record-level reader "
        "`openalex_id_record_share` uses for the id. So a configured key that matched nothing now "
        "warns and exits 1 where it used to be silent: that is more corpora warning, on purpose, "
        "and it is the point of the change. The min_openalex_record_share boundary is untouched — "
        "the OpenAlex id was already judged this way, and the other three have caught up with it. "
        "The banner text moved with the rule and now names which of the three situations fired, "
        "because one sentence covering all three read \"No identity evidence reached a single "
        "record\" over corpora whose Observed values on the same line read "
        "`openalex_id_on_records=2/6`.",
    ),
    (
        "A pi-name over a corpus file that carries no search block — was discarded, now wins",
        "This line is kept so a reversed precedence is visible instead of silent. Two places "
        "supply the name a report is read against: `--pi-name` on the command line and whatever "
        "name the harvest that wrote the corpus file was run under. The rule has always been that "
        "the command line wins and the recorded name fills the gap, and `cli._profile_corpus` "
        "applied it — but only to a file carrying a `search` key. A file already in the Section 4 "
        "corpus shape goes to `build_report` untouched, and `build_report` had the two the other "
        "way round, so on that one path `--pi-name` was parsed, logged and then dropped. The name "
        "warning (G7) then measured the name in the file, found it on every byline, and the run "
        "exited 0 with nothing on the page about the name that had been typed. It now measures "
        "the typed name on both paths. So a corpus profiled under a name nobody on it holds warns "
        "and exits 1 where it used to be silent: that is more corpora warning, on purpose, and it "
        "is the point of the change. What did not change is the other half of the rule — with no "
        "name on the command line and none in the config, the name the file records is still what "
        "the report is about, which is what lets `compare` read several corpora about different "
        "people without a name per directory.",
    ),
    (
        "Percentile, quantile position, letter grade",
        "Still refused, for two reasons that are different and are kept apart on purpose. A "
        "percentile or a quantile position needs a reference population, and nothing here holds "
        "one: every normalisation anchor in `scoring` is a declared constant rather than a value "
        "measured off a group of researchers, and no calculation sees more than the few corpora on "
        "one page. So a percentile is uncomputable here rather than merely withheld — a statement "
        "about where someone sits among researchers cannot be manufactured out of two or nine "
        "files a user happened to load, and the rank that is printed says in its own text that it "
        "is a position among exactly those files and nothing wider. A letter grade is the other "
        "case, and it is refused by decision: stars are produced and letters are not. That split "
        "between two coarsenings of one number is deliberate rather than an inconsistency waiting "
        "to be tidied — a star band is printed beside the score it coarsens and the edges that "
        "produced it, while a letter detaches from its scale on sight and travels as a verdict "
        "about a person. Recorded here so that a later reader does not unify the two and reverse "
        "a decision they were not party to.",
    ),
    (
        "Graduation rate, time to degree, attrition, \"students who left\"",
        "The denominator — everyone who joined — is structurally unobservable, and a degree-thesis "
        "roster does not repair it, because a thesis exists only for somebody who finished. "
        "Section 17 counts graduates on record and how many of them hold no paper in this corpus, "
        "which is a narrower thing reported as counts with a floor and a ceiling, never as a rate "
        "and never divided by a population nobody can enumerate. Stated as prose in CAV-00 and "
        "CAV-32, never approximated with a number.",
    ),
    (
        "Authorship \"fairness\" or \"credit generosity\" scores",
        "Require knowing contribution, which is in no field at any level.",
    ),
    (
        "Year-over-year percentage change — still dropped. Fitted slopes — no longer dropped",
        "Kept on one line so the fourth reversal is visible rather than silent. The original "
        "objection stands unaltered: five right-censored integer points do not support a slope, "
        "and 3 papers to 5 is not \"+67%\". Year-over-year change is still refused outright for "
        "exactly that second reason — it turns two small counts into a number that looks like a "
        "rate. What changed is the slope, and only in how it is presented: Section 9 prints one "
        "with the interval around it and the count of points it was fitted over in the same "
        "sentence, and `profile/trends.py` refuses to fit at all below four points. The reader is "
        "shown the width instead of being asked to trust the middle.",
    ),
    (
        "Any distinction between PhD student, master's student, postdoc, staff scientist, technician, "
        "clinical fellow, visiting scholar",
        "No field supports it.",
    ),
    (
        "Span-based seniority reclassification",
        "A span cap encodes an assumption about degree length and evicts the longest-serving trainees, "
        "who are the most informative people in the corpus.",
    ),
    (
        "Affiliation-gated cohort membership",
        "Pre-2014 records carry only the first author's affiliation, so the gate deletes real trainees "
        "for a data-coverage reason.",
    ),
    (
        "A same-name-collision score, threshold or gate on the co-author partition",
        "Attempted and dropped on the measurement, not on principle. Section 19 partitions the corpus "
        "by shared co-authorship, and a cut-off on that partition was the obvious next step. Counted "
        "on three real corpora — one known to hold at least five researchers, two better filtered — "
        "the cluster counts were 15, 16 and 10 and the largest cluster held 21%, 35% and 42%. The "
        "count separates none of them: every real researcher accumulates one-off collaborators and "
        "each becomes a single-record cluster, so the count tracks how many one-off papers there are "
        "rather than how many people are in the corpus. Three contaminated samples cannot calibrate a "
        "cut-off, and one guessed from them would refuse real broad-ranging researchers — a worse "
        "failure than the one it would prevent. What separates one person from several is whether the "
        "clusters' subject matter is related, which needs the subject classification dropped at the "
        "top of this register. So Section 19 prints the partition and hands the reading over.",
    ),
    (
        "A sentiment score or overall rating from the evaluations in Section 20",
        "Refused by decision, not by difficulty — a polarity label over a handful of posts is four "
        "lines of code. Section 20 holds statements collected by hand from public pages, and "
        "everyone in it chose to write something: people post after an experience strong enough to "
        "be worth typing up, in either direction, and there is no observable denominator of the "
        "people who felt neither way. An average over that measures who bothered to post. It would "
        "also carry the authority of a measurement and the content of a guess, and it would be "
        "quoted long after the text it came from was forgotten. So no row of that table is "
        "labelled positive or negative, none is averaged, none is rated, and none enters the "
        "composite score in Section 16 at any weight, including zero. The statements are printed "
        "as given, beside the source they came from and the day it was read, and the reading is "
        "the reader's.",
    ),
)
