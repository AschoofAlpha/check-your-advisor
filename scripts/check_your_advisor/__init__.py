"""
check-your-advisor — read what PubMed records about a researcher and report it
back as facts with denominators.

The verbs are dispatched from `cli.py`, whose docstring lists every one of them;
`profile/` builds the report, and the modules beside it each feed one part of
it. `scripts/run.py` is the entry point from a clone, `python -m
check_your_advisor` and the `check-your-advisor` console script the entry points
from an install.

`__version__` is the version this source tree carries. It must agree with
`version` in pyproject.toml and in .claude-plugin/plugin.json — for a copy
installed from a plugin marketplace, the last one is what Claude Code compares
to decide whether there is an update — and tests/test_declared_counts.py fails
when the three disagree.
"""

__version__ = "0.3.0"

__all__ = ["__version__"]
