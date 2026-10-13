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

`project config set` and `unset` change one file, one line at a time, so the
comments and layout a person wrote survive every change.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .core import ProjectorError, UsageError, write_through


CONFIG_NAME = ".projector.toml"

# Every key Projector reads, with its type. `project config set` accepts only
# these keys and values of these types, and `Config.setting` refuses a file's
# value of another type.
SETTINGS: dict[str, type] = {
    "projects.dir": str,
    "instructions.enabled": bool,
    "site.enabled": bool,
    "site.prepare": str,
    "review.username": str,
    "review.allow_approve": bool,
    "review.publish_rule": bool,
    "review.gate": str,
    "review.incremental": bool,
    "review.summarize": bool,
    "fix.resolve_human_threads": bool,
    "watch.auto_start": bool,
}


class ConfigError(ProjectorError):
    exit_code = 78


def _describe(kind: type) -> str:
    return {bool: "true or false", int: "an integer"}.get(kind, "a string")


def _has_type(value: Any, kind: type) -> bool:
    # `bool` subclasses `int`, so `true` must not pass for an integer.
    return isinstance(value, kind) and (kind is bool or not isinstance(value, bool))


def same_value(one: Any, other: Any) -> bool:
    return type(one) is type(other) and one == other


def _unknown_key(key: str, reason: str = "") -> UsageError:
    return UsageError(f"unknown key {key!r}{reason}; the keys are {', '.join(sorted(SETTINGS))}")


def setting_type(key: str) -> type:
    try:
        return SETTINGS[key]
    except KeyError:
        raise _unknown_key(key) from None


def parse_value(key: str, text: str) -> Any:
    """`text` from the command line as the type `key` takes."""

    kind = setting_type(key)
    if kind is bool and text in ("true", "false"):
        return text == "true"
    if kind is int and re.fullmatch(r"[+-]?[0-9]+", text):
        return int(text)
    if kind is str:
        try:
            text.encode("utf-8")
        except UnicodeEncodeError:
            raise UsageError(f"{key} must be text that UTF-8 can encode") from None
        return text
    raise UsageError(f"{key} must be {_describe(kind)}, not {text!r}")


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
        """Look a value up by dotted key, so `review.username` reaches a table."""

        current: Any = self.values
        for part in key.split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

    def setting(self, key: str) -> Any:
        """A key from `SETTINGS`, or None when unset; a value of another type is an error naming the key."""

        value = self.get(key)
        kind = SETTINGS[key]
        if value is not None and not _has_type(value, kind):
            raise ConfigError(f"{key} must be {_describe(kind)}, not {type(value).__name__}")
        return value

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


@dataclass(frozen=True)
class Change:
    """What `write_value` did to one key: `set`, `unset`, or `unchanged`."""

    action: str
    key: str
    old: Any
    new: Any
    path: Path


def write_value(path: Path, key: str, value: Any) -> Change:
    """Set `key` to `value` in the file at `path`, or remove it when `value` is None.

    Only the lines that set `key` change, so every comment, blank line, and
    other setting stays byte for byte. The edit is checked twice: before
    writing, by parsing the new text, and after, by reading the file back,
    which restores the original when the two disagree.
    """

    original = path.read_bytes().decode("utf-8") if path.exists() else None
    try:
        before = tomllib.loads(original or "")
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path}: invalid TOML: {error}") from error
    old = Config(before, {}, []).get(key)
    if same_value(old, value):
        return Change("unchanged", key, old, value, path)

    expected = {name: setting for name, setting in _flatten(before).items()
                if name != key and not name.startswith(f"{key}.")}
    if value is not None:
        expected[key] = value
    text = _edited(original or "", key, value)
    if not _reads_back(text, key, value, expected):
        raise ProjectorError(
            f"{path}: cannot change {key} by editing its own line, as when an inline table holds it; "
            "the file is unchanged"
        )
    write_through(path, text)
    if not _reads_back(path.read_bytes().decode("utf-8"), key, value, expected):
        if original is None:
            path.resolve().unlink(missing_ok=True)
        else:
            write_through(path, original)
        raise ProjectorError(f"{path}: {key} did not read back as written, so the file was restored")
    return Change("unset" if value is None else "set", key, old, value, path)


def unset_value(path: Path, key: str) -> Change:
    """Remove `key` from the file at `path`.

    A key outside `SETTINGS` is removed when the file sets it, so a setting
    Projector no longer reads can be cleaned up, and refused when the file
    does not, so a misspelled key still gets the list of keys.
    """

    change = write_value(path, key, None)
    if change.action == "unchanged" and key not in SETTINGS:
        raise _unknown_key(key, f", which {path} does not set")
    return change


def _reads_back(text: str, key: str, value: Any, expected: dict[str, Any]) -> bool:
    try:
        values = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return False
    return same_value(Config(values, {}, []).get(key), value) and _flatten(values) == expected


_KEY_PART = r"""(?:[A-Za-z0-9_-]+|"(?:[^"\\\n]|\\.)*"|'[^'\n]*')"""
_DOTTED_KEY = rf"{_KEY_PART}(?:[ \t]*\.[ \t]*{_KEY_PART})*"
_HEADER = re.compile(rf"[ \t]*(\[\[?)[ \t]*({_DOTTED_KEY})[ \t]*\]")
_ASSIGNMENT = re.compile(rf"([ \t]*)({_DOTTED_KEY})[ \t]*=[ \t]*")


@dataclass(frozen=True)
class _Header:
    line: int
    table: tuple[str, ...]
    array: bool


@dataclass(frozen=True)
class _Entry:
    """One `key = value` statement: lines `start` up to `end`, under the table `table`."""

    start: int
    end: int
    table: tuple[str, ...]
    key: tuple[str, ...]
    indent: str
    value_start: int

    @property
    def path(self) -> tuple[str, ...]:
        return self.table + self.key


def _key_path(dotted: str) -> tuple[str, ...]:
    """A dotted key as TOML reads it, with quotes and escapes resolved."""

    node: Any = tomllib.loads(f"{dotted} = 0")
    path: list[str] = []
    while isinstance(node, dict):
        ((name, node),) = node.items()
        path.append(name)
    return tuple(path)


def _statement_end(lines: list[str], start: int) -> int:
    """The line after the last one the statement at `start` spans.

    A value such as a multi-line string or array continues onto later lines,
    and the first run of lines that parses on its own is the whole statement.
    """

    for end in range(start + 1, len(lines) + 1):
        try:
            tomllib.loads("".join(lines[start:end]))
        except tomllib.TOMLDecodeError:
            continue
        return end
    raise ConfigError(f"line {start + 1}: cannot find the end of the value")


def _scan(lines: list[str]) -> tuple[list[_Header], list[_Entry]]:
    headers: list[_Header] = []
    entries: list[_Entry] = []
    table: tuple[str, ...] = ()
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
        elif header := _HEADER.match(line):
            table = _key_path(header.group(2))
            headers.append(_Header(index, table, header.group(1) == "[["))
            index += 1
        else:
            assignment = _ASSIGNMENT.match(line)
            if assignment is None:
                raise ConfigError(f"line {index + 1}: cannot read the key")
            end = _statement_end(lines, index)
            entries.append(
                _Entry(index, end, table, _key_path(assignment.group(2)), assignment.group(1), assignment.end())
            )
            index = end
    return headers, entries


def _value_end(statement: str, value_start: int) -> int:
    """Where the value that begins at `value_start` ends, so a comment after it survives.

    The value ends either at the end of the statement or before a `#` that
    starts a comment. A `#` inside a string leaves the text before it
    unparseable, so the first cut that parses is the end of the value.
    """

    cuts = [match.start() for match in re.finditer("#", statement) if match.start() > value_start]
    for cut in [*cuts, len(statement)]:
        head = statement[:cut].rstrip()
        try:
            tomllib.loads(head)
        except tomllib.TOMLDecodeError:
            continue
        return len(head)
    raise ConfigError("cannot find the end of the value")


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    # JSON escapes every control character TOML does except DEL, which a TOML
    # basic string may not hold unescaped.
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007F")


def _edited(text: str, key: str, value: Any) -> str:
    """`text` with `key` set to `value`, or removed when `value` is None."""

    lines = re.findall(r"[^\n]*\n|[^\n]+\Z", text)
    newline = "\r\n" if "\r\n" in text else "\n"
    path = tuple(key.split("."))
    headers, entries = _scan(lines)

    entry = next((entry for entry in entries if entry.path == path), None)
    if entry is not None:
        if value is None:
            del lines[entry.start:entry.end]
        else:
            statement = "".join(lines[entry.start:entry.end])
            end = _value_end(statement, entry.value_start)
            lines[entry.start:entry.end] = [statement[:entry.value_start] + _toml_value(value) + statement[end:]]
        return "".join(lines)
    if value is None:
        return text

    parent = path[:-1]
    header = next((header for header in headers if header.table == parent and not header.array), None)
    if header is not None or not parent:
        # After the section's last statement rather than at its end, so a
        # comment that introduces the next table stays above that table.
        siblings = [entry for entry in entries if entry.table == parent]
        written = path[-1:]
        after = siblings[-1] if siblings else None
        at = after.end if after else (header.line + 1 if header else 0)
    else:
        # A table made by dotted keys, as `watch.auto_start = true` at the top
        # level makes `watch`, cannot also take a `[watch]` header, so the key
        # joins those dotted keys.
        dotted = [entry for entry in entries
                  if len(entry.table) < len(parent) < len(entry.path) and entry.path[:len(parent)] == parent]
        if not dotted:
            separator = newline if text.strip() and not text.endswith(newline * 2) else ""
            ending = newline if text and not text.endswith("\n") else ""
            return (f"{text}{ending}{separator}[{'.'.join(parent)}]{newline}"
                    f"{path[-1]} = {_toml_value(value)}{newline}")
        after = dotted[-1]
        written = path[len(after.table):]
        at = after.end

    if at and not lines[at - 1].endswith("\n"):
        lines[at - 1] += newline
    indent = after.indent if after else ""
    lines.insert(at, f"{indent}{'.'.join(written)} = {_toml_value(value)}{newline}")
    return "".join(lines)
