"""The Claude Code permission rule `init` adds to a repository's settings.

Claude Code's auto mode refuses a session's clean review of a pull request its
own GitHub account opened, as self-approval, although the review is a COMMENT
that moves no reviewDecision and a person still merges. An allow rule for
`project review publish` in the repository's `.claude/settings.json` resolves
that command before the classifier runs. Claude Code applies a repository's
allow rules only after its workspace trust dialog lists them, so each person
sees the rule before it takes effect.
"""

from __future__ import annotations

import json
from pathlib import Path

from .core import FileAction, write_through

SETTINGS_PATH = ".claude/settings.json"
RULE = "Bash(project review publish *)"


def allow_publish(root: Path, apply: bool = True) -> FileAction:
    """Add the rule to `permissions.allow` in the repository's Claude Code settings.

    Everything else in the file is kept. A file `init` cannot read as settings,
    or one that links outside the repository, is left alone with a note.
    Without `apply`, a file that lacks the rule is only reported, with how to
    add it.
    """

    path = root / SETTINGS_PATH

    def kept(reason: str) -> FileAction:
        return FileAction(SETTINGS_PATH, "kept", f"{SETTINGS_PATH} {reason}, so the rule allowing "
                                                 f"`project review publish` was not added; add {RULE} to "
                                                 "permissions.allow yourself")

    target = path.resolve()
    if not target.is_relative_to(root.resolve()):
        return kept(f"links outside the repository ({target})")
    # A committed `.claude` link checks out as a plain file where Git makes no symlinks.
    if path.parent.exists() and not path.parent.is_dir():
        return kept("cannot be written, because .claude is not a directory")
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
        return FileAction(SETTINGS_PATH, "unchanged")
    if not apply:
        return FileAction(SETTINGS_PATH, "kept", f"{SETTINGS_PATH} lacks {RULE}, without which Claude Code's auto "
                                                 "mode refuses a review loop's clean review of your own pull "
                                                 "request; run `project init` yourself in a terminal, or pass "
                                                 "--publish-rule, to add it")
    allow.append(RULE)
    write_through(path, json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    return FileAction(SETTINGS_PATH, "updated" if existed else "created")
