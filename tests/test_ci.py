"""CI review setup: the workflow `project init --ci-review` writes, the secrets it sets, and the action's own checks,
as docs/projects/review-in-ci/readme.md § 2.8 and § 2.10 describe."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from projector import ci, cli, review, summary
from projector.core import UsageError
from tests.test_projector import RepositoryTestCase, RulesetGitHub

ACTION = Path(__file__).parents[1] / "actions" / "review" / "action.yml"


class InitCiReviewTests(RepositoryTestCase):
    """`project init`'s CI review step in a repository whose origin is acme/app on GitHub."""

    def setUp(self) -> None:
        super().setUp()
        subprocess.run(["git", "-C", str(self.root), "remote", "add", "origin", "https://github.com/acme/app.git"],
                       check=True)
        self.enterContext(mock.patch.object(review, "run_gh", RulesetGitHub()))
        self.secrets: list[str] = []
        self.gh_calls: list[tuple[str, ...]] = []
        self.enterContext(mock.patch.object(summary, "gh", side_effect=self.gh))
        self.enterContext(mock.patch.object(summary, "push_decision",
                                            return_value=("https://acme.github.io/app/", "")))
        self.set: list[tuple[list[str], Path | None]] = []
        self.enterContext(mock.patch.object(cli, "run_interactive",
                                            side_effect=lambda argv, source=None: self.set.append((argv, source)) or 0))

    def gh(self, *args: str) -> str:
        self.gh_calls.append(args)
        if args[:2] == ("secret", "list"):
            return json.dumps([{"name": name} for name in self.secrets])
        raise AssertionError(f"unexpected gh call: {args}")

    def init(self, *extra: str) -> tuple[int, str, str]:
        return self.invoke("init", "--no-site", "--no-publish-rule", "--no-require-review", *extra)

    def workflow(self) -> Path:
        return self.root / ci.WORKFLOW_PATH

    def recorded(self) -> dict:
        return tomllib.loads((self.root / ".projector.toml").read_text()).get("review", {})

    def test_a_yes_writes_the_workflow_and_records_the_settings_keeping_every_reviewer(self) -> None:
        (self.root / ".projector.toml").write_text('[review]\nreviewers = "someone"\n')

        code, out, err = self.init("--ci-review", "--ci-agents", "claude,codex=gpt-5.5")
        again, out_again, _ = self.init()

        self.assertEqual((0, 0), (code, again), err)
        self.assertEqual(ci.workflow_text("v0", {"claude": "", "codex": "gpt-5.5"}), self.workflow().read_text())
        self.assertIn(f"created {ci.WORKFLOW_PATH}", out)
        self.assertIn(f"unchanged {ci.WORKFLOW_PATH}", out_again,
                      "the recorded agents and models rebuild the same workflow")
        self.assertEqual((True, "claude, codex=gpt-5.5", "someone, github-actions[bot]"),
                         (self.recorded()["ci"], self.recorded()["ci_agents"], self.recorded()["reviewers"]),
                         "the bot joins the reviewers the repository already counts, and joins them once")

    def test_init_replaces_a_workflow_nobody_edited_and_keeps_an_edited_one(self) -> None:
        self.workflow().parent.mkdir(parents=True)
        self.workflow().write_text(ci.workflow_text("v0.0.1", {"codex": "gpt-5.5"}))

        _, out, _ = self.init("--ci-review")
        self.assertIn(f"updated {ci.WORKFLOW_PATH}", out)
        self.assertEqual(ci.workflow_text("v0", {"claude": ""}), self.workflow().read_text())

        edited = self.workflow().read_text().replace("timeout-minutes: 60", "timeout-minutes: 90")
        self.workflow().write_text(edited)
        _, out, err = self.init()

        self.assertEqual(edited, self.workflow().read_text())
        self.assertIn(f"kept {ci.WORKFLOW_PATH}", out)
        self.assertIn("project review ci-workflow", err)

    def test_init_has_gh_set_a_missing_secret_without_reading_it(self) -> None:
        self.at_a_terminal()

        code, _, err = self.init("--ci-review")

        self.assertEqual(0, code, err)
        self.assertEqual([(["gh", "secret", "set", "CLAUDE_CODE_OAUTH_TOKEN", "--repo", "acme/app"], None)], self.set,
                         "no value on the command line, and gh reads it from the terminal")
        self.assertIn("claude setup-token", err)

    def test_a_codex_login_is_set_from_its_file_only_once_you_confirm(self) -> None:
        self.at_a_terminal()
        home = self.root.parent / "codex-home"
        self.enterContext(mock.patch.dict(os.environ, {"CODEX_HOME": str(home)}))
        for answer, expected in (("n\n", []), ("y\n", [(["gh", "secret", "set", "CODEX_AUTH_JSON", "--repo",
                                                          "acme/app"], home / "auth.json")])):
            with self.subTest(answer=answer), mock.patch("sys.stdin.readline", return_value=answer):
                self.set.clear()
                self.init("--ci-review", "--ci-agents", "codex=gpt-5.5", "--ci-credential", "subscription")
                self.assertEqual(expected, self.set)

    def test_a_present_secret_is_kept_and_two_of_one_agent_are_reported(self) -> None:
        self.at_a_terminal()
        self.secrets = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_AUTH_JSON"]

        _, _, err = self.init("--ci-review", "--ci-agents", "claude,codex=gpt-5.5")

        self.assertEqual([], self.set)
        self.assertIn("ANTHROPIC_API_KEY is set for the claude review", err)
        self.assertIn("both CODEX_AUTH_JSON and OPENAI_API_KEY are set, so the codex review refuses to start", err)

    def test_an_agent_the_action_has_no_model_for_is_refused_before_anything_is_written(self) -> None:
        # Its job would stop before it reviewed anything, on every pull request.
        code, _, err = self.init("--ci-review", "--ci-agents", "claude,codex")

        self.assertNotEqual(0, code)
        self.assertIn("codex=<model>", err)
        self.assertFalse(self.workflow().exists())
        self.assertEqual(([], None, None), (self.set, self.recorded().get("ci"), self.recorded().get("ci_agents")),
                         "no secret is set and nothing is recorded")

    def test_without_a_terminal_or_a_flag_init_adds_no_ci_review(self) -> None:
        code, _, err = self.init()

        self.assertEqual(0, code, err)
        self.assertFalse(self.workflow().exists())
        self.assertEqual(([], []), (self.gh_calls, self.set), "no secret is listed or set")
        self.assertNotIn("ci", self.recorded(), "nothing is recorded without an answer")

    def test_a_no_at_the_terminal_is_recorded_and_asked_no_more(self) -> None:
        self.at_a_terminal()
        with mock.patch("sys.stdin.readline", return_value="\n"):
            _, _, first = self.init()
        _, _, second = self.init()

        self.assertIn(cli.CI_REVIEW_QUESTION, first)
        self.assertNotIn(cli.CI_REVIEW_QUESTION, second)
        self.assertIs(False, self.recorded()["ci"])
        self.assertFalse(self.workflow().exists())


class WorkflowTests(unittest.TestCase):
    def test_a_workflow_init_wrote_reads_as_unedited_with_any_agents_and_ref(self) -> None:
        for ref, agents in (("v0", {"claude": ""}), ("main", {"claude": "", "codex": "gpt-5.5"}),
                            ("abc123", {"codex": "gpt-5.5"})):
            with self.subTest(ref=ref, agents=agents):
                text = ci.workflow_text(ref, agents)
                self.assertTrue(ci.unedited(text))
                self.assertFalse(ci.unedited(text.replace("cancel-in-progress: true", "cancel-in-progress: false")))
        self.assertFalse(ci.unedited(ci.body("v0", {"claude": ""})), "a file with no digest line is a hand-written one")

    def test_the_workflow_passes_both_credentials_of_each_agent(self) -> None:
        # The action reads the one that is set, so the workflow need not know which kind the repository chose.
        text = ci.workflow_text("v0", {"claude": "", "codex": "gpt-5.5"})
        for agent, names in ci.CREDENTIALS.items():
            for name in names.values():
                with self.subTest(agent=agent, name=name):
                    self.assertIn(f"          {name}: ${{{{ secrets.{name} }}}}\n", text)
        self.assertIn("uses: ninjudd/projector/actions/review@v0\n", text)

    def test_parse_agents_reads_what_format_agents_records_and_refuses_the_rest(self) -> None:
        agents = ci.parse_agents("codex = gpt-5.5, claude, codex=other")
        self.assertEqual({"codex": "gpt-5.5", "claude": ""}, agents, "the first entry for an agent wins")
        self.assertEqual(agents, ci.parse_agents(ci.format_agents(agents)))
        self.assertEqual({"claude": "claude-opus-5-5[1m]"}, ci.parse_agents("claude=claude-opus-5-5[1m]"))
        for text in ("", "claude,gemini", " , ", "codex", "codex=", "codex=gpt 5", "codex='gpt'", "claude=[x]"):
            with self.subTest(text=text), self.assertRaises(UsageError):
                ci.parse_agents(text)


def step_script(name: str) -> str:
    """The shell script of the review action's step called `name`."""
    lines = ACTION.read_text().splitlines()
    start = lines.index(f"    - name: {name}")
    start = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |") + 1
    end = next(i for i in range(start, len(lines)) if not lines[i].startswith("        ") and lines[i].strip())
    return "\n".join(line[8:] for line in lines[start:end]) + "\n"


def review_jobs(workflow: str) -> list[dict[str, str]]:
    """The inputs the review job of a workflow `ci.workflow_text` wrote gives the action, once for each entry of
    its matrix's `include:` list, with each `${{ matrix.<key> }}` read from that entry, as GitHub reads it."""
    lines = workflow.splitlines()
    entries: list[dict[str, str]] = []
    for line in lines[lines.index("        include:") + 1:]:
        if line.startswith("          - "):
            entries.append({})
        elif not line.startswith("            "):
            break
        key, _, value = line.strip().removeprefix("- ").partition(": ")
        entries[-1][key] = value
    start = lines.index("        with:") + 1
    given = dict(line.strip().partition(": ")[::2] for line in lines[start:lines.index("        env:", start)])
    expression = re.compile(r"\$\{\{ matrix\.(\w+) \}\}")
    return [{name: expression.sub(lambda found: entry.get(found.group(1), ""), value)
             for name, value in given.items()} for entry in entries]


@unittest.skipIf(shutil.which("bash") is None, "the action's steps run in bash")
class ActionTests(unittest.TestCase):
    def check(self, agent: str, **secrets: str) -> subprocess.CompletedProcess:
        environment = {"PATH": os.environ["PATH"], "AGENT": agent, **secrets}
        return subprocess.run(["bash", "-c", step_script("Check the agent's credentials")], env=environment,
                              capture_output=True, text=True)

    def test_an_agent_runs_with_exactly_one_credential(self) -> None:
        for agent, names in ci.CREDENTIALS.items():
            subscription, key = names["subscription"], names["api-key"]
            for given, passes in (({subscription: "s"}, True), ({key: "k"}, True),
                                  ({subscription: "s", key: "k"}, False), ({}, False), ({subscription: ""}, False)):
                with self.subTest(agent=agent, given=sorted(given)):
                    result = self.check(agent, **given)
                    self.assertEqual(passes, result.returncode == 0, result.stdout)
        self.assertIn("delete the secret", self.check("claude", CLAUDE_CODE_OAUTH_TOKEN="s",
                                                      ANTHROPIC_API_KEY="k").stdout)
        self.assertEqual(0, self.check("command").returncode, "a command the repository names brings its own")

    def test_every_job_the_generated_workflow_runs_reaches_the_review_with_a_model(self) -> None:
        # The workflow's matrix and `with:` are one side of the seam, and the action's review step the other.
        jobs = review_jobs(ci.workflow_text("v0", ci.parse_agents("claude, codex=gpt-5.5")))
        stub = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (stub / "project").write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$STUB/args"\n')
        (stub / "project").chmod(0o755)
        ran = {}
        for inputs in jobs:
            environment = {"PATH": f"{stub}{os.pathsep}{os.environ['PATH']}", "STUB": str(stub), "PR": "7",
                           "AGENT": inputs["agent"], "MODEL": inputs.get("model", ""), "EFFORT": "high",
                           "MODE": "auto", "REREVIEW": "false", "TIMEOUT": "45", "PROJECTOR_ROOT": str(stub)}
            result = subprocess.run(["bash", "-eo", "pipefail", "-c", step_script("Review the pull request")],
                                    env=environment, capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            args = (stub / "args").read_text().splitlines()
            ran[inputs["agent"]] = args[args.index("--model") + 1]
        self.assertEqual({"claude": "claude-opus-5-5", "codex": "gpt-5.5"}, ran)

    def test_every_step_that_runs_project_with_the_workflow_token_names_the_login_it_posts_as(self) -> None:
        # GitHub refuses `gh api user` to that token, so without the name a step fails after it pushes or posts.
        steps = ACTION.read_text().split("\n    - ")[1:]
        with_token = [step for step in steps if "GH_TOKEN: ${{ github.token }}" in step]
        self.assertIn("name: Publish the summaries", [step.splitlines()[0] for step in with_token])
        for step in with_token:
            with self.subTest(step=step.splitlines()[0]):
                self.assertIn(f"        {review.TOKEN_LOGIN}: {cli.CI_REVIEWER}\n", step)

    def test_only_the_review_step_hands_its_summary_off(self) -> None:
        # The summary job's publish must push; a handoff there would publish nothing.
        text = ACTION.read_text()
        review_step = text[text.index("    - name: Review the pull request"):text.index("    - name: Save the review")]
        self.assertEqual(1, text.count("PROJECTOR_SUMMARY_HANDOFF"))
        self.assertIn("PROJECTOR_SUMMARY_HANDOFF", review_step)


if __name__ == "__main__":
    unittest.main()
