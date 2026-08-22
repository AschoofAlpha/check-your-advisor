#!/usr/bin/env python3
"""Run every test file, optionally with third-party packages blocked.

    python tests/run_all.py
    python tests/run_all.py --block-third-party

The blocker has to live inside the child process, not this one: each test file
is executed as its own interpreter so that a crash or a `sys.exit` in one does
not end the run. So `--block-third-party` is passed down as a `-c` bootstrap
that installs an import hook and then hands control to the test file. Installing
it here would prove nothing about the files, which never import into this
process at all.

Counting is deliberately format-tolerant. Most files print
`Summary: N passed / M failed / T total`; one prints Chinese and marks each
assertion with `[PASS]`. A runner that understood only the first format would
silently score that file zero and still report success.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

BOOTSTRAP = """
import sys, runpy
BLOCKED = {"requests", "urllib3", "pandas", "numpy", "matplotlib",
           "fitz", "pymupdf", "openpyxl"}

class _Blocker:
    def find_module(self, name, path=None):
        return self.find_spec(name, path)
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError("third-party import blocked in this run: " + name)
        return None

sys.meta_path.insert(0, _Blocker())
runpy.run_path(sys.argv[1], run_name="__main__")
"""

SUMMARY_RE = re.compile(r"(\d+)\s+passed\s*/\s*(\d+)\s+failed")


def count(output: str) -> tuple[int, int]:
    m = SUMMARY_RE.search(output)
    if m:
        return int(m.group(1)), int(m.group(2))
    # The Chinese-formatted file: one [PASS] or [FAIL] per assertion.
    return output.count("[PASS]"), output.count("[FAIL]")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--block-third-party", action="store_true",
                    help="Fail any test file that imports requests, numpy, matplotlib, ...")
    args = ap.parse_args()

    files = sorted(HERE.glob("test_*.py"))
    if not files:
        print("no test files found", file=sys.stderr)
        return 2

    total_pass = total_fail = 0
    broken: list[str] = []

    for f in files:
        cmd = ([sys.executable, "-c", BOOTSTRAP, str(f)] if args.block_third_party
               else [sys.executable, str(f)])
        # Children inherit a locale-encoded stdout, and most test files print
        # Chinese. Without this the child raises UnicodeEncodeError on its first
        # print and this runner decodes whatever survived as the wrong codec —
        # which reads as a broken test rather than a broken pipe. The product
        # itself no longer depends on this being set; see _force_utf8_stdio in
        # cli.py, and test_stdio_encoding.py, which deliberately runs without it.
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              cwd=HERE.parent / "scripts", env=env)
        out = (proc.stdout or "") + (proc.stderr or "")
        passed, failed = count(out)
        total_pass += passed
        total_fail += failed
        status = "ok  " if proc.returncode == 0 and failed == 0 else "FAIL"
        if status == "FAIL":
            broken.append(f.name)
        print(f"  {status}  {f.name:<32} {passed:>5} passed  {failed} failed")
        if status == "FAIL":
            tail = "\n".join(out.strip().splitlines()[-6:])
            print("\n".join("        " + ln for ln in tail.splitlines()))

    mode = "third-party blocked" if args.block_third_party else "normal"
    print(f"\n{total_pass} passed / {total_fail} failed / {len(files)} files ({mode})")
    if broken:
        print("failing files: " + ", ".join(broken))
    return 1 if (total_fail or broken) else 0


if __name__ == "__main__":
    sys.exit(main())
