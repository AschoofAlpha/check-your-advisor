"""Locating and dating a harvested corpus.

Both helpers here used to live in `analysis.py`, which was deleted with the
`analyze` subcommand. They are the only parts of that module the surviving code
actually called: `profile.roles` needs the date parser, and both `download` and
`profile` need to find the most recent `papers_*.json`. Everything else in that
file existed to draw raster charts, and took matplotlib and numpy with it.

Standard library only, and it has to stay that way: `profile` reaches this
module through `roles.py`, and a third-party import here would put the whole
report behind an install again.
"""

from __future__ import annotations

import glob
import os
import re

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def find_latest_json(output_dir: str) -> str | None:
    """The most recently modified `papers_*.json` in `output_dir`, or None."""
    files = glob.glob(os.path.join(output_dir, "papers_*.json"))
    return max(files, key=os.path.getmtime) if files else None


def _date_iso(value: str) -> str:
    """Parse a PubMed publication date into `YYYY-MM-DD`.

    Deliberately lossy and deliberately total: PubMed dates arrive as "2014",
    "2014 Sep", "2014 Sep 11" and worse, and every consumer downstream wants a
    sortable string. Missing pieces are fabricated — month and day fall back to
    1, and a value with no four-digit year at all becomes 1900-01-01.

    That 1900 is load-bearing elsewhere: `roles.py` treats it as the marker for
    a record whose date could not be read, rather than as a real early paper.
    """
    value = str(value or "").strip()
    match = re.search(r"(\d{4})", value)
    year = int(match.group(1)) if match else 1900
    month = 1
    day = 1
    month_match = re.search(r"\b([A-Za-z]{3})[A-Za-z]*\b", value)
    if month_match:
        month = _MONTHS.get(month_match.group(1).lower(), 1)
    nums = [int(n) for n in re.findall(r"\b\d{1,2}\b", value)]
    if nums:
        day = max(1, min(nums[-1], 31))
    return f"{year:04d}-{month:02d}-{day:02d}"
