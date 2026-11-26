"""Run the test suite across worker processes.

    PYTHONPATH=src python3 -m tests [-j JOBS] [-v] [--timeout SECONDS] [NAME ...]

Each worker runs `python3 -m unittest` on a chunk of one test class, so a
worker shares nothing with another but the machine. Chunks start largest
first and a free worker takes the next, which keeps every core busy until the
end. A NAME narrows the run to a module, class, or test, as unittest takes it.
The run prints each failing chunk's output, then one summary, and exits 1 when
any chunk fails.

A chunk reads nothing from the terminal, and one that runs past `--timeout`
seconds is stopped with every process it started and fails, so a test waiting
on something that never comes fails its chunk instead of holding up the run.
Ctrl-C stops every running chunk the same way.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import signal
import subprocess
import sys
import threading
import time
import unittest
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHUNK = 8
# The slowest chunk takes under a minute, so one that runs five is waiting on something that never comes.
TIMEOUT = 300


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


def stop(process: subprocess.Popen) -> None:
    """Kill a chunk with every process it started."""
    if hasattr(os, "killpg"):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    else:
        # Windows has no process groups to kill, so only the chunk itself stops, and a process it started
        # that holds its output open still keeps `communicate` waiting.
        process.kill()


# A chunk runs in a session of its own, so Ctrl-C reaches only the runner, which stops these itself.
running: set[subprocess.Popen] = set()
running_lock = threading.Lock()
interrupted = threading.Event()


def interrupt(pool: ThreadPoolExecutor) -> None:
    """Start no more chunks, and stop every one that is running."""
    pool.shutdown(wait=False, cancel_futures=True)
    with running_lock:
        interrupted.set()
        for process in running:
            stop(process)


def run(ids: list[str], verbose: bool, timeout: float = TIMEOUT) -> subprocess.CompletedProcess:
    """Run one chunk with no stdin, in a process group of its own, so that past `timeout` the whole group is
    killed: a process a test started that waits on input would otherwise hold the output pipes open."""
    command = [sys.executable, "-m", "unittest", *(["-v"] if verbose else []), *ids]
    with subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, start_new_session=True) as process:
        with running_lock:
            running.add(process)
            if interrupted.is_set():
                stop(process)
        try:
            out, err = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            stop(process)
            out, err = process.communicate()
            err += f"\nThe chunk ran past {timeout:g} seconds and was stopped.\n"
            return subprocess.CompletedProcess(command, 1, out, err)
        finally:
            with running_lock:
                running.discard(process)
    return subprocess.CompletedProcess(command, process.returncode, out, err)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m tests", description=__doc__.split("\n\n")[0])
    parser.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 1, help="worker processes (default: one per CPU)")
    parser.add_argument("-v", "--verbose", action="store_true", help="print every chunk's unittest output")
    parser.add_argument("--timeout", type=float, default=TIMEOUT, metavar="SECONDS",
                        help=f"seconds a chunk may run before it is stopped and fails (default: {TIMEOUT})")
    parser.add_argument("names", nargs="*", help="modules, classes, or tests to run (default: the whole suite)")
    args = parser.parse_args(argv)

    start = time.monotonic()
    jobs = chunks(args.names)
    ran = skipped = 0
    failed: list[tuple[list[str], str]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        try:
            for ids, result in zip(jobs, pool.map(lambda ids: run(ids, args.verbose, args.timeout), jobs)):
                output = result.stderr + result.stdout
                if args.verbose:
                    print(output, end="", flush=True)
                ran += sum(int(n) for n in re.findall(r"^Ran (\d+) tests?", output, re.M))
                skipped += sum(int(n) for n in re.findall(r"skipped=(\d+)", output))
                if result.returncode != 0:
                    failed.append((ids, output))
                elif not args.verbose:
                    print(".", end="", flush=True)
        except KeyboardInterrupt:
            interrupt(pool)
            raise
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
