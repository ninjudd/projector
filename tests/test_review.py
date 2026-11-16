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
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from urllib.parse import unquote

from projector import cli, review
from projector.review import NotFound, ReviewError
from tests.scratch import copy_of

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
        self.pr = {"number": 1, "state": "open", "user": {"login": "operator"}, "labels": [],
                   "head": {"sha": head, "ref": "feature", "repo": {"full_name": REPO}},
                   "base": {"ref": "trunk", "sha": base_sha, "repo": {"full_name": REPO, "default_branch": "trunk"}}}
        # The labels the repository has, which `gh pr edit` can add, and the labels created during the test.
        self.repo_labels: set[str] = set()
        self.created_labels: list[dict] = []
        # The status contexts a ruleset on the default branch requires, and every status posted.
        self.rules: list[str] = []
        self.rules_branches: list[str] = []
        self.statuses: list[dict] = []
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
        # Runs once, right after the next `gh pr ready`, as GitHub catching up with a push it had not yet shown.
        self.after_ready = None
        # Runs once, right after the next label edit, as a push landing at that moment.
        self.after_edit = None
        # Reviews the listing leaves out, as one GitHub has not indexed yet.
        self.unlisted: set[int] = set()
        # Answer a dismissal without dismissing anything.
        self.ignore_dismissals = False
        # The account gh is signed in as, which a call without a token acts as.
        self.login = "operator"
        # CI on the head: the check runs each look at the head finds, a poll at a time, the last repeating; the
        # head's workflow runs; and the workflow file behind each check suite.
        self.check_polls: list[list[dict]] = [[]]
        self.workflow_runs: list[dict] = []
        self.suites: dict[int, str] = {}

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
            if self.after_ready:
                hook, self.after_ready = self.after_ready, None
                hook()
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
        if args[:3] == ["pr", "edit", "1"]:
            add = [args[i + 1] for i, arg in enumerate(args) if arg == "--add-label"]
            remove = [args[i + 1] for i, arg in enumerate(args) if arg == "--remove-label"]
            missing = [name for name in add if name not in self.repo_labels]
            if missing:
                raise ReviewError(f"gh {' '.join(args)} failed: '{missing[0]}' not found")
            names = [label["name"] for label in self.pr["labels"] if label["name"] not in remove]
            self.pr["labels"] = [{"name": name} for name in names + [name for name in add if name not in names]]
            if self.after_edit:
                hook, self.after_edit = self.after_edit, None
                hook()
            return ""
        if args[:4] == ["api", "-X", "POST", f"repos/{REPO}/labels"]:
            fields = self.fields(args)
            if fields["name"] in self.repo_labels:
                raise ReviewError(f"gh {' '.join(args)} failed: Validation Failed (HTTP 422) already_exists")
            self.repo_labels.add(fields["name"])
            self.created_labels.append(fields)
            return "{}"
        if args[:3] == ["api", "-X", "POST"] and args[3].startswith(f"repos/{REPO}/statuses/"):
            self.statuses.append(dict(self.fields(args), sha=args[3].rsplit("/", 1)[1], token=token))
            return "{}"
        if args[:2] == ["api", "--paginate"] and args[2].startswith(f"repos/{REPO}/rules/branches/"):
            self.rules_branches.append(args[2].removeprefix(f"repos/{REPO}/rules/branches/"))
            return "".join(f"{context}\n" for context in self.rules)
        if len(args) == 2 and re.match(rf"repos/{REPO}/commits/[0-9a-f]+/check-runs\?", args[1]):
            runs = self.check_polls.pop(0) if len(self.check_polls) > 1 else self.check_polls[0]
            name = re.search(r"check_name=([^&]+)", args[1])
            # GitHub pages a commit's check runs 30 at a time.
            listed = [r for r in runs if r["name"] == unquote(name.group(1))] if name else runs[:30]
            return json.dumps({"total_count": len(listed), "check_runs": listed})
        if len(args) == 2 and args[1].startswith(f"repos/{REPO}/actions/runs?head_sha="):
            return json.dumps({"workflow_runs": self.workflow_runs})
        if len(args) == 2 and args[1].startswith(f"repos/{REPO}/actions/runs?check_suite_id="):
            suite = int(args[1].rsplit("=", 1)[1])
            return json.dumps({"workflow_runs": [{"path": self.suites[suite]}] if suite in self.suites else []})
        if len(args) == 4 and re.fullmatch(rf"repos/{REPO}/commits/[0-9a-f]+/status", args[1]):
            sha = args[1].split("/")[4]
            states = [s["state"] for s in self.statuses if s["sha"] == sha and s["context"] == review.REVIEW_CONTEXT]
            return f"{states[-1]}\n" if states else ""
        raise AssertionError(f"unexpected gh call: {args}")

    @staticmethod
    def fields(args: list[str]) -> dict[str, str]:
        """The `-f name=value` fields of a `gh api` call."""
        return dict(args[i + 1].split("=", 1) for i, arg in enumerate(args) if arg == "-f")

    def labels(self) -> list[str]:
        return [label["name"] for label in self.pr["labels"]]

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


def review_repositories(tmp: Path) -> tuple[str, str]:
    """Build a bare origin holding `trunk` and a first head, the work clone that pushed it, and a reviewer's checkout."""
    origin = tmp / "origin.git"
    git(tmp, "init", "--quiet", "--bare", "-b", "trunk", str(origin))
    work = tmp / "work"
    git(tmp, "init", "--quiet", "-b", "trunk", str(work))
    (work / "README.md").write_text("trunk\n")
    git(work, "add", ".")
    git(work, "commit", "--quiet", "-m", "trunk")
    base = git(work, "rev-parse", "HEAD")
    git(work, "remote", "add", "origin", str(origin))
    git(work, "push", "--quiet", "origin", "trunk")
    head = ReviewCase.push(work, "first head")
    checkout = tmp / "checkout"
    git(tmp, "clone", "--quiet", str(origin), str(checkout))
    # The clone's origin is a local path; the commands read the repository from
    # origin's GitHub URL, so point the URL there and keep fetching locally.
    git(checkout, "remote", "set-url", "origin", f"https://github.com/{REPO}.git")
    git(checkout, "config", f"url.{origin}.insteadOf", f"https://github.com/{REPO}.git")
    return base, head


class ReviewCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp, (self.base, self.head) = copy_of(review_repositories)
        self.origin = tmp / "origin.git"
        self.work = tmp / "work"
        self.checkout = tmp / "checkout"
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

    @staticmethod
    def push(work: Path, text: str) -> str:
        (work / "change.txt").write_text(text + "\n")
        git(work, "add", ".")
        git(work, "commit", "--quiet", "-m", text)
        git(work, "push", "--quiet", "--force", "origin", "HEAD:refs/heads/feature", "HEAD:refs/pull/1/head")
        return git(work, "rev-parse", "HEAD")

    def push_head(self, text: str) -> str:
        return self.push(self.work, text)

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
    def test_sets_up_the_exact_head_holds_its_lock_and_posts_the_start_comment_as_the_reviewer(self) -> None:
        # The operator's own `git fetch` may have just written FETCH_HEAD, for a `git checkout FETCH_HEAD` to follow.
        fetch_head = self.checkout / ".git" / "FETCH_HEAD"
        fetch_head.write_text(f"{self.base}\t\tbranch 'trunk' of origin\n")

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
        self.assertEqual(f"{self.base}\t\tbranch 'trunk' of origin\n", fetch_head.read_text(),
                         "the operator's FETCH_HEAD is left alone")

        held = json.loads(self.lock(self.head).read_text())
        self.assertEqual(("example-loop", self.head), (held["loop"], held["sha"]), "the lock records who took it")
        self.assertIn("host", held)
        self.assertIn("taken_at", held)

        posts = self.github.posts()
        self.assertEqual(1, len(posts))
        self.assertEqual("token-operator", posts[0][1])
        for args, token in self.github.calls:
            self.assertNotIn("switch", args)
            writes = any(a.startswith("body=") for a in args) or args[:2] == ["pr", "edit"] \
                or args[:4] == ["api", "-X", "POST", f"repos/{REPO}/labels"]
            self.assertEqual("token-operator" if writes else None, token,
                             f"only a write carries the reviewer's token: {args}")
        self.assertEqual(["reviewing"], self.github.labels())
        self.assertEqual([{"name": "reviewing", "color": "fbca04",
                           "description": "A Projector review of this pull request is in progress"}],
                         self.github.created_labels, "the first review in a repository creates the label")

    def test_another_fetch_rewriting_fetch_head_does_not_read_as_a_moved_head(self) -> None:
        real = review.git

        def racing(*args: str) -> str:
            out = real(*args)
            if "fetch" in args:
                # Another review's fetch, or a summary publish's, lands in the same checkout right after this one.
                (self.checkout / ".git" / "FETCH_HEAD").write_text(f"{self.base}\t\tbranch 'trunk' of origin\n")
            return out

        with mock.patch.object(review, "git", racing):
            code, _, err = self.setup_review()

        self.assertEqual(0, code, err)
        self.assertEqual(self.head, self.read_state()["sha"])
        self.assertEqual("", git(self.checkout, "for-each-ref", "refs/projector/"), "the fetch's own refs are gone")

    def test_takes_off_a_clean_label_no_clean_verdict_on_the_head_supports(self) -> None:
        # A push left `clean` behind in a repository without the workflow that removes it.
        self.github.repo_labels.update({"clean", "reviewing"})
        self.github.pr["labels"] = [{"name": "clean"}]
        self.github.reviews.append({"id": 800, "user": {"login": "operator"}, "event": "COMMENT", "comments": [],
                                    "commit_id": self.base, "at": "2026-09-27T01:00:00Z",
                                    "body": verdict_body(self.base, "gpt-5.5")})

        code, out, err = self.setup_review()

        self.assertEqual(0, code, err)
        self.assertEqual(["reviewing"], self.github.labels(), "the clean verdict is on an earlier head")
        self.assertIn("removed the clean label", out)
        self.assertEqual([], self.github.created_labels, "a label the repository has is used as it is")

    def test_a_verdict_read_that_fails_keeps_the_clean_label_and_finishes_the_setup(self) -> None:
        # The start comment is already posted and the lock held when setup reads the verdicts.
        self.github.repo_labels.update({"clean", "reviewing"})
        self.github.pr["labels"] = [{"name": "clean"}]
        self.github.fail = "author_association"

        code, _, err = self.setup_review()

        self.assertEqual(0, code, err)
        self.assertIn("warning: kept the clean label on acme/app#1, because its verdicts could not be read", err)
        self.assertEqual(["clean", "reviewing"], self.github.labels())
        self.assertEqual(self.head, self.read_state()["sha"], "the review is set up, so the next setup is not refused")

    def test_keeps_a_clean_label_another_models_clean_verdict_on_the_head_supports(self) -> None:
        self.github.repo_labels.update({"clean", "reviewing"})
        self.github.pr["labels"] = [{"name": "clean"}]
        self.github.reviews.append({"id": 800, "user": {"login": "operator"}, "event": "COMMENT", "comments": [],
                                    "commit_id": self.head, "at": "2026-09-27T01:00:00Z",
                                    "body": verdict_body(self.head, "gpt-5.5")})

        code, _, err = self.setup_review()

        self.assertEqual(0, code, err)
        self.assertEqual(["clean", "reviewing"], self.github.labels())

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
        self.assertIn(f" sha={new} model=claude-opus-5-5 -->", body, "the edit keeps the model")
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

    # A time long past that a gate command sets the lock back to with `touch -r`, so `[ lock -nt reference ]`
    # holds only once gate touched the lock.
    PAST = 1_000_000_000

    def reference(self) -> Path:
        path = Path(tempfile.mkdtemp()) / "reference"
        path.write_text("")
        os.utime(path, (self.PAST, self.PAST))
        return path

    def test_touches_the_lock_as_it_starts_and_as_it_ends(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        lock, reference = self.lock(self.head), self.reference()
        os.utime(lock, (self.PAST, self.PAST))
        self.configure(f'[ "{lock}" -nt "{reference}" ] && echo started > "$1/gate.started"; '
                       f'touch -r "{reference}" "{lock}"')

        code, _, err = self.later("gate", "--loop", "l1")

        self.assertEqual(0, code, err)
        self.assertTrue((Path(self.read_state()["worktree"]) / "gate.started").exists(), "touched as it starts")
        self.assertGreater(lock.stat().st_mtime, self.PAST, "touched as it ends")

    def test_a_long_gate_keeps_its_lock_fresh_while_its_command_runs(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        lock, reference = self.lock(self.head), self.reference()
        # Each round sets the lock back, so it reads fresh at the round's end only if gate touched it meanwhile.
        self.configure(f'for round in 1 2; do touch -r "{reference}" "{lock}"; sleep 0.2; '
                       f'if [ "{lock}" -nt "{reference}" ]; then echo $round >> "$1/gate.rounds"; fi; done')

        with mock.patch.object(review, "TOUCH_EVERY", 0.05):
            code, _, err = self.later("gate", "--loop", "l1")

        self.assertEqual(0, code, err)
        self.assertEqual("1\n2\n", (Path(self.read_state()["worktree"]) / "gate.rounds").read_text())

    def test_a_gate_in_another_loop_touches_no_lock(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        lock = self.lock(self.head)
        os.utime(lock, (self.PAST, self.PAST))
        self.configure("sleep 0.15")

        with mock.patch.object(review, "TOUCH_EVERY", 0.05):
            code, _, err = self.later("gate", "--loop", "l2")

        self.assertEqual(0, code, err)
        self.assertEqual(self.PAST, lock.stat().st_mtime)

    def test_refuses_when_review_gate_is_unset(self) -> None:
        self.assertEqual(0, self.setup_review()[0])

        code, _, err = self.later("gate")

        self.assertEqual(1, code)
        self.assertIn("nothing to run", err)
        self.assertNotIn("gate", self.read_state())

    def test_refuses_an_untrusted_head_as_untrusted_without_running_anything(self) -> None:
        self.github.pr["head"]["repo"] = {"full_name": "someone/app"}
        self.github.pr["head"]["ref"] = "does-not-exist-here"
        self.assertEqual(0, self.setup_review()[0])
        worktree = Path(self.read_state()["worktree"])

        unset = self.later("gate")
        self.configure(self.GATE)
        configured = self.later("gate")

        # With review.gate unset too, a refusal that says to run the repository's checks would send the
        # review straight to the head's code.
        self.assertEqual(1, unset[0])
        self.assertIn("review it by reading", unset[2])
        self.assertNotIn("run the repository's checks", unset[2])
        self.assertEqual(1, configured[0])
        self.assertIn("untrusted", configured[2])
        self.assertIn("review it by reading", configured[2])
        self.assertFalse((worktree / "gate.args").exists())
        self.assertNotIn("gate", self.read_state())

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


CHECK_YML = "on: pull_request\njobs:\n  check:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n"


def check_run(conclusion: str | None = "success", name: str = "check", app: str = "github-actions",
              suite: int = 7) -> dict:
    """A check run as GitHub's check-runs endpoint lists it; a `None` conclusion is a run still in progress."""
    return {"name": name, "status": "completed" if conclusion else "in_progress", "conclusion": conclusion,
            "app": {"slug": app}, "check_suite": {"id": suite},
            "html_url": f"https://github.com/{REPO}/actions/runs/1/job/{suite}"}


class CiGateTests(ReviewCase):
    """`project review gate` with `review.gate_checks`: it reads the head's GitHub Actions check runs instead of
    running the suite, and gives no evidence where CI cannot vouch for the head's code."""

    def setUp(self) -> None:
        super().setUp()
        # The gate looks again at once, and a test whose fake never settles fails in seconds instead of spinning for
        # the real 30 minutes.
        for name, value in (("CI_POLL", 0), ("CI_START", 0), ("CI_WAIT", 5)):
            patcher = mock.patch.object(review, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.github.suites = {7: ".github/workflows/check.yml"}

    def heads(self, base: dict[str, str], head: dict[str, str | None]) -> None:
        """Move trunk to the first head's trunk plus `base`, push a head that changes `head` on it (None deletes a
        file), and set the head up for review."""
        git(self.work, "checkout", "--quiet", "--detach", self.base)
        for files in (base, head):
            for name, text in files.items():
                path = self.work / name
                if text is None:
                    path.unlink()
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text)
            git(self.work, "add", "-A")
            git(self.work, "commit", "--quiet", "--allow-empty", "-m", "ci")
            if files is base:
                trunk = git(self.work, "rev-parse", "HEAD")
                git(self.work, "push", "--quiet", "--force", "origin", "HEAD:refs/heads/trunk")
        self.head = git(self.work, "rev-parse", "HEAD")
        git(self.work, "push", "--quiet", "--force", "origin", "HEAD:refs/heads/feature", "HEAD:refs/pull/1/head")
        self.github.pr["head"]["sha"], self.github.pr["base"]["sha"] = self.head, trunk
        code, _, err = self.setup_review()
        self.assertEqual(0, code, err)

    def configure(self, checks: str = "check", command: str | None = None) -> None:
        lines = [f"gate_checks = {json.dumps(checks)}"] + ([f"gate = {json.dumps(command)}"] if command else [])
        (self.checkout / ".projector.toml").write_text("[review]\n" + "\n".join(lines) + "\n")

    def gate(self) -> tuple[int, dict, str]:
        code, out, err = self.later("gate", "--json")
        return code, json.loads(out), err

    def test_a_passing_check_passes_the_gate_without_running_the_suite(self) -> None:
        # Catches a gate that ignores review.gate_checks, or reads a passing record as anything but a pass.
        self.heads({".github/workflows/check.yml": CHECK_YML}, {"src.py": "x = 1\n"})
        self.configure()
        self.github.check_polls = [[check_run("success")]]

        code, record, err = self.gate()

        self.assertEqual(0, code, err)
        self.assertEqual((None, "ci", [{"name": "check", "conclusion": "success", "url": check_run()["html_url"]}]),
                         (record["command"], record["source"], record["checks"]))
        self.assertTrue(review.gate_passed(self.read_state()))

    def test_a_check_that_failed_fails_and_one_that_did_not_run_gives_no_evidence(self) -> None:
        # Catches a gate that sends the fixer after a test failure that never happened, or that passes a check
        # that ran nothing.
        self.heads({".github/workflows/check.yml": CHECK_YML}, {"src.py": "x = 1\n"})
        self.configure()
        for conclusion, expected in (("failure", 1), ("timed_out", 1), ("cancelled", 75), ("skipped", 75),
                                     ("neutral", 75)):
            with self.subTest(conclusion):
                self.github.check_polls = [[check_run(conclusion)]]

                code, record, err = self.gate()

                self.assertEqual(expected, code, err)
                self.assertEqual(conclusion, record["checks"][0]["conclusion"])
                self.assertFalse(review.gate_passed(self.read_state()))

    def test_a_check_still_running_when_the_wait_ends_gives_no_evidence(self) -> None:
        # Catches a gate that reads a pending check as passed, or that never returns.
        self.heads({".github/workflows/check.yml": CHECK_YML}, {"src.py": "x = 1\n"})
        self.configure()
        self.github.check_polls = [[check_run(None)]]

        with mock.patch.object(review, "CI_WAIT", 0):
            code, record, err = self.gate()
            printed = self.later("gate")[2]

        self.assertEqual(75, code, err)
        self.assertEqual(None, record["checks"][0]["conclusion"])
        self.assertIn("no completed run within", record["reason"])
        self.assertIn("check: no completed run", printed)
        self.assertIn("CI gave no evidence: check had no completed run within", printed)

    def test_only_a_github_actions_run_vouches_for_the_head(self) -> None:
        # Catches a gate that trusts any check run named `check`, which any app installed on the repository can post.
        self.heads({".github/workflows/check.yml": CHECK_YML}, {"src.py": "x = 1\n"})
        self.configure()
        self.github.check_polls = [[check_run("success", app="someone-elses-app")]]
        self.github.workflow_runs = [{"id": 1, "status": "completed", "conclusion": "success"}]

        code, record, err = self.gate()

        self.assertEqual(75, code, err)
        self.assertIn("no run of check is coming", record["reason"])

    def test_finds_the_named_check_beyond_the_first_page_of_the_heads_check_runs(self) -> None:
        # Catches a read of the commit's check runs that sees only the first page of 30.
        self.heads({".github/workflows/check.yml": CHECK_YML}, {"src.py": "x = 1\n"})
        self.configure()
        self.github.check_polls = [[check_run("success", name=f"lint {n}") for n in range(30)] + [check_run("success")]]

        code, record, err = self.gate()

        self.assertEqual(0, code, err)

    def test_a_head_that_changed_what_its_check_runs_gives_no_evidence(self) -> None:
        # Catches a gate that lets a head vouch for itself through its workflow or a local action, and one that
        # refuses a head for changing a workflow that decides none of the named checks.
        setup = "runs:\n  using: composite\n  steps:\n    - run: ./install.sh\n      shell: bash\n"
        uses_setup = CHECK_YML + "      - uses: ./.github/actions/setup\n      - uses: ./.github/workflows/test.yml\n"
        base = {".github/workflows/check.yml": uses_setup, ".github/workflows/release.yml": "on: push\n",
                ".github/workflows/test.yml": "on: workflow_call\n", ".github/actions/setup/action.yml": setup,
                ".github/actions/setup/install.sh": "true\n"}
        cases = (
            (".github/workflows/check.yml", {".github/workflows/check.yml": uses_setup + "# skip it\n"}, 75),
            (".github/workflows/test.yml", {".github/workflows/test.yml": "on: workflow_call\n# skip\n"}, 75),
            (".github/actions/setup/install.sh", {".github/actions/setup/install.sh": "exit 0\n"}, 75),
            (".github/actions/setup/action.yml", {".github/actions/setup/action.yml": setup + "# skip\n"}, 75),
            ("only the release workflow", {".github/workflows/release.yml": "on: [push, workflow_dispatch]\n"}, 0),
        )
        for changed, head, expected in cases:
            with self.subTest(changed):
                self.heads(base, head)
                self.configure()
                self.github.check_polls = [[check_run("success")]]

                code, record, err = self.gate()

                self.assertEqual(expected, code, err)
                if expected:
                    self.assertIn(f"the head changed {changed}, which decides what check runs", record["reason"])

    def test_a_check_whose_files_cannot_be_told_gives_no_evidence(self) -> None:
        # Catches a gate that accepts CI when it cannot say which files decided the run.
        expression = CHECK_YML + "      - uses: ${{ matrix.action }}\n"
        for case, workflow, suites, words in (
                ("no workflow run matches its suite", CHECK_YML, {}, "no workflow run matches the check suite"),
                ("an expression names a step", expression, {7: ".github/workflows/check.yml"}, "an expression"),
                ("its workflow is gone at the head", CHECK_YML, {7: ".github/workflows/gone.yml"}, "cannot be read")):
            with self.subTest(case):
                self.heads({".github/workflows/check.yml": workflow}, {"src.py": "x = 1\n"})
                self.configure()
                self.github.suites = suites
                self.github.check_polls = [[check_run("success")]]

                code, record, err = self.gate()

                self.assertEqual(75, code, err)
                self.assertIn(words, record["reason"])

    def test_an_untrusted_head_gets_cis_result_and_runs_no_command(self) -> None:
        # Catches a gate that runs the head's code on an untrusted head, and one that keeps CI's result from it.
        self.github.pr["head"]["repo"] = {"full_name": "someone/app"}
        self.github.pr["head"]["ref"] = "does-not-exist-here"
        self.heads({".github/workflows/check.yml": CHECK_YML}, {"src.py": "x = 1\n"})
        self.assertFalse(self.read_state()["trusted"])
        self.configure(command=GateTests.GATE)
        self.github.check_polls = [[check_run("success")]]

        code, record, err = self.gate()

        self.assertEqual(0, code, err)
        self.assertEqual(None, record["command"])
        self.assertFalse((Path(self.read_state()["worktree"]) / "gate.args").exists(), "the command did not run")

    def test_a_failing_command_decides_the_gate_before_ci_is_read(self) -> None:
        # Catches a gate that lets a passing CI run cover a failure in the part of the gate CI cannot run.
        self.heads({".github/workflows/check.yml": CHECK_YML}, {"src.py": "x = 1\n"})
        self.configure(command="exit 4")
        self.github.check_polls = [[check_run("success")]]

        code, record, err = self.gate()

        self.assertEqual((4, "exit 4", "local"), (code, record["command"], record["source"]))
        self.assertFalse(any("check-runs" in " ".join(args) for args, _ in self.github.calls))

    def test_ends_the_wait_as_soon_as_no_run_of_the_check_can_come(self) -> None:
        # Catches a gate that waits 30 minutes for a run that waits for a person, or for a job that is gone.
        self.heads({".github/workflows/check.yml": CHECK_YML}, {"src.py": "x = 1\n"})
        self.configure()
        cases = (
            ("waits for approval", [{"id": 1, "status": "completed", "conclusion": "action_required",
                                     "html_url": "https://github.com/acme/app/actions/runs/1"}],
             "https://github.com/acme/app/actions/runs/1 waits for a maintainer to approve it"),
            ("every run finished", [{"id": 1, "status": "completed", "conclusion": "success"}],
             "every workflow run on the head finished without it"),
            ("no run started", [], "no workflow run started on the head"),
        )
        for case, workflow_runs, words in cases:
            with self.subTest(case):
                self.github.check_polls = [[]]
                self.github.workflow_runs = workflow_runs

                # With no wait left, a gate that misses the early exit stops at the timeout, whose reason differs.
                with mock.patch.object(review, "CI_WAIT", 0):
                    code, record, err = self.gate()

                self.assertEqual(75, code, err)
                self.assertIn(words, record["reason"])

    def test_keeps_waiting_while_a_workflow_run_on_the_head_is_still_coming(self) -> None:
        # Catches a gate that gives up while a run is queued, or held by an environment approval or a concurrency
        # group.
        self.heads({".github/workflows/check.yml": CHECK_YML}, {"src.py": "x = 1\n"})
        self.configure()
        for status in ("queued", "in_progress", "waiting", "pending", "requested"):
            with self.subTest(status):
                self.github.workflow_runs = [{"id": 1, "status": status}]
                self.github.check_polls = [[], [], [check_run("success")]]

                code, record, err = self.gate()

                self.assertEqual(0, code, err)

    def test_without_gate_checks_an_untrusted_head_is_refused_as_before(self) -> None:
        # Catches a gate that reads CI, or runs anything, for a repository that never named its checks.
        self.github.pr["head"]["repo"] = {"full_name": "someone/app"}
        self.github.pr["head"]["ref"] = "does-not-exist-here"
        self.heads({}, {"src.py": "x = 1\n"})

        code, _, err = self.later("gate")

        self.assertEqual(1, code)
        self.assertIn("review it by reading", err)
        self.assertFalse(any("check-runs" in " ".join(args) for args, _ in self.github.calls))


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
    def test_publishes_a_clean_self_review_labels_it_clean_and_cleans_up_in_order(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.assertEqual(["reviewing"], self.github.labels(), "setup marks the review in progress")

        code, out, err = self.publish("clean", cwd=Path(tempfile.mkdtemp()))

        self.assertEqual(0, code, err)
        review_body = self.posted()["body"]
        self.assertEqual(
            f"{review.MARK} **Projector review** of `{self.head[:7]}` · `1.2.3` · model `claude-opus-5-5` · "
            "**CLEAN** · took 12m 34s",
            review_body.splitlines()[0])
        self.assertIn(f"<!-- projector-review v=1 verdict=clean projector=1.2.3 model=claude-opus-5-5 "
                      f"sha={self.head} findings=0 seconds=754 covered=3/3 -->", review_body)
        self.assertNotIn("projector-suggestions", review_body, "a review that lists none counts none")
        self.assertIn("0 finding threads: 0 resolved, 0 open", review_body)
        self.assertEqual(("COMMENT", self.head, "token-operator"),
                         (self.posted()["event"], self.posted()["commit_id"], "token-" + self.posted()["user"]["login"]))
        self.assertEqual(["clean"], self.github.labels(), "a clean head is labeled clean and no longer reviewing")
        self.assertTrue(self.github.pr["draft"], "draft state is the author's")
        self.assertFalse(any(args[:2] == ["pr", "ready"] for args, _ in self.github.calls))
        self.assertEqual({}, self.github.comments, "the start comment is gone")
        self.assertFalse(self.lock(self.head).exists(), "the lock is released")
        record = json.loads((self.state / "loops" / "l1" / "published.json").read_text())
        self.assertEqual([(REPO, 1, self.head, 901, "clean", "claude-opus-5-5")],
                         [(r["repo"], r["number"], r["sha"], r["review_id"], r["verdict"], r["model"]) for r in record])
        steps = [" ".join(args) for args, _ in self.github.calls]
        reread = next(i for i, c in enumerate(steps) if c == f"api repos/{REPO}/pulls/1/reviews/901")
        labeled = [i for i, c in enumerate(steps) if c.startswith("pr edit")][-1]
        delete = next(i for i, c in enumerate(steps) if c.startswith("api -X DELETE"))
        self.assertEqual("pr edit 1 --repo acme/app --add-label clean --remove-label reviewing", steps[labeled],
                         "one edit takes reviewing off and puts clean on")
        self.assertLess(reread, labeled, "the newest verdict counts the review as read back")
        self.assertLess(labeled, delete, "the start comment goes only after the re-read and the labels")
        self.assertIn("published review 901", out)
        self.assertIn(f"{self.head[:7]} is signed off", out)
        self.assertEqual([], self.github.statuses, "no ruleset requires the check, so no status is posted")

    def test_a_clean_publish_posts_success_on_the_reviewed_sha_even_when_the_head_moves(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.github.rules = [review.REVIEW_CONTEXT]
        pushed = "f" * 40
        self.github.after_post = lambda: self.github.pr["head"].update(sha=pushed)

        code, out, err = self.publish("clean")

        self.assertEqual(0, code, err)
        self.assertEqual([(self.head, "success", review.REVIEW_CONTEXT, "Clean: claude-opus-5-5", review_page(901),
                           "token-operator")],
                         [(s["sha"], s["state"], s["context"], s["description"], s["target_url"], s["token"])
                          for s in self.github.statuses])
        self.assertEqual(["trunk"], self.github.rules_branches, "the default branch's rules decide")
        self.assertEqual([], self.github.labels(), "the head moved, so the pull request is not labeled clean")
        self.assertIn(f"posted {review.REVIEW_CONTEXT} success on {self.head[:7]}", out)

    def test_a_push_during_the_label_edit_takes_the_clean_label_back_off(self) -> None:
        # The push lands after the publish read the head and before its edit added the label, so the event the
        # push queued lists no clean and the label workflow skips it.
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.github.rules = [review.REVIEW_CONTEXT]
        pushed = "f" * 40
        self.github.after_edit = lambda: self.github.pr["head"].update(sha=pushed)

        code, out, err = self.publish("clean")

        self.assertEqual(0, code, err)
        self.assertEqual([], self.github.labels(), "the head moved during the edit, so clean comes back off")
        self.assertEqual([(self.head, "success")], [(s["sha"], s["state"]) for s in self.github.statuses],
                         "the reviewed head keeps its status")
        self.assertNotIn("added the clean label", out)
        self.assertIn("removed the reviewing label", out)

    def test_changes_requested_posts_a_failing_status_where_the_check_is_required(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.github.rules = [review.REVIEW_CONTEXT]
        self.open_finding()

        code, _, err = self.publish("changes-requested")

        self.assertEqual(0, code, err)
        self.assertEqual([("failure", "Changes requested: 1 open finding")],
                         [(s["state"], s["description"]) for s in self.github.statuses])
        self.assertEqual([], self.github.labels())

    def test_a_refused_label_change_warns_and_the_review_still_signs_off(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.github.rules = [review.REVIEW_CONTEXT]
        self.github.fail = "pr edit"

        code, _, err = self.publish("clean")

        self.assertEqual(0, code, err)
        self.assertIn("warning: left the labels on acme/app#1 as they were", err)
        self.assertEqual(["success"], [s["state"] for s in self.github.statuses])
        self.assertEqual({}, self.github.comments, "the publish finished")

    def test_the_labels_stay_alone_while_review_labels_is_off(self) -> None:
        (self.checkout / ".projector.toml").write_text("[review]\nlabels = false\n")
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        code, _, err = self.publish("clean")

        self.assertEqual(0, code, err)
        self.assertFalse(any(args[:2] == ["pr", "edit"] for args, _ in self.github.calls))
        self.assertEqual([], self.github.labels())

    def test_changes_requested_posts_findings_with_the_marker_and_leaves_draft_state_alone(self) -> None:
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
        self.assertFalse(self.github.pr["draft"], "a review that requests changes leaves the pull request ready")
        self.assertFalse(any(args[:2] == ["pr", "ready"] for args, _ in self.github.calls))
        self.assertEqual([], self.github.labels(), "neither reviewing nor clean")

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
        self.github.pr.update(user={"login": "teammate"}, draft=False)
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

    def test_a_clean_cross_author_review_never_approves_a_draft(self) -> None:
        self.github.pr["user"] = {"login": "teammate"}
        self.github.permissions = {"teammate": "write"}
        self.github.commit_authors = ["teammate"]
        (self.checkout / ".projector.toml").write_text("[review]\nallow_approve = true\n")
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        code, _, err = self.publish("clean")

        self.assertEqual(0, code, err)
        self.assertEqual("COMMENT", self.posted()["event"], "the author is still working on a draft")
        self.assertEqual(["clean"], self.github.labels())


class PublishRefusalTests(PublishCase):

    def refused(self, result: tuple[int, str, str], words: str) -> None:
        code, _, err = result
        self.assertEqual(1, code, "a refusal exits non-zero")
        self.assertIn(words, err)
        self.assertEqual([], self.github.reviews, "nothing was posted")
        self.assertTrue(self.lock(self.head).exists(), "a refused publish keeps the lock")

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

    def test_incremental_refuses_what_it_does_not_take_and_a_full_review_needs_a_verdict_and_coverage(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        lead = self.dir / "incremental.md"
        lead.write_text("The push widens row r2's padding.\n")
        body = str(self.body())

        for extra, words in ((["--threads", str(self.threads(finding()))], "--incremental takes no --threads"),
                             (["--covered", "1/1"], "--incremental takes no --covered"),
                             (["--second-verdict", "901"], "--incremental takes no --second-verdict"),
                             (["--verdict", "changes-requested"], "an incremental review is always clean")):
            with self.subTest(words):
                self.refused(self.later("publish", "--incremental", "--body", str(lead), *extra), words)
        self.refused(self.later("publish", "--body", body, "--covered", "3/3"),
                     "--verdict must be clean or changes-requested")
        self.refused(self.later("publish", "--body", body, "--verdict", "clean"), "--covered must be")

    def test_a_body_cannot_carry_its_own_signature_or_marker_so_took_is_never_stale(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        stale = (f"{review.MARK} **Projector review** · model `m` · **CLEAN** · took 3m 00s\n\n"
                 "{census}\n")

        self.refused(self.publish("clean", body=self.body(stale)), "signature line")
        self.refused(self.publish("clean", body=self.body("{census}\n\n<!-- projector-review v=1 verdict=clean -->\n")),
                     "signature line")

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

    def test_a_second_publish_on_the_same_head_needs_a_rereview(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.assertEqual(0, self.publish("clean")[0])
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        code, _, err = self.publish("clean")

        self.assertEqual(1, code)
        self.assertIn("--rereview", err)
        self.assertEqual(1, len(self.github.reviews))

        self.assertEqual(0, self.later("release")[0])
        self.assertEqual(0, self.setup_review("--loop", "l1", "--rereview")[0])
        code, _, err = self.publish("clean")

        self.assertEqual(0, code, err)
        self.assertEqual(2, len(self.github.reviews), "a re-review publishes a second verdict on the same head")

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

    def test_the_review_names_no_effort_when_the_host_reports_only_whitespace(self) -> None:
        # No effort at all is the case every other test here publishes.
        for text in self.written("  "):
            self.assertNotIn("effort", text)

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
        self.github.rules = [review.REVIEW_CONTEXT]
        self.github.fail = "statuses/"

        failed = self.publish("clean")
        self.github.fail = None
        retried = self.publish("clean")

        self.assertEqual(1, failed[0])
        self.assertIn("HTTP 502", failed[2])
        self.assertEqual(0, retried[0], retried[2])
        self.assertEqual(1, len(self.github.reviews), "the retry did not post a second review")
        self.assertEqual([(self.head, "success")], [(s["sha"], s["state"]) for s in self.github.statuses])
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

    def test_a_signature_that_would_not_match_the_skill_is_refused(self) -> None:
        self.model = "has`backtick"
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        self.refused(self.publish("clean"), "does not match review-pr/SKILL.md")

    def test_publish_without_a_review_holding_the_lock_is_refused(self) -> None:
        self.assertEqual(0, self.setup_review()[0])
        self.assertEqual(0, self.later("release", "--repo", REPO)[0])

        code, _, err = self.publish("clean", loop=None)

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


# The review marker as release 0.7.19 reads it, copied from that release. A review loop on another machine
# may run it, and it reads a marker with any field it does not know as no verdict at all.
MARKER_0_7_19 = re.compile(
    r"^<!-- projector-review v=1 verdict=(clean|approved|changes-requested) (?:projector=(\S+) )?model=(\S+) "
    r"(?:effort=(\S+) )?sha=([0-9a-f]{40}) findings=\d+ seconds=\d+ covered=\d+/\d+ -->$"
)


def verdict_0_7_19(body: str) -> tuple[str, str] | None:
    """The verdict and head release 0.7.19 reads from a review body, or None when it reads none."""
    found = next(filter(None, map(MARKER_0_7_19.match, body.splitlines())), None)
    return (found.group(1), found.group(5)) if found else None


class PublishSuggestionTests(PublishCase):
    def test_a_release_from_before_suggestions_reads_the_verdict_of_every_review_this_one_writes(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.assertEqual(0, self.publish("changes-requested", "--threads", str(self.threads(*P3, finding())))[0])
        listing = self.posted()["body"]
        # The fake's census reads no thread the first review opened, so the re-review may be clean.
        self.assertEqual(0, self.setup_review("--loop", "l1", "--rereview")[0])
        self.assertEqual(0, self.publish("clean")[0])

        self.assertEqual(("changes-requested", self.head), verdict_0_7_19(listing))
        self.assertEqual(("clean", self.head), verdict_0_7_19(self.posted()["body"]))

    def test_lists_p3_entries_in_one_form_after_the_body_and_counts_them_before_the_list(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        code, _, err = self.publish("changes-requested", "--threads", str(self.threads(P3[0], finding(), P3[1])))

        self.assertEqual(0, code, err)
        posted = self.posted()
        self.assertTrue(posted["body"].endswith(
            "Covered 3 of 3 changed files.\n\n"
            "<!-- projector-suggestions v=1 count=2 -->\n"
            "### Suggestions\n\n"
            "- `src/x.py:12`: Name the helper `warm`, as the cache calls it.\n"
            "- `README.md`: Say that a cold start warms the cache.\n"), posted["body"])
        self.assertIn(" covered=3/3 -->\n", posted["body"], "the review's marker keeps the shape every release reads")
        self.assertIn(" findings=1 ", posted["body"], "a P3 is no finding")
        self.assertIn("1 finding thread: 0 resolved, 1 open", posted["body"])
        self.assertEqual([("src/x.py", 3)], [(c["path"], c["line"]) for c in posted["comments"]],
                         "a P3 opens no thread, and its line need not be in the diff")
        self.assertEqual(LISTED, review.parse_suggestions(posted["body"]))
        self.assertEqual(2, review.listed_suggestions(posted))

    def test_a_head_whose_only_entries_are_p3_is_clean(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        code, _, err = self.publish("clean", "--threads", str(self.threads(*P3)))

        self.assertEqual(0, code, err)
        self.assertEqual(([], "COMMENT"), (self.posted()["comments"], self.posted()["event"]))
        self.assertIn(" findings=0 seconds=754 covered=3/3 -->", self.posted()["body"])
        self.assertIn("\n<!-- projector-suggestions v=1 count=2 -->\n### Suggestions\n", self.posted()["body"])
        self.assertEqual(["clean"], self.github.labels(), "a suggestion is no finding, so the head is signed off")

    def test_a_body_that_lists_suggestions_by_hand_publishes_as_written_and_is_counted(self) -> None:
        # Another host may run a skill that predates P3 entries and write the section itself.
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        text = LABELED["level 3 heading"].replace("2 finding threads: 2 resolved, 0 open", "{census}")

        code, _, err = self.publish("clean", body=self.body(text))

        self.assertEqual(0, code, err)
        self.assertIn("\n<!-- projector-suggestions v=1 count=3 -->\n### Suggestions\n", self.posted()["body"],
                      "the count goes directly before the body's own label")
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
        # A refused publish keeps the lock, so one setup serves every case. IdentityTests covers the other
        # spellings of one model; the context window here shows the collision check compares identities.
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        for model in ("claude-opus-5-5[1m]", None):
            with self.subTest(model=model):
                self.github.reviews = []
                self.add_verdict(model)

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

    def test_every_command_a_review_runs_takes_its_loop_and_model(self) -> None:
        skill = (Path(cli.__file__).resolve().parents[2] / "skills" / "review-pr" / "SKILL.md").read_text()
        sentence = re.search(r"Pass `--loop <session-id>` and `--model <model-id>` to each\s+`project review`\s+command "
                             r"you\s+run\s+on\s+the\s+pull\s+request: (.*?)\. ", skill, re.S)
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


def record_entry(review_id: int, published_at: str, repo: str = REPO, number: int = 1) -> dict:
    return {"repo": repo, "number": number, "sha": "a" * 40, "review_id": review_id, "verdict": "clean",
            "published_at": published_at, "base": "b" * 40, "from": None, "model": "claude-opus-5-5"}


class HandOverTests(PublishCase):
    """The watch daemon hands a review to another loop by clearing its lock. Each command touches the lock as it
    runs, so an untouched lock shows a stalled review; `settle`, `move`, and `publish` refuse once the lock names
    another loop; and a loop's record is written under a lock, so the daemon's copy at a hand-over loses no
    entry."""

    def age(self, lock: Path) -> None:
        """Leave the lock untouched for an hour, as a stalled review does."""
        then = time.time() - 3600
        os.utime(lock, (then, then))

    def assert_touched(self, lock: Path) -> None:
        self.assertGreater(lock.stat().st_mtime, time.time() - 60, f"{lock.name} was not touched")

    def age_on(self, call: str) -> None:
        """Age the review's locks each time gh is asked for `call`, as a slow step would leave them."""
        answer = review.run_gh

        def aging(args: list[str], token: str | None = None) -> str:
            if call in " ".join(args):
                for lock in self.folder().glob("*.lock"):
                    self.age(lock)
            return answer(args, token)

        patcher = mock.patch.object(review, "run_gh", aging)
        patcher.start()
        self.addCleanup(patcher.stop)

    def record(self, loop: str) -> Path:
        return self.state / "loops" / loop / "published.json"

    def hand_over(self) -> None:
        """What a hand-over from loop l1 to loop l2 does: the daemon clears the lock and copies the record, and the
        new owner sets up."""
        for lock in review.review_locks(REPO, 1, self.state):
            review.clear_lock(lock)
        review.copy_published("l1", "l2", REPO, 1, self.state)
        self.assertEqual(0, self.setup_review("--loop", "l2")[0])

    def take_over(self) -> None:
        """The hand-over as it leaves the files when it lands in the middle of a publish: the new owner's lock,
        state, and record, written without a call to GitHub."""
        for lock in review.review_locks(REPO, 1, self.state):
            review.clear_lock(lock)
        review.ensure_loop(self.state, "l2")
        state = self.read_state()
        state.pop("published", None)
        state.update(loop="l2", start_comment={"id": 4242, "created_at": "2026-09-27T01:13:00Z"})
        self.new_owner = {"state": state, "lock": {"sha": self.head, "loop": "l2"}}
        (self.folder() / "state.json").write_text(json.dumps(state))
        self.lock(self.head).write_text(json.dumps(self.new_owner["lock"]))

    def owned(self) -> dict[str, tuple[bytes, float]]:
        """The new owner's state, lock, and record, each with when it last changed."""
        return {path.name: (path.read_bytes(), path.stat().st_mtime)
                for path in (self.folder() / "state.json", self.lock(self.head), self.record("l2"))}

    def test_setup_touches_the_lock_once_its_slow_steps_are_done(self) -> None:
        self.age_on(f"repos/{REPO}/issues/1/comments")

        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        self.assert_touched(self.lock(self.head))

    def test_census_touches_the_lock_of_the_review_its_model_and_loop_name(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        lock = self.lock(self.head)
        self.age(lock)

        other = self.later("census", "--loop", "l2", "--model", self.model)
        untouched = lock.stat().st_mtime
        own = self.later("census", "--loop", "l1", "--model", self.model)

        self.assertEqual((0, 0), (other[0], own[0]), other[2] + own[2])
        self.assertLess(untouched, time.time() - 600, "census in another loop keeps no other loop's review alive")
        self.assert_touched(lock)

    def test_census_names_the_review_by_its_model_and_without_one_touches_none_of_several(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.assertEqual(0, self.as_model("gpt-5.5", "--loop", "l1")[0])
        claude, gpt = self.lock(self.head, "claude-opus-5-5"), self.lock(self.head, "gpt-5.5")
        self.age(claude)
        self.age(gpt)

        unnamed = self.later("census")
        ages = (claude.stat().st_mtime, gpt.stat().st_mtime)
        named = self.later("census", "--model", "gpt-5.5")

        self.assertEqual((0, 0), (unnamed[0], named[0]), unnamed[2] + named[2])
        self.assertTrue(unnamed[1].startswith("0 finding threads"), "census counts with no review picked")
        self.assertLess(max(ages), time.time() - 600, "census without --model picks none of two models")
        self.assertLess(claude.stat().st_mtime, time.time() - 600)
        self.assert_touched(gpt)

    def test_move_touches_the_new_heads_lock_once_it_moves(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.github.pr["head"]["sha"] = second = self.push_head("second head")
        self.age_on("-X PATCH")

        self.assertEqual(0, self.later("move", "--loop", "l1")[0])

        self.assert_touched(self.lock(second))

    def test_interdiff_touches_the_lock_of_its_own_loops_review(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        lock = self.lock(self.head)
        self.age(lock)

        other = self.later("interdiff", "--loop", "l2")
        untouched = lock.stat().st_mtime
        own = self.later("interdiff", "--loop", "l1")

        self.assertEqual((1, 1), (other[0], own[0]), "with no full review to build on, interdiff refuses")
        self.assertLess(untouched, time.time() - 600, "interdiff in another loop keeps no other loop's review alive")
        self.assert_touched(lock)

    def test_a_session_id_of_any_length_the_inbox_takes_is_a_loop_id(self) -> None:
        session = "s" + "0123456789" * 9 + "abcdefghi"
        too_long = "s" * 129

        set_up = self.setup_review("--loop", session)
        published = self.publish("clean", loop=session)
        refused = self.setup_review("--loop", too_long)

        self.assertEqual(100, len(session))
        self.assertEqual((0, 0), (set_up[0], published[0]), set_up[2] + published[2])
        self.assertEqual([901], [entry["review_id"] for entry in json.loads(self.record(session).read_text())])
        self.assertEqual(1, refused[0])
        self.assertIn("up to 128 letters, digits", refused[2])

    def test_publish_touches_the_lock_before_it_submits(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.age(self.lock(self.head))
        seen: list[float] = []
        self.github.before_post = lambda: seen.append(self.lock(self.head).stat().st_mtime)

        self.assertEqual(0, self.publish("clean")[0])

        self.assertGreater(seen[0], time.time() - 60)

    def test_publish_after_a_hand_over_refuses_and_leaves_the_new_owners_state_lock_and_record(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.hand_over()
        owned = self.owned()

        code, _, err = self.publish("clean", loop="l1")

        self.assertEqual(1, code)
        self.assertIn(f"the review of {REPO}#1 at {self.head[:7]} by model {self.model} was handed over to another "
                      "loop: its lock names loop l2, and this command runs in loop l1; stop and report this rather "
                      "than running `project review setup` again", err)
        self.assertEqual([], self.github.reviews)
        self.assertEqual(owned, self.owned())

    def test_publish_in_a_loop_whose_lock_is_gone_refuses_as_handed_over(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        for lock in review.review_locks(REPO, 1, self.state):
            review.clear_lock(lock)

        code, _, err = self.publish("clean")

        self.assertEqual(1, code)
        self.assertIn("was handed over to another loop: its lock is gone; stop and report this", err)
        self.assertEqual([], self.github.reviews)

    def test_move_after_a_hand_over_refuses_before_it_takes_a_lock_or_edits_the_start_comment(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.hand_over()
        owned, comments = self.owned(), dict(self.github.comments)
        self.github.pr["head"]["sha"] = second = self.push_head("second head")

        code, _, err = self.later("move", "--loop", "l1")

        self.assertEqual(1, code)
        self.assertIn("was handed over to another loop: its lock names loop l2, and this command runs in loop l1",
                      err)
        self.assertFalse(self.lock(second).exists())
        self.assertEqual(comments, self.github.comments)
        self.assertEqual(owned, self.owned())

    def test_a_review_outside_any_loop_moves_and_publishes_as_before(self) -> None:
        self.assertEqual(0, self.setup_review()[0])
        self.github.pr["head"]["sha"] = second = self.push_head("second head")

        moved = self.later("move")
        published = self.publish("clean", loop=None)

        self.assertEqual((0, 0), (moved[0], published[0]), moved[2] + published[2])
        self.assertFalse(self.lock(second).exists())
        self.assertFalse((self.state / "loops").exists())

    def test_without_loop_a_command_runs_in_the_loop_setup_recorded(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        code, _, err = self.publish("clean", loop=None)

        self.assertEqual(0, code, err)
        self.assertEqual([901], [entry["review_id"] for entry in json.loads(self.record("l1").read_text())])

    def test_a_publish_handed_over_once_it_submitted_writes_no_state_and_keeps_the_new_lock(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.github.after_post = self.take_over

        code, _, err = self.publish("clean")

        self.assertEqual(1, code)
        self.assertIn("was handed over to another loop after review 901 was submitted: its lock names loop l2, "
                      "and this command runs in loop l1", err)
        self.assertEqual(1, len(self.github.reviews), "the verdict submitted before the hand-over stands")
        self.assertEqual(self.new_owner["state"], self.read_state())
        self.assertEqual(self.new_owner["lock"], json.loads(self.lock(self.head).read_text()))
        self.assertEqual([], json.loads(self.record("l1").read_text()))

    def test_publish_checks_the_lock_again_before_its_final_writes(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        answer = review.run_gh

        def handed_over_while_labeling(args: list[str], token: str | None = None) -> str:
            if args[:2] == ["pr", "edit"]:
                self.take_over()
            return answer(args, token)

        with mock.patch.object(review, "run_gh", handed_over_while_labeling):
            code, _, err = self.publish("clean")

        self.assertEqual(1, code)
        self.assertIn("was handed over to another loop after review 901 was submitted", err)
        self.assertEqual(self.new_owner["state"], self.read_state())
        self.assertEqual(self.new_owner["lock"], json.loads(self.lock(self.head).read_text()))
        self.assertEqual([], json.loads(self.record("l2").read_text()))
        self.assertEqual([901], [entry["review_id"] for entry in json.loads(self.record("l1").read_text())],
                         "the stalled loop's own record keeps the review it submitted")

    def test_the_new_owner_rereviews_a_head_the_former_owner_reviewed_with_the_record_copied(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.assertEqual(0, self.publish("clean", loop="l1")[0])
        self.assertEqual(0, self.setup_review("--loop", "l2", "--rereview")[0])
        uncopied = self.publish("clean", loop="l2")

        copied = review.copy_published("l1", "l2", REPO, 1, self.state)
        accepted = self.publish("clean", loop="l2")

        self.assertEqual(1, uncopied[0])
        self.assertIn("that loop l2 did not publish", uncopied[2])
        self.assertEqual(1, copied)
        self.assertEqual(0, accepted[0], accepted[2])
        self.assertEqual([901, 902], [entry["review_id"] for entry in json.loads(self.record("l2").read_text())])

    def test_a_copy_racing_a_publish_loses_no_entry(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l2")[0])
        self.record("l1").parent.mkdir(parents=True)
        self.record("l1").write_text(json.dumps([record_entry(700, "2026-09-26T00:00:00+00:00")]))
        write, copied, copiers = review.write_json, [], []

        def copy_mid_write(path: Path, value: object) -> None:
            if path == self.record("l2") and not copiers and threading.current_thread() is threading.main_thread():
                copier = threading.Thread(target=lambda: copied.append(
                    review.copy_published("l1", "l2", REPO, 1, self.state)))
                copiers.append(copier)
                copier.start()
                # Long enough for an unlocked copy to read the record and write it back before this write lands.
                copier.join(0.2)
            write(path, value)

        with mock.patch.object(review, "write_json", copy_mid_write):
            code, _, err = self.publish("clean", loop="l2")
            copiers[0].join()

        self.assertEqual(0, code, err)
        self.assertEqual([1], copied)
        self.assertEqual([700, 901], [entry["review_id"] for entry in json.loads(self.record("l2").read_text())])

    def test_review_locks_lists_each_models_lock_on_the_pull_request(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.assertEqual(0, self.as_model("gpt-5.5")[0])

        locks = review.review_locks("ACME/App", 1, self.state)

        self.assertEqual([(REPO, 1, "claude-opus-5-5", self.head, "l1"), (REPO, 1, "gpt-5.5", self.head, None)],
                         [(lock.repo, lock.number, lock.model_key, lock.sha, lock.loop) for lock in locks])
        self.assertEqual([lock.path.stat().st_mtime for lock in locks], [lock.modified for lock in locks])
        self.assertEqual([self.lock(self.head, "claude-opus-5-5"), self.lock(self.head, "gpt-5.5")],
                         [lock.path for lock in locks])
        self.assertEqual([], review.review_locks(REPO, 2, self.state))
        self.assertEqual([], review.review_locks(REPO, 1, self.state / "nowhere"))

    def test_clear_lock_removes_the_lock_it_read_and_leaves_one_another_loop_took_since(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        [lock] = review.review_locks(REPO, 1, self.state)

        review.clear_lock(lock)
        review.clear_lock(lock)
        cleared = lock.path.exists()
        lock.path.write_text(json.dumps({"sha": self.head, "loop": "l2"}))
        review.clear_lock(lock)

        self.assertFalse(cleared)
        self.assertTrue(lock.path.exists(), "a lock another loop took since the daemon read it stays")

    def test_clear_lock_waits_for_a_publish_checking_the_lock(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        [lock] = review.review_locks(REPO, 1, self.state)
        clearing = threading.Thread(target=review.clear_lock, args=(lock,))

        with review.review_mutex(self.folder()):
            clearing.start()
            clearing.join(0.1)
            held = lock.path.exists()
        clearing.join()

        self.assertTrue(held, "the lock stays while the mutex is held")
        self.assertFalse(lock.path.exists())

    def test_copy_published_copies_the_pull_requests_new_entries_in_published_order(self) -> None:
        l1 = [record_entry(700, "2026-09-26T01:00:00+00:00"), record_entry(701, "2026-09-26T02:00:00+00:00", "acme/other"),
              record_entry(702, "2026-09-26T03:00:00+00:00", number=2), record_entry(704, "2026-09-25T00:00:00+00:00",
                                                                                      "ACME/App")]
        for loop, entries in (("l1", l1), ("l2", [record_entry(700, "2026-09-26T01:00:00+00:00"),
                                                   record_entry(703, "2026-09-26T04:00:00+00:00")])):
            self.record(loop).parent.mkdir(parents=True)
            self.record(loop).write_text(json.dumps(entries))

        first = review.copy_published("l1", "l2", REPO, 1, self.state)
        again = review.copy_published("l1", "l2", REPO, 1, self.state)
        missing = review.copy_published("l9", "l3", REPO, 1, self.state)

        self.assertEqual((1, 0, 0), (first, again, missing))
        self.assertEqual([704, 700, 703], [entry["review_id"] for entry in json.loads(self.record("l2").read_text())])
        self.assertFalse((self.state / "loops" / "l3").exists())
        self.assertFalse((self.state / "loops" / "l9").exists())


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
        # A second setup while the first head's lock is still held, as a restarted session runs, keeps that
        # head's worktree, so the state lists two.
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.github.pr["head"]["sha"] = self.push_head("second head")
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.assertEqual(0, self.publish("clean")[0])
        made = [Path(path) for path in self.read_state()["worktrees"]]
        record = (self.state / "loops" / "l1" / "published.json").read_text()

        code, out, err = self.finish("--repo", REPO, "--loop", "l1", cwd=Path(tempfile.mkdtemp()))

        self.assertEqual(0, code, err)
        self.assertEqual(2, len(made), "the held lock's worktree and the current head's")
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

        self.assertEqual(["reviewing"], self.github.labels())

        code, out, err = self.finish("--force")

        self.assertEqual(0, code, err)
        self.assertEqual({}, self.github.comments)
        self.assertEqual([(["api", "-X", "DELETE", f"repos/{REPO}/issues/comments/101"], "token-operator")],
                         self.deletions())
        self.assertEqual([], self.github.labels(), "no review is in progress any more")
        self.assertIn("removed the reviewing label", out)
        self.assertFalse(worktree.exists())
        self.assertFalse(self.folder().exists(), "the lock and the state are gone")

    def test_force_leaves_reviewing_while_another_models_review_is_in_progress(self) -> None:
        self.github.comment(review.start_comment("gpt-5.5", self.head))
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        code, _, err = self.finish("--force")

        self.assertEqual(0, code, err)
        self.assertEqual(["reviewing"], self.github.labels(), "gpt-5.5 is still reviewing")

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
                          "released": [], "state": False, "labels": None}, json.loads(out))
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
        self.github.rules = [review.REVIEW_CONTEXT]
        self.github.fail = "statuses/"
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


class SupersededWorktreeTests(PublishCase):
    """A review keeps one scratch worktree, its current head's, so a long-lived pull request does not keep a
    full checkout of every head it ever had."""

    def registered(self) -> set[Path]:
        listing = git(self.checkout, "worktree", "list", "--porcelain").splitlines()
        return {Path(line.removeprefix("worktree ")).resolve() for line in listing if line.startswith("worktree ")}

    def test_setup_of_the_next_head_removes_the_published_heads_worktree(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.assertEqual(0, self.publish("clean")[0])
        old = Path(self.read_state()["worktree"])
        self.github.pr["head"]["sha"] = self.push_head("second head")

        code, out, err = self.setup_review("--loop", "l1")

        self.assertEqual(0, code, err)
        state = self.read_state()
        self.assertFalse(old.exists())
        self.assertNotIn(old.resolve(), self.registered())
        self.assertEqual([state["worktree"]], state["worktrees"])
        self.assertTrue(Path(state["worktree"]).is_dir())
        self.assertIn(f"removed the earlier head's worktree {old}\n", out)
        self.assertNotIn("removed", state, "what setup removed is reported, not recorded")

    def test_move_removes_the_worktree_of_the_head_it_moves_from(self) -> None:
        self.assertEqual(0, self.setup_review()[0])
        old = Path(self.read_state()["worktree"])
        self.github.pr["head"]["sha"] = self.push_head("second head")

        code, out, err = self.later("move", "--json")

        self.assertEqual(0, code, err)
        self.assertFalse(old.exists())
        self.assertEqual([str(old)], json.loads(out)["removed"])
        self.assertEqual([self.read_state()["worktree"]], self.read_state()["worktrees"])

    def test_a_held_lock_keeps_its_heads_worktree_and_a_stale_one_does_not(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.assertEqual(0, self.publish("clean")[0])
        first, first_sha = Path(self.read_state()["worktree"]), self.head
        self.lock(first_sha).write_text(json.dumps({"taken_at": self.clock.isoformat()}))
        self.github.pr["head"]["sha"] = self.push_head("second head")

        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        self.assertTrue(first.is_dir(), "a review may still be reading a locked head's worktree")
        self.assertEqual([str(first), self.read_state()["worktree"]], self.read_state()["worktrees"])

        self.lock(first_sha).write_text(json.dumps({"taken_at": (self.clock - timedelta(days=2)).isoformat()}))
        self.github.pr["head"]["sha"] = self.push_head("third head")
        self.assertEqual(0, self.later("move")[0])

        self.assertFalse(first.exists(), "a lock older than a day is stale")

    def test_another_models_worktree_stays(self) -> None:
        self.assertEqual(0, self.as_model("gpt-5.5", "--loop", "gpt")[0])
        gpt = Path(self.read_state("gpt-5.5")["worktree"])
        self.assertEqual(0, self.as_model("claude-opus-5-5", "--loop", "claude")[0])
        self.github.pr["head"]["sha"] = self.push_head("second head")

        self.assertEqual(0, self.later("move", "--model", "claude-opus-5-5")[0])

        self.assertTrue(gpt.is_dir())

    def test_a_worktree_that_will_not_come_out_stays_listed_and_setup_succeeds(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.assertEqual(0, self.publish("clean")[0])
        old = Path(self.read_state()["worktree"])
        self.github.pr["head"]["sha"] = self.push_head("second head")

        with mock.patch.object(review, "remove_worktree", side_effect=OSError("busy")):
            code, out, err = self.setup_review("--loop", "l1")

        self.assertEqual(0, code, err)
        self.assertTrue(old.is_dir())
        self.assertEqual([str(old), self.read_state()["worktree"]], self.read_state()["worktrees"])
        self.assertNotIn("removed the earlier head's worktree", out)


class PruneTests(PublishCase):
    """`prune` sweeps the scratch worktrees no review needs, listing them until --apply removes them."""

    def prune(self, *extra: str) -> tuple[int, str, str]:
        return self.project("prune", "--state-dir", str(self.state), *extra, cwd=Path(tempfile.mkdtemp()))

    def registered(self) -> set[Path]:
        listing = git(self.checkout, "worktree", "list", "--porcelain").splitlines()
        return {Path(line.removeprefix("worktree ")).resolve() for line in listing if line.startswith("worktree ")}

    def worktree_at(self, path: Path, sha: str | None = None) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        git(self.checkout, "worktree", "add", "--quiet", "--detach", str(path), sha or self.base)
        return path

    def pull_folder(self, repo: str = REPO, number: int = 1) -> Path:
        return self.state / "worktrees" / repo / str(number)

    def test_lists_leftovers_without_apply_and_removes_them_with_it(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.assertEqual(0, self.publish("clean")[0])
        current = Path(self.read_state()["worktree"])
        key = review.model_key(self.model)
        earlier = self.worktree_at(self.pull_folder() / key / ("a" * 12))
        old_layout = self.worktree_at(self.pull_folder() / ("b" * 12))
        unreviewed = self.worktree_at(self.pull_folder() / review.model_key("gpt-5.5") / ("c" * 12))
        locked = self.worktree_at(self.pull_folder() / key / ("d" * 12))
        (self.folder() / f"{'d' * 40}.lock").write_text(json.dumps({"taken_at": self.clock.isoformat()}))

        code, out, err = self.prune()

        self.assertEqual(0, code, err)
        for path in (current, earlier, old_layout, unreviewed, locked):
            self.assertTrue(path.is_dir(), "a dry run removes nothing")
        self.assertIn(f"would remove {earlier}: the worktree of an earlier head\n", out)
        self.assertIn(f"would remove {old_layout}: made before each model kept its own worktree folder\n", out)
        self.assertIn(f"would remove {unreviewed}: no review of the pull request by this model is set up here\n",
                      out)
        self.assertIn(f"kept {current}: the current head of an open pull request's review\n", out)
        self.assertIn(f"kept {locked}: its head's lock is held\n", out)
        self.assertIn("3 scratch worktrees can go; run `project review prune --apply` to remove them", out)

        code, out, err = self.prune("--apply", "--json")

        self.assertEqual(0, code, err)
        result = json.loads(out)
        self.assertEqual({str(earlier), str(old_layout), str(unreviewed)}, {e["path"] for e in result["removed"]})
        self.assertEqual({str(current), str(locked)}, {e["path"] for e in result["kept"]})
        for path in (earlier, old_layout, unreviewed):
            self.assertFalse(path.exists())
            self.assertNotIn(path.resolve(), self.registered())
        self.assertFalse(unreviewed.parent.exists(), "an emptied model folder goes too")
        self.assertTrue(current.is_dir())
        self.assertTrue(locked.is_dir())

        code, out, err = self.prune()

        self.assertEqual(0, code, err)
        self.assertIn("there is nothing to prune", out)

    def test_a_closed_pull_requests_current_worktree_goes_and_an_unreadable_ones_stays(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.assertEqual(0, self.publish("clean")[0])
        current = Path(self.read_state()["worktree"])
        self.github.missing = True

        code, out, err = self.prune("--apply")

        self.assertEqual(0, code, err)
        self.assertTrue(current.is_dir())
        self.assertIn(f"kept {current}: GitHub could not say whether {REPO}#1 is open", out)

        self.github.missing = False
        self.github.pr["state"] = "closed"
        code, out, err = self.prune("--apply")

        self.assertEqual(0, code, err)
        self.assertFalse(current.exists())
        self.assertIn(f"removed {current}: {REPO}#1 is closed; `project review finish 1 --repo {REPO} --force` "
                      "ends its review\n", out)
        self.assertTrue((self.folder() / "state.json").is_file(), "prune leaves the review's state to finish")

    def test_a_review_set_up_while_prune_asks_github_keeps_its_worktree(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.assertEqual(0, self.publish("clean")[0])
        self.github.pr["head"]["sha"] = self.push_head("second head")
        lookup, pending = review.read_pull_request, [True]

        def set_up_meanwhile(slug: str, number: int) -> dict:
            # Another loop sets this model's review up on the new head and publishes it, which releases its
            # lock, and a second model sets up its first review and still holds its lock.
            if pending:
                pending.clear()
                self.assertEqual(0, self.setup_review("--loop", "l1")[0])
                self.assertEqual(0, self.publish("clean")[0])
                self.assertEqual(0, self.as_model("gpt-5.5", "--loop", "gpt")[0])
            return lookup(slug, number)

        with mock.patch.object(review, "read_pull_request", side_effect=set_up_meanwhile):
            code, out, err = self.prune("--apply")

        self.assertEqual(0, code, err)
        published = Path(self.read_state("claude-opus-5-5")["worktree"])
        locked = Path(self.read_state("gpt-5.5")["worktree"])
        self.assertTrue(published.is_dir(), out)
        self.assertTrue(locked.is_dir(), out)
        self.assertIn(f"kept {published}: the current head of a review set up while prune ran\n", out)
        self.assertIn(f"kept {locked}: its head's lock is held\n", out)

    def test_repo_limits_the_sweep(self) -> None:
        elsewhere = self.state / "worktrees" / "other" / "app" / "5" / "old-model" / ("e" * 12)
        elsewhere.mkdir(parents=True)
        old_layout = self.worktree_at(self.pull_folder() / ("b" * 12))

        code, out, err = self.prune("--repo", REPO)

        self.assertEqual(0, code, err)
        self.assertIn(f"run `project review prune --apply --repo {REPO}`", out)
        self.assertNotIn("other/app", out)

        code, out, err = self.prune("--repo", REPO, "--apply")

        self.assertEqual(0, code, err)
        self.assertFalse(old_layout.exists())
        self.assertTrue(elsewhere.is_dir())

        code, out, err = self.prune("--apply")

        self.assertEqual(0, code, err)
        self.assertFalse(elsewhere.exists(), "a folder that is no worktree goes too")
        self.assertFalse((self.state / "worktrees" / "other").exists())

    def test_a_file_or_a_link_where_worktrees_live_is_kept_and_named_as_no_worktree(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.assertEqual(0, self.publish("clean")[0])
        key = review.model_key(self.model)
        summary = self.pull_folder() / "summary-1.json"
        summary.write_text('{"version": 1}\n')
        note = self.pull_folder() / key / "notes.md"
        note.write_text("notes\n")
        target = Path(tempfile.mkdtemp())
        (target / "keep.txt").write_text("keep\n")
        link = self.pull_folder() / "linked"
        link.symlink_to(target, target_is_directory=True)
        broken = self.pull_folder() / ("f" * 12)
        (broken / "src").mkdir(parents=True)

        code, out, err = self.prune("--apply")

        self.assertEqual(0, code, err)
        self.assertEqual('{"version": 1}\n', summary.read_text())
        self.assertEqual("notes\n", note.read_text())
        self.assertTrue(link.is_symlink())
        self.assertEqual("keep\n", (target / "keep.txt").read_text())
        self.assertIn(f"kept {summary}: a file, not a worktree; prune removes only worktree folders\n", out)
        self.assertIn(f"kept {note}: a file, not a worktree; prune removes only worktree folders\n", out)
        self.assertIn(f"kept {link}: a link, not a worktree; prune removes only worktree folders\n", out)
        self.assertIn(f"removed {broken}: made before each model kept its own worktree folder\n", out)
        self.assertFalse(broken.exists(), "a short-SHA folder that lost its .git file is an old worktree")
        self.assertIn("removed 1 scratch worktree\n", out)

    def test_with_no_state_directory_there_is_nothing_to_prune(self) -> None:
        code, out, err = self.prune("--apply")

        self.assertEqual(0, code, err)
        self.assertEqual("no scratch worktree is left over; there is nothing to prune\n", out)
        self.assertEqual([], self.github.calls)


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

    def test_settle_touches_the_heads_lock(self) -> None:
        then = time.time() - 3600
        os.utime(self.lock(self.head), (then, then))

        code, _, err = self.settle("fixed", "--loop", "l1")

        self.assertEqual(0, code, err)
        self.assertGreater(self.lock(self.head).stat().st_mtime, time.time() - 60)

    def test_settle_after_a_hand_over_refuses_and_posts_nothing(self) -> None:
        self.assertEqual(0, self.later("release", "--repo", REPO)[0])
        self.assertEqual(0, self.setup_review("--loop", "l2")[0])

        code, _, err = self.settle("fixed", "--loop", "l1")

        self.assertEqual(1, code)
        self.assertIn("was handed over to another loop: its lock names loop l2, and this command runs in loop l1",
                      err)
        self.assertEqual([], self.mutations())


def iso(at: datetime) -> str:
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")


class LatestWinsCase(PublishCase):
    """Publishing as claude-opus-5-5 on head H, where gpt-5.5 may have reviewed H too."""

    def json_publish(self, verdict: str, *extra: str, loop: str = "l1") -> dict:
        code, out, err = self.publish(verdict, "--json", *extra, loop=loop)
        self.assertEqual(0, code, err)
        return json.loads(out)


class LatestWinsTests(LatestWinsCase):
    def test_a_clean_newest_verdict_signs_it_off_over_an_older_models_change_request(self) -> None:
        older = self.add_verdict("gpt-5.5", verdict="changes-requested")
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        result = self.json_publish("clean")

        self.assertEqual(("claude-opus-5-5", "clean", True),
                         (result["newest"]["model"], result["newest"]["verdict"], result["signed_off"]))
        self.assertEqual(result["review_id"], result["newest"]["review"])
        self.assertEqual(["clean"], self.github.labels(), f"gpt-5.5's review {older} is older, so it no longer counts")

    def test_a_newer_change_request_takes_the_clean_label_off(self) -> None:
        self.add_verdict("gpt-5.5")
        self.github.repo_labels.add("clean")
        self.github.pr.update(draft=False, labels=[{"name": "clean"}])
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.assertEqual(["clean", "reviewing"], self.github.labels(), "gpt-5.5's clean verdict supports clean")

        result = self.json_publish("changes-requested", "--threads", str(self.threads(finding())))

        self.assertEqual(("changes-requested", False), (result["newest"]["verdict"], result["signed_off"]))
        self.assertEqual([], self.github.labels())
        self.assertFalse(self.github.pr["draft"], "draft state is the author's")

    def test_a_change_request_posted_after_this_clean_verdict_wins(self) -> None:
        # gpt-5.5 submits while claude-opus-5-5 is between its own submit and its sign-off.
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        newer = []
        self.github.after_post = lambda: newer.append(
            self.add_verdict("gpt-5.5", verdict="changes-requested", at=iso(self.clock + timedelta(seconds=1))))

        code, out, err = self.publish("clean")

        self.assertEqual(0, code, err)
        self.assertEqual([], self.github.labels(), "the newest verdict requests changes")
        self.assertIn(f"the newest verdict on the head is gpt-5.5's changes-requested, review {newer[0]}", out)
        self.assertIn(f"{self.head[:7]} is not signed off", out)

    def test_two_models_that_publish_together_leave_the_later_ones_sign_off(self) -> None:
        # A newer change request winning is test_a_change_request_posted_after_this_clean_verdict_wins; here the
        # later verdict is clean, and claude-opus-5-5's sign-off must leave gpt-5.5's clean label on.
        self.assertEqual(0, self.as_model("claude-opus-5-5", "--loop", "claude")[0])
        self.assertEqual(0, self.as_model("gpt-5.5", "--loop", "gpt")[0])

        def gpt_publishes() -> None:
            # A second later, between claude-opus-5-5's submit and its sign-off.
            self.clock += timedelta(seconds=1)
            self.assertEqual(0, self.publish("clean", "--model", "gpt-5.5", loop="gpt")[0])

        self.github.after_post = gpt_publishes
        result = self.json_publish("clean", "--model", "claude-opus-5-5", loop="claude")

        self.assertEqual(("gpt-5.5", "clean", True),
                         (result["newest"]["model"], result["newest"]["verdict"], result["signed_off"]))
        self.assertEqual(["clean"], self.github.labels())

    def test_a_clean_verdict_needs_another_models_finding_settled_first(self) -> None:
        self.github.pages = [page([thread("T1", FINDING.replace(" -->", " model=gpt-5.5 -->") % self.head)], None)]
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        code, _, err = self.publish("clean")

        self.assertEqual(1, code)
        self.assertIn("a clean verdict needs no open finding, and 1 are open", err)
        self.assertEqual([], self.github.reviews)

    def test_a_finding_another_model_posts_mid_publish_keeps_the_head_from_signing_off(self) -> None:
        # DismissalTests.test_a_finding_another_model_posts_mid_publish_holds_the_change_requests checks the
        # text this prints.
        self.github.rules = [review.REVIEW_CONTEXT]
        self.assertEqual(0, self.as_model("claude-opus-5-5", "--loop", "claude")[0])
        self.assertEqual(0, self.as_model("gpt-5.5", "--loop", "gpt")[0])

        def gpt_publishes() -> None:
            # After claude-opus-5-5 counted no open finding, and a second before its clean verdict lands.
            code, _, err = self.publish("changes-requested", "--model", "gpt-5.5", "--threads",
                                        str(self.threads(finding())), loop="gpt")
            self.assertEqual(0, code, err)
            self.assertEqual(["reviewing"], self.github.labels(),
                             "claude-opus-5-5's review is still in progress, so reviewing stays")
            marker = self.github.reviews[-1]["comments"][0]["body"].splitlines()[0]
            self.github.pages = [page([thread("T1", marker)], None)]
            self.clock += timedelta(seconds=1)

        self.github.before_post = gpt_publishes
        result = self.json_publish("clean", "--model", "claude-opus-5-5", loop="claude")

        self.assertEqual([], self.github.labels(), "gpt-5.5's open finding keeps clean off, and no review is "
                                                   "in progress")
        self.assertEqual(["failure", "failure"], [s["state"] for s in self.github.statuses])
        self.assertEqual(("claude-opus-5-5", "clean"),
                         (result["newest"]["model"], result["newest"]["verdict"]), "the clean verdict is newest")
        self.assertEqual((False, 1), (result["signed_off"], result["open_threads"]))

    def test_the_review_read_back_counts_when_the_listing_lags(self) -> None:
        self.add_verdict("gpt-5.5", verdict="changes-requested")
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.github.after_post = lambda: self.github.unlisted.add(self.github.reviews[-1]["id"])

        result = self.json_publish("clean")

        self.assertEqual((result["review_id"], True), (result["newest"]["review"], result["signed_off"]))

    def test_a_verdict_from_an_account_the_repository_does_not_let_in_does_not_count(self) -> None:
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.github.after_post = lambda: self.add_verdict(
            "gpt-5.5", verdict="changes-requested", login="passer-by", association="NONE",
            at=iso(self.clock + timedelta(seconds=1)))

        result = self.json_publish("clean")

        self.assertEqual(("claude-opus-5-5", True), (result["newest"]["model"], result["signed_off"]))

    def test_in_a_private_or_internal_repository_every_verdict_counts(self) -> None:
        # Only an account that can see the repository can review there, and GitHub hides a private organization
        # membership from a token that cannot see it, reporting a lower association.
        # ReviewCountsTests covers each visibility and association; this shows publish passes the visibility on.
        self.github.pr["base"]["repo"]["visibility"] = "private"
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        self.github.after_post = lambda: self.add_verdict(
            "gpt-5.5", verdict="changes-requested", login="teammate", association="CONTRIBUTOR",
            at=iso(self.clock + timedelta(seconds=1)))

        result = self.json_publish("clean")

        self.assertEqual(("gpt-5.5", False), (result["newest"]["model"], result["signed_off"]))

    def test_a_cross_author_review_approves_on_its_own_clean_verdict_and_leaves_draft_state_alone(self) -> None:
        self.github.pr.update(user={"login": "teammate"}, draft=False)
        self.github.permissions = {"teammate": "write"}
        self.github.commit_authors = ["teammate"]
        (self.checkout / ".projector.toml").write_text("[review]\nallow_approve = true\n")
        self.add_verdict("gpt-5.5", verdict="changes-requested")
        # Publish waits for no other model: neither codex-mini's live start comment on the head nor its newer
        # verdict on an earlier head holds the sign-off.
        self.github.comment(review.start_comment("codex-mini", self.head))
        self.add_verdict("codex-mini", sha=self.base, verdict="changes-requested", at="2026-09-27T01:30:00Z")
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        result = self.json_publish("clean")

        self.assertEqual(("APPROVE", True), (self.posted()["event"], result["signed_off"]))
        self.assertFalse(self.github.pr["draft"], "another author's draft state is theirs")
        self.assertFalse(any(args[:2] == ["pr", "ready"] for args, _ in self.github.calls))
        self.assertEqual(["reviewing", "clean"], self.github.labels(), "codex-mini's review is still in progress")


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
        self.assertEqual(["1 finding thread is open on the head, so it is not signed off",
                          f"{self.head[:7]} is not signed off", "removed the reviewing label"], out.splitlines()[1:])
        self.assertEqual("CHANGES_REQUESTED", self.review_state(self.request))
        self.assertEqual([], self.dismissals())

    def test_an_approval_replaces_the_change_request_so_nothing_is_dismissed(self) -> None:
        self.github.pr["draft"] = False
        (self.checkout / ".projector.toml").write_text("[review]\nallow_approve = true\n")
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])

        result = self.json_publish("clean")

        self.assertEqual(("APPROVE", []), (result["event"], result["dismissed"]))
        self.assertEqual("CHANGES_REQUESTED", self.review_state(self.request))
        self.assertEqual([], self.dismissals())

    def test_a_failed_dismissal_keeps_the_lock_and_a_retry_finishes_it(self) -> None:
        # A failed dismissal keeps the lock and posts no second review, so each failure retries the one before.
        self.assertEqual(0, self.setup_review("--loop", "l1")[0])
        cases = {"refused": (lambda: setattr(self.github, "fail", "dismissals"),
                             f"could not dismiss change request {self.request}"),
                 "still standing": (lambda: setattr(self.github, "ignore_dismissals", True),
                                    f"change request {self.request} still stands after its dismissal")}
        for name, (arrange, why) in cases.items():
            with self.subTest(name):
                self.github.fail, self.github.ignore_dismissals = None, False
                arrange()

                code, _, err = self.publish("clean")

                self.assertEqual(1, code)
                self.assertIn(why, err)
                self.assertIn(f"dismiss it at {review_page(self.request)} and run publish again", err)
                self.assertTrue(self.lock(self.head).exists())
                self.assertEqual(1, len(self.github.comments), "the start comment stays until the retry")
                self.assertEqual("CHANGES_REQUESTED", self.review_state(self.request))

        self.github.fail, self.github.ignore_dismissals = None, False
        code, out, err = self.publish("clean")

        self.assertEqual(0, code, err)
        self.assertEqual(2, len(self.github.reviews), "the retries post no second review")
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
                          "  1 open finding thread: src/x.py:4 (gpt-5.5)",
                          "  not signed off, a draft; no Projector label"], out.splitlines())
        self.assertEqual((REPO, 1, self.head, True, "clean", False, [], None),
                         (as_json["repo"], as_json["number"], as_json["head"], as_json["draft"], as_json["verdict"],
                          as_json["signed_off"], as_json["labels"], as_json["check"]))
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
                          f"  claude-opus-5-5 (operator)   changes-requested   review {mine}, 14:02 UTC",
                          "  signed off, a draft; no Projector label"],
                         out.splitlines())

    def test_one_model_prints_its_verdict_then_the_sign_off_with_projectors_labels_and_status(self) -> None:
        newest = self.add_verdict("claude-opus-5-5")
        self.github.pr.update(draft=False, labels=[{"name": "clean"}, {"name": "bug"}])
        self.github.statuses.append({"sha": self.head, "state": "success", "context": review.REVIEW_CONTEXT})

        self.assertEqual([f"#1 at {self.head[:7]}: clean from claude-opus-5-5, {review_page(newest)}",
                          f"  signed off; labeled clean; {review.REVIEW_CONTEXT} success"],
                         self.status()[1].splitlines())

    def test_shows_how_many_suggestions_each_review_lists_from_its_count_or_else_its_list(self) -> None:
        older = self.add_verdict("gpt-5.5", at="2026-09-27T14:02:00Z")
        newest = self.add_verdict("claude-opus-5-5", at="2026-09-27T14:20:00Z")
        counted, written = self.github.reviews
        counted["body"] += "\n<!-- projector-suggestions v=1 count=1 -->\nThe list this line counts is not read.\n"
        written["body"] += "\n" + LABELED["bold line"]

        code, out, err = self.status()
        result = json.loads(self.status("--json")[1])

        self.assertEqual(0, code, err)
        self.assertEqual([f"#1 at {self.head[:7]}: clean with 3 suggestions from claude-opus-5-5, "
                          f"{review_page(newest)}",
                          f"  claude-opus-5-5   clean               review {newest}, 14:20 UTC, 3 suggestions",
                          f"  gpt-5.5           clean               review {older}, 14:02 UTC, 1 suggestion",
                          "  signed off, a draft; no Projector label"],
                         out.splitlines())
        self.assertEqual([(newest, 3), (older, 1)], [(m["review"], m["suggestions"]) for m in result["models"]],
                         "a review with no count line, as 0.7.19 publishes, is counted by its list")
        self.assertEqual((3, 4), (result["newest"]["suggestions"], result["suggestions"]),
                         "the head's count is every model's, as `review suggestions` lists them")

    def test_fix_pr_reads_the_sign_off_status_prints_where_no_label_shows_it(self) -> None:
        # With review.labels off, or a label GitHub refused, only the status line says a head is signed off.
        # Every account the repository lets in counts (test_each_account_keeps_its_own_newest_verdict_by_a_model),
        # so fix-pr runs status once, with no --reviewer.
        self.add_verdict("claude-opus-5-5")
        self.github.pr["draft"] = False
        fix_pr = " ".join((Path(cli.__file__).resolve().parents[2] / "skills" / "fix-pr" / "SKILL.md").read_text()
                          .split())

        last = self.status()[1].splitlines()[-1]

        self.assertEqual("  signed off; no Projector label", last)
        self.assertIn("the last line of `project review status` says `signed off`", fix_pr)
        self.assertNotIn("carries the `clean` label, which is how a self-review signs off", fix_pr)
        self.assertIn("`project review status <number>` reports `clean`", fix_pr)
        self.assertNotIn("--reviewer", fix_pr)

    def test_an_account_the_repository_does_not_let_in_counts_only_as_the_reviewer(self) -> None:
        self.add_verdict("claude-opus-5-5", login="review-bot", association="NONE", verdict="changes-requested")

        mine = json.loads(self.status("--json")[1])
        bot = json.loads(self.status("--json", "--reviewer", "review-bot")[1])

        self.assertEqual(("unreviewed", None, []), (mine["verdict"], mine["newest"], mine["models"]))
        self.assertEqual(["#1 at " + self.head[:7] + ": unreviewed", "  not signed off, a draft; no Projector label"],
                         self.status()[1].splitlines())
        self.assertEqual("changes-requested", bot["verdict"])

    def test_in_a_private_or_internal_repository_every_account_counts(self) -> None:
        older = self.add_verdict("claude-opus-5-5", login="teammate", association="CONTRIBUTOR",
                                 at="2026-09-27T14:02:00Z")
        newest = self.add_verdict("gpt-5.5", login="review-bot", association="NONE",
                                  verdict="changes-requested", at="2026-09-27T14:20:00Z")
        for visibility in ("private", "internal", "public"):
            with self.subTest(visibility=visibility):
                self.github.pr["base"]["repo"]["visibility"] = visibility

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


class DraftTests(ReviewCase):
    """`project review hold` and `project review ready` on pull request 1, which the operator, signed in to gh,
    opened."""

    def run_draft(self, command: str, *args: str, cwd: Path | None = None) -> tuple[int, str, str]:
        return self.project(command, "1", *args, cwd=cwd)

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

    def drafts(self) -> list[list[str]]:
        return [args for args, _ in self.github.calls if args[:2] == ["pr", "ready"]]

    def test_hold_converts_a_ready_pull_request_to_a_draft_and_leaves_its_labels(self) -> None:
        self.github.pr.update(draft=False, labels=[{"name": "clean"}])

        code, out, err = self.run_draft("hold")

        self.assertEqual(0, code, err)
        self.assertTrue(self.github.pr["draft"])
        self.assertEqual([["pr", "ready", "1", "--repo", REPO, "--undo"]], self.drafts())
        self.assertEqual(["clean"], self.github.labels(), "a held pull request was signed off, and still is")
        self.assertEqual([f"converted {REPO}#1 to a draft at {self.head[:7]}"], out.splitlines())

    def test_ready_marks_a_draft_ready_for_review(self) -> None:
        code, out, err = self.run_draft("ready", "--json")

        self.assertEqual(0, code, err)
        self.assertFalse(self.github.pr["draft"])
        self.assertEqual([["pr", "ready", "1", "--repo", REPO]], self.drafts())
        self.assertEqual({"repo": REPO, "number": 1, "head": self.head, "draft": False, "changed": True}, json.loads(out))

    def test_names_the_head_github_shows_after_the_move_not_the_one_it_showed_before(self) -> None:
        # Right after a push, GitHub can still show the head before it, so the head the command first read is
        # stale, and naming it would say an older head was marked ready.
        pushed = "f" * 40
        self.github.after_ready = lambda: self.github.pr["head"].update(sha=pushed)

        code, out, err = self.run_draft("ready")

        self.assertEqual(0, code, err)
        self.assertEqual([f"marked {REPO}#1 ready for review at {pushed[:7]}"], out.splitlines())

    def test_a_state_already_reached_changes_nothing(self) -> None:
        for command, draft, line in (("hold", True, "is already a draft"), ("ready", False, "is already ready")):
            with self.subTest(command=command):
                self.github.pr["draft"] = draft
                self.github.calls.clear()

                code, out, err = self.run_draft(command)

                self.assertEqual(0, code, err)
                self.assertEqual([], self.drafts())
                self.assertIn(f"{REPO}#1 {line}", out)

    def test_a_state_that_does_not_read_back_exits_one(self) -> None:
        answer = self.github.__call__

        def ignored(args: list[str], token: str | None = None) -> str:
            return "" if args[:2] == ["pr", "ready"] else answer(args, token)

        with mock.patch.object(review, "run_gh", ignored):
            code, _, err = self.run_draft("ready")

        self.assertEqual(1, code)
        self.assertIn(f"{REPO}#1 is still a draft after `gh pr ready`; run `gh pr ready 1 --repo {REPO}`", err)

    def test_refuses_anyone_but_the_author(self) -> None:
        self.github.pr["user"] = {"login": "teammate"}
        for command in ("hold", "ready"):
            with self.subTest(command=command):
                code, _, err = self.run_draft(command)

                self.assertEqual(1, code)
                self.assertIn(f"gh is signed in as operator, and {REPO}#1 is teammate's; only the pull request's "
                              "author moves its draft state", err)
        self.assertEqual([], self.drafts())

    def test_refuses_a_pull_request_that_is_not_open(self) -> None:
        self.github.pr["state"] = "closed"

        code, _, err = self.run_draft("ready")

        self.assertEqual(1, code)
        self.assertIn(f"{REPO}#1 is closed; only an open pull request has a draft state to move", err)
        self.assertEqual([], self.drafts())

    def test_repo_wins_over_the_checkout_it_runs_in(self) -> None:
        elsewhere = self.checkout_of("other/app")
        self.assertEqual("other/app", review.named_repo(None, elsewhere, "to hold a pull request in"),
                         "the checkout alone names another repository")

        code, _, err = self.run_draft("ready", "--repo", REPO, cwd=elsewhere)

        self.assertEqual(0, code, err)
        self.assertEqual({REPO}, self.repos_called())

    def test_without_repo_it_acts_in_the_checkouts_origin(self) -> None:
        code, _, err = self.run_draft("hold", cwd=self.checkout)

        self.assertEqual(0, code, err)
        self.assertEqual({REPO}, self.repos_called())

    def test_refuses_without_repo_outside_a_checkout_with_a_github_origin(self) -> None:
        cases = {"outside a checkout": Path(tempfile.mkdtemp()), "a checkout with no origin": self.checkout_of(None)}
        for name, cwd in cases.items():
            for command in ("hold", "ready"):
                with self.subTest(name, command=command):
                    code, _, err = self.run_draft(command, cwd=cwd)

                    self.assertEqual(1, code)
                    self.assertIn("is not in a checkout whose origin is on GitHub", err)
                    self.assertIn("pass --repo owner/name", err)
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
    def refused(self, words: str, *extra: str) -> dict:
        result = self.interdiff("--model", self.model, *extra)
        self.assertFalse(result["incremental"])
        self.assertIn(words, result["reason"])
        self.assertFalse(result["needs_gate"])
        return result

    def test_each_rule_that_needs_no_other_setup_refuses_a_pushed_head_on_its_own(self) -> None:
        # One pushed head that every rule passes, as test_a_pushed_commit_counts_only_its_own_lines shows. Each
        # case changes one input interdiff reads, and the input is put back before the next case, so each
        # refusal comes from the rule its case names. Interdiff writes nothing but the lock's time.
        self.pushed()
        config = self.checkout / ".projector.toml"
        record = self.state / "loops" / "l1" / "published.json"
        saved = (record.read_text(), [dict(r) for r in self.github.reviews], self.github.pages)
        self.assertTrue(self.interdiff()["incremental"], "the head every case starts from passes every rule")

        def earlier_head_missing() -> None:
            self.edit_record(lambda entries: entries[-1].update(sha="f" * 40))
            self.github.reviews[-1]["body"] = self.github.reviews[-1]["body"].replace(self.reviewed, "f" * 40)

        cases = (
            ("review.incremental is false",
             lambda: config.write_text("[review]\nincremental = false\n"), ()),
            ("the newest verdict in loop l1's record requests changes",
             lambda: self.edit_record(lambda entries: entries[-1].update(verdict="changes-requested")), ()),
            ("loop l1's record holds no clean full review",
             lambda: self.edit_record(lambda entries: entries.clear()), ()),
            # A clean full review another loop on the same account published is not this loop's to build on.
            ("loop l2's record holds no clean full review", lambda: None, ("--loop", "l2")),
            ("the full review 901 of", lambda: self.github.reviews.clear(), ()),
            ("was made by Projector unknown, model claude-opus-5-5, no effort, and this run is "
             "Projector 1.2.3, model claude-opus-5-5, no effort",
             lambda: self.github.reviews[-1].update(
                 body=self.github.reviews[-1]["body"].replace(" projector=1.2.3 ", " ")), ()),
            ("1 finding thread is open", self.open_finding, ()),
            # An earlier release recorded no merge base, and a checkout that was never installed names every
            # release `unknown`, so the version rule cannot catch it.
            ("the change cannot be measured: loop l1's record holds no merge base for the full review",
             lambda: self.edit_record(lambda entries: entries[-1].pop("base")), ()),
            ("the change cannot be measured: the checkout lacks the earlier head fffffff", earlier_head_missing, ()),
        )
        for words, arrange, extra in cases:
            with self.subTest(words):
                arrange()
                try:
                    result = self.refused(words, *extra)
                    self.assertEqual((None, [], ""), (result["kind"], result["files"], result["patch"]),
                                     "refused before the change was measured")
                finally:
                    config.unlink(missing_ok=True)
                    record.write_text(saved[0])
                    self.github.reviews, self.github.pages = [dict(r) for r in saved[1]], saved[2]
        for value in ('"yes"', "1"):
            with self.subTest(incremental=value):
                config.write_text(f"[review]\nincremental = {value}\n")

                code, _, err = self.later("interdiff", "--json")

                self.assertNotEqual(0, code)
                self.assertIn("review.incremental must be true or false", err)
        config.unlink()
        self.assertTrue(self.interdiff()["incremental"], "every case put its input back")

    def test_a_review_no_loop_runs_is_full(self) -> None:
        self.push_feature(self.build(self.reviewed, {"a.css": css(r1=9, r2=9)}))
        self.assertEqual(0, self.setup_review()[0])

        self.refused("this review runs in no loop")

    def test_an_untrusted_head_is_full(self) -> None:
        self.github.commit_authors = ["operator", "stranger"]
        self.pushed()

        self.refused("is untrusted")

    def test_a_head_the_loop_already_judged_gets_a_full_rereview(self) -> None:
        self.review_head(self.reviewed, "--rereview")

        self.refused(f"loop l1 already published a verdict on {self.reviewed[:7]}")

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

    def test_a_moved_base_takes_a_gate_that_ci_passed(self) -> None:
        # Catches a CI result that the incremental gate rule does not read as a passing gate.
        (self.work / ".github" / "workflows").mkdir(parents=True)
        self.move_trunk({".github/workflows/check.yml": CHECK_YML})
        self.review_head(self.build(self.merge(self.reviewed, self.trunk), {"a.css": css(r1=9, r2=9)}))
        (self.checkout / ".projector.toml").write_text('[review]\ngate_checks = "check"\n')
        self.github.suites = {7: ".github/workflows/check.yml"}
        self.github.check_polls = [[check_run("success")]]

        before = self.interdiff()
        code, _, err = self.later("gate")

        self.assertTrue(before["needs_gate"])
        self.assertEqual(0, code, err)
        self.assertTrue(self.interdiff()["incremental"])

    def test_a_gate_that_passed_on_an_earlier_head_does_not_count(self) -> None:
        self.move_trunk({"trunk.txt": "moved\n"})
        merged = self.review_head(self.merge(self.reviewed, self.trunk))
        self.assertEqual(0, self.gate())
        self.push_feature(self.build(merged, {"a.css": css(r1=9, r2=9)}))
        self.assertEqual(0, self.later("move")[0])

        result = self.interdiff()

        self.assertTrue(result["needs_gate"])

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
        self.assertEqual(["clean"], self.github.labels(), "an incremental review signs the head off like a full one")
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

    def test_another_loops_incremental_review_on_the_head_is_a_collision(self) -> None:
        head = self.pushed()
        self.assertEqual(0, self.publish_incremental()[0])
        self.assertEqual(0, self.setup_review("--loop", "l2", "--rereview")[0])

        code, _, err = self.publish("clean", loop="l2")

        self.assertEqual(1, code)
        self.assertIn("another loop", err)
        self.assertTrue(self.lock(head).exists())


class KindPhraseTests(unittest.TestCase):
    def test_every_kind_has_one_phrase_that_the_output_and_the_lead_both_use(self) -> None:
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
    def test_an_incremental_review_brings_and_counts_the_suggestions_of_the_full_review_its_lead_links(self) -> None:
        # The setup's full review listed none; the re-review of the same head lists two and is the newest full
        # review, which the incremental review of a push links. The incremental review lists none of its own,
        # so a count of its own would read 0 while fix-pr still has the full review's two to decide.
        self.assertEqual(0, self.setup_review("--loop", "l1", "--rereview")[0])
        self.assertEqual(0, self.publish("clean", "--threads", str(self.threads(*P3)))[0])
        full = self.posted()["id"]
        head = self.pushed()
        self.assertEqual(0, self.publish_incremental()[0])
        incremental = self.posted()["id"]
        self.assertIn(f"({review_page(full)})", self.posted()["body"])
        self.assertIsNotNone(verdict_0_7_19(self.posted()["body"]), "0.7.19 reads the incremental verdict")
        self.assertNotIn("projector-suggestions", self.posted()["body"])

        code, out, err = self.project("suggestions", "1", "--repo", REPO, "--json")
        status = self.project("status", "1", "--repo", REPO)
        result = json.loads(self.project("status", "1", "--repo", REPO, "--json")[1])

        self.assertEqual(0, code, err)
        self.assertEqual([dict(item, url=review_page(full), sha=self.reviewed) for item in LISTED], json.loads(out),
                         f"the suggestions name the full review's head, not {head[:7]}")
        self.assertEqual(0, status[0], status[2])
        self.assertEqual([f"#1 at {head[:7]}: clean with 2 suggestions from claude-opus-5-5, "
                          f"{review_page(incremental)}", "  signed off, a draft; labeled clean"], status[1].splitlines())
        self.assertEqual((incremental, 2, 2),
                         (result["newest"]["review"], result["newest"]["suggestions"], result["suggestions"]))


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


class SkillTextTests(unittest.TestCase):
    """The review skills act through Projector's commands, which a host's automatic approval allows, rather than
    the raw operations it refuses."""

    SKILLS = Path(cli.__file__).resolve().parents[2] / "skills"

    def test_the_review_skills_clean_up_through_the_commands_alone(self) -> None:
        # Removing a worktree by hand leaves the state naming it, and a raw comment deletion is what Claude
        # Code's auto mode refuses, where one narrow rule allows the command.
        review_pr = (self.SKILLS / "review-pr" / "SKILL.md").read_text()
        review_repo = (self.SKILLS / "review-repo" / "SKILL.md").read_text()

        self.assertIn("`project review finish <number>`", review_pr)
        self.assertIn("`--replace <comment-id>`", review_pr)
        for text in (review_pr, review_repo):
            self.assertIn("`project review finish <number> --repo <owner>/<repo> --force`", " ".join(text.split()))
            self.assertNotIn("issues/comments", text)
            self.assertNotIn("git worktree", text)

    def test_review_pr_settles_threads_through_the_command_alone(self) -> None:
        # A hand-typed marker that misses `projector-verify` makes the reviewer's own reply read as the author
        # answering, which fires RESPONDED.
        skill = (self.SKILLS / "review-pr" / "SKILL.md").read_text()

        self.assertIn("project review settle <number> --thread <thread-id> --result <result> --body <file>", skill)
        for result in review.SETTLED:
            self.assertIn(f"`--result {result}`", skill)
        self.assertNotIn("resolveReviewThread", skill)
        self.assertNotIn("Every reply you post opens with", skill)

    def test_the_skills_move_draft_state_through_the_commands_alone(self) -> None:
        # A host's automatic approval can refuse a bare `gh pr ready` as self-approval, where the rule that
        # allows Projector's commands allows these.
        for name in ("fix-pr", "implement", "spec", "work", "cleanup"):
            text = (self.SKILLS / name / "SKILL.md").read_text()
            with self.subTest(skill=name):
                self.assertIn("project review ready <number> --repo <owner>/<repo>", text)
                commands = "\n".join(re.findall(r"^\s*```(?:sh|bash)\n(.*?)^\s*```", text, re.M | re.S))
                self.assertNotIn("gh pr ready", commands, "a skill runs no bare gh pr ready")
                prose = " ".join(text.split())
                self.assertIn("as a tool call of its own", prose)
        fix_pr = " ".join((self.SKILLS / "fix-pr" / "SKILL.md").read_text().split())
        self.assertIn("project review hold <number> --repo <owner>/<repo>", fix_pr)
        # Claude Code and Codex check each command of a chain and take the strictest result, so a `gh` command
        # beside `project review ready` sends the whole call to auto mode, which refused it as undoing the hold.
        self.assertIn("Run `project review hold` and `project review ready` each as a tool call of its own", fix_pr)
        self.assertIn("**The call ran more than the command.** Run the command again, alone.", fix_pr)
        # A lone refusal sends the user to `init` only when `review rules` says the file lacks the rules.
        self.assertIn("Run `project review rules --json`, which writes nothing, and read `allowed`", fix_pr)

    def test_no_skill_example_joins_a_review_command_to_another(self) -> None:
        # A joined line falls to the host's automatic approval even where `Bash(project *)` allows the command.
        for path in sorted(self.SKILLS.glob("*/SKILL.md")):
            text = path.read_text()
            for block in re.findall(r"^\s*```(?:sh|bash)\n(.*?)^\s*```", text, re.M | re.S):
                for line in block.splitlines():
                    if re.search(r"project (review (ready|hold|publish)|summary publish)\b", line):
                        with self.subTest(skill=path.parent.name, line=line.strip()):
                            # A placeholder such as `<clean|changes-requested>` names choices, not a pipe.
                            bare = re.sub(r"<[^>]*>", "", line)
                            self.assertIsNone(re.search(r";|&&|\|", bare), "the command runs on a line by itself")


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
