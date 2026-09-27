"""The `project review` commands, against a real Git remote and a fake GitHub.

Git runs for real: a bare origin holds `main` and GitHub's `refs/pull/<n>/head`,
and the checkout is a clone of it. `gh` is replaced by `FakeGitHub`, which
answers the calls the commands make and records each one with the token it
carried, so a test can see who posted what.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from projector import cli, review
from projector.review import NotFound

REPO = "acme/app"


def git(cwd: Path, *args: str) -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


class FakeGitHub:
    """The slice of GitHub the review commands use, for one pull request."""

    def __init__(self, head: str, base_sha: str) -> None:
        self.pr = {"state": "open", "head": {"sha": head, "repo": {"full_name": REPO}},
                   "base": {"ref": "trunk", "sha": base_sha, "repo": {"full_name": REPO}}}
        self.calls: list[tuple[list[str], str | None]] = []
        self.comments: dict[int, str] = {}
        self.pages: list[dict] = []
        self.missing = False

    def __call__(self, args: list[str], token: str | None = None) -> str:
        self.calls.append((list(args), token))
        if args[:2] == ["auth", "token"]:
            return f"token-{args[-1]}\n"
        if args == ["api", "user", "--jq", ".login"]:
            return "operator\n"
        if args[:2] == ["api", "graphql"]:
            after = next((a.split("=", 1)[1] for a in args if a.startswith("after=")), None)
            index = 0 if after is None else int(after)
            return json.dumps(self.pages[index])
        if args == ["api", f"repos/{REPO}/pulls/1"]:
            if self.missing:
                raise NotFound("gh: Not Found (HTTP 404)")
            return json.dumps(self.pr)
        if args[:2] == ["api", f"repos/{REPO}/issues/1/comments"]:
            comment_id = 100 + len(self.comments) + 1
            self.comments[comment_id] = args[3].removeprefix("body=")
            return json.dumps({"id": comment_id, "created_at": "2026-09-27T01:02:03Z"})
        if args[:3] == ["api", "-X", "PATCH"]:
            comment_id = int(args[3].rsplit("/", 1)[1])
            self.comments[comment_id] = args[5].removeprefix("body=")
            return "{}"
        if len(args) == 2 and args[1].startswith(f"repos/{REPO}/issues/comments/"):
            return json.dumps({"body": self.comments[int(args[1].rsplit("/", 1)[1])]})
        raise AssertionError(f"unexpected gh call: {args}")

    def posts(self) -> list[tuple[list[str], str | None]]:
        return [(args, token) for args, token in self.calls if "-f" in args and any(a.startswith("body=") for a in args)]


class ReviewCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.tmp = tmp
        self.origin = tmp / "origin.git"
        git(tmp, "init", "--quiet", "--bare", "-b", "trunk", str(self.origin))
        work = tmp / "work"
        git(tmp, "init", "--quiet", "-b", "trunk", str(work))
        (work / "README.md").write_text("main\n")
        git(work, "add", ".")
        git(work, "commit", "--quiet", "-m", "main")
        self.base = git(work, "rev-parse", "HEAD")
        git(work, "remote", "add", "origin", str(self.origin))
        git(work, "push", "--quiet", "origin", "trunk")
        self.work = work
        self.head = self.push_head("first head")
        self.checkout = tmp / "checkout"
        git(tmp, "clone", "--quiet", str(self.origin), str(self.checkout))
        self.state = tmp / "state"
        self.github = FakeGitHub(self.head, self.base)
        patcher = mock.patch.object(review, "run_gh", self.github)
        patcher.start()
        self.addCleanup(patcher.stop)

    def push_head(self, text: str) -> str:
        (self.work / "change.txt").write_text(text + "\n")
        git(self.work, "add", ".")
        git(self.work, "commit", "--quiet", "-m", text)
        git(self.work, "push", "--quiet", "--force", "origin", "HEAD:refs/pull/1/head")
        return git(self.work, "rev-parse", "HEAD")

    def project(self, *args: str, cwd: Path | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli.Path, "cwd", return_value=cwd or self.checkout), \
             redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["review", *args])
        return code, out.getvalue(), err.getvalue()

    def setup_review(self, *extra: str) -> tuple[int, str, str]:
        return self.project("setup", "1", "--repo", REPO, "--checkout", str(self.checkout),
                            "--state-dir", str(self.state), "--model", "claude-opus-5-5", "--effort", "low", *extra)

    def read_state(self) -> dict:
        return json.loads((self.state / REPO / "1.json").read_text())


class SetupTests(ReviewCase):
    def test_sets_up_the_exact_head_and_posts_the_start_comment_as_the_reviewer(self) -> None:
        code, out, err = self.setup_review()

        self.assertEqual(0, code, err)
        state = self.read_state()
        self.assertEqual(self.head, state["sha"])
        self.assertEqual(self.base, state["base"])
        self.assertEqual(self.head, git(Path(state["worktree"]), "rev-parse", "HEAD"))
        self.assertEqual({"id": 101, "created_at": "2026-09-27T01:02:03Z"}, state["start_comment"])
        self.assertEqual(1, state["heads"])
        self.assertEqual(
            f"{review.START_MARK} **Projector review started** · model `claude-opus-5-5` · effort `low` · "
            f"reviewing `{self.head[:7]}`\n\n<!-- projector-start v=1 sha={self.head} -->\n",
            self.github.comments[101],
        )
        self.assertIn(f"reviewing {REPO}#1 at {self.head}", out)

    def test_posts_with_a_token_for_that_call_and_never_switches_accounts(self) -> None:
        code, _, err = self.setup_review()

        self.assertEqual(0, code, err)
        posts = self.github.posts()
        self.assertEqual(1, len(posts))
        self.assertEqual("token-operator", posts[0][1])
        for args, token in self.github.calls:
            self.assertNotIn("switch", args)
            if args not in [a for a, _ in posts]:
                self.assertIsNone(token, f"only posting carries the reviewer's token: {args}")

    def test_the_reviewer_comes_from_review_username(self) -> None:
        (self.checkout / ".projector.toml").write_text('[review]\nusername = "review-bot"\n')

        code, _, err = self.setup_review()

        self.assertEqual(0, code, err)
        self.assertEqual("token-review-bot", self.github.posts()[0][1])
        self.assertEqual("review-bot", self.read_state()["reviewer"])

    def test_refuses_a_closed_pull_request(self) -> None:
        self.github.pr["state"] = "closed"

        code, _, err = self.setup_review()

        self.assertEqual(1, code)
        self.assertIn("is closed", err)
        self.assertEqual([], self.github.posts())
        self.assertFalse((self.state / REPO / f"1-{self.head}.lock").exists())

    def test_refuses_a_head_from_a_fork(self) -> None:
        self.github.pr["head"]["repo"] = {"full_name": "someone/app"}

        code, _, err = self.setup_review()

        self.assertEqual(1, code)
        self.assertIn("someone/app", err)
        self.assertEqual([], self.github.posts())

    def test_refuses_a_missing_pull_request(self) -> None:
        self.github.missing = True

        code, _, err = self.setup_review()

        self.assertEqual(1, code)
        self.assertIn("does not exist", err)

    def test_a_second_instance_for_the_same_head_exits_without_posting(self) -> None:
        first = self.setup_review()
        second = self.setup_review()

        self.assertEqual(0, first[0], first[2])
        self.assertEqual(1, second[0])
        self.assertIn("holds", second[2])
        self.assertEqual(1, len(self.github.posts()))

    def test_refuses_when_the_fetched_head_is_not_the_head_github_reports(self) -> None:
        # GitHub's answer and the fetched ref disagree when the head moves in
        # between; the SHA is never taken on trust from either alone.
        self.github.pr["head"]["sha"] = self.base

        code, _, err = self.setup_review()

        self.assertEqual(1, code)
        self.assertIn("moved", err)
        self.assertEqual([], self.github.posts())


class MoveTests(ReviewCase):
    def test_follows_a_moved_head_and_edits_the_start_comment_in_place(self) -> None:
        self.assertEqual(0, self.setup_review()[0])
        old = self.head
        new = self.push_head("second head")
        self.github.pr["head"]["sha"] = new

        code, out, err = self.project("move", "1", "--repo", REPO, "--state-dir", str(self.state))

        self.assertEqual(0, code, err)
        state = self.read_state()
        self.assertEqual(new, state["sha"])
        self.assertEqual(2, state["heads"])
        self.assertEqual(new, git(Path(state["worktree"]), "rev-parse", "HEAD"))
        body = self.github.comments[101]
        self.assertEqual([101], list(self.github.comments), "edited in place, not a second comment")
        self.assertIn(f"reviewing `{new[:7]}`", body)
        self.assertIn(f"moved from `{old[:7]}`", body)
        self.assertIn(f"sha={new}", body)
        self.assertFalse((self.state / REPO / f"1-{old}.lock").exists())
        self.assertTrue((self.state / REPO / f"1-{new}.lock").exists())

    def test_refuses_a_head_that_did_not_move(self) -> None:
        self.assertEqual(0, self.setup_review()[0])

        code, _, err = self.project("move", "1", "--repo", REPO, "--state-dir", str(self.state))

        self.assertEqual(1, code)
        self.assertIn("nothing to move", err)

    def test_refuses_without_a_review_set_up(self) -> None:
        code, _, err = self.project("move", "1", "--repo", REPO, "--state-dir", str(self.state))

        self.assertEqual(1, code)
        self.assertIn("project review setup 1", err)


def thread(thread_id: str, body: str, *, resolved: bool = False, outdated: bool = False,
           path: str = "src/x.py", line: int | None = 3, original: int | None = None) -> dict:
    return {"id": thread_id, "isResolved": resolved, "isOutdated": outdated, "path": path,
            "line": line, "originalLine": original, "comments": {"nodes": [{"body": body}]}}


def page(nodes: list[dict], next_cursor: str | None) -> dict:
    return {"data": {"repository": {"pullRequest": {"reviewThreads": {
        "pageInfo": {"hasNextPage": next_cursor is not None, "endCursor": next_cursor}, "nodes": nodes}}}}}


class CensusTests(ReviewCase):
    FINDING = "<!-- projector-finding v=1 priority=P2 --> **P2 · A nil deref**"

    def setUp(self) -> None:
        super().setUp()
        self.github.pages = [
            page([thread("T1", self.FINDING), thread("T2", self.FINDING, resolved=True),
                  thread("T3", "Could this be a constant?")], "1"),
            page([thread("T4", self.FINDING, outdated=True, line=None, original=7, path="src/y.py")], None),
        ]

    def test_counts_finding_threads_across_pages_and_marks_outdated_ones(self) -> None:
        code, out, err = self.project("census", "1", "--repo", REPO)

        self.assertEqual(0, code, err)
        self.assertEqual(["3 finding threads: 1 resolved, 2 open", "src/x.py:3", "src/y.py:7 outdated"],
                         out.splitlines())
        graphql = [args for args, _ in self.github.calls if args[:2] == ["api", "graphql"]]
        self.assertEqual(2, len(graphql))
        self.assertIn("after=1", graphql[1])

    def test_prints_json(self) -> None:
        code, out, err = self.project("census", "1", "--repo", REPO, "--json")

        self.assertEqual(0, code, err)
        result = json.loads(out)
        self.assertEqual((3, 1, 2), (result["total"], result["resolved"], result["open"]))
        self.assertEqual(["T1", "T2", "T4"], [t["id"] for t in result["threads"]])
        self.assertEqual(self.FINDING, result["threads"][0]["comments"][0]["body"],
                         "each thread carries its comments, so the review settles it from this list")

    def test_runs_outside_any_checkout_with_repo(self) -> None:
        elsewhere = Path(tempfile.mkdtemp())

        code, out, err = self.project("census", "1", "--repo", REPO, cwd=elsewhere)

        self.assertEqual(0, code, err)
        self.assertTrue(out.startswith("3 finding threads"))

    def test_outside_a_checkout_without_repo_says_to_pass_it(self) -> None:
        elsewhere = Path(tempfile.mkdtemp())

        code, _, err = self.project("census", "1", cwd=elsewhere)

        self.assertEqual(1, code)
        self.assertIn("--repo", err)


class ArgumentTests(unittest.TestCase):
    def test_no_review_command_takes_a_sha(self) -> None:
        # Every SHA comes from GitHub or the state file; a typed one is how two
        # wrong 40-character SHAs reached a publish.
        for command in ("setup", "move", "census"):
            with self.subTest(command):
                help_text = io.StringIO()
                with redirect_stdout(help_text), self.assertRaises(SystemExit):
                    cli.main(["review", command, "--help"])
                self.assertNotIn("sha", help_text.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
