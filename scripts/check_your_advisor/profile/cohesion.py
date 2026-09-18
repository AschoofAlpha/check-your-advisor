"""
Whether this corpus looks like one person's collaboration network or several.

The corpus decides every number in the report, and the way it goes wrong is
always the same: papers by a different person with the same name get in, and the
roster, the time-to-first-author and the turnover figures are all wrong in a way
that looks perfectly normal on the page. Warning G3 catches the worst case — a
corpus no configured identity evidence reached, printed in bold at the top of
this section — but it says nothing about a corpus whose evidence did reach the
records and is weak anyway, which is where the failure actually lives.

The signal here is collaboration, not subject matter. Remove the PI, who is on
every record by construction, and ask which records are still tied together by a
shared co-author. One person's output is tied together by the people they work
with; two people sharing a name have no reason to share anyone else.

WHAT THIS DOES NOT DO, AND WHY
------------------------------
It does not gate, score or threshold, because the measurements do not support a
threshold. Three real corpora, counted with this exact function:

    corpus                        papers  clusters  largest
    weak evidence, known-polluted     28        15      21%
    full institution name             34        16      35%
    full institution name, 1 year     19        10      42%

The first is known to contain at least five different researchers. The others
are better filtered but not clean. Cluster *count* separates none of them: any
real researcher accumulates one-off collaborators, and each becomes a
single-record cluster, so the count is dominated by how many one-off papers
there are rather than by how many people are in the corpus. The largest-cluster
share shows a gradient across those three, but three contaminated samples cannot
calibrate a cut-off, and a threshold guessed from them would refuse real
broad-ranging researchers.

What actually separates "one person working across fields" from "several people
sharing a name" is whether the clusters' subject matter is related — and this
toolkit deliberately does no subject classification (see the register in Section
14 for why MeSH does not rescue it). So this module prints the clusters and the
journals in them, and the reading is the reader's. Looking at the first corpus
above, the split is not subtle: six records in oncology and surgery, five in
analytical chemistry, three in structural biology, two in soil microbiology, and
nine singletons in unrelated venues. Nobody needs a threshold to see that.

The honest use is diagnostic: many clusters whose journals have nothing to do
with each other means re-harvest with `--orcid` before believing any number in
this report.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

# Below this many records the partition says nothing: four papers that share no
# co-author are four clusters whether or not they are four people.
MIN_N_COHESION = 5

# A cluster this size or larger is printed with its journals; smaller ones are
# summarised as a count. Not a judgement about them — a page with thirty
# one-record clusters listed in full buries the ones worth reading.
CLUSTER_DETAIL_MIN = 2

# How many of a cluster's records a person must be on to count as recurring in
# it. One threshold, used for both `recurring_people` and `edges`, because they
# are the same rule seen twice: the people worth naming and the people worth
# joining are the same people. Two copies of the number would let a figure draw
# an edge to a person it does not draw a node for.
#
# Two reasons for the rule itself, and the second is the load-bearing one:
#
# - It bounds the output. One 20-author record is 190 pairs, and a cluster of
#   thirty such records is a list nobody can read and a figure nobody can draw.
# - A person seen once is on one record and explains nothing about why that
#   record joined this cluster. Drawing them would put the corpus's whole author
#   list on the page under the name "collaboration network", which is the
#   1934x21506 failure the figure set exists to avoid.
#
# So an edge here means: both endpoints recur in this cluster, and they are on
# at least one record together. Not "these two people collaborate a lot".
RECURRENCE_MIN_RECORDS = 2


def _person_names(paper: Mapping[str, Any]) -> list[str]:
    """Every non-PI person on one record, as name strings.

    `persons` is the consortium-stripped author list `roles.prepare_paper`
    builds, and `pi_person_index` indexes into it. A record whose PI was never
    located contributes all of its authors: dropping the record instead would
    quietly shrink the denominator this whole section is about.
    """
    persons = paper.get("persons") or paper.get("authors") or []
    pi_index = paper.get("pi_person_index")
    names = []
    for index, person in enumerate(persons):
        if index == pi_index:
            continue
        name = (person.get("name") or "").strip() if isinstance(person, dict) else ""
        if name:
            names.append(name)
    return names


def coauthor_clusters(
    papers: Sequence[Mapping[str, Any]],
    pi_name: str = "",
) -> dict[str, Any]:
    """
    Partition the corpus into records connected by shared co-authors.

    Two records are in the same cluster when a chain of shared non-PI authors
    joins them. `pi_name` removes the PI from records where `pi_person_index`
    did not locate them, which otherwise merges the whole corpus into one
    cluster and hides exactly what this is for.

    Returns the partition and nothing derived from it: no score, no verdict, no
    ordering of the people inside a cluster. Clusters are ordered by size purely
    so the page is readable, and that is stated on the page.
    """
    n = len(papers)
    if n < MIN_N_COHESION:
        return {
            "denominator": n,
            "min_n": MIN_N_COHESION,
            "suppressed": True,
            "n_clusters": None,
            "clusters": [],
            "largest_size": None,
            "singleton_clusters": None,
            "records_in_singletons": None,
        }

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_b] = root_a

    pi_key = (pi_name or "").strip().casefold()
    first_seen: dict[str, int] = {}
    for index, paper in enumerate(papers):
        for name in _person_names(paper):
            if pi_key and name.casefold() == pi_key:
                continue
            if name in first_seen:
                union(first_seen[name], index)
            else:
                first_seen[name] = index

    members: dict[int, list[int]] = defaultdict(list)
    for index in range(n):
        members[find(index)].append(index)

    clusters = []
    for indices in sorted(members.values(), key=lambda group: (-len(group), group[0])):
        records = [papers[i] for i in indices]
        journals = Counter((r.get("journal") or "").strip() for r in records if (r.get("journal") or "").strip())
        years = [r.get("year") for r in records if isinstance(r.get("year"), int)]
        # Only people who recur inside the cluster: the ones that made it a
        # cluster. A name appearing once is on one record and explains nothing.
        recurring = Counter()
        per_record: list[set[str]] = []
        for record in records:
            names = {name for name in _person_names(record)
                     if not (pi_key and name.casefold() == pi_key)}
            per_record.append(names)
            for name in names:
                recurring[name] += 1
        recurring_names = {name for name, count in recurring.items()
                           if count >= RECURRENCE_MIN_RECORDS}
        # Shared bylines among those people, counted once per record. Emitted
        # here rather than derived later so the figure that draws this network
        # reads the partition instead of re-deriving it from the corpus: two
        # walks over the same author lists is two chances to disagree about who
        # is in a cluster, and the drawing is the half nobody would check.
        pairs: Counter = Counter()
        for names in per_record:
            shared = sorted(names & recurring_names)
            for left_index, left in enumerate(shared):
                for right in shared[left_index + 1:]:
                    pairs[(left, right)] += 1
        clusters.append({
            "size": len(indices),
            "pmids": [str(r.get("pmid", "")) for r in records],
            "journals": [{"journal": name, "count": count}
                         for name, count in sorted(journals.items(), key=lambda kv: (-kv[1], kv[0]))],
            "year_range": (min(years), max(years)) if years else None,
            "recurring_people": [{"name": name, "n_records": count}
                                 for name, count in sorted(recurring.items(), key=lambda kv: (-kv[1], kv[0]))
                                 if count >= RECURRENCE_MIN_RECORDS],
            # Ordered by the two names, never by the count: ordering edges by
            # weight is the first step to ordering the people on them.
            "edges": [{"a": left, "b": right, "n_records": count}
                      for (left, right), count in sorted(pairs.items())],
            "detailed": len(indices) >= CLUSTER_DETAIL_MIN,
        })

    singletons = [c for c in clusters if c["size"] == 1]
    return {
        "denominator": n,
        "min_n": MIN_N_COHESION,
        "suppressed": False,
        "n_clusters": len(clusters),
        "clusters": clusters,
        "largest_size": clusters[0]["size"] if clusters else None,
        "singleton_clusters": len(singletons),
        "records_in_singletons": len(singletons),
        "detail_min": CLUSTER_DETAIL_MIN,
    }
