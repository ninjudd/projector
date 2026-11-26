"""The test runner in `tests/__main__.py`: no chunk can hold the run up."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests import __main__ as runner

PROBE = '''
import subprocess
import time
import unittest


class Probe(unittest.TestCase):
    def test_reads_stdin_to_its_end(self):
        # As a fake CLI that pipes its input through `cat` does.
        self.assertEqual("", subprocess.run(["cat"], stdout=subprocess.PIPE, text=True).stdout)

    def test_waits_on_a_child_that_never_ends(self):
        subprocess.Popen(["sleep", "60"]).wait()

    def test_never_ends(self):
        time.sleep(60)
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

    def test_a_chunk_past_its_timeout_fails_where_process_groups_cannot_be_killed(self) -> None:
        # Windows has no `os.killpg`.
        killpg = os.killpg
        del os.killpg
        self.addCleanup(setattr, os, "killpg", killpg)
        start = time.monotonic()

        result = runner.run(["runner_probe.Probe.test_never_ends"], False, timeout=1)

        self.assertLess(time.monotonic() - start, 10)
        self.assertEqual(1, result.returncode)
        self.assertIn("The chunk ran past 1 seconds and was stopped.", result.stderr)


if __name__ == "__main__":
    unittest.main()
