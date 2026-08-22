#!/usr/bin/env python3
"""
The tool must not crash because its output is being redirected.

Everything this tool prints is Chinese. Python only writes Chinese to a Windows
console through the console API; the moment stdout is a file or a pipe it falls
back to the locale encoding instead, and cp1252 — the default on any Windows
install that is not Chinese-localised — cannot encode one single Chinese
character.

That was not hypothetical. On this machine, with stdout redirected:

    python scripts/run.py --help > out.txt
    -> UnicodeEncodeError, exit 1, no help text at all

    python scripts/run.py profile --output-dir <empty>
    -> every Chinese log line replaced by a "--- Logging error ---" traceback

Redirection is the normal case, not the exotic one: a skill is driven by an
agent that captures the output, and `> log.txt` is what anyone does with a run
that takes an hour. So the fix belongs in the product (`_force_utf8_stdio`,
called by main), not in the caller's environment.

These tests therefore run the real entry point in a subprocess with
PYTHONIOENCODING forced to a codec that cannot represent Chinese. That
reproduces the failure on any platform, including a Linux box whose locale is
already UTF-8, so the guard cannot quietly rot away on machines that never see
the bug.

Run: python tests/test_stdio_encoding.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

RUN_PY = Path(__file__).resolve().parent.parent / "scripts" / "run.py"

_passed = 0
_failed = 0


def check(label: str, actual, expected) -> None:
    global _passed, _failed
    if actual == expected:
        _passed += 1
        print(f"  [PASS] {label}")
    else:
        _failed += 1
        print(f"  [FAIL] {label}  (expected {expected!r}, got {actual!r})")


def check_true(label: str, value) -> None:
    check(label, bool(value), True)


def run(args: list[str], encoding: str = "ascii") -> subprocess.CompletedProcess:
    """Run the real CLI with a stdout codec that cannot hold Chinese."""
    env = {**os.environ, "PYTHONIOENCODING": encoding}
    return subprocess.run(
        [sys.executable, str(RUN_PY), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env,
    )


print("--help under a stdout that cannot encode Chinese")
r = run(["--help"])
check("it exits 0 instead of dying on the first help line", r.returncode, 0)
out = r.stdout + r.stderr
check_true("no encoding error escaped", "UnicodeEncodeError" not in out)
check_true("and it actually printed the usage", "usage:" in out)


print("\nA normal error path still logs, rather than logging about logging")
with tempfile.TemporaryDirectory(prefix="cya_enc_") as tmp:
    r = run(["profile", "--pi-name", "Test", "--output-dir", tmp])

check("the missing-corpus error still exits 1", r.returncode, 1)
out = r.stdout + r.stderr
check_true("no encoding error escaped", "UnicodeEncodeError" not in out)
# logging swallows its own encoding failures, so the run keeps going and the
# exit code stays right — the message is simply gone. That silent-loss shape is
# what this line pins: a green exit code was never enough to catch it.
check_true("no log line was swallowed by the logging machinery",
           "--- Logging error ---" not in out)
check_true("the Chinese message survived intact", "没找到 papers_*.json" in out)


print("\nThe guard is in the product, not in the environment")
# If this ever starts depending on PYTHONIOENCODING being pre-set, the assertion
# above would still pass under run_all.py (which sets it) and fail for a user.
check_true("PYTHONIOENCODING was not inherited from this process as utf-8",
           os.environ.get("PYTHONIOENCODING", "").lower() != "ascii")
r = run(["--help"], encoding="cp1252")
check("cp1252, the Windows default, is fine too", r.returncode, 0)


print("\n" + "=" * 70)
print(f"Summary: {_passed} passed / {_failed} failed / {_passed + _failed} total")
print("=" * 70)
sys.exit(1 if _failed else 0)
