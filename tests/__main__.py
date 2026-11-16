"""Run the test suite across worker processes.

    PYTHONPATH=src python3 -m tests [-j JOBS] [-v] [NAME ...]

Each worker runs `python3 -m unittest` on a chunk of one test class, so a
worker shares nothing with another but the machine. Chunks start largest
first and a free worker takes the next, which keeps every core busy until the
end. A NAME narrows the run to a module, class, or test, as unittest takes it.
The run prints each failing chunk's output, then one summary, and exits 1 when
any chunk fails.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
import unittest
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHUNK = 8


def tests_in(suite: unittest.TestSuite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from tests_in(test)
        else:
            yield test


def chunks(names: list[str]) -> list[list[str]]:
    """Split the selected tests into lists of test ids, at most CHUNK from one class each, largest first."""
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromNames(names) if names else loader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
    by_class: dict[str, list[str]] = defaultdict(list)
    for test in tests_in(suite):
        if isinstance(test, unittest.loader._FailedTest):
            # A module that fails to import: run its name, so unittest reports the error.
            by_class[test.id()] = []
            continue
        by_class[f"{type(test).__module__}.{type(test).__qualname__}"].append(test.id())
    jobs = []
    for name, ids in by_class.items():
        if not ids:
            jobs.append([name.removeprefix("unittest.loader._FailedTest.")])
        for start in range(0, len(ids), CHUNK):
            jobs.append(ids[start:start + CHUNK])
    return sorted(jobs, key=len, reverse=True)


def run(ids: list[str], verbose: bool) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "unittest", *(["-v"] if verbose else []), *ids],
                          cwd=ROOT, capture_output=True, text=True)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m tests", description=__doc__.split("\n\n")[0])
    parser.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 1, help="worker processes (default: one per CPU)")
    parser.add_argument("-v", "--verbose", action="store_true", help="print every chunk's unittest output")
    parser.add_argument("names", nargs="*", help="modules, classes, or tests to run (default: the whole suite)")
    args = parser.parse_args(argv)

    start = time.monotonic()
    jobs = chunks(args.names)
    ran = skipped = 0
    failed: list[tuple[list[str], str]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        for ids, result in zip(jobs, pool.map(lambda ids: run(ids, args.verbose), jobs)):
            output = result.stderr + result.stdout
            if args.verbose:
                print(output, end="", flush=True)
            ran += sum(int(n) for n in re.findall(r"^Ran (\d+) tests?", output, re.M))
            skipped += sum(int(n) for n in re.findall(r"skipped=(\d+)", output))
            if result.returncode != 0:
                failed.append((ids, output))
            elif not args.verbose:
                print(".", end="", flush=True)
    print()
    for ids, output in failed:
        print("=" * 70, f"\nFAILED chunk: {ids[0].rsplit('.', 1)[0] if len(ids) > 1 else ids[0]}\n", output, sep="")
    print("-" * 70)
    print(f"Ran {ran} tests in {time.monotonic() - start:.1f}s across {len(jobs)} chunks, {args.jobs} at a time")
    note = f" (skipped={skipped})" if skipped else ""
    print(f"FAILED ({len(failed)} of {len(jobs)} chunks){note}" if failed else f"OK{note}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
