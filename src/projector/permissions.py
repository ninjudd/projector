"""The Claude Code permission rule `init` adds to the user's Claude Code settings.

Claude Code's auto mode refuses a session's clean review of a pull request its
own GitHub account opened, as self-approval, although the review is a COMMENT
that moves no reviewDecision and a person still merges. An allow rule for
`project review publish` resolves that command before the classifier runs. The
rule goes in the person's own settings, so it holds in every repository, clone,
and worktree they open, and no repository has to carry a permission for
everyone who opens it.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

from .core import FileAction, write_through

RULE = "Bash(project review publish *)"


def settings_path(environ: Mapping[str, str] = os.environ) -> Path:
    """The user's Claude Code settings: `$CLAUDE_CONFIG_DIR/settings.json`, or `~/.claude/settings.json`."""

    configured = environ.get("CLAUDE_CONFIG_DIR")
    if configured:
        return Path(configured).expanduser() / "settings.json"
    return Path(environ.get("HOME") or Path.home()) / ".claude" / "settings.json"


def shown(path: Path, environ: Mapping[str, str] = os.environ) -> str:
    """`path` as `init` reports it, with the home directory written as `~`."""

    home = Path(environ.get("HOME") or Path.home())
    return f"~/{path.relative_to(home).as_posix()}" if path.is_relative_to(home) else str(path)


def allow_publish(apply: bool = True, environ: Mapping[str, str] = os.environ) -> FileAction:
    """Add the rule to `permissions.allow` in the user's Claude Code settings.

    Everything else in the file is kept, and a file linked into a dotfiles
    repository is written through its link. A file `init` cannot read as
    settings is left alone with a note. Without `apply`, a file that lacks the
    rule is only reported, with how to add it.
    """

    path = settings_path(environ)
    name = shown(path, environ)

    def kept(reason: str) -> FileAction:
        return FileAction(name, "kept", f"{name} {reason}, so the rule allowing `project review publish` was not "
                                        f"added; add {RULE} to permissions.allow yourself")

    if path.parent.exists() and not path.parent.is_dir():
        return kept(f"cannot be written, because {shown(path.parent, environ)} is not a directory")
    existed = path.exists()
    try:
        text = path.read_text(encoding="utf-8") if existed else ""
    except UnicodeDecodeError:
        return kept("is not UTF-8")
    except OSError as error:
        return kept(f"could not be read ({error.strerror})")
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
    if RULE in allow:
        return FileAction(name, "unchanged")
    if not apply:
        return FileAction(name, "kept", f"{name} lacks {RULE}, without which Claude Code's auto mode refuses a "
                                        "review loop's clean review of your own pull request unless the "
                                        "repository's own settings allow it; run `project init` yourself in a "
                                        "terminal, or pass --publish-rule, to add it")
    allow.append(RULE)
    try:
        write_through(path, json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    except OSError as error:
        # A file linked into a read-only directory, such as the Nix store.
        return kept(f"could not be written ({error.strerror})")
    return FileAction(name, "updated" if existed else "created")
