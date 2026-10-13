"""The permission rules `init` adds to the user's Claude Code settings and Codex rules.

The `project` command is how an agent is meant to change Projector's state:
plans, the inbox, loops, holds, reviews, and summaries. Each host's automatic
approval can refuse such a command that the user asked for, and a prompt for
one pushes an agent back toward editing the files by hand or calling `gh api`
itself. Claude Code's auto mode refuses a session's clean review of a pull
request its own GitHub account opened, as self-approval, although the review
is a COMMENT that moves no reviewDecision and a person still merges. It also
refuses the deletion of the comment that holds a pull request in draft, as a
write to an external system, which leaves a clean pull request stuck in draft.
Codex's approval reviewer refuses `project summary publish`, which sends a
pull request's diff to the repository's own site, as sending repository
content to another host.

So the rules allow every `project` command, and a new command needs no new
rule, except the few that reach outside Projector's own state, which still
ask: `project upgrade` runs an installer it downloads, `project init` writes
these rules, and `project config set` and `unset` can change a setting that
widens an agent's authority, such as `review.allow_approve`. The rules go in
the person's own settings, so they hold in every repository, clone, and
worktree they open, and no repository has to carry a permission for everyone
who opens it.

Claude Code evaluates deny rules, then ask rules, then allow rules, and the
first match decides, so an ask rule prompts for a command that `Bash(project *)`
also allows. A trailing ` *` matches the bare command too, so
`Bash(project init *)` asks for `project init` with or without arguments.
The CLI's own options, such as `--root`, come before its command, so
`project --root . init` matches the allow rule and no command's ask rule;
`Bash(project -*)` asks for every command line that starts with an option,
`project --version` included. Claude Code matches these rules against the
command text an agent writes, so they keep an agent from running a command
unasked, not from spelling it another way, such as with a quoted command name.

Codex's `prefix_rule` matches exact tokens with no wildcard, so no rule can
match an option such as `--root=.` before the command. Codex applies the
strictest decision when several rules match, but a `prompt` rule for
`["project", "init"]` would miss `project --root=. init` under a rule allowing
`["project"]`. The Codex rules therefore allow each command by name except
`init`, `upgrade`, and `config`, whose reading commands get a rule of their
own, and anything else, a command line that starts with an option included,
falls through to Codex's usual approval. A new top-level command reaches
Codex once `init` rewrites the file.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Optional

from .core import FileAction, write_through

ALLOW = ("Bash(project *)",)
ASK = ("Bash(project init *)", "Bash(project upgrade *)", "Bash(project config set *)",
       "Bash(project config unset *)", "Bash(project -*)")
# The narrower rules Projector wrote before `Bash(project *)` covered them,
# which `init` removes from `permissions.allow`.
SUPERSEDED = ("Bash(project review publish *)", "Bash(project review hold *)", "Bash(project review finish *)",
              "Bash(project review settle *)", "Bash(project summary publish *)")

ASKED = "`project init`, `project upgrade`, and `project config set` and `unset`"
COMMANDS = f"every `project` command except {ASKED}"

# Every top-level command but `init`, `upgrade`, and `config`, which the
# second pattern opens only for reading.
CODEX_COMMANDS = ("check", "create", "done", "edit", "inbox", "list", "loop", "owner", "priority", "review", "search",
                  "show", "site", "status", "summary")
CODEX_PATTERNS = (["project", list(CODEX_COMMANDS)], ["project", "config", ["get", "list", "paths"]])
CODEX_JUSTIFICATIONS = ("Runs a Projector command, which works on plans, the inbox, loops, holds, reviews, or "
                        "summaries.",
                        "Reads Projector's configuration.")
CODEX_PREFIX_RULES = tuple(f'prefix_rule(pattern = {json.dumps(pattern)}, decision = "allow")'
                           for pattern in CODEX_PATTERNS)
# Codex loads every `.rules` file in its rules directory, and writes the
# approvals a person saves to `default.rules`, so Projector keeps its rules in
# a file of its own and rewrites that file whole.
CODEX_RULES = (
    "# Written by `project init`, which replaces this file. Put your own rules in another file.\n"
    "# `project init`, `project upgrade`, `project config set` and `unset`, and a command line that starts with an\n"
    "# option are left to Codex's usual approval.\n"
    + "".join(f'prefix_rule(\n    pattern = {json.dumps(pattern)},\n    decision = "allow",\n'
              f'    justification = "{justification}",\n)\n'
              for pattern, justification in zip(CODEX_PATTERNS, CODEX_JUSTIFICATIONS))
)


def settings_path(environ: Mapping[str, str] = os.environ) -> Path:
    """The user's Claude Code settings: `$CLAUDE_CONFIG_DIR/settings.json`, or `~/.claude/settings.json`."""

    configured = environ.get("CLAUDE_CONFIG_DIR")
    if configured:
        return Path(configured).expanduser() / "settings.json"
    return Path(environ.get("HOME") or Path.home()) / ".claude" / "settings.json"


def codex_home(environ: Mapping[str, str] = os.environ) -> Path:
    """Codex's configuration directory: `$CODEX_HOME`, or `~/.codex`."""

    configured = environ.get("CODEX_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path(environ.get("HOME") or Path.home()) / ".codex"


def shown(path: Path, environ: Mapping[str, str] = os.environ) -> str:
    """`path` as `init` reports it, with the home directory written as `~`."""

    home = Path(environ.get("HOME") or Path.home())
    return f"~/{path.relative_to(home).as_posix()}" if path.is_relative_to(home) else str(path)


def read_user_file(path: Path, environ: Mapping[str, str] = os.environ) -> tuple[str, Optional[str]]:
    """The text of one of the user's files, "" when it does not exist, and why `init` cannot read it, if it cannot."""

    if path.parent.exists() and not path.parent.is_dir():
        return "", f"cannot be written, because {shown(path.parent, environ)} is not a directory"
    if not path.exists():
        return "", None
    try:
        return path.read_text(encoding="utf-8"), None
    except UnicodeDecodeError:
        return "", "is not UTF-8"
    except OSError as error:
        return "", f"could not be read ({error.strerror})"


def allow_commands(apply: bool = True, environ: Mapping[str, str] = os.environ,
                   hint: bool = True) -> list[FileAction]:
    """Add Projector's permission rules for Claude Code, and for Codex where Codex is installed.

    Without `apply`, a file that lacks the rules is only reported, with how to
    add them unless `hint` is false.
    """

    actions = [allow_in_claude(apply, environ, hint)]
    codex = allow_in_codex(apply, environ, hint)
    if codex is not None:
        actions.append(codex)
    return actions


def allow_in_claude(apply: bool = True, environ: Mapping[str, str] = os.environ, hint: bool = True) -> FileAction:
    """Add `ALLOW` to `permissions.allow` and `ASK` to `permissions.ask` in the user's Claude Code settings.

    The `SUPERSEDED` rules leave `permissions.allow`, and everything else in
    the file is kept. A file linked into a dotfiles repository is written
    through its link. A file `init` cannot read as settings is left alone with
    a note. Without `apply`, only a missing rule is reported, because a
    superseded rule left in place allows nothing that `ALLOW` does not.
    """

    path = settings_path(environ)
    name = shown(path, environ)

    def kept(reason: str) -> FileAction:
        return FileAction(name, "kept", f"{name} {reason}, so the rules allowing {COMMANDS} were not added; add "
                                        f"{', '.join(ALLOW)} to permissions.allow and {', '.join(ASK)} to "
                                        "permissions.ask yourself")

    existed = path.exists()
    text, unreadable = read_user_file(path, environ)
    if unreadable:
        return kept(unreadable)
    try:
        settings = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        return kept("is not valid JSON")
    if not isinstance(settings, dict):
        return kept("does not hold a JSON object")
    permissions = settings.setdefault("permissions", {})
    if not isinstance(permissions, dict):
        return kept("has a `permissions` that is not an object")
    for key in ("allow", "ask"):
        if not isinstance(permissions.setdefault(key, []), list):
            return kept(f"has a `permissions.{key}` that is not a list")
    allow, ask = permissions["allow"], permissions["ask"]
    missing_allow = [rule for rule in ALLOW if rule not in allow]
    missing_ask = [rule for rule in ASK if rule not in ask]
    superseded = any(rule in SUPERSEDED for rule in allow)
    if not missing_allow and not missing_ask and not (apply and superseded):
        return FileAction(name, "unchanged")
    if not apply:
        consequence = ("Claude Code can ask before, or its auto mode refuse, a Projector command an agent runs, such "
                       "as a review loop's clean review of your own pull request, the end of a hold on it, or the "
                       "publish of a summary, unless the repository's own settings allow it"
                       if missing_allow else f"an agent can run {ASKED} without asking you")
        return FileAction(name, "kept", f"{name} lacks {', '.join(missing_allow + missing_ask)}, without which "
                                        f"{consequence}"
                                        + ("; run `project init` yourself in a terminal, or pass --publish-rule, to "
                                           "add them" if hint else ""))
    allow[:] = [rule for rule in allow if rule not in SUPERSEDED] + missing_allow
    ask.extend(missing_ask)
    try:
        write_through(path, json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    except OSError as error:
        # A file linked into a read-only directory, such as the Nix store.
        return kept(f"could not be written ({error.strerror})")
    return FileAction(name, "updated" if existed else "created")


def allow_in_codex(apply: bool = True, environ: Mapping[str, str] = os.environ,
                   hint: bool = True) -> Optional[FileAction]:
    """Write Projector's rules file into Codex's rules directory, or None where Codex is not installed.

    Codex is taken to be installed when its configuration directory exists, so
    `init` never creates one for a person who does not use Codex. A file linked
    elsewhere is written through its link.
    """

    home = codex_home(environ)
    if not home.is_dir():
        return None
    path = home / "rules" / "projector.rules"
    name = shown(path, environ)

    def kept(reason: str) -> FileAction:
        return FileAction(name, "kept", f"{name} {reason}, so the Codex rules allowing {COMMANDS} were not added; "
                                        f"add {' and '.join(CODEX_PREFIX_RULES)} to a .rules file in "
                                        f"{shown(path.parent, environ)} yourself")

    existed = path.exists()
    text, unreadable = read_user_file(path, environ)
    if unreadable:
        return kept(unreadable)
    if text == CODEX_RULES:
        return FileAction(name, "unchanged")
    if not apply:
        return FileAction(name, "kept", f"{name} lacks Projector's current rules, without which Codex can ask "
                                        "before, or its approval reviewer refuse, a Projector command an agent runs, "
                                        "such as `project summary publish`"
                                        + ("; run `project init` yourself in a terminal, or pass --publish-rule, to "
                                           "add it" if hint else ""))
    try:
        write_through(path, CODEX_RULES)
    except OSError as error:
        return kept(f"could not be written ({error.strerror})")
    return FileAction(name, "updated" if existed else "created")
