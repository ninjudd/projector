from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from projector import cli, permissions

# Each command line with the decision Claude Code and then Codex reach under
# the rules `init` writes, where "none" leaves the command to the host's usual
# approval.
DECISIONS = (
    ("project", "allow", "none"),
    ("project review publish --loop x", "allow", "allow"),
    ("project review hold 12 --drop", "allow", "allow"),
    ("project review finish 12", "allow", "allow"),
    ("project summary publish --summary summary.json", "allow", "allow"),
    ("project inbox ask question", "allow", "allow"),
    ("project list --json", "allow", "allow"),
    ("project site serve", "allow", "allow"),
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
    ("projector list", "none", "none"),
)


def claude_decision(command: str) -> str:
    """The decision Claude Code's permissions documentation gives `command` under Projector's rules.

    Ask rules come before allow rules and the first match decides. A `*`
    stands for any text, and a trailing ` *` that is a rule's only wildcard
    also matches the bare command.
    """

    for decision, rules in (("ask", permissions.ASK), ("allow", permissions.ALLOW)):
        for rule in rules:
            pattern = rule.removeprefix("Bash(").removesuffix(")")
            if pattern.endswith(" *") and pattern.count("*") == 1 and command == pattern[:-2]:
                return decision
            if re.fullmatch(".*".join(map(re.escape, pattern.split("*"))), command, re.DOTALL):
                return decision
    return "none"


def codex_decision(command: str) -> str:
    """The decision Codex's rules documentation gives `command`: a pattern matches a prefix of its tokens, where a
    list matches any one of its tokens."""

    tokens = command.split()
    for pattern in permissions.CODEX_PATTERNS:
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
                result = subprocess.run(["codex", "execpolicy", "check", "--rules", str(rules), *command.split()],
                                        check=True, capture_output=True, text=True,
                                        env={**os.environ, "CODEX_HOME": home.name})
                self.assertEqual(codex, json.loads(result.stdout).get("decision", "none"))

    def test_the_codex_rules_name_every_command_but_those_that_ask(self) -> None:
        commands = subcommands(cli.parser())
        self.assertEqual(set(commands) - {"init", "upgrade", "config"}, set(permissions.CODEX_COMMANDS),
                         "a new command is either allowed by name or left to ask")
        self.assertEqual(set(subcommands(commands["config"])) - {"set", "unset"},
                         set(permissions.CODEX_PATTERNS[1][2]))
