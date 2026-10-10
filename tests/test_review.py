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
            sha = re.search(r"sha=([0-9a-f]+)", args[4]).group(1)
            return "".join(f"{r['id']}\n" for r in self.reviews if r["user"]["login"] == login
                           and re.search(f"projector-review .* sha={sha}", r["body"]))
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


class ArgumentTests(unittest.TestCase):
    def test_no_review_command_takes_a_sha(self) -> None:
        # Every SHA comes from GitHub or the state file; a typed one is how two
        # wrong 40-character SHAs reached a publish.
        for command in ("setup", "move", "census", "release", "publish", "gate"):
            with self.subTest(command):
                help_text = io.StringIO()
                with redirect_stdout(help_text), self.assertRaises(SystemExit):
                    cli.main(["review", command, "--help"])
                self.assertIsNone(re.search(r"--\S*sha|metavar.*SHA|\bSHA\b", help_text.getvalue()))
                self.assertIn("--loop", help_text.getvalue())


if __name__ == "__main__":
    unittest.main()
