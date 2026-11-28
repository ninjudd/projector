"""The test runner in `tests/__main__.py`: no chunk can hold the run up."""

from __future__ import annotations

import contextlib
import io
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests import __main__ as runner

PROBE = '''
import os
import subprocess
import unittest


class Probe(unittest.TestCase):
    def test_reads_stdin_to_its_end(self):
        # As a fake CLI that pipes its input through `cat` does.
        self.assertEqual("", subprocess.run(["cat"], stdout=subprocess.PIPE, text=True).stdout)

    def test_waits_on_a_child_that_never_ends(self):
        subprocess.Popen(["sleep", "60"]).wait()

    def test_records_a_child_that_never_ends(self):
        child = subprocess.Popen(["sleep", "60"])
        pidfile = os.environ["PROBE_PID_FILE"]
        with open(pidfile + ".new", "w") as f:
            f.write(str(child.pid))
        os.replace(pidfile + ".new", pidfile)
        child.wait()
'''


class RunTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = Path(tempfile.mkdtemp())
        (folder / "runner_probe.py").write_text(PROBE)
        path = os.pathsep.join(filter(None, [str(folder), os.environ.get("PYTHONPATH")]))
        patcher = mock.patch.dict(os.environ, {"PYTHONPATH": path})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_chunk_reads_no_stdin_even_when_the_runner_has_one_that_stays_open(self) -> None:
        # A runner started in the background can hold a stdin that never closes, and a chunk that inherits it
        # waits forever on a test that reads stdin.
        read, write = os.pipe()
        saved = os.dup(0)
        os.dup2(read, 0)
        self.addCleanup(os.close, write)
        self.addCleanup(os.close, read)
        self.addCleanup(os.close, saved)
        self.addCleanup(os.dup2, saved, 0)

        result = runner.run(["runner_probe.Probe.test_reads_stdin_to_its_end"], False, timeout=30)

        self.assertEqual(0, result.returncode, result.stderr)

    def test_a_chunk_past_its_timeout_is_stopped_with_its_children_and_fails(self) -> None:
        start = time.monotonic()

        result = runner.run(["runner_probe.Probe.test_waits_on_a_child_that_never_ends"], False, timeout=1)

        self.assertLess(time.monotonic() - start, 10, "the child's open pipes must not keep the run waiting")
        self.assertEqual(1, result.returncode)
        self.assertIn("The chunk ran past 1 seconds and was stopped.", result.stderr)

    def test_an_interrupt_stops_the_running_chunks_with_their_children(self) -> None:
        # Each chunk runs in a session of its own, so the SIGINT that Ctrl-C sends the terminal's foreground
        # process group reaches only the runner.
        pidfile = Path(tempfile.mkdtemp()) / "pid"
        process = subprocess.Popen(
            [sys.executable, "-m", "tests", "runner_probe.Probe.test_records_a_child_that_never_ends"],
            cwd=runner.ROOT, env={**os.environ, "PROBE_PID_FILE": str(pidfile)}, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        self.addCleanup(process.kill)
        deadline = time.monotonic() + 30
        while not pidfile.exists():
            self.assertLess(time.monotonic(), deadline, "the chunk never started its child")
            time.sleep(0.05)
        child = int(pidfile.read_text())
        self.addCleanup(kill, child)
        start = time.monotonic()

        os.killpg(process.pid, signal.SIGINT)
        process.communicate(timeout=20)

        self.assertLess(time.monotonic() - start, 10)
        deadline = time.monotonic() + 5
        while alive(child):
            self.assertLess(time.monotonic(), deadline, "the chunk's child outlived the interrupted run")
            time.sleep(0.05)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def kill(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL)


class MainTests(unittest.TestCase):
    def test_help_names_the_timeout_in_seconds_as_the_usage_line_does(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            runner.main(["--help"])

        self.assertIn("--timeout SECONDS", out.getvalue())


if __name__ == "__main__":
    unittest.main()
