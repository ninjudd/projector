from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

from projector import cli, permissions

# Each command line with the decision Claude Code and then Codex reach under
# the rules `init` writes, where "none" leaves the command to the host's usual
# approval.
DECISIONS = (
    ("project", "allow", "none"),
    ("project review publish 12 --body body.md --loop x", "allow", "allow"),
    ("project review hold 12 --repo acme/app", "allow", "allow"),
    ("project review ready 12 --repo acme/app", "allow", "allow"),
    ("project review setup 12 --model claude", "allow", "allow"),
    ("project review finish 12 --force", "allow", "allow"),
    ("project review prune --apply", "allow", "allow"),
    ("project review settle 12 --thread T --result fixed --body reply.md", "allow", "allow"),
    ("project review suggestions 12 --json", "allow", "allow"),
    ("project review workflow", "allow", "allow"),
    ("project review gate 12", "allow", "none"),
    ("project worktree 12", "allow", "allow"),
    ("project worktree fix/login", "allow", "allow"),
    ("project summary publish --summary summary.json", "allow", "allow"),
    ("project inbox ask question --recommend yes --session s1", "allow", "allow"),
    ("project inbox tell notice --session s1", "allow", "allow"),
    ("project inbox request 'Test the gutters' --detail '1. Open Fyra.' --session s1", "allow", "allow"),
    ("project inbox withdraw 3 --session s1", "allow", "allow"),
    ("project inbox restore 3 --session s1", "allow", "allow"),
    ("project inbox revise 3 --question 'Merge it now?' --session s1", "allow", "allow"),
    ("project inbox revise 3 --notice 'Pushed the fix to the branch' --session s1", "allow", "allow"),
    ("project inbox watch --session s1", "allow", "allow"),
    ("project inbox claim 3 --session s1", "allow", "allow"),
    ("project inbox answer 3.a --session s1", "ask", "none"),
    ("project inbox answer 3.c --text yes", "ask", "none"),
    ("PROJECTOR_SESSION=s1 project inbox answer 3.a", "ask", "none"),
    ("project inbox dismiss 3 --session s1", "ask", "none"),
    ("project inbox snooze 3 --for 30", "ask", "none"),
    ("project inbox done 3 --session s1", "ask", "none"),
    ("project inbox done 3 --text 'All good'", "ask", "none"),
    ("project inbox decline 3 --session s1", "ask", "none"),
    ("project inbox start 3 --action 'Install the build' --session s1", "ask", "none"),
    ("project inbox unclear 3 --subject 'Pushed the fix' --detail '' --session s1", "ask", "none"),
    ("project inbox ready 3 --session s1", "allow", "allow"),
    ("project inbox block 3 --reason 'Unlock the phone' --session s1", "allow", "allow"),
    ("project inbox discuss 3 --text 'Why decline it?' --session s1", "ask", "none"),
    ("project inbox discuss --pr acme/app#12 --text hello --session s1", "ask", "none"),
    ("project inbox read 3 --session s1", "ask", "none"),
    ("project inbox reply 3 --text 'The cap bounds retries.' --session s1", "allow", "allow"),
    ("project console", "ask", "none"),
    ("project list --json", "allow", "allow"),
    ("project config get review.username", "allow", "allow"),
    ("project config list", "allow", "allow"),
    ("project config paths", "allow", "allow"),
    ("project config", "allow", "none"),
    ("project init", "ask", "none"),
    ("project init --publish-rule", "ask", "none"),
    ("project upgrade", "ask", "none"),
    ("project upgrade status", "ask", "none"),
    ("project config set review.allow_approve true", "ask", "none"),
    ("project config unset review.allow_approve", "ask", "none"),
    ("project init --root .", "ask", "none"),
    ("project config set review.allow_approve true --root=.", "ask", "none"),
    ("project list --root=. --projects-dir=plans", "allow", "allow"),
    ("project version", "allow", "allow"),
    ("project help review publish", "allow", "allow"),
    # A line that starts with an option matches no command's ask rule, and the
    # CLI refuses it.
    ("project --root . init", "allow", "none"),
    ("project --projects-dir=plans config set review.allow_approve true", "allow", "none"),
    ("project -- init", "allow", "none"),
    ("project --version", "allow", "none"),
    ("project --help", "allow", "none"),
    ("project site page --summary summary.json --out page", "allow", "allow"),
    ("project site status --repo owner/name", "allow", "allow"),
    ("project site workflow --write", "allow", "allow"),
    ("project site build --out site", "allow", "none"),
    ("project site serve", "allow", "none"),
    ("project site build --out site --prepare 'make docs'", "ask", "none"),
    ("project site build --out site --prepare=make", "ask", "none"),
    ("project site serve --prepare make", "ask", "none"),
    ("project site serve --allow-prepare", "ask", "none"),
    ("project site build --out site --no-prepare", "ask", "none"),
    ("project --root . site serve --prepare make", "allow", "none"),
    ("project edit plan", "allow", "none"),
    ("project create plan --no-edit", "allow", "none"),
    ("VISUAL='sh -c make' project edit plan", "none", "none"),
    ("EDITOR=make project create plan", "none", "none"),
    ("PATH=/tmp/bin:/usr/bin project list", "none", "none"),
    ("XDG_STATE_HOME=/tmp/state project site build --out site", "none", "none"),
    ("LANG=C project list", "allow", "none"),
    ("HOME=/tmp project init --publish-rule", "ask", "none"),
    ("projector list", "none", "none"),
    # A path to a program named `project` matches no rule: Claude Code matches
    # the command text, and Codex resolves the path to its basename only for
    # a path a `host_executable` entry lists, and the rules list none.
    ("/tmp/x/project summary publish --summary summary.json", "none", "none"),
    ("/usr/local/bin/project list --json", "none", "none"),
    ("./project review publish 12 --body body.md", "none", "none"),
    ("project ios devices --json", "allow", "allow"),
    ("project ios install 12 --device 00008110-0001", "allow", "allow"),
    ("project ios crashes --mac", "allow", "allow"),
    ("project ios cleanup", "allow", "allow"),
    ("project ios build 12 --no-install", "allow", "none"),
    ("project ios build feature/login --mac", "allow", "none"),
    ("project ios build 12 --allow-untrusted", "ask", "none"),
    ("project ios build --allow-untrusted 12 --ipad", "ask", "none"),
    ("project ios logs --last 20m", "allow", "none"),
)

# What the Codex rules leave to Codex's usual approval: each command with the
# subcommands left out, or None where the whole command is.
CODEX_LEFT_OUT = {"init": None, "upgrade": None, "create": None, "edit": None, "console": None, "agent": {"key"},
                  "config": {"set", "unset"},
                  "inbox": {"answer", "decline", "discuss", "dismiss", "done", "read", "snooze", "start", "unclear"},
                  "review": {"gate"},
                  "site": {"build", "serve"}, "ios": {"build", "logs"}}

# Some of the variables Claude Code lets an allow rule match past. Its list is
# fixed, and holds none of VISUAL, EDITOR, PATH, HOME, or XDG_STATE_HOME.
SAFE_VARIABLES = {"LANG", "NODE_ENV", "TZ"}


def claude_decision(command: str) -> str:
    """The decision Claude Code's permissions documentation gives `command` under Projector's rules.

    Ask rules come before allow rules and the first match decides. A `*`
    stands for any text, and a trailing ` *` that is a rule's only wildcard
    also matches the bare command. An ask rule matches past any leading
    variable assignment, and an allow rule only past one of a few variables.
    """

    words = shlex.split(command)
    assigned = set()
    while words and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0], re.DOTALL):
        assigned.add(words.pop(0).split("=", 1)[0])
    text = " ".join(words)
    for decision, rules in (("ask", permissions.ASK), ("allow", permissions.ALLOW)):
        if decision == "allow" and not assigned <= SAFE_VARIABLES:
            continue
        for rule in rules:
            pattern = rule.removeprefix("Bash(").removesuffix(")")
            if pattern.endswith(" *") and pattern.count("*") == 1 and text == pattern[:-2]:
                return decision
            if re.fullmatch(".*".join(map(re.escape, pattern.split("*"))), text, re.DOTALL):
                return decision
    return "none"


def codex_decision(command: str) -> str:
    """The decision Codex's rules documentation gives `command`: a pattern matches a prefix of its tokens, where a
    list matches any one of its tokens."""

    tokens = shlex.split(command)
    for pattern, _ in permissions.CODEX_ALLOW:
        if len(tokens) >= len(pattern) and all(
                token in (part if isinstance(part, list) else [part]) for token, part in zip(tokens, pattern)):
            return "allow"
    return "none"


def subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    return next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction)).choices


class PermissionRulesTests(unittest.TestCase):
    def test_claude_code_allows_projector_commands_and_asks_before_the_rest(self) -> None:
        for command, claude, _ in DECISIONS:
            with self.subTest(command):
                self.assertEqual(claude, claude_decision(command))

    def test_codex_allows_projector_commands_by_name(self) -> None:
        for command, _, codex in DECISIONS:
            with self.subTest(command):
                self.assertEqual(codex, codex_decision(command))

    @unittest.skipUnless(shutil.which("codex"), "checking the Codex rules needs the codex CLI")
    def test_codex_itself_reaches_the_same_decisions(self) -> None:
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        rules = Path(home.name) / "projector.rules"
        rules.write_text(permissions.CODEX_RULES)
        for command, _, codex in DECISIONS:
            with self.subTest(command):
                # A live session resolves program paths, which the check does only when asked.
                result = subprocess.run(["codex", "execpolicy", "check", "--resolve-host-executables",
                                         "--rules", str(rules), *shlex.split(command)],
                                        check=True, capture_output=True, text=True,
                                        env={**os.environ, "CODEX_HOME": home.name})
                self.assertEqual(codex, json.loads(result.stdout).get("decision", "none"))

    def test_the_codex_rules_name_every_command_or_leave_it_out(self) -> None:
        (_, whole), *rest = (pattern for pattern, _ in permissions.CODEX_ALLOW)
        partial = {command: set(names) for _, command, names in rest}
        commands = subcommands(cli.parser())
        self.assertLessEqual(set(whole) | set(partial), set(commands))
        for command, parser in commands.items():
            with self.subTest(command):
                if command in whole:
                    self.assertNotIn(command, CODEX_LEFT_OUT)
                elif command in partial:
                    names = set(subcommands(parser))
                    self.assertLessEqual(partial[command], names)
                    self.assertEqual(CODEX_LEFT_OUT[command] & names, names - partial[command],
                                     "a new subcommand is either allowed by name or deliberately left out")
                else:
                    self.assertIn(command, CODEX_LEFT_OUT, "a new command is either allowed or deliberately left out")
                    self.assertIsNone(CODEX_LEFT_OUT[command])

    def test_the_documented_codex_rules_are_the_ones_init_writes(self) -> None:
        documentation = (Path(__file__).resolve().parents[1] / "docs" / "cli.md").read_text()
        documented = re.findall(r"^ *pattern = (\[.*\]),$", documentation, re.MULTILINE)

        self.assertEqual([pattern for pattern, _ in permissions.CODEX_ALLOW], list(map(json.loads, documented)))
        self.assertIn(f"  {permissions.CODEX_HOST_EXECUTABLE}\n", documentation)
        self.assertTrue(permissions.CODEX_RULES.endswith(permissions.CODEX_HOST_EXECUTABLE + "\n"))

    def test_each_command_line_is_one_the_parser_runs_or_refuses_for_its_leading_option(self) -> None:
        incomplete = {"project", "project config", "projector list"}
        parser = cli.parser()
        for command, _, _ in DECISIONS:
            if command in incomplete:
                continue
            with self.subTest(command):
                words = shlex.split(command)
                while re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0], re.DOTALL):
                    words.pop(0)
                if not words[1].startswith("-"):
                    parser.parse_args(words[1:])
                    continue
                stderr = StringIO()
                with redirect_stderr(stderr), self.assertRaises(SystemExit) as stop:
                    parser.parse_args(words[1:])
                self.assertEqual(2, stop.exception.code)
                self.assertIn(f"error: {words[1]}: options go after the command", stderr.getvalue())

    def test_site_build_and_serve_take_the_prepare_flags_only_spelled_in_full(self) -> None:
        parser = cli.parser()
        for command in (["site", "build", "--out", "site"], ["site", "serve"]):
            for abbreviated in (["--prep", "make"], ["--allow"], ["--allow-prep"], ["--no-prep"]):
                with self.subTest(command=command[1], flag=abbreviated[0]):
                    with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as stop:
                        parser.parse_args([*command, *abbreviated])
                    self.assertEqual(2, stop.exception.code)
            self.assertEqual("make", parser.parse_args([*command, "--prepare=make"]).prepare)
            self.assertEqual("make", parser.parse_args([*command, "--prepare", "make"]).prepare)
            self.assertTrue(parser.parse_args([*command, "--allow-prepare"]).allow_prepare)

    def test_ios_build_takes_allow_untrusted_only_spelled_in_full(self) -> None:
        parser = cli.parser()
        self.assertTrue(parser.parse_args(["ios", "build", "12", "--allow-untrusted"]).allow_untrusted)
        for abbreviated in ("--allow", "--allow-unt", "--allow-untrust"):
            with self.subTest(abbreviated):
                with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as stop:
                    parser.parse_args(["ios", "build", "12", abbreviated])
                self.assertEqual(2, stop.exception.code)


SKILLS = Path(__file__).resolve().parents[1] / "skills"


def flat(path: Path) -> str:
    """A skill's text with its line breaks folded, so a phrase reads the same wherever the lines wrap."""
    return " ".join(path.read_text().split())


class SummaryPublishTests(unittest.TestCase):
    """The summary publish the skills run is one the generated rules allow, run as a call of its own, and a
    refused one is diagnosed rather than sent back through `project init`."""

    def publish_lines(self) -> list[str]:
        skill = (SKILLS / "summarize-pr" / "SKILL.md").read_text()
        lines = re.findall(r"^project summary publish .*$", skill, re.MULTILINE)
        self.assertIn("project summary publish --summary WORKDIR/summary.json", lines)
        return lines

    def test_the_generated_rules_allow_the_publish_lines_the_skill_runs(self) -> None:
        for line in self.publish_lines():
            with self.subTest(line):
                self.assertEqual(("allow", "allow"), (claude_decision(line), codex_decision(line)))
                self.assertTrue(permissions.codex_allows(permissions.CODEX_RULES, shlex.split(line)))
                self.assertTrue(permissions.codex_allows("\n".join(permissions.CODEX_PREFIX_RULES),
                                                         shlex.split(line)))
        self.assertTrue(permissions.codex_allows(permissions.CODEX_RULES, permissions.PUBLISH))
        for command in ("project review gate 12", "project init", "project site build --out site",
                        "gh api repos/acme/app/pages", "project"):
            with self.subTest(command):
                self.assertEqual(codex_decision(command) == "allow",
                                 permissions.codex_allows(permissions.CODEX_RULES, shlex.split(command)))

    @unittest.skipUnless(shutil.which("codex"), "checking the Codex rules needs the codex CLI")
    def test_codex_itself_allows_the_publish_lines_the_skill_runs(self) -> None:
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        rules = Path(home.name) / "projector.rules"
        rules.write_text(permissions.CODEX_RULES)

        for line in self.publish_lines():
            with self.subTest(line):
                result = subprocess.run(["codex", "execpolicy", "check", "--rules", str(rules), *shlex.split(line)],
                                        check=True, capture_output=True, text=True,
                                        env={**os.environ, "CODEX_HOME": home.name})

                decision = json.loads(result.stdout)
                self.assertEqual("allow", decision["decision"])
                self.assertEqual(["project", "summary"],
                                 decision["matchedRules"][0]["prefixRuleMatch"]["matchedPrefix"])

    def test_the_skill_publishes_alone_with_no_visibility_check_joined_to_it(self) -> None:
        # Both hosts check each command of a chain and take the strictest result, so a visibility check joined
        # to the publish sends the whole call to automatic approval, which can refuse it.
        skill = flat(SKILLS / "summarize-pr" / "SKILL.md")

        self.assertNotRegex(skill, r"(&&|;|\|)\s*project summary publish")
        self.assertNotRegex(skill, r"project summary publish[^`]*(&&|;|\|)")


@unittest.skipIf(os.name == "nt", "the fake codex is a shell script")
class CodexVersionTests(unittest.TestCase):
    """`init` reads the version of the `codex` on `PATH` to decide whether the rules can hold `host_executable`."""

    def version_with(self, script: str | None) -> tuple[int, ...] | None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        if script is not None:
            codex = Path(folder.name) / "codex"
            codex.write_text(f"#!/bin/sh\n{script}\n")
            codex.chmod(0o755)
        return permissions.codex_version({"PATH": folder.name})

    def test_the_version_comes_from_codex_version(self) -> None:
        self.assertEqual((0, 106, 0), self.version_with('echo "codex-cli 0.106.0"'))
        self.assertEqual((0, 169, 2), self.version_with('echo "codex-cli 0.169.2-alpha.1"'))

    def test_a_codex_that_says_no_version_reads_as_unknown(self) -> None:
        for name, script in {"missing": None, "failing": 'echo "codex-cli 0.1.0"; exit 1',
                             "silent": "true", "no version": 'echo "codex-cli dev"'}.items():
            with self.subTest(name):
                self.assertIsNone(self.version_with(script))

    def test_an_old_codex_gets_the_rules_without_the_line_it_cannot_parse(self) -> None:
        self.assertEqual(permissions.CODEX_RULES_WITHOUT_HOST_EXECUTABLE, permissions.codex_rules((0, 106, 9)))
        for version in ((0, 107, 0), (0, 169, 0), (1, 0, 0), None):
            with self.subTest(version=version):
                self.assertEqual(permissions.CODEX_RULES, permissions.codex_rules(version))
