"""Layered `.projector.toml` configuration.

Every layer is a file of the same name, so there is one thing to learn:

1. The **user layer**, `~/.projector.toml`, always read wherever the repository
   lives.
2. The **walk**, every `.projector.toml` from the home directory down to the
   starting directory, nearest last.

When the repository sits under the home directory these overlap -- the home
directory is the top of the walk -- and the file is read once, at the lowest
precedence either rule would give it. The user layer is stated separately for
the case where they do not overlap: a checkout at `/opt/src/thing`, a mounted
volume, or a container's `/workspace` has no home directory among its
ancestors, so a walk alone would never reach the user's own file and personal
configuration would vanish exactly where it is hardest to notice.

The walk stops at the home directory rather than the filesystem root, so a file
placed above it is never read. When the home directory is not an ancestor of
the starting directory, no ancestor is read at all: those directories are
outside the user's own space, which is where an unexpected file is more likely
rather than less.

A worktree under the repository, as `.claude/worktrees/<name>` is, keeps the
repository and its parents on the walk. A worktree created outside the
repository does not, and sees only its own file and the user layer.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Optional

from .core import ProjectorError


CONFIG_NAME = ".projector.toml"


class ConfigError(ProjectorError):
    exit_code = 78


def user_config_path(environ: Optional[dict[str, str]] = None) -> Path:
    """The user layer: the same filename, in the home directory."""

    env = os.environ if environ is None else environ
    return (Path(env.get("HOME", "~")) / CONFIG_NAME).expanduser()


def _within(directory: Path, ancestor: Path) -> bool:
    return directory == ancestor or ancestor in directory.parents


def config_paths(start: Path, home: Path, environ: Optional[dict[str, str]] = None) -> list[Path]:
    """Every configuration file that applies at `start`, lowest precedence first.

    Only paths that exist are returned, so the result doubles as the list of
    files a value could have come from.
    """

    start = start.resolve()
    home = home.resolve()
    found: list[Path] = []

    user = user_config_path(environ)
    if user.is_file():
        # Resolved like the walked paths, so every path this returns is
        # comparable with every other one -- including for the deduplication
        # below, which depends on the two spellings matching.
        found.append(user.resolve())

    # When the starting directory is outside the home directory, its ancestors
    # are outside the user's own space too, so the walk covers only the
    # starting directory itself. Checked once: it cannot change as we climb.
    climb = _within(start, home)

    # Collect the walk from `start` upward, then reverse: the nearest file has
    # the highest precedence and must be merged last.
    walked: list[Path] = []
    directory = start
    while True:
        candidate = directory / CONFIG_NAME
        if candidate.is_file():
            walked.append(candidate)
        if not climb or directory == home or directory == directory.parent:
            break
        directory = directory.parent

    # The home directory is both the user layer and the top of the walk, so a
    # repository under it finds the same file twice. Reading it twice would be
    # harmless for values but would misreport provenance and list the path
    # twice, so keep the first occurrence, which already has the lowest
    # precedence.
    for path in reversed(walked):
        if path not in found:
            found.append(path)
    return found


def read_config_file(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path}: invalid TOML: {error}") from error


def merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge `overlay` onto `base`, returning a new mapping.

    Tables merge key by key so a nearer file can override one setting in a
    table without discarding the rest of it. Every other value, arrays
    included, replaces wholesale -- appending to an inherited array is not
    something a reader could predict from looking at one file.
    """

    result = dict(base)
    for key, value in overlay.items():
        existing = result.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            result[key] = merge(existing, value)
        else:
            result[key] = value
    return result


def _flatten(mapping: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in mapping.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{dotted}."))
        else:
            flat[dotted] = value
    return flat


class Config:
    """A merged configuration plus where each value came from."""

    def __init__(self, values: dict[str, Any], sources: dict[str, Path], paths: list[Path]) -> None:
        self.values = values
        self.sources = sources
        self.paths = paths

    def get(self, key: str, default: Any = None) -> Any:
        """Look a value up by dotted key, so `review.effort` reaches a table."""

        current: Any = self.values
        for part in key.split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

    def source(self, key: str) -> Optional[Path]:
        return self.sources.get(key)

    def flat(self) -> dict[str, Any]:
        return _flatten(self.values)


def load(start: Path, home: Optional[Path] = None, environ: Optional[dict[str, str]] = None) -> Config:
    env = os.environ if environ is None else environ
    if home is None:
        home = Path(env.get("HOME") or Path.home())

    paths = config_paths(start, home, env)
    values: dict[str, Any] = {}
    sources: dict[str, Path] = {}
    for path in paths:
        loaded = read_config_file(path)
        values = merge(values, loaded)
        # Recorded after merging so the last writer of each leaf wins, which is
        # the file whose value actually survived.
        for dotted in _flatten(loaded):
            sources[dotted] = path

    # A later file can replace a scalar with a table, which leaves the scalar's
    # own dotted key attributed to a file that no longer supplies it. Drop any
    # key the merged result does not actually have.
    final = _flatten(values)
    sources = {key: path for key, path in sources.items() if key in final}
    return Config(values, sources, paths)
