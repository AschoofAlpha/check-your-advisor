"""
Advisor profile: what the PubMed record says about being this person's student.

The report answers one question — what is it like to be this named researcher's
graduate student — from publication metadata alone. It is not a literature
search.

Scope, in three groups that must not be flattened into one. The difference
between them is the difference between a measurement, a refusal and a missing
input, and a reader who cannot tell them apart cannot tell which would change
tomorrow:

**Computed and written to disk.** Citation counts and the h-index (`impact`,
fetched by `check_your_advisor.citations` into its own
`citations_<timestamp>.json`), one composite score out of 100 under a weight
table the user sets (`scoring`), the star band that score falls in, and several
corpora laid side by side, ranked, with a sentence saying which of two scored
higher (`ranking`, `report.build_comparison`). Each of these prints beside the
denominator it was computed over and the inputs it consumed. This reverses the
original blanket prohibition, in two deliberate steps and after review each
time: first the values, then the ordering.

**Refused by design.** Percentile and quantile position — not merely withheld
but uncomputable here, because nothing in this package holds a reference
population and the corpora on a page are the few a user chose to load. Letter
tiers A/B/C: stars are produced and letters are not, and that split is a
decision, recorded so that a later reader does not unify them and reverse a call
they were not party to. Fitted trends, slopes and year-over-year percentage
change, unchanged from the original register — a handful of right-censored
integer points do not support a slope. The current machine-readable register is
`ranking.RANKING_EXCLUSIONS`; `scoring.SCORING_EXCLUSIONS` remains true of
`composite_score` itself, which sees one corpus and can order nothing, and
report Section 16 prints both with the sentence that says how they fit together.

**Ordering of people, still refused entirely.** No roster is ever sorted by a
count, in the Markdown, in the HTML or in the interactive control, and no person
is ever placed above another. The ranks that now exist are among *corpora*, on
the side-by-side page, and every one of them prints the count it was taken over.

**Supplied by hand, or absent and said to be.** Journal Impact Factor, JCR
quartile and CAS partition (`check_your_advisor.journals`), and the degree-thesis
roster that carries graduates who published nothing
(`check_your_advisor.theses`). Neither is shipped and neither is fetched: those
are licensed products and subscription libraries, there is no crawler in this
package, and there will not be one. Each module defines the schema, emits the
worklist of what this corpus actually needs looked up, joins the file the user
filled in, and prints the edition and the retrieval date beside every number.
Report Sections 17 and 18 render them, and print the reason a table is absent
rather than an empty cell.

Layout:

  roles    Section 6 of docs/profile-metrics-spec.md — PI resolution, record
           exclusions, person keys, strata, affiliation signal.
  metrics  Section 7 — one pure function per metric, each returning its value
           together with the denominator it was computed over.
  impact   The citation side of the same contract — coverage, h-index,
           i10-index, median — over counts fetched elsewhere and passed in.
  scoring  The composite score: declared normalisation anchors, an exposed
           weight table, and per-component raw inputs, weights and
           contributions that add back up to the score by hand.
  ranking  Ordering, coarsening and direction — rank among the corpora on one
           page, the star band, and the one-sentence "A scored higher than B".
           Reads the dicts `scoring` returned and recomputes no score, so a
           position can never disagree with the number beside it.
  report   Sections 5, 8 and 9 — gates, the verbatim caveat strings, the
           Markdown plus JSON rendering, and the side-by-side comparison.

  svg      Domain-free SVG primitives (docs/profile-visual-spec.md Section 3).
  charts   The five figures, each a pure metric-dict-to-SVG-string function.
  html_report  The reading surface: one self-contained .html carrying the same
           numbers, the figures inline, and every caveat uncollapsed.

`charts.figures_for_report` is deliberately not re-exported here. `cmd_profile`
guards its import so that a drawing layer which will not load costs the reader
five figures instead of the whole report; re-exporting it would run that import
as a side effect of importing this package and make the guard unreachable.
Import it by module path: `from check_your_advisor.profile.charts import ...`.

The package is named `profile` inside `check_your_advisor`, which shadows a
standard-library module name at the top level. Absolute imports keep that legal,
but anything importing the stdlib profiler from inside this package must say
`import profile` at top level and mean it.
"""

from __future__ import annotations

from .caveats import CAVEATS, caveat
from .html_report import render_html, write_html
from .impact import citation_metrics, h_index, i10_index
from .metrics import (
    activity_span,
    affiliation_strings,
    equal_contrib_occurrences,
    first_author_slots,
    lead_slot_partition,
    person_roster,
    pi_byline_positions,
    records_per_year,
    roster_turnover,
    team_size,
    time_to_lead,
    titles_by_year,
    venue_repetition,
)
from .ranking import (
    RANKING_EXCLUSIONS,
    RANK_METHOD,
    STAR_BANDS,
    STAR_BASIS,
    STAR_MAX,
    comparative_statement,
    rank_corpora,
    star_rating,
)
from .report import (
    ORDER_BY_LABEL,
    ORDER_BY_SCORE,
    build_comparison,
    build_report,
    build_report_from_path,
    check_corpus_gates,
    check_source_path,
    json_record,
    load_corpus,
    render_comparison_markdown,
    render_markdown,
    resolve_score_weights,
    write_comparison,
    write_report,
)
from .roles import (
    affiliation_signal,
    apply_record_exclusions,
    build_people,
    default_gantt_exclude_names,
    is_collective_name,
    prepare_paper,
    resolve_pi,
)
from .scoring import (
    COMPONENT_NAMES,
    DEFAULT_WEIGHTS,
    SCORING_EXCLUSIONS,
    composite_score,
)

__all__ = [
    "CAVEATS",
    "COMPONENT_NAMES",
    "DEFAULT_WEIGHTS",
    "ORDER_BY_LABEL",
    "ORDER_BY_SCORE",
    "RANKING_EXCLUSIONS",
    "RANK_METHOD",
    "SCORING_EXCLUSIONS",
    "STAR_BANDS",
    "STAR_BASIS",
    "STAR_MAX",
    "activity_span",
    "affiliation_signal",
    "affiliation_strings",
    "apply_record_exclusions",
    "build_comparison",
    "build_people",
    "build_report",
    "build_report_from_path",
    "caveat",
    "check_corpus_gates",
    "check_source_path",
    "citation_metrics",
    "comparative_statement",
    "composite_score",
    "default_gantt_exclude_names",
    "equal_contrib_occurrences",
    "first_author_slots",
    "h_index",
    "i10_index",
    "is_collective_name",
    "json_record",
    "lead_slot_partition",
    "load_corpus",
    "person_roster",
    "pi_byline_positions",
    "prepare_paper",
    "rank_corpora",
    "records_per_year",
    "render_comparison_markdown",
    "render_html",
    "render_markdown",
    "resolve_pi",
    "resolve_score_weights",
    "roster_turnover",
    "star_rating",
    "team_size",
    "time_to_lead",
    "titles_by_year",
    "venue_repetition",
    "write_comparison",
    "write_html",
    "write_report",
]
