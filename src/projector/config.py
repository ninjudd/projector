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

A walked file that the repository you work in tracks is policy, reviewed where
the repository merges its changes, so its values come from the default branch,
the commit `refs/remotes/origin/HEAD` names, and never from the working copy
of whatever branch is checked out. The repository is the one whose working
tree holds the starting directory, and every worktree of it shares its common
Git directory. For each walked directory in one of its working trees, the
blob at that path on the default branch applies, even where the branch deleted
the file. A file Git tracks on this branch only, which the branch added, is
ignored until it merges. A file Git does not track is read from disk, as is
every file outside the repository, the user layer, and every file when the
repository has no `origin/HEAD`. Git's own files say where the repository and
its refs are, and a cache under the state directory holds each blob by the
commit and path, and whether each file is tracked by the index's signature,
so a load that finds what it needs there starts no process.

Any file may also hold `[override.<priority>]` tables, where the priority is a
whole number from 1 to 99. After the ordinary merge, every override beats
every ordinary value. Among overrides of one setting the higher priority wins,
and at equal priority the farthest file wins, with the user layer farthest of
all, so a team can outrank a company by choosing a higher number and a person
can outrank everyone.

A destination, a setting in `DESTINATIONS` that says where your data goes,
applies from a file the repository commits only once you allow its value with
`project config allow`, recorded in the state directory's `allowed.json`.

`project config set` and `unset` change one file, one line at a time, so the
comments and layout a person wrote survive every change.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import tempfile
import time
import tomllib
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import crypto
from .core import ProjectorError, UsageError, run_bounded, state_home, write_through


CONFIG_NAME = ".projector.toml"

# Every key Projector reads, with its type. `project config set` accepts only
# these keys and values of these types, and `Config.setting` refuses a file's
# value of another type.
SETTINGS: dict[str, type] = {
    "projects.dir": str,
    "instructions.enabled": bool,
    "instructions.ios": bool,
    "site.enabled": bool,
    "site.prepare": str,
    "review.username": str,
    "review.allow_approve": bool,
    "review.publish_rule": bool,
    "review.gate": str,
    "review.gate_checks": str,
    "review.incremental": bool,
    "review.summarize": bool,
    "review.labels": bool,
    "review.require_check": bool,
    "review.reviewers": str,
    "review.runner": str,
    "review.ci": bool,
    "review.ci_agents": str,
    "review.ci_app": str,
    "review.ci_app_id": int,
    "review.ci_runs_on": str,
    "review.ci_credential": str,
    "review.context_repos": str,
    "fix.resolve_human_threads": bool,
    "watch.auto_start": bool,
    "watch.interval": int,
    "watch.full_every": int,
    "watch.renotify": int,
    "watch.review_stall": int,
    "watch.fetch_default": bool,
    "panel.background": str,
    "agent.message_limit": int,
    "agent.auto_register": bool,
    "agent.key": str,
    "agent.peers": dict,
    "agent.peer_limit": int,
    "agent.relay": str,
    "agent.s3_region": str,
    "agent.s3_endpoint": str,
    "agent.s3_profile": str,
    "agent.s3_poll": int,
    "ios.app": str,
    "ios.project": str,
    "ios.output": str,
    "hosted.url": str,
    "hosted.relay": str,
}

# The least value `project config set` accepts for each whole-number key. The
# watch daemon reads a smaller `watch.interval` that a file sets by hand as this
# minimum, so it never polls GitHub more often.
MINIMUMS: dict[str, int] = {
    "watch.interval": 30,
    "watch.full_every": 1,
    "watch.renotify": 1,
    "watch.review_stall": 1,
    "agent.message_limit": 1,
    "agent.peer_limit": 1,
    "agent.s3_poll": 2,
}


# Keys read only from the user layer, `~/.projector.toml`, each with every key
# under it. A walked file's value is ignored, so a repository cannot plant a
# key its author knows, or a peer whose agents could then post to your inbox.
USER_ONLY = frozenset({"agent.key", "agent.peers"})

# The table that holds a file's overrides, `[override.<priority>]`, so no
# setting's name may start with it.
OVERRIDE = "override"
PRIORITY = re.compile(r"[1-9][0-9]?")

# The settings that say where your data goes. The owner of a repository's
# default branch may not be you, so a value read from it applies only once you
# allow it with `project config allow`.
DESTINATIONS: frozenset[str] = frozenset({"agent.relay", "agent.s3_endpoint", "agent.s3_profile", "hosted.url",
                                          "hosted.relay"})

# The forms `agent.relay` takes: `origin`, the checkout's own repository, a
# GitHub repository the relay carries records through, or an S3 bucket and
# prefix that hold them as objects.
RELAY_ORIGIN = "origin"
RELAY_GITHUB = "github:"
RELAY_S3 = "s3://"
RELAY_SLUG = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
# An S3 bucket's name, and each `/`-separated segment of a relay's prefix, in
# lowercase only, so every rule that compares locations without case compares
# S3 locations exactly.
S3_BUCKET = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")
S3_SEGMENT = re.compile(r"[a-z0-9._-]+")
# Where an S3 location with no `agent.s3_endpoint` lives, in its identity.
AWS_ENDPOINT = "https://s3.amazonaws.com"
# The longest identity a location may have: the longest `location` a record
# carries.
MAX_LOCATION = 256

# Keys whose value can be a secret. A value under `keychain:` names an item in
# the OS keychain and holds no secret; any other value is the secret itself.
SECRETS = frozenset({"agent.key"})
KEYCHAIN = "keychain:"
INLINE_KEY = "x25519:"


class ConfigError(ProjectorError):
    exit_code = 78


class NotAllowed(ConfigError):
    """A destination that a file the repository commits sets, which you have not allowed. A caller treats the
    setting as unset, with this as the reason."""

    def __init__(self, key: str, value: Any, source: "Source") -> None:
        self.key, self.value, self.source = key, value, source
        super().__init__(f"{source} sets {key} = {value!r}, which sends your data where that file's owner chooses; "
                         f"run `project config allow {key} {shlex.quote(str(value))}` in a terminal to use it")


@dataclass(frozen=True)
class S3Relay:
    """An S3 bucket and the prefix under it that `agent.relay` names, which may be empty."""

    bucket: str
    prefix: str


def relay_location(value: str) -> Optional[str | S3Relay]:
    """The repository an `agent.relay` value names, `owner/repo`, its S3 bucket and prefix, or None for `origin`,
    the checkout's own repository. Any other value is refused, naming the forms."""

    if value == RELAY_ORIGIN:
        return None
    if value.startswith(RELAY_S3):
        bucket, _, prefix = value[len(RELAY_S3):].partition("/")
        segments = prefix.split("/") if prefix else []
        if (S3_BUCKET.fullmatch(bucket) and ".." not in bucket and not value.endswith("/")
                and all(S3_SEGMENT.fullmatch(segment) and segment not in (".", "..") for segment in segments)):
            return S3Relay(bucket, prefix)
        raise ConfigError(f"agent.relay is {value!r}, and an S3 location is s3://<bucket>/<prefix>: a bucket name of "
                          "3 to 63 lowercase letters, digits, dots, and hyphens, then an optional prefix of "
                          "lowercase letters, digits, dots, underscores, and hyphens in segments separated by /, "
                          "with no empty, . or .. segment and no trailing /")
    slug = value[len(RELAY_GITHUB):] if value.startswith(RELAY_GITHUB) else None
    if slug is not None and RELAY_SLUG.fullmatch(slug) and slug.split("/")[1] not in (".", ".."):
        return slug
    raise ConfigError(f"agent.relay is {value!r}, and it takes origin, github:<owner>/<repo>, such as "
                      "github:acme/agents, or s3://<bucket>/<prefix>, such as s3://acme-agents/relay")


def s3_endpoint(value: str, key: str = "agent.s3_endpoint") -> str:
    """An `agent.s3_endpoint` value, or that of the setting `key` names, as a location's identity writes it:
    `https://`, the host in lowercase, and its port unless it is 443. A value with a path, query, fragment, or user
    info is refused."""

    try:
        parts: Optional[urllib.parse.SplitResult] = urllib.parse.urlsplit(value)
        port = parts.port if parts else None
    except ValueError:
        parts, port = None, None
    if (parts is None or parts.scheme.lower() != "https" or not parts.hostname or parts.path not in ("", "/")
            or parts.query or parts.fragment or "@" in parts.netloc or value.endswith(("?", "#"))):
        raise ConfigError(f"{key} is {value!r}, and it takes an https:// URL with no path, query, or "
                          "user info, such as https://s3.example.com")
    host = parts.hostname.lower()
    host = f"[{host}]" if ":" in host else host
    return f"https://{host}" + (f":{port}" if port not in (None, 443) else "")


# The hosted Projector service `hosted.url` names when no file sets it, and the store of its relay, which
# `hosted.relay` names.
HOSTED_URL = "https://api.projector.bot"
HOSTED_RELAY = "https://relay.projector.bot"
LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})


def hosted_url(value: str) -> str:
    """A `hosted.url` value as requests and the review's OIDC audience use it: the scheme, the host in lowercase,
    and its port unless it is the scheme's own. It takes `https://`, or `http://` for a service on this machine,
    and refuses a path, query, fragment, or user info."""

    try:
        parts: Optional[urllib.parse.SplitResult] = urllib.parse.urlsplit(value)
        port = parts.port if parts else None
    except ValueError:
        parts, port = None, None
    scheme = parts.scheme.lower() if parts else ""
    host = (parts.hostname or "").lower() if parts else ""
    if (parts is None or not host or parts.path not in ("", "/") or parts.query or parts.fragment
            or "@" in parts.netloc or value.endswith(("?", "#"))
            or not (scheme == "https" or scheme == "http" and host in LOOPBACK)):
        raise ConfigError(f"hosted.url is {value!r}, and it takes an https:// URL with no path, query, or user info, "
                          f"such as {HOSTED_URL}, or http:// for a service on this machine")
    host = f"[{host}]" if ":" in host else host
    return f"{scheme}://{host}" + (f":{port}" if port not in (None, 443 if scheme == "https" else 80) else "")


def s3_identity(endpoint: Optional[str], bucket: str, prefix: str) -> str:
    """An S3 location's identity: the path-style URL of its prefix, on AWS when no endpoint names another store."""

    return "/".join(part for part in (s3_endpoint(endpoint) if endpoint else AWS_ENDPOINT, bucket, prefix) if part)


def is_s3(location: str) -> bool:
    """Whether a relay location is an S3 location's identity rather than a repository slug."""

    return location.startswith("https://")


def location_key(location: str) -> str:
    """The relative name a location's cache, outbox, and inbox mirror are filed under: a repository's slug in
    lowercase, or `s3/` and the first 16 hex digits of the SHA-256 of an S3 location's identity."""

    if is_s3(location):
        return f"s3/{hashlib.sha256(location.encode()).hexdigest()[:16]}"
    return location.lower()


def is_destination(key: str) -> bool:
    return key in DESTINATIONS


def allowed_path(environ: Optional[dict[str, str]] = None) -> Path:
    """Where the destinations you allowed are recorded: this machine's state, not a checkout or its config."""

    return state_home(environ) / "allowed.json"


def approvals(path: Path) -> list[dict[str, Any]]:
    try:
        found = json.loads(path.read_bytes())
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as error:
        raise ConfigError(f"{path}: cannot read the destinations you allowed: {error}") from error
    if not isinstance(found, dict) or found.get("schema_version") != 1 or not isinstance(found.get("allowed"), list):
        raise ConfigError(f"{path}: not a record of allowed destinations")
    return [entry for entry in found["allowed"] if isinstance(entry, dict)]


def _write_approvals(path: Path, entries: list[dict[str, Any]]) -> None:
    write_through(path, json.dumps({"schema_version": 1, "allowed": entries}, indent=2, sort_keys=True) + "\n")


def is_allowed(path: Path, key: str, value: Any) -> bool:
    return any(entry.get("key") == key and same_value(entry.get("value"), value) for entry in approvals(path))


def allow(path: Path, key: str, value: Any, source: Optional[str], now: float) -> bool:
    """Record that you allowed `key` to be `value`, answering False when you already had."""

    entries = approvals(path)
    if any(entry.get("key") == key and same_value(entry.get("value"), value) for entry in entries):
        return False
    allowed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    _write_approvals(path, [*entries, {"key": key, "value": value, "source": source, "allowed_at": allowed_at}])
    return True


def disallow(path: Path, key: str, value: Any) -> bool:
    """Remove the approval of `key` as `value`, answering whether there was one."""

    entries = approvals(path)
    kept = [entry for entry in entries if not (entry.get("key") == key and same_value(entry.get("value"), value))]
    if len(kept) == len(entries):
        return False
    _write_approvals(path, kept)
    return True


def is_secret(key: str, value: Any) -> bool:
    return key in SECRETS and isinstance(value, str) and not value.startswith(KEYCHAIN)


def inline_key(value: str) -> bytes:
    """The private key an inline `agent.key` holds: `x25519:` and the base64 of 32 bytes."""

    if not value.startswith(INLINE_KEY):
        raise ConfigError("agent.key is neither keychain:<key-id> nor x25519:<base64 key>")
    try:
        private = base64.b64decode(value[len(INLINE_KEY):], validate=True)
    except (binascii.Error, ValueError):
        private = b""
    if len(private) != 32:
        raise ConfigError("agent.key holds no 32-byte X25519 key after x25519:")
    return private


def redact(key: str, value: Any) -> Any:
    """`value`, found at the dotted `key`, with each inline secret at or under `key` shown by its key ID."""

    if isinstance(value, dict):
        return {name: redact(f"{key}.{name}" if key else name, child) for name, child in value.items()}
    if not is_secret(key, value):
        return value
    try:
        found = crypto.key_id(crypto.public_key(inline_key(value)))
    except ConfigError:
        found = "unknown"
    return f"<redacted, key ID {found}>"


def check_private(path: Path) -> None:
    """Refuse a file that holds an inline secret when other accounts can read or change it. Windows relies on
    the profile's ACL instead."""

    if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ConfigError(f"{path} holds agent.key inline, and other accounts can read it; run `chmod 600 {path}`")


def user_value(key: str, environ: Optional[dict[str, str]] = None) -> Any:
    """A key from `SETTINGS` in the user layer alone, checking that a file holding an inline secret is private."""

    path = user_config_path(environ)
    if not path.is_file():
        return None
    value = layered([(Source(path), read_config_file(path))], path).setting(key)
    if is_secret(key, value):
        check_private(path)
    return value


def key_id_of(value: str) -> str:
    """The key ID an `agent.key` value names, read without the keychain."""

    if value.startswith(KEYCHAIN):
        return value[len(KEYCHAIN):]
    return crypto.key_id(crypto.public_key(inline_key(value)))


def public_key(text: str) -> bytes:
    """The 32-byte X25519 public key that `text` holds as base64, the form `project agent key show` prints."""

    try:
        public = base64.b64decode(text.strip(), validate=True)
    except (binascii.Error, ValueError):
        public = b""
    if len(public) != 32:
        raise ConfigError(f"{text!r} is not a public key, the base64 of 32 bytes that `project agent key show` prints")
    return public


def peers(environ: Optional[dict[str, str]] = None) -> dict[str, bytes]:
    """`agent.peers` from `~/.projector.toml` alone: each peer's name with its public key. Every name matches
    `agents.NAME`, no two names hold one key, and none holds the key `agent.key` names. Reads no keychain."""

    from .agents import NAME

    table = user_value("agent.peers", environ) or {}
    value = user_value("agent.key", environ)
    own = key_id_of(value) if value else None
    found: dict[str, bytes] = {}
    holders: dict[str, str] = {}
    for name, text in table.items():
        if not NAME.fullmatch(name):
            raise ConfigError(f"agent.peers.{name} is not a peer name: use 1 to 32 lowercase letters, digits, and "
                              "hyphens, starting with a letter or digit")
        if not isinstance(text, str):
            raise ConfigError(f"agent.peers.{name} must be a public key, not {type(text).__name__}")
        try:
            public = public_key(text)
        except ConfigError:
            raise ConfigError(f"agent.peers.{name} is not a public key, the base64 of 32 bytes") from None
        key_id = crypto.key_id(public)
        if key_id == own:
            raise ConfigError(f"agent.peers.{name} holds your own public key, key {key_id}")
        if key_id in holders:
            raise ConfigError(f"agent.peers.{holders[key_id]} and agent.peers.{name} hold one key, key {key_id}")
        holders[key_id] = name
        found[name] = public
    return found


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
    if kind is bool:
        if text not in ("true", "false"):
            raise UsageError(f"{key} must be true or false, not {text!r}")
        return text == "true"
    if kind is int:
        least = MINIMUMS[key]
        if not re.fullmatch(r"[0-9]+", text) or int(text) < least:
            raise UsageError(f"{key} must be a whole number of at least {least}, not {text!r}")
        return int(text)
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise UsageError(f"{key} must be text that UTF-8 can encode") from None
    try:
        if key == "agent.relay":
            relay_location(text)
        elif key == "agent.s3_endpoint":
            s3_endpoint(text)
        elif key == "hosted.url":
            hosted_url(text)
        elif key == "hosted.relay":
            s3_endpoint(text, key)
    except ConfigError as error:
        raise UsageError(str(error)) from None
    return text


def user_config_path(environ: Optional[dict[str, str]] = None) -> Path:
    """The user layer: the same filename, in the home directory."""

    env = os.environ if environ is None else environ
    return (Path(env.get("HOME", "~")) / CONFIG_NAME).expanduser()


def _within(directory: Path, ancestor: Path) -> bool:
    return directory == ancestor or ancestor in directory.parents


def walked_directories(start: Path, home: Path) -> list[Path]:
    """The directories whose `.projector.toml` the walk reads, nearest first.

    When the starting directory is outside the home directory, its ancestors
    are outside the user's own space too, so the walk covers only the
    starting directory itself.
    """

    start = start.resolve()
    home = home.resolve()
    climb = _within(start, home)
    directories = [start]
    while climb and directories[-1] != home and directories[-1] != directories[-1].parent:
        directories.append(directories[-1].parent)
    return directories


def read_config_file(path: Path) -> dict[str, Any]:
    return parse_config(path.read_bytes(), str(path))


def parse_config(data: bytes, label: str) -> dict[str, Any]:
    try:
        return tomllib.loads(data.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise ConfigError(f"{label}: invalid TOML: {error}") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{label}: invalid TOML: {error}") from error


# Git

ORIGIN_HEAD = "refs/remotes/origin/HEAD"
# Where the watch daemon fetches each default branch, as `refs/projector/default/origin/main`, so the fetch
# moves no ref you use.
DEFAULT_REFS = "refs/projector/default/"
OBJECT_ID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
GIT_TIMEOUT = 30.0
# The variables that point Git at another repository, index, or object store
# than the one Projector names on the command line.
GIT_LOCATION = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
                "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX")


@dataclass(frozen=True)
class WorkingTree:
    """One working tree of a repository: its top, its own Git directory, and the common directory every
    worktree of the repository shares."""

    top: Path
    git_dir: Path
    common: Path


@dataclass(frozen=True)
class DefaultBranch:
    """The default branch as `refs/remotes/origin/HEAD` names it, such as `origin/main`, and its commit."""

    name: str
    commit: str


def git_output(args: list[str], *, cwd: Optional[Path] = None, input: Optional[bytes] = None) -> bytes:
    environment = {name: value for name, value in os.environ.items() if name not in GIT_LOCATION}
    try:
        done = run_bounded(["git", *args], cwd=cwd, input=input, env=environment, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ConfigError(f"cannot read the repository's configuration: git did not run: {error}") from None
    if done.returncode:
        problem = done.stderr.decode("utf-8", "replace").strip()
        raise ConfigError(f"cannot read the repository's configuration: git {args[-1]} failed: {problem}")
    return done.stdout


def _text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _tree_at(top: Path) -> Optional[WorkingTree]:
    """The working tree whose top is `top`, from its `.git` directory or `gitdir:` file, or from
    `git rev-parse` when the layout is not one Git documents."""

    dot_git = top / ".git"
    if dot_git.is_dir():
        git_dir = dot_git
    elif dot_git.is_file():
        pointer = _text(dot_git) or ""
        git_dir = (top / pointer.removeprefix("gitdir:").strip()).resolve()
        if not pointer.startswith("gitdir:") or not git_dir.is_dir():
            return _rev_parse_tree(top)
    else:
        return None
    common = _text(git_dir / "commondir")
    return WorkingTree(top, git_dir, (git_dir / common.strip()).resolve() if common else git_dir)


def _rev_parse_tree(top: Path) -> Optional[WorkingTree]:
    # One path a run, because `git rev-parse` ends each path with a newline that a path can also hold.
    found = []
    for option in ("--show-toplevel", "--absolute-git-dir", "--git-common-dir"):
        try:
            output = git_output(["rev-parse", "--path-format=absolute", option], cwd=top)
        except ConfigError:
            return None
        found.append(Path(os.fsdecode(output.removesuffix(b"\n"))).resolve())
    return WorkingTree(*found)


def working_tree(directory: Path) -> Optional[WorkingTree]:
    """The working tree that holds `directory`, or None outside every repository."""

    for top in (directory, *directory.parents):
        if (top / ".git").exists():
            return _tree_at(top)
    return None


def _packed_ref(common: Path, name: str) -> Optional[str]:
    packed = _text(common / "packed-refs") or ""
    at = packed.find(f" {name}\n")
    if at < 0:
        return None
    return packed[packed.rfind("\n", 0, at) + 1:at]


def resolve_ref(common: Path, name: str) -> Optional[str]:
    """The object a ref names, from its loose file or `packed-refs`, or None when it has none."""

    loose = _text(common / name)
    found = loose.strip() if loose is not None else _packed_ref(common, name)
    return found if found and OBJECT_ID.fullmatch(found) else None


def default_branch(tree: WorkingTree, cache: Path) -> Optional[DefaultBranch]:
    """The default branch of the repository `tree` belongs to, or None when it has no `origin/HEAD`.

    Its commit is the newer of the one `origin/HEAD` names, which your own
    fetches move, and the one in `refs/projector/default/<branch>`, which the
    watch daemon's fetch moves. When neither descends from the other, as after
    a force-push, yours wins.
    """

    target = _origin_head(tree)
    if target is None:
        return None
    origin = _commit_of(tree, target)
    if origin is None:
        return None
    name = target.removeprefix("refs/remotes/")
    return DefaultBranch(name, _newer(tree.common, origin, _commit_of(tree, DEFAULT_REFS + name), cache))


def _reftable(tree: WorkingTree) -> bool:
    """Whether the repository keeps its refs in a reftable, which has no files to read, so Git reads them."""

    return (tree.common / "reftable").is_dir()


def _origin_head(tree: WorkingTree) -> Optional[str]:
    if _reftable(tree):
        try:
            return os.fsdecode(git_output(["symbolic-ref", "-q", ORIGIN_HEAD], cwd=tree.top)).strip() or None
        except ConfigError:
            return None
    head = _text(tree.common / ORIGIN_HEAD)
    return head[len("ref:"):].strip() if head is not None and head.startswith("ref:") else None


def _commit_of(tree: WorkingTree, name: str) -> Optional[str]:
    if not _reftable(tree):
        return resolve_ref(tree.common, name)
    try:
        found = os.fsdecode(git_output(["rev-parse", "-q", "--verify", f"{name}^{{commit}}"], cwd=tree.top)).strip()
    except ConfigError:
        return None
    return found if OBJECT_ID.fullmatch(found) else None


def _newer(common: Path, origin: str, fetched: Optional[str], cache: Path) -> str:
    """`fetched` when it descends from `origin`, and `origin` otherwise, asking `git merge-base` once a pair."""

    if fetched is None or fetched == origin:
        return origin
    record = cache / f"{origin}-{fetched}.newer"
    known = _text(record)
    if known is not None and known in (origin, fetched):
        return known
    try:
        base = os.fsdecode(git_output(["--git-dir", str(common), "merge-base", origin, fetched])).strip()
    except ConfigError:
        # Unrelated histories have no merge base, and a ref whose commit is gone has nothing to read.
        return origin
    chosen = fetched if base == origin else origin
    _store(record, chosen.encode("ascii"))
    return chosen


def _trees(directories: list[Path]) -> list[Optional[WorkingTree]]:
    """The working tree holding each of `directories`, an ancestor chain ordered nearest first."""

    found: list[Optional[WorkingTree]] = []
    current = working_tree(directories[-1])
    for directory in reversed(directories):
        if directory != directories[-1] and (directory / ".git").exists():
            current = _tree_at(directory)
        found.append(current)
    return found[::-1]


# The cache of what Git said, under the state directory

CACHE_LIFETIME = 30 * 24 * 3600.0


def cache_dir(environ: Optional[dict[str, str]] = None) -> Path:
    return state_home(environ) / "config-cache"


def _blob_name(commit: str, path: str) -> str:
    return f"{commit}-{hashlib.sha256(path.encode('utf-8', 'surrogateescape')).hexdigest()[:16]}"


def _store(path: Path, data: bytes) -> None:
    """Write a cache file whole, or leave it out when the state directory cannot take it, since the cache only
    saves a Git process."""

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    except OSError:
        pass


def _prune(cache: Path) -> None:
    """Remove the blobs no load has written for `CACHE_LIFETIME`, which are mostly those of commits the default
    branch has moved past."""

    cutoff = time.time() - CACHE_LIFETIME
    try:
        for path in cache.iterdir():
            if path.suffix in (".toml", ".absent", ".newer") and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
    except OSError:
        pass


SYMBOLIC_LINK = "120000"
# What `git cat-file --batch --follow-symlinks` prints in place of an object, for a link it cannot follow to one.
BROKEN_LINKS = {b"symlink": "leads outside the repository", b"dangling": "leads to no file",
                b"loop": "leads into a loop of links", b"notdir": "leads through a file as though it were a folder"}


def _listed_objects(common: Path, commit: str, paths: list[str], label: str) -> dict[str, str]:
    """The object each of `paths` names on the commit `commit`, for the paths it holds as files, from one
    `git ls-tree` that takes the paths as arguments and separates its entries with NUL, so any byte in a path
    reads back exactly. A symbolic link among them is a configuration error, because only the batch, which
    cannot name such a path, follows a link."""

    listed = git_output(["--git-dir", str(common), "--literal-pathspecs", "ls-tree", "-z", "--full-tree", commit,
                         "--", *paths])
    found = {}
    for entry in listed.split(b"\0"):
        info, _, name = entry.partition(b"\t")
        fields = info.decode("ascii", "replace").split(" ")
        if len(fields) == 3 and fields[0] == SYMBOLIC_LINK:
            raise ConfigError(f"{label}:{os.fsdecode(name)}: a symbolic link at a path with a newline cannot be "
                              "followed; commit the file itself")
        if len(fields) == 3 and fields[1] == "blob":
            found[os.fsdecode(name)] = fields[2]
    return found


def default_blobs(common: Path, commit: str, paths: list[str], cache: Path, *, label: str
                  ) -> dict[str, Optional[bytes]]:
    """Each of `paths` as the commit `commit` holds it, or None where it holds no file, asking one
    `git cat-file --batch` for the paths the cache lacks.

    A symbolic link is followed within that commit's tree, never on disk, and one that leads to no file there
    is a configuration error that names it as `<label>:<path>`."""

    found: dict[str, Optional[bytes]] = {}
    wanted = []
    for path in paths:
        name = _blob_name(commit, path)
        try:
            found[path] = (cache / f"{name}.toml").read_bytes()
        except OSError:
            if (cache / f"{name}.absent").exists():
                found[path] = None
            else:
                wanted.append(path)
    if not wanted:
        return found
    # Git's batch input is one name a line, so a path with a newline is asked for by the object `git ls-tree`
    # finds at it, and one that names no file there is absent.
    names = {path: f"{commit}:{path}" for path in wanted if "\n" not in path}
    lined = [path for path in wanted if "\n" in path]
    if lined:
        names.update(_listed_objects(common, commit, lined, label))
    asked = [path for path in wanted if path in names]
    output = git_output(["--git-dir", str(common), "cat-file", "--batch", "--follow-symlinks"],
                        input="".join(f"{names[path]}\n" for path in asked).encode("utf-8", "surrogateescape")
                        ) if asked else b""
    at = 0
    contents: dict[str, Optional[bytes]] = {}
    for path in asked:
        end = output.index(b"\n", at)
        header = output[at:end].split(b" ")
        at = end + 1
        content = None
        # `<object> <type> <size>`, or `<problem> <size>` for a link it cannot follow, each before a body of that
        # size; a name it cannot find ends `missing`, with no body.
        if len(header) in (2, 3) and header[-1].isdigit():
            size = int(header[-1])
            body = output[at:at + size]
            at += size + 1
            if len(header) == 2 and header[0] in BROKEN_LINKS:
                raise ConfigError(f"{label}:{path}: the symbolic link {BROKEN_LINKS[header[0]]} "
                                  f"({body.decode('utf-8', 'replace')}); commit the file itself, or a link to a "
                                  "file in the repository")
            if header[1:2] == [b"blob"]:
                content = body
        contents[path] = content
    for path in wanted:
        content = contents.get(path)
        found[path] = content
        name = _blob_name(commit, path)
        _store(cache / (f"{name}.absent" if content is None else f"{name}.toml"), content or b"")
    _prune(cache)
    return found


def _signature(path: Path) -> Optional[list[int]]:
    try:
        found = path.stat()
    except OSError:
        return None
    return [found.st_size, found.st_mtime_ns, found.st_ino]


def tracked_paths(tree: WorkingTree, paths: list[str], cache: Path) -> set[str]:
    """Which of `paths` the index of `tree` tracks, asking one `git ls-files` for the paths the cache lacks for
    the index as it is now."""

    index = tree.git_dir / "index"
    signature = _signature(index)
    if signature is None:
        return set()
    record_path = cache / "tracked.json"
    try:
        record = json.loads(record_path.read_bytes())
    except (OSError, ValueError):
        record = None
    if not isinstance(record, dict) or record.get("schema_version") != 1 or not isinstance(record.get("indexes"),
                                                                                          dict):
        record = {"schema_version": 1, "indexes": {}}
    indexes = record["indexes"]
    entry = indexes.get(str(index))
    known = entry["tracked"] if isinstance(entry, dict) and entry.get("signature") == signature else {}
    wanted = [path for path in paths if path not in known]
    if wanted:
        listed = git_output(["--git-dir", str(tree.git_dir), "--work-tree", str(tree.top), "--literal-pathspecs",
                             "ls-files", "-z", "--cached", "--", *wanted], cwd=tree.top)
        found = {os.fsdecode(name) for name in listed.split(b"\0") if name}
        known = {**known, **{path: path in found for path in wanted}}
        indexes[str(index)] = {"signature": signature, "tracked": known}
        for gone in [name for name in indexes if not Path(name).exists()]:
            del indexes[gone]
        _store(record_path, json.dumps(record, sort_keys=True).encode("utf-8"))
    return {path for path in paths if known.get(path)}


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


@dataclass(frozen=True)
class Source:
    """Where values came from: a file on disk, or, when `blob` names it, as `origin/main:.projector.toml` does,
    the copy of the walked file at `path` on the default branch."""

    path: Path
    blob: Optional[str] = None
    override: Optional[int] = None

    @property
    def location(self) -> str:
        return self.blob or str(self.path)

    def __str__(self) -> str:
        return self.location if self.override is None else f"{self.location} (override {self.override})"


class Config:
    """A merged configuration, the sources it read lowest precedence first, where each value came from, the
    user-only keys a walked file set, a note for each walked file whose working copy does not apply, the default
    branch each file in the repository waits on to change, the user layer, and where your approvals live."""

    def __init__(self, values: dict[str, Any], sources: dict[str, Source], paths: list[Source],
                 ignored: Optional[list[tuple[Source, str]]] = None, notes: Optional[list[tuple[Path, str]]] = None,
                 committed: Optional[dict[Path, str]] = None, user: Optional[Path] = None,
                 approvals: Optional[Path] = None) -> None:
        self.values = values
        self.sources = sources
        self.paths = paths
        self.ignored = ignored or []
        self.notes = notes or []
        self.committed = committed or {}
        self.user = user
        self.approvals = approvals

    def get(self, key: str, default: Any = None) -> Any:
        """Look a value up by dotted key, so `review.username` reaches a table."""

        current: Any = self.values
        for part in key.split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

    def setting(self, key: str) -> Any:
        """A key from `SETTINGS`, or None when unset; a value of another type is an error naming the key, and a
        destination a committed file sets that you have not allowed raises `NotAllowed`."""

        value = self.get(key)
        kind = SETTINGS[key]
        # TOML reads `true` as a bool, which Python also counts as an int.
        if value is not None and (not isinstance(value, kind) or (kind is int and isinstance(value, bool))):
            expected = {bool: "true or false", int: "a whole number", dict: "a table"}.get(kind, "a string")
            raise ConfigError(f"{key} must be {expected}, not {type(value).__name__}")
        source = self.sources.get(key)
        if value is not None and is_destination(key) and source is not None and source.blob is not None and (
                self.approvals is None or not is_allowed(self.approvals, key, value)):
            raise NotAllowed(key, value, source)
        return value

    def source(self, key: str) -> Optional[Source]:
        return self.sources.get(key)

    def from_user(self, key: str) -> bool:
        """Whether `key`'s value comes from the user layer, which you wrote yourself."""

        source = self.sources.get(key)
        return source is not None and source.blob is None and source.path == self.user

    def confine(self, key: str, path: Path, places: tuple[Path, ...]) -> None:
        """Refuse `path`, where `key`'s value leads, unless it resolves inside one of `places`."""

        resolved = path.resolve()
        if not any(_within(resolved, place.resolve()) for place in places):
            raise ConfigError(f"{self.source(key)} sets {key} = {self.get(key)!r}, which resolves to {resolved}, "
                              f"outside {' and '.join(str(place) for place in places)}")

    def flat(self) -> dict[str, Any]:
        return _flatten(self.values)


@dataclass
class _Walked:
    """What one load learned about the walk: each source read with its values, lowest precedence first, and the
    notes and default branches `Config` reports."""

    layers: list[tuple[Source, dict[str, Any]]] = field(default_factory=list)
    notes: list[tuple[Path, str]] = field(default_factory=list)
    committed: dict[Path, str] = field(default_factory=dict)


def _walk(start: Path, home: Path, user: Optional[Path], environ: dict[str, str]) -> _Walked:
    directories = walked_directories(start, home)
    trees = _trees(directories)
    repository = trees[0]
    cache = cache_dir(environ)
    branch = default_branch(repository, cache) if repository is not None else None
    # Each walked file, farthest first, with its path in the repository when it lies in one of its working trees.
    candidates: list[tuple[Path, Optional[WorkingTree], Optional[str]]] = []
    for directory, tree in zip(reversed(directories), reversed(trees)):
        candidate = directory / CONFIG_NAME
        if candidate == user:
            continue
        if branch is not None and tree is not None and repository is not None and tree.common == repository.common:
            candidates.append((candidate, tree, candidate.relative_to(tree.top).as_posix()))
        elif candidate.is_file():
            candidates.append((candidate, None, None))

    walked = _Walked()
    if user is not None:
        walked.layers.append((Source(user), read_config_file(user)))
    if branch is None or repository is None:
        walked.layers.extend((Source(path), read_config_file(path)) for path, _, _ in candidates)
        return walked

    blobs = default_blobs(repository.common, branch.commit,
                          list(dict.fromkeys(path for _, _, path in candidates if path is not None)), cache,
                          label=branch.name)
    tracked: set[tuple[Path, str]] = set()
    for tree in {tree for _, tree, path in candidates if tree is not None}:
        asked = [path for candidate, other, path in candidates
                 if other == tree and path is not None and blobs[path] is None and candidate.is_file()]
        if asked:
            tracked.update((tree.top, path) for path in tracked_paths(tree, asked, cache))

    for candidate, tree, path in candidates:
        if tree is None or path is None:
            walked.layers.append((Source(candidate), read_config_file(candidate)))
            continue
        blob = blobs[path]
        if blob is not None:
            source = Source(candidate, f"{branch.name}:{path}")
            walked.committed[candidate] = branch.name
            try:
                working = candidate.read_bytes()
            except OSError:
                working = None
            if working != blob:
                walked.notes.append((candidate, f"working copy ignored until merged; {source} applies"))
            # Two worktrees of one repository walk the same path on the default branch, and the nearer one wins.
            walked.layers = [layer for layer in walked.layers if layer[0].blob != source.blob]
            walked.layers.append((source, parse_config(blob, str(source))))
        elif (tree.top, path) in tracked:
            walked.committed[candidate] = branch.name
            walked.notes.append((candidate, f"not on {branch.name} yet, so it is ignored until it merges"))
        elif candidate.is_file():
            walked.layers.append((Source(candidate), read_config_file(candidate)))
    return walked


def load(start: Path, home: Optional[Path] = None, environ: Optional[dict[str, str]] = None) -> Config:
    env = os.environ if environ is None else environ
    if home is None:
        home = Path(env.get("HOME") or Path.home())

    found = user_config_path(env)
    # Resolved like the walked paths, so the home directory's file, which is
    # both the user layer and the top of the walk, is read once.
    user = found.resolve() if found.is_file() else None
    walked = _walk(start, home, user, env)
    config = layered(walked.layers, user)
    config.notes, config.committed, config.approvals = walked.notes, walked.committed, allowed_path(env)
    return config


def load_committed(data: Optional[bytes], label: str, environ: Optional[dict[str, str]] = None) -> Config:
    """What `load` reads at the top of a checkout of a repository you have no checkout of: the user layer, then
    `data`, the `.projector.toml` its default branch commits, which `label` names, or nothing when it has none."""

    env = os.environ if environ is None else environ
    found = user_config_path(env)
    user = found.resolve() if found.is_file() else None
    layers = [(Source(user), read_config_file(user))] if user is not None else []
    if data is not None:
        layers.append((Source(Path(CONFIG_NAME), label), parse_config(data, label)))
    config = layered(layers, user)
    config.approvals = allowed_path(env)
    return config


def _overrides(source: Source, loaded: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    """Take the `[override.<priority>]` tables out of a file's values, refusing a priority outside 1 to 99."""

    found = loaded.pop(OVERRIDE, None)
    if found is None:
        return []
    if not isinstance(found, dict):
        raise ConfigError(f"{source}: {OVERRIDE} must hold tables named by priority, such as [{OVERRIDE}.1]")
    tables = []
    for priority, table in found.items():
        if not PRIORITY.fullmatch(priority) or not isinstance(table, dict):
            raise ConfigError(f"{source}: [{OVERRIDE}.{priority}] needs a priority, a whole number from 1 to 99, "
                              f"such as [{OVERRIDE}.1]")
        tables.append((int(priority), table))
    return tables


def drop_user_only(loaded: dict[str, Any]) -> list[str]:
    """Remove each `USER_ONLY` key, and everything under it, from a walked file's values or an override table, and
    answer the dotted keys removed. The subtree is found by walking the parsed tables, never by splitting a
    flattened key, since a quoted key such as `"alice.work"` holds a dot of its own."""

    removed: list[str] = []
    for name in sorted(USER_ONLY):
        *tables, last = name.split(".")
        chain = [loaded]
        for part in tables:
            child = chain[-1].get(part)
            if not isinstance(child, dict):
                break
            chain.append(child)
        else:
            if last not in chain[-1]:
                continue
            value = chain[-1].pop(last)
            removed += sorted(_flatten(value, f"{name}.")) if isinstance(value, dict) and value else [name]
            # A table left empty, as `[agent]` is once `agent.peers` goes from a file that set nothing else
            # there, is dropped too, so `config get` finds no value the file was refused.
            for parent, part in zip(reversed(chain[:-1]), reversed(tables)):
                if parent[part]:
                    break
                del parent[part]
    return removed


def layered(layers: list[tuple[Source, dict[str, Any]]], user: Optional[Path]) -> Config:
    """Merge `layers`, lowest precedence first, then apply every override over them.

    Among overrides, the higher priority wins, and at equal priority the
    farther file wins, so `user`, the first layer, wins over every walked file.
    A user-only key is read only as an ordinary setting of `user`, never from
    an override in any file, so the commands that remove or replace it, which
    edit only the ordinary key, leave no other copy in force.
    """

    values: dict[str, Any] = {}
    sources: dict[str, Source] = {}
    ignored: list[tuple[Source, str]] = []
    overrides: list[tuple[int, int, Source, dict[str, Any]]] = []
    for nearness, (source, loaded) in enumerate(layers):
        for priority, table in _overrides(source, loaded):
            overriding = Source(source.path, source.blob, priority)
            ignored += [(overriding, key) for key in drop_user_only(table)]
            overrides.append((priority, -nearness, overriding, table))
        if source.path != user:
            ignored += [(source, key) for key in drop_user_only(loaded)]
        values = merge(values, loaded)
        # Recorded after merging so the last writer of each leaf wins, which is
        # the file whose value actually survived.
        for dotted in _flatten(loaded):
            sources[dotted] = source
    for _, _, source, table in sorted(overrides, key=lambda found: found[:2]):
        values = merge(values, table)
        for dotted in _flatten(table):
            sources[dotted] = source

    # A later file can replace a scalar with a table, which leaves the scalar's
    # own dotted key attributed to a file that no longer supplies it. Drop any
    # key the merged result does not actually have.
    final = _flatten(values)
    sources = {key: source for key, source in sources.items() if key in final}
    return Config(values, sources, [source for source, _ in layers], ignored, user=user)


@dataclass(frozen=True)
class Change:
    """What `write_value` did to one key: `set`, `unset`, or `unchanged`."""

    action: str
    key: str
    old: Any
    new: Any
    path: Path
    override: Optional[int] = None


def write_value(path: Path, setting: str, value: Any, override: Optional[int] = None) -> Change:
    """Set `setting` to `value` in the file at `path`, or remove it when `value` is None, under
    `[override.<override>]` when `override` names a priority.

    Only the lines that set the key change, so every comment, blank line, and
    other setting stays byte for byte. The edit is checked twice: before
    writing, by parsing the new text, and after, by reading the file back,
    which restores the original when the two disagree.
    """

    change = _write_key(path, setting if override is None else f"{OVERRIDE}.{override}.{setting}", value)
    return Change(change.action, setting, change.old, change.new, path, override)


def _write_key(path: Path, key: str, value: Any) -> Change:
    original = path.read_bytes().decode("utf-8") if path.exists() else None
    try:
        before = tomllib.loads(original or "")
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path}: invalid TOML: {error}") from error
    old = Config(before, {}, []).get(key)
    if same_value(old, value):
        return Change("unchanged", key, old, value, path)
    if isinstance(old, dict):
        command = "unset removes" if value is None else "set changes"
        raise ProjectorError(f"{path}: {key} is a table, and {command} one setting, not a table; the file is unchanged")

    expected = {name: setting for name, setting in _flatten(before).items() if name != key}
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


def unset_value(path: Path, key: str, override: Optional[int] = None) -> Change:
    """Remove `key` from the file at `path`, or from its `[override.<override>]` table.

    A key outside `SETTINGS` is removed when the file sets it, so a setting
    Projector no longer reads can be cleaned up, and refused when the file
    does not, so a misspelled key still gets the list of keys.
    """

    change = write_value(path, key, None, override)
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

    def table_header(table: tuple[str, ...]) -> Optional[_Header]:
        return next((header for header in headers if header.table == table and not header.array), None)

    parent = path[:-1]
    header = table_header(parent)
    if header is None and path[0] == OVERRIDE and len(path) > 3:
        # An override holds settings under their usual dotted names, as
        # `[override.1]` and then `agent.relay = ...` do.
        parent = path[:2]
        header = table_header(parent)
    if header is not None or not parent:
        # After the section's last statement rather than at its end, so a
        # comment that introduces the next table stays above that table.
        siblings = [entry for entry in entries if entry.table == parent]
        written = path[len(parent):]
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
                    f"{'.'.join(path[len(parent):])} = {_toml_value(value)}{newline}")
        after = dotted[-1]
        written = path[len(after.table):]
        at = after.end

    if at and not lines[at - 1].endswith("\n"):
        lines[at - 1] += newline
    indent = after.indent if after else ""
    lines.insert(at, f"{indent}{'.'.join(written)} = {_toml_value(value)}{newline}")
    return "".join(lines)
