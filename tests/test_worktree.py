"""`project worktree`: print the folder of the worktree that has a branch, or a pull request's branch, checked out."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from projector import cli, review

REPO = "acme/app"
# The setup commits, and a CI runner has no Git identity configured.
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.com"}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV}).stdout.strip()


class WorktreeCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        # Resolved, because macOS reaches the temporary folder through a symlink that Git prints resolved.
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.checkout = self.tmp / "checkout"
        git(self.tmp, "init", "--quiet", "-b", "trunk", str(self.checkout))
        git(self.checkout, "commit", "--quiet", "--allow-empty", "-m", "trunk")
        git(self.checkout, "remote", "add", "origin", f"git@github.com:{REPO}.git")
        self.feature = self.add("feature", "feature")
        self.nested = self.add("fix/login", "nested folder")
        self.add_detached("review")
        self.gone = self.add("gone", "gone")
        shutil.rmtree(self.gone)
        self.prs: dict[int, dict] = {}
        github = mock.patch.object(review, "run_gh", self.github)
        github.start()
        self.addCleanup(github.stop)

    def add(self, branch: str, folder: str) -> Path:
        path = self.tmp / folder
        git(self.checkout, "worktree", "add", "--quiet", "-b", branch, str(path))
        return path

    def add_detached(self, folder: str) -> Path:
        path = self.tmp / folder
        git(self.checkout, "worktree", "add", "--quiet", "--detach", str(path))
        return path

    def github(self, args: list[str], token: str | None = None) -> str:
        for number, pr in self.prs.items():
            if args == ["api", f"repos/{REPO}/pulls/{number}"]:
                return json.dumps(pr)
        raise review.NotFound("HTTP 404")

    def project(self, *args: str, cwd: Path | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli.Path, "cwd", return_value=cwd or self.checkout), \
                redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["worktree", *args])
        return code, out.getvalue(), err.getvalue()

    def test_prints_the_worktree_that_has_the_branch_checked_out(self) -> None:
        self.assertEqual((0, f"{self.feature}\n", ""), self.project("feature"))
        # A branch name with a slash, in a folder whose name has a space, comes back whole.
        self.assertEqual((0, f"{self.nested}\n", ""), self.project("fix/login"))
        # The main checkout is a worktree too.
        self.assertEqual((0, f"{self.checkout}\n", ""), self.project("trunk"))

    def test_finds_another_worktree_from_inside_a_linked_one(self) -> None:
        self.assertEqual((0, f"{self.checkout}\n", ""), self.project("trunk", cwd=self.feature))

    def test_matches_the_whole_branch_name(self) -> None:
        code, out, err = self.project("feat")
        self.assertEqual((66, ""), (code, out))
        self.assertEqual("project: no worktree has branch feat checked out\n", err)

    def test_refuses_a_branch_checked_out_only_in_a_folder_that_is_gone(self) -> None:
        code, out, err = self.project("gone")
        self.assertEqual((66, ""), (code, out))
        self.assertIn("no worktree has branch gone checked out", err)

    def test_prints_the_worktree_of_a_pull_requests_head_branch(self) -> None:
        self.prs[12] = {"number": 12, "head": {"ref": "fix/login", "sha": "0" * 40}}
        self.assertEqual((0, f"{self.nested}\n", ""), self.project("12"))

    def test_names_the_pull_requests_branch_when_no_worktree_has_it(self) -> None:
        self.prs[13] = {"number": 13, "head": {"ref": "elsewhere", "sha": "0" * 40}}
        code, out, err = self.project("13")
        self.assertEqual((66, ""), (code, out))
        self.assertEqual("project: no worktree has pull request 13's branch elsewhere checked out\n", err)

    def test_says_when_the_pull_request_cannot_be_read(self) -> None:
        code, out, err = self.project("14")
        self.assertNotEqual(0, code)
        self.assertEqual("", out)
        self.assertIn(f"{REPO}#14 does not exist", err)


if __name__ == "__main__":
    unittest.main()
