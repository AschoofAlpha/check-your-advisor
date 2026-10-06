#!/usr/bin/env python3
"""Check Your Advisor — the one entry point, for running from a clone.

    python run.py harvest --author "Wang Wei" --affiliation "Peking Union Medical College"
    python run.py profile --pi-name "Wang Wei"

Every verb is dispatched through here. The list, with what each one does, is
at the top of check_your_advisor/cli.py, and `python run.py <verb> --help`
describes one; `python run.py --version` prints the version.

The two above are the ones every run needs. `harvest` collects the papers and
decides which of them are really this person's; `profile` turns that corpus
into the report. They are separate commands because harvesting is slow and
bound by the network while the report is instant and offline — you harvest
once and re-read the record many times.

Nothing needs installing. The package below imports only the standard
library, so this file works from a fresh clone.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from check_your_advisor.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
