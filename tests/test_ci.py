"""CI review setup: the workflow `project init --ci-review` writes, the secrets it sets, and the action's own checks,
as docs/projects/review-in-ci/readme.md § 2.8 and § 2.10 describe."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from projector import ci, cli, review, run, summary
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
        # The public apps GitHub answers for; a private app reads as missing, as it does to another account.
        self.apps = {"projector-review": 5827721}
        self.offline = False
        self.enterContext(mock.patch.object(summary, "gh_lookup", side_effect=self.lookup))

    def lookup(self, *args: str) -> str | None:
        if args[0] == "api" and args[1].startswith("apps/"):
            if self.offline:
                raise summary.SummaryError("the gh CLI is not installed")
            found = self.apps.get(args[1].removeprefix("apps/"))
            return f"{found}\n" if found else None
        raise AssertionError(f"unexpected gh call: {args}")

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

    def test_an_app_posts_the_reviews_and_a_later_init_keeps_it(self) -> None:
        (self.root / ".projector.toml").write_text('[review]\nreviewers = "someone, github-actions[bot]"\n')

        code, _, err = self.init("--ci-review", "--ci-app", "projector-review")
        again, out_again, _ = self.init()

        self.assertEqual((0, 0), (code, again), err)
        self.assertEqual(ci.workflow_text("v0", {"claude": ""}, 5827721), self.workflow().read_text())
        self.assertIn(f"unchanged {ci.WORKFLOW_PATH}", out_again,
                      "the recorded app rebuilds the same workflow, so the reviews never fall back to the bot")
        self.assertEqual(("projector-review", 5827721, "someone, projector-review[bot]"),
                         (self.recorded()["ci_app"], self.recorded()["ci_app_id"], self.recorded()["reviewers"]),
                         "the app takes github-actions[bot]'s place, and every other reviewer stays")
        self.assertIn(f"gh secret set {ci.APP_KEY} --repo acme/app < <key file>", err)

    def test_a_later_init_refreshes_the_app_workflow_without_github_but_ci_app_still_asks_it(self) -> None:
        self.assertEqual(0, self.init("--ci-review", "--ci-app", "projector-review")[0])
        self.offline = True

        code, out, err = self.init()
        named, _, named_err = self.init("--ci-app", "projector-review")

        self.assertEqual(0, code, err)
        self.assertIn(f"unchanged {ci.WORKFLOW_PATH}", out, "the recorded ID rebuilds the workflow with no lookup")
        self.assertNotEqual(0, named, "--ci-app always asks GitHub, so it can refuse an app GitHub does not show")
        self.assertIn("could not look up the GitHub App projector-review", named_err)

    def test_the_hosted_app_has_each_job_request_an_oidc_token_and_passes_no_key(self) -> None:
        # The hosted service mints the token for the job's OIDC token, so a workflow without id-token: write
        # cannot post, and one that still passed the key input would ask for a secret nobody has.
        code, out, err = self.init("--ci-review", "--ci-app", ci.HOSTED_APP)
        again, out_again, _ = self.init()
        printed, shown, _ = self.invoke("review", "ci-workflow")

        self.assertEqual((0, 0, 0), (code, again, printed), err)
        text = self.workflow().read_text()
        self.assertEqual([(ci.HOSTED_APP, None, None)],
                         [(job.get("app"), job.get("app-id"), job.get("app-private-key")) for job in review_jobs(text)])
        self.assertNotIn(ci.APP_KEY, text + err)
        jobs = text.split("\n  summary:\n")
        self.assertEqual([True, True], [re.search(r"\n    permissions:\n(      .+\n)*      id-token: write\n", job)
                                        is not None for job in jobs], "both jobs request the service's token")
        self.assertIn(f"\n          app: {ci.HOSTED_APP}\n", jobs[1])
        self.assertTrue(ci.unedited(text))
        self.assertIn(f"unchanged {ci.WORKFLOW_PATH}", out_again)
        self.assertEqual(text, shown, "project review ci-workflow prints the workflow init wrote")
        self.assertEqual((ci.HOSTED_APP, ci.HOSTED_LOGIN), (self.recorded()["ci_app"], self.recorded()["reviewers"]))
        self.assertNotIn("ci_app_id", self.recorded())

    def test_an_app_recorded_without_its_id_is_left_as_it_is_when_github_cannot_answer(self) -> None:
        (self.root / ".projector.toml").write_text('[review]\nci = true\nci_app = "projector-review"\n')
        self.offline = True

        code, _, err = self.init()

        self.assertEqual(0, code, err)
        self.assertIn("left the CI review as it is", err)
        self.assertFalse(self.workflow().exists(), "a workflow without the app would post as github-actions[bot]")
        self.assertNotIn("ci_app_id", self.recorded())

    def test_an_app_github_does_not_show_is_refused_before_anything_is_written(self) -> None:
        code, _, err = self.init("--ci-review", "--ci-app", "private-app")

        self.assertNotEqual(0, code)
        self.assertIn("make the app public", err)
        self.assertFalse(self.workflow().exists())
        self.assertNotIn("ci", self.recorded())

    def test_init_sets_the_app_key_from_the_file_you_name_without_reading_it(self) -> None:
        self.at_a_terminal()
        self.secrets = ["CLAUDE_CODE_OAUTH_TOKEN"]
        key = self.root.parent / "projector-review.pem"
        key.write_text("not a real key\n")

        with mock.patch("sys.stdin.readline", return_value=f"{key}\n"):
            code, _, err = self.init("--ci-review", "--ci-app", "projector-review")

        self.assertEqual(0, code, err)
        self.assertEqual([(["gh", "secret", "set", ci.APP_KEY, "--repo", "acme/app"], key)], self.set,
                         "gh reads the key from the file, so it is on no command line")
        self.assertIn(f"delete {key}", err)

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

    def test_both_jobs_run_on_the_labels_you_name_and_a_later_init_keeps_them(self) -> None:
        # A refresh that forgot the labels would move the reviews back to a hosted runner, away from the
        # credential and network the repository's own runner holds.
        code, _, err = self.init("--ci-review", "--ci-runs-on", "self-hosted, linux,projector")
        again, out_again, _ = self.init()

        self.assertEqual((0, 0), (code, again), err)
        self.assertEqual(["[self-hosted, linux, projector]"] * 2, runs_on(self.workflow().read_text()))
        self.assertIn(f"unchanged {ci.WORKFLOW_PATH}", out_again)
        self.assertEqual("self-hosted, linux, projector", self.recorded()["ci_runs_on"])

        named, _, named_err = self.init("--ci-runs-on", "ubuntu-latest")
        self.assertEqual(0, named, named_err)
        self.assertEqual(["ubuntu-latest"] * 2, runs_on(self.workflow().read_text()),
                         "naming the hosted runner moves the reviews back to it")

    def test_the_gate_note_names_the_runner_the_reviews_run_on(self) -> None:
        # A hosted label told to install tools on "your runner" hides the advice a hosted runner needs.
        (self.root / ".projector.toml").write_text("[review]\ngate = 'make check'\n")
        notes = {}
        for labels in ("ubuntu-latest", "self-hosted,linux"):
            notes[labels] = self.init("--ci-review", "--ci-runs-on", labels)[2]

        self.assertIn("on a hosted runner", notes["ubuntu-latest"])
        self.assertNotIn("on your runner", notes["ubuntu-latest"])
        self.assertIn("on your runner", notes["self-hosted,linux"])

    def test_a_runner_credential_passes_no_secret_and_sets_none(self) -> None:
        # An unset secret reads as empty, and an empty variable in the step's env hides the runner's own.
        self.at_a_terminal()

        code, _, err = self.init("--ci-review", "--ci-agents", "claude,codex=gpt-5.5", "--ci-runs-on", "self-hosted",
                                 "--ci-credential", "runner")
        again, out_again, _ = self.init()

        self.assertEqual((0, 0), (code, again), err)
        self.assertNotIn("secrets.", self.workflow().read_text())
        self.assertEqual(([], []), (self.set, self.gh_calls), "no secret is listed or set")
        self.assertIn("the codex review reads its credential from your runner: run `codex login`", err)
        self.assertEqual("runner", self.recorded()["ci_credential"])
        self.assertIn(f"unchanged {ci.WORKFLOW_PATH}", out_again, "a refresh keeps passing no secret")

    def test_a_runner_credential_on_a_hosted_runner_is_refused_before_anything_is_written(self) -> None:
        # A hosted runner holds no credential of yours, so every review would stop at the credential check.
        for labels in ((), ("--ci-runs-on", "ubuntu-latest"), ("--ci-runs-on", "macos-latest"),
                       ("--ci-runs-on", "ubuntu-24.04,windows-2025")):
            with self.subTest(labels=labels):
                code, _, err = self.init("--ci-review", "--ci-credential", "runner", *labels)

                self.assertNotEqual(0, code)
                self.assertIn("--ci-runs-on", err)
                self.assertFalse(self.workflow().exists())
                self.assertEqual((None, None), (self.recorded().get("ci"), self.recorded().get("ci_credential")))

    def test_moving_a_runner_credential_back_to_a_hosted_runner_asks_for_a_secret_kind(self) -> None:
        # Naming the hosted runner alone would keep the recorded `runner` and write a workflow with no secret.
        self.assertEqual(0, self.init("--ci-review", "--ci-runs-on", "self-hosted", "--ci-credential", "runner")[0])
        written = self.workflow().read_text()

        code, _, err = self.init("--ci-runs-on", "ubuntu-latest")
        moved, _, moved_err = self.init("--ci-runs-on", "ubuntu-latest", "--ci-credential", "api-key")

        self.assertNotEqual(0, code)
        self.assertIn("--ci-credential", err)
        self.assertEqual(0, moved, moved_err)
        self.assertNotEqual(written, self.workflow().read_text())
        self.assertIn("ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}", self.workflow().read_text())


def runs_on(workflow: str) -> list[str]:
    """The `runs-on` value of each job in `workflow`, in order."""
    return re.findall(r"^    runs-on: (.*)$", workflow, re.M)


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
        self.assertEqual([ci.HOSTED] * 2, runs_on(text), "with no labels, both jobs run on a hosted runner")

    def test_a_runner_credential_runs_on_any_runner_github_does_not_host(self) -> None:
        # An Actions Runner Controller scale set carries only its own name, and a runner registered with
        # --no-default-labels has no `self-hosted`, so a guard that wants that label would refuse both.
        for labels in (["arc-runner-set"], ["projector", "linux"], ["self-hosted", "ubuntu-24.04"], ["Self-Hosted"]):
            with self.subTest(labels=labels):
                self.assertNotIn("secrets.CLAUDE", ci.workflow_text("v0", {"claude": ""}, None, labels, ci.RUNNER))

    def test_parse_runs_on_reads_what_format_runs_on_records_and_refuses_the_rest(self) -> None:
        labels = ci.parse_runs_on(" self-hosted,linux , self-hosted,gpu.large_1")
        self.assertEqual(["self-hosted", "linux", "gpu.large_1"], labels)
        self.assertEqual(labels, ci.parse_runs_on(ci.format_runs_on(labels)))
        # Each of these would break the workflow's YAML or name no runner at all.
        for text in ("", " , ", "self-hosted, [linux]", "a: b", "my runner", "-x"):
            with self.subTest(text=text), self.assertRaises(UsageError):
                ci.parse_runs_on(text)

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
    end = next(i for i in range(start, len(lines)) if not lines[i].startswith("          "))
    given = dict(line.strip().partition(": ")[::2] for line in lines[start:end])
    expression = re.compile(r"\$\{\{ matrix\.(\w+) \}\}")
    return [{name: expression.sub(lambda found: entry.get(found.group(1), ""), value)
             for name, value in given.items()} for entry in entries]


@unittest.skipIf(shutil.which("bash") is None, "the action's steps run in bash")
class ActionTests(unittest.TestCase):
    def test_the_claude_agent_gets_every_plugin_projector_depends_on(self) -> None:
        # Claude Code loads no plugin whose dependency is missing, so without them every CI review ran with no
        # review-pr skill, and an agent that did not find the skill's files by hand published no verdict.
        root = Path(__file__).parents[1]
        manifest = json.loads((root / ".claude-plugin" / "plugin.json").read_text())
        marketplace = json.loads((root / ".claude-plugin" / "marketplace.json").read_text())
        self.assertTrue(manifest.get("dependencies"), "the test needs a dependency to check")
        with tempfile.TemporaryDirectory() as scratch:
            bin_dir, log = Path(scratch, "bin"), Path(scratch, "calls")
            bin_dir.mkdir()
            for name in ("npm", "claude"):
                fake = bin_dir / name
                fake.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\n')
                fake.chmod(0o755)
            environment = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "AGENT": "claude", "CLAUDE_VERSION": "9.9.9",
                           "PROJECTOR_ROOT": str(root)}
            subprocess.run(["bash", "-e", "-c", step_script("Install the agent")], env=environment, check=True,
                           capture_output=True, text=True)
            calls = log.read_text().splitlines()
        self.assertIn(f"claude plugin marketplace add {root}", calls)
        listed = {plugin["name"] for plugin in marketplace["plugins"]}
        for dependency in manifest["dependencies"]:
            with self.subTest(dependency=dependency):
                self.assertIn(dependency, listed, "the marketplace the action adds must carry the dependency")
                plugin = f"{dependency}@{marketplace['name']}"
                # install leaves a kept copy at an older pin, so a persistent runner needs update after it.
                self.assertLess(calls.index(f"claude plugin install {plugin}"),
                                calls.index(f"claude plugin update {plugin}"))
        self.assertLess(calls.index(f"claude plugin marketplace add {root}"),
                        calls.index(f"claude plugin install {manifest['dependencies'][0]}@{marketplace['name']}"))

    def test_the_action_names_its_reviews_by_its_source_trees_version(self) -> None:
        # The runner installs no distribution, so a review names the version only through this variable.
        script = step_script("Set up Projector")
        read = re.search(r"^version=\$\(.*?plugin\.json\"\)$", script, re.M | re.S).group(0)
        self.assertIn(f'echo "{review.PRODUCER_VERSION}=$version"', script)
        root = Path(__file__).parents[1]
        # The step runs in the caller's checkout, whose own top-level json.py must not replace the standard one.
        with tempfile.TemporaryDirectory() as caller:
            Path(caller, "json.py").write_text("def load(file):\n    return {'version': 'hijacked'}\n")
            found = subprocess.run(["bash", "-c", f'{read}\nprintf %s "$version"'], cwd=caller,
                                   env={**os.environ, "root": str(root)}, capture_output=True, text=True,
                                   check=True).stdout
        self.assertEqual(json.loads((root / ".claude-plugin" / "plugin.json").read_text())["version"], found)

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

    def test_a_codex_login_on_the_runner_counts_as_its_one_credential(self) -> None:
        home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (home / ".codex").mkdir()
        (home / ".codex" / "auth.json").write_text("{}")
        elsewhere = home / "runner-2"
        elsewhere.mkdir()
        (elsewhere / "auth.json").write_text("{}")
        for given, passes in (({"HOME": str(home)}, True),
                              ({"HOME": str(elsewhere.parent / "nobody"), "CODEX_HOME": str(elsewhere)}, True),
                              ({"HOME": str(home), "OPENAI_API_KEY": "k"}, False),
                              ({"HOME": str(home), "CODEX_AUTH_JSON": "{}"}, True),
                              ({"HOME": str(elsewhere)}, False)):
            with self.subTest(given=sorted(given)):
                result = self.check("codex", **given)
                self.assertEqual(passes, result.returncode == 0, result.stdout)
        self.assertIn("run `codex login` as the runner's user", self.check("codex", HOME=str(elsewhere)).stdout,
                      "a runner with nothing says what to set up on it")
        self.assertIn("the Codex login in", self.check("codex", HOME=str(home), OPENAI_API_KEY="k").stdout)

    def install(self, agent: str, prints: str | None, home: Path, **given: str) -> tuple[list[str], dict[str, str]]:
        """Run the action's install step for `agent` with a fake CLI on PATH that prints `prints` for `--version`,
        or none when it is None, and a fake npm. Returns the commands the fakes ran and what the step put in
        GITHUB_ENV."""
        stub = home / "bin"
        stub.mkdir(exist_ok=True)
        ran = home / "ran"
        ran.write_text("")
        # Like npm, the fake leaves a CLI behind that later steps can run; it logs what they run but has no version.
        installed = f'#!/bin/sh\n[ "$1" = --version ] && exit 1\ncat > /dev/null\necho "{agent} $*" >> "{ran}"\n'
        (stub / "npm").write_text(f'#!/bin/sh\necho "npm $*" >> "{ran}"\n'
                                  f"cat > '{stub / agent}' <<'EOF'\n{installed}EOF\nchmod +x '{stub / agent}'\n")
        (stub / "npm").chmod(0o755)
        (stub / agent).unlink(missing_ok=True)
        if prints is not None:
            (stub / agent).write_text(f'#!/bin/sh\n[ "$1" = --version ] && {{ echo "{prints}"; exit 0; }}\n'
                                      f'cat > /dev/null\necho "{agent} $*" >> "{ran}"\n')
            (stub / agent).chmod(0o755)
        environment = {"PATH": f"{stub}{os.pathsep}/usr/bin{os.pathsep}/bin", "HOME": str(home), "AGENT": agent,
                       "CLAUDE_VERSION": "2.1.327", "CODEX_VERSION": "0.169.0", "RUNNER_TEMP": str(home / "temp"),
                       "GITHUB_ENV": str(home / "github-env"), "PROJECTOR_ROOT": str(home / "projector"), **given}
        (home / "temp").mkdir(exist_ok=True)
        (home / "github-env").write_text("")
        result = subprocess.run(["bash", "-eo", "pipefail", "-c", step_script("Install the agent")], env=environment,
                                capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        exported = dict(line.partition("=")[::2] for line in (home / "github-env").read_text().splitlines())
        return ran.read_text().splitlines(), exported

    def test_a_cli_already_at_the_pinned_version_is_not_installed_again(self) -> None:
        # A self-hosted runner's user may have no write access where npm installs globally.
        for agent, prints, installs in (("claude", "2.1.327 (Claude Code)", False), ("codex", "codex-cli 0.169.0", False),
                                        ("claude", "2.1.3270 (Claude Code)", True), ("claude", "2.1.32 (Claude Code)", True),
                                        ("codex", "codex-cli 0.169.0-alpha.1", True), ("claude", None, True)):
            with self.subTest(agent=agent, prints=prints), tempfile.TemporaryDirectory() as home:
                ran, _ = self.install(agent, prints, Path(home), OPENAI_API_KEY="k")
                self.assertEqual(installs, any(line.startswith("npm install --global") for line in ran), ran)

    def test_a_codex_login_on_the_runner_is_used_in_place_and_never_replaced(self) -> None:
        # Codex refreshes the login in place, so a copy, or a home the job throws away, makes it go stale.
        home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (home / "projector" / "skills").mkdir(parents=True)
        codex = home / ".codex"
        (codex / "skills" / ".system").mkdir(parents=True)
        (codex / "auth.json").write_text('{"tokens": "refreshed"}')

        for run in (1, 2):
            with self.subTest(run=run):
                ran, exported = self.install("codex", "codex-cli 0.169.0", home)
                self.assertEqual(str(codex), exported["CODEX_HOME"])
                self.assertEqual('{"tokens": "refreshed"}', (codex / "auth.json").read_text())
                self.assertEqual([], ran, "nothing logs in over the runner's login")
                self.assertEqual(home / "projector" / "skills", (codex / "skills" / "projector").readlink())
                self.assertEqual([".system", "projector"], sorted(path.name for path in (codex / "skills").iterdir()),
                                 "the runner's own skills stay, and a second job links nothing inside the link")

        ran, exported = self.install("codex", "codex-cli 0.169.0", home, OPENAI_API_KEY="k")
        self.assertEqual(str(home / "temp" / "codex-home"), exported["CODEX_HOME"], "a key still gets a home of its own")
        self.assertEqual(["codex login --with-api-key"], ran)

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

    def test_an_app_workflow_gives_the_action_the_app_and_its_key(self) -> None:
        with_app, without = ci.workflow_text("v0", {"claude": ""}, 5827721), ci.workflow_text("v0", {"claude": ""})

        self.assertEqual([("'5827721'", f"${{{{ secrets.{ci.APP_KEY} }}}}")],
                         [(job.get("app-id"), job.get("app-private-key")) for job in review_jobs(with_app)],
                         "the app and its key are both inputs of the action")
        self.assertTrue(ci.unedited(with_app))
        self.assertEqual(([None], False), ([job.get("app-id") for job in review_jobs(without)], ci.APP_KEY in without))

    def test_the_app_key_reaches_only_the_step_that_mints_its_token(self) -> None:
        # A caller's env reaches every step of a composite action, the agent's and npm's install scripts included,
        # and a composite action's inputs set no environment variable. So the workflow passes the key only as an
        # input, and only the step that mints the token reads that input.
        own = Path(__file__).parents[1] / ".github" / "workflows" / "projector-review.yml"
        for name, workflow in (("generated", ci.workflow_text("v0", {"claude": ""}, 5827721)), ("own", own.read_text())):
            with self.subTest(workflow=name):
                self.assertEqual({f"app-private-key: ${{{{ secrets.{ci.APP_KEY} }}}}"},
                                 {line.strip() for line in workflow.splitlines() if ci.APP_KEY in line},
                                 "the key is in no job's or step's env, only each job's input")
        steps = {step.splitlines()[0].removeprefix("name: "): step for step in ACTION.read_text().split("\n    - ")[1:]}
        minting = ["Mint the app's token", "Mint the context token"]
        self.assertEqual(minting,
                         [name for name, step in steps.items() if "app-private-key" in step or ci.APP_KEY in step])
        for name in minting:
            self.assertIn("private-key: ${{ inputs.app-private-key }}\n", steps[name])
            self.assertIn("uses: actions/create-github-app-token@", steps[name])

    def test_the_review_and_its_summary_post_as_the_app_whose_token_they_use(self) -> None:
        # The token and the login must come from the same step, or a step posts as one account and names another;
        # GitHub refuses `gh api user` to a token of no user, so a step without the login fails once it posts.
        steps = {step.splitlines()[0].removeprefix("name: "): step for step in ACTION.read_text().split("\n    - ")[1:]}
        minted = re.search(r"\n      id: (\S+)\n", steps["Mint the app's token"]).group(1)
        hosted = re.search(r"\n      id: (\S+)\n", steps["Request the hosted service's token"]).group(1)
        for minting in ("Mint the app's token", "Request the hosted service's token"):
            self.assertNotIn("inputs.step", steps[minting].split("run:")[0].split("uses:")[0],
                             "the summary job mints the app's token too")
        for name in ("Review the pull request", "Publish the summaries"):
            with self.subTest(step=name):
                self.assertIn(f"GH_TOKEN: ${{{{ steps.{minted}.outputs.token || steps.{hosted}.outputs.token || "
                              "github.token }}", steps[name])
                self.assertIn(f"{review.TOKEN_LOGIN}: ${{{{ steps.{minted}.outputs.app-slug && "
                              f"format('{{0}}[bot]', steps.{minted}.outputs.app-slug) || "
                              f"steps.{hosted}.outputs.login || '{cli.CI_REVIEWER}' }}}}", steps[name])
        self.assertIn(f"{summary.DISPATCH_TOKEN}: ${{{{ github.token }}}}", steps["Publish the summaries"],
                      "the rebuild event goes out on the workflow's token, which can send it")

    def test_only_the_review_step_hands_its_summary_off(self) -> None:
        # The summary job's publish must push; a handoff there would publish nothing.
        text = ACTION.read_text()
        review_step = text[text.index("    - name: Review the pull request"):text.index("    - name: Save the review")]
        self.assertEqual(1, text.count("PROJECTOR_SUMMARY_HANDOFF"))
        self.assertIn("PROJECTOR_SUMMARY_HANDOFF", review_step)

    def context_token(self, listed: str) -> str:
        """The `repositories` the action's context step hands the step that mints the context token, when the
        repository's setting lists `listed`."""
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (tmp / "project").write_text(f"#!/bin/sh\nprintf '%s\\n' '{listed}'\n")
        (tmp / "project").chmod(0o755)
        output = tmp / "output"
        output.write_text("")
        environment = {"PATH": f"{tmp}{os.pathsep}{os.environ['PATH']}", "GITHUB_REPOSITORY": "acme/app",
                       "GITHUB_OUTPUT": str(output), "PROJECTOR_ROOT": str(Path(__file__).parents[1])}
        result = subprocess.run(["bash", "-eo", "pipefail", "-c", step_script("Read the context repositories")],
                                cwd=tmp, env=environment, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        return dict(line.partition("=")[::2] for line in output.read_text().splitlines())["repositories"]

    def test_only_a_read_only_token_reaches_the_context_repositories_and_the_agent_never_gets_it(self) -> None:
        # The review's token writes comments and statuses, so on a context repository a pull request could steer
        # the agent to write there. Only a token that reads them may reach them, and only the run's clones use it.
        self.assertEqual("docs,lib", self.context_token("acme/docs, other/x, acme/app, acme/lib"))
        self.assertEqual("", self.context_token(""), "no context repositories mint no context token")
        self.assertEqual("", self.context_token("other/docs"))

        steps = {step.splitlines()[0].removeprefix("name: "): step for step in ACTION.read_text().split("\n    - ")[1:]}
        ids = {name: found.group(1) for name, step in steps.items()
               if (found := re.search(r"\n      id: (\S+)\n", step))}
        self.assertNotIn("repositories:", steps["Mint the app's token"], "the review's token reaches its repository")
        mint = steps["Mint the context token"]
        self.assertIn(f"if: steps.{ids['Read the context repositories']}.outputs.repositories != ''\n", mint)
        self.assertIn(f"repositories: ${{{{ steps.{ids['Read the context repositories']}.outputs.repositories }}}}\n",
                      mint)
        self.assertEqual(["permission-contents: read"],
                         [line.strip() for line in mint.splitlines() if line.strip().startswith("permission-")])
        # With an owner and no repositories, a token would reach every repository the installation covers.
        self.assertNotIn("owner:", mint)
        self.assertIn(f"{run.CONTEXT_TOKEN}: ${{{{ steps.{ids['Mint the context token']}.outputs.token }}}}\n",
                      steps["Review the pull request"])
        self.assertEqual(1, ACTION.read_text().count(f"steps.{ids['Mint the context token']}.outputs.token"),
                         "no other step gets the context token")
        # GitHub refuses the whole token over one repository the app cannot reach; the review goes on without
        # the context, which the run learns from the step's outcome.
        self.assertIn("continue-on-error: true\n", mint)
        self.assertIn(f"{run.CONTEXT_MINT}: ${{{{ steps.{ids['Mint the context token']}.outcome }}}}\n",
                      steps["Review the pull request"])

    def test_a_pull_request_cannot_add_a_context_repository_to_its_own_review(self) -> None:
        # The step that sets the token's reach reads the setting with `project config get`, which must take a
        # tracked .projector.toml from the default branch, not from the pull request's head. It finds the default
        # branch through origin/HEAD, which only the action's setup sets in a fresh checkout.
        steps = [step.splitlines()[0] for step in ACTION.read_text().split("\n    - ")[1:]]
        self.assertIn("git remote set-head origin --auto\n", step_script("Set up Projector"))
        self.assertLess(steps.index("name: Set up Projector"), steps.index("name: Read the context repositories"))
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        environment = {**os.environ, "HOME": str(tmp / "home"), "XDG_STATE_HOME": str(tmp / "state"),
                       "PYTHONPATH": str(Path(__file__).parents[1] / "src"), "GIT_CONFIG_GLOBAL": os.devnull}
        (tmp / "home").mkdir()

        def git(*args: str, cwd: Path = tmp / "checkout") -> None:
            subprocess.run(["git", *args], cwd=cwd, env=environment, check=True, capture_output=True)

        git("init", "-q", "--bare", "-b", "main", str(tmp / "origin.git"), cwd=tmp)
        git("clone", "-q", str(tmp / "origin.git"), str(tmp / "checkout"), cwd=tmp)
        git("config", "user.name", "Test")
        git("config", "user.email", "test@example.com")
        (tmp / "checkout" / ".projector.toml").write_text('[review]\ncontext_repos = "acme/docs"\n')
        git("add", ".projector.toml")
        git("commit", "-qm", "Read the docs")
        git("push", "-q", "origin", "HEAD:main")
        git("remote", "set-head", "origin", "--auto")
        git("checkout", "-qb", "widen")
        (tmp / "checkout" / ".projector.toml").write_text('[review]\ncontext_repos = "acme/docs, acme/secrets"\n')
        git("commit", "-qam", "Read the secrets too")

        found = subprocess.run([sys.executable, "-m", "projector", "config", "get", "review.context_repos",
                                "--default", ""], cwd=tmp / "checkout", env=environment, capture_output=True,
                               text=True, check=True).stdout
        self.assertEqual("acme/docs", found.strip())


if __name__ == "__main__":
    unittest.main()
