"""`project review run`: one head's review by an agent with no session, as docs/projects/review-in-ci/readme.md § 2
describes."""

from __future__ import annotations

import io
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Callable, Optional
from unittest import mock

from projector import cli, review, run
from projector.review import ReviewError

REPO = "acme/app"
HEAD = "a" * 40
MODEL = "claude-opus-5-5"
SKILLS = Path(cli.__file__).resolve().parents[2] / "skills"

# The agent `review.runner` names in these tests. It records the environment it saw, writes the files a `setup`
# would when its folder has `setup.json`, then does what the `behavior` file says: publish a verdict, write a
# report and publish nothing, or outlast its time.
AGENT = '''
import json, os, pathlib, sys, time
here = pathlib.Path(sys.argv[1])
(here / "seen.json").write_text(json.dumps({"env": dict(os.environ), "stdin": sys.stdin.read()}))
if (here / "setup.json").exists():
    for path, content in json.loads((here / "setup.json").read_text()).items():
        pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(path).write_text(json.dumps(content))
behavior = (here / "behavior").read_text().strip()
if behavior == "publish":
    (here / "published.json").write_text(json.dumps({"id": 950, "sha": os.environ["HEAD_SHA"]}))
elif behavior == "report":
    pathlib.Path(os.environ["PROJECTOR_RUN_REPORT"]).write_text("the plan names no gate; which one runs?\\n")
elif behavior == "sleep":
    time.sleep(60)
'''


def verdict_body(sha: str, model: str = MODEL) -> str:
    return (f"Projector review\n\n<!-- projector-review v=1 verdict=clean projector=1.2.3 model={model} effort=high "
            f"sha={sha} findings=0 seconds=60 covered=1/1 -->\n\nClean.")


class FakeGitHub:
    """The calls `project review run` makes, for pull request 1 of acme/app."""

    def __init__(self, agent_folder: Path) -> None:
        self.agent_folder = agent_folder
        self.pr = {"number": 1, "state": "open", "draft": False, "user": {"login": "operator"},
                   "head": {"sha": HEAD, "ref": "feature", "repo": {"full_name": REPO}},
                   "base": {"ref": "main", "repo": {"full_name": REPO}}}
        self.reviews: list[dict] = []
        self.starts: list[dict] = []
        self.posted: list[str] = []
        self.deleted: list[str] = []
        # The labels the repository has, each `gh pr edit` with whether the agent had started by then, and whether
        # GitHub refuses every edit.
        self.repo_labels: set[str] = set()
        self.edits: list[tuple[str, bool]] = []
        self.refuse_edits = False
        # Called when the run's cleanup lists start comments, after the agent ran.
        self.on_cleanup: Optional[Callable[[], None]] = None

    def listed_reviews(self) -> list[dict]:
        published = self.agent_folder / "published.json"
        found = list(self.reviews)
        if published.exists():
            made = json.loads(published.read_text())
            found.append({"id": made["id"], "url": f"https://github.com/{REPO}/pull/1#pullrequestreview-{made['id']}",
                          "at": "2026-11-18T12:00:00Z", "body": verdict_body(made["sha"])})
        return found

    def __call__(self, args: list[str], token: str | None = None) -> str:
        if args == ["api", f"repos/{REPO}/pulls/1"]:
            return json.dumps(self.pr)
        if args == ["api", "user", "--jq", ".login"]:
            return "operator\n"
        if args[:3] == ["auth", "token", "--user"]:
            return f"token-{args[3]}\n"
        if args[:3] == ["api", "--paginate", f"repos/{REPO}/pulls/1/reviews"]:
            return "".join(json.dumps(r) + "\n" for r in self.listed_reviews())
        if args[:3] == ["api", "--paginate", f"repos/{REPO}/issues/1/comments"]:
            if self.on_cleanup is not None and (self.agent_folder / "seen.json").exists():
                self.on_cleanup()
            return "".join(json.dumps(c) + "\n" for c in self.starts)
        if args[:2] == ["api", f"repos/{REPO}/issues/1/comments"] and args[2] == "-f":
            self.posted.append(args[3].removeprefix("body="))
            return "{}"
        if args[:3] == ["api", "-X", "DELETE"]:
            self.deleted.append(args[3])
            return ""
        if args[:2] == ["pr", "edit"]:
            add = [args[i + 1] for i, arg in enumerate(args) if arg == "--add-label"]
            remove = {args[i + 1] for i, arg in enumerate(args) if arg == "--remove-label"}
            if self.refuse_edits:
                raise ReviewError("HTTP 403: Resource not accessible by integration")
            if any(name not in self.repo_labels for name in add):
                raise ReviewError(f"'{add[0]}' not found")
            self.edits.append((" ".join(args[5:]), (self.agent_folder / "seen.json").exists()))
            kept = [label for label in self.pr.get("labels") or [] if label["name"] not in remove]
            self.pr["labels"] = kept + [{"name": name} for name in add]
            return ""
        if args[:4] == ["api", "-X", "POST", f"repos/{REPO}/labels"]:
            self.repo_labels.add(args[5].removeprefix("name="))
            return "{}"
        raise AssertionError(f"unexpected gh call: {args}")


class RunTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.tmp = Path(scratch.name)
        self.checkout = self.tmp / "checkout"
        subprocess.run(["git", "init", "-q", str(self.checkout)], check=True)
        subprocess.run(["git", "-C", str(self.checkout), "remote", "add", "origin", f"https://github.com/{REPO}.git"],
                       check=True)
        self.agent = self.tmp / "agent"
        self.agent.mkdir()
        (self.agent / "agent.py").write_text(AGENT)
        self.behave("publish")
        self.runner = shlex.join([sys.executable, str(self.agent / "agent.py"), str(self.agent)])
        self.github = FakeGitHub(self.agent)
        for patcher in (mock.patch.object(review, "run_gh", self.github),
                        mock.patch.dict(os.environ, {"HEAD_SHA": HEAD, "XDG_STATE_HOME": str(self.tmp / "state")})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def behave(self, behavior: str) -> None:
        (self.agent / "behavior").write_text(behavior)

    def review_run(self, **options: object) -> int:
        settings = dict(model=MODEL, agent="command", configured_runner=self.runner)
        settings.update(options)
        self.out, self.err = io.StringIO(), io.StringIO()
        with redirect_stdout(self.out), redirect_stderr(self.err):
            return run.run(self.tmp / "state" / "projector" / "reviews", 1, self.checkout, **settings)

    def seen(self) -> dict:
        return json.loads((self.agent / "seen.json").read_text())

    def started(self) -> bool:
        return (self.agent / "seen.json").exists()

    def test_a_published_verdict_passes(self) -> None:
        self.assertEqual(0, self.review_run())

        self.assertEqual([], self.github.posted)
        environment = self.seen()["env"]
        self.assertEqual(("1", "run", MODEL), (environment["PROJECTOR_HEADLESS"], environment["PROJECTOR_LOOP"],
                                               environment["PROJECTOR_MODEL"]))
        self.assertIn("review-pr skill to review pull request 1 in acme/app", self.seen()["stdin"])

    def test_run_refuses_a_fork_or_a_draft_before_it_starts_anything(self) -> None:
        # A manual dispatch can name any pull request, so the command, not the workflow's `if`, is the boundary.
        for change, why in ((lambda pr: pr["head"].update(repo={"full_name": "outsider/app"}), "outsider/app"),
                            (lambda pr: pr["head"].update(repo=None), "a deleted fork"),
                            (lambda pr: pr.update(draft=True), "it is a draft")):
            with self.subTest(why=why):
                self.setUp()
                change(self.github.pr)

                with self.assertRaisesRegex(ReviewError, why):
                    self.review_run()

                self.assertFalse(self.started(), "no agent reads an outsider's diff or a draft")
                self.assertEqual([], self.github.posted)

    def test_run_skips_a_head_the_reviewer_and_model_already_reviewed(self) -> None:
        self.github.reviews.append({"id": 900, "url": "https://example.test/900", "at": "2026-11-18T10:00:00Z",
                                    "body": verdict_body(HEAD)})

        self.assertEqual(0, self.review_run())
        self.assertFalse(self.started(), "a re-run of a finished job posts nothing")

        self.github.reviews[0]["body"] = verdict_body(HEAD, model="gpt-5.5")
        self.assertEqual(0, self.review_run())
        self.assertTrue(self.started(), "another model's verdict is a second opinion, not this review")

    def test_a_rereview_fails_unless_the_run_itself_publishes(self) -> None:
        # The earlier verdict is on the same head by the same model, so only a new review id shows this run's.
        self.github.reviews.append({"id": 900, "url": "https://example.test/900", "at": "2026-11-18T13:00:00Z",
                                    "body": verdict_body(HEAD)})
        self.behave("nothing")

        self.assertEqual(1, self.review_run(rereview=True))
        self.assertEqual("1", self.seen()["env"]["PROJECTOR_REREVIEW"])

    def test_a_verdict_on_a_head_pushed_during_the_run_passes(self) -> None:
        # The skill moves an unpublished review to a head pushed while it runs, so the agent publishes on the
        # newer head; a run that looked only at the head it started on failed a review that stood.
        newer = "b" * 40
        with mock.patch.dict(os.environ, {"HEAD_SHA": newer}):
            self.assertEqual(0, self.review_run())
        self.assertEqual([], self.github.posted)

        # A verdict on that newer head from before the run is still not this run's.
        self.setUp()
        self.github.reviews.append({"id": 900, "url": "https://example.test/900", "at": "2026-11-18T10:00:00Z",
                                    "body": verdict_body(newer)})
        self.behave("nothing")
        self.assertEqual(1, self.review_run())

    def test_run_replaces_a_cancelled_runs_start_comment(self) -> None:
        at = review.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        for comment_id, sha, model in ((301, HEAD, MODEL), (302, "b" * 40, MODEL), (303, HEAD, "gpt-5.5")):
            self.github.starts.append({"id": comment_id, "url": f"https://example.test/{comment_id}", "login": "operator",
                                       "body": review.start_comment(model, sha), "created_at": at, "updated_at": at})

        self.assertEqual(0, self.review_run())

        self.assertEqual("301", self.seen()["env"]["PROJECTOR_REPLACE"],
                         "only this model's comment on this head blocks the agent's setup")

    def test_run_fails_without_a_verdict_and_says_why_on_the_pull_request(self) -> None:
        for behavior, reason in (("nothing", "the agent exited 0 and published no verdict"),
                                 ("report", "the plan names no gate; which one runs?")):
            with self.subTest(behavior=behavior):
                self.setUp()
                self.behave(behavior)
                with mock.patch.dict(os.environ, {"GITHUB_SERVER_URL": "https://github.com",
                                                  "GITHUB_REPOSITORY": REPO, "GITHUB_RUN_ID": "77"}):
                    code = self.review_run()

                self.assertEqual(1, code)
                self.assertEqual(1, len(self.github.posted))
                self.assertIn(reason, self.github.posted[0])
                self.assertIn(f"https://github.com/{REPO}/actions/runs/77", self.github.posted[0])
                self.assertIn(f"sha={HEAD}", self.github.posted[0])

    def review_files(self, loop: str, start_comment: int) -> dict[str, dict]:
        """The state and lock a `setup` in `loop` writes for HEAD, by path."""
        paths = review.Paths(self.tmp / "state" / "projector" / "reviews", REPO, 1, MODEL)
        state = {"repo": REPO, "number": 1, "sha": HEAD, "checkout": str(self.checkout), "worktrees": [],
                 "reviewer": "operator", "model": MODEL, "identity": review.identity(MODEL), "loop": loop,
                 "start_comment": {"id": start_comment, "created_at": "2026-11-18T11:00:00Z"}}
        lock = {"sha": HEAD, "host": "runner", "loop": loop, "pid": 1, "taken_at": review.now().isoformat()}
        return {str(paths.state): state, str(paths.lock(HEAD)): lock}

    def test_a_failed_run_cleans_up_the_review_its_agent_set_up(self) -> None:
        # A setup killed between taking its lock and writing its state leaves only the lock, which the state
        # cache would carry into the next run and refuse its setup for a day.
        for kept, why in ((slice(None), "the unpublished review's state and lock are discarded"),
                          (slice(1, None), "a lock with no state is discarded")):
            with self.subTest(why=why):
                self.setUp()
                files = dict(list(self.review_files("run", 501).items())[kept])
                (self.agent / "setup.json").write_text(json.dumps(files))
                self.behave("nothing")

                self.assertEqual(1, self.review_run())

                self.assertEqual([], [path for path in files if Path(path).exists()], why)

    def test_a_failed_run_keeps_the_worktree_of_a_review_set_up_during_its_cleanup(self) -> None:
        # Cleanup released the head's lock before it removed the worktrees, so a setup for the same head that took
        # the lock while cleanup read GitHub recreated the worktree path, and cleanup then removed it.
        paths = review.Paths(self.tmp / "state" / "projector" / "reviews", REPO, 1, MODEL)
        worktree = paths.worktree(HEAD)
        files = self.review_files("run", 501)
        files[str(paths.state)]["worktrees"] = [str(worktree)]
        (self.agent / "setup.json").write_text(json.dumps(files))
        self.behave("nothing")
        worktree.mkdir(parents=True)

        released: list[bool] = []

        def next_setup() -> None:
            if not paths.lock(HEAD).exists():
                released.append(worktree.exists())
                review.take_lock(paths, HEAD, "laptop")
                worktree.mkdir(parents=True, exist_ok=True)
        self.github.on_cleanup = next_setup

        self.assertEqual(1, self.review_run())

        self.assertEqual([False], released, "the run's own worktree is gone by the time its lock is")
        self.assertTrue(worktree.is_dir(), "the next review's worktree stands")
        self.assertEqual("laptop", review.read_lock(paths.lock(HEAD)).loop)

    def test_a_failed_run_leaves_a_review_handed_over_during_it_alone(self) -> None:
        # The watch daemon cleared the run's lock and the new owner's setup took its own before writing the state,
        # so the state still names the run's loop; cleaning up from the state alone deleted the new owner's lock.
        paths = review.Paths(self.tmp / "state" / "projector" / "reviews", REPO, 1, MODEL)
        files = self.review_files("run", 501)
        files[str(paths.lock(HEAD))]["loop"] = "laptop"
        (self.agent / "setup.json").write_text(json.dumps(files))
        self.behave("nothing")

        self.assertEqual(1, self.review_run())

        self.assertEqual(list(files), [path for path in files if Path(path).exists()],
                         "the new owner keeps its lock and the state it is about to rewrite")
        self.assertEqual([], self.github.deleted)

    def test_a_failed_run_leaves_another_loops_live_review_alone(self) -> None:
        # Another loop holds this pull request and model's lock, so the agent's setup is refused and it publishes
        # nothing; cleaning up regardless deleted that loop's state, lock, and worktrees from under it.
        files = self.review_files("laptop", 400)
        for path, content in files.items():
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(json.dumps(content))
        self.behave("nothing")

        self.assertEqual(1, self.review_run())

        self.assertEqual(list(files), [path for path in files if Path(path).exists()])
        self.assertEqual([], self.github.deleted, "the other review's start comment stands")
        self.assertEqual(1, len(self.github.posted), "the run still says on the pull request that it failed")

    def test_an_agent_out_of_time_is_stopped_and_the_run_fails(self) -> None:
        self.behave("sleep")

        self.assertEqual(1, self.review_run(timeout_minutes=0.02))

        self.assertIn("ran out of its 0.02 minutes", self.github.posted[0])

    def test_a_windows_agent_out_of_time_is_killed_and_reaped(self) -> None:
        # Windows has no os.killpg or signal.SIGKILL, so a timeout that reached for them raised, left the agent
        # running, and skipped the run's cleanup and failure comment.
        process = mock.Mock(pid=4242, returncode=1)
        process.communicate.side_effect = subprocess.TimeoutExpired("agent", 1)
        cwd = Path.cwd()
        with mock.patch.object(subprocess, "Popen", return_value=process) as popen, \
                mock.patch.object(subprocess, "run") as taskkill, \
                mock.patch.object(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, create=True), \
                mock.patch.object(os, "killpg", side_effect=AttributeError("module 'os' has no attribute 'killpg'")), \
                mock.patch.object(os, "name", "nt"):
            result = run.start(run.Invocation(["agent"]), {}, cwd, 1)

        self.assertEqual((1, True), result)
        self.assertEqual(0x200, popen.call_args.kwargs.get("creationflags"))
        self.assertNotIn("start_new_session", popen.call_args.kwargs)
        self.assertEqual(["taskkill", "/F", "/T", "/PID", "4242"], taskkill.call_args.args[0],
                         "the agent's whole process tree ends, not only the agent")
        process.wait.assert_called_once()

    def labels(self) -> list[str]:
        return [label["name"] for label in self.github.pr.get("labels") or []]

    def test_run_labels_the_pull_request_reviewing_before_the_agent_starts(self) -> None:
        # Only the agent's `setup` added `reviewing`, about a minute into the job, once the agent had read the skill
        # and the repository, so the pull request read as idle until then.
        self.assertEqual(0, self.review_run())

        self.assertEqual([("--add-label reviewing", False)], self.github.edits,
                         "the label goes on before the agent process starts")
        self.assertIn("added the reviewing label", self.out.getvalue())
        self.assertIn("reviewing", self.github.repo_labels, "a repository without the label gets it created")

    def test_a_refused_run_adds_no_label(self) -> None:
        # A run that labeled before its refusal checks would mark a fork, a draft, a closed pull request, or a head
        # it already reviewed as in progress, with no review coming to take the label off.
        refusals = ((lambda: self.github.pr["head"].update(repo={"full_name": "outsider/app"}), "a fork"),
                    (lambda: self.github.pr.update(draft=True), "a draft"),
                    (lambda: self.github.pr.update(state="closed"), "a closed pull request"),
                    (lambda: self.github.reviews.append({"id": 900, "url": "https://example.test/900",
                                                         "at": "2026-11-18T10:00:00Z", "body": verdict_body(HEAD)}),
                     "a head already reviewed"))
        for change, why in refusals:
            with self.subTest(why=why):
                self.setUp()
                change()

                try:
                    self.assertEqual(0, self.review_run())
                except ReviewError:
                    pass

                self.assertEqual([], self.github.edits, why)
                self.assertFalse(self.started())

    def test_review_labels_false_keeps_the_run_off_the_labels(self) -> None:
        # The early label ignored `review.labels = false`, which turns every review command's labels off.
        for behavior in ("publish", "nothing"):
            with self.subTest(behavior=behavior):
                self.setUp()
                self.behave(behavior)

                self.review_run(labels=False)

                self.assertEqual([], self.github.edits)
                self.assertTrue(self.started())

    def test_a_label_github_refuses_warns_and_the_agent_still_starts(self) -> None:
        # The labels are a view, so a refusal that failed the run would cost the review itself.
        self.github.refuse_edits = True

        self.assertEqual(0, self.review_run())

        self.assertIn("warning: left the labels on acme/app#1 as they were", self.err.getvalue())
        self.assertTrue(self.started())

    def test_every_run_that_publishes_no_verdict_takes_reviewing_off(self) -> None:
        # Only `finish` of a review the agent set up took `reviewing` off, so once the run added it before the agent's
        # `setup`, a launch failure, a timeout or an exit before setup, or a crash left the pull request reading as
        # reviewing with nothing reviewing it.
        def set_up_and_exit() -> dict:
            (self.agent / "setup.json").write_text(json.dumps(self.review_files("run", 501)))
            self.behave("nothing")
            return {}

        cases = (("the agent could not start", lambda: {"configured_runner": str(self.tmp / "missing-agent")}),
                 ("the agent exited before its setup", lambda: self.behave("nothing") or {}),
                 ("the agent set up and exited with no verdict", set_up_and_exit),
                 ("the agent ran out of time", lambda: self.behave("sleep") or {"timeout_minutes": 0.02}))
        for why, prepare in cases:
            with self.subTest(why=why):
                self.setUp()

                self.assertEqual(1, self.review_run(**prepare()))

                self.assertEqual("--add-label reviewing", self.github.edits[0][0])
                self.assertEqual([], self.labels(), why)

        self.setUp()
        with mock.patch.object(run, "start", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.review_run()
        self.assertEqual([], self.labels(), "a run interrupted before the agent's setup takes the label off")

    def test_runner_takes_the_agent_then_review_runner_then_claude(self) -> None:
        self.assertEqual(("claude", None), run.runner(None, None))
        self.assertEqual(("codex", None), run.runner(None, "codex"))
        self.assertEqual(("command", "my-agent --fast"), run.runner(None, "my-agent --fast"))
        self.assertEqual(("claude", None), run.runner("claude", "my-agent --fast"))
        for agent, configured in (("command", None), ("command", "codex"), ("gemini", None)):
            with self.subTest(agent=agent, configured=configured), self.assertRaises(ReviewError):
                run.runner(agent, configured)


def matches(rule: str, command: str) -> bool:
    """Whether a Claude Code `Bash(...)` pattern's command part matches `command`: a trailing ` *` matches the bare
    command and any arguments after it."""
    prefix = rule.removesuffix(" *")
    return command == prefix or command.startswith(prefix + " ")


class PresetTests(unittest.TestCase):
    def invocations(self) -> list[run.Invocation]:
        scratch, state, checkout = Path("/tmp/scratch"), Path("/tmp/state"), Path("/tmp/checkout")
        return [built for mode in run.MODES for built in (
            run.claude_invocation(1, REPO, MODEL, "high", mode, scratch, Path("/tmp/projector")),
            run.codex_invocation(1, REPO, "gpt-5.5", "high", mode, scratch, state, checkout, {"HOME": "/tmp/home"}))]

    def test_presets_never_skip_permissions_or_wait_for_a_prompt(self) -> None:
        # A stuck run tempts a fix that turns every check off; the runner holds a token and a model credential.
        for invocation in self.invocations():
            with self.subTest(argv=invocation.argv[:2]):
                text = " ".join(invocation.argv)
                self.assertNotRegex(text, r"dangerously|bypassPermissions|danger-full-access")
                if invocation.argv[0] == "claude":
                    self.assertIn("--permission-prompts none", text)
                else:
                    self.assertIn("--sandbox workspace-write", text)

    def test_claude_preset_allows_every_command_the_review_skill_runs(self) -> None:
        skill = (SKILLS / "review-pr" / "SKILL.md").read_text()
        blocks = re.findall(r"^\s*```(?:sh|bash)\n(.*?)^\s*```", skill, re.M | re.S)
        commands = [line.strip() for block in blocks for line in block.replace("\\\n", " ").splitlines()
                    if line.strip().split(" ", 1)[0] in ("project", "gh", "git")]
        commands.append("gh repo view acme/app --json visibility")
        allowed = [rule.removeprefix("Bash(").removesuffix(")") for rule in run.claude_invocation(
            1, REPO, MODEL, "high", "allowlist", Path("/tmp/s"), None).argv if rule.startswith("Bash(")]
        denied = [f"{command}" for command in run.DENIED_COMMANDS]
        self.assertGreater(len(commands), 5)
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(any(matches(rule, command) for rule in allowed if rule not in denied))
                self.assertFalse(any(matches(rule, command) for rule in denied))
        for refused in ("git push origin HEAD", "gh pr merge 1", "gh repo edit --visibility public"):
            with self.subTest(refused=refused):
                self.assertTrue(any(matches(rule, refused) for rule in denied))

    def test_the_codex_rules_forbid_what_a_reviewer_never_runs(self) -> None:
        rules = run.codex_rules()
        for command in run.DENIED_COMMANDS:
            self.assertIn(json.dumps(command.removesuffix(" *").split(" ")), rules)
        self.assertEqual(len(run.DENIED_COMMANDS), rules.count('decision = "forbidden"'))

    def test_the_review_skill_reads_every_variable_the_run_sets(self) -> None:
        skill = (SKILLS / "review-pr" / "SKILL.md").read_text()
        source = Path(run.__file__).read_text()
        for name in sorted(set(re.findall(r"\b(PROJECTOR_[A-Z_]+)=", source)) | {"PROJECTOR_REPLACE"}):
            with self.subTest(name=name):
                self.assertIn(name, skill, "a variable the skill never reads does nothing")


if __name__ == "__main__":
    unittest.main()
