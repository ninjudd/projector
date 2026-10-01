"""The permission rules `init` adds to the user's Claude Code settings and Codex rules.

Each host's automatic approval can refuse a Projector publish that the user
asked for. Claude Code's auto mode refuses a session's clean review of a pull
request its own GitHub account opened, as self-approval, although the review
is a COMMENT that moves no reviewDecision and a person still merges. Codex's
approval reviewer refuses `project summary publish`, which sends a pull
request's diff to the repository's own site, as sending repository content to
another host. A rule that allows `project review publish` and `project summary
publish` resolves each command before that review runs. The rules go in the
person's own settings, so they hold in every repository, clone, and worktree
they open, and no repository has to carry a permission for everyone who opens
it.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Optional

from .core import FileAction, write_through

RULES = ("Bash(project review publish *)", "Bash(project summary publish *)")

# Codex loads every `.rules` file in its rules directory, and writes the
# approvals a person saves to `default.rules`, so Projector keeps its rule in a
# file of its own and rewrites that file whole.
CODEX_RULE = 'prefix_rule(pattern = ["project", ["review", "summary"], "publish"], decision = "allow")'
CODEX_RULES = """\
# Written by `project init`, which replaces this file. Put your own rules in another file.
prefix_rule(
    pattern = ["project", ["review", "summary"], "publish"],
    decision = "allow",
    justification = "Publishes a Projector review or pull request summary that the user asked for.",
)
"""

COMMANDS = "`project review publish` and `project summary publish`"


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


def allow_publish(apply: bool = True, environ: Mapping[str, str] = os.environ) -> list[FileAction]:
    """Add the publish rules for Claude Code, and for Codex where Codex is installed.

    Without `apply`, a file that lacks the rules is only reported, with how to
    add them.
    """

    actions = [allow_in_claude(apply, environ)]
    codex = allow_in_codex(apply, environ)
    if codex is not None:
        actions.append(codex)
    return actions


def allow_in_claude(apply: bool = True, environ: Mapping[str, str] = os.environ) -> FileAction:
    """Add the rules to `permissions.allow` in the user's Claude Code settings.

    Everything else in the file is kept, and a file linked into a dotfiles
    repository is written through its link. A file `init` cannot read as
    settings is left alone with a note.
    """

    path = settings_path(environ)
    name = shown(path, environ)

    def kept(reason: str) -> FileAction:
        return FileAction(name, "kept", f"{name} {reason}, so the rules allowing {COMMANDS} were not added; add "
                                        f"{' and '.join(RULES)} to permissions.allow yourself")

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
    allow = permissions.setdefault("allow", [])
    if not isinstance(allow, list):
        return kept("has a `permissions.allow` that is not a list")
    missing = [rule for rule in RULES if rule not in allow]
    if not missing:
        return FileAction(name, "unchanged")
    if not apply:
        return FileAction(name, "kept", f"{name} lacks {' and '.join(missing)}, without which Claude Code's auto "
                                        "mode can refuse a review loop's clean review of your own pull request or "
                                        "the publish of a summary, unless the repository's own settings allow it; "
                                        "run `project init` yourself in a terminal, or pass --publish-rule, to add "
                                        "them")
    allow.extend(missing)
    try:
        write_through(path, json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    except OSError as error:
        # A file linked into a read-only directory, such as the Nix store.
        return kept(f"could not be written ({error.strerror})")
    return FileAction(name, "updated" if existed else "created")


def allow_in_codex(apply: bool = True, environ: Mapping[str, str] = os.environ) -> Optional[FileAction]:
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
        return FileAction(name, "kept", f"{name} {reason}, so the Codex rule allowing {COMMANDS} was not added; add "
                                        f"{CODEX_RULE} to a .rules file in {shown(path.parent, environ)} yourself")

    existed = path.exists()
    text, unreadable = read_user_file(path, environ)
    if unreadable:
        return kept(unreadable)
    if text == CODEX_RULES:
        return FileAction(name, "unchanged")
    if not apply:
        return FileAction(name, "kept", f"{name} lacks Projector's rule, without which Codex's approval reviewer "
                                        f"can refuse {COMMANDS}; run `project init` yourself in a terminal, or "
                                        "pass --publish-rule, to add it")
    try:
        write_through(path, CODEX_RULES)
    except OSError as error:
        return kept(f"could not be written ({error.strerror})")
    return FileAction(name, "updated" if existed else "created")
