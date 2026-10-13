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
these rules, `project config set` and `unset` can change a setting that
widens an agent's authority, such as `review.allow_approve`, and
`project site build` and `serve` run the shell command `--prepare` names, or
the checkout's own `site.prepare` once `--allow-prepare` allows it. Ask rules
also cover what only the person may record: `project inbox answer`,
`dismiss`, and `snooze` record the person's answer to an item an agent
posted, or their choice to dismiss or put it off, so an agent running one
could answer its own question. Projector's band runs those commands itself,
not through an agent's shell, so the person answering from the band is never
asked. The rules go in the person's own settings, so they hold in every
repository, clone, and worktree they open, and no repository has to carry a
permission for everyone who opens it.

Claude Code evaluates deny rules, then ask rules, then allow rules, and the
first match decides, so an ask rule prompts for a command that `Bash(project *)`
also allows. A trailing ` *` matches the bare command too, so
`Bash(project init *)` asks for `project init` with or without arguments.
The CLI's own options, such as `--root`, come before its command, so
`project --root . init` matches the allow rule and no command's ask rule;
`Bash(project -*)` asks for every command line that starts with an option,
`project --version` included. `Bash(project site *prepare*)` asks for both
`--prepare` flags in either spelling, `--prepare x` or `--prepare=x`, and
`site build` and `serve` refuse an abbreviated option such as `--prep`, so
no shorter spelling slips past it; it asks for `--no-prepare` too.

Two commands open the person's editor, `$VISUAL` or `$EDITOR`: `project edit`,
and `project create` without `--no-edit`. They need not ask. Each opens the
editor only at a terminal, which Claude Code gives no command, and Claude
Code lets an allow rule match past a leading variable assignment only for a
fixed list of variables, such as `LANG`, that holds neither of those, nor
`PATH` or `HOME`. So `VISUAL='sh -c …' project edit x` is not allowed by
`Bash(project *)`, and neither is any command that picks a program Projector
runs. `project review gate` runs the checks `review.gate` configures, as a
review loop does on every review, and is allowed. Claude Code matches these
rules against the command text an agent writes, so they keep an agent from
running a command unasked, not from spelling it another way, such as with a
quoted command name.

Codex's `prefix_rule` matches exact tokens with no wildcard, so no rule can
match an option such as `--root=.` before the command. Codex applies the
strictest decision when several rules match, but a `prompt` rule for
`["project", "init"]` would miss `project --root=. init` under a rule allowing
`["project"]`. The Codex rules therefore allow commands by name, and anything
else, a command line that starts with an option included, falls through to
Codex's usual approval. Codex runs an allowed command outside its sandbox,
and can give it a terminal the agent types into, so the rules also leave out
every command that can run another program: `create` and `edit`, whose
editor an agent could drive, `review gate`, and `site build` and `serve`.
They leave out `inbox answer`, `dismiss`, and `snooze` too. A new command
reaches Codex once it is added here and `init` rewrites the file.
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
       "Bash(project config unset *)", "Bash(project site *prepare*)", "Bash(project inbox answer *)",
       "Bash(project inbox dismiss *)", "Bash(project inbox snooze *)", "Bash(project -*)")
# The narrower rules Projector wrote before `Bash(project *)` covered them,
# which `init` removes from `permissions.allow`.
SUPERSEDED = ("Bash(project review publish *)", "Bash(project review hold *)", "Bash(project review finish *)",
              "Bash(project review settle *)", "Bash(project summary publish *)")

ASKED = ("`project init`, `project upgrade`, `project config set` and `unset`, `project site` with "
         "`--prepare` or `--allow-prepare`, and `project inbox answer`, `dismiss`, and `snooze`")
COMMANDS = "every `project` command but the few that reach outside Projector's own state"

# Each pattern Codex may run without asking, and outside its sandbox, with the
# justification Codex shows for it.
CODEX_ALLOW = (
    (["project", ["check", "done", "list", "loop", "owner", "priority", "search", "show", "status", "summary"]],
     "Runs a Projector command that works on plans, loops, or pull request summaries."),
    (["project", "config", ["get", "list", "paths"]], "Reads Projector's configuration."),
    (["project", "inbox", ["ask", "claim", "delivered", "list", "reopen", "show", "tell", "watch", "withdraw"]],
     "Posts, reads, withdraws, or delivers an inbox item, but never records the person's answer to one."),
    (["project", "review", ["census", "hold", "interdiff", "move", "publish", "release", "rules", "setup",
                            "status"]],
     "Runs a step of a Projector review other than its gate, which runs the repository's own checks."),
    (["project", "site", ["page", "status", "workflow"]],
     "Builds a summary page, says where the site is hosted, or writes the site's workflow file."),
)
CODEX_PREFIX_RULES = tuple(f'prefix_rule(pattern = {json.dumps(pattern)}, decision = "allow")'
                           for pattern, _ in CODEX_ALLOW)
# Codex loads every `.rules` file in its rules directory, and writes the
# approvals a person saves to `default.rules`, so Projector keeps its rules in
# a file of its own and rewrites that file whole.
CODEX_RULES = (
    "# Written by `project init`, which replaces this file. Put your own rules in another file.\n"
    "# Codex's usual approval decides every command these rules leave out: `project init`, `project upgrade`,\n"
    "# `project config set` and `unset`, `project create` and `project edit`, which can open an editor,\n"
    "# `project review gate` and `project site build` and `serve`, which run a command the repository or\n"
    "# the command line names, `project inbox answer`, `dismiss`, and `snooze`, which record the person's\n"
    "# choice, and a command line that starts with an option.\n"
    + "".join(f'prefix_rule(\n    pattern = {json.dumps(pattern)},\n    decision = "allow",\n'
              f'    justification = "{justification}",\n)\n'
              for pattern, justification in CODEX_ALLOW)
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
