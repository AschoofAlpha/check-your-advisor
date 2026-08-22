"""Allow `python -m check_your_advisor ...`."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main() or 0)
