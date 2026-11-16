"""Scratch directories that are expensive to build, built once per run and copied for each test.

A fixture of Git repositories costs a dozen or more `git` processes, and a
suite that builds the same one before every test spends most of its time
there. `copy_of` builds it once, then gives each test its own copy, so tests
stay isolated while the processes run once.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

# Git records absolute paths in these files: a remote's URL and `insteadOf`
# rules in `config`, and a linked worktree's location in `gitdir` and `.git`.
PATH_FILES = frozenset({"config", "gitdir", ".git"})

built: dict[str, tuple[Path, Any]] = {}


def copy_of(build: Callable[[Path], Any]) -> tuple[Path, Any]:
    """Return a fresh copy of the directory `build` fills, and what `build` returned.

    `build` runs once per process, in an empty directory it is given, and
    returns anything the tests need to know about what it made, such as commit
    SHAs. Every absolute path to that directory that Git recorded in the copy
    is rewritten to the copy, so a remote that names a sibling repository
    names the copy's sibling.
    """
    key = f"{build.__module__}.{build.__qualname__}"
    if key not in built:
        source = Path(tempfile.mkdtemp(prefix="template-"))
        built[key] = (source, build(source))
    source, value = built[key]
    target = Path(tempfile.mkdtemp())
    shutil.copytree(source, target, symlinks=True, dirs_exist_ok=True)
    relocate(target, str(source), str(target))
    return target, value


def relocate(root: Path, old: str, new: str) -> None:
    for path in root.rglob("*"):
        if path.name in PATH_FILES and path.is_file() and not path.is_symlink():
            text = path.read_text()
            if old in text:
                path.write_text(text.replace(old, new))
