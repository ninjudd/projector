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
    ("project review hold 12 --drop", "allow", "allow"),
    ("project review setup 12 --model claude", "allow", "allow"),
    ("project review finish 12 --force", "allow", "allow"),
    ("project review settle 12 --thread T --result fixed --body reply.md", "allow", "allow"),
    ("project review suggestions 12 --json", "allow", "allow"),
    ("project review gate 12", "allow", "none"),
    ("project summary publish --summary summary.json", "allow", "allow"),
    ("project inbox ask question --recommend yes --session s1", "allow", "allow"),
    ("project inbox tell notice --session s1", "allow", "allow"),
    ("project inbox withdraw 3 --session s1", "allow", "allow"),
    ("project inbox revise 3 --question 'Merge it now?' --session s1", "allow", "allow"),
    ("project inbox watch --session s1", "allow", "allow"),
    ("project inbox claim 3 --session s1", "allow", "allow"),
    ("project inbox answer 3.a --session s1", "ask", "none"),
    ("project inbox answer 3.c --text yes", "ask", "none"),
    ("PROJECTOR_SESSION=s1 project inbox answer 3.a", "ask", "none"),
    ("project inbox dismiss 3 --session s1", "ask", "none"),
    ("project inbox snooze 3 --for 30", "ask", "none"),
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
    ("project --root . init", "ask", "none"),
    ("project --root=. upgrade", "ask", "none"),
    ("project --projects-dir=plans config set review.allow_approve true", "ask", "none"),
    ("project --root=. list", "ask", "none"),
    ("project --version", "ask", "none"),
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
    ("project --root . site serve --prepare make", "ask", "none"),
    ("project edit plan", "allow", "none"),
    ("project create plan --no-edit", "allow", "none"),
    ("VISUAL='sh -c make' project edit plan", "none", "none"),
    ("EDITOR=make project create plan", "none", "none"),
    ("PATH=/tmp/bin:/usr/bin project list", "none", "none"),
    ("XDG_STATE_HOME=/tmp/state project site build --out site", "none", "none"),
    ("LANG=C project list", "allow", "none"),
    ("HOME=/tmp project init --publish-rule", "ask", "none"),
    ("projector list", "none", "none"),
)

# What the Codex rules leave to Codex's usual approval: each command with the
# subcommands left out, or None where the whole command is.
CODEX_LEFT_OUT = {"init": None, "upgrade": None, "create": None, "edit": None,
                  "config": {"set", "unset"}, "inbox": {"answer", "dismiss", "snooze"}, "review": {"gate"},
                  "site": {"build", "serve"}}

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
                result = subprocess.run(["codex", "execpolicy", "check", "--rules", str(rules),
                                         *shlex.split(command)],
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

    def test_each_command_line_is_one_the_parser_runs(self) -> None:
        incomplete = {"project", "project config", "project --version", "projector list"}
        for command, _, _ in DECISIONS:
            if command in incomplete:
                continue
            with self.subTest(command):
                words = shlex.split(command)
                while re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0], re.DOTALL):
                    words.pop(0)
                cli.parser().parse_args(words[1:])

    def test_site_build_and_serve_take_the_prepare_flags_only_spelled_in_full(self) -> None:
        for command in (["site", "build", "--out", "site"], ["site", "serve"]):
            for abbreviated in (["--prep", "make"], ["--allow"], ["--allow-prep"], ["--no-prep"]):
                with self.subTest(command=command[1], flag=abbreviated[0]):
                    with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as stop:
                        cli.parser().parse_args([*command, *abbreviated])
                    self.assertEqual(2, stop.exception.code)
            self.assertEqual("make", cli.parser().parse_args([*command, "--prepare=make"]).prepare)
            self.assertEqual("make", cli.parser().parse_args([*command, "--prepare", "make"]).prepare)
            self.assertTrue(cli.parser().parse_args([*command, "--allow-prepare"]).allow_prepare)
