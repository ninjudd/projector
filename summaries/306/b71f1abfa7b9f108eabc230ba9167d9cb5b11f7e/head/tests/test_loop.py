"""The `project loop` commands, against a tracked file in a temporary directory."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from projector import cli


class LoopCase(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.tracked = Path(scratch.name) / "tracked"

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["loop", *args, "--tracked", str(self.tracked)])
        return code, out.getvalue(), err.getvalue()


class DropTests(LoopCase):
    def test_appends_an_exclusion_and_keeps_every_other_line(self) -> None:
        self.tracked.write_text("# the loop's pull requests\nacme/app#1\n\nacme/app#2\n")

        code, out, err = self.run_cli("drop", "acme/app#2")

        self.assertEqual((0, ""), (code, err))
        self.assertEqual(f"dropped acme/app#2 from {self.tracked}\n", out)
        self.assertEqual("# the loop's pull requests\nacme/app#1\n\nacme/app#2\n!acme/app#2\n", self.tracked.read_text())

    def test_starts_its_line_on_a_line_of_its_own(self) -> None:
        self.tracked.write_text("acme/app#1")

        self.assertEqual(0, self.run_cli("drop", "acme/app#1")[0])

        self.assertEqual("acme/app#1\n!acme/app#1\n", self.tracked.read_text())

    def test_changes_nothing_for_a_pull_request_already_dropped_in_any_letter_case(self) -> None:
        self.tracked.write_text("acme/app#2\n  !ACME/App#2  \n")

        code, out, err = self.run_cli("drop", "acme/app#2", "--json")

        self.assertEqual((0, ""), (code, err))
        self.assertEqual({"action": "unchanged", "pr": "acme/app#2", "schema_version": 2, "tracked": str(self.tracked)},
                         json.loads(out))
        self.assertEqual("acme/app#2\n  !ACME/App#2  \n", self.tracked.read_text())

    def test_refuses_a_reference_that_is_not_owner_repo_number(self) -> None:
        self.tracked.write_text("acme/app#1\n")
        for written in ("acme/app", "acme/app#0", "#12", "acme/app#12 extra", "!acme/app#12"):
            with self.subTest(written=written):
                code, out, err = self.run_cli("drop", written)

                self.assertEqual((65, ""), (code, out))
                self.assertEqual(f"project: {written!r} is not owner/repo#number\n", err)
        self.assertEqual("acme/app#1\n", self.tracked.read_text())

    def test_refuses_a_tracked_file_that_does_not_exist(self) -> None:
        code, out, err = self.run_cli("drop", "acme/app#1")

        self.assertEqual((66, ""), (code, out))
        self.assertEqual(f"project: no tracked file at {self.tracked}\n", err)
        self.assertFalse(self.tracked.exists())


class RestoreTests(LoopCase):
    def test_removes_only_the_pull_requests_exclusions_in_any_letter_case(self) -> None:
        self.tracked.write_text("acme/app#1\nacme/app#2\n!acme/app#2\n!acme/app#1\n !ACME/APP#2\n")
        self.tracked.chmod(0o640)

        code, out, err = self.run_cli("restore", "Acme/App#2")

        self.assertEqual((0, ""), (code, err))
        self.assertEqual(f"restored Acme/App#2 to {self.tracked}\n", out)
        self.assertEqual("acme/app#1\nacme/app#2\n!acme/app#1\n", self.tracked.read_text())
        self.assertEqual(0o640, self.tracked.stat().st_mode & 0o777)

    def test_changes_nothing_for_a_pull_request_that_is_not_dropped(self) -> None:
        self.tracked.write_text("acme/app#1\n!acme/app#3\n")

        code, out, err = self.run_cli("restore", "acme/app#1", "--json")

        self.assertEqual((0, ""), (code, err))
        self.assertEqual("unchanged", json.loads(out)["action"])
        self.assertEqual("acme/app#1\n!acme/app#3\n", self.tracked.read_text())

    def test_drop_then_restore_leaves_the_file_as_it_was(self) -> None:
        self.tracked.write_text("acme/app#1\nacme/app#2\n")

        self.assertEqual(0, self.run_cli("drop", "acme/app#2")[0])
        self.assertEqual(0, self.run_cli("restore", "acme/app#2")[0])

        self.assertEqual("acme/app#1\nacme/app#2\n", self.tracked.read_text())

    def test_refuses_a_malformed_reference_and_a_missing_file(self) -> None:
        self.assertEqual(65, self.run_cli("restore", "acme/app")[0])
        self.tracked.unlink(missing_ok=True)
        self.assertEqual(66, self.run_cli("restore", "acme/app#1")[0])


class RemoveTests(LoopCase):
    def test_removes_the_pull_requests_lines_in_any_letter_case_and_keeps_every_other_line(self) -> None:
        self.tracked.write_text("# the loop's pull requests\nacme/app#1\n\n  ACME/App#2  \nacme/app#3\nacme/app#2\n"
                                "!acme/app#1\n")
        self.tracked.chmod(0o640)

        code, out, err = self.run_cli("remove", "Acme/app#2")

        self.assertEqual((0, ""), (code, err))
        self.assertEqual(f"removed Acme/app#2 from {self.tracked}\n", out)
        self.assertEqual("# the loop's pull requests\nacme/app#1\n\nacme/app#3\n!acme/app#1\n", self.tracked.read_text())
        self.assertEqual(0o640, self.tracked.stat().st_mode & 0o777)

    def test_leaves_the_pull_requests_exclusion_in_place(self) -> None:
        self.tracked.write_text("acme/app#2\n!ACME/app#2\n")

        self.assertEqual(0, self.run_cli("remove", "acme/app#2")[0])

        self.assertEqual("!ACME/app#2\n", self.tracked.read_text())

    def test_changes_nothing_for_a_pull_request_the_file_does_not_list(self) -> None:
        self.tracked.write_text("acme/app#1\n!acme/app#2\n# acme/app#2\n")
        inode = self.tracked.stat().st_ino

        code, out, err = self.run_cli("remove", "acme/app#2")

        self.assertEqual((0, ""), (code, err))
        self.assertEqual(f"acme/app#2 is not listed in {self.tracked}\n", out)
        self.assertEqual("acme/app#1\n!acme/app#2\n# acme/app#2\n", self.tracked.read_text())
        self.assertEqual(inode, self.tracked.stat().st_ino)

    def test_reports_its_action_as_json(self) -> None:
        self.tracked.write_text("acme/app#1\nacme/app#2\n")

        removed = self.run_cli("remove", "acme/app#2", "--json")
        unchanged = self.run_cli("remove", "acme/app#2", "--json")

        for (code, out, err), action in ((removed, "removed"), (unchanged, "unchanged")):
            with self.subTest(action=action):
                self.assertEqual((0, ""), (code, err))
                self.assertEqual({"action": action, "pr": "acme/app#2", "schema_version": 2,
                                  "tracked": str(self.tracked)}, json.loads(out))
        self.assertEqual("acme/app#1\n", self.tracked.read_text())

    def test_refuses_a_reference_that_is_not_owner_repo_number(self) -> None:
        self.tracked.write_text("acme/app#1\n")
        for written in ("acme/app", "acme/app#0", "#12", "acme/app#1 extra", "!acme/app#1"):
            with self.subTest(written=written):
                code, out, err = self.run_cli("remove", written)

                self.assertEqual((65, ""), (code, out))
                self.assertEqual(f"project: {written!r} is not owner/repo#number\n", err)
        self.assertEqual("acme/app#1\n", self.tracked.read_text())

    def test_refuses_a_tracked_file_that_does_not_exist(self) -> None:
        code, out, err = self.run_cli("remove", "acme/app#1")

        self.assertEqual((66, ""), (code, out))
        self.assertEqual(f"project: no tracked file at {self.tracked}\n", err)
        self.assertFalse(self.tracked.exists())


if __name__ == "__main__":
    unittest.main()
