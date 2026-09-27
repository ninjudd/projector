"""The `project review` commands, against a real Git remote and a fake GitHub.

Git runs for real: a bare origin holds `trunk`, the head branch `feature`, and
GitHub's `refs/pull/<n>/head`, and the checkout is a clone of it. `gh` is
replaced by `FakeGitHub`, which answers the calls the commands make and
records each one with the token it carried, so a test can see who posted what.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from projector import cli, review
from projector.review import NotFound, ReviewError

REPO = "acme/app"


def git(cwd: Path, *args: str) -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


class FakeGitHub:
    """The slice of GitHub the review commands use, for one pull request."""

    def __init__(self, head: str, base_sha: str) -> None:
        self.pr = {"number": 1, "state": "open", "user": {"login": "operator"},
                   "head": {"sha": head, "ref": "feature", "repo": {"full_name": REPO}},
                   "base": {"ref": "trunk", "sha": base_sha, "repo": {"full_name": REPO}}}
        self.commit_authors = ["operator"]
        self.permissions: dict[str, str] = {}
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
            return json.dumps(self.pages[0 if after is None else int(after)])
        if args == ["api", f"repos/{REPO}/pulls/1"]:
            if self.missing:
                raise NotFound("gh: Not Found (HTTP 404)")
            return json.dumps(self.pr)
        if args[:3] == ["api", "--paginate", f"repos/{REPO}/pulls/1/commits"]:
            return "".join(f"{login}\n" for login in self.commit_authors)
        if len(args) >= 2 and args[1].startswith(f"repos/{REPO}/collaborators/"):
            login = args[1].split("/")[4]
            if login not in self.permissions:
                raise NotFound("gh: Not Found (HTTP 404)")
            return self.permissions[login] + "\n"
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
        return [(args, token) for args, token in self.calls if any(a.startswith("body=") for a in args)]


class ReviewCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.origin = tmp / "origin.git"
        git(tmp, "init", "--quiet", "--bare", "-b", "trunk", str(self.origin))
        work = tmp / "work"
        git(tmp, "init", "--quiet", "-b", "trunk", str(work))
        (work / "README.md").write_text("trunk\n")
        git(work, "add", ".")
        git(work, "commit", "--quiet", "-m", "trunk")
        self.base = git(work, "rev-parse", "HEAD")
        git(work, "remote", "add", "origin", str(self.origin))
        git(work, "push", "--quiet", "origin", "trunk")
        self.work = work
        self.head = self.push_head("first head")
        self.checkout = tmp / "checkout"
        git(tmp, "clone", "--quiet", str(self.origin), str(self.checkout))
        # The clone's origin is a local path; the commands read the repository from
        # origin's GitHub URL, so point the URL there and keep fetching locally.
        git(self.checkout, "remote", "set-url", "origin", f"https://github.com/{REPO}.git")
        git(self.checkout, "config", f"url.{self.origin}.insteadOf", f"https://github.com/{REPO}.git")
        self.state = tmp / "state"
        self.github = FakeGitHub(self.head, self.base)
        patcher = mock.patch.object(review, "run_gh", self.github)
        patcher.start()
        self.addCleanup(patcher.stop)

    def push_head(self, text: str) -> str:
        (self.work / "change.txt").write_text(text + "\n")
        git(self.work, "add", ".")
        git(self.work, "commit", "--quiet", "-m", text)
        git(self.work, "push", "--quiet", "--force", "origin", "HEAD:refs/heads/feature", "HEAD:refs/pull/1/head")
        return git(self.work, "rev-parse", "HEAD")

    def project(self, *args: str, cwd: Path | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli.Path, "cwd", return_value=cwd or self.checkout), \
             redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["review", *args])
        return code, out.getvalue(), err.getvalue()

    def setup_review(self, *extra: str, cwd: Path | None = None) -> tuple[int, str, str]:
        return self.project("setup", "1", "--state-dir", str(self.state), "--model", "claude-opus-5-5",
                            "--effort", "low", *extra, cwd=cwd)

    def later(self, command: str, *extra: str, cwd: Path | None = None) -> tuple[int, str, str]:
        return self.project(command, "1", "--state-dir", str(self.state), *extra, cwd=cwd)

    def read_state(self) -> dict:
        return json.loads((self.state / "prs" / REPO / "1.json").read_text())

    def lock(self, sha: str) -> Path:
        return self.state / "prs" / REPO / f"1-{sha}.lock"


class SetupTests(ReviewCase):
    def test_sets_up_the_exact_head_and_posts_the_start_comment_as_the_reviewer(self) -> None:
        code, out, err = self.setup_review("--loop", "starkey-loop")

        self.assertEqual(0, code, err)
        state = self.read_state()
        self.assertEqual((self.head, self.base, 1), (state["sha"], state["base"], state["heads"]))
        self.assertEqual(REPO, state["repo"])
        self.assertEqual(str(self.checkout.resolve()), state["checkout"])
        worktree = Path(state["worktree"])
        self.assertEqual(self.state / "worktrees" / REPO / "1" / self.head[:12], worktree)
        self.assertEqual(self.head, git(worktree, "rev-parse", "HEAD"))
        self.assertFalse(worktree.is_relative_to(self.checkout), "never inside the checkout")
        self.assertTrue(state["trusted"])
        self.assertEqual({"id": 101, "created_at": "2026-09-27T01:02:03Z"}, state["start_comment"])
        self.assertEqual(("starkey-loop", False), (state["loop"], state["rereview"]))
        self.assertEqual([], json.loads((self.state / "loops" / "starkey-loop" / "published.json").read_text()))
        self.assertEqual(
            f"{review.START_MARK} **Projector review started** · model `claude-opus-5-5` · effort `low` · "
            f"reviewing `{self.head[:7]}`\n\n<!-- projector-start v=1 sha={self.head} -->\n",
            self.github.comments[101],
        )
        self.assertIn(f"reviewing {REPO}#1 at {self.head}", out)

    def test_holds_the_lock_after_setup_recording_who_took_it(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        held = json.loads(self.lock(self.head).read_text())
        self.assertEqual(("l1", self.head), (held["loop"], held["sha"]))
        self.assertIn("host", held)
        self.assertIn("taken_at", held)

    def test_posts_with_a_token_for_that_call_and_never_switches_accounts(self) -> None:
        code, _, err = self.setup_review()

        self.assertEqual(0, code, err)
        posts = self.github.posts()
        self.assertEqual(1, len(posts))
        self.assertEqual("token-operator", posts[0][1])
        for args, token in self.github.calls:
            self.assertNotIn("switch", args)
            if not any(a.startswith("body=") for a in args):
                self.assertIsNone(token, f"only posting carries the reviewer's token: {args}")

    def test_the_reviewer_comes_from_review_username(self) -> None:
        (self.checkout / ".projector.toml").write_text('[review]\nusername = "review-bot"\n')

        code, _, err = self.setup_review()

        self.assertEqual(0, code, err)
        self.assertEqual("token-review-bot", self.github.posts()[0][1])
        self.assertEqual(("review-bot", "operator"), (self.read_state()["reviewer"], self.read_state()["operator"]))

    def test_sets_up_a_fork_head_from_its_pull_ref_and_records_it_untrusted(self) -> None:
        self.github.pr["head"]["repo"] = {"full_name": "someone/app"}
        self.github.pr["head"]["ref"] = "does-not-exist-here"

        code, out, err = self.setup_review()

        self.assertEqual(0, code, err)
        state = self.read_state()
        self.assertEqual(self.head, git(Path(state["worktree"]), "rev-parse", "HEAD"))
        self.assertTrue(state["cross_repository"])
        self.assertFalse(state["trusted"])
        self.assertIn("someone/app", state["untrusted_because"])
        self.assertIn("review it by reading", out)

    def test_an_author_without_write_access_or_a_foreign_commit_is_untrusted(self) -> None:
        cases = {
            "author": ({"user": {"login": "stranger"}}, ["operator"], {}),
            "commit": ({}, ["operator", "stranger"], {}),
            "anonymous commit": ({}, ["operator", ""], {}),
            "bot": ({"user": {"login": "renovate[bot]"}}, ["operator"], {"renovate[bot]": "write"}),
        }
        for name, (pr, authors, permissions) in cases.items():
            with self.subTest(name):
                self.github.pr.update(pr)
                self.github.commit_authors = authors
                self.github.permissions = permissions
                trusted, why = review.trusted_head(REPO, self.github.pr, "operator")
                self.assertFalse(trusted)
                self.assertTrue(why)
                self.github.pr["user"] = {"login": "operator"}

    def test_a_collaborator_with_write_access_is_trusted(self) -> None:
        self.github.pr["user"] = {"login": "teammate"}
        self.github.commit_authors = ["teammate", "operator"]
        self.github.permissions = {"teammate": "write"}

        self.assertEqual((True, ""), review.trusted_head(REPO, self.github.pr, "operator"))

    def test_refuses_a_closed_or_missing_pull_request_and_releases_the_lock(self) -> None:
        self.github.pr["state"] = "closed"
        closed = self.setup_review()
        self.github.pr["state"] = "open"
        self.github.missing = True
        missing = self.setup_review()

        self.assertEqual((1, 1), (closed[0], missing[0]))
        self.assertIn("is closed", closed[2])
        self.assertIn("does not exist", missing[2])
        self.assertEqual([], self.github.posts())
        self.assertEqual([], list((self.state / "prs" / REPO).glob("*.lock")) if (self.state / "prs").exists() else [])

    def test_a_refusal_after_the_lock_releases_it(self) -> None:
        # GitHub's answer and the fetched ref disagree when the head moves in
        # between; the SHA is never taken on trust from either alone.
        self.github.pr["head"]["sha"] = self.base

        code, _, err = self.setup_review()

        self.assertEqual(1, code)
        self.assertIn("moved", err)
        self.assertEqual([], self.github.posts())
        self.assertFalse(self.lock(self.base).exists())

    def test_a_second_instance_for_the_same_head_exits_without_posting(self) -> None:
        first = self.setup_review("--loop", "a")
        second = self.setup_review("--loop", "b")

        self.assertEqual(0, first[0], first[2])
        self.assertEqual(1, second[0])
        self.assertIn("loop a", second[2])
        self.assertIn("project review release 1", second[2])
        self.assertEqual(1, len(self.github.posts()))

    def test_a_lock_older_than_a_day_is_stale(self) -> None:
        self.assertEqual(0, self.setup_review()[0])
        held = json.loads(self.lock(self.head).read_text())
        held["taken_at"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        self.lock(self.head).write_text(json.dumps(held))

        code, _, err = self.setup_review("--rereview")

        self.assertEqual(0, code, err)
        self.assertTrue(self.read_state()["rereview"])

    def test_release_clears_the_lock_so_a_rereview_can_set_up(self) -> None:
        self.assertEqual(0, self.setup_review()[0])

        code, out, err = self.later("release", "--repo", REPO)
        again = self.setup_review("--rereview")

        self.assertEqual(0, code, err)
        self.assertIn("released 1 lock", out)
        self.assertEqual(0, again[0], again[2])
        self.assertTrue(self.read_state()["rereview"])

    def test_refuses_outside_a_checkout(self) -> None:
        code, _, err = self.setup_review(cwd=Path(tempfile.mkdtemp()))

        self.assertEqual(1, code)
        self.assertIn("--checkout", err)
        self.assertEqual([], self.github.posts())

    def test_repo_must_match_the_checkouts_origin(self) -> None:
        code, _, err = self.setup_review("--repo", "other/app")

        self.assertEqual(1, code)
        self.assertIn(REPO, err)
        self.assertEqual([], self.github.posts())

    def test_checkout_can_be_named_from_anywhere(self) -> None:
        code, _, err = self.setup_review("--checkout", str(self.checkout), cwd=Path(tempfile.mkdtemp()))

        self.assertEqual(0, code, err)

    def test_refuses_an_unusable_loop_id(self) -> None:
        code, _, err = self.setup_review("--loop", "../escape")

        self.assertEqual(1, code)
        self.assertIn("--loop", err)


class MoveTests(ReviewCase):
    def test_follows_a_moved_head_from_anywhere_and_edits_the_start_comment_in_place(self) -> None:
        self.assertEqual(0, self.setup_review()[0])
        old = self.head
        new = self.push_head("second head")
        self.github.pr["head"]["sha"] = new

        code, _, err = self.later("move", cwd=Path(tempfile.mkdtemp()))

        self.assertEqual(0, code, err)
        state = self.read_state()
        self.assertEqual((new, 2), (state["sha"], state["heads"]))
        self.assertEqual(new, git(Path(state["worktree"]), "rev-parse", "HEAD"))
        body = self.github.comments[101]
        self.assertEqual([101], list(self.github.comments), "edited in place, not a second comment")
        self.assertIn(f"reviewing `{new[:7]}`", body)
        self.assertIn(f"moved from `{old[:7]}`", body)
        self.assertIn(f"sha={new}", body)
        self.assertFalse(self.lock(old).exists())
        self.assertTrue(self.lock(new).exists())

    def test_rechecks_trust_on_the_new_head(self) -> None:
        self.assertEqual(0, self.setup_review()[0])
        self.github.pr["head"]["sha"] = self.push_head("second head")
        self.github.commit_authors = ["operator", "stranger"]

        self.assertEqual(0, self.later("move")[0])

        self.assertFalse(self.read_state()["trusted"])

    def test_refuses_a_head_that_did_not_move(self) -> None:
        self.assertEqual(0, self.setup_review()[0])

        code, _, err = self.later("move")

        self.assertEqual(1, code)
        self.assertIn("nothing to move", err)

    def test_refuses_without_a_review_set_up(self) -> None:
        code, _, err = self.later("move", "--repo", REPO)

        self.assertEqual(1, code)
        self.assertIn("project review setup 1", err)


def thread(thread_id: str, body: str, *, resolved: bool = False, outdated: bool = False,
           path: str = "src/x.py", line: int | None = 3, original: int | None = None) -> dict:
    return {"id": thread_id, "isResolved": resolved, "isOutdated": outdated, "path": path,
            "line": line, "originalLine": original, "comments": {"nodes": [{"databaseId": 9, "body": body}]}}


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
        code, out, err = self.later("census", "--repo", REPO)

        self.assertEqual(0, code, err)
        self.assertEqual(["3 finding threads: 1 resolved, 2 open", "src/x.py:3", "src/y.py:7 outdated"],
                         out.splitlines())
        graphql = [args for args, _ in self.github.calls if args[:2] == ["api", "graphql"]]
        self.assertEqual(2, len(graphql))
        self.assertIn("after=1", graphql[1])

    def test_prints_json_with_each_threads_comments(self) -> None:
        code, out, err = self.later("census", "--repo", REPO, "--json")

        self.assertEqual(0, code, err)
        result = json.loads(out)
        self.assertEqual((3, 1, 2), (result["total"], result["resolved"], result["open"]))
        self.assertEqual(["T1", "T2", "T4"], [t["id"] for t in result["threads"]])
        self.assertEqual(self.FINDING, result["threads"][0]["comments"][0]["body"])

    def test_finds_the_repository_from_the_review_set_up_for_the_pull_request(self) -> None:
        self.assertEqual(0, self.setup_review()[0])

        code, out, err = self.later("census", cwd=Path(tempfile.mkdtemp()))

        self.assertEqual(0, code, err)
        self.assertTrue(out.startswith("3 finding threads"))

    def test_outside_a_checkout_with_no_review_set_up_says_to_pass_repo(self) -> None:
        code, _, err = self.later("census", cwd=Path(tempfile.mkdtemp()))

        self.assertEqual(1, code)
        self.assertIn("--repo", err)


class ArgumentTests(unittest.TestCase):
    def test_no_review_command_takes_a_sha(self) -> None:
        # Every SHA comes from GitHub or the state file; a typed one is how two
        # wrong 40-character SHAs reached a publish.
        for command in ("setup", "move", "census", "release"):
            with self.subTest(command):
                help_text = io.StringIO()
                with redirect_stdout(help_text), self.assertRaises(SystemExit):
                    cli.main(["review", command, "--help"])
                self.assertNotIn("sha", help_text.getvalue().lower())
                self.assertIn("--loop", help_text.getvalue())


if __name__ == "__main__":
    unittest.main()
