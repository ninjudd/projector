"""The `project review` commands, against a real Git remote and a fake GitHub.

Git runs for real: a bare origin holds `trunk`, the head branch `feature`, and
GitHub's `refs/pull/<n>/head`, and the checkout is a clone of it. `gh` is
replaced by `FakeGitHub`, which answers the calls the commands make and
records each one with the token it carried, so a test can see who posted what.
"""

from __future__ import annotations

import importlib.metadata as metadata
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from projector import cli, review
from projector.review import NotFound, ReviewError

REPO = "acme/app"


def review_page(review_id: int) -> str:
    return f"https://github.com/{REPO}/pull/1#pullrequestreview-{review_id}"


GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.com", "GIT_EDITOR": "true"}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, env=dict(os.environ, **GIT_ENV), check=True, capture_output=True,
                          text=True).stdout.strip()


def git_status(cwd: Path, *args: str) -> int:
    """Run git where a non-zero exit is expected, as a merge or rebase that stops on a conflict."""
    return subprocess.run(["git", *args], cwd=cwd, env=dict(os.environ, **GIT_ENV), capture_output=True).returncode


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
        self.pages: list[dict] = [{"data": {"repository": {"pullRequest": {"reviewThreads": {
            "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}}}}}]
        self.missing = False
        self.pr["draft"] = True
        # Every review on the pull request, as the REST reviews endpoint returns them.
        self.reviews: list[dict] = []
        self.files = [("src/x.py", "@@ -1,2 +1,6 @@\n a\n+b\n+c\n+d\n+e\n f"),
                      ("src/z.py", "@@ -10,0 +11,2 @@\n+x\n+y")]
        self.fail: str | None = None

    def __call__(self, args: list[str], token: str | None = None) -> str:
        self.calls.append((list(args), token))
        if self.fail and self.fail in " ".join(args):
            raise ReviewError(f"gh {' '.join(args)} failed: HTTP 502")
        if args[:3] == ["api", "--paginate", f"repos/{REPO}/pulls/1/reviews"]:
            login = re.search(r'\.user\.login == "([^"]+)"', args[4]).group(1)
            return "".join(json.dumps({"id": r["id"], "url": review_page(r["id"]), "at": r.get("at"),
                                       "body": r["body"]}) + "\n"
                           for r in self.reviews if r["user"]["login"] == login and "projector-review" in r["body"])
        if args[:3] == ["api", "--paginate", f"repos/{REPO}/pulls/1/files"]:
            return "".join(json.dumps([name, patch]) + "\n" for name, patch in self.files)
        if args[:4] == ["api", "-X", "POST", f"repos/{REPO}/pulls/1/reviews"]:
            payload = json.loads(Path(args[5]).read_text())
            review_id = 900 + len(self.reviews) + 1
            self.reviews.append({"id": review_id, "user": {"login": token.removeprefix("token-")},
                                 "body": payload["body"], "event": payload["event"],
                                 "commit_id": payload["commit_id"], "comments": payload["comments"]})
            return json.dumps({"id": review_id})
        if len(args) == 2 and args[1].startswith(f"repos/{REPO}/pulls/1/reviews/"):
            review_id = int(args[1].rsplit("/", 1)[1])
            return json.dumps(next(r for r in self.reviews if r["id"] == review_id))
        if args[:2] == ["pr", "ready"]:
            self.pr["draft"] = "--undo" in args
            return ""
        if args[:3] == ["api", "-X", "DELETE"]:
            del self.comments[int(args[3].rsplit("/", 1)[1])]
            return ""
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
        # Reviews name the installed Projector, so a test sees one version whatever this machine has.
        installed = mock.patch.object(metadata, "version", return_value="1.2.3")
        installed.start()
        self.addCleanup(installed.stop)

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
                            *extra, cwd=cwd)

    def later(self, command: str, *extra: str, cwd: Path | None = None) -> tuple[int, str, str]:
        return self.project(command, "1", "--state-dir", str(self.state), *extra, cwd=cwd)

    def read_state(self) -> dict:
        return json.loads((self.state / "prs" / REPO / "1.json").read_text())

    def lock(self, sha: str) -> Path:
        return self.state / "prs" / REPO / f"1-{sha}.lock"


class SetupTests(ReviewCase):
    def test_sets_up_the_exact_head_and_posts_the_start_comment_as_the_reviewer(self) -> None:
        code, out, err = self.setup_review("--loop", "example-loop")

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
        self.assertEqual(("example-loop", False), (state["loop"], state["rereview"]))
        self.assertEqual([], json.loads((self.state / "loops" / "example-loop" / "published.json").read_text()))
        self.assertEqual(
            f"{review.MARK} **Projector review started** · `1.2.3` · model `claude-opus-5-5` · "
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


class GateTests(ReviewCase):
    GATE = ('printf "%s %s %s %s" "$1" "$2" "$PROJECTOR_WORKTREE" "$PROJECTOR_BASE" > "$1/gate.args"; '
            'pwd -P > "$1/gate.pwd"; exit 3')

    def configure(self, command: str) -> None:
        (self.checkout / ".projector.toml").write_text(f"[review]\ngate = {json.dumps(command)}\n")

    def test_runs_the_command_in_the_worktree_and_exits_with_its_status(self) -> None:
        self.assertEqual(0, self.setup_review()[0])
        self.configure(self.GATE)
        state = self.read_state()
        worktree = Path(state["worktree"])

        code, _, err = self.later("gate", cwd=Path(tempfile.mkdtemp()))

        self.assertEqual(3, code, err)
        self.assertEqual(f"{worktree} {state['base']} {worktree} {state['base']}",
                         (worktree / "gate.args").read_text())
        self.assertEqual(str(worktree.resolve()), (worktree / "gate.pwd").read_text().strip())
        record = self.read_state()["gate"]
        self.assertEqual((self.GATE, 3, state["sha"]), (record["command"], record["exit_code"], record["sha"]))
        self.assertIn("exited 3", err)

    def test_a_passing_gate_exits_zero_and_is_recorded(self) -> None:
        self.assertEqual(0, self.setup_review()[0])
        self.configure("true")

        code, out, err = self.later("gate", "--json")

        self.assertEqual(0, code, err)
        self.assertEqual(0, json.loads(out)["exit_code"])
        self.assertEqual(0, self.read_state()["gate"]["exit_code"])

    def test_refuses_when_review_gate_is_unset(self) -> None:
        self.assertEqual(0, self.setup_review()[0])

        code, _, err = self.later("gate")

        self.assertEqual(1, code)
        self.assertIn("nothing to run", err)
        self.assertNotIn("gate", self.read_state())

    def test_refuses_an_untrusted_head_without_running_anything(self) -> None:
        self.github.pr["head"]["repo"] = {"full_name": "someone/app"}
        self.github.pr["head"]["ref"] = "does-not-exist-here"
        self.assertEqual(0, self.setup_review()[0])
        self.configure(self.GATE)
        worktree = Path(self.read_state()["worktree"])

        code, _, err = self.later("gate")

        self.assertEqual(1, code)
        self.assertIn("untrusted", err)
        self.assertIn("review it by reading", err)
        self.assertFalse((worktree / "gate.args").exists())
        self.assertNotIn("gate", self.read_state())

    def test_an_untrusted_head_is_refused_as_untrusted_when_review_gate_is_unset(self) -> None:
        # A refusal that says to run the repository's checks would send the
        # review straight to the head's code.
        self.github.pr["head"]["repo"] = {"full_name": "someone/app"}
        self.github.pr["head"]["ref"] = "does-not-exist-here"
        self.assertEqual(0, self.setup_review()[0])

        code, _, err = self.later("gate")

        self.assertEqual(1, code)
        self.assertIn("review it by reading", err)
        self.assertNotIn("run the repository's checks", err)

    def test_json_output_stays_parseable_when_the_gate_prints(self) -> None:
        # The gate runs as a child process, so only a real process sees what it
        # writes to its own standard output.
        self.assertEqual(0, self.setup_review()[0])
        self.configure("echo gate says hello; echo and to stderr >&2")
        src = Path(review.__file__).resolve().parents[1]
        env = dict(os.environ, PYTHONPATH=str(src))

        plain = subprocess.run([sys.executable, "-m", "projector", "review", "gate", "1", "--repo", REPO,
                                "--state-dir", str(self.state)], env=env, capture_output=True, text=True)
        as_json = subprocess.run([sys.executable, "-m", "projector", "review", "gate", "1", "--repo", REPO,
                                  "--state-dir", str(self.state), "--json"], env=env, capture_output=True, text=True)

        self.assertEqual(0, plain.returncode, plain.stderr)
        self.assertIn("gate says hello", plain.stdout, "plain output streams the gate's stdout as it is")
        self.assertEqual(0, as_json.returncode, as_json.stderr)
        self.assertEqual(0, json.loads(as_json.stdout)["exit_code"])
        self.assertIn("gate says hello", as_json.stderr)
        self.assertIn("and to stderr", as_json.stderr)

    def test_refuses_without_a_review_set_up(self) -> None:
        self.configure("true")

        code, _, err = self.later("gate", "--repo", REPO)

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


FINDING = "<!-- projector-finding v=1 priority=P2 sha=%s --> **P2 · A nil deref**"


def finding(path: str = "src/x.py", line: int = 3, priority: str = "P2",
            body: str = "**P2 · The cache returns nil after a restart**\n\n`load` skips the warm-up.\n\n"
                        "**Fix:** warm the cache in `load`.") -> dict:
    return {"path": path, "line": line, "priority": priority, "body": body}


class PublishCase(ReviewCase):
    START = datetime(2026, 9, 27, 1, 2, 3, tzinfo=timezone.utc)

    def setUp(self) -> None:
        super().setUp()
        self.clock = self.START + timedelta(minutes=12, seconds=34)
        patcher = mock.patch.object(review, "now", lambda: self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.dir = Path(tempfile.mkdtemp())

    def body(self, text: str = "This change warms the cache.\n\n{census}\n\nCovered 3 of 3 changed files.\n") -> Path:
        path = self.dir / "body.md"
        path.write_text(text)
        return path

    def threads(self, *items: dict) -> Path:
        path = self.dir / "threads.json"
        path.write_text(json.dumps(list(items)))
        return path

    def publish(self, verdict: str, *extra: str, body: Path | None = None, loop: str | None = "l1",
                cwd: Path | None = None) -> tuple[int, str, str]:
        args = ["--verdict", verdict, "--body", str(body or self.body()), "--covered", "3/3"]
        if loop:
            args += ["--loop", loop]
        return self.later("publish", *args, *extra, cwd=cwd)

    def open_finding(self) -> None:
        self.github.pages = [{"data": {"repository": {"pullRequest": {"reviewThreads": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [thread("T1", FINDING % self.head)]}}}}}]

    def posted(self) -> dict:
        return self.github.reviews[-1]


class PublishTests(PublishCase):
    def test_publishes_a_clean_self_review_marks_it_ready_and_cleans_up_in_order(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        code, out, err = self.publish("clean", cwd=Path(tempfile.mkdtemp()))

        self.assertEqual(0, code, err)
        review_body = self.posted()["body"]
        self.assertEqual(
            f"{review.MARK} **Projector review** · `1.2.3` · model `claude-opus-5-5` · **CLEAN** · "
            "took 12m 34s",
            review_body.splitlines()[0])
        self.assertIn(f"<!-- projector-review v=1 verdict=clean projector=1.2.3 model=claude-opus-5-5 "
                      f"sha={self.head} findings=0 seconds=754 covered=3/3 -->", review_body)
        self.assertIn("0 finding threads: 0 resolved, 0 open", review_body)
        self.assertEqual(("COMMENT", self.head, "token-operator"),
                         (self.posted()["event"], self.posted()["commit_id"], "token-" + self.posted()["user"]["login"]))
        self.assertFalse(self.github.pr["draft"], "a clean self-review marks the pull request ready")
        self.assertEqual({}, self.github.comments, "the start comment is gone")
        self.assertFalse(self.lock(self.head).exists(), "the lock is released")
        record = json.loads((self.state / "loops" / "l1" / "published.json").read_text())
        self.assertEqual([(REPO, 1, self.head, 901, "clean")],
                         [(r["repo"], r["number"], r["sha"], r["review_id"], r["verdict"]) for r in record])
        steps = [" ".join(args) for args, _ in self.github.calls]
        ready = next(i for i, c in enumerate(steps) if c.startswith("pr ready"))
        reread = next(i for i, c in enumerate(steps) if c == f"api repos/{REPO}/pulls/1/reviews/901")
        delete = next(i for i, c in enumerate(steps) if c.startswith("api -X DELETE"))
        self.assertLess(ready, reread)
        self.assertLess(reread, delete, "the start comment goes only after draft state and the re-read")
        self.assertIn("published review 901", out)

    def test_changes_requested_posts_findings_with_the_marker_and_returns_the_pull_request_to_draft(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.github.pr["draft"] = False

        code, _, err = self.publish("changes-requested", "--threads", str(self.threads(finding())))

        self.assertEqual(0, code, err)
        comment = self.posted()["comments"][0]
        self.assertEqual(("src/x.py", 3, "RIGHT"), (comment["path"], comment["line"], comment["side"]))
        self.assertTrue(comment["body"].startswith(f"<!-- projector-finding v=1 priority=P2 sha={self.head} -->\n"
                                                   "**P2 · The cache returns nil"))
        self.assertIn("findings=1 ", self.posted()["body"])
        self.assertIn("1 finding thread: 0 resolved, 1 open", self.posted()["body"])
        self.assertTrue(self.github.pr["draft"])

    def test_a_cross_author_review_requests_changes_and_leaves_draft_state_alone(self) -> None:
        self.github.pr["user"] = {"login": "teammate"}
        self.github.permissions = {"teammate": "write"}
        self.github.commit_authors = ["teammate"]
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        code, _, err = self.publish("changes-requested", "--threads", str(self.threads(finding())))

        self.assertEqual(0, code, err)
        self.assertEqual("REQUEST_CHANGES", self.posted()["event"])
        self.assertTrue(self.github.pr["draft"], "untouched")
        self.assertFalse(any(args[:2] == ["pr", "ready"] for args, _ in self.github.calls))

    def test_a_clean_cross_author_review_is_a_comment_unless_approval_is_allowed(self) -> None:
        self.github.pr["user"] = {"login": "teammate"}
        self.github.permissions = {"teammate": "write"}
        self.github.commit_authors = ["teammate"]
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.assertEqual(0, self.publish("clean")[0])
        self.assertEqual("COMMENT", self.posted()["event"])

        (self.checkout / ".projector.toml").write_text("[review]\nallow_approve = true\n")
        self.assertEqual(0, self.setup_review("--loop", "l1", "--rereview")[0])
        code, _, err = self.publish("clean")

        self.assertEqual(0, code, err)
        self.assertEqual("APPROVE", self.posted()["event"])


class PublishRefusalTests(PublishCase):

    def refused(self, result: tuple[int, str, str], words: str) -> None:
        code, _, err = result
        self.assertEqual(1, code, "a refusal exits non-zero")
        self.assertIn(words, err)
        self.assertEqual([], self.github.reviews, "nothing was posted")
        self.assertTrue(self.lock(self.head).exists(), "a refused publish keeps the lock")

    def test_approved_is_not_a_verdict(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        with self.assertRaises(SystemExit) as refused, redirect_stderr(io.StringIO()):
            self.publish("approved")

        self.assertEqual(2, refused.exception.code)
        self.assertEqual([], self.github.reviews, "nothing was posted")

    def test_the_duration_and_seconds_are_always_present_and_agree(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.clock = self.START + timedelta(hours=1, minutes=4, seconds=9)

        self.assertEqual(0, self.publish("clean")[0])

        first = self.posted()["body"].splitlines()[0]
        self.assertTrue(first.endswith("· took 1h 04m"), first)
        self.assertIn("seconds=3849 ", self.posted()["body"])

    def test_no_sha_is_ever_typed_and_a_moved_head_is_refused(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.github.pr["head"]["sha"] = self.push_head("second head")

        self.refused(self.publish("clean"), "project review move 1")

    def test_a_missing_or_empty_body_is_refused(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        self.refused(self.publish("clean", body=self.dir / "nowhere.md"), "does not exist")
        self.refused(self.publish("clean", body=self.body("   \n")), "empty")

    def test_a_body_cannot_carry_its_own_signature_so_took_is_never_stale(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        stale = (f"{review.MARK} **Projector review** · model `m` · **CLEAN** · took 3m 00s\n\n"
                 "{census}\n")

        self.refused(self.publish("clean", body=self.body(stale)), "signature line")

    def test_an_anchor_outside_the_diff_or_without_a_line_is_refused(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        self.refused(self.publish("changes-requested", "--threads", str(self.threads(finding(line=40)))),
                     "outside the pull request's diff hunks")
        no_line = finding()
        no_line["line"] = None
        self.refused(self.publish("changes-requested", "--threads", str(self.threads(no_line))), "line number")
        self.refused(self.publish("changes-requested", "--threads", str(self.threads(finding(path="docs/a.md")))),
                     "not a file this pull request changes")

    def test_a_finding_needs_its_header_and_fix_and_leaves_the_marker_to_publish(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        self.refused(self.publish("changes-requested", "--threads",
                                  str(self.threads(finding(body="A nil deref.\n\n**Fix:** check it.")))), "must open with")
        self.refused(self.publish("changes-requested", "--threads",
                                  str(self.threads(finding(body="**P2 · A nil deref**\n\nNo fix.")))), "**Fix:**")
        self.refused(self.publish("changes-requested", "--threads",
                                  str(self.threads(finding(body=FINDING % self.head + "\n\n**Fix:** x")))), "marker")

    def test_a_second_publish_on_the_same_head_is_refused_without_rereview(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.assertEqual(0, self.publish("clean")[0])
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        code, _, err = self.publish("clean")

        self.assertEqual(1, code)
        self.assertIn("--rereview", err)
        self.assertEqual(1, len(self.github.reviews))

    def test_a_rereview_publishes_a_second_verdict_on_the_same_head(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.assertEqual(0, self.publish("clean")[0])
        self.assertEqual(0, self.setup_review("--loop", "l1", "--rereview")[0])

        code, _, err = self.publish("clean")

        self.assertEqual(0, code, err)
        self.assertEqual(2, len(self.github.reviews))

    def test_another_loops_verdict_on_the_head_is_a_collision(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "a")[0])
        self.assertEqual(0, self.publish("clean", loop="a")[0])
        self.assertEqual(0, self.setup_review("--loop", "b", "--rereview")[0])

        code, _, err = self.publish("clean", loop="b")

        self.assertEqual(1, code)
        self.assertIn("another loop", err)

    def written(self, effort: str | None) -> tuple[str, str, str]:
        """The start comment, signature line, and marker a review writes with CLAUDE_EFFORT as given."""
        environment = {} if effort is None else {"CLAUDE_EFFORT": effort}
        with mock.patch.dict(os.environ, environment):
            self.assertEqual(0, self.setup_review("--loop", "l1")[0])
            start = self.github.comments[101]
            self.assertEqual(0, self.publish("clean", loop="l1")[0])
        signature, marker = self.posted()["body"].split("\n\n", 2)[:2]
        return start, signature, marker

    def test_the_host_effort_names_the_review_when_it_is_set(self) -> None:
        start, signature, marker = self.written("xhigh")

        self.assertIn("· `1.2.3` · model `claude-opus-5-5` · effort `xhigh` · reviewing ", start)
        self.assertIn("· `1.2.3` · model `claude-opus-5-5` · effort `xhigh` · **CLEAN** ·", signature)
        self.assertIn("projector=1.2.3 model=claude-opus-5-5 effort=xhigh sha=", marker)
        self.assertNotIn("effort", json.dumps(self.read_state()), "read live, never stored")

    def test_a_checkout_that_was_never_installed_names_its_version_unknown(self) -> None:
        with mock.patch.object(metadata, "version", side_effect=metadata.PackageNotFoundError):
            start, signature, marker = self.written(None)

        self.assertIn("· `unknown` · model `claude-opus-5-5` · reviewing ", start)
        self.assertIn("· `unknown` · model `claude-opus-5-5` · **CLEAN** ·", signature)
        self.assertIn("verdict=clean projector=unknown model=claude-opus-5-5 sha=", marker)

    def test_the_review_names_no_effort_when_the_host_reports_none(self) -> None:
        for effort in (None, "", "  "):
            with self.subTest(effort=effort):
                self.setUp()
                for text in self.written(effort):
                    self.assertNotIn("effort", text)

    def test_effort_is_never_an_argument(self) -> None:
        with self.assertRaises(SystemExit) as unknown, redirect_stderr(io.StringIO()):
            self.project("setup", "1", "--state-dir", str(self.state), "--model", "m", "--effort", "low")
        self.assertEqual(2, unknown.exception.code)

    def test_an_older_verdict_whose_marker_carries_effort_still_collides(self) -> None:
        self.github.reviews.append({
            "id": 850, "user": {"login": "operator"}, "event": "COMMENT", "commit_id": self.head, "comments": [],
            "body": (f"{review.MARK} **Projector review** · model `m` · effort `low` · **APPROVED** · took 1m 00s\n\n"
                     f"<!-- projector-review v=1 verdict=approved model=m effort=low sha={self.head} "
                     "findings=0 seconds=60 covered=1/1 -->\n"),
        })
        self.assertEqual(0, self.setup_review("--loop", "b")[0])

        code, _, err = self.publish("clean", loop="b")

        self.assertEqual(1, code)
        self.assertIn("another loop", err)

    def test_outside_a_loop_a_second_verdict_needs_its_id_named_in_the_body(self) -> None:
        self.assertEqual(0, self.setup_review()[0])
        self.assertEqual(0, self.publish("clean", loop=None)[0])
        self.assertEqual(0, self.setup_review()[0])

        refused = self.publish("clean", loop=None)
        uncited = self.publish("clean", "--second-verdict", "901", loop=None)
        cited = self.publish("clean", "--second-verdict", "901", loop=None,
                             body=self.body("A second verdict beside review 901.\n\n{census}\n"))

        self.assertEqual((1, 1, 0), (refused[0], uncited[0], cited[0]), cited[2])
        self.assertIn("--second-verdict", refused[2])
        self.assertIn("name the earlier review 901", uncited[2])

    def test_a_gh_failure_mid_publish_exits_non_zero_keeps_the_lock_and_a_retry_finishes(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.github.fail = "pr ready"

        failed = self.publish("clean")
        self.github.fail = None
        retried = self.publish("clean")

        self.assertEqual(1, failed[0])
        self.assertIn("HTTP 502", failed[2])
        self.assertEqual(0, retried[0], retried[2])
        self.assertEqual(1, len(self.github.reviews), "the retry did not post a second review")
        self.assertFalse(self.github.pr["draft"])
        self.assertFalse(self.lock(self.head).exists())

    def test_clean_is_refused_while_a_finding_is_open(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.open_finding()

        self.refused(self.publish("clean"), "needs no open finding")

    def test_changes_requested_is_refused_on_a_clean_head(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        self.refused(self.publish("changes-requested"), "the head is clean")

    def test_a_body_without_the_census_in_prose_is_refused(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        self.refused(self.publish("clean", body=self.body("No census here.\n")), "{census}")
        self.refused(self.publish("clean", body=self.body("Only quoted: `print({census})`.\n")), "{census}")

    def test_braces_that_are_not_placeholders_publish_as_written(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        self.assertEqual(0, self.publish("clean", body=self.body("{census}\n\nChecked {checks}.\n"))[0])

        self.assertIn("Checked {checks}.", self.posted()["body"])

    def test_placeholders_fill_from_state_and_the_same_clock(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        body = self.body("{census}\n\nReviewed {short_sha} ({sha}) in {took}, {seconds} seconds.\n")
        self.assertEqual(0, self.publish("clean", body=body)[0])

        self.assertIn(f"Reviewed {self.head[:7]} ({self.head}) in 12m 34s, 754 seconds.", self.posted()["body"])

    def test_a_span_holding_only_a_placeholder_is_filled_in_code_font(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        body = self.body("Reviewed `{short_sha}` (`{sha}`) in `{took}`.\n\n`{census}`\n\n```\n{short_sha}\n```\n")
        code, _, err = self.publish("clean", body=body)

        self.assertEqual(0, code, err)
        posted = self.posted()["body"]
        self.assertIn(f"Reviewed `{self.head[:7]}` (`{self.head}`) in `12m 34s`.", posted)
        self.assertIn("`0 finding threads: 0 resolved, 0 open`", posted)
        self.assertIn("```\n{short_sha}\n```", posted, "a fenced block stays quoted code")

    def test_prose_and_code_that_mention_markers_or_braces_publish_as_written(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        body = self.body(
            "The loop id `example-projector-review-loop` and the `projector-finding` marker both appear here.\n\n"
            "{census}\n\nSuggestions\n- `f\"sha={sha}\"` should read `state[\"{short_sha}\"]`.\n"
            "- Findings are `{path, line}` objects.\n\n```\nprint(f\"{sha} {took}\")\n```\n")
        item = finding(body="**P2 · The `projector-finding` marker is parsed by substring**\n\n"
                            "`census` matches `{name}` too loosely.\n\n**Fix:** match the comment.")

        code, _, err = self.publish("changes-requested", "--threads", str(self.threads(item)), body=body)

        self.assertEqual(0, code, err)
        posted = self.posted()["body"]
        self.assertIn('`f"sha={sha}"` should read `state["{short_sha}"]`.', posted)
        self.assertIn("`{path, line}` objects", posted)
        self.assertIn('print(f"{sha} {took}")', posted)
        self.assertIn("`example-projector-review-loop`", posted)
        self.assertIn("matches `{name}` too loosely", self.posted()["comments"][0]["body"])

    def test_a_body_that_opens_its_own_marker_comment_is_refused(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        self.refused(self.publish("clean", body=self.body(
            "{census}\n\n<!-- projector-review v=1 verdict=clean -->\n")), "signature line")

    def test_a_signature_that_would_not_match_the_skill_is_refused(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        path = self.state / "prs" / REPO / "1.json"
        state = json.loads(path.read_text())
        state["model"] = "has space"
        path.write_text(json.dumps(state))

        self.refused(self.publish("clean"), "does not match review-pr/SKILL.md")

    def test_publish_without_a_review_holding_the_lock_is_refused(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.assertEqual(0, self.later("release", "--repo", REPO)[0])

        code, _, err = self.publish("clean")

        self.assertEqual(1, code)
        self.assertIn("project review setup 1", err)


def css(**padding: int) -> str:
    """A stylesheet of six rows, each 8px unless `padding` names it, as r1=9 does."""
    return "".join(f".r{i} {{ padding: {padding.get(f'r{i}', 8)}px; }}\n" for i in range(6))


class IncrementalCase(PublishCase):
    """A pull request whose head `self.reviewed` has a clean full review that loop l1 published.

    The trunk's `self.fork` adds a.css, and the reviewed head changes its row r1's padding
    from 8px to 9px. Each test makes a new head from there and sets it up as loop l1.
    """

    BODY = ("The push widens row r2's padding in `a.css`. Only a `padding` value changes, so every selector "
            "matches what it did.")

    def setUp(self) -> None:
        super().setUp()
        self.trunk = self.base
        self.fork = self.move_trunk({"a.css": css()})
        self.reviewed = self.push_feature(self.build(self.fork, {"a.css": css(r1=9)}))
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.assertEqual(0, self.publish("clean")[0])

    def write(self, files: dict[str, str | None]) -> None:
        for name, content in files.items():
            path = self.work / name
            if content is None:
                path.unlink()
            else:
                path.write_text(content)
        git(self.work, "add", "-A")

    def build(self, start: str, *changes: dict[str, str | None], message: str = "change") -> str:
        """Commit each change in turn on top of `start`, writing a file or deleting it for None."""
        git(self.work, "checkout", "--quiet", "--detach", start)
        for files in changes:
            self.write(files)
            git(self.work, "commit", "--quiet", "--allow-empty", "-m", message)
        return git(self.work, "rev-parse", "HEAD")

    def push_feature(self, sha: str) -> str:
        git(self.work, "push", "--quiet", "--force", "origin", f"{sha}:refs/heads/feature", f"{sha}:refs/pull/1/head")
        self.github.pr["head"]["sha"] = sha
        return sha

    def move_trunk(self, *changes: dict[str, str | None]) -> str:
        self.trunk = self.build(self.trunk, *changes, message="trunk")
        git(self.work, "push", "--quiet", "--force", "origin", f"{self.trunk}:refs/heads/trunk")
        self.github.pr["base"]["sha"] = self.trunk
        return self.trunk

    def merge(self, head: str, other: str, resolved: dict[str, str | None] | None = None) -> str:
        git(self.work, "checkout", "--quiet", "--detach", head)
        if git_status(self.work, "merge", "--quiet", "--no-edit", other):
            self.write(resolved or {})
            git(self.work, "commit", "--quiet", "--no-edit")
        return git(self.work, "rev-parse", "HEAD")

    def rebase(self, head: str, onto: str, upstream: str | None = None,
               resolved: dict[str, str | None] | None = None) -> str:
        git(self.work, "checkout", "--quiet", "--detach", head)
        if git_status(self.work, "rebase", "--quiet", "--onto", onto, upstream or self.fork):
            self.write(resolved or {})
            git(self.work, "rebase", "--continue")
        return git(self.work, "rev-parse", "HEAD")

    def review_head(self, sha: str, *extra: str) -> str:
        self.push_feature(sha)
        code, _, err = self.setup_review("--loop", "l1", *extra)
        self.assertEqual(0, code, err)
        return sha

    def pushed(self, *extra: str) -> str:
        """A one-line push on the reviewed head, set up for review."""
        return self.review_head(self.build(self.reviewed, {"a.css": css(r1=9, r2=9)}), *extra)

    def interdiff(self) -> dict:
        code, out, err = self.later("interdiff", "--json")
        result = json.loads(out)
        self.assertEqual(0 if result["incremental"] else 1, code, err)
        return result

    def gate(self, command: str = "true") -> int:
        (self.checkout / ".projector.toml").write_text(f"[review]\ngate = {json.dumps(command)}\n")
        return self.later("gate")[0]

    def publish_incremental(self, *extra: str, text: str | None = None) -> tuple[int, str, str]:
        path = self.dir / "incremental.md"
        path.write_text(self.BODY if text is None else text)
        return self.later("publish", "--incremental", "--body", str(path), *extra)

    def edit_record(self, change) -> None:
        path = self.state / "loops" / "l1" / "published.json"
        record = json.loads(path.read_text())
        change(record)
        path.write_text(json.dumps(record))


class InterdiffMeasureTests(IncrementalCase):
    def test_a_pushed_commit_counts_only_its_own_lines(self) -> None:
        self.pushed()

        result = self.interdiff()

        self.assertTrue(result["incremental"], result["reason"])
        self.assertEqual(("push", None, [], 1, 1, ["a.css"]),
                         (result["kind"], result["unchanged_commits"], result["conflicts"], result["insertions"],
                          result["deletions"], result["files"]))
        self.assertIn("-.r2 { padding: 8px; }\n+.r2 { padding: 9px; }", result["patch"])
        self.assertEqual((self.reviewed, review_page(901), False), (result["from"], result["review"], result["needs_gate"]))

    def test_a_merge_of_the_trunk_counts_the_edit_alone(self) -> None:
        self.move_trunk({"trunk.txt": "".join(f"line {i}\n" for i in range(50))})
        self.review_head(self.build(self.merge(self.reviewed, self.trunk), {"a.css": css(r1=9, r2=9)}))
        self.assertEqual(0, self.gate())

        result = self.interdiff()

        self.assertTrue(result["incremental"], result["reason"])
        self.assertEqual(("merge", None, [], 1, 1, ["a.css"]),
                         (result["kind"], result["unchanged_commits"], result["conflicts"], result["insertions"],
                          result["deletions"], result["files"]))

    def test_a_clean_rebase_and_a_new_commit_give_that_commits_change(self) -> None:
        self.move_trunk({"trunk.txt": "moved\n"})
        self.review_head(self.build(self.rebase(self.reviewed, self.trunk), {"a.css": css(r1=9, r3=9)}))
        self.assertEqual(0, self.gate())

        result = self.interdiff()

        self.assertTrue(result["incremental"], result["reason"])
        self.assertEqual(("rebase", True, [], 1, 1, ["a.css"]),
                         (result["kind"], result["unchanged_commits"], result["conflicts"], result["insertions"],
                          result["deletions"], result["files"]))
        self.assertIn("+.r3 { padding: 9px; }", result["patch"])

    def assert_resolved(self, result: dict) -> None:
        """The conflict the reviewed head's 9px and the trunk's 12px made, resolved to 10px."""
        self.assertTrue(result["incremental"], result["reason"])
        self.assertEqual((["a.css"], 1, 7), (result["conflicts"], result["insertions"], result["deletions"]))
        self.assertIn(f"-<<<<<<< {self.trunk}\n-.r1 {{ padding: 12px; }}\n-||||||| {self.fork}\n"
                      f"-.r1 {{ padding: 8px; }}\n-=======\n-.r1 {{ padding: 9px; }}\n->>>>>>> {self.reviewed}\n"
                      "+.r1 { padding: 10px; }", result["patch"])

    def test_a_rebase_that_resolved_a_conflict_shows_the_resolution(self) -> None:
        self.move_trunk({"a.css": css(r1=12)})
        self.review_head(self.rebase(self.reviewed, self.trunk, resolved={"a.css": css(r1=10)}))
        self.assertEqual(0, self.gate())

        result = self.interdiff()

        self.assert_resolved(result)
        self.assertEqual(("rebase", False), (result["kind"], result["unchanged_commits"]))

    def test_a_merge_that_resolved_a_conflict_shows_the_resolution(self) -> None:
        self.move_trunk({"a.css": css(r1=12)})
        self.review_head(self.merge(self.reviewed, self.trunk, resolved={"a.css": css(r1=10)}))
        self.assertEqual(0, self.gate())

        result = self.interdiff()

        self.assert_resolved(result)
        self.assertEqual(("merge", None), (result["kind"], result["unchanged_commits"]))

    def test_an_amend_is_a_rewrite_and_a_reworded_message_changes_nothing(self) -> None:
        self.review_head(self.build(self.fork, {"a.css": css(r1=9, r4=9)}))
        amended = self.interdiff()
        self.review_head(self.build(self.fork, {"a.css": css(r1=9)}, message="reworded"))
        reworded = self.interdiff()

        self.assertTrue(amended["incremental"], amended["reason"])
        self.assertEqual(("rewrite", False, 1, 1), (amended["kind"], amended["unchanged_commits"],
                                                    amended["insertions"], amended["deletions"]))
        self.assertTrue(reworded["incremental"], reworded["reason"])
        self.assertEqual(("rewrite", True, 0, 0, [], ""),
                         (reworded["kind"], reworded["unchanged_commits"], reworded["insertions"],
                          reworded["deletions"], reworded["files"], reworded["patch"]))

    def test_a_stack_cascade_counts_the_childs_edit_alone(self) -> None:
        parent = self.build(self.fork, {"p.txt": "parent\n"})
        git(self.work, "push", "--quiet", "origin", f"{parent}:refs/heads/parent")
        self.github.pr["base"] = {"ref": "parent", "sha": parent, "repo": {"full_name": REPO}}
        child = self.review_head(self.build(parent, {"a.css": css(r1=9)}))
        self.assertEqual(0, self.publish("clean")[0])
        # A fix amends the parent's commit, and the child is rebased onto it with one more edit.
        amended = self.build(self.fork, {"p.txt": "parent, fixed\n"})
        git(self.work, "push", "--quiet", "--force", "origin", f"{amended}:refs/heads/parent")
        self.github.pr["base"]["sha"] = amended
        self.review_head(self.build(self.rebase(child, amended, upstream=parent), {"a.css": css(r1=9, r5=9)}))
        self.assertEqual(0, self.gate())

        result = self.interdiff()

        self.assertTrue(result["incremental"], result["reason"])
        self.assertEqual(("rebase", True, [], 1, 1, ["a.css"]),
                         (result["kind"], result["unchanged_commits"], result["conflicts"], result["insertions"],
                          result["deletions"], result["files"]))
        # The merge base Git computes for the child sits below the parent, so a replay
        # from it brings the parent's old commit along and conflicts with its amend.
        below = git(self.checkout, "merge-base", child, amended)
        self.assertEqual(["p.txt"], review.measure(self.checkout, child, below, self.github.pr["head"]["sha"],
                                                   amended).conflicts)

    def test_a_child_retargeted_after_its_parent_merges_is_a_retarget_not_a_merge(self) -> None:
        parent = self.build(self.fork, {"p.txt": "parent\n"})
        git(self.work, "push", "--quiet", "origin", f"{parent}:refs/heads/parent")
        self.github.pr["base"] = {"ref": "parent", "sha": parent, "repo": {"full_name": REPO}}
        child = self.review_head(self.build(parent, {"a.css": css(r1=9)}))
        self.assertEqual(0, self.publish("clean")[0])
        # The parent squash-merges, and GitHub retargets the child onto the trunk; the child
        # then pushes a commit without merging or rebasing.
        self.move_trunk({"p.txt": "parent\n"})
        self.github.pr["base"] = {"ref": "trunk", "sha": self.trunk, "repo": {"full_name": REPO}}
        self.review_head(self.build(child, {"a.css": css(r1=9, r2=9)}))
        self.assertEqual(0, self.gate())

        result = self.interdiff()
        _, out, _ = self.later("interdiff")

        self.assertTrue(result["incremental"], result["reason"])
        self.assertEqual(("retarget", None, []), (result["kind"], result["unchanged_commits"], result["conflicts"]))
        self.assertEqual(["a.css", "p.txt"], result["files"], "the parent's change is in the diff GitHub shows now")
        self.assertIn("kind: retarget onto trunk\n", out)

    def test_an_earlier_head_missing_from_the_checkout_cannot_be_measured(self) -> None:
        missing = "f" * 40
        self.edit_record(lambda record: record[-1].update(sha=missing))
        self.github.reviews[-1]["body"] = self.github.reviews[-1]["body"].replace(self.reviewed, missing)
        self.pushed()

        result = self.interdiff()

        self.assertIn("cannot be measured: the checkout lacks the earlier head fffffff", result["reason"])
        self.assertEqual((None, None, [], ""), (result["kind"], result["conflicts"], result["files"], result["patch"]))

    def test_a_record_without_the_full_reviews_merge_base_cannot_be_measured(self) -> None:
        # An earlier release recorded no merge base, and a checkout that was never
        # installed names every release `unknown`, so the version rule cannot catch it.
        self.edit_record(lambda record: record[-1].pop("base"))
        self.pushed()

        result = self.interdiff()

        self.assertIn("cannot be measured", result["reason"])
        self.assertIn("holds no merge base for the full review", result["reason"])
        self.assertEqual((self.reviewed, None, None), (result["from"], result["kind"], result["conflicts"]))

    def test_without_merge_tree_a_moved_base_cannot_be_measured_but_an_unmoved_one_can(self) -> None:
        real = subprocess.run

        def older_git(args, *rest, **options):
            if "merge-tree" in args:
                return subprocess.CompletedProcess(args, 129, b"", b"error: unknown option `merge-base=...'")
            return real(args, *rest, **options)

        with mock.patch.object(review.subprocess, "run", older_git):
            self.pushed()
            unmoved = self.interdiff()
            self.move_trunk({"trunk.txt": "moved\n"})
            self.review_head(self.merge(self.reviewed, self.trunk))
            moved = self.interdiff()

        self.assertTrue(unmoved["incremental"], unmoved["reason"])
        self.assertIn("cannot be measured: git -c merge.conflictStyle=diff3 merge-tree exited 129", moved["reason"])
        self.assertIsNone(moved["conflicts"])

    def test_prints_the_earlier_review_the_kind_the_counts_the_refusal_and_the_patch(self) -> None:
        self.move_trunk({"a.css": css(r1=12)})
        self.review_head(self.rebase(self.reviewed, self.trunk, resolved={"a.css": css(r1=10)}))

        code, out, err = self.later("interdiff")

        self.assertEqual(1, code, err)
        self.assertEqual([
            f"earlier full review: {self.reviewed[:7]}, {review_page(901)}",
            "kind: rebase onto trunk; a reviewed commit changed",
            "conflicts: a.css",
            "change: +1 −7 lines in 1 file",
            "refused: the merge base moved, and no `project review gate` run on this head has exited 0 (needs_gate)",
            "",
            "diff --git a/a.css b/a.css",
        ], out.splitlines()[:7])


class InterdiffRuleTests(IncrementalCase):
    def refused(self, words: str) -> dict:
        result = self.interdiff()
        self.assertFalse(result["incremental"])
        self.assertIn(words, result["reason"])
        self.assertFalse(result["needs_gate"])
        return result

    def test_review_incremental_false_gives_a_full_review(self) -> None:
        self.pushed()
        (self.checkout / ".projector.toml").write_text("[review]\nincremental = false\n")

        self.refused("review.incremental is false")

    def test_review_incremental_that_is_not_a_boolean_refuses_and_names_the_key(self) -> None:
        self.pushed()
        for value in ('"yes"', "1"):
            with self.subTest(value=value):
                (self.checkout / ".projector.toml").write_text(f"[review]\nincremental = {value}\n")

                code, _, err = self.later("interdiff", "--json")

                self.assertNotEqual(0, code)
                self.assertIn("review.incremental must be true or false", err)

    def test_a_review_no_loop_runs_is_full(self) -> None:
        self.push_feature(self.build(self.reviewed, {"a.css": css(r1=9, r2=9)}))
        self.assertEqual(0, self.setup_review()[0])

        self.refused("no review loop runs this review")

    def test_an_untrusted_head_is_full(self) -> None:
        self.github.commit_authors = ["operator", "stranger"]
        self.pushed()

        self.refused("is untrusted")

    def test_a_newest_verdict_that_requests_changes_is_followed_by_a_full_review(self) -> None:
        self.edit_record(lambda record: record[-1].update(verdict="changes-requested"))
        self.pushed()

        self.refused("the newest verdict in loop l1's record requests changes")

    def test_a_head_the_loop_already_judged_gets_a_full_rereview(self) -> None:
        self.review_head(self.reviewed, "--rereview")

        self.refused(f"loop l1 already published a verdict on {self.reviewed[:7]}")

    def test_a_record_with_no_full_review_is_full(self) -> None:
        self.edit_record(lambda record: record.clear())
        self.pushed()

        self.refused("loop l1's record holds no clean full review")

    def test_a_clean_full_review_from_another_loop_on_the_same_account_leads_to_a_full_review(self) -> None:
        self.push_feature(self.build(self.reviewed, {"a.css": css(r1=9, r2=9)}))
        self.assertEqual(0, self.setup_review("--loop", "l2")[0])

        self.refused("loop l2's record holds no clean full review")

    def test_a_full_review_by_another_version_model_or_effort_is_not_built_on(self) -> None:
        earlier = "was made by Projector 1.2.3, model claude-opus-5-5, no effort, and this run is "
        head = self.build(self.reviewed, {"a.css": css(r1=9, r2=9)})
        with mock.patch.object(metadata, "version", return_value="1.2.4"):
            self.review_head(head)
            self.refused(earlier + "Projector 1.2.4, model claude-opus-5-5, no effort")
        self.assertEqual(0, self.later("release")[0])
        self.review_head(head, "--model", "claude-sonnet-5")
        self.refused(earlier + "Projector 1.2.3, model claude-sonnet-5, no effort")
        self.assertEqual(0, self.later("release")[0])
        self.review_head(head)
        with mock.patch.dict(os.environ, {"CLAUDE_EFFORT": "high"}):
            self.refused(earlier + "Projector 1.2.3, model claude-opus-5-5, effort high")

    def test_a_full_review_missing_from_github_is_not_built_on(self) -> None:
        self.github.reviews.clear()
        self.pushed()

        self.refused("the full review 901 of")

    def test_an_open_finding_gives_a_full_review(self) -> None:
        self.pushed()
        self.open_finding()

        self.refused("1 finding thread is open")

    def test_a_binary_file_a_symlink_or_a_submodule_cannot_be_read(self) -> None:
        def binary() -> None:
            (self.work / "blob.bin").write_bytes(b"\x00\x01\x02")
            git(self.work, "add", "blob.bin")

        def symlink() -> None:
            os.symlink("a.css", self.work / "link")
            git(self.work, "add", "link")

        def submodule() -> None:
            git(self.work, "update-index", "--add", "--cacheinfo", f"160000,{self.base},sub")

        for name, make, path in (("binary", binary, "blob.bin"), ("symlink", symlink, "link"),
                                 ("submodule", submodule, "sub")):
            with self.subTest(name):
                git(self.work, "checkout", "--quiet", "--detach", self.reviewed)
                make()
                git(self.work, "commit", "--quiet", "-m", name)
                self.review_head(git(self.work, "rev-parse", "HEAD"))

                result = self.refused(f"a binary file, a symlink, or a submodule: {path}")

                self.assertEqual("push", result["kind"], "the change was measured")
                self.assertIn(path, result["files"])

    def test_an_added_deleted_renamed_or_mode_changed_file_is_read_like_any_other(self) -> None:
        git(self.work, "checkout", "--quiet", "--detach", self.reviewed)
        self.write({"new.txt": "new\n", "README.md": None, "a.css": None, "b.css": css(r1=9)})
        os.chmod(self.work / "new.txt", 0o755)
        git(self.work, "add", "-A")
        git(self.work, "commit", "--quiet", "-m", "move things")
        self.review_head(git(self.work, "rev-parse", "HEAD"))

        result = self.interdiff()

        self.assertTrue(result["incremental"], result["reason"])
        self.assertEqual(["README.md", "a.css", "b.css", "new.txt"], result["files"])

    def test_after_an_incremental_review_the_change_is_measured_from_the_full_review(self) -> None:
        first = self.pushed()
        self.assertEqual(0, self.publish_incremental()[0])
        self.review_head(self.build(first, {"a.css": css(r1=9, r2=9, r3=9)}))

        result = self.interdiff()

        self.assertTrue(result["incremental"], result["reason"])
        self.assertEqual((self.reviewed, 2, 2), (result["from"], result["insertions"], result["deletions"]))

    def test_a_moved_base_needs_a_gate_that_passed_on_this_head(self) -> None:
        self.move_trunk({"trunk.txt": "moved\n"})
        self.review_head(self.build(self.merge(self.reviewed, self.trunk), {"a.css": css(r1=9, r2=9)}))

        before = self.interdiff()
        failed = self.gate("false")
        after_failure = self.interdiff()
        passed = self.gate("true")
        after_pass = self.interdiff()

        for result in (before, after_failure):
            self.assertFalse(result["incremental"])
            self.assertTrue(result["needs_gate"])
            self.assertIn("the merge base moved", result["reason"])
            self.assertEqual("merge", result["kind"], "every other rule passed, so the change was measured")
        self.assertEqual((1, 0), (failed, passed))
        self.assertTrue(after_pass["incremental"], after_pass["reason"])

    def test_a_gate_that_passed_on_an_earlier_head_does_not_count(self) -> None:
        self.move_trunk({"trunk.txt": "moved\n"})
        merged = self.review_head(self.merge(self.reviewed, self.trunk))
        self.assertEqual(0, self.gate())
        self.push_feature(self.build(merged, {"a.css": css(r1=9, r2=9)}))
        self.assertEqual(0, self.later("move")[0])

        result = self.interdiff()

        self.assertTrue(result["needs_gate"])

    def test_an_unmoved_base_needs_no_gate(self) -> None:
        self.pushed()

        self.assertTrue(self.interdiff()["incremental"])
        self.assertNotIn("gate", self.read_state())

    def test_an_empty_change_across_a_moved_base_still_needs_the_gate(self) -> None:
        self.move_trunk({"trunk.txt": "moved\n"})
        self.review_head(self.rebase(self.reviewed, self.trunk))

        result = self.interdiff()

        self.assertEqual(([], 0, 0, True), (result["files"], result["insertions"], result["deletions"],
                                            result["needs_gate"]))


class IncrementalPublishTests(IncrementalCase):
    def test_publishes_a_clean_incremental_self_review_with_exactly_its_parts(self) -> None:
        head = self.pushed()

        code, out, err = self.publish_incremental()

        self.assertEqual(0, code, err)
        posted = self.posted()
        self.assertEqual(
            f"{review.MARK} **Projector review** · `1.2.3` · model `claude-opus-5-5` · **CLEAN** · "
            f"incremental from `{self.reviewed[:7]}` · took 12m 34s\n\n"
            f"<!-- projector-review v=1 verdict=clean projector=1.2.3 model=claude-opus-5-5 sha={head} findings=0 "
            "seconds=754 covered=1/1 -->\n"
            f"<!-- projector-incremental v=1 from={self.reviewed} kind=push insertions=1 deletions=1 files=1 "
            "conflicts=0 -->\n\n"
            f"Incremental review of the change since the full review of [`{self.reviewed[:7]}`]({review_page(901)}): "
            "+1 −1 lines in 1 file.\n\n"
            f"{self.BODY}\n",
            posted["body"])
        lines = posted["body"].splitlines()
        self.assertIsNotNone(review.SIGNATURE.match(lines[0]))
        self.assertIsNotNone(review.MARKER.match(lines[2]))
        self.assertEqual(review.Verdict("clean", head, "1.2.3", "claude-opus-5-5", None, self.reviewed),
                         review.verdict_marker(posted["body"]))
        self.assertEqual(("COMMENT", head, []), (posted["event"], posted["commit_id"], posted["comments"]))
        self.assertFalse(self.github.pr["draft"], "a clean self-review marks the pull request ready")
        self.assertEqual({}, self.github.comments, "the start comment is gone")
        self.assertFalse(self.lock(head).exists(), "the lock is released")
        record = json.loads((self.state / "loops" / "l1" / "published.json").read_text())
        self.assertEqual([(self.reviewed, "clean", self.fork, None), (head, "clean", self.fork, self.reviewed)],
                         [(r["sha"], r["verdict"], r["base"], r["from"]) for r in record])
        self.assertIn(f"at {head}, incremental from {self.reviewed[:7]}", out)

    def test_after_a_rebase_with_a_conflict_the_lead_names_the_rebase_and_the_file(self) -> None:
        self.move_trunk({"a.css": css(r1=12)})
        self.review_head(self.rebase(self.reviewed, self.trunk, resolved={"a.css": css(r1=10)}))
        self.assertEqual(0, self.gate())

        code, _, err = self.publish_incremental()

        self.assertEqual(0, code, err)
        body = self.posted()["body"]
        self.assertIn(f"Incremental review of the change since the full review of [`{self.reviewed[:7]}`]"
                      f"({review_page(901)}) across a rebase onto `trunk`: +1 −7 lines in 1 file, with a resolved "
                      "conflict in `a.css`.", body)
        self.assertIn("kind=rebase insertions=1 deletions=7 files=1 conflicts=1 -->", body)

    def test_on_another_authors_pull_request_it_comments_and_leaves_approval_to_a_person(self) -> None:
        self.github.pr["user"] = {"login": "teammate"}
        self.github.permissions = {"teammate": "write"}
        self.github.commit_authors = ["teammate"]
        (self.checkout / ".projector.toml").write_text("[review]\nallow_approve = true\n")
        self.pushed()
        before = len(self.github.calls)

        code, _, err = self.publish_incremental()

        self.assertEqual(0, code, err)
        self.assertEqual("COMMENT", self.posted()["event"], "never an approval, even where approval is allowed")
        self.assertTrue(self.posted()["body"].endswith(f"{self.BODY}\n\nA human approval is what remains.\n"))
        self.assertFalse(any(args[:2] == ["pr", "ready"] for args, _ in self.github.calls[before:]),
                         "draft state is the author's")

    def refused(self, result: tuple[int, str, str], words: str, head: str) -> None:
        code, _, err = result
        self.assertEqual(1, code, "a refusal exits non-zero")
        self.assertIn(words, err)
        self.assertEqual(1, len(self.github.reviews), "only the full review was posted")
        self.assertTrue(self.lock(head).exists(), "a refused publish keeps the lock for the full review")

    def test_refuses_when_a_finding_opened_since_interdiff_ran(self) -> None:
        head = self.pushed()
        self.assertTrue(self.interdiff()["incremental"])
        self.open_finding()

        self.refused(self.publish_incremental(), "an incremental review is refused: 1 finding thread is open", head)

    def test_refuses_a_moved_base_with_no_passing_gate_on_the_head(self) -> None:
        self.move_trunk({"trunk.txt": "moved\n"})
        head = self.review_head(self.merge(self.reviewed, self.trunk))

        self.refused(self.publish_incremental(), "the merge base moved", head)

    def test_refuses_a_head_that_moved(self) -> None:
        head = self.pushed()
        self.push_feature(self.build(head, {"a.css": css(r1=9, r2=9, r3=9)}))

        self.refused(self.publish_incremental(), "project review move 1", head)

    def test_refuses_what_an_incremental_review_does_not_take(self) -> None:
        head = self.pushed()
        for extra, words in ((["--threads", str(self.threads(finding()))], "--threads"),
                             (["--covered", "1/1"], "--covered"),
                             (["--second-verdict", "901"], "--second-verdict"),
                             (["--verdict", "changes-requested"], "always clean")):
            with self.subTest(words):
                self.refused(self.publish_incremental(*extra), words, head)

    def test_refuses_a_body_that_is_empty_too_long_or_carries_what_publish_writes(self) -> None:
        head = self.pushed()
        signature = f"{review.MARK} **Projector review** · `1.2.3` · model `m` · **CLEAN** · took 1m 00s\n\nText.\n"
        for text, words in (("  \n", "empty"),
                            ("x" * 601, "601 characters once filled"),
                            ("x" * 570 + " {sha}", "611 characters once filled"),
                            (signature, "signature line"),
                            (f"Text.\n<!-- projector-incremental v=1 from={self.reviewed} -->\n", "markers"),
                            ("Text.\n<!-- projector-review v=1 verdict=clean -->\n", "markers"),
                            ("Text.\n\n{census}\n", "prints no census")):
            with self.subTest(words):
                self.refused(self.publish_incremental(text=text), words, head)
        self.assertEqual(0, self.publish_incremental(text="x" * 600)[0], "600 characters is the limit, not past it")

    def test_an_ordinary_review_body_with_an_incremental_line_is_refused(self) -> None:
        head = self.pushed()
        body = self.body(f"{{census}}\n\n<!-- projector-incremental v=1 from={self.reviewed} kind=push -->\n")

        self.refused(self.publish("clean", body=body), "markers", head)

    def test_another_loops_incremental_review_on_the_head_is_a_collision(self) -> None:
        head = self.pushed()
        self.assertEqual(0, self.publish_incremental()[0])
        self.assertEqual(0, self.setup_review("--loop", "l2", "--rereview")[0])

        code, _, err = self.publish("clean", loop="l2")

        self.assertEqual(1, code)
        self.assertIn("another loop", err)
        self.assertTrue(self.lock(head).exists())

    def test_a_publish_without_incremental_still_needs_a_verdict_and_coverage(self) -> None:
        self.pushed()
        body = str(self.body())

        no_verdict = self.later("publish", "--body", body, "--covered", "1/1")
        no_coverage = self.later("publish", "--body", body, "--verdict", "clean")

        self.assertEqual((1, 1), (no_verdict[0], no_coverage[0]))
        self.assertIn("--verdict must be clean or changes-requested", no_verdict[2])
        self.assertIn("--covered must be", no_coverage[2])


class VerdictMarkerTests(unittest.TestCase):
    SHA, FROM = "a" * 40, "b" * 40
    MARKER = (f"<!-- projector-review v=1 verdict=clean projector=0.7.2 model=claude-opus-5-5 effort=high sha={'a' * 40} "
              "findings=0 seconds=38 covered=1/1 -->")
    INCREMENTAL = (f"<!-- projector-incremental v=1 from={'b' * 40} kind=push insertions=2 deletions=2 files=1 "
                   "conflicts=0 -->")

    def test_reads_the_verdict_what_produced_it_and_the_incremental_line_after_it(self) -> None:
        self.assertEqual(review.Verdict("clean", self.SHA, "0.7.2", "claude-opus-5-5", "high", self.FROM),
                         review.verdict_marker(f"Signature\n\n{self.MARKER}\n{self.INCREMENTAL}\n\nLead.\n"))
        self.assertEqual(review.Verdict("clean", self.SHA, "0.7.2", "claude-opus-5-5", "high", None),
                         review.verdict_marker(f"Signature\n\n{self.MARKER}\n\nBody.\n"))

    def test_an_incremental_line_counts_only_directly_after_a_marker(self) -> None:
        self.assertIsNone(review.verdict_marker(f"Body.\n{self.INCREMENTAL}\n"))
        self.assertIsNone(review.verdict_marker(f"{self.MARKER}\n\n{self.INCREMENTAL}\n").incremental_from)


class ArgumentTests(unittest.TestCase):
    def test_no_review_command_takes_a_sha(self) -> None:
        # Every SHA comes from GitHub or the state file; a typed one is how two
        # wrong 40-character SHAs reached a publish.
        for command in ("setup", "move", "census", "release", "publish", "gate", "interdiff"):
            with self.subTest(command):
                help_text = io.StringIO()
                with redirect_stdout(help_text), self.assertRaises(SystemExit):
                    cli.main(["review", command, "--help"])
                self.assertIsNone(re.search(r"--\S*sha|metavar.*SHA|\bSHA\b", help_text.getvalue()))
                self.assertIn("--loop", help_text.getvalue())


if __name__ == "__main__":
    unittest.main()
