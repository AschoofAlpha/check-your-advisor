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


# ======================================================================
# The declared-count gate
# ======================================================================
#
# Five separate times in this repository a number in the prose stopped matching
# the code and was printed to a reader as measured fact — most recently a README
# claiming 2947 assertions beside the word "实测" and a date. The counts below
# are the two nobody can hold in their head: how many assertions this suite runs
# and how many files it runs them from. This run measures both. The docs claim
# both. If they disagree, the run fails and names the line to edit.
#
# Deleting the sentence is a failure too. A gate you can silence by removing the
# claim is not a gate — hence `missing` below.
#
# What this does NOT check is anything a normal test can reach: figure counts,
# verb counts, the blocked-module list, the import claims. Those live in
# tests/test_declared_counts.py, because they need no measurement.

REPO = HERE.parent

# Each entry is one sentence that states a count, and the groups it must yield.
#   full    — assertions in a plain run
#   blocked — assertions surviving --block-third-party
#   files   — how many test files the suite has
DECLARED: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("README.md",
     re.compile(r"(?P<full>[\d,]+) assertions across (?P<files>\d+) files")),
    ("README.zh-CN.md",
     re.compile(r"(?P<files>\d+) 个文件 (?P<full>[\d,]+) 条断言")),
    ("pyproject.toml",
     re.compile(r"(?P<blocked>[\d,]+) of (?P<full>[\d,]+) assertions still pass"
                r" \((?P<files>\d+) files")),
)


def read_declarations() -> tuple[list[tuple[str, int, dict[str, int]]], list[str]]:
    """Every declared count, with the 1-based line it sits on."""
    found: list[tuple[str, int, dict[str, int]]] = []
    missing: list[str] = []
    for name, rx in DECLARED:
        path = REPO / name
        if not path.exists():
            missing.append(f"{name} (file not found)")
            continue
        text = path.read_text(encoding="utf-8")
        m = rx.search(text)
        if m is None:
            missing.append(f"{name} (no sentence matching /{rx.pattern}/)")
            continue
        line = text[: m.start()].count("\n") + 1
        found.append((name, line, {k: int(v.replace(",", ""))
                                   for k, v in m.groupdict().items() if v is not None}))
    return found, missing


def check_declared_counts(measured_total: int, measured_files: int, blocked: bool) -> bool:
    """True if the docs still describe this run. Prints what to edit if not."""
    found, missing = read_declarations()
    which = "blocked" if blocked else "full"
    mode = "--block-third-party" if blocked else "a plain run"
    problems: list[str] = []

    for name in missing:
        problems.append(
            f"  {name}\n"
            f"      the sentence that declares the count is gone. Restore it —\n"
            f"      removing the claim is not how this gate is satisfied.")

    for name, line, counts in found:
        if which in counts and counts[which] != measured_total:
            problems.append(
                f"  {name}:{line}\n"
                f"      says {counts[which]} assertions under {mode};"
                f" this run produced {measured_total}.\n"
                f"      -> write {measured_total} on that line.")
        if "files" in counts and counts["files"] != measured_files:
            problems.append(
                f"  {name}:{line}\n"
                f"      says {counts['files']} test files; this run found {measured_files}.\n"
                f"      -> write {measured_files} on that line.")

    # The three sentences must also agree with each other, or fixing one run
    # leaves the other mode declaring a number nothing measures.
    for field in ("full", "files"):
        values = {counts[field] for _n, _l, counts in found if field in counts}
        if len(values) > 1:
            where = ", ".join(f"{n}:{l}={c[field]}" for n, l, c in found if field in c)
            problems.append(
                f"  the three declarations disagree on '{field}': {where}\n"
                f"      -> make them equal before deciding which is right.")

    if not problems:
        return True

    print("\n" + "=" * 70)
    print("DECLARED-COUNT DRIFT — the docs claim a number this run did not produce.")
    print("=" * 70)
    print(f"measured here: {measured_total} assertions across {measured_files}"
          f" files ({'third-party blocked' if blocked else 'normal'})\n")
    for p in problems:
        print(p)
    print("\nAlso refresh the 'measured <date>' stamp beside each number you edit;"
          "\na stale date on a corrected number is the same failure wearing a hat.")
    print("This gate is tests/run_all.py::check_declared_counts. Editing it to"
          "\nagree with the docs is not a fix.")
    print("=" * 70)
    return False


def _force_utf8_stdio() -> None:
    """Let this runner print a failing child's output without dying on it.

    The child already gets `PYTHONIOENCODING=utf-8` below, so it prints Chinese
    happily. This process did not, and it echoes the last six lines of a failing
    file — so on Windows, where a redirected stream falls back to cp1252, *any*
    failure whose tail held a Chinese character (a log line, or just this repo's
    own path) ended the run with UnicodeEncodeError instead of a report. The
    suite was green often enough for that to go unnoticed: the crash could only
    fire on the one run where something was already wrong.

    Same fix, same reason as `cli._force_utf8_stdio`, which the product applies
    to itself.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # already replaced, e.g. by a capture
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def main() -> int:
    _force_utf8_stdio()
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

    # After the summary line, never before it: the summary is what a reader and
    # every script quote, and it must survive a drifted README unchanged.
    declared_ok = check_declared_counts(total_pass + total_fail, len(files),
                                        args.block_third_party)
    return 1 if (total_fail or broken or not declared_ok) else 0


if __name__ == "__main__":
    sys.exit(main())
