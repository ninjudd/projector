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
from unittest import mock

from projector import cli, review, run
from projector.review import ReviewError

REPO = "acme/app"
HEAD = "a" * 40
MODEL = "claude-opus-5-5"
SKILLS = Path(cli.__file__).resolve().parents[2] / "skills"

# The agent `review.runner` names in these tests. It records the environment it saw, then does what the
# `behavior` file in its folder says: publish a verdict, write a report and publish nothing, or outlast its time.
AGENT = '''
import json, os, pathlib, sys, time
here = pathlib.Path(sys.argv[1])
(here / "seen.json").write_text(json.dumps({"env": dict(os.environ), "stdin": sys.stdin.read()}))
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
            return "".join(json.dumps(c) + "\n" for c in self.starts)
        if args[:2] == ["api", f"repos/{REPO}/issues/1/comments"] and args[2] == "-f":
            self.posted.append(args[3].removeprefix("body="))
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
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
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
                with mock.patch.object(review, "finish", wraps=review.finish) as finish, \
                        mock.patch.dict(os.environ, {"GITHUB_SERVER_URL": "https://github.com",
                                                     "GITHUB_REPOSITORY": REPO, "GITHUB_RUN_ID": "77"}):
                    code = self.review_run()

                self.assertEqual(1, code)
                self.assertEqual(1, len(self.github.posted))
                self.assertIn(reason, self.github.posted[0])
                self.assertIn(f"https://github.com/{REPO}/actions/runs/77", self.github.posted[0])
                self.assertIn(f"sha={HEAD}", self.github.posted[0])
                self.assertTrue(finish.call_args.args[4], "the cleanup is forced, since nothing was published")

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
