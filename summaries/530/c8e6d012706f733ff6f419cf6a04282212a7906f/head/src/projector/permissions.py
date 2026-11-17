"""The permission rules `init` adds to the user's Claude Code settings and Codex rules.

The `project` command is how an agent is meant to change Projector's state:
plans, the inbox, loops, holds, reviews, and summaries. Each host's automatic
approval can refuse such a command that the user asked for, and a prompt for
one pushes an agent back toward editing the files by hand or calling `gh api`
itself. Claude Code's auto mode refuses a session's clean review of a pull
request its own GitHub account opened, as self-approval, although the review
is a COMMENT that moves no reviewDecision and a person still merges. It also
refuses the deletion of a comment, as a write to an external system: the
comment that holds a pull request in draft, which leaves a clean pull request
stuck in draft, and a finished review's start comment, which leaves the pull
request saying a review is in progress. The reply and resolution that settle
a finding thread are writes of the same kind. Codex's approval reviewer
refuses `project summary publish`, which sends a
pull request's diff to the repository's own site, as sending repository
content to another host.

So the rules allow every `project` command, and a new command needs no new
rule, except the few that reach outside Projector's own state, which still
ask: `project upgrade` runs an installer it downloads, `project init` writes
these rules, `project config set` and `unset` can change a setting that
widens an agent's authority, such as `review.allow_approve`, and
`project site build` and `serve` run the shell command `--prepare` names, or
the checkout's own `site.prepare` once `--allow-prepare` allows it, and
`project ios build --allow-untrusted` runs the build scripts of a pull request
whose head `review.trusted_head` does not trust. Ask rules
also cover what only the person may record: `project inbox answer`,
`dismiss`, `snooze`, `done`, `decline`, `start`, and `unclear` record the
person's answer to an item an agent posted, their outcome for a request, their
consent to a request's start action, their word that they don't understand an
item, or their choice to dismiss or put it off, so an agent running one could
answer its own question, mark its own request done, consent to its own start
action, or mark its own item unclear. `project inbox discuss` records the
person's words in an item's thread, which reach the agent with the person's
authority, and `project inbox read` records that the person read the agent's
replies, so both ask too: an agent running one could put words in the
person's mouth or hide its own reply. `project console` records all of
these from a terminal, so it asks too: it refuses to start without a
terminal, which Claude Code gives no command today, and the rule covers a
mode that gives one. `project inbox reply` records the
agent's own words, so it needs no ask rule. Projector's panel runs those commands
itself, not through an agent's shell, so the person answering from the panel is
never asked. `project inbox restore` needs no ask rule: it reopens an item the
person dismissed or snoozed, which takes no choice from them, and an agent
runs it when the person says a dismissal was a mistake. The rules go in the
person's own settings, so they hold in every
repository, clone, and worktree they open, and no repository has to carry a
permission for everyone who opens it.

Claude Code evaluates deny rules, then ask rules, then allow rules, and the
first match decides, so an ask rule prompts for a command that `Bash(project *)`
also allows. A trailing ` *` matches the bare command too, so
`Bash(project init *)` asks for `project init` with or without arguments.
The CLI refuses an option before the command, so every command line it runs
starts with its command, and the command's own ask rule matches it wherever
its options go: `Bash(project init *)` asks for `project init --root .`. A
line that starts with an option, such as `project --root . init`, matches
only the allow rule, and the CLI refuses it. `Bash(project site *prepare*)`
asks for both `--prepare` flags in either spelling, `--prepare x` or
`--prepare=x`, and `site build` and `serve` refuse an abbreviated option such
as `--prep`, so no shorter spelling slips past it; it asks for `--no-prepare`
too. `Bash(project ios *allow-untrusted*)` asks for `--allow-untrusted` the
same way, and every `project ios` command refuses an abbreviated option.

Two commands open the person's editor, `$VISUAL` or `$EDITOR`: `project edit`,
and `project create` without `--no-edit`. They need not ask. Each opens the
editor only at a terminal, which Claude Code gives no command, and Claude
Code lets an allow rule match past a leading variable assignment only for a
fixed list of variables, such as `LANG`, that holds neither of those, nor
`PATH` or `HOME`. So `VISUAL='sh -c …' project edit x` is not allowed by
`Bash(project *)`, and neither is any command that picks a program Projector
runs.

`project review gate` runs the checkout's `review.gate` command, and stays
allowed so a review loop runs unattended. An agent that edits `review.gate`
and then runs the gate therefore runs that command without a prompt, the
same edit-then-run pattern as any allowed test runner. Codex leaves the gate
to its usual approval, as the last paragraph says. Claude Code matches these
rules against the command text an agent writes, so they keep an agent from
running a command unasked, not from spelling it another way, such as with a
quoted command name.

Codex's `prefix_rule` matches exact tokens with no wildcard. The Codex rules
allow commands by name, and anything else, a command line that starts with an
option included, falls through to Codex's usual approval. Codex runs an
allowed command outside its sandbox, and can give it a terminal the agent
types into, so the rules also leave out every command that can run another
program: `create` and `edit`, whose editor an agent could drive,
`review gate`, `site build` and `serve`, and `ios build` and `logs`, which run
the repository's build scripts or `sudo`. They leave out `inbox answer`,
`dismiss`, `snooze`, `done`, `decline`, `start`, `unclear`, `discuss`, and
`read` too, and `console`, whose terminal an agent could type answers into. A new
command reaches Codex once it is added here and `init` rewrites the file.

Both hosts check each command of a chain such as `a && b` on its own and
take the strictest result, so a rule covers a `project` command only when
the call runs it alone, and the skills run each publish that way. No rule
allows `gh` or the shell to make a chain pass.

Codex matches an absolute program path, such as `/tmp/x/project`, against the
rules for its basename unless a `host_executable` entry lists the paths it
accepts, so the Codex rules end with one that lists none. Claude Code matches
the command text, so `Bash(project *)` never matched a path in the first place.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Optional

from .core import FileAction, write_through

ALLOW = ("Bash(project *)",)
ASK = ("Bash(project init *)", "Bash(project upgrade *)", "Bash(project config set *)",
       "Bash(project config unset *)", "Bash(project site *prepare*)", "Bash(project inbox answer *)",
       "Bash(project inbox dismiss *)", "Bash(project inbox snooze *)", "Bash(project inbox done *)",
       "Bash(project inbox decline *)", "Bash(project inbox start *)",
       "Bash(project inbox unclear *)", "Bash(project inbox discuss *)", "Bash(project inbox read *)",
       "Bash(project console)", "Bash(project console *)", "Bash(project ios *allow-untrusted*)")
# The narrower rules Projector wrote before `Bash(project *)` covered them,
# which `init` removes from `permissions.allow`.
SUPERSEDED = ("Bash(project review publish *)", "Bash(project review hold *)", "Bash(project review finish *)",
              "Bash(project review settle *)", "Bash(project summary publish *)")
# The ask rule Projector wrote before the CLI refused an option before the
# command, which `init` removes from `permissions.ask`.
RETIRED_ASK = ("Bash(project -*)",)

ASKED = ("`project init`, `project upgrade`, `project config set` and `unset`, `project site` with "
         "`--prepare` or `--allow-prepare`, `project ios build --allow-untrusted`, and `project inbox answer`, `dismiss`, `snooze`, `done`, "
         "`decline`, `start`, `unclear`, `discuss`, and `read`, and `project console`")
COMMANDS = "every `project` command but the few that reach outside Projector's own state"

# Each pattern Codex may run without asking, and outside its sandbox, with the
# justification Codex shows for it.
CODEX_ALLOW = (
    (["project", ["check", "done", "help", "list", "loop", "owner", "priority", "search", "show", "status", "summary",
                  "version", "watch", "worktree"]],
     "Runs a Projector command that works on plans, loops, watches, or pull request summaries, finds a branch's "
     "worktree, or prints its version or help."),
    (["project", "agent", ["list", "read", "register", "reply", "send", "unregister"]],
     "Registers or unregisters the session, lists your registered sessions, or sends, reads, or answers a message "
     "between them."),
    (["project", "config", ["get", "list", "paths"]], "Reads Projector's configuration."),
    (["project", "inbox", ["ask", "block", "claim", "delivered", "list", "ready", "reopen", "reply", "request",
                           "restore", "revise", "show", "tell", "watch", "withdraw"]],
     "Posts, revises, reads, withdraws, restores, delivers, or replies in the thread of an inbox item, or reports "
     "on a request's start, but never records the person's answer to one or their words."),
    (["project", "ios", ["cleanup", "crashes", "devices", "install"]],
     "Lists paired iOS devices, installs a kept build, copies crash reports, or removes project ios's worktree "
     "and builds, but never builds or reads a device's log."),
    (["project", "review", ["census", "finish", "hold", "interdiff", "move", "prune", "publish", "ready", "release",
                            "rules", "settle", "setup", "status", "suggestions", "workflow"]],
     "Runs a step of a Projector review other than its gate, which runs the repository's own checks."),
    (["project", "site", ["page", "status", "workflow"]],
     "Builds a summary page, says where the site is hosted, or writes the site's workflow file."),
)
CODEX_PREFIX_RULES = tuple(f'prefix_rule(pattern = {json.dumps(pattern)}, decision = "allow")'
                           for pattern, _ in CODEX_ALLOW)
# The command a summary publishes with. `review rules` says whether each file
# still allows it, so a session whose publish was refused can tell a missing
# rule from a refusal that a matching rule did not prevent.
PUBLISH = ("project", "summary", "publish")
# The Claude Code allow rules that match `PUBLISH`: the broad one, and the
# narrower one Projector wrote before it.
CLAUDE_PUBLISH_RULES = ("Bash(project *)", "Bash(project summary publish *)")
# Codex 0.108 and newer resolve an absolute program path to its basename
# before matching a rule, so without this line `/tmp/x/project summary` would
# match the `project` rules and run outside the sandbox. An empty list keeps
# every absolute path for `project` to Codex's usual approval, and leaves the
# bare name, which the skills run, allowed. Codex before 0.107 cannot parse it
# and drops the whole policy, so `init` leaves it out for a Codex on `PATH` it
# finds to be older, which resolves no path. A newer Codex that reads the same
# rules, such as the desktop app, does, so the note `init` prints says so.
CODEX_HOST_EXECUTABLE = 'host_executable(name = "project", paths = [])'
# The first Codex that parses `CODEX_HOST_EXECUTABLE`.
HOST_EXECUTABLE_SINCE = (0, 107)
# Codex loads every `.rules` file in its rules directory, and writes the
# approvals a person saves to `default.rules`, so Projector keeps its rules in
# a file of its own and rewrites that file whole.
CODEX_RULES = (
    "# Written by `project init`, which replaces this file. Put your own rules in another file.\n"
    "# Codex's usual approval decides every command these rules leave out: `project init`, `project upgrade`,\n"
    "# `project config set` and `unset`, `project create` and `project edit`, which can open an editor,\n"
    "# `project review gate` and `project site build` and `serve`, which run a command the repository or\n"
    "# the command line names, `project ios build` and `logs`, which run the repository's build scripts or\n"
    "# `sudo`, `project inbox answer`, `dismiss`, `snooze`, `done`, `decline`, `start`,\n"
    "# `unclear`, `discuss`, and `read`, which record the person's choice, words, or reading,\n"
    "# `project console`, which records them from a terminal, and a command line that starts with an\n"
    "# option.\n"
    + "".join(f'prefix_rule(\n    pattern = {json.dumps(pattern)},\n    decision = "allow",\n'
              f'    justification = "{justification}",\n)\n'
              for pattern, justification in CODEX_ALLOW)
    + "# These rules allow only the bare name `project`, never a path to a program of that name.\n"
    + CODEX_HOST_EXECUTABLE + "\n"
)
# The rules for a Codex older than `HOST_EXECUTABLE_SINCE`: the same, without
# the line it cannot parse and the comment that introduces it.
CODEX_RULES_WITHOUT_HOST_EXECUTABLE = CODEX_RULES.split("# These rules allow only the bare name")[0]


def codex_version(environ: Mapping[str, str] = os.environ) -> Optional[tuple[int, ...]]:
    """The version `codex --version` reports, from the `codex` on `environ`'s `PATH`, or None when there is none.

    A Codex that is missing, does not run, or prints no version reads as None,
    which `codex_rules` treats as current, since a desktop app with no command
    on `PATH` updates itself.
    """

    executable = shutil.which("codex", path=environ.get("PATH"))
    if executable is None:
        return None
    try:
        result = subprocess.run([executable, "--version"], capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    found = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", result.stdout) if result.returncode == 0 else None
    return tuple(int(part) for part in found.groups()) if found else None


def codex_rules(version: Optional[tuple[int, ...]]) -> str:
    """The rules file for a Codex at `version`, which leaves out `CODEX_HOST_EXECUTABLE` for one too old to parse it."""

    return CODEX_RULES if version is None or version >= HOST_EXECUTABLE_SINCE else CODEX_RULES_WITHOUT_HOST_EXECUTABLE


def shown_version(version: tuple[int, ...]) -> str:
    return ".".join(map(str, version))


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


def codex_rules_path(environ: Mapping[str, str] = os.environ) -> Optional[Path]:
    """Projector's own Codex rules file, or None where Codex is not installed, which its directory's absence says."""

    home = codex_home(environ)
    return home / "rules" / "projector.rules" if home.is_dir() else None


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

    The `SUPERSEDED` rules leave `permissions.allow`, the `RETIRED_ASK` rules
    leave `permissions.ask`, and everything else in the file is kept. A file
    linked into a dotfiles repository is written through its link. A file
    `init` cannot read as settings is left alone with a note. Without `apply`,
    only a missing rule is reported, because a superseded rule left in place
    allows nothing that `ALLOW` does not, and a retired one asks only before a
    command line the CLI refuses.
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
    superseded = any(rule in SUPERSEDED for rule in allow) or any(rule in RETIRED_ASK for rule in ask)
    if not missing_allow and not missing_ask and not (apply and superseded):
        return FileAction(name, "unchanged")
    if not apply:
        consequence = ("Claude Code can ask before, or its auto mode refuse, a Projector command an agent runs, such "
                       "as a review loop's clean review of your own pull request, the end of a hold on it, the "
                       "settling of a finding, the cleanup after a review, or the publish of a summary, unless the "
                       "repository's own settings allow it"
                       if missing_allow else f"an agent can run {ASKED} without asking you")
        return FileAction(name, "kept", f"{name} lacks {', '.join(missing_allow + missing_ask)}, without which "
                                        f"{consequence}"
                                        + ("; run `project init` yourself in a terminal, or pass --publish-rule, to "
                                           "add them" if hint else ""))
    allow[:] = [rule for rule in allow if rule not in SUPERSEDED] + missing_allow
    ask[:] = [rule for rule in ask if rule not in RETIRED_ASK] + missing_ask
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

    path = codex_rules_path(environ)
    if path is None:
        return None
    name = shown(path, environ)
    version = codex_version(environ)
    expected = codex_rules(version)
    too_old = version is not None and expected != CODEX_RULES
    codex = f"Codex {shown_version(version)}" if version is not None else "Codex"
    too_old_note = (f"{codex} is older than {shown_version(HOST_EXECUTABLE_SINCE)}, the first to parse "
                    f"`{CODEX_HOST_EXECUTABLE}`, so {name} leaves it out; while it does, any Codex 0.108 or newer "
                    "that reads it, such as the desktop app, runs a path to a program named `project` outside its "
                    "sandbox, so update or remove the old `codex` and run `project init` again to add the line"
                    if too_old else None)

    def kept(reason: str) -> FileAction:
        lines = CODEX_PREFIX_RULES if too_old else (*CODEX_PREFIX_RULES, CODEX_HOST_EXECUTABLE)
        return FileAction(name, "kept", f"{name} {reason}, so the Codex rules allowing {COMMANDS} were not added; "
                                        f"add {' and '.join(lines)} to a .rules file in "
                                        f"{shown(path.parent, environ)} yourself")

    existed = path.exists()
    text, unreadable = read_user_file(path, environ)
    if unreadable:
        return kept(unreadable)
    if text == expected:
        return FileAction(name, "unchanged", too_old_note)
    if not apply:
        if too_old and text == CODEX_RULES:
            return FileAction(name, "kept", f"{name} holds `{CODEX_HOST_EXECUTABLE}`, which {codex} cannot parse, so "
                                            "Codex loads no rules at all, your own included; update Codex"
                                            + (", or run `project init` yourself in a terminal, or pass "
                                               "--publish-rule, to write the rules it can parse" if hint else ""))
        example = ("; it still allows `project summary publish`" if codex_allows(text, PUBLISH)
                   else ", such as `project summary publish`")
        return FileAction(name, "kept", f"{name} lacks Projector's current rules, without which Codex can ask "
                                        "before, or its approval reviewer refuse, a Projector command an agent runs"
                                        + example
                                        + ("; run `project init` yourself in a terminal, or pass --publish-rule, to "
                                           "add them" if hint else ""))
    try:
        write_through(path, expected)
    except OSError as error:
        return kept(f"could not be written ({error.strerror})")
    return FileAction(name, "updated" if existed else "created", too_old_note)


def codex_allows(text: str, command: Sequence[str]) -> bool:
    """Whether an `allow` rule in a rules file Projector wrote matches a prefix of `command`'s words.

    Projector writes each pattern as JSON on one line, where a word that is a
    list matches any one of its words, so this reads the files `init` wrote,
    in this release's layout or the one-line layout of `CODEX_PREFIX_RULES`,
    and no other Starlark.
    """

    for match in re.finditer(r'prefix_rule\(\s*pattern = (\[.*\]),\s*decision = "allow"', text):
        try:
            pattern = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(pattern, list) and 0 < len(pattern) <= len(command) and all(
                word in (part if isinstance(part, list) else [part]) for word, part in zip(command, pattern)):
            return True
    return False


def publish_allowed(environ: Mapping[str, str] = os.environ) -> dict[str, bool]:
    """Whether each file `allow_commands` reports, by its shown path, allows `project summary publish`.

    A file can lack Projector's current rules and still allow the publish,
    which is what tells a refusal despite a matching rule from a missing one.
    A file that cannot be read allows nothing, and neither does a Codex rules
    file holding `CODEX_HOST_EXECUTABLE` for a Codex too old to parse it,
    since that Codex then loads no rules at all.
    """

    path = settings_path(environ)
    text, unreadable = read_user_file(path, environ)
    try:
        settings = json.loads(text) if text.strip() and not unreadable else {}
    except json.JSONDecodeError:
        settings = {}
    permissions = settings.get("permissions") if isinstance(settings, dict) else None
    allow = permissions.get("allow") if isinstance(permissions, dict) else None
    allowed = {shown(path, environ): isinstance(allow, list) and any(rule in allow for rule in CLAUDE_PUBLISH_RULES)}
    codex = codex_rules_path(environ)
    if codex is not None:
        text, unreadable = read_user_file(codex, environ)
        unparsable = CODEX_HOST_EXECUTABLE in text and codex_rules(codex_version(environ)) != CODEX_RULES
        allowed[shown(codex, environ)] = not unreadable and not unparsable and codex_allows(text, PUBLISH)
    return allowed
