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
        # Each issue comment's body by id, and who posted it and when.
        self.comments: dict[int, str] = {}
        self.comment_info: dict[int, dict] = {}
        self.next_comment = 101
        self.created_at = "2026-09-27T01:02:03Z"
        self.pages: list[dict] = [{"data": {"repository": {"pullRequest": {"reviewThreads": {
            "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}}}}}]
        self.missing = False
        self.pr["draft"] = True
        # Every review on the pull request, as the REST reviews endpoint returns them.
        self.reviews: list[dict] = []
        self.files = [("src/x.py", "@@ -1,2 +1,6 @@\n a\n+b\n+c\n+d\n+e\n f"),
                      ("src/z.py", "@@ -10,0 +11,2 @@\n+x\n+y")]
        self.fail: str | None = None
        # Runs once, right after the next review is submitted, as another session acting at that moment.
        self.after_post = None
        # Runs once, as the next review is submitted and before GitHub records it.
        self.before_post = None
        # Reviews the listing leaves out, as one GitHub has not indexed yet.
        self.unlisted: set[int] = set()
        # Answer a dismissal without dismissing anything.
        self.ignore_dismissals = False
        # The account gh is signed in as, which a call without a token acts as.
        self.login = "operator"

    def __call__(self, args: list[str], token: str | None = None) -> str:
        self.calls.append((list(args), token))
        if self.fail and self.fail in " ".join(args):
            raise ReviewError(f"gh {' '.join(args)} failed: HTTP 502")
        if args[:3] == ["api", "--paginate", f"repos/{REPO}/pulls/1/reviews"]:
            login = re.search(r'\.user\.login == "([^"]+)"', args[4])
            projector_only = "projector-review" in args[4]
            return "".join(json.dumps({"id": r["id"], "url": review_page(r["id"]), "at": r.get("at"),
                                       "login": r["user"]["login"], "association": r.get("association", "OWNER"),
                                       "state": self.state(r), "body": r["body"]}) + "\n"
                           for r in self.reviews if (login is None or r["user"]["login"] == login.group(1))
                           and (not projector_only or "projector-review" in r["body"]) and r["id"] not in self.unlisted)
        if args[:3] == ["api", "-X", "PUT"] and args[3].endswith("/dismissals"):
            found = next(r for r in self.reviews if r["id"] == int(args[3].split("/")[-2]))
            if not self.ignore_dismissals:
                found.update(state="DISMISSED", dismissal=args[5].removeprefix("message="))
            return json.dumps({"id": found["id"], "state": self.state(found)})
        if args[:3] == ["api", "--paginate", f"repos/{REPO}/pulls/1/files"]:
            return "".join(json.dumps([name, patch]) + "\n" for name, patch in self.files)
        if args[:4] == ["api", "-X", "POST", f"repos/{REPO}/pulls/1/reviews"]:
            payload = json.loads(Path(args[5]).read_text())
            if self.before_post:
                hook, self.before_post = self.before_post, None
                hook()
            review_id = 900 + len(self.reviews) + 1
            self.reviews.append({"id": review_id, "user": {"login": token.removeprefix("token-")},
                                 "body": payload["body"], "event": payload["event"],
                                 "commit_id": payload["commit_id"], "comments": payload["comments"],
                                 "at": review.now().strftime("%Y-%m-%dT%H:%M:%SZ")})
            if self.after_post:
                hook, self.after_post = self.after_post, None
                hook()
            return json.dumps({"id": review_id})
        if len(args) == 2 and args[1].startswith(f"repos/{REPO}/pulls/1/reviews/"):
            review_id = int(args[1].rsplit("/", 1)[1])
            found = next(r for r in self.reviews if r["id"] == review_id)
            return json.dumps(dict(found, html_url=review_page(review_id), submitted_at=found.get("at")))
        if args[:2] == ["pr", "ready"]:
            self.pr["draft"] = "--undo" in args
            return ""
        if args[:3] == ["api", "-X", "DELETE"]:
            del self.comments[int(args[3].rsplit("/", 1)[1])]
            return ""
        if args[:3] == ["api", "--paginate", f"repos/{REPO}/issues/1/comments"]:
            marker = re.search(r'contains\("([^"]+)"\)', args[4]).group(1)
            return "".join(json.dumps({"id": i, "url": self.comment_url(i), "body": body, **self.comment_info[i]})
                           + "\n" for i, body in self.comments.items() if marker in body)
        if args[:2] == ["auth", "token"]:
            return f"token-{args[-1]}\n"
        if args == ["api", "user", "--jq", ".login"]:
            return f"{self.login}\n"
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
            login = token.removeprefix("token-") if token else self.login
            comment_id = self.comment(args[3].removeprefix("body="), login)
            return json.dumps({"id": comment_id, "html_url": self.comment_url(comment_id),
                               "created_at": self.created_at})
        if args[:3] == ["api", "-X", "PATCH"]:
            comment_id = int(args[3].rsplit("/", 1)[1])
            self.comments[comment_id] = args[5].removeprefix("body=")
            return "{}"
        if len(args) == 2 and args[1].startswith(f"repos/{REPO}/issues/comments/"):
            comment_id = int(args[1].rsplit("/", 1)[1])
            if comment_id not in self.comments:
                raise NotFound("gh: Not Found (HTTP 404)")
            return json.dumps({"id": comment_id, "html_url": self.comment_url(comment_id),
                               "body": self.comments[comment_id]})
        raise AssertionError(f"unexpected gh call: {args}")

    def comment(self, body: str, login: str = "operator", created_at: str | None = None,
                updated_at: str | None = None) -> int:
        """Post an issue comment as `login`, created at `created_at` or the fake's time, and return its id."""
        comment_id, self.next_comment = self.next_comment, self.next_comment + 1
        self.comments[comment_id] = body
        created = created_at or self.created_at
        self.comment_info[comment_id] = {"login": login, "created_at": created, "updated_at": updated_at or created}
        return comment_id

    @staticmethod
    def comment_url(comment_id: int) -> str:
        return f"https://github.com/{REPO}/pull/1#issuecomment-{comment_id}"

    @staticmethod
    def state(review: dict) -> str:
        """The review's state as GitHub lists it: the event it was submitted with, until it is dismissed."""
        return review.get("state") or {"APPROVE": "APPROVED", "REQUEST_CHANGES": "CHANGES_REQUESTED",
                                       "COMMENT": "COMMENTED"}[review["event"]]

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
        self.model = "claude-opus-5-5"
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
        return self.project("setup", "1", "--state-dir", str(self.state), "--model", self.model, *extra, cwd=cwd)

    def later(self, command: str, *extra: str, cwd: Path | None = None) -> tuple[int, str, str]:
        return self.project(command, "1", "--state-dir", str(self.state), *extra, cwd=cwd)

    def folder(self, model: str | None = None) -> Path:
        return self.state / "prs" / REPO / "1" / review.model_key(model or self.model)

    def read_state(self, model: str | None = None) -> dict:
        return json.loads((self.folder(model) / "state.json").read_text())

    def lock(self, sha: str, model: str | None = None) -> Path:
        return self.folder(model) / f"{sha}.lock"


class SetupTests(ReviewCase):
    def test_sets_up_the_exact_head_and_posts_the_start_comment_as_the_reviewer(self) -> None:
        code, out, err = self.setup_review("--loop", "example-loop")

        self.assertEqual(0, code, err)
        state = self.read_state()
        self.assertEqual((self.head, self.base, 1), (state["sha"], state["base"], state["heads"]))
        self.assertEqual(REPO, state["repo"])
        self.assertEqual(str(self.checkout.resolve()), state["checkout"])
        worktree = Path(state["worktree"])
        self.assertEqual(self.state / "worktrees" / REPO / "1" / "claude-opus-5-5" / self.head[:12], worktree)
        self.assertEqual(self.head, git(worktree, "rev-parse", "HEAD"))
        self.assertFalse(worktree.is_relative_to(self.checkout), "never inside the checkout")
        self.assertTrue(state["trusted"])
        self.assertEqual({"id": 101, "created_at": "2026-09-27T01:02:03Z"}, state["start_comment"])
        self.assertEqual(("claude-opus-5-5", "claude-opus-5-5"), (state["model"], state["identity"]))
        self.assertEqual(("example-loop", False), (state["loop"], state["rereview"]))
        self.assertEqual([], json.loads((self.state / "loops" / "example-loop" / "published.json").read_text()))
        self.assertEqual(
            f"{review.MARK} **Projector review started** · `1.2.3` · model `claude-opus-5-5` · "
            f"reviewing `{self.head[:7]}`\n\n<!-- projector-start v=1 sha={self.head} model=claude-opus-5-5 -->\n",
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
        self.assertEqual([], list(self.state.rglob("*.lock")) if self.state.exists() else [])

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

    def test_an_edit_that_does_not_read_back_records_nothing_and_running_move_again_retries_it(self) -> None:
        self.assertEqual(0, self.setup_review()[0])
        new = self.push_head("second head")
        self.github.pr["head"]["sha"] = new
        fake = self.github

        def dropping_edits(args: list[str], token: str | None = None) -> str:
            return "{}" if args[:3] == ["api", "-X", "PATCH"] else fake(args, token)

        with mock.patch.object(review, "run_gh", dropping_edits):
            code, _, err = self.later("move")

        self.assertEqual(1, code)
        self.assertIn("run `project review move 1` again", err)
        self.assertEqual((self.head, 1), (self.read_state()["sha"], self.read_state()["heads"]))
        self.assertTrue(self.lock(self.head).exists())
        self.assertFalse(self.lock(new).exists())

        code, _, err = self.later("move")

        self.assertEqual(0, code, err)
        self.assertEqual((new, 2), (self.read_state()["sha"], self.read_state()["heads"]))
        self.assertIn(f"sha={new}", self.github.comments[101])


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

    def add_verdict(self, model: str | None, at: str = "2026-09-27T01:10:00Z", sha: str | None = None,
                    verdict: str = "clean", login: str = "operator", association: str = "OWNER",
                    event: str = "COMMENT") -> int:
        review_id = 800 + len(self.github.reviews)
        self.github.reviews.append({"id": review_id, "user": {"login": login}, "event": event, "comments": [],
                                    "commit_id": sha or self.head, "at": at, "association": association,
                                    "body": verdict_body(sha or self.head, model, verdict)})
        return review_id

    def as_model(self, model: str, *extra: str) -> tuple[int, str, str]:
        self.model = model
        return self.setup_review(*extra)


class PublishTests(PublishCase):
    def test_publishes_a_clean_self_review_marks_it_ready_and_cleans_up_in_order(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        code, out, err = self.publish("clean", cwd=Path(tempfile.mkdtemp()))

        self.assertEqual(0, code, err)
        review_body = self.posted()["body"]
        self.assertEqual(
            f"{review.MARK} **Projector review** of `{self.head[:7]}` · `1.2.3` · model `claude-opus-5-5` · "
            "**CLEAN** · took 12m 34s",
            review_body.splitlines()[0])
        self.assertIn(f"<!-- projector-review v=1 verdict=clean projector=1.2.3 model=claude-opus-5-5 "
                      f"sha={self.head} findings=0 seconds=754 covered=3/3 suggestions=0 -->", review_body)
        self.assertIn("0 finding threads: 0 resolved, 0 open", review_body)
        self.assertEqual(("COMMENT", self.head, "token-operator"),
                         (self.posted()["event"], self.posted()["commit_id"], "token-" + self.posted()["user"]["login"]))
        self.assertFalse(self.github.pr["draft"], "a clean self-review marks the pull request ready")
        self.assertEqual({}, self.github.comments, "the start comment is gone")
        self.assertFalse(self.lock(self.head).exists(), "the lock is released")
        record = json.loads((self.state / "loops" / "l1" / "published.json").read_text())
        self.assertEqual([(REPO, 1, self.head, 901, "clean", "claude-opus-5-5")],
                         [(r["repo"], r["number"], r["sha"], r["review_id"], r["verdict"], r["model"]) for r in record])
        steps = [" ".join(args) for args, _ in self.github.calls]
        ready = next(i for i, c in enumerate(steps) if c.startswith("pr ready"))
        reread = next(i for i, c in enumerate(steps) if c == f"api repos/{REPO}/pulls/1/reviews/901")
        delete = next(i for i, c in enumerate(steps) if c.startswith("api -X DELETE"))
        self.assertLess(reread, ready, "the newest verdict counts the review as read back")
        self.assertLess(ready, delete, "the start comment goes only after the re-read and draft state")
        self.assertIn("published review 901", out)

    def test_changes_requested_posts_findings_with_the_marker_and_returns_the_pull_request_to_draft(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.github.pr["draft"] = False

        code, _, err = self.publish("changes-requested", "--threads", str(self.threads(finding())))

        self.assertEqual(0, code, err)
        comment = self.posted()["comments"][0]
        self.assertEqual(("src/x.py", 3, "RIGHT"), (comment["path"], comment["line"], comment["side"]))
        self.assertTrue(comment["body"].startswith(f"<!-- projector-finding v=1 priority=P2 sha={self.head} "
                                                   "model=claude-opus-5-5 -->\n**P2 · The cache returns nil"))
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
            "body": (f"{review.MARK} **Projector review** · model `claude-opus-5-5` · effort `low` · "
                     "**APPROVED** · took 1m 00s\n\n"
                     "<!-- projector-review v=1 verdict=approved model=claude-opus-5-5 effort=low "
                     f"sha={self.head} findings=0 seconds=60 covered=1/1 -->\n"),
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
        self.model = "has`backtick"
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        self.refused(self.publish("clean"), "does not match review-pr/SKILL.md")

    def test_publish_without_a_review_holding_the_lock_is_refused(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.assertEqual(0, self.later("release", "--repo", REPO)[0])

        code, _, err = self.publish("clean")

        self.assertEqual(1, code)
        self.assertIn("project review setup 1", err)


# A full review's body below its marker, in the order method.md § 7 gives, and the suggestions sections
# reviewers have written by hand, which the label forms below wrap.
INTENT = ("This change caches the parsed configuration, so each command reads `.projector.toml` once.\n\n"
          "2 finding threads: 2 resolved, 0 open\n\nCovered 4 of 4 changed files; none generated.\n")
CHECKED = ("- **The cache.** `load_config` keys the cache on the checkout's path, and nothing writes the file "
           "between two reads.\n"
           "- **The gate.** The unit tests pass at this head.\n")
ITEMS = ("- `src/app/config.py:42`: name the cache `_parsed`, as the module's other caches are named.\n"
         "- `docs/cli.md`, the configuration section: say that an edit to `.projector.toml` takes effect on the\n"
         "  next command, not the next session.\n"
         "- Split `load` in two, so reading and checking the file can be tested apart.")
WRITTEN = [{"path": "src/app/config.py", "line": 42,
            "text": "name the cache `_parsed`, as the module's other caches are named."},
           {"path": "docs/cli.md", "line": None,
            "text": "the configuration section: say that an edit to `.projector.toml` takes effect on the next "
                    "command, not the next session."},
           {"path": None, "line": None, "text": "Split `load` in two, so reading and checking the file can be "
                                               "tested apart."}]
LABELED = {
    "level 2 heading": f"{INTENT}\nThe rest holds at this head:\n\n{CHECKED}\n## Suggestions\n\n{ITEMS}\n",
    "level 3 heading": f"{INTENT}\n### Suggestions\n\n{ITEMS}\n\n### What was checked\n\n{CHECKED}",
    "bold line": f"{INTENT}\n**Suggestions**\n\n{ITEMS}\n\n**What was checked**\n\n{CHECKED}",
    "plain line": f"{INTENT}\nSuggestions:\n{ITEMS}\nWhat was checked:\n{CHECKED}",
}
P3 = [{"path": "src/x.py", "line": 12, "priority": "P3", "body": "Name the helper `warm`, as\nthe cache calls it."},
      {"path": "README.md", "priority": "P3", "body": "Say that a cold start warms the cache."}]
LISTED = [{"path": "src/x.py", "line": 12, "text": "Name the helper `warm`, as the cache calls it."},
          {"path": "README.md", "line": None, "text": "Say that a cold start warms the cache."}]


class PublishSuggestionTests(PublishCase):
    def test_lists_p3_entries_in_one_form_after_the_body_and_counts_them_in_the_marker(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        code, _, err = self.publish("changes-requested", "--threads", str(self.threads(P3[0], finding(), P3[1])))

        self.assertEqual(0, code, err)
        posted = self.posted()
        self.assertTrue(posted["body"].endswith(
            "Covered 3 of 3 changed files.\n\n"
            "### Suggestions\n\n"
            "- `src/x.py:12`: Name the helper `warm`, as the cache calls it.\n"
            "- `README.md`: Say that a cold start warms the cache.\n"), posted["body"])
        self.assertIn(" covered=3/3 suggestions=2 -->", posted["body"])
        self.assertIn(" findings=1 ", posted["body"], "a P3 is no finding")
        self.assertIn("1 finding thread: 0 resolved, 1 open", posted["body"])
        self.assertEqual([("src/x.py", 3)], [(c["path"], c["line"]) for c in posted["comments"]],
                         "a P3 opens no thread, and its line need not be in the diff")
        self.assertEqual(LISTED, review.parse_suggestions(posted["body"]))
        self.assertEqual(2, review.verdict_marker(posted["body"]).suggestions)

    def test_a_head_whose_only_entries_are_p3_is_clean(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        code, _, err = self.publish("clean", "--threads", str(self.threads(*P3)))

        self.assertEqual(0, code, err)
        self.assertEqual(([], "COMMENT"), (self.posted()["comments"], self.posted()["event"]))
        self.assertIn(" findings=0 seconds=754 covered=3/3 suggestions=2 -->", self.posted()["body"])
        self.assertFalse(self.github.pr["draft"])

    def test_a_body_that_lists_suggestions_by_hand_publishes_as_written_and_is_counted(self) -> None:
        # Another host may run a skill that predates P3 entries and write the section itself.
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        text = LABELED["level 3 heading"].replace("2 finding threads: 2 resolved, 0 open", "{census}")

        code, _, err = self.publish("clean", body=self.body(text))

        self.assertEqual(0, code, err)
        self.assertIn(" covered=3/3 suggestions=3 -->", self.posted()["body"])
        self.assertTrue(self.posted()["body"].endswith(CHECKED), "the body publishes as written")
        self.assertEqual(WRITTEN, review.parse_suggestions(self.posted()["body"]))

    def test_refuses_a_body_that_lists_suggestions_beside_p3_entries(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        for label, text in LABELED.items():
            with self.subTest(label):
                body = self.body(text.replace("2 finding threads: 2 resolved, 0 open", "{census}"))

                code, _, err = self.publish("clean", "--threads", str(self.threads(*P3)), body=body)

                self.assertEqual(1, code)
                self.assertIn("the body has a suggestions section of its own, and the threads file gives P3 "
                              "entries", err)
                self.assertEqual([], self.github.reviews, "nothing was posted")
                self.assertTrue(self.lock(self.head).exists(), "the refusal keeps the lock")

    def test_refuses_a_p3_entry_the_list_could_not_show(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        for entry, words in (({"priority": "P3", "body": "Rename it."}, "finding 2 is a P3 and needs the path"),
                             ({"path": "a b.py", "priority": "P3", "body": "Rename it."}, "with no space"),
                             ({"path": "a.py", "line": 0, "priority": "P3", "body": "Rename it."},
                              "a P3's line is a line number"),
                             ({"path": "a.py", "priority": "P3", "body": " \n"}, "a P3 needs a body"),
                             ({"path": "a.py", "priority": "P3", "body": "<!-- projector-finding v=1 --> x"},
                              "leave Projector's markers out")):
            with self.subTest(words):
                code, _, err = self.publish("clean", "--threads", str(self.threads(P3[0], entry)))

                self.assertEqual(1, code)
                self.assertIn(words, err)
                self.assertEqual([], self.github.reviews, "nothing was posted")

    def test_a_priority_outside_p1_p2_and_p3_is_refused(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        code, _, err = self.publish("changes-requested", "--threads", str(self.threads(finding(priority="P4"))))

        self.assertEqual(1, code)
        self.assertIn("priority must be P1, P2, or P3", err)


def verdict_body(sha: str, model: str | None, verdict: str = "clean") -> str:
    """A Projector review's signature line and marker for `sha` by `model`, or in an older shape naming no model."""
    if model is None:
        return f"**Projector review**\n\n<!-- projector-review v=1 verdict={verdict} sha={sha} findings=0 -->\n"
    return (f"{review.MARK} **Projector review** · `1.2.3` · model `{model}` · **CLEAN** · took 1m 00s\n\n"
            f"<!-- projector-review v=1 verdict={verdict} projector=1.2.3 model={model} sha={sha} findings=0 "
            "seconds=60 covered=1/1 -->\n")


# The comment `project review hold --add` posts for fix-pr while it builds a change for the pull request.
HOLD = ("Held in draft while the following changes are implemented:\n\n"
        "- Skip fenced code blocks when looking for headings\n\n"
        "<!-- projector-hold v=1 -->\n")


class ModelTests(PublishCase):
    """Each model is a reviewer of its own: its own state, lock, worktree, and collision check."""

    def test_two_models_set_up_one_head_side_by_side_and_publishing_one_leaves_the_other(self) -> None:
        self.assertEqual(0, self.as_model("claude-opus-5-5", "--loop", "claude")[0])
        code, _, err = self.as_model("gpt-5.5", "--loop", "gpt")

        self.assertEqual(0, code, err)
        opus, gpt = self.read_state("claude-opus-5-5"), self.read_state("gpt-5.5")
        self.assertNotEqual(opus["worktree"], gpt["worktree"])
        self.assertEqual(self.state / "worktrees" / REPO / "1" / "gpt-5.5" / self.head[:12], Path(gpt["worktree"]))
        self.assertEqual(self.head, git(Path(opus["worktree"]), "rev-parse", "HEAD"))
        self.assertIn("model=gpt-5.5 -->", self.github.comments[gpt["start_comment"]["id"]])

        published = self.publish("clean", "--model", "claude-opus-5-5", loop="claude")

        self.assertEqual(0, published[0], published[2])
        self.assertFalse(self.lock(self.head, "claude-opus-5-5").exists())
        self.assertTrue(self.lock(self.head, "gpt-5.5").exists())
        self.assertEqual(gpt, self.read_state("gpt-5.5"))
        self.assertTrue(Path(gpt["worktree"]).is_dir())
        self.assertIn(gpt["start_comment"]["id"], self.github.comments)

    def test_a_command_after_setup_needs_model_when_two_models_are_set_up(self) -> None:
        self.assertEqual(0, self.as_model("claude-opus-5-5", "--loop", "claude")[0])
        self.assertEqual(0, self.as_model("gpt-5.5", "--loop", "gpt")[0])

        code, _, err = self.publish("clean", loop="gpt")

        self.assertEqual(1, code)
        self.assertIn("by claude-opus-5-5, gpt-5.5 are set up here; pass --model", err)
        self.assertEqual([], self.github.reviews)
        self.assertEqual(0, self.publish("clean", "--model", "gpt-5.5", loop="gpt")[0])

    def test_one_model_with_state_needs_no_model_flag(self) -> None:
        self.assertEqual(0, self.as_model("gpt-5.5", "--loop", "gpt")[0])

        code, _, err = self.publish("clean", loop="gpt")

        self.assertEqual(0, code, err)
        self.assertIn("model=gpt-5.5 ", self.posted()["body"])

    def test_another_sessions_live_start_comment_by_the_same_model_refuses_setup(self) -> None:
        foreign = self.github.comment(review.start_comment("claude-opus-5-5", self.head))

        code, _, err = self.setup_review("--loop", "l1")

        self.assertEqual(1, code)
        self.assertIn(f"start comment {foreign}", err)
        self.assertIn(f"run `project review setup 1 --model claude-opus-5-5 --replace {foreign}`", err)
        self.assertFalse(self.lock(self.head).exists(), "a refused setup releases its lock")
        self.assertEqual([foreign], list(self.github.comments), "nothing was posted")
        self.assertEqual(0, self.as_model("gpt-5.5")[0], "another model is a second opinion")

    def test_replace_deletes_a_stopped_sessions_start_comment_and_sets_up_in_its_place(self) -> None:
        stopped = self.github.comment(review.start_comment("claude-opus-5-5[1m]", self.head))

        code, out, err = self.setup_review("--loop", "l1", "--replace", str(stopped))

        self.assertEqual(0, code, err)
        posted = self.read_state()["start_comment"]["id"]
        self.assertEqual([posted], list(self.github.comments), "the new start comment replaces the stopped one")
        self.assertIn((["api", "-X", "DELETE", f"repos/{REPO}/issues/comments/{stopped}"], "token-operator"),
                      self.github.calls)
        self.assertIn(f"deleted start comment {stopped}", out)

    def test_replace_refuses_any_comment_but_this_models_start_comment_and_deletes_nothing(self) -> None:
        cases = {
            "another model's start comment": (self.github.comment(review.start_comment("gpt-5.5", self.head)),
                                              "is model gpt-5.5's"),
            "another account's start comment": (
                self.github.comment(review.start_comment("claude-opus-5-5", self.head), login="someone"),
                "is not a start comment operator posted"),
            "a comment that is no start comment": (self.github.comment("Looks good to me."),
                                                   "is not a start comment operator posted"),
            "a comment that does not exist": (999, "is not a start comment operator posted"),
        }
        comments = dict(self.github.comments)
        for name, (comment, why) in cases.items():
            with self.subTest(name):
                code, _, err = self.setup_review("--replace", str(comment))

                self.assertEqual(1, code)
                self.assertIn(f"comment {comment} {why}", err)
                self.assertFalse(self.lock(self.head).exists(), "a refused setup releases its lock")
        self.assertEqual(comments, self.github.comments)
        self.assertEqual([], self.github.posts())
        self.assertFalse(any(args[:3] == ["api", "-X", "DELETE"] for args, _ in self.github.calls))

    def test_the_same_model_at_another_context_window_is_the_same_reviewer_at_setup(self) -> None:
        self.github.comment(review.start_comment("claude-opus-5-5[1m]", self.head))

        self.assertEqual(1, self.setup_review()[0])

    def test_a_start_comment_naming_no_model_is_read_by_its_first_line(self) -> None:
        body = review.start_comment("claude-opus-5-5", self.head).replace(" model=claude-opus-5-5 -->", " -->")
        self.github.comment(body)

        self.assertEqual(1, self.setup_review()[0])
        self.assertEqual(0, self.as_model("gpt-5.5")[0])

    def test_a_start_comment_a_day_old_or_followed_by_its_models_verdict_is_not_live(self) -> None:
        stale = self.github.comment(review.start_comment("claude-opus-5-5", self.head),
                                    created_at="2026-09-25T01:00:00Z")
        self.assertEqual(0, self.setup_review()[0], "a start comment a day old is stale")
        self.assertEqual(0, self.later("release")[0])
        finished = self.github.comment(review.start_comment("claude-opus-5-5", self.head))
        self.add_verdict("claude-opus-5-5", at="2026-09-27T01:10:00Z")

        code, _, err = self.setup_review("--rereview")

        self.assertEqual(0, code, err)
        self.assertIn(stale, self.github.comments, "another session's comment is left for its own session")
        self.assertIn(finished, self.github.comments)

    def test_a_verdict_before_the_start_comment_leaves_it_live(self) -> None:
        self.add_verdict("claude-opus-5-5", at="2026-09-27T01:00:00Z")
        self.github.comment(review.start_comment("claude-opus-5-5", self.head))

        self.assertEqual(1, self.setup_review("--rereview")[0], "a re-review in progress")

    def test_setup_replaces_the_start_comment_this_machine_recorded(self) -> None:
        self.assertEqual(0, self.setup_review()[0])
        self.assertEqual(0, self.later("release")[0])

        code, _, err = self.setup_review("--rereview")

        self.assertEqual(0, code, err)
        self.assertEqual([102], list(self.github.comments), "the earlier comment is deleted, not left live")
        self.assertEqual(102, self.read_state()["start_comment"]["id"])

    def test_another_models_verdict_on_the_head_is_not_a_collision(self) -> None:
        self.add_verdict("gpt-5.5")
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        code, _, err = self.publish("clean")

        self.assertEqual(0, code, err)

    def test_a_verdict_by_the_same_model_or_naming_no_model_is_another_loop(self) -> None:
        for model in ("claude-opus-5-5[1m]", "Claude-Opus-5-5", None):
            with self.subTest(model=model):
                self.setUp()
                self.add_verdict(model)
                self.assertEqual(0, self.setup_review("--loop", "l1")[0])

                code, _, err = self.publish("clean")

                self.assertEqual(1, code)
                self.assertIn("another loop of the same model", err)
                self.assertEqual(1, len(self.github.reviews))

    def test_setup_refuses_a_model_id_no_folder_can_be_made_from(self) -> None:
        for model in ("", "has space", ".hidden", "[1m]", "..", "a\tb"):
            with self.subTest(model=model):
                code, _, err = self.as_model(model)

                self.assertEqual(1, code)
                self.assertIn("is not a usable model ID", err)
        self.assertEqual([], self.github.posts())

    def test_two_models_that_share_a_folder_refuse_rather_than_share_state(self) -> None:
        self.assertEqual(0, self.as_model("a/b")[0])
        self.assertEqual("a-b", self.folder().name)

        code, _, err = self.as_model("a-b")

        self.assertEqual(1, code)
        self.assertIn("is model a/b's", err)
        self.assertIn(f"`project review finish 1 --repo {REPO} --model a/b`", err)
        self.assertEqual("a/b", self.read_state("a/b")["model"])

    def test_every_command_the_review_loop_runs_takes_its_loop_and_model(self) -> None:
        skill = (Path(cli.__file__).resolve().parents[2] / "skills" / "start-review-loop" / "SKILL.md").read_text()
        sentence = re.search(r"passes `--loop <id>` and `--model <model-id>` to each\s+`project review` command it "
                             r"runs on a pull request: (.*?)\. ", skill, re.S)
        commands = re.findall(r"`(\w+)`", sentence.group(1))
        self.assertEqual(["setup", "move", "census", "settle", "interdiff", "gate", "publish", "release", "finish"],
                         commands)
        flags = ("--loop", "l1", "--model", "claude-opus-5-5")
        # Each command, as the loop runs it on one pull request: a refusal is fine, an unknown flag is not.
        self.assertEqual(0, self.project("setup", "1", "--state-dir", str(self.state), *flags)[0])
        self.github.pr["head"]["sha"] = self.push_head("second head")
        extras = {"publish": ("--verdict", "clean", "--body", str(self.body()), "--covered", "3/3"),
                  "settle": ("--thread", "PRRT_none", "--result", "fixed", "--body", str(self.body()))}
        ran = {"setup": 0}
        for command in commands[1:]:
            ran[command] = self.later(command, *flags, *extras.get(command, ()))[0]

        self.assertEqual({"setup": 0, "move": 0, "census": 0, "settle": 1, "interdiff": 1, "gate": 1, "publish": 0,
                          "release": 0, "finish": 0},
                         ran, "settle refuses a thread that is not on the pull request, interdiff with no full review "
                              "to build on, and gate with review.gate unset")

    def test_move_keeps_the_model_in_the_start_comment(self) -> None:
        self.assertEqual(0, self.setup_review()[0])
        self.github.pr["head"]["sha"] = self.push_head("second head")

        self.assertEqual(0, self.later("move")[0])

        self.assertIn(f" sha={self.github.pr['head']['sha']} model=claude-opus-5-5 -->", self.github.comments[101])


class FinishTests(PublishCase):
    """`finish` ends one model's review on this machine, so nothing it made is removed by hand."""

    def finish(self, *extra: str, cwd: Path | None = None) -> tuple[int, str, str]:
        return self.later("finish", *extra, cwd=cwd)

    def registered(self) -> set[Path]:
        """The worktrees Git lists for the checkout."""
        listing = git(self.checkout, "worktree", "list", "--porcelain").splitlines()
        return {Path(line.removeprefix("worktree ")).resolve() for line in listing if line.startswith("worktree ")}

    def deletions(self) -> list[tuple[list[str], str | None]]:
        return [(args, token) for args, token in self.github.calls if args[:3] == ["api", "-X", "DELETE"]]

    def test_removes_every_worktree_the_reviews_made_and_the_state_and_keeps_the_loop_record(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.assertEqual(0, self.publish("clean")[0])
        self.github.pr["head"]["sha"] = self.push_head("second head")
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.github.pr["head"]["sha"] = self.push_head("third head")
        self.assertEqual(0, self.later("move")[0])
        self.assertEqual(0, self.publish("clean")[0])
        made = [Path(path) for path in self.read_state()["worktrees"]]
        record = (self.state / "loops" / "l1" / "published.json").read_text()

        code, out, err = self.finish("--repo", REPO, "--loop", "l1", cwd=Path(tempfile.mkdtemp()))

        self.assertEqual(0, code, err)
        self.assertEqual(3, len(made), "a later setup keeps the worktrees the earlier heads made")
        for path in made:
            self.assertFalse(path.exists())
            self.assertNotIn(path.resolve(), self.registered())
            self.assertIn(f"removed worktree {path}", out)
        self.assertFalse(self.folder().exists(), "the state and the review's folder are gone")
        self.assertFalse((self.state / "worktrees" / REPO / "1" / review.model_key(self.model)).exists())
        self.assertEqual(record, (self.state / "loops" / "l1" / "published.json").read_text())
        self.assertIn("the loop's record of published reviews stays", out)

    def test_deletes_its_models_start_comments_that_no_longer_mean_a_review_in_progress(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.assertEqual(0, self.publish("clean")[0])
        other = "c" * 40
        before_verdict = self.github.comment(review.start_comment(self.model, self.head),
                                             created_at="2026-09-27T01:00:00Z")
        day_old = self.github.comment(review.start_comment(self.model, other), created_at="2026-09-25T01:00:00Z")
        running = self.github.comment(review.start_comment(self.model, other))
        second_opinion = self.github.comment(review.start_comment("gpt-5.5", self.head),
                                             created_at="2026-09-25T01:00:00Z")
        another_account = self.github.comment(review.start_comment(self.model, self.head), login="someone",
                                              created_at="2026-09-25T01:00:00Z")
        published = len(self.deletions())

        code, out, err = self.finish()

        self.assertEqual(0, code, err)
        self.assertEqual({running, second_opinion, another_account}, set(self.github.comments))
        self.assertEqual([(["api", "-X", "DELETE", f"repos/{REPO}/issues/comments/{comment}"], "token-operator")
                          for comment in (before_verdict, day_old)], self.deletions()[published:])
        self.assertIn(f"deleted start comment {before_verdict}\ndeleted start comment {day_old}\n", out)

    def test_refuses_an_unpublished_review_without_force_and_discards_it_with_force(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        state = self.read_state()
        worktree = Path(state["worktree"])

        code, _, err = self.finish()

        self.assertEqual(1, code)
        self.assertIn(f"review of {REPO}#1 at {self.head[:7]} is unpublished", err)
        self.assertIn(f"`project review finish 1 --repo {REPO} --force`", err)
        self.assertEqual(state, self.read_state())
        self.assertTrue(worktree.is_dir())
        self.assertTrue(self.lock(self.head).exists())
        self.assertEqual([101], list(self.github.comments))
        self.assertEqual([], self.deletions())

        code, _, err = self.finish("--force")

        self.assertEqual(0, code, err)
        self.assertEqual({}, self.github.comments)
        self.assertEqual([(["api", "-X", "DELETE", f"repos/{REPO}/issues/comments/101"], "token-operator")],
                         self.deletions())
        self.assertFalse(worktree.exists())
        self.assertFalse(self.folder().exists(), "the lock and the state are gone")

    def test_a_second_run_finds_nothing_to_clean_and_succeeds(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.assertEqual(0, self.publish("clean")[0])
        self.assertEqual(0, self.finish()[0])
        calls = len(self.github.calls)

        code, out, err = self.finish()

        self.assertEqual(0, code, err)
        self.assertEqual(f"no review of {REPO}#1 is set up here; there is nothing to finish\n", out)
        code, out, err = self.finish("--repo", REPO, "--model", self.model, "--json", cwd=Path(tempfile.mkdtemp()))

        self.assertEqual(0, code, err)
        self.assertEqual({"repo": REPO, "number": 1, "model": self.model, "comments": [], "worktrees": [],
                          "released": [], "state": False}, json.loads(out))
        self.assertEqual(calls, len(self.github.calls), "with nothing set up, finish asks GitHub nothing")

    def test_outside_a_checkout_it_needs_repo(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.assertEqual(0, self.publish("clean")[0])
        state = self.read_state()

        code, _, err = self.finish(cwd=Path(tempfile.mkdtemp()))

        self.assertEqual(1, code)
        self.assertIn("so there is no repository to finish a review in; pass --repo owner/name", err)
        self.assertEqual(state, self.read_state())
        self.assertTrue(Path(state["worktree"]).is_dir())

    def test_a_run_in_another_repositorys_checkout_leaves_its_review_alone_and_refuses(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        state = self.read_state()
        elsewhere = Path(tempfile.mkdtemp())
        git(elsewhere, "init", "--quiet")
        git(elsewhere, "remote", "add", "origin", "https://github.com/other/app.git")

        for extra in (("--force", "--model", self.model), ("--model", self.model), ()):
            with self.subTest(extra=extra):
                code, _, err = self.finish(*extra, cwd=elsewhere)

                self.assertEqual(1, code)
                self.assertIn(f"no review of other/app#1, this checkout's origin, is set up here, and a review of #1 "
                              f"in {REPO} is; pass --repo", err)
        code, out, err = self.finish("--repo", "other/app", "--force", cwd=elsewhere)

        self.assertEqual(0, code, err)
        self.assertIn("no review of other/app#1 is set up here", out)
        self.assertEqual(state, self.read_state())
        self.assertTrue(Path(state["worktree"]).is_dir())
        self.assertTrue(self.lock(self.head).exists())
        self.assertEqual([101], list(self.github.comments))
        self.assertEqual([], self.deletions())

    def test_a_state_that_names_another_repository_is_left_alone(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.assertEqual(0, self.publish("clean")[0])
        state = dict(self.read_state(), repo="other/app")
        (self.folder() / "state.json").write_text(json.dumps(state))

        code, _, err = self.finish("--force")

        self.assertEqual(1, code)
        self.assertIn(f"names other/app, not {REPO}, so finish leaves it alone", err)
        self.assertEqual(state, self.read_state())
        self.assertTrue(Path(state["worktree"]).is_dir())

    def test_a_publish_that_stopped_after_posting_is_named_as_unfinished(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.github.fail = "pr ready"
        self.assertEqual(1, self.publish("clean")[0])
        self.github.fail = None

        code, _, err = self.finish()

        self.assertEqual(1, code)
        self.assertIn(f"publish of its review of {REPO}#1 at {self.head[:7]} did not finish: review 901 is posted, "
                      "and start comment 101 still stands; run the same `project review publish 1` command again",
                      err)
        self.assertEqual(0, self.publish("clean")[0], "the publish finishes as the refusal says")
        self.assertEqual(0, self.finish()[0])

    def test_removes_a_worktree_whose_checkout_is_gone(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.assertEqual(0, self.publish("clean")[0])
        state = self.read_state()
        state["checkout"] = str(Path(tempfile.mkdtemp()) / "gone")
        (self.folder() / "state.json").write_text(json.dumps(state))

        code, _, err = self.finish()

        self.assertEqual(0, code, err)
        self.assertFalse(Path(state["worktree"]).exists())
        self.assertFalse(self.folder().exists())

    def test_leaves_another_models_review_alone(self) -> None:
        self.assertEqual(0, self.as_model("claude-opus-5-5", "--loop", "claude")[0])
        self.assertEqual(0, self.as_model("gpt-5.5", "--loop", "gpt")[0])
        gpt = self.read_state("gpt-5.5")
        self.assertEqual(0, self.publish("clean", "--model", "claude-opus-5-5", loop="claude")[0])

        code, _, err = self.finish("--model", "claude-opus-5-5", "--loop", "claude")

        self.assertEqual(0, code, err)
        self.assertFalse(self.folder("claude-opus-5-5").exists())
        self.assertEqual(gpt, self.read_state("gpt-5.5"))
        self.assertTrue(Path(gpt["worktree"]).is_dir())
        self.assertTrue(self.lock(self.head, "gpt-5.5").exists())
        self.assertIn(gpt["start_comment"]["id"], self.github.comments)

    def test_the_review_skills_clean_up_through_the_commands_alone(self) -> None:
        # Removing a worktree by hand leaves the state naming it, and a raw comment deletion is what Claude
        # Code's auto mode refuses, where one narrow rule allows the command.
        skills = Path(cli.__file__).resolve().parents[2] / "skills"
        review_pr = (skills / "review-pr" / "SKILL.md").read_text()
        review_loop = (skills / "start-review-loop" / "SKILL.md").read_text()

        self.assertIn("`project review finish <number>`", review_pr)
        self.assertIn("`--replace <comment-id>`", review_pr)
        self.assertIn("`project review finish <number> --repo <owner>/<repo> --force`", review_loop)
        for text in (review_pr, review_loop):
            self.assertNotIn("issues/comments", text)
            self.assertNotIn("git worktree", text)


class SettleTests(PublishCase):
    """`settle` writes the verify marker from the review's state, so no reply on a finding is typed by hand."""

    THREAD = "PRRT_finding"
    REPLY = "The warm-up runs in `load` now, so a restart starts warm."

    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.threads: dict[str, dict] = {}
        self.add_thread(self.THREAD, FINDING % self.head)
        self.next_reply = 601
        self.calls: list[tuple[list[str], str | None]] = []
        fake = self.github

        def answer(args: list[str], token: str | None = None) -> str:
            fields = dict(arg.split("=", 1) for arg in args[3::2]) if args[:2] == ["api", "graphql"] else {}
            query = fields.get("query", "")
            if "node(id:" in query or "ReviewThread" in query:
                self.calls.append((list(args), token))
            if "node(id:" in query:
                return self.node(fields["t"])
            if "addPullRequestReviewThreadReply" in query:
                return self.reply(fields["t"], fields["b"], token)
            if "unresolveReviewThread" in query or "resolveReviewThread" in query:
                self.threads[fields["t"]]["resolved"] = "unresolveReviewThread" not in query
                return "{}"
            if len(args) == 2 and args[1].startswith(f"repos/{REPO}/pulls/comments/"):
                return self.comment(int(args[1].rsplit("/", 1)[1]))
            return fake(args, token)

        patcher = mock.patch.object(review, "run_gh", answer)
        patcher.start()
        self.addCleanup(patcher.stop)

    def add_thread(self, thread_id: str, first: str, *, number: int = 1, repo: str = REPO,
                   resolved: bool = False) -> None:
        self.threads[thread_id] = {"resolved": resolved, "number": number, "repo": repo,
                                   "comments": [{"databaseId": 500 + len(self.threads), "body": first,
                                                 "login": "operator"}]}

    def node(self, thread_id: str) -> str:
        if thread_id not in self.threads:
            raise NotFound(f"gh: Could not resolve to a node with the global id of '{thread_id}'")
        found = self.threads[thread_id]
        last = found["comments"][-1]
        return json.dumps({"data": {"node": {
            "isResolved": found["resolved"],
            "pullRequest": {"number": found["number"], "repository": {"nameWithOwner": found["repo"]}},
            "first": {"nodes": [{"body": found["comments"][0]["body"]}]},
            "last": {"nodes": [{"databaseId": last["databaseId"], "body": last["body"]}]}}}})

    def reply(self, thread_id: str, body: str, token: str | None) -> str:
        reply_id, self.next_reply = self.next_reply, self.next_reply + 1
        self.threads[thread_id]["comments"].append({"databaseId": reply_id, "body": body,
                                                    "login": (token or "").removeprefix("token-")})
        return json.dumps({"data": {"addPullRequestReviewThreadReply": {"comment": {"databaseId": reply_id}}}})

    def comment(self, comment_id: int) -> str:
        for found in self.threads.values():
            for posted in found["comments"]:
                if posted["databaseId"] == comment_id:
                    return json.dumps({"id": comment_id, "body": posted["body"],
                                       "html_url": f"https://github.com/{REPO}/pull/1#discussion_r{comment_id}"})
        raise NotFound("gh: Not Found (HTTP 404)")

    def reply_file(self, text: str | None = None) -> Path:
        path = self.dir / "reply.md"
        path.write_text(self.REPLY + "\n" if text is None else text)
        return path

    def settle(self, result: str, *extra: str, thread: str | None = None, body: Path | None = None
               ) -> tuple[int, str, str]:
        return self.later("settle", "--thread", thread or self.THREAD, "--result", result,
                          "--body", str(body or self.reply_file()), *extra)

    def mutations(self) -> list[str]:
        return [next(a for a in args if a.startswith("query=")).split("(input", 1)[0].rsplit(" ", 1)[-1]
                for args, _ in self.calls if any(a.startswith("query=mutation") for a in args)]

    def test_each_result_replies_with_the_marker_and_resolves_reopens_or_leaves_the_thread(self) -> None:
        for result, before, after, mutations in (
                ("fixed", False, True, ["addPullRequestReviewThreadReply", "resolveReviewThread"]),
                ("accepted", False, True, ["addPullRequestReviewThreadReply", "resolveReviewThread"]),
                ("withdrawn", False, True, ["addPullRequestReviewThreadReply", "resolveReviewThread"]),
                ("kept", False, False, ["addPullRequestReviewThreadReply"]),
                ("kept", True, True, ["addPullRequestReviewThreadReply"]),
                ("reopened", True, False, ["addPullRequestReviewThreadReply", "unresolveReviewThread"])):
            with self.subTest(result=result, resolved=before):
                self.add_thread(self.THREAD, FINDING % self.head, resolved=before)
                self.calls.clear()

                code, out, err = self.settle(result, "--loop", "l1", "--model", self.model)

                self.assertEqual(0, code, err)
                posted = self.threads[self.THREAD]["comments"][-1]
                self.assertEqual(f"<!-- projector-verify v=1 result={result} sha={self.head} model=claude-opus-5-5 -->"
                                 f"\n{self.REPLY}\n", posted["body"])
                self.assertEqual("operator", posted["login"], "the reviewer posts the reply")
                self.assertTrue(all(token == "token-operator" for args, token in self.calls
                                    if any(a.startswith("query=mutation") for a in args)))
                self.assertIs(after, self.threads[self.THREAD]["resolved"])
                self.assertEqual(mutations, self.mutations(), "the reply goes first, so no loop reads the thread "
                                                              "as answered without it")
                self.assertIn(f"replied {result} on thread {self.THREAD} of {REPO}#1 at {self.head[:7]}", out)
                self.assertIn(f"the thread is {'resolved' if after else 'open'}", out)

    def test_the_marker_names_the_head_and_model_the_review_state_records(self) -> None:
        self.github.pr["head"]["sha"] = self.push_head("second head")
        self.assertEqual(0, self.later("move")[0])

        code, out, err = self.settle("fixed", "--json")

        self.assertEqual(0, code, err)
        moved = self.read_state()["sha"]
        self.assertTrue(self.threads[self.THREAD]["comments"][-1]["body"].startswith(
            f"<!-- projector-verify v=1 result=fixed sha={moved} model=claude-opus-5-5 -->\n"))
        self.assertEqual({"repo": REPO, "number": 1, "thread": self.THREAD, "result": "fixed", "sha": moved,
                          "model": self.model, "comment": 601,
                          "url": f"https://github.com/{REPO}/pull/1#discussion_r601", "resolved": True},
                         json.loads(out))

    def test_refuses_any_thread_but_a_projector_finding_thread_on_this_pull_request(self) -> None:
        self.add_thread("PRRT_other_pr", FINDING % self.head, number=2)
        self.add_thread("PRRT_other_repo", FINDING % self.head, repo="acme/other")
        self.add_thread("PRRT_person", "Could this use the cache?")
        for thread, why in (("PRRT_missing", "is not a review thread on acme/app#1"),
                            ("PRRT_other_pr", "is not a review thread on acme/app#1"),
                            ("PRRT_other_repo", "is not a review thread on acme/app#1"),
                            ("PRRT_person", "carries no Projector finding")):
            with self.subTest(thread=thread):
                code, _, err = self.settle("fixed", thread=thread)

                self.assertEqual(1, code)
                self.assertIn(why, err)
        self.assertEqual([], self.mutations(), "nothing was posted or resolved")
        self.assertEqual([1] * 4, [len(found["comments"]) for found in self.threads.values()])

    def test_refuses_a_result_outside_the_set_and_a_body_that_is_empty_or_carries_a_marker(self) -> None:
        with self.assertRaises(SystemExit) as refused, redirect_stderr(io.StringIO()):
            self.settle("approved")
        self.assertEqual(2, refused.exception.code)
        with self.assertRaisesRegex(ReviewError, "--result must be one of fixed, accepted, withdrawn, kept, reopened"):
            review.settle(review.Paths(self.state, REPO, 1, self.model), self.THREAD, "approved", self.reply_file())
        for text, why in (("\n", "the body is empty"),
                          (f"<!-- projector-verify v=1 result=fixed sha={self.head} model=x -->\nFixed.\n",
                           "leave the marker out of the body"),
                          (None, "does not exist")):
            with self.subTest(why=why):
                body = self.dir / "missing.md" if text is None else self.reply_file(text)

                code, _, err = self.settle("fixed", body=body)

                self.assertEqual(1, code)
                self.assertIn(why, err)
        self.assertEqual([], self.mutations())

    def test_a_reply_that_does_not_read_back_with_its_marker_exits_one(self) -> None:
        def mangled(comment_id: int) -> str:
            return json.dumps({"id": comment_id, "body": self.REPLY, "html_url": None})

        with mock.patch.object(self, "comment", mangled):
            code, _, err = self.settle("fixed")

        self.assertEqual(1, code)
        self.assertIn("does not open with <!-- projector-verify v=1 result=fixed", err)

    def test_a_thread_whose_resolution_does_not_take_exits_one(self) -> None:
        def unchanged(args: list[str], token: str | None = None) -> str:
            if any(a.startswith("query=mutation") and "resolveReviewThread" in a for a in args):
                return "{}"
            return answer(args, token)

        answer = review.run_gh
        with mock.patch.object(review, "run_gh", unchanged):
            code, _, err = self.settle("fixed")

        self.assertEqual(1, code)
        self.assertIn(f"thread {self.THREAD} is open after fixed; run settle again", err)

    def test_running_settle_again_after_its_reply_posted_resolves_without_a_second_reply(self) -> None:
        marker = f"<!-- projector-verify v=1 result=fixed sha={self.head} model=claude-opus-5-5 -->"
        self.reply(self.THREAD, f"{marker}\n{self.REPLY}\n", "token-operator")

        code, _, err = self.settle("fixed")

        self.assertEqual(0, code, err)
        self.assertEqual(2, len(self.threads[self.THREAD]["comments"]), "no second reply")
        self.assertEqual(["resolveReviewThread"], self.mutations())
        self.assertTrue(self.threads[self.THREAD]["resolved"])

    def test_refuses_without_a_review_set_up(self) -> None:
        code, _, err = self.later("settle", "--repo", REPO, "--model", "gpt-5.5", "--thread", self.THREAD,
                                  "--result", "fixed", "--body", str(self.reply_file()))

        self.assertEqual(1, code)
        self.assertIn("project review setup 1 --model gpt-5.5", err)
        self.assertEqual([], self.mutations())

    def test_review_pr_settles_threads_through_the_command_alone(self) -> None:
        # A hand-typed marker that misses `projector-verify` makes the reviewer's own reply read as the author
        # answering, which fires RESPONDED.
        skill = (Path(cli.__file__).resolve().parents[2] / "skills" / "review-pr" / "SKILL.md").read_text()

        self.assertIn("project review settle <number> --thread <thread-id> --result <result> --body <file>", skill)
        for result in review.SETTLED:
            self.assertIn(f"`--result {result}`", skill)
        self.assertNotIn("resolveReviewThread", skill)
        self.assertNotIn("Every reply you post opens with", skill)


def iso(at: datetime) -> str:
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")


class LatestWinsCase(PublishCase):
    """Publishing as claude-opus-5-5 on head H, where gpt-5.5 may have reviewed H too."""

    def json_publish(self, verdict: str, *extra: str, loop: str = "l1") -> dict:
        code, out, err = self.publish(verdict, "--json", *extra, loop=loop)
        self.assertEqual(0, code, err)
        return json.loads(out)


class LatestWinsTests(LatestWinsCase):
    def test_a_clean_newest_verdict_marks_it_ready_over_an_older_models_change_request(self) -> None:
        older = self.add_verdict("gpt-5.5", verdict="changes-requested")
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        result = self.json_publish("clean")

        self.assertEqual(("claude-opus-5-5", "clean", False), (result["newest"]["model"], result["newest"]["verdict"],
                                                                result["draft"]))
        self.assertEqual(result["review_id"], result["newest"]["review"])
        self.assertFalse(self.github.pr["draft"], f"gpt-5.5's review {older} is older, so it no longer counts")

    def test_a_newer_change_request_drafts_it(self) -> None:
        self.add_verdict("gpt-5.5")
        self.github.pr["draft"] = False
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        result = self.json_publish("changes-requested", "--threads", str(self.threads(finding())))

        self.assertEqual(("changes-requested", True), (result["newest"]["verdict"], result["draft"]))
        self.assertTrue(self.github.pr["draft"])

    def test_a_change_request_posted_after_this_clean_verdict_wins(self) -> None:
        # gpt-5.5 submits while claude-opus-5-5 is between its own submit and setting draft state.
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        newer = []
        self.github.after_post = lambda: newer.append(
            self.add_verdict("gpt-5.5", verdict="changes-requested", at=iso(self.clock + timedelta(seconds=1))))

        code, out, err = self.publish("clean")

        self.assertEqual(0, code, err)
        self.assertTrue(self.github.pr["draft"], "the newest verdict requests changes")
        self.assertFalse(any(args[:2] == ["pr", "ready"] for args, _ in self.github.calls), "it was a draft already")
        self.assertIn(f"the newest verdict on the head is gpt-5.5's changes-requested, review {newer[0]}", out)

    def test_two_models_that_publish_together_leave_the_later_ones_draft_state(self) -> None:
        for gpt_verdict in ("clean", "changes-requested"):
            with self.subTest(gpt=gpt_verdict):
                self.setUp()
                self.assertEqual(0, self.as_model("claude-opus-5-5", "--loop", "claude")[0])
                self.assertEqual(0, self.as_model("gpt-5.5", "--loop", "gpt")[0])
                threads = ["--threads", str(self.threads(finding()))] if gpt_verdict == "changes-requested" else []

                def gpt_publishes() -> None:
                    # A second later, between claude-opus-5-5's submit and its draft state.
                    self.clock += timedelta(seconds=1)
                    self.assertEqual(0, self.publish(gpt_verdict, "--model", "gpt-5.5", *threads, loop="gpt")[0])

                self.github.after_post = gpt_publishes
                result = self.json_publish("clean", "--model", "claude-opus-5-5", loop="claude")

                self.assertEqual(("gpt-5.5", gpt_verdict), (result["newest"]["model"], result["newest"]["verdict"]))
                self.assertEqual(gpt_verdict == "changes-requested", self.github.pr["draft"])

    def test_a_clean_verdict_needs_another_models_finding_settled_first(self) -> None:
        self.github.pages = [page([thread("T1", FINDING.replace(" -->", " model=gpt-5.5 -->") % self.head)], None)]
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        code, _, err = self.publish("clean")

        self.assertEqual(1, code)
        self.assertIn("a clean verdict needs no open finding, and 1 are open", err)
        self.assertEqual([], self.github.reviews)

    def test_a_finding_another_model_posts_mid_publish_keeps_the_pull_request_a_draft(self) -> None:
        for draft in (True, False):
            with self.subTest(draft=draft):
                self.setUp()
                self.github.pr["draft"] = draft
                self.assertEqual(0, self.as_model("claude-opus-5-5", "--loop", "claude")[0])
                self.assertEqual(0, self.as_model("gpt-5.5", "--loop", "gpt")[0])

                def gpt_publishes() -> None:
                    # After claude-opus-5-5 counted no open finding, and a second before its clean verdict lands.
                    code, _, err = self.publish("changes-requested", "--model", "gpt-5.5", "--threads",
                                                str(self.threads(finding())), loop="gpt")
                    self.assertEqual(0, code, err)
                    marker = self.github.reviews[-1]["comments"][0]["body"].splitlines()[0]
                    self.github.pages = [page([thread("T1", marker)], None)]
                    self.clock += timedelta(seconds=1)

                self.github.before_post = gpt_publishes
                as_json = ("--json",) if draft else ()
                code, out, err = self.publish("clean", "--model", "claude-opus-5-5", *as_json, loop="claude")

                self.assertEqual(0, code, err)
                self.assertTrue(self.github.pr["draft"], "gpt-5.5's open finding keeps the pull request a draft")
                if draft:
                    result = json.loads(out)
                    self.assertEqual(("claude-opus-5-5", "clean"),
                                     (result["newest"]["model"], result["newest"]["verdict"]), "the clean verdict is newest")
                    self.assertEqual((True, 1), (result["draft"], result["open_threads"]))
                else:
                    self.assertEqual(["1 finding thread is open on the head, so it stays a draft", "marked draft"],
                                     out.splitlines()[1:], "the clean verdict is newest, so no other verdict is named")

    def test_the_review_read_back_counts_when_the_listing_lags(self) -> None:
        self.add_verdict("gpt-5.5", verdict="changes-requested")
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.github.after_post = lambda: self.github.unlisted.add(self.github.reviews[-1]["id"])

        result = self.json_publish("clean")

        self.assertEqual((result["review_id"], False), (result["newest"]["review"], self.github.pr["draft"]))

    def test_a_verdict_from_an_account_the_repository_does_not_let_in_does_not_count(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.github.after_post = lambda: self.add_verdict(
            "gpt-5.5", verdict="changes-requested", login="passer-by", association="NONE",
            at=iso(self.clock + timedelta(seconds=1)))

        result = self.json_publish("clean")

        self.assertEqual(("claude-opus-5-5", False), (result["newest"]["model"], self.github.pr["draft"]))

    def test_in_a_private_or_internal_repository_every_verdict_counts(self) -> None:
        # Only an account that can see the repository can review there, and GitHub hides a private organization
        # membership from a token that cannot see it, reporting a lower association.
        for visibility, association, newest, draft in (
                ("private", "CONTRIBUTOR", "gpt-5.5", True), ("internal", "NONE", "gpt-5.5", True),
                ("public", "CONTRIBUTOR", "claude-opus-5-5", False), ("public", "NONE", "claude-opus-5-5", False)):
            with self.subTest(visibility=visibility, association=association):
                self.setUp()
                self.github.pr["base"]["repo"]["visibility"] = visibility
                self.assertEqual(0, self.setup_review("--loop", "l1")[0])
                self.github.after_post = lambda: self.add_verdict(
                    "gpt-5.5", verdict="changes-requested", login="teammate", association=association,
                    at=iso(self.clock + timedelta(seconds=1)))

                result = self.json_publish("clean")

                self.assertEqual((newest, draft), (result["newest"]["model"], self.github.pr["draft"]))

    def test_the_authors_hold_keeps_a_clean_newest_verdict_a_draft(self) -> None:
        # fix-pr holds a pull request while it builds a change no review has read.
        for draft in (True, False):
            with self.subTest(draft=draft):
                self.setUp()
                self.github.pr["draft"] = draft
                self.github.comment(HOLD)
                self.assertEqual(0, self.setup_review("--loop", "l1")[0])

                result = self.json_publish("clean")

                self.assertEqual(("clean", True, True), (result["newest"]["verdict"], result["held"], result["draft"]))
                self.assertTrue(self.github.pr["draft"])
                self.assertEqual(not draft, any(args[:2] == ["pr", "ready"] for args, _ in self.github.calls))

    def test_publish_says_the_hold_is_what_keeps_a_clean_head_a_draft(self) -> None:
        self.github.pr["draft"] = False
        self.github.comment(HOLD)
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        code, out, err = self.publish("clean")

        self.assertEqual(0, code, err)
        self.assertEqual(["the author's hold comment is open, so it stays a draft until the author deletes it",
                          "marked draft"], out.splitlines()[1:])

    def test_a_hold_from_anyone_but_the_author_holds_nothing(self) -> None:
        self.github.comment(HOLD, login="passerby")
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        result = self.json_publish("clean")

        self.assertEqual((False, False), (result["held"], result["draft"]))
        self.assertFalse(self.github.pr["draft"])

    def test_a_cross_author_review_does_not_approve_while_the_author_holds_the_pull_request(self) -> None:
        cases = {"held by its author": ("teammate", "COMMENT"), "held by the reviewer": ("operator", "APPROVE")}
        for name, (login, event) in cases.items():
            with self.subTest(name):
                self.setUp()
                self.github.pr["user"] = {"login": "teammate"}
                self.github.permissions = {"teammate": "write"}
                self.github.commit_authors = ["teammate"]
                (self.checkout / ".projector.toml").write_text("[review]\nallow_approve = true\n")
                self.github.comment(HOLD, login=login)
                self.assertEqual(0, self.setup_review("--loop", "l1")[0])

                result = self.json_publish("clean")

                self.assertEqual((event, None), (self.posted()["event"], result["draft"]))
                self.assertTrue(self.github.pr["draft"], "another author's draft state is theirs")

    def test_a_cross_author_review_approves_on_its_own_clean_verdict_and_leaves_draft_state_alone(self) -> None:
        self.github.pr["user"] = {"login": "teammate"}
        self.github.permissions = {"teammate": "write"}
        self.github.commit_authors = ["teammate"]
        (self.checkout / ".projector.toml").write_text("[review]\nallow_approve = true\n")
        self.add_verdict("gpt-5.5", verdict="changes-requested")
        self.github.comment(review.start_comment("codex-mini", self.head))
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        result = self.json_publish("clean")

        self.assertEqual(("APPROVE", None, None), (self.posted()["event"], result["draft"], result["newest"]))
        self.assertTrue(self.github.pr["draft"], "another author's draft state is theirs")
        self.assertFalse(any(args[:2] == ["pr", "ready"] for args, _ in self.github.calls))

    def test_publish_waits_for_no_other_model_and_reads_no_wait_setting(self) -> None:
        (self.checkout / ".projector.toml").write_text('[review]\npeer_wait = "anything"\n')
        self.add_verdict("gpt-5.5", sha=self.base)
        self.github.comment(review.start_comment("gpt-5.5", self.head))
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        self.json_publish("clean")

        self.assertFalse(self.github.pr["draft"], "neither another model's earlier head nor its start comment holds it")


class DismissalTests(LatestWinsCase):
    """claude-opus-5-5 requested changes on an earlier head of another author's pull request."""

    def setUp(self) -> None:
        super().setUp()
        self.github.pr["user"] = {"login": "teammate"}
        self.github.permissions = {"teammate": "write"}
        self.github.commit_authors = ["teammate"]
        self.request = self.change_request(self.model)

    def change_request(self, model: str, login: str = "operator", sha: str | None = None,
                       at: str = "2026-09-27T01:10:00Z") -> int:
        return self.add_verdict(model, sha=sha or self.base, verdict="changes-requested", login=login, at=at,
                                event="REQUEST_CHANGES")

    def review_state(self, review_id: int) -> str:
        return FakeGitHub.state(next(r for r in self.github.reviews if r["id"] == review_id))

    def dismissals(self) -> list[tuple[list[str], str | None]]:
        return [(args, token) for args, token in self.github.calls if args[:3] == ["api", "-X", "PUT"]]

    def test_a_clean_newest_verdict_dismisses_the_standing_change_request(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        code, out, err = self.publish("clean")

        self.assertEqual(0, code, err)
        self.assertEqual("COMMENT", self.posted()["event"])
        self.assertEqual("DISMISSED", self.review_state(self.request))
        dismissed = next(r for r in self.github.reviews if r["id"] == self.request)
        self.assertEqual(f"{review.MARK} The newest verdict on {self.head[:7]} is clean: "
                         f"{review_page(self.posted()['id'])}", dismissed["dismissal"])
        self.assertEqual(["token-operator"], [token for _, token in self.dismissals()])
        self.assertIn(f"dismissed change request {self.request}", out)
        self.assertTrue(self.github.pr["draft"], "another author's draft state is theirs")
        steps = [" ".join(args) for args, _ in self.github.calls]
        dismiss = next(i for i, c in enumerate(steps) if c.endswith("event=DISMISS"))
        delete = next(i for i, c in enumerate(steps) if c.startswith("api -X DELETE"))
        self.assertLess(dismiss, delete, "the start comment goes only after the dismissal")

    def test_dismisses_only_the_reviewers_projector_change_requests_since_its_last_approval(self) -> None:
        self.github.reviews.append({"id": 850, "user": {"login": "operator"}, "event": "APPROVE", "body": "",
                                    "comments": [], "commit_id": self.base, "at": "2026-09-27T01:11:00Z"})
        standing = self.change_request(self.model)
        by_hand, naming_the_loop = 851, 852
        for review_id, body in ((by_hand, "Rename this first."),
                                (naming_the_loop, "The projector-review loop missed the empty case; handle it.")):
            self.github.reviews.append({"id": review_id, "user": {"login": "operator"}, "event": "REQUEST_CHANGES",
                                        "body": body, "comments": [], "commit_id": self.base,
                                        "at": "2026-09-27T01:12:00Z"})
        colleague = self.change_request("gpt-5.5", login="colleague")
        already = self.change_request(self.model)
        next(r for r in self.github.reviews if r["id"] == already)["state"] = "DISMISSED"
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        result = self.json_publish("clean")

        self.assertEqual([standing], result["dismissed"])
        self.assertEqual({self.request: "CHANGES_REQUESTED", standing: "DISMISSED", by_hand: "CHANGES_REQUESTED",
                          naming_the_loop: "CHANGES_REQUESTED", colleague: "CHANGES_REQUESTED"},
                         {i: self.review_state(i)
                          for i in (self.request, standing, by_hand, naming_the_loop, colleague)})

    def test_dismisses_a_change_request_whose_marker_names_no_version(self) -> None:
        older = next(r for r in self.github.reviews if r["id"] == self.request)
        older["body"] = older["body"].replace(" projector=1.2.3 ", " ")
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        result = self.json_publish("clean")

        self.assertEqual([self.request], result["dismissed"])
        self.assertEqual("DISMISSED", self.review_state(self.request))

    def test_dismisses_an_older_models_change_request_on_the_head_without_waiting_for_its_review(self) -> None:
        peer = self.change_request("gpt-5.5", sha=self.head)
        self.github.comment(review.start_comment("gpt-5.5", self.head))
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        result = self.json_publish("clean")

        self.assertEqual(("claude-opus-5-5", "clean"), (result["newest"]["model"], result["newest"]["verdict"]))
        self.assertEqual([self.request, peer], result["dismissed"])
        self.assertEqual("DISMISSED", self.review_state(peer), "gpt-5.5's change request is older, so it no longer counts")

    def test_a_change_request_posted_after_this_clean_verdict_dismisses_nothing(self) -> None:
        # gpt-5.5 submits while claude-opus-5-5 is between its own submit and the dismissal.
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        newer = []
        self.github.after_post = lambda: newer.append(self.change_request(
            "gpt-5.5", sha=self.head, at=iso(self.clock + timedelta(seconds=1))))

        result = self.json_publish("clean")

        self.assertEqual((newer[0], "changes-requested", []),
                         (result["newest"]["review"], result["newest"]["verdict"], result["dismissed"]))
        self.assertEqual(["CHANGES_REQUESTED"] * 2, [self.review_state(i) for i in (self.request, newer[0])])
        self.assertEqual([], self.dismissals())

    def test_a_finding_another_model_posts_mid_publish_holds_the_change_requests(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        def gpt_posts_a_finding() -> None:
            # After claude-opus-5-5 counted no open finding, and before its clean verdict lands.
            self.github.pages = [page([thread("T1", FINDING.replace(" -->", " model=gpt-5.5 -->") % self.head)],
                                      None)]

        self.github.before_post = gpt_posts_a_finding
        code, out, err = self.publish("clean")

        self.assertEqual(0, code, err)
        self.assertEqual(["1 finding thread is open on the head, so the change requests stand"], out.splitlines()[1:])
        self.assertEqual("CHANGES_REQUESTED", self.review_state(self.request))
        self.assertEqual([], self.dismissals())

    def test_the_authors_hold_keeps_the_change_request_standing(self) -> None:
        # The author is building changes no review has read, so nothing clears the pull request to merge yet.
        self.github.comment(HOLD, login="teammate")
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        code, out, err = self.publish("clean")

        self.assertEqual(0, code, err)
        self.assertEqual(["the author's hold comment is open, so the change requests stand until the author deletes it"],
                         out.splitlines()[1:])
        self.assertEqual("CHANGES_REQUESTED", self.review_state(self.request))
        self.assertEqual([], self.dismissals())

    def test_an_approval_replaces_the_change_request_so_nothing_is_dismissed(self) -> None:
        (self.checkout / ".projector.toml").write_text("[review]\nallow_approve = true\n")
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        result = self.json_publish("clean")

        self.assertEqual(("APPROVE", []), (result["event"], result["dismissed"]))
        self.assertEqual("CHANGES_REQUESTED", self.review_state(self.request))
        self.assertEqual([], self.dismissals())

    def test_a_failed_dismissal_keeps_the_lock_and_a_retry_finishes_it(self) -> None:
        cases = {"refused": lambda: setattr(self.github, "fail", "dismissals"),
                 "still standing": lambda: setattr(self.github, "ignore_dismissals", True)}
        for name, arrange in cases.items():
            with self.subTest(name):
                self.setUp()
                self.assertEqual(0, self.setup_review("--loop", "l1")[0])
                arrange()

                code, _, err = self.publish("clean")

                self.assertEqual(1, code)
                self.assertIn(f"dismiss it at {review_page(self.request)} and run publish again", err)
                self.assertTrue(self.lock(self.head).exists())
                self.assertEqual(1, len(self.github.comments), "the start comment stays until the retry")

                self.github.fail, self.github.ignore_dismissals = None, False
                code, out, err = self.publish("clean")

                self.assertEqual(0, code, err)
                self.assertEqual(2, len(self.github.reviews), "the retry posts no second review")
                self.assertEqual("DISMISSED", self.review_state(self.request))
                self.assertFalse(self.lock(self.head).exists())


class StatusTests(LatestWinsCase):
    def status(self, *extra: str) -> tuple[int, str, str]:
        return self.project("status", "1", "--repo", REPO, *extra)

    def test_reports_the_newest_verdict_each_models_and_the_open_threads_whatever_they_are(self) -> None:
        older = self.add_verdict("gpt-5.5", at="2026-09-27T14:02:00Z", verdict="changes-requested")
        newest = self.add_verdict("claude-opus-5-5", at="2026-09-27T14:20:00Z")
        self.add_verdict("codex-mini", sha=self.base, at="2026-09-27T14:25:00Z", verdict="changes-requested")
        self.github.comment(review.start_comment("codex-mini", self.head))
        self.github.pages = [page([thread("T1", FINDING % self.head, path="src/x.py", line=4),
                                   thread("T2", FINDING % self.head, resolved=True)], None)]
        self.github.pages[0]["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"][0]["comments"]["nodes"][0][
            "body"] = f"<!-- projector-finding v=1 priority=P2 sha={self.head} model=gpt-5.5 -->\n**P2 · A nil deref**"

        code, out, err = self.status()
        as_json = json.loads(self.status("--json")[1])

        self.assertEqual(0, code, err)
        self.assertEqual([f"#1 at {self.head[:7]}: clean from claude-opus-5-5, {review_page(newest)}",
                          f"  claude-opus-5-5   clean               review {newest}, 14:20 UTC",
                          f"  gpt-5.5           changes-requested   review {older}, 14:02 UTC",
                          "  1 open finding thread: src/x.py:4 (gpt-5.5)"], out.splitlines())
        self.assertEqual((REPO, 1, self.head, True, "clean"),
                         (as_json["repo"], as_json["number"], as_json["head"], as_json["draft"], as_json["verdict"]))
        self.assertEqual({"model": "claude-opus-5-5", "verdict": "clean", "review": newest, "url": review_page(newest),
                          "at": "2026-09-27T14:20:00Z", "login": "operator", "suggestions": 0}, as_json["newest"])
        self.assertEqual([("claude-opus-5-5", "clean", newest), ("gpt-5.5", "changes-requested", older)],
                         [(m["model"], m["verdict"], m["review"]) for m in as_json["models"]],
                         "a verdict on an earlier head and a start comment are not on the list")
        self.assertEqual([{"path": "src/x.py", "line": 4, "model": "gpt-5.5"}], as_json["open_threads"])

    def test_a_newer_change_request_is_the_newest_verdict(self) -> None:
        self.add_verdict("claude-opus-5-5", at="2026-09-27T14:02:00Z")
        newest = self.add_verdict("gpt-5.5", at="2026-09-27T14:20:00Z", verdict="changes-requested")

        result = json.loads(self.status("--json")[1])

        self.assertEqual(("changes-requested", "gpt-5.5", newest),
                         (result["verdict"], result["newest"]["model"], result["newest"]["review"]))

    def test_each_account_keeps_its_own_newest_verdict_by_a_model(self) -> None:
        # A teammate whose own loop runs the same model reviews the head after the operator's loop did. The
        # review loop looks for its own account's verdict, so the teammate's must not replace it.
        mine = self.add_verdict("claude-opus-5-5", at="2026-09-27T14:02:00Z", verdict="changes-requested")
        theirs = self.add_verdict("claude-opus-5-5", login="teammate", association="COLLABORATOR",
                                  at="2026-09-27T14:20:00Z")

        code, out, err = self.status()
        result = json.loads(self.status("--json")[1])

        self.assertEqual(0, code, err)
        self.assertEqual(("clean", theirs), (result["verdict"], result["newest"]["review"]))
        self.assertEqual([("teammate", theirs), ("operator", mine)],
                         [(m["login"], m["review"]) for m in result["models"]])
        self.assertEqual([f"#1 at {self.head[:7]}: clean from claude-opus-5-5, {review_page(theirs)}",
                          f"  claude-opus-5-5 (teammate)   clean               review {theirs}, 14:20 UTC",
                          f"  claude-opus-5-5 (operator)   changes-requested   review {mine}, 14:02 UTC"],
                         out.splitlines())

    def test_a_verdict_whose_marker_names_no_version_or_spells_clean_approved_counts(self) -> None:
        older = self.add_verdict("gpt-5.5", at="2026-09-27T14:02:00Z", verdict="changes-requested")
        newest = self.add_verdict("claude-opus-5-5", at="2026-09-27T14:20:00Z")
        for found in self.github.reviews:
            body = found["body"].replace("verdict=clean projector=1.2.3 ", "verdict=approved ")
            found["body"] = body.replace(" projector=1.2.3 ", " ")

        result = json.loads(self.status("--json")[1])

        self.assertEqual([("claude-opus-5-5", "clean", newest), ("gpt-5.5", "changes-requested", older)],
                         [(m["model"], m["verdict"], m["review"]) for m in result["models"]])
        self.assertEqual("clean", result["verdict"])

    def test_one_model_prints_one_line(self) -> None:
        newest = self.add_verdict("claude-opus-5-5")

        self.assertEqual([f"#1 at {self.head[:7]}: clean from claude-opus-5-5, {review_page(newest)}"],
                         self.status()[1].splitlines())

    def test_shows_how_many_suggestions_the_newest_review_lists_from_its_marker_or_else_its_body(self) -> None:
        older = self.add_verdict("gpt-5.5", at="2026-09-27T14:02:00Z")
        newest = self.add_verdict("claude-opus-5-5", at="2026-09-27T14:20:00Z")
        counted, written = self.github.reviews
        counted["body"] = counted["body"].replace(" -->", " suggestions=1 -->") + "\nNo section in this body.\n"
        written["body"] += "\n" + LABELED["bold line"]

        code, out, err = self.status()
        result = json.loads(self.status("--json")[1])

        self.assertEqual(0, code, err)
        self.assertEqual([f"#1 at {self.head[:7]}: clean with 3 suggestions from claude-opus-5-5, "
                          f"{review_page(newest)}",
                          f"  claude-opus-5-5   clean               review {newest}, 14:20 UTC, 3 suggestions",
                          f"  gpt-5.5           clean               review {older}, 14:02 UTC, 1 suggestion"],
                         out.splitlines())
        self.assertEqual([(newest, 3), (older, 1)], [(m["review"], m["suggestions"]) for m in result["models"]],
                         "a marker that gives no count is read by its body, and one that does by its marker")
        self.assertEqual((3, 4), (result["newest"]["suggestions"], result["suggestions"]),
                         "the head's count is every model's, as `review suggestions` lists them")

    def test_every_account_the_repository_lets_in_counts_so_fix_pr_runs_it_once(self) -> None:
        skill = (Path(cli.__file__).resolve().parents[2] / "skills" / "fix-pr" / "SKILL.md").read_text()
        self.assertIn("`project review status <number>` reports `clean`", skill)
        self.assertNotIn("--reviewer", skill)
        teammate = self.add_verdict("claude-opus-5-5", login="teammate", association="COLLABORATOR")

        result = json.loads(self.status("--json")[1])

        self.assertEqual(("clean", teammate), (result["verdict"], result["newest"]["review"]),
                         "the operator's status sees the teammate's loop")

    def test_an_open_hold_is_held_beside_the_newest_verdict_until_the_author_deletes_it(self) -> None:
        newest = self.add_verdict("claude-opus-5-5")
        hold = self.github.comment(HOLD)
        self.github.comment(HOLD, login="passerby")

        code, out, err = self.status()
        held = json.loads(self.status("--json")[1])
        del self.github.comments[hold]
        lifted = json.loads(self.status("--json")[1])

        self.assertEqual(0, code, err)
        self.assertEqual([f"#1 at {self.head[:7]}: held; clean from claude-opus-5-5, {review_page(newest)}",
                          f"  held in draft by comment {hold} until the changes it lists are pushed"], out.splitlines())
        self.assertEqual(("clean", True, [{"id": hold, "url": f"https://github.com/{REPO}/pull/1#issuecomment-{hold}"}]),
                         (held["verdict"], held["held"], held["holds"]))
        self.assertEqual(("clean", False, []), (lifted["verdict"], lifted["held"], lifted["holds"]))

    def test_the_hold_the_command_writes_is_the_one_publish_status_and_the_review_watcher_read(self) -> None:
        skills = Path(cli.__file__).resolve().parents[2] / "skills"
        fix_pr = [line.strip() for line in (skills / "fix-pr" / "SKILL.md").read_text().splitlines()]
        watcher = (skills / "start-review-loop" / "scripts" / "watch-prs.sh").read_text()

        self.assertEqual(HOLD, review.hold_body(["Skip fenced code blocks when looking for headings"]))
        self.assertEqual(review.HOLD_MARKER, HOLD.splitlines()[-1])
        self.assertTrue(HOLD.splitlines()[0] in fix_pr, "fix-pr shows the opening line the command writes")
        self.assertTrue(review.HOLD_MARKER in fix_pr, "fix-pr shows the marker the command writes")
        self.assertIn(f'contains("{review.HOLD_MARKER}")', watcher)

    def test_fix_pr_holds_and_ends_the_hold_through_the_command_alone(self) -> None:
        # The command posts the hold before it drafts the pull request, so fix-pr must not take either step by
        # hand. A raw comment deletion is also what Claude Code's auto mode refuses, where one narrow rule
        # allows the command.
        fix_pr = (Path(cli.__file__).resolve().parents[2] / "skills" / "fix-pr" / "SKILL.md").read_text()

        for mode, shown in (("--add", r'\\?\s*--add "'), ("--drop TEXT", r'--drop "'), ("--drop", r"--drop$")):
            self.assertTrue(re.search(rf"project review hold <number> --repo <owner>/<repo> {shown}", fix_pr, re.M),
                            f"fix-pr shows hold {mode}, naming the repository")
        self.assertFalse("--lift" in fix_pr, "a bare --drop ends the hold")
        self.assertFalse("issues/comments" in fix_pr, "fix-pr posts, edits, finds, and deletes no hold by hand")
        self.assertFalse("gh pr ready" in fix_pr, "fix-pr drafts no pull request apart from the hold")

    def test_an_account_the_repository_does_not_let_in_counts_only_as_the_reviewer(self) -> None:
        self.add_verdict("claude-opus-5-5", login="review-bot", association="NONE", verdict="changes-requested")

        mine = json.loads(self.status("--json")[1])
        bot = json.loads(self.status("--json", "--reviewer", "review-bot")[1])

        self.assertEqual(("unreviewed", None, []), (mine["verdict"], mine["newest"], mine["models"]))
        self.assertEqual(["#1 at " + self.head[:7] + ": unreviewed"], self.status()[1].splitlines())
        self.assertEqual("changes-requested", bot["verdict"])

    def test_in_a_private_or_internal_repository_every_account_counts(self) -> None:
        for visibility in ("private", "internal", "public"):
            with self.subTest(visibility=visibility):
                self.setUp()
                self.github.pr["base"]["repo"]["visibility"] = visibility
                older = self.add_verdict("claude-opus-5-5", login="teammate", association="CONTRIBUTOR",
                                         at="2026-09-27T14:02:00Z")
                newest = self.add_verdict("gpt-5.5", login="review-bot", association="NONE",
                                          verdict="changes-requested", at="2026-09-27T14:20:00Z")

                result = json.loads(self.status("--json")[1])

                if visibility == "public":
                    self.assertEqual(("unreviewed", []), (result["verdict"], result["models"]))
                else:
                    self.assertEqual(("changes-requested", [newest, older]),
                                     (result["verdict"], [m["review"] for m in result["models"]]))

    def test_a_pull_request_that_cannot_be_read_exits_one(self) -> None:
        self.github.missing = True

        code, _, err = self.status()

        self.assertEqual(1, code)
        self.assertIn("does not exist", err)


class SuggestionsCommandTests(LatestWinsCase):
    def suggestions(self, *extra: str) -> tuple[int, str, str]:
        return self.project("suggestions", "1", "--repo", REPO, *extra, cwd=Path(tempfile.mkdtemp()))

    def add_review(self, model: str, listing: str, at: str, sha: str | None = None, login: str = "operator") -> int:
        """A full review by `model` on `sha` whose body, below its marker, is `listing`."""
        review_id = self.add_verdict(model, at=at, sha=sha, login=login)
        self.github.reviews[-1]["body"] += f"\n{listing}"
        return review_id

    def test_prints_nothing_and_exits_0_when_no_review_lists_a_suggestion(self) -> None:
        self.assertEqual((0, ""), self.suggestions()[:2], "an unreviewed head")
        self.add_review("claude-opus-5-5", f"{INTENT}\n### What was checked\n\n{CHECKED}", "2026-09-27T14:02:00Z")

        self.assertEqual((0, ""), self.suggestions()[:2])
        self.assertEqual((0, []), (self.suggestions("--json")[0], json.loads(self.suggestions("--json")[1])))

    def test_lists_each_suggestion_of_the_newest_review_by_each_model_on_the_head(self) -> None:
        self.add_review("claude-opus-5-5", LABELED["level 2 heading"], "2026-09-27T14:00:00Z")
        newest = self.add_review("claude-opus-5-5", LABELED["plain line"], "2026-09-27T14:20:00Z")
        other = self.add_review("gpt-5.5", "### Suggestions\n\n- `src/z.py:11`: Return early.\n",
                                "2026-09-27T14:10:00Z")
        self.add_review("gpt-5.5", "### Suggestions\n\n- `src/z.py:3`: A suggestion on an earlier head.\n",
                        "2026-09-27T14:30:00Z", sha=self.base)

        code, out, err = self.suggestions()
        listed = json.loads(self.suggestions("--json")[1])

        self.assertEqual(0, code, err)
        self.assertEqual([dict(item, url=review_page(newest), sha=self.head) for item in WRITTEN]
                         + [{"url": review_page(other), "sha": self.head, "path": "src/z.py", "line": 11,
                             "text": "Return early."}], listed,
                         "the older review by the same model and a review of an earlier head give none")
        self.assertEqual(
            f"src/app/config.py:42, in the review of {self.head[:7]}: {review_page(newest)}\n"
            "name the cache `_parsed`, as the module's other caches are named.\n\n"
            f"docs/cli.md, in the review of {self.head[:7]}: {review_page(newest)}\n"
            "the configuration section: say that an edit to `.projector.toml` takes effect on the next command, "
            "not the next session.\n\n"
            f"(no file named), in the review of {self.head[:7]}: {review_page(newest)}\n"
            "Split `load` in two, so reading and checking the file can be tested apart.\n\n"
            f"src/z.py:11, in the review of {self.head[:7]}: {review_page(other)}\n"
            "Return early.\n", out)

    def test_reads_every_label_a_reviewer_has_written(self) -> None:
        for label, text in LABELED.items():
            with self.subTest(label):
                self.github.reviews = []
                found = self.add_review("claude-opus-5-5", text, "2026-09-27T14:20:00Z")

                listed = json.loads(self.suggestions("--json")[1])

                self.assertEqual([dict(item, url=review_page(found), sha=self.head) for item in WRITTEN], listed)

    def test_reads_the_review_publish_wrote(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.assertEqual(0, self.publish("clean", "--threads", str(self.threads(*P3)))[0])

        listed = json.loads(self.suggestions("--json")[1])

        self.assertEqual([dict(item, url=review_page(901), sha=self.head) for item in LISTED], listed)

    def test_a_pull_request_that_cannot_be_read_exits_one(self) -> None:
        self.github.missing = True

        code, out, err = self.suggestions()

        self.assertEqual((1, ""), (code, out))
        self.assertIn("does not exist", err)

    def test_a_github_failure_exits_one(self) -> None:
        self.github.fail = "pulls/1/reviews"

        code, out, err = self.suggestions()

        self.assertEqual((1, ""), (code, out))
        self.assertIn("HTTP 502", err)


class HoldTests(ReviewCase):
    """`project review hold` on pull request 1, which the operator, signed in to gh, opened."""

    CHANGE = "Skip fenced code blocks when looking for headings"
    OTHER = "Name the held layer in the stack's readiness report"

    def hold(self, *args: str, cwd: Path | None = None) -> tuple[int, str, str]:
        return self.project("hold", "1", *args, cwd=cwd)

    def checkout_of(self, repo: str | None) -> Path:
        """A Git checkout whose origin is `repo` on GitHub, or that has no origin."""
        checkout = Path(tempfile.mkdtemp())
        git(checkout, "init", "--quiet")
        if repo:
            git(checkout, "remote", "add", "origin", f"https://github.com/{repo}.git")
        return checkout

    def repos_called(self) -> set[str]:
        """Every repository a call to GitHub named."""
        return {match.group(1) for args, _ in self.github.calls for arg in args
                if (match := re.match(r"repos/([^/]+/[^/]+)/", arg))} | \
            {args[args.index("--repo") + 1] for args, _ in self.github.calls if "--repo" in args}

    def held(self) -> dict[int, str]:
        """Each comment that carries the hold marker, by id."""
        return {i: body for i, body in self.github.comments.items() if review.HOLD_MARKER in body}

    def call_index(self, match) -> int:
        return next(i for i, (args, _) in enumerate(self.github.calls) if match(args))

    def drafts(self) -> list[list[str]]:
        return [args for args, _ in self.github.calls if args[:2] == ["pr", "ready"]]

    def test_posts_the_hold_then_drafts_the_pull_request(self) -> None:
        self.github.pr["draft"] = False

        code, out, err = self.hold("--add", self.CHANGE, "--add", f"  {self.OTHER}  ", "--add", self.CHANGE)

        self.assertEqual(0, code, err)
        [(comment, body)] = self.held().items()
        self.assertEqual("Held in draft while the following changes are implemented:\n\n"
                         f"- {self.CHANGE}\n- {self.OTHER}\n\n<!-- projector-hold v=1 -->\n", body)
        self.assertEqual("operator", self.github.comment_info[comment]["login"], "posted as the signed-in author")
        self.assertTrue(self.github.pr["draft"])
        self.assertEqual([["pr", "ready", "1", "--repo", REPO, "--undo"]], self.drafts())
        posted = self.call_index(lambda args: args[:2] == ["api", f"repos/{REPO}/issues/1/comments"])
        read_back = self.call_index(lambda args: args == ["api", f"repos/{REPO}/issues/comments/{comment}"])
        drafted = self.call_index(lambda args: args[:2] == ["pr", "ready"])
        self.assertLess(posted, read_back)
        self.assertLess(read_back, drafted, "the pull request turns draft only once the hold reads back")
        self.assertEqual([f"posted hold comment {comment} on {REPO}#1: {FakeGitHub.comment_url(comment)}",
                          f"held in draft while 2 changes are implemented: {self.CHANGE}; {self.OTHER}"],
                         out.splitlines())

    def test_holds_a_draft_without_touching_its_draft_state(self) -> None:
        code, _, err = self.hold("--add", self.CHANGE)

        self.assertEqual(0, code, err)
        self.assertEqual(1, len(self.held()), "a draft waiting on its review is held too")
        self.assertEqual([], self.drafts())
        self.assertTrue(self.github.pr["draft"])

    def test_a_hold_that_does_not_read_back_drafts_nothing(self) -> None:
        # As a body posted with `-f body=@<file>` would: the marker is there, but not on the last line.
        self.github.pr["draft"] = False
        mangled = f"{review.HOLD_MARKER}\n\n- {self.CHANGE}\n"

        with mock.patch.object(review, "hold_body", lambda changes: mangled):
            code, _, err = self.hold("--add", self.CHANGE)

        self.assertEqual(1, code)
        self.assertIn("does not read back as written", err)
        self.assertEqual([], self.drafts())
        self.assertFalse(self.github.pr["draft"])

    def test_extends_the_open_hold_skipping_listed_changes(self) -> None:
        comment = self.github.comment(HOLD)

        code, out, err = self.hold("--add", f" {self.CHANGE} ", "--add", self.OTHER)

        self.assertEqual(0, code, err)
        self.assertEqual({comment: "Held in draft while the following changes are implemented:\n\n"
                                   f"- {self.CHANGE}\n- {self.OTHER}\n\n<!-- projector-hold v=1 -->\n"},
                         self.held(), "one comment, each change once, the marker last")
        self.assertEqual([], [args for args, _ in self.github.calls
                              if args[:2] == ["api", f"repos/{REPO}/issues/1/comments"]],
                         "the hold is edited, never posted twice")
        self.assertEqual(f"added hold comment {comment} on {REPO}#1: {FakeGitHub.comment_url(comment)}",
                         out.splitlines()[0])

    def test_refuses_anyone_but_the_author(self) -> None:
        self.github.pr["user"] = {"login": "teammate"}
        theirs = self.github.comment(HOLD, login="teammate")
        for args in (["--add", self.CHANGE], ["--drop", self.CHANGE], ["--drop"]):
            with self.subTest(" ".join(args)):
                code, _, err = self.hold(*args)

                self.assertEqual(1, code)
                self.assertIn("gh is signed in as operator, and acme/app#1 is teammate's", err)
                self.assertIn("only the pull request's author can hold it", err)
        self.assertEqual({theirs: HOLD}, self.held(), "the author's hold is left as it is")
        self.assertEqual([], [args for args, _ in self.github.calls if args[:2] == ["api", "-X"]])
        self.assertEqual([], self.drafts())

    def test_drops_one_change(self) -> None:
        comment = self.github.comment(review.hold_body([self.CHANGE, self.OTHER]))

        code, out, err = self.hold("--drop", self.OTHER, "--json")

        self.assertEqual(0, code, err)
        self.assertEqual({comment: review.hold_body([self.CHANGE])}, self.held())
        self.assertEqual(("dropped", [self.CHANGE]), (json.loads(out)["action"], json.loads(out)["changes"]))
        self.assertEqual([], self.drafts())

    def test_dropping_the_last_change_ends_the_hold(self) -> None:
        comment = self.github.comment(HOLD)

        code, out, err = self.hold("--drop", self.CHANGE)

        self.assertEqual(0, code, err)
        self.assertEqual({}, self.held())
        self.assertEqual([f"ended the hold on {REPO}#1, deleting hold comment {comment}, which listed {self.CHANGE}",
                          f"the review loop reviews {self.head[:7]} next, and a clean verdict marks it ready; it is a "
                          "draft"], out.splitlines())
        self.assertEqual([], self.drafts())

    def test_drop_works_across_every_hold_comment_and_ends_the_hold_only_when_none_is_left(self) -> None:
        # An older hold, as one posted by hand or by a second --add racing the first, holds the pull request too.
        older = self.github.comment(HOLD)
        newest = self.github.comment(review.hold_body([self.OTHER]))

        code, out, err = self.hold("--drop", self.OTHER)

        self.assertEqual(0, code, err)
        self.assertEqual({older: HOLD}, self.held(), "the older hold still holds the pull request")
        self.assertEqual([f"still held by comment {older} on {REPO}#1: {FakeGitHub.comment_url(older)}",
                          f"1 change still listed: {self.CHANGE}; it is a draft"], out.splitlines())

        self.github.comments[newest] = review.hold_body([self.OTHER])
        code, out, err = self.hold("--drop", self.CHANGE, "--json")

        self.assertEqual(0, code, err)
        self.assertEqual({newest: review.hold_body([self.OTHER])}, self.held(),
                         "a change only the older comment lists is dropped from it")
        self.assertEqual(("dropped", [self.OTHER]), (json.loads(out)["action"], json.loads(out)["changes"]))

        self.github.comments[older] = review.hold_body([self.CHANGE, self.OTHER])
        code, out, err = self.hold("--drop", self.OTHER, "--drop", self.CHANGE, "--json")

        self.assertEqual(0, code, err)
        self.assertEqual({}, self.held())
        self.assertEqual(("ended", [self.OTHER, self.CHANGE]), (json.loads(out)["action"], json.loads(out)["changes"]))

    def test_refuses_a_drop_that_matches_no_change_and_changes_nothing(self) -> None:
        self.assertEqual(0, self.hold("--add", self.CHANGE)[0])
        before = self.held()

        code, _, err = self.hold("--drop", self.CHANGE, "--drop", "Rename the flag")

        self.assertEqual(1, code)
        self.assertIn(f'lists no change "Rename the flag"; it lists "{self.CHANGE}"', err)
        self.assertEqual(before, self.held())

        self.github.comments.clear()
        code, _, err = self.hold("--drop", self.CHANGE)
        self.assertEqual(1, code)
        self.assertIn("has no hold open, so there is no change to drop", err)

    def test_a_bare_drop_ends_the_hold_on_any_head_and_leaves_draft_state_alone(self) -> None:
        # The caller ends the hold once the push that carries its changes is done; the command trusts it, so an
        # unchanged head ends the hold too.
        for draft in (True, False):
            with self.subTest(draft=draft):
                self.github.comments.clear()
                self.assertEqual(0, self.hold("--add", self.CHANGE, "--add", self.OTHER)[0])
                [comment] = self.held()
                self.github.pr["draft"] = draft
                drafts = len(self.drafts())

                code, out, err = self.hold("--drop")

                self.assertEqual(0, code, err)
                self.assertEqual({}, self.held())
                self.assertEqual((draft, drafts), (self.github.pr["draft"], len(self.drafts())),
                                 "ending the hold never marks the pull request ready")
                self.assertEqual([f"ended the hold on {REPO}#1, deleting hold comment {comment}, which listed "
                                  f"{self.CHANGE}; {self.OTHER}",
                                  f"the review loop reviews {self.head[:7]} next, and a clean verdict marks it ready; "
                                  f"it is {'a draft' if draft else 'not a draft'}"], out.splitlines())

    def test_a_bare_drop_deletes_every_hold_comment_by_the_author(self) -> None:
        self.github.comment(HOLD)
        self.github.comment(review.hold_body([self.OTHER, self.CHANGE]))
        planted = self.github.comment(HOLD, login="passerby")

        code, out, err = self.hold("--drop", "--json")

        self.assertEqual(0, code, err)
        self.assertEqual({planted: HOLD}, self.held(), "a comment by anyone but the author is not the author's hold")
        self.assertEqual(("ended", [self.CHANGE, self.OTHER]), (json.loads(out)["action"], json.loads(out)["changes"]))

    def test_a_bare_drop_with_no_hold_open_exits_0(self) -> None:
        planted = self.github.comment(HOLD, login="passerby")

        code, out, err = self.hold("--drop")

        self.assertEqual(0, code, err)
        self.assertEqual([f"no hold is open on {REPO}#1; it is a draft"], out.splitlines())
        self.assertIn(planted, self.github.comments, "a comment by anyone but the author is not the author's hold")

    def test_refuses_a_bare_drop_beside_drop_text_before_asking_github(self) -> None:
        comment = self.github.comment(review.hold_body([self.CHANGE, self.OTHER]))
        for args in (["--drop", "--drop", self.CHANGE], ["--drop", self.CHANGE, "--drop"]):
            with self.subTest(args=args):
                code, _, err = self.hold(*args)

                self.assertEqual(1, code)
                self.assertIn("a bare --drop ends the whole hold, so it cannot be combined with --drop TEXT", err)
        self.assertEqual({comment: review.hold_body([self.CHANGE, self.OTHER])}, self.held())
        self.assertEqual([], self.github.calls)

    def test_json_reports_each_action(self) -> None:
        def run(*args: str) -> dict:
            code, out, err = self.hold(*args, "--json")
            self.assertEqual(0, code, err)
            return json.loads(out)

        posted = run("--add", self.CHANGE)
        [comment] = self.held()
        added = run("--add", self.OTHER)
        dropped = run("--drop", self.CHANGE)
        ended = run("--drop")

        pull = {"repo": REPO, "number": 1, "head": self.head}
        url = {"id": comment, "url": FakeGitHub.comment_url(comment)}
        self.assertEqual(dict(pull, action="posted", comment=url, changes=[self.CHANGE], draft=True), posted)
        self.assertEqual(dict(pull, action="added", comment=url, changes=[self.CHANGE, self.OTHER], draft=True), added)
        self.assertEqual(dict(pull, action="dropped", comment=url, changes=[self.OTHER], draft=True), dropped)
        self.assertEqual(dict(pull, action="ended", comment=url, changes=[self.OTHER], draft=True), ended)
        self.assertEqual(dict(pull, action="none", comment=None, changes=[], draft=True), run("--drop"))

    def test_repo_wins_over_the_checkout_it_runs_in(self) -> None:
        elsewhere = self.checkout_of("other/app")
        self.assertEqual("other/app", review.named_repo(None, elsewhere, "to hold a pull request in"),
                         "the checkout alone names another repository")

        code, _, err = self.hold("--repo", REPO, "--add", self.CHANGE, cwd=elsewhere)

        self.assertEqual(0, code, err)
        self.assertEqual(1, len(self.held()))
        self.assertEqual({REPO}, self.repos_called())

    def test_without_repo_it_holds_in_the_checkouts_origin(self) -> None:
        code, out, err = self.hold("--add", self.CHANGE, cwd=self.checkout)

        self.assertEqual(0, code, err)
        self.assertEqual(1, len(self.held()))
        self.assertEqual({REPO}, self.repos_called())
        self.assertTrue(out.startswith("posted hold comment 101 on acme/app#1: "), out)

    def test_a_review_set_up_for_the_same_number_in_another_repository_is_ignored(self) -> None:
        xdg = Path(tempfile.mkdtemp())
        state = xdg / "projector" / "reviews" / "prs" / "other" / "app" / "1" / review.model_key(self.model)
        state.mkdir(parents=True)
        (state / "state.json").write_text(json.dumps({"model": self.model}))
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(xdg)}):
            self.assertEqual("other/app", review.find_repo(review.state_dir(), 1, None, self.checkout),
                             "the commands that follow a review would take other/app from its state")

            code, _, err = self.hold("--add", self.CHANGE, cwd=self.checkout)

        self.assertEqual(0, code, err)
        self.assertEqual({REPO}, self.repos_called())

    def test_refuses_without_repo_outside_a_checkout_with_a_github_origin(self) -> None:
        cases = {"outside a checkout": Path(tempfile.mkdtemp()), "a checkout with no origin": self.checkout_of(None)}
        for name, cwd in cases.items():
            with self.subTest(name):
                code, _, err = self.hold("--drop", cwd=cwd)

                self.assertEqual(1, code)
                self.assertIn("is not in a checkout whose origin is on GitHub", err)
                self.assertIn("pass --repo owner/name", err)
        self.assertEqual([], self.github.calls)

    def test_refuses_a_change_that_is_not_one_line_before_asking_github(self) -> None:
        for text in ("", "   ", "Rename\nthe flag"):
            with self.subTest(text=text):
                code, _, err = self.hold("--add", self.CHANGE, "--add", text)

                self.assertEqual(1, code)
                self.assertIn("give each change as one line of text", err)
        self.assertEqual([], self.github.calls)

    def test_refuses_to_hold_a_pull_request_that_is_not_open(self) -> None:
        self.github.pr["state"] = "closed"

        code, _, err = self.hold("--add", self.CHANGE)

        self.assertEqual(1, code)
        self.assertIn(f"{REPO}#1 is closed; only an open pull request is held", err)
        self.assertEqual({}, self.held())

    def test_takes_add_or_drop_but_not_both_and_no_lift(self) -> None:
        for args in ([], ["--add", self.CHANGE, "--drop"], ["--add", self.CHANGE, "--drop", self.OTHER], ["--lift"]):
            with self.subTest(args=args), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                cli.main(["review", "hold", "1", *args])
            self.assertEqual(2, raised.exception.code)
        self.assertEqual([], self.github.calls)


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

    def interdiff(self, *extra: str) -> dict:
        code, out, err = self.later("interdiff", "--json", *extra)
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
        result = self.interdiff("--model", self.model)
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
        self.model = "claude-sonnet-5"
        self.review_head(head)
        self.refused(earlier + "Projector 1.2.3, model claude-sonnet-5, no effort")
        self.model = "claude-opus-5-5"
        self.assertEqual(0, self.later("release", "--model", self.model)[0])
        self.review_head(head)
        with mock.patch.dict(os.environ, {"CLAUDE_EFFORT": "high"}):
            self.refused(earlier + "Projector 1.2.3, model claude-opus-5-5, effort high")

    def test_a_full_review_whose_marker_names_no_version_is_not_built_on(self) -> None:
        full = self.github.reviews[-1]
        full["body"] = full["body"].replace(" projector=1.2.3 ", " ")
        self.pushed()

        self.refused("was made by Projector unknown, model claude-opus-5-5, no effort, and this run is "
                     "Projector 1.2.3, model claude-opus-5-5, no effort")

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
            f"{review.MARK} **Projector review** of `{head[:7]}` · `1.2.3` · model `claude-opus-5-5` · "
            f"**CLEAN** · incremental from `{self.reviewed[:7]}` · took 12m 34s\n\n"
            f"<!-- projector-review v=1 verdict=clean projector=1.2.3 model=claude-opus-5-5 sha={head} findings=0 "
            "seconds=754 covered=1/1 suggestions=0 -->\n"
            f"<!-- projector-incremental v=1 from={self.reviewed} kind=push insertions=1 deletions=1 files=1 "
            "conflicts=0 -->\n\n"
            f"Incremental review of the change since the full review of [`{self.reviewed[:7]}`]({review_page(901)}): "
            "+1 −1 lines in 1 file.\n\n"
            f"{self.BODY}\n",
            posted["body"])
        lines = posted["body"].splitlines()
        self.assertIsNotNone(review.SIGNATURE.match(lines[0]))
        self.assertIsNotNone(review.MARKER.match(lines[2]))
        self.assertEqual(review.Verdict("clean", head, "1.2.3", "claude-opus-5-5", None, self.reviewed, 0),
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


class KindPhraseTests(unittest.TestCase):
    def test_every_kind_has_one_phrase_that_the_output_and_the_lead_both_use(self) -> None:
        self.assertEqual({"push", "merge", "retarget", "rebase", "rewrite"}, set(review.KINDS))
        for kind, phrase in review.KINDS.items():
            with self.subTest(kind):
                self.assertTrue(phrase)
                result = {"incremental": True, "reason": None, "needs_gate": False, "from": "f" * 40,
                          "review": review_page(901), "kind": kind, "unchanged_commits": None, "conflicts": [],
                          "insertions": 1, "deletions": 1, "files": ["a.css"], "patch": ""}
                printed = phrase.format(base="trunk")
                lead = review.incremental_lead(result, "trunk")

                self.assertIn(f"kind: {printed}", review.interdiff_lines(result, "trunk"))
                if kind == "push":
                    self.assertNotIn("across", lead)
                else:
                    self.assertIn(f" across a {phrase.format(base='`trunk`')}: +1 −1 lines in 1 file.", lead)
                self.assertIsNotNone(review.INCREMENTAL.match(
                    f"<!-- projector-incremental v=1 from={'f' * 40} kind={kind} insertions=1 deletions=1 files=1 "
                    "conflicts=0 -->"))


class IncrementalSuggestionsTests(IncrementalCase):
    def build_on_suggestions(self) -> tuple[int, str]:
        """Publish a full review of the reviewed head that lists two suggestions, then an incremental review of a
        push that builds on it, and return the full review's id and the new head. The setup's full review listed
        none; the re-review of the same head is the newest full review, which the incremental review links."""
        self.assertEqual(0, self.setup_review("--loop", "l1", "--rereview")[0])
        self.assertEqual(0, self.publish("clean", "--threads", str(self.threads(*P3)))[0])
        full = self.posted()["id"]
        head = self.pushed()
        self.assertEqual(0, self.publish_incremental()[0])
        self.assertIn(f"({review_page(full)})", self.posted()["body"])
        self.assertIn(" covered=1/1 suggestions=0 -->", self.posted()["body"])
        return full, head

    def test_an_incremental_review_brings_the_suggestions_of_the_full_review_its_lead_links(self) -> None:
        full, head = self.build_on_suggestions()

        code, out, err = self.project("suggestions", "1", "--repo", REPO, "--json")

        self.assertEqual(0, code, err)
        self.assertEqual([dict(item, url=review_page(full), sha=self.reviewed) for item in LISTED], json.loads(out),
                         f"the suggestions name the full review's head, not {head[:7]}")

    def test_status_counts_the_suggestions_of_the_full_review_an_incremental_review_builds_on(self) -> None:
        # The incremental review lists none of its own, so a count of its own would read 0 while fix-pr still
        # has the full review's two to decide.
        _, head = self.build_on_suggestions()
        incremental = self.posted()["id"]

        code, out, err = self.project("status", "1", "--repo", REPO)
        result = json.loads(self.project("status", "1", "--repo", REPO, "--json")[1])
        listed = json.loads(self.project("suggestions", "1", "--repo", REPO, "--json")[1])

        self.assertEqual(0, code, err)
        self.assertEqual([f"#1 at {head[:7]}: clean with 2 suggestions from claude-opus-5-5, "
                          f"{review_page(incremental)}"], out.splitlines())
        self.assertEqual((incremental, 2, 2, len(listed)),
                         (result["newest"]["review"], result["newest"]["suggestions"], result["suggestions"], 2))


class SuggestionParsingTests(unittest.TestCase):
    def test_reads_each_label_a_reviewer_has_written_in_any_case(self) -> None:
        for label, text in LABELED.items():
            for body in (text, text.replace("Suggestions", "SUGGESTIONS")):
                with self.subTest(label, body=body[len(INTENT):len(INTENT) + 30]):
                    self.assertEqual(WRITTEN, review.parse_suggestions(body))

    def test_reads_the_form_publish_writes_back_as_it_was_given(self) -> None:
        self.assertEqual(LISTED, review.parse_suggestions(f"{INTENT}\n{review.suggestions_section(LISTED)}\n"))

    def test_a_body_without_the_section_has_none(self) -> None:
        for body in (f"{INTENT}\n### What was checked\n\n{CHECKED}",
                     f"{INTENT}\nThe suggestions below are optional.\n\n{CHECKED}",
                     f"{INTENT}\n```markdown\n## Suggestions\n\n- `a.py:1`: quoted, not listed.\n```\n",
                     f"{INTENT}\n##### Suggestions\n\n{ITEMS}\n"):
            with self.subTest(body[len(INTENT):len(INTENT) + 40]):
                self.assertIsNone(review.parse_suggestions(body))

    def test_an_empty_section_lists_none(self) -> None:
        self.assertEqual([], review.parse_suggestions(f"{INTENT}\n### Suggestions\n\n### What was checked\n"))

    def test_the_section_ends_at_a_paragraph_and_a_deeper_list_joins_its_item(self) -> None:
        body = (f"{INTENT}\n### Suggestions (P3)\n\n"
                "- `src/x.py:3-9`: Inline the helper.\n"
                "  - It has one caller.\n"
                "1. src/y.py:4 — Drop the unused import.\n\n"
                "The rest holds at this head.\n\n"
                "- **The gate.** It passes.\n")

        self.assertEqual([{"path": "src/x.py", "line": 3, "text": "Inline the helper. - It has one caller."},
                          {"path": "src/y.py", "line": 4, "text": "Drop the unused import."}],
                         review.parse_suggestions(body))


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

    def test_reads_the_suggestion_count_and_none_from_a_marker_that_gives_no_count(self) -> None:
        counted = self.MARKER.replace("covered=1/1 -->", "covered=1/1 suggestions=3 -->")

        self.assertEqual(3, review.verdict_marker(f"Signature\n\n{counted}\n\nBody.\n").suggestions)
        self.assertIsNone(review.verdict_marker(f"Signature\n\n{self.MARKER}\n\nBody.\n").suggestions)

    def test_an_incremental_line_counts_only_directly_after_a_marker(self) -> None:
        self.assertIsNone(review.verdict_marker(f"Body.\n{self.INCREMENTAL}\n"))
        self.assertIsNone(review.verdict_marker(f"{self.MARKER}\n\n{self.INCREMENTAL}\n").incremental_from)

    def test_a_marker_that_names_no_version_reads_with_none(self) -> None:
        older = ("<!-- projector-review v=1 verdict=clean model=claude-opus-5-5[1m] effort=high "
                 "sha=8f1ec500248ff566848d88e8f4fae6e6ed85d256 findings=0 seconds=66 covered=1/1 -->")

        self.assertEqual(review.Verdict("clean", "8f1ec500248ff566848d88e8f4fae6e6ed85d256", None,
                                        "claude-opus-5-5[1m]", "high", None),
                         review.verdict_marker(f"Signature\n\n{older}\n\nBody.\n"))

    def test_a_clean_verdict_spelled_approved_reads_as_clean(self) -> None:
        older = (f"<!-- projector-review v=1 verdict=approved model=claude-opus-5-5 effort=low sha={self.SHA} "
                 "findings=0 seconds=60 covered=1/1 -->")

        self.assertEqual(review.Verdict("clean", self.SHA, None, "claude-opus-5-5", "low", None),
                         review.verdict_marker(older))

    def test_a_marker_still_needs_its_verdict_model_and_head(self) -> None:
        for broken in (self.MARKER.replace("verdict=clean", "verdict=lgtm"),
                       self.MARKER.replace("model=claude-opus-5-5 ", ""),
                       self.MARKER.replace(f"sha={self.SHA}", f"sha={self.SHA[:7]}"),
                       self.MARKER.replace("projector=0.7.2 ", "projector= ")):
            with self.subTest(broken):
                self.assertIsNone(review.verdict_marker(broken))


class IdentityTests(unittest.TestCase):
    def test_a_model_is_one_reviewer_in_any_case_and_at_any_context_window(self) -> None:
        for model, expected in (("claude-opus-5-5", "claude-opus-5-5"), ("Claude-Opus-5-5[1m]", "claude-opus-5-5"),
                                ("gpt-5.5", "gpt-5.5"), ("anthropic/claude-opus-5-5", "anthropic/claude-opus-5-5"),
                                ("model[a]b", "model[a]b"), ("model[a][b]", "model[a]")):
            with self.subTest(model):
                self.assertEqual(expected, review.identity(model))

    def test_two_versions_of_one_model_are_two_reviewers(self) -> None:
        self.assertNotEqual(review.identity("claude-opus-5-5"), review.identity("claude-opus-5-6"))


class ReviewCountsTests(unittest.TestCase):
    def test_every_review_counts_in_a_private_or_internal_repository_in_either_case(self) -> None:
        # GraphQL gives a repository's visibility in capitals and REST in lowercase. A visibility GitHub does
        # not give counts as public.
        for visibility, counts in (("PRIVATE", True), ("private", True), ("INTERNAL", True), ("internal", True),
                                   ("PUBLIC", False), ("public", False), (None, False), ("", False)):
            with self.subTest(visibility=visibility):
                self.assertIs(counts, review.review_counts("CONTRIBUTOR", visibility))
                self.assertIs(counts, review.review_counts(None, visibility))
                for association in review.TRUSTED:
                    self.assertTrue(review.review_counts(association, visibility), f"{association} counts everywhere")

    def test_the_visibility_comes_from_the_pull_requests_base_repository(self) -> None:
        self.assertEqual("internal", review.repo_visibility({"base": {"repo": {"visibility": "internal"}}}))
        self.assertIsNone(review.repo_visibility({"base": {"repo": {"full_name": "owner/example"}}}))
        self.assertIsNone(review.repo_visibility({}))


class ArgumentTests(unittest.TestCase):
    def test_no_review_command_takes_a_sha(self) -> None:
        # Every SHA comes from GitHub or the state file; a typed one is how two
        # wrong 40-character SHAs reached a publish.
        for command in ("setup", "move", "census", "settle", "release", "finish", "publish", "gate", "interdiff"):
            with self.subTest(command):
                help_text = io.StringIO()
                with redirect_stdout(help_text), self.assertRaises(SystemExit):
                    cli.main(["review", command, "--help"])
                self.assertIsNone(re.search(r"--\S*sha|metavar.*SHA|\bSHA\b", help_text.getvalue()))
                self.assertIn("--loop", help_text.getvalue())


if __name__ == "__main__":
    unittest.main()
